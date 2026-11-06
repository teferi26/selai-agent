"""Property status PATCH — body validation + route registration."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.properties import PropertyStatusBody
from app.db.models import PropertyStatus


def test_status_body_accepts_every_real_enum_value():
    for status in PropertyStatus:
        assert PropertyStatusBody(status=status.value).status is status


def test_status_body_rejects_unknown_status():
    with pytest.raises(ValidationError):
        PropertyStatusBody(status="vendido")


def test_patch_route_is_registered():
    from app.api.properties import router

    assert any(
        r.path == "/api/v1/agencies/{agency_id}/properties/{property_id}"
        and "PATCH" in r.methods
        for r in router.routes
    )


def test_agent_search_only_offers_available_properties():
    """The AI must never propose sold/rented listings: `search_properties`
    hard-filters on PropertyStatus.AVAILABLE."""
    import inspect

    from app.agent import tools

    src = inspect.getsource(tools._search_properties)
    assert "PropertyStatus.AVAILABLE" in src
