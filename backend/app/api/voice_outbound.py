"""Trigger outbound voice calls (proactive setter for dormant leads).

Used by:
- Dashboard "Llamar a este lead" button.
- Celery sweep `leads.proactive_outbound_voice` for dormant high-temp leads.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.voice.client import VapiClient
from app.core.auth import AgencyMemberDep
from app.core.logging import get_logger
from app.db.models import Agency, Lead
from app.db.session import get_db
from app.services.plan_limits import ensure_can_use_voice

router = APIRouter(
    prefix="/api/v1/agencies/{agency_id}/voice",
    tags=["voice"],
    dependencies=[AgencyMemberDep],
)
log = get_logger(__name__)


class OutboundCallBody(BaseModel):
    lead_id: uuid.UUID
    assistant_id: str | None = None  # override; otherwise read from agency settings


@router.post("/outbound-call", status_code=202)
async def outbound_call(
    agency_id: uuid.UUID,
    body: OutboundCallBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    await ensure_can_use_voice(db, agency_id)
    lead = await db.get(Lead, body.lead_id)
    if lead is None or lead.agency_id != agency_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lead_not_found")
    if not lead.phone_e164:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "lead_has_no_phone")

    agency = await db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agency_not_found")

    assistant_id = body.assistant_id or (agency.settings or {}).get("vapi_assistant_id")
    if not assistant_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no_vapi_assistant_configured")

    client = VapiClient()
    try:
        result = await client.place_outbound_call(
            assistant_id=assistant_id,
            customer_number_e164=lead.phone_e164,
            metadata={
                "agency_id": str(agency_id),
                "lead_id": str(lead.id),
                "customer_name": lead.full_name,
            },
        )
    except Exception as exc:
        log.exception("voice.outbound.failed", lead=str(lead.id))
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"vapi_error: {exc}",
        ) from exc

    return {"status": "dispatched", "call_id": result.get("id", "")}
