"""Google Business Messages — webhook scaffold.

Google's Business Messages partner API uses a Pub/Sub push subscription.
Their JSON shape:
  {
    "agent": "brands/.../agents/...",
    "conversationId": "<id>",
    "message": { "messageId": "...", "name": "...", "text": "..." },
    "context": { "userInfo": { "displayName": "..." }, "placeId": "..." }
  }

Until we sign up as a partner, this endpoint is dormant — but wired so a
day-1 enable is a config change, not a code change.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.settings import settings
from app.db.models import Agency, ConversationChannel
from app.db.session import get_db
from app.services.conversation_service import InboundPayload, handle_inbound
from app.services.lead_resolver import InboundIdentity

router = APIRouter(prefix="/webhooks/google-business", tags=["google-business"])
log = get_logger(__name__)


@router.post("")
async def inbound(
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    if not settings.is_dev:
        expected = settings.secret_key.get_secret_value()
        if x_webhook_secret != expected:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad_secret")

    body = await request.json()
    msg = body.get("message") or {}
    text = msg.get("text") or ""
    if not text:
        return {"status": "ignored"}

    conversation_id = body.get("conversationId")
    agent_name = body.get("agent") or ""
    user_info = (body.get("context") or {}).get("userInfo") or {}
    display_name = user_info.get("displayName")

    agency_id = await _agency_for_gbm_agent(db, agent_name)
    if agency_id is None:
        log.warning("gbm.no_agency_match", agent=agent_name)
        return {"status": "no_agency_match"}

    identity = InboundIdentity(
        channel=ConversationChannel.WEB,  # treat GBM as web until we add a dedicated enum
        external_thread_id=conversation_id,
        display_name=display_name,
        source_campaign="google_business",
    )
    payload = InboundPayload(
        identity=identity,
        agency_id=agency_id,
        text=text,
        external_message_id=msg.get("messageId"),
        media=[],
        raw={"google_business": body},
    )
    try:
        await handle_inbound(db, payload)
        await db.commit()
    except Exception:
        await db.rollback()
        log.exception("gbm.handle_inbound.failed")
        raise

    # NOTE: outbound replies require a separate POST to the Business Messages
    # API with OAuth — implement once we have the partner credentials.
    return {"status": "ok"}


async def _agency_for_gbm_agent(db: AsyncSession, agent_name: str) -> uuid.UUID | None:
    if not agent_name:
        return None
    # `.as_string()` (not `.astext`) — the column is the generic sqlalchemy
    # JSON type; `.astext` is postgres-dialect-only and raised AttributeError
    # on every inbound Business Messages webhook.
    stmt = select(Agency).where(
        Agency.settings["google_business_agent_name"].as_string() == agent_name
    )
    agency = (await db.execute(stmt)).scalar_one_or_none()
    return agency.id if agency else None
