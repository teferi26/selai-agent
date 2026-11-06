"""Outbound SMS via Twilio.

Two ways of getting credentials, the same shape as `WhatsAppClient`:

- `TwilioSMSClient()` → selai's own env-configured Twilio project. Used as
  the fallback when WhatsApp is undeliverable (no opt-in window, or the 24h
  customer-care window expired) and by the missed-call follow-up worker.
- `TwilioSMSClient(account_sid=…, auth_token=…, from_number=…)` → the
  agency's own Twilio account, connected from the dashboard
  (POST /channels/sms). SMS is sold from Pro up and each agency bills its
  own Twilio account, so in production this is the normal path: replying to
  an inbound SMS with the global credentials would answer from a number the
  lead never texted, and bill selai for it.

`client_for_settings` picks between the two from an agency's settings blob.
"""
from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from app.channels.sms.config import credentials_from_settings, owns_number
from app.core.logging import get_logger
from app.core.settings import settings

log = get_logger(__name__)

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"

# Twilio splits anything longer into billable segments; 1600 is the hard
# ceiling of a single API request.
MAX_BODY_CHARS = 1600


class TwilioSMSClient:
    def __init__(
        self,
        account_sid: str | None = None,
        auth_token: str | None = None,
        from_number: str | None = None,
    ) -> None:
        self.account_sid = account_sid or settings.twilio_account_sid
        self.auth_token = auth_token or settings.twilio_auth_token.get_secret_value()
        self.from_number = from_number or settings.twilio_from_number

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=8),
        reraise=True,
    )
    async def send(self, to_e164: str, body: str) -> str:
        sid, token, from_number = self.account_sid, self.auth_token, self.from_number
        if not (sid and token and from_number):
            log.warning("sms.no_credentials.skipped", to=to_e164)
            return "dev-stub"

        url = f"{TWILIO_API_BASE}/Accounts/{sid}/Messages.json"
        payload = {"To": to_e164, "From": from_number, "Body": body[:MAX_BODY_CHARS]}

        async with httpx.AsyncClient(timeout=15, auth=(sid, token)) as http:
            resp = await http.post(url, data=payload)
        if resp.status_code >= 400:
            log.error("sms.send.error", status=resp.status_code, body=resp.text[:200])
            resp.raise_for_status()
        return resp.json().get("sid", "")


def client_for_settings(
    cfg: dict[str, Any] | None, to_number: str | None = None
) -> TwilioSMSClient:
    """The agency's own Twilio client when it connected one; else the global.

    Takes the settings blob rather than the `Agency` row so a background task
    can use it after the request's session is gone — same reason as
    `_client_for_settings` in app/api/whatsapp.py.

    `to_number` is the number the inbound SMS was sent to. The agency's
    credentials only speak for *its* number, so when the message arrived on a
    different one (the single-agency fallback landing on selai's global
    number) the reply has to go out through the global client: answering from
    the agency's own number would text the lead from a number they never
    wrote to, and Twilio would bill the agency for a conversation it cannot
    see.
    """
    creds = credentials_from_settings(cfg)
    if creds is None:
        return TwilioSMSClient()
    if to_number is not None and not owns_number(cfg, to_number):
        return TwilioSMSClient()
    return TwilioSMSClient(
        account_sid=creds.account_sid,
        auth_token=creds.auth_token,
        from_number=creds.from_number,
    )
