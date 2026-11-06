"""Who am I — the dashboard's session bootstrap.

Returns the authenticated user, their agency and the subscription state
in one call, so the frontend never needs a hardcoded agency id.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, require_auth
from app.db.models import Agency
from app.db.session import get_db
from app.services.plan_limits import get_plan_state, get_usage

router = APIRouter(prefix="/api/v1/me", tags=["me"])


class MeUsage(BaseModel):
    resource: str
    used: int
    limit: int


class MeOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    agency_id: uuid.UUID
    agency_name: str
    agency_slug: str
    subscription_status: str | None
    plan_code: str | None
    plan_name: str | None
    monthly_price_eur: int | None
    trial_ends_at: str | None
    trial_is_expired: bool
    usage: list[MeUsage]


@router.get("", response_model=MeOut)
async def me(
    auth: AuthContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> MeOut:
    if auth.user is None:
        # Authenticated in Clerk but not linked to any agency yet → the
        # dashboard sends them to the onboarding flow.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": "not_onboarded"}
        )

    agency = await db.get(Agency, auth.user.agency_id)
    if agency is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "agency_missing"})

    sub, plan = await get_plan_state(db, agency.id)
    usage = await get_usage(db, agency.id)

    return MeOut(
        user_id=auth.user.id,
        email=auth.user.email,
        full_name=auth.user.full_name,
        role=auth.user.role.value,
        agency_id=agency.id,
        agency_name=agency.name,
        agency_slug=agency.slug,
        subscription_status=sub.status.value if sub else None,
        plan_code=plan.code if plan else None,
        plan_name=plan.name if plan else None,
        monthly_price_eur=plan.monthly_price_eur if plan else None,
        trial_ends_at=sub.trial_ends_at.isoformat() if sub and sub.trial_ends_at else None,
        trial_is_expired=bool(
            sub
            and sub.trial_ends_at
            and sub.trial_ends_at <= datetime.now(UTC)
        ),
        usage=[MeUsage(resource=u.resource, used=u.used, limit=u.limit) for u in usage],
    )
