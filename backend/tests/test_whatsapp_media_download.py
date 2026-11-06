"""`WhatsAppClient.download_media` must never trust the size of what it is
being handed.

The download runs inline in the webhook, once per inbound message, with the
lead waiting on the other side. Reading the whole body and *then* measuring
it is not a cap: by the time the check runs the bytes are already in RAM, so
a single oversized (or endless) media object is an out-of-memory switch
operated by someone else. The stream has to stop mid-flight.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.channels.whatsapp import client as client_module
from app.channels.whatsapp.client import MEDIA_MAX_BYTES, WhatsAppClient

MEDIA_ID = "media-abc-123"
CHUNK = b"x" * (256 * 1024)  # 256 KiB


@pytest.fixture
def graph(monkeypatch):
    """Serves the two Graph hops from an in-memory transport.

    `state` scripts the binary hop; `state.chunks_served` is how much of the
    body the client actually pulled — the point of the whole exercise.
    """

    class State:
        media_url = "https://lookaside.fbsbx.com/whatsapp_business/attachments/x"
        metadata: dict = {"url": media_url, "mime_type": "audio/ogg; codecs=opus"}
        metadata_status = 200
        binary_status = 200
        total_chunks = 4
        headers: dict[str, str] = {"content-type": "audio/ogg"}
        chunks_served = 0

    state = State()

    async def _body():
        for _ in range(state.total_chunks):
            state.chunks_served += 1
            yield CHUNK

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(MEDIA_ID):
            return httpx.Response(state.metadata_status, json=state.metadata)
        return httpx.Response(
            state.binary_status, headers=state.headers, content=_body()
        )

    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(client_module.httpx, "AsyncClient", factory)
    return state


def _download(**kwargs):
    client = WhatsAppClient(access_token="EAAG-token", phone_number_id="pnid-1")
    return asyncio.run(client.download_media(MEDIA_ID, **kwargs))


def test_downloads_a_normal_voice_note(graph):
    audio, mime = _download()
    assert audio == CHUNK * 4
    assert mime == "audio/ogg; codecs=opus"
    assert graph.chunks_served == 4


def test_mime_falls_back_to_the_response_content_type(graph):
    graph.metadata = {"url": graph.media_url}
    _, mime = _download()
    assert mime == "audio/ogg"


def test_body_over_the_cap_is_abandoned_mid_stream(graph):
    """No content-length to warn us: the only defence is counting bytes as
    they arrive and hanging up."""
    graph.total_chunks = 10_000  # 2.5 GiB if we were foolish enough
    graph.headers = {"content-type": "audio/ogg"}

    assert _download(max_bytes=len(CHUNK) * 3) is None
    # Four chunks: three fit, the fourth crossed the line and ended it.
    assert graph.chunks_served == 4


def test_declared_content_length_over_the_cap_reads_nothing(graph):
    graph.headers = {
        "content-type": "audio/ogg",
        "content-length": str(MEDIA_MAX_BYTES + 1),
    }

    assert _download() is None
    assert graph.chunks_served == 0


def test_default_cap_is_metas_own_media_limit():
    """16 MB is Meta's ceiling for audio/image/video, so anything above it is
    not a voice note we failed to read — it is something else entirely."""
    assert MEDIA_MAX_BYTES == 16 * 1024 * 1024


def test_metadata_error_returns_none(graph):
    graph.metadata_status = 401
    assert _download() is None
    assert graph.chunks_served == 0


def test_binary_error_returns_none(graph):
    graph.binary_status = 403
    assert _download() is None


def test_missing_url_returns_none(graph):
    graph.metadata = {"mime_type": "audio/ogg"}
    assert _download() is None
    assert graph.chunks_served == 0


def test_empty_body_returns_none(graph):
    graph.total_chunks = 0
    assert _download() is None


def test_no_access_token_never_calls_graph(graph):
    client = WhatsAppClient(access_token="", phone_number_id="pnid-1")
    assert asyncio.run(client.download_media(MEDIA_ID)) is None
    assert graph.chunks_served == 0


def test_slow_media_cannot_hold_the_webhook_open(monkeypatch):
    """The timeout is a budget for the whole download, not per request hop:
    a lead is waiting for the reply while this runs. Deliberately does not
    use the `graph` fixture — it patches the same attribute."""

    async def _crawl():
        await asyncio.sleep(5)
        yield CHUNK

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(MEDIA_ID):
            return httpx.Response(200, json={"url": "https://lookaside.test/f"})
        return httpx.Response(200, content=_crawl())

    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(client_module.httpx, "AsyncClient", factory)

    async def _run():
        client = WhatsAppClient(access_token="EAAG-token", phone_number_id="pnid-1")
        started = asyncio.get_running_loop().time()
        result = await client.download_media(MEDIA_ID, timeout_seconds=0.2)
        return result, asyncio.get_running_loop().time() - started

    result, elapsed = asyncio.run(_run())
    assert result is None
    assert elapsed < 2
