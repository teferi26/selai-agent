"""Self-service signup."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, require_auth
from app.core.logging import get_logger
from app.db.models import Invitation, User
from app.db.models.invitation import normalize_email
from app.db.session import get_db
from app.services.legal_documents import (
    LegalEvidenceError,
    current_versions,
    load_document,
)
from app.services.onboarding import onboard_agency

router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])
log = get_logger(__name__)


class OnboardingBody(BaseModel):
    agency_name: str = Field(min_length=2, max_length=160)
    owner_email: EmailStr
    owner_full_name: str = Field(min_length=2, max_length=120)
    owner_phone: str | None = Field(default=None, max_length=40)
    # NB: the Clerk identity used to travel in the body (`clerk_user_id`).
    # Never again: an attacker could register any email as an agency and put
    # SOMEONE ELSE'S Clerk id on the owner row, hijacking that person's first
    # login. The identity now comes exclusively from the verified session
    # token; an old client still sending the field is simply ignored.
    timezone: str = "Europe/Madrid"
    default_language: str = "es"
    plan_code: str | None = None

    # GDPR / B2B consent (Parte B). All three are mandatory; marketing is opt-in.
    accepted_terms: bool = False
    accepted_dpa: bool = False
    accepted_privacy: bool = False
    marketing_opt_in: bool = False
    # Versions of each document the user is signing. Must match the server's
    # current_versions() at submission time — if the legal text changed
    # between rendering the form and submitting, force a re-load.
    accepted_versions: dict[str, str] = Field(default_factory=dict)
    accepted_hashes: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_consents(self) -> OnboardingBody:
        missing = [
            name
            for name, value in (
                ("accepted_terms", self.accepted_terms),
                ("accepted_dpa", self.accepted_dpa),
                ("accepted_privacy", self.accepted_privacy),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                f"Required consent missing: {', '.join(missing)}. "
                "All three of accepted_terms, accepted_dpa, accepted_privacy must be true."
            )
        return self


class OnboardingOut(BaseModel):
    agency_id: str
    user_id: str
    vapi_assistant_id: str | None
    trial_ends_at: str


@router.post("", response_model=OnboardingOut, status_code=201)
async def onboard(
    body: OnboardingBody,
    request: Request,
    auth: AuthContext = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
) -> OnboardingOut:
    # Whoever signs up must BE the owner they claim to register. Without this,
    # the endpoint was open to the internet and trusted the body outright:
    # anyone could create an agency for any email address and bind an
    # arbitrary Clerk id to the owner row — hijacking that user's account the
    # first time they logged in (require_auth resolves users by clerk_user_id
    # first, email second).
    #
    # The dev bypass (X-Dev-* headers, only when Clerk is unconfigured in a
    # dev environment) keeps working for tests and local work.
    if not auth.dev_bypass:
        if not auth.email:
            # Without an email claim we cannot tie the session to the owner
            # row. This claim is part of the production Clerk config; failing
            # loudly beats onboarding someone we cannot identify.
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "email_claim_missing",
                    "message": (
                        "Tu sesión no incluye el email verificado. Cierra "
                        "sesión y vuelve a entrar; si persiste, escríbenos a "
                        "hola@selai.es."
                    ),
                },
            )
        if auth.email.lower() != body.owner_email.lower():
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "email_mismatch",
                    "message": (
                        "El email de la cuenta debe ser el mismo con el que "
                        "has iniciado sesión."
                    ),
                },
            )

    # Block stale consent: both the version and the SHA-256 of the exact
    # rendered document must match what was displayed. A version alone is not
    # enough: provider identity can change while a browser tab stays open.
    server_versions = current_versions()
    required_slugs = ("terms_of_service", "dpa", "privacy_policy")
    stale: list[dict[str, str]] = []
    for slug in required_slugs:
        sent = body.accepted_versions.get(slug)
        current = server_versions.get(slug)
        current_hash = load_document(slug, current).hash if current else ""
        sent_hash = body.accepted_hashes.get(slug)
        if sent != current or sent_hash != current_hash:
            stale.append(
                {
                    "slug": slug,
                    "sent": sent or "",
                    "current": current or "",
                    "sent_hash": sent_hash or "",
                    "current_hash": current_hash,
                }
            )
    if stale:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "stale_consent", "documents": stale},
        )

    existing = (
        await db.execute(
            select(User).where(func.lower(User.email) == body.owner_email.lower())
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "email_already_registered")

    # An invited colleague must never end up here. The panel already routes
    # them to /invitacion/{token}, but this endpoint is public and a stale tab,
    # a bookmarked /onboarding or a direct POST would otherwise create a
    # *second, empty* agency for someone whose seat already exists — the exact
    # failure this whole feature is about.
    #
    # Nothing about the invitation is echoed back: the caller has not proved
    # they own this mailbox, so neither the token nor the agency's name
    # belongs in the answer.
    pending_invite = (
        await db.execute(
            select(Invitation).where(
                Invitation.email == normalize_email(body.owner_email),
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
                Invitation.expires_at > datetime.now(UTC),
            )
        )
    ).scalars().first()
    if pending_invite is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "pending_invitation",
                "message": (
                    "Tienes una invitación pendiente para unirte a un equipo. "
                    "Abre el enlace que te enviamos por email en lugar de crear "
                    "una agencia nueva."
                ),
            },
        )

    request_ip = request.client.host if request.client else None
    request_ua = request.headers.get("user-agent")

    try:
        result = await onboard_agency(
            db,
            agency_name=body.agency_name,
            owner_email=body.owner_email,
            owner_full_name=body.owner_full_name,
            owner_phone=body.owner_phone,
            # The verified session identity — never the body (see above). In
            # the dev bypass there is no real Clerk id worth persisting.
            clerk_user_id=None if auth.dev_bypass else auth.clerk_user_id,
            timezone_name=body.timezone,
            default_language=body.default_language,
            plan_code=body.plan_code,
            accepted_versions=body.accepted_versions,
            accepted_hashes=body.accepted_hashes,
            marketing_opt_in=body.marketing_opt_in,
            request_ip=request_ip,
            request_ua=request_ua,
        )
        await db.commit()
    except LegalEvidenceError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "stale_consent", "message": str(exc)},
        ) from exc
    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        await db.rollback()
        log.exception("onboarding.failed")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    # Welcome email with the 7 puesta-en-marcha steps. Best-effort — a mail
    # provider hiccup must never fail a signup that already committed.
    try:
        from app.services.emails import build_welcome_email_html, send_email

        await send_email(
            to=body.owner_email,
            subject="Bienvenido a selai — tu agente de IA está listo",
            html=build_welcome_email_html(body.agency_name, body.owner_full_name),
        )
    except Exception:
        log.exception("onboarding.welcome_email_failed", agency=str(result.agency_id))

    return OnboardingOut(
        agency_id=str(result.agency_id),
        user_id=str(result.user_id),
        vapi_assistant_id=result.vapi_assistant_id,
        trial_ends_at=result.trial_ends_at.isoformat(),
    )
