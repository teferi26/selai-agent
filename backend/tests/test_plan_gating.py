"""Plan gating: the features the pricing page sells per plan are actually
enforced.

Until now the flags in `seed_plans.py` were decoration almost everywhere —
`ensure_feature` was called from exactly one endpoint — so a Starter (99 €)
used voice, SMS, templates, PDF offers and the marketplace, all sold from
Pro (199 €) and Agencia (399 €) up, and selai paid the provider bills.

Three things are checked for every gated endpoint:

  * a Starter is refused with 403 and the machine-readable code the panel
    turns into an upgrade prompt,
  * a plan that does include the feature gets through,
  * an agency with **no subscription row** (dev environments, seeded demo
    data) is never blocked.

Plus the two cases where the money leaks by degrees rather than in one go:
the CSV import counted against the properties limit once instead of per row,
and inbound voice was not metered at all.

The plan fixtures are built from the real `PLANS` list in seed_plans.py, so
flipping a flag there (say `sms: True` on Starter) fails these tests instead
of silently giving the feature away.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import benchmarking as benchmarking_module
from app.api import sms as sms_module
from app.api import voice as voice_module
from app.core.settings import settings
from app.db.models import PropertyType, SubscriptionStatus
from app.db.session import get_db
from app.main import app
from app.scripts.seed_plans import PLANS
from app.services import plan_limits
from app.services.plan_limits import TRIAL_PLAN_CODE

AGENCY_ID = uuid.uuid4()
OTHER_AGENCY_ID = uuid.uuid4()

IN_TRIAL = datetime.now(UTC) + timedelta(days=9)
TRIAL_OVER = datetime.now(UTC) - timedelta(minutes=5)

_SPECS = {spec["code"]: spec for spec in PLANS}


def _plan(code: str) -> SimpleNamespace:
    """A stand-in for the `Plan` row seeded for `code`, with the real flags."""
    return SimpleNamespace(id=uuid.uuid4(), **_SPECS[code])


# ---------------------------------------------------------------------------
# Fake session: answers the queries the gates (and the gated endpoints) make
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, *, scalar=None, rows=()):
        self._scalar = scalar
        self._rows = list(rows)

    def scalar_one_or_none(self):
        return self._scalar

    def scalar_one(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class PlanSession:
    """The agency's subscription + the Plan it points at, which is all any
    `plan_limits` gate reads, plus the few rows the gated endpoints need."""

    def __init__(
        self,
        plan: SimpleNamespace | None,
        *,
        status: SubscriptionStatus = SubscriptionStatus.ACTIVE,
        voice_minutes_used: int = 0,
        properties_used: int = 0,
        agents_used: int = 0,
        existing_properties=(),
        consent=None,
        agency=None,
        lead=None,
        trial_ends_at: datetime | None = None,
        seeded_plans: bool = True,
    ):
        self.plan = plan
        # The `pro` row `enforced_plan_state` looks up while a trial is live.
        # `seeded_plans=False` stands for a database where it is missing.
        self.trial_plan = _plan(TRIAL_PLAN_CODE) if seeded_plans else None
        self.subscription = (
            None
            if plan is None
            else SimpleNamespace(
                id=uuid.uuid4(),
                agency_id=AGENCY_ID,
                plan_id=plan.id,
                status=status,
                voice_minutes_used=voice_minutes_used,
                trial_ends_at=trial_ends_at,
            )
        )
        self.properties_used = properties_used
        self.agents_used = agents_used
        self.existing_properties = list(existing_properties)
        self.consent = consent
        self.agency = agency
        self.lead = lead
        self.added: list = []
        self.commits = 0

    async def execute(self, stmt):
        sql = str(stmt)
        if "FROM subscriptions" in sql:
            return _Result(scalar=self.subscription)
        if "FROM plans" in sql:
            return _Result(rows=[self.trial_plan] if self.trial_plan else [])
        if "FROM users" in sql:
            return _Result(scalar=self.agents_used)
        if "count(*)" in sql and "FROM properties" in sql:
            return _Result(scalar=self.properties_used)
        if "FROM properties" in sql:
            return _Result(rows=self.existing_properties)
        if "FROM consent_logs" in sql:
            return _Result(rows=[self.consent] if self.consent else [])
        # Benchmark aggregates (leads / visits / messages): an empty sample is
        # a valid answer, and what this file cares about is who gets to run
        # them at all.
        if "FROM leads" in sql or "FROM visits" in sql or "messages" in sql:
            return _Result(rows=[])
        # The voice brain resolves the calling agency before metering it
        # (`_resolve_agency`'s cascade: metadata id, slug, assistant id, phone
        # number id, single-tenant fallback). Every strategy ends in a lookup
        # against `agencies`, and this file is about the plan gate that runs
        # *after* that — so routing always lands on the tenant under test.
        # `Agency.id` selects come back as bare ids because that is what the
        # cascade reads (`.scalars()`), while `select(Agency)` wants the row.
        if "FROM agencies" in sql:
            if "count(" in sql:
                return _Result(scalar=1)
            if "agencies.id" in sql and "agencies.name" not in sql:
                return _Result(rows=[AGENCY_ID])
            return _Result(rows=[self.agency] if self.agency else [])
        raise AssertionError(f"unexpected query: {sql}")

    async def get(self, model, pk):
        name = getattr(model, "__name__", "")
        if name == "Plan":
            return self.plan
        if name == "Agency":
            return self.agency
        if name == "Lead":
            return self.lead
        return None

    def add(self, obj):
        # SQLAlchemy fills the `uuid4` column default on flush; nothing
        # flushes against a fake session, and the endpoints read `.id` back
        # into their response models.
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


@pytest.fixture
def make_client(monkeypatch):
    """TestClient wired to a PlanSession, with the dev auth bypass on."""
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")

    def _build(session: PlanSession) -> TestClient:
        async def _override():
            yield session

        app.dependency_overrides[get_db] = _override
        return TestClient(app)

    yield _build
    app.dependency_overrides.pop(get_db, None)


def _denied_feature(resp) -> str:
    """The feature named in a 403 from the plan gates."""
    assert resp.status_code == 403, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "feature_not_in_plan", detail
    return detail["feature"]


# ---------------------------------------------------------------------------
# The flags this file relies on are the ones the pricing page sells
# ---------------------------------------------------------------------------


def test_seeded_flags_match_what_the_pricing_page_sells():
    starter, pro, agencia = (_SPECS[c]["features"] for c in ("starter", "pro", "agencia"))
    for flag in ("sms", "voice"):
        assert starter.get(flag) is False and pro[flag] and agencia[flag]
    for flag in ("plantillas", "ofertas_pdf"):
        assert not starter.get(flag) and pro[flag] and agencia[flag]
    assert not starter.get("marketplace") and not pro.get("marketplace")
    assert agencia["marketplace"] is True
    # `informes` is a tier string, not a boolean — the reason benchmarking
    # cannot be gated with `ensure_feature`.
    assert starter["informes"] == "básicos"
    assert pro["informes"] == agencia["informes"] == "completos"


# ---------------------------------------------------------------------------
# Templates (`plantillas`)
# ---------------------------------------------------------------------------

TEMPLATES_URL = f"/api/v1/agencies/{AGENCY_ID}/templates"


def test_starter_cannot_create_a_template(make_client):
    client = make_client(PlanSession(_plan("starter")))
    resp = client.post(TEMPLATES_URL, json={"name": "Bienvenida", "body": "Hola {{nombre}}"})
    assert _denied_feature(resp) == "plantillas"


def test_starter_cannot_render_a_template(make_client):
    client = make_client(PlanSession(_plan("starter")))
    resp = client.post(f"{TEMPLATES_URL}/{uuid.uuid4()}/render", json={})
    assert _denied_feature(resp) == "plantillas"


def test_pro_can_create_a_template(make_client):
    session = PlanSession(_plan("pro"))
    resp = make_client(session).post(
        TEMPLATES_URL, json={"name": "Bienvenida", "body": "Hola {{nombre}}"}
    )
    assert resp.status_code == 201
    assert session.added[0].name == "Bienvenida"


def test_agency_without_a_plan_can_create_a_template(make_client):
    session = PlanSession(None)
    resp = make_client(session).post(
        TEMPLATES_URL, json={"name": "Bienvenida", "body": "Hola {{nombre}}"}
    )
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Offers (`ofertas_pdf`)
# ---------------------------------------------------------------------------

OFFERS_URL = f"/api/v1/agencies/{AGENCY_ID}/offers"


def _offer_body() -> dict:
    return {
        "lead_id": str(uuid.uuid4()),
        "property_id": str(uuid.uuid4()),
        "amount_eur": 180000,
    }


def test_starter_cannot_create_a_pdf_offer(make_client):
    resp = make_client(PlanSession(_plan("starter"))).post(OFFERS_URL, json=_offer_body())
    assert _denied_feature(resp) == "ofertas_pdf"


def test_pro_gets_past_the_offer_gate(make_client):
    """400 `lead_invalid`, not 403: the plan let it through and the endpoint
    then rejected the made-up lead id, which is the next check in line."""
    resp = make_client(PlanSession(_plan("pro"))).post(OFFERS_URL, json=_offer_body())
    assert resp.status_code == 400
    assert resp.json()["detail"] == "lead_invalid"


def test_agency_without_a_plan_gets_past_the_offer_gate(make_client):
    resp = make_client(PlanSession(None)).post(OFFERS_URL, json=_offer_body())
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Marketplace (`marketplace`, Agencia only)
# ---------------------------------------------------------------------------

CLAIM_URL = f"/api/v1/marketplace/offers/{uuid.uuid4()}/claim"
PUBLISH_URL = "/api/v1/marketplace/publish"


def test_starter_cannot_claim_a_marketplace_lead(make_client):
    resp = make_client(PlanSession(_plan("starter"))).post(
        CLAIM_URL, json={"buyer_agency_id": str(AGENCY_ID)}
    )
    assert _denied_feature(resp) == "marketplace"


def test_pro_cannot_claim_a_marketplace_lead(make_client):
    """The marketplace is the one feature Pro does not buy either."""
    resp = make_client(PlanSession(_plan("pro"))).post(
        CLAIM_URL, json={"buyer_agency_id": str(AGENCY_ID)}
    )
    assert _denied_feature(resp) == "marketplace"


def test_agencia_gets_past_the_claim_gate(make_client):
    """404 `offer_not_found`: the plan passed and the lookup for the made-up
    offer id is what stopped it."""
    resp = make_client(PlanSession(_plan("agencia"))).post(
        CLAIM_URL, json={"buyer_agency_id": str(AGENCY_ID)}
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "offer_not_found"


def test_agency_without_a_plan_gets_past_the_claim_gate(make_client):
    resp = make_client(PlanSession(None)).post(
        CLAIM_URL, json={"buyer_agency_id": str(AGENCY_ID)}
    )
    assert resp.status_code == 404


def _publish_body(lead_id: uuid.UUID) -> dict:
    return {"lead_id": str(lead_id), "asking_fee_eur": 300}


def test_starter_cannot_publish_a_lead_to_the_marketplace(make_client):
    lead = SimpleNamespace(id=uuid.uuid4(), agency_id=AGENCY_ID)
    resp = make_client(PlanSession(_plan("starter"), lead=lead)).post(
        PUBLISH_URL, json=_publish_body(lead.id)
    )
    assert _denied_feature(resp) == "marketplace"


def test_agencia_gets_past_the_publish_gate(make_client):
    """Also a 403, but the consent one: it proves the plan gate let it by and
    the GDPR check downstream is what refused."""
    lead = SimpleNamespace(id=uuid.uuid4(), agency_id=AGENCY_ID)
    resp = make_client(PlanSession(_plan("agencia"), lead=lead)).post(
        PUBLISH_URL, json=_publish_body(lead.id)
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "consent_for_data_sharing_required"


# ---------------------------------------------------------------------------
# Benchmarks (`informes` tier)
# ---------------------------------------------------------------------------

BENCHMARKS_URL = f"/api/v1/agencies/{AGENCY_ID}/benchmarks"


def test_starter_cannot_read_the_cross_agency_benchmarks(make_client):
    resp = make_client(PlanSession(_plan("starter"))).get(BENCHMARKS_URL)
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["code"] == "feature_not_in_plan"
    assert detail["feature"] == "informes"
    assert detail["required"] == benchmarking_module.MIN_REPORT_TIER
    assert detail["current"] == "básicos"


def test_a_plan_with_full_reports_can_read_the_benchmarks(make_client):
    resp = make_client(PlanSession(_plan("agencia"))).get(BENCHMARKS_URL)
    assert resp.status_code == 200
    assert resp.json()["range_days"] == 30


def test_agency_without_a_plan_can_read_the_benchmarks(make_client):
    assert make_client(PlanSession(None)).get(BENCHMARKS_URL).status_code == 200


def test_report_tier_ordering_is_accent_and_case_insensitive():
    # Plan rows are hand-edited in production; "Basicos" is still Starter.
    assert plan_limits.report_tier_meets("completos", "completos") is True
    assert plan_limits.report_tier_meets("COMPLETOS", "completos") is True
    assert plan_limits.report_tier_meets("básicos", "completos") is False
    assert plan_limits.report_tier_meets("Basicos", "completos") is False
    assert plan_limits.report_tier_meets("completos", "basicos") is True
    # A tier we cannot read never unlocks the expensive report.
    assert plan_limits.report_tier_meets(None, "completos") is False
    assert plan_limits.report_tier_meets("", "completos") is False
    assert plan_limits.report_tier_meets("premium", "completos") is False


# ---------------------------------------------------------------------------
# Phone channel + voice preview (`voice`)
# ---------------------------------------------------------------------------

PHONE_URL = f"/api/v1/agencies/{AGENCY_ID}/channels/phone"
PREVIEW_URL = f"/api/v1/agencies/{AGENCY_ID}/voice/preview"


def _agency() -> SimpleNamespace:
    return SimpleNamespace(
        id=AGENCY_ID,
        name="Inmo Sol",
        slug="inmo-sol",
        contact_email="dueno@inmosol.example.com",
        settings={},
    )


def test_starter_cannot_provision_an_ai_phone_number(make_client):
    resp = make_client(PlanSession(_plan("starter"), agency=_agency())).post(PHONE_URL, json={})
    assert _denied_feature(resp) == "voice"


def test_a_starter_that_already_has_a_number_still_reads_it_back(make_client):
    """The gate sits after the idempotent early return: a number provisioned
    before the downgrade must not turn into a 403 in the panel."""
    agency = _agency()
    agency.settings = {"ai_phone_number": "+34910000000"}
    resp = make_client(PlanSession(_plan("starter"), agency=agency)).post(PHONE_URL, json={})
    assert resp.status_code == 200
    assert resp.json() == {"status": "active", "number": "+34910000000"}


def test_starter_cannot_synthesize_a_voice_preview(make_client):
    resp = make_client(PlanSession(_plan("starter"))).post(
        PREVIEW_URL,
        json={"provider": "11labs", "voice_id": "v1", "text": "Hola"},
    )
    assert _denied_feature(resp) == "voice"


def test_pro_gets_past_the_voice_preview_gate(make_client, monkeypatch):
    """501 `preview_unavailable` (no ElevenLabs key in tests) instead of 403:
    the plan is not what stops it."""
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "elevenlabs_api_key", SecretStr(""))
    resp = make_client(PlanSession(_plan("pro"))).post(
        PREVIEW_URL,
        json={"provider": "11labs", "voice_id": "v1", "text": "Hola"},
    )
    assert resp.status_code == 501


def test_agency_without_a_plan_gets_past_the_voice_preview_gate(make_client, monkeypatch):
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "elevenlabs_api_key", SecretStr(""))
    resp = make_client(PlanSession(None)).post(
        PREVIEW_URL,
        json={"provider": "11labs", "voice_id": "v1", "text": "Hola"},
    )
    assert resp.status_code == 501


# ---------------------------------------------------------------------------
# Inbound SMS (`sms`) — a webhook, so it drops with 200 instead of 4xx
# ---------------------------------------------------------------------------


@pytest.fixture
def sms_wiring(monkeypatch):
    handled: list = []

    async def _agency_for_number(db, to_number):
        # La fila, no el id: desde SMS self-serve el webhook necesita
        # `settings` para saber con qué token de Twilio se firmó la petición
        # y con qué credenciales responder (app/api/sms.py).
        return SimpleNamespace(id=AGENCY_ID, settings={})

    async def _handle_inbound(db, payload):
        handled.append(payload)
        return SimpleNamespace(reply_text="")

    monkeypatch.setattr(sms_module, "_agency_for_number", _agency_for_number)
    monkeypatch.setattr(sms_module, "handle_inbound", _handle_inbound)
    return handled


SMS_FORM = {
    "From": "+34600111222",
    "To": "+34910000000",
    "Body": "¿Tenéis pisos en Chamberí?",
    "MessageSid": "SM123",
}


def test_starter_inbound_sms_is_dropped_without_answering(make_client, sms_wiring):
    """200 so Twilio does not retry, but nothing is spent: no Claude tokens
    and no outbound message billed to selai."""
    resp = make_client(PlanSession(_plan("starter"))).post("/webhooks/sms", data=SMS_FORM)
    assert resp.status_code == 200
    assert resp.json() == {"status": "feature_not_in_plan"}
    assert sms_wiring == []


def test_pro_inbound_sms_is_answered(make_client, sms_wiring):
    resp = make_client(PlanSession(_plan("pro"))).post("/webhooks/sms", data=SMS_FORM)
    assert resp.json() == {"status": "ok"}
    assert len(sms_wiring) == 1


def test_agency_without_a_plan_still_gets_its_sms_answered(make_client, sms_wiring):
    resp = make_client(PlanSession(None)).post("/webhooks/sms", data=SMS_FORM)
    assert resp.json() == {"status": "ok"}
    assert len(sms_wiring) == 1


def test_paused_agency_inbound_sms_is_dropped(make_client, sms_wiring):
    """The `is_subscription_blocked` check WhatsApp and email already had."""
    session = PlanSession(_plan("pro"), status=SubscriptionStatus.PAUSED)
    resp = make_client(session).post("/webhooks/sms", data=SMS_FORM)
    assert resp.json() == {"status": "subscription_blocked"}
    assert sms_wiring == []


# ---------------------------------------------------------------------------
# Inbound voice minutes — the dignified goodbye
# ---------------------------------------------------------------------------


@pytest.fixture
def voice_wiring(monkeypatch):
    handled: list = []

    async def _handle_inbound(db, payload):
        handled.append(payload)
        return SimpleNamespace(reply_text="Sí, tenemos tres pisos en Chamberí.")

    monkeypatch.setattr(voice_module, "handle_inbound", _handle_inbound)
    return handled


def _llm_body() -> dict:
    return {
        "messages": [{"role": "user", "content": "¿Tenéis pisos en Chamberí?"}],
        "metadata": {"agency_id": str(AGENCY_ID)},
        "call": {"id": "call_1"},
    }


def _reply_of(resp) -> str:
    return resp.json()["choices"][0]["message"]["content"]


def test_inbound_call_says_goodbye_when_the_minutes_run_out(make_client, voice_wiring):
    """Not a hang-up and not an error: there is a person on the line, so the
    turn that hits the ceiling gets a proper farewell — and the agent stops
    spending Claude tokens and Vapi minutes selai would have paid for."""
    session = PlanSession(_plan("pro"), voice_minutes_used=600)  # cap is 600

    resp = make_client(session).post("/voice/llm", json=_llm_body())

    assert resp.status_code == 200
    assert _reply_of(resp) == voice_module.VOICE_MINUTES_EXHAUSTED_REPLY
    assert voice_wiring == [], "the agent kept talking past the plan's minutes"
    # Nothing about billing reaches the caller: they are the agency's lead.
    reply = _reply_of(resp).lower()
    assert "plan" not in reply and "minuto" not in reply


def test_a_starter_inbound_call_is_never_served(make_client, voice_wiring):
    """Starter includes 0 voice minutes, so the very first turn is the last."""
    resp = make_client(PlanSession(_plan("starter"))).post("/voice/llm", json=_llm_body())
    assert _reply_of(resp) == voice_module.VOICE_MINUTES_EXHAUSTED_REPLY
    assert voice_wiring == []


def test_inbound_call_within_the_minutes_is_served(make_client, voice_wiring):
    session = PlanSession(_plan("pro"), voice_minutes_used=599)
    resp = make_client(session).post("/voice/llm", json=_llm_body())
    assert "Chamberí" in _reply_of(resp)
    assert len(voice_wiring) == 1


def test_agency_without_a_plan_is_still_served_by_the_voice_brain(make_client, voice_wiring):
    resp = make_client(PlanSession(None)).post("/voice/llm", json=_llm_body())
    assert "Chamberí" in _reply_of(resp)


async def test_a_broken_billing_read_never_hangs_up_on_the_caller(monkeypatch):
    """`voice_minutes_status` fails OPEN: cutting a real conversation short
    because a lookup blew up is worse than a few unmetered minutes."""

    async def _boom(db, agency_id):
        raise RuntimeError("database on fire")

    monkeypatch.setattr(plan_limits, "get_plan_state", _boom)

    assert await plan_limits.voice_minutes_status(None, AGENCY_ID) is None


# ---------------------------------------------------------------------------
# CSV import — the properties limit, counted row by row
# ---------------------------------------------------------------------------

IMPORT_URL = f"/api/v1/agencies/{AGENCY_ID}/properties/import"
HEADER = "referencia,titulo,precio,ciudad\n"


def _csv(*refs: str) -> bytes:
    body = "".join(f"{ref},Piso {ref},185000,Madrid\n" for ref in refs)
    return (HEADER + body).encode("utf-8")


@pytest.fixture(autouse=True)
def no_broker(monkeypatch):
    """The import enqueues an embedding backfill; without Redis, Celery would
    sit in its connection retry loop."""
    from app.workers import tasks

    monkeypatch.setattr(
        tasks.embed_missing_properties, "delay", lambda *a, **k: None, raising=False
    )


def _upload(client, payload: bytes):
    return client.post(IMPORT_URL, files={"file": ("catalogo.csv", payload, "text/csv")})


def test_import_stops_at_the_limit_and_says_how_many_were_left_out(make_client):
    """The 5.000-row file at 99/100: one listing fits, the rest are reported
    instead of imported. Before this, the whole file went in."""
    session = PlanSession(_plan("starter"), properties_used=99)  # cap is 100

    body = _upload(make_client(session), _csv("REF-1", "REF-2", "REF-3")).json()

    assert body["created"] == 1
    assert body["limit_skipped"] == 2
    assert body["limit_reason"] == "plan_limit_reached"
    assert body["properties_limit"] == 100
    assert len(session.added) == 1
    # Plain Spanish for the panel, alongside the machine-readable fields.
    assert "2 propiedades" in body["errors"][0]
    assert "100" in body["errors"][0]


def test_rows_that_update_an_existing_reference_do_not_count(make_client):
    """A full catalog must still accept its monthly refresh: the export is
    almost all updates, and updates create nothing."""
    known = SimpleNamespace(
        agency_id=AGENCY_ID,
        reference="REF-1",
        title="Piso viejo",
        description="",
        property_type=PropertyType.APARTMENT,
        operation="sale",
        price=100000,
        address="",
        neighborhood=None,
        city="Madrid",
        postal_code=None,
        bedrooms=2,
        bathrooms=1,
        area_sqm=70,
        extra={},
    )
    session = PlanSession(
        _plan("starter"), properties_used=100, existing_properties=[known]
    )

    body = _upload(make_client(session), _csv("REF-1")).json()

    assert body["updated"] == 1
    assert body["created"] == 0
    assert body["limit_skipped"] == 0
    assert body["limit_reason"] is None
    assert known.price == 185000


def test_a_full_catalog_reports_every_new_listing_as_left_out(make_client):
    session = PlanSession(_plan("starter"), properties_used=100)

    body = _upload(make_client(session), _csv("REF-1", "REF-2")).json()

    assert body["created"] == 0
    assert body["limit_skipped"] == 2
    assert session.added == []


def test_a_rejected_reference_repeated_in_the_file_counts_once(make_client):
    """The agency counts listings, not rows: a duplicated reference that did
    not fit is one property left out, not two."""
    session = PlanSession(_plan("starter"), properties_used=100)

    body = _upload(make_client(session), _csv("REF-1", "REF-1")).json()

    assert body["limit_skipped"] == 1


def test_an_agency_without_a_plan_imports_everything(make_client):
    session = PlanSession(None)

    body = _upload(make_client(session), _csv("REF-1", "REF-2", "REF-3")).json()

    assert body["created"] == 3
    assert body["limit_skipped"] == 0
    assert body["properties_limit"] is None


def test_a_paused_agency_cannot_import_at_all(make_client):
    session = PlanSession(_plan("pro"), status=SubscriptionStatus.PAUSED, properties_used=0)

    resp = _upload(make_client(session), _csv("REF-1"))

    assert resp.status_code == 402
    assert resp.json()["detail"]["code"] == "subscription_inactive"
    assert session.added == []


# ---------------------------------------------------------------------------
# The 14-day trial — the false positive that hit every new customer
# ---------------------------------------------------------------------------
#
# Every signup lands on `starter` (services/onboarding.py picks the cheapest
# active plan) with status TRIALING, while the site sells those 14 days as
# "todas las funciones del plan Pro [...] para hacer llamadas de prueba" and
# the activation wizard walks the owner through a voice step and a phone step.
# Enforcing the parked plan therefore answered 403 to the wizard's own buttons
# on day one, with a panel message ("inténtalo de nuevo en unos minutos") that
# would never come true.


def _trialing(code: str = "starter", **kwargs) -> PlanSession:
    return PlanSession(
        _plan(code),
        status=SubscriptionStatus.TRIALING,
        trial_ends_at=IN_TRIAL,
        **kwargs,
    )


@pytest.fixture
def no_outbound_email(monkeypatch):
    """The phone fallback emails the team; tests must not reach the provider."""
    from app.api import channels as channels_module

    async def _send(*args, **kwargs):
        return True

    monkeypatch.setattr(channels_module, "send_email", _send)


def test_a_trialing_agency_can_provision_its_ai_phone_number(
    make_client, no_outbound_email
):
    """Step 3 of the activation wizard, for 100% of new customers."""
    session = _trialing(agency=_agency())
    resp = make_client(session).post(PHONE_URL, json={})
    assert resp.status_code == 200, resp.text
    # No Vapi key in tests, so it takes the "we provision it by hand" branch —
    # which is the wizard's documented outcome, not an error.
    assert resp.json()["status"] == "requested"


def test_a_trialing_agency_can_preview_a_voice(make_client, monkeypatch):
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "elevenlabs_api_key", SecretStr(""))
    resp = make_client(_trialing()).post(
        PREVIEW_URL, json={"provider": "11labs", "voice_id": "v1", "text": "Hola"}
    )
    assert resp.status_code == 501  # 501 = no provider key, not 403 = no plan


def test_a_trialing_agency_gets_its_test_calls_answered(make_client, voice_wiring):
    """`starter` includes 0 voice minutes; the trial's plan includes 600."""
    resp = make_client(_trialing()).post("/voice/llm", json=_llm_body())
    assert "Chamberí" in _reply_of(resp)
    assert len(voice_wiring) == 1


