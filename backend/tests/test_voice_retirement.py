"""Agencies that already chose a voice which no longer exists.

Two catalog entries (`sonic-multilingual-es-male`, `-neutral`) were invented
Cartesia ids and were replaced by real ElevenLabs voices. Removing them from
`_VOICE_CATALOG` is right, but it is only half the change: an agency that
picked one still has it in `settings.voice_id` and in its Vapi assistant, and
both panels preselect by matching that id against the catalog — so the screen
would tell it that it has chosen no voice, while the phone keeps trying to
speak with an id nothing can resolve.

`RETIRED_VOICE_IDS` + `app/scripts/fix_retired_voices.py` close that. These
tests cover the map's invariants (a wrong entry there is a migration that
moves agencies onto another dead id) and the repair decision, which is pure.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.voice_settings import (
    _VOICE_CATALOG,
    RETIRED_VOICE_IDS,
    catalog_voice,
    replacement_for,
)
from app.scripts.fix_retired_voices import Repair, repair_for, repaired_settings

RETIRED_MALE = "sonic-multilingual-es-male"
RETIRED_FEMALE = "sonic-multilingual-es-neutral"


def _agency(**settings) -> SimpleNamespace:
    return SimpleNamespace(slug="inmo-sol", settings=settings or None)


# ---------------------------------------------------------------------------
# The map itself
# ---------------------------------------------------------------------------


def test_both_removed_voices_have_a_successor():
    """The two ids the catalog used to publish. Dropping one from this map
    silently strands whoever chose it."""
    assert set(RETIRED_VOICE_IDS) == {RETIRED_MALE, RETIRED_FEMALE}


def test_no_retired_id_is_still_in_the_catalog():
    """A voice cannot be both offered and retired: the script would rewrite an
    agency's live choice out from under it."""
    catalog_ids = {v.id for v in _VOICE_CATALOG}
    assert catalog_ids.isdisjoint(RETIRED_VOICE_IDS)


@pytest.mark.parametrize("retired_id", sorted(RETIRED_VOICE_IDS))
def test_every_successor_is_a_real_catalog_entry(retired_id):
    """Pointing at an id that is not in the catalog would move the agency from
    one voice its panel cannot show to another."""
    assert catalog_voice(RETIRED_VOICE_IDS[retired_id]) is not None


@pytest.mark.parametrize("retired_id", sorted(RETIRED_VOICE_IDS))
def test_every_successor_can_actually_be_heard(retired_id):
    """The point of the replacement: unlike what it replaces, the agency can
    press play on it."""
    assert catalog_voice(RETIRED_VOICE_IDS[retired_id]).previewable is True


def test_the_successor_keeps_the_gender_that_was_chosen():
    """The one attribute the agency picked on purpose. Silently turning the
    male voice into a female one is not a migration, it is a surprise."""
    assert catalog_voice(RETIRED_VOICE_IDS[RETIRED_MALE]).gender == "male"
    assert catalog_voice(RETIRED_VOICE_IDS[RETIRED_FEMALE]).gender == "female"


def test_no_successor_is_itself_retired():
    """Guards against a chain: retiring a replacement later without following
    the map through would leave the migration pointing at a dead id."""
    assert set(RETIRED_VOICE_IDS.values()).isdisjoint(RETIRED_VOICE_IDS)


# ---------------------------------------------------------------------------
# replacement_for
# ---------------------------------------------------------------------------


def test_a_retired_id_resolves_to_its_successor():
    voice = replacement_for(RETIRED_MALE)

    assert voice is not None
    assert voice.id == RETIRED_VOICE_IDS[RETIRED_MALE]


@pytest.mark.parametrize("voice_id", [None, "", "EXAVITQu4vr4xnSDxMaL", "sonic-multilingual"])
def test_a_live_or_absent_voice_resolves_to_nothing(voice_id):
    """Including the cloned-voice case: an agency on its own ElevenLabs clone
    has an id that is in no catalog and must not be touched either."""
    assert replacement_for(voice_id) is None


def test_an_unknown_id_is_left_alone():
    """A hand-edited or future id is not our business — better a voice we do
    not recognise than one we overwrote."""
    assert replacement_for("voz-que-alguien-metio-a-mano") is None


