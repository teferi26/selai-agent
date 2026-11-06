"""Voice catalog + preview endpoint — body validation and provider gating.

No Postgres needed: dev-mode auth bypass (no Clerk JWKS configured) plus a
fake session that answers the one query the endpoint makes — the plan
lookup behind the `voice` feature gate. Whether that gate blocks Starter is
covered in test_plan_gating.py; here it always passes (no subscription row).
"""
from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.api import voice_settings as vs_module
from app.api.voice_settings import (
    _VOICE_CATALOG,
    PREVIEWABLE_PROVIDERS,
    CatalogVoice,
    VoicePreviewBody,
)
from app.core.settings import settings
from app.db.session import get_db
from app.main import app

AGENCY = uuid.uuid4()
URL = f"/api/v1/agencies/{AGENCY}/voice/preview"


class _NoSubscription:
    def scalar_one_or_none(self):
        return None


class FakeSession:
    async def execute(self, stmt):
        assert "subscriptions" in str(stmt)
        return _NoSubscription()

    async def rollback(self):
        pass


@pytest.fixture
def client(monkeypatch):
    # Force the dev auth path (X-Dev-User trusted headers, no DB lookup).
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")

    async def _override():
        yield FakeSession()

    app.dependency_overrides[get_db] = _override
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# (3)(20) The catalog is honest about what can be heard
#
# The landing page promises "escuchas voces naturales con muestra de audio".
# Three of the seven entries were Cartesia voices the preview endpoint answers
# 501 for, and two of those three were not Cartesia voice ids at all (Cartesia
# identifies voices by UUID), so choosing one wrote a voiceId nothing could
# resolve into the agency's Vapi assistant.
# ---------------------------------------------------------------------------


def test_every_catalog_voice_says_whether_it_can_be_previewed():
    assert all(isinstance(v.previewable, bool) for v in _VOICE_CATALOG)


def test_previewable_matches_what_the_endpoint_will_actually_do():
    """The flag is the endpoint's rule, not a second copy of it. If they ever
    disagree the UI offers a play button that 501s."""
    for voice in _VOICE_CATALOG:
        expected = voice.preview_url is not None or voice.provider in PREVIEWABLE_PROVIDERS
        assert voice.previewable is expected, voice.id


def test_the_flag_cannot_be_forged_by_the_catalog_author():
    """`previewable` is computed, so a hand-written `previewable=True` on a
    provider we cannot synthesize is impossible rather than merely discouraged."""
    voice = CatalogVoice(
        id="whatever",
        provider="cartesia",
        name="X",
        language="es",
        gender="female",
        description="d",
        previewable=True,
    )
    assert voice.previewable is False


def test_only_the_default_assistant_voice_lacks_a_preview():
    """`_provision_vapi_assistant` assigns cartesia/sonic-multilingual to every
    new assistant, so it has to stay listed even though it cannot be heard;
    every *other* entry must be previewable."""
    silent = [v.id for v in _VOICE_CATALOG if not v.previewable]
    assert silent == ["sonic-multilingual"]


def test_the_voice_new_assistants_are_provisioned_with_is_in_the_catalog():
    """Otherwise a freshly-onboarded agency opens the voice screen and sees
    nothing selected, because its assistant's voice is not on the list. Read
    off the provisioning source so changing one side fails here."""
    import inspect
    import re

    from app.services import onboarding

    source = inspect.getsource(onboarding._provision_vapi_assistant)
    match = re.search(r'"voice":\s*\{"provider":\s*"([^"]+)",\s*"voiceId":\s*"([^"]+)"\}', source)
    assert match, "the Vapi voice payload changed shape; update this test"
    provider, voice_id = match.groups()

    assert any(v.provider == provider and v.id == voice_id for v in _VOICE_CATALOG)


def test_no_invented_cartesia_voice_ids_came_back():
    """Cartesia voice ids are UUIDs; `sonic-*` strings are model names. Only
    the one the assistant is actually provisioned with may remain."""
    cartesia = {v.id for v in _VOICE_CATALOG if v.provider == "cartesia"}
    assert cartesia == {"sonic-multilingual"}


def test_the_catalog_keeps_both_genders_available():
    genders = {v.gender for v in _VOICE_CATALOG}
    assert {"male", "female"} <= genders


def test_catalog_endpoint_serializes_previewable(client):
    resp = client.get("/api/v1/voice/catalog")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == len(_VOICE_CATALOG)
    assert all("previewable" in v for v in body)
    assert [v["id"] for v in body if not v["previewable"]] == ["sonic-multilingual"]


# ---------------------------------------------------------------------------
# Body validation (pure pydantic — no HTTP)
# ---------------------------------------------------------------------------


def test_preview_body_rejects_text_over_300_chars():
    with pytest.raises(ValidationError):
        VoicePreviewBody(provider="11labs", voice_id="v1", text="a" * 301)


def test_preview_body_rejects_empty_text():
    with pytest.raises(ValidationError):
        VoicePreviewBody(provider="11labs", voice_id="v1", text="")


def test_preview_body_accepts_300_chars():
    body = VoicePreviewBody(provider="11labs", voice_id="v1", text="a" * 300)
    assert len(body.text) == 300


# ---------------------------------------------------------------------------
# Endpoint behavior
# ---------------------------------------------------------------------------


def _payload(**overrides) -> dict:
    base = {"provider": "11labs", "voice_id": "EXAVITQu4vr4xnSDxMaL", "text": "Hola, soy tu agente."}
    return {**base, **overrides}


def test_unknown_provider_is_501(client, monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", SecretStr("xi-test"))
    resp = client.post(URL, json=_payload(provider="cartesia"))
    assert resp.status_code == 501
    assert resp.json()["detail"] == "preview_unavailable"


def test_missing_api_key_is_501(client, monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", SecretStr(""))
    resp = client.post(URL, json=_payload())
    assert resp.status_code == 501
    assert resp.json()["detail"] == "preview_unavailable"


def test_text_over_300_chars_is_422(client):
    resp = client.post(URL, json=_payload(text="a" * 301))
    assert resp.status_code == 422


class _FakeResponse:
    status_code = 200
    content = b"ID3fake-mp3-bytes"
    text = ""


class _FakeOkClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        _FakeOkClient.last_call = {"url": url, "json": json, "headers": headers}
        return _FakeResponse()


class _FakeBoomClient(_FakeOkClient):
    async def post(self, url, json=None, headers=None):
        raise httpx.ConnectTimeout("no route to elevenlabs")


def test_elevenlabs_preview_returns_mpeg_audio(client, monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", SecretStr("xi-test"))
    monkeypatch.setattr(vs_module.httpx, "AsyncClient", _FakeOkClient)

    resp = client.post(URL, json=_payload())
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/mpeg"
    assert resp.content == b"ID3fake-mp3-bytes"

    call = _FakeOkClient.last_call
    assert call["url"].endswith("/v1/text-to-speech/EXAVITQu4vr4xnSDxMaL")
    assert call["json"]["model_id"] == "eleven_multilingual_v2"
    assert call["headers"]["xi-api-key"] == "xi-test"


def test_provider_failure_is_502(client, monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", SecretStr("xi-test"))
    monkeypatch.setattr(vs_module.httpx, "AsyncClient", _FakeBoomClient)

    resp = client.post(URL, json=_payload())
    assert resp.status_code == 502
    assert resp.json()["detail"] == "preview_provider_failed"
