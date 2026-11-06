"""Property endpoints — listing + vision description.

Listing CRUD is intentionally minimal here; the dashboard's main
interaction is reading the catalog and asking the AI to (re)write the
description from photos.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, StringConstraints, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    AgencyMemberDep,
    AuthContext,
    OwnerOnlyDep,
    require_agency_member,
)
from app.core.logging import get_logger
from app.db.models import Property, PropertyStatus
from app.db.session import get_db
from app.services.plan_limits import ensure_subscription_usable, usage_for
from app.services.property_import import apply_row_to_property, parse_properties_csv
from app.services.property_vision import describe_property_from_photos

router = APIRouter(
    prefix="/api/v1/agencies/{agency_id}/properties",
    tags=["properties"],
    dependencies=[AgencyMemberDep],
)
log = get_logger(__name__)

DESCRIPTION_DRAFT_KEY = "description_ai_draft"
DESCRIPTION_HISTORY_KEY = "description_history"
DESCRIPTION_HISTORY_LIMIT = 3
MAX_PROPERTY_DESCRIPTION_LENGTH = 12_000

DescriptionText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=MAX_PROPERTY_DESCRIPTION_LENGTH),
]
TargetLanguage = Annotated[
    str,
    StringConstraints(
        min_length=2,
        max_length=16,
        pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z]{2,8})?$",
    ),
]


def _description_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _actor_id(auth: AuthContext) -> str:
    user_id = getattr(auth.user, "id", None)
    return str(user_id) if user_id is not None else auth.clerk_user_id


def _active_description_draft(prop: Property) -> dict[str, Any] | None:
    raw = (getattr(prop, "extra", None) or {}).get(DESCRIPTION_DRAFT_KEY)
    if not isinstance(raw, dict):
        return None
    required = ("id", "description", "generated_description", "created_at", "updated_at")
    if not all(isinstance(raw.get(key), str) for key in required):
        return None
    return dict(raw)


def _description_history(prop: Property) -> list[dict[str, Any]]:
    raw = (getattr(prop, "extra", None) or {}).get(DESCRIPTION_HISTORY_KEY)
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _non_blank_description(value: str) -> str:
    if not value.strip():
        raise ValueError("description_cannot_be_blank")
    return value


async def _locked_property(
    db: AsyncSession, agency_id: uuid.UUID, property_id: uuid.UUID
) -> Property:
    """Lock one tenant-scoped row while applying/restoring a description.

    The hash checks below prevent stale writes, but without the row lock two
    simultaneous approvals could both pass the check before either commits.
    """

    prop = (
        await db.execute(
            select(Property)
            .where(Property.id == property_id, Property.agency_id == agency_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if prop is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "property_not_found")
    return prop


class DescriptionDraftOut(BaseModel):
    id: uuid.UUID
    description: str
    generated_description: str
    highlights: list[str]
    weaknesses: list[str]
    base_description: str
    created_at: datetime
    updated_at: datetime


def _draft_out(raw: dict[str, Any]) -> DescriptionDraftOut:
    try:
        return DescriptionDraftOut.model_validate(raw)
    except Exception as exc:
        # Corrupt importer/custom-field data must not leak as a 500 or be
        # mistaken for a valid approval draft.
        raise HTTPException(
            status.HTTP_409_CONFLICT, "invalid_description_draft"
        ) from exc


class DescriptionHistoryItem(BaseModel):
    id: uuid.UUID
    description: str
    replaced_at: datetime
    restored_at: datetime | None = None
    can_restore: bool


class PropertyOut(BaseModel):
    id: uuid.UUID
    reference: str
    title: str
    city: str
    neighborhood: str | None
    price: int
    bedrooms: int | None
    area_sqm: int | None
    status: str
    description: str
    image_urls: list[str]
    created_at: datetime
    has_description_draft: bool = False
    has_description_history: bool = False


@router.get("", response_model=list[PropertyOut])
async def list_properties(
    agency_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[PropertyOut]:
    rows = (
        await db.execute(
            select(Property)
            .where(Property.agency_id == agency_id)
            .order_by(Property.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        PropertyOut(
            id=p.id,
            reference=p.reference,
            title=p.title,
            city=p.city,
            neighborhood=p.neighborhood,
            price=p.price,
            bedrooms=p.bedrooms,
            area_sqm=p.area_sqm,
            status=p.status.value,
            description=p.description,
            image_urls=p.image_urls or [],
            has_description_draft=_active_description_draft(p) is not None,
            has_description_history=bool(_description_history(p)),
            created_at=p.created_at,
        )
        for p in rows
    ]


class PropertyStatusBody(BaseModel):
    """Body of the status PATCH. `status` is validated against the real
    `PropertyStatus` enum, so an unknown value 422s before touching the DB."""

    status: PropertyStatus


class PropertyStatusOut(BaseModel):
    ok: bool
    status: str


@router.patch("/{property_id}", response_model=PropertyStatusOut)
async def update_property_status(
    agency_id: uuid.UUID,
    property_id: uuid.UUID,
    body: PropertyStatusBody,
    db: AsyncSession = Depends(get_db),
) -> PropertyStatusOut:
    """Mark a listing sold/rented/available/... from the dashboard.

    The agent's `search_properties` tool only ever offers
    `PropertyStatus.AVAILABLE` listings, so flipping to SOLD/RENTED here
    immediately removes the property from what the AI can propose.
    """
    prop = await db.get(Property, property_id)
    if prop is None or prop.agency_id != agency_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "property_not_found")

    prop.status = body.status
    await db.commit()
    log.info(
        "property.status_updated",
        agency=str(agency_id),
        prop=str(property_id),
        status=body.status.value,
    )
    return PropertyStatusOut(ok=True, status=body.status.value)


class ImportResult(BaseModel):
    imported: int  # created + updated — kept for older clients
    created: int
    updated: int
    skipped: int  # rows the parser could not read (bad price, no reference…)
    errors: list[str]
    # Listings the file carried that were left out because the plan's
    # properties limit was reached. Distinct from `skipped`, which is about
    # malformed rows: these are perfectly good listings, and the panel has to
    # say so ("se han importado 40 de 60; tu plan permite 100 propiedades")
    # instead of showing a silent partial import.
    limit_skipped: int = 0
    # Machine-readable reason, so the panel can offer the upgrade link.
    # Same code the 402 from `plan_limits` carries.
    limit_reason: str | None = None
    properties_limit: int | None = None


@router.post("/import", response_model=ImportResult)
async def import_properties(
    agency_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> ImportResult:
    """Bulk import from a CSV export (`,` or `;`, UTF-8/BOM/latin-1).

    Upserts by `reference`: re-uploading the monthly export refreshes the
    listings the agency already has instead of duplicating them (which
    would make the AI offer the same flat twice). Rows whose reference is
    new are created.

    Parsing/column-mapping and the field-by-field refresh live in
    `services.property_import` (pure, unit-tested); this endpoint resolves
    the existing references and persists.

    Plan gate: the properties limit is counted **row by row**. The old check
    ran once, before parsing, and asked only "are you already at the cap?" —
    so an agency at 99/100 could upload a 5.000-row file and import all of
    it. Now each new reference consumes one slot, the import stops creating
    when the slots run out, and the result says how many listings were left
    out and why. Rows that update a reference the agency already has never
    consume a slot (the docstring promised that; only now is it true), which
    is also why the cap is no longer a 402 up front: an export made of
    updates has to keep working at 100/100.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty_file")

    # A canceled/unpaid/paused account imports nothing at all.
    await ensure_subscription_usable(db, agency_id)

    # None = not metered (no plan row: dev and seeded data) or unlimited.
    properties_usage = await usage_for(db, agency_id, "properties")
    free_slots = properties_usage.remaining if properties_usage is not None else None

    parsed = parse_properties_csv(raw)

    references = [row["reference"] for row in parsed.rows]
    by_reference: dict[str, Property] = {}
    if references:
        existing = (
            await db.execute(
                select(Property).where(
                    Property.agency_id == agency_id,
                    Property.reference.in_(references),
                )
            )
        ).scalars().all()
        # Belt and braces: the lookup already filters by tenant, but the
        # write site re-checks so a future edit to that query can never turn
        # a re-import into "overwrite another agency's catalog".
        by_reference = {
            prop.reference: prop for prop in existing if prop.agency_id == agency_id
        }

    created = 0
    updated = 0
    # References left out for lack of plan slots. A set, not a counter: a file
    # that repeats a rejected reference must be reported as one listing left
    # out, which is what the agency will count when comparing with its export.
    over_limit: set[str] = set()
    # `strict` on purpose: the parser guarantees one `provided` set per row,
    # and the old fallback ("assume the file provided everything") was the
    # exact behaviour that lets a CSV default overwrite a curated column.
    for row, provided in zip(parsed.rows, parsed.provided, strict=True):
        reference = row["reference"]
        target = by_reference.get(reference)
        if target is not None:
            apply_row_to_property(target, row, provided)
            updated += 1
            continue
        if free_slots is not None and created >= free_slots:
            # Out of slots: skip the creation but keep walking the file, so
            # the remaining updates still land and the count of what was left
            # out is the real one.
            over_limit.add(reference)
            continue
        new_property = Property(agency_id=agency_id, **row)
        db.add(new_property)
        # A file that repeats the same reference must not create two rows:
        # later occurrences update the one we just staged.
        by_reference[reference] = new_property
        created += 1

    await db.commit()

    if created:
        # Backfill embeddings in the background; without Redis this must
        # never break the import itself.
        try:
            from app.workers.tasks import embed_missing_properties

            embed_missing_properties.delay()
        except Exception:
            log.warning("import.embed_enqueue_failed", agency=str(agency_id))

    plan_limit = (
        properties_usage.limit
        if properties_usage is not None and properties_usage.limit >= 0
        else None
    )

    # Leading the errors list with the limit, in the same plain Spanish as the
    # parser's own messages: a client that only renders `errors` (the panel
    # today, the mobile app) must not present a half-done import as a clean
    # one. The structured fields below are for the upgrade prompt.
    errors = list(parsed.errors[:20])
    if over_limit:
        errors.insert(
            0,
            f"No se han importado {len(over_limit)} propiedades porque tu plan "
            f"permite {plan_limit} y ya lo has alcanzado.",
        )
        log.warning(
            "properties.import_hit_plan_limit",
            agency=str(agency_id),
            left_out=len(over_limit),
            limit=plan_limit,
        )

    log.info(
        "properties.imported",
        agency=str(agency_id),
        created=created,
        updated=updated,
        skipped=parsed.skipped,
    )
    return ImportResult(
        imported=created + updated,
        created=created,
        updated=updated,
        skipped=parsed.skipped,
        errors=errors,
        limit_skipped=len(over_limit),
        limit_reason="plan_limit_reached" if over_limit else None,
        properties_limit=plan_limit,
    )


