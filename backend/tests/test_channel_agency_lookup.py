"""Settings-mapping agency lookups for the SMS and Google Business channels.

Regression: these queries used the postgres-only `.astext` on the generic
sqlalchemy JSON column, which raised AttributeError while *building* the
statement — every inbound SMS / Business Messages webhook crashed with a 500
before reaching any handler logic.
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from app.api.google_business import _agency_for_gbm_agent
from app.api.sms import _agency_for_number


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value


class FakeSession:
    """Returns the same agency for every query; records the statements so a
    broken expression would blow up inside the helper under test."""

    def __init__(self, agency):
        self.agency = agency
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result(self.agency)


def _agency() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), settings={})


def test_sms_lookup_builds_json_string_match():
    agency = _agency()
    db = FakeSession(agency)

    found = asyncio.run(_agency_for_number(db, "+34600111222"))

    # The row, not just the id: the SMS webhook needs the settings blob to
    # know which Twilio auth token signed the request and which credentials
    # to answer with (see app/api/sms.py).
    assert found is agency
    assert len(db.statements) == 1  # exact match found: no fallback queries
    sql = str(db.statements[0]).lower()
    assert "settings" in sql and "where" in sql


def test_gbm_lookup_builds_json_string_match():
    agency = _agency()
    db = FakeSession(agency)

    found = asyncio.run(_agency_for_gbm_agent(db, "brands/x/agents/y"))

    assert found == agency.id
    assert len(db.statements) == 1
    sql = str(db.statements[0]).lower()
    assert "settings" in sql and "where" in sql