def test_a_trialing_agency_gets_its_sms_answered(make_client, sms_wiring):
    resp = make_client(_trialing()).post("/webhooks/sms", data=SMS_FORM)
    assert resp.json() == {"status": "ok"}
    assert len(sms_wiring) == 1


def test_a_trialing_agency_can_read_the_benchmarks(make_client):
    assert make_client(_trialing()).get(BENCHMARKS_URL).status_code == 200


def test_a_trialing_agency_can_create_a_template(make_client):
    resp = make_client(_trialing()).post(
        TEMPLATES_URL, json={"name": "Bienvenida", "body": "Hola {{nombre}}"}
    )
    assert resp.status_code == 201


def test_the_trial_imports_against_the_catalog_size_it_advertises(make_client):
    """A trial with 250 listings must not be told its catalog is too big."""
    session = _trialing(properties_used=100)  # starter's cap, pro's is 500
    body = _upload(make_client(session), _csv("REF-1", "REF-2")).json()
    assert body["created"] == 2
    assert body["limit_skipped"] == 0
    assert body["properties_limit"] == 500


def test_the_trial_does_not_hand_out_what_the_trial_does_not_sell(make_client):
    """"Todas las funciones del plan Pro" — and the marketplace is not one."""
    resp = make_client(_trialing()).post(
        CLAIM_URL, json={"buyer_agency_id": str(AGENCY_ID)}
    )
    assert _denied_feature(resp) == "marketplace"
    # The 403 names the plan that was *evaluated*, which during a trial is the
    # advertised one. Pinned because the panel reads it to build the upgrade
    # prompt: it must not tell a trial user "your plan is starter" when what
    # refused them was Pro's feature list.
    assert resp.json()["detail"]["plan"] == TRIAL_PLAN_CODE


