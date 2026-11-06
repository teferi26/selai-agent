"""Google Calendar for an agency: OAuth 2.0 web-server flow + events.insert.

The web sells "sincronizamos las visitas con tu Google Calendar" and until
now nothing here could obtain a token: `User.google_refresh_token` existed as
a column and no code path ever wrote it. This module implements the real
flow, at **agency** level:

- one connection per agency (stored in `agency.settings`, no migration),
  because that is what the customer buys and what the panel manages. The
  legacy per-user helper below is kept only so `app/agent/tools.py` keeps
  importing something that works.

Official contracts used (verified against Google's docs, do not "improve"
these strings):

- Consent screen  `GET  https://accounts.google.com/o/oauth2/v2/auth`
  with `client_id`, `redirect_uri`, `response_type=code`, `scope`,
  `access_type=offline` + `prompt=consent` (the only combination that
  guarantees a `refresh_token`), `include_granted_scopes=true`, `state`.
- Token endpoint  `POST https://oauth2.googleapis.com/token`
  `grant_type=authorization_code` (with `code` + `redirect_uri`) to exchange,
  `grant_type=refresh_token` to renew. A refresh token the customer revoked
  from their Google account answers `400 {"error": "invalid_grant"}`.
- Event creation  `POST https://www.googleapis.com/calendar/v3/calendars/{calendarId}/events`
  — `start`/`end` are the only required body fields; `calendar.events` is an
  accepted scope for it.

Why raw httpx instead of `google-api-python-client`: that client is blocking
and would stall the event loop inside a request handler, and it discovers the
API surface over the network at import time. Three endpoints do not justify
either cost, and httpx is what every other integration in this repo uses.

Scope is deliberately the minimum, `calendar.events`: we create events, we
never read the customer's agenda. The side effect is that we cannot ask
Google which account was connected (`calendars.get` and `userinfo` both need
scopes we do not request), so the panel shows the calendar id until the first
event comes back — `events.insert` echoes `organizer.email`, which we then
store. Better an empty field for a day than a scope we do not need.
"""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any
from urllib.parse import quote, urlencode, urlparse
from zoneinfo import ZoneInfo

import httpx

from app.core.logging import get_logger
from app.core.settings import settings
from app.services.secret_store import (
    SecretDecryptionError,
    encrypt_secret_settings,
    secret_from_settings,
)

log = get_logger(__name__)

# --- Google endpoints ------------------------------------------------------
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"

# Only what we need: create events. Never read the agenda.
SCOPES = ("https://www.googleapis.com/auth/calendar.events",)

# Path of our own callback. Must match, byte for byte, one of the "Authorized
# redirect URIs" of the OAuth client in Google Cloud Console.
CALLBACK_PATH = "/api/v1/calendar/callback"

DEFAULT_CALENDAR_ID = "primary"
DEFAULT_TIMEZONE = "Europe/Madrid"

# The state only has to survive the round trip through the consent screen.
STATE_TTL_SECONDS = 15 * 60

HTTP_TIMEOUT = 15.0

# --- Keys inside `agency.settings` ------------------------------------------
# The refresh token is a secret with the same rules as `whatsapp_access_token`
# (see app/api/channels.py): it lives in the JSON blob and never appears in a
# response body. `_EXPOSED_SETTINGS_KEYS` in app/api/agency_settings.py is a
# whitelist, so it cannot leak through that endpoint either.
KEY_REFRESH_TOKEN = "google_calendar_refresh_token"
KEY_CALENDAR_ID = "google_calendar_id"
KEY_ACCOUNT_EMAIL = "google_calendar_account_email"
KEY_CONNECTED_AT = "google_calendar_connected_at"
# Set when Google told us the grant is dead (the customer revoked it from
# myaccount.google.com). Kept *after* the token is dropped so the panel can
# say "vuelve a conectarlo" instead of silently showing "no conectado".
KEY_REVOKED_AT = "google_calendar_revoked_at"
# Last time a visit could not be copied, and why (our own short code, never
# Google's prose). A connection that answers `403 insufficientPermissions` or
# times out for a week still looks "conectado" from the outside: without this
# the panel promises a mirror that stopped existing and nobody finds out until
# a client is standing at a door nobody wrote down.
KEY_LAST_ERROR = "google_calendar_last_error"
KEY_LAST_ERROR_AT = "google_calendar_last_error_at"

