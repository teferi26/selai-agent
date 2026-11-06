"""End-to-end GDPR export/erasure over every lead-owned subsystem.

The production models run against an in-memory SQL database through a tiny
async facade.  This exercises the real SQL statements and model constraints,
not a session fake that returns whichever rows the test expects.
"""

from __future__ import annotations

import sys
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import JSON, create_engine, select
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.db.models import (
    AgentRun,
    ConsentAction,
    ConsentLog,
    ConsentScope,
    Conversation,
    ConversationChannel,
    ConversationStatus,
    ExternalErasureRequest,
    InboundOutbox,
    Lead,
    LeadOffer,
    LeadOfferStatus,
    Message,
    MessageDirection,
    MessageRole,
    Offer,
    OfferStatus,
    Visit,
    VisitStatus,
    WebhookOutbox,
    WebhookSubscription,
)
from app.services import gdpr, storage
from app.services.gdpr_erasure import (
    confirm_external_erasure,
    queue_external_erasure,
    record_external_erasure_failure,
    subject_pseudonym,
)
from app.services.secret_store import decrypt_secret


class AsyncSessionFacade:
    def __init__(self, session: Session):
        self.sync = session

    async def get(self, model, key):
        return self.sync.get(model, key)

    async def execute(self, statement):
        return self.sync.execute(statement)

    def add(self, value):
        self.sync.add(value)

    async def flush(self):
        self.sync.flush()

    async def commit(self):
        self.sync.commit()


TABLES = [
    Lead.__table__,
    Conversation.__table__,
    Message.__table__,
    Visit.__table__,
    ConsentLog.__table__,
    Offer.__table__,
    LeadOffer.__table__,
    AgentRun.__table__,
    ExternalErasureRequest.__table__,
    InboundOutbox.__table__,
    WebhookSubscription.__table__,
    WebhookOutbox.__table__,
]


@pytest.fixture
def sqlite_queue_types():
    """Use portable JSON only while this production-model fixture is active."""
    columns = (
        InboundOutbox.__table__.c.payload,
        InboundOutbox.__table__.c.context,
        WebhookOutbox.__table__.c.payload,
        WebhookSubscription.__table__.c.events,
    )
    original_types = [column.type for column in columns]
    for column in columns:
        column.type = JSON()
    yield
    for column, original_type in zip(columns, original_types, strict=True):
        column.type = original_type


