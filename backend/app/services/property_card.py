"""Generate a personalized property card PDF and store it."""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import Agency, Lead, Property
from app.services.calculators import mortgage_quote
from app.services.match_reasoning import reasons_for_match
from app.services.pdf import render_pdf
from app.services.storage import save_bytes

log = get_logger(__name__)


async def build_property_card(
    db: AsyncSession,
    *,
    lead: Lead,
    property_: Property,
) -> str:
    """Render the PDF, persist it, return its public URL."""
    agency = await db.get(Agency, property_.agency_id)
    if agency is None:
        raise ValueError("agency missing")

    reasons = await reasons_for_match(db, lead=lead, property_=property_)

    mortgage = None
    if property_.operation == "sale" and property_.price > 0:
        try:
            q = mortgage_quote(price_eur=property_.price)
            mortgage = {
                "down_payment_pct": 20,
                "years": q.years,
                "annual_rate_pct": q.annual_rate_pct,
                "monthly_payment_eur": f"{q.monthly_payment_eur:,.0f}",
            }
        except Exception:
            pass

    context = {
        "lang": lead.language or "es",
        "agency": {"name": agency.name},
        "lead": {"first_name": (lead.full_name or "").split(" ", 1)[0]},
        "property": {
            "title": property_.title,
            "reference": property_.reference,
            "operation": property_.operation,
            "price_eur": f"{property_.price:,}",
            "address": property_.address,
            "neighborhood": property_.neighborhood,
            "city": property_.city,
            "bedrooms": property_.bedrooms,
            "bathrooms": property_.bathrooms,
            "area_sqm": property_.area_sqm,
            "floor": property_.floor,
            "energy_rating": property_.energy_rating,
            "image_url": (property_.image_urls or [None])[0],
            "has_elevator": property_.has_elevator,
            "has_parking": property_.has_parking,
            "has_terrace": property_.has_terrace,
            "has_pool": property_.has_pool,
            "has_ac": property_.has_ac,
            "has_heating": property_.has_heating,
            "furnished": property_.furnished,
            "pet_friendly": property_.pet_friendly,
            "description": property_.description,
        },
        "reasons": reasons,
        "mortgage": mortgage,
    }

    pdf_bytes = render_pdf("property_card.html", context)
    key = f"agencies/{property_.agency_id}/cards/{lead.id}_{property_.id}.pdf"
    url = save_bytes(key, pdf_bytes, content_type="application/pdf")
    return url


# Re-export for convenience
_ = uuid