def test_a_trial_on_a_pricier_plan_is_never_downgraded(make_client):
    """404 `offer_not_found`: an Agencia trial keeps Agencia's marketplace."""
    resp = make_client(_trialing("agencia")).post(
        CLAIM_URL, json={"buyer_agency_id": str(AGENCY_ID)}
    )
    assert resp.status_code == 404


def test_an_expired_trial_row_no_longer_carries_the_trial(make_client):
    """`lifecycle.trial_expiry_sweep` flips TRIALING → PAUSED on a schedule, so
    between the deadline and the next sweep the row still reads TRIALING. The
    date is what decides, not the label."""
    session = PlanSession(
        _plan("starter"),
        status=SubscriptionStatus.TRIALING,
        trial_ends_at=TRIAL_OVER,
        agency=_agency(),
    )
    assert _denied_feature(make_client(session).post(PHONE_URL, json={})) == "voice"


def test_without_the_advertised_plan_seeded_nothing_is_invented(make_client):
    """No `pro` row in the database (a fresh install): the trial falls back to
    the plan it is parked on instead of guessing at rights."""
    session = PlanSession(
        _plan("starter"),
        status=SubscriptionStatus.TRIALING,
        trial_ends_at=IN_TRIAL,
        seeded_plans=False,
        agency=_agency(),
    )
    assert _denied_feature(make_client(session).post(PHONE_URL, json={})) == "voice"


