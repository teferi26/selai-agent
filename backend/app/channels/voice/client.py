"""Vapi REST client for outbound calls + assistant management.

We use Vapi for the voice infrastructure (telephony, STT, TTS, barge-in)
and configure each agency's assistant to use our own backend as its
"custom LLM" — so the brain stays in one place.
"""
from __future__ import annotations

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.core.logging import get_logger
from app.core.settings import settings

log = get_logger(__name__)
_BASE = "https://api.vapi.ai"


class VapiClient:
    def __init__(self) -> None:
        self.api_key = settings.vapi_api_key.get_secret_value()
        self.phone_number_id = settings.vapi_phone_number_id

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    # Placing a call is NOT idempotent: POST /call rings a real phone. A blind
    # retry on a read timeout or a 5xx can dial the same lead two or three
    # times (item 223). So we only retry failures that prove the request never
    # reached Vapi — the connection was never established. Anything after that
    # (read timeout, HTTP error) is reported to the caller, which owns the
    # decision; app/workers/tasks.py claims the call before placing it and
    # keeps the claim on failure, precisely so nobody dials twice.
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=8),
        retry=retry_if_exception_type((httpx.ConnectError, httpx.ConnectTimeout)),
        reraise=True,
    )
    async def place_outbound_call(
        self,
        *,
        assistant_id: str,
        customer_number_e164: str,
        metadata: dict | None = None,
    ) -> dict:
        if not self.api_key:
            log.warning("vapi.no_api_key.outbound_call_skipped", to=customer_number_e164)
            return {"id": "dev-stub"}
        payload = {
            "assistantId": assistant_id,
            "phoneNumberId": self.phone_number_id,
            "customer": {"number": customer_number_e164},
        }
        if metadata:
            payload["metadata"] = metadata

        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(f"{_BASE}/call", json=payload, headers=self._headers())
        if resp.status_code >= 400:
            log.error("vapi.outbound.error", status=resp.status_code, body=resp.text[:200])
            resp.raise_for_status()
        return resp.json()
