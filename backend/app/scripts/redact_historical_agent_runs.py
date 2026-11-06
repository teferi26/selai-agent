"""Backfill PII redaction on historical `agent_runs`.

Idempotent: only touches rows where `output_redacted=false`. Processes in
chunks of 1000 so it can be run safely on large tables without holding a
single huge transaction.

Usage (inside the backend container):

    python -m app.scripts.redact_historical_agent_runs

Run once after deploying the migration `20260503_0006_agent_run_redaction`.
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings import settings
from app.db.models import AgentRun, Lead
from app.services.pii import REDACTION_VERSION, redact, redact_dict

CHUNK_SIZE = 1000


async def _process_chunk(db: AsyncSession) -> int:
    stmt = (
        select(AgentRun)
        .where(AgentRun.output_redacted == False)  # noqa: E712 — SQLAlchemy bool literal
        .limit(CHUNK_SIZE)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    if not rows:
        return 0

    # Lookup full_name per lead so the lead's own first name survives
    lead_ids = {r.lead_id for r in rows if r.lead_id is not None}
    lead_names: dict = {}
    if lead_ids:
        leads = list(
            (await db.execute(select(Lead).where(Lead.id.in_(lead_ids)))).scalars().all()
        )
        lead_names = {lead.id: lead.full_name for lead in leads}

    for run in rows:
        allow_name = lead_names.get(run.lead_id) if run.lead_id else None
        run.output_text = redact(run.output_text or "", allow_first_name_of=allow_name)
        new_calls: list[dict] = []
        for tc in run.tool_calls or []:
            if isinstance(tc, dict) and tc.get("name") == "search_properties":
                new_calls.append(tc)
            elif isinstance(tc, dict):
                new_calls.append(
                    {
                        "name": tc.get("name"),
                        "input": redact_dict(tc.get("input"), allow_first_name_of=allow_name),
                        "result": redact(tc.get("result", "") or "", allow_first_name_of=allow_name),
                    }
                )
            else:
                new_calls.append(tc)
        run.tool_calls = new_calls
        run.output_redacted = True
        run.redaction_version = REDACTION_VERSION

    await db.commit()
    return len(rows)


async def main() -> int:
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    total = 0
    while True:
        async with session_factory() as db:
            n = await _process_chunk(db)
        if n == 0:
            break
        total += n
        print(f"redacted {total} rows so far...")
    await engine.dispose()
    print(f"done. {total} rows updated.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
