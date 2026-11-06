"""(169) CSV import upserts by `reference` instead of duplicating.

Two layers:
  * `apply_row_to_property` — pure field-by-field refresh.
  * POST /properties/import — resolves existing references, counts
    created/updated/skipped and never adds a second row for a reference the
    agency already has (the AI offering the same flat twice is the bug that
    destroys credibility fastest).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.settings import settings
from app.db.models import Property, PropertyType
from app.db.session import get_db
from app.main import app
from app.services.property_import import apply_row_to_property, parse_properties_csv

AGENCY_ID = uuid.uuid4()

HEADER = "referencia,titulo,precio,ciudad,habitaciones,descripcion\n"


def _csv(*lines: str) -> bytes:
    return (HEADER + "".join(line + "\n" for line in lines)).encode("utf-8")


# ---------------------------------------------------------------------------
# Pure helper
# ---------------------------------------------------------------------------


def _existing(**overrides) -> SimpleNamespace:
    data = {
        "reference": "REF-1",
        "title": "Piso viejo",
        "description": "Descripción escrita a mano por la agencia.",
        "property_type": PropertyType.APARTMENT,
        "operation": "sale",
        "price": 100000,
        "address": "",
        "neighborhood": None,
        "city": "Madrid",
        "postal_code": None,
        "bedrooms": 2,
        "bathrooms": 1,
        "area_sqm": 70,
        "extra": {"import_source": "csv"},
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_apply_row_updates_the_fields_the_file_carries():
    parsed = parse_properties_csv(_csv("REF-1,Piso reformado,185.000 €,Madrid,3,Muy luminoso"))
    prop = _existing()

    changed = apply_row_to_property(prop, parsed.rows[0], parsed.provided[0])

    assert changed is True
    assert prop.title == "Piso reformado"
    assert prop.price == 185000
    assert prop.bedrooms == 3
    assert prop.description == "Muy luminoso"


def test_apply_row_never_blanks_a_column_the_file_omits():
    """The monthly export rarely carries the description the agency wrote."""
    csv_bytes = b"referencia,titulo,precio\nREF-1,Piso reformado,185000\n"
    parsed = parse_properties_csv(csv_bytes)
    prop = _existing()

    apply_row_to_property(prop, parsed.rows[0], parsed.provided[0])

    assert prop.description == "Descripción escrita a mano por la agencia."
    # `property_type`/`operation` have parser defaults — absent columns must
    # not silently retype the listing.
    assert prop.property_type is PropertyType.APARTMENT
    assert prop.bedrooms == 2
    assert prop.title == "Piso reformado"


def test_apply_row_reports_no_change_when_nothing_moved():
    parsed = parse_properties_csv(_csv("REF-1,Piso viejo,100000,Madrid,2,x"))
    prop = _existing(description="x")

    assert apply_row_to_property(prop, parsed.rows[0], parsed.provided[0]) is False


def test_apply_row_never_touches_the_reference():
    parsed = parse_properties_csv(_csv("REF-1,Piso reformado,185000,Madrid,3,Luminoso"))
    prop = _existing(reference="REF-1")

    apply_row_to_property(prop, parsed.rows[0], parsed.provided[0])

    assert prop.reference == "REF-1"


def test_provided_is_parallel_to_rows():
    parsed = parse_properties_csv(
        _csv(
            "REF-1,Piso uno,100000,Madrid,2,uno",
            ",,sin precio,,,",
            "REF-3,Piso tres,300000,Madrid,4,tres",
        )
    )
    assert len(parsed.provided) == len(parsed.rows) == 2
    assert "title" in parsed.provided[0]
    assert parsed.skipped == 1


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeNoSubscription:
    """The plan lookup (`ensure_subscription_usable` + `usage_for`): no
    subscription row -> not metered, so the gate passes and the import is
    not capped. The per-row cap itself is covered in test_plan_gating.py."""

    def scalar_one_or_none(self):
        return None


class FakeSession:
    def __init__(self, existing: list):
        self.existing = existing
        self.added: list = []
        self.commits = 0

    async def execute(self, stmt):
        sql = str(stmt)
        if "subscriptions" in sql:
            return _FakeNoSubscription()
        assert "properties" in sql
        return _FakeResult(self.existing)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


@pytest.fixture(autouse=True)
def no_broker(monkeypatch):
    """The endpoint enqueues the embedding backfill after a successful
    import; without Redis, Celery would sit in its connection retry loop."""
    from app.workers import tasks

    monkeypatch.setattr(
        tasks.embed_missing_properties, "delay", lambda *a, **k: None, raising=False
    )


@pytest.fixture
def make_client(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")

    def _build(session: FakeSession) -> TestClient:
        async def _override():
            yield session

        app.dependency_overrides[get_db] = _override
        return TestClient(app)

    yield _build
    app.dependency_overrides.pop(get_db, None)


def _upload(client, payload: bytes):
    return client.post(
        f"/api/v1/agencies/{AGENCY_ID}/properties/import",
        files={"file": ("catalogo.csv", payload, "text/csv")},
    )


def test_new_references_are_created(make_client):
    session = FakeSession(existing=[])

    body = _upload(
        make_client(session),
        _csv("REF-1,Piso uno,100000,Madrid,2,uno", "REF-2,Piso dos,200000,Madrid,3,dos"),
    ).json()

    assert body == {
        "imported": 2,
        "created": 2,
        "updated": 0,
        "skipped": 0,
        "errors": [],
        # No plan row -> nothing was left out for lack of slots.
        "limit_skipped": 0,
        "limit_reason": None,
        "properties_limit": None,
    }
    assert len(session.added) == 2
    assert {p.reference for p in session.added} == {"REF-1", "REF-2"}


def test_known_reference_updates_instead_of_duplicating(make_client):
    known = Property(
        agency_id=AGENCY_ID,
        reference="REF-1",
        title="Piso uno",
        property_type=PropertyType.APARTMENT,
        price=100000,
        city="Madrid",
        bedrooms=2,
        description="antigua",
        extra={},
    )
    session = FakeSession(existing=[known])

    body = _upload(
        make_client(session),
        _csv(
            "REF-1,Piso uno reformado,185000,Madrid,3,nueva",
            "REF-2,Piso dos,200000,Madrid,3,dos",
        ),
    ).json()

    assert body["created"] == 1
    assert body["updated"] == 1
    assert body["imported"] == 2
    # Only the genuinely new listing was added — no duplicate for REF-1.
    assert [p.reference for p in session.added] == ["REF-2"]
    assert known.title == "Piso uno reformado"
    assert known.price == 185000
    assert known.bedrooms == 3
    assert known.description == "nueva"


def test_reimporting_the_same_file_creates_nothing(make_client):
    known = [
        Property(
            agency_id=AGENCY_ID,
            reference=ref,
            title=title,
            property_type=PropertyType.APARTMENT,
            price=price,
            city="Madrid",
            extra={},
        )
        for ref, title, price in (("REF-1", "Piso uno", 100000), ("REF-2", "Piso dos", 200000))
    ]
    session = FakeSession(existing=known)

    body = _upload(
        make_client(session),
        _csv("REF-1,Piso uno,100000,Madrid,2,uno", "REF-2,Piso dos,200000,Madrid,3,dos"),
    ).json()

    assert body["created"] == 0
    assert body["updated"] == 2
    assert session.added == []


def test_a_reference_repeated_inside_the_file_is_not_duplicated(make_client):
    session = FakeSession(existing=[])

    body = _upload(
        make_client(session),
        _csv("REF-1,Piso uno,100000,Madrid,2,uno", "REF-1,Piso uno bis,150000,Madrid,3,bis"),
    ).json()

    assert body["created"] == 1
    assert body["updated"] == 1
    assert len(session.added) == 1
    assert session.added[0].title == "Piso uno bis"
    assert session.added[0].price == 150000


def test_broken_rows_are_still_reported_as_skipped(make_client):
    session = FakeSession(existing=[])

    body = _upload(
        make_client(session),
        _csv("REF-1,Piso uno,100000,Madrid,2,uno", "REF-2,,a consultar,Madrid,3,dos"),
    ).json()

    assert body["created"] == 1
    assert body["updated"] == 0
    assert body["skipped"] == 1
    assert body["errors"]


def test_empty_file_is_rejected(make_client):
    resp = _upload(make_client(FakeSession(existing=[])), b"")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "empty_file"
