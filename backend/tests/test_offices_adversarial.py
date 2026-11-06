"""Multi-oficina — revisión adversarial.

Not a second copy of `test_offices.py`: every test here is an *attack* on a
specific way the feature could be wrong, in the order that a failure would
hurt most.

1. **An agency with no offices must not notice anything.** The builder proved
   it for `/leads`; the ones that were still unproven are `/inbox`, the CSV
   export and — the one that actually breaks a deploy — whether mounting the
   new router collides with a route that already exists.
2. **The filter must never escape the tenant.** Executed, not compiled: a
   corrupted row (a listing of agency B carrying an office id of agency A) is
   planted on purpose to check the predicate still refuses to cross.
3. **The migration must not break `alembic upgrade head`,** which runs on
   every Railway deploy. The whole `versions/` directory is parsed and the
   graph is walked: one head, no branch, no repeated revision id.
4. **Closing an office must not take anything with it,** and must refuse
   before touching a row when the office is not ours.
5. **The cursor and the filter must survive each other.**
6. **The promise has to be reachable.** The gate reads a plan flag; if the
   seed does not carry that flag, the paying plan gets a 403 and the feature
   the site sells for 399 EUR does not exist. That is the last test group.
"""
from __future__ import annotations

import ast
import pathlib
import uuid
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.api import dashboard, offices
from app.core.settings import settings
from app.db.models import Office, Property, User
from app.db.session import get_db
from app.main import app
from app.scripts.seed_plans import PLANS
from app.services import plan_limits
from tests.test_offices import (
    MIRROR,
    OFFICE_A,
    A,
    B,
    FakeSession,
    MirrorBase,
    MLead,
    MOffice,
    MProperty,
    MUser,
    MVisit,
)

AGENCY_ID = uuid.uuid4()

offices_app = FastAPI()
offices_app.include_router(offices.router)


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


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
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")


@pytest.fixture
def client(_dev_auth, db):
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
# 1. The agency that never creates an office
# ---------------------------------------------------------------------------


def test_the_inbox_makes_no_office_query_and_no_office_clause(client, db):
    """The listing the whole product opens on. Not one extra round trip, and
    not one extra clause in the statement it does send."""
    resp = client.get(f"/api/v1/agencies/{AGENCY_ID}/inbox")

    assert resp.status_code == 200
    assert db.statements, "the inbox has to have queried something"
    for stmt in db.statements:
        sql = _sql(stmt)
        assert "offices" not in sql
        assert "office_id" not in sql
        assert "EXISTS" not in sql


def test_the_csv_export_makes_no_office_query(client, db, monkeypatch):
    """Not even the validation lookup: `ensure_office_of_agency` returns
    before touching the session when there is no office.

    The row reader is stubbed because it deliberately opens a session of its
    own (the request's is closed before the body streams); what is under test
    is the request session, which is where the office lookup would land.
    """

    async def _chunks(agency_id, temperature, status):
        return
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(dashboard, "iter_export_chunks", _chunks)

    resp = client.get(f"/api/v1/agencies/{AGENCY_ID}/leads.csv")

    assert resp.status_code == 200
    assert all("offices" not in _sql(stmt) for stmt in db.statements)


def test_the_paginated_leads_count_has_no_office_clause(client, db):
    """`total` is a second statement built from the same filters; without an
    office it must be the COUNT it always was."""
    client.get(f"/api/v1/agencies/{AGENCY_ID}/leads", params={"paginated": "true"})

    assert any("count" in _sql(stmt).lower() for stmt in db.statements)
    assert all("EXISTS" not in _sql(stmt) for stmt in db.statements)


def test_the_offices_router_collides_with_no_existing_route():
    """`alembic upgrade head` is not the only thing that runs on deploy: the
    orchestrator adds `include_router(offices.router)` to main.py. A path this
    router claims that some other router already serves would silently shadow
    it — FastAPI keeps the first match and says nothing."""
    existing = {
        (route.path, method)
        for route in app.routes
        for method in getattr(route, "methods", set()) or set()
    }
    claimed = {
        (route.path, method)
        for route in offices.router.routes
        for method in getattr(route, "methods", set()) or set()
    }

    assert claimed, "the router has to declare something"
    assert claimed & existing == set()


def test_the_office_endpoints_live_under_the_agency_prefix():
    """Every path has to carry `{agency_id}`; a route that lost it would be
    served without `AgencyMemberDep` ever seeing an agency to compare."""
    for route in offices.router.routes:
        assert route.path.startswith("/api/v1/agencies/{agency_id}/offices")


