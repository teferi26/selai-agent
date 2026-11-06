"""Mobile push-token registration and privacy-safe logout cleanup."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api.devices import (
    DeviceRegistration,
    DeviceUnregistration,
    register_device,
    unregister_device,
)
from app.core.auth import AuthContext

TOKEN = "ExponentPushToken[device-install-123]"


class _ScalarResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self, existing=None):
        self.existing = existing
        self.added: list = []
        self.statements: list = []
        self.commits = 0

    async def execute(self, statement):
        self.statements.append(statement)
        return _ScalarResult(self.existing)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _auth(user_id: uuid.UUID | None) -> AuthContext:
    user = SimpleNamespace(id=user_id) if user_id else None
    return AuthContext(
        clerk_user_id="clerk-user",
        email="owner@example.com",
        raw_claims={},
        user=user,
        dev_bypass=user is None,
    )


@pytest.mark.parametrize("platform", ["web", "windows", "IOS", ""])
def test_registration_rejects_non_mobile_platforms(platform):
    with pytest.raises(ValidationError):
        DeviceRegistration(expo_push_token=TOKEN, platform=platform)


@pytest.mark.parametrize("token", ["", "short", " " * 20, "x" * 256])
def test_device_payload_rejects_unusable_tokens(token):
    with pytest.raises(ValidationError):
        DeviceUnregistration(expo_push_token=token)


async def test_registration_repoints_an_existing_install_to_the_current_user():
    old_agency = uuid.uuid4()
    agency_id = uuid.uuid4()
    user_id = uuid.uuid4()
    existing = SimpleNamespace(agency_id=old_agency, user_id=None, platform="ios")
    db = FakeSession(existing)

    result = await register_device(
        agency_id,
        DeviceRegistration(expo_push_token=TOKEN, platform="android"),
        _auth(user_id),
        db,
    )

    assert result == {"ok": True}
    assert existing.agency_id == agency_id
    assert existing.user_id == user_id
    assert existing.platform == "android"
    assert db.commits == 1


async def test_registration_creates_a_token_owned_by_the_signed_in_user():
    agency_id = uuid.uuid4()
    user_id = uuid.uuid4()
    db = FakeSession()

    await register_device(
        agency_id,
        DeviceRegistration(expo_push_token=TOKEN, platform="ios"),
        _auth(user_id),
        db,
    )

    assert len(db.added) == 1
    row = db.added[0]
    assert row.agency_id == agency_id
    assert row.user_id == user_id
    assert row.expo_push_token == TOKEN
    assert db.commits == 1


async def test_logout_deletes_only_this_users_token_in_this_agency():
    agency_id = uuid.uuid4()
    user_id = uuid.uuid4()
    db = FakeSession()

    response = await unregister_device(
        agency_id,
        DeviceUnregistration(expo_push_token=TOKEN),
        _auth(user_id),
        db,
    )

    assert response.status_code == 204
    assert db.commits == 1
    compiled = db.statements[0].compile()
    assert TOKEN in compiled.params.values()
    assert agency_id in compiled.params.values()
    assert user_id in compiled.params.values()
    sql = str(compiled)
    assert "device_tokens.user_id IS NULL" in sql
    assert " OR " in sql


async def test_logout_is_idempotent_for_the_local_dev_session():
    agency_id = uuid.uuid4()
    db = FakeSession()

    response = await unregister_device(
        agency_id,
        DeviceUnregistration(expo_push_token=TOKEN),
        _auth(None),
        db,
    )

    assert response.status_code == 204
    assert db.commits == 1
    compiled = db.statements[0].compile()
    assert TOKEN in compiled.params.values()
    assert agency_id in compiled.params.values()
    assert "device_tokens.user_id" not in str(compiled)