class DescribeBody(BaseModel):
    # Kept only to give old clients a typed refusal instead of silently
    # overwriting. Applying now has its own exact-text endpoint below.
    apply: bool = False
    replace_existing_draft: bool = False
    image_urls: list[str] | None = None  # override; otherwise use stored ones
    target_language: TargetLanguage = "es"


class DescriptionTextBody(BaseModel):
    description: DescriptionText

    @field_validator("description")
    @classmethod
    def description_is_not_blank(cls, value: str) -> str:
        return _non_blank_description(value)


class ApplyDescriptionBody(DescriptionTextBody):
    draft_id: uuid.UUID
    # A stale draft is rejected by default. The dashboard only sends this
    # after showing the newly loaded description and asking a second time.
    force: bool = False
    confirmed_current_description: Annotated[
        str, StringConstraints(max_length=MAX_PROPERTY_DESCRIPTION_LENGTH)
    ] | None = None


class ApplyDescriptionOut(BaseModel):
    description: str
    applied_at: datetime
    changed: bool
    undo_id: uuid.UUID | None


class RestoreDescriptionOut(BaseModel):
    description: str
    restored_at: datetime


class CancelDescriptionDraftOut(BaseModel):
    ok: bool


async def _scoped_property(
    db: AsyncSession, agency_id: uuid.UUID, property_id: uuid.UUID
) -> Property:
    prop = await db.get(Property, property_id)
    if prop is None or prop.agency_id != agency_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "property_not_found")
    return prop


