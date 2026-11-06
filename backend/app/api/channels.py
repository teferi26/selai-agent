"""Per-agency channel connections — the dashboard's "Canales" page.

Everything lives in `agency.settings` (JSON), no migrations:

- GET    ""            → connection status of every channel in one payload.
  Includes evidence, not just configuration: `email.received_count` and
  `whatsapp.inbound_count`/`last_inbound_at` say whether anything ever
  actually arrived, which is the only thing that proves a webhook works.
  `whatsapp.webhook_verify_token` is derived per agency (see
  `agency_verify_token`): the raw global one is a cross-tenant secret and
  must not be printed in anybody's panel.
- POST   "/whatsapp"   → connect the agency's own WhatsApp Cloud number.
  The credentials are verified against Meta's Graph API *before* being
  persisted, so a stored connection is always a working one. `app_secret` is
  **required** (217): the guide has the agency create its own Meta app, and
  Meta signs that app's webhooks with that app's secret — without it every
  inbound webhook is a 401 and the channel is dead while the panel shows a
  green tick.
- DELETE "/whatsapp"   → disconnect (drop the stored credentials).
- POST   "/whatsapp/test" → send "Mensaje de prueba de selai" to a number the
  owner types in, with the agency's own credentials. The only end-to-end
  check that proves the connection works in the direction that matters.
- DELETE "/whatsapp"   → disconnect (drop the stored credentials).
- POST   "/sms"        → connect the agency's own Twilio account. Same rule
  as WhatsApp: the credentials are checked against Twilio *before* being
  persisted (the account must exist, the number must be *that* number, must
  belong to the account and be SMS-capable), and `auth_token` never comes
  back out — `GET ""` answers with `sms.configured` instead. The token is
  also the key Twilio signs its webhooks with, so storing it is what makes
  the inbound half possible at all (see app/api/sms.py). A number already
  connected by another agency is a 409: the inbound webhook resolves the
  tenant by the number alone and two rows leave it unable to.
- DELETE "/sms"        → disconnect (drop the stored credentials).
- POST   "/phone"      → provision an AI phone number in Vapi. If Vapi has
  no numbers available (free numbers are US-only), we persist the request,
  email the team and return `{"status": "requested"}` — the dashboard shows
  it as "te lo activamos en menos de 24h" instead of a hard error.
- POST   "/webchat/verify" → fetch the agency's own website and look for the
  widget snippet with *this* agency's slug. Replaces a checkbox of honour
  with a fact.

Both connect endpoints validate their body **by hand** instead of with
Pydantic constraints. A Pydantic failure carries the value it rejected into
the 422 (`input`), and for a *missing* field that value is the whole request
body — which is where the access token and the auth token are. So the models
accept anything and the routes answer with a code that names the problem
without repeating the input.
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.sms.client import TWILIO_API_BASE
from app.channels.sms.config import (
    ACCOUNT_SID_RE,
    SMS_SETTINGS_KEYS,
    credentials_from_settings,
    normalize_e164,
)
from app.channels.whatsapp.client import WhatsAppClient
from app.channels.whatsapp.security import agency_verify_token
from app.core.auth import AgencyMemberDep
from app.core.logging import get_logger
from app.core.settings import settings
from app.db.models import (
    Agency,
    Conversation,
    ConversationChannel,
    Message,
    MessageDirection,
    WebhookSubscription,
)
from app.db.session import get_db
from app.services.emails import build_phone_number_request_email_html, send_email
from app.services.plan_limits import ensure_feature, ensure_subscription_usable
from app.services.secret_store import (
    SecretDecryptionError,
    encrypt_secret_settings,
    secret_from_settings,
)
from app.services.url_guard import WEB_PORTS, PublicTarget, resolve_public_target

router = APIRouter(
    prefix="/api/v1/agencies/{agency_id}/channels",
    tags=["channels"],
    dependencies=[AgencyMemberDep],
)
log = get_logger(__name__)

_GRAPH_BASE = "https://graph.facebook.com"
_VAPI_BASE = "https://api.vapi.ai"

# Where each agency receives lead email. The local-part convention matches
# `_agency_for_email` in app/api/email.py (`<slug>-leads@...`); the domain is
# configuration (`settings.email_leads_domain`), because which domain can
# actually receive depends on the inbound provider's plan.
#
# Read through a function rather than bound at import: a module-level constant
# would freeze whatever the environment said when the process booted, and no
# test could move it.
def leads_email_for(slug: str) -> str:
    return f"{slug}-leads@{settings.email_leads_domain}"

# Internal inbox that receives phone-number provisioning requests.
PHONE_REQUESTS_EMAIL = "hola@selai.es"

# Every WhatsApp key we own inside `agency.settings`; DELETE removes exactly
# this set, nothing else.
_WHATSAPP_SETTINGS_KEYS = (
    "whatsapp_phone_number_id",
    "whatsapp_waba_id",
    "whatsapp_access_token",
    "whatsapp_app_secret",
    "whatsapp_display_number",
    "whatsapp_verified_name",
    "whatsapp_connected_at",
    "whatsapp_subscribed_apps",
)

# Path Meta must call. Same route as app/api/whatsapp.py's router prefix.
_WEBHOOK_PATH = "/webhooks/whatsapp"

# Path Twilio must call. Same route as app/api/sms.py's router prefix.
_SMS_WEBHOOK_PATH = "/webhooks/sms"

# (189) Wording matters: whoever gets this on their phone must recognise it
# instantly as the test they just triggered, not as a message from a client.
WHATSAPP_TEST_MESSAGE = "Mensaje de prueba de selai"

# (195) Ceilings for the website check. Someone else's server is on the
# other end, so it gets ten seconds, three hops and a megabyte — after that
# the answer is "no lo hemos podido comprobar", which is honest and cheap.
_VERIFY_TIMEOUT_SECONDS = 10.0
_VERIFY_MAX_REDIRECTS = 3
_VERIFY_MAX_BYTES = 1_000_000
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def webhook_callback_url() -> str:
    return f"{settings.public_base_url.rstrip('/')}{_WEBHOOK_PATH}"


def sms_webhook_url() -> str:
    """What the agency pastes into "A MESSAGE COMES IN" in the Twilio console.

    Also the URL the inbound webhook rebuilds the Twilio signature over, so
    the two must stay the same string — see app/api/sms.py.
    """
    return f"{settings.public_base_url.rstrip('/')}{_SMS_WEBHOOK_PATH}"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class WhatsAppStatus(BaseModel):
    connected: bool
    credentials_unreadable: bool = False
    phone_number: str | None = None
    verified_name: str | None = None
    # Webhook configuration the agency must paste into its Meta app (or that
    # we already subscribed for them). Shown copyable in the dashboard.
    webhook_callback_url: str
    webhook_verify_token: str
    # True only when Meta accepted our POST /{waba_id}/subscribed_apps.
    # False means the agency still has to configure the webhook by hand.
    subscribed_apps: bool = False
    # True when the agency stored its own Meta app secret. It is the *only*
    # signature we accept for this number (app/api/whatsapp.py), so the panel
    # has to say so: a wrong secret means no message ever arrives. Required
    # since 217, so False here means a connection made before that and now
    # rejecting every webhook — the panel must shout, not whisper.
    # The secret itself is never returned.
    app_secret_set: bool = False
    # …but only for a number the agency connected on its own Meta app. A
    # number served by *ours* (`settings.whatsapp_phone_number_id`) is signed
    # with the global secret and works perfectly without a stored one, so the
    # panel must not tell those agencies their channel is dead. The two flags
    # together, and only together, mean "we are rejecting everything right
    # now": `app_secret_required and not app_secret_set`.
    app_secret_required: bool = False
    # (217) Evidence that the whole inbound chain works — Meta's webhook
    # reached us, the signature validated, and the message was stored.
    # Counted over inbound `Message` rows of WhatsApp conversations, not over
    # conversations: a channel serving one returning lead creates a single
    # conversation and would otherwise look frozen at the day it started,
    # while a channel that broke last week would keep showing the timestamp
    # of a conversation opened months ago. `last_inbound_at` means what its
    # name says — when the last WhatsApp message actually came in.
    inbound_count: int = 0
    last_inbound_at: datetime | None = None


class SmsStatus(BaseModel):
    # The three credentials are stored and were verified against Twilio when
    # they were. `auth_token` is deliberately absent from this model: it is
    # the key Twilio signs webhooks with, and a panel any member of the
    # agency can open is not a place to print it. Same treatment as
    # `whatsapp_access_token`.
    configured: bool
    credentials_unreadable: bool = False
    phone_number: str | None = None
    # Not a secret — Twilio's account identifier is the username half of the
    # pair and is useless without the token. Shown so an owner with several
    # Twilio projects can tell which one is connected.
    account_sid: str | None = None
    friendly_name: str | None = None
    # What the agency must paste into the number's "A MESSAGE COMES IN" field
    # in the Twilio console. Copyable in the dashboard.
    webhook_url: str
    # The only honest proof the inbound half works: credentials that verify
    # against Twilio prove we can *send*, nothing more. Counted over inbound
    # `Message` rows of SMS conversations — a conversation is created once per
    # lead, so its date answers "when did a new lead first write", not "when
    # did the last message arrive". `inbound_count == 0` means no SMS has ever
    # reached us, however green the rest of the card looks.
    inbound_count: int = 0
    last_inbound_at: datetime | None = None


class EmailStatus(BaseModel):
    address: str
    received_count: int
    last_received_at: datetime | None = None


class PhoneStatus(BaseModel):
    number: str | None = None
    status: Literal["none", "requested", "active"]


class WebchatStatus(BaseModel):
    slug: str
    # (195) True only after POST /webchat/verify actually found the snippet
    # on the agency's own site. A checkbox the user ticked does not count.
    verified: bool = False
    verified_url: str | None = None
    verified_at: datetime | None = None


class WebhooksStatus(BaseModel):
    count: int


class ChannelsStatusOut(BaseModel):
    whatsapp: WhatsAppStatus
    sms: SmsStatus
    email: EmailStatus
    phone: PhoneStatus
    webchat: WebchatStatus
    webhooks: WebhooksStatus


class WhatsAppConnectIn(BaseModel):
    """Every field optional and unconstrained **on purpose** — see
    `SmsConnectIn` below for the reason (a rejected value comes back inside
    the 422). The three are checked by hand in the route, which is also
    where the messages the panel prints come from.
    """

    phone_number_id: str = ""
    waba_id: str | None = None
    access_token: str = ""
    # (217) Required. The agency's own Meta app secret is the only signature
    # we accept for its number (app/api/whatsapp.py), and the app it just
    # created signs with it. Accepting the connection without it produced the
    # worst possible outcome: a green "conectado" over a channel where not a
    # single message could ever arrive.
    app_secret: str = ""


class WhatsAppConnectOut(BaseModel):
    connected: bool
    phone_number: str | None = None
    verified_name: str | None = None
    subscribed_apps: bool = False
    app_secret_set: bool = False
    webhook_callback_url: str
    webhook_verify_token: str


class SmsConnectIn(BaseModel):
    """Credentials for the agency's own Twilio account.

    Every field is optional and carries **no** constraint, which looks like
    a mistake and is the opposite of one: Pydantic v2 puts the value it
    rejected into `input`, and FastAPI serialises that straight into the 422
    body. Any constraint on `auth_token` therefore hands the token back to
    the caller when it trips — and the `missing` error is worse still,
    because *its* `input` is the whole request body, token included. That
    422 ends up in the browser console, in the error tracker and in every
    4xx log line.

    So nothing here can fail: the route checks the three values by hand and
    answers with a code (`auth_token_required`, `account_sid_invalid`,
    `invalid_phone`) that names the problem without repeating the input.
    """

    account_sid: str = ""
    auth_token: str = ""
    phone_number: str = ""


class SmsConnectOut(BaseModel):
    connected: bool
    phone_number: str
    account_sid: str
    friendly_name: str | None = None
    webhook_url: str


class PhoneProvisionOut(BaseModel):
    status: Literal["active", "requested"]
    number: str | None = None


class WhatsAppTestIn(BaseModel):
    to_phone: str = Field(min_length=6, max_length=32)


class WhatsAppTestOut(BaseModel):
    sent: bool
    to_phone: str
    message: str


class WebchatVerifyIn(BaseModel):
    url: str = Field(min_length=4, max_length=512)


class WebchatVerifyOut(BaseModel):
    verified: bool
    url: str
    # Machine-readable outcome the dashboard turns into plain Spanish:
    #   ok               → snippet found with this agency's slug
    #   widget_not_found → page loaded, no selai snippet on it
    #   other_slug       → a selai snippet, but for a different agency
    #   unreachable      → we could not load the page
    #   blocked_url      → not a public http(s) address we may fetch
    reason: Literal["ok", "widget_not_found", "other_slug", "unreachable", "blocked_url"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_agency(db: AsyncSession, agency_id: uuid.UUID) -> Agency:
    agency = await db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agency_not_found")
    return agency


def _merge_settings(agency: Agency, updates: dict) -> None:
    """Reassign the whole dict so SQLAlchemy change-tracking notices."""
    agency.settings = {
        **dict(agency.settings or {}),
        **encrypt_secret_settings(updates),
    }


def _clean(value: object) -> str:
    """A credential field as a trimmed string, whatever the client sent.

    The connect models accept anything so that no rejected value can travel
    back inside a 422 (see `SmsConnectIn`), which means the type check lands
    here: a number, a null or a nested object all become "" and are refused
    below by the same rule as a blank one.
    """
    return value.strip() if isinstance(value, str) else ""


# Ceilings for what we store. A Twilio auth token is 32 hex characters and a
# Meta app secret is 32 too; anything an order of magnitude bigger is a
# paste accident, not a credential.
MAX_CREDENTIAL_LEN = 256
MAX_TOKEN_LEN = 4096


def own_app_secret_required(cfg: dict) -> bool:
    """True when this agency's number is served by its *own* Meta app.

    Mirrors `_agency_signing_secret` in app/api/whatsapp.py, which is what
    actually decides: our own number keeps validating against the global
    secret, so an agency sitting on it has no secret of its own to store and
    nothing is wrong with it. Telling those agencies "falta el secreto y por
    eso no entra ningún mensaje" would be the same class of lie this item is
    about, only pointing the other way.
    """
    stored = cfg.get("whatsapp_phone_number_id")
    if not stored:
        return False
    ours = settings.whatsapp_phone_number_id
    return not (ours and str(stored) == str(ours))


# ---------------------------------------------------------------------------
# GET "" — status of every channel
# ---------------------------------------------------------------------------


@router.get("", response_model=ChannelsStatusOut)
async def channels_status(
    agency_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ChannelsStatusOut:
    agency = await _get_agency(db, agency_id)
    cfg = dict(agency.settings or {})

    whatsapp_credentials_unreadable = False
    try:
        whatsapp_access_token = secret_from_settings(cfg, "whatsapp_access_token")
        whatsapp_app_secret = secret_from_settings(cfg, "whatsapp_app_secret")
    except SecretDecryptionError:
        whatsapp_credentials_unreadable = True
        whatsapp_access_token = ""
        whatsapp_app_secret = ""
        log.error("channels.whatsapp.credentials_unreadable", agency=str(agency_id))
    whatsapp_connected = bool(
        whatsapp_access_token
        and cfg.get("whatsapp_phone_number_id")
        and not whatsapp_credentials_unreadable
    )

    sms_credentials_unreadable = False
    try:
        sms_credentials = credentials_from_settings(cfg)
    except SecretDecryptionError:
        sms_credentials_unreadable = True
        sms_credentials = None
        log.error("channels.sms.credentials_unreadable", agency=str(agency_id))

    email_row = (
        await db.execute(
            select(func.count(Conversation.id), func.max(Conversation.created_at)).where(
                Conversation.agency_id == agency_id,
                Conversation.channel == ConversationChannel.EMAIL,
            )
        )
    ).one()
    received_count = int(email_row[0] or 0)
    last_received_at = email_row[1]

    # (217) The only honest answer to "does the webhook work?": credentials
    # that verify against Graph prove we can *send*, nothing more.
    #
    # Over messages, not conversations: a conversation is created once per
    # lead and never again, so its `created_at` answers "when did a new lead
    # first write", not "when did a message last arrive". Both readings that
    # matter here were wrong with it — a live channel whose leads are all
    # returning ones looked stale, and a channel that stopped working weeks
    # ago kept reporting the day its last new lead appeared.
    whatsapp_row = (
        await db.execute(
            select(func.count(Message.id), func.max(Message.created_at))
            .select_from(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.agency_id == agency_id,
                Message.direction == MessageDirection.INBOUND,
                Conversation.channel == ConversationChannel.WHATSAPP,
            )
        )
    ).one()
    inbound_count = int(whatsapp_row[0] or 0)
    last_inbound_at = whatsapp_row[1]

    # Same evidence, same reasoning, for SMS: stored credentials only prove
    # we can send. Until a real SMS has come in, the webhook in the Twilio
    # console is unproven and the card must say so.
    sms_row = (
        await db.execute(
            select(func.count(Message.id), func.max(Message.created_at))
            .select_from(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Message.agency_id == agency_id,
                Message.direction == MessageDirection.INBOUND,
                Conversation.channel == ConversationChannel.SMS,
            )
        )
    ).one()
    sms_inbound_count = int(sms_row[0] or 0)
    sms_last_inbound_at = sms_row[1]

    webhook_count = (
        await db.execute(
            select(func.count())
            .select_from(WebhookSubscription)
            .where(WebhookSubscription.agency_id == agency_id)
        )
    ).scalar_one()

    ai_number = cfg.get("ai_phone_number")
    if ai_number:
        phone_state: Literal["none", "requested", "active"] = "active"
    elif cfg.get("phone_number_requested"):
        phone_state = "requested"
    else:
        phone_state = "none"

    return ChannelsStatusOut(
        whatsapp=WhatsAppStatus(
            connected=whatsapp_connected,
            credentials_unreadable=whatsapp_credentials_unreadable,
            phone_number=cfg.get("whatsapp_display_number"),
            verified_name=cfg.get("whatsapp_verified_name"),
            webhook_callback_url=webhook_callback_url(),
            webhook_verify_token=agency_verify_token(agency_id),
            subscribed_apps=bool(cfg.get("whatsapp_subscribed_apps")),
            app_secret_set=bool(whatsapp_app_secret),
            app_secret_required=own_app_secret_required(cfg),
            inbound_count=inbound_count,
            last_inbound_at=last_inbound_at,
        ),
        sms=SmsStatus(
            # All three together, never one of them: a half-stored account
            # can neither send nor validate a signature.
            configured=sms_credentials is not None,
            credentials_unreadable=sms_credentials_unreadable,
            phone_number=cfg.get("twilio_sms_number"),
            account_sid=cfg.get("twilio_account_sid"),
            friendly_name=cfg.get("twilio_sms_friendly_name"),
            webhook_url=sms_webhook_url(),
            inbound_count=sms_inbound_count,
            last_inbound_at=sms_last_inbound_at,
        ),
        email=EmailStatus(
            address=leads_email_for(agency.slug),
            received_count=received_count,
            last_received_at=last_received_at,
        ),
        phone=PhoneStatus(number=ai_number, status=phone_state),
        webchat=WebchatStatus(
            slug=agency.slug,
            verified=bool(
                (cfg.get("setup_checklist") or {}).get("webchat_verified")
            ),
            verified_url=cfg.get("webchat_verified_url"),
            verified_at=_parse_iso(cfg.get("webchat_verified_at")),
        ),
        webhooks=WebhooksStatus(count=int(webhook_count or 0)),
    )


def _parse_iso(value: object) -> datetime | None:
    """Settings JSON is hand-editable; a bad timestamp must not 500 the page."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# POST/DELETE "/whatsapp" — connect the agency's own Cloud API number
