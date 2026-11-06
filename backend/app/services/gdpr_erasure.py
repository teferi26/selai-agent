"""Pseudonyms and durable compensation records for GDPR erasure."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import settings
from app.db.models import ExternalErasureRequest
from app.services.secret_store import encrypt_secret_strict

_SUBJECT_DOMAIN = b"selai:gdpr-subject:v1\x00"
_AGENCY_DOMAIN = b"selai:gdpr-agency:v1\x00"
_LOCATOR_DOMAIN = b"selai:gdpr-external-locator:v1\x00"
PURGED_AT_KEY = "operational_data_purged_at"


def _root_key() -> bytes:
    return settings.secret_key.get_secret_value().encode("utf-8")


def subject_pseudonym(agency_id: object, lead_id: object) -> str:
    """Stable, non-reversible link for the minimal retained legal evidence."""
    message = f"{agency_id}:{lead_id}".encode()
    digest = hmac.new(_root_key(), _SUBJECT_DOMAIN + message, hashlib.sha256).hexdigest()
    return f"lead_{digest}"


def agency_pseudonym(agency_id: object) -> str:
    """Stable, non-reversible reference for a whole-tenant purge."""
    digest = hmac.new(
        _root_key(),
        _AGENCY_DOMAIN + str(agency_id).encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"agency_{digest}"


def locator_digest(locator: str) -> str:
    """Keyed digest used solely to deduplicate encrypted outbox locators."""
    return hmac.new(
        _root_key(),
        _LOCATOR_DOMAIN + locator.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def queue_external_erasure(
    db: AsyncSession,
    *,
    agency_id,
    pseudonym: str,
    provider: str,
    resource_type: str,
    locator: str,
) -> ExternalErasureRequest:
    """Add one pending request; never marks or implies provider completion."""
    request = ExternalErasureRequest(
        agency_id=agency_id,
        subject_pseudonym=pseudonym,
        provider=provider[:48],
        resource_type=resource_type[:64],
        locator_digest=locator_digest(locator),
        locator_encrypted=encrypt_secret_strict(locator),
        status="pending",
        attempts=0,
    )
    db.add(request)
    return request


def record_external_erasure_failure(request: ExternalErasureRequest, *, error_code: str) -> None:
    """Record an attempt without retaining exception text or personal data."""
    request.attempts = int(request.attempts or 0) + 1
    request.status = "pending"
    request.last_error = error_code[:120]


def confirm_external_erasure(
    request: ExternalErasureRequest, *, provider_confirmation: str
) -> None:
    """Complete only after an explicit positive provider confirmation.

    The confirmation itself can contain a remote identifier, so only its
    keyed digest is retained as a technical audit token.
    """
    if not provider_confirmation.strip():
        raise ValueError("provider confirmation is required")
    request.attempts = int(request.attempts or 0) + 1
    request.status = "completed"
    request.last_error = None
    request.confirmation_digest = locator_digest(provider_confirmation)
    request.completed_at = datetime.now(UTC)
