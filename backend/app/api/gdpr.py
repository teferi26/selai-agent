"""GDPR endpoints — call from the dashboard or expose via signed magic link
to leads themselves so they can self-serve their data.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AgencyMemberDep, OwnerOnlyDep
from app.db.models import ConsentAction, ConsentLog, ConsentScope, Lead
from app.db.session import get_db
from app.services.gdpr import erase_lead, erasure_status, export_lead_data

# Every route here carries {agency_id}; membership is enforced router-wide.
# Lead-facing self-service will come later via signed magic links.
router = APIRouter(prefix="/gdpr", tags=["gdpr"], dependencies=[AgencyMemberDep])


class ConsentBody(BaseModel):
    scope: ConsentScope
    action: ConsentAction
    source: str = "dashboard"
    evidence: str = ""


@router.post("/agencies/{agency_id}/leads/{lead_id}/consent", status_code=201)
async def record_consent(
    agency_id: uuid.UUID,
    lead_id: uuid.UUID,
    body: ConsentBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    # (n5) The lead must BELONG to the agency in the path. Without this, a
    # member of agency A could write consent rows onto any lead id of agency
    # B: the row was stamped with A's agency_id, so B's own view missed it,
    # while the marketplace's consent gate — which checks by lead — read it
    # as valid. A poisoned audit trail that LOOKS complete is worse than a
    # missing one.
    lead = await db.get(Lead, lead_id)
    if lead is None or lead.agency_id != agency_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lead_not_found")

    log = ConsentLog(
        agency_id=agency_id,
        lead_id=lead_id,
        scope=body.scope,
        action=body.action,
        source=body.source,
        evidence=body.evidence,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.add(log)
    await db.commit()
    return {"id": str(log.id)}


@router.get("/agencies/{agency_id}/leads/{lead_id}/export")
async def export_lead(
    agency_id: uuid.UUID,
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    data = await export_lead_data(db, agency_id, lead_id)
    if data.get("error"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, data["error"])
    return data


class ErasureOut(BaseModel):
    erasure_reference: str
    local_erasure_completed: bool
    managed_artifacts_deleted: int
    external_erasure_status: Literal["not_required", "pending"]
    external_erasures_pending: int


class ExternalErasureStatusOut(BaseModel):
    provider: str
    resource_type: str
    status: str
    attempts: int
    completed_at: str | None


class ErasureStatusOut(BaseModel):
    erasure_reference: str
    local_erasure_completed: bool
    external_erasure_status: Literal["pending", "completed"]
    external_erasures_pending: int
    external_requests: list[ExternalErasureStatusOut]


@router.get(
    "/agencies/{agency_id}/erasures/{erasure_reference}",
    response_model=ErasureStatusOut,
    dependencies=[OwnerOnlyDep],
)
async def get_erasure_status(
    agency_id: uuid.UUID,
    erasure_reference: str,
    db: AsyncSession = Depends(get_db),
) -> ErasureStatusOut:
    if not erasure_reference.startswith("lead_") or len(erasure_reference) != 69:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "erasure_not_found")
    result = await erasure_status(db, agency_id, erasure_reference)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "erasure_not_found")
    return ErasureStatusOut.model_validate(result)


# Irreversible: art. 17 erasure wipes every local subsystem. Owner/admin only
# (item 175). A 202 response is deliberate: provider-side copies are reported
# as pending until their own positive confirmation arrives.
@router.delete(
    "/agencies/{agency_id}/leads/{lead_id}",
    status_code=202,
    response_model=ErasureOut,
    dependencies=[OwnerOnlyDep],
)
async def erase(
    agency_id: uuid.UUID,
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ErasureOut:
    result = await erase_lead(db, agency_id, lead_id)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lead_not_found")
    await db.commit()
    return ErasureOut(
        erasure_reference=result.subject_pseudonym,
        local_erasure_completed=result.local_erasure_completed,
        managed_artifacts_deleted=result.managed_artifacts_deleted,
        external_erasure_status=("pending" if result.external_erasures_pending else "not_required"),
        external_erasures_pending=result.external_erasures_pending,
    )
