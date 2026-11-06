"""Complete data-subject export and erasure for one lead.

The local transaction covers every table that can carry that person's data.
Objects or provider records outside PostgreSQL are either actually deleted
from managed storage or written to a durable *pending* compensation outbox;
local deletion is never presented as provider confirmation.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.settings import settings
from app.db.models import (
    Agency,
    AgentRun,
    ConsentAction,
    ConsentLog,
    ConsentScope,
    Conversation,
    ExternalErasureRequest,
    InboundOutbox,
    Lead,
    LeadOffer,
    Message,
    Offer,
    Visit,
    WebhookOutbox,
    WebhookSubscription,
)
from app.services.gdpr_erasure import (
    agency_pseudonym,
    locator_digest,
    queue_external_erasure,
    subject_pseudonym,
)
from app.services.storage import delete_tenant_key_prefix

log = get_logger(__name__)

_CHANNEL_PROVIDER = {
    "whatsapp": "meta_whatsapp",
    "email": "resend",
    "voice": "vapi",
    "sms": "twilio",
}
_RECORDING_KEYS = frozenset(
    {
        "recording_url",
        "recordingUrl",
        "last_call_recording_url",
        "audio_url",
        "audioUrl",
        "transcript_url",
        "transcriptUrl",
    }
)


@dataclass(frozen=True)
class ErasureResult:
    subject_pseudonym: str
    local_erasure_completed: bool
    external_erasures_pending: int
    managed_artifacts_deleted: int


async def queue_agency_external_erasures(
    db: AsyncSession,
    *,
    agency: Agency,
) -> int:
    """Inventory external tenant copies before a termination purge.

    The local records remain the source for every provider locator until the
    caller commits this outbox and the destructive tenant purge atomically.
    This function only creates ``pending`` work; provider completion still
    requires the explicit confirmation path in :mod:`gdpr_erasure`.
    """
    agency_id = agency.id
    purge_pseudonym = agency_pseudonym(agency_id)
    pending_targets: dict[
        tuple[str, str, str], tuple[str, str, str, str]
    ] = {}

    def collector(pseudonym: str):
        def pending(provider: str, resource_type: str, locator: str) -> None:
            cleaned = str(locator or "").strip()
            if not cleaned:
                return
            normalized_provider = str(provider or "unknown_provider")[:48]
            normalized_resource = str(resource_type or "external_copy")[:64]
            key = (
                normalized_provider,
                normalized_resource,
                locator_digest(cleaned),
            )
            pending_targets.setdefault(
                key,
                (
                    pseudonym,
                    normalized_provider,
                    normalized_resource,
                    cleaned,
                ),
            )

        return pending

    # Reuse the subject-level inventory so a termination purge has the same
    # provider coverage as an Article 17 request for every lead.
    leads = await _all(db, select(Lead).where(Lead.agency_id == agency_id))
    for lead in leads:
        related = await _related_rows(db, agency_id, lead)
        _collect_provider_targets(
            lead=lead,
            pseudonym=subject_pseudonym(agency_id, lead.id),
            related=related,
            pending=collector(subject_pseudonym(agency_id, lead.id)),
        )

    tenant_pending = collector(purge_pseudonym)

    # Receipts can predate creation of a Lead/Conversation (for example after
    # a worker outage), so inventory every tenant receipt, not only those the
    # subject matching pass could associate with an existing lead.
    receipts = await _all(
        db,
        select(InboundOutbox).where(InboundOutbox.agency_id == agency_id),
    )
    if _collect_inbound_receipt_targets(receipts, tenant_pending):
        tenant_pending(
            "deepgram",
            "transcription_input_manual_review",
            _processing_window(purge_pseudonym, receipts),
        )

    # Some LLM runs are agency/property operations without a lead foreign key.
    # Keep one bounded review window so those external processing copies are
    # not made invisible when the local audit rows disappear.
    runs = await _all(db, select(AgentRun).where(AgentRun.agency_id == agency_id))
    if runs:
        tenant_pending(
            "anthropic",
            "processing_window_manual_review",
            _processing_window(purge_pseudonym, runs),
        )

    # A webhook payload need not mention a lead (property and visit events are
    # valid too). Any attempted delivery may have left a subscriber-side copy.
    deliveries = await _all(
        db,
        select(WebhookOutbox).where(WebhookOutbox.agency_id == agency_id),
    )
    subscription_ids = {row.subscription_id for row in deliveries}
    subscriptions = (
        await _all(
            db,
            select(WebhookSubscription).where(
                WebhookSubscription.agency_id == agency_id,
                WebhookSubscription.id.in_(subscription_ids),
            ),
        )
        if subscription_ids
        else []
    )
    subscriptions_by_id = {row.id: row for row in subscriptions}
    for delivery in deliveries:
        _collect_webhook_delivery_target(
            delivery,
            subscriptions_by_id.get(delivery.subscription_id),
            tenant_pending,
        )

    _collect_agency_integration_targets(agency.settings or {}, tenant_pending)

    # Subject erasures may already have queued some targets. Skip those
    # idempotently instead of turning a later agency purge into a unique-key
    # failure that would leave operational data undeleted.
    existing_rows = (
        await db.execute(
            select(
                ExternalErasureRequest.provider,
                ExternalErasureRequest.resource_type,
                ExternalErasureRequest.locator_digest,
            ).where(ExternalErasureRequest.agency_id == agency_id)
        )
    ).all()
    existing = {tuple(row) for row in existing_rows}
    created = 0
    for key, (pseudonym, provider, resource_type, locator) in sorted(
        pending_targets.items()
    ):
        if key in existing:
            continue
        queue_external_erasure(
            db,
            agency_id=agency_id,
            pseudonym=pseudonym,
            provider=provider,
            resource_type=resource_type,
            locator=locator,
        )
        existing.add(key)
        created += 1

    # Detect encryption/schema/constraint errors while all source rows still
    # exist. The caller's commit remains the atomic durability boundary.
    await db.flush()
    return created


async def _all(db: AsyncSession, statement) -> list[Any]:
    return list((await db.execute(statement)).scalars().all())


async def _related_rows(db: AsyncSession, agency_id: uuid.UUID, lead: Lead) -> dict[str, list[Any]]:
    lead_id = lead.id
    conversations = await _all(
        db,
        select(Conversation).where(
            Conversation.agency_id == agency_id,
            Conversation.lead_id == lead_id,
        ),
    )
    conversation_ids = [conversation.id for conversation in conversations]
    messages = (
        await _all(
            db,
            select(Message).where(
                Message.agency_id == agency_id,
                Message.conversation_id.in_(conversation_ids),
            ),
        )
        if conversation_ids
        else []
    )
    visits = await _all(
        db,
        select(Visit).where(Visit.agency_id == agency_id, Visit.lead_id == lead_id),
    )
    consents = await _all(
        db,
        select(ConsentLog).where(
            ConsentLog.agency_id == agency_id,
            ConsentLog.lead_id == lead_id,
        ),
    )
    offers = await _all(
        db,
        select(Offer).where(Offer.agency_id == agency_id, Offer.lead_id == lead_id),
    )
    marketplace_relations = await _all(
        db,
        select(LeadOffer).where(
            or_(
                LeadOffer.lead_id == lead_id,
                LeadOffer.transferred_lead_id == lead_id,
            )
        ),
    )
    run_predicates = [AgentRun.lead_id == lead_id]
    if conversation_ids:
        run_predicates.append(AgentRun.conversation_id.in_(conversation_ids))
    agent_runs = await _all(
        db,
        select(AgentRun).where(
            AgentRun.agency_id == agency_id,
            or_(*run_predicates),
        ),
    )
    subject_identifiers = _subject_identifiers(
        lead=lead,
        conversations=conversations,
        messages=messages,
        visits=visits,
        offers=offers,
    )
    inbound_receipts = [
        row
        for row in await _all(
            db,
            select(InboundOutbox).where(InboundOutbox.agency_id == agency_id),
        )
        if _contains_subject_identifier(row.payload, subject_identifiers)
        or _contains_subject_identifier(row.context, subject_identifiers)
    ]
    webhook_deliveries = [
        row
        for row in await _all(
            db,
            select(WebhookOutbox).where(WebhookOutbox.agency_id == agency_id),
        )
        if _contains_subject_identifier(row.payload, subject_identifiers)
    ]
    subscription_ids = {row.subscription_id for row in webhook_deliveries}
    webhook_subscriptions = (
        await _all(
            db,
            select(WebhookSubscription).where(
                WebhookSubscription.agency_id == agency_id,
                WebhookSubscription.id.in_(subscription_ids),
            ),
        )
        if subscription_ids
        else []
    )
    return {
        "conversations": conversations,
        "messages": messages,
        "visits": visits,
        "consents": consents,
        "offers": offers,
        "marketplace_relations": marketplace_relations,
        "agent_runs": agent_runs,
        "inbound_receipts": inbound_receipts,
        "webhook_deliveries": webhook_deliveries,
        "webhook_subscriptions": webhook_subscriptions,
    }


async def export_lead_data(
    db: AsyncSession, agency_id: uuid.UUID, lead_id: uuid.UUID
) -> dict[str, Any]:
    lead = await db.get(Lead, lead_id)
    if lead is None or lead.agency_id != agency_id:
        return {"error": "lead_not_found"}

    related = await _related_rows(db, agency_id, lead)
    pseudonym = subject_pseudonym(agency_id, lead_id)
    external_requests = await _all(
        db,
        select(ExternalErasureRequest).where(
            ExternalErasureRequest.agency_id == agency_id,
            ExternalErasureRequest.subject_pseudonym == pseudonym,
        ),
    )

    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "lead": _serialize(lead),
        "conversations": [_serialize(row) for row in related["conversations"]],
        "messages": [_serialize(row) for row in related["messages"]],
        "visits": [_serialize(row) for row in related["visits"]],
        "consents": [_serialize(row) for row in related["consents"]],
        "offers": [_serialize(row) for row in related["offers"]],
        # Marketplace redemption tokens and private commercial notes are not
        # the data subject's personal data and must not cross tenant borders.
        "marketplace_relations": [
            _serialize_marketplace_relation(row) for row in related["marketplace_relations"]
        ],
        "agent_runs": [_serialize(row) for row in related["agent_runs"]],
        "inbound_receipts": [_serialize(row) for row in related["inbound_receipts"]],
        "webhook_deliveries": [_serialize(row) for row in related["webhook_deliveries"]],
        "artifacts": _artifact_manifest(lead, related),
        # Never export encrypted locators/digests. Status is enough to tell
        # the requester honestly whether an external deletion is unresolved.
        "external_erasure_requests": [
            {
                "provider": row.provider,
                "resource_type": row.resource_type,
                "status": row.status,
                "attempts": row.attempts,
                "created_at": _iso(row.created_at),
                "completed_at": _iso(row.completed_at),
            }
            for row in external_requests
        ],
    }


async def erasure_status(
    db: AsyncSession, agency_id: uuid.UUID, pseudonym: str
) -> dict[str, Any] | None:
    evidence = await _all(
        db,
        select(ConsentLog).where(
            ConsentLog.agency_id == agency_id,
            ConsentLog.subject_pseudonym == pseudonym,
            ConsentLog.source == "gdpr_erasure",
        ),
    )
    if not evidence:
        return None
    requests = await _all(
        db,
        select(ExternalErasureRequest).where(
            ExternalErasureRequest.agency_id == agency_id,
            ExternalErasureRequest.subject_pseudonym == pseudonym,
        ),
    )
    pending_count = sum(row.status != "completed" for row in requests)
    return {
        "erasure_reference": pseudonym,
        "local_erasure_completed": True,
        "external_erasure_status": "pending" if pending_count else "completed",
        "external_erasures_pending": pending_count,
        "external_requests": [
            {
                "provider": row.provider,
                "resource_type": row.resource_type,
                "status": row.status,
                "attempts": row.attempts,
                "completed_at": _iso(row.completed_at),
            }
            for row in requests
        ],
    }


async def erase_lead(
    db: AsyncSession,
    agency_id: uuid.UUID,
    lead_id: uuid.UUID,
    *,
    requested_by: str = "lead",
    reason: str = "GDPR art. 17 request",
) -> ErasureResult | None:
    lead = await db.get(Lead, lead_id)
    if lead is None or lead.agency_id != agency_id:
        return None

    # These strings may contain a person's name or free-form explanation.
    # They authorize the call at a higher layer but are intentionally not
    # persisted: the retained record is only the event, time and pseudonym.
    _ = requested_by, reason
    pseudonym = subject_pseudonym(agency_id, lead_id)
    related = await _related_rows(db, agency_id, lead)
    pending_targets: set[tuple[str, str, str]] = set()

    def pending(provider: str, resource_type: str, locator: str) -> None:
        cleaned = str(locator or "").strip()
        if cleaned:
            pending_targets.add((provider, resource_type, cleaned))

    _collect_provider_targets(
        lead=lead,
        pseudonym=pseudonym,
        related=related,
        pending=pending,
    )

    managed_deleted = 0
    storage_provider = "s3" if settings.s3_bucket.strip() else "local_storage"
    managed_prefixes = [f"cards/{lead_id}_"] + [
        f"offers/{offer.id}.pdf" for offer in related["offers"]
    ]
    for key_prefix in managed_prefixes:
        try:
            managed_deleted += delete_tenant_key_prefix(agency_id, key_prefix)
        except Exception as exc:  # deletion is compensated durably below
            log.warning(
                "gdpr.managed_artifact_deletion_failed",
                subject=pseudonym,
                storage=storage_provider,
                error=type(exc).__name__,
            )
            pending(storage_provider, "managed_object_prefix", key_prefix)

    for provider, resource_type, locator in sorted(pending_targets):
        queue_external_erasure(
            db,
            agency_id=agency_id,
            pseudonym=pseudonym,
            provider=provider,
            resource_type=resource_type,
            locator=locator,
        )
        log.info(
            "gdpr.external_erasure_pending",
            subject=pseudonym,
            provider=provider,
            resource_type=resource_type,
        )

    # Retain only the minimum legal shape. Free-form evidence, IP, user agent
    # and the lead FK are direct/linkable personal data and are removed.
    for consent in related["consents"]:
        consent.lead_id = None
        consent.subject_pseudonym = pseudonym
        consent.source = "retained_legal_evidence"
        consent.evidence = ""
        consent.ip_address = None
        consent.user_agent = None
        consent.document_version = None

    db.add(
        ConsentLog(
            agency_id=agency_id,
            lead_id=None,
            user_id=None,
            subject_pseudonym=pseudonym,
            scope=ConsentScope.DATA_PROCESSING,
            action=ConsentAction.REVOKED,
            source="gdpr_erasure",
            evidence="",
            ip_address=None,
            user_agent=None,
        )
    )
    await db.flush()
    all_external_requests = await _all(
        db,
        select(ExternalErasureRequest).where(
            ExternalErasureRequest.agency_id == agency_id,
            ExternalErasureRequest.subject_pseudonym == pseudonym,
        ),
    )
    pending_external_count = sum(request.status != "completed" for request in all_external_requests)

    inbound_receipt_ids = [row.id for row in related["inbound_receipts"]]
    if inbound_receipt_ids:
        await db.execute(
            delete(InboundOutbox).where(
                InboundOutbox.agency_id == agency_id,
                InboundOutbox.id.in_(inbound_receipt_ids),
            )
        )
    webhook_delivery_ids = [row.id for row in related["webhook_deliveries"]]
    if webhook_delivery_ids:
        await db.execute(
            delete(WebhookOutbox).where(
                WebhookOutbox.agency_id == agency_id,
                WebhookOutbox.id.in_(webhook_delivery_ids),
            )
        )

    conversation_ids = [row.id for row in related["conversations"]]
    if conversation_ids:
        await db.execute(
            delete(Message).where(
                Message.agency_id == agency_id,
                Message.conversation_id.in_(conversation_ids),
            )
        )
    await db.execute(
        delete(AgentRun).where(
            AgentRun.agency_id == agency_id,
            or_(
                AgentRun.lead_id == lead_id,
                AgentRun.conversation_id.in_(conversation_ids)
                if conversation_ids
                else AgentRun.lead_id == lead_id,
            ),
        )
    )
    await db.execute(delete(Offer).where(Offer.agency_id == agency_id, Offer.lead_id == lead_id))
    # The seller's listing is local subject data and disappears. A relation
    # owned by another controller only loses its pointer; its review request
    # stays pending in the outbox instead of crossing tenants silently.
    await db.execute(
        delete(LeadOffer).where(
            LeadOffer.seller_agency_id == agency_id,
            LeadOffer.lead_id == lead_id,
        )
    )
    await db.execute(
        update(LeadOffer)
        .where(LeadOffer.transferred_lead_id == lead_id)
        .values(transferred_lead_id=None)
    )
    await db.execute(delete(Visit).where(Visit.agency_id == agency_id, Visit.lead_id == lead_id))
    await db.execute(
        delete(Conversation).where(
            Conversation.agency_id == agency_id,
            Conversation.lead_id == lead_id,
        )
    )
    await db.execute(delete(Lead).where(Lead.agency_id == agency_id, Lead.id == lead_id))

    return ErasureResult(
        subject_pseudonym=pseudonym,
        local_erasure_completed=True,
        external_erasures_pending=pending_external_count,
        managed_artifacts_deleted=managed_deleted,
    )


def _collect_provider_targets(*, lead, pseudonym: str, related, pending) -> None:
    conversations = related["conversations"]
    messages = related["messages"]
    visits = related["visits"]
    offers = related["offers"]
    runs = related["agent_runs"]
    marketplace = related["marketplace_relations"]
    inbound_receipts = related["inbound_receipts"]
    webhook_deliveries = related["webhook_deliveries"]
    subscriptions = {row.id: row for row in related["webhook_subscriptions"]}
    provider_by_conversation = {
        row.id: _CHANNEL_PROVIDER.get(_enum_value(row.channel)) for row in conversations
    }

    for conversation in conversations:
        provider = provider_by_conversation[conversation.id]
        if provider and conversation.external_thread_id:
            pending(provider, "conversation", str(conversation.external_thread_id))
        for url in _recording_urls(conversation.extra):
            pending(provider or "unknown_channel_provider", "recording", url)

    deepgram_used = False
    for message in messages:
        provider = provider_by_conversation.get(message.conversation_id)
        if provider and message.external_message_id:
            pending(provider, "message", str(message.external_message_id))
        raw = message.raw if isinstance(message.raw, dict) else {}
        for key in ("audio_media_id", "media_id", "document_id", "image_id", "video_id"):
            if raw.get(key):
                pending(provider or "unknown_channel_provider", "media", str(raw[key]))
        if raw.get("audio_media_id") or (
            _enum_value(getattr(message, "role", "")) == "lead"
            and str(getattr(message, "content", "")).strip() == "[audio]"
        ):
            deepgram_used = True
        for item in message.media if isinstance(message.media, list) else []:
            if not isinstance(item, dict):
                continue
            locator = item.get("id") or item.get("media_id") or item.get("url")
            if locator:
                pending(provider or "unknown_channel_provider", "media", str(locator))

    # A receipt can be awaiting processing and therefore have no Message row
    # yet. Capture its upstream provider references before clearing the local
    # queue item, otherwise a request arriving during an outage would leave an
    # untracked provider copy.
    deepgram_used = (
        _collect_inbound_receipt_targets(inbound_receipts, pending)
        or deepgram_used
    )

    for visit in visits:
        if visit.calendar_event_id:
            pending("google_calendar", "calendar_event", str(visit.calendar_event_id))

    for offer in offers:
        sign_provider = str(offer.sign_provider or "esign_provider")
        if offer.sign_envelope_id:
            pending(sign_provider, "signature_envelope", str(offer.sign_envelope_id))
        elif offer.sign_url:
            pending(sign_provider, "signature_workflow", str(offer.sign_url))

    for url in _recording_urls(getattr(lead, "extra", {})):
        pending("vapi", "recording", url)

    if runs:
        pending(
            "anthropic",
            "processing_window_manual_review",
            _processing_window(pseudonym, runs),
        )
    if deepgram_used:
        pending(
            "deepgram",
            "transcription_input_manual_review",
            _processing_window(pseudonym, messages),
        )

    # A CRM subscriber may already have accepted a delivery even if it later
    # returned an error. Unattempted rows are deleted locally and never leave
    # the platform; attempted rows need an explicit, tracked controller action.
    for delivery in webhook_deliveries:
        _collect_webhook_delivery_target(
            delivery,
            subscriptions.get(delivery.subscription_id),
            pending,
            lead_id=lead.id,
        )

    for relation in marketplace:
        if relation.lead_id == lead.id and relation.transferred_lead_id:
            pending(
                "selai_marketplace_buyer_controller",
                "transferred_lead_review",
                json.dumps(
                    {
                        "buyer_agency_id": str(relation.buyer_agency_id),
                        "transferred_lead_id": str(relation.transferred_lead_id),
                    },
                    sort_keys=True,
                ),
            )
        elif relation.transferred_lead_id == lead.id:
            pending(
                "selai_marketplace_seller_controller",
                "origin_lead_review",
                json.dumps(
                    {
                        "seller_agency_id": str(relation.seller_agency_id),
                        "origin_lead_id": str(relation.lead_id),
                    },
                    sort_keys=True,
                ),
            )


def _collect_inbound_receipt_targets(receipts: list[Any], pending) -> bool:
    """Collect provider locators from durable receipts; return audio usage."""
    deepgram_used = False
    for receipt in receipts:
        provider = _CHANNEL_PROVIDER.get(str(receipt.channel))
        payload = receipt.payload if isinstance(receipt.payload, dict) else {}
        context = receipt.context if isinstance(receipt.context, dict) else {}
        identity = payload.get("identity")
        if not isinstance(identity, dict):
            identity = {}
        if identity.get("external_thread_id"):
            pending(
                provider or "unknown_channel_provider",
                "conversation",
                str(identity["external_thread_id"]),
            )
        if payload.get("external_message_id"):
            pending(
                provider or "unknown_channel_provider",
                "message",
                str(payload["external_message_id"]),
            )
        # Resend's receiving object id is distinct from the RFC Message-ID.
        # New receipts retain it in encrypted erasure work before their local
        # context is cleared; historical rows still contribute Message-ID.
        if receipt.channel == "email" and context.get("provider_email_id"):
            pending("resend", "received_email", str(context["provider_email_id"]))
        for locator in _media_locators(payload.get("media")):
            pending(provider or "unknown_channel_provider", "media", locator)
        raw = payload.get("raw")
        if isinstance(raw, dict):
            for key in (
                "audio_media_id",
                "media_id",
                "document_id",
                "image_id",
                "video_id",
            ):
                if raw.get(key):
                    pending(
                        provider or "unknown_channel_provider",
                        "media",
                        str(raw[key]),
                    )
            deepgram_used = deepgram_used or bool(raw.get("audio_media_id"))
    return deepgram_used


def _collect_webhook_delivery_target(
    delivery: Any,
    subscription: Any | None,
    pending,
    *,
    lead_id: Any | None = None,
) -> None:
    if not delivery.attempt_count and delivery.delivered_at is None:
        return
    payload = delivery.payload if isinstance(delivery.payload, dict) else {}
    payload_lead_id = lead_id or payload.get("lead_id")
    pending(
        "agency_crm_webhook",
        "subscriber_copy",
        json.dumps(
            {
                "delivery_id": str(delivery.id),
                "lead_id": str(payload_lead_id) if payload_lead_id else None,
                "subscription_id": str(delivery.subscription_id),
                "target_url": (
                    subscription.target_url if subscription is not None else None
                ),
            },
            sort_keys=True,
        ),
    )


def _collect_agency_integration_targets(settings_blob: dict[str, Any], pending) -> None:
    """Queue externally provisioned tenant resources without deleting them."""
    for key, provider, resource_type in (
        ("vapi_assistant_id", "vapi", "assistant"),
        ("vapi_phone_number_id", "vapi", "phone_number"),
        ("elevenlabs_voice_id", "elevenlabs", "voice_clone"),
        ("google_business_agent_name", "google_business", "agent"),
    ):
        if settings_blob.get(key):
            pending(provider, resource_type, str(settings_blob[key]))

    whatsapp_locator = {
        key: settings_blob.get(key)
        for key in ("whatsapp_waba_id", "whatsapp_phone_number_id")
        if settings_blob.get(key)
    }
    if whatsapp_locator:
        pending(
            "meta_whatsapp",
            "business_integration_manual_review",
            json.dumps(whatsapp_locator, sort_keys=True),
        )

    calendar_locator = {
        key: settings_blob.get(key)
        for key in (
            "google_calendar_account_email",
            "google_calendar_id",
            "google_calendar_refresh_token",
        )
        if settings_blob.get(key)
    }
    if calendar_locator:
        pending(
            "google_calendar",
            "oauth_grant_manual_review",
            json.dumps(calendar_locator, sort_keys=True),
        )


def _processing_window(pseudonym: str, rows: list[Any]) -> str:
    timestamps = [
        row.created_at.isoformat()
        for row in rows
        if isinstance(getattr(row, "created_at", None), datetime)
    ]
    return json.dumps(
        {
            "subject_pseudonym": pseudonym,
            "first_seen": min(timestamps) if timestamps else None,
            "last_seen": max(timestamps) if timestamps else None,
        },
        sort_keys=True,
    )


def _recording_urls(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in _RECORDING_KEYS and isinstance(child, str) and child.startswith("http"):
                found.append(child)
            elif isinstance(child, (dict, list)):
                found.extend(_recording_urls(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_recording_urls(child))
    return found


def _media_locators(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    locators: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        locator = item.get("id") or item.get("media_id") or item.get("url")
        if locator:
            locators.append(str(locator))
    return locators


def _subject_identifiers(
    *,
    lead: Lead,
    conversations: list[Any],
    messages: list[Any],
    visits: list[Any],
    offers: list[Any],
) -> set[str]:
    """Strong exact identifiers used to find denormalized queue payloads.

    Free-form names are intentionally excluded: two people can share a name,
    while UUIDs, provider ids and normalized contact coordinates are suitable
    for a tenant-scoped subject match.
    """
    values: list[Any] = [
        lead.id,
        lead.phone_e164,
        lead.email,
        lead.whatsapp_wa_id,
    ]
    for row in conversations:
        values.extend((row.id, row.external_thread_id))
    for row in messages:
        values.extend((row.id, row.external_message_id))
    for row in visits:
        values.extend((row.id, row.calendar_event_id))
    for row in offers:
        values.extend((row.id, row.sign_envelope_id, row.sign_url))
    return {_normalized_identifier(value) for value in values if value}


def _contains_subject_identifier(value: Any, identifiers: set[str]) -> bool:
    if isinstance(value, dict):
        return any(_contains_subject_identifier(child, identifiers) for child in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_subject_identifier(child, identifiers) for child in value)
    return bool(value is not None and _normalized_identifier(value) in identifiers)


def _normalized_identifier(value: Any) -> str:
    return str(value).strip().casefold()


def _artifact_manifest(lead: Lead, related: dict[str, list[Any]]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for offer in related["offers"]:
        if offer.pdf_url:
            artifacts.append({"kind": "offer_pdf", "url": offer.pdf_url, "offer_id": str(offer.id)})
        if offer.sign_url:
            artifacts.append(
                {
                    "kind": "signature_workflow",
                    "url": offer.sign_url,
                    "offer_id": str(offer.id),
                }
            )
    for message in related["messages"]:
        for media in message.media if isinstance(message.media, list) else []:
            artifacts.append(
                {"kind": "message_media", "message_id": str(message.id), "media": media}
            )
    for url in _recording_urls(getattr(lead, "extra", {})):
        artifacts.append({"kind": "voice_recording", "url": url, "source": "lead"})
    for conversation in related["conversations"]:
        for url in _recording_urls(conversation.extra):
            artifacts.append(
                {
                    "kind": "voice_recording",
                    "url": url,
                    "source": "conversation",
                    "conversation_id": str(conversation.id),
                }
            )
    return artifacts


def _serialize_marketplace_relation(row: LeadOffer) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "seller_agency_id": str(row.seller_agency_id),
        "buyer_agency_id": str(row.buyer_agency_id) if row.buyer_agency_id else None,
        "lead_id": str(row.lead_id),
        "transferred_lead_id": (str(row.transferred_lead_id) if row.transferred_lead_id else None),
        "status": _enum_value(row.status),
        "claimed_at": _iso(row.claimed_at),
        "created_at": _iso(row.created_at),
    }


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _serialize(obj: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col in obj.__table__.columns:
        value = getattr(obj, col.name)
        if isinstance(value, datetime):
            value = value.isoformat()
        elif isinstance(value, uuid.UUID):
            value = str(value)
        elif hasattr(value, "value"):
            value = value.value
        out[col.name] = value
    return out
