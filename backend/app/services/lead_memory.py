"""Cross-channel memory for a lead.

The agent runner only sees the current conversation's transcript. This
module provides a compact summary of the lead's *other* conversations
(across channels and time) so the agent can answer "as we discussed by
email last month..." without re-asking everything.

Strategy:
- For each closed/different conversation, take a short slice of recent
  messages.
- Compose a chronological "memory bullet" list. ~700 tokens hard cap.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, Message, MessageRole
from app.services.pii import redact

MAX_OTHER_CONVOS = 5
MAX_MSGS_PER_CONVO = 6
MAX_CHARS_PER_MSG = 240
MAX_TOTAL_CHARS = 3500


async def build_cross_channel_memory(
    db: AsyncSession,
    *,
    lead_id: uuid.UUID,
    exclude_conversation_id: uuid.UUID,
    allow_first_name_of: str | None = None,
) -> str:
    """Return a concise plaintext recap of past conversations of this lead
    on other channels / older threads. Empty string if nothing relevant."""
    convs = list(
        (
            await db.execute(
                select(Conversation)
                .where(
                    Conversation.lead_id == lead_id,
                    Conversation.id != exclude_conversation_id,
                )
                .order_by(Conversation.updated_at.desc())
                .limit(MAX_OTHER_CONVOS)
            )
        ).scalars().all()
    )
    if not convs:
        return ""

    chunks: list[str] = []
    for conv in convs:
        msgs = list(
            (
                await db.execute(
                    select(Message)
                    .where(Message.conversation_id == conv.id)
                    .order_by(Message.created_at.desc())
                    .limit(MAX_MSGS_PER_CONVO)
                )
            ).scalars().all()
        )
        msgs.reverse()
        if not msgs:
            continue

        when = conv.updated_at.strftime("%Y-%m-%d")
        header = f"\n## {when} — canal {conv.channel.value}"
        lines = [header]
        for m in msgs:
            who = (
                "Cliente"
                if m.role == MessageRole.LEAD
                else "Agente humano"
                if m.role == MessageRole.AGENT_HUMAN
                else "Agente IA"
            )
            content = (m.content or "").strip().replace("\n", " ")
            content = redact(content, allow_first_name_of=allow_first_name_of)
            if len(content) > MAX_CHARS_PER_MSG:
                content = content[:MAX_CHARS_PER_MSG] + "…"
            lines.append(f"- {who}: {content}")
        chunks.append("\n".join(lines))

    out = "\n".join(chunks).strip()
    if len(out) > MAX_TOTAL_CHARS:
        out = out[:MAX_TOTAL_CHARS] + "\n…(truncado)"
    return out