@pytest.fixture
def subject_db(tmp_path, monkeypatch, sqlite_queue_types):
    engine = create_engine("sqlite://")
    Lead.metadata.create_all(engine, tables=TABLES)
    sync = Session(engine, expire_on_commit=False)
    db = AsyncSessionFacade(sync)

    agency_id = uuid.uuid4()
    other_agency_id = uuid.uuid4()
    lead_id = uuid.uuid4()
    other_lead_id = uuid.uuid4()
    property_id = uuid.uuid4()
    now = datetime.now(UTC)

    lead = Lead(
        id=lead_id,
        agency_id=agency_id,
        full_name="Ana Datos Personales",
        phone_e164="+34600111222",
        email="ana@example.test",
        whatsapp_wa_id="34600111222",
        qualification_notes="Busca casa por divorcio",
        extra={"last_call_recording_url": "https://vapi.example/recordings/call-1"},
    )
    other_lead = Lead(
        id=other_lead_id,
        agency_id=other_agency_id,
        full_name="Persona que debe quedar",
        email="keep@example.test",
    )
    conversation = Conversation(
        id=uuid.uuid4(),
        agency_id=agency_id,
        lead_id=lead_id,
        channel=ConversationChannel.WHATSAPP,
        status=ConversationStatus.ACTIVE,
        external_thread_id="wa-thread-personal",
        extra={"recording_url": "https://vapi.example/recordings/conversation-1"},
    )
    other_conversation = Conversation(
        id=uuid.uuid4(),
        agency_id=other_agency_id,
        lead_id=other_lead_id,
        channel=ConversationChannel.EMAIL,
        status=ConversationStatus.ACTIVE,
        external_thread_id="keep-thread",
    )
    message = Message(
        id=uuid.uuid4(),
        agency_id=agency_id,
        conversation_id=conversation.id,
        direction=MessageDirection.INBOUND,
        role=MessageRole.LEAD,
        content="[audio]",
        raw={"audio_media_id": "wa-media-personal"},
        external_message_id="wamid-personal",
        media=[{"url": "https://meta.example/media/personal"}],
    )
    other_message = Message(
        id=uuid.uuid4(),
        agency_id=other_agency_id,
        conversation_id=other_conversation.id,
        direction=MessageDirection.INBOUND,
        role=MessageRole.LEAD,
        content="keep",
        raw={},
        media=[],
    )
    visit = Visit(
        id=uuid.uuid4(),
        agency_id=agency_id,
        lead_id=lead_id,
        property_id=property_id,
        scheduled_at=now + timedelta(days=1),
        status=VisitStatus.CONFIRMED,
        calendar_event_id="google-event-personal",
        pre_visit_briefing="Divorcio, tratar con tacto",
        post_visit_notes="Le gustó",
    )
    consent = ConsentLog(
        id=uuid.uuid4(),
        agency_id=agency_id,
        lead_id=lead_id,
        scope=ConsentScope.MARKETING,
        action=ConsentAction.GRANTED,
        source="whatsapp",
        evidence="Sí, Ana acepta",
        ip_address="203.0.113.55",
        user_agent="Personal Browser",
    )
    offer = Offer(
        id=uuid.uuid4(),
        agency_id=agency_id,
        lead_id=lead_id,
        property_id=property_id,
        amount_eur=250_000,
        operation="sale",
        conditions="Contiene datos financieros personales",
        status=OfferStatus.SENT,
        pdf_url=f"https://api.selai.es/files/agencies/{agency_id}/offers/personal.pdf",
        sign_url="https://sign.example/workflow/personal",
        sign_provider="signaturit",
        sign_envelope_id="envelope-personal",
    )
    marketplace = LeadOffer(
        id=uuid.uuid4(),
        seller_agency_id=agency_id,
        lead_id=lead_id,
        preview={"zone": "Alicante"},
        asking_fee_eur=500,
        commission_pct=10,
        notes="nota comercial privada",
        expires_at=now + timedelta(days=7),
        status=LeadOfferStatus.CLAIMED,
        buyer_agency_id=other_agency_id,
        transferred_lead_id=uuid.uuid4(),
        redemption_token="redemption-secret-personal",
    )
    run = AgentRun(
        id=uuid.uuid4(),
        agency_id=agency_id,
        lead_id=lead_id,
        conversation_id=conversation.id,
        model="claude-test",
        output_text="Ana se divorcia",
        tool_calls=[{"lead_email": "ana@example.test"}],
    )
    webhook_subscription = WebhookSubscription(
        id=uuid.uuid4(),
        agency_id=agency_id,
        target_url="https://crm.example.test/selai",
        events=["lead.created"],
        secret="must-never-be-exported",
    )
    inbound_receipt = InboundOutbox(
        id=uuid.uuid4(),
        agency_id=agency_id,
        channel="whatsapp",
        idempotency_key="subject-inbound-receipt",
        payload={
            "identity": {
                "email": "ana@example.test",
                "external_thread_id": "queued-thread-personal",
            },
            "text": "Mensaje personal aún en cola",
            "external_message_id": "wamid-personal",
            "media": [{"id": "queued-media-personal"}],
            "raw": {"audio_media_id": "queued-audio-personal"},
        },
        context={"phone_number_id": "business-phone-id"},
        available_at=now,
    )
    keep_inbound_receipt = InboundOutbox(
        id=uuid.uuid4(),
        agency_id=agency_id,
        channel="email",
        idempotency_key="same-tenant-decoy-receipt",
        payload={"identity": {"email": "keep@example.test"}, "text": "keep"},
        context={"provider_email_id": "resend-received-decoy"},
        available_at=now,
    )
    foreign_inbound_receipt = InboundOutbox(
        id=uuid.uuid4(),
        agency_id=other_agency_id,
        channel="email",
        idempotency_key="foreign-tenant-same-identifier",
        payload={"identity": {"email": "ana@example.test"}, "text": "foreign"},
        context={},
        available_at=now,
    )
    webhook_delivery = WebhookOutbox(
        id=uuid.uuid4(),
        subscription_id=webhook_subscription.id,
        agency_id=agency_id,
        event="lead.created",
        payload={"lead_id": str(lead_id), "email": "ana@example.test"},
        dedup_key="subject-webhook-delivery",
        attempt_count=1,
        available_at=now,
    )
    keep_webhook_delivery = WebhookOutbox(
        id=uuid.uuid4(),
        subscription_id=webhook_subscription.id,
        agency_id=agency_id,
        event="lead.created",
        payload={"lead_id": str(uuid.uuid4()), "email": "keep@example.test"},
        dedup_key="same-tenant-decoy-webhook",
        available_at=now,
    )
    foreign_webhook_delivery = WebhookOutbox(
        id=uuid.uuid4(),
        subscription_id=uuid.uuid4(),
        agency_id=other_agency_id,
        event="lead.created",
        payload={"lead_id": str(lead_id), "email": "ana@example.test"},
        dedup_key="foreign-tenant-same-identifier",
        available_at=now,
    )

    sync.add_all(
        [
            lead,
            other_lead,
            conversation,
            other_conversation,
            message,
            other_message,
            visit,
            consent,
            offer,
            marketplace,
            run,
            webhook_subscription,
            inbound_receipt,
            keep_inbound_receipt,
            foreign_inbound_receipt,
            webhook_delivery,
            keep_webhook_delivery,
            foreign_webhook_delivery,
        ]
    )
    sync.flush()

    pseudonym = subject_pseudonym(agency_id, lead_id)
    queue_external_erasure(
        db,
        agency_id=agency_id,
        pseudonym=pseudonym,
        provider="preexisting_provider",
        resource_type="preexisting_resource",
        locator="preexisting-personal-locator",
    )
    sync.commit()

    monkeypatch.setattr(settings, "s3_bucket", "")
    monkeypatch.setattr(storage, "LOCAL_ROOT", tmp_path.resolve())
    monkeypatch.setattr(storage, "_s3_enabled", lambda: False)
    offer_file = tmp_path / "agencies" / str(agency_id) / "offers" / f"{offer.id}.pdf"
    card_file = tmp_path / "agencies" / str(agency_id) / "cards" / f"{lead_id}_{property_id}.pdf"
    keep_card = (
        tmp_path / "agencies" / str(agency_id) / "cards" / f"{other_lead_id}_{property_id}.pdf"
    )
    other_tenant_file = tmp_path / "agencies" / str(other_agency_id) / "offers" / "keep.pdf"
    for path in (offer_file, card_file, keep_card, other_tenant_file):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode())

    yield {
        "db": db,
        "sync": sync,
        "agency_id": agency_id,
        "other_agency_id": other_agency_id,
        "lead": lead,
        "other_lead": other_lead,
        "conversation": conversation,
        "message": message,
        "visit": visit,
        "consent": consent,
        "offer": offer,
        "marketplace": marketplace,
        "run": run,
        "inbound_receipt": inbound_receipt,
        "keep_inbound_receipt": keep_inbound_receipt,
        "foreign_inbound_receipt": foreign_inbound_receipt,
        "webhook_delivery": webhook_delivery,
        "keep_webhook_delivery": keep_webhook_delivery,
        "foreign_webhook_delivery": foreign_webhook_delivery,
        "pseudonym": pseudonym,
        "offer_file": offer_file,
        "card_file": card_file,
        "keep_card": keep_card,
        "other_tenant_file": other_tenant_file,
    }
    sync.close()


