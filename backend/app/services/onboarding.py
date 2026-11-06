"""Self-service onboarding: create agency + owner user + Vapi assistant
in one shot.

Called from POST /api/v1/onboarding. The agency starts on a 14-day trial
of the default Plan; billing kicks in afterwards.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.settings import settings
from app.db.models import (
    Agency,
    ConsentAction,
    ConsentLog,
    ConsentScope,
    Plan,
    Subscription,
    SubscriptionStatus,
    User,
    UserRole,
)
from app.services.legal_documents import (
    KNOWN_SLUGS,
    consent_evidence,
    current_versions,
    load_document,
)

log = get_logger(__name__)


@dataclass
class OnboardingResult:
    agency_id: uuid.UUID
    user_id: uuid.UUID
    vapi_assistant_id: str | None
    trial_ends_at: datetime


# The sentence stored in `consent_logs.evidence` when the caller does not send
# the exact checkbox text the owner read. Named rather than inlined because it
# is a legal claim about what was agreed: a copy of it in a test (or in a
# second caller) that drifts from this one is a copy that lies.
DEFAULT_CONSENT_TEXT = (
    "He leído y acepto los Términos de Servicio, el Acuerdo de Encargo de "
    "Tratamiento y la Política de Privacidad."
)


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:80] or f"agency-{uuid.uuid4().hex[:6]}"


async def onboard_agency(
    db: AsyncSession,
    *,
    agency_name: str,
    owner_email: str,
    owner_full_name: str,
    owner_phone: str | None,
    clerk_user_id: str | None = None,
    timezone_name: str = "Europe/Madrid",
    default_language: str = "es",
    plan_code: str | None = None,
    accepted_versions: dict[str, str] | None = None,
    accepted_hashes: dict[str, str] | None = None,
    marketing_opt_in: bool = False,
    request_ip: str | None = None,
    request_ua: str | None = None,
    accepted_text: str | None = None,
) -> OnboardingResult:
    base_slug = _slugify(agency_name)
    slug = base_slug
    suffix = 1
    while (await db.execute(select(Agency).where(Agency.slug == slug))).scalar_one_or_none():
        suffix += 1
        slug = f"{base_slug}-{suffix}"

    agency = Agency(
        name=agency_name,
        slug=slug,
        contact_email=owner_email,
        contact_phone=owner_phone,
        timezone=timezone_name,
        default_language=default_language,
        settings={},
    )
    db.add(agency)
    await db.flush()

    user = User(
        agency_id=agency.id,
        email=owner_email,
        full_name=owner_full_name,
        role=UserRole.OWNER,
        phone=owner_phone,
        clerk_user_id=clerk_user_id,
    )
    db.add(user)
    await db.flush()  # need user.id to attach consent logs

    # Owner consents (RGPD/B2B). The endpoint validated the checkboxes are
    # all true and the versions match `current_versions()`; here we just
    # persist the audit trail. `marketing_opt_in` is optional.
    versions = accepted_versions or current_versions()
    hashes = accepted_hashes or {
        document_slug: load_document(document_slug, version).hash
        for document_slug, version in versions.items()
        if document_slug in KNOWN_SLUGS
    }
    accepted_label = accepted_text or DEFAULT_CONSENT_TEXT
    scope_for_slug = {
        "terms_of_service": ConsentScope.TERMS_OF_SERVICE,
        "dpa": ConsentScope.DPA,
        "privacy_policy": ConsentScope.PRIVACY_POLICY,
    }
    # NOTE: `document_slug`, not `slug`. This loop used to reuse the `slug`
    # name and left it bound to the last legal document ("privacy_policy"),
    # so every Vapi assistant below was created with
    # `metadata={"slug": "privacy_policy"}` instead of the agency's slug —
    # which is exactly the metadata the voice webhooks route on.
    for document_slug in KNOWN_SLUGS:
        if document_slug not in scope_for_slug:
            continue
        version = versions.get(document_slug)
        if not version:
            continue
        db.add(
            ConsentLog(
                agency_id=agency.id,
                user_id=user.id,
                lead_id=None,
                scope=scope_for_slug[document_slug],
                action=ConsentAction.GRANTED,
                source="onboarding",
                evidence=consent_evidence(
                    slug=document_slug,
                    version=version,
                    expected_hash=hashes.get(document_slug, ""),
                    user_agent=request_ua,
                    accepted_text=accepted_label,
                ),
                ip_address=request_ip,
                user_agent=request_ua,
                document_version=version,
            )
        )
    # Also persist DATA_PROCESSING for the owner so the marketplace gating
    # and any future "right to be forgotten" flow have an explicit record.
    db.add(
        ConsentLog(
            agency_id=agency.id,
            user_id=user.id,
            lead_id=None,
            scope=ConsentScope.DATA_PROCESSING,
            action=ConsentAction.GRANTED,
            source="onboarding",
            evidence=accepted_label,
            ip_address=request_ip,
            user_agent=request_ua,
        )
    )
    if marketing_opt_in:
        db.add(
            ConsentLog(
                agency_id=agency.id,
                user_id=user.id,
                lead_id=None,
                scope=ConsentScope.MARKETING,
                action=ConsentAction.GRANTED,
                source="onboarding",
                evidence="Marketing opt-in marcado en el formulario de signup.",
                ip_address=request_ip,
                user_agent=request_ua,
            )
        )

    plan = None
    if plan_code:
        plan = (await db.execute(select(Plan).where(Plan.code == plan_code))).scalar_one_or_none()
    if plan is None:
        # scalars().first(): several plans are active in production, so
        # scalar_one_or_none() would raise MultipleResultsFound.
        plan = (
            (
                await db.execute(
                    select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.monthly_price_eur)
                )
            )
            .scalars()
            .first()
        )

    trial_ends = datetime.now(UTC) + timedelta(days=14)
    if plan is not None:
        db.add(
            Subscription(
                agency_id=agency.id,
                plan_id=plan.id,
                status=SubscriptionStatus.TRIALING,
                trial_ends_at=trial_ends,
            )
        )

    assistant_id: str | None = None
    try:
        assistant_id = await _provision_vapi_assistant(
            agency_name=agency_name,
            slug=slug,
            agency_id=agency.id,
            recording_notice=(agency.settings or {}).get("recording_notice", True) is not False,
        )
        if assistant_id:
            agency.settings = {**(agency.settings or {}), "vapi_assistant_id": assistant_id}
    except Exception:
        log.exception("onboarding.vapi_provision_failed", agency=slug)

    await db.flush()
    return OnboardingResult(
        agency_id=agency.id,
        user_id=user.id,
        vapi_assistant_id=assistant_id,
        trial_ends_at=trial_ends,
    )


RECORDING_NOTICE_PREFIX = (
    "Le informamos de que esta llamada puede ser grabada para mejorar el servicio. "
)


def build_first_message(agency_name: str, *, recording_notice: bool = True) -> str:
    """Vapi assistant greeting. Pure — unit-tested.

    (50) Unless the agency explicitly set `settings["recording_notice"]`
    to false, the greeting starts with the legally-required recording notice.
    """
    first = f"Hola, soy el asistente virtual de {agency_name}. ¿En qué te puedo ayudar?"
    if recording_notice:
        first = RECORDING_NOTICE_PREFIX + first
    return first


async def sync_vapi_first_message(
    assistant_id: str,
    *,
    agency_name: str,
    recording_notice: bool,
) -> bool:
    """Apply the current recording notice to an existing Vapi assistant.

    Settings must only claim the notice is active after Vapi has accepted the
    matching greeting.  Returning a boolean lets the settings endpoint fail
    closed without exposing provider details to the customer.
    """
    api_key = settings.vapi_api_key.get_secret_value()
    if not api_key or not assistant_id:
        log.error(
            "vapi.first_message.sync_unavailable",
            assistant=assistant_id or None,
            api_key_configured=bool(api_key),
        )
        return False

    expected_message = build_first_message(
        agency_name,
        recording_notice=recording_notice,
    )
    payload = {"firstMessage": expected_message}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.patch(
                f"https://api.vapi.ai/assistant/{assistant_id}",
                json=payload,
                headers=headers,
            )
    except Exception:
        log.exception("vapi.first_message.sync_error", assistant=assistant_id)
        return False

    if resp.status_code >= 400:
        log.error(
            "vapi.first_message.sync_failed",
            assistant=assistant_id,
            status=resp.status_code,
            body=resp.text[:200],
        )
        return False
    try:
        saved_message = resp.json().get("firstMessage")
    except (AttributeError, TypeError, ValueError):
        saved_message = None
    if saved_message != expected_message:
        log.error(
            "vapi.first_message.sync_not_persisted",
            assistant=assistant_id,
        )
        return False
    return True


async def _provision_vapi_assistant(
    *,
    agency_name: str,
    slug: str,
    agency_id: uuid.UUID | str | None = None,
    recording_notice: bool = True,
) -> str | None:
    # NOTE(50): callers pass the agency's `settings["recording_notice"]` at
    # provisioning time. Existing assistants are kept in sync by
    # `PATCH /settings` through `sync_vapi_first_message` above.
    api_key = settings.vapi_api_key.get_secret_value()
    if not api_key:
        return None

    public_base = settings.public_base_url
    # The assistant's metadata is the only thing Vapi echoes back on an
    # *inbound* call, and it is what `_resolve_agency` (app/api/voice.py)
    # routes on. Without `agency_id` there, an inbound call to a multi-tenant
    # deployment resolved to nobody and was dropped without a trace.
    # `agency_id` is optional only because one caller (app/api/channels.py,
    # `_ensure_vapi_assistant`) still does not pass it; that is a bug, so it
    # is logged instead of being silently accepted.
    metadata: dict[str, Any] = {"slug": slug}
    if agency_id:
        metadata["agency_id"] = str(agency_id)
    else:
        log.warning("vapi.create_assistant.without_agency_id", slug=slug)
    payload: dict[str, Any] = {
        "name": f"Realtor AI — {agency_name}",
        "model": {
            "provider": "custom-llm",
            "url": f"{public_base.rstrip('/')}/voice/llm",
            "model": "realtor-ai-voice",
        },
        "voice": {"provider": "cartesia", "voiceId": "sonic-multilingual"},
        # (n32) Igual que en transcription.py: sin idioma, nova-3 transcribe
        # como si la llamada fuera en inglés. Aquí duele más — es el teléfono
        # de la agencia, en directo.
        "transcriber": {"provider": "deepgram", "model": "nova-3", "language": "multi"},
        "serverUrl": f"{public_base.rstrip('/')}/webhooks/voice",
        "firstMessage": build_first_message(agency_name, recording_notice=recording_notice),
        "metadata": metadata,
    }

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.post("https://api.vapi.ai/assistant", json=payload, headers=headers)
    if resp.status_code >= 400:
        log.error("vapi.create_assistant.error", status=resp.status_code, body=resp.text[:200])
        resp.raise_for_status()
    return resp.json().get("id")
