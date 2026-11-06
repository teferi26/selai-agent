"""Item 162 — WhatsApp voice notes reach the agent as text.

The parser exposes the audio media id, the route downloads it from Graph
and transcribes it with Deepgram before `handle_inbound`. Every step is
best-effort: any failure degrades to the previous "[audio]" behaviour
instead of breaking the webhook.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import whatsapp as wa_module
from app.channels.whatsapp.parser import parse_whatsapp_webhook
from app.core.settings import settings
from app.db.session import get_db
from app.main import app

WA_ID = "34600111222"
MEDIA_ID = "media-abc-123"
AUDIO_BYTES = b"OggS-fake-voice-note"


def _audio_body(*, media_id: str | None = MEDIA_ID) -> dict:
    audio: dict = {"mime_type": "audio/ogg; codecs=opus", "voice": True}
    if media_id is not None:
        audio["id"] = media_id
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "pnid-agency"},
                            "contacts": [{"wa_id": WA_ID, "profile": {"name": "Juan"}}],
                            "messages": [
                                {
                                    "from": WA_ID,
                                    "id": "wamid.AUDIO1",
                                    "type": "audio",
                                    "audio": audio,
                                }
                            ],
                        }
                    }
                ]
            }
        ]
    }


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parser_exposes_audio_media_id():
    payload = parse_whatsapp_webhook(_audio_body())[0]
    assert payload.raw["audio_media_id"] == MEDIA_ID
    assert payload.raw["audio_mime_type"] == "audio/ogg; codecs=opus"
    # Text is still the placeholder until the route transcribes it.
    assert payload.text == "[audio]"


def test_parser_audio_without_id_has_no_media_id():
    payload = parse_whatsapp_webhook(_audio_body(media_id=None))[0]
    assert "audio_media_id" not in payload.raw


def test_parser_text_message_has_no_media_id():
    body = _audio_body()
    body["entry"][0]["changes"][0]["value"]["messages"][0] = {
        "from": WA_ID,
        "id": "wamid.TEXT1",
        "type": "text",
        "text": {"body": "Hola"},
    }
    payload = parse_whatsapp_webhook(body)[0]
    assert "audio_media_id" not in payload.raw


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value

    def first(self):
        # Only the (173) dedup lookup calls this: nothing stored yet.
        return None


class FakeSession:
    def __init__(self, agency):
        self.agency = agency

    async def execute(self, stmt):
        if "messages" in str(stmt).lower():
            return _ScalarResult(None)  # dedup lookup: not seen before
        return _ScalarResult(self.agency)

    async def commit(self):
        pass

    async def rollback(self):
        pass


def _fake_task_session(session):
    """The background task's own session (item 173), faked."""

    @asynccontextmanager
    async def _factory():
        yield session
        await session.commit()

    return _factory


class RecordingClient:
    """WhatsAppClient stand-in whose download_media is scripted per test."""

    instances: list[RecordingClient] = []
    download_result: object = None
    download_exc: Exception | None = None

    def __init__(self, access_token=None, phone_number_id=None):
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.downloads: list[str] = []
        self.sent: list[tuple[str, str]] = []
        type(self).instances.append(self)

    async def download_media(self, media_id, **kwargs):
        self.downloads.append(media_id)
        if type(self).download_exc is not None:
            raise type(self).download_exc
        return type(self).download_result

    async def send_text(self, to, text):
        self.sent.append((to, text))
        return "wamid.OUT"

    async def mark_as_read(self, message_id):
        pass


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "whatsapp_app_secret", SecretStr(""))
    monkeypatch.setattr(wa_module, "WhatsAppClient", RecordingClient)
    RecordingClient.instances = []
    RecordingClient.download_result = (AUDIO_BYTES, "audio/ogg; codecs=opus")
    RecordingClient.download_exc = None

    handled: list = []

    async def fake_handle(db, payload):
        handled.append(payload)
        return SimpleNamespace(
            reply_text="Te llamo ahora.", conversation_id=uuid.uuid4(), handed_off=False
        )

    monkeypatch.setattr(wa_module, "handle_inbound", fake_handle)

    calls: list[dict] = []
    result = SimpleNamespace(transcript="Hola, quiero ver el piso de Malasaña")

    async def fake_transcribe(data, *, content_type="audio/ogg", **kwargs):
        calls.append({"data": data, "content_type": content_type, **kwargs})
        if isinstance(result.transcript, Exception):
            raise result.transcript
        return result.transcript

    monkeypatch.setattr(wa_module, "transcribe_audio", fake_transcribe)

    agency = SimpleNamespace(
        id=uuid.uuid4(),
        slug="inmo-sol",
        settings={
            "whatsapp_access_token": "EAAG-agency",
            "whatsapp_phone_number_id": "pnid-agency",
        },
    )

    session = FakeSession(agency)
    monkeypatch.setattr(wa_module, "task_session", _fake_task_session(session))
    # (216) Debounce off: this module is about transcription, not timing.
    monkeypatch.setattr(wa_module, "DEBOUNCE_SECONDS", 0)

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    yield SimpleNamespace(handled=handled, transcriptions=calls, result=result)
    app.dependency_overrides.pop(get_db, None)


