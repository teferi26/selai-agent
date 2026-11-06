"""Smoke tests for the deterministic parts of the scoring service.

Pure-component scorers are fully unit-testable; integration tests live
elsewhere because they need the DB.
"""
from app.db.models import LeadStatus
from app.services.lead_scoring import _score_qualification, _score_status, _temperature_for


class _StubLead:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_temperature_thresholds():
    assert _temperature_for(0) == "cold"
    assert _temperature_for(39) == "cold"
    assert _temperature_for(40) == "warm"
    assert _temperature_for(69) == "warm"
    assert _temperature_for(70) == "hot"
    assert _temperature_for(100) == "hot"


def test_qualification_components():
    lead = _StubLead(
        budget_max=350_000,
        preferred_cities=["Madrid"],
        preferred_neighborhoods=None,
        min_bedrooms=2,
        operation="sale",
        full_name="Juan",
        email="j@e.com",
        phone_e164="+34600111222",
        must_haves=["parking"],
    )
    comps = _score_qualification(lead)
    total = sum(c.points for c in comps)
    # All components should fire for a fully-qualified lead:
    assert total > 30
    labels = {c.label for c in comps}
    assert "Presupuesto definido" in labels
    assert "Zona definida" in labels
    assert "Habitaciones definidas" in labels


def test_status_bonus_won():
    lead = _StubLead(status=LeadStatus.WON)
    comps = _score_status(lead)
    assert comps and comps[0].points == 40


def test_status_bonus_lost_negative():
    lead = _StubLead(status=LeadStatus.LOST)
    comps = _score_status(lead)
    assert comps and comps[0].points == -50