# ---------------------------------------------------------------------------


async def _subscribe_app_to_waba(waba_id: str | None, access_token: str) -> bool:
    """POST /{waba_id}/subscribed_apps — makes Meta deliver this WABA's
    webhooks to the app the token belongs to.

    Best-effort by design: it needs `whatsapp_business_management` on the
    token and a WABA id, neither of which we can guarantee. Any failure just
    returns False; the caller persists that and the dashboard asks the
    agency to configure the webhook manually.
    """
    if not waba_id:
        return False

    url = f"{_GRAPH_BASE}/{settings.whatsapp_api_version}/{waba_id}/subscribed_apps"
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(
                url, headers={"Authorization": f"Bearer {access_token}"}
            )
    except Exception:
        log.warning("channels.whatsapp.subscribe_apps_error", waba=str(waba_id))
        return False

    if resp.status_code >= 400:
        log.warning(
            "channels.whatsapp.subscribe_apps_rejected",
            waba=str(waba_id),
            status=resp.status_code,
            body=resp.text[:200],
        )
        return False

    try:
        return bool((resp.json() or {}).get("success", True))
    except ValueError:
        # 2xx without a JSON body: Meta accepted it, take it as success.
        return True


@router.post("/whatsapp", response_model=WhatsAppConnectOut)
async def connect_whatsapp(
    agency_id: uuid.UUID,
    body: WhatsAppConnectIn,
    db: AsyncSession = Depends(get_db),
) -> WhatsAppConnectOut:
    agency = await _get_agency(db, agency_id)

    # Checked here, not by the model: a Pydantic failure would echo the
    # rejected value — and, when a field is simply missing, the entire body —
    # back into the 422. `access_token` and `app_secret` are in that body.
    phone_number_id = _clean(body.phone_number_id)
    access_token = _clean(body.access_token)
    # A blank secret stores as empty and lands us back in the state (217)
    # exists to prevent: green tick, every webhook rejected.
    app_secret = _clean(body.app_secret)
    waba_id = _clean(body.waba_id) or None
    if not phone_number_id or len(phone_number_id) > 64:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "phone_number_id_required"
        )
    if not access_token or len(access_token) > MAX_TOKEN_LEN:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "access_token_required")
    if not app_secret or len(app_secret) > MAX_CREDENTIAL_LEN:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "app_secret_required")
    if waba_id is not None and len(waba_id) > 64:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "waba_id_invalid")

    # Verify the credentials against Graph before persisting anything: a
    # stored connection must always be a working one.
    url = f"{_GRAPH_BASE}/{settings.whatsapp_api_version}/{phone_number_id}"
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.get(
                url,
                params={"fields": "display_phone_number,verified_name"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except Exception as exc:
        log.exception("channels.whatsapp.provider_error", agency=str(agency_id))
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "whatsapp_provider_error"
        ) from exc

    if resp.status_code >= 500:
        log.error(
            "channels.whatsapp.graph_5xx",
            agency=str(agency_id),
            status=resp.status_code,
            body=resp.text[:200],
        )
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "whatsapp_provider_error")
    if resp.status_code >= 400:
        # Bad token or bad phone_number_id — the caller's input is wrong.
        log.warning(
            "channels.whatsapp.verification_failed",
            agency=str(agency_id),
            status=resp.status_code,
            body=resp.text[:200],
        )
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "whatsapp_verification_failed"
        )

    try:
        data = resp.json()
    except ValueError as exc:
        # 200 with a non-JSON body (interfering proxy, Graph hiccup): treat
        # as a provider error, never persist unverified credentials.
        log.error("channels.whatsapp.graph_bad_json", agency=str(agency_id))
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "whatsapp_provider_error"
        ) from exc
    display_number = data.get("display_phone_number")
    verified_name = data.get("verified_name")

    # Best-effort: subscribe our app to the agency's WABA so Meta actually
    # delivers webhooks. Without this the connection looks fine but no
    # message ever reaches the assistant, so we record whether it worked and
    # the dashboard tells the agency to finish it by hand when it didn't.
    subscribed = await _subscribe_app_to_waba(waba_id, access_token)

    _merge_settings(
        agency,
        {
            "whatsapp_phone_number_id": phone_number_id,
            "whatsapp_waba_id": waba_id,
            "whatsapp_access_token": access_token,
            "whatsapp_app_secret": app_secret,
            "whatsapp_display_number": display_number,
            "whatsapp_verified_name": verified_name,
            "whatsapp_connected_at": datetime.now(UTC).isoformat(),
            "whatsapp_subscribed_apps": subscribed,
        },
    )
    await db.commit()
    log.info(
        "channels.whatsapp.connected",
        agency=str(agency_id),
        phone=display_number,
        subscribed_apps=subscribed,
    )
    return WhatsAppConnectOut(
        connected=True,
        phone_number=display_number,
        verified_name=verified_name,
        subscribed_apps=subscribed,
        app_secret_set=bool(app_secret),
        webhook_callback_url=webhook_callback_url(),
        webhook_verify_token=agency_verify_token(agency_id),
    )


