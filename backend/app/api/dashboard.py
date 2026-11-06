"""Read-only API the Next.js dashboard talks to.

Gated behind Clerk JWT verification (`require_auth`). In dev, the
fallback X-Dev-User header is honored.
"""
from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import and_, case, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompts import CONVERSATION_NOTE_EXTRA_KEY, MAX_CONVERSATION_NOTE_CHARS
from app.agent.tools import HANDOFF_AT_KEY, HANDOFF_REASON_KEY, HANDOFF_URGENCY_KEY
from app.api.channels import leads_email_for
from app.api.visits import resolve_agency_timezone
from app.channels.email.client import ResendClient
from app.channels.email.formatting import email_parts
from app.channels.whatsapp.client import WhatsAppClient
from app.core.auth import AgencyMemberDep
from app.core.logging import get_logger
from app.db.models import (
    Agency,
    AgentRun,
    Conversation,
    ConversationChannel,
    ConversationStatus,
    Lead,
    LeadStatus,
    LeadTemperature,
    Message,
    MessageDirection,
    MessageRole,
    Office,
    Property,
    User,
    Visit,
    VisitStatus,
)
from app.db.session import get_db
from app.services.secret_store import secret_from_settings

log = get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/agencies/{agency_id}",
    tags=["dashboard"],
    dependencies=[AgencyMemberDep],
)


# ---------------------------------------------------------------------------
# Keyset pagination
#
# The listings used to stop at a hard `limit` (50 conversations, 100 leads)
# and say nothing about it, so an agency past that size simply could not see
# its older rows — and the CSV export built from the same list quietly
# exported the first 100 leads under the name "todos".
#
# OFFSET pagination would not fix it: rows arrive while the user pages, and
# with OFFSET that shifts every later page (the same conversation shows up
# twice, another one is never shown). A keyset cursor pins the position to
# the last row *seen* instead of to a row count, so pages neither overlap nor
# skip. The tie-breaker is the row id: `created_at` is not unique — two leads
# imported in the same batch share it to the microsecond — and a cursor on
# the timestamp alone would drop every row that ties with the page boundary.
#
# The cursor is opaque on purpose (clients must not build one), but it is not
# a security token: every query still filters by the `agency_id` in the path,
# which `AgencyMemberDep` already authorized. The agency stamped inside is a
# consistency check — pasting agency A's cursor into agency B's listing is a
# bug, and answering 400 says so instead of silently returning page 1.
# ---------------------------------------------------------------------------

CURSOR_VERSION = 1


def encode_cursor(agency_id: uuid.UUID, key: list[Any]) -> str:
    """Opaque, URL-safe encoding of one row's position in the sort order."""
    raw = json.dumps({"v": CURSOR_VERSION, "a": str(agency_id), "k": key}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str, agency_id: uuid.UUID, *, expected_len: int) -> list[Any]:
    """Inverse of `encode_cursor`. Anything unexpected is a 400, never a crash.

    A cursor is user input: it arrives in a query string, gets pasted between
    tabs, and survives a deploy that changed the sort order. Every one of
    those has to end in "empieza de nuevo", not in a 500.
    """
    padding = "=" * (-len(cursor) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding))
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_cursor") from exc

    if not isinstance(payload, dict) or payload.get("v") != CURSOR_VERSION:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_cursor")
    if payload.get("a") != str(agency_id):
        # Another tenant's cursor. The listing would have filtered its rows
        # out anyway, so this is not a leak — it is a caller who is confused
        # about which agency they are paging.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_cursor")

    key = payload.get("k")
    if not isinstance(key, list) or len(key) != expected_len:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_cursor")
    return key


def _cursor_datetime(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_cursor") from exc
    # Naive timestamps compare as "no timezone" against tz-aware columns and
    # blow up inside SQLAlchemy; the columns are all `timezone=True`.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _cursor_uuid(value: Any) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_cursor") from exc


def _cursor_int(value: Any) -> int:
    # `True` is an int in Python and would silently become rank 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_cursor")
    return value


def keyset_before(ts_col, id_col, ts: datetime, row_id: uuid.UUID):
    """Rows strictly after the cursor in a `(ts DESC, id DESC)` ordering.

    Written as an explicit OR instead of a row-value comparison so it reads
    the same as the ORDER BY next to it and runs on any backend. The second
    branch is the whole point: without it every row sharing `ts` with the
    cursor row is skipped.
    """
    return or_(ts_col < ts, and_(ts_col == ts, id_col < row_id))


def keyset_after_ranked(rank_col, ts_col, id_col, rank: int, ts: datetime, row_id: uuid.UUID):
    """Same, for a `(rank ASC, ts DESC, id DESC)` ordering."""
    return or_(rank_col > rank, and_(rank_col == rank, keyset_before(ts_col, id_col, ts, row_id)))


def latest_message_rank(conv_col, ts_col, id_col):
    """1 for the newest message of each conversation, 2 for the next, and so on.

    Extracted so the test can run it against a real table. The tie-break on
    the message id is not decoration: `Message.created_at` defaults to
    Postgres' `now()`, which is the *transaction* timestamp, so the inbound
    message and the reply of the same turn share it exactly. Ranking without
    the tie-break leaves the winner up to the planner; picking the maximum
    without one returns both.
    """
    return func.row_number().over(
        partition_by=conv_col, order_by=(desc(ts_col), desc(id_col))
    )


# ---------------------------------------------------------------------------
# Office filter (plan Agencia, "varias oficinas")
#
# The site promises "cada contacto va solo a la oficina que le corresponde".
# Nothing stamps an office on a lead — leads arrive from WhatsApp, a form or
# a phone call, long before anyone decides whose lead it is — so the office
# is *derived* from the two links that do exist and are real foreign keys:
#
#   * the property the lead was taken to visit  -> `properties.office_id`
#   * the person handling it (conversation assigned, or visit assigned)
#     -> `users.office_id`
#
# A lead therefore belongs to office X when it has at least one link to X.
# "At least one" and not "exactly one" is what makes the second promise —
# "Comparte contactos entre tus oficinas" — true rather than aspirational: a
# lead being shown two branches around Madrid shows up in both.
#
# Everything below is inert unless a caller passes `?office=`. Without the
# parameter not a single clause is added to any query, which is the whole
# contract for the agencies (almost all of them) that never create an office.
# ---------------------------------------------------------------------------


def office_user_ids(agency_id: uuid.UUID, office_id: uuid.UUID, *, user=User):
    """The ids of the people assigned to one office of this agency."""
    return select(user.id).where(user.agency_id == agency_id, user.office_id == office_id)


def office_belongs_to_agency(agency_id: uuid.UUID, office_id: uuid.UUID, *, office=Office):
    """Predicate: is `office_id` really one of `agency_id`'s branches?

    The endpoints already answer 404 for a stranger's office
    (`ensure_office_of_agency`), so this is the second lock on the same door.
    It is here because the first one lives in the caller: any future caller
    that filters by an office without validating it — the automatic
    "cada comercial ve solo su oficina" this feature is one line away from —
    would otherwise be trusting that no listing anywhere carries an office id
    from another tenant, which nothing in the schema enforces.

    Uncorrelated, so Postgres evaluates it once per statement, not per row.
    """
    return (
        select(office.id)
        .where(office.id == office_id, office.agency_id == agency_id)
        .exists()
    )


def lead_in_office(
    agency_id: uuid.UUID,
    office_id: uuid.UUID,
    *,
    lead=Lead,
    conversation=Conversation,
    visit=Visit,
    property_=Property,
    user=User,
    office=Office,
):
    """Predicate: does this lead have any link to `office_id`?

    `.correlate(lead)` is not decoration. Both subqueries select FROM tables
    that also appear in the enclosing statement (`conversations` is joined in
    the inbox), and SQLAlchemy's automatic correlation would happily pull
    them out of the subquery's FROM, leaving an EXISTS that is true for every
    row. Naming the one table to correlate against pins it.

    The model classes are keyword arguments with real defaults so the test
    can execute this exact expression against a SQLite mirror: the production
    tables carry UUID, ARRAY and pgvector columns and cannot be created
    outside Postgres, and a filter that decides who sees whose leads deserves
    to be run, not just compiled.
    """
    members = office_user_ids(agency_id, office_id, user=user)
    by_conversation = (
        select(conversation.id)
        .where(
            conversation.lead_id == lead.id,
            conversation.agency_id == agency_id,
            conversation.assigned_user_id.in_(members),
        )
        .correlate(lead)
        .exists()
    )
    by_visit = (
        select(visit.id)
        .join(property_, property_.id == visit.property_id)
        .where(
            visit.lead_id == lead.id,
            visit.agency_id == agency_id,
            # The listing too, not just the visit. Nothing in the database
            # guarantees that a listing's office belongs to the listing's
            # agency — only `assign_property` does, and it is one endpoint
            # among the several ways rows get written (CSV import, a fix in
            # psql, whatever writes next). Without this clause a single
            # mis-stamped listing lets one tenant's visit answer another
            # tenant's office filter, and "toda query filtra por agency_id"
            # stops being true exactly where it decides who sees whose
            # contacts.
            property_.agency_id == agency_id,
            or_(
                property_.office_id == office_id,
                visit.assigned_user_id.in_(members),
            ),
        )
        .correlate(lead)
        .exists()
    )
    return and_(
        office_belongs_to_agency(agency_id, office_id, office=office),
        or_(by_conversation, by_visit),
    )


def conversation_in_office(
    agency_id: uuid.UUID,
    office_id: uuid.UUID,
    *,
    lead=Lead,
    conversation=Conversation,
    visit=Visit,
    property_=Property,
    user=User,
    office=Office,
):
    """Same question for one thread of the inbox.

    A conversation handed off to someone belongs to that person's office;
    otherwise it inherits whatever its lead is linked to. The second half is
    what keeps the filter useful: most threads are handled by the AI and have
    no assigned user at all.

    The first half needs no ownership check of its own: `members` is already
    "users of *this* agency assigned to that office", which is empty for an
    office that is not ours. The second half carries the check inside
    `lead_in_office`.
    """
    members = office_user_ids(agency_id, office_id, user=user)
    return or_(
        conversation.assigned_user_id.in_(members),
        lead_in_office(
            agency_id,
            office_id,
            lead=lead,
            conversation=conversation,
            visit=visit,
            property_=property_,
            user=user,
            office=office,
        ),
    )


async def ensure_office_of_agency(
    db: AsyncSession, agency_id: uuid.UUID, office_id: uuid.UUID | None
) -> None:
    """404 unless `office_id` is one of this agency's offices.

    Filtering by a stranger's office id would otherwise just answer an empty
    list: the right rows, the wrong story — the panel would print "no hay
    leads" for what is really "esa oficina no es tuya". It also stops the
    parameter from becoming a way to probe which uuids exist elsewhere.
    """
    if office_id is None:
        return
    found = (
        await db.execute(
            select(Office.id).where(Office.id == office_id, Office.agency_id == agency_id)
        )
    ).scalar_one_or_none()
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "office_not_found")


