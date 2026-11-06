"""Pick the best human agent to assign to a visit.

Strategy (in order):
1. If the agent has both `home_lat` and `home_lng` AND the property has
   coordinates, sort by haversine distance and pick the closest active agent.
2. Otherwise, fall back to the first active agent (deterministic by role).

Future: factor in current load (open visits today) and language match
(lead.language vs user.spoken_languages once added).
"""
from __future__ import annotations

import math
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Property, User


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometers."""
    r = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


async def pick_agent_for_visit(
    db: AsyncSession,
    *,
    agency_id: uuid.UUID,
    property_: Property,
) -> User | None:
    stmt = (
        select(User)
        .where(User.agency_id == agency_id, User.is_active.is_(True))
        .order_by(User.role.asc())
    )
    candidates = list((await db.execute(stmt)).scalars().all())
    if not candidates:
        return None

    if property_.latitude is None or property_.longitude is None:
        return candidates[0]

    p_lat = float(property_.latitude)
    p_lng = float(property_.longitude)

    geo_candidates = [c for c in candidates if c.home_lat is not None and c.home_lng is not None]
    if not geo_candidates:
        return candidates[0]

    geo_candidates.sort(key=lambda u: haversine_km(p_lat, p_lng, float(u.home_lat), float(u.home_lng)))
    return geo_candidates[0]
