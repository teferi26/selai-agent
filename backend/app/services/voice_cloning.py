"""ElevenLabs voice cloning.

Workflow:
1. Agency uploads 30-60s of clean audio of their top performer (with that
   person's signed consent — required by ElevenLabs Terms).
2. We POST it to ElevenLabs Instant Voice Cloning.
3. Save the resulting `voice_id` on `agency.settings.elevenlabs_voice_id`.
4. The agency configures their Vapi assistant `voice = elevenlabs/<voice_id>`.

This module wraps the API call. UI/legal consent capture lives in the
dashboard.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from app.core.logging import get_logger
from app.core.settings import settings

log = get_logger(__name__)
_ELEVENLABS_URL = "https://api.elevenlabs.io/v1/voices/add"


def _api_key() -> str:
    return settings.elevenlabs_api_key.get_secret_value()


@dataclass
class VoiceCloneResult:
    voice_id: str
    name: str


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=8),
    reraise=True,
)
async def clone_voice(
    *,
    name: str,
    description: str,
    audio_files: list[tuple[str, bytes, str]],  # [(filename, data, content_type)]
) -> VoiceCloneResult:
    api_key = _api_key()
    if not api_key:
        log.warning("voice_cloning.no_api_key")
        return VoiceCloneResult(voice_id="dev-stub", name=name)

    headers = {"xi-api-key": api_key}
    data = {"name": name, "description": description}
    files = [
        ("files", (filename, content, ctype))
        for (filename, content, ctype) in audio_files
    ]

    async with httpx.AsyncClient(timeout=60) as http:
        resp = await http.post(_ELEVENLABS_URL, headers=headers, data=data, files=files)
    if resp.status_code >= 400:
        log.error("voice_cloning.error", status=resp.status_code, body=resp.text[:200])
        resp.raise_for_status()
    body = resp.json()
    return VoiceCloneResult(voice_id=body.get("voice_id", ""), name=name)