# ---------------------------------------------------------------------------
# The repair decision
# ---------------------------------------------------------------------------


def test_an_agency_on_a_retired_voice_is_planned_for_repair():
    agency = _agency(
        voice_provider="cartesia",
        voice_id=RETIRED_MALE,
        voice_name="Sonic — Mateo",
        vapi_assistant_id="asst_1",
    )

    plan = repair_for(agency)

    assert plan == Repair(
        agency_slug="inmo-sol",
        retired_id=RETIRED_MALE,
        voice=catalog_voice(RETIRED_VOICE_IDS[RETIRED_MALE]),
        assistant_id="asst_1",
    )


def test_an_agency_that_never_chose_a_voice_is_skipped():
    assert repair_for(_agency()) is None
    assert repair_for(_agency(ai_tone="cercano")) is None


def test_an_agency_on_a_live_voice_is_skipped():
    assert repair_for(_agency(voice_id="EXAVITQu4vr4xnSDxMaL")) is None


def test_the_default_assistant_voice_is_not_treated_as_retired():
    """`sonic-multilingual` is still in the catalog and is what every new
    assistant is provisioned with; migrating it would rewrite agencies that
    never touched the voice screen."""
    assert repair_for(_agency(voice_id="sonic-multilingual")) is None


def test_a_non_string_voice_id_does_not_crash_the_run():
    """One hand-edited settings blob must not stop the migration for the rest
    of the agencies."""
    assert repair_for(_agency(voice_id={"id": RETIRED_MALE})) is None


def test_an_agency_without_an_assistant_is_still_repaired_in_the_database():
    """It has no phone yet, but its panel has to show the right voice, and the
    assistant will be provisioned from these settings later."""
    plan = repair_for(_agency(voice_id=RETIRED_FEMALE))

    assert plan is not None
    assert plan.assistant_id is None


# ---------------------------------------------------------------------------
# What gets written
# ---------------------------------------------------------------------------


def test_the_three_voice_keys_move_together():
    """Rewriting the id and leaving `voice_name` behind is how a panel ends up
    labelling George as "Sonic — Mateo"."""
    voice = catalog_voice(RETIRED_VOICE_IDS[RETIRED_MALE])

    after = repaired_settings(
        {"voice_provider": "cartesia", "voice_id": RETIRED_MALE, "voice_name": "Sonic — Mateo"},
        voice,
    )

    assert after == {
        "voice_provider": voice.provider,
        "voice_id": voice.id,
        "voice_name": voice.name,
    }


def test_everything_else_in_the_settings_blob_survives():
    """The same JSON column holds the assistant id, the phone number and the
    agency's AI instructions."""
    voice = catalog_voice(RETIRED_VOICE_IDS[RETIRED_MALE])
    before = {
        "voice_id": RETIRED_MALE,
        "vapi_assistant_id": "asst_1",
        "ai_phone_number": "+34 910 00 00 00",
        "ai_instructions": "Nunca prometas precio sin ver la vivienda.",
        "recording_notice": False,
    }

    after = repaired_settings(dict(before), voice)

    assert after["vapi_assistant_id"] == "asst_1"
    assert after["ai_phone_number"] == "+34 910 00 00 00"
    assert after["ai_instructions"] == before["ai_instructions"]
    assert after["recording_notice"] is False


def test_the_blob_is_a_new_dict_so_sqlalchemy_persists_it():
    """`Agency.settings` is a JSON column; an in-place edit commits nothing."""
    before = {"voice_id": RETIRED_MALE}

    after = repaired_settings(before, catalog_voice(RETIRED_VOICE_IDS[RETIRED_MALE]))

    assert after is not before
    assert before == {"voice_id": RETIRED_MALE}


def test_a_second_run_finds_nothing_to_do():
    """Idempotence, end to end over the pure half: the repaired agency is no
    longer a candidate."""
    voice = catalog_voice(RETIRED_VOICE_IDS[RETIRED_MALE])
    agency = _agency(voice_id=RETIRED_MALE, vapi_assistant_id="asst_1")

    agency.settings = repaired_settings(dict(agency.settings), voice)

    assert repair_for(agency) is None
