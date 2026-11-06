"""Oficinas — CRUD of an agency's branches, plus who works in each one.

What the site sells (dashboard/src/components/marketing/Pricing.tsx, plan
Agencia): "Cada contacto va solo a la oficina que le corresponde" and
"Comparte contactos entre tus oficinas". This module is the half that lets
an owner *declare* the offices and hand out the assignments; the half that
uses them is the optional `?office=` filter on the panel listings
(api/dashboard.py).

Three rules govern everything here:

* **Agencia plan only.** `ensure_feature(..., "multi_oficina")` gates the two
  calls that put the feature to work — creating an office and assigning
  someone to one. Renaming and deleting are *not* gated: an agency that
  downgrades has to be able to tidy up, and a gate that traps rows would be
  a worse bug than a free rename.
* **Owner only.** Everything that writes carries `OwnerOnlyDep`. Reading is
  open to any member, because the panel needs the office names to paint the
  filter and an agent seeing "Oficina Centro" exists leaks nothing.
* **An office is a label, not a tenant.** Deleting one detaches its users and
  its listings; it never deletes a lead, a listing or a conversation.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, StringConstraints
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AgencyMemberDep, OwnerOnlyDep
from app.core.logging import get_logger
from app.db.models import Office, Property, User, UserRole
from app.db.session import get_db
from app.services.plan_limits import ensure_feature

log = get_logger(__name__)

# The plan feature flag this whole module hangs off. Named once so the seed
# script, the tests and the error body can never drift apart.
MULTI_OFFICE_FEATURE = "multi_oficina"

router = APIRouter(
    prefix="/api/v1/agencies/{agency_id}/offices",
    tags=["offices"],
    dependencies=[AgencyMemberDep],
)

OfficeName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
]
OfficeAddress = Annotated[str, StringConstraints(strip_whitespace=True, max_length=512)]


class OfficeOut(BaseModel):
    id: uuid.UUID
    name: str
    address: str | None
    created_at: datetime
    # Rendered by the settings page so "borrar" can say what it detaches.
    user_count: int
    property_count: int


class OfficeCreate(BaseModel):
    name: OfficeName
    address: OfficeAddress | None = None


class OfficeUpdate(BaseModel):
    """Partial: only the fields actually sent are touched."""

    name: OfficeName | None = None
    address: OfficeAddress | None = None


class OfficeMemberOut(BaseModel):
    user_id: uuid.UUID
    full_name: str
    email: str
    role: UserRole
    office_id: uuid.UUID | None


class OfficeAssignment(BaseModel):
    """`null` puts the row back to "toda la agencia"."""

    office_id: uuid.UUID | None = None


class OfficePropertyOut(BaseModel):
    """A listing and the branch that manages it.

    `PropertyOut` (api/properties.py, another module) does not carry
    `office_id`, so without this read side the assignment could be written and
    never shown back — and the half of the promise that actually works today
    (a lead belongs to the office of the flat it visited) would have no screen
    at all.
    """

    property_id: uuid.UUID
    reference: str
    title: str
    city: str
    office_id: uuid.UUID | None


async def _owned_office(db: AsyncSession, agency_id: uuid.UUID, office_id: uuid.UUID) -> Office:
    """The office, or 404 — never another tenant's row.

    404 rather than 403 on purpose: answering "forbidden" would confirm that
    the id exists somewhere else.
    """
    office = (
        await db.execute(
            select(Office).where(Office.id == office_id, Office.agency_id == agency_id)
        )
    ).scalar_one_or_none()
    if office is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "office_not_found")
    return office


async def _reject_duplicate_name(
    db: AsyncSession,
    agency_id: uuid.UUID,
    name: str,
    *,
    excluding: uuid.UUID | None = None,
) -> None:
    """409 when the agency already has an office by that name.

    Case-insensitive: "Centro" and "centro" are the same branch to a human,
    and two identically-named rows make the office filter unusable.
    """
    stmt = select(Office.id).where(
        Office.agency_id == agency_id, func.lower(Office.name) == name.lower()
    )
    if excluding is not None:
        stmt = stmt.where(Office.id != excluding)
    if (await db.execute(stmt)).scalar_one_or_none() is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "office_name_taken", "name": name},
        )


def _counts_subqueries():
    """How many people and listings hang off each office.

    Both counts name `agency_id` as well as `office_id`. Matching on the
    office alone would be correct only while no row is ever mis-stamped, and
    these two numbers are printed inside the "¿Cerrar la oficina?" prompt as
    a promise of what is about to be detached — a number borrowed from
    another tenant would be both wrong and a disclosure.
    """
    return (
        select(func.count())
        .select_from(User)
        .where(User.office_id == Office.id, User.agency_id == Office.agency_id)
        .scalar_subquery(),
        select(func.count())
        .select_from(Property)
        .where(Property.office_id == Office.id, Property.agency_id == Office.agency_id)
        .scalar_subquery(),
    )


@router.get("", response_model=list[OfficeOut])
async def list_offices(
    agency_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> list[OfficeOut]:
    """Every office of the agency, oldest first. Empty list for almost everyone.

    Not gated by plan: the panel calls this on every render of the listings to
    decide whether to paint the office filter at all, and a 403 there would
    turn "no tienes oficinas" into an error screen for Starter and Pro.
    """
    users_count, props_count = _counts_subqueries()
    rows = (
        await db.execute(
            select(Office, users_count, props_count)
            .where(Office.agency_id == agency_id)
            .order_by(Office.created_at.asc(), Office.id.asc())
        )
    ).all()
    return [
        OfficeOut(
            id=office.id,
            name=office.name,
            address=office.address,
            created_at=office.created_at,
            user_count=int(user_count or 0),
            property_count=int(property_count or 0),
        )
        for office, user_count, property_count in rows
    ]


@router.post(
    "",
    response_model=OfficeOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[OwnerOnlyDep],
)
async def create_office(
    agency_id: uuid.UUID, body: OfficeCreate, db: AsyncSession = Depends(get_db)
) -> OfficeOut:
    await ensure_feature(db, agency_id, MULTI_OFFICE_FEATURE)
    await _reject_duplicate_name(db, agency_id, body.name)

    office = Office(agency_id=agency_id, name=body.name, address=body.address or None)
    db.add(office)
    await db.commit()
    await db.refresh(office)
    log.info("offices.created", agency=str(agency_id), office=str(office.id))
    return OfficeOut(
        id=office.id,
        name=office.name,
        address=office.address,
        created_at=office.created_at,
        user_count=0,
        property_count=0,
    )


# ---------------------------------------------------------------------------
# Assignments. Declared before `/{office_id}` so the literal segments can
# never be read as an office id.
# ---------------------------------------------------------------------------


@router.get("/members", response_model=list[OfficeMemberOut])
async def list_members(
    agency_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> list[OfficeMemberOut]:
    """Everyone in the agency and the office they are assigned to (or none)."""
    users = (
        await db.execute(
            select(User).where(User.agency_id == agency_id).order_by(User.full_name.asc())
        )
    ).scalars().all()
    return [
        OfficeMemberOut(
            user_id=user.id,
            full_name=user.full_name,
            email=user.email,
            role=user.role,
            office_id=user.office_id,
        )
        for user in users
    ]


@router.put("/members/{user_id}", response_model=OfficeMemberOut, dependencies=[OwnerOnlyDep])
async def assign_member(
    agency_id: uuid.UUID,
    user_id: uuid.UUID,
    body: OfficeAssignment,
    db: AsyncSession = Depends(get_db),
) -> OfficeMemberOut:
    """Move a colleague to an office, or back to "toda la agencia" with null."""
    await ensure_feature(db, agency_id, MULTI_OFFICE_FEATURE)

    user = (
        await db.execute(select(User).where(User.id == user_id, User.agency_id == agency_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user_not_found")
    if body.office_id is not None:
        await _owned_office(db, agency_id, body.office_id)

    user.office_id = body.office_id
    await db.commit()
    return OfficeMemberOut(
        user_id=user.id,
        full_name=user.full_name,
        email=user.email,
        role=user.role,
        office_id=user.office_id,
    )


@router.get("/properties", response_model=list[OfficePropertyOut])
async def list_properties_by_office(
    agency_id: uuid.UUID,
    q: str | None = Query(None, max_length=120, description="Busca por referencia, título o ciudad."),
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[OfficePropertyOut]:
    """The catalogue with its office assignments, for the settings screen.

    Capped and searchable rather than complete: the Agencia plan allows 2.000
    listings and a page that renders one dropdown per listing would be
    unusable long before that. Not plan-gated, for the same reason `GET
    /offices` is not — the screen has to be able to explain itself to a
    Starter agency instead of erroring.
    """
    stmt = select(Property).where(Property.agency_id == agency_id)
    if q:
        needle = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Property.reference.ilike(needle),
                Property.title.ilike(needle),
                Property.city.ilike(needle),
            )
        )
    rows = (
        await db.execute(stmt.order_by(Property.reference.asc()).limit(limit))
    ).scalars().all()
    return [
        OfficePropertyOut(
            property_id=listing.id,
            reference=listing.reference,
            title=listing.title,
            city=listing.city,
            office_id=listing.office_id,
        )
        for listing in rows
    ]


@router.put("/properties/{property_id}", dependencies=[OwnerOnlyDep])
async def assign_property(
    agency_id: uuid.UUID,
    property_id: uuid.UUID,
    body: OfficeAssignment,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str | None]:
    """Which branch manages a listing.

    This is the load-bearing half of "cada contacto va a la oficina que le
    corresponde": a lead that visits a listing belongs to that listing's
    office (see `lead_in_office` in api/dashboard.py). Lives here rather than
    in api/properties.py so the whole feature is one module; the catálogo
    screen can call it as-is.
    """
    await ensure_feature(db, agency_id, MULTI_OFFICE_FEATURE)

    listing = (
        await db.execute(
            select(Property).where(Property.id == property_id, Property.agency_id == agency_id)
        )
    ).scalar_one_or_none()
    if listing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "property_not_found")
    if body.office_id is not None:
        await _owned_office(db, agency_id, body.office_id)

    listing.office_id = body.office_id
    await db.commit()
    return {
        "property_id": str(listing.id),
        "office_id": str(listing.office_id) if listing.office_id else None,
    }


# ---------------------------------------------------------------------------
# Rename / close. Not plan-gated: an agency that drops to Pro must still be
# able to unwind what it created while it was on Agencia.
# ---------------------------------------------------------------------------


@router.patch("/{office_id}", response_model=OfficeOut, dependencies=[OwnerOnlyDep])
async def update_office(
    agency_id: uuid.UUID,
    office_id: uuid.UUID,
    body: OfficeUpdate,
    db: AsyncSession = Depends(get_db),
) -> OfficeOut:
    office = await _owned_office(db, agency_id, office_id)
    if body.name is not None:
        await _reject_duplicate_name(db, agency_id, body.name, excluding=office_id)
        office.name = body.name
    if body.address is not None:
        # "" is how the form clears the field; store NULL, not an empty string.
        office.address = body.address or None
    await db.commit()
    await db.refresh(office)

    users_count, props_count = _counts_subqueries()
    counts = (
        await db.execute(select(users_count, props_count).where(Office.id == office_id))
    ).first()
    return OfficeOut(
        id=office.id,
        name=office.name,
        address=office.address,
        created_at=office.created_at,
        user_count=int((counts[0] if counts else 0) or 0),
        property_count=int((counts[1] if counts else 0) or 0),
    )


@router.delete("/{office_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[OwnerOnlyDep])
async def delete_office(
    agency_id: uuid.UUID, office_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> None:
    """Close an office. Its people and its listings go back to "toda la agencia".

    The detach is written explicitly instead of leaning on the FK's
    `ON DELETE SET NULL`: the database would do it either way, but doing it
    here means the objects already loaded in this session agree with the
    rows, and the behaviour does not depend on which backend is underneath.
    """
    office = await _owned_office(db, agency_id, office_id)

    await db.execute(
        update(User)
        .where(User.agency_id == agency_id, User.office_id == office_id)
        .values(office_id=None)
    )
    await db.execute(
        update(Property)
        .where(Property.agency_id == agency_id, Property.office_id == office_id)
        .values(office_id=None)
    )
    await db.delete(office)
    await db.commit()
    log.info("offices.deleted", agency=str(agency_id), office=str(office_id))
