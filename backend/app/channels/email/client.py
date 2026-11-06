"""Outbound email via Resend.

Threading: we set `In-Reply-To` and `References` headers so client mail
clients group our replies into the original thread.
"""
from __future__ import annotations

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from app.core.logging import get_logger
from app.core.settings import settings

log = get_logger(__name__)
_RESEND_URL = "https://api.resend.com/emails"


class ResendClient:
    def __init__(self) -> None:
        self.api_key = settings.resend_api_key.get_secret_value()
        # `email_from` when the deployment sets one; otherwise the same
        # verified sender the transactional emails use. A placeholder domain
        # is treated as unset for the same reason: Resend rejects unverified
        # domains, and a reply that dies at send time is a lead who wrote to
        # an agency and never heard back.
        configured = settings.email_from.strip()
        if "example.com" in configured:
            configured = ""
        self.from_addr = configured or settings.resend_from

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=8),
        reraise=True,
    )
    async def send(
        self,
        *,
        to: str,
        subject: str,
        text: str,
        html: str | None = None,
        in_reply_to: str | None = None,
        references: list[str] | None = None,
        reply_to: str | None = None,
    ) -> str:
        if not self.api_key:
            log.warning("email.no_api_key.skipping", to=to)
            return "dev-stub"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict = {
            "from": self.from_addr,
            "to": [to],
            "subject": subject,
            "text": text,
        }
        if html:
            payload["html"] = html
        if reply_to:
            payload["reply_to"] = reply_to

        custom_headers: dict[str, str] = {}
        if in_reply_to:
            custom_headers["In-Reply-To"] = in_reply_to
        if references:
            custom_headers["References"] = " ".join(references)
        if custom_headers:
            payload["headers"] = custom_headers

        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(_RESEND_URL, json=payload, headers=headers)
        if resp.status_code >= 400:
            log.error("email.send.error", status=resp.status_code, body=resp.text)
            resp.raise_for_status()
        return resp.json().get("id", "")
