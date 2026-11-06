"""Settings normalization + the production fail-fast (item 229)."""

import pytest
from pydantic import SecretStr

from app.core import settings as settings_module
from app.core.settings import (
    InsecureProductionConfigError,
    Settings,
    _active_legal_review_blockers,
    verify_production_secrets,
)

VALID_SECRET = "8f3c1d9e-not-a-placeholder-6d44f1b0"
VALID_TENANT_KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


def test_plain_postgresql_url_gets_asyncpg_driver():
    s = Settings(database_url="postgresql://u:p@host:5432/db")
    assert s.database_url == "postgresql+asyncpg://u:p@host:5432/db"


def test_heroku_style_postgres_url_gets_asyncpg_driver():
    s = Settings(database_url="postgres://u:p@host:5432/db")
    assert s.database_url == "postgresql+asyncpg://u:p@host:5432/db"


def test_explicit_asyncpg_url_untouched():
    url = "postgresql+asyncpg://u:p@host:5432/db"
    assert Settings(database_url=url).database_url == url


def test_sync_url_property_for_alembic():
    s = Settings(database_url="postgresql://u:p@host:5432/db")
    assert s.database_url_sync == "postgresql+psycopg://u:p@host:5432/db"


def test_clerk_issuer_is_derived_from_the_instance_jwks_origin():
    s = Settings(
        clerk_jwks_url=(
            "https://renewing-bobcat-00.clerk.accounts.dev/.well-known/jwks.json"
        )
    )
    assert s.clerk_expected_issuer == (
        "https://renewing-bobcat-00.clerk.accounts.dev"
    )


def test_explicit_clerk_issuer_supports_a_generic_jwks_endpoint():
    s = Settings(
        clerk_jwks_url="https://api.clerk.com/v1/jwks",
        clerk_issuer="https://renewing-bobcat-00.clerk.accounts.dev/",
    )
    assert s.clerk_expected_issuer == (
        "https://renewing-bobcat-00.clerk.accounts.dev"
    )


# ---------------------------------------------------------------------------
# Item 229 — production refuses placeholder webhook auth and missing Clerk /
# Stripe credentials. Better a loud crash at boot than private routes nobody
# can enter or a payment that Stripe accepts but the local plan never applies.
# ---------------------------------------------------------------------------


