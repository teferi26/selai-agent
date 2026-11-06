"""Retrieval of the *content* of an inbound email from Resend.

The `email.received` webhook carries **metadata only** — `email_id`, `from`,
`to`, `cc`, `bcc`, `subject` and the attachment list. It does NOT carry the
body or the headers. Resend documents the reason: serverless platforms cap the
size of a request body, so a 20 MB email with attachments could not be
delivered as a webhook at all.

That matters more here than it would elsewhere. A lead from Idealista or
Fotocasa arrives as a noreply notification whose *subject* says nothing useful
("Nuevo contacto para tu inmueble") and whose body holds the name, the phone
and the message. Without the body there is no lead: just an empty conversation
with a portal robot.

So after the webhook is verified we come back here and fetch the real thing:

    GET https://api.resend.com/emails/receiving/{id}
    Authorization: Bearer re_...

which answers with `text`, `html`, `headers` and `message_id`.
"""
from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from app.core.logging import get_logger
from app.core.settings import settings

log = get_logger(__name__)

_RECEIVING_URL = "https://api.resend.com/emails/receiving"


def resend_inbound_email_id(payload: dict[str, Any]) -> str | None:
    """The id to fetch, or None if this is not a Resend inbound event.

    Deliberately narrow: only `email.received` carries an inbound message. The
    same endpoint also receives our own delivery notifications (`email.sent`,
    `email.bounced`…) when the webhook is subscribed to more events than it
    needs, and those must not trigger a fetch.
    """
    if payload.get("type") != "email.received":
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    email_id = data.get("email_id") or data.get("id")
    return email_id if isinstance(email_id, str) and email_id else None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=8),
    reraise=True,
)
async def _get(email_id: str, api_key: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.get(
            f"{_RECEIVING_URL}/{email_id}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
    resp.raise_for_status()
    body = resp.json()
    return body if isinstance(body, dict) else {}


async def fetch_inbound_content(email_id: str) -> dict[str, Any] | None:
    """Full content of a received email, or None if it could not be fetched.

    Never raises: the caller decides what a missing body means. Returning None
    rather than an empty dict keeps that decision explicit — an email whose
    body is genuinely empty and an email we failed to read are not the same
    thing, and only the second one deserves a retry.
    """
    # Its own key, falling back to the sending one. Resend keys are scoped, and
    # the production sending key is restricted to "sending access" — reading a
    # received email would 401 on it. The fix is NOT to widen that key: a
    # full-access key can also create domains and export contacts, and it lives
    # in the same env as everything else. So reads get a separate credential,
    # and a deployment that only has one full-access key still works.
    api_key = (
        settings.resend_read_api_key.get_secret_value()
        or settings.resend_api_key.get_secret_value()
    )
    if not api_key:
        # Same posture as the outbound client: without a key we are in a dev
        # environment, and a test that posts a full payload by hand should not
        # be forced through the network.
        log.warning("email.receiving.no_api_key", email_id=email_id)
        return None
    try:
        return await _get(email_id, api_key)
    except httpx.HTTPStatusError as exc:
        log.error(
            "email.receiving.http_error",
            email_id=email_id,
            status=exc.response.status_code,
        )
    except httpx.HTTPError as exc:
        log.error("email.receiving.failed", email_id=email_id, error=str(exc))
    return None


def merge_inbound_content(payload: dict[str, Any], content: dict[str, Any]) -> None:
    """Fold the fetched body/headers into the webhook payload, in place.

    The webhook's own metadata wins where both carry a field: `to` in
    particular decides which agency the lead belongs to, and the envelope
    recipient in the webhook is the one Resend actually delivered to. The
    fetched copy only fills what the webhook never had.
    """
    data = payload.get("data")
    if not isinstance(data, dict):
        return
    for field in ("text", "html", "headers", "message_id", "subject", "from"):
        value = content.get(field)
        if value in (None, "", [], {}):
            continue
        if field in ("subject", "from") and data.get(field):
            continue  # the webhook already said it; do not second-guess it
        data[field] = value
