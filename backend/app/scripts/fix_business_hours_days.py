"""Fix `settings.business_hours.days` written with the old panel convention.

Until July 2026 the dashboard wrote weekdays as 1 = Monday … 7 = Sunday, while
the backend has always read them as `datetime.weekday()` numbers (0 = Monday).
The API validator only ever rejected the out-of-range 7, so every other value
was stored happily and then applied one day late: an agency that ticked
"lunes a viernes" was attended Tuesday to Saturday. Nothing in the UI showed
it, because the GET returns the raw number and the panel repainted it with its
own scale.

The panel now writes 0..6. This script repairs the rows written before that.

WHAT COUNTS AS "OLD CONVENTION" (read this before running)
----------------------------------------------------------
A stored set like [1,2,3] is genuinely ambiguous when you look at it alone: it
is "martes a jueves" under the new convention and "lunes a miércoles" under the
old one. Three things make the decision safe:

  1. `business_hours` has exactly one writer in the whole product — the panel's
     IAClient.tsx. No seed, no onboarding, no mobile screen and no backend path
     ever writes it. So every row that predates the panel fix was written with
     the old scale, and shifting it by -1 is not a guess.
  2. The old panel could not emit 0. Any set containing 0 therefore came from
     the fixed panel and is left alone.
  3. `--panel-deployed-at` closes the one hole rule 2 leaves open. An agency
     that saves a Monday-less schedule (Tue-Sat, say) from the FIXED panel,
     in the window between the deploy and this run, looks exactly like an
     old-convention row. Rule 2 cannot tell them apart — but `agencies
     .updated_at` can: a row last written after the deploy might be new, so
     it is reported for a human instead of shifted. Required with --apply,
     because silently shifting a schedule that was already right is the very
     bug this migration exists to undo.

Nothing is ever shifted twice and nothing is ever shifted on a guess: rows the
script cannot classify are left untouched, printed, and the run exits 1.

ORDER OF OPERATIONS
-------------------
Deploy the fixed panel FIRST, then run this, and pass the deploy time as
`--panel-deployed-at`. The reverse order is worse: the old panel would keep
writing 1..7 into rows this script has already marked as converted, and the
marker would stop anyone from ever noticing.

The conversion is recorded as `settings.business_hours_convention = "py"`, a
sibling of `business_hours`. PATCH /settings preserves unknown keys, so the
marker survives every later edit and a second run is a no-op.

Usage (inside the backend container):

    python -m app.scripts.fix_business_hours_days                       # dry run
    python -m app.scripts.fix_business_hours_days \
        --apply --panel-deployed-at 2026-07-31T20:00:00+00:00          # writes

A dry run is the default on purpose: this rewrites customer configuration.
Exit code is 1 while any agency still needs a human decision.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings import settings as app_settings
from app.db.models import Agency

# Marker written into the settings blob once an agency's days are known to be
# in Python convention. Its only job is to make a second run a no-op.
CONVENTION_KEY = "business_hours_convention"
CONVENTION_PY = "py"

CONVERTED = "converted"
ALREADY_MARKED = "already_marked"
ALREADY_PY = "already_py"
NO_HOURS = "no_hours"
MALFORMED = "malformed"
NEEDS_REVIEW = "needs_review"
RECENTLY_TOUCHED = "recently_touched"

# Actions that leave an agency still needing a human. They are printed one by
# one and they make the run exit non-zero: a migration that cannot classify a
# row has not finished, and saying otherwise is the failure mode this whole
# change is about.
UNRESOLVED = (NEEDS_REVIEW, MALFORMED, RECENTLY_TOUCHED)


@dataclass(frozen=True)
class Plan:
    """What the script would do to one agency, and why."""

    action: str
    reason: str
    before: list[int] | None = None
    after: list[int] | None = None

    @property
    def writes(self) -> bool:
        """Whether applying this plan changes the stored blob.

        MALFORMED is deliberately absent. `is_within_business_hours` coerces
        with `int(d)`, so days like ["1","2"] are unusable *here* but perfectly
        live in production — still shifted, still wrong. Stamping such a row as
        converted would freeze the damage: every later run would skip it and
        the report would say the migration is done.
        """
        return self.action in (CONVERTED, ALREADY_PY, NO_HOURS)


def _read_days(blob: dict) -> list[int] | None:
    """The stored days as a list of ints, or None if unusable.

    Deliberately strict: anything the backend itself would refuse to read
    (`is_within_business_hours` falls back to its default on any exception)
    is not something we should be arithmetic on.
    """
    hours = blob.get("business_hours")
    if not isinstance(hours, dict):
        return None
    raw = hours.get("days")
    if not isinstance(raw, list) or not raw:
        return None
    days: list[int] = []
    for value in raw:
        # bool is an int subclass; True would silently become day 1.
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        days.append(value)
    return days


def was_touched_after(updated_at: datetime | None, cutoff: datetime | None) -> bool:
    """Could this row have been written by the already-fixed panel?

    `None` cutoff means the caller did not supply a deploy time, so the guard
    is off and every row is treated as old (the pre-guard behaviour, only
    reachable on a dry run). A missing `updated_at` counts as touched: when the
    evidence is absent the safe answer is "I cannot tell", never "it is old".
    """
    if cutoff is None:
        return False
    if updated_at is None:
        return True
    return _as_utc(updated_at) >= _as_utc(cutoff)


def _as_utc(value: datetime) -> datetime:
    """Naive timestamps are read as UTC — that is what the DB stores."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def plan_for(blob: dict, *, touched_after_panel_fix: bool = False) -> Plan:
    """Decide what to do with one agency's settings blob. Pure."""
    if blob.get(CONVENTION_KEY) == CONVENTION_PY:
        return Plan(ALREADY_MARKED, "already converted by a previous run")

    days = _read_days(blob)
    if days is None:
        if not isinstance(blob.get("business_hours"), dict):
            # Nothing to convert now, but mark it: from here on the only
            # writer is the fixed panel, so a later run must not touch it.
            return Plan(NO_HOURS, "no business_hours stored")
        return Plan(MALFORMED, "business_hours present but days unusable")

    out_of_range = [d for d in days if d < 0 or d > 7]
    if out_of_range:
        return Plan(NEEDS_REVIEW, f"days outside 0-7: {out_of_range}", before=days)
    if 0 in days and 7 in days:
        # Contradictory: 0 only exists in the new scale, 7 only in the old one.
        return Plan(NEEDS_REVIEW, "contains both 0 and 7", before=days)
    if 0 in days:
        # The old panel had no 0, so this row already speaks Python weekdays.
        # Unambiguous whatever the timestamp says, so it is checked first.
        return Plan(ALREADY_PY, "contains day 0, cannot come from the old panel", before=days)

    shifted = sorted({d - 1 for d in days})
    if touched_after_panel_fix:
        # Monday-less schedule written after the panel was fixed: it may
        # already be correct (Tue-Sat really means Tue-Sat) or it may be an old
        # row the agency happened to re-save. Shifting on a coin flip would
        # recreate the exact bug we are undoing, invisibly.
        return Plan(
            RECENTLY_TOUCHED,
            "settings written after the panel fix and no day 0 to prove the scale",
            before=sorted(set(days)),
            after=shifted,
        )
    return Plan(CONVERTED, "shifted one day back", before=sorted(set(days)), after=shifted)


