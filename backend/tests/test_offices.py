"""Multi-oficina (plan Agencia).

What the site sells, verbatim (dashboard/src/components/marketing):

* "Varias oficinas: cada contacto va a la que le corresponde"
* "Cada contacto va solo a la oficina que le corresponde"
* "Comparte contactos entre tus oficinas"

Four things have to hold for that to be true without breaking everyone else,
and each has its own section below:

1. an agency with no offices notices nothing — the listings build the very
   same statement they built before this feature existed;
2. the filter is a tenant boundary, not a convenience: another agency's
   office id is a 404, and a lead linked to office B never comes back when
   filtering by office A;
3. creating an office needs the Agencia plan and the owner's role;
4. the migration only adds, and undoes exactly what it added.

Section 2 runs the *production* predicate against a SQLite mirror of the
schema. The real tables cannot be created outside Postgres (UUID, ARRAY and
pgvector columns), and `lead_in_office` takes its models as keyword
arguments precisely so the thing that decides who sees whose contacts is
executed here rather than merely compiled.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import String, create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.api import dashboard, offices
from app.core.settings import settings
from app.db.session import get_db
from app.main import app
from app.services import plan_limits

AGENCY_ID = uuid.uuid4()
OTHER_AGENCY_ID = uuid.uuid4()

# The offices router is mounted on an app of its own rather than on
# `app.main.app`. It is not registered in main.py yet (that file is edited by
# hand, one line of import and one of include_router), and mounting it onto
# the shared singleton from a test would duplicate every route the moment it
# is. Nothing about the router depends on which app carries it.
offices_app = FastAPI()
offices_app.include_router(offices.router)


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class FakeResult:
    def __init__(self, payload):
        self._payload = list(payload)

    def all(self):
        return list(self._payload)

    def first(self):
        return self._payload[0] if self._payload else None

    def scalars(self):
        return self

    def scalar_one_or_none(self):
        return self._payload[0] if self._payload else None

    def scalar_one(self):
        return self._payload[0] if self._payload else 0


class FakeSession:
    """Hands back canned results in order; the last one repeats."""

    def __init__(self, *results):
        self.results = list(results) or [[]]
        self.statements: list = []
        self.added: list = []
        self.deleted: list = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        payload = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        return FakeResult(payload)

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def refresh(self, obj):
        # Stand-in for the server-side defaults a real flush would fill in.
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(UTC)

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def get(self, model, pk):
        return None


@pytest.fixture
def db():
    session = FakeSession()

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    offices_app.dependency_overrides[get_db] = _override
    yield session
    app.dependency_overrides.pop(get_db, None)
    offices_app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def _dev_auth(monkeypatch):
    # No Clerk configured -> dev bypass, which satisfies both
    # `ensure_agency_access` and `require_role`.
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")


@pytest.fixture
def client(_dev_auth, db):
    """The real app — for the panel listings (`/leads`, `/inbox`)."""
    return TestClient(app)


@pytest.fixture
def offices_client(_dev_auth, db):
    return TestClient(offices_app)


def _patch_plan(monkeypatch, features: dict | None, code: str = "agencia"):
    plan = SimpleNamespace(code=code, features=features, monthly_price_eur=399)

    async def fake_get_plan_state(db, agency_id):
        return (None, plan)

    monkeypatch.setattr(plan_limits, "get_plan_state", fake_get_plan_state)


# ---------------------------------------------------------------------------
# 1. An agency with no offices notices nothing
# ---------------------------------------------------------------------------


def test_leads_filter_without_office_adds_no_clause():
    """The regla de oro, executed on the statement itself."""
    sql = _sql(dashboard._leads_filtered(AGENCY_ID, None, None))

    assert "EXISTS" not in sql
    assert "office" not in sql
    assert "offices" not in sql


def test_leads_filter_is_identical_whether_or_not_office_is_passed_as_none():
    """`office=None` must be the same statement as not passing it at all —
    the default is what every existing caller (mobile app included) hits."""
    assert _sql(dashboard._leads_filtered(AGENCY_ID, None, None)) == _sql(
        dashboard._leads_filtered(AGENCY_ID, None, None, None)
    )


def test_lead_filters_signature_keeps_office_optional_and_last():
    """A positional-arg caller elsewhere in the codebase must not shift."""
    conditions = dashboard._lead_filters(AGENCY_ID, None, None)

    assert len(conditions) == 1  # only the agency_id predicate


def test_leads_endpoint_still_answers_a_bare_array(client, db):
    resp = client.get(f"/api/v1/agencies/{AGENCY_ID}/leads")

    assert resp.status_code == 200
    assert resp.json() == []


def test_inbox_endpoint_still_answers_a_bare_array(client, db):
    resp = client.get(f"/api/v1/agencies/{AGENCY_ID}/inbox")

    assert resp.status_code == 200
    assert resp.json() == []


def test_no_office_param_means_no_office_lookup(client, db):
    """Not one extra round trip for the agencies that never use offices."""
    client.get(f"/api/v1/agencies/{AGENCY_ID}/leads")

    assert all("offices" not in _sql(stmt) for stmt in db.statements)


def test_the_export_keeps_its_old_call_shape_without_an_office(
    client, db, monkeypatch
):
    """`iter_export_chunks` is called with three arguments, exactly as before
    offices existed. Anything else breaks callers that wrap it."""
    seen: list[tuple] = []

    async def _chunks(agency_id, temperature, status):
        seen.append((agency_id, temperature, status))
        return
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(dashboard, "iter_export_chunks", _chunks)

    resp = client.get(f"/api/v1/agencies/{AGENCY_ID}/leads.csv")

    assert resp.status_code == 200
    assert seen == [(AGENCY_ID, None, None)]


def test_the_export_passes_the_office_when_there_is_one(client, db, monkeypatch):
    office_id = uuid.uuid4()
    db.results = [[office_id]]
    seen: list[tuple] = []

    async def _chunks(agency_id, temperature, status, office=None):
        seen.append((agency_id, temperature, status, office))
        return
        yield  # pragma: no cover

    monkeypatch.setattr(dashboard, "iter_export_chunks", _chunks)

    resp = client.get(
        f"/api/v1/agencies/{AGENCY_ID}/leads.csv", params={"office": str(office_id)}
    )

    assert resp.status_code == 200
    assert seen == [(AGENCY_ID, None, None, office_id)]


# ---------------------------------------------------------------------------
# 2. The filter is a tenant boundary
# ---------------------------------------------------------------------------


def test_another_agencys_office_is_404_not_an_empty_list(client, db):
    """Empty would be the right rows with the wrong story, and would let the
    parameter be used to probe which office ids exist elsewhere."""
    db.results = [[]]

    resp = client.get(
        f"/api/v1/agencies/{AGENCY_ID}/leads", params={"office": str(uuid.uuid4())}
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "office_not_found"


def test_the_office_lookup_is_scoped_to_the_agency(client, db):
    db.results = [[]]
    office_id = uuid.uuid4()

    client.get(f"/api/v1/agencies/{AGENCY_ID}/leads", params={"office": str(office_id)})

    lookup = _sql(db.statements[0])
    assert "FROM offices" in lookup
    assert "offices.agency_id" in lookup
    assert "offices.id" in lookup


def test_inbox_validates_the_office_too(client, db):
    db.results = [[]]

    resp = client.get(
        f"/api/v1/agencies/{AGENCY_ID}/inbox", params={"office": str(uuid.uuid4())}
    )

    assert resp.status_code == 404


def test_csv_export_validates_the_office_too(client, db):
    db.results = [[]]

    resp = client.get(
        f"/api/v1/agencies/{AGENCY_ID}/leads.csv", params={"office": str(uuid.uuid4())}
    )

    assert resp.status_code == 404


def test_the_export_carries_the_same_office_filter_as_the_listing():
    """Otherwise "Exportar CSV" hands a branch manager the whole agency."""
    office_id = uuid.uuid4()
    listing = _sql(dashboard._leads_filtered(AGENCY_ID, None, None, office_id))

    # Three: the lead's link through a conversation, its link through a visit,
    # and the check that the office is this agency's at all
    # (`office_belongs_to_agency`).
    assert listing.count("EXISTS") == 3
    # Same helper builds both; pinning the shared helper is what makes the
    # export, the page and the `total` count unable to disagree.
    assert dashboard._lead_filters(AGENCY_ID, None, None, office_id).__len__() == 2


def test_every_office_subquery_is_agency_scoped():
    """Multi-tenancy rule: no query without an agency_id, subqueries included."""
    sql = _sql(dashboard._leads_filtered(AGENCY_ID, None, None, uuid.uuid4()))

    assert "conversations.agency_id" in sql
    assert "visits.agency_id" in sql
    assert "users.agency_id" in sql


def test_the_exists_subqueries_are_not_flattened_into_the_outer_query():
    """`correlate(lead)` is load-bearing: without it SQLAlchemy pulls
    `conversations` out of the subquery FROM and the EXISTS is true for
    every row, which would show office A the whole agency."""
    sql = _sql(dashboard._leads_filtered(AGENCY_ID, None, None, uuid.uuid4()))

    assert "EXISTS (SELECT conversations.id \nFROM conversations" in sql
    assert "EXISTS (SELECT visits.id \nFROM visits JOIN properties" in sql


# --- the predicate, executed against a SQLite mirror -----------------------


class MirrorBase(DeclarativeBase):
    pass


class MOffice(MirrorBase):
    __tablename__ = "offices"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    agency_id: Mapped[str] = mapped_column(String)


class MUser(MirrorBase):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    agency_id: Mapped[str] = mapped_column(String)
    office_id: Mapped[str | None] = mapped_column(String, nullable=True)


class MLead(MirrorBase):
    __tablename__ = "leads"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    agency_id: Mapped[str] = mapped_column(String)


class MProperty(MirrorBase):
    __tablename__ = "properties"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    agency_id: Mapped[str] = mapped_column(String)
    office_id: Mapped[str | None] = mapped_column(String, nullable=True)


class MConversation(MirrorBase):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    agency_id: Mapped[str] = mapped_column(String)
    lead_id: Mapped[str] = mapped_column(String)
    assigned_user_id: Mapped[str | None] = mapped_column(String, nullable=True)


class MVisit(MirrorBase):
    __tablename__ = "visits"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    agency_id: Mapped[str] = mapped_column(String)
    lead_id: Mapped[str] = mapped_column(String)
    property_id: Mapped[str] = mapped_column(String)
    assigned_user_id: Mapped[str | None] = mapped_column(String, nullable=True)


MIRROR = dict(
    lead=MLead,
    conversation=MConversation,
    visit=MVisit,
    property_=MProperty,
    user=MUser,
    # The predicate checks the office belongs to the agency before anything
    # else, so the mirror has to carry the offices table too.
    office=MOffice,
)

A = "agency-a"
B = "agency-b"
OFFICE_A = "office-a"
OFFICE_B = "office-b"


@pytest.fixture
def mirror() -> Session:
    """A tiny world: two offices of agency A, one of agency B.

    * `lead-a`   only ever talks to Ana (office A).
    * `lead-b`   only ever visits a listing of office B.
    * `lead-both` visited an office-B listing *and* is handled by Ana —
      the "comparte contactos entre tus oficinas" case.
    * `lead-none` has nothing attached: pure AI, nobody assigned.
    * `lead-other` belongs to another agency entirely.
    """
    engine = create_engine("sqlite://")
    MirrorBase.metadata.create_all(engine)
    session = Session(engine)
    session.add_all(
        [
            MOffice(id=OFFICE_A, agency_id=A),
            MOffice(id=OFFICE_B, agency_id=A),
            MOffice(id="office-other", agency_id=B),
            MUser(id="ana", agency_id=A, office_id=OFFICE_A),
            MUser(id="bruno", agency_id=A, office_id=OFFICE_B),
            MUser(id="sin-oficina", agency_id=A, office_id=None),
            MProperty(id="piso-a", agency_id=A, office_id=OFFICE_A),
            MProperty(id="piso-b", agency_id=A, office_id=OFFICE_B),
            MProperty(id="piso-sin", agency_id=A, office_id=None),
            MLead(id="lead-a", agency_id=A),
            MLead(id="lead-b", agency_id=A),
            MLead(id="lead-both", agency_id=A),
            MLead(id="lead-none", agency_id=A),
            MLead(id="lead-other", agency_id=B),
            MConversation(id="c1", agency_id=A, lead_id="lead-a", assigned_user_id="ana"),
            MConversation(id="c2", agency_id=A, lead_id="lead-b", assigned_user_id=None),
            MConversation(id="c3", agency_id=A, lead_id="lead-both", assigned_user_id="ana"),
            MConversation(id="c4", agency_id=A, lead_id="lead-none", assigned_user_id=None),
            MConversation(
                id="c5", agency_id=B, lead_id="lead-other", assigned_user_id="ana"
            ),
            MVisit(
                id="v1",
                agency_id=A,
                lead_id="lead-b",
                property_id="piso-b",
                assigned_user_id=None,
            ),
            MVisit(
                id="v2",
                agency_id=A,
                lead_id="lead-both",
                property_id="piso-b",
                assigned_user_id=None,
            ),
        ]
    )
    session.commit()
    yield session
    session.close()


def _leads_of(session: Session, agency: str, office: str | None) -> set[str]:
    stmt = select(MLead.id).where(MLead.agency_id == agency)
    if office is not None:
        stmt = stmt.where(dashboard.lead_in_office(agency, office, **MIRROR))
    return {row for (row,) in session.execute(stmt).all()}


def test_a_user_of_office_a_does_not_see_office_b_leads(mirror):
    assert _leads_of(mirror, A, OFFICE_A) == {"lead-a", "lead-both"}
    assert "lead-b" not in _leads_of(mirror, A, OFFICE_A)


def test_a_user_of_office_b_does_not_see_office_a_leads(mirror):
    assert _leads_of(mirror, A, OFFICE_B) == {"lead-b", "lead-both"}
    assert "lead-a" not in _leads_of(mirror, A, OFFICE_B)


def test_a_shared_lead_shows_up_in_both_offices(mirror):
    """"Comparte contactos entre tus oficinas" — belonging to one branch is
    not exclusive, otherwise a lead shown two flats would vanish from one."""
    assert "lead-both" in _leads_of(mirror, A, OFFICE_A)
    assert "lead-both" in _leads_of(mirror, A, OFFICE_B)


def test_without_the_filter_every_lead_of_the_agency_is_returned(mirror):
    assert _leads_of(mirror, A, None) == {"lead-a", "lead-b", "lead-both", "lead-none"}


def test_a_lead_attached_to_nobody_is_out_of_every_office(mirror):
    """It is still in the unfiltered listing — the filter narrows, and a lead
    nobody has taken yet belongs to the agency, not to a branch."""
    assert "lead-none" not in _leads_of(mirror, A, OFFICE_A)
    assert "lead-none" not in _leads_of(mirror, A, OFFICE_B)
    assert "lead-none" in _leads_of(mirror, A, None)


def test_the_office_filter_never_reaches_another_agencys_rows(mirror):
    """Agency B has a conversation assigned to Ana (agency A's user). Even so,
    filtering agency B by agency A's office must not hand over `lead-other` —
    and filtering agency A must not pull `lead-other` in."""
    assert "lead-other" not in _leads_of(mirror, A, OFFICE_A)
    assert _leads_of(mirror, B, OFFICE_A) == set()


def test_the_property_link_alone_is_enough(mirror):
    """`lead-b` has no assigned human at all: it belongs to office B purely
    because the flat it visited does. That is "cada contacto va a la oficina
    que le corresponde" with the data that actually exists."""
    assert "lead-b" in _leads_of(mirror, A, OFFICE_B)


def test_conversation_predicate_matches_the_assigned_humans_office(mirror):
    stmt = (
        select(MConversation.id)
        .join(MLead, MLead.id == MConversation.lead_id)
        .where(
            MConversation.agency_id == A,
            dashboard.conversation_in_office(A, OFFICE_A, **MIRROR),
        )
    )
    assert {row for (row,) in mirror.execute(stmt).all()} == {"c1", "c3"}


def test_conversation_predicate_falls_back_to_the_leads_office(mirror):
    """Most threads are answered by the AI and have no assigned user. If the
    inbox filter only looked at `assigned_user_id` it would be empty."""
    stmt = (
        select(MConversation.id)
        .join(MLead, MLead.id == MConversation.lead_id)
        .where(
            MConversation.agency_id == A,
            dashboard.conversation_in_office(A, OFFICE_B, **MIRROR),
        )
    )
    matched = {row for (row,) in mirror.execute(stmt).all()}

    assert "c2" in matched  # unassigned, but its lead visited an office-B flat
    assert "c1" not in matched


# ---------------------------------------------------------------------------
# 3. Plan and role
# ---------------------------------------------------------------------------


def test_the_feature_flag_is_named_multi_oficina():
    """Pinned because the flag has to be added by hand to seed_plans.py; a
    typo on either side silently opens or closes the whole feature."""
    assert offices.MULTI_OFFICE_FEATURE == "multi_oficina"


def test_creating_an_office_needs_the_agencia_plan(offices_client, db, monkeypatch):
    _patch_plan(monkeypatch, {"voice": True, "plantillas": True}, code="pro")

    resp = offices_client.post(
        f"/api/v1/agencies/{AGENCY_ID}/offices", json={"name": "Oficina Centro"}
    )

    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["code"] == "feature_not_in_plan"
    assert detail["feature"] == "multi_oficina"
    assert db.added == []


def test_creating_an_office_works_on_the_agencia_plan(offices_client, db, monkeypatch):
    _patch_plan(monkeypatch, {"multi_oficina": True})
    db.results = [[]]  # no office by that name yet

    resp = offices_client.post(
        f"/api/v1/agencies/{AGENCY_ID}/offices",
        json={"name": "  Oficina Centro  ", "address": "Calle Mayor 1"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Oficina Centro"  # stripped
    assert body["user_count"] == 0
    assert len(db.added) == 1
    assert db.added[0].agency_id == AGENCY_ID


def test_a_duplicate_office_name_is_409(offices_client, db, monkeypatch):
    _patch_plan(monkeypatch, {"multi_oficina": True})
    db.results = [[uuid.uuid4()]]  # the name lookup finds a row

    resp = offices_client.post(
        f"/api/v1/agencies/{AGENCY_ID}/offices", json={"name": "Oficina Centro"}
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "office_name_taken"


def test_assigning_someone_to_an_office_needs_the_agencia_plan(offices_client, db, monkeypatch):
    _patch_plan(monkeypatch, {"voice": True}, code="pro")

    resp = offices_client.put(
        f"/api/v1/agencies/{AGENCY_ID}/offices/members/{uuid.uuid4()}",
        json={"office_id": str(uuid.uuid4())},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["feature"] == "multi_oficina"


def test_reading_the_offices_is_not_plan_gated(offices_client, db, monkeypatch):
    """A Starter agency must get an empty list, not an error screen: the panel
    calls this to decide whether to paint the filter at all."""
    _patch_plan(monkeypatch, {"voice": False}, code="starter")

    resp = offices_client.get(f"/api/v1/agencies/{AGENCY_ID}/offices")

    assert resp.status_code == 200
    assert resp.json() == []


def test_closing_an_office_is_not_plan_gated(offices_client, db, monkeypatch):
    """An agency that downgrades has to be able to unwind what it created."""
    _patch_plan(monkeypatch, {"voice": True}, code="pro")
    office = SimpleNamespace(id=uuid.uuid4(), agency_id=AGENCY_ID, name="Centro")
    db.results = [[office], []]

    resp = offices_client.delete(f"/api/v1/agencies/{AGENCY_ID}/offices/{office.id}")

    assert resp.status_code == 204
    assert db.deleted == [office]


def test_closing_an_office_detaches_its_people_and_listings(offices_client, db, monkeypatch):
    """Never a cascade: an office is a label, the tenant is the agency."""
    _patch_plan(monkeypatch, {"multi_oficina": True})
    office = SimpleNamespace(id=uuid.uuid4(), agency_id=AGENCY_ID, name="Centro")
    db.results = [[office], []]

    offices_client.delete(f"/api/v1/agencies/{AGENCY_ID}/offices/{office.id}")

    updates = [_sql(stmt) for stmt in db.statements if "UPDATE" in _sql(stmt)]
    assert any("UPDATE users SET office_id" in sql for sql in updates)
    assert any("UPDATE properties SET office_id" in sql for sql in updates)
    assert all("DELETE FROM leads" not in _sql(stmt) for stmt in db.statements)


def test_another_agencys_office_cannot_be_renamed_or_closed(offices_client, db, monkeypatch):
    _patch_plan(monkeypatch, {"multi_oficina": True})
    db.results = [[]]  # the ownership lookup finds nothing

    patched = offices_client.patch(
        f"/api/v1/agencies/{AGENCY_ID}/offices/{uuid.uuid4()}", json={"name": "Mía"}
    )
    deleted = offices_client.delete(f"/api/v1/agencies/{AGENCY_ID}/offices/{uuid.uuid4()}")

    assert patched.status_code == 404
    assert deleted.status_code == 404


def _route(path: str, method: str):
    for route in offices.router.routes:
        if route.path == path and method in route.methods:
            return route
    raise AssertionError(f"route {method} {path} not found")


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/v1/agencies/{agency_id}/offices", "POST"),
        ("/api/v1/agencies/{agency_id}/offices/{office_id}", "PATCH"),
        ("/api/v1/agencies/{agency_id}/offices/{office_id}", "DELETE"),
        ("/api/v1/agencies/{agency_id}/offices/members/{user_id}", "PUT"),
        ("/api/v1/agencies/{agency_id}/offices/properties/{property_id}", "PUT"),
    ],
)
def test_every_writing_route_is_owner_only(path, method):
    """Structural, so a refactor cannot quietly drop the role check: an agent
    must not be able to reorganise the company from the API."""
    dependencies = [str(d.dependency) for d in _route(path, method).dependencies]

    assert any("_dependency" in dep for dep in dependencies), (
        f"{method} {path} lost OwnerOnlyDep"
    )


def test_the_router_is_agency_scoped():
    assert offices.router.prefix == "/api/v1/agencies/{agency_id}/offices"
    assert offices.router.dependencies, "router must carry AgencyMemberDep"


# ---------------------------------------------------------------------------
# 4. The migration
# ---------------------------------------------------------------------------


class OpRecorder:
    """Stands in for `alembic.op` so upgrade()/downgrade() can be *run*."""

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return _record

    def names(self) -> list[str]:
        return [name for name, _a, _kw in self.calls]

    def targets(self, op_name: str) -> list:
        return [args[0] for name, args, _kw in self.calls if name == op_name]


@pytest.fixture
def migration():
    """Loaded by path: `alembic/versions` is a script directory, not a package
    (its module names start with a digit and cannot even be imported)."""
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260801_0015_offices.py"
    )
    spec = importlib.util.spec_from_file_location("migration_0015_offices", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(module, monkeypatch, func: str) -> OpRecorder:
    recorder = OpRecorder()
    monkeypatch.setattr(module, "op", recorder)
    getattr(module, func)()
    return recorder


def test_the_revision_sits_where_it_was_assigned(migration):
    """Single chain, no branches: 0013 -> 0014 (equipo) -> 0015 (oficinas)."""
    assert migration.revision == "20260801_0015"
    assert migration.down_revision == "20260801_0014"


def test_the_upgrade_is_purely_additive(migration, monkeypatch):
    recorder = _run(migration, monkeypatch, "upgrade")

    assert set(recorder.names()) <= {
        "create_table",
        "add_column",
        "create_index",
        "create_foreign_key",
    }
    assert recorder.targets("create_table") == ["offices"]
    assert recorder.targets("add_column") == ["users", "properties"]


def test_the_new_columns_are_nullable_and_have_no_default(migration, monkeypatch):
    """The whole regla de oro rests on this: existing rows keep NULL, and NULL
    means "toda la agencia"."""
    recorder = _run(migration, monkeypatch, "upgrade")

    added = [args[1] for name, args, _kw in recorder.calls if name == "add_column"]
    assert len(added) == 2
    for column in added:
        assert column.name == "office_id"
        assert column.nullable is True
        assert column.server_default is None


def test_detaching_never_deletes(migration, monkeypatch):
    """`SET NULL`, not `CASCADE`: closing an office must not take its people
    or its listings with it."""
    recorder = _run(migration, monkeypatch, "upgrade")

    fks = [kwargs for name, _a, kwargs in recorder.calls if name == "create_foreign_key"]
    assert len(fks) == 2
    assert all(kw.get("ondelete") == "SET NULL" for kw in fks)


def test_the_downgrade_undoes_exactly_what_the_upgrade_did(migration, monkeypatch):
    up = _run(migration, monkeypatch, "upgrade")
    down = _run(migration, monkeypatch, "downgrade")

    assert set(down.names()) <= {
        "drop_table",
        "drop_column",
        "drop_index",
        "drop_constraint",
    }
    assert down.targets("drop_table") == up.targets("create_table")
    assert sorted(down.targets("drop_column")) == sorted(up.targets("add_column"))
    assert sorted(down.targets("drop_index")) == sorted(up.targets("create_index"))


def _tables_touched(recorder: OpRecorder) -> set[str]:
    """Which table each recorded operation acts on.

    Alembic puts the table in a different place per operation: first
    positional for `drop_table`/`drop_column`, second for `drop_constraint`,
    and the `table_name` keyword for `drop_index`.
    """
    tables: set[str] = set()
    for name, args, kwargs in recorder.calls:
        if name == "drop_index":
            tables.add(kwargs.get("table_name") or args[1])
        elif name == "drop_constraint":
            tables.add(args[1])
        elif args:
            tables.add(args[0])
    return tables


def test_the_downgrade_touches_no_other_table(migration, monkeypatch):
    down = _run(migration, monkeypatch, "downgrade")

    assert _tables_touched(down) == {"offices", "users", "properties"}


def test_the_migration_only_speaks_of_its_own_column(migration, monkeypatch):
    """No backfill, no UPDATE, no rewrite of anything that already existed."""
    up = _run(migration, monkeypatch, "upgrade")

    assert "execute" not in up.names()
    assert "alter_column" not in up.names()
    assert "bulk_insert" not in up.names()


def test_the_model_and_the_migration_agree_on_the_table(migration):
    from app.db.models import Office

    assert Office.__tablename__ == "offices"
    assert Office.__table__.c.name.type.length == 120
    assert Office.__table__.c.address.nullable is True
    # sa is imported so the migration's sa.Column(...) objects are real ones.
    assert isinstance(Office.__table__.c.name.type, sa.String)