# ---------------------------------------------------------------------------
# POST "/whatsapp/test" — send a test message with the agency's credentials
# ---------------------------------------------------------------------------


def normalize_msisdn(raw: str) -> str | None:
    """Digits only, E.164-ish, the shape Meta's Cloud API expects.

    A bare 9-digit number is assumed Spanish (+34): that is what an owner
    types when asked for "tu móvil", and rejecting it would fail the test
    for the most common input of all. Anything shorter than 8 digits is not
    a phone number.
    """
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 9 and not (raw or "").strip().startswith("+"):
        digits = f"34{digits}"
    if not 8 <= len(digits) <= 15:
        return None
    return digits


@router.post("/whatsapp/test", response_model=WhatsAppTestOut)
async def send_whatsapp_test(
    agency_id: uuid.UUID,
    body: WhatsAppTestIn,
    db: AsyncSession = Depends(get_db),
) -> WhatsAppTestOut:
    """(189) Send one real message through the agency's own connection.

    It is the only check that exercises the outbound half end to end —
    credentials, number, Meta — with a result the owner can see on their
    own phone. The inbound half is proved a moment later, when they reply
    and the answer appears in the panel.
    """
    agency = await _get_agency(db, agency_id)
    cfg = dict(agency.settings or {})
    access_token = secret_from_settings(cfg, "whatsapp_access_token")
    phone_number_id = cfg.get("whatsapp_phone_number_id")
    if not (access_token and phone_number_id):
        # 501, not 400: nothing is wrong with the request, the feature just
        # isn't connected yet. The dashboard says so instead of blaming the
        # user's input.
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "whatsapp_not_connected")

    to_phone = normalize_msisdn(body.to_phone)
    if to_phone is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_phone")

    client = WhatsAppClient(
        access_token=str(access_token), phone_number_id=str(phone_number_id)
    )
    try:
        await client.send_text(to_phone, WHATSAPP_TEST_MESSAGE)
    except Exception as exc:
        log.warning(
            "channels.whatsapp.test_send_failed",
            agency=str(agency_id),
            error=str(exc)[:200],
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "whatsapp_send_failed"
        ) from exc

    log.info("channels.whatsapp.test_sent", agency=str(agency_id))
    return WhatsAppTestOut(
        sent=True, to_phone=to_phone, message=WHATSAPP_TEST_MESSAGE
    )


