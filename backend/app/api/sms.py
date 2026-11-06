"""Twilio SMS inbound webhook.

Twilio POSTs `application/x-www-form-urlencoded` and signs the request in
`X-Twilio-Signature`: base64 of HMAC-SHA1, over the **URL it called** with
every form field appended in sorted `key + value` order. The key is the auth
token of the Twilio account that owns the number.

Which auth token, though, is the whole point of this module.

Each agency connects its *own* Twilio account (dashboard → Canales → SMS,
`POST /channels/sms`), and Twilio signs that account's webhooks with **that**
account's token, never with ours. So the owning agency is resolved *first*,
from the still-unverified `To` field and with a read-only query, because
**which** token is the right one depends on it. This is exactly the bug
WhatsApp had (item 217) and the fix is the same shape:

- SMS for a number an agency connected → that agency's own auth token and
  nothing else. The global token belongs to selai's Twilio project and is
  shared by every tenant riding on it; accepting it as a fallback would let
  anyone holding it forge leads into the one tenant that isolated itself.
- SMS for *our own* number (`settings.twilio_from_number`) or for a number no
  agency claims → the global token. That number is served by our project, so
  our token is the sender of record.
- An agency that connected a number and stored no token is a **rejection**,
  not a fallback: its webhooks are signed with a token we do not have, and
  the global check could only ever say "no". `POST /channels/sms` stores the
  three values together so this state cannot be created any more; older rows
  edited by hand get a 401 with a log that says which number and whose.

The URL half of the signature has its own trap. Twilio signs the URL it was
configured with — the public `https://…` one we hand the agency — while
`str(request.url)` is what *this process* sees, and uvicorn is not started
with `--forwarded-allow-ips` for the platform's proxy, so behind TLS
termination the scheme it reports is `http`. Signing over that string alone
rejects every real SMS in production while passing every test on localhost.
Both candidates are therefore checked: the literal request URL and the same
path rebuilt on `settings.public_base_url`, which is the URL printed in the
panel for the agency to paste into Twilio.

Everything with side effects — the agent run, the reply, any write — happens
after the signature check, never before.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.sms.client import client_for_settings
from app.channels.sms.config import owns_number
from app.channels.sms.parser import parse_twilio_sms
from app.core.logging import get_logger
from app.core.settings import settings
from app.db.models import Agency
from app.db.session import get_db
from app.services.conversation_service import handle_inbound
from app.services.plan_limits import ensure_feature, is_subscription_blocked
from app.services.secret_store import SecretDecryptionError, secret_from_settings

router = APIRouter(prefix="/webhooks/sms", tags=["sms"])
log = get_logger(__name__)

# base64 of a 20-byte HMAC-SHA1: 27 base64 chars plus one "=" of padding.
# Anything else can be thrown out before parsing or touching the database.
_SIGNATURE_RE = re.compile(r"^[A-Za-z0-9+/]{27}=$")

# E.164 tops out at 15 digits. The `To` field is unverified input that goes
# straight into a query on a route the rate limiter skips, so anything that
# is not shaped like a phone number is not worth asking the database about.
_TO_NUMBER_RE = re.compile(r"^\+?[0-9]{6,15}$")


def is_well_formed_signature(signature: str | None) -> bool:
    """Cheap shape check, no secrets involved.

    First gate of the webhook: the endpoint is exempt from the rate limiter
    (app/core/middleware.py), so garbage has to cost as little as possible —
    and the tenant lookup below runs *before* the signature is verified.
    """
    return bool(signature) and bool(_SIGNATURE_RE.match(signature or ""))


@router.post("")
async def inbound_sms(
    request: Request,
    x_twilio_signature: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    if not is_well_formed_signature(x_twilio_signature) and not signature_checks_are_off():
        # Missing, or not even shaped like a Twilio signature: reject before
        # the tenant lookup. That lookup is a database query an
        # unauthenticated caller can trigger on a route the rate limiter
        # skips, so it must not be reachable with a blank header.
        log.warning("sms.missing_signature")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad_signature")

    form_data = await request.form()
    form = {k: form_data.get(k) for k in form_data}

    # Read-only tenant resolution *before* the signature check: the token to
    # verify with belongs to the agency that owns the number this SMS was
    # sent to. Nothing is trusted here beyond a lookup.
    to_number = _form_value(form, "To")
    agency = await _agency_for_number(db, to_number)

    try:
        token = _signing_token(agency, to_number)
    except SecretDecryptionError as exc:
        log.error(
            "sms.webhook.credentials_unreadable",
            agency=str(agency.id) if agency is not None else None,
            to=_short(to_number),
        )
        # Twilio retries 5xx responses; acknowledging here would lose the SMS.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "channel_credentials_unavailable"
        ) from exc
    if token is None:
        # Connected number, no stored token: its Twilio account signs with a
        # secret we do not have, so there is nothing to check against. Say
        # which number and whose, instead of a bare 401 nobody can debug.
        log.warning(
            "sms.agency_without_auth_token",
            to=_short(to_number),
            agency=str(agency.id) if agency is not None else None,
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad_signature")

    if not _verify_twilio(request, form, x_twilio_signature, token):
        # A bad signature is either an attack or — far more often — an agency
        # whose Twilio console points at a different URL than the one we
        # print. The log has to tell them apart without a debugger.
        log.warning(
            "sms.bad_signature",
            to=_short(to_number),
            known_number=agency is not None,
            agency=str(agency.id) if agency is not None else None,
            token_checked="agency" if token else "global",
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad_signature")

    # ── Signature verified: side effects allowed from here on. ──
    payload = parse_twilio_sms(form)
    if payload is None:
        return {"status": "ignored"}

    if agency is None:
        log.warning("sms.no_agency_match", to=_short(to_number))
        return {"status": "no_agency_match"}
    agency_id = agency.id
    # Snapshot the blob now: `handle_inbound` commits, which can expire the
    # ORM instance, and the reply below needs the credentials.
    agency_settings = dict(agency.settings or {})
    payload.agency_id = agency_id

    # Paused/canceled/unpaid accounts are not served — same rule (and same
    # 200) as the WhatsApp and email webhooks, which SMS never got. A 4xx
    # here would only make Twilio retry.
    if await is_subscription_blocked(db, agency_id):
        log.info("sms.skipped_blocked_subscription", agency=str(agency_id))
        return {"status": "subscription_blocked"}

    # SMS is sold from Pro up (seed_plans.py: starter has `sms: False`).
    # Answering it for a Starter spends Claude tokens plus a real Twilio
    # message that selai pays for and never invoices.
    try:
        await ensure_feature(db, agency_id, "sms")
    except HTTPException:
        log.info("sms.skipped_feature_not_in_plan", agency=str(agency_id))
        return {"status": "feature_not_in_plan"}

    try:
        result = await handle_inbound(db, payload)
        await db.commit()
    except Exception:
        await db.rollback()
        log.exception("sms.handle_inbound.failed")
        raise

    if result.reply_text and payload.identity.phone_e164:
        # The agency's own Twilio account, so the answer comes from the
        # number the lead texted and is billed to whoever owns it.
        client = client_for_settings(agency_settings, to_number)
        try:
            await client.send(payload.identity.phone_e164, result.reply_text)
        except Exception:
            log.exception("sms.send.failed", agency=str(agency_id))

    # Twilio expects empty TwiML or 200; return JSON, Twilio is lenient.
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------


def _form_value(form: dict, key: str) -> str:
    """A form field as a plain string. Twilio posts urlencoded, but the value
    is still typed `str | UploadFile` and this runs on unverified input."""
    value = form.get(key)
    return value if isinstance(value, str) else ""


def _short(value: object, limit: int = 32) -> str | None:
    """Log-safe rendering of an unverified value."""
    if value in (None, ""):
        return None
    return str(value)[:limit]


def signature_checks_are_off() -> bool:
    """True when nothing is verified at all: development with no global token.

    The documented dev escape hatch, and the same condition WhatsApp uses
    (`_signature_checks_are_off` in app/api/whatsapp.py). It is deliberately
    *not* "development", full stop: a developer pointing a real Twilio number
    at a tunnel has a token configured, and silently accepting unsigned
    requests there would hide the exact misconfiguration this module exists
    to surface.
    """
    return settings.is_dev and not settings.twilio_auth_token.get_secret_value()


def is_our_own_number(to_number: str) -> bool:
    """The number *we* own (the env-configured one), served by our project.

    Kept apart from `owns_number` because the two answer different
    questions: whose tenant a message belongs to, versus whose Twilio
    account is the sender of record for it. Same split as
    `_is_our_own_number` in app/api/whatsapp.py.
    """
    ours = settings.twilio_from_number
    return bool(ours) and bool(to_number) and str(ours) == str(to_number)


def _signing_token(agency: Agency | None, to_number: str) -> str | None:
    """Which auth token Twilio must have signed this webhook with.

    - `""` → the global one, which `_verify_twilio` resolves. The number is
      ours, or no agency claims it.
    - `"<token>"` → the agency's own, the only signature accepted for the
      number that agency connected.
    - `None` → the agency connected this number and stored no token. Not a
      fallback: see the module docstring. The caller rejects.
    """
    if agency is None:
        return ""
    cfg = agency.settings or {}
    if is_our_own_number(to_number):
        # Asked *before* "whose number is it": our own number is served by
        # our Twilio project and signed with our token even when an agency
        # has it written down as its own (the pilot, a row that predates
        # `POST /channels/sms`). Reading `owns_number` first left those
        # agencies looking for an agency token that cannot exist, and every
        # SMS to the shared number answered 401 for ever. It is also the
        # tighter rule: the agency's token must not sign for a number the
        # whole platform shares.
        return ""
    if not owns_number(cfg, to_number):
        # No agency claims this number: our project is the sender of record.
        return ""
    token = secret_from_settings(cfg, "twilio_auth_token")
    if token:
        return token
    # Connected number, nothing to check against. The escape hatch applies
    # only here, and only because in that state nothing was being verified
    # anyway: it must not turn a local environment into one that rejects
    # webhooks the verification itself would have accepted.
    return "" if signature_checks_are_off() else None


def _signed_url_candidates(request: Request) -> list[str]:
    """The URL strings Twilio may have signed, most literal first.

    Twilio hashes the URL configured in its console. `str(request.url)` is
    what this process received, which behind a TLS-terminating proxy says
    `http://` and on a private hostname — neither of which Twilio ever saw.
    `settings.public_base_url` is the origin we print in the panel for the
    agency to paste, so the same path rebuilt on it is the other candidate.
    """
    literal = str(request.url)
    candidates = [literal]
    public = (settings.public_base_url or "").rstrip("/")
    if public:
        rebuilt = f"{public}{request.url.path}"
        if request.url.query:
            rebuilt = f"{rebuilt}?{request.url.query}"
        if rebuilt != literal:
            candidates.append(rebuilt)
    return candidates


def signature_for(url: str, form: dict, token: str) -> str:
    """Twilio's scheme: base64(HMAC-SHA1(url + sorted key+value pairs))."""
    payload = url
    for key in sorted(form.keys()):
        value = form.get(key)
        payload += key + (value if isinstance(value, str) else "")
    digest = hmac.new(token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def _verify_twilio(
    request: Request, form: dict, signature: str | None, token: str
) -> bool:
    """`token` overrides the global auth token (per-agency Twilio account).
    When it is falsy the global `settings.twilio_auth_token` is used — the
    same contract as `verify_signature` in app/channels/whatsapp/security.py.
    """
    effective = token or settings.twilio_auth_token.get_secret_value()
    if not effective:
        # Nothing to check against. In development that is the escape hatch
        # (no Twilio project configured yet); anywhere else it means the
        # deployment is unconfigured and must not accept anonymous webhooks.
        return settings.is_dev
    if not signature:
        return False
    return any(
        hmac.compare_digest(signature_for(url, form, effective), signature)
        for url in _signed_url_candidates(request)
    )


# ---------------------------------------------------------------------------
# Tenant resolution
# ---------------------------------------------------------------------------


async def _agency_for_number(db: AsyncSession, to_number: str) -> Agency | None:
    """The agency that owns the number this SMS was sent to.

    Returns the row, not just the id: the caller needs the settings blob for
    the signature token and for the credentials it answers with.
    """
    if _TO_NUMBER_RE.match(to_number or ""):
        # Only when it looks like a number: `to_number` is unverified, of
        # unbounded length, and this runs before the signature check on a
        # route the rate limiter skips. A megabyte of `To` must not become a
        # megabyte of query parameter. Garbage falls through to the same
        # place an unknown number does.
        #
        # `.as_string()` (not `.astext`) — the column is the generic sqlalchemy
        # JSON type; `.astext` is postgres-dialect-only and raised
        # AttributeError on every inbound SMS.
        stmt = select(Agency).where(
            Agency.settings["twilio_sms_number"].as_string() == to_number
        )
        # Two agencies can hold the same number: `POST /channels/sms` only
        # asks Twilio whether the caller's credentials see it, and two selai
        # tenants of the same group share one Twilio account (the Agencia
        # plan sells multi-oficina). A number ported between accounts does it
        # too, when the old agency never pressed "desconectar".
        # `scalar_one_or_none()` raises on two rows → 500 on *every* SMS to
        # that number, which Twilio retries: both tenants go silent with the
        # panel in green. Refusing serves them no better, but it is bounded,
        # it is in the log, and it does not hand one tenant the other's
        # leads. `POST /channels/sms` now returns 409 so the state stops
        # being reachable; this is for the rows that already exist.
        try:
            agency = (await db.execute(stmt)).scalar_one_or_none()
        except MultipleResultsFound:
            log.error("sms.duplicate_number", to=_short(to_number))
            return None
        if agency is not None:
            return agency

    # Fallback: with exactly one agency, it is the tenant. Keeps the pilot
    # working before the number is mapped.
    count = (await db.execute(select(func.count()).select_from(Agency))).scalar_one()
    if count == 1:
        agency = (await db.execute(select(Agency).limit(1))).scalar_one()
        stored = (agency.settings or {}).get("twilio_sms_number")
        if (
            to_number
            and stored
            and str(stored) != str(to_number)
            and to_number != settings.twilio_from_number
        ):
            # The agency connected its *own* number and this SMS arrived on a
            # different, unknown one. Falling back would file the message
            # under this agency and answer from a number the sender never
            # texted. Drop it instead — same rule as the WhatsApp webhook.
            log.warning(
                "sms.fallback_rejected",
                to=_short(to_number),
                agency_number=_short(stored),
            )
            return None
        return agency
    return None
