"""In-app support form — POST /api/v1/support.

The panel (botón de ayuda y pantallas de error) and the mobile app send
whatever the user typed; it arrives in `INTERNAL_EMAIL` as an email carrying
all the context the client could resolve.

Three decisions worth keeping:

- `require_auth`, **not** `AgencyMemberDep`. This form has to work precisely
  when something is broken — including a user whose agency does not load, or
  who has no agency yet. Requiring agency membership would leave it useless
  in the scenario it exists for. Everything about the agency is therefore
  best-effort: the ticket goes out with whatever we could resolve.
- It never answers 5xx because of the email. If Resend is down, the ticket is
  written to the log at error level and the user still gets `{"ok": true}`:
  they have done their part, and telling them to try again only doubles the
  noise into a mailbox that is not receiving anyway.
- The email is internal and transactional: no List-Unsubscribe header, and it
  does not go through the agency's email preferences (`should_send`). Nobody
  unsubscribes from their own support ticket. That decision is recorded once,
  in `EMAIL_CATEGORY_BY_KIND["support_ticket"] = None`, and read from there.
"""
from __future__ import annotations

import time
from collections import OrderedDict, deque
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, require_auth
from app.core.logging import get_logger
from app.db.models import Agency
from app.db.session import get_db
from app.services.emails import (
    INTERNAL_EMAIL,
    build_support_ticket_email_html,
    category_for,
    send_email,
)
from app.services.plan_limits import get_plan_state

router = APIRouter(prefix="/api/v1/support", tags=["support"])
log = get_logger(__name__)

# Name of this email in `EMAIL_CATEGORY_BY_KIND`, which is the one place that
# says whether a product email is opt-out-able. Read it, never hard-code.
SUPPORT_EMAIL_KIND = "support_ticket"


# ---------------------------------------------------------------------------
# Per-user rate limit
# ---------------------------------------------------------------------------
# The endpoint is authenticated, so this is not about abuse: it is about a
# client retry loop (an error screen that re-submits on every render) turning
# one bad afternoon into a thousand identical emails. Five tickets in ten
# minutes is far more than any human writes and far less than a loop sends.
#
# Same shape and same limitation as `_session_rate_limited` in
# app/api/web_chat.py: in-memory and per-process, so the real ceiling is
# `SUPPORT_MAX_PER_WINDOW × workers`. That is fine here — the goal is a cheap
# floor under the flood, not an exact quota.
SUPPORT_WINDOW_SECONDS = 10 * 60
SUPPORT_MAX_PER_WINDOW = 5
# The key is a user id, not something the client picks, so the dict cannot be
# flooded on purpose. The cap is still there because the process is long-lived
# and every user who ever wrote a ticket would otherwise stay in memory.
_MAX_TRACKED_USERS = 10_000

_user_hits: OrderedDict[str, deque[float]] = OrderedDict()


def _user_rate_limited(key: str, *, now: float | None = None) -> bool:
    """Record a hit for `key`; True when it just exceeded its budget."""
    now = time.time() if now is None else now
    cutoff = now - SUPPORT_WINDOW_SECONDS

    window = _user_hits.get(key)
    if window is None:
        window = _user_hits[key] = deque()
    _user_hits.move_to_end(key)
    while len(_user_hits) > _MAX_TRACKED_USERS:
        _user_hits.popitem(last=False)
    while window and window[0] < cutoff:
        window.popleft()
    if len(window) >= SUPPORT_MAX_PER_WINDOW:
        return True
    window.append(now)
    return False


def _rate_limit_key(auth: AuthContext) -> str:
    """Who is being counted. The DB row first (stable across Clerk sessions),
    then the Clerk id, then the email. In dev mode without an X-Dev-Email
    header everyone shares the `dev-user` bucket; that is a local-only quirk,
    not a production one."""
    if auth.user is not None:
        return f"user:{auth.user.id}"
    if auth.clerk_user_id:
        return f"clerk:{auth.clerk_user_id}"
    return f"email:{auth.email or 'anonymous'}"