# ---------------------------------------------------------------------------
# Inbox: latest message per conversation, with lead info, prioritized.
# ---------------------------------------------------------------------------

class InboxItem(BaseModel):
    conversation_id: uuid.UUID
    channel: ConversationChannel
    status: ConversationStatus
    lead_id: uuid.UUID
    lead_name: str | None
    lead_temperature: LeadTemperature
    last_message_at: datetime
    last_message_role: MessageRole
    last_message_preview: str
    unread: bool
    assigned_user_id: uuid.UUID | None


class InboxPage(BaseModel):
    items: list[InboxItem]
    # Null means "this was the last page". Feed it back as `?cursor=` to get
    # the next one; never build one by hand.
    next_cursor: str | None


@router.get("/inbox", response_model=None)
async def inbox(
    agency_id: uuid.UUID,
    q: str | None = Query(None, max_length=200),
    channel: ConversationChannel | None = None,
    conv_status: ConversationStatus | None = None,
    only_unhandled: bool = False,
    office: uuid.UUID | None = Query(
        None,
        description="Solo conversaciones de esa oficina. Sin el parámetro, todas.",
    ),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, max_length=512),
    paginated: bool = Query(
        False,
        description="Devuelve {items, next_cursor} en vez de la lista pelada.",
    ),
    db: AsyncSession = Depends(get_db),
) -> list[InboxItem] | InboxPage:
    """The unified inbox, newest first with hot leads on top.

    Two response shapes on purpose. Without `paginated`/`cursor` the body is
    the bare array it has always been, because the mobile app and the panel
    parse it as one and a silent envelope would break both. Ask for
    `paginated=true` (or send a `cursor`) and you get `{items, next_cursor}`
    instead — the only way to reach anything past the first page.

    Caveat worth knowing before building on it: the sort key is *live* data
    (a lead's temperature, the timestamp of its last message). A conversation
    that gets a new message while you are paging moves to the top and can
    therefore be seen twice, and one that cools down can move past you. That
    is inherent to sorting an inbox by recency; the cursor guarantees
    consistency against the ordering, not against the clock.

    `office` is optional and inert when absent: without it the statement
    built here is exactly the one that ran before offices existed.
    """
    await ensure_office_of_agency(db, agency_id, office)

    # One row per conversation, guaranteed.
    #
    # This used to be `GROUP BY conversation_id` + a self-join on
    # `created_at == max(created_at)`, which returns *one row per message tied
    # at the maximum*. That is not a corner case here: `Message.created_at` is
    # `server_default=func.now()` and Postgres' `now()` is the transaction
    # timestamp, so the inbound message and the reply written in the same turn
    # (one `task_session()`, no commit in between) carry the exact same
    # microsecond. Every conversation whose last turn produced both therefore
    # appeared *twice* in the inbox, with the preview flipping between the
    # lead's text and the agent's, a page of `limit` rows holding barely half
    # that many conversations, and `has_more` counting the duplicates.
    #
    # `row_number()` picks the winner explicitly, breaking the tie on message
    # id — the same tie-break the cursor uses — so the row that represents a
    # conversation is stable across pages instead of planner-dependent.
    ranked_msg = (
        select(
            Message.id.label("message_id"),
            Message.conversation_id.label("conversation_id"),
            Message.created_at.label("last_at"),
            latest_message_rank(Message.conversation_id, Message.created_at, Message.id).label(
                "rn"
            ),
        )
        .where(Message.agency_id == agency_id)
        .subquery()
    )

    stmt = (
        select(Conversation, Lead, Message, ranked_msg.c.last_at)
        .join(Lead, Lead.id == Conversation.lead_id)
        .join(
            ranked_msg,
            (ranked_msg.c.conversation_id == Conversation.id) & (ranked_msg.c.rn == 1),
        )
        .join(Message, Message.id == ranked_msg.c.message_id)
        .where(Conversation.agency_id == agency_id)
    )
    if q:
        needle = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Lead.full_name.ilike(needle),
                Lead.phone_e164.ilike(needle),
                Message.content.ilike(needle),
            )
        )
    if channel:
        stmt = stmt.where(Conversation.channel == channel)
    if conv_status:
        stmt = stmt.where(Conversation.status == conv_status)
    if only_unhandled:
        stmt = stmt.where(Conversation.status == ConversationStatus.ACTIVE)
    if office is not None:
        stmt = stmt.where(conversation_in_office(agency_id, office))

    # Hot leads first, then most recent.
    temp_priority = case(
        (Lead.temperature == LeadTemperature.HOT, 0),
        (Lead.temperature == LeadTemperature.WARM, 1),
        else_=2,
    )

    if cursor:
        rank, ts, row_id = decode_cursor(cursor, agency_id, expected_len=3)
        stmt = stmt.where(
            keyset_after_ranked(
                temp_priority,
                ranked_msg.c.last_at,
                Conversation.id,
                _cursor_int(rank),
                _cursor_datetime(ts),
                _cursor_uuid(row_id),
            )
        )

    stmt = stmt.order_by(
        temp_priority.asc(),
        desc(ranked_msg.c.last_at),
        # Without this the order of rows tied on (priority, last_at) is
        # whatever the planner feels like, and a cursor built from one of
        # them would skip or repeat its twins on the next page.
        desc(Conversation.id),
    )
    # One extra row is how we know whether a next page exists without a
    # second COUNT over the same join.
    stmt = stmt.limit(limit + 1)

    rows = (await db.execute(stmt)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    out: list[InboxItem] = []
    for conv, lead, msg, last_at in rows:
        out.append(
            InboxItem(
                conversation_id=conv.id,
                channel=conv.channel,
                status=conv.status,
                lead_id=lead.id,
                lead_name=lead.full_name,
                lead_temperature=lead.temperature,
                last_message_at=last_at,
                last_message_role=msg.role,
                last_message_preview=(msg.content or "")[:200],
                unread=(msg.role == MessageRole.LEAD and conv.status == ConversationStatus.ACTIVE),
                assigned_user_id=conv.assigned_user_id,
            )
        )

    if not paginated and not cursor:
        return out

    next_cursor: str | None = None
    if has_more and rows:
        conv, lead, _msg, last_at = rows[-1]
        next_cursor = encode_cursor(
            agency_id,
            [_temperature_rank(lead.temperature), _isoformat(last_at), str(conv.id)],
        )
    return InboxPage(items=out, next_cursor=next_cursor)


# Mirror of the `case()` above. Both express the same thing — hot first, then
# warm, then the rest — and the cursor is worthless if they disagree, so the
# test pins them together.
TEMPERATURE_RANK = {LeadTemperature.HOT: 0, LeadTemperature.WARM: 1}


def _temperature_rank(temperature: LeadTemperature) -> int:
    return TEMPERATURE_RANK.get(temperature, 2)


def _isoformat(value: datetime) -> str:
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


# ---------------------------------------------------------------------------
# Conversation detail
# ---------------------------------------------------------------------------

class MessageOut(BaseModel):
    id: uuid.UUID
    role: MessageRole
    content: str
    created_at: datetime


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def conversation_messages(
    agency_id: uuid.UUID,
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[MessageOut]:
    stmt = (
        select(Message)
        .where(
            Message.agency_id == agency_id,
            Message.conversation_id == conversation_id,
        )
        .order_by(Message.created_at.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [MessageOut(id=m.id, role=m.role, content=m.content, created_at=m.created_at) for m in rows]


# ---------------------------------------------------------------------------
# Warm handoff
# ---------------------------------------------------------------------------

class HandoffBody(BaseModel):
    user_id: uuid.UUID | None = None
    note: str = ""


@router.post("/conversations/{conversation_id}/handoff", status_code=200)
async def warm_handoff(
    agency_id: uuid.UUID,
    conversation_id: uuid.UUID,
    body: HandoffBody,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    conv = await db.get(Conversation, conversation_id)
    if conv is None or conv.agency_id != agency_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation_not_found")

    if body.user_id:
        target = await db.get(User, body.user_id)
        if target is None or target.agency_id != agency_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_user")
        conv.assigned_user_id = target.id
    conv.status = ConversationStatus.HANDED_OFF

    if body.note:
        db.add(
            Message(
                agency_id=agency_id,
                conversation_id=conv.id,
                direction="outbound",  # type: ignore[arg-type]
                role=MessageRole.SYSTEM,
                content=f"[Handoff a humano] {body.note}",
                raw={},
            )
        )

    await db.commit()
    return {"status": "handed_off"}


# ---------------------------------------------------------------------------
# Resume: give the conversation back to the AI after a handoff
# ---------------------------------------------------------------------------

@router.post("/conversations/{conversation_id}/resume", status_code=200)
async def resume_ai(
    agency_id: uuid.UUID,
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    conv = await db.get(Conversation, conversation_id)
    if conv is None or conv.agency_id != agency_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation_not_found")

    conv.status = ConversationStatus.ACTIVE
    # (13) The handoff is over, so its context has to go with it. Nothing used
    # to read these keys, so leaving them behind was invisible; now that
    # `conversation_detail` reports them, a resumed thread would keep showing
    # the reason it was escalated for *last* time — and a later manual handoff
    # (which carries no reason of its own) would present it as the reason for
    # that one. Whole-dict reassignment: SQLAlchemy does not track in-place
    # JSON edits, and everything else in `extra` (the private note, the raw
    # call records) stays.
    conv.extra = {
        k: v
        for k, v in (conv.extra or {}).items()
        if k not in (HANDOFF_REASON_KEY, HANDOFF_URGENCY_KEY, HANDOFF_AT_KEY)
    }
    await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Per-conversation note: correcting the AI on THIS thread only.
#
# `agency.settings["ai_instructions"]` is the standing rule for every
# conversation; this is the "en esta conversación no le ofrezcas el ático,
# ya está reservado" that the owner needs *now* and that would be wrong as a
# permanent instruction. It lands in `conversation.extra` and the agent reads
# it on its next turn (agent/runner.py `read_conversation_note`).
#
# One note per conversation: writing again replaces it, an empty body clears
# it. A list would need its own UI to review and delete entries, and a stale
# instruction nobody remembers writing is worse than no instruction.
# ---------------------------------------------------------------------------

class ConversationNoteBody(BaseModel):
    text: str = Field(default="", max_length=MAX_CONVERSATION_NOTE_CHARS)


class ConversationNoteOut(BaseModel):
    text: str
    updated_at: datetime | None = None


def _read_note(conv: Conversation) -> ConversationNoteOut:
    blob = (conv.extra or {}).get(CONVERSATION_NOTE_EXTRA_KEY)
    if isinstance(blob, str):
        return ConversationNoteOut(text=blob)
    if isinstance(blob, dict):
        raw_at = blob.get("updated_at")
        return ConversationNoteOut(
            text=str(blob.get("text") or ""), updated_at=_parse_dt(raw_at)
        )
    return ConversationNoteOut(text="")


async def _owned_conversation(
    db: AsyncSession, agency_id: uuid.UUID, conversation_id: uuid.UUID
) -> Conversation:
    conv = await db.get(Conversation, conversation_id)
    if conv is None or conv.agency_id != agency_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation_not_found")
    return conv


@router.get("/conversations/{conversation_id}/note", response_model=ConversationNoteOut)
async def get_conversation_note(
    agency_id: uuid.UUID,
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ConversationNoteOut:
    return _read_note(await _owned_conversation(db, agency_id, conversation_id))


@router.post("/conversations/{conversation_id}/note", response_model=ConversationNoteOut)
async def set_conversation_note(
    agency_id: uuid.UUID,
    conversation_id: uuid.UUID,
    body: ConversationNoteBody,
    db: AsyncSession = Depends(get_db),
) -> ConversationNoteOut:
    conv = await _owned_conversation(db, agency_id, conversation_id)

    # Collapsed to a single line: it is rendered inline in the system prompt,
    # where stray newlines only help someone trying to fake a new section.
    text = " ".join(str(body.text or "").split())[:MAX_CONVERSATION_NOTE_CHARS]
    extra = {**(conv.extra or {})}
    if text:
        extra[CONVERSATION_NOTE_EXTRA_KEY] = {
            "text": text,
            "updated_at": datetime.now(UTC).isoformat(),
        }
    else:
        extra.pop(CONVERSATION_NOTE_EXTRA_KEY, None)
    # Whole-dict reassignment: SQLAlchemy does not track in-place JSON edits.
    conv.extra = extra

    await db.commit()
    log.info(
        "conversation.note_set",
        agency=str(agency_id),
        conversation=str(conversation_id),
        cleared=not text,
    )
    return _read_note(conv)


# ---------------------------------------------------------------------------
# Conversation state: who is answering, and through which channel.
#
# The mobile screen used to work this out by downloading the whole inbox and
# searching it for one row. That cost ~200 rows with their message previews on
# every open and every pull-to-refresh, and it could not answer at all past
# the inbox ceiling — an agency with more open conversations than that simply
# got "no hemos podido confirmar quién atiende" forever.
#
# It also could not tell a deleted (or another tenant's) conversation from an
# empty one: `/messages` filters by agency and answers `[]` either way, so the
# app showed "esta conversación todavía no tiene historial" for a thread that
# does not exist. This route 404s, which is the difference.
# ---------------------------------------------------------------------------

class ConversationOut(BaseModel):
    id: uuid.UUID
    status: ConversationStatus
    channel: ConversationChannel
    lead_id: uuid.UUID
    assigned_user_id: uuid.UUID | None
    # (13) Why the thread was handed over. `escalate_to_human` (agent/tools.py)
    # and the guardrail (agent/runner.py) both store it in `conv.extra`, and it
    # already travels to the agency in the handoff push and the email subject
    # (services/conversation_service.py `build_handoff_summary`) — so the panel
    # showing it adds no exposure the agency does not already have. Without it
    # the screen says "derivada a humano" and the person picking up the phone
    # has to re-read the whole thread to find out why.
    #
    # All three describe the handoff the conversation is *in*. Null on a thread
    # the AI is attending, even when the keys are still in `extra` — see
    # `_AI_ATTENDED_STATUSES`.
    handoff_reason: str | None = None
    handoff_urgency: str | None = None
    handoff_at: datetime | None = None


# The urgency the agent may set. Anything else is provider/model noise and is
# reported as unknown rather than passed through to a UI that colour-codes it.
_HANDOFF_URGENCIES = ("low", "normal", "high")

# Statuses in which the AI, not a person, is the one attending the thread.
# `POST /resume` now deletes the handoff keys, but every conversation resumed
# before that shipped still carries them, so the read side refuses them too:
# a handoff reason is only true while the conversation is actually in a
# handoff (or finished as one, where it reads as history). SNOOZED is listed
# with ACTIVE because nothing sets it today — the day something does, it will
# be the AI waiting its turn, not a human holding the thread.
_AI_ATTENDED_STATUSES = (ConversationStatus.ACTIVE, ConversationStatus.SNOOZED)


@router.get("/conversations/{conversation_id}", response_model=ConversationOut)
async def conversation_detail(
    agency_id: uuid.UUID,
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ConversationOut:
    """State of one conversation. 404 when it is not this agency's."""
    conv = await _owned_conversation(db, agency_id, conversation_id)
    # Whitelisted key by key, never `conv.extra` wholesale: the same dict also
    # holds the private conversation note and the raw voice-call records, and
    # neither belongs in the payload of a "who is answering" call.
    extra = {} if conv.status in _AI_ATTENDED_STATUSES else (conv.extra or {})
    raw_reason = extra.get(HANDOFF_REASON_KEY)
    reason = " ".join(str(raw_reason).split()) if raw_reason else ""
    raw_urgency = extra.get(HANDOFF_URGENCY_KEY)
    return ConversationOut(
        id=conv.id,
        status=conv.status,
        channel=conv.channel,
        lead_id=conv.lead_id,
        assigned_user_id=conv.assigned_user_id,
        # The tool stores "" when the model escalated without giving a reason;
        # the panel wants null there so it can render its own fallback copy
        # instead of an empty quote bubble.
        handoff_reason=reason or None,
        handoff_urgency=raw_urgency if raw_urgency in _HANDOFF_URGENCIES else None,
        handoff_at=_parse_dt(extra.get(HANDOFF_AT_KEY)),
    )


# ---------------------------------------------------------------------------
# Manual reply: a human answers the lead from the dashboard.
#
# The message goes out through the *real* channel — nothing is stored as
# "sent" unless the provider accepted it. Channels we cannot actually write
# to (web chat, SMS, a finished voice call) answer 501 instead of pretending.
# ---------------------------------------------------------------------------

# Channels a human can write to from the dashboard today.
REPLYABLE_CHANNELS = {ConversationChannel.WHATSAPP, ConversationChannel.EMAIL}

# Both provider clients (channels/whatsapp/client.py, channels/email/client.py)
# log a warning and return this id instead of failing when their credentials
# are missing — a dev convenience that, taken at face value here, would store
# a message the client never received. A returned id equal to this marker
# means *nothing left the building*. Pinned by a test against the real
# clients so it cannot drift silently.
NOT_SENT_MARKER = "dev-stub"

# Meta only accepts free-form text within 24h of the customer's last message
# (error 131047, legacy 470). It is not a transient failure — the only way
# through is an approved template, which selai does not send yet — so the UI
# has to say that instead of offering a pointless retry.
_WHATSAPP_WINDOW_ERROR_CODES = {131047, 470}


def _is_whatsapp_window_error(exc: BaseException) -> bool:
    """True when Meta rejected the send because the 24h window is closed."""
    response = getattr(exc, "response", None)
    if response is None:
        return False
    try:
        error = ((response.json() or {}).get("error")) or {}
    except Exception:
        return False
    try:
        if int(error.get("code")) in _WHATSAPP_WINDOW_ERROR_CODES:
            return True
    except (TypeError, ValueError):
        pass
    haystack = f"{error.get('message', '')} {error.get('error_data', '')}".lower()
    return "24 hours" in haystack or "re-engagement" in haystack


class ReplyBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("El mensaje no puede estar vacío.")
        return stripped


class ReplyOut(BaseModel):
    id: uuid.UUID
    role: MessageRole
    content: str
    created_at: datetime
    external_message_id: str | None
    handed_off: bool


def _whatsapp_client_for(agency: Agency | None) -> WhatsAppClient:
    """The agency's own Cloud API credentials when it connected its number
    from the dashboard; otherwise the global env-configured client.

    Built inline from `agency.settings` on purpose: the webhook's own
    helper also has to reason about which number the inbound arrived on,
    which is meaningless for an outbound reply.
    """
    cfg = dict((agency.settings if agency is not None else None) or {})
    access_token = secret_from_settings(cfg, "whatsapp_access_token")
    phone_number_id = cfg.get("whatsapp_phone_number_id")
    if access_token and phone_number_id:
        return WhatsAppClient(
            access_token=str(access_token), phone_number_id=str(phone_number_id)
        )
    return WhatsAppClient()


async def _last_inbound_email(
    db: AsyncSession, agency_id: uuid.UUID, conversation_id: uuid.UUID
) -> Message | None:
    """Most recent inbound email message: subject for `Re: ...` and
    Message-ID/References so the reply lands in the client's thread instead
    of opening a fresh conversation in their mailbox."""
    stmt = (
        select(Message)
        .where(
            Message.agency_id == agency_id,
            Message.conversation_id == conversation_id,
            Message.direction == MessageDirection.INBOUND,
        )
        .order_by(desc(Message.created_at))
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


@router.post("/conversations/{conversation_id}/reply", response_model=ReplyOut)
async def manual_reply(
    agency_id: uuid.UUID,
    conversation_id: uuid.UUID,
    body: ReplyBody,
    db: AsyncSession = Depends(get_db),
) -> ReplyOut:
    conv = await db.get(Conversation, conversation_id)
    if conv is None or conv.agency_id != agency_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation_not_found")

    if conv.channel not in REPLYABLE_CHANNELS:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "channel_not_supported",
        )

    lead = await db.get(Lead, conv.lead_id)
    if lead is None or lead.agency_id != agency_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lead_not_found")

    agency = await db.get(Agency, agency_id)

    # Send first, persist after: a message only shows as sent if it left.
    external_id: str | None = None
    if conv.channel is ConversationChannel.WHATSAPP:
        destination = lead.whatsapp_wa_id or lead.phone_e164
        if not destination:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "lead_without_whatsapp")
        try:
            external_id = await _whatsapp_client_for(agency).send_text(
                destination, body.text
            )
        except Exception as exc:
            log.exception("manual_reply.whatsapp_failed", conversation=str(conv.id))
            if _is_whatsapp_window_error(exc):
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "whatsapp_window_expired"
                ) from exc
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "send_failed") from exc
    else:
        if not lead.email:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "lead_without_email")
        last_inbound = await _last_inbound_email(db, agency_id, conv.id)
        previous = (
            str((last_inbound.raw or {}).get("subject") or "") if last_inbound else ""
        )
        base_subject = previous or (
            f"Tu consulta a {agency.name}" if agency is not None else "Tu consulta"
        )
        subject = base_subject if base_subject.lower().startswith("re:") else f"Re: {base_subject}"

        # Same threading and Reply-To as the AI's own replies: without the
        # Message-ID the client's mailbox opens a brand-new thread, and
        # without the Reply-To their answer dies in the platform mailbox
        # instead of coming back to this conversation.
        in_reply_to = (
            (last_inbound.external_message_id or "").strip() or None
            if last_inbound
            else None
        )
        text_part, html_part = email_parts(body.text)
        try:
            external_id = await ResendClient().send(
                to=lead.email,
                subject=subject[:255],
                text=text_part,
                html=html_part,
                in_reply_to=in_reply_to,
                references=[in_reply_to] if in_reply_to else None,
                reply_to=leads_email_for(agency.slug) if agency is not None else None,
            )
        except Exception as exc:
            log.exception("manual_reply.email_failed", conversation=str(conv.id))
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "send_failed") from exc

    # The provider answered "fine" without being configured: the agency never
    # connected this channel, so the message does not exist for the client and
    # must not exist for us either.
    if external_id == NOT_SENT_MARKER:
        log.warning(
            "manual_reply.channel_not_connected",
            agency=str(agency_id),
            channel=conv.channel.value,
        )
        raise HTTPException(status.HTTP_409_CONFLICT, "channel_not_connected")

    # Answering by hand *is* taking over: pause the AI if it hadn't been.
    if conv.status is not ConversationStatus.HANDED_OFF:
        conv.status = ConversationStatus.HANDED_OFF

    now = datetime.now(UTC)
    message_id = uuid.uuid4()
    message = Message(
        id=message_id,
        agency_id=agency_id,
        conversation_id=conv.id,
        direction=MessageDirection.OUTBOUND,
        role=MessageRole.AGENT_HUMAN,
        content=body.text,
        raw={"sent_from": "dashboard", "channel": conv.channel.value},
        external_message_id=external_id or None,
        created_at=now,
    )
    db.add(message)
    await db.commit()

    return ReplyOut(
        id=message_id,
        role=MessageRole.AGENT_HUMAN,
        content=body.text,
        created_at=now,
        external_message_id=external_id or None,
        handed_off=True,
    )


# ---------------------------------------------------------------------------
# Lead listing + detail
# ---------------------------------------------------------------------------

class LeadOut(BaseModel):
    id: uuid.UUID
    full_name: str | None
    phone_e164: str | None
    email: str | None
    status: LeadStatus
    temperature: LeadTemperature
    score: int
    language: str
    budget_min: int | None
    budget_max: int | None
    operation: str | None
    created_at: datetime
    next_action_at: datetime | None = None
    next_action_note: str | None = None


LEAD_NEXT_ACTION_EXTRA_KEY = "crm_next_action"
MAX_NEXT_ACTION_NOTE_CHARS = 500


def _lead_out(lead: Lead) -> LeadOut:
    """Public lead shape plus the operational reminder stored in `extra`.

    `extra` is intentionally schemaless and contains historical/provider data,
    so a malformed old value must degrade to an empty reminder rather than
    taking the whole leads page down.
    """
    at: datetime | None = None
    note: str | None = None
    blob = (getattr(lead, "extra", None) or {}).get(LEAD_NEXT_ACTION_EXTRA_KEY)
    if isinstance(blob, dict):
        raw_at = blob.get("at")
        if raw_at:
            try:
                parsed = datetime.fromisoformat(str(raw_at))
                if parsed.utcoffset() is not None:
                    at = parsed
            except (TypeError, ValueError):
                pass
        raw_note = blob.get("note")
        if isinstance(raw_note, str) and raw_note.strip():
            note = raw_note.strip()[:MAX_NEXT_ACTION_NOTE_CHARS]

    if at is None or note is None:
        at = None
        note = None

    return LeadOut.model_validate(lead, from_attributes=True).model_copy(
        update={"next_action_at": at, "next_action_note": note}
    )


class LeadUpdateBody(BaseModel):
    """Editable CRM fields. Omitted means unchanged; explicit null clears."""

    status: LeadStatus | None = None
    next_action_at: datetime | None = None
    next_action_note: str | None = Field(default=None, max_length=MAX_NEXT_ACTION_NOTE_CHARS)

    @field_validator("status")
    @classmethod
    def _status_cannot_be_cleared(cls, value: LeadStatus | None) -> LeadStatus:
        if value is None:
            raise ValueError("El estado no se puede borrar.")
        return value

    @field_validator("next_action_at")
    @classmethod
    def _next_action_must_be_future_and_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.utcoffset() is None:
            raise ValueError("La próxima acción debe incluir zona horaria.")
        normalized = value.astimezone(UTC)
        if normalized <= datetime.now(UTC):
            raise ValueError("La próxima acción debe estar en el futuro.")
        return normalized

    @field_validator("next_action_note")
    @classmethod
    def _normalize_next_action_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @model_validator(mode="after")
    def _at_least_one_change(self) -> LeadUpdateBody:
        if not self.model_fields_set:
            raise ValueError("Indica al menos un cambio.")
        return self


class LeadsPage(BaseModel):
    items: list[LeadOut]
    next_cursor: str | None
    # Every lead matching the filters, not just the ones on this page. The
    # export button needs it to be able to say how many rows it is about to
    # download instead of implying it downloads whatever is on screen.
    total: int


def _lead_filters(agency_id: uuid.UUID, temperature, status, office=None) -> list:
    """The WHERE of the leads listing, shared by the page, the count and the
    export so the three can never disagree about what "todos" means.

    `office` joins that rule rather than being bolted onto the listing alone:
    an export that ignored the filter would hand an office manager the whole
    agency's contacts under a file named after their branch."""
    conditions = [Lead.agency_id == agency_id]
    if temperature:
        conditions.append(Lead.temperature == temperature)
    if status:
        conditions.append(Lead.status == status)
    if office is not None:
        conditions.append(lead_in_office(agency_id, office))
    return conditions


def _leads_filtered(agency_id: uuid.UUID, temperature, status, office=None):
    return select(Lead).where(*_lead_filters(agency_id, temperature, status, office))


@router.get("/leads", response_model=None)
async def list_leads(
    agency_id: uuid.UUID,
    temperature: LeadTemperature | None = None,
    status: LeadStatus | None = None,
    office: uuid.UUID | None = Query(
        None, description="Solo los leads de esa oficina. Sin el parámetro, todos."
    ),
    limit: int = Query(100, ge=1, le=500),
    cursor: str | None = Query(None, max_length=512),
    paginated: bool = Query(
        False,
        description="Devuelve {items, next_cursor, total} en vez de la lista pelada.",
    ),
    db: AsyncSession = Depends(get_db),
) -> list[LeadOut] | LeadsPage:
    """Leads of the agency, newest first.

    Same two shapes as `/inbox`: bare array by default (what the mobile app
    and the old panel expect), `{items, next_cursor, total}` when the caller
    asks for `paginated=true` or sends a `cursor`.

    `office` is optional; without it nothing about this query changes.
    """
    await ensure_office_of_agency(db, agency_id, office)

    stmt = _leads_filtered(agency_id, temperature, status, office)
    if cursor:
        ts, row_id = decode_cursor(cursor, agency_id, expected_len=2)
        stmt = stmt.where(
            keyset_before(Lead.created_at, Lead.id, _cursor_datetime(ts), _cursor_uuid(row_id))
        )

    stmt = stmt.order_by(Lead.created_at.desc(), desc(Lead.id)).limit(limit + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [_lead_out(row) for row in rows]

    if not paginated and not cursor:
        return items

    total = (
        await db.execute(
            select(func.count())
            .select_from(Lead)
            .where(*_lead_filters(agency_id, temperature, status, office))
        )
    ).scalar_one()

    next_cursor: str | None = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor(agency_id, [_isoformat(last.created_at), str(last.id)])
    return LeadsPage(items=items, next_cursor=next_cursor, total=int(total))


# ---------------------------------------------------------------------------
# CSV export — all of them, not the first page
#
# The panel used to build this file in the browser from the leads the page had
# already loaded, which meant "Exportar CSV" produced exactly 100 rows and
# called them "todos los leads". Streaming it from here fixes both halves: the
# server walks the whole table with the same keyset cursor as the listing (so
# no OFFSET drift, no ceiling), and the browser never holds more than one
# chunk.
#
# Path note: `/leads.csv`, not `/leads/export.csv`, so it can never be
# shadowed by `/leads/{lead_id}` no matter which of the two routes ends up
# declared first.
# ---------------------------------------------------------------------------

CSV_HEADER = [
    "nombre",
    "telefono",
    "email",
    "temperatura",
    "estado",
    "presupuesto_min",
    "presupuesto_max",
    "operacion",
    "fuente",
    "idioma",
    "puntuacion",
    "fecha_alta",
]

# Spain opens CSVs in Excel with `;` as the separator and expects a BOM, or
# every accented name arrives mojibake. Same two choices the old client-side
# export made — the file the agency gets must not change shape just because
# it is now produced on the server.
CSV_SEPARATOR = ";"
CSV_NEWLINE = "\r\n"
UTF8_BOM = "﻿"

# How many leads we hold in memory at once while streaming. Small enough that
# an agency with 200k leads costs a few hundred KB, large enough that we are
# not doing a round trip per row.
EXPORT_CHUNK = 500


def _csv_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    # Quoting does not stop spreadsheet formula execution. Prefix any cell
    # whose first meaningful character can start a formula; Excel hides the
    # apostrophe and preserves values such as international phone numbers as
    # text. Leading whitespace is included because spreadsheet parsers may
    # trim it before deciding whether to evaluate the cell.
    meaningful = text.lstrip(" \t\r\n")
    if meaningful.startswith(("=", "+", "-", "@")):
        text = "'" + text
    if any(ch in text for ch in ('"', CSV_SEPARATOR, "\n", "\r")):
        return '"' + text.replace('"', '""') + '"'
    return text


def lead_csv_row(lead: Lead, tz: tzinfo = UTC) -> list[str]:
    """One CSV line's cells, in `CSV_HEADER` order.

    `fuente` is `source_channel` — the real thing. The browser-side export
    used to put the operation (venta/alquiler) in that column with a comment
    admitting it was an approximation; the column exists on the model and was
    simply never read.

    The date is rendered in the agency's timezone for the same reason the
    agenda is (see api/visits.py): `created_at` is an absolute instant, and
    printing it in UTC files a lead captured at 00:30 in Madrid under the
    previous day.
    """
    created = getattr(lead, "created_at", None)
    if created is not None:
        created = (created if created.tzinfo else created.replace(tzinfo=UTC)).astimezone(tz)
    return [
        _csv_cell(lead.full_name),
        _csv_cell(lead.phone_e164),
        _csv_cell(lead.email),
        _csv_cell(getattr(lead.temperature, "value", lead.temperature)),
        _csv_cell(getattr(lead.status, "value", lead.status)),
        _csv_cell(lead.budget_min),
        _csv_cell(lead.budget_max),
        _csv_cell(lead.operation),
        _csv_cell(getattr(lead, "source_channel", None)),
        _csv_cell(lead.language),
        _csv_cell(lead.score),
        _csv_cell(created.date().isoformat() if created is not None else ""),
    ]


async def iter_export_chunks(
    agency_id: uuid.UUID, temperature, status, office=None
) -> AsyncIterator[list[Lead]]:
    """Every matching lead, in keyset chunks, on a session of our own.

    Not the request's session on purpose: FastAPI closes a `yield` dependency
    before the streaming body is consumed, so reading from `get_db` here
    would hand us a closed session halfway through the file.
    """
    from app.db.session import session_scope

    last: tuple[datetime, uuid.UUID] | None = None
    async with session_scope() as db:
        while True:
            stmt = _leads_filtered(agency_id, temperature, status, office)
            if last is not None:
                stmt = stmt.where(keyset_before(Lead.created_at, Lead.id, last[0], last[1]))
            stmt = stmt.order_by(Lead.created_at.desc(), desc(Lead.id)).limit(EXPORT_CHUNK)
            rows = list((await db.execute(stmt)).scalars().all())
            if not rows:
                return
            yield rows
            if len(rows) < EXPORT_CHUNK:
                return
            last = (rows[-1].created_at, rows[-1].id)


async def render_leads_csv(
    chunks: Callable[[], AsyncIterator[list[Lead]]],
    tz: tzinfo = UTC,
) -> AsyncIterator[bytes]:
    """Turn the chunk stream into CSV bytes, header first."""
    yield (UTF8_BOM + CSV_SEPARATOR.join(CSV_HEADER) + CSV_NEWLINE).encode("utf-8")
    async for rows in chunks():
        block = "".join(
            CSV_SEPARATOR.join(lead_csv_row(lead, tz)) + CSV_NEWLINE for lead in rows
        )
        yield block.encode("utf-8")


@router.get("/leads.csv")
async def export_leads_csv(
    agency_id: uuid.UUID,
    temperature: LeadTemperature | None = None,
    status: LeadStatus | None = None,
    office: uuid.UUID | None = Query(
        None, description="Solo los leads de esa oficina. Sin el parámetro, todos."
    ),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Every lead matching the filters, as a CSV Excel opens correctly.

    `db` is used here and only here — to read the agency's timezone and to
    validate `office` before the response starts. The rows themselves are
    read on a session of the generator's own, because this one is closed
    before the body streams.
    """
    await ensure_office_of_agency(db, agency_id, office)

    agency = await db.get(Agency, agency_id)
    tz, _name = resolve_agency_timezone(agency.timezone if agency is not None else None)

    def _chunks() -> AsyncIterator[list[Lead]]:
        # The office argument is only passed when there is one. Not
        # cosmetic: the export without an office has to keep making the exact
        # call it made before offices existed — same arity, same order —
        # because that is the promise to every caller that never uses them.
        if office is None:
            return iter_export_chunks(agency_id, temperature, status)
        return iter_export_chunks(agency_id, temperature, status, office)

    filename = f"leads-{datetime.now(tz).date().isoformat()}.csv"
    return StreamingResponse(
        render_leads_csv(_chunks, tz),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/leads/{lead_id}", response_model=LeadOut)
async def lead_detail(
    agency_id: uuid.UUID,
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> LeadOut:
    """One lead by id — same shape as the /leads items.

    Without it the only way to open a lead card was to download the whole
    listing and pick the row out of it, so every lead past the page limit
    was unreachable (the mobile app said "lead no encontrado" from the 201st
    lead on).
    """
    lead = await db.get(Lead, lead_id)
    if lead is None or lead.agency_id != agency_id:
        # Same 404 for "does not exist" and "belongs to another agency": the
        # difference between the two answers would confirm that a given id is
        # a real lead of someone else's.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lead_not_found")
    return _lead_out(lead)


@router.patch("/leads/{lead_id}", response_model=LeadOut)
async def update_lead(
    agency_id: uuid.UUID,
    lead_id: uuid.UUID,
    body: LeadUpdateBody,
    db: AsyncSession = Depends(get_db),
) -> LeadOut:
    """Move a lead through the pipeline and maintain its next human action."""
    lead = await db.get(Lead, lead_id)
    if lead is None or lead.agency_id != agency_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lead_not_found")

    changed = body.model_fields_set
    if "status" in changed:
        # Explicit null is rejected by the validator, so this is a real enum.
        assert body.status is not None
        lead.status = body.status

    if changed & {"next_action_at", "next_action_note"}:
        extra = {**(lead.extra or {})}
        current = extra.get(LEAD_NEXT_ACTION_EXTRA_KEY)
        action = dict(current) if isinstance(current, dict) else {}

        if "next_action_at" in changed:
            if body.next_action_at is None:
                action.pop("at", None)
            else:
                action["at"] = body.next_action_at.isoformat()
        if "next_action_note" in changed:
            if body.next_action_note is None:
                action.pop("note", None)
            else:
                action["note"] = body.next_action_note

        # A date without a concrete action (or vice versa) is not executable.
        # Validate after merging so clients may patch only one half of an
        # already complete reminder.
        if bool(action.get("at")) != bool(action.get("note")):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "incomplete_next_action")

        if action.get("at") and action.get("note"):
            action["updated_at"] = datetime.now(UTC).isoformat()
            extra[LEAD_NEXT_ACTION_EXTRA_KEY] = action
        else:
            extra.pop(LEAD_NEXT_ACTION_EXTRA_KEY, None)
        # Whole-dict reassignment is required for SQLAlchemy JSON tracking.
        lead.extra = extra

    await db.commit()
    log.info(
        "lead.crm_updated",
        agency=str(agency_id),
        lead=str(lead_id),
        fields=sorted(changed),
    )
    return _lead_out(lead)


@router.get("/conversations/{conversation_id}/lead", response_model=LeadOut)
async def conversation_lead(
    agency_id: uuid.UUID,
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> LeadOut:
    """The lead behind a conversation — same shape as the /leads items."""
    conv = await db.get(Conversation, conversation_id)
    if conv is None or conv.agency_id != agency_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation_not_found")

    lead = await db.get(Lead, conv.lead_id)
    if lead is None or lead.agency_id != agency_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "lead_not_found")
    return _lead_out(lead)


# ---------------------------------------------------------------------------
# Calls: the /calls page — one row per finished voice call.
#
# There is no dedicated `calls` table: each voice call lives inside its
# lead's VOICE conversation, and the Vapi end-of-call webhook appends a
# per-call record to `conversation.extra["calls"]` (see app/api/voice.py).
# Conversations from before that change only carry `lead.extra["last_call_*"]`
# — we fall back to that so historical calls still show up.
# ---------------------------------------------------------------------------

class CallOut(BaseModel):
    conversation_id: uuid.UUID
    lead_id: uuid.UUID
    lead_name: str | None
    lead_phone: str | None
    call_id: str | None
    ended_at: datetime | None
    duration_seconds: float | None
    recording_url: str | None
    transcript_summary: str | None


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


# Every field below comes straight from the Vapi webhook payload (see
# app/api/voice.py, which stores `msg.get(...)` verbatim). One provider
# hiccup — a duration sent as text, a summary sent as an object — used to
# raise a ValidationError and take the whole /calls page down with it, so
# each value is coerced defensively and degrades to None on its own.


def _as_text(value) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, int | float):
        return str(value)
    return None


def _as_seconds(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", "."))
        except ValueError:
            return None
    return None


def _as_recording_url(value) -> str | None:
    """Only real links: this lands in an `<audio src>` in the dashboard."""
    url = _as_text(value)
    if url is None:
        return None
    return url if url.startswith(("http://", "https://")) else None


def _call_entries(conv, lead) -> list[CallOut]:
    """Flatten one voice conversation into CallOut rows. Pure — unit-tested."""
    records = (conv.extra or {}).get("calls")
    out: list[CallOut] = []
    if isinstance(records, list) and records:
        for rec in records:
            if not isinstance(rec, dict):
                continue
            out.append(
                CallOut(
                    conversation_id=conv.id,
                    lead_id=lead.id,
                    lead_name=lead.full_name,
                    lead_phone=lead.phone_e164,
                    call_id=_as_text(rec.get("call_id")),
                    ended_at=_parse_dt(rec.get("ended_at")),
                    duration_seconds=_as_seconds(rec.get("duration_seconds")),
                    recording_url=_as_recording_url(rec.get("recording_url")),
                    transcript_summary=_as_text(rec.get("summary")),
                )
            )
        return out

    # Legacy fallback: pre-`conversation.extra` calls only left a trace on the lead.
    lead_extra = lead.extra or {}
    if any(
        lead_extra.get(k)
        for k in ("last_call_summary", "last_call_recording_url", "last_call_at")
    ):
        out.append(
            CallOut(
                conversation_id=conv.id,
                lead_id=lead.id,
                lead_name=lead.full_name,
                lead_phone=lead.phone_e164,
                call_id=_as_text(conv.external_thread_id),
                ended_at=_parse_dt(lead_extra.get("last_call_at")),
                duration_seconds=_as_seconds(lead_extra.get("last_call_duration_sec")),
                recording_url=_as_recording_url(lead_extra.get("last_call_recording_url")),
                transcript_summary=_as_text(lead_extra.get("last_call_summary")),
            )
        )
    return out


@router.get("/calls", response_model=list[CallOut])
async def list_calls(
    agency_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[CallOut]:
    stmt = (
        select(Conversation, Lead)
        # The lead is scoped too: a conversation of ours pointing at another
        # tenant's lead is corruption, and it must not surface that person's
        # name and phone in our listing.
        .join(Lead, (Lead.id == Conversation.lead_id) & (Lead.agency_id == agency_id))
        .where(
            Conversation.agency_id == agency_id,
            Conversation.channel == ConversationChannel.VOICE,
        )
        .order_by(desc(Conversation.updated_at))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()

    calls: list[CallOut] = []
    for conv, lead in rows:
        calls.extend(_call_entries(conv, lead))

    epoch = datetime.min.replace(tzinfo=UTC)

    def _key(c: CallOut) -> datetime:
        dt = c.ended_at
        if dt is None:
            return epoch
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

    calls.sort(key=_key, reverse=True)
    return calls[:limit]


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------

class Kpis(BaseModel):
    range_days: int
    leads_total: int
    leads_new: int
    leads_qualified: int
    visits_scheduled: int
    visits_completed: int
    handoffs: int
    avg_first_response_seconds: float | None
    agent_input_tokens: int
    agent_output_tokens: int


@router.get("/kpis", response_model=Kpis)
async def kpis(
    agency_id: uuid.UUID,
    range_days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
) -> Kpis:
    since = datetime.now(UTC) - timedelta(days=range_days)

    leads_total = (await db.execute(
        select(func.count()).select_from(Lead).where(Lead.agency_id == agency_id)
    )).scalar_one()

    leads_new = (await db.execute(
        select(func.count()).select_from(Lead).where(
            Lead.agency_id == agency_id, Lead.created_at >= since
        )
    )).scalar_one()

    leads_qualified = (await db.execute(
        select(func.count()).select_from(Lead).where(
            Lead.agency_id == agency_id,
            Lead.created_at >= since,
            Lead.status.in_([
                LeadStatus.QUALIFIED, LeadStatus.VISIT_SCHEDULED,
                LeadStatus.VISITED, LeadStatus.NEGOTIATING, LeadStatus.WON,
            ]),
        )
    )).scalar_one()

    visits_scheduled = (await db.execute(
        select(func.count()).select_from(Visit).where(
            Visit.agency_id == agency_id, Visit.created_at >= since
        )
    )).scalar_one()

    visits_completed = (await db.execute(
        select(func.count()).select_from(Visit).where(
            Visit.agency_id == agency_id,
            Visit.status == VisitStatus.COMPLETED,
            Visit.scheduled_at >= since,
        )
    )).scalar_one()

    handoffs = (await db.execute(
        select(func.count()).select_from(Conversation).where(
            Conversation.agency_id == agency_id,
            Conversation.status == ConversationStatus.HANDED_OFF,
            Conversation.updated_at >= since,
        )
    )).scalar_one()

    # Average first-response time: time from first inbound to first outbound per conversation.
    inbound_subq = (
        select(
            Message.conversation_id,
            func.min(Message.created_at).label("first_in"),
        )
        .where(Message.agency_id == agency_id, Message.role == MessageRole.LEAD)
        .group_by(Message.conversation_id)
        .subquery()
    )
    outbound_subq = (
        select(
            Message.conversation_id,
            func.min(Message.created_at).label("first_out"),
        )
        .where(
            Message.agency_id == agency_id,
            Message.role.in_([MessageRole.AGENT_AI, MessageRole.AGENT_HUMAN]),
        )
        .group_by(Message.conversation_id)
        .subquery()
    )
    avg_resp = (
        await db.execute(
            select(
                func.avg(
                    func.extract("epoch", outbound_subq.c.first_out - inbound_subq.c.first_in)
                )
            ).select_from(inbound_subq).join(
                outbound_subq, outbound_subq.c.conversation_id == inbound_subq.c.conversation_id
            )
        )
    ).scalar_one()

    tokens = (await db.execute(
        select(
            func.coalesce(func.sum(AgentRun.input_tokens), 0),
            func.coalesce(func.sum(AgentRun.output_tokens), 0),
        ).where(AgentRun.agency_id == agency_id, AgentRun.created_at >= since)
    )).one()

    return Kpis(
        range_days=range_days,
        leads_total=leads_total,
        leads_new=leads_new,
        leads_qualified=leads_qualified,
        visits_scheduled=visits_scheduled,
        visits_completed=visits_completed,
        handoffs=handoffs,
        avg_first_response_seconds=float(avg_resp) if avg_resp is not None else None,
        agent_input_tokens=int(tokens[0]),
        agent_output_tokens=int(tokens[1]),
    )


# ---------------------------------------------------------------------------
# Funnel (conversion by stage)
# ---------------------------------------------------------------------------

class FunnelStage(BaseModel):
    status: LeadStatus
    count: int
    conversion_from_previous: float | None


@router.get("/funnel", response_model=list[FunnelStage])
async def funnel(
    agency_id: uuid.UUID,
    range_days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
) -> list[FunnelStage]:
    since = datetime.now(UTC) - timedelta(days=range_days)
    order = [
        LeadStatus.NEW,
        LeadStatus.ENGAGED,
        LeadStatus.QUALIFIED,
        LeadStatus.VISIT_SCHEDULED,
        LeadStatus.VISITED,
        LeadStatus.NEGOTIATING,
        LeadStatus.WON,
    ]
    # Count leads that have *reached* each stage or beyond. Statuses are
    # ordered, so "reached X" = status in order[order.index(X):].
    counts: list[int] = []
    for idx in range(len(order)):
        reached = order[idx:]
        n = (
            await db.execute(
                select(func.count())
                .select_from(Lead)
                .where(
                    Lead.agency_id == agency_id,
                    Lead.created_at >= since,
                    Lead.status.in_(reached),
                )
            )
        ).scalar_one()
        counts.append(n)

    stages: list[FunnelStage] = []
    for i, stage in enumerate(order):
        prev = counts[i - 1] if i > 0 else None
        ratio = (counts[i] / prev * 100.0) if prev and prev > 0 else None
        stages.append(
            FunnelStage(
                status=stage,
                count=counts[i],
                conversion_from_previous=round(ratio, 1) if ratio is not None else None,
            )
        )
    return stages


# Keep imports tidy
_ = (Property,)