def apply_plan(blob: dict, plan: Plan) -> dict:
    """Return a NEW blob with the plan applied.

    Never mutates in place: the caller reassigns `agency.settings` so
    SQLAlchemy's change tracking notices (a JSON column mutated in place does
    not persist).
    """
    if not plan.writes:
        return blob
    updated = {**blob, CONVENTION_KEY: CONVENTION_PY}
    if plan.action == CONVERTED and plan.after is not None:
        hours = dict(updated["business_hours"])
        hours["days"] = plan.after
        updated["business_hours"] = hours
    return updated


async def run(
    db: AsyncSession,
    *,
    apply: bool,
    panel_deployed_at: datetime | None = None,
) -> dict[str, int]:
    agencies = list((await db.execute(select(Agency))).scalars().all())
    counts: dict[str, int] = {}
    review: list[str] = []

    for agency in agencies:
        blob = dict(agency.settings or {})
        plan = plan_for(
            blob,
            touched_after_panel_fix=was_touched_after(
                getattr(agency, "updated_at", None), panel_deployed_at
            ),
        )
        counts[plan.action] = counts.get(plan.action, 0) + 1

        if plan.action == CONVERTED:
            # Printed for every single conversion: this is the audit trail.
            print(
                f"  [{plan.action}] {agency.slug} ({agency.id}): "
                f"days {plan.before} -> {plan.after}"
            )
        elif plan.action in UNRESOLVED:
            line = (
                f"  [{plan.action}] {agency.slug} ({agency.id}): {plan.reason}"
                f" | days {plan.before}"
                + (f" | would become {plan.after}" if plan.after is not None else "")
            )
            print(line)
            review.append(line)

        if apply and plan.writes:
            agency.settings = apply_plan(blob, plan)

    if apply:
        await db.commit()

    print()
    print(f"agencies inspected: {len(agencies)}")
    for action in (
        CONVERTED,
        ALREADY_PY,
        ALREADY_MARKED,
        NO_HOURS,
        MALFORMED,
        RECENTLY_TOUCHED,
        NEEDS_REVIEW,
    ):
        if counts.get(action):
            print(f"  {action}: {counts[action]}")
    if review:
        print()
        print("STILL PENDING — left untouched, unmarked, decide by hand:")
        for line in review:
            print(line)
        print(
            "  These are NOT converted. Until you resolve them the migration is\n"
            "  not finished, which is why this run exits 1."
        )
    return counts


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="fix_business_hours_days",
        description="Repair business_hours.days written with the old 1..7 panel scale.",
    )
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    parser.add_argument(
        "--panel-deployed-at",
        metavar="ISO8601",
        help=(
            "when the fixed panel went live, e.g. 2026-07-31T20:00:00+00:00. "
            "Rows written at or after that instant are reported instead of "
            "shifted. Required with --apply."
        ),
    )
    args = parser.parse_args(argv)
    if args.panel_deployed_at:
        try:
            args.cutoff = datetime.fromisoformat(args.panel_deployed_at)
        except ValueError:
            parser.error(
                f"--panel-deployed-at: {args.panel_deployed_at!r} is not an ISO-8601 datetime"
            )
    else:
        args.cutoff = None
    if args.apply and args.cutoff is None:
        # Refusing is the point: without a cutoff the script cannot tell a
        # Monday-less schedule saved by the fixed panel from an old row, and
        # would shift a correct schedule without anyone noticing.
        parser.error("--apply requires --panel-deployed-at (the time the fixed panel went live)")
    return args


async def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    if not args.apply:
        print("DRY RUN — nothing will be written. Re-run with --apply to commit.")
    if args.cutoff is None:
        print(
            "WARNING: no --panel-deployed-at, so rows saved by the fixed panel "
            "cannot be told apart from old ones. Counts below are an upper bound."
        )
    else:
        print(f"panel deployed at {_as_utc(args.cutoff).isoformat()} (rows newer than this are reported, not shifted)")
    print()

    engine = create_async_engine(app_settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            counts = await run(db, apply=args.apply, panel_deployed_at=args.cutoff)
    finally:
        await engine.dispose()

    print()
    if args.apply:
        print("done. changes committed.")
    else:
        print("done. no changes written (dry run).")
    return 1 if any(counts.get(action) for action in UNRESOLVED) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