def _draft_conflict(current_description: str) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT,
        detail={
            "code": "description_changed_since_draft",
            "current_description": current_description,
        },
    )


@router.post("/{property_id}/describe-from-photos", response_model=DescriptionDraftOut)
async def describe_from_photos(
    agency_id: uuid.UUID,
    property_id: uuid.UUID,
    body: DescribeBody,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_agency_member),
) -> DescriptionDraftOut:
    prop = await _scoped_property(db, agency_id, property_id)

    if body.apply:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "direct_apply_disabled"},
        )

    current_draft = _active_description_draft(prop)
    if current_draft is not None and not body.replace_existing_draft:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "active_description_draft_exists"},
        )

    images = body.image_urls if body.image_urls is not None else list(prop.image_urls or [])
    if not images:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no_images_to_describe")

    try:
        result = await describe_property_from_photos(
            prop,
            image_urls=images,
            target_language=body.target_language,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:
        log.exception("vision.describe_failed", prop=str(property_id))
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "vision_provider_failed",
        ) from exc

    generated_description = result.description
    if (
        not isinstance(generated_description, str)
        or not generated_description.strip()
        or len(generated_description) > MAX_PROPERTY_DESCRIPTION_LENGTH
    ):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail={"code": "vision_invalid_description"},
        )

    now = datetime.now(UTC)
    draft = {
        "id": str(uuid.uuid4()),
        "description": generated_description,
        "generated_description": generated_description,
        "highlights": result.highlights,
        "weaknesses": result.weaknesses,
        "base_description": prop.description,
        "base_description_sha256": _description_hash(prop.description),
        "target_language": body.target_language,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "created_by": _actor_id(auth),
    }
    try:
        draft_out = DescriptionDraftOut.model_validate(draft)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail={"code": "vision_invalid_response"},
        ) from exc
    draft["highlights"] = draft_out.highlights
    draft["weaknesses"] = draft_out.weaknesses

    extra = dict(getattr(prop, "extra", None) or {})
    extra[DESCRIPTION_DRAFT_KEY] = draft
    prop.extra = extra
    await db.commit()
    log.info(
        "property.description_draft_generated",
        agency=str(agency_id),
        prop=str(property_id),
        draft=draft["id"],
    )
    return draft_out


