"""app/scripts/fix_business_hours_days — repairing panel-convention weekdays.

The panel used to write 1 = Monday … 7 = Sunday while the backend reads
`datetime.weekday()` numbers (0 = Monday). These tests pin the detection rules
the script uses to decide what is old data and what is not, and check the fix
against the real evaluator, not just against the stored numbers.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.agency_settings import BusinessHours
from app.scripts.fix_business_hours_days import (
    ALREADY_MARKED,
    ALREADY_PY,
    CONVENTION_KEY,
    CONVENTION_PY,
    CONVERTED,
    MALFORMED,
    NEEDS_REVIEW,
    NO_HOURS,
    RECENTLY_TOUCHED,
    _parse_args,
    apply_plan,
    plan_for,
    run,
    was_touched_after,
)
from app.services.conversation_service import is_within_business_hours

# 2026-07-20 is a Monday, so `MONDAY + timedelta(days=n)` is the day Python
# numbers `n`. Used to check meaning, not just arithmetic.
MONDAY = datetime(2026, 7, 20, 12, 0)


def _blob(days, **extra) -> dict:
    return {"business_hours": {"start": "09:00", "end": "19:00", "days": days}, **extra}


def _open_on(blob: dict, weekday: int) -> bool:
    return is_within_business_hours(MONDAY + timedelta(days=weekday), blob["business_hours"])


# ---------------------------------------------------------------------------
# The bug this migration exists for
# ---------------------------------------------------------------------------


def test_old_panel_monday_to_friday_is_evaluated_as_tuesday_to_saturday():
    """The stored damage, before touching anything: what the agency ticked as
    Mon-Fri is applied Tue-Sat, which is why the fix cannot be panel-only."""
    stored = _blob([1, 2, 3, 4, 5])  # "lunes a viernes" in the old panel
    assert _open_on(stored, 0) is False  # Monday: closed, but the agency said open
    assert _open_on(stored, 5) is True  # Saturday: open, and nobody asked for it


def test_conversion_restores_the_intended_days():
    stored = _blob([1, 2, 3, 4, 5])
    fixed = apply_plan(stored, plan_for(stored))
    assert fixed["business_hours"]["days"] == [0, 1, 2, 3, 4]
    assert _open_on(fixed, 0) is True  # Monday
    assert _open_on(fixed, 4) is True  # Friday
    assert _open_on(fixed, 5) is False  # Saturday
    assert _open_on(fixed, 6) is False  # Sunday


def test_converted_days_are_accepted_by_the_api_validator():
    """A repaired blob must survive the next PATCH from the panel unchanged."""
    stored = _blob([1, 2, 3, 4, 5, 6])
    fixed = apply_plan(stored, plan_for(stored))
    validated = BusinessHours(**fixed["business_hours"])
    assert validated.days == [0, 1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# Detection rules
# ---------------------------------------------------------------------------


def test_plain_old_convention_row_is_converted():
    plan = plan_for(_blob([1, 2, 3]))
    assert plan.action == CONVERTED
    assert plan.before == [1, 2, 3]
    assert plan.after == [0, 1, 2]


def test_day_zero_means_the_row_already_speaks_python():
    """The old panel had no 0, so its presence proves the fixed panel wrote
    this. Shifting it would push Monday to -1 and lose the day entirely."""
    plan = plan_for(_blob([0, 1, 2, 3, 4]))
    assert plan.action == ALREADY_PY
    fixed = apply_plan(_blob([0, 1, 2, 3, 4]), plan)
    assert fixed["business_hours"]["days"] == [0, 1, 2, 3, 4]
    assert fixed[CONVENTION_KEY] == CONVENTION_PY


def test_sunday_seven_converts_to_six():
    # Unreachable through the API (the validator rejects 7) but possible via a
    # direct write; under the old scale 7 was Sunday, which is 6 for Python.
    plan = plan_for(_blob([7]))
    assert plan.action == CONVERTED
    assert plan.after == [6]


def test_duplicates_collapse():
    plan = plan_for(_blob([1, 1, 2]))
    assert plan.after == [0, 1]


def test_marker_makes_a_second_run_a_no_op():
    stored = _blob([1, 2, 3])
    once = apply_plan(stored, plan_for(stored))
    twice_plan = plan_for(once)
    assert twice_plan.action == ALREADY_MARKED
    assert apply_plan(once, twice_plan) == once


def test_idempotent_across_three_runs():
    blob = _blob([1, 2, 3, 4, 5])
    for _ in range(3):
        blob = apply_plan(blob, plan_for(blob))
    assert blob["business_hours"]["days"] == [0, 1, 2, 3, 4]


def test_agency_without_hours_is_marked_so_later_runs_skip_it():
    plan = plan_for({"ai_mode": "always"})
    assert plan.action == NO_HOURS
    marked = apply_plan({"ai_mode": "always"}, plan)
    assert marked[CONVENTION_KEY] == CONVENTION_PY
    assert plan_for(marked).action == ALREADY_MARKED


@pytest.mark.parametrize(
    "hours",
    [
        {"start": "09:00", "end": "19:00", "days": []},
        {"start": "09:00", "end": "19:00", "days": "todos"},
        {"start": "09:00", "end": "19:00", "days": [1, "2"]},
        {"start": "09:00", "end": "19:00", "days": [True, 2]},
        {"start": "09:00", "end": "19:00"},
    ],
)
def test_malformed_days_are_never_arithmetic(hours):
    plan = plan_for({"business_hours": hours})
    assert plan.action == MALFORMED
    assert plan.after is None


@pytest.mark.parametrize(
    "hours",
    [
        {"start": "09:00", "end": "19:00", "days": []},
        {"start": "09:00", "end": "19:00", "days": [1, "2"]},
    ],
)
def test_malformed_rows_are_not_stamped_as_converted(hours):
    """A row we could not classify must stay visible to the next run.

    Marking it would be the product saying "migrated" about data nobody
    repaired: every later run would skip it and the summary would read clean.
    """
    stored = {"business_hours": hours}
    plan = plan_for(stored)
    after = apply_plan(stored, plan)
    assert plan.writes is False
    assert CONVENTION_KEY not in after
    assert plan_for(after).action == MALFORMED  # still flagged on a second run


def test_string_days_are_live_and_still_wrong_so_they_must_not_be_marked():
    """`is_within_business_hours` coerces with int(), so ["1","2"] is not inert
    data: it is a working, day-shifted schedule. Unusable *here* is not the
    same as harmless, which is why MALFORMED never writes the marker."""
    stored = {"business_hours": {"start": "09:00", "end": "19:00", "days": ["1", "2"]}}
    assert _open_on(stored, 0) is False  # Monday: the agency meant open
    assert _open_on(stored, 2) is True  # Wednesday: nobody asked for it
    assert plan_for(stored).writes is False


# ---------------------------------------------------------------------------
# The window between deploying the fixed panel and running this
# ---------------------------------------------------------------------------

CUTOFF = datetime(2026, 7, 31, 20, 0, tzinfo=UTC)


def test_row_saved_after_the_panel_fix_is_reported_not_shifted():
    """Tue-Sat written by the FIXED panel is [1..5] — identical to old Mon-Fri.
    Shifting it would silently move a correct schedule back a day, which is the
    same invisible corruption this migration exists to undo."""
    plan = plan_for(_blob([1, 2, 3, 4, 5]), touched_after_panel_fix=True)
    assert plan.action == RECENTLY_TOUCHED
    assert plan.writes is False
    # The operator still gets told what the conversion *would* have been.
    assert plan.before == [1, 2, 3, 4, 5]
    assert plan.after == [0, 1, 2, 3, 4]


def test_day_zero_beats_the_timestamp():
    """A set containing 0 cannot come from the old panel, so it needs no
    timestamp evidence and must not be dragged into review."""
    plan = plan_for(_blob([0, 1, 2, 3, 4]), touched_after_panel_fix=True)
    assert plan.action == ALREADY_PY


def test_recent_row_stays_pending_on_later_runs():
    stored = _blob([1, 2, 3, 4, 5])
    after = apply_plan(stored, plan_for(stored, touched_after_panel_fix=True))
    assert CONVENTION_KEY not in after
    assert plan_for(after, touched_after_panel_fix=True).action == RECENTLY_TOUCHED


@pytest.mark.parametrize(
    ("updated_at", "cutoff", "expected"),
    [
        (datetime(2026, 7, 31, 21, 0, tzinfo=UTC), CUTOFF, True),
        (datetime(2026, 7, 31, 19, 0, tzinfo=UTC), CUTOFF, False),
        (CUTOFF, CUTOFF, True),  # exactly at the deploy instant: cannot tell
        (datetime(2026, 7, 31, 21, 0), CUTOFF, True),  # naive is read as UTC
        (None, CUTOFF, True),  # no evidence is not evidence of "old"
        (datetime(2020, 1, 1, tzinfo=UTC), None, False),  # guard off
    ],
)
def test_was_touched_after(updated_at, cutoff, expected):
    assert was_touched_after(updated_at, cutoff) is expected


def test_apply_requires_a_deploy_time():
    """Without a cutoff the script cannot tell "already right" from "needs
    shifting", so writing would be a coin flip on customer config."""
    with pytest.raises(SystemExit):
        _parse_args(["--apply"])
    # A dry run without it is fine — it only reads.
    assert _parse_args([]).cutoff is None
    ok = _parse_args(["--apply", "--panel-deployed-at", "2026-07-31T20:00:00+00:00"])
    assert ok.apply and ok.cutoff == datetime(2026, 7, 31, 20, 0, tzinfo=UTC)


