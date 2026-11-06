"""Item 195 — "Comprobar mi web" actually checks the web.

Step 7 of the wizard used to end with a button of honour: the owner said
"ya lo he puesto" and we marked it done. A chat that never appeared on the
customer's site is churn nobody sees coming.

This endpoint fetches the URL the agency declared and looks for the snippet
with *its own* slug. Because it fetches a URL a user chose, from inside our
network, the SSRF guard is as much of the feature as the parsing — half of
this file is about what we refuse to fetch.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import channels as channels_module
from app.api.channels import find_widget_slug
from app.core.settings import settings
from app.db.session import get_db
from app.main import app
from app.services import url_guard

SNIPPET = '<script src="https://www.selai.es/widget.js" data-selai="inmo-sol" async></script>'


# ---------------------------------------------------------------------------
# Finding the snippet — pure
# ---------------------------------------------------------------------------


def test_the_snippet_we_hand_out_is_recognised():
    html = f"<html><body><p>Hola</p>{SNIPPET}</body></html>"
    assert find_widget_slug(html) == "inmo-sol"


def test_quoting_and_spacing_do_not_matter():
    """The snippet goes through WordPress plugins and page builders that
    rewrite attributes; refusing their output would fail real installs."""
    variants = [
        "<script data-selai='inmo-sol' src='/widget.js'></script>",
        '<SCRIPT SRC="https://www.selai.es/widget.js" DATA-SELAI="inmo-sol"></SCRIPT>',
        '<script\n  src="https://www.selai.es/widget.js"\n  data-selai = "inmo-sol"\n  async\n></script>',
        "<script src=https://www.selai.es/widget.js data-selai=inmo-sol async></script>",
    ]
    for html in variants:
        assert find_widget_slug(html) == "inmo-sol", html


def test_a_page_without_the_widget_finds_nothing():
    assert find_widget_slug("<html><body>Hola</body></html>") is None
    assert find_widget_slug("") is None


def test_two_unrelated_scripts_do_not_add_up_to_an_install():
    """One script loads widget.js, another carries a data-selai. Neither is
    the snippet, and reporting "instalado" here would be a lie the owner
    only discovers when a lead never arrives."""
    html = (
        '<script src="https://www.selai.es/widget.js"></script>'
        '<script src="/otra-cosa.js" data-selai="inmo-sol"></script>'
    )
    assert find_widget_slug(html) is None


def test_another_agencys_snippet_is_reported_as_such():
    html = SNIPPET.replace("inmo-sol", "otra-inmo")
    assert find_widget_slug(html) == "otra-inmo"


# ---------------------------------------------------------------------------
# The SSRF guard
# ---------------------------------------------------------------------------


@pytest.fixture
def dns(monkeypatch):
    """Pin name resolution so the guard is tested, not the tester's DNS."""
    mapping = {"www.inmosol.example.com": ["93.184.216.34"]}

    async def _fake_getaddrinfo(host, port, **kwargs):
        addresses = mapping.get(host)
        if addresses is None:
            raise OSError("NXDOMAIN")
        return [(2, 1, 6, "", (addr, 0)) for addr in addresses]

    class _Loop:
        async def getaddrinfo(self, host, port, **kwargs):
            return await _fake_getaddrinfo(host, port, **kwargs)

    monkeypatch.setattr(url_guard.asyncio, "get_running_loop", lambda: _Loop())
    return mapping


async def test_a_public_https_url_is_allowed(dns):
    assert await channels_module._validate_target("https://www.inmosol.example.com/") is not None


async def test_a_bare_domain_is_treated_as_https(dns):
    target = await channels_module._validate_target("www.inmosol.example.com")
    assert target is not None
    assert target.startswith("https://")


async def test_loopback_and_private_ranges_are_refused(dns):
    dns["evil.example"] = ["127.0.0.1"]
    assert await channels_module._validate_target("http://evil.example/") is None

    dns["intranet.example"] = ["10.0.0.7"]
    assert await channels_module._validate_target("http://intranet.example/") is None


async def test_the_cloud_metadata_endpoint_is_refused(dns):
    """169.254.169.254 hands out the machine's credentials to anything that
    can make it issue a GET. This endpoint issues GETs for a living."""
    dns["metadata.example"] = ["169.254.169.254"]
    assert await channels_module._validate_target("http://metadata.example/") is None
    assert await channels_module._validate_target("http://169.254.169.254/") is None


