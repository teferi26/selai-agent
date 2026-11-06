"""Per-agency Twilio configuration inside `agency.settings`.

SMS is sold from Pro up, and every agency bills its own Twilio account, so
the credentials that matter are the ones the agency stored from the
dashboard — not the global env ones, which only exist so selai can send the
WhatsApp fallback from its own project.

The keys live in the `agency.settings` JSON blob, exactly like WhatsApp's
(`app/api/channels.py`): nothing here needs a column, and the blob is
already the place where channel secrets go. `twilio_auth_token` is a secret
and is **never** returned by any endpoint; `GET /channels` answers with a
boolean instead. `twilio_sms_number` is the one key that predates this
module — `app/api/sms.py` has always resolved the tenant by it — so it keeps
its name and existing rows keep working.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.secret_store import secret_from_settings

# Every key this feature owns. DELETE /channels/sms removes exactly this
# set and nothing else.
SMS_SETTINGS_KEYS: tuple[str, ...] = (
    "twilio_account_sid",
    "twilio_auth_token",
    "twilio_sms_number",
    "twilio_sms_number_sid",
    "twilio_sms_friendly_name",
    "twilio_sms_connected_at",
)

# Twilio account identifiers are "AC" + 32 hex chars. Checking the shape
# before spending a request turns "Twilio no reconoce esos datos" (which
# sends the owner off to regenerate a perfectly good token) into "eso no es
# un Account SID".
ACCOUNT_SID_RE = re.compile(r"^AC[0-9a-fA-F]{32}$")

# Spain, for the bare 9-digit number an owner types when asked for "tu
# número". Same assumption as `normalize_msisdn` in app/api/channels.py.
_DEFAULT_COUNTRY_CODE = "34"


@dataclass(frozen=True)
class SmsCredentials:
    """The three values that make an agency's Twilio account usable."""

    account_sid: str
    auth_token: str
    from_number: str


def credentials_from_settings(cfg: dict[str, Any] | None) -> SmsCredentials | None:
    """The agency's own Twilio credentials, or None when it never connected.

    All three are required together: a half-stored account can neither send
    nor validate a webhook signature, and treating it as "connected" is the
    class of green tick that costs a customer their leads.
    """
    blob = cfg or {}
    account_sid = str(blob.get("twilio_account_sid") or "").strip()
    auth_token = secret_from_settings(blob, "twilio_auth_token").strip()
    from_number = str(blob.get("twilio_sms_number") or "").strip()
    if not (account_sid and auth_token and from_number):
        return None
    return SmsCredentials(
        account_sid=account_sid, auth_token=auth_token, from_number=from_number
    )


def owns_number(cfg: dict[str, Any] | None, number: str | None) -> bool:
    """True when `number` is the number this agency connected.

    The question the webhook asks before deciding whose auth token Twilio
    signed with — an agency's credentials only speak for its own number.
    """
    stored = (cfg or {}).get("twilio_sms_number")
    return bool(stored) and bool(number) and str(stored) == str(number)


def normalize_e164(raw: str | None) -> str | None:
    """"+34 910 00 00 00" → "+34910000000"; None when it isn't a number.

    Twilio speaks strict E.164 with the leading "+" both in the webhook's
    `To` and in the `PhoneNumber` filter of its API, while an owner copies
    the number off the console with whatever spacing it is displayed with.
    Storing the normalized form is what lets the inbound webhook match the
    tenant with a single exact comparison.
    """
    text = (raw or "").strip()
    if not text:
        return None
    # "0034…" is how the number is written on half the paperwork in Spain.
    if text.startswith("00"):
        text = f"+{text[2:]}"
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    if not text.startswith("+") and len(digits) == 9:
        digits = f"{_DEFAULT_COUNTRY_CODE}{digits}"
    if not 8 <= len(digits) <= 15:
        return None
    return f"+{digits}"
