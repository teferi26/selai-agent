"""Audit log of every LLM invocation.

Captures inputs, outputs, tool calls, tokens and latency — critical to debug
agent behavior, compute cost per agency, and run offline evaluations/A-B tests
on prompts.
"""
from __future__ import annotations

import uuid

from sqlalchemy import JSON, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey


class AgentRun(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "agent_runs"

    agency_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agencies.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="SET NULL"), index=True
    )

    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), default="v1", nullable=False)

    # What the agent "thought" (summary of tool calls + final text)
    output_text: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # Full structured log of tool calls in order
    tool_calls: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)

    # Tokens + cost
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6))

    # Latency
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # If the run errored, capture why
    error: Mapped[str | None] = mapped_column(Text)

    # PII redaction state (Parte A — hardening): true once `output_text` and
    # `tool_calls` have been passed through `services.pii.redact()`. Old rows
    # default to false until the backfill script processes them.
    output_redacted: Mapped[bool] = mapped_column(default=False, nullable=False)
    redaction_version: Mapped[str | None] = mapped_column(String(8))

    # Output guardrail verdict — one of pass/rewrite/escalate/block (or null
    # for runs that errored before reaching the guardrail).
    guardrail_action: Mapped[str | None] = mapped_column(String(16))