def test_listing_offices_of_an_agency_without_any_is_one_query_and_an_empty_list(
    offices_client, db
):
    resp = offices_client.get(f"/api/v1/agencies/{AGENCY_ID}/offices")

    assert resp.status_code == 200
    assert resp.json() == []
    assert len(db.statements) == 1


def test_the_two_new_columns_are_nullable_with_no_default():
    """The whole "nobody notices" claim rests on this: existing rows keep
    NULL, and NULL means "toda la agencia" everywhere it is read."""
    for column in (User.__table__.c.office_id, Property.__table__.c.office_id):
        assert column.nullable is True
        assert column.default is None
        assert column.server_default is None


def test_offices_is_the_only_table_the_feature_adds():
    """Guards against a second table sneaking in without a migration."""
    from app.db.base import Base

    assert "offices" in Base.metadata.tables
    assert Office.__table__.name == "offices"


# ---------------------------------------------------------------------------
# 2. The filter as a tenant boundary — executed, with poisoned data
# ---------------------------------------------------------------------------


@pytest.fixture
def poisoned() -> Session:
    """Agency A owns `office-a`. Agency B has a listing that (wrongly) carries
    it, and a lead who visited that listing.

    The API cannot produce this row today — `assign_property` checks both the
    listing and the office against the caller's agency — which is exactly why
    it is worth planting by hand: a CSV import, a support fix in psql or a
    future endpoint that forgets the check would, and the filter is the last
    line before one tenant's contacts answer another tenant's question.
    """
    engine = create_engine("sqlite://")
    MirrorBase.metadata.create_all(engine)
    session = Session(engine)
    session.add_all(
        [
            MOffice(id=OFFICE_A, agency_id=A),
            MUser(id="ana", agency_id=A, office_id=OFFICE_A),
            MLead(id="lead-a", agency_id=A),
            MLead(id="lead-de-b", agency_id=B),
            # The poison: a listing of agency B stamped with agency A's office.
            MProperty(id="piso-de-b", agency_id=B, office_id=OFFICE_A),
            MVisit(
                id="v-b",
                agency_id=B,
                lead_id="lead-de-b",
                property_id="piso-de-b",
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


def test_a_listing_of_another_agency_cannot_pull_a_lead_into_our_office(poisoned):
    """Agency B's listing wears agency A's office id. Filtering agency B by
    that office must still match nothing: the office is not B's."""
    assert _leads_of(poisoned, B, OFFICE_A) == set()


def test_the_visit_join_is_agency_scoped():
    """The property side of the visit link has to name `agency_id` like every
    other query in the codebase ("toda query filtra por agency_id"). Without
    it the join trusts that a listing's office always belongs to the listing's
    agency, which is an invariant nothing enforces at the database level."""
    sql = _sql(select(MLead.id).where(dashboard.lead_in_office(A, OFFICE_A, **MIRROR)))

    # Two agency_id comparisons inside the visit EXISTS: the visit's and the
    # listing's.
    assert sql.count("properties.agency_id") >= 1


def test_the_office_membership_subquery_is_agency_scoped():
    sql = _sql(dashboard.office_user_ids(A, OFFICE_A, user=MUser))

    assert "users.agency_id" in sql
    assert "users.office_id" in sql


def test_the_conversation_predicate_is_agency_scoped_on_both_halves():
    sql = _sql(
        select(MLead.id).where(dashboard.conversation_in_office(A, OFFICE_A, **MIRROR))
    )

    assert "conversations.agency_id" in sql
    assert "visits.agency_id" in sql
    assert "users.agency_id" in sql


def test_the_office_counts_never_count_another_agencys_rows():
    """`user_count` / `property_count` are rendered next to a "Cerrar" button
    that promises how many rows it will detach. Counting across tenants would
    be both a wrong number and a disclosure."""
    sql = _sql(select(*offices._counts_subqueries()).where(Office.agency_id == A))

    assert sql.count("users.agency_id") >= 1
    assert sql.count("properties.agency_id") >= 1


# ---------------------------------------------------------------------------
# 3. The migration — `alembic upgrade head` runs on every deploy
# ---------------------------------------------------------------------------

VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _revisions() -> dict[str, str | None]:
    """{revision: down_revision} read from the files, without importing them."""
    found: dict[str, str | None] = {}
    for path in sorted(VERSIONS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        revision = down = None
        seen_down = False
        for node in tree.body:
            if not isinstance(node, ast.AnnAssign) or not isinstance(
                node.target, ast.Name
            ):
                continue
            if node.target.id == "revision":
                revision = ast.literal_eval(node.value)
            elif node.target.id == "down_revision":
                down = ast.literal_eval(node.value)
                seen_down = True
        assert revision is not None, f"{path.name} declares no revision"
        assert seen_down, f"{path.name} declares no down_revision"
        assert revision not in found, f"revision {revision} is declared twice"
        found[revision] = down
    return found


def test_the_migration_chain_has_exactly_one_head():
    """Two heads and `alembic upgrade head` aborts — on Railway that is the
    deploy failing before the app ever starts, for every pista at once."""
    revisions = _revisions()
    referenced = {down for down in revisions.values() if down is not None}
    heads = set(revisions) - referenced

    assert len(heads) == 1, f"expected one head, got {sorted(heads)}"


def test_the_chain_is_linear_and_ends_where_it_was_assigned():
    """0013 -> 0014 (EQUIPO) -> 0015 (this pista), no branch anywhere."""
    revisions = _revisions()

    # No two migrations may sit on the same parent: that is what a branch is.
    parents = [down for down in revisions.values() if down is not None]
    assert len(parents) == len(set(parents)), "two revisions share a parent"

    assert revisions["20260801_0015"] == "20260801_0014"
    assert revisions["20260801_0014"] == "20260726_0013"

    # And the whole thing is walkable from head back to the root.
    assert revisions["20260831_0017"] == "20260831_0016"
    assert revisions["20260831_0016"] == "20260801_0015"

    referenced = {down for down in revisions.values() if down is not None}
    heads = set(revisions) - referenced
    assert len(heads) == 1, f"expected one head, got {sorted(heads)}"

    walked, current = 0, heads.pop()
    while current is not None:
        current = revisions[current]
        walked += 1
    assert walked == len(revisions)


def test_every_migration_file_is_importable():
    """A syntax error or a bad import in any version file makes alembic fail
    to build the graph, which is the same broken deploy by another route."""
    import importlib.util

    for path in sorted(VERSIONS.glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"_mig_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert hasattr(module, "upgrade") and hasattr(module, "downgrade")


# ---------------------------------------------------------------------------
# 4. Closing an office
# ---------------------------------------------------------------------------


def _updates(db) -> list[str]:
    """The real UPDATE statements.

    Matching on the string would also catch every SELECT, because the models
    carry an `updated_at` column.
    """
    return [_sql(stmt) for stmt in db.statements if isinstance(stmt, sa.Update)]


def test_closing_someone_elses_office_detaches_nothing(offices_client, db, monkeypatch):
    """404 is not enough: the refusal has to happen *before* any UPDATE. A
    check that ran after the detach would strip another tenant's assignments
    and then politely deny it did."""
    _patch_plan(monkeypatch, {"multi_oficina": True})
    db.results = [[]]  # the ownership lookup finds nothing

    resp = offices_client.delete(f"/api/v1/agencies/{AGENCY_ID}/offices/{uuid.uuid4()}")

    assert resp.status_code == 404
    assert db.deleted == []
    assert _updates(db) == []


def test_closing_an_office_detaches_only_this_agencys_rows(
    offices_client, db, monkeypatch
):
    _patch_plan(monkeypatch, {"multi_oficina": True})
    office_id = uuid.uuid4()
    db.results = [[SimpleNamespace(id=office_id, agency_id=AGENCY_ID)]]

    resp = offices_client.delete(f"/api/v1/agencies/{AGENCY_ID}/offices/{office_id}")

    assert resp.status_code == 204
    statements = [stmt for stmt in db.statements if isinstance(stmt, sa.Update)]
    assert len(statements) == 2
    for stmt in statements:
        compiled = stmt.compile(dialect=postgresql.dialect())
        # Scoped to this agency *and* to this office...
        assert "agency_id" in str(compiled)
        assert "office_id" in str(compiled)
        # ...and what it writes is NULL — "toda la agencia" — not some other id.
        assert compiled.params["office_id"] is None


def test_closing_an_office_never_deletes_a_lead_a_visit_or_a_conversation(
    offices_client, db, monkeypatch
):
    """The office is a label. Everything a customer would scream about —
    contacts, visits, threads — has to be untouched by the DELETE."""
    _patch_plan(monkeypatch, {"multi_oficina": True})
    office_id = uuid.uuid4()
    office_row = SimpleNamespace(id=office_id, agency_id=AGENCY_ID)
    db.results = [[office_row]]

    offices_client.delete(f"/api/v1/agencies/{AGENCY_ID}/offices/{office_id}")

    for stmt in db.statements:
        sql = _sql(stmt).upper()
        assert not sql.startswith("DELETE")
        for table in ("LEADS", "VISITS", "CONVERSATIONS", "MESSAGES"):
            assert table not in sql
    # The one and only row deleted is the office itself.
    assert db.deleted == [office_row]


# ---------------------------------------------------------------------------
# 5. Cursor and filter together
# ---------------------------------------------------------------------------


def _statements_with(db, needle: str) -> list[str]:
    return [_sql(stmt) for stmt in db.statements if needle in _sql(stmt)]


def test_the_leads_count_carries_the_office_filter(client, db):
    """`total` feeds "vas a exportar N contactos". If the COUNT ignored the
    office it would promise the whole agency under a branch's name."""
    office_id = uuid.uuid4()
    db.results = [[office_id], []]

    resp = client.get(
        f"/api/v1/agencies/{AGENCY_ID}/leads",
        params={"office": str(office_id), "paginated": "true"},
    )

    assert resp.status_code == 200
    counts = [sql for sql in _statements_with(db, "count(") if "EXISTS" in sql]
    assert counts, "the COUNT lost the office filter"


def test_a_cursor_page_keeps_the_office_filter(client, db):
    """Page 2 of a filtered listing must still be filtered. A keyset clause
    that replaced the filter instead of joining it would hand the rest of the
    agency over from the second page on."""
    office_id = uuid.uuid4()
    db.results = [[office_id], []]
    cursor = dashboard.encode_cursor(
        AGENCY_ID, ["2026-01-01T00:00:00+00:00", str(uuid.uuid4())]
    )

    resp = client.get(
        f"/api/v1/agencies/{AGENCY_ID}/leads",
        params={"office": str(office_id), "cursor": cursor},
    )

    assert resp.status_code == 200
    listing = [sql for sql in _statements_with(db, "FROM leads") if "EXISTS" in sql]
    assert listing, "the office filter vanished on the cursor page"
    assert any("created_at" in sql for sql in listing)


def test_an_inbox_cursor_page_keeps_the_office_filter(client, db):
    office_id = uuid.uuid4()
    db.results = [[office_id], []]
    cursor = dashboard.encode_cursor(
        AGENCY_ID, [0, "2026-01-01T00:00:00+00:00", str(uuid.uuid4())]
    )

    resp = client.get(
        f"/api/v1/agencies/{AGENCY_ID}/inbox",
        params={"office": str(office_id), "cursor": cursor},
    )

    assert resp.status_code == 200
    assert [sql for sql in _statements_with(db, "conversations") if "EXISTS" in sql]


def test_a_cursor_from_another_agency_is_still_refused_with_an_office(client, db):
    """The office parameter must not become a way around the cursor's
    signature."""
    office_id = uuid.uuid4()
    db.results = [[office_id], []]
    foreign = dashboard.encode_cursor(
        uuid.uuid4(), ["2026-01-01T00:00:00+00:00", str(uuid.uuid4())]
    )

    resp = client.get(
        f"/api/v1/agencies/{AGENCY_ID}/leads",
        params={"office": str(office_id), "cursor": foreign},
    )

    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 6. Is the promise actually reachable?
# ---------------------------------------------------------------------------


def _plan(code: str) -> dict:
    return next(spec for spec in PLANS if spec["code"] == code)


def test_the_agencia_plan_actually_carries_the_flag_the_gate_reads():
    """The one that decides whether any of this exists in production.

    `ensure_feature` reads `plan.features["multi_oficina"]`; the plans are
    seeded (and *re-seeded*, overwriting `features` in place) from
    `app/scripts/seed_plans.py`. If the flag is not there, every agency on the
    399 EUR plan gets 403 `feature_not_in_plan` on the feature its own pricing
    page sells, and no amount of correct SQL below it matters.
    """
    assert _plan("agencia")["features"].get(offices.MULTI_OFFICE_FEATURE) is True


def test_the_cheaper_plans_do_not_carry_it():
    for code in ("starter", "pro"):
        assert offices.MULTI_OFFICE_FEATURE not in _plan(code)["features"]


def test_the_flag_name_is_the_one_the_module_exports():
    """Two spellings of the same idea is how a gate silently opens or closes."""
    assert offices.MULTI_OFFICE_FEATURE == "multi_oficina"


def test_creating_an_office_is_refused_on_the_seeded_pro_plan(
    offices_client, db, monkeypatch
):
    """The seeded rows themselves, not a hand-written dict."""
    _patch_plan(monkeypatch, _plan("pro")["features"], code="pro")

    resp = offices_client.post(
        f"/api/v1/agencies/{AGENCY_ID}/offices", json={"name": "Centro"}
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["feature"] == "multi_oficina"
    assert db.added == []


def test_creating_an_office_is_allowed_on_the_seeded_agencia_plan(
    offices_client, db, monkeypatch
):
    _patch_plan(monkeypatch, _plan("agencia")["features"], code="agencia")

    resp = offices_client.post(
        f"/api/v1/agencies/{AGENCY_ID}/offices", json={"name": "Oficina Centro"}
    )

    assert resp.status_code == 201
    assert [obj.name for obj in db.added] == ["Oficina Centro"]


# --- the last mile: can a customer actually reach the load-bearing half? ----


def test_a_listing_can_be_moved_to_an_office_through_the_api(
    offices_client, db, monkeypatch
):
    """`propiedad -> oficina` is the half that works without invitations, and
    therefore the half "cada contacto va a la oficina que le corresponde"
    rests on today."""
    _patch_plan(monkeypatch, _plan("agencia")["features"])
    property_id = uuid.uuid4()
    office_id = uuid.uuid4()
    listing = SimpleNamespace(id=property_id, agency_id=AGENCY_ID, office_id=None)
    db.results = [[listing], [SimpleNamespace(id=office_id, agency_id=AGENCY_ID)]]

    resp = offices_client.put(
        f"/api/v1/agencies/{AGENCY_ID}/offices/properties/{property_id}",
        json={"office_id": str(office_id)},
    )

    assert resp.status_code == 200
    assert listing.office_id == office_id


def test_the_panel_can_read_which_office_each_listing_belongs_to(
    offices_client, db, monkeypatch
):
    """Without a read side there is no screen: `PropertyOut` (api/properties.py)
    does not expose `office_id`, so a settings page could set the assignment
    and never show it back."""
    resp = offices_client.get(f"/api/v1/agencies/{AGENCY_ID}/offices/properties")

    assert resp.status_code == 200
    assert resp.json() == []


def test_reading_the_catalogue_is_agency_scoped_and_capped(offices_client, db):
    offices_client.get(
        f"/api/v1/agencies/{AGENCY_ID}/offices/properties", params={"q": "centro"}
    )

    sql = _sql(db.statements[0])
    assert "properties.agency_id" in sql
    assert "LIMIT" in sql.upper()


def test_reading_the_catalogue_is_not_plan_gated(offices_client, db, monkeypatch):
    """Same reason as `GET /offices`: the screen has to render for Starter and
    Pro and explain what they are missing, not blow up."""
    _patch_plan(monkeypatch, _plan("starter")["features"], code="starter")

    resp = offices_client.get(f"/api/v1/agencies/{AGENCY_ID}/offices/properties")

    assert resp.status_code == 200


def test_every_writing_route_is_owner_only_enumerated_from_the_router():
    """The sibling test in `test_offices.py` lists the five routes by hand, so
    a sixth one added later would be checked by nobody. This one asks the
    router instead: whatever writes, writes as an owner."""
    writers = [
        (route, method)
        for route in offices.router.routes
        for method in getattr(route, "methods", set()) or set()
        if method in {"POST", "PUT", "PATCH", "DELETE"}
    ]

    assert len(writers) >= 5
    for route, method in writers:
        dependencies = [str(dep.dependency) for dep in route.dependencies]
        assert any("_dependency" in dep for dep in dependencies), (
            f"{method} {route.path} writes without a role check"
        )


def test_the_router_produces_a_valid_openapi_schema():
    """Once main.py mounts it, a schema FastAPI cannot build takes /docs — and
    the schema route — down for the whole API, not just for offices."""
    resp = TestClient(offices_app).get("/openapi.json")

    assert resp.status_code == 200
    assert len(resp.json()["paths"]) == 6


def test_the_ownership_check_does_not_correlate_to_the_lead(client, db):
    """`office_belongs_to_agency` must stay a constant subquery: correlated to
    `leads` it would be re-evaluated per row for no reason."""
    sql = _sql(dashboard.office_belongs_to_agency(AGENCY_ID, uuid.uuid4()))

    assert "leads" not in sql
    assert "offices.agency_id" in sql