@router.get("/{property_id}/description-draft", response_model=DescriptionDraftOut)
async def get_description_draft(
    agency_id: uuid.UUID,
    property_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> DescriptionDraftOut:
    prop = await _scoped_property(db, agency_id, property_id)
    draft = _active_description_draft(prop)
    if draft is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "description_draft_not_found")
    return _draft_out(draft)


@router.put(
    "/{property_id}/description-draft/{draft_id}", response_model=DescriptionDraftOut
)
async def update_description_draft(
    agency_id: uuid.UUID,
    property_id: uuid.UUID,
    draft_id: uuid.UUID,
    body: DescriptionTextBody,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_agency_member),
) -> DescriptionDraftOut:
    prop = await _locked_property(db, agency_id, property_id)
    draft = _active_description_draft(prop)
    if draft is None or draft.get("id") != str(draft_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "description_draft_not_found")

    draft["description"] = body.description
    draft["updated_at"] = datetime.now(UTC).isoformat()
    draft["updated_by"] = _actor_id(auth)
    draft_out = _draft_out(draft)
    extra = dict(prop.extra or {})
    extra[DESCRIPTION_DRAFT_KEY] = draft
    prop.extra = extra
    await db.commit()
    return draft_out


@router.delete(
    "/{property_id}/description-draft/{draft_id}",
    response_model=CancelDescriptionDraftOut,
)
async def cancel_description_draft(
    agency_id: uuid.UUID,
    property_id: uuid.UUID,
    draft_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = Depends(require_agency_member),
) -> CancelDescriptionDraftOut:
    prop = await _locked_property(db, agency_id, property_id)
    draft = _active_description_draft(prop)
    # A repeated DELETE after a client timeout is safe and reports success.
    if draft is None:
        return CancelDescriptionDraftOut(ok=True)
    if draft.get("id") != str(draft_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "description_draft_not_found")

    extra = dict(prop.extra or {})
    extra.pop(DESCRIPTION_DRAFT_KEY, None)
    prop.extra = extra
    await db.commit()
    log.info(
        "property.description_draft_cancelled",
        agency=str(agency_id),
        prop=str(property_id),
        draft=str(draft_id),
        actor=_actor_id(auth),
    )
    return CancelDescriptionDraftOut(ok=True)