async def test_export_contains_every_subject_subsystem_without_marketplace_secrets(subject_db):
    ctx = subject_db
    exported = await gdpr.export_lead_data(ctx["db"], ctx["agency_id"], ctx["lead"].id)

    assert exported["lead"]["email"] == "ana@example.test"
    assert {row["id"] for row in exported["conversations"]} == {str(ctx["conversation"].id)}
    assert {row["id"] for row in exported["messages"]} == {str(ctx["message"].id)}
    assert {row["id"] for row in exported["visits"]} == {str(ctx["visit"].id)}
    assert {row["id"] for row in exported["consents"]} == {str(ctx["consent"].id)}
    assert {row["id"] for row in exported["offers"]} == {str(ctx["offer"].id)}
    assert {row["id"] for row in exported["agent_runs"]} == {str(ctx["run"].id)}
    assert {row["id"] for row in exported["inbound_receipts"]} == {str(ctx["inbound_receipt"].id)}
    assert exported["inbound_receipts"][0]["payload"]["identity"]["email"] == ("ana@example.test")
    assert {row["id"] for row in exported["webhook_deliveries"]} == {
        str(ctx["webhook_delivery"].id)
    }
    assert "must-never-be-exported" not in str(exported)
    assert len(exported["marketplace_relations"]) == 1
    marketplace_json = str(exported["marketplace_relations"])
    assert "redemption-secret-personal" not in marketplace_json
    assert "nota comercial privada" not in marketplace_json
    assert {artifact["kind"] for artifact in exported["artifacts"]} >= {
        "offer_pdf",
        "signature_workflow",
        "message_media",
        "voice_recording",
    }
    external = exported["external_erasure_requests"]
    assert external == [
        {
            "provider": "preexisting_provider",
            "resource_type": "preexisting_resource",
            "status": "pending",
            "attempts": 0,
            "created_at": external[0]["created_at"],
            "completed_at": None,
        }
    ]
    assert "locator" not in str(external).lower()


