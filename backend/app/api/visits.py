"""Visit endpoints — the agenda listing, manual booking, and post-visit notes.

The listing is what the dashboard's /visitas page and the mobile "agenda de
hoy" read: the AI schedules visits and writes a pre-visit briefing, and
until now nothing could show them.

`POST ""` is the human counterpart of the agent's `schedule_visit` tool: a
visit agreed on the phone, at the office, or moved from another calendar has
to be able to enter the agenda too. It is also where the Google Calendar
mirror hangs (`sync_visit_to_calendar`) — best effort, always after the visit
is committed, because a visit agreed with a person must not depend on Google
answering.

Agents use the dashboard/mobile to drop a voice memo after each visit.
We transcribe (Deepgram), then defer structuring to a Celery task so the
HTTP response stays snappy.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AgencyMemberDep
from app.core.logging import get_logger
from app.db.models import Agency, Lead, Property, Visit, VisitStatus
from app.db.session import get_db
from app.services.event_dispatcher import emit_visit_completed, emit_visit_scheduled
from app.services.google_calendar import sync_visit_to_calendar
from app.services.transcription import transcribe_audio

log = get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/agencies/{agency_id}/visits",
    tags=["visits"],
    dependencies=[AgencyMemberDep],
)

DEFAULT_TIMEZONE = "Europe/Madrid"


def resolve_agency_timezone(name: str | None) -> tuple[tzinfo, str]:
    """Best-effort timezone lookup that can never raise.

    A bad `agency.timezone` falls back to Madrid; an image without a
    timezone database falls back to UTC. Same contract (and same fallbacks)
    as `app.agent.runner.resolve_timezone` — duplicated on purpose so the
    API layer does not import the agent stack for six lines.
    """
    for candidate in ((name or "").strip(), DEFAULT_TIMEZONE):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate), candidate
        except Exception:
            log.warning("visits.bad_timezone", tz=candidate)
    return UTC, "UTC"


# ---------------------------------------------------------------------------
# Agenda listing
# ---------------------------------------------------------------------------


class VisitOut(BaseModel):
    id: uuid.UUID
    scheduled_at: datetime
    status: VisitStatus
    lead_name: str | None
    lead_phone: str | None
    property_title: str | None
    property_id: uuid.UUID
    briefing: str
    # IANA name of the agency's timezone. `scheduled_at` is an absolute
    # instant, so whoever renders the agenda needs this to print the hour
    # and to group by day: doing either in the server's timezone puts a
    # 00:30 Madrid visit on the previous day and shows it as 22:30.
    timezone: str


@router.get("", response_model=list[VisitOut])
async def list_visits(
    agency_id: uuid.UUID,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    upcoming: bool = Query(
        default=False,
        description="Bound the listing at midnight today in the agency's timezone.",
    ),
    limit: int = Query(default=200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[VisitOut]:
    """Visits of this agency, chronological. `from`/`to` bound `scheduled_at`.

    `upcoming=true` (what the agenda screen uses) replaces an absent `from`
    with the start of today *in the agency's own timezone* — the caller
    cannot compute that bound itself without first knowing the timezone.
    An explicit `from` always wins.

    Both joins are outer and scoped to the agency: a listing deleted after
    the visit was booked must not make the visit disappear from the agenda,
    and a row pointing at another tenant's lead or property must show up
    without that tenant's data.
    """
    agency = await db.get(Agency, agency_id)
    tz, tz_name = resolve_agency_timezone(agency.timezone if agency is not None else None)

    if upcoming and from_ is None:
        from_ = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)

    stmt = (
        select(Visit, Lead, Property)
        .outerjoin(Lead, (Lead.id == Visit.lead_id) & (Lead.agency_id == agency_id))
        .outerjoin(
            Property,
            (Property.id == Visit.property_id) & (Property.agency_id == agency_id),
        )
        .where(Visit.agency_id == agency_id)
    )
    if from_ is not None:
        stmt = stmt.where(Visit.scheduled_at >= from_)
    if to is not None:
        stmt = stmt.where(Visit.scheduled_at <= to)
    stmt = stmt.order_by(Visit.scheduled_at.asc()).limit(limit)

    rows = (await db.execute(stmt)).all()
    return [
        VisitOut(
            id=visit.id,
            scheduled_at=visit.scheduled_at,
            status=visit.status,
            lead_name=lead.full_name if lead is not None else None,
            lead_phone=lead.phone_e164 if lead is not None else None,
            property_title=prop.title if prop is not None else None,
            property_id=visit.property_id,
            # `pre_visit_briefing` is filled by a background job shortly
            # before the visit — empty string until then, never null.
            briefing=visit.pre_visit_briefing or "",
            timezone=tz_name,
        )
        for visit, lead, prop in rows
    ]


# ---------------------------------------------------------------------------
# Manual booking
# ---------------------------------------------------------------------------


class VisitCreateIn(BaseModel):
    lead_id: uuid.UUID
    property_id: uuid.UUID
    scheduled_at: datetime
    # Same clamp as the agent's tool (app/agent/tools.py): a 0-minute or
    # 8-hour "visit" makes every collision and office-hours check meaningless.
    duration_minutes: int = Field(default=30, ge=15, le=240)
    briefing: str = Field(default="", max_length=2000)


@router.post("", response_model=VisitOut, status_code=status.HTTP_201_CREATED)
async def create_visit(
    agency_id: uuid.UUID,
    body: VisitCreateIn,
    db: AsyncSession = Depends(get_db),
) -> VisitOut:
    """Book a visit by hand and mirror it in Google Calendar.

    Status is CONFIRMED, not REQUESTED: unlike the agent's proposal, a human
    typing this has already agreed the time with the client.

    Both rows are checked against `agency_id` before anything is written —
    the ids arrive from the client, and a lead or property of another tenant
    must be a 404, not a cross-tenant visit.
    """
    agency = await db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agency_not_found")
    tz, tz_name = resolve_agency_timezone(agency.timezone)

    lead = await db.get(Lead, body.lead_id)
    if lead is None or lead.agency_id != agency_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lead_not_found")
    prop = await db.get(Property, body.property_id)
    if prop is None or prop.agency_id != agency_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "property_not_found")

    scheduled_at = body.scheduled_at
    if scheduled_at.tzinfo is None:
        # A datetime-local input has no offset. It means the hour the agency
        # works in, never the server's — reading it as UTC would file a 09:00
        # Madrid visit at 11:00 in summer.
        scheduled_at = scheduled_at.replace(tzinfo=tz)

    visit = Visit(
        agency_id=agency_id,
        lead_id=lead.id,
        property_id=prop.id,
        scheduled_at=scheduled_at,
        duration_minutes=body.duration_minutes,
        status=VisitStatus.CONFIRMED,
        pre_visit_briefing=body.briefing,
        post_visit_notes="",
    )
    db.add(visit)
    await db.flush()  # the CRM event needs the row's id

    await emit_visit_scheduled(db, visit, lead=lead)
    # Committed *before* talking to Google: the visit is the fact, the
    # calendar event is a copy. If the process dies during the Google call
    # the agenda is still right.
    await db.commit()

    # Best effort by contract — never raises, returns None when the agency has
    # no calendar connected or Google is having a bad day.
    await sync_visit_to_calendar(agency, visit, lead=lead, property_=prop)
    try:
        await db.commit()
    except Exception:
        # Only the event id (and a possible "Google revoked us" notice) are in
        # this second commit. The visit is already booked; answering 500 now
        # would tell the caller the opposite of what happened.
        await db.rollback()
        log.exception("visits.calendar_metadata_commit_failed", visit=str(visit.id))

    return VisitOut(
        id=visit.id,
        scheduled_at=visit.scheduled_at,
        status=visit.status,
        lead_name=lead.full_name,
        lead_phone=lead.phone_e164,
        property_title=prop.title,
        property_id=visit.property_id,
        briefing=visit.pre_visit_briefing or "",
        timezone=tz_name,
    )


async def _mark_visit_done(db: AsyncSession, visit: Visit) -> None:
    """A post-visit note is proof the visit happened: close it and say so.

    The status flip used to live inside the Celery task that structures the
    transcript, which meant a visit whose transcription failed stayed
    CONFIRMED forever even though the agent had clearly been there — and the
    `visit.completed` webhook had no honest place to fire from. Doing it here
    makes the completion depend on the agent's action rather than on a
    provider answering; the worker's own flip is now a no-op.
    """
    visit.status = VisitStatus.COMPLETED
    lead = await db.get(Lead, visit.lead_id)
    if lead is not None and lead.agency_id != visit.agency_id:
        lead = None  # cross-tenant row: never put its name in a payload
    # Scheduled first: a CRM that never heard the visit was booked cannot
    # place its completion. Both are idempotent, so the usual case (the
    # booking was already announced) sends only the completion.
    await emit_visit_scheduled(db, visit, lead=lead)
    await emit_visit_completed(db, visit, lead=lead)


@router.post("/{visit_id}/voice-note", status_code=202)
async def upload_voice_note(
    agency_id: uuid.UUID,
    visit_id: uuid.UUID,
    file: UploadFile = File(...),
    language: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    visit = await db.get(Visit, visit_id)
    if visit is None or visit.agency_id != agency_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "visit_not_found")

    audio = await file.read()
    if not audio:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty_audio")

    transcript = await transcribe_audio(
        audio,
        content_type=file.content_type or "audio/ogg",
        language=language,
    )
    if not transcript:
        # Fallback: store raw marker so a human can retry.
        visit.post_visit_notes = "[transcripción no disponible]"
        await _mark_visit_done(db, visit)
        await db.commit()
        return {"status": "transcription_unavailable"}

    # Save transcript immediately so the agent sees *something*.
    visit.post_visit_notes = transcript
    await _mark_visit_done(db, visit)
    await db.commit()

    # Defer structured rewrite.
    try:
        from app.workers.tasks import structure_voice_note
        structure_voice_note.delay(str(visit_id), transcript)
    except Exception:
        pass

    return {"status": "accepted", "transcript_preview": transcript[:200]}