# Dev + empty app secret: any *well-formed* signature header passes (the
# "sha256=" + 64 hex shape is checked before the body is even parsed).
DEV_SIGNATURE = "sha256=" + "0" * 64


def _post(body: dict):
    client = TestClient(app)
    return client.post(
        "/webhooks/whatsapp", json=body, headers={"X-Hub-Signature-256": DEV_SIGNATURE}
    )


def test_voice_note_reaches_the_agent_as_text(env):
    resp = _post(_audio_body())
    assert resp.status_code == 200

    assert RecordingClient.instances[0].downloads == [MEDIA_ID]
    assert env.transcriptions[0]["data"] == AUDIO_BYTES
    assert env.transcriptions[0]["content_type"] == "audio/ogg; codecs=opus"
    # Short timeout: a voice note blocks the reply.
    assert env.transcriptions[0]["timeout_seconds"] == wa_module._TRANSCRIPTION_TIMEOUT

    assert len(env.handled) == 1
    assert env.handled[0].text == "[Nota de voz] Hola, quiero ver el piso de Malasaña"


def test_download_failure_degrades_to_audio_placeholder(env):
    RecordingClient.download_result = None

    resp = _post(_audio_body())
    assert resp.status_code == 200
    assert env.transcriptions == []
    assert env.handled[0].text == "[audio]"
    # The conversation still happens: the lead gets an answer.
    assert RecordingClient.instances[0].sent == [(WA_ID, "Te llamo ahora.")]


def test_download_exception_degrades_cleanly(env):
    RecordingClient.download_exc = RuntimeError("graph exploded")

    resp = _post(_audio_body())
    assert resp.status_code == 200
    assert env.handled[0].text == "[audio]"


def test_transcription_failure_degrades_cleanly(env):
    env.result.transcript = RuntimeError("deepgram down")

    resp = _post(_audio_body())
    assert resp.status_code == 200
    assert env.handled[0].text == "[audio]"


def test_empty_transcript_keeps_placeholder(env):
    """No Deepgram key or unintelligible audio: never send "[Nota de voz] "
    with nothing after it."""
    env.result.transcript = "   "

    resp = _post(_audio_body())
    assert resp.status_code == 200
    assert env.handled[0].text == "[audio]"


def test_media_is_downloaded_with_the_agency_token(env):
    """Media ids only resolve for the app that received the message: reading
    the agency's voice note with our global token is a 401 from Graph, and
    the lead silently gets "[audio]" instead of an answer."""
    resp = _post(_audio_body())
    assert resp.status_code == 200

    downloader = RecordingClient.instances[0]
    assert downloader.downloads == [MEDIA_ID]
    assert downloader.access_token == "EAAG-agency"
    assert downloader.phone_number_id == "pnid-agency"


def test_media_for_our_own_number_uses_the_global_token(env):
    """Inbound on the number *we* own (the agency connected a different one):
    its stored credentials are not valid there, for the download either."""
    body = _audio_body()
    body["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"] = "pnid-ours"

    resp = _post(body)
    assert resp.status_code == 200

    downloader = RecordingClient.instances[0]
    assert downloader.downloads == [MEDIA_ID]
    assert downloader.access_token is None  # global env-configured client
    assert downloader.phone_number_id is None


def test_text_message_never_downloads_media(env):
    body = _audio_body()
    body["entry"][0]["changes"][0]["value"]["messages"][0] = {
        "from": WA_ID,
        "id": "wamid.TEXT1",
        "type": "text",
        "text": {"body": "Hola"},
    }

    resp = _post(body)
    assert resp.status_code == 200
    assert RecordingClient.instances[0].downloads == []
    assert env.transcriptions == []
    assert env.handled[0].text == "Hola"