def _settings(env: str, secret: str, **overrides) -> Settings:
    values = {
        "app_env": env,
        "secret_key": SecretStr(secret),
        "tenant_credential_active_key_id": "primary-2026",
        "tenant_credential_keys": {"primary-2026": SecretStr(VALID_TENANT_KEY)},
        "tenant_credential_write_mode": "v2",
        "tenant_credential_plaintext_write_ack": False,
        "clerk_jwks_url": "https://clerk.example.test/.well-known/jwks.json",
        "stripe_secret_key": SecretStr("sk_live_test-value"),
        "stripe_webhook_secret": SecretStr("whsec_test-value"),
        "stripe_billing_portal_configuration_id": "bpc_test-value",
        "s3_bucket": "selai-test-private",
        "legal_company_name": "Selai Provider Test SL",
        "legal_company_vat": "B12345678",
        "legal_company_address": "Calle Prueba 1, 03001 Alicante",
        "legal_company_city": "Alicante",
        "legal_dpo_email": "legal@provider.dev",
        "legal_review_confirmed": True,
        "stripe_tax_setup_confirmed": True,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize(
    "secret",
    [
        "change-me",
        "change-me-in-prod",
        "CHANGE-ME",
        "",
        "   ",
        "too-short-but-not-placeholder",
        "a" * 64,
    ],
)
def test_production_refuses_to_start_on_a_placeholder_secret(secret):
    with pytest.raises(InsecureProductionConfigError):
        verify_production_secrets(_settings("production", secret))


def test_the_error_says_what_to_do():
    """A crash nobody can act on is just a crash."""
    with pytest.raises(InsecureProductionConfigError) as exc:
        verify_production_secrets(_settings("production", "change-me"))
    assert "SECRET_KEY" in str(exc.value)


def test_production_starts_with_real_credentials_and_final_legal_docs(monkeypatch):
    monkeypatch.setattr(settings_module, "_active_legal_review_blockers", lambda: [])
    verify_production_secrets(_settings("production", VALID_SECRET))


@pytest.mark.parametrize("bucket", ["", " ", "\t"])
def test_production_requires_persistent_object_storage(bucket):
    with pytest.raises(InsecureProductionConfigError) as exc:
        verify_production_secrets(
            _settings("production", VALID_SECRET, s3_bucket=bucket)
        )

    assert "S3_BUCKET" in str(exc.value)


@pytest.mark.parametrize("env", ["development", "staging"])
def test_non_production_can_use_local_ephemeral_storage(env):
    verify_production_secrets(
        _settings(env, "change-me", s3_bucket="")
    )


@pytest.mark.parametrize(
    ("active_key_id", "keyring"),
    [
        ("", {}),
        ("missing", {"other": SecretStr(VALID_TENANT_KEY)}),
        ("bad:key", {"bad:key": SecretStr(VALID_TENANT_KEY)}),
        ("primary", {"primary": SecretStr("not-a-fernet-key")}),
        ("primary", {"primary": SecretStr("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")}),
        (
            "primary",
            {
                "primary": SecretStr(VALID_TENANT_KEY),
                "duplicate": SecretStr(VALID_TENANT_KEY),
            },
        ),
    ],
)
def test_production_requires_a_valid_explicit_tenant_keyring(active_key_id, keyring):
    with pytest.raises(InsecureProductionConfigError) as exc:
        verify_production_secrets(
            _settings(
                "production",
                VALID_SECRET,
                tenant_credential_active_key_id=active_key_id,
                tenant_credential_keys=keyring,
            )
        )

    message = str(exc.value)
    assert "TENANT_CREDENTIAL_ACTIVE_KEY_ID" in message
    assert "TENANT_CREDENTIAL_KEYS" in message


def test_production_requires_explicit_ack_for_expand_plaintext_writes():
    with pytest.raises(InsecureProductionConfigError) as exc:
        verify_production_secrets(
            _settings(
                "production",
                VALID_SECRET,
                tenant_credential_write_mode="plaintext",
                tenant_credential_plaintext_write_ack=False,
            )
        )

    assert "TENANT_CREDENTIAL_PLAINTEXT_WRITE_ACK" in str(exc.value)


def test_production_allows_acknowledged_expand_phase(monkeypatch):
    monkeypatch.setattr(settings_module, "_active_legal_review_blockers", lambda: [])
    verify_production_secrets(
        _settings(
            "production",
            VALID_SECRET,
            tenant_credential_write_mode="plaintext",
            tenant_credential_plaintext_write_ack=True,
        )
    )


@pytest.mark.parametrize(
    "stripe_secret",
    [
        "sk_test_not-production",
        "rk_live_restricted",
        "sk_not-live",
        "pk_live_public",
        "sk_live_",
        "sk_live_x",
    ],
)
def test_production_requires_a_live_stripe_secret_key(stripe_secret):
    with pytest.raises(InsecureProductionConfigError) as exc:
        verify_production_secrets(
            _settings(
                "production",
                VALID_SECRET,
                stripe_secret_key=SecretStr(stripe_secret),
            )
        )
    assert "STRIPE_SECRET_KEY" in str(exc.value)


@pytest.mark.parametrize(
    ("setting", "env_name"),
    [
        ("legal_review_confirmed", "LEGAL_REVIEW_CONFIRMED"),
        ("stripe_tax_setup_confirmed", "STRIPE_TAX_SETUP_CONFIRMED"),
    ],
)
def test_production_requires_explicit_legal_and_tax_review(setting, env_name):
    with pytest.raises(InsecureProductionConfigError) as exc:
        verify_production_secrets(
            _settings(
                "production",
                VALID_SECRET,
                **{setting: False},
            )
        )
    assert env_name in str(exc.value)


@pytest.mark.parametrize(
    ("setting", "env_name", "value"),
    [
        ("legal_company_name", "LEGAL_COMPANY_NAME", ""),
        ("legal_company_vat", "LEGAL_COMPANY_VAT", "[NIF pendiente]"),
        ("legal_company_address", "LEGAL_COMPANY_ADDRESS", "por completar"),
        ("legal_company_city", "LEGAL_COMPANY_CITY", "{{CITY}}"),
        ("legal_dpo_email", "LEGAL_DPO_EMAIL", "pending@example.invalid"),
        ("legal_company_name", "LEGAL_COMPANY_NAME", "[RAZON SOCIAL]"),
        ("legal_company_vat", "LEGAL_COMPANY_VAT", "TODO"),
        ("legal_company_city", "LEGAL_COMPANY_CITY", "Ciudad de ejemplo"),
    ],
)
def test_production_refuses_missing_or_placeholder_legal_identity(setting, env_name, value):
    with pytest.raises(InsecureProductionConfigError) as exc:
        verify_production_secrets(
            _settings("production", VALID_SECRET, **{setting: value})
        )
    assert env_name in str(exc.value)


@pytest.mark.parametrize(
    ("setting", "env_name", "value"),
    [
        ("legal_company_name", "LEGAL_COMPANY_NAME", "x"),
        ("legal_company_name", "LEGAL_COMPANY_NAME", "12"),
        ("legal_company_vat", "LEGAL_COMPANY_VAT", "B1"),
        ("legal_company_vat", "LEGAL_COMPANY_VAT", "11111111"),
        ("legal_company_address", "LEGAL_COMPANY_ADDRESS", "x"),
        ("legal_company_address", "LEGAL_COMPANY_ADDRESS", "12345678"),
        ("legal_company_city", "LEGAL_COMPANY_CITY", "x"),
        ("legal_company_city", "LEGAL_COMPANY_CITY", "12"),
    ],
)
def test_production_refuses_structurally_fake_legal_identity(setting, env_name, value):
    with pytest.raises(InsecureProductionConfigError) as exc:
        verify_production_secrets(
            _settings("production", VALID_SECRET, **{setting: value})
        )
    assert env_name in str(exc.value)


def test_confirmed_review_cannot_override_a_pending_marker(monkeypatch):
    monkeypatch.setattr(
        settings_module,
        "_active_legal_review_blockers",
        lambda: ["terms_of_service:v6"],
    )

    with pytest.raises(InsecureProductionConfigError) as exc:
        verify_production_secrets(_settings("production", VALID_SECRET))

    message = str(exc.value)
    assert "LEGAL_REVIEW_CONFIRMED" in message
    assert "pendiente de revisión final" in message
    assert "terms_of_service:v6" in message


def test_legal_review_scanner_checks_only_the_latest_version(tmp_path):
    for slug in ("terms_of_service", "dpa", "privacy_policy"):
        v1 = tmp_path / "v1"
        v1.mkdir(exist_ok=True)
        (v1 / f"{slug}.es.md").write_text(
            "pendiente de revisión final",
            encoding="utf-8",
        )
        v2 = tmp_path / "v2"
        v2.mkdir(exist_ok=True)
        (v2 / f"{slug}.es.md").write_text("Revisado y publicado", encoding="utf-8")

    assert _active_legal_review_blockers(tmp_path) == []

    v3 = tmp_path / "v3"
    v3.mkdir()
    (v3 / "terms_of_service.es.md").write_text(
        "PENDIENTE DE REVISIÓN FINAL",
        encoding="utf-8",
    )
    assert _active_legal_review_blockers(tmp_path) == ["terms_of_service:v3"]


def test_production_refuses_invalid_legal_email():
    with pytest.raises(InsecureProductionConfigError) as exc:
        verify_production_secrets(
            _settings(
                "production",
                VALID_SECRET,
                legal_dpo_email="not-an-email",
            )
        )
    assert "LEGAL_DPO_EMAIL" in str(exc.value)


def test_empty_legal_environment_values_use_visible_pending_labels_in_development():
    s = Settings(
        legal_company_name="",
        legal_company_vat="",
        legal_company_address="",
        legal_company_city="",
        legal_dpo_email="",
    )
    assert s.legal_company_name == "[Razón social pendiente]"
    assert s.legal_company_vat == "[NIF/VAT pendiente]"
    assert s.legal_company_address == "[Domicilio pendiente]"
    assert s.legal_company_city == "[Ciudad pendiente]"
    assert s.legal_dpo_email == "[Email legal/DPO pendiente]"


@pytest.mark.parametrize(
    ("setting", "env_name", "value"),
    [
        ("clerk_jwks_url", "CLERK_JWKS_URL", ""),
        ("clerk_jwks_url", "CLERK_JWKS_URL", "   "),
        ("clerk_jwks_url", "CLERK_JWKS_URL", "http://clerk.test/.well-known/jwks.json"),
        ("clerk_jwks_url", "CLERK_JWKS_URL", "javascript:alert(1)"),
        (
            "clerk_jwks_url",
            "CLERK_JWKS_URL",
            "https://user:secret@clerk.test/.well-known/jwks.json",
        ),
        ("stripe_secret_key", "STRIPE_SECRET_KEY", SecretStr("")),
        ("stripe_webhook_secret", "STRIPE_WEBHOOK_SECRET", SecretStr("")),
        ("stripe_webhook_secret", "STRIPE_WEBHOOK_SECRET", SecretStr("not-a-secret")),
        ("stripe_webhook_secret", "STRIPE_WEBHOOK_SECRET", SecretStr("sk_live_wrong-kind")),
        ("stripe_webhook_secret", "STRIPE_WEBHOOK_SECRET", SecretStr("whsec_")),
        ("stripe_webhook_secret", "STRIPE_WEBHOOK_SECRET", SecretStr("whsec_x")),
        (
            "stripe_billing_portal_configuration_id",
            "STRIPE_BILLING_PORTAL_CONFIGURATION_ID",
            "",
        ),
        (
            "stripe_billing_portal_configuration_id",
            "STRIPE_BILLING_PORTAL_CONFIGURATION_ID",
            "not-a-portal-config",
        ),
        (
            "stripe_billing_portal_configuration_id",
            "STRIPE_BILLING_PORTAL_CONFIGURATION_ID",
            "bpc_",
        ),
    ],
)
def test_production_refuses_to_start_without_payment_or_auth_credentials(setting, env_name, value):
    with pytest.raises(InsecureProductionConfigError) as exc:
        verify_production_secrets(
            _settings("production", VALID_SECRET, **{setting: value})
        )
    assert env_name in str(exc.value)


@pytest.mark.parametrize(
    ("overrides", "env_name"),
    [
        ({"clerk_issuer": "http://clerk.example.test"}, "CLERK_ISSUER"),
        ({"clerk_issuer": "https://user:secret@clerk.example.test"}, "CLERK_ISSUER"),
        ({"clerk_authorized_parties": []}, "CLERK_AUTHORIZED_PARTIES"),
        (
            {"clerk_authorized_parties": ["https://selai.es/login"]},
            "CLERK_AUTHORIZED_PARTIES",
        ),
        (
            {"clerk_authorized_parties": ["http://localhost:3000"]},
            "CLERK_AUTHORIZED_PARTIES",
        ),
    ],
)
def test_production_refuses_unsafe_clerk_claim_policy(overrides, env_name):
    with pytest.raises(InsecureProductionConfigError) as exc:
        verify_production_secrets(_settings("production", VALID_SECRET, **overrides))
    assert env_name in str(exc.value)


@pytest.mark.parametrize("env", ["development", "staging"])
def test_other_environments_are_not_blocked(env):
    """Local dev and CI must keep working out of the box."""
    verify_production_secrets(_settings(env, "change-me"))


def test_the_app_refuses_to_boot_in_production_with_the_default_secret(monkeypatch):
    """The check is wired into the lifespan, not just importable."""
    from fastapi.testclient import TestClient

    from app.core.settings import settings as live_settings
    from app.main import app

    monkeypatch.setattr(live_settings, "app_env", "production")
    monkeypatch.setattr(live_settings, "secret_key", SecretStr("change-me"))

    with pytest.raises(InsecureProductionConfigError), TestClient(app):
        pass  # pragma: no cover — startup never completes


def test_the_app_boots_in_development(monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.settings import settings as live_settings
    from app.main import app

    monkeypatch.setattr(live_settings, "app_env", "development")
    monkeypatch.setattr(live_settings, "secret_key", SecretStr("change-me"))
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
