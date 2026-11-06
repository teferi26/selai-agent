"""Per-agency few-shot examples extracted from past WON conversations.

Idea: a setter that closes deals does it with a particular cadence,
opening lines, and objection handling. Surface 2-3 of those past
exchanges to the LLM as in-context examples so the AI matches the
agency's selling style without per-tenant fine-tuning.

We cache the rendered block on the agency itself (refreshed daily by a
Celery task) so the runner doesn't pay DB cost every turn.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Agency,
    Conversation,
    Lead,
    LeadStatus,
    Message,
    MessageRole,
)
from app.services.pii import REDACTION_VERSION, redact

MAX_EXAMPLES = 3
MAX_TURNS_PER_EXAMPLE = 6
MAX_CHARS_PER_TURN = 220
TTL_HOURS = 24


def get_cached_few_shot(agency: Agency) -> str | None:
    cache = (agency.settings or {}).get("few_shot_cache")
    if not cache:
        return None
    if cache.get("redaction_version") != REDACTION_VERSION:
        # Cache predates current PII rules — force a refresh next time.
        return None
    try:
        ts = datetime.fromisoformat(cache["computed_at"])
    except (KeyError, ValueError, TypeError):
        return None
    age_h = (datetime.now(UTC) - ts).total_seconds() / 3600
    if age_h > TTL_HOURS:
        return None
    return cache.get("block") or None


async def refresh_agency_few_shot(db: AsyncSession, agency_id: uuid.UUID) -> str:
    """Recompute the few-shot block from the most recent WON leads."""
    leads_stmt = (
        select(Lead)
        .where(Lead.agency_id == agency_id, Lead.status == LeadStatus.WON)
        .order_by(Lead.updated_at.desc())
        .limit(MAX_EXAMPLES * 3)  # over-fetch; we filter for "good" examples
    )
    won_leads = list((await db.execute(leads_stmt)).scalars().all())

    examples: list[str] = []
    for lead in won_leads:
        if len(examples) >= MAX_EXAMPLES:
            break
        block = await _render_lead_example(db, lead)
        if block:
            examples.append(block)

    if not examples:
        rendered = ""
    else:
        rendered = (
            "EJEMPLOS DE CONVERSACIONES QUE TERMINARON EN VENTA EN ESTA AGENCIA. "
            "Toma como referencia el tono, el orden de las preguntas y cómo se cierran "
            "objeciones. NO copies literal, adapta al lead actual.\n\n"
            + "\n\n---\n\n".join(examples)
        )

    agency = await db.get(Agency, agency_id)
    if agency is not None:
        s = dict(agency.settings or {})
        s["few_shot_cache"] = {
            "computed_at": datetime.now(UTC).isoformat(),
            "block": rendered,
            "redaction_version": REDACTION_VERSION,
        }
        agency.settings = s
        await db.flush()
    return rendered


async def _render_lead_example(db: AsyncSession, lead: Lead) -> str | None:
    convs = list(
        (
            await db.execute(
                select(Conversation)
                .where(Conversation.lead_id == lead.id)
                .order_by(Conversation.created_at.asc())
                .limit(1)
            )
        ).scalars().all()
    )
    if not convs:
        return None
    msgs_stmt = (
        select(Message)
        .where(Message.conversation_id == convs[0].id)
        .order_by(Message.created_at.asc())
        .limit(MAX_TURNS_PER_EXAMPLE)
    )
    msgs = list((await db.execute(msgs_stmt)).scalars().all())
    if len(msgs) < 2:
        return None

    lines: list[str] = [f"## Lead que cerró ({convs[0].channel.value})"]
    for m in msgs:
        if m.role == MessageRole.SYSTEM:
            continue
        who = "Cliente" if m.role == MessageRole.LEAD else "Agente"
        text = (m.content or "").strip().replace("\n", " ")
        # Examples come from CLOSED leads — never preserve their PII.
        text = redact(text)
        if len(text) > MAX_CHARS_PER_TURN:
            text = text[:MAX_CHARS_PER_TURN] + "…"
        lines.append(f"{who}: {text}")
    return "\n".join(lines)