def test_bad_deploy_time_is_rejected_instead_of_ignored():
    with pytest.raises(SystemExit):
        _parse_args(["--panel-deployed-at", "ayer por la tarde"])


def test_out_of_range_days_need_a_human():
    plan = plan_for(_blob([1, 9]))
    assert plan.action == NEEDS_REVIEW
    # Left exactly as found: no marker either, so it stays visible.
    untouched = apply_plan(_blob([1, 9]), plan)
    assert untouched == _blob([1, 9])
    assert CONVENTION_KEY not in untouched


def test_zero_and_seven_together_need_a_human():
    plan = plan_for(_blob([0, 7]))
    assert plan.action == NEEDS_REVIEW
    assert plan.writes is False


# ---------------------------------------------------------------------------
# Blob handling
# ---------------------------------------------------------------------------


def test_apply_plan_does_not_mutate_the_input():
    """The caller reassigns agency.settings; an in-place mutation of the JSON
    column would not persist."""
    stored = _blob([1, 2, 3])
    original = {"business_hours": {"start": "09:00", "end": "19:00", "days": [1, 2, 3]}}
    apply_plan(stored, plan_for(stored))
    assert stored == original


def test_other_settings_keys_survive_the_conversion():
    stored = _blob([1, 2, 3], ai_mode="out_of_hours", voice_id="v-1", setup_checklist={"crm": True})
    fixed = apply_plan(stored, plan_for(stored))
    assert fixed["ai_mode"] == "out_of_hours"
    assert fixed["voice_id"] == "v-1"
    assert fixed["setup_checklist"] == {"crm": True}


