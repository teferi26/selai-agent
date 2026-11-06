"""Backfill `agency_id` into the metadata of the Vapi assistants we already created.

Every assistant provisioned before `_provision_vapi_assistant` started writing
`agency_id` (app/services/onboarding.py) carries only a `slug` — and for a
while that slug was wrong, because the onboarding function reused the loop
variable of the legal-consent loop and passed "privacy_policy" as the agency
slug. Both problems mean an inbound call through those assistants cannot be
attributed to an agency by metadata alone.

`_resolve_agency` (app/api/voice.py) recovers them at call time via the
assistant id / phone number id, so nothing is broken while this has not run.
This script closes the gap at the source: it rewrites each assistant's
metadata to `{"slug": <real slug>, "agency_id": <uuid>}`, keeping any other
key that is already there.

Idempotent by construction: it reads the assistant's current metadata first
and only PATCHes when the result would differ, so a second run reports
"al día" for everything and issues no writes.

Two things it refuses to do, because both would carve a cross-tenant misroute
into Vapi and then report success:

- touch an assistant id that appears in more than one agency's settings;
- overwrite an `agency_id` that is already there and names a *different*
  agency (that is what a duplicated assistant looks like). `--force` does it
  anyway, for when the operator has checked which agency owns the assistant.

Usage (inside the backend container):

    python -m app.scripts.backfill_vapi_metadata
    python -m app.scripts.backfill_vapi_metadata --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.settings import settings
from app.db.models import Agency

VAPI_BASE = "https://api.vapi.ai"
HTTP_TIMEOUT = 20


def desired_metadata(current: dict[str, Any], *, agency_id: str, slug: str) -> dict[str, Any]:
    """The metadata the assistant should end up with.

    Pure, so the merge rule is unit-testable. Other keys are preserved: Vapi's
    PATCH replaces the whole `metadata` object, so anything we do not send is
    deleted. `slug` is overwritten on purpose — our database is the authority
    on it and some assistants carry the wrong one.
    """
    merged = dict(current)
    merged["agency_id"] = agency_id
    merged["slug"] = slug
    return merged


async def _get_metadata(http: httpx.AsyncClient, assistant_id: str) -> dict[str, Any] | None:
    """Current metadata of a Vapi assistant, or None if it cannot be read."""
    resp = await http.get(f"{VAPI_BASE}/assistant/{assistant_id}")
    if resp.status_code >= 400:
        print(f"  ! no se pudo leer el asistente {assistant_id}: HTTP {resp.status_code}")
        return None
    metadata = resp.json().get("metadata")
    return metadata if isinstance(metadata, dict) else {}


async def _patch_metadata(
    http: httpx.AsyncClient, assistant_id: str, metadata: dict[str, Any]
) -> bool:
    resp = await http.patch(f"{VAPI_BASE}/assistant/{assistant_id}", json={"metadata": metadata})
    if resp.status_code >= 400:
        print(f"  ! PATCH rechazado para {assistant_id}: HTTP {resp.status_code} {resp.text[:200]}")
        return False
    # A 2xx is not proof that Vapi stored what we sent (unknown fields get
    # dropped silently by plenty of APIs). When the response echoes the
    # assistant back, check it, so "actualizado" means actualizado.
    try:
        echoed = resp.json().get("metadata")
    except ValueError:
        echoed = None
    if isinstance(echoed, dict) and echoed != metadata:
        print(f"  ! Vapi aceptó el PATCH pero guardó otra cosa: {echoed}")
        return False
    return True


def assistants_claimed_by_several(targets: list[tuple[Any, str]]) -> dict[str, list[str]]:
    """Assistant ids stored by more than one agency, mapped to those agencies.

    Pure so it is unit-testable. Backfilling one of these would stamp the last
    agency's id on an assistant several agencies believe is theirs.
    """
    owners: dict[str, list[str]] = {}
    for agency, assistant_id in targets:
        owners.setdefault(assistant_id, []).append(agency.slug)
    return {aid: slugs for aid, slugs in owners.items() if len(slugs) > 1}


def conflicting_agency_id(current: dict[str, Any], *, agency_id: str) -> str | None:
    """An `agency_id` already in Vapi that names a different agency, if any.

    Pure so it is unit-testable. This is the fingerprint of an assistant that
    was duplicated from another agency's: stamping our id over it would make
    the misroute permanent instead of fixing it.
    """
    existing = current.get("agency_id")
    if isinstance(existing, str) and existing and existing != agency_id:
        return existing
    return None


async def run(*, dry_run: bool = False, force: bool = False) -> int:
    api_key = settings.vapi_api_key.get_secret_value()
    if not api_key:
        # Loud and non-zero: the caller must not read "0 asistentes
        # actualizados" as "ya estaba todo hecho".
        print("VAPI_API_KEY no está configurada: no se ha tocado ningún asistente.")
        return 1

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        agencies = list((await db.execute(select(Agency))).scalars().all())
    await engine.dispose()

    # Filtering in Python instead of in SQL: the agencies table is small and
    # this keeps the script free of JSON-operator dialect quirks.
    targets = [(a, str((a.settings or {}).get("vapi_assistant_id") or "")) for a in agencies]
    targets = [(a, aid) for a, aid in targets if aid]

    # Same assistant id in two agencies: whichever we wrote last would own
    # every call through it. Skip them and say so — a human has to decide.
    shared = assistants_claimed_by_several(targets)

    print(f"{len(agencies)} agencias, {len(targets)} con asistente de Vapi.")

    patched = up_to_date = failed = skipped = 0
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers=headers) as http:
        for agency, assistant_id in targets:
            print(f"- {agency.slug} ({assistant_id})")
            if assistant_id in shared:
                print(f"  ! SIN TOCAR: este asistente figura en {shared[assistant_id]}. "
                      "Arregla el settings de esas agencias antes de volver a pasar.")
                skipped += 1
                continue

            current = await _get_metadata(http, assistant_id)
            if current is None:
                failed += 1
                continue

            other = conflicting_agency_id(current, agency_id=str(agency.id))
            if other and not force:
                print(f"  ! SIN TOCAR: en Vapi ya pone agency_id={other}, que no es {agency.id}. "
                      "Parece un asistente duplicado; compruébalo y usa --force si procede.")
                skipped += 1
                continue

            wanted = desired_metadata(current, agency_id=str(agency.id), slug=agency.slug)
            if wanted == current:
                print("  ya está al día")
                up_to_date += 1
                continue

            if dry_run:
                print(f"  [dry-run] quedaría {wanted}")
                patched += 1
                continue

            if await _patch_metadata(http, assistant_id, wanted):
                print(f"  actualizado -> {wanted}")
                patched += 1
            else:
                failed += 1

    verb = "se actualizarían" if dry_run else "actualizados"
    print(
        f"Hecho. {patched} {verb}, {up_to_date} ya al día, "
        f"{skipped} sin tocar por conflicto, {failed} con error."
    )
    # Non-zero when anything was left undone: "0 con error" on its own would
    # read as "todo listo" while assistants stay unroutable.
    return 1 if (failed or skipped) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra qué metadata quedaría en cada asistente sin escribir nada en Vapi.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Sobrescribe también los asistentes cuya metadata ya nombra a otra agencia. "
            "Úsalo solo tras comprobar de quién es cada asistente."
        ),
    )
    args = parser.parse_args()
    return asyncio.run(run(dry_run=args.dry_run, force=args.force))


if __name__ == "__main__":
    sys.exit(main())