def test_trial_is_live_reads_the_deadline():
    def _sub(status, ends):
        return SimpleNamespace(status=status, trial_ends_at=ends)

    assert plan_limits.trial_is_live(_sub(SubscriptionStatus.TRIALING, IN_TRIAL))
    assert not plan_limits.trial_is_live(_sub(SubscriptionStatus.TRIALING, TRIAL_OVER))
    assert not plan_limits.trial_is_live(_sub(SubscriptionStatus.ACTIVE, IN_TRIAL))
    assert not plan_limits.trial_is_live(None)
    # No deadline recorded: nothing says the trial is over.
    assert plan_limits.trial_is_live(_sub(SubscriptionStatus.TRIALING, None))
    # Naive timestamps (SQLite, hand-written rows) must not raise on compare.
    assert plan_limits.trial_is_live(
        _sub(SubscriptionStatus.TRIALING, IN_TRIAL.replace(tzinfo=None))
    )
    assert not plan_limits.trial_is_live(
        _sub(SubscriptionStatus.TRIALING, TRIAL_OVER.replace(tzinfo=None))
    )


# ---------------------------------------------------------------------------
# A live call is never cut in the middle
# ---------------------------------------------------------------------------

GREETING = "Inmobiliaria Sol, ¿en qué puedo ayudarte?"
REAL_ANSWER = "Sí, tenemos tres pisos en Chamberí."


