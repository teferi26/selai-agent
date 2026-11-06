"""An existing Vapi assistant must match the recording-notice setting."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import agency_settings as settings_api
from app.core.settings import settings
from app.db.session import get_db
from app.main import app
from app.services import onboarding

AGENCY_ID = uuid.uuid4()


class FakeSession:
    def __init__(self, agency_settings: dict, *, fail_commit: bool = False):
        self.agency = SimpleNamespace(
            id=AGENCY_ID,
            name="Inmo Sol",
            slug="inmo-sol",
            timezone="Europe/Madrid",
            default_language="es",
            settings=dict(agency_settings),
        )
        self.commits = 0
        self.rollbacks = 0
        self.fail_commit = fail_commit
        self.locked = False

    async def get(self, model, pk, **kwargs):
        self.locked = kwargs.get("with_for_update") is True
        return self.agency if pk == AGENCY_ID else None

    async def commit(self):
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("database unavailable")

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, obj):
        pass


def _client(monkeypatch, db: FakeSession, *, raise_server_exceptions: bool = True):
    async def _override():
        yield db

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")
    app.dependency_overrides[get_db] = _override
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_db, None)


def test_existing_assistant_is_synced_before_the_setting_is_saved(monkeypatch):
    calls: list[dict] = []

    async def _sync(assistant_id, **kwargs):
        calls.append({"assistant_id": assistant_id, **kwargs})
        return True

    monkeypatch.setattr(settings_api, "sync_vapi_first_message", _sync)
    db = FakeSession({"vapi_assistant_id": "asst_1", "recording_notice": False})

    response = _client(monkeypatch, db).patch(
        f"/api/v1/agencies/{AGENCY_ID}/settings",
        json={"recording_notice": True},
    )

    assert response.status_code == 200
    assert response.json()["settings"]["recording_notice"] is True
    assert db.locked is True
    assert db.commits == 1
    assert calls == [
        {
            "assistant_id": "asst_1",
            "agency_name": "Inmo Sol",
            "recording_notice": True,
        }
    ]


def test_provider_failure_does_not_save_a_false_compliance_state(monkeypatch):
    async def _fail(*args, **kwargs):
        return False

    monkeypatch.setattr(settings_api, "sync_vapi_first_message", _fail)
    before = {"vapi_assistant_id": "asst_1", "recording_notice": False}
    db = FakeSession(before)

    response = _client(monkeypatch, db).patch(
        f"/api/v1/agencies/{AGENCY_ID}/settings",
        json={"recording_notice": True},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "recording_notice_sync_failed"
    assert db.agency.settings == before
    assert db.commits == 0
    assert db.rollbacks == 1


def test_unchanged_notice_does_not_make_a_provider_call(monkeypatch):
    async def _unexpected(*args, **kwargs):
        raise AssertionError("Vapi should not be called")

    monkeypatch.setattr(settings_api, "sync_vapi_first_message", _unexpected)
    db = FakeSession({"vapi_assistant_id": "asst_1"})

    response = _client(monkeypatch, db).patch(
        f"/api/v1/agencies/{AGENCY_ID}/settings",
        json={"recording_notice": True},
    )

    assert response.status_code == 200
    assert db.commits == 1


def test_notice_is_saved_without_remote_sync_before_an_assistant_exists(monkeypatch):
    async def _unexpected(*args, **kwargs):
        raise AssertionError("Vapi should not be called")

    monkeypatch.setattr(settings_api, "sync_vapi_first_message", _unexpected)
    db = FakeSession({})

    response = _client(monkeypatch, db).patch(
        f"/api/v1/agencies/{AGENCY_ID}/settings",
        json={"recording_notice": False},
    )

    assert response.status_code == 200
    assert response.json()["settings"]["recording_notice"] is False


def test_database_failure_compensates_the_provider_change(monkeypatch):
    calls: list[bool] = []

    async def _sync(assistant_id, *, agency_name, recording_notice):
        calls.append(recording_notice)
        return True

    monkeypatch.setattr(settings_api, "sync_vapi_first_message", _sync)
    db = FakeSession(
        {"vapi_assistant_id": "asst_1", "recording_notice": False},
        fail_commit=True,
    )

    response = _client(monkeypatch, db, raise_server_exceptions=False).patch(
        f"/api/v1/agencies/{AGENCY_ID}/settings",
        json={"recording_notice": True},
    )

    assert response.status_code == 500
    assert calls == [True, False]
    assert db.rollbacks == 1


class _FakeVapiResponse:
    def __init__(self, status_code: int, first_message: str | None):
        self.status_code = status_code
        self.text = "provider response"
        self.first_message = first_message

    def json(self):
        return {"firstMessage": self.first_message}


class _FakeVapiClient:
    status_code = 200
    calls: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def patch(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return _FakeVapiResponse(self.status_code, json.get("firstMessage") if json else None)


async def test_vapi_sync_patches_only_the_first_message(monkeypatch):
    monkeypatch.setattr(settings, "vapi_api_key", SecretStr("vapi-test"))
    monkeypatch.setattr(onboarding.httpx, "AsyncClient", _FakeVapiClient)
    _FakeVapiClient.status_code = 200
    _FakeVapiClient.calls.clear()

    ok = await onboarding.sync_vapi_first_message(
        "asst_1",
        agency_name="Inmo Sol",
        recording_notice=True,
    )

    assert ok is True
    assert _FakeVapiClient.calls[0]["url"].endswith("/assistant/asst_1")
    assert set(_FakeVapiClient.calls[0]["json"]) == {"firstMessage"}
    assert _FakeVapiClient.calls[0]["json"]["firstMessage"].startswith(
        onboarding.RECORDING_NOTICE_PREFIX
    )
    assert "vapi-test" not in repr(_FakeVapiClient.calls[0]["json"])


async def test_vapi_sync_reports_provider_rejection(monkeypatch):
    monkeypatch.setattr(settings, "vapi_api_key", SecretStr("vapi-test"))
    monkeypatch.setattr(onboarding.httpx, "AsyncClient", _FakeVapiClient)
    _FakeVapiClient.status_code = 503
    _FakeVapiClient.calls.clear()

    assert not await onboarding.sync_vapi_first_message(
        "asst_1",
        agency_name="Inmo Sol",
        recording_notice=True,
    )


async def test_vapi_sync_rejects_a_200_that_did_not_store_the_message(monkeypatch):
    class _IgnoringClient(_FakeVapiClient):
        async def patch(self, url, json=None, headers=None):
            return _FakeVapiResponse(200, "old greeting")

    monkeypatch.setattr(settings, "vapi_api_key", SecretStr("vapi-test"))
    monkeypatch.setattr(onboarding.httpx, "AsyncClient", _IgnoringClient)

    assert not await onboarding.sync_vapi_first_message(
        "asst_1",
        agency_name="Inmo Sol",
        recording_notice=True,
    )


async def test_vapi_sync_fails_closed_without_an_api_key(monkeypatch):
    monkeypatch.setattr(settings, "vapi_api_key", SecretStr(""))

    assert not await onboarding.sync_vapi_first_message(
        "asst_1",
        agency_name="Inmo Sol",
        recording_notice=True,
    )
