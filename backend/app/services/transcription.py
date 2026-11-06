"""Audio transcription via Deepgram.

Deepgram is the same vendor we'll use for real-time voice in Sprint 3,
keeping vendor count down. Supports ES/EN/FR and auto language detect.
If no API key is configured we return a sentinel so the caller can fall
back gracefully.
"""
from __future__ import annotations

import httpx

from app.core.logging import get_logger
from app.core.settings import settings

log = get_logger(__name__)

_DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"


async def transcribe_audio(
    data: bytes,
    *,
    content_type: str = "audio/ogg",
    language: str | None = None,
    timeout_seconds: float = 60.0,
) -> str:
    """Transcribe `data`, or "" on any failure (no key, provider error…).

    `timeout_seconds` is short for interactive paths (a WhatsApp voice note
    blocks the reply) and long for background ones (post-visit notes).
    """
    api_key = settings.deepgram_api_key.get_secret_value()
    if not api_key:
        log.warning("transcription.no_api_key")
        return ""

    params = {
        "model": "nova-3",
        "smart_format": "true",
        "punctuate": "true",
        # (n32) Sin `language`, nova-3 cae a su modelo por defecto: INGLÉS.
        # Todo el producto es español, así que una nota de voz de un cliente
        # volvía como una transcripción fonética sin sentido, y el agente
        # contestaba a eso. `multi` detecta el idioma (incluye español,
        # catalán, inglés, francés y alemán, que son los cinco que vendemos)
        # en vez de arriesgar un 400 con un código monolingüe que el modelo
        # no soporte.
        "language": language or "multi",
    }

    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": content_type,
    }

    async with httpx.AsyncClient(timeout=timeout_seconds) as http:
        resp = await http.post(_DEEPGRAM_URL, params=params, headers=headers, content=data)
    if resp.status_code >= 400:
        log.error("transcription.error", status=resp.status_code, body=resp.text[:200])
        return ""
    data_json = resp.json()
    try:
        return (
            data_json["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
        )
    except (KeyError, IndexError):
        return ""
