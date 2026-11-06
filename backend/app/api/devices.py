"""Mobile device registration for Expo push notifications."""
from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, StringConstraints
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AgencyMemberDep, AuthContext, require_agency_member
from app.core.logging import get_logger
from app.db.models import DeviceToken
from app.db.session import get_db

router = APIRouter(
    prefix="/api/v1/agencies/{agency_id}/devices",
    tags=["devices"],
    dependencies=[AgencyMemberDep],
)
log = get_logger(__name__)


ExpoPushToken = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=10, max_length=255),
]


class DeviceRegistration(BaseModel):
    expo_push_token: ExpoPushToken
    platform: Literal["ios", "android"]


class DeviceUnregistration(BaseModel):
    expo_push_token: ExpoPushToken


@router.post("", status_code=status.HTTP_201_CREATED)
async def register_device(
    agency_id: uuid.UUID,
    body: DeviceRegistration,
    auth: AuthContext = Depends(require_agency_member),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    """Upsert by token: reinstalls and agency switches re-point the row."""
    existing = (
        await db.execute(
            select(DeviceToken).where(DeviceToken.expo_push_token == body.expo_push_token)
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.agency_id = agency_id
        existing.platform = body.platform
        if auth.user is not None:
            existing.user_id = auth.user.id
    else:
        db.add(
            DeviceToken(
                agency_id=agency_id,
                user_id=auth.user.id if auth.user is not None else None,
                expo_push_token=body.expo_push_token,
                platform=body.platform,
            )
        )
    await db.commit()
    return {"ok": True}


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_device(
    agency_id: uuid.UUID,
    body: DeviceUnregistration,
    auth: AuthContext = Depends(require_agency_member),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Forget this install without revealing whether its token existed.

    A signed-in user may only remove their own token (or an old unowned row)
    from their agency.  The idempotent 204 keeps logout reliable when Expo
    retries or the row was already cleaned up.
    """
    conditions = [
        DeviceToken.expo_push_token == body.expo_push_token,
        DeviceToken.agency_id == agency_id,
    ]
    if auth.user is not None:
        conditions.append(
            or_(DeviceToken.user_id == auth.user.id, DeviceToken.user_id.is_(None))
        )
    await db.execute(delete(DeviceToken).where(*conditions))
    await db.commit()
    log.info(
        "devices.unregistered",
        agency=str(agency_id),
        user=str(auth.user.id) if auth.user is not None else None,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
