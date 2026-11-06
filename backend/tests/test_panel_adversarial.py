"""Adversarial pass over the operational panel (visits / manual reply /
calls / catalog upsert).

Every test here started as an attack on the built feature:

* answering a conversation that belongs to *another* agency,
* answering by WhatsApp outside Meta's 24h customer-service window,
* answering through a channel the agency never actually connected (both
  provider clients stub the send when their credentials are missing, so a
  "sent" id can be a lie),
* an agenda that groups by the server's timezone instead of the agency's,
* joins that reach rows of another tenant,
* a re-import that overwrites curated columns with the CSV's guesses,
* one malformed call record from the voice webhook killing the whole
  /calls listing.

Same harness as tests/test_channels_api.py: dev auth bypass plus a fake DB
session injected through `dependency_overrides`, so no Postgres is needed.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import dashboard as dashboard_module
from app.api import visits as visits_module
from app.api.dashboard import _call_entries
from app.channels.email.client import ResendClient
from app.channels.whatsapp.client import WhatsAppClient
from app.core.settings import settings
from app.db.models import ConversationChannel, ConversationStatus, Message, Property, PropertyType
from app.db.session import get_db
from app.main import app
from app.services.property_import import apply_row_to_property, parse_properties_csv

AGENCY_ID = uuid.uuid4()
OTHER_AGENCY_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return _FakeScalars(self._rows)


@pytest.fixture
def dev_auth(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")


def _client(session) -> TestClient:
    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_db, None)


# ===========================================================================
# 1. Manual reply
# ===========================================================================


def _conversation(
    channel: ConversationChannel = ConversationChannel.WHATSAPP,
    agency_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=agency_id or AGENCY_ID,
        lead_id=uuid.uuid4(),
        channel=channel,
        status=ConversationStatus.ACTIVE,
    )


def _lead(conv: SimpleNamespace, **overrides) -> SimpleNamespace:
    data = {
        "id": conv.lead_id,
        "agency_id": conv.agency_id,
        "full_name": "Juan Pérez",
        "phone_e164": "+34600111222",
        "whatsapp_wa_id": "34600111222",
        "email": "juan@example.com",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class ReplySession:
    def __init__(self, conv, lead, agency=None):
        self.conv = conv
        self.lead = lead
        self.agency = agency or SimpleNamespace(
            id=AGENCY_ID, name="Inmo Sol", slug="inmo-sol", settings={}
        )
        self.added: list = []
        self.commits = 0

    async def get(self, model, pk):
        name = getattr(model, "__name__", str(model))
        if name == "Conversation":
            return self.conv if self.conv is not None and pk == self.conv.id else None
        if name == "Lead":
            return self.lead if self.lead is not None and pk == self.lead.id else None
        if name == "Agency":
            return self.agency if pk == self.agency.id else None
        raise AssertionError(f"unexpected get({name})")

    async def execute(self, stmt):
        return _FakeResult([])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass

    @property
    def messages(self) -> list[Message]:
        return [o for o in self.added if isinstance(o, Message)]


class SpyClient:
    """Provider double whose send result is programmable per test."""

    sent: list[tuple[str, str]] = []
    result: str = "wamid.OK"
    exc: Exception | None = None

    def __init__(self, access_token=None, phone_number_id=None):
        self.access_token = access_token
        self.phone_number_id = phone_number_id

    async def send_text(self, to: str, text: str) -> str:
        if type(self).exc is not None:
            raise type(self).exc
        type(self).sent.append((to, text))
        return type(self).result

    async def send(self, **kwargs) -> str:
        if type(self).exc is not None:
            raise type(self).exc
        type(self).sent.append((kwargs.get("to", ""), kwargs.get("text", "")))
        return type(self).result


@pytest.fixture(autouse=True)
def providers(monkeypatch):
    SpyClient.sent = []
    SpyClient.result = "wamid.OK"
    SpyClient.exc = None
    monkeypatch.setattr(dashboard_module, "WhatsAppClient", SpyClient)
    monkeypatch.setattr(dashboard_module, "ResendClient", SpyClient)
    return SpyClient


def _reply(client, conv, text: str = "Te llamo en 10 minutos."):
    return client.post(
        f"/api/v1/agencies/{AGENCY_ID}/conversations/{conv.id}/reply",
        json={"text": text},
    )


def _meta_error(code: int, message: str) -> httpx.HTTPStatusError:
    """The exception httpx raises for a Meta rejection, body included."""
    request = httpx.Request("POST", "https://graph.facebook.com/v21.0/1/messages")
    response = httpx.Response(
        400,
        request=request,
        json={
            "error": {
                "message": message,
                "type": "OAuthException",
                "code": code,
                "error_data": {"details": message},
            }
        },
    )
    return httpx.HTTPStatusError(message, request=request, response=response)


# --- tenancy ---------------------------------------------------------------


def test_reply_to_another_agencys_conversation_never_reaches_the_provider(dev_auth):
    """404 is not enough: the text must not leave, either."""
    conv = _conversation(agency_id=OTHER_AGENCY_ID)
    session = ReplySession(conv, _lead(conv))

    resp = _reply(_client(session), conv)

    assert resp.status_code == 404
    assert resp.json()["detail"] == "conversation_not_found"
    assert SpyClient.sent == []
    assert session.added == []
    assert session.commits == 0


def test_reply_when_the_lead_row_belongs_to_another_agency_is_404(dev_auth):
    """A conversation of ours pointing at a foreign lead is corruption, not a
    licence to message that person."""
    conv = _conversation()
    session = ReplySession(conv, _lead(conv, agency_id=OTHER_AGENCY_ID))

    resp = _reply(_client(session), conv)

    assert resp.status_code == 404
    assert resp.json()["detail"] == "lead_not_found"
    assert SpyClient.sent == []
    assert session.added == []


# --- Meta's 24h window -----------------------------------------------------


@pytest.mark.parametrize(
    ("code", "message"),
    [
        (131047, "Message failed to send because more than 24 hours have passed"),
        (470, "Re-engagement message"),
    ],
)
def test_whatsapp_outside_the_24h_window_says_so_and_stores_nothing(dev_auth, code, message):
    """Meta refuses free-form text 24h after the customer's last message. It
    is not a retryable glitch, so the API must not answer the same
    'inténtalo de nuevo' as a transient failure."""
    SpyClient.exc = _meta_error(code, message)
    conv = _conversation()
    session = ReplySession(conv, _lead(conv))

    resp = _reply(_client(session), conv)

    assert resp.status_code == 409
    assert resp.json()["detail"] == "whatsapp_window_expired"
    assert session.added == []
    assert session.commits == 0
    assert conv.status is ConversationStatus.ACTIVE


def test_other_provider_rejections_stay_a_generic_send_failure(dev_auth):
    SpyClient.exc = _meta_error(131026, "Message undeliverable")
    conv = _conversation()
    session = ReplySession(conv, _lead(conv))

    resp = _reply(_client(session), conv)

    assert resp.status_code == 502
    assert resp.json()["detail"] == "send_failed"
    assert session.added == []


def test_a_transport_error_without_response_is_a_generic_send_failure(dev_auth):
    SpyClient.exc = httpx.ConnectError("dns")
    conv = _conversation()
    session = ReplySession(conv, _lead(conv))

    resp = _reply(_client(session), conv)

    assert resp.status_code == 502
    assert resp.json()["detail"] == "send_failed"


# --- channel not actually connected ----------------------------------------


@pytest.mark.parametrize(
    "channel", [ConversationChannel.WHATSAPP, ConversationChannel.EMAIL]
)
def test_a_stubbed_send_is_never_stored_as_sent(dev_auth, channel):
    """Both provider clients log-and-stub instead of failing when their
    credentials are missing. Persisting that would show the agent a message
    the client never received."""
    SpyClient.result = "dev-stub"
    conv = _conversation(channel=channel)
    session = ReplySession(conv, _lead(conv))

    resp = _reply(_client(session), conv)

    assert resp.status_code == 409
    assert resp.json()["detail"] == "channel_not_connected"
    assert session.added == []
    assert session.commits == 0
    assert conv.status is ConversationStatus.ACTIVE


async def test_the_stub_marker_still_matches_the_real_whatsapp_client(monkeypatch):
    """Pins the coupling: if the client stops returning this marker, the
    guard above turns into a silent lie and this test fails first."""
    monkeypatch.setattr(settings, "whatsapp_access_token", SecretStr(""))
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "")

    sent = await WhatsAppClient(access_token="", phone_number_id="").send_text("34600", "hola")

    assert sent == dashboard_module.NOT_SENT_MARKER


async def test_the_stub_marker_still_matches_the_real_email_client(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", SecretStr(""))

    sent = await ResendClient().send(to="a@b.c", subject="s", text="t")

    assert sent == dashboard_module.NOT_SENT_MARKER


def test_a_send_without_an_id_is_still_a_send(dev_auth):
    """Meta accepted it but we could not read an id back: the client *did*
    get the message, so hiding it would make the agent send it twice."""
    SpyClient.result = ""
    conv = _conversation()
    session = ReplySession(conv, _lead(conv))

    resp = _reply(_client(session), conv)

    assert resp.status_code == 200
    assert resp.json()["external_message_id"] is None
    assert len(session.messages) == 1


def test_a_successful_send_is_still_stored(dev_auth):
    """The guards above must not turn every reply into an error."""
    conv = _conversation()
    session = ReplySession(conv, _lead(conv))

    resp = _reply(_client(session), conv)

    assert resp.status_code == 200
    assert len(session.messages) == 1
    assert session.messages[0].external_message_id == "wamid.OK"


# ===========================================================================
# 2. Visits agenda
# ===========================================================================


def _visit(scheduled_at: datetime, **overrides) -> SimpleNamespace:
    data = {
        "id": uuid.uuid4(),
        "agency_id": AGENCY_ID,
        "scheduled_at": scheduled_at,
        "status": visits_module.VisitStatus.CONFIRMED,
        "property_id": uuid.uuid4(),
        "pre_visit_briefing": "",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class VisitsSession:
    def __init__(self, rows=None, agency=None):
        self.rows = rows or []
        self.agency = agency
        self.statements: list = []

    async def get(self, model, pk):
        name = getattr(model, "__name__", str(model))
        if name == "Agency":
            return self.agency
        raise AssertionError(f"unexpected get({name})")

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _FakeResult(self.rows)

    async def commit(self):
        pass

    async def rollback(self):
        pass

    @property
    def last_sql(self) -> str:
        return _flat(self.statements[-1])

    @property
    def last_joins(self) -> str:
        return _joins(self.last_sql)

    @property
    def last_params(self) -> dict:
        return self.statements[-1].compile().params


def _flat(stmt) -> str:
    """Rendered statement on a single line — SQLAlchemy breaks FROM/WHERE
    onto their own lines, which makes substring assertions miss."""
    return " ".join(str(stmt).split())


def _joins(sql: str) -> str:
    """Only the ON clauses. Asserting on the whole statement would pass for
    free: `SELECT properties.agency_id` is in there either way."""
    clauses = []
    for chunk in sql.split(" ON ")[1:]:
        for stop in (" JOIN ", " WHERE ", " ORDER BY ", " LIMIT "):
            chunk = chunk.split(stop, 1)[0]
        clauses.append(chunk)
    return " | ".join(clauses)


def _agency(timezone: str = "Europe/Madrid") -> SimpleNamespace:
    return SimpleNamespace(id=AGENCY_ID, name="Inmo Sol", timezone=timezone, settings={})


def _list_visits(client, params: dict | None = None):
    return client.get(f"/api/v1/agencies/{AGENCY_ID}/visits", params=params or {})


def test_the_property_join_cannot_reach_another_agencys_listing(dev_auth):
    """`visits.agency_id` is filtered, but the join to properties was not:
    a visit pointing at a foreign property would show its title."""
    session = VisitsSession(agency=_agency())

    resp = _list_visits(_client(session))

    assert resp.status_code == 200
    assert "properties.agency_id" in session.last_joins


def test_the_lead_join_cannot_reach_another_agencys_lead(dev_auth):
    session = VisitsSession(agency=_agency())

    _list_visits(_client(session))

    assert "leads.agency_id" in session.last_joins


def test_a_foreign_property_row_degrades_to_no_title(dev_auth):
    """With the scoped outer join the visit still shows — without the other
    tenant's data."""
    visit = _visit(datetime(2026, 7, 27, 17, 30, tzinfo=UTC))
    session = VisitsSession(rows=[(visit, None, None)], agency=_agency())

    item = _list_visits(_client(session)).json()[0]

    assert item["property_title"] is None
    assert item["lead_name"] is None
    assert item["lead_phone"] is None