def test_start_and_end_are_not_touched():
    stored = _blob([1, 2, 3])
    stored["business_hours"]["start"] = "10:30"
    stored["business_hours"]["end"] = "20:00"
    fixed = apply_plan(stored, plan_for(stored))
    assert fixed["business_hours"]["start"] == "10:30"
    assert fixed["business_hours"]["end"] == "20:00"


# ---------------------------------------------------------------------------
# The loop itself: run() over a stand-in session
# ---------------------------------------------------------------------------
#
# There is no DB in this suite, so the session is faked. It is worth doing
# anyway: "idempotent" is a claim about the *loop* (does it re-read what it
# just wrote and shift again?), and asserting it on plan_for alone would be
# proving the easy half. Two consecutive apply runs over the same rows is the
# scenario an operator actually creates — a rerun after a timeout, a second
# deploy, a colleague running it too.


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class FakeSession:
    """Just enough of AsyncSession for run(): one select, one commit."""

    def __init__(self, rows):
        self.rows = rows
        self.commits = 0

    async def execute(self, _statement):
        return _FakeResult(self.rows)

    async def commit(self):
        self.commits += 1


def _agency(slug: str, settings: dict, updated_at: datetime | None = None):
    return SimpleNamespace(
        id=uuid.uuid4(), slug=slug, settings=settings, updated_at=updated_at
    )