def _llm_body_with(messages: list[dict]) -> dict:
    return {
        "messages": messages,
        "metadata": {"agency_id": str(AGENCY_ID)},
        "call": {"id": "call_1"},
    }


def test_a_call_in_progress_is_not_hung_up_when_another_call_spends_the_last_minute(
    make_client, voice_wiring
):
    """Two lines busy: call A ends and crosses the ceiling, call B is
    mid-sentence. Cutting B there is a person hearing a goodbye halfway
    through their own question."""
    session = PlanSession(_plan("pro"), voice_minutes_used=600)  # cap is 600

    resp = make_client(session).post(
        "/voice/llm",
        json=_llm_body_with(
            [
                {"role": "assistant", "content": GREETING},
                {"role": "user", "content": "Busco un piso de dos habitaciones"},
                {"role": "assistant", "content": REAL_ANSWER},
                {"role": "user", "content": "¿Y con terraza?"},
            ]
        ),
    )

    assert _reply_of(resp) != voice_module.VOICE_MINUTES_EXHAUSTED_REPLY
    assert len(voice_wiring) == 1


def test_a_call_that_starts_over_the_cap_is_stopped_on_its_first_turn(
    make_client, voice_wiring
):
    """The greeting Vapi plays before the caller speaks is not an answer of
    ours, so it must not buy the call a free turn."""
    session = PlanSession(_plan("pro"), voice_minutes_used=600)

    resp = make_client(session).post(
        "/voice/llm",
        json=_llm_body_with(
            [
                {"role": "assistant", "content": GREETING},
                {"role": "user", "content": "Hola, ¿tenéis pisos en Chamberí?"},
            ]
        ),
    )

    assert _reply_of(resp) == voice_module.VOICE_MINUTES_EXHAUSTED_REPLY
    assert voice_wiring == []