class SupportTicketIn(BaseModel):
    """The body the panel and the app send.

    `contact_email` is a plain string on purpose: a strict email validator
    would reject a slightly mistyped address and lose the whole ticket, when
    the worst case is a human squinting at it. `screen`, `app_version` and
    `error_ref` are whatever the client knows — the route, the Next.js digest,
    the Sentry id — and all of them are optional because a user who hits the
    button from a blank screen still deserves to be heard.

    Every string arrives trimmed, and a blank optional field means the same as
    a missing one, so neither client has to normalise anything before posting.
    Unknown fields are ignored: an app version that starts sending one more
    piece of context must not start getting 422s from an older backend.
    """

    message: str = Field(min_length=5, max_length=4000)
    surface: Literal["web", "mobile"]
    screen: str | None = Field(default=None, max_length=200)
    app_version: str | None = Field(default=None, max_length=40)
    error_ref: str | None = Field(default=None, max_length=200)
    contact_email: str | None = Field(default=None, max_length=200)

    @field_validator("message", mode="before")
    @classmethod
    def _trim_message(cls, value: object) -> object:
        """Trim *before* the length check, which cuts both ways: a message
        pasted with the newline the textarea added no longer loses its last
        allowed character to a 422, and five spaces stops being a valid
        ticket — five of those fill the inbox without saying anything.
        Non-strings pass through untouched so the type error stays a 422."""
        return value.strip() if isinstance(value, str) else value

    @field_validator("screen", "app_version", "error_ref", "contact_email", mode="before")
    @classmethod
    def _blank_is_absent(cls, value: object) -> object:
        """Clients send "" far more often than null for "I have nothing here".
        Both mean the same, and the email should say "(no indicada)" rather
        than show an empty cell that reads like data we lost."""
        if isinstance(value, str):
            return value.strip() or None
        return value


@router.post("")
async def create_support_ticket(
    body: SupportTicketIn,
    auth: AuthContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    key = _rate_limit_key(auth)
    if _user_rate_limited(key):
        log.warning("support.rate_limited", key=key, surface=body.surface)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Has enviado varios mensajes seguidos. Espera unos minutos y, si el "
            f"problema sigue, escríbenos directamente a {INTERNAL_EMAIL}.",
        )

    user = auth.user
    user_email = user.email if user is not None else auth.email
    agency: Agency | None = None
    plan_name: str | None = None
    if user is not None:
        # Best effort by design: a user writing *because* their agency does not
        # load must not get an error from the form that is supposed to rescue
        # them. Whatever we fail to resolve simply travels as "(sin agencia)".
        try:
            agency = await db.get(Agency, user.agency_id)
            if agency is not None:
                _, plan = await get_plan_state(db, agency.id)
                plan_name = plan.name if plan is not None else None
        except Exception:
            log.exception("support.context_unresolved", key=key)

    subject, html = build_support_ticket_email_html(
        message=body.message,
        surface=body.surface,
        user_name=user.full_name if user is not None else None,
        user_email=user_email,
        contact_email=body.contact_email,
        agency_name=agency.name if agency is not None else None,
        agency_slug=agency.slug if agency is not None else None,
        plan_name=plan_name,
        screen=body.screen,
        app_version=body.app_version,
        error_ref=body.error_ref,
    )

    delivered = False
    try:
        delivered = await send_email(
            INTERNAL_EMAIL, subject, html, category=category_for(SUPPORT_EMAIL_KIND)
        )
    except Exception:
        # `send_email` promises never to raise; if that promise ever breaks it
        # must not become a 500 on this endpoint of all endpoints.
        log.exception("support.email_error", key=key)

    if not delivered:
        # The log is now the only copy of the ticket, so it carries the text
        # (truncated) instead of just "it failed" — otherwise the user wrote
        # into a void. It is their own message, not lead data.
        log.error(
            "support.email_failed",
            key=key,
            email=user_email,
            contact_email=body.contact_email,
            surface=body.surface,
            screen=body.screen,
            message=body.message[:500],
        )
    else:
        log.info("support.ticket_sent", key=key, surface=body.surface, screen=body.screen)

    return {"ok": True}
