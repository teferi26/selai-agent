"""Public legal documents + owner consent endpoints.

- `GET /api/v1/legal/documents` — catalog of TdS / DPA / Privacy at their
  current versions.
- `GET /api/v1/legal/documents/{slug}` — markdown source (optionally a
  specific version via `?version=v1`).
- `GET /api/v1/legal/agencies/{agency_id}/consent-status` — what the owner
  has signed so far (used by the dashboard to show or hide the legacy
  consent gate).
- `POST /api/v1/legal/agencies/{agency_id}/legacy-consent` — for agencies
  created before Parte B; signs the missing scopes.
- `GET /api/v1/legal/agencies/{agency_id}/consent-pdf` — downloadable PDF
  proving when, by whom and from which IP a given consent was granted.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from html import escape
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AgencyMemberDep, AuthContext, require_agency_member
from app.core.logging import get_logger
from app.db.models import (
    Agency,
    ConsentAction,
    ConsentLog,
    ConsentScope,
    User,
    UserRole,
)
from app.db.session import get_db
from app.services.legal_documents import (
    KNOWN_SLUGS,
    LegalEvidenceError,
    accepted_document_from_evidence,
    consent_evidence,
    current_version,
    current_versions,
    list_documents,
    load_document,
)

router = APIRouter(prefix="/api/v1/legal", tags=["legal"])
log = get_logger(__name__)


# ---------------------------- Catalog endpoints ---------------------------- #


class LegalDocSummary(BaseModel):
    slug: str
    version: str
    title: str
    effective_date: str
    language: str
    document_hash: str


@router.get("/documents", response_model=list[LegalDocSummary])
async def list_legal_documents() -> list[LegalDocSummary]:
    return [LegalDocSummary(**doc) for doc in list_documents()]


@router.get("/documents/{slug}", response_class=PlainTextResponse)
async def get_legal_document(slug: str, version: str | None = None) -> str:
    if slug not in KNOWN_SLUGS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown document")
    try:
        version = version or current_version(slug)
        doc = load_document(slug, version)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return doc.render()


# ----------------------------- Consent status ----------------------------- #


class ConsentStatus(BaseModel):
    agency_id: str
    owner_user_id: str | None
    consents: dict[str, dict[str, Any]]
    missing: list[str]
    stale: list[dict[str, str]]


async def _resolve_active_owners(db: AsyncSession, agency_id: uuid.UUID) -> list[User]:
    stmt = (
        select(User)
        .where(
            User.agency_id == agency_id,
            User.role == UserRole.OWNER,
            User.is_active.is_(True),
        )
        .order_by(User.created_at.asc(), User.id.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


def _scope_for_slug(slug: str) -> ConsentScope:
    return {
        "terms_of_service": ConsentScope.TERMS_OF_SERVICE,
        "dpa": ConsentScope.DPA,
        "privacy_policy": ConsentScope.PRIVACY_POLICY,
    }[slug]


@router.get(
    "/agencies/{agency_id}/consent-status",
    response_model=ConsentStatus,
    dependencies=[AgencyMemberDep],
)
async def consent_status(agency_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> ConsentStatus:
    agency = await db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agency not found")

    owners = await _resolve_active_owners(db, agency_id)
    owner_ids = {owner.id for owner in owners}
    consents: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    stale: list[dict[str, str]] = []
    versions = current_versions()

    for slug in ("terms_of_service", "dpa", "privacy_policy"):
        scope = _scope_for_slug(slug)
        stmt = (
            select(ConsentLog)
            .where(
                ConsentLog.agency_id == agency_id,
                ConsentLog.scope == scope,
                ConsentLog.action == ConsentAction.GRANTED,
            )
            .order_by(ConsentLog.created_at.desc())
            .limit(1)
        )
        last = (await db.execute(stmt)).scalar_one_or_none()
        if last is None:
            missing.append(slug)
            continue
        consents[slug] = {
            "version": last.document_version or "",
            "granted_at": last.created_at.isoformat(),
            "ip_address": last.ip_address,
            "user_id": str(last.user_id) if last.user_id else None,
        }
        current = versions.get(slug)
        if current and last.document_version and last.document_version != current:
            stale.append({"slug": slug, "signed": last.document_version, "current": current})
            continue

        try:
            accepted = accepted_document_from_evidence(last.evidence)
        except LegalEvidenceError:
            accepted = None
        if accepted is None or accepted.slug != slug or accepted.version != last.document_version:
            # Old evidence hashed only a template and cannot reconstruct the
            # provider identity the owner saw. Mark it stale so the existing
            # legacy-consent screen asks for a fresh, provable acceptance.
            stale.append(
                {
                    "slug": slug,
                    "signed": last.document_version or "",
                    "current": current or "",
                    "reason": "rendered_evidence_missing",
                }
            )
            continue

        current_hash = load_document(slug, current).hash if current else ""
        if accepted.document_hash != current_hash:
            stale.append(
                {
                    "slug": slug,
                    "signed": last.document_version or "",
                    "current": current or "",
                    "reason": "rendered_document_changed",
                }
            )
            continue

        if last.user_id not in owner_ids:
            stale.append(
                {
                    "slug": slug,
                    "signed": last.document_version or "",
                    "current": current or "",
                    "reason": "owner_signature_missing",
                }
            )

    return ConsentStatus(
        agency_id=str(agency_id),
        owner_user_id=str(owners[0].id) if owners else None,
        consents=consents,
        missing=missing,
        stale=stale,
    )


# ---------------------- Legacy consent (agencies pre-B) ---------------------- #


class LegacyConsentBody(BaseModel):
    accepted_terms: bool = False
    accepted_dpa: bool = False
    accepted_privacy: bool = False
    marketing_opt_in: bool = False
    accepted_versions: dict[str, str]
    accepted_hashes: dict[str, str]


@router.post(
    "/agencies/{agency_id}/legacy-consent",
    status_code=201,
)
async def legacy_consent(
    agency_id: uuid.UUID,
    body: LegacyConsentBody,
    request: Request,
    auth: AuthContext = Depends(require_agency_member),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    agency = await db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agency not found")

    # This action is a legal signature, not an ordinary agency mutation. It
    # must be attributed to the authenticated owner who actually clicked the
    # checkbox. Admins, agents and the header-only development bypass cannot
    # sign on somebody else's behalf.
    signer = auth.user
    if (
        auth.dev_bypass
        or signer is None
        or not signer.is_active
        or signer.agency_id != agency_id
        or signer.role != UserRole.OWNER
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "owner_consent_required"},
        )

    if not (body.accepted_terms and body.accepted_dpa and body.accepted_privacy):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "All three of accepted_terms, accepted_dpa, accepted_privacy must be true.",
        )

    server_versions = current_versions()
    for slug in ("terms_of_service", "dpa", "privacy_policy"):
        server_version = server_versions.get(slug)
        server_hash = load_document(slug, server_version).hash if server_version else ""
        if (
            body.accepted_versions.get(slug) != server_version
            or body.accepted_hashes.get(slug) != server_hash
        ):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                {
                    "code": "stale_consent",
                    "slug": slug,
                    "current_version": server_version or "",
                    "current_hash": server_hash,
                },
            )

    request_ip = request.client.host if request.client else None
    request_ua = request.headers.get("user-agent")
    accepted_label = (
        "He leído y acepto los Términos de Servicio, el Acuerdo de Encargo de "
        "Tratamiento y la Política de Privacidad de selai."
    )
    try:
        evidence_by_slug = {
            slug: consent_evidence(
                slug=slug,
                version=body.accepted_versions[slug],
                expected_hash=body.accepted_hashes[slug],
                user_agent=request_ua,
                accepted_text=accepted_label,
            )
            for slug in ("terms_of_service", "dpa", "privacy_policy")
        }
    except LegalEvidenceError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "stale_consent", "message": str(exc)},
        ) from exc

    created = 0
    for slug in ("terms_of_service", "dpa", "privacy_policy"):
        scope = _scope_for_slug(slug)
        # Idempotent: skip if a non-stale consent already exists.
        existing = await db.execute(
            select(ConsentLog)
            .where(
                ConsentLog.agency_id == agency_id,
                ConsentLog.user_id == signer.id,
                ConsentLog.scope == scope,
                ConsentLog.action == ConsentAction.GRANTED,
                ConsentLog.document_version == body.accepted_versions[slug],
            )
            .order_by(ConsentLog.created_at.desc())
            .limit(1)
        )
        existing_consent = existing.scalar_one_or_none()
        if existing_consent is not None:
            try:
                accepted = accepted_document_from_evidence(existing_consent.evidence)
            except LegalEvidenceError:
                accepted = None
            if (
                accepted is not None
                and accepted.slug == slug
                and accepted.version == body.accepted_versions[slug]
                and accepted.document_hash == body.accepted_hashes[slug]
            ):
                continue
        db.add(
            ConsentLog(
                agency_id=agency_id,
                user_id=signer.id,
                lead_id=None,
                scope=scope,
                action=ConsentAction.GRANTED,
                source="legacy_consent",
                evidence=evidence_by_slug[slug],
                ip_address=request_ip,
                user_agent=request_ua,
                document_version=body.accepted_versions[slug],
            )
        )
        created += 1
    if body.marketing_opt_in:
        existing_mkt = await db.execute(
            select(ConsentLog)
            .where(
                ConsentLog.agency_id == agency_id,
                ConsentLog.user_id == signer.id,
                ConsentLog.scope == ConsentScope.MARKETING,
                ConsentLog.action == ConsentAction.GRANTED,
            )
            .limit(1)
        )
        if not existing_mkt.scalar_one_or_none():
            db.add(
                ConsentLog(
                    agency_id=agency_id,
                    user_id=signer.id,
                    lead_id=None,
                    scope=ConsentScope.MARKETING,
                    action=ConsentAction.GRANTED,
                    source="legacy_consent",
                    evidence="Marketing opt-in en pantalla de aceptación retroactiva.",
                    ip_address=request_ip,
                    user_agent=request_ua,
                )
            )
    await db.commit()
    return {"created": created, "agency_id": str(agency_id)}


# ----------------------------- Consent PDF ----------------------------- #


@router.get("/agencies/{agency_id}/consent-pdf", dependencies=[AgencyMemberDep])
async def consent_pdf(
    agency_id: uuid.UUID,
    scope: str,
    version: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> Response:
    if scope not in ("terms_of_service", "dpa", "privacy_policy"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid scope for PDF")
    consent_scope = _scope_for_slug(scope)

    stmt = (
        select(ConsentLog)
        .where(
            ConsentLog.agency_id == agency_id,
            ConsentLog.scope == consent_scope,
            ConsentLog.action == ConsentAction.GRANTED,
        )
        .order_by(ConsentLog.created_at.desc())
    )
    if version:
        stmt = stmt.where(ConsentLog.document_version == version)
    consent = (await db.execute(stmt.limit(1))).scalar_one_or_none()
    if consent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no consent on record")

    target_version = consent.document_version or current_version(scope)
    try:
        accepted = accepted_document_from_evidence(consent.evidence)
    except LegalEvidenceError as exc:
        log.warning(
            "legal.consent_evidence_invalid",
            agency=str(agency_id),
            scope=scope,
            version=target_version,
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "consent evidence is invalid; fresh consent required",
        ) from exc
    if accepted is None or accepted.slug != scope or accepted.version != target_version:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "consent predates rendered evidence; fresh consent required",
        )

    pdf_bytes = _render_pdf(
        title=accepted.title,
        version=accepted.version,
        markdown_body=accepted.rendered_markdown,
        granted_at=consent.created_at,
        ip_address=consent.ip_address,
        user_agent=consent.user_agent,
        user_id=consent.user_id,
        document_hash=accepted.document_hash,
    )
    filename = f"selai-{scope}-{accepted.version}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _render_pdf(
    *,
    title: str,
    version: str,
    markdown_body: str,
    granted_at: datetime,
    ip_address: str | None,
    user_agent: str | None,
    user_id: uuid.UUID | None,
    document_hash: str,
) -> bytes:
    """Render the consent record as PDF.

    Local import of WeasyPrint because importing it pulls in heavy native
    libs (cairo/pango); we only want that cost paid when someone actually
    requests a PDF — startup time stays unaffected.
    """
    from weasyprint import HTML  # type: ignore

    body_html = escape(markdown_body).replace("\n", "<br/>")
    safe_title = escape(title)
    safe_version = escape(version)
    safe_user_id = escape(str(user_id or "pendiente"))
    safe_ip_address = escape(ip_address or "no registrada")
    safe_user_agent = escape(user_agent or "no registrado")
    safe_document_hash = escape(document_hash)
    generated_at = datetime.now(UTC).isoformat()
    html = f"""\
<html>
<head>
<meta charset='utf-8'>
<style>
  body {{ font-family: Helvetica, Arial, sans-serif; padding: 32px; color: #0B0D17; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  .meta {{ color: #555; font-size: 11px; margin-bottom: 24px; }}
  .body {{ font-size: 11px; line-height: 1.5; white-space: pre-wrap; }}
  .footer {{ margin-top: 32px; padding-top: 16px; border-top: 1px solid #ccc; font-size: 10px; color: #444; }}
</style>
</head>
<body>
  <h1>{safe_title}</h1>
  <div class='meta'>Versión {safe_version} — generado por selai · {generated_at}</div>
  <div class='body'>{body_html}</div>
  <div class='footer'>
    <strong>Evidencia de aceptación</strong><br/>
    Aceptado: {granted_at.isoformat()}<br/>
    Usuario: {safe_user_id}<br/>
    IP: {safe_ip_address}<br/>
    User-Agent: {safe_user_agent}<br/>
    SHA-256 documento: {safe_document_hash}
  </div>
</body>
</html>
"""
    return HTML(string=html).write_pdf()