def test_a_caller_who_talks_past_the_farewell_keeps_getting_the_farewell(
    make_client, voice_wiring
):
    """Otherwise the goodbye would be the price of admission to a free call."""
    session = PlanSession(_plan("pro"), voice_minutes_used=600)

    resp = make_client(session).post(
        "/voice/llm",
        json=_llm_body_with(
            [
                {"role": "user", "content": "¿Tenéis pisos?"},
                {
                    "role": "assistant",
                    "content": voice_module.VOICE_MINUTES_EXHAUSTED_REPLY,
                },
                {"role": "user", "content": "¿Oye? ¿Sigues ahí?"},
            ]
        ),
    )

    assert _reply_of(resp) == voice_module.VOICE_MINUTES_EXHAUSTED_REPLY
    assert voice_wiring == []


def test_a_transcript_we_cannot_read_does_not_buy_voice_minutes():
    """No assistant history in the request → the cap applies. Failing the
    other way would make an unreadable payload a free line."""
    assert voice_module.call_is_already_under_way([]) is False
    assert (
        voice_module.call_is_already_under_way([{"role": "user", "content": "hola"}])
        is False
    )
    assert (
        voice_module.call_is_already_under_way(
            [{"role": "system", "content": "eres un agente"}]
        )
        is False
    )
    assert (
        voice_module.call_is_already_under_way(
            [
                {"role": "user", "content": "hola"},
                {"role": "assistant", "content": ""},
            ]
        )
        is False
    )


