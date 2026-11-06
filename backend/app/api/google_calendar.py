"""Google Calendar connection — the dashboard's "Calendario" page.

Three endpoints, two routers, because they do not share an authentication
model:

- `router` (agency-scoped, `AgencyMemberDep`)
    GET    /api/v1/agencies/{id}/calendar            → status, never the token
    GET    /api/v1/agencies/{id}/calendar/authorize  → the consent screen URL
    DELETE /api/v1/agencies/{id}/calendar            → disconnect

- `callback_router` (public)
    GET    /api/v1/calendar/callback                 → Google redirects here

The callback **cannot** be authenticated: Google redirects a browser to it
with nothing but `code` and `state`, and the dashboard's Clerk session does
not travel to the API host. What identifies the tenant is the signed `state`
(see `app.services.google_calendar.parse_state`) — a forged or expired one is
a 400 and nothing is written.

Connecting and disconnecting are owner/admin actions: the calendar belongs to
the agency, not to whoever happens to be logged in, and a commercial agent
must not be able to unplug the office agenda.

The refresh token never leaves the backend. `GET` returns `connected`, the
calendar id, the account email (learnt from the first event Google echoes
back) and — when the customer revoked the grant from their Google account —
`revoked: true`, so the panel asks for a reconnection instead of pretending
everything is fine.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AgencyMemberDep, OwnerOnlyDep
from app.core.logging import get_logger
from app.db.models import Agency
from app.db.session import get_db
from app.services import google_calendar as gcal

router = APIRouter(
    prefix="/api/v1/agencies/{agency_id}/calendar",
    tags=["calendar"],
    dependencies=[AgencyMemberDep],
)
callback_router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])
log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CalendarStatusOut(BaseModel):
    connected: bool
    # False when this deployment has no GOOGLE_OAUTH_CLIENT_ID/SECRET. The
    # panel hides the button instead of offering one that always fails.
    configured: bool
    calendar_id: str | None = None
    # Only known once Google has echoed it back on the first created event —
    # `calendar.events` alone does not authorise asking who the user is.
    account_email: str | None = None
    connected_at: datetime | None = None
    # The customer revoked our access from their Google account. Not the same
    # as "never connected": it needs an explicit "vuelve a conectarlo".
    revoked: bool = False
    revoked_at: datetime | None = None
    # Still connected, but the last visit could not be copied. Our own short
    # code (`http_403`, `google_unreachable`, …), never Google's prose — the
    # panel turns it into one sentence in Spanish.
    sync_error: str | None = None
    sync_error_at: datetime | None = None


class AuthorizeOut(BaseModel):
    authorization_url: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_agency(db: AsyncSession, agency_id: uuid.UUID) -> Agency:
    agency = await db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agency_not_found")
    return agency


def _parse_iso(value: object) -> datetime | None:
    """Settings JSON is hand-editable; a bad timestamp must not 500 the page."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _status(agency: Agency) -> CalendarStatusOut:
    info = gcal.connection_info(agency)
    return CalendarStatusOut(
        connected=bool(info["connected"]),
        configured=bool(info["configured"]),
        calendar_id=info["calendar_id"],
        account_email=info["account_email"],
        connected_at=_parse_iso(info["connected_at"]),
        revoked=bool(info["revoked"]),
        revoked_at=_parse_iso(info["revoked_at"]),
        sync_error=info["sync_error"],
        sync_error_at=_parse_iso(info["sync_error_at"]),
    )


