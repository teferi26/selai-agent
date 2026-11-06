"""Move agencies off voices that were removed from the catalog.

Two entries of the voice catalog (app/api/voice_settings.py) carried invented
Cartesia ids — `sonic-multilingual-es-male` and `sonic-multilingual-es-neutral`
— and were replaced by real ElevenLabs voices. Deleting the entries does not
delete the choice an agency already made with them, and that choice lives in
two places at once:

  - `agencies.settings.voice_id/voice_provider/voice_name`, which is the only
    thing both panels use to preselect a voice. With a dead id there, the
    screen says the agency has chosen nothing.
  - the agency's Vapi assistant, PATCHed with the dead voiceId at the moment
    it was chosen. That is the one that actually speaks on the phone, and no
    amount of fixing the database changes it.

So this repairs both, in that order, one agency at a time. It is idempotent:
the second run finds no retired id and writes nothing.

`--dry-run` prints the plan without touching the database or Vapi. The default
is to apply, matching `backfill_vapi_metadata.py` (the risk here is the
opposite of that of `fix_business_hours_days.py`: leaving an agency on a voice
that cannot speak is worse than rewriting a pointer that is already dead).

Usage (inside the backend container):

    python -m app.scripts.fix_retired_voices
    python -m app.scripts.fix_retired_voices --dry-run

Exit code is 1 if any agency was left half-repaired — database updated but
Vapi still on the dead voice — because that agency's calls are still broken
and somebody has to know.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.voice_settings import CatalogVoice, _apply_voice_to_vapi, replacement_for
from app.core.settings import settings
from app.db.models import Agency


@dataclass(frozen=True)
class Repair:
    """What one agency needs, decided before anything is written."""

    agency_slug: str
    retired_id: str
    voice: CatalogVoice
    assistant_id: str | None


def repair_for(agency: Agency) -> Repair | None:
    """The repair this agency needs, or None if it is already fine.

    Pure (reads only the ORM object) so the whole decision is unit-testable.
    """
    blob = agency.settings or {}
    voice_id = blob.get("voice_id")
    replacement = replacement_for(voice_id if isinstance(voice_id, str) else None)
    if replacement is None:
        return None

    assistant_id = blob.get("vapi_assistant_id")
    return Repair(
        agency_slug=agency.slug,
        retired_id=str(voice_id),
        voice=replacement,
        assistant_id=str(assistant_id) if assistant_id else None,
    )


def repaired_settings(current: dict[str, Any], voice: CatalogVoice) -> dict[str, Any]:
    """The settings blob with the three voice keys pointing at `voice`.

    A new dict, not an in-place edit: `Agency.settings` is a JSON column and
    SQLAlchemy does not track mutations inside one. Every other key is carried
    over — this blob also holds the assistant id, the phone number and the
    agency's AI instructions.
    """
    return {
        **current,
        "voice_provider": voice.provider,
        "voice_id": voice.id,
        "voice_name": voice.name,
    }


async def run(*, dry_run: bool = False) -> int:
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    half_repaired: list[str] = []
    repaired = 0

    async with session_factory() as db:
        # Filtered in Python, not in SQL: the agencies table is small and this
        # keeps the script free of JSON-operator dialect quirks.
        agencies = list((await db.execute(select(Agency))).scalars().all())
        plans = [(a, r) for a in agencies if (r := repair_for(a)) is not None]

        print(f"{len(agencies)} agencias, {len(plans)} con una voz retirada.")

        for agency, plan in plans:
            print(f"- {plan.agency_slug}: {plan.retired_id} -> {plan.voice.id} ({plan.voice.name})")
            if dry_run:
                continue

            agency.settings = repaired_settings(dict(agency.settings or {}), plan.voice)
            await db.commit()
            repaired += 1

            # The assistant is what actually speaks. A failure here leaves the
            # agency listed below and the run exits non-zero.
            if not plan.assistant_id or not settings.vapi_api_key.get_secret_value():
                print("  ! sin asistente de Vapi (o sin VAPI_API_KEY): nada que aplicar")
                half_repaired.append(plan.agency_slug)
                continue
            if await _apply_voice_to_vapi(plan.assistant_id, plan.voice.provider, plan.voice.id):
                print("  asistente actualizado")
            else:
                print("  ! el PATCH a Vapi falló: el asistente sigue con la voz muerta")
                half_repaired.append(plan.agency_slug)

    await engine.dispose()

    if dry_run:
        print(f"Hecho (dry-run). {len(plans)} agencias se corregirían.")
        return 0

    print(f"Hecho. {repaired} agencias corregidas, {len(half_repaired)} sin aplicar en Vapi.")
    if half_repaired:
        print("Estas agencias tienen el ajuste bien pero el asistente no: " + ", ".join(half_repaired))
    return 1 if half_repaired else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra qué agencias se moverían de voz sin escribir nada.",
    )
    args = parser.parse_args()
    return asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
