from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from cryptography.fernet import Fernet
from pydantic import SecretStr

from alembic import command
from app.core.settings import settings
from app.scripts.rewrap_tenant_credentials import (
    _UPDATE_ONE_KEY,
    _UPDATE_WEBHOOK_SECRET,
    CredentialRewrapError,
    RewrapAction,
    WebhookSecretRewrapAction,
    apply_rewrap_actions,
    apply_webhook_rewrap_actions,
    plan_rewrap_actions,
    plan_webhook_rewrap_actions,
    run,
)
from app.services.secret_store import SecretKeyConfigurationError, decrypt_secret

TENANT_KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
ROOT_SECRET = "migration-root-secret-32-chars!!"


@pytest.fixture(autouse=True)
def explicit_keyring(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "secret_key", SecretStr(ROOT_SECRET))
    monkeypatch.setattr(settings, "tenant_credential_active_key_id", "primary")
    monkeypatch.setattr(
        settings,
        "tenant_credential_keys",
        {"primary": SecretStr(TENANT_KEY)},
    )
    monkeypatch.setattr(settings, "tenant_credential_write_mode", "v2")


def _legacy_envelope(value: str) -> str:
    derived = hashlib.sha256(b"selai:agency-settings:v1\x00" + ROOT_SECRET.encode("utf-8")).digest()
    token = Fernet(base64.urlsafe_b64encode(derived)).encrypt(value.encode()).decode()
    return f"enc:v1:{token}"


def test_preflight_plans_plaintext_and_v1_without_touching_other_settings():
    rows = [
        {
            "id": "agency-1",
            "settings": {
                "ordinary_setting": "visible",
                "twilio_auth_token": "plaintext-token",
                "whatsapp_app_secret": _legacy_envelope("legacy-secret"),
            },
        }
    ]

    actions = plan_rewrap_actions(rows)

    assert [(action.setting_key, decrypt_secret(action.replacement)) for action in actions] == [
        ("twilio_auth_token", "plaintext-token"),
        ("whatsapp_app_secret", "legacy-secret"),
    ]
    assert "plaintext-token" not in repr(actions[0])
    assert all(action.replacement.startswith("enc:v2:primary:") for action in actions)


def test_wrong_legacy_key_aborts_during_preflight_before_any_write(monkeypatch):
    rows = [
        {
            "id": "agency-1",
            "settings": {"google_calendar_refresh_token": _legacy_envelope("refresh-token")},
        }
    ]
    monkeypatch.setattr(settings, "secret_key", SecretStr("different-root-secret-32-chars!!"))

    with pytest.raises(CredentialRewrapError, match="cannot be decrypted"):
        plan_rewrap_actions(rows)


def test_same_key_concurrency_aborts_the_transaction():
    class FakeConnection:
        def execute(self, statement, params):
            return SimpleNamespace(rowcount=0)

    action = RewrapAction(
        agency_id="agency-1",
        setting_key="twilio_auth_token",
        expected="old",
        replacement="new",
    )

    with pytest.raises(CredentialRewrapError, match="concurrent credential update"):
        apply_rewrap_actions(FakeConnection(), [action])  # type: ignore[arg-type]


def test_online_update_changes_one_json_key_with_compare_and_swap():
    sql = str(_UPDATE_ONE_KEY).casefold()
    assert "jsonb_set" in sql
    assert "settings ->>" in sql
    assert "setting_key" in sql
    assert "expected" in sql


def test_webhook_preflight_rewraps_a_plaintext_signing_secret():
    actions = plan_webhook_rewrap_actions(
        [{"id": "subscription-1", "secret": "plain-signing-secret"}]
    )

    assert len(actions) == 1
    assert decrypt_secret(actions[0].replacement) == "plain-signing-secret"
    assert "plain-signing-secret" not in repr(actions[0])


def test_unreadable_webhook_secret_aborts_during_preflight():
    with pytest.raises(CredentialRewrapError, match="cannot be decrypted"):
        plan_webhook_rewrap_actions(
            [{"id": "subscription-1", "secret": "enc:v2:missing:not-a-token"}]
        )


def test_webhook_secret_compare_and_swap_detects_a_race():
    class FakeConnection:
        def execute(self, statement, params):
            return SimpleNamespace(rowcount=0)

    action = WebhookSecretRewrapAction(
        subscription_id="subscription-1",
        expected="old",
        replacement="new",
    )

    with pytest.raises(CredentialRewrapError, match="concurrent signing secret update"):
        apply_webhook_rewrap_actions(FakeConnection(), [action])  # type: ignore[arg-type]


def test_webhook_secret_update_is_a_compare_and_swap():
    sql = str(_UPDATE_WEBHOOK_SECRET).casefold()
    assert "where id = :subscription_id" in sql
    assert "secret = :expected" in sql
    assert "secret = :replacement" in sql


def test_apply_refuses_to_run_before_writes_switch_to_v2(monkeypatch):
    monkeypatch.setattr(settings, "tenant_credential_write_mode", "plaintext")

    with pytest.raises(SecretKeyConfigurationError, match="WRITE_MODE must be v2"):
        run(apply=True)


def test_alembic_revision_can_render_in_offline_sql_mode(capsys):
    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))

    command.upgrade(config, "20260801_0015:20260831_0017", sql=True)

    rendered = capsys.readouterr().out
    assert "20260831_0016" in rendered
    assert "20260831_0017" in rendered
