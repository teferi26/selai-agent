"""_render_agency_context — the /setup wizard profile keys feed the prompt."""
from __future__ import annotations

from types import SimpleNamespace

from app.agent.runner import _render_agency_context


def test_wizard_profile_keys_render():
    agency = SimpleNamespace(
        settings={
            "business_name": "Inmo Sol Playa",
            "zones": ["Alicante centro", "San Juan"],
            "schedule_text": "L-V 9:00-19:00",
            "website": "https://inmosol.example",
        }
    )
    ctx = _render_agency_context(agency)
    assert "Inmo Sol Playa" in ctx
    assert "Alicante centro, San Juan" in ctx
    assert "L-V 9:00-19:00" in ctx
    assert "https://inmosol.example" in ctx


def test_legacy_keys_still_render():
    agency = SimpleNamespace(
        settings={"operating_zones": "Zona norte", "office_hours": "9 a 19"}
    )
    ctx = _render_agency_context(agency)
    assert "Zona norte" in ctx
    assert "9 a 19" in ctx


def test_schedule_text_wins_over_office_hours():
    agency = SimpleNamespace(
        settings={"schedule_text": "L-V 10-18", "office_hours": "9 a 19"}
    )
    ctx = _render_agency_context(agency)
    assert "L-V 10-18" in ctx
    assert "9 a 19" not in ctx


def test_empty_settings_render_empty():
    assert _render_agency_context(SimpleNamespace(settings=None)) == ""