@router.delete("/whatsapp")
async def disconnect_whatsapp(
    agency_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    agency = await _get_agency(db, agency_id)
    agency.settings = {
        k: v
        for k, v in dict(agency.settings or {}).items()
        if k not in _WHATSAPP_SETTINGS_KEYS
    }
    await db.commit()
    log.info("channels.whatsapp.disconnected", agency=str(agency_id))
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST/DELETE "/sms" — connect the agency's own Twilio account
# ---------------------------------------------------------------------------


async def _fetch_twilio_number(
    account_sid: str, auth_token: str, phone_number: str
) -> dict:
    """The IncomingPhoneNumber record for `phone_number`, or raise.

    One request answers both questions this endpoint has to answer before it
    persists anything:

        GET /2010-04-01/Accounts/{AccountSid}/IncomingPhoneNumbers.json
            ?PhoneNumber=+34910000000

    authenticated with the SID and token as HTTP Basic. A 401/403 means the
    credentials are wrong; a 200 with an empty `incoming_phone_numbers` list
    means they are right but the number is not in that account (the classic
    mistake: the number lives in a subaccount, or in the other Twilio project
    the owner also has open).
    """
    url = f"{TWILIO_API_BASE}/Accounts/{account_sid}/IncomingPhoneNumbers.json"
    try:
        async with httpx.AsyncClient(timeout=15, auth=(account_sid, auth_token)) as http:
            resp = await http.get(
                url, params={"PhoneNumber": phone_number, "PageSize": 1}
            )
    except Exception as exc:
        log.exception("channels.sms.provider_error")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "sms_provider_error") from exc

    if resp.status_code >= 500:
        log.error(
            "channels.sms.twilio_5xx", status=resp.status_code, body=resp.text[:200]
        )
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "sms_provider_error")
    if resp.status_code >= 400:
        # Bad SID or bad token — the caller's input is wrong.
        log.warning(
            "channels.sms.verification_failed",
            status=resp.status_code,
            body=resp.text[:200],
        )
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "sms_verification_failed"
        )

    try:
        data = resp.json()
    except ValueError as exc:
        # 200 with a non-JSON body (interfering proxy, Twilio hiccup): treat
        # as a provider error, never persist unverified credentials.
        log.error("channels.sms.twilio_bad_json")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "sms_provider_error") from exc

    numbers = data.get("incoming_phone_numbers")
    if not isinstance(numbers, list) or not numbers:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "sms_number_not_found"
        )
    record = numbers[0]
    if not isinstance(record, dict):
        log.error("channels.sms.twilio_bad_shape")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "sms_provider_error")

    # `?PhoneNumber=` is a **filter**, not a lookup: Twilio documents partial
    # matches and `*` wildcards on it, so an incomplete number answers 200
    # with a *different* number of the same account. Storing what came back
    # would connect a number the owner never typed — green tick, and every
    # SMS to the number they meant landing on `no_agency_match`. Compared as
    # numbers rather than strings, because Twilio's spelling is its own.
    returned = normalize_e164(record.get("phone_number"))
    if returned is not None and returned != phone_number:
        log.warning(
            "channels.sms.number_mismatch", asked=phone_number, got=returned
        )
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "sms_number_not_found"
        )

    # `"capabilities": {"voice": true, "sms": false, "mms": true, "fax": false}`
    # — lowercase in the 2010-04-01 JSON API. A voice-only number cannot
    # receive a single SMS, and storing it would be a green tick over a
    # channel that is dead by construction. Only an explicit `false` rejects:
    # an unfamiliar payload shape is a reason to trust Twilio's 200, not to
    # block a connection that may be perfectly good.
    capabilities = record.get("capabilities")
    if isinstance(capabilities, dict) and capabilities.get("sms") is False:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "sms_number_not_sms_capable"
        )
    return record