SETTINGS_KEYS = (
    KEY_REFRESH_TOKEN,
    KEY_CALENDAR_ID,
    KEY_ACCOUNT_EMAIL,
    KEY_CONNECTED_AT,
    KEY_REVOKED_AT,
    KEY_LAST_ERROR,
    KEY_LAST_ERROR_AT,
)


class CalendarError(RuntimeError):
    """Google answered something we cannot act on."""


class CalendarNotConfiguredError(CalendarError):
    """No GOOGLE_OAUTH_CLIENT_ID / SECRET in this deployment."""


class CalendarAccessRevokedError(CalendarError):
    """`invalid_grant`: the refresh token is dead and only the customer can fix it."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def oauth_configured() -> bool:
    """False when the deployment has no Google credentials.

    Repo pattern (same as Resend, Twilio, Vapi): without the key the feature
    announces itself as unavailable and returns a falsy value — it never
    raises into a request that was about to succeed for other reasons.
    """
    return bool(
        settings.google_oauth_client_id
        and settings.google_oauth_client_secret.get_secret_value()
    )


def redirect_uri() -> str:
    """Where Google sends the browser back.

    `GOOGLE_OAUTH_REDIRECT_URI` wins when set (a deployment behind a
    different public hostname than `PUBLIC_BASE_URL`); otherwise it is
    derived, so a normal deployment has one URL to configure instead of two
    that can silently disagree — a mismatch here is `redirect_uri_mismatch`
    on Google's side, after the customer has already typed their password.

    A configured value whose *path* is not our callback is ignored, not
    obeyed. The `.env` and `.env.example` that ship with the repo carry
    `GOOGLE_OAUTH_REDIRECT_URI=http://localhost:8000/oauth/google/callback`,
    a route this app has never served: obeying it sends the customer to type
    their Google password and land on a 404, with no error anywhere on our
    side. Only the host may differ from `PUBLIC_BASE_URL` (a proxy, another
    public hostname); the path has to be the one we answer on.
    """
    explicit = (settings.google_oauth_redirect_uri or "").strip()
    derived = f"{settings.public_base_url.rstrip('/')}{CALLBACK_PATH}"
    if not explicit:
        return derived
    if urlparse(explicit).path.rstrip("/") != CALLBACK_PATH:
        # Loud: the symptom is a 404 (or `redirect_uri_mismatch`) on Google's
        # screen *after* the customer typed their password, and nothing else
        # in our logs would point at the .env line that caused it.
        log.error(
            "gcal.redirect_uri.ignored_wrong_path",
            configured=explicit,
            using=derived,
            expected_path=CALLBACK_PATH,
        )
        return derived
    return explicit


# ---------------------------------------------------------------------------
# `state` — signed, so nobody can hang their calendar off another agency
# ---------------------------------------------------------------------------
#
# The callback is public (Google redirects a browser to it, with no session of
# ours attached), so `state` is the *only* thing that says which tenant the
# code belongs to. Unsigned, anybody could start the flow with someone else's
# agency id in the state and connect their own Google account to that agency —
# or worse, redirect a victim through it. Signed with SECRET_KEY, like the
# per-agency WhatsApp verify token (app/channels/whatsapp/security.py).


def _state_digest(payload: str) -> str:
    key = settings.secret_key.get_secret_value()
    # utf-8, not ascii: the payload we sign is ours, but the one we *verify*
    # arrives from an anonymous query string. `int("١٢٣")` succeeds (Python
    # accepts unicode digits), so an ascii-only encode turned a forged state
    # into an unhandled UnicodeEncodeError — a 500 on a public endpoint.
    return hmac.new(key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def build_state(agency_id: uuid.UUID | str, *, issued_at: int | None = None) -> str:
    """`<agency hex>.<unix seconds>.<hmac>` — no server-side storage."""
    agency_hex = uuid.UUID(str(agency_id)).hex
    issued = int(issued_at if issued_at is not None else time.time())
    payload = f"{agency_hex}.{issued}"
    return f"{payload}.{_state_digest(payload)}"


def _verified_state(state: str | None) -> tuple[uuid.UUID, int] | None:
    """`(agency, issued_at)` when the signature is ours. No clock involved."""
    if not state or not state.isascii():
        return None
    parts = state.split(".")
    if len(parts) != 3:
        return None
    agency_hex, issued_raw, digest = parts
    # `int()` also accepts unicode digits, whitespace, "+12" and "1_2"; the
    # timestamp we mint is plain ASCII digits and nothing else has to parse.
    if not issued_raw.isdigit():
        return None
    try:
        agency_id = uuid.UUID(hex=agency_hex)
        issued = int(issued_raw)
    except (ValueError, AttributeError, TypeError):
        return None

    expected = _state_digest(f"{agency_hex}.{issued_raw}")
    # Both sides are hex: `compare_digest` on str is ASCII-safe here.
    if not hmac.compare_digest(expected, digest):
        return None
    return agency_id, issued


def parse_state(state: str | None, *, now: float | None = None) -> uuid.UUID | None:
    """The agency the state was issued for, or None if it is not usable.

    None covers every failure the same way on purpose — bad shape, forged
    signature, expired — because the caller's answer is identical and telling
    an anonymous caller *which* check failed is free information.
    """
    verified = _verified_state(state)
    if verified is None:
        return None
    agency_id, issued = verified
    current = now if now is not None else time.time()
    if current - issued > STATE_TTL_SECONDS or issued > current + 60:
        return None
    return agency_id


def state_is_expired(state: str | None, *, now: float | None = None) -> bool:
    """Ours, correctly signed, and stale — i.e. the customer took too long.

    Only used to choose the wording of the callback page: a state we signed
    proves this is a real attempt of ours, so "la conexión ha caducado, vuelve
    a intentarlo" is honest and useful. Anything unsigned stays a flat 400 —
    an anonymous caller learns nothing about why.
    """
    verified = _verified_state(state)
    if verified is None:
        return False
    _, issued = verified
    current = now if now is not None else time.time()
    return current - issued > STATE_TTL_SECONDS


def authorization_url(agency_id: uuid.UUID | str, *, state: str | None = None) -> str:
    """The consent screen URL the owner has to open.

    `access_type=offline` + `prompt=consent` together are what guarantee a
    refresh token: without `offline` Google returns none at all, and without
    `consent` it omits it on every authorisation after the first — which is
    exactly the "reconnect after revoking" case, so the reconnection would
    appear to work and leave us with a connection that dies in an hour.
    """
    if not oauth_configured():
        raise CalendarNotConfiguredError("google_oauth_not_configured")
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state or build_state(agency_id),
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


# ---------------------------------------------------------------------------
# Token endpoint
# ---------------------------------------------------------------------------


async def _token_request(data: dict[str, str]) -> dict[str, Any]:
    if not oauth_configured():
        raise CalendarNotConfiguredError("google_oauth_not_configured")
    body = {
        **data,
        "client_id": settings.google_oauth_client_id,
        "client_secret": settings.google_oauth_client_secret.get_secret_value(),
    }
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as http:
            resp = await http.post(TOKEN_ENDPOINT, data=body)
    except Exception as exc:  # noqa: BLE001 — transport, not credentials
        raise CalendarError("google_unreachable") from exc

    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        payload = {}

    if resp.status_code >= 400:
        error = str(payload.get("error") or "")
        if error == "invalid_grant":
            # The only error the customer can fix, and the only one worth
            # persisting: the grant is gone (revoked, or the account was
            # deleted). Retrying will never help.
            raise CalendarAccessRevokedError(error)
        log.warning(
            "gcal.token_request_failed",
            status=resp.status_code,
            error=error or "unknown",
        )
        raise CalendarError(error or f"http_{resp.status_code}")
    return payload


async def exchange_code(code: str) -> dict[str, Any]:
    """Authorization code → tokens. `redirect_uri` must match the one we sent."""
    return await _token_request(
        {
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri(),
        }
    )


def grants_calendar_access(payload: dict[str, Any]) -> bool:
    """Whether the consent Google just described actually lets us write events.

    Google's consent screen has granular permissions: the customer can untick
    the calendar box and press "Continuar". Google still answers with a
    perfectly valid token — for the scopes that *were* granted. Storing it
    leaves the panel saying "Conectado" over a connection that answers
    `403 insufficientPermissions` to every single visit, forever, in silence.

    A response with no `scope` field at all is taken as good: we only refuse
    when Google tells us, explicitly, that our scope is not in there.
    """
    raw = payload.get("scope")
    if not isinstance(raw, str) or not raw.strip():
        return True
    granted = set(raw.split())
    return all(scope in granted for scope in SCOPES)


# Access tokens live one hour; asking for a new one on every single event
# would double the latency of scheduling a visit and hammer Google for
# nothing. Cached by a hash of the refresh token (never the token itself) and
# bounded, because the key is per tenant and this process is long-lived.
_MAX_CACHED_TOKENS = 500
_access_tokens: dict[str, tuple[str, float]] = {}
# Refresh a minute early: a token that expires while in flight is a 401 we
# would then have to retry.
_EXPIRY_MARGIN_SECONDS = 60


def _cache_key(refresh_token: str) -> str:
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()


def reset_token_cache() -> None:
    """Drop every cached access token (tests, and disconnect)."""
    _access_tokens.clear()


async def access_token_for(refresh_token: str, *, force: bool = False) -> str:
    """A usable access token, refreshing through Google only when needed."""
    key = _cache_key(refresh_token)
    cached = _access_tokens.get(key)
    if cached and not force and cached[1] > time.time():
        return cached[0]

    payload = await _token_request(
        {"refresh_token": refresh_token, "grant_type": "refresh_token"}
    )
    token = str(payload.get("access_token") or "")
    if not token:
        raise CalendarError("no_access_token")

    # `or 3600` would be wrong here: 0 is a real answer ("already expired")
    # and must not be read as "no value, assume an hour".
    raw_expiry = payload.get("expires_in")
    try:
        expires_in = 3600 if raw_expiry is None else int(raw_expiry)
    except (TypeError, ValueError):
        expires_in = 3600
    if len(_access_tokens) >= _MAX_CACHED_TOKENS:
        _access_tokens.clear()
    # Floor at 0, never at "a few seconds more": a token Google says is
    # already expired must be refreshed on the next use, not cached because
    # the arithmetic went negative.
    _access_tokens[key] = (token, time.time() + max(expires_in - _EXPIRY_MARGIN_SECONDS, 0))
    return token


def forget_token(refresh_token: str) -> None:
    _access_tokens.pop(_cache_key(refresh_token), None)


# ---------------------------------------------------------------------------
# Calendar API
# ---------------------------------------------------------------------------


async def insert_event(
    *,
    refresh_token: str,
    calendar_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """`events.insert`. Retries once on 401 with a freshly minted token."""
    # Percent-encoded like Google's own clients do: today the id is always
    # "primary", but it comes out of a hand-editable JSON blob and a "/" in
    # it would turn this into a different call of the Calendar API.
    url = f"{CALENDAR_API_BASE}/calendars/{quote(calendar_id, safe='')}/events"

    for attempt in (0, 1):
        token = await access_token_for(refresh_token, force=attempt == 1)
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as http:
                resp = await http.post(
                    url,
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
                )
        except Exception as exc:  # noqa: BLE001
            raise CalendarError("google_unreachable") from exc

        if resp.status_code == 401 and attempt == 0:
            # The cached token expired earlier than advertised, or was
            # revoked mid-flight. One forced refresh, then we believe it.
            forget_token(refresh_token)
            continue
        if resp.status_code >= 400:
            log.warning(
                "gcal.event_insert_failed",
                status=resp.status_code,
                body=resp.text[:300],
            )
            raise CalendarError(f"http_{resp.status_code}")
        return resp.json()

    raise CalendarError("unauthorized")


# ---------------------------------------------------------------------------
# Connection state, stored in `agency.settings`
# ---------------------------------------------------------------------------


def _cfg(agency: Any) -> dict:
    return dict(getattr(agency, "settings", None) or {})


def _write(agency: Any, updates: dict, *, drop: tuple[str, ...] = ()) -> None:
    """Reassign the whole dict so SQLAlchemy change tracking notices."""
    blob = {**_cfg(agency), **encrypt_secret_settings(updates)}
    for key in drop:
        blob.pop(key, None)
    agency.settings = blob


def is_connected(agency: Any) -> bool:
    return bool(_cfg(agency).get(KEY_REFRESH_TOKEN))


def store_connection(
    agency: Any,
    *,
    refresh_token: str,
    calendar_id: str = DEFAULT_CALENDAR_ID,
    account_email: str | None = None,
) -> None:
    updates = {
        KEY_REFRESH_TOKEN: refresh_token,
        KEY_CALENDAR_ID: calendar_id or DEFAULT_CALENDAR_ID,
        KEY_CONNECTED_AT: datetime.now(UTC).isoformat(),
    }
    # A successful connection clears the revocation notice and the failure
    # notice; leaving either would make the panel warn about a grant that
    # works. The stored account email goes too: a reconnection may well be a
    # *different* Google account (that is usually why people reconnect), and
    # the panel would otherwise keep naming the old one over a calendar that
    # is no longer the one receiving the visits. It is learnt again from the
    # first event Google echoes back.
    dropped = [KEY_REVOKED_AT, KEY_LAST_ERROR, KEY_LAST_ERROR_AT]
    if account_email:
        updates[KEY_ACCOUNT_EMAIL] = account_email
    else:
        # `_write` applies `drop` *after* the updates, so this must not be in
        # the list when the caller did give us an address.
        dropped.append(KEY_ACCOUNT_EMAIL)
    _write(agency, updates, drop=tuple(dropped))


def clear_connection(agency: Any, *, revoked: bool = False) -> None:
    """Drop the credentials. `revoked=True` leaves the "reconnect" notice.

    The account email survives a revocation on purpose: "Google ha revocado
    el acceso de maria@inmosol.example.com" is actionable, "se ha revocado el acceso"
    is not.
    """
    cfg = _cfg(agency)
    try:
        token = secret_from_settings(cfg, KEY_REFRESH_TOKEN)
    except SecretDecryptionError:
        # A damaged envelope cannot correspond to any cache entry. Disconnect
        # must still work so the owner can reconnect and replace it.
        log.error("gcal.credentials_unreadable", agency=str(getattr(agency, "id", "")))
        token = ""
    if token:
        forget_token(token)

    if revoked:
        _write(
            agency,
            {KEY_REVOKED_AT: datetime.now(UTC).isoformat()},
            # The failure notice goes with it: "Google nos ha retirado el
            # acceso" is the whole story, a second warning underneath is noise.
            drop=(KEY_REFRESH_TOKEN, KEY_CONNECTED_AT, KEY_LAST_ERROR, KEY_LAST_ERROR_AT),
        )
        return
    _write(agency, {}, drop=SETTINGS_KEYS)


def record_sync_failure(agency: Any, code: str) -> None:
    """Remember that the last visit could not be copied.

    Only the code is stored (`http_403`, `google_unreachable`, …), never
    Google's own message: it is prose, it changes, and it can carry the
    calendar's contents. Truncated because part of it (`error` in Google's
    token response) comes from outside and `agency.settings` is not a place
    to accumulate unbounded strings.
    """
    _write(
        agency,
        {KEY_LAST_ERROR: code[:80], KEY_LAST_ERROR_AT: datetime.now(UTC).isoformat()},
    )


def clear_sync_failure(agency: Any) -> None:
    """A visit went through: drop the warning, but only if there was one.

    Guarded so the ordinary path does not dirty `agency.settings` (and force
    an UPDATE) on every single visit.
    """
    cfg = _cfg(agency)
    if cfg.get(KEY_LAST_ERROR) or cfg.get(KEY_LAST_ERROR_AT):
        _write(agency, {}, drop=(KEY_LAST_ERROR, KEY_LAST_ERROR_AT))


def connection_info(agency: Any) -> dict[str, Any]:
    """What the panel is allowed to see. Never the refresh token."""
    cfg = _cfg(agency)
    connected = bool(cfg.get(KEY_REFRESH_TOKEN))
    revoked_at = cfg.get(KEY_REVOKED_AT)
    last_error = cfg.get(KEY_LAST_ERROR)
    return {
        "connected": connected,
        "configured": oauth_configured(),
        "calendar_id": cfg.get(KEY_CALENDAR_ID) if connected else None,
        "account_email": cfg.get(KEY_ACCOUNT_EMAIL),
        "connected_at": cfg.get(KEY_CONNECTED_AT) if connected else None,
        # True only while disconnected: a reconnection clears the flag.
        "revoked": bool(revoked_at) and not connected,
        "revoked_at": revoked_at if not connected else None,
        # A connection that is still there but has stopped working. Shown only
        # while connected: once the grant is gone, `revoked` is the honest
        # headline and two warnings about the same thing help nobody.
        "sync_error": str(last_error) if last_error and connected else None,
        "sync_error_at": cfg.get(KEY_LAST_ERROR_AT) if last_error and connected else None,
    }


# ---------------------------------------------------------------------------
# Visits → events
# ---------------------------------------------------------------------------


def agency_timezone(agency: Any) -> tuple[tzinfo, str]:
    """Same contract (and fallbacks) as `app.api.visits.resolve_agency_timezone`.

    Duplicated rather than imported: `app.api.visits` imports this module, and
    a service must not depend on the API layer to resolve six lines.
    """
    for candidate in ((getattr(agency, "timezone", None) or "").strip(), DEFAULT_TIMEZONE):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate), candidate
        except Exception:  # noqa: BLE001 — no tzdata in the image
            log.warning("gcal.bad_timezone", tz=candidate)
    return UTC, "UTC"


def build_event_body(
    *,
    start: datetime,
    duration_minutes: int,
    tz: tzinfo,
    tz_name: str,
    summary: str,
    description: str,
    location: str | None = None,
) -> dict[str, Any]:
    """The `events.insert` body. Times in the agency's own timezone.

    `scheduled_at` is an absolute instant; rendering it in the server's
    timezone would put an 09:00 Madrid visit in the calendar at 07:00.
    Sending both the RFC3339 offset *and* `timeZone` is what Google's own
    examples do, and it keeps the event readable when the owner opens it.
    """
    if start.tzinfo is None:
        # Naive rows exist in old data: read them as agency-local, which is
        # what whoever typed them meant.
        start = start.replace(tzinfo=tz)
    local_start = start.astimezone(tz)
    local_end = local_start + timedelta(minutes=max(int(duration_minutes or 30), 5))
    return {
        "summary": summary,
        "description": description,
        "start": {"dateTime": local_start.isoformat(), "timeZone": tz_name},
        "end": {"dateTime": local_end.isoformat(), "timeZone": tz_name},
        **({"location": location} if location else {}),
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 60},
                {"method": "email", "minutes": 24 * 60},
            ],
        },
    }


def _visit_summary(lead: Any, property_: Any) -> str:
    lead_name = (getattr(lead, "full_name", None) or "").strip() or "Cliente"
    title = (getattr(property_, "title", None) or "").strip()
    reference = (getattr(property_, "reference", None) or "").strip()
    place = title or reference or "propiedad"
    return f"Visita: {lead_name} — {place}"


def _visit_description(visit: Any, lead: Any, property_: Any) -> str:
    lines = [f"Cliente: {(getattr(lead, 'full_name', None) or '').strip() or 'sin nombre'}"]
    phone = (getattr(lead, "phone_e164", None) or "").strip()
    # The phone is the whole point of the description: whoever opens the event
    # on their way to the visit needs to be able to call.
    lines.append(f"Teléfono: {phone}" if phone else "Teléfono: no disponible")
    email = (getattr(lead, "email", None) or "").strip()
    if email:
        lines.append(f"Email: {email}")
    reference = (getattr(property_, "reference", None) or "").strip()
    if reference:
        lines.append(f"Referencia: {reference}")
    briefing = (getattr(visit, "pre_visit_briefing", None) or "").strip()
    if briefing:
        lines.append("")
        lines.append(briefing)
    lines.append("")
    lines.append("Creado automáticamente por selai.")
    return "\n".join(lines)


async def sync_visit_to_calendar(
    agency: Any,
    visit: Any,
    *,
    lead: Any = None,
    property_: Any = None,
) -> str | None:
    """Best-effort: put `visit` in the agency's Google Calendar.

    **Never raises.** A visit is agreed with a human being over WhatsApp; if
    Google is down, or the customer revoked the grant an hour ago, the visit
    still gets booked and the agenda in the panel is still right. The only
    thing we lose is the mirror in their calendar, and the log says so.

    Mutates `agency.settings` (on revocation, and to learn the connected
    account) and `visit.calendar_event_id`. The caller commits.
    """
    cfg = _cfg(agency)
    try:
        refresh_token = secret_from_settings(cfg, KEY_REFRESH_TOKEN)
    except SecretDecryptionError:
        record_sync_failure(agency, "credentials_unreadable")
        log.error("gcal.credentials_unreadable", agency=str(getattr(agency, "id", "")))
        return None
    if not refresh_token:
        return None
    if not oauth_configured():
        # Credentials stored by an environment that had the keys, running in
        # one that does not. Nothing to do, and nothing is broken.
        log.warning("gcal.sync.skipped_not_configured", agency=str(getattr(agency, "id", "")))
        return None

    calendar_id = cfg.get(KEY_CALENDAR_ID) or DEFAULT_CALENDAR_ID
    tz, tz_name = agency_timezone(agency)
    body = build_event_body(
        start=getattr(visit, "scheduled_at", None) or datetime.now(tz),
        duration_minutes=getattr(visit, "duration_minutes", 30) or 30,
        tz=tz,
        tz_name=tz_name,
        summary=_visit_summary(lead, property_),
        description=_visit_description(visit, lead, property_),
        location=(getattr(property_, "address", None) or None),
    )

    try:
        event = await insert_event(
            refresh_token=str(refresh_token), calendar_id=str(calendar_id), body=body
        )
    except CalendarAccessRevokedError:
        # The customer pulled the grant from their Google account. Silently
        # retrying forever is how an integration dies without anyone noticing:
        # mark it, so the panel can ask for a reconnection.
        clear_connection(agency, revoked=True)
        log.warning("gcal.sync.revoked", agency=str(getattr(agency, "id", "")))
        return None
    except CalendarError as exc:
        # The connection is still there (only `invalid_grant` says otherwise),
        # so "conectado" stays true — but something has to be visible in the
        # panel, or a calendar that stopped mirroring looks exactly like one
        # that is working.
        record_sync_failure(agency, str(exc) or "unknown")
        log.warning(
            "gcal.sync.failed",
            agency=str(getattr(agency, "id", "")),
            error=str(exc),
        )
        return None
    except Exception:  # noqa: BLE001 — best effort means best effort
        record_sync_failure(agency, "unexpected_error")
        log.exception("gcal.sync.unexpected_error")
        return None

    event_id = event.get("id")
    if event_id:
        visit.calendar_event_id = str(event_id)

    clear_sync_failure(agency)

    # Opportunistic: `events.insert` echoes the account that owns the
    # calendar, which is the only way to learn it without asking for a wider
    # scope. Stored once so the panel can say *which* Google account is
    # connected.
    if not cfg.get(KEY_ACCOUNT_EMAIL):
        organizer = event.get("organizer") or event.get("creator") or {}
        email = organizer.get("email") if isinstance(organizer, dict) else None
        if email:
            _write(agency, {KEY_ACCOUNT_EMAIL: str(email)})

    log.info(
        "gcal.sync.ok",
        agency=str(getattr(agency, "id", "")),
        visit=str(getattr(visit, "id", "")),
        # `event` is structlog's own key for the message; shadowing it raises.
        event_id=str(event_id or ""),
    )
    return str(event_id) if event_id else None


# ---------------------------------------------------------------------------
# Legacy per-user helper
# ---------------------------------------------------------------------------


def create_visit_event(
    *,
    refresh_token: str,
    calendar_id: str,
    summary: str,
    description: str,
    start: datetime,
    duration_minutes: int = 30,
    attendees: list[str] | None = None,
    location: str | None = None,
) -> dict[str, Any]:
    """Per-*user* event creation, kept for `app/agent/tools.py`.

    That call site reads `User.google_refresh_token`, a column nothing has
    ever written (there was no OAuth flow until this module), so the branch is
    dead in practice — but the import is real and removing the function would
    take the whole app down at startup. Signature and sync shape preserved
    verbatim; the agency-level replacement is `sync_visit_to_calendar`, and
    the patch for that call site is in the item's report.
    """
    if not refresh_token or not calendar_id:
        log.warning("gcal.skipped.no_credentials")
        return {"id": "dev-stub", "htmlLink": ""}

    end = start + timedelta(minutes=duration_minutes)
    body: dict[str, Any] = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start.isoformat(), "timeZone": DEFAULT_TIMEZONE},
        "end": {"dateTime": end.isoformat(), "timeZone": DEFAULT_TIMEZONE},
    }
    if attendees:
        body["attendees"] = [{"email": a} for a in attendees]
    if location:
        body["location"] = location

    if not oauth_configured():
        log.warning("gcal.skipped.not_configured")
        return {"id": "dev-stub", "htmlLink": ""}

    with httpx.Client(timeout=HTTP_TIMEOUT) as http:
        token_resp = http.post(
            TOKEN_ENDPOINT,
            data={
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret.get_secret_value(),
            },
        )
        token_resp.raise_for_status()
        access_token = token_resp.json().get("access_token", "")
        resp = http.post(
            f"{CALENDAR_API_BASE}/calendars/{calendar_id}/events",
            json=body,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()
