"""(172) `sweep_price_drops` builds a query that actually runs.

`Property.extra` is the generic sqlalchemy JSON type, so indexed
expressions have no postgres-only `.astext`; using it raised AttributeError
before a single row was fetched, which meant the Beat task died every 30
minutes and no lead ever got a price-drop alert. Same bug (and same fix)
as api/whatsapp.py and api/sms.py.

Grouped with the billing tests because the fix shipped with that batch.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.db.models import LeadStatus
from app.workers import tasks

AGENCY_ID = uuid.uuid4()
PROPERTY_ID = uuid.uuid4()


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class FakeSession:
    def __init__(self, properties, leads):
        self.properties = properties
        self.leads = leads
        self.sql: list[str] = []
        self.params: list[dict] = []

    async def get(self, model, pk):
        # (n21) el helper de WhatsApp resuelve la agencia para usar SUS
        # credenciales; sin fila, cae al cliente global (fake en estos tests).
        return None

    async def execute(self, stmt):
        # Compiling is the point: `.astext` blew up right here.
        sql = str(stmt)
        self.sql.append(sql)
        self.params.append(dict(stmt.compile().params))
        if "FROM properties" in sql:
            return _Result(self.properties)
        if "FROM leads" in sql:
            return _Result(self.leads)
        raise AssertionError(f"unexpected query: {sql}")


def _property(extra=None):
    return SimpleNamespace(
        id=PROPERTY_ID,
        agency_id=AGENCY_ID,
        title="Piso en Chamberí",
        operation="sale",
        price=380_000,
        city="Madrid",
        neighborhood="Chamberí",
        bedrooms=3,
        price_history=[{"from": 420_000, "to": 380_000}],
        extra={"pending_price_drop_alert": "true"} if extra is None else extra,
        last_price_alert_at=None,
    )


def _lead(**kw):
    base = dict(
        id=uuid.uuid4(),
        agency_id=AGENCY_ID,
        status=LeadStatus.QUALIFIED,
        whatsapp_wa_id="34600111222",
        email=None,
        interested_property_ids=[str(PROPERTY_ID)],
        operation=None,
        budget_min=None,
        budget_max=None,
        min_bedrooms=None,
        preferred_cities=None,
        preferred_neighborhoods=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def channels(monkeypatch):
    sent = SimpleNamespace(whatsapp=[], email=[])

    class _WA:
        def __init__(self, **kwargs):
            pass

        async def send_text(self, wa_id, text):
            sent.whatsapp.append({"to": wa_id, "text": text})
            # Como el cliente real: el id del mensaje es la prueba de entrega.
            return "wamid.test"

    class _Resend:
        async def send(self, to, subject, text):
            sent.email.append({"to": to, "subject": subject, "text": text})
            return "resend-id"

    monkeypatch.setattr("app.channels.whatsapp.client.WhatsAppClient", _WA)
    monkeypatch.setattr("app.channels.email.client.ResendClient", _Resend)
    return sent


def _scope(session):
    @asynccontextmanager
    async def _fake():
        yield session

    return _fake


async def test_sweep_query_compiles_and_notifies(monkeypatch, channels):
    prop = _property()
    db = FakeSession([prop], [_lead()])
    monkeypatch.setattr(tasks, "session_scope", _scope(db))

    await tasks._sweep_price_drops()

    # The flag is read as a bound string comparison on the JSON column —
    # building this statement is exactly what used to raise AttributeError.
    assert "FROM properties" in db.sql[0]
    assert set(db.params[0].values()) == {"pending_price_drop_alert", "true"}

    assert len(channels.whatsapp) == 1
    body = channels.whatsapp[0]["text"]
    assert "420,000" in body and "380,000" in body
    assert "-9.5%" in body


async def test_flag_is_cleared_after_the_sweep(monkeypatch, channels):
    prop = _property()
    db = FakeSession([prop], [_lead()])
    monkeypatch.setattr(tasks, "session_scope", _scope(db))

    await tasks._sweep_price_drops()

    assert "pending_price_drop_alert" not in prop.extra
    assert prop.extra["last_drop_alert_notified"] == 1
    assert prop.last_price_alert_at is not None


async def test_price_increase_notifies_nobody(monkeypatch, channels):
    prop = _property()
    prop.price_history = [{"from": 350_000, "to": 380_000}]
    db = FakeSession([prop], [_lead()])
    monkeypatch.setattr(tasks, "session_scope", _scope(db))

    await tasks._sweep_price_drops()

    assert channels.whatsapp == []


async def test_lead_outside_the_criteria_is_skipped(monkeypatch, channels):
    prop = _property()
    outsider = _lead(interested_property_ids=[], preferred_cities=["Valencia"])
    db = FakeSession([prop], [outsider])
    monkeypatch.setattr(tasks, "session_scope", _scope(db))

    await tasks._sweep_price_drops()

    assert channels.whatsapp == []
    assert prop.extra["last_drop_alert_notified"] == 0