async def _number_owner(db: AsyncSession, number: str) -> uuid.UUID | None:
    """Which agency already has this number written down, if any.

    `.limit(1)` and `first()` on purpose: this runs precisely to stop a
    second row from existing, so it must not be the thing that trips over
    one that already does.
    """
    stmt = (
        select(Agency.id)
        .where(Agency.settings["twilio_sms_number"].as_string() == number)
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


@router.post("/sms", response_model=SmsConnectOut)
async def connect_sms(
    agency_id: uuid.UUID,
    body: SmsConnectIn,
    db: AsyncSession = Depends(get_db),
) -> SmsConnectOut:
    agency = await _get_agency(db, agency_id)

    # By hand, not by the model: a Pydantic error carries the value it
    # rejected — and for a missing field, the whole body — into the 422, and
    # the body is where the auth token is. See `SmsConnectIn`.
    account_sid = _clean(body.account_sid)
    auth_token = _clean(body.auth_token)
    if not auth_token or len(auth_token) > MAX_CREDENTIAL_LEN:
        # A blank token stores as empty: a connection that looks green and
        # can neither send nor validate an inbound signature.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "auth_token_required")
    if not ACCOUNT_SID_RE.match(account_sid):
        # Checked before spending a request so the panel can say "eso no es
        # un Account SID" instead of sending the owner off to regenerate a
        # token that was fine all along.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "account_sid_invalid"
        )

    number = normalize_e164(body.phone_number)
    if number is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_phone")

    # SMS is a Pro-and-up feature (seed_plans.py: starter has `sms: False`),
    # and the inbound webhook enforces the same flag. Letting a Starter store
    # credentials here would sell them a channel that silently drops every
    # message that arrives on it. Checked before the Twilio round-trip: the
    # answer does not depend on it.
    await ensure_subscription_usable(db, agency_id)
    await ensure_feature(db, agency_id, "sms")

    # One number, one tenant. Twilio happily shows the same number to two
    # selai agencies that share a Twilio account — a group with two offices,
    # which is exactly what the Agencia plan sells — and the inbound webhook
    # resolves the tenant by this number alone: two rows and it cannot tell
    # whose lead it is holding (see `_agency_for_number` in app/api/sms.py).
    # Refused here, before anything is stored and before Twilio is asked.
    owner = await _number_owner(db, number)
    if owner is not None and owner != agency_id:
        log.warning(
            "channels.sms.number_taken", agency=str(agency_id), owner=str(owner)
        )
        raise HTTPException(status.HTTP_409_CONFLICT, "sms_number_taken")

    record = await _fetch_twilio_number(account_sid, auth_token, number)

    # Twilio's own spelling of the number is authoritative E.164 and is what
    # arrives in the webhook's `To`. Normalized above to the same shape the
    # webhook compares against, so the tenant resolves with one exact match.
    stored_number = normalize_e164(record.get("phone_number")) or number
    friendly_name = record.get("friendly_name")
    _merge_settings(
        agency,
        {
            "twilio_account_sid": account_sid,
            "twilio_auth_token": auth_token,
            "twilio_sms_number": stored_number,
            "twilio_sms_number_sid": record.get("sid"),
            "twilio_sms_friendly_name": friendly_name,
            "twilio_sms_connected_at": datetime.now(UTC).isoformat(),
        },
    )
    await db.commit()
    log.info("channels.sms.connected", agency=str(agency_id), number=stored_number)
    return SmsConnectOut(
        connected=True,
        phone_number=stored_number,
        account_sid=body.account_sid,
        friendly_name=friendly_name,
        webhook_url=sms_webhook_url(),
    )