def test_each_visit_carries_the_agency_timezone(dev_auth):
    """The dashboard groups the agenda by day and prints the hour: doing
    that in the server's timezone puts a 00:30 Madrid visit on the previous
    day and shows 22:30."""
    visit = _visit(datetime(2026, 7, 26, 22, 30, tzinfo=UTC))
    session = VisitsSession(rows=[(visit, None, None)], agency=_agency())

    item = _list_visits(_client(session)).json()[0]

    assert item["timezone"] == "Europe/Madrid"


def test_a_broken_agency_timezone_falls_back_instead_of_500(dev_auth):
    visit = _visit(datetime(2026, 7, 26, 22, 30, tzinfo=UTC))
    session = VisitsSession(rows=[(visit, None, None)], agency=_agency(timezone="Marte/Olympus"))

    resp = _list_visits(_client(session))

    assert resp.status_code == 200
    assert resp.json()[0]["timezone"] == "Europe/Madrid"


def test_a_missing_agency_row_does_not_take_the_agenda_down(dev_auth):
    visit = _visit(datetime(2026, 7, 27, 9, 0, tzinfo=UTC))
    session = VisitsSession(rows=[(visit, None, None)], agency=None)

    resp = _list_visits(_client(session))

    assert resp.status_code == 200
    assert resp.json()[0]["timezone"] == "Europe/Madrid"