def _page(title: str, message: str) -> HTMLResponse:
    """The tiny page Google's redirect lands on.

    The flow runs in a popup opened by the dashboard, so the honest end of it
    is "cierra esta ventana" plus an automatic `window.close()`. Rendering a
    page here (instead of redirecting to the panel) means the API never has
    to know the dashboard's public URL, and there is no attacker-supplied
    redirect target anywhere in the flow.
    """
    return HTMLResponse(
        "<!doctype html>"
        '<html lang="es"><head><meta charset="utf-8">'
        f"<title>{title}</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:0;display:flex;"
        "min-height:100vh;align-items:center;justify-content:center;"
        "background:#f8fafc;color:#0f172a}div{max-width:26rem;padding:2rem;"
        "text-align:center}h1{font-size:1.1rem;margin:0 0 .5rem}"
        "p{margin:0;color:#475569;font-size:.9rem}</style></head>"
        f"<body><div><h1>{title}</h1><p>{message}</p></div>"
        "<script>setTimeout(function(){window.close()},2500)</script>"
        "</body></html>"
    )


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@router.get("", response_model=CalendarStatusOut)
async def calendar_status(
    agency_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> CalendarStatusOut:
    return _status(await _get_agency(db, agency_id))


# ---------------------------------------------------------------------------
# Authorize
# ---------------------------------------------------------------------------


@router.get("/authorize", response_model=AuthorizeOut, dependencies=[OwnerOnlyDep])
async def calendar_authorize(
    agency_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AuthorizeOut:
    """The Google consent URL for *this* agency. Opening it is the client's job.

    Returned as JSON rather than a 302 so the dashboard can open it in a
    popup and keep the panel where it was.
    """
    await _get_agency(db, agency_id)
    try:
        url = gcal.authorization_url(agency_id)
    except gcal.CalendarNotConfiguredError:
        # Not the customer's fault and not a bug: this deployment has no
        # Google credentials. 503 + a code the panel turns into plain Spanish.
        log.warning("calendar.authorize.not_configured", agency=str(agency_id))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "google_calendar_not_configured"
        ) from None
    return AuthorizeOut(authorization_url=url)


# ---------------------------------------------------------------------------
# Callback (public — Google redirects the browser here)
# ---------------------------------------------------------------------------


@callback_router.get("/callback", response_class=HTMLResponse)
async def calendar_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    if error:
        # `access_denied` is the customer pressing "Cancelar". Nothing broke.
        log.info("calendar.callback.denied", error=error)
        return _page(
            "No se ha conectado el calendario",
            "Has cancelado la conexión con Google. Puedes intentarlo de nuevo cuando quieras.",
        )

    agency_id = gcal.parse_state(state)
    if agency_id is None and gcal.state_is_expired(state):
        # Firmado por nosotros, pero viejo: el cliente se dejó la pantalla de
        # Google abierta. No es un ataque y no merece un JSON en crudo dentro
        # de una ventana emergente.
        log.info("calendar.callback.expired_state")
        return _page(
            "La conexión ha caducado",
            "Has tardado demasiado en la pantalla de Google. Vuelve al panel y pulsa "
            "«Conectar Google Calendar» otra vez.",
        )
    if agency_id is None or not code:
        # A state we did not sign (or one older than 15 minutes) is the case
        # this signature exists for: without it, anybody could send a code
        # with someone else's agency id and hang their calendar off that
        # tenant. Nothing is written and nothing is looked up.
        log.warning("calendar.callback.bad_state", has_code=bool(code))
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_state")

    agency = await db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid_state")

    try:
        payload = await gcal.exchange_code(code)
    except gcal.CalendarError as exc:
        log.warning("calendar.callback.exchange_failed", agency=str(agency_id), error=str(exc))
        return _page(
            "No se ha podido conectar",
            "Google no ha aceptado la conexión. Vuelve a intentarlo desde el panel.",
        )

    if not gcal.grants_calendar_access(payload):
        # Granular consent: the customer unticked the calendar permission and
        # pressed "Continuar". Google answers with a valid token for whatever
        # was granted; storing it would leave the panel showing "Conectado"
        # over a connection that cannot create a single event.
        log.warning("calendar.callback.scope_not_granted", agency=str(agency_id))
        return _page(
            "Falta el permiso de calendario",
            "Google no nos ha dado permiso para crear eventos. Vuelve a intentarlo desde el "
            "panel y deja marcada la casilla del calendario.",
        )

    refresh_token = str(payload.get("refresh_token") or "")
    if not refresh_token:
        # We ask with `prompt=consent`, so this should not happen. If it does,
        # storing an access token that dies in an hour would be worse than
        # saying so: the panel would show "conectado" over a dead connection.
        log.warning("calendar.callback.no_refresh_token", agency=str(agency_id))
        return _page(
            "No se ha podido conectar",
            "Google no ha devuelto un permiso permanente. Revoca el acceso a selai en tu "
            "cuenta de Google y vuelve a conectarlo.",
        )

    gcal.store_connection(agency, refresh_token=refresh_token)
    await db.commit()
    log.info("calendar.connected", agency=str(agency_id))
    return _page(
        "Calendario conectado",
        # "Cerrar la ventana" sirve cuando esto es una emergente; si el
        # navegador la bloqueó, el cliente llegó aquí en la misma pestaña y lo
        # que tiene que hacer es volver al panel. Vale para los dos casos.
        "Ya puedes cerrar esta ventana y volver al panel: las próximas visitas "
        "aparecerán en tu Google Calendar.",
    )


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------


@router.delete("", dependencies=[OwnerOnlyDep])
async def calendar_disconnect(
    agency_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    """Forget the credentials.

    We do not call Google's `/revoke`: the customer may have granted the same
    Google project other permissions, and a disconnection in our panel must
    mean "stop using it", not "reach into their account". Revoking for real is
    one click in myaccount.google.com and we point them there in the UI.
    """
    agency = await _get_agency(db, agency_id)
    gcal.clear_connection(agency)
    await db.commit()
    log.info("calendar.disconnected", agency=str(agency_id))
    return {"ok": True}
