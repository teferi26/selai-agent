from __future__ import annotations

import base64
import hashlib

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.core.settings import settings
from app.services.secret_store import (
    ENCRYPTED_PREFIX,
    LEGACY_ENCRYPTED_PREFIX,
    SecretDecryptionError,
    SecretKeyConfigurationError,
    decrypt_secret,
    encrypt_secret,
    encrypt_secret_settings,
    ensure_explicit_tenant_keyring,
    rewrap_secret,
    secret_from_settings,
)

OLD_KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
NEW_KEY = "ICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8="
ROOT_SECRET = "test-root-secret-32-chars-long!!"


@pytest.fixture(autouse=True)
def stable_root_secret(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "secret_key", SecretStr(ROOT_SECRET))
    monkeypatch.setattr(settings, "tenant_credential_active_key_id", "")
    monkeypatch.setattr(settings, "tenant_credential_keys", {})
    monkeypatch.setattr(settings, "tenant_credential_write_mode", "v2")
    monkeypatch.setattr(settings, "tenant_credential_plaintext_write_ack", False)


def _configure_keyring(monkeypatch, active: str, keys: dict[str, str]) -> None:
    monkeypatch.setattr(settings, "tenant_credential_active_key_id", active)
    monkeypatch.setattr(
        settings,
        "tenant_credential_keys",
        {key_id: SecretStr(value) for key_id, value in keys.items()},
    )


def _legacy_envelope(value: str) -> str:
    derived = hashlib.sha256(b"selai:agency-settings:v1\x00" + ROOT_SECRET.encode("utf-8")).digest()
    token = Fernet(base64.urlsafe_b64encode(derived)).encrypt(value.encode("utf-8")).decode("ascii")
    return f"{LEGACY_ENCRYPTED_PREFIX}{token}"


def test_round_trip_uses_an_authenticated_envelope():
    encrypted = encrypt_secret("super-secret-token")

    assert encrypted.startswith(ENCRYPTED_PREFIX)
    assert encrypted != "super-secret-token"
    assert "super-secret-token" not in encrypted
    assert decrypt_secret(encrypted) == "super-secret-token"


def test_new_envelope_names_the_active_dedicated_key(monkeypatch):
    _configure_keyring(monkeypatch, "key-2026-09", {"key-2026-09": OLD_KEY})

    encrypted = encrypt_secret("token")

    assert encrypted.startswith("enc:v2:key-2026-09:")
    assert decrypt_secret(encrypted) == "token"


def test_expand_mode_reads_v2_but_keeps_new_writes_plaintext(monkeypatch):
    _configure_keyring(monkeypatch, "key-2026-09", {"key-2026-09": OLD_KEY})
    encrypted = encrypt_secret("already-encrypted")
    monkeypatch.setattr(settings, "tenant_credential_write_mode", "plaintext")

    assert decrypt_secret(encrypted) == "already-encrypted"
    assert encrypt_secret("new-during-expand") == "new-during-expand"


def test_encryption_is_randomized():
    assert encrypt_secret("same-token") != encrypt_secret("same-token")


def test_legacy_plaintext_remains_readable():
    assert decrypt_secret("legacy-token") == "legacy-token"
    assert secret_from_settings({"twilio_auth_token": "legacy-token"}, "twilio_auth_token") == (
        "legacy-token"
    )


def test_legacy_v1_envelope_remains_readable():
    assert decrypt_secret(_legacy_envelope("legacy-v1-token")) == "legacy-v1-token"


def test_empty_values_stay_empty():
    assert encrypt_secret("") == ""
    assert decrypt_secret(None) == ""
    assert secret_from_settings({}, "whatsapp_access_token") == ""


def test_valid_envelope_is_not_encrypted_twice():
    encrypted = encrypt_secret("token")

    assert encrypt_secret(encrypted) == encrypted


@pytest.mark.parametrize("value", ["enc:v1:", "enc:v1:not-a-fernet-token", "enc:v1:\u00f1"])
def test_corrupt_envelope_fails_closed(value):
    with pytest.raises(SecretDecryptionError, match="stored_secret_cannot_be_decrypted"):
        decrypt_secret(value)


def test_wrong_root_secret_cannot_decrypt(monkeypatch):
    encrypted = encrypt_secret("token")
    monkeypatch.setattr(settings, "secret_key", SecretStr("another-root-secret-32-chars!!!"))

    with pytest.raises(SecretDecryptionError):
        decrypt_secret(encrypted)


def test_wrong_dedicated_key_cannot_decrypt(monkeypatch):
    _configure_keyring(monkeypatch, "primary", {"primary": OLD_KEY})
    encrypted = encrypt_secret("token")
    _configure_keyring(monkeypatch, "primary", {"primary": NEW_KEY})

    with pytest.raises(SecretDecryptionError):
        decrypt_secret(encrypted)


def test_rotation_reads_old_key_and_rewraps_to_active_key(monkeypatch):
    _configure_keyring(monkeypatch, "old", {"old": OLD_KEY})
    old_envelope = encrypt_secret("rotating-token")

    _configure_keyring(monkeypatch, "new", {"old": OLD_KEY, "new": NEW_KEY})
    new_envelope = rewrap_secret(old_envelope)

    assert decrypt_secret(old_envelope) == "rotating-token"
    assert new_envelope.startswith("enc:v2:new:")
    assert decrypt_secret(new_envelope) == "rotating-token"

    _configure_keyring(monkeypatch, "new", {"new": NEW_KEY})
    assert decrypt_secret(new_envelope) == "rotating-token"
    with pytest.raises(SecretDecryptionError):
        decrypt_secret(old_envelope)


def test_rewrap_upgrades_plaintext_and_v1(monkeypatch):
    _configure_keyring(monkeypatch, "primary", {"primary": OLD_KEY})

    plaintext = rewrap_secret("plain-token")
    legacy = rewrap_secret(_legacy_envelope("legacy-token"))

    assert plaintext.startswith("enc:v2:primary:")
    assert legacy.startswith("enc:v2:primary:")
    assert decrypt_secret(plaintext) == "plain-token"
    assert decrypt_secret(legacy) == "legacy-token"
    assert rewrap_secret(legacy) == legacy


def test_migration_preflight_requires_an_explicit_keyring():
    with pytest.raises(SecretKeyConfigurationError, match="TENANT_CREDENTIAL"):
        ensure_explicit_tenant_keyring()


def test_unknown_encrypted_version_fails_closed():
    with pytest.raises(SecretDecryptionError):
        decrypt_secret("enc:v99:key:token")


def test_batch_helper_only_encrypts_named_credentials():
    protected = encrypt_secret_settings(
        {
            "whatsapp_access_token": "meta-token",
            "twilio_auth_token": "twilio-token",
            "whatsapp_phone_number_id": "12345",
            "ordinary_setting": "visible",
        }
    )

    assert decrypt_secret(protected["whatsapp_access_token"]) == "meta-token"
    assert decrypt_secret(protected["twilio_auth_token"]) == "twilio-token"
    assert protected["whatsapp_phone_number_id"] == "12345"
    assert protected["ordinary_setting"] == "visible"


def test_unknown_key_cannot_be_read_through_secret_helper():
    with pytest.raises(KeyError, match="not a protected settings key"):
        secret_from_settings({"ordinary_setting": "visible"}, "ordinary_setting")
