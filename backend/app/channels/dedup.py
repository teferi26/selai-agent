"""Inbound de-duplication shared by the channel webhooks (item 173).

Meta and Resend both retry a webhook whose response was slow or lost. The
retry carries the *same* provider message id, so without a check the lead
gets the agent's answer twice — the failure that most directly contradicts
what the product promises.

Two layers, on purpose:

- this short-circuit, run *before* anything with side effects (no agent run,
  no reply, no write), so a retry costs one indexed query;
- the partial unique index on `messages (agency_id, external_message_id)`
  (migration 20260725_0012), which is what actually holds when two workers
  process the two copies at the same time and both pass the check.

The query is scoped by `agency_id` like every other query in the product:
two tenants may legitimately hold the same provider id.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Message, MessageDirection


async def inbound_already_processed(
    db: AsyncSession, agency_id: uuid.UUID, external_message_id: str | None
) -> bool:
    """True when this agency already stored an *inbound* message with that
    provider id.

    A payload without an id cannot be deduplicated (nothing to compare), so
    it is always treated as new: better a rare duplicate than dropping every
    message a provider sends without one.

    Inbound only, matching the index. Outbound rows carry whatever the
    sending client returned — including the literal "dev-stub" when a client
    is not configured — and letting one of those answer "yes, already seen"
    would silently drop a real lead's message.
    """
    if not external_message_id:
        return False

    stmt = (
        select(Message.id)
        .where(
            Message.agency_id == agency_id,
            Message.external_message_id == external_message_id,
            Message.direction == MessageDirection.INBOUND,
        )
        .limit(1)
    )
    return (await db.execute(stmt)).first() is not None