async def test_a_host_with_one_public_and_one_private_address_is_refused(dns):
    """Every resolved address is checked: otherwise a hostname could pass on
    the public one and be connected to on the private one."""
    dns["mixed.example"] = ["93.184.216.34", "192.168.1.5"]
    assert await channels_module._validate_target("http://mixed.example/") is None


async def test_non_http_schemes_are_refused(dns):
    for url in ("file:///etc/passwd", "ftp://www.inmosol.example.com/", "gopher://x/"):
        assert await channels_module._validate_target(url) is None


async def test_credentials_in_the_url_are_refused(dns):
    """`https://user:pass@host/` would hand the agency's credentials to
    whatever host we end up talking to."""
    assert (
        await channels_module._validate_target("https://u:p@www.inmosol.example.com/") is None
    )


async def test_a_host_that_does_not_resolve_is_refused(dns):
    assert await channels_module._validate_target("https://no-existe.example/") is None


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------


class FakeSession:
    def __init__(self, agency):
        self.agency = agency
        self.commits = 0

    async def get(self, model, pk):
        return self.agency if pk == self.agency.id else None

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


@pytest.fixture
def agency():
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Inmo Sol",
        slug="inmo-sol",
        contact_email="dueno@inmosol.example.com",
        settings={},
    )


@pytest.fixture
def client(monkeypatch, agency):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")
    session = FakeSession(agency)

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    yield SimpleNamespace(http=TestClient(app), session=session)
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def page(monkeypatch):
    """Scriptable stand-in for the download."""
    state = SimpleNamespace(html=None, url="https://www.inmosol.example.com/", calls=[])

    async def _fake_fetch(url):
        state.calls.append(url)
        return state.html, state.url

    monkeypatch.setattr(channels_module, "fetch_page", _fake_fetch)

    async def _allow(url):
        return url if url.startswith("http") else None

    monkeypatch.setattr(channels_module, "_validate_target", _allow)
    return state


def _url(agency) -> str:
    return f"/api/v1/agencies/{agency.id}/channels/webchat/verify"


def test_finding_the_snippet_marks_the_step_verified(client, agency, page):
    page.html = f"<html>{SNIPPET}</html>"

    resp = client.http.post(_url(agency), json={"url": "https://www.inmosol.example.com"})
    assert resp.status_code == 200
    assert resp.json() == {
        "verified": True,
        "url": "https://www.inmosol.example.com/",
        "reason": "ok",
    }

    checklist = agency.settings["setup_checklist"]
    assert checklist["webchat_verified"] is True
    # The wizard's own marker too, so the progress bar cannot disagree with
    # the badge next to it.
    assert checklist["webchat"] is True
    assert agency.settings["webchat_verified_url"] == "https://www.inmosol.example.com/"
    assert agency.settings["webchat_verified_at"]
    assert client.session.commits == 1


def test_a_page_without_the_widget_is_not_verified(client, agency, page):
    page.html = "<html><body>Nuestra web</body></html>"

    body = client.http.post(_url(agency), json={"url": "https://www.inmosol.example.com"}).json()
    assert body["verified"] is False
    assert body["reason"] == "widget_not_found"
    assert "setup_checklist" not in agency.settings
    assert client.session.commits == 0


def test_someone_elses_snippet_is_called_out(client, agency, page):
    """Copy-pasted from another agency's site, or from our docs: the chat
    would work and every lead would land in the wrong inbox."""
    page.html = SNIPPET.replace("inmo-sol", "otra-inmo")

    body = client.http.post(_url(agency), json={"url": "https://www.inmosol.example.com"}).json()
    assert body["verified"] is False
    assert body["reason"] == "other_slug"
    assert "setup_checklist" not in agency.settings


def test_a_site_we_cannot_load_says_so(client, agency, page):
    page.html = None

    body = client.http.post(_url(agency), json={"url": "https://www.inmosol.example.com"}).json()
    assert body["verified"] is False
    assert body["reason"] == "unreachable"


def test_a_url_the_guard_refuses_never_reaches_the_fetcher(
    client, agency, page, monkeypatch
):
    async def _refuse(url):
        return None

    monkeypatch.setattr(channels_module, "_validate_target", _refuse)

    body = client.http.post(_url(agency), json={"url": "http://localhost:8000"}).json()
    assert body["verified"] is False
    assert body["reason"] == "blocked_url"
    assert page.calls == []


def test_verification_shows_up_in_the_channels_status(client, agency, page):
    page.html = f"<html>{SNIPPET}</html>"
    client.http.post(_url(agency), json={"url": "https://www.inmosol.example.com"})

    # GET /channels reads the same settings; the dashboard needs it to show
    # "Comprobado" without re-running the check on every page load.
    checklist = agency.settings["setup_checklist"]
    assert checklist["webchat_verified"] is True