# ---------------------------------------------------------------------------
# A dead account does not get a new phone number billed to selai
# ---------------------------------------------------------------------------


def test_a_paused_agency_cannot_provision_a_phone_number(make_client):
    """A Vapi number is a monthly bill selai pays from the minute it exists."""
    session = PlanSession(
        _plan("pro"), status=SubscriptionStatus.PAUSED, agency=_agency()
    )
    resp = make_client(session).post(PHONE_URL, json={})
    assert resp.status_code == 402
    assert resp.json()["detail"]["code"] == "subscription_inactive"


def test_a_paused_agency_still_reads_back_the_number_it_already_has(make_client):
    """Nothing is spent, and the panel must not turn a working number into an
    error the day the trial expires."""
    agency = _agency()
    agency.settings = {"ai_phone_number": "+34910000000"}
    session = PlanSession(
        _plan("pro"), status=SubscriptionStatus.PAUSED, agency=agency
    )
    resp = make_client(session).post(PHONE_URL, json={})
    assert resp.status_code == 200
    assert resp.json() == {"status": "active", "number": "+34910000000"}


def test_remaining_is_the_headroom_not_a_yes_or_no():
    assert plan_limits.check_limit("properties", 99, 100).remaining == 1
    assert plan_limits.check_limit("properties", 100, 100).remaining == 0
    # Over the cap (a downgrade) must not hand out negative slots.
    assert plan_limits.check_limit("properties", 120, 100).remaining == 0
    # Negative limit = unlimited.
    assert plan_limits.check_limit("properties", 999, -1).remaining is None