@router.delete("/sms")
async def disconnect_sms(
    agency_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    agency = await _get_agency(db, agency_id)
    agency.settings = {
        k: v
        for k, v in dict(agency.settings or {}).items()
        if k not in SMS_SETTINGS_KEYS
    }
    await db.commit()
    log.info("channels.sms.disconnected", agency=str(agency_id))
    return {"ok": True}


# ---------------------------------------------------------------------------
# POST "/phone" — provision an AI phone number in Vapi
# ---------------------------------------------------------------------------


async def _ensure_vapi_assistant(db: AsyncSession, agency: Agency) -> str | None:
    """Same lazy provisioning as PUT /voice (app/api/voice_settings.py)."""
    assistant_id = (agency.settings or {}).get("vapi_assistant_id")
    if assistant_id or not settings.vapi_api_key.get_secret_value():
        return assistant_id

    try:
        from app.services.onboarding import _provision_vapi_assistant

        assistant_id = await _provision_vapi_assistant(
            agency_name=agency.name,
            slug=agency.slug,
            recording_notice=(
                (agency.settings or {}).get("recording_notice", True) is not False
            ),
        )
    except Exception:
        log.exception("channels.phone.vapi_lazy_provision_failed", agency=str(agency.id))
        return None

    if assistant_id:
        _merge_settings(agency, {"vapi_assistant_id": assistant_id})
        await db.commit()
    return assistant_id


async def _create_vapi_phone_number(assistant_id: str) -> tuple[str, str | None] | None:
    """POST /phone-number in Vapi. Returns (number, phone_number_id) or None
    on any failure — the caller falls back to the manual-request flow."""
    api_key = settings.vapi_api_key.get_secret_value()
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.post(
                f"{_VAPI_BASE}/phone-number",
                json={"provider": "vapi", "assistantId": str(assistant_id)},
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
    except Exception:
        log.exception("channels.phone.vapi_error")
        return None

    if resp.status_code >= 400:
        log.warning(
            "channels.phone.vapi_rejected",
            status=resp.status_code,
            body=resp.text[:200],
        )
        return None

    data = resp.json()
    number = data.get("number")
    if not number:
        # Created but no number assigned yet — treat as unavailable and let
        # the team finish it by hand rather than showing a broken "active".
        log.warning("channels.phone.vapi_no_number", body=str(data)[:200])
        return None
    return str(number), data.get("id")


@router.post("/phone", response_model=PhoneProvisionOut, response_model_exclude_none=True)
async def provision_phone(
    agency_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> PhoneProvisionOut:
    agency = await _get_agency(db, agency_id)
    cfg = dict(agency.settings or {})

    existing = cfg.get("ai_phone_number")
    if existing:
        return PhoneProvisionOut(status="active", number=str(existing))

    # The phone channel is Pro and up (seed_plans.py: starter has
    # `voice: False`). Gated *after* the idempotent early return so an agency
    # that already has a number can still read it back, and before anything
    # that spends money: a Vapi number is billed to selai from the minute it
    # is provisioned, and the manual fallback puts a human on the job.
    #
    # The status check comes first for the same reason: a canceled, unpaid or
    # paused account asking for a *new* number is a monthly bill selai would
    # pay and never invoice. A live trial passes both (see plan_limits).
    await ensure_subscription_usable(db, agency_id)
    await ensure_feature(db, agency_id, "voice")

    assistant_id = await _ensure_vapi_assistant(db, agency)

    provisioned: tuple[str, str | None] | None = None
    if assistant_id and settings.vapi_api_key.get_secret_value():
        provisioned = await _create_vapi_phone_number(str(assistant_id))

    if provisioned is not None:
        number, vapi_phone_number_id = provisioned
        _merge_settings(
            agency,
            {"ai_phone_number": number, "vapi_phone_number_id": vapi_phone_number_id},
        )
        await db.commit()
        log.info("channels.phone.active", agency=str(agency_id), number=number)
        return PhoneProvisionOut(status="active", number=number)

    # Fallback: Vapi couldn't hand us a number right now (free numbers are
    # US-only and may be unavailable). Persist the request and alert the
    # team so they can provision one manually within 24h.
    _merge_settings(agency, {"phone_number_requested": True})
    await db.commit()

    try:
        html = build_phone_number_request_email_html(
            agency_name=agency.name,
            slug=agency.slug,
            contact_email=agency.contact_email,
        )
        await send_email(PHONE_REQUESTS_EMAIL, "Solicitud de número de teléfono IA", html)
    except Exception:
        log.exception("channels.phone.request_email_failed", agency=str(agency_id))

    log.info("channels.phone.requested", agency=str(agency_id))
    return PhoneProvisionOut(status="requested")


# ---------------------------------------------------------------------------
# POST "/webchat/verify" — is the widget really on the agency's website?
# ---------------------------------------------------------------------------

# `<script ...>` opening tags, so the snippet's attributes can be read as a
# unit: two unrelated scripts, one loading widget.js and one carrying a
# data-selai, must not add up to a false "installed".
_SCRIPT_TAG_RE = re.compile(r"<script\b[^>]*>", re.IGNORECASE)
_DATA_SELAI_RE = re.compile(r"""data-selai\s*=\s*["']?([^"'\s>]+)""", re.IGNORECASE)


def find_widget_slug(html: str) -> str | None:
    """The slug of the selai widget installed on this page, if any.

    Returns None when no script tag loads `widget.js` with a `data-selai`
    attribute. Deliberately forgiving about quoting and spacing: the snippet
    goes through WordPress plugins and page builders that rewrite it.
    """
    for tag in _SCRIPT_TAG_RE.findall(html or ""):
        if "widget.js" not in tag.lower():
            continue
        match = _DATA_SELAI_RE.search(tag)
        if match:
            return match.group(1).strip()
    return None


async def _validate_target(url: str) -> str | None:
    """The URL normalized for fetching, or None when we must not fetch it.

    Thin wrapper over `app/services/url_guard.py`, kept as a module-level
    name because it is the seam the route is tested through.
    """
    target = await resolve_public_target(url, allowed_ports=WEB_PORTS)
    return target.url if target is not None else None


def _verify_client() -> httpx.AsyncClient:
    """The HTTP client the website check uses. A function so tests can
    hand it a transport instead of the network."""
    return httpx.AsyncClient(
        timeout=_VERIFY_TIMEOUT_SECONDS,
        follow_redirects=False,
        headers={"User-Agent": "selai-widget-check/1.0 (+https://www.selai.es)"},
    )


async def _read_capped(resp: httpx.Response) -> str:
    """At most `_VERIFY_MAX_BYTES` of the body, decoded leniently.

    Read chunk by chunk and stopped at the ceiling: someone else's server is
    on the other end and `resp.text[:1MB]` would already have pulled the
    whole thing — a gigabyte of it, if that is what they served — into this
    process's memory before slicing it.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.aiter_bytes():
        chunks.append(chunk)
        total += len(chunk)
        if total >= _VERIFY_MAX_BYTES:
            break
    raw = b"".join(chunks)[:_VERIFY_MAX_BYTES]
    try:
        encoding = resp.encoding or "utf-8"
    except Exception:
        encoding = "utf-8"
    return raw.decode(encoding, errors="replace")


async def _fetch_hop(
    http: httpx.AsyncClient, target: PublicTarget
) -> tuple[str | None, str | None]:
    """One request. Returns `(html, redirect_to)`, at most one of them set."""
    try:
        async with http.stream(
            "GET",
            target.request_url,
            headers={"Host": target.host_header},
            extensions={"sni_hostname": target.host},
        ) as resp:
            if resp.status_code in _REDIRECT_STATUSES:
                location = resp.headers.get("location")
                if not location:
                    return None, None
                return None, str(httpx.URL(target.url).join(location))
            if resp.status_code >= 400:
                log.info(
                    "channels.webchat.fetch_status",
                    url=target.url[:200],
                    status=resp.status_code,
                )
                return None, None
            return await _read_capped(resp), None
    except Exception:
        log.info("channels.webchat.fetch_failed", url=target.url[:200])
        return None, None


async def fetch_page(url: str) -> tuple[str | None, str]:
    """Download `url` and return `(html, final_url)`; html is None on failure.

    Redirects are followed by hand, up to `_VERIFY_MAX_REDIRECTS`, so every
    hop goes back through the guard — an open redirect on the agency's own
    site would otherwise walk us straight into the private network the first
    check refused.

    Each hop is requested at the address the guard validated, with the real
    host in `Host` and in the TLS SNI. Handing the hostname to httpx instead
    would let a second DNS answer point the connection somewhere the check
    never saw.
    """
    current = url
    async with _verify_client() as http:
        for _ in range(_VERIFY_MAX_REDIRECTS + 1):
            target = await resolve_public_target(current, allowed_ports=WEB_PORTS)
            if target is None:
                return None, current
            html, redirect_to = await _fetch_hop(http, target)
            if html is not None:
                return html, target.url
            if redirect_to is None:
                return None, target.url
            current = redirect_to

    return None, current


@router.post("/webchat/verify", response_model=WebchatVerifyOut)
async def verify_webchat(
    agency_id: uuid.UUID,
    body: WebchatVerifyIn,
    db: AsyncSession = Depends(get_db),
) -> WebchatVerifyOut:
    """(195) Load the agency's website and look for its own widget snippet.

    Step 7 of the wizard used to be a button of honour: the user said "ya lo
    he puesto" and we believed it. A chat that never appeared on the
    customer's site is churn nobody sees coming, so this replaces the claim
    with a fact — and, when it fails, says which of the two failure modes it
    was (no snippet at all, or a snippet for another agency).
    """
    agency = await _get_agency(db, agency_id)

    target = await _validate_target(body.url)
    if target is None:
        log.info("channels.webchat.blocked_url", agency=str(agency_id))
        return WebchatVerifyOut(verified=False, url=body.url, reason="blocked_url")

    html, final_url = await fetch_page(target)
    if html is None:
        return WebchatVerifyOut(verified=False, url=final_url, reason="unreachable")

    found = find_widget_slug(html)
    if found is None:
        return WebchatVerifyOut(
            verified=False, url=final_url, reason="widget_not_found"
        )
    if found.strip().lower() != agency.slug.lower():
        log.info(
            "channels.webchat.other_slug", agency=str(agency_id), found=found[:64]
        )
        return WebchatVerifyOut(verified=False, url=final_url, reason="other_slug")

    checklist = dict((agency.settings or {}).get("setup_checklist") or {})
    checklist["webchat_verified"] = True
    # The wizard's own step marker, so the progress bar cannot disagree with
    # the badge right next to it.
    checklist["webchat"] = True
    _merge_settings(
        agency,
        {
            "setup_checklist": checklist,
            "webchat_verified_url": final_url,
            "webchat_verified_at": datetime.now(UTC).isoformat(),
        },
    )
    await db.commit()
    log.info("channels.webchat.verified", agency=str(agency_id), url=final_url[:200])
    return WebchatVerifyOut(verified=True, url=final_url, reason="ok")
