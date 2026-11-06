"""(163) Owner capture: prompt directive, `save_seller_lead` tool and alert."""
from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.agent.prompts import SELLER_CAPTURE_HEADER, render_setter_prompt
from app.agent.tools import TOOL_SCHEMAS, AgentContext, execute_tool
from app.db.models import Agency, Lead, LeadStatus, LeadTemperature
from app.services import conversation_service as cs

# ---------------------------------------------------------------------------
# Prompt directive
# ---------------------------------------------------------------------------


def _prompt() -> str:
    return render_setter_prompt(
        agency_name="Inmo Sol",
        channel="whatsapp",
        lead_summary="Nombre: Juan",
        agency_context="Pisos en Madrid",
    )


def test_prompt_treats_owners_as_a_capture_opportunity():
    prompt = _prompt()
    assert SELLER_CAPTURE_HEADER in prompt
    assert "VENDER o ALQUILAR SU propiedad" in prompt
    assert "save_seller_lead" in prompt


def test_prompt_asks_for_zone_type_price_and_urgency_without_valuing():
    prompt = _prompt()
    for expected in ("zona exacta", "tipo (piso", "qué precio espera", "para cuándo"):
        assert expected in prompt
    assert "No tases el inmueble" in prompt


# ---------------------------------------------------------------------------
# Tool schema
# ---------------------------------------------------------------------------


def _schema(name: str) -> dict:
    return next(s for s in TOOL_SCHEMAS if s["name"] == name)


def test_save_seller_lead_schema_requires_zone_and_type():
    schema = _schema("save_seller_lead")
    assert schema["input_schema"]["required"] == ["zone", "property_type"]
    props = schema["input_schema"]["properties"]
    assert set(props) == {"zone", "property_type", "expected_price", "urgency", "notes"}
    assert props["urgency"]["enum"] == ["low", "normal", "high"]


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------


class _FakeDB:
    """Answers `get()` by model, counts flushes. No Postgres involved."""

    def __init__(self, lead=None, agency=None):
        self.lead = lead
        self.agency = agency
        self.flushes = 0

    async def get(self, model, pk):
        if model is Lead:
            return self.lead
        if model is Agency:
            return self.agency
        return None

    async def flush(self):
        self.flushes += 1


def _lead(**overrides) -> SimpleNamespace:
    base = {
        "id": uuid.uuid4(),
        "full_name": "Marta López",
        "phone_e164": "+34600111222",
        "email": None,
        "status": LeadStatus.NEW,
        "temperature": LeadTemperature.COLD,
        "extra": {},
        "qualification_notes": "",
    }
    return SimpleNamespace(**{**base, **overrides})


def _ctx(db, lead, channel="whatsapp") -> AgentContext:
    return AgentContext(
        db=db,
        agency_id=uuid.uuid4(),
        lead_id=lead.id,
        conversation_id=uuid.uuid4(),
        channel=channel,
    )


async def test_save_seller_lead_persists_the_opportunity():
    lead = _lead()
    db = _FakeDB(lead=lead)
    result = await execute_tool(
        "save_seller_lead",
        {
            "zone": "Chamberí",
            "property_type": "piso",
            "expected_price": 420000,
            "urgency": "high",
            "notes": "90 m2, reformado",
        },
        _ctx(db, lead),
    )

    assert result.startswith("OK:")
    seller = lead.extra["seller_lead"]
    assert seller["zone"] == "Chamberí"
    assert seller["property_type"] == "piso"
    assert seller["expected_price"] == 420000
    assert seller["urgency"] == "high"
    assert seller["notes"] == "90 m2, reformado"
    assert seller["channel"] == "whatsapp"
    datetime.fromisoformat(seller["captured_at"])
    assert db.flushes == 1


async def test_save_seller_lead_marks_the_lead_hot_and_engaged():
    lead = _lead()
    await execute_tool(
        "save_seller_lead",
        {"zone": "Gràcia", "property_type": "ático"},
        _ctx(_FakeDB(lead=lead), lead),
    )
    assert lead.temperature == LeadTemperature.HOT
    assert lead.status == LeadStatus.ENGAGED
    # The sticky mark (165) is what keeps the recompute from undoing this.
    datetime.fromisoformat(lead.extra["hot_marked_at"])
    assert lead.extra["hot_marked_by"] == "save_seller_lead"


async def test_save_seller_lead_keeps_previous_extra_keys():
    lead = _lead(extra={"foreign_buyer_mode": True})
    await execute_tool(
        "save_seller_lead",
        {"zone": "Nervión", "property_type": "chalet"},
        _ctx(_FakeDB(lead=lead), lead),
    )
    assert lead.extra["foreign_buyer_mode"] is True
    assert lead.extra["seller_lead"]["zone"] == "Nervión"