def _run(session, *, apply, cutoff=None):
    return asyncio.run(run(session, apply=apply, panel_deployed_at=cutoff))


def test_run_twice_does_not_shift_twice():
    old = _agency("vieja", _blob([1, 2, 3, 4, 5]), datetime(2026, 6, 1, tzinfo=UTC))
    session = FakeSession([old])

    first = _run(session, apply=True, cutoff=CUTOFF)
    assert first[CONVERTED] == 1
    assert old.settings["business_hours"]["days"] == [0, 1, 2, 3, 4]

    second = _run(session, apply=True, cutoff=CUTOFF)
    assert second.get(CONVERTED) is None
    assert second[ALREADY_MARKED] == 1
    # The days a human would read on Monday morning, after two runs.
    assert old.settings["business_hours"]["days"] == [0, 1, 2, 3, 4]
    assert _open_on(old.settings, 0) is True
    assert _open_on(old.settings, 5) is False


def test_dry_run_writes_nothing_at_all():
    old = _agency("vieja", _blob([1, 2, 3]), datetime(2026, 6, 1, tzinfo=UTC))
    session = FakeSession([old])
    counts = _run(session, apply=False, cutoff=CUTOFF)
    assert counts[CONVERTED] == 1  # it still reports what it would do
    assert old.settings == _blob([1, 2, 3])
    assert session.commits == 0

    # ...and the real run afterwards still converts it: the dry run left no trace.
    assert _run(session, apply=True, cutoff=CUTOFF)[CONVERTED] == 1


def test_run_leaves_rows_it_cannot_classify_untouched():
    old = _agency("vieja", _blob([1, 2, 3, 4, 5]), datetime(2026, 6, 1, tzinfo=UTC))
    recent = _agency("reciente", _blob([1, 2, 3, 4, 5]), datetime(2026, 8, 1, tzinfo=UTC))
    broken = _agency(
        "rara",
        {"business_hours": {"start": "09:00", "end": "19:00", "days": ["1"]}},
        datetime(2026, 6, 1, tzinfo=UTC),
    )
    never = _agency("sin-horario", {"ai_mode": "always"}, datetime(2026, 6, 1, tzinfo=UTC))
    session = FakeSession([old, recent, broken, never])

    counts = _run(session, apply=True, cutoff=CUTOFF)
    assert counts == {CONVERTED: 1, RECENTLY_TOUCHED: 1, MALFORMED: 1, NO_HOURS: 1}
    assert old.settings["business_hours"]["days"] == [0, 1, 2, 3, 4]
    assert recent.settings["business_hours"]["days"] == [1, 2, 3, 4, 5]  # untouched
    assert CONVENTION_KEY not in recent.settings
    assert CONVENTION_KEY not in broken.settings
    assert never.settings[CONVENTION_KEY] == CONVENTION_PY

    # A second run keeps reporting the two unresolved ones instead of going quiet.
    again = _run(session, apply=True, cutoff=CUTOFF)
    assert again[RECENTLY_TOUCHED] == 1
    assert again[MALFORMED] == 1
    assert again[ALREADY_MARKED] == 2


def test_run_without_agencies_is_a_no_op():
    session = FakeSession([])
    assert _run(session, apply=True, cutoff=CUTOFF) == {}


def test_agency_with_null_settings_does_not_crash():
    blank = _agency("nueva", None, datetime(2026, 6, 1, tzinfo=UTC))
    session = FakeSession([blank])
    assert _run(session, apply=True, cutoff=CUTOFF) == {NO_HOURS: 1}
    assert blank.settings == {CONVENTION_KEY: CONVENTION_PY}