def test_upcoming_bounds_the_agenda_at_midnight_in_the_agency_timezone(dev_auth):
    session = VisitsSession(agency=_agency())

    resp = _list_visits(_client(session), {"upcoming": "true"})

    assert resp.status_code == 200
    assert "visits.scheduled_at >= " in session.last_sql
    bound = next(
        value
        for value in session.last_params.values()
        if isinstance(value, datetime)
    )
    madrid = ZoneInfo("Europe/Madrid")
    assert bound.tzinfo is not None
    assert (bound.hour, bound.minute, bound.second) == (0, 0, 0)
    assert bound.date() == datetime.now(madrid).date()
    assert bound.utcoffset() == datetime.now(madrid).utcoffset()


def test_an_explicit_from_still_wins_over_upcoming(dev_auth):
    session = VisitsSession(agency=_agency())
    since = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)

    _list_visits(_client(session), {"upcoming": "true", "from": since.isoformat()})

    assert since in list(session.last_params.values())


def test_without_upcoming_the_agenda_is_still_unbounded(dev_auth):
    session = VisitsSession(agency=_agency())

    _list_visits(_client(session))

    assert "scheduled_at >= " not in session.last_sql


# ===========================================================================
# 3. Calls listing
# ===========================================================================


def _voice_conversation(calls: list) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        extra={"calls": calls},
        external_thread_id=None,
        updated_at=datetime.now(UTC),
    )


