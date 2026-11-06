"""Extended agency settings (52)(53)(77)(50) — patch validation + GET defaults."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api.agency_settings import (
    MAX_FORBIDDEN_PHRASE_LEN,
    MAX_FORBIDDEN_PHRASES,
    AgencySettingsPatch,
    BusinessHours,
    _serialize,
)


def _agency(settings: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        name="Inmo Sol",
        slug="inmo-sol",
        timezone="Europe/Madrid",
        default_language="es",
        settings=settings or {},
    )


# ---------------------------------------------------------------------------
# PATCH validation
# ---------------------------------------------------------------------------


def test_patch_accepts_the_new_keys():
    patch = AgencySettingsPatch(
        ai_tone="formal",
        forbidden_phrases=["te regalamos la reforma"],
        ai_mode="out_of_hours",
        business_hours={"start": "10:00", "end": "18:30", "days": [0, 1, 2]},
        recording_notice=False,
    )
    assert patch.ai_tone == "formal"
    assert patch.business_hours is not None
    assert patch.business_hours.days == [0, 1, 2]


@pytest.mark.parametrize("tone", ["seco", "FORMAL", "amable"])
def test_patch_rejects_unknown_ai_tone(tone):
    with pytest.raises(ValidationError):
        AgencySettingsPatch(ai_tone=tone)


@pytest.mark.parametrize("mode", ["never", "office", "OUT_OF_HOURS"])
def test_patch_rejects_unknown_ai_mode(mode):
    with pytest.raises(ValidationError):
        AgencySettingsPatch(ai_mode=mode)


def test_patch_rejects_too_many_forbidden_phrases():
    with pytest.raises(ValidationError):
        AgencySettingsPatch(forbidden_phrases=["x"] * (MAX_FORBIDDEN_PHRASES + 1))


def test_patch_rejects_too_long_forbidden_phrase():
    with pytest.raises(ValidationError):
        AgencySettingsPatch(forbidden_phrases=["x" * (MAX_FORBIDDEN_PHRASE_LEN + 1)])


def test_patch_accepts_max_sized_forbidden_phrases():
    patch = AgencySettingsPatch(
        forbidden_phrases=["x" * MAX_FORBIDDEN_PHRASE_LEN] * MAX_FORBIDDEN_PHRASES
    )
    assert len(patch.forbidden_phrases) == MAX_FORBIDDEN_PHRASES


def test_patch_accepts_agency_profile_keys():
    patch = AgencySettingsPatch(
        business_name="Inmo Sol Playa",
        zones=["Alicante centro", "San Juan"],
        schedule_text="L-V 9:00-19:00, S 10:00-14:00",
        website="https://inmosol.example",
    )
    assert patch.business_name == "Inmo Sol Playa"
    assert patch.zones == ["Alicante centro", "San Juan"]


def test_patch_rejects_too_many_zones():
    with pytest.raises(ValidationError):
        AgencySettingsPatch(zones=["z"] * 31)


def test_serialize_exposes_agency_profile():
    out = _serialize(
        _agency(
            {
                "business_name": "Inmo Sol Playa",
                "zones": ["Alicante centro"],
                "schedule_text": "L-V 9-19",
                "website": "https://inmosol.example",
            }
        )
    )
    assert out.settings.business_name == "Inmo Sol Playa"
    assert out.settings.zones == ["Alicante centro"]
    assert out.settings.schedule_text == "L-V 9-19"
    assert out.settings.website == "https://inmosol.example"


def test_serialize_tolerates_malformed_zones():
    out = _serialize(_agency({"zones": "no soy una lista"}))
    assert out.settings.zones == []


@pytest.mark.parametrize(
    "bad",
    [
        {"start": "9:00", "end": "19:00", "days": [0]},      # HH must be 2 digits
        {"start": "09:00", "end": "24:00", "days": [0]},     # 24:00 invalid
        {"start": "09:00", "end": "19:60", "days": [0]},     # minutes out of range
        {"start": "09:00", "end": "19:00", "days": [7]},     # day out of range
        {"start": "09:00", "end": "19:00", "days": []},      # at least one day
        {"start": "09:00", "days": [0]},                     # end missing
    ],
)
def test_business_hours_rejects_malformed(bad):
    with pytest.raises(ValidationError):
        BusinessHours(**bad)


def test_business_hours_days_are_deduped_and_sorted():
    bh = BusinessHours(start="09:00", end="19:00", days=[4, 0, 4, 2])
    assert bh.days == [0, 2, 4]


# ---------------------------------------------------------------------------
# GET serialization defaults
# ---------------------------------------------------------------------------


def test_serialize_defaults_when_settings_empty():
    view = _serialize(_agency()).settings
    assert view.ai_tone is None
    assert view.forbidden_phrases == []
    assert view.ai_mode == "always"
    assert view.business_hours is None
    assert view.recording_notice is True


def test_serialize_exposes_stored_values():
    view = _serialize(
        _agency(
            {
                "ai_tone": "formal",
                "forbidden_phrases": ["sin comisión"],
                "ai_mode": "out_of_hours",
                "business_hours": {"start": "10:00", "end": "14:00", "days": [0]},
                "recording_notice": False,
            }
        )
    ).settings
    assert view.ai_tone == "formal"
    assert view.forbidden_phrases == ["sin comisión"]
    assert view.ai_mode == "out_of_hours"
    assert view.business_hours == {"start": "10:00", "end": "14:00", "days": [0]}
    assert view.recording_notice is False


def test_serialize_none_values_render_as_defaults():
    view = _serialize(
        _agency(
            {
                "ai_tone": None,
                "forbidden_phrases": None,
                "ai_mode": None,
                "business_hours": None,
                "recording_notice": None,
            }
        )
    ).settings
    assert view.ai_tone is None
    assert view.forbidden_phrases == []
    assert view.ai_mode == "always"
    assert view.business_hours is None
    # Only an explicit False disables the recording notice.
    assert view.recording_notice is True
