"""Thin async client for Meta WhatsApp Business Cloud API.

Exposes only what we need: send text, send image, mark as read, download
media. Extend as features are added (templates, interactive buttons, flows).
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from app.core.logging import get_logger
from app.core.settings import settings

log = get_logger(__name__)

# Meta caps WhatsApp media at 16 MB (audio/image/video) and 100 MB for
# documents. We only download what the assistant can actually use — a voice
# note — so 16 MB is both the provider's ceiling and ours.
MEDIA_MAX_BYTES = 16 * 1024 * 1024


class WhatsAppClient:
    def __init__(self, access_token: str | None = None, phone_number_id: str | None = None) -> None:
        self.access_token = access_token or settings.whatsapp_access_token.get_secret_value()
        self.phone_number_id = phone_number_id or settings.whatsapp_phone_number_id
        self.api_version = settings.whatsapp_api_version
        self._graph = f"https://graph.facebook.com/{self.api_version}"
        self._base = f"{self._graph}/{self.phone_number_id}"

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.access_token or not self.phone_number_id:
            # Dev convenience: log instead of failing when creds are absent.
            log.warning("whatsapp.client.no_credentials", path=path, payload=payload)
            return {"messages": [{"id": "dev-stub"}]}

        url = f"{self._base}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            log.error(
                "whatsapp.api.error",
                status=resp.status_code,
                body=resp.text,
                payload=payload,
            )
            resp.raise_for_status()
        return resp.json()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=8),
        reraise=True,
    )
    async def send_text(self, to: str, text: str) -> str:
        """Send a plain text message. Returns external message id."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": True, "body": text[:4096]},
        }
        data = await self._post("messages", payload)
        return data.get("messages", [{}])[0].get("id", "")

    async def send_image(self, to: str, image_url: str, caption: str = "") -> str:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "image",
            "image": {"link": image_url, "caption": caption[:1024]},
        }
        data = await self._post("messages", payload)
        return data.get("messages", [{}])[0].get("id", "")

    async def mark_as_read(self, message_id: str) -> None:
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        await self._post("messages", payload)

    async def download_media(
        self,
        media_id: str,
        *,
        timeout_seconds: float = 10.0,
        max_bytes: int = MEDIA_MAX_BYTES,
    ) -> tuple[bytes, str | None] | None:
        """Download a media object (voice note, image…) from Graph.

        Two hops, as Meta requires: `GET /{media_id}` returns a short-lived,
        authenticated `url`, then that url is fetched with the same Bearer
        token. Returns (bytes, mime_type) or None on any failure — callers
        degrade instead of breaking the webhook.

        The body is **streamed** and abandoned as soon as it goes past
        `max_bytes`: this runs inline in the webhook, one request per inbound
        message, so buffering whatever the other end feels like sending would
        be an out-of-memory switch operated by someone else. `timeout_seconds`
        is the budget for the *whole* operation, both hops included — the lead
        is waiting for the reply on the other side.
        """
        if not self.access_token or not media_id:
            log.warning("whatsapp.media.no_credentials", media_id=media_id)
            return None

        headers = {"Authorization": f"Bearer {self.access_token}"}
        try:
            async with (
                asyncio.timeout(timeout_seconds),
                httpx.AsyncClient(timeout=timeout_seconds) as http,
            ):
                meta = await http.get(f"{self._graph}/{media_id}", headers=headers)
                if meta.status_code >= 400:
                    log.warning(
                        "whatsapp.media.metadata_error",
                        status=meta.status_code,
                        body=meta.text[:200],
                    )
                    return None
                info = meta.json()
                url = info.get("url")
                if not url:
                    log.warning("whatsapp.media.no_url", media_id=media_id)
                    return None

                content = await self._read_capped(http, url, headers, max_bytes)
                if content is None:
                    return None
                content, content_type = content
        except Exception:
            log.warning("whatsapp.media.request_failed", media_id=media_id)
            return None

        if not content:
            log.warning("whatsapp.media.empty")
            return None
        return content, (info.get("mime_type") or content_type)

    @staticmethod
    async def _read_capped(
        http: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        max_bytes: int,
    ) -> tuple[bytes, str | None] | None:
        """Stream `url`, stopping the moment it exceeds `max_bytes`."""
        async with http.stream("GET", url, headers=headers) as resp:
            if resp.status_code >= 400:
                log.warning("whatsapp.media.download_error", status=resp.status_code)
                return None

            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                # Announced too big: hang up before reading a single chunk.
                log.warning("whatsapp.media.too_large", size=int(declared))
                return None

            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    log.warning("whatsapp.media.too_large", size=total)
                    return None
                chunks.append(chunk)
        return b"".join(chunks), resp.headers.get("content-type")
