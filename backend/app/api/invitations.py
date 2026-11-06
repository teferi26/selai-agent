"""Team invitations — the missing half of "hasta 2/5/15 personas de tu equipo".

Three routers, because they answer to three different guards:

* `router` (`/api/v1/agencies/{agency_id}/invitations`) — the owner's side:
  create, list, revoke, resend. Agency membership router-wide; the mutating
  routes additionally demand the owner role.
* `members_router` (`/api/v1/agencies/{agency_id}/members`) — who is already
  in, plus the seat count the panel renders as "3 de 5 plazas".
* `public_router` (`/api/v1/invitations`) — the invitee's side. The preview is
  public (the token *is* the credential, and the page has to be readable
  before the person has an account); accepting demands a Clerk session.

The seat limit sold on the pricing page is enforced here, in two places and
on purpose:

* when inviting, counting members **plus pending invitations** — otherwise a
  Starter (2 seats) sends five invitations and ends up with six people;
* when accepting, because plans get downgraded between the invitation and the
  click.

Acceptance is the delicate part. A Clerk session whose email does not match
the invitation is refused with an explanation instead of being quietly
onboarded into a brand-new agency, which is what the product did before this
module existed.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AgencyMemberDep, AuthContext, OwnerOnlyDep, require_auth
from app.core.logging import get_logger
from app.db.models import Agency, Invitation, User, UserRole
from app.db.models.invitation import (
    INVITATION_TTL_DAYS,
    default_expiry,
    generate_token,
    normalize_email,
)
from app.db.session import get_db
from app.services.emails import build_invitation_email_html, invitation_url, send_email
from app.services.plan_limits import ensure_can_add, usage_for

log = get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/agencies/{agency_id}/invitations",
    tags=["invitations"],
    dependencies=[AgencyMemberDep],
)
members_router = APIRouter(
    prefix="/api/v1/agencies/{agency_id}/members",
    tags=["invitations"],
    dependencies=[AgencyMemberDep],
)
public_router = APIRouter(prefix="/api/v1/invitations", tags=["invitations"])

# Owners invite; owners cannot mint an `admin` (that is ours, for support).
InvitableRole = Literal["agent", "owner"]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class InvitationIn(BaseModel):
    email: EmailStr
    role: InvitableRole = "agent"


class InvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    status: str
    created_at: datetime | None
    expires_at: datetime
    invited_by: str | None
    # (n26) Solo en las respuestas de crear y reenviar (rutas de owner), nunca
    # en el listado: el panel decía "invitación enviada" tanto si el correo
    # salió como si el proveedor lo rechazó, y el compañero se quedaba
    # esperando un email que no existía. Con esto el panel puede decir la
    # verdad y ofrecer el enlace para pasarlo por WhatsApp.
    email_sent: bool | None = None
    accept_url: str | None = None


class MemberOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    # False until the person has logged in through Clerk at least once, which
    # is the honest answer to "did the invitation actually land?".
    has_signed_in: bool


class Seats(BaseModel):
    """What the panel renders as "3 de 5 plazas"."""

    members: int
    pending: int
    used: int
    limit: int | None  # None = no metered plan (dev/seeded agencies)


class MembersOut(BaseModel):
    members: list[MemberOut]
    seats: Seats


class InvitationPreview(BaseModel):
    """What the /invitacion/{token} landing page shows before there is an
    account. Deliberately thin: agency, who invited, which email to use."""

    agency_name: str
    inviter_name: str | None
    email: str
    role: str
    status: str
    expires_at: datetime


class AcceptBody(BaseModel):
    full_name: str | None = Field(default=None, max_length=120)


class AcceptOut(BaseModel):
    agency_id: uuid.UUID
    agency_name: str
    user_id: uuid.UUID
    role: str


# ---------------------------------------------------------------------------
# Seat accounting
# ---------------------------------------------------------------------------


def _pending_filter(agency_id: uuid.UUID, now: datetime):
    return (
        Invitation.agency_id == agency_id,
        Invitation.accepted_at.is_(None),
        Invitation.revoked_at.is_(None),
        Invitation.expires_at > now,
    )


async def _pending_count(
    db: AsyncSession, agency_id: uuid.UUID, *, exclude_id: uuid.UUID | None = None
) -> int:
    """Invitations currently holding a seat.

    `exclude_id` leaves one row out, which is what "resend" needs: the
    invitation being revived must not be counted both as what is already out
    and as what is about to go out.
    """
    stmt = (
        select(func.count())
        .select_from(Invitation)
        .where(*_pending_filter(agency_id, datetime.now(UTC)))
    )
    if exclude_id is not None:
        stmt = stmt.where(Invitation.id != exclude_id)
    return (await db.execute(stmt)).scalar_one()


async def _seats(
    db: AsyncSession, agency_id: uuid.UUID, *, exclude_id: uuid.UUID | None = None
) -> Seats:
    """Members + outstanding invitations against the plan's `max_agents`.

    A pending invitation is a seat already promised: not counting it is how a
    plan of 2 quietly becomes a team of 6.
    """
    usage = await usage_for(db, agency_id, "agents")
    pending = await _pending_count(db, agency_id, exclude_id=exclude_id)
    members = usage.used if usage is not None else await _member_count(db, agency_id)
    limit = usage.limit if usage is not None and usage.limit >= 0 else None
    return Seats(members=members, pending=pending, used=members + pending, limit=limit)


async def _ensure_seat_for_one_more(
    db: AsyncSession, agency_id: uuid.UUID, *, exclude_id: uuid.UUID | None = None
) -> None:
    """402 unless one more outstanding seat fits in the plan.

    The gate for *handing out* a seat, as opposed to `ensure_can_add`, which
    only knows about people who already joined. Both run: `ensure_can_add`
    also refuses a canceled/unpaid/paused subscription, and that has to block
    an invitation as much as it blocks anything else.

    `exclude_id` is the invitation about to be (re)sent, so it is not counted
    twice — see `_pending_count`.
    """
    await ensure_can_add(db, agency_id, "agents")
    seats = await _seats(db, agency_id, exclude_id=exclude_id)
    if seats.limit is not None and seats.used >= seats.limit:
        raise _seat_limit_error(seats)


async def _member_count(db: AsyncSession, agency_id: uuid.UUID) -> int:
    return (
        await db.execute(
            select(func.count()).select_from(User).where(User.agency_id == agency_id)
        )
    ).scalar_one()


def _seat_limit_error(seats: Seats) -> HTTPException:
    """Same 402 shape `plan_limits` raises, so the panel's existing upgrade
    prompt recognises it without a second code path."""
    return HTTPException(
        status.HTTP_402_PAYMENT_REQUIRED,
        detail={
            "code": "plan_limit_reached",
            "resource": "agents",
            "used": seats.used,
            "limit": seats.limit,
        },
    )


def _out(
    inv: Invitation,
    inviter_name: str | None = None,
    *,
    email_sent: bool | None = None,
    accept_url: str | None = None,
) -> InvitationOut:
    return InvitationOut(
        id=inv.id,
        email=inv.email,
        role=inv.role.value if isinstance(inv.role, UserRole) else str(inv.role),
        status=inv.status,
        created_at=getattr(inv, "created_at", None),
        expires_at=inv.expires_at,
        invited_by=inviter_name,
        email_sent=email_sent,
        # El token ES la credencial: solo viaja en la respuesta de quien acaba
        # de crear o reenviar la invitación (owner), jamás en el listado, que
        # cualquier miembro del equipo puede leer.
        accept_url=accept_url,
    )


async def _inviter_names(
    db: AsyncSession, agency_id: uuid.UUID, invitations: list[Invitation]
) -> dict:
    """Names of whoever sent each invitation. Scoped by `agency_id` as well as
    by id: the ids come from rows of this agency, but a query that does not
    say so is one refactor away from being a leak."""
    ids = {inv.invited_by_user_id for inv in invitations if inv.invited_by_user_id}
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(User.id, User.full_name).where(
                User.agency_id == agency_id, User.id.in_(ids)
            )
        )
    ).all()
    return {row[0]: row[1] for row in rows}


async def _send_invitation_email(
    db: AsyncSession, inv: Invitation, agency: Agency | None, inviter: User | None
) -> bool:
    """Best-effort: a mail-provider hiccup must not lose an invitation that is
    already stored — the owner can resend from the team screen."""
    try:
        return await send_email(
            to=inv.email,
            subject=f"Únete al equipo de {agency.name if agency else 'tu agencia'} en selai",
            html=build_invitation_email_html(
                agency.name if agency else "tu agencia",
                inviter.full_name if inviter else None,
                inv.token,
                role=inv.role.value if isinstance(inv.role, UserRole) else str(inv.role),
                expires_days=INVITATION_TTL_DAYS,
            ),
        )
    except Exception:
        log.exception("invitations.email_failed", invitation=str(inv.id))
        return False


# ---------------------------------------------------------------------------
# Owner side
# ---------------------------------------------------------------------------


@router.post("", response_model=InvitationOut, status_code=201, dependencies=[OwnerOnlyDep])
async def create_invitation(
    agency_id: uuid.UUID,
    body: InvitationIn,
    auth: AuthContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> InvitationOut:
    email = normalize_email(body.email)

    # The plan limit that is actually sold: members plus the invitations
    # already out.
    await _ensure_seat_for_one_more(db, agency_id)

    existing_user = (
        await db.execute(select(User).where(func.lower(User.email) == email))
    ).scalar_one_or_none()
    if existing_user is not None:
        # `users.email` is globally unique, so someone else's agency holding
        # this address is a hard stop, not a retryable error.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": (
                    "already_member"
                    if existing_user.agency_id == agency_id
                    else "email_already_registered"
                )
            },
        )

    now = datetime.now(UTC)
    duplicate = (
        await db.execute(
            select(Invitation).where(*_pending_filter(agency_id, now), Invitation.email == email)
        )
    ).scalars().first()
    if duplicate is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"code": "invitation_already_pending"}
        )

    inv = Invitation(
        agency_id=agency_id,
        email=email,
        role=UserRole(body.role),
        token=generate_token(),
        invited_by_user_id=auth.user.id if auth.user else None,
        expires_at=default_expiry(),
    )
    db.add(inv)
    await db.commit()

    agency = await db.get(Agency, agency_id)
    sent = await _send_invitation_email(db, inv, agency, auth.user)
    return _out(
        inv,
        auth.user.full_name if auth.user else None,
        email_sent=sent,
        accept_url=invitation_url(inv.token),
    )


@router.get("", response_model=list[InvitationOut])
async def list_invitations(
    agency_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> list[InvitationOut]:
    """Every invitation ever sent by this agency, newest first, with a derived
    status. Accepted and revoked ones stay visible: "who did we invite?" is a
    question with a history."""
    rows = (
        await db.execute(
            select(Invitation)
            .where(Invitation.agency_id == agency_id)
            .order_by(Invitation.created_at.desc())
        )
    ).scalars().all()
    names = await _inviter_names(db, agency_id, list(rows))
    return [_out(inv, names.get(inv.invited_by_user_id)) for inv in rows]


async def _owned_invitation(
    db: AsyncSession, agency_id: uuid.UUID, invitation_id: uuid.UUID
) -> Invitation:
    """One invitation of *this* agency, or 404.

    The `agency_id` is in the WHERE clause rather than in an `if` after a
    primary-key fetch: the row of another tenant must never be loaded in the
    first place, so no later edit can accidentally use it.
    """
    inv = (
        await db.execute(
            select(Invitation).where(
                Invitation.id == invitation_id, Invitation.agency_id == agency_id
            )
        )
    ).scalar_one_or_none()
    if inv is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": "invitation_not_found"}
        )
    return inv


@router.delete("/{invitation_id}", status_code=204, dependencies=[OwnerOnlyDep])
async def revoke_invitation(
    agency_id: uuid.UUID, invitation_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> None:
    inv = await _owned_invitation(db, agency_id, invitation_id)
    if inv.accepted_at is not None:
        # Revoking is about a link, not about a colleague: removing someone who
        # already joined is a different (not yet built) action.
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"code": "invitation_already_accepted"}
        )
    if inv.revoked_at is None:
        inv.revoked_at = datetime.now(UTC)
        await db.commit()


@router.post("/{invitation_id}/resend", response_model=InvitationOut, dependencies=[OwnerOnlyDep])
async def resend_invitation(
    agency_id: uuid.UUID,
    invitation_id: uuid.UUID,
    auth: AuthContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> InvitationOut:
    """Same token, fresh deadline. Resending is what an owner does when the
    email was lost or the week ran out, so the expiry is pushed forward;
    minting a new token would only break the link the person may still have.

    Pushing the deadline forward on an **expired** invitation brings a seat
    back from the dead, and an expired invitation stopped counting the moment
    it died. Without the seat check below, a Starter (2) whose invitation
    lapsed could invite someone else and then resend the old one: three seats
    promised on a plan of two, discovered by the third person when the accept
    fails, not by the owner when they press the button.
    """
    inv = await _owned_invitation(db, agency_id, invitation_id)
    if inv.accepted_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"code": "invitation_already_accepted"}
        )
    if inv.revoked_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "invitation_revoked"})

    await _ensure_seat_for_one_more(db, agency_id, exclude_id=inv.id)

    inv.expires_at = default_expiry()
    await db.commit()

    agency = await db.get(Agency, agency_id)
    inviter = (
        await db.get(User, inv.invited_by_user_id) if inv.invited_by_user_id else None
    ) or auth.user
    sent = await _send_invitation_email(db, inv, agency, inviter)
    return _out(
        inv,
        inviter.full_name if inviter else None,
        email_sent=sent,
        accept_url=invitation_url(inv.token),
    )


@members_router.get("", response_model=MembersOut)
async def list_members(agency_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> MembersOut:
    rows = (
        await db.execute(
            select(User).where(User.agency_id == agency_id).order_by(User.created_at.asc())
        )
    ).scalars().all()
    return MembersOut(
        members=[
            MemberOut(
                id=u.id,
                email=u.email,
                full_name=u.full_name,
                role=u.role.value if isinstance(u.role, UserRole) else str(u.role),
                is_active=bool(u.is_active),
                has_signed_in=bool(u.clerk_user_id),
            )
            for u in rows
        ],
        seats=await _seats(db, agency_id),
    )


# ---------------------------------------------------------------------------
# Invitee side
# ---------------------------------------------------------------------------


async def _by_token(db: AsyncSession, token: str) -> Invitation:
    inv = (
        await db.execute(select(Invitation).where(Invitation.token == token))
    ).scalar_one_or_none()
    if inv is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"code": "invitation_not_found"}
        )
    return inv


@public_router.get("/{token}", response_model=InvitationPreview)
async def preview_invitation(token: str, db: AsyncSession = Depends(get_db)) -> InvitationPreview:
    """Public on purpose: the landing page has to say who invited whom
    *before* the person has an account. The token is 256 bits of randomness,
    so holding it is already proof of having received the email, and nothing
    here is worth more than what that email already said.

    Once the invitation is dead (used, revoked or lapsed) that stops being
    true. The token does not expire from the inboxes it reached — the
    invitee's, and anyone's they forwarded it to — while a route that keeps
    answering "maria@x.es was invited to Inmobiliaria Sol by Ana" is a
    permanent, unauthenticated lookup of three personal data points that buys
    the reader nothing: the only thing left to say is that the link is dead.
    """
    inv = await _by_token(db, token)
    role = inv.role.value if isinstance(inv.role, UserRole) else str(inv.role)
    if inv.status != "pending":
        return InvitationPreview(
            agency_name="",
            inviter_name=None,
            email="",
            role=role,
            status=inv.status,
            expires_at=inv.expires_at,
        )

    agency = await db.get(Agency, inv.agency_id)
    inviter = (
        await db.get(User, inv.invited_by_user_id) if inv.invited_by_user_id else None
    )
    # Belt and braces on a public route: never name someone from another
    # tenant, however the row got there.
    if inviter is not None and inviter.agency_id != inv.agency_id:
        inviter = None
    return InvitationPreview(
        agency_name=agency.name if agency else "",
        inviter_name=inviter.full_name if inviter else None,
        email=inv.email,
        role=role,
        status=inv.status,
        expires_at=inv.expires_at,
    )


@public_router.post("/{token}/accept", response_model=AcceptOut, status_code=201)
async def accept_invitation(
    token: str,
    body: AcceptBody | None = None,
    auth: AuthContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> AcceptOut:
    """Exchange an invitation for membership of the agency that sent it.

    Every refusal gets its own status so the panel can say something true:
    404 unknown link, 403 revoked, 409 already used, 410 expired, 403 wrong
    email, 402 the agency ran out of seats.
    """
    inv = await _by_token(db, token)

    if inv.revoked_at is not None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail={"code": "invitation_revoked"}
        )
    if inv.accepted_at is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"code": "invitation_already_accepted"}
        )
    if inv.is_expired():
        raise HTTPException(
            status.HTTP_410_GONE, detail={"code": "invitation_expired"}
        )

    session_email = normalize_email(auth.email or "")
    if session_email != inv.email:
        # The seat is nominative: accepting with another account would put an
        # address the owner never invited inside their agency's data.
        log.warning(
            "invitations.email_mismatch",
            invitation=str(inv.id),
            session_email=session_email or None,
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={
                "code": "invitation_email_mismatch",
                "invited_email": inv.email,
                "session_email": session_email or None,
            },
        )

    agency = await db.get(Agency, inv.agency_id)
    if agency is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "agency_missing"})

    # Already a user? Either this is a double click (same agency: idempotent)
    # or the person already belongs somewhere else and cannot be in two.
    existing = auth.user or (
        await db.execute(select(User).where(func.lower(User.email) == inv.email))
    ).scalar_one_or_none()
    if existing is not None:
        if existing.agency_id != inv.agency_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail={"code": "already_in_another_agency"}
            )
        if existing.clerk_user_id is None and auth.clerk_user_id:
            existing.clerk_user_id = auth.clerk_user_id
        inv.accepted_at = datetime.now(UTC)
        await db.commit()
        return AcceptOut(
            agency_id=agency.id,
            agency_name=agency.name,
            user_id=existing.id,
            role=existing.role.value if isinstance(existing.role, UserRole) else str(existing.role),
        )

    # Seats are re-checked here: plans get downgraded between the invitation
    # and the click, and the promise is "hasta N personas", not "N at the
    # moment somebody pressed invitar".
    await ensure_can_add(db, inv.agency_id, "agents")

    full_name = (body.full_name if body else None) or inv.email.split("@")[0]
    user = User(
        agency_id=inv.agency_id,
        email=inv.email,
        full_name=full_name.strip()[:120],
        role=inv.role if isinstance(inv.role, UserRole) else UserRole(inv.role),
        clerk_user_id=auth.clerk_user_id or None,
    )
    db.add(user)
    inv.accepted_at = datetime.now(UTC)
    try:
        await db.commit()
    except IntegrityError:
        # Two tabs, one invitation: the unique email index is the arbiter.
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail={"code": "invitation_already_accepted"}
        ) from None

    log.info(
        "invitations.accepted", invitation=str(inv.id), agency=str(inv.agency_id)
    )
    return AcceptOut(
        agency_id=agency.id,
        agency_name=agency.name,
        user_id=user.id,
        role=user.role.value if isinstance(user.role, UserRole) else str(user.role),
    )