@router.put(
    "/{property_id}/description",
    response_model=ApplyDescriptionOut,
)
async def apply_reviewed_description(
    agency_id: uuid.UUID,
    property_id: uuid.UUID,
    body: ApplyDescriptionBody,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = OwnerOnlyDep,
) -> ApplyDescriptionOut:
    prop = await _locked_property(db, agency_id, property_id)
    draft = _active_description_draft(prop)
    if draft is None or draft.get("id") != str(body.draft_id):
        # The commit may have succeeded even if the client saw a timeout.  A
        # retry of the same exact approval is therefore idempotent; it must
        # neither create a second history item nor pretend a different text
        # was applied.
        audit = (getattr(prop, "extra", None) or {}).get("last_vision_audit")
        if (
            isinstance(audit, dict)
            and audit.get("draft_id") == str(body.draft_id)
            and prop.description == body.description
        ):
            try:
                applied_at = datetime.fromisoformat(audit["applied_at"])
                undo_id = (
                    uuid.UUID(audit["undo_id"])
                    if isinstance(audit.get("undo_id"), str)
                    else None
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "description_draft_not_active"
                ) from exc
            return ApplyDescriptionOut(
                description=prop.description,
                applied_at=applied_at,
                changed=bool(audit.get("changed")),
                undo_id=undo_id,
            )
        raise HTTPException(status.HTTP_409_CONFLICT, "description_draft_not_active")

    validated_draft = _draft_out(draft)

    expected_hash = draft.get("base_description_sha256")
    if body.force:
        # The user must confirm the *exact* live text they just reviewed.  If
        # it changes again between conflict display and confirmation, return
        # the newer version instead of silently overwriting it.
        if body.confirmed_current_description != prop.description:
            raise _draft_conflict(prop.description)
    elif expected_hash != _description_hash(prop.description):
        raise _draft_conflict(prop.description)

    now = datetime.now(UTC)
    previous = prop.description
    changed = body.description != previous
    undo_id: uuid.UUID | None = None
    extra = dict(prop.extra or {})

    if changed:
        undo_id = uuid.uuid4()
        history = _description_history(prop)
        history.append(
            {
                "id": str(undo_id),
                "description": previous,
                "applied_description_sha256": _description_hash(body.description),
                "replaced_at": now.isoformat(),
                "replaced_by": _actor_id(auth),
                "draft_id": str(body.draft_id),
                "restored_at": None,
            }
        )
        extra[DESCRIPTION_HISTORY_KEY] = history[-DESCRIPTION_HISTORY_LIMIT:]

    prop.description = body.description
    extra["last_vision_audit"] = {
        "highlights": validated_draft.highlights,
        "weaknesses": validated_draft.weaknesses,
        "language": draft.get("target_language") or "es",
        "draft_id": str(body.draft_id),
        "generated_at": draft.get("created_at"),
        "applied_at": now.isoformat(),
        "applied_by": _actor_id(auth),
        "description_sha256": _description_hash(body.description),
        "changed": changed,
        "undo_id": str(undo_id) if undo_id is not None else None,
    }
    extra.pop(DESCRIPTION_DRAFT_KEY, None)
    prop.extra = extra
    await db.commit()
    log.info(
        "property.description_applied",
        agency=str(agency_id),
        prop=str(property_id),
        draft=str(body.draft_id),
        changed=changed,
    )
    return ApplyDescriptionOut(
        description=prop.description,
        applied_at=now,
        changed=changed,
        undo_id=undo_id,
    )


@router.get(
    "/{property_id}/description-history", response_model=list[DescriptionHistoryItem]
)
async def list_description_history(
    agency_id: uuid.UUID,
    property_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[DescriptionHistoryItem]:
    prop = await _scoped_property(db, agency_id, property_id)
    current_hash = _description_hash(prop.description)
    items: list[DescriptionHistoryItem] = []
    for raw in reversed(_description_history(prop)):
        try:
            restored_at = raw.get("restored_at")
            items.append(
                DescriptionHistoryItem(
                    id=raw["id"],
                    description=raw["description"],
                    replaced_at=raw["replaced_at"],
                    restored_at=restored_at,
                    can_restore=(
                        restored_at is None
                        and raw.get("applied_description_sha256") == current_hash
                    ),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return items


@router.post(
    "/{property_id}/description-history/{history_id}/restore",
    response_model=RestoreDescriptionOut,
)
async def restore_description_history(
    agency_id: uuid.UUID,
    property_id: uuid.UUID,
    history_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    auth: AuthContext = OwnerOnlyDep,
) -> RestoreDescriptionOut:
    prop = await _locked_property(db, agency_id, property_id)
    history = _description_history(prop)
    entry = next((item for item in history if item.get("id") == str(history_id)), None)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "description_history_not_found")
    previous = entry.get("description")
    if not isinstance(previous, str):
        raise HTTPException(status.HTTP_409_CONFLICT, "invalid_description_history")

    restored_at = entry.get("restored_at")
    if restored_at is not None:
        # As with applying, retrying after an uncertain response is safe when
        # this same restore is still the current value.
        if prop.description == previous and isinstance(restored_at, str):
            try:
                return RestoreDescriptionOut(
                    description=prop.description,
                    restored_at=datetime.fromisoformat(restored_at),
                )
            except ValueError:
                pass
        raise HTTPException(status.HTTP_409_CONFLICT, "description_history_already_restored")
    if entry.get("applied_description_sha256") != _description_hash(prop.description):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "description_changed_since_history"},
        )

    now = datetime.now(UTC)
    prop.description = previous
    entry["restored_at"] = now.isoformat()
    entry["restored_by"] = _actor_id(auth)
    extra = dict(prop.extra or {})
    extra[DESCRIPTION_HISTORY_KEY] = history[-DESCRIPTION_HISTORY_LIMIT:]
    prop.extra = extra
    await db.commit()
    log.info(
        "property.description_restored",
        agency=str(agency_id),
        prop=str(property_id),
        history=str(history_id),
    )
    return RestoreDescriptionOut(description=prop.description, restored_at=now)
