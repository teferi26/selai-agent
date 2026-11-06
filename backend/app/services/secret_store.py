"""Authenticated, rotatable encryption for credentials in ``agency.settings``.

New writes use ``enc:v2:<key-id>:<fernet-token>``.  The key id selects one
entry from the dedicated tenant credential keyring, so operators can add a new
key, switch writes, rewrap stored values and only then remove the previous key.
Legacy plaintext and ``enc:v1`` values remain readable during that rollout.
Unknown or malformed encrypted envelopes always fail closed.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

from app.core.settings import (
    LOCAL_DERIVED_TENANT_KEY_ID,
    settings,
    tenant_credential_keyring_issues,
)

LEGACY_ENCRYPTED_PREFIX = "enc:v1:"
ENCRYPTED_PREFIX = "enc:v2:"
SECRET_SETTINGS_KEYS = frozenset(
    {
        "google_calendar_refresh_token",
        "twilio_auth_token",
        "whatsapp_access_token",
        "whatsapp_app_secret",
    }
)

_LEGACY_KEY_DOMAIN = b"selai:agency-settings:v1\x00"
_LOCAL_KEY_DOMAIN = b"selai:agency-settings:local-v2\x00"


class SecretDecryptionError(RuntimeError):
    """A stored encrypted credential cannot be authenticated or decrypted."""


class SecretKeyConfigurationError(RuntimeError):
    """The tenant credential keyring is absent or invalid."""


def _legacy_fernet() -> Fernet:
    root_secret = settings.secret_key.get_secret_value().encode("utf-8")
    derived_key = hashlib.sha256(_LEGACY_KEY_DOMAIN + root_secret).digest()
    return Fernet(base64.urlsafe_b64encode(derived_key))


def _key_value(value: SecretStr | str) -> str:
    return value.get_secret_value() if isinstance(value, SecretStr) else str(value)


def _keyring(*, require_explicit: bool = False) -> tuple[str, dict[str, Fernet]]:
    required = require_explicit or settings.app_env == "production"
    issues = tenant_credential_keyring_issues(settings, require_explicit=required)
    if issues:
        raise SecretKeyConfigurationError("tenant_credential_keyring_invalid: " + "; ".join(issues))

    active = settings.tenant_credential_active_key_id.strip()
    configured = dict(settings.tenant_credential_keys or {})
    if not active and not configured:
        # Development and CI remain zero-config.  This reserved key can never
        # pass production validation or the explicit migration preflight.
        root = settings.secret_key.get_secret_value().encode("utf-8")
        derived = hashlib.sha256(_LOCAL_KEY_DOMAIN + root).digest()
        return LOCAL_DERIVED_TENANT_KEY_ID, {
            LOCAL_DERIVED_TENANT_KEY_ID: Fernet(base64.urlsafe_b64encode(derived))
        }

    return active, {
        key_id: Fernet(_key_value(material).encode("ascii"))
        for key_id, material in configured.items()
    }


def ensure_explicit_tenant_keyring() -> str:
    """Validate the migration/rotation keyring and return its active key id."""
    active, _ = _keyring(require_explicit=True)
    return active


def _decrypt_token(fernet: Fernet, token: str) -> str:
    try:
        return fernet.decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError, ValueError) as exc:
        raise SecretDecryptionError("stored_secret_cannot_be_decrypted") from exc


def _parse_v2(value: str) -> tuple[str, str]:
    parts = value.split(":", 3)
    if len(parts) != 4 or parts[0] != "enc" or parts[1] != "v2" or not parts[2] or not parts[3]:
        raise SecretDecryptionError("stored_secret_cannot_be_decrypted")
    return parts[2], parts[3]


def decrypt_secret(value: str | None) -> str:
    """Return one credential, accepting legacy plaintext but not corrupt envelopes."""
    if not value:
        return ""
    if value.startswith(LEGACY_ENCRYPTED_PREFIX):
        token = value.removeprefix(LEGACY_ENCRYPTED_PREFIX)
        return _decrypt_token(_legacy_fernet(), token)
    if value.startswith(ENCRYPTED_PREFIX):
        key_id, token = _parse_v2(value)
        _, keyring = _keyring()
        fernet = keyring.get(key_id)
        if fernet is None:
            raise SecretDecryptionError("stored_secret_cannot_be_decrypted")
        return _decrypt_token(fernet, token)
    if value.startswith("enc:"):
        raise SecretDecryptionError("stored_secret_cannot_be_decrypted")
    return value


def _encrypt_plaintext(value: str) -> str:
    active_key_id, keyring = _keyring()
    token = keyring[active_key_id].encrypt(value.encode("utf-8")).decode("ascii")
    return f"{ENCRYPTED_PREFIX}{active_key_id}:{token}"


def encrypt_secret(value: str | None) -> str:
    """Encrypt a new credential while preserving any valid stored envelope."""
    if not value:
        return ""
    if value.startswith("enc:"):
        decrypt_secret(value)
        return value
    if settings.tenant_credential_write_mode == "plaintext":
        # Expand phase only: every process first learns to read v2 while old
        # binaries can still consume new writes. Production requires the
        # explicit acknowledgement checked in verify_production_secrets.
        return value
    return _encrypt_plaintext(value)


def encrypt_secret_strict(value: str) -> str:
    """Always return an authenticated v2 envelope, even during expand mode.

    The temporary plaintext write mode exists for credential migrations where
    old binaries still need to read new rows.  It is never appropriate for a
    GDPR compensation outbox: provider locators can themselves be personal
    data or bearer URLs and there is no legacy consumer for that table.
    """
    if value.startswith("enc:"):
        value = decrypt_secret(value)
    return _encrypt_plaintext(value)


def rewrap_secret(value: str | None) -> str:
    """Return a valid credential under the active v2 key.

    Plaintext, legacy v1 and v2 values under an older configured key are
    upgraded.  A v2 value already using the active key is authenticated and
    returned byte-for-byte so the command is idempotent.
    """
    if not value:
        return ""
    active_key_id, _ = _keyring(require_explicit=True)
    if value.startswith(ENCRYPTED_PREFIX):
        key_id, _ = _parse_v2(value)
        plaintext = decrypt_secret(value)
        if key_id == active_key_id:
            return value
        return _encrypt_plaintext(plaintext)
    return _encrypt_plaintext(decrypt_secret(value))


def encrypt_secret_settings(updates: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a settings update and encrypt only credential values it contains."""
    protected = dict(updates)
    for key in SECRET_SETTINGS_KEYS.intersection(protected):
        raw = protected[key]
        protected[key] = encrypt_secret(None if raw is None else str(raw))
    return protected


def secret_from_settings(cfg: Mapping[str, Any] | None, key: str) -> str:
    """Read a named tenant credential without ever exposing unrelated settings."""
    if key not in SECRET_SETTINGS_KEYS:
        raise KeyError(f"{key} is not a protected settings key")
    raw = (cfg or {}).get(key)
    return decrypt_secret(None if raw is None else str(raw))
