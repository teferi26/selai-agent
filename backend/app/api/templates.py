"""Message template CRUD + rendering."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AgencyMemberDep
from app.db.models import Lead, MessageTemplate, Property
from app.db.session import get_db
from app.services.plan_limits import ensure_feature
from app.services.templating import render_template

router = APIRouter(
    prefix="/api/v1/agencies/{agency_id}/templates",
    tags=["templates"],
    dependencies=[AgencyMemberDep],
)

# Sold as "Plantillas de mensajes a tu medida", Pro and up
# (seed_plans.py: only pro/agencia carry `plantillas`).
FEATURE = "plantillas"


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1)
    channel: str = "whatsapp"
    language: str = "es"
    description: str = ""


class TemplateOut(TemplateIn):
    id: uuid.UUID


# Reading and deleting stay open on purpose: an agency that downgrades must
# still see the templates it wrote and be able to clean them up. What the
# plan gates is *making* and *using* them.
@router.get("", response_model=list[TemplateOut])
async def list_templates(agency_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(MessageTemplate)
            .where(MessageTemplate.agency_id == agency_id)
            .order_by(MessageTemplate.name.asc())
        )
    ).scalars().all()
    return [TemplateOut(id=r.id, name=r.name, body=r.body, channel=r.channel,
                        language=r.language, description=r.description) for r in rows]


@router.post("", response_model=TemplateOut, status_code=201)
async def create_template(agency_id: uuid.UUID, body: TemplateIn, db: AsyncSession = Depends(get_db)):
    await ensure_feature(db, agency_id, FEATURE)
    t = MessageTemplate(
        agency_id=agency_id,
        name=body.name,
        body=body.body,
        channel=body.channel,
        language=body.language,
        description=body.description,
    )
    db.add(t)
    await db.commit()
    return TemplateOut(id=t.id, **body.model_dump())


@router.delete("/{template_id}", status_code=204)
async def delete_template(agency_id: uuid.UUID, template_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    t = await db.get(MessageTemplate, template_id)
    if t is None or t.agency_id != agency_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template_not_found")
    await db.delete(t)
    await db.commit()


class RenderBody(BaseModel):
    lead_id: uuid.UUID | None = None
    property_id: uuid.UUID | None = None


@router.post("/{template_id}/render")
async def render(
    agency_id: uuid.UUID,
    template_id: uuid.UUID,
    body: RenderBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    await ensure_feature(db, agency_id, FEATURE)

    t = await db.get(MessageTemplate, template_id)
    if t is None or t.agency_id != agency_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template_not_found")

    lead = await db.get(Lead, body.lead_id) if body.lead_id else None
    prop = await db.get(Property, body.property_id) if body.property_id else None
    if lead and lead.agency_id != agency_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "lead_not_in_agency")
    if prop and prop.agency_id != agency_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "property_not_in_agency")

    rendered = render_template(t.body, lead=lead, property_=prop)
    return {"rendered": rendered}