async def test_agency_purge_inventory_queues_all_locatable_external_copies(subject_db):
    ctx = subject_db
    agency = SimpleNamespace(
        id=ctx["agency_id"],
        settings={
            "vapi_assistant_id": "assistant-personal",
            "elevenlabs_voice_id": "voice-clone-personal",
            "whatsapp_waba_id": "waba-personal",
            "google_calendar_id": "calendar-personal",
            "google_calendar_account_email": "owner-personal@example.test",
        },
    )

    created = await gdpr.queue_agency_external_erasures(ctx["db"], agency=agency)
    await ctx["db"].commit()

    requests = ctx["sync"].scalars(
        select(ExternalErasureRequest).where(
            ExternalErasureRequest.agency_id == ctx["agency_id"]
        )
    ).all()
    assert created == len(requests) - 1  # one target was queued by the fixture
    assert {row.provider for row in requests} >= {
        "meta_whatsapp",
        "resend",
        "vapi",
        "google_calendar",
        "signaturit",
        "anthropic",
        "deepgram",
        "elevenlabs",
        "agency_crm_webhook",
        "selai_marketplace_buyer_controller",
    }
    decrypted = {decrypt_secret(row.locator_encrypted) for row in requests}
    assert {
        "queued-thread-personal",
        "queued-media-personal",
        "queued-audio-personal",
        "resend-received-decoy",
        "assistant-personal",
        "voice-clone-personal",
        "google-event-personal",
        "envelope-personal",
    } <= decrypted
    assert "keep-thread" not in str(decrypted)
    assert "foreign" not in str(decrypted)
    assert all(row.status == "pending" and row.completed_at is None for row in requests)

    assert await gdpr.queue_agency_external_erasures(ctx["db"], agency=agency) == 0


