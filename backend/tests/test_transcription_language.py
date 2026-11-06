"""El audio en español no puede transcribirse como si fuera inglés (n32).

nova-3 sin parámetro `language` cae a su modelo por defecto, que es inglés.
Una nota de voz de un cliente español volvía como una transcripción fonética
sin sentido — y el agente contestaba a eso. En el teléfono es peor: la llamada
va en directo.

Todo el producto se vende en español (y en catalán, inglés, francés y alemán:
los cinco idiomas del plan Pro), así que la petición SIEMPRE lleva idioma.
"""
from __future__ import annotations

import pytest

from app.core.settings import settings
from app.services import transcription

pytestmark = pytest.mark.anyio


class _Resp:
    status_code = 200

    def json(self):
        return {"results": {"channels": [{"alternatives": [{"transcript": "hola"}]}]}}


@pytest.fixture
def captured(monkeypatch):
    seen: dict = {}

    class _Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, params=None, headers=None, content=None):
            seen["params"] = params
            return _Resp()

    monkeypatch.setattr(settings, "deepgram_api_key", type(settings.deepgram_api_key)("dg_test"))
    monkeypatch.setattr(transcription.httpx, "AsyncClient", _Client)
    return seen


async def test_a_request_without_an_explicit_language_still_carries_one(captured):
    await transcription.transcribe_audio(b"audio")
    assert captured["params"]["language"] == "multi"


async def test_an_explicit_language_wins(captured):
    await transcription.transcribe_audio(b"audio", language="es")
    assert captured["params"]["language"] == "es"


def test_the_phone_assistant_is_created_with_a_language_too():
    """El transcriptor de Vapi tenía el mismo agujero, y ahí la llamada es
    en directo con un cliente al teléfono."""
    from pathlib import Path

    source = Path(transcription.__file__).parent.parent / "services" / "onboarding.py"
    text = source.read_text(encoding="utf-8")
    idx = text.index('"transcriber"')
    fragmento = text[idx : idx + 200]
    assert '"language"' in fragmento, fragmento[:120]
