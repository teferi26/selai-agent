"""Offer creation, listing, and PDF download.

Signing integration (Signaturit / FirmaFy) is not implemented here — the
endpoint returns the PDF URL and `sign_url` is filled in by a future
provider-specific worker.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AgencyMemberDep
from app.db.models import Lead, Offer, OfferStatus, Property
from app.db.session import get_db
from app.services.event_dispatcher import emit_offer_created, emit_offer_signed
from app.services.offer_pdf import build_offer_pdf
from app.services.plan_limits import ensure_feature

router = APIRouter(
    prefix="/api/v1/agencies/{agency_id}/offers",
    tags=["offers"],
    dependencies=[AgencyMemberDep],
)

# Sold as "Ofertas en PDF listas para firmar", Pro and up
# (seed_plans.py: only pro/agencia carry `ofertas_pdf`).
FEATURE = "ofertas_pdf"


class OfferIn(BaseModel):
    lead_id: uuid.UUID
    property_id: uuid.UUID
    amount_eur: int = Field(ge=1)
    operation: str = "sale"
    conditions: str = ""
    valid_until: datetime | None = None


class OfferOut(BaseModel):
    id: uuid.UUID
    lead_id: uuid.UUID
    property_id: uuid.UUID
    amount_eur: int
    operation: str
    status: OfferStatus
    pdf_url: str | None
    sign_url: str | None
    created_at: datetime
    sent_at: datetime | None
    signed_at: datetime | None


def _to_out(o: Offer) -> OfferOut:
    return OfferOut(
        id=o.id,
        lead_id=o.lead_id,
        property_id=o.property_id,
        amount_eur=o.amount_eur,
        operation=o.operation,
        status=o.status,
        pdf_url=o.pdf_url,
        sign_url=o.sign_url,
        created_at=o.created_at,
        sent_at=o.sent_at,
        signed_at=o.signed_at,
    )


@router.post("", response_model=OfferOut, status_code=201)
async def create_offer(
    agency_id: uuid.UUID,
    body: OfferIn,
    db: AsyncSession = Depends(get_db),
) -> OfferOut:
    # Only creating an offer is gated. Listing and the status PATCH stay open
    # so an agency that downgrades can still finish the offers it already
    # sent to its clients — locking those would break a live negotiation.
    await ensure_feature(db, agency_id, FEATURE)

    lead = await db.get(Lead, body.lead_id)
    prop = await db.get(Property, body.property_id)
    if not lead or lead.agency_id != agency_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "lead_invalid")
    if not prop or prop.agency_id != agency_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "property_invalid")

    offer = Offer(
        agency_id=agency_id,
        lead_id=body.lead_id,
        property_id=body.property_id,
        amount_eur=body.amount_eur,
        operation=body.operation,
        conditions=body.conditions,
        valid_until=body.valid_until,
        status=OfferStatus.DRAFT,
    )
    db.add(offer)
    await db.flush()

    try:
        offer.pdf_url = await build_offer_pdf(db, offer=offer)
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"pdf_failed: {exc}") from exc

    # Announced before the commit so the "already announced" marker is part of
    # the same transaction as the offer: either both land or neither does.
    await emit_offer_created(db, offer)

    await db.commit()
    return _to_out(offer)


@router.get("", response_model=list[OfferOut])
async def list_offers(agency_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[OfferOut]:
    rows = (
        await db.execute(
            select(Offer).where(Offer.agency_id == agency_id).order_by(Offer.created_at.desc())
        )
    ).scalars().all()
    return [_to_out(o) for o in rows]


class OfferStatusBody(BaseModel):
    status: OfferStatus


@router.patch("/{offer_id}/status", response_model=OfferOut)
async def update_status(
    agency_id: uuid.UUID,
    offer_id: uuid.UUID,
    body: OfferStatusBody,
    db: AsyncSession = Depends(get_db),
) -> OfferOut:
    o = await db.get(Offer, offer_id)
    if not o or o.agency_id != agency_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "offer_not_found")

    now = datetime.now(UTC)
    o.status = body.status
    if body.status == OfferStatus.SENT and o.sent_at is None:
        o.sent_at = now
    elif body.status == OfferStatus.OPENED and o.opened_at is None:
        o.opened_at = now
    elif body.status == OfferStatus.SIGNED and o.signed_at is None:
        o.signed_at = now

    # This PATCH is the only place an offer becomes signed today (the signing
    # provider is not wired yet and will call it too). Re-sending the same
    # status is a no-op for the webhook: `emit_offer_signed` is idempotent.
    if body.status == OfferStatus.SIGNED:
        await emit_offer_signed(db, o)

    await db.commit()
    return _to_out(o)