# ---------------------------------------------------------------------------
# The fetch itself — redirects, pinning, and the size ceiling
# ---------------------------------------------------------------------------


@pytest.fixture
def transport(monkeypatch):
    """Drive `fetch_page` against a scripted transport instead of the network."""
    state = SimpleNamespace(requests=[], responses={}, default=None)

    def _handler(request: httpx.Request) -> httpx.Response:
        state.requests.append(request)
        key = f"{request.headers.get('host')}{request.url.path}"
        response = state.responses.get(key) or state.default
        if response is None:
            raise AssertionError(f"unscripted request: {key}")
        return response() if callable(response) else response

    def _client():
        return httpx.AsyncClient(
            transport=httpx.MockTransport(_handler),
            timeout=1.0,
            follow_redirects=False,
        )

    monkeypatch.setattr(channels_module, "_verify_client", _client)
    return state


async def test_the_request_goes_to_the_address_we_validated(dns, transport):
    """DNS rebinding: the name resolves public while we check it and private
    a millisecond later, when the HTTP client resolves it again. Connecting
    to the address the guard saw — with the real host in `Host` and in the
    SNI — is what closes that."""
    transport.default = httpx.Response(200, text=f"<html>{SNIPPET}</html>")

    html, final_url = await channels_module.fetch_page("https://www.inmosol.example.com/")

    assert html is not None
    assert final_url == "https://www.inmosol.example.com/"
    sent = transport.requests[0]
    assert sent.url.host == "93.184.216.34"
    assert sent.headers["host"] == "www.inmosol.example.com"
    assert sent.extensions["sni_hostname"] == "www.inmosol.example.com"


async def test_a_redirect_into_the_private_network_is_refused(dns, transport):
    """An open redirect on the agency's own site would otherwise walk us
    straight into the network the first check just refused."""
    dns["intranet.example"] = ["10.0.0.7"]
    transport.default = httpx.Response(
        302, headers={"location": "http://intranet.example/admin"}
    )

    html, _final = await channels_module.fetch_page("https://www.inmosol.example.com/")

    assert html is None
    # One request only: the second hop never left the process.
    assert len(transport.requests) == 1


async def test_a_relative_redirect_is_followed_on_the_same_host(dns, transport):
    transport.responses["www.inmosol.example.com/"] = httpx.Response(
        301, headers={"location": "/es/inicio"}
    )
    transport.responses["www.inmosol.example.com/es/inicio"] = httpx.Response(
        200, text=f"<html>{SNIPPET}</html>"
    )

    html, final_url = await channels_module.fetch_page("https://www.inmosol.example.com/")

    assert find_widget_slug(html or "") == "inmo-sol"
    assert final_url == "https://www.inmosol.example.com/es/inicio"


async def test_a_redirect_loop_gives_up(dns, transport):
    transport.default = httpx.Response(302, headers={"location": "/otra"})

    html, _final = await channels_module.fetch_page("https://www.inmosol.example.com/")

    assert html is None
    assert len(transport.requests) == channels_module._VERIFY_MAX_REDIRECTS + 1


async def test_a_huge_page_is_not_pulled_into_memory(dns, transport):
    """`resp.text[:1MB]` would already have downloaded whatever the other
    end decided to serve. The read stops at the ceiling instead."""
    chunk = b"x" * 65536
    consumed = SimpleNamespace(count=0)

    async def _body():
        for _ in range(64):  # 4 MB if nobody stops reading
            consumed.count += 1
            yield chunk

    transport.default = lambda: httpx.Response(200, content=_body())

    html, _final = await channels_module.fetch_page("https://www.inmosol.example.com/")

    assert html is not None
    assert len(html) == channels_module._VERIFY_MAX_BYTES
    assert consumed.count <= (channels_module._VERIFY_MAX_BYTES // len(chunk)) + 1


async def test_a_non_web_port_is_refused(dns, transport):
    """The guard is not a port scanner for hire: a customer website answers
    on a web port, and everything else is a typo or a probe."""
    assert await channels_module._validate_target("https://www.inmosol.example.com:22/") is None
    assert await channels_module._validate_target("https://www.inmosol.example.com:9200/") is None
    assert await channels_module._validate_target("https://www.inmosol.example.com:8443/") is not None
    assert transport.requests == []


async def test_a_malformed_port_is_refused(dns):
    assert await channels_module._validate_target("https://www.inmosol.example.com:notaport/") is None
