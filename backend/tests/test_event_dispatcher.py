"""Domain event whitelist."""
from app.services.event_dispatcher import SUPPORTED_EVENTS


def test_supported_events_contain_lead_lifecycle():
    assert "lead.created" in SUPPORTED_EVENTS
    assert "lead.qualified" in SUPPORTED_EVENTS
    assert "visit.scheduled" in SUPPORTED_EVENTS
    assert "visit.completed" in SUPPORTED_EVENTS
    assert "offer.signed" in SUPPORTED_EVENTS


def test_no_unexpected_events_added_silently():
    assert {
        "lead.created",
        "lead.qualified",
        "lead.temperature_changed",
        "visit.scheduled",
        "visit.completed",
        "offer.created",
        "offer.signed",
    } == SUPPORTED_EVENTS
