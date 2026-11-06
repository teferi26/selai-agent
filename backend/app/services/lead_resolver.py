"""Find or create the Lead + Conversation for an inbound message.

Match priority for leads:
  1. Same channel external id (whatsapp_wa_id, email, phone_e164)
  2. Same phone (E.164) across channels
  3. Otherwise create a new lead.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, ConversationChannel, Lead


@dataclass
class InboundIdentity:
    """Identifying info for the sender of a message."""
    channel: ConversationChannel
    external_thread_id: str | None = None
    phone_e164: str | None = None
    email: str | None = None
    whatsapp_wa_id: str | None = None
    display_name: str | None = None
    language: str | None = None
    source_campaign: str | None = None


async def resolve_lead_and_conversation(
    db: AsyncSession,
    agency_id: uuid.UUID,
    identity: InboundIdentity,
) -> tuple[Lead, Conversation]:
    lead = await _find_lead(db, agency_id, identity)
    if lead is None:
        lead = Lead(
            agency_id=agency_id,
            full_name=identity.display_name,
            phone_e164=identity.phone_e164,
            email=identity.email,
            whatsapp_wa_id=identity.whatsapp_wa_id,
            source_channel=identity.channel.value,
            source_campaign=identity.source_campaign,
            language=identity.language or "es",
        )
        db.add(lead)
        await db.flush()
    else:
        # Backfill missing identifiers (lead seen first via email, now sends WA, etc.)
        if identity.phone_e164 and not lead.phone_e164:
            lead.phone_e164 = identity.phone_e164
        if identity.email and not lead.email:
            lead.email = identity.email
        if identity.whatsapp_wa_id and not lead.whatsapp_wa_id:
            lead.whatsapp_wa_id = identity.whatsapp_wa_id
        if identity.display_name and not lead.full_name:
            lead.full_name = identity.display_name

    conv = await _find_conversation(db, agency_id, lead.id, identity)
    if conv is None:
        conv = Conversation(
            agency_id=agency_id,
            lead_id=lead.id,
            channel=identity.channel,
            external_thread_id=identity.external_thread_id,
        )
        db.add(conv)
        await db.flush()

    return lead, conv


async def _find_lead(
    db: AsyncSession, agency_id: uuid.UUID, identity: InboundIdentity
) -> Lead | None:
    clauses = []
    if identity.whatsapp_wa_id:
        clauses.append(Lead.whatsapp_wa_id == identity.whatsapp_wa_id)
    if identity.phone_e164:
        clauses.append(Lead.phone_e164 == identity.phone_e164)
    if identity.email:
        clauses.append(Lead.email == identity.email)
    if not clauses:
        return None

    stmt = select(Lead).where(Lead.agency_id == agency_id, or_(*clauses)).limit(1)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _find_conversation(
    db: AsyncSession,
    agency_id: uuid.UUID,
    lead_id: uuid.UUID,
    identity: InboundIdentity,
) -> Conversation | None:
    stmt = (
        select(Conversation)
        .where(
            Conversation.agency_id == agency_id,
            Conversation.lead_id == lead_id,
            Conversation.channel == identity.channel,
        )
        .order_by(Conversation.created_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()
