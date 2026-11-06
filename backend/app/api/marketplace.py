"""Inter-agency lead marketplace.

Seller agency publishes a sanitized preview. Other agencies browse, then
"claim" → we copy the lead into their tenant + record buyer/commission.

Compliance: publishing a lead requires evidence of consent for inter-agency
data sharing. We rely on the seller having captured an explicit consent
through `/gdpr/.../consent` for the scope `data_processing` BEFORE listing.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, AuthDep, ensure_agency_access, require_auth
from app.db.models import (
    ConsentAction,
    ConsentLog,
    ConsentScope,
    Lead,
    LeadOffer,
    LeadOfferStatus,
)
from app.db.session import get_db
from app.services.plan_limits import ensure_feature

router = APIRouter(prefix="/api/v1/marketplace", tags=["marketplace"], dependencies=[AuthDep])

# Agencia-only on the pricing page (seed_plans.py: only `agencia` carries
# `marketplace`). Gated on every endpoint, browsing included: the listings
# are other agencies' leads, so seeing them is already the product.
FEATURE = "marketplace"


def _build_preview(lead: Lead) -> dict:
    """PII-free preview: enough info for buyer agencies to decide."""
    return {
        "language": lead.language,
        "operation": lead.operation,
        "budget_min": lead.budget_min,
        "budget_max": lead.budget_max,
        "min_bedrooms": lead.min_bedrooms,
        "preferred_cities": lead.preferred_cities,
        "preferred_neighborhoods": lead.preferred_neighborhoods,
        "must_haves": lead.must_haves,
        "temperature": lead.temperature.value,
        "score": lead.score,
        "source_channel": lead.source_channel,
    }


# ---------------------------------------------------------------------------
# Sell side
# ---------------------------------------------------------------------------

class PublishBody(BaseModel):
    lead_id: uuid.UUID
    asking_fee_eur: int = Field(ge=0, le=10_000)
    commission_pct: int = Field(default=10, ge=0, le=50)
    notes: str = ""
    days_open: int = Field(default=14, ge=1, le=60)


class OfferOut(BaseModel):
    id: uuid.UUID
    seller_agency_id: uuid.UUID
    asking_fee_eur: int
    commission_pct: int
    expires_at: datetime
    status: LeadOfferStatus
    preview: dict


def _to_out(o: LeadOffer) -> OfferOut:
    return OfferOut(
        id=o.id,
        seller_agency_id=o.seller_agency_id,
        asking_fee_eur=o.asking_fee_eur,
        commission_pct=o.commission_pct,
        expires_at=o.expires_at,
        status=o.status,
        preview=o.preview,
    )


@router.post("/publish", response_model=OfferOut, status_code=201)
async def publish(
    body: PublishBody,
    auth: AuthContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> OfferOut:
    lead = await db.get(Lead, body.lead_id)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lead_not_found")
    try:
        ensure_agency_access(auth, lead.agency_id)
    except HTTPException:
        # 404, not 403: don't reveal that another tenant's lead id exists.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lead_not_found") from None

    # Checked after the ownership test on purpose: the plan of the agency
    # that owns the lead is the one selling it.
    await ensure_feature(db, lead.agency_id, FEATURE)

    consent = (
        await db.execute(
            select(ConsentLog).where(
                ConsentLog.lead_id == lead.id,
                # Solo consentimientos registrados por la agencia dueña del
                # lead: una fila cruzada de otro tenant no autoriza publicar
                # los datos de esta persona en el marketplace.
                ConsentLog.agency_id == lead.agency_id,
                ConsentLog.scope == ConsentScope.DATA_PROCESSING,
                ConsentLog.action == ConsentAction.GRANTED,
            )
        )
    ).scalars().first()
    if consent is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "consent_for_data_sharing_required",
        )

    offer = LeadOffer(
        seller_agency_id=lead.agency_id,
        lead_id=lead.id,
        preview=_build_preview(lead),
        asking_fee_eur=body.asking_fee_eur,
        commission_pct=body.commission_pct,
        notes=body.notes,
        expires_at=datetime.now(UTC) + timedelta(days=body.days_open),
        redemption_token=secrets.token_urlsafe(24),
    )
    db.add(offer)
    await db.commit()
    return _to_out(offer)


# ---------------------------------------------------------------------------
# Buy side
# ---------------------------------------------------------------------------

@router.get("/offers", response_model=list[OfferOut])
async def list_open_offers(
    auth: AuthContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> list[OfferOut]:
    # The listing shows real leads (zone, budget, contact hints) from paying
    # agencies. A valid Clerk token with NO users row — someone who signed up
    # and never onboarded, or was removed from a team — used to slip through
    # the plan gate because there was no agency to check the plan of. Free
    # browsing of the marketplace for whoever creates a bare account.
    #
    # Now: no agency, no listing (except the dev bypass, which has no user
    # row by construction). And with an agency, the plan gate always runs.
    if auth.agency_id is None:
        if not auth.dev_bypass:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail={"code": "not_onboarded"},
            )
    else:
        await ensure_feature(db, auth.agency_id, FEATURE)

    rows = (
        await db.execute(
            select(LeadOffer)
            .where(
                LeadOffer.status == LeadOfferStatus.OPEN,
                LeadOffer.expires_at > datetime.now(UTC),
            )
            .order_by(LeadOffer.created_at.desc())
            .limit(200)
        )
    ).scalars().all()
    return [_to_out(o) for o in rows]


class ClaimBody(BaseModel):
    buyer_agency_id: uuid.UUID


@router.post("/offers/{offer_id}/claim", response_model=OfferOut)
async def claim(
    offer_id: uuid.UUID,
    body: ClaimBody,
    auth: AuthContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> OfferOut:
    ensure_agency_access(auth, body.buyer_agency_id)
    # The buyer's plan pays for this: claiming copies a lead into their tenant.
    await ensure_feature(db, body.buyer_agency_id, FEATURE)

    offer = await db.get(LeadOffer, offer_id)
    if offer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "offer_not_found")
    if offer.status != LeadOfferStatus.OPEN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "offer_not_claimable")
    if offer.expires_at <= datetime.now(UTC):
        offer.status = LeadOfferStatus.EXPIRED
        await db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "offer_expired")
    if offer.seller_agency_id == body.buyer_agency_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot_claim_own_offer")

    seller_lead = await db.get(Lead, offer.lead_id)
    if seller_lead is None:
        raise HTTPException(status.HTTP_410_GONE, "lead_no_longer_exists")

    transferred = Lead(
        agency_id=body.buyer_agency_id,
        full_name=seller_lead.full_name,
        phone_e164=seller_lead.phone_e164,
        email=seller_lead.email,
        whatsapp_wa_id=seller_lead.whatsapp_wa_id,
        budget_min=seller_lead.budget_min,
        budget_max=seller_lead.budget_max,
        operation=seller_lead.operation,
        preferred_cities=seller_lead.preferred_cities,
        preferred_neighborhoods=seller_lead.preferred_neighborhoods,
        min_bedrooms=seller_lead.min_bedrooms,
        must_haves=seller_lead.must_haves,
        deal_breakers=seller_lead.deal_breakers,
        qualification_notes=(
            f"[Adquirido del marketplace] origen offer={offer.id}\n\n"
            + (seller_lead.qualification_notes or "")
        ),
        language=seller_lead.language,
        source_channel="marketplace",
        source_campaign=f"offer:{offer.id}",
    )
    db.add(transferred)
    await db.flush()

    offer.status = LeadOfferStatus.CLAIMED
    offer.buyer_agency_id = body.buyer_agency_id
    offer.claimed_at = datetime.now(UTC)
    offer.transferred_lead_id = transferred.id

    # Mark the seller lead as won-by-other so it stops appearing in their pipeline.
    from app.db.models import LeadStatus
    seller_lead.status = LeadStatus.LOST
    seller_lead.qualification_notes = (
        seller_lead.qualification_notes or ""
    ) + f"\n[Cedido en marketplace a {body.buyer_agency_id} por offer {offer.id}]"

    await db.commit()
    return _to_out(offer)
