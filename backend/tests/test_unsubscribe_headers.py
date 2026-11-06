"""(233) One-click unsubscribe + per-category email preferences.

Gmail and Outlook degrade the deliverability of *every* message from a
domain that sends bulk mail without RFC 8058 headers — including the
hot-lead alerts that make the customer money — and the LSSI expects a
one-click opt-out on anything commercial. So: lifecycle mail carries the
headers and respects three switches; critical transactional mail (dunning,
payment confirmation, "your number is live") carries neither, because there
is nothing to opt out of.

The two `(subject, html)` builders shared with the lifecycle tasks live here
too — they are the mail the preferences actually govern.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.api.agency_settings import AgencySettingsPatch, _serialize
from app.core.settings import settings
from app.db.session import get_db
from app.main import app
from app.services import emails
from app.services.emails import (
    EMAIL_CATEGORIES,
    UNSUBSCRIBE_MAILTO,
    UNSUBSCRIBE_URL,
    build_resource_usage_alert_email_html,
    build_winback_email_html,
    send_email,
    should_send,
)

AGENCY_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# Headers on the wire
# ---------------------------------------------------------------------------


class _FakeResponse:
    status_code = 200
    text = "{}"

    def json(self) -> dict:
        return {"id": "email_1"}


class _FakeClient:
    last_payload: dict | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        type(self).last_payload = json
        return _FakeResponse()


@pytest.fixture
def resend(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", SecretStr("re_test"))
    monkeypatch.setattr(emails.httpx, "AsyncClient", _FakeClient)
    _FakeClient.last_payload = None
    return _FakeClient


async def test_transactional_email_has_no_unsubscribe_header(resend):
    """Dunning and friends: you cannot opt out of "we could not charge you"."""
    await send_email("a@b.es", "No hemos podido cobrar", "<p>x</p>")
    assert "headers" not in (resend.last_payload or {})


@pytest.mark.parametrize("category", EMAIL_CATEGORIES)
async def test_lifecycle_email_carries_one_click_unsubscribe(resend, category):
    await send_email("a@b.es", "Resumen", "<p>x</p>", category=category)

    sent = resend.last_payload["headers"]
    assert sent["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert f"<mailto:{UNSUBSCRIBE_MAILTO}>" in sent["List-Unsubscribe"]
    assert f"<{UNSUBSCRIBE_URL}>" in sent["List-Unsubscribe"]


async def test_the_unsubscribe_url_points_at_the_preferences_block(resend):
    assert UNSUBSCRIBE_URL.endswith("/billing#emails")


async def test_an_unknown_category_is_treated_as_transactional(resend):
    await send_email("a@b.es", "x", "<p>x</p>", category="nonsense")
    assert "headers" not in (resend.last_payload or {})


async def test_the_body_of_the_email_is_untouched(resend):
    await send_email("a@b.es", "Asunto", "<p>cuerpo</p>", category="digest")
    payload = resend.last_payload
    assert payload["to"] == ["a@b.es"]
    assert payload["subject"] == "Asunto"
    assert payload["html"] == "<p>cuerpo</p>"


async def test_send_email_still_works_without_an_api_key(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", SecretStr(""))
    assert await send_email("a@b.es", "x", "<p>x</p>", category="digest") is False


# ---------------------------------------------------------------------------
# should_send
# ---------------------------------------------------------------------------


def _agency(blob=None):
    return SimpleNamespace(id=AGENCY_ID, name="Inmo Sol", settings=blob)


@pytest.mark.parametrize("category", EMAIL_CATEGORIES)
def test_an_agency_that_never_touched_preferences_gets_everything(category):
    assert should_send(_agency(), category) is True
    assert should_send(_agency({}), category) is True
    assert should_send(_agency({"email_prefs": {}}), category) is True


@pytest.mark.parametrize("category", EMAIL_CATEGORIES)
def test_switching_a_category_off_stops_it(category):
    agency = _agency({"email_prefs": {category: False}})
    assert should_send(agency, category) is False


def test_switching_one_category_off_leaves_the_others_alone():
    agency = _agency({"email_prefs": {"digest": False}})
    assert should_send(agency, "digest") is False
    assert should_send(agency, "alerts") is True
    assert should_send(agency, "lifecycle") is True


def test_transactional_mail_ignores_the_preferences():
    """Even with everything switched off, dunning goes out."""
    agency = _agency({"email_prefs": dict.fromkeys(EMAIL_CATEGORIES, False)})
    assert should_send(agency, None) is True
    assert should_send(agency, "dunning") is True


def test_a_corrupt_preferences_blob_does_not_silence_the_agency():
    assert should_send(_agency({"email_prefs": "off"}), "digest") is True
    assert should_send(_agency("not a dict"), "digest") is True
    assert should_send(SimpleNamespace(), "digest") is True


# ---------------------------------------------------------------------------
# The preferences are readable and patchable from /settings
# ---------------------------------------------------------------------------


class FakeSession:
    def __init__(self, blob):
        self.agency = SimpleNamespace(
            id=AGENCY_ID,
            name="Inmo Sol",
            slug="inmo-sol",
            timezone="Europe/Madrid",
            default_language="es",
            contact_email="info@inmosol.example.com",
            settings=dict(blob),
        )

    async def get(self, model, pk, **kwargs):
        return self.agency if pk == AGENCY_ID else None

    async def commit(self):
        pass

    async def refresh(self, obj):
        pass

    async def rollback(self):
        pass


def _client(monkeypatch, session):
    async def _override():
        yield session

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")
    app.dependency_overrides[get_db] = _override
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_db, None)


def test_get_defaults_to_everything_on():
    out = _serialize(SimpleNamespace(
        name="Inmo Sol",
        slug="inmo-sol",
        timezone="Europe/Madrid",
        default_language="es",
        settings={},
    ))
    assert out.settings.email_prefs.model_dump() == {
        "digest": True,
        "alerts": True,
        "lifecycle": True,
    }


def test_get_reflects_what_was_stored():
    out = _serialize(SimpleNamespace(
        name="Inmo Sol",
        slug="inmo-sol",
        timezone="Europe/Madrid",
        default_language="es",
        settings={"email_prefs": {"digest": False}},
    ))
    assert out.settings.email_prefs.digest is False
    assert out.settings.email_prefs.alerts is True


def test_patch_turns_one_switch_off(monkeypatch):
    db = FakeSession({})
    client = _client(monkeypatch, db)

    resp = client.patch(
        f"/api/v1/agencies/{AGENCY_ID}/settings",
        json={"email_prefs": {"digest": False}},
    )

    assert resp.status_code == 200
    assert resp.json()["settings"]["email_prefs"] == {
        "digest": False,
        "alerts": True,
        "lifecycle": True,
    }
    assert db.agency.settings["email_prefs"]["digest"] is False


def test_patch_merges_instead_of_replacing(monkeypatch):
    """A client that only knows about `digest` must not silently re-enable
    the switches the customer turned off from a newer screen."""
    db = FakeSession({"email_prefs": {"alerts": False, "lifecycle": False}})
    client = _client(monkeypatch, db)

    resp = client.patch(
        f"/api/v1/agencies/{AGENCY_ID}/settings",
        json={"email_prefs": {"digest": False}},
    )

    assert resp.json()["settings"]["email_prefs"] == {
        "digest": False,
        "alerts": False,
        "lifecycle": False,
    }


def test_patch_can_turn_a_switch_back_on(monkeypatch):
    db = FakeSession({"email_prefs": {"digest": False}})
    client = _client(monkeypatch, db)

    resp = client.patch(
        f"/api/v1/agencies/{AGENCY_ID}/settings",
        json={"email_prefs": {"digest": True}},
    )

    assert resp.json()["settings"]["email_prefs"]["digest"] is True


def test_patch_leaves_the_rest_of_the_blob_alone(monkeypatch):
    db = FakeSession({"voice_id": "sonic", "setup_checklist": {"whatsapp": True}})
    client = _client(monkeypatch, db)

    client.patch(
        f"/api/v1/agencies/{AGENCY_ID}/settings",
        json={"email_prefs": {"alerts": False}},
    )

    assert db.agency.settings["voice_id"] == "sonic"
    assert db.agency.settings["setup_checklist"] == {"whatsapp": True}


def test_patch_rejects_a_non_boolean_switch():
    with pytest.raises(ValidationError):
        AgencySettingsPatch(email_prefs={"digest": "maybe"})


def test_an_unknown_switch_is_ignored(monkeypatch):
    db = FakeSession({})
    client = _client(monkeypatch, db)

    resp = client.patch(
        f"/api/v1/agencies/{AGENCY_ID}/settings",
        json={"email_prefs": {"promos": False}},
    )

    assert resp.status_code == 200
    assert "promos" not in resp.json()["settings"]["email_prefs"]


# ---------------------------------------------------------------------------
# Lifecycle builders shared with the workers: these return (subject, html)
# ---------------------------------------------------------------------------


def test_winback_returns_subject_and_html():
    subject, html = build_winback_email_html(
        "Inmo Sol", {"leads": 12, "conversations": 40, "visits": 3}
    )
    assert "Inmo Sol" in subject
    assert html.startswith("<div")
    assert "12" in html and "40" in html and "3" in html
    assert "www.selai.es/billing" in html


def test_winback_survives_missing_or_junk_stats():
    """A background task must not die because one counter came back None."""
    subject, html = build_winback_email_html("Inmo Sol", {})
    assert subject and html
    _, html = build_winback_email_html("Inmo Sol", {"leads": None, "visits": "x"})
    assert "0" in html


def test_winback_does_not_invent_a_discount():
    """Honest tone: we have no coupon to give here, so we do not imply one."""
    _, html = build_winback_email_html("Inmo Sol", {"leads": 1})
    lowered = html.lower()
    assert "gratis" not in lowered
    assert "descuento" not in lowered


def test_resource_alert_returns_subject_and_html():
    subject, html = build_resource_usage_alert_email_html("Inmo Sol", "properties", 180, 200)
    assert "propiedades" in subject
    assert "180" in html and "200" in html
    assert "90%" in html


def test_resource_alert_says_when_the_limit_is_reached():
    _, html = build_resource_usage_alert_email_html("Inmo Sol", "agents", 5, 5)
    assert "límite" in html
    assert "usuarios" in html


def test_resource_alert_falls_back_to_the_raw_resource_name():
    subject, html = build_resource_usage_alert_email_html("Inmo Sol", "webhooks", 9, 10)
    assert "webhooks" in subject
    assert "webhooks" in html


def test_resource_alert_survives_a_zero_limit():
    _, html = build_resource_usage_alert_email_html("Inmo Sol", "properties", 3, 0)
    assert "Inmo Sol" in html
