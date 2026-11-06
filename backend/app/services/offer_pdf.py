"""Render the formal offer PDF and store it."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agency, Lead, Offer, Property
from app.services.pdf import render_pdf
from app.services.storage import save_bytes


async def build_offer_pdf(db: AsyncSession, *, offer: Offer) -> str:
    agency = await db.get(Agency, offer.agency_id)
    lead = await db.get(Lead, offer.lead_id)
    prop = await db.get(Property, offer.property_id)
    if not (agency and lead and prop):
        raise ValueError("offer dependencies missing")

    context = {
        "agency": {"name": agency.name},
        "lead": {
            "full_name": lead.full_name,
            "email": lead.email,
            "phone_e164": lead.phone_e164,
        },
        "property": {
            "title": prop.title,
            "reference": prop.reference,
            "address": prop.address,
            "city": prop.city,
        },
        "offer": {
            "id": str(offer.id),
            "short_id": str(offer.id)[:8],
            "operation": offer.operation,
            "amount_eur_fmt": f"{offer.amount_eur:,}",
            "conditions": offer.conditions,
            "valid_until": offer.valid_until.strftime("%d/%m/%Y") if offer.valid_until else None,
            "created_at": offer.created_at.strftime("%d/%m/%Y"),
        },
    }

    pdf_bytes = render_pdf("offer.html", context)
    key = f"agencies/{offer.agency_id}/offers/{offer.id}.pdf"
    return save_bytes(key, pdf_bytes, content_type="application/pdf")
