"""Generate "why this property suits you" bullets for a (lead, property)
pair, using Claude. Output is cached on the lead's `extra` so we don't
spend tokens regenerating the same pair.
"""
from __future__ import annotations

from typing import Any

from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.settings import settings
from app.db.models import Lead, Property

log = get_logger(__name__)

_client: AsyncAnthropic | None = None


def _client_singleton() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
    return _client


async def reasons_for_match(db: AsyncSession, *, lead: Lead, property_: Property) -> list[str]:
    """Return up to 3 short bullets in the lead's language."""
    cache: dict[str, Any] = (lead.extra or {}).get("match_reasons", {})
    key = str(property_.id)
    if cache.get(key):
        return cache[key][:3]

    prompt = f"""\
Lead: idioma={lead.language}, presupuesto={lead.budget_min or '?'}-{lead.budget_max or '?'} EUR, \
operación={lead.operation or '?'}, mín hab={lead.min_bedrooms or '?'}, \
imprescindibles={', '.join(lead.must_haves or []) or 'ninguno'}, \
descartes={', '.join(lead.deal_breakers or []) or 'ninguno'}, \
zonas={', '.join(lead.preferred_neighborhoods or lead.preferred_cities or []) or 'cualquiera'}.

Propiedad: {property_.title}, {property_.bedrooms or '?'} hab, {property_.bathrooms or '?'} baños, \
{property_.area_sqm or '?'} m², {property_.price} EUR, {property_.neighborhood or property_.city}, \
operación {property_.operation}.
Características: ascensor={property_.has_elevator}, parking={property_.has_parking}, \
terraza={property_.has_terrace}, piscina={property_.has_pool}, AC={property_.has_ac}, \
amueblado={property_.furnished}, mascotas={property_.pet_friendly}.

Devuelve EXACTAMENTE 3 bullets cortos (máx 90 caracteres cada uno) en idioma {lead.language}, \
explicando por qué este piso encaja con lo que el lead busca. Cada bullet debe referirse a un dato \
concreto del lead que coincide con un dato concreto del piso. NO inventes datos. Si no hay 3 \
encajes claros, da menos. Sin numeración, solo guiones."""

    try:
        client = _client_singleton()
        resp = await client.messages.create(
            model=settings.agent_model_fast,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "\n".join(b.text for b in resp.content if b.type == "text")
    except Exception:
        log.exception("match_reasons.failed", lead=str(lead.id), prop=str(property_.id))
        return []

    bullets = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Strip leading markers
        for prefix in ("- ", "* ", "• ", "– "):
            if line.startswith(prefix):
                line = line[len(prefix):]
                break
        if line and len(line) < 200:
            bullets.append(line)
        if len(bullets) >= 3:
            break

    # Cache
    cache[key] = bullets
    lead.extra = {**(lead.extra or {}), "match_reasons": cache}
    return bullets