async def test_erasure_removes_local_data_and_keeps_only_minimal_pseudonymous_evidence(
    subject_db,
):
    ctx = subject_db
    result = await gdpr.erase_lead(
        ctx["db"],
        ctx["agency_id"],
        ctx["lead"].id,
        requested_by="Ana Datos Personales",
        reason="Texto libre con ana@example.test",
    )
    await ctx["db"].commit()

    assert result is not None and result.local_erasure_completed is True
    assert result.managed_artifacts_deleted == 2
    sync = ctx["sync"]
    for model, identifier in (
        (Lead, ctx["lead"].id),
        (Conversation, ctx["conversation"].id),
        (Message, ctx["message"].id),
        (Visit, ctx["visit"].id),
        (Offer, ctx["offer"].id),
        (LeadOffer, ctx["marketplace"].id),
        (AgentRun, ctx["run"].id),
        (InboundOutbox, ctx["inbound_receipt"].id),
        (WebhookOutbox, ctx["webhook_delivery"].id),
    ):
        assert sync.get(model, identifier) is None, model.__name__

    # Decoy subject and both same-tenant/other-tenant artifacts survive.
    assert sync.get(Lead, ctx["other_lead"].id) is not None
    assert sync.get(InboundOutbox, ctx["keep_inbound_receipt"].id) is not None
    assert sync.get(InboundOutbox, ctx["foreign_inbound_receipt"].id) is not None
    assert sync.get(WebhookOutbox, ctx["keep_webhook_delivery"].id) is not None
    assert sync.get(WebhookOutbox, ctx["foreign_webhook_delivery"].id) is not None
    assert not ctx["offer_file"].exists()
    assert not ctx["card_file"].exists()
    assert ctx["keep_card"].exists()
    assert ctx["other_tenant_file"].exists()

    evidence = sync.scalars(
        select(ConsentLog).where(
            ConsentLog.agency_id == ctx["agency_id"],
            ConsentLog.subject_pseudonym == ctx["pseudonym"],
        )
    ).all()
    assert len(evidence) == 2  # minimized original + final erasure event
    assert {row.source for row in evidence} == {
        "retained_legal_evidence",
        "gdpr_erasure",
    }
    for row in evidence:
        assert row.lead_id is None and row.user_id is None
        assert row.evidence == ""
        assert row.ip_address is None and row.user_agent is None
        assert row.subject_pseudonym.startswith("lead_")
    evidence_text = str([row.__dict__ for row in evidence])
    assert "Ana Datos Personales" not in evidence_text
    assert "ana@example.test" not in evidence_text

    requests = sync.scalars(
        select(ExternalErasureRequest).where(
            ExternalErasureRequest.subject_pseudonym == ctx["pseudonym"]
        )
    ).all()
    providers = {row.provider for row in requests}
    assert providers >= {
        "preexisting_provider",
        "meta_whatsapp",
        "vapi",
        "google_calendar",
        "signaturit",
        "anthropic",
        "deepgram",
        "selai_marketplace_buyer_controller",
        "agency_crm_webhook",
    }
    assert result.external_erasures_pending == len(requests)
    for row in requests:
        assert row.status == "pending"
        assert row.completed_at is None
        assert row.locator_encrypted.startswith("enc:v2:")
        assert "personal" not in row.locator_encrypted
    decrypted = [(row, decrypt_secret(row.locator_encrypted)) for row in requests]
    locators = {locator for _, locator in decrypted}
    assert {
        "queued-thread-personal",
        "queued-media-personal",
        "queued-audio-personal",
    } <= locators
    crm_locators = [locator for row, locator in decrypted if row.provider == "agency_crm_webhook"]
    assert len(crm_locators) == 1
    assert "https://crm.example.test/selai" in crm_locators[0]
    assert "must-never-be-exported" not in str(locators)
    assert "same-tenant-decoy" not in str(locators)

    status = await gdpr.erasure_status(ctx["db"], ctx["agency_id"], ctx["pseudonym"])
    assert status is not None
    assert status["local_erasure_completed"] is True
    assert status["external_erasure_status"] == "pending"
    assert status["external_erasures_pending"] == len(requests)
    assert "locator" not in str(status).lower()


async def test_managed_storage_failure_becomes_pending_not_false_success(subject_db, monkeypatch):
    ctx = subject_db

    def _storage_down(*args, **kwargs):
        raise RuntimeError("storage unavailable with personal locator")

    monkeypatch.setattr(gdpr, "delete_tenant_key_prefix", _storage_down)
    result = await gdpr.erase_lead(ctx["db"], ctx["agency_id"], ctx["lead"].id)
    await ctx["db"].commit()

    assert result is not None
    storage_requests = (
        ctx["sync"]
        .scalars(
            select(ExternalErasureRequest).where(
                ExternalErasureRequest.subject_pseudonym == ctx["pseudonym"],
                ExternalErasureRequest.provider == "local_storage",
            )
        )
        .all()
    )
    assert len(storage_requests) == 2
    assert all(row.status == "pending" for row in storage_requests)
    assert ctx["offer_file"].exists() and ctx["card_file"].exists()