async def test_save_seller_lead_without_zone_saves_nothing():
    lead = _lead()
    db = _FakeDB(lead=lead)
    result = await execute_tool(
        "save_seller_lead", {"zone": "  ", "property_type": "piso"}, _ctx(db, lead)
    )
    assert result.startswith("ERROR")
    assert "seller_lead" not in lead.extra
    assert lead.temperature == LeadTemperature.COLD
    assert db.flushes == 0


async def test_save_seller_lead_tolerates_a_price_written_as_text():
    lead = _lead()
    await execute_tool(
        "save_seller_lead",
        {"zone": "Triana", "property_type": "piso", "expected_price": "320.000 €"},
        _ctx(_FakeDB(lead=lead), lead),
    )
    assert lead.extra["seller_lead"]["expected_price"] == 320000


async def test_save_seller_lead_drops_an_invalid_urgency():
    lead = _lead()
    await execute_tool(
        "save_seller_lead",
        {"zone": "Triana", "property_type": "piso", "urgency": "urgentísimo"},
        _ctx(_FakeDB(lead=lead), lead),
    )
    assert lead.extra["seller_lead"]["urgency"] is None


async def test_recapture_keeps_the_first_captured_at():
    lead = _lead()
    ctx = _ctx(_FakeDB(lead=lead), lead)
    await execute_tool("save_seller_lead", {"zone": "Ruzafa", "property_type": "piso"}, ctx)
    first = lead.extra["seller_lead"]["first_captured_at"]
    await execute_tool(
        "save_seller_lead",
        {"zone": "Ruzafa", "property_type": "piso", "expected_price": 250000},
        ctx,
    )
    assert lead.extra["seller_lead"]["first_captured_at"] == first
    assert lead.extra["seller_lead"]["expected_price"] == 250000


# ---------------------------------------------------------------------------
# Alert to the agency (push + email), same pattern as the hot-lead alert
# ---------------------------------------------------------------------------


def test_seller_lead_just_captured_only_fires_on_the_first_capture():
    assert cs.seller_lead_just_captured(None, {"zone": "Chamberí"}) is True
    assert cs.seller_lead_just_captured({"zone": "Chamberí"}, {"zone": "Chamberí"}) is False
    assert cs.seller_lead_just_captured(None, None) is False


@pytest.fixture
def notifications(monkeypatch):
    pushes: list[dict] = []
    emails: list[dict] = []

    async def _fake_push(db, agency_id, title, body, data=None):
        pushes.append({"title": title, "body": body, "data": data})
        return 1

    async def _fake_email(to, subject, html):
        emails.append({"to": to, "subject": subject, "html": html})
        return True

    monkeypatch.setattr("app.services.push.send_push", _fake_push)
    monkeypatch.setattr("app.services.emails.send_email", _fake_email)
    return pushes, emails


async def test_seller_lead_alert_pushes_and_emails_the_agency(notifications):
    pushes, emails = notifications
    lead = _lead()
    agency = SimpleNamespace(id=uuid.uuid4(), name="Inmo Sol", contact_email="dueno@inmosol.example.com")
    conv_id = uuid.uuid4()

    await cs._notify_seller_lead(
        _FakeDB(lead=lead, agency=agency),
        agency.id,
        lead,
        conv_id,
        {
            "zone": "Chamberí",
            "property_type": "piso",
            "expected_price": 420000,
            "urgency": "high",
        },
    )

    assert pushes[0]["title"] == "Oportunidad de captación: Marta López"
    assert "piso" in pushes[0]["body"] and "Chamberí" in pushes[0]["body"]
    assert pushes[0]["data"]["conversation_id"] == str(conv_id)

    assert emails[0]["to"] == "dueno@inmosol.example.com"
    assert emails[0]["subject"] == "Oportunidad de captación: Marta López"
    html = emails[0]["html"]
    assert "Chamberí" in html
    assert "420000" in html
    assert "quiere venderlo ya" in html
    assert "+34600111222" in html


async def test_seller_lead_alert_skips_email_without_contact_email(notifications):
    pushes, emails = notifications
    lead = _lead()
    agency = SimpleNamespace(id=uuid.uuid4(), name="Inmo Sol", contact_email=None)

    await cs._notify_seller_lead(
        _FakeDB(lead=lead, agency=agency), agency.id, lead, uuid.uuid4(), {"zone": "Chamberí"}
    )
    assert len(pushes) == 1
    assert emails == []


def test_seller_lead_details_only_include_what_we_know():
    details = cs._seller_lead_details({"zone": "Chamberí", "property_type": "piso"})
    assert details[0].startswith("Oportunidad de captación")
    joined = " | ".join(details)
    assert "Precio que espera" not in joined
    assert "Urgencia" not in joined
    assert "Notas" not in joined
