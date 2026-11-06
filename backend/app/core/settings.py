"""Centralized settings loaded from environment.

All runtime config flows through here. Modules import `settings` and never
read `os.environ` directly — keeps tests deterministic and secrets auditable.
"""

import base64
import binascii
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import (
    AliasChoices,
    EmailStr,
    Field,
    SecretStr,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_env: Literal["development", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    secret_key: SecretStr = SecretStr("change-me")

    # Tenant provider credentials use a keyring independent from SECRET_KEY.
    # The environment value is a JSON object of {key_id: fernet_key}; keeping
    # old entries makes rotation an expand/rewrap/contract operation.
    tenant_credential_active_key_id: str = ""
    tenant_credential_keys: dict[str, SecretStr] = Field(default_factory=dict)
    tenant_credential_write_mode: Literal["plaintext", "v2"] = "v2"
    tenant_credential_plaintext_write_ack: bool = False

    # Global legal identity of the selai service provider. These values belong
    # to the provider, never to an agency tenant. Development keeps explicit
    # pending labels so legal pages remain readable without inventing a real
    # company; production rejects every pending/empty value at startup.
    legal_company_name: str = "[Razón social pendiente]"
    legal_company_vat: str = "[NIF/VAT pendiente]"
    legal_company_address: str = "[Domicilio pendiente]"
    legal_company_city: str = "[Ciudad pendiente]"
    legal_dpo_email: str = "[Email legal/DPO pendiente]"
    legal_review_confirmed: bool = False

    # Database
    database_url: str = "postgresql+asyncpg://realtor:realtor@localhost:5432/realtor"
    redis_url: str = "redis://localhost:6379/0"

    # Rate limiter backend (see app/core/middleware.py).
    # "auto"   — Redis outside development, in-memory in development/tests.
    # "redis"  — always Redis (still falls back to memory if Redis errors).
    # "memory" — never touch Redis.
    rate_limit_backend: Literal["auto", "memory", "redis"] = "auto"

    # Anthropic
    anthropic_api_key: SecretStr = SecretStr("")
    agent_model_fast: str = "claude-haiku-4-5-20251001"
    agent_model_smart: str = "claude-opus-4-7"

    # Guardrail (output validation before sending to lead). Disable in dev to
    # save Haiku tokens and ~500ms latency; deterministic rules always run.
    guardrail_llm_enabled: bool = True

    # Voyage embeddings (recommended pairing for Claude)
    voyage_api_key: SecretStr = SecretStr("")

    # WhatsApp
    whatsapp_verify_token: SecretStr = SecretStr("")
    whatsapp_app_secret: SecretStr = SecretStr("")
    whatsapp_access_token: SecretStr = SecretStr("")
    whatsapp_phone_number_id: str = ""
    whatsapp_api_version: str = "v21.0"

    # Email
    resend_api_key: SecretStr = SecretStr("")
    # Reading an inbound email's body needs a Resend key with *read* access,
    # and the sending key is deliberately restricted to "sending access" only.
    # Rather than upgrade the production sending credential to full access —
    # which would also let it create domains and export contacts if it ever
    # leaked — inbound reads get their own key. Empty falls back to
    # `resend_api_key`, so a single full-access key still works.
    resend_read_api_key: SecretStr = SecretStr("")
    # Sender for the AI's conversational replies (ResendClient). Empty falls
    # back to `resend_from` below, so by default replies leave from the same
    # verified identity as every other email. The old default was the
    # "noreply@example.com" placeholder, and it was a landmine: Resend refuses
    # unverified domains, so with the variable unset every AI reply to a lead
    # died with a 403 at the very last step — silently, in a background task.
    email_from: str = ""
    # Transactional/product emails (welcome, handoff alerts, digests).
    resend_from: str = "selai <hola@selai.es>"
    # Domain where each agency receives its portal leads: the address handed to
    # the agency is `<slug>-leads@<this>`. It is configuration, not decoration:
    # it must be a domain the inbound provider is actually set up to receive
    # for, and on Resend's free plan that cannot be a second custom domain —
    # only the managed `<id>.resend.app` one. Moving to a paid plan is a change
    # of this variable, not of the code.
    #
    # Changing it takes effect immediately for every agency (the address is
    # built on the fly, never stored), so anyone who already pasted the old
    # address into Idealista or Fotocasa stops receiving without any error.
    # Announce it before touching this.
    email_leads_domain: str = "leads.selai.es"
    # Signing secret of the Resend *inbound* webhook (Svix, `whsec_...`).
    # Empty means Resend Inbound is not wired yet and only our own
    # X-Webhook-Secret callers are accepted.
    resend_webhook_secret: SecretStr = SecretStr("")

    # Google Calendar (OAuth 2.0 web server flow — app/services/google_calendar.py)
    # Both names are accepted for each credential: the fields predate the
    # integration, and the Google Cloud console calls them "Client ID" and
    # "Client secret", so GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET is what
    # anyone copying from that screen will write in the .env.
    # Empty (the default) means the whole feature degrades: the panel says the
    # calendar is unavailable and visits are still scheduled — nothing raises.
    google_oauth_client_id: str = Field(
        default="",
        validation_alias=AliasChoices("google_oauth_client_id", "google_client_id"),
    )
    google_oauth_client_secret: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("google_oauth_client_secret", "google_client_secret"),
    )
    # Optional. Empty means "derive it from PUBLIC_BASE_URL" (see
    # `google_calendar.redirect_uri`), so a normal deployment configures one
    # public URL instead of two that can silently disagree. Set it only when
    # the API answers on a different hostname than PUBLIC_BASE_URL.
    google_oauth_redirect_uri: str = ""

    # Voice
    vapi_api_key: SecretStr = SecretStr("")
    vapi_phone_number_id: str = ""

    # SMS (Twilio) — fallback channel cuando WhatsApp no llega
    twilio_account_sid: str = ""
    twilio_auth_token: SecretStr = SecretStr("")
    twilio_from_number: str = ""

    # Auth (Clerk)
    clerk_jwks_url: str = ""
    # Clerk session tokens are issued by the instance's Frontend API origin.
    # Empty derives that origin from CLERK_JWKS_URL (the launch guide uses the
    # instance-specific `/.well-known/jwks.json` URL).  Keep the override for
    # installations that retrieve keys from Clerk's generic Backend API.
    clerk_issuer: str = ""
    # Clerk's default session token has no `aud` claim.  If a JWT template or
    # customized session token adds one, configure this value and verification
    # becomes mandatory.  Empty therefore means "standard Clerk session token",
    # not "accept any audience": a token that unexpectedly carries `aud` is
    # still rejected unless this expected value is configured.
    clerk_audience: str = ""
    # The `azp` claim is the browser Origin that asked Clerk for the session
    # token.  Native clients can legitimately omit it; when present it must be
    # one of these exact origins (Clerk's documented CSRF/subdomain defence).
    clerk_authorized_parties: list[str] = Field(
        default_factory=lambda: ["https://selai.es", "https://www.selai.es"]
    )

    # Billing (Stripe)
    stripe_secret_key: SecretStr = SecretStr("")
    stripe_webhook_secret: SecretStr = SecretStr("")
    stripe_billing_portal_configuration_id: str = ""
    stripe_tax_setup_confirmed: bool = False

    # Voice infra
    deepgram_api_key: SecretStr = SecretStr("")
    elevenlabs_api_key: SecretStr = SecretStr("")

    # Portales
    idealista_client_id: str = ""
    idealista_client_secret: SecretStr = SecretStr("")

    # Hosting / URLs públicas
    public_base_url: str = "http://localhost:8000"

    # Storage (local fallback + S3)
    local_storage_root: str = "./data/storage"
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = "eu-west-1"

    # Observability
    sentry_dsn: str = ""

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_database_url(cls, v: str) -> str:
        """Accept the plain URLs managed providers hand out (Railway, Heroku
        style `postgres://` / `postgresql://`) and pin the asyncpg driver."""
        if isinstance(v, str):
            if v.startswith("postgres://"):
                return v.replace("postgres://", "postgresql+asyncpg://", 1)
            if v.startswith("postgresql://"):
                return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    @field_validator(
        "legal_company_name",
        "legal_company_vat",
        "legal_company_address",
        "legal_company_city",
        "legal_dpo_email",
        mode="before",
    )
    @classmethod
    def _empty_legal_values_stay_explicitly_pending(cls, v: object, info: ValidationInfo) -> object:
        """An empty copied `.env.example` must not render invisible parties."""
        if isinstance(v, str) and not v.strip():
            defaults = {
                "legal_company_name": "[Razón social pendiente]",
                "legal_company_vat": "[NIF/VAT pendiente]",
                "legal_company_address": "[Domicilio pendiente]",
                "legal_company_city": "[Ciudad pendiente]",
                "legal_dpo_email": "[Email legal/DPO pendiente]",
            }
            return defaults[info.field_name]
        return v

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"

    @property
    def clerk_expected_issuer(self) -> str:
        """Exact `iss` accepted for Clerk session tokens.

        The configured override wins.  Otherwise an instance JWKS URL such as
        ``https://foo.clerk.accounts.dev/.well-known/jwks.json`` has the same
        origin as the token issuer, so deriving it avoids a second environment
        variable that can silently disagree with the key set.
        """
        configured = self.clerk_issuer.strip().rstrip("/")
        if configured:
            return configured
        try:
            parsed = urlsplit(self.clerk_jwks_url.strip())
        except ValueError:
            return ""
        if not parsed.scheme or not parsed.netloc:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    @property
    def database_url_sync(self) -> str:
        """Sync URL for Alembic migrations."""
        return self.database_url.replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


# ---------------------------------------------------------------------------
# Production fail-fast (item 229)
# ---------------------------------------------------------------------------


class InsecureProductionConfigError(RuntimeError):
    """Raised when production lacks a critical auth or billing credential."""


def _is_placeholder_secret(value: str) -> bool:
    """`SECRET_KEY` values that are not credible shared secrets.

    The shipped defaults are `change-me` (settings.py) and `change-me-in-prod`
    (.env.example). Production also rejects short or single-pattern values so
    copied filler cannot authenticate inbound voice, email or provider hooks.
    """
    cleaned = (value or "").strip()
    return (
        not cleaned
        or cleaned.lower().startswith("change-me")
        or len(cleaned) < 32
        or len(set(cleaned)) < 8
    )


_TENANT_CREDENTIAL_KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
LOCAL_DERIVED_TENANT_KEY_ID = "local-derived-do-not-use"


def _fernet_key_bytes(value: SecretStr | str) -> bytes | None:
    """Decode one canonical 32-byte Fernet key without exposing it."""
    raw_value = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
    try:
        decoded = base64.b64decode(raw_value.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        return None
    if len(decoded) != 32:
        return None
    if base64.urlsafe_b64encode(decoded).decode("ascii") != raw_value:
        return None
    return decoded


def tenant_credential_keyring_issues(
    s: Settings,
    *,
    require_explicit: bool,
) -> list[str]:
    """Return actionable keyring errors for startup and migration tooling."""
    active = s.tenant_credential_active_key_id.strip()
    keys = dict(s.tenant_credential_keys or {})
    if not active and not keys:
        return (
            ["TENANT_CREDENTIAL_ACTIVE_KEY_ID and TENANT_CREDENTIAL_KEYS are required"]
            if require_explicit
            else []
        )

    issues: list[str] = []
    if not _TENANT_CREDENTIAL_KEY_ID_RE.fullmatch(active):
        issues.append("TENANT_CREDENTIAL_ACTIVE_KEY_ID has an invalid format")
    if active == LOCAL_DERIVED_TENANT_KEY_ID:
        issues.append("TENANT_CREDENTIAL_ACTIVE_KEY_ID uses the reserved local key id")
    if active and active not in keys:
        issues.append("TENANT_CREDENTIAL_ACTIVE_KEY_ID is absent from TENANT_CREDENTIAL_KEYS")

    decoded_keys: list[bytes] = []
    for key_id, material in keys.items():
        if not _TENANT_CREDENTIAL_KEY_ID_RE.fullmatch(key_id):
            issues.append(f"TENANT_CREDENTIAL_KEYS contains invalid key id {key_id!r}")
            continue
        if key_id == LOCAL_DERIVED_TENANT_KEY_ID:
            issues.append("TENANT_CREDENTIAL_KEYS contains the reserved local key id")
        decoded = _fernet_key_bytes(material)
        if decoded is None or len(set(decoded)) < 8:
            issues.append(f"TENANT_CREDENTIAL_KEYS[{key_id!r}] is not a strong Fernet key")
            continue
        decoded_keys.append(decoded)
    if len(decoded_keys) != len(set(decoded_keys)):
        issues.append("TENANT_CREDENTIAL_KEYS reuses the same material under multiple key ids")
    return issues


def _is_secure_service_url(value: str) -> bool:
    """Accept only an HTTPS endpoint without embedded credentials."""
    try:
        parsed = urlsplit((value or "").strip())
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    )


def _is_secure_origin(value: str) -> bool:
    """An HTTPS origin only: no credentials, path, query or fragment."""
    try:
        parsed = urlsplit((value or "").strip())
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def _is_placeholder_legal_value(value: str) -> bool:
    """Values that cannot identify a contracting party in production."""
    cleaned = (value or "").strip().casefold()
    markers = (
        "pendiente",
        "pending",
        "completar",
        "por definir",
        "sin definir",
        "placeholder",
        "change-me",
        "example",
        "ejemplo",
        "{{",
        "}}",
        ".invalid",
    )
    bracketed = cleaned.startswith("[") and cleaned.endswith("]")
    generic_token = cleaned in {"todo", "tbd", "n/a", "na", "-"}
    return not cleaned or bracketed or generic_token or any(marker in cleaned for marker in markers)


def _is_structurally_invalid_legal_value(field_name: str, value: str) -> bool:
    """Reject values too small to identify a real contracting party.

    This is deliberately not a country-specific VAT validator: selai may sell
    internationally. It only enforces enough structure that filler such as
    ``x`` or ``123`` cannot pass the production gate.
    """
    cleaned = (value or "").strip()
    letters = sum(char.isalpha() for char in cleaned)
    if field_name == "legal_company_name":
        return len(cleaned) < 3 or letters < 2
    if field_name == "legal_company_vat":
        compact = "".join(char for char in cleaned if char.isalnum())
        return (
            len(compact) < 8
            or len(compact) > 24
            or not any(char.isdigit() for char in compact)
            or len(set(compact.casefold())) < 3
        )
    if field_name == "legal_company_address":
        return len(cleaned) < 8 or letters < 3
    if field_name == "legal_company_city":
        return len(cleaned) < 2 or letters < 2
    return False


_ACTIVE_LEGAL_SLUGS = ("terms_of_service", "dpa", "privacy_policy")
_LEGAL_REVIEW_MARKER = "pendiente de revisión final"
_LEGAL_ROOT = Path(__file__).resolve().parent.parent / "legal"


def _active_legal_review_blockers(root: Path | None = None) -> list[str]:
    """Return active legal docs that still self-identify as pending review.

    The latest numeric version containing each known slug is the published
    version, matching ``legal_documents.current_version`` without importing
    that service back into settings and creating a circular dependency.
    Missing or unreadable active documents also block production.
    """
    root = root or _LEGAL_ROOT
    blockers: list[str] = []
    for slug in _ACTIVE_LEGAL_SLUGS:
        candidates: list[tuple[int, Path]] = []
        try:
            children = tuple(root.iterdir())
        except OSError:
            blockers.append(f"{slug}:unreadable")
            continue
        for child in children:
            if not child.is_dir() or not child.name.startswith("v"):
                continue
            try:
                version_number = int(child.name[1:])
            except ValueError:
                continue
            path = child / f"{slug}.es.md"
            if path.is_file():
                candidates.append((version_number, path))
        if not candidates:
            blockers.append(f"{slug}:missing")
            continue
        _, active_path = max(candidates, key=lambda item: item[0])
        try:
            active_text = active_path.read_text(encoding="utf-8-sig")
        except OSError:
            blockers.append(f"{slug}:unreadable")
            continue
        if _LEGAL_REVIEW_MARKER.casefold() in active_text.casefold():
            blockers.append(f"{slug}:{active_path.parent.name}")
    return blockers


def verify_production_secrets(s: Settings | None = None) -> None:
    """Refuse to start production without credentials and provider identity.

    SECRET_KEY is what authenticates the Vapi and inbound-email webhooks: with
    the shipped default anyone can inject fake calls and fake leads into any
    agency. Clerk protects every private API route, and both Stripe credentials
    are required for a payment to become an active local subscription. Only
    enforced when `app_env == "production"` so local dev and CI keep their
    explicit stubs.
    """
    s = s or settings
    if s.app_env != "production":
        return

    missing: list[str] = []
    if _is_placeholder_secret(s.secret_key.get_secret_value()):
        missing.append("SECRET_KEY")
    keyring_issues = tenant_credential_keyring_issues(s, require_explicit=True)
    if keyring_issues:
        missing.append("TENANT_CREDENTIAL_ACTIVE_KEY_ID/TENANT_CREDENTIAL_KEYS")
    if (
        s.tenant_credential_write_mode == "plaintext"
        and not s.tenant_credential_plaintext_write_ack
    ):
        missing.append("TENANT_CREDENTIAL_PLAINTEXT_WRITE_ACK")
    if not _is_secure_service_url(s.clerk_jwks_url):
        missing.append("CLERK_JWKS_URL")
    if not _is_secure_origin(s.clerk_expected_issuer):
        missing.append("CLERK_ISSUER")
    if not s.clerk_authorized_parties or not all(
        _is_secure_origin(origin) for origin in s.clerk_authorized_parties
    ):
        missing.append("CLERK_AUTHORIZED_PARTIES")
    stripe_secret_key = s.stripe_secret_key.get_secret_value().strip()
    if not stripe_secret_key.startswith("sk_live_") or len(stripe_secret_key) < 16:
        missing.append("STRIPE_SECRET_KEY")
    stripe_webhook_secret = s.stripe_webhook_secret.get_secret_value().strip()
    if not stripe_webhook_secret.startswith("whsec_") or len(stripe_webhook_secret) < 16:
        missing.append("STRIPE_WEBHOOK_SECRET")
    portal_configuration_id = s.stripe_billing_portal_configuration_id.strip()
    if not portal_configuration_id.startswith("bpc_") or len(portal_configuration_id) < 8:
        missing.append("STRIPE_BILLING_PORTAL_CONFIGURATION_ID")
    # Generated property cards and formal offers contain customer PII.  The
    # local fallback is intentionally not served in production and an
    # ephemeral container filesystem would lose it on every deploy, so a
    # durable S3-compatible bucket is a production requirement rather than an
    # optional optimisation.
    if not s.s3_bucket.strip():
        missing.append("S3_BUCKET")

    legal_fields = (
        ("LEGAL_COMPANY_NAME", "legal_company_name", s.legal_company_name),
        ("LEGAL_COMPANY_VAT", "legal_company_vat", s.legal_company_vat),
        ("LEGAL_COMPANY_ADDRESS", "legal_company_address", s.legal_company_address),
        ("LEGAL_COMPANY_CITY", "legal_company_city", s.legal_company_city),
        ("LEGAL_DPO_EMAIL", "legal_dpo_email", s.legal_dpo_email),
    )
    for env_name, field_name, value in legal_fields:
        if _is_placeholder_legal_value(value) or _is_structurally_invalid_legal_value(
            field_name, value
        ):
            missing.append(env_name)

    legal_review_blockers: list[str] = []
    if not s.legal_review_confirmed:
        missing.append("LEGAL_REVIEW_CONFIRMED")
    else:
        legal_review_blockers = _active_legal_review_blockers()
        if legal_review_blockers:
            missing.append("LEGAL_REVIEW_CONFIRMED")
    if not s.stripe_tax_setup_confirmed:
        missing.append("STRIPE_TAX_SETUP_CONFIRMED")

    if "LEGAL_DPO_EMAIL" not in missing:
        try:
            TypeAdapter(EmailStr).validate_python(s.legal_dpo_email)
        except ValidationError:
            missing.append("LEGAL_DPO_EMAIL")

    if missing:
        blocker_detail = (
            " Los documentos legales activos siguen marcados como 'pendiente "
            "de revisión final' o no son legibles: " + ", ".join(legal_review_blockers) + "."
            if legal_review_blockers
            else ""
        )
        keyring_detail = (
            " El keyring de credenciales tenant no es válido: " + "; ".join(keyring_issues) + "."
            if keyring_issues
            else ""
        )
        raise InsecureProductionConfigError(
            "Falta configuracion critica de produccion: "
            + ", ".join(missing)
            + ". Configura estas variables antes de arrancar; SECRET_KEY debe ser "
            "aleatoria, tener al menos 32 caracteres diversos y no puede empezar por "
            "'change-me', STRIPE_SECRET_KEY debe "
            "ser una clave live (sk_live_), STRIPE_WEBHOOK_SECRET debe ser el secreto "
            "de firma whsec_ del endpoint, CLERK_JWKS_URL y el issuer derivado/configurado "
            "deben ser HTTPS sin credenciales, CLERK_AUTHORIZED_PARTIES debe contener "
            "orígenes HTTPS exactos, "
            "el portal de Stripe debe usar una "
            "configuracion guardada (bpc_) y la identidad legal debe identificar "
            "al prestador real. S3_BUCKET debe apuntar a almacenamiento de objetos "
            "persistente para PDFs y archivos; el disco efimero del contenedor no "
            "es válido. Las confirmaciones legal y fiscal solo se activan "
            "despues de revisar contratos, IVA y precios de Stripe. La clave activa de "
            "credenciales tenant debe existir en un keyring Fernet dedicado; conserva "
            "las claves anteriores hasta terminar el recifrado." + blocker_detail + keyring_detail
        )
