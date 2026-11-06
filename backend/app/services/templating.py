"""Render message templates with placeholders.

Placeholders look like {{lead.first_name}} or {{property.title}}. We avoid
Jinja to keep render predictable and side-effect-free.
"""
from __future__ import annotations

import re
from typing import Any

from app.db.models import Lead, Property

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_]+\.[a-zA-Z_]+)\s*\}\}")


def render_template(body: str, *, lead: Lead | None = None, property_: Property | None = None) -> str:
    context = _build_context(lead=lead, property_=property_)

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        value = context.get(key)
        return "" if value is None else str(value)

    return _PLACEHOLDER.sub(repl, body)


def _build_context(*, lead: Lead | None, property_: Property | None) -> dict[str, Any]:
    ctx: dict[str, Any] = {}
    if lead:
        ctx["lead.first_name"] = (lead.full_name or "").split(" ", 1)[0]
        ctx["lead.full_name"] = lead.full_name
        ctx["lead.email"] = lead.email
        ctx["lead.phone"] = lead.phone_e164
        ctx["lead.budget_max"] = lead.budget_max
        ctx["lead.budget_min"] = lead.budget_min
        ctx["lead.language"] = lead.language
    if property_:
        ctx["property.title"] = property_.title
        ctx["property.reference"] = property_.reference
        ctx["property.price"] = f"€{property_.price:,}"
        ctx["property.bedrooms"] = property_.bedrooms
        ctx["property.bathrooms"] = property_.bathrooms
        ctx["property.area_sqm"] = property_.area_sqm
        ctx["property.address"] = property_.address
        ctx["property.neighborhood"] = property_.neighborhood
        ctx["property.city"] = property_.city
    return ctx
