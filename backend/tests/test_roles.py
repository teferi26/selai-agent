"""Item 175 / contract 3 — `require_role`.

`UserRole` has existed since the first migration and nothing ever checked
it: any commercial agent could erase a lead irreversibly. This wave adds the
dependency and applies it to the one endpoint that destroys data for good,
`DELETE /gdpr/agencies/{agency_id}/leads/{lead_id}`.

Two levels here: the dependency's own behaviour, and a structural check that
the GDPR erase route really carries it (so a refactor can't quietly drop it).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.auth import AuthContext, require_role
from app.db.models import UserRole

AGENCY = uuid.uuid4()


class FakeUser:
    def __init__(self, role: UserRole, is_active: bool = True):
        self.agency_id = AGENCY
        self.role = role
        self.is_active = is_active


def _ctx(user=None, dev_bypass: bool = False) -> AuthContext:
    return AuthContext(
        clerk_user_id="user_test",
        email="test@selai.es",
        raw_claims={},
        user=user,
        dev_bypass=dev_bypass,
    )


async def _call(dep, ctx: AuthContext) -> AuthContext:
    return await dep(auth=ctx)


# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------


async def test_owner_passes_an_owner_only_dependency():
    dep = require_role(UserRole.OWNER)
    ctx = _ctx(user=FakeUser(UserRole.OWNER))
    assert await _call(dep, ctx) is ctx


async def test_agent_is_rejected_with_role_required():
    dep = require_role(UserRole.OWNER)
    with pytest.raises(HTTPException) as exc:
        await _call(dep, _ctx(user=FakeUser(UserRole.AGENT)))
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "role_required"


async def test_rejection_says_which_roles_would_have_worked():
    """The dashboard hides the button; the API still has to explain itself."""
    dep = require_role(UserRole.OWNER, UserRole.ADMIN)
    with pytest.raises(HTTPException) as exc:
        await _call(dep, _ctx(user=FakeUser(UserRole.AGENT)))
    assert exc.value.detail["required"] == ["admin", "owner"]


async def test_any_of_several_roles_is_enough():
    dep = require_role(UserRole.OWNER, UserRole.ADMIN)
    for role in (UserRole.OWNER, UserRole.ADMIN):
        assert await _call(dep, _ctx(user=FakeUser(role))) is not None


async def test_authenticated_but_unknown_user_is_rejected():
    """A valid Clerk token whose email matches no user row carries no role."""
    dep = require_role(UserRole.OWNER)
    with pytest.raises(HTTPException) as exc:
        await _call(dep, _ctx(user=None))
    assert exc.value.status_code == 403


async def test_deactivated_owner_is_rejected():
    """Off-boarding a partner must take the destructive powers with it."""
    dep = require_role(UserRole.OWNER)
    with pytest.raises(HTTPException) as exc:
        await _call(dep, _ctx(user=FakeUser(UserRole.OWNER, is_active=False)))
    assert exc.value.status_code == 403


async def test_dev_bypass_is_honoured():
    """Same rule as ensure_agency_access: with no Clerk configured there is
    no user row to carry a role and the local dashboard must still work."""
    dep = require_role(UserRole.OWNER)
    ctx = _ctx(user=None, dev_bypass=True)
    assert await _call(dep, ctx) is ctx


# ---------------------------------------------------------------------------
# Structural: the irreversible GDPR endpoint carries the guard
# ---------------------------------------------------------------------------


def _guarded_by(route, factory_name: str) -> bool:
    """Whether `route`'s dependency tree contains a dependency built by
    `factory_name` (the closure's __qualname__ is "require_role.<locals>...")."""
    seen: set[int] = set()

    def walk(dep) -> bool:
        if id(dep) in seen:
            return False
        seen.add(id(dep))
        if getattr(dep.call, "__qualname__", "").startswith(factory_name):
            return True
        return any(walk(sub) for sub in dep.dependencies)

    return walk(route.dependant)


def _gdpr_route(method: str, path: str):
    """Look the route up on the router itself.

    FastAPI 0.139 includes routers lazily (`app.routes` holds
    `_IncludedRouter` wrappers, not `APIRoute`s), so walking `app.routes`
    silently finds nothing. The router's own routes already carry the
    `/gdpr` prefix and both router-level and route-level dependencies, which
    is exactly what we want to assert on.
    """
    from fastapi.routing import APIRoute

    from app.api.gdpr import router

    for route in router.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            return route
    raise AssertionError(f"{method} {path} is not registered on the GDPR router")


def test_gdpr_erase_requires_a_role():
    route = _gdpr_route("DELETE", "/gdpr/agencies/{agency_id}/leads/{lead_id}")
    assert _guarded_by(route, "require_role"), "irreversible erasure must be owner/admin only"


def test_gdpr_erase_still_requires_agency_membership():
    """The role check is a third layer, not a replacement: an owner of
    another agency must still be refused."""
    from app.core.auth import require_agency_member

    route = _gdpr_route("DELETE", "/gdpr/agencies/{agency_id}/leads/{lead_id}")
    assert _guarded_by(route, require_agency_member.__qualname__)


def test_gdpr_erasure_status_is_owner_only_and_tenant_scoped():
    from app.core.auth import require_agency_member

    route = _gdpr_route(
        "GET", "/gdpr/agencies/{agency_id}/erasures/{erasure_reference}"
    )
    assert _guarded_by(route, "require_role")
    assert _guarded_by(route, require_agency_member.__qualname__)


def test_gdpr_read_endpoints_stay_open_to_the_whole_agency():
    """Only the irreversible action is restricted this wave. Export and
    consent logging are part of a commercial agent's daily job."""
    for method, path in (
        ("GET", "/gdpr/agencies/{agency_id}/leads/{lead_id}/export"),
        ("POST", "/gdpr/agencies/{agency_id}/leads/{lead_id}/consent"),
    ):
        assert not _guarded_by(_gdpr_route(method, path), "require_role"), path


# ---------------------------------------------------------------------------
# Over HTTP: the shape of the tree is not proof that a request is refused.
# ---------------------------------------------------------------------------


class _RecordingSession:
    """Records writes so a rejected request can be shown to have made none."""

    def __init__(self):
        self.commits = 0

    async def execute(self, stmt):
        raise AssertionError("this endpoint should go through the mocked service")

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


@pytest.fixture
def erase_client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import gdpr as gdpr_module
    from app.core.auth import require_auth
    from app.db.session import get_db

    erased: list = []

    async def _erase_lead(db, agency_id, lead_id):
        erased.append((agency_id, lead_id))
        return SimpleNamespace(
            subject_pseudonym="lead_" + "a" * 64,
            local_erasure_completed=True,
            managed_artifacts_deleted=2,
            external_erasures_pending=1,
        )

    monkeypatch.setattr(gdpr_module, "erase_lead", _erase_lead)

    mini = FastAPI()
    mini.include_router(gdpr_module.router)

    ctx = {"auth": _ctx(user=FakeUser(UserRole.OWNER))}
    session = _RecordingSession()

    async def _override_auth():
        return ctx["auth"]

    async def _override_db():
        yield session

    mini.dependency_overrides[require_auth] = _override_auth
    mini.dependency_overrides[get_db] = _override_db

    def _delete():
        return TestClient(mini).delete(f"/gdpr/agencies/{AGENCY}/leads/{uuid.uuid4()}")

    return SimpleNamespace(delete=_delete, ctx=ctx, erased=erased, session=session)


def test_an_owner_can_erase_a_lead(erase_client):
    response = erase_client.delete()
    assert response.status_code == 202
    assert response.json() == {
        "erasure_reference": "lead_" + "a" * 64,
        "local_erasure_completed": True,
        "managed_artifacts_deleted": 2,
        "external_erasure_status": "pending",
        "external_erasures_pending": 1,
    }
    assert len(erase_client.erased) == 1


def test_an_agent_gets_403_role_required_and_nothing_is_deleted(erase_client):
    erase_client.ctx["auth"] = _ctx(user=FakeUser(UserRole.AGENT))
    resp = erase_client.delete()
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "role_required"
    assert erase_client.erased == []
    assert erase_client.session.commits == 0
