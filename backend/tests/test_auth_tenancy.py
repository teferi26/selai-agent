"""Multi-tenant boundary tests.

Covers the two layers that keep one agency out of another's data:

1. `ensure_agency_access` unit behavior (membership, inactive users,
   dev bypass).
2. Structural regression: every registered route namespaced under
   `/agencies/{agency_id}` must carry the `require_agency_member` guard,
   so a newly added router can't silently ship unauthenticated.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.core.auth import (
    AuthContext,
    ensure_agency_access,
    require_agency_member,
)

AGENCY = uuid.uuid4()
OTHER_AGENCY = uuid.uuid4()


class FakeUser:
    def __init__(self, agency_id: uuid.UUID, is_active: bool = True):
        self.agency_id = agency_id
        self.is_active = is_active


def _ctx(user=None, dev_bypass: bool = False) -> AuthContext:
    return AuthContext(
        clerk_user_id="user_test",
        email="test@selai.es",
        raw_claims={},
        user=user,
        dev_bypass=dev_bypass,
    )


def test_member_of_agency_is_allowed():
    ensure_agency_access(_ctx(user=FakeUser(AGENCY)), AGENCY)


def test_member_of_other_agency_is_denied():
    with pytest.raises(HTTPException) as exc:
        ensure_agency_access(_ctx(user=FakeUser(OTHER_AGENCY)), AGENCY)
    assert exc.value.status_code == 403


def test_authenticated_but_unknown_user_is_denied():
    with pytest.raises(HTTPException) as exc:
        ensure_agency_access(_ctx(user=None), AGENCY)
    assert exc.value.status_code == 403


def test_inactive_user_is_denied():
    with pytest.raises(HTTPException) as exc:
        ensure_agency_access(_ctx(user=FakeUser(AGENCY, is_active=False)), AGENCY)
    assert exc.value.status_code == 403


def test_dev_bypass_allows():
    ensure_agency_access(_ctx(user=None, dev_bypass=True), AGENCY)


def test_agency_id_property():
    assert _ctx(user=FakeUser(AGENCY)).agency_id == AGENCY
    assert _ctx(user=None).agency_id is None


# ---------------------------------------------------------------------------
# Structural guard: no agency-scoped route may ship without the membership
# dependency.
# ---------------------------------------------------------------------------


def _has_guard(dependant) -> bool:
    seen: set[int] = set()

    def walk(dep) -> bool:
        if dep is None or id(dep) in seen:
            return False
        seen.add(id(dep))
        if dep.call is require_agency_member:
            return True
        return any(walk(sub) for sub in dep.dependencies)

    return walk(dependant)


def _effective_routes(routes) -> list[tuple[str, object]]:
    """Every route the app can actually serve, as (path, dependant).

    Walking `app.routes` and picking out `APIRoute` used to be enough. Since
    FastAPI 0.139 `include_router` stores a lazy `_IncludedRouter` wrapper
    instead, so that filter matches one route out of seventy-two and *zero*
    agency-scoped ones: this guard — the structural half of the multi-tenant
    boundary — was asserting that an empty list is empty, and had been
    passing green while asserting nothing. Expanding the wrappers gives back
    the combined path and the combined dependency tree (router-level plus
    route-level), which is exactly what has to be inspected.
    """
    from fastapi.routing import APIRoute, _EffectiveRouteContext, _IncludedRouter

    out: list[tuple[str, object]] = []
    for route in routes:
        if isinstance(route, _IncludedRouter):
            out.extend(_effective_routes(route.effective_candidates()))
            out.extend(_effective_routes(route.effective_low_priority_routes()))
        elif isinstance(route, _EffectiveRouteContext | APIRoute):
            out.append((route.path, route.dependant))
    return out


def test_the_route_table_is_actually_being_inspected():
    """Guards the guard. If this ever drops to zero again, the test below
    goes green without checking a single route."""
    from app.main import app

    scoped = [p for p, _ in _effective_routes(app.routes) if "/agencies/{agency_id}" in p]
    assert len(scoped) >= 20, (
        f"only {len(scoped)} agency-scoped routes found; the route walk is broken"
    )


def test_every_agency_scoped_route_is_guarded():
    from app.main import app

    unguarded = [
        path
        for path, dependant in _effective_routes(app.routes)
        if "/agencies/{agency_id}" in path and not _has_guard(dependant)
    ]
    assert unguarded == [], f"agency-scoped routes without membership guard: {unguarded}"
