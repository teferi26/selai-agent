"""The public demo agency seed (app/scripts/seed_demo_public.py).

It runs on every container boot, so the only property that really matters is
idempotency: second run must create nothing. It must also stay free of a
subscription — the demo is never metered or billed.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest

from app.db.models import Agency, Property, PropertyStatus, PropertyType, Subscription
from app.scripts import seed_demo_public


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class FakeSession:
    """Tiny in-memory stand-in: agencies by slug, property references per agency."""

    def __init__(self):
        self.agencies: list = []
        self.properties: list = []

    async def execute(self, stmt):
        sql = str(stmt)
        if "FROM agencies" in sql:
            return _Result(self.agencies)
        if "FROM properties" in sql:
            return _Result([(p.reference,) for p in self.properties])
        raise AssertionError(f"unexpected query: {sql}")

    def add(self, obj):
        if isinstance(obj, Agency):
            obj.id = obj.id or uuid.uuid4()
            self.agencies.append(obj)
        elif isinstance(obj, Property):
            self.properties.append(obj)
        else:  # pragma: no cover - the seed only adds these two
            raise AssertionError(f"unexpected insert: {type(obj)}")

    async def flush(self):
        for agency in self.agencies:
            if agency.id is None:
                agency.id = uuid.uuid4()


@pytest.fixture
def db(monkeypatch):
    session = FakeSession()

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(seed_demo_public, "session_scope", _scope)
    return session


async def test_first_run_creates_the_agency_and_six_properties(db):
    await seed_demo_public.seed()

    assert len(db.agencies) == 1
    agency = db.agencies[0]
    assert agency.slug == "demo"
    assert agency.name == "Inmobiliaria Demo selai"
    assert agency.settings["is_public_demo"] is True

    assert len(db.properties) == 6
    assert all(p.agency_id == agency.id for p in db.properties)


async def test_second_run_creates_nothing(db):
    await seed_demo_public.seed()
    await seed_demo_public.seed()

    assert len(db.agencies) == 1
    assert len(db.properties) == 6


async def test_partial_catalogue_is_completed_not_duplicated(db):
    await seed_demo_public.seed()
    # Someone deleted two listings from the panel.
    db.properties = db.properties[:4]

    await seed_demo_public.seed()

    refs = [p.reference for p in db.properties]
    assert len(refs) == 6
    assert len(set(refs)) == 6


async def test_demo_agency_has_no_subscription(db):
    await seed_demo_public.seed()
    assert not any(isinstance(o, Subscription) for o in db.properties + db.agencies)


# ---------------------------------------------------------------------------
# Catalogue quality — the demo chat answers with these, so they must be usable
# ---------------------------------------------------------------------------


def test_catalogue_covers_madrid_and_valencia_sale_and_rent():
    specs = seed_demo_public.DEMO_PROPERTIES
    assert len(specs) == 6

    cities = {s["city"] for s in specs}
    assert "Madrid" in cities
    assert "Valencia" in cities

    operations = {s["operation"] for s in specs}
    assert operations == {"sale", "rent"}


def test_every_listing_is_answerable():
    for spec in seed_demo_public.DEMO_PROPERTIES:
        assert spec["price"] > 0
        assert spec["area_sqm"] > 0
        assert spec["bedrooms"] is not None
        assert len(spec["description"]) > 200, spec["reference"]
        assert isinstance(spec["property_type"], PropertyType)


def test_references_are_unique():
    refs = [s["reference"] for s in seed_demo_public.DEMO_PROPERTIES]
    assert len(refs) == len(set(refs))


def test_rentals_are_priced_monthly_not_as_sales():
    """A 1.150 EUR "sale" would make the demo agent talk nonsense."""
    for spec in seed_demo_public.DEMO_PROPERTIES:
        if spec["operation"] == "rent":
            assert spec["price"] < 10_000
        else:
            assert spec["price"] > 100_000


async def test_seeded_listings_are_available_and_spanish(db):
    await seed_demo_public.seed()
    assert all(p.status is PropertyStatus.AVAILABLE for p in db.properties)
    assert all(p.country == "ES" for p in db.properties)


def test_release_job_runs_the_public_seed_not_the_web_process():
    from pathlib import Path

    from app.scripts.release import RELEASE_STEPS

    commands = [" ".join(step) for step in RELEASE_STEPS]
    assert commands.index(next(c for c in commands if "seed_plans" in c)) < commands.index(
        next(c for c in commands if "seed_demo_public" in c)
    )

    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
    cmd = dockerfile.read_text(encoding="utf-8")
    assert "app.scripts.seed_demo_public" not in cmd
    assert "alembic upgrade" not in cmd
    assert "uvicorn app.main:app" in cmd