async def test_s3_partial_delete_error_becomes_pending_erasure(subject_db, monkeypatch):
    ctx = subject_db

    class PartialDeleteClient:
        def list_objects_v2(self, **kwargs):
            return {
                "Contents": [{"Key": f"{kwargs['Prefix']}personal.pdf"}],
                "IsTruncated": False,
            }

        def delete_objects(self, **_kwargs):
            return {
                "Errors": [
                    {
                        "Key": "must-not-be-logged.pdf",
                        "Code": "AccessDenied",
                        "Message": "denied",
                    }
                ]
            }

    client = PartialDeleteClient()
    monkeypatch.setitem(
        sys.modules,
        "boto3",
        SimpleNamespace(client=lambda *_args, **_kwargs: client),
    )
    monkeypatch.setattr(settings, "s3_bucket", "private-gdpr-bucket")
    monkeypatch.setattr(storage, "_s3_enabled", lambda: True)

    result = await gdpr.erase_lead(ctx["db"], ctx["agency_id"], ctx["lead"].id)
    await ctx["db"].commit()

    assert result is not None
    storage_requests = (
        ctx["sync"]
        .scalars(
            select(ExternalErasureRequest).where(
                ExternalErasureRequest.subject_pseudonym == ctx["pseudonym"],
                ExternalErasureRequest.provider == "s3",
            )
        )
        .all()
    )
    assert len(storage_requests) == 2
    assert all(row.status == "pending" for row in storage_requests)


def test_external_work_needs_positive_confirmation_and_never_stores_raw_errors(subject_db):
    ctx = subject_db
    request = ctx["sync"].scalars(select(ExternalErasureRequest)).first()
    record_external_erasure_failure(
        request,
        error_code="provider_timeout",
    )
    assert request.status == "pending"
    assert request.attempts == 1
    assert request.last_error == "provider_timeout"

    with pytest.raises(ValueError, match="confirmation"):
        confirm_external_erasure(request, provider_confirmation="")
    assert request.status == "pending"

    confirm_external_erasure(request, provider_confirmation="provider-ticket-personal")
    assert request.status == "completed"
    assert request.completed_at is not None
    assert request.confirmation_digest is not None
    assert "provider-ticket-personal" not in request.confirmation_digest


def test_consent_fk_and_check_allow_only_pseudonymous_post_erasure_evidence():
    lead_fk = next(iter(ConsentLog.__table__.c.lead_id.foreign_keys))
    assert lead_fk.ondelete == "SET NULL"
    checks = {
        str(constraint.sqltext)
        for constraint in ConsentLog.__table__.constraints
        if hasattr(constraint, "sqltext")
    }
    assert any("subject_pseudonym IS NOT NULL" in check for check in checks)


def test_key_prefix_deletion_is_tenant_bound(tmp_path, monkeypatch):
    agency = uuid.uuid4()
    other = uuid.uuid4()
    monkeypatch.setattr(storage, "LOCAL_ROOT", tmp_path.resolve())
    monkeypatch.setattr(storage, "_s3_enabled", lambda: False)
    own = tmp_path / "agencies" / str(agency) / "cards"
    foreign = tmp_path / "agencies" / str(other) / "cards"
    own.mkdir(parents=True)
    foreign.mkdir(parents=True)
    (own / "lead_a.pdf").write_bytes(b"a")
    (own / "other.pdf").write_bytes(b"other")
    (foreign / "lead_a.pdf").write_bytes(b"foreign")

    assert storage.delete_tenant_key_prefix(agency, "cards/lead_") == 1
    assert not (own / "lead_a.pdf").exists()
    assert (own / "other.pdf").exists()
    assert (foreign / "lead_a.pdf").exists()
    with pytest.raises(ValueError):
        storage.delete_tenant_key_prefix(agency, "../")