def _voice_lead() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(), full_name="Juan Pérez", phone_e164="+34600111222", extra={}
    )


def test_a_malformed_call_record_does_not_break_the_listing():
    """`conversation.extra["calls"]` is filled straight from the Vapi
    webhook — every value there is whatever the provider sent."""
    conv = _voice_conversation(
        [
            {
                "call_id": 991,
                "ended_at": "2026-07-20T10:00:00+00:00",
                "duration_seconds": "no consta",
                "recording_url": {"url": "https://vapi.example/rec.mp3"},
                "summary": ["troceado"],
            }
        ]
    )

    calls = _call_entries(conv, _voice_lead())

    assert len(calls) == 1
    call = calls[0]
    assert call.call_id == "991"
    assert call.duration_seconds is None
    assert call.recording_url is None
    assert call.transcript_summary is None


def test_a_numeric_duration_sent_as_text_is_still_shown():
    conv = _voice_conversation([{"call_id": "a", "duration_seconds": "95.5"}])

    assert _call_entries(conv, _voice_lead())[0].duration_seconds == 95.5


def test_a_recording_url_that_is_not_http_is_dropped():
    """It ends up in an <audio src>; only real links get there."""
    conv = _voice_conversation(
        [{"call_id": "a", "recording_url": "javascript:alert(1)"}]
    )

    assert _call_entries(conv, _voice_lead())[0].recording_url is None


def test_a_good_record_next_to_a_broken_one_survives():
    conv = _voice_conversation(
        [
            {"call_id": "bad", "duration_seconds": {"secs": 3}},
            {
                "call_id": "good",
                "ended_at": "2026-07-21T16:30:00+00:00",
                "duration_seconds": 40.5,
                "recording_url": "https://vapi.example/ok.mp3",
                "summary": "Quiere ver el ático.",
            },
        ]
    )

    calls = _call_entries(conv, _voice_lead())

    assert [c.call_id for c in calls] == ["bad", "good"]
    assert calls[0].duration_seconds is None
    assert calls[1].recording_url == "https://vapi.example/ok.mp3"


class CallsSession:
    def __init__(self, rows):
        self.rows = rows
        self.statements: list = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _FakeResult(self.rows)

    async def commit(self):
        pass

    async def rollback(self):
        pass

    @property
    def last_sql(self) -> str:
        return _flat(self.statements[-1])

    @property
    def last_joins(self) -> str:
        return _joins(self.last_sql)


def test_calls_endpoint_survives_a_poisoned_record(dev_auth):
    conv = _voice_conversation([{"call_id": 7, "duration_seconds": "n/d"}])
    session = CallsSession([(conv, _voice_lead())])

    resp = _client(session).get(f"/api/v1/agencies/{AGENCY_ID}/calls")

    assert resp.status_code == 200
    assert resp.json()[0]["call_id"] == "7"


def test_calls_listing_is_scoped_to_the_agency_on_both_tables(dev_auth):
    session = CallsSession([])

    _client(session).get(f"/api/v1/agencies/{AGENCY_ID}/calls")

    assert "conversations.agency_id" in session.last_sql.split(" WHERE ", 1)[1]
    assert "leads.agency_id" in session.last_joins


# ===========================================================================
# 4. Catalog import / upsert
# ===========================================================================


def _existing_property(**overrides) -> SimpleNamespace:
    data = {
        "agency_id": AGENCY_ID,
        "reference": "REF-1",
        "title": "Local en Chamberí",
        "description": "Texto escrito a mano por la agencia.",
        "property_type": PropertyType.COMMERCIAL,
        "operation": "rent",
        "price": 100000,
        "address": "Calle Pez 10",
        "neighborhood": "Chamberí",
        "city": "Madrid",
        "postal_code": "28010",
        "bedrooms": 2,
        "bathrooms": 1,
        "area_sqm": 70,
        "extra": {"import_source": "csv"},
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_an_unrecognized_type_never_retypes_a_curated_listing():
    """`tipo` = "Nave industrial" is not in our alias table; guessing
    'apartamento' would silently change what the AI offers."""
    parsed = parse_properties_csv(
        b"referencia,titulo,precio,tipo\nREF-1,Local,185000,Nave industrial\n"
    )
    prop = _existing_property()

    apply_row_to_property(prop, parsed.rows[0], parsed.provided[0])

    assert prop.property_type is PropertyType.COMMERCIAL


def test_an_unrecognized_operation_never_flips_rent_to_sale():
    parsed = parse_properties_csv(
        b"referencia,titulo,precio,operacion\nREF-1,Local,185000,Traspaso\n"
    )
    prop = _existing_property()

    apply_row_to_property(prop, parsed.rows[0], parsed.provided[0])

    assert prop.operation == "rent"


def test_a_recognized_type_does_update_the_listing():
    parsed = parse_properties_csv(
        b"referencia,titulo,precio,tipo,operacion\nREF-1,Chalet,185000,chalet,alquiler\n"
    )
    prop = _existing_property()

    apply_row_to_property(prop, parsed.rows[0], parsed.provided[0])

    assert prop.property_type is PropertyType.HOUSE
    assert prop.operation == "rent"


def test_a_new_row_with_an_unrecognized_type_still_gets_a_type():
    """The column is NOT NULL; creation has to pick something."""
    parsed = parse_properties_csv(
        b"referencia,titulo,precio,tipo\nREF-9,Nave,185000,Nave industrial\n"
    )

    assert parsed.rows[0]["property_type"] is PropertyType.APARTMENT
    assert "property_type" not in parsed.provided[0]


def test_empty_cells_never_blank_curated_columns():
    parsed = parse_properties_csv(
        b"referencia,titulo,precio,ciudad,descripcion,barrio\nREF-1,Local,185000,,,\n"
    )
    prop = _existing_property()

    apply_row_to_property(prop, parsed.rows[0], parsed.provided[0])

    assert prop.city == "Madrid"
    assert prop.description == "Texto escrito a mano por la agencia."
    assert prop.neighborhood == "Chamberí"


def test_a_row_with_an_unusable_price_is_skipped_not_zeroed():
    parsed = parse_properties_csv(
        b"referencia,titulo,precio\nREF-1,Local,a consultar\n"
    )

    assert parsed.rows == []
    assert parsed.skipped == 1


class _NoSubscriptionResult:
    """The import's plan lookup: no subscription row -> not metered."""

    def scalar_one_or_none(self):
        return None


class ImportSession:
    def __init__(self, existing: list):
        self.existing = existing
        self.added: list = []
        self.statements: list = []
        self.commits = 0

    async def execute(self, stmt):
        self.statements.append(stmt)
        if "subscriptions" in str(stmt):
            return _NoSubscriptionResult()
        return _FakeResult(self.existing)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass

    @property
    def last_sql(self) -> str:
        return _flat(self.statements[-1])

    @property
    def last_params(self) -> dict:
        return self.statements[-1].compile().params


@pytest.fixture
def no_broker(monkeypatch):
    from app.workers import tasks

    monkeypatch.setattr(
        tasks.embed_missing_properties, "delay", lambda *a, **k: None, raising=False
    )


def _import(client, payload: bytes):
    return client.post(
        f"/api/v1/agencies/{AGENCY_ID}/properties/import",
        files={"file": ("catalogo.csv", payload, "text/csv")},
    )


CSV = b"referencia,titulo,precio,ciudad\nREF-1,Piso reformado,185000,Madrid\n"


def test_the_reference_lookup_is_scoped_to_the_agency(dev_auth, no_broker):
    session = ImportSession(existing=[])

    _import(_client(session), CSV)

    assert "properties.agency_id" in session.last_sql.split(" WHERE ", 1)[1]
    assert AGENCY_ID in session.last_params.values()


def test_a_reference_owned_by_another_agency_is_never_overwritten(dev_auth, no_broker):
    """Belt and braces: if the lookup ever stops filtering by tenant, the
    write site must still refuse to touch someone else's listing."""
    foreign = Property(
        agency_id=OTHER_AGENCY_ID,
        reference="REF-1",
        title="Piso de la competencia",
        property_type=PropertyType.APARTMENT,
        price=100000,
        city="Barcelona",
        extra={},
    )
    session = ImportSession(existing=[foreign])

    body = _import(_client(session), CSV).json()

    assert foreign.title == "Piso de la competencia"
    assert foreign.price == 100000
    assert body["created"] == 1
    assert body["updated"] == 0
    assert [p.agency_id for p in session.added] == [AGENCY_ID]


def test_the_endpoint_keeps_the_curated_type_on_an_unknown_csv_value(dev_auth, no_broker):
    """End to end: the row and its `provided` set have to stay paired all the
    way from the parser to the update."""
    known = Property(
        agency_id=AGENCY_ID,
        reference="REF-1",
        title="Local en Chamberí",
        property_type=PropertyType.COMMERCIAL,
        operation="rent",
        price=100000,
        city="Madrid",
        extra={},
    )
    session = ImportSession(existing=[known])

    body = _import(
        _client(session),
        b"referencia,titulo,precio,tipo,operacion\n"
        b"REF-0,Piso nuevo,120000,piso,venta\n"
        b"REF-1,Local reformado,185000,Nave industrial,Traspaso\n",
    ).json()

    assert body["created"] == 1
    assert body["updated"] == 1
    assert known.title == "Local reformado"
    assert known.price == 185000
    assert known.property_type is PropertyType.COMMERCIAL
    assert known.operation == "rent"
    # The genuinely new row still gets the type the file did name.
    assert session.added[0].property_type is PropertyType.APARTMENT
    assert session.added[0].operation == "sale"


def test_a_reimport_that_changes_nothing_reports_it_as_updated(dev_auth, no_broker):
    """`imported` must keep meaning "rows we took in", not "rows we wrote"."""
    known = Property(
        agency_id=AGENCY_ID,
        reference="REF-1",
        title="Piso reformado",
        property_type=PropertyType.APARTMENT,
        price=185000,
        city="Madrid",
        extra={},
    )
    session = ImportSession(existing=[known])

    body = _import(_client(session), CSV).json()

    assert body == {
        "imported": 1,
        "created": 0,
        "updated": 1,
        "skipped": 0,
        "errors": [],
        # No subscription row in this fake session, so the properties limit
        # does not apply and nothing was left out (see test_plan_gating.py
        # for the per-row cap itself).
        "limit_skipped": 0,
        "limit_reason": None,
        "properties_limit": None,
    }
    assert session.added == []


def test_visits_range_filter_still_reaches_the_query(dev_auth):
    """Regression guard for the range params after the join rework."""
    session = VisitsSession(agency=_agency())
    since = datetime(2026, 7, 26, tzinfo=UTC)
    until = since + timedelta(days=5)

    _list_visits(
        _client(session), {"from": since.isoformat(), "to": until.isoformat()}
    )

    values = list(session.last_params.values())
    assert since in values
    assert until in values
