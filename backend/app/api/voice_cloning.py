"""Voice cloning endpoints for agencies."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AgencyMemberDep
from app.core.logging import get_logger
from app.db.models import Agency
from app.db.session import get_db
from app.services.plan_limits import ensure_feature
from app.services.voice_cloning import clone_voice

router = APIRouter(
    prefix="/api/v1/agencies/{agency_id}/voice-cloning",
    tags=["voice-cloning"],
    dependencies=[AgencyMemberDep],
)
log = get_logger(__name__)


@router.post("", status_code=201)
async def upload_voice(
    agency_id: uuid.UUID,
    name: str = Form(...),
    consent_signed: bool = Form(...),
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    if not consent_signed:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "consent_required: la persona cuya voz se clona debe firmar consentimiento explícito.",
        )

    agency = await db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agency_not_found")

    # The pricing page sells voice cloning as Agencia-only — enforce it.
    await ensure_feature(db, agency_id, "voice_cloning")

    audio_payload: list[tuple[str, bytes, str]] = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        audio_payload.append((f.filename or "sample.mp3", data, f.content_type or "audio/mpeg"))

    if not audio_payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no_audio_provided")

    try:
        result = await clone_voice(
            name=f"{agency.name} — {name}",
            description=f"Voice clone for {agency.name}, signed consent on file.",
            audio_files=audio_payload,
        )
    except Exception as exc:
        log.exception("voice_cloning.failed", agency=str(agency_id))
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"provider_failed: {exc}") from exc

    # A freshly cloned voice becomes the active assistant voice: that is what
    # "usa tu propia voz" means to the agency. Provision the Vapi assistant
    # lazily if the agency predates the Vapi key; tolerate every failure.
    from app.api.voice_settings import _apply_voice_to_vapi
    from app.core.settings import settings as app_settings
    from app.services.onboarding import _provision_vapi_assistant

    assistant_id = (agency.settings or {}).get("vapi_assistant_id")
    if app_settings.vapi_api_key.get_secret_value() and not assistant_id:
        try:
            assistant_id = await _provision_vapi_assistant(
                agency_name=agency.name,
                slug=agency.slug,
                # Without this the assistant carries no `agency_id` in its
                # Vapi metadata and every inbound call through it depends on
                # our settings blob to be routable at all (`_resolve_agency`
                # in app/api/voice.py).
                agency_id=agency.id,
            )
        except Exception:
            log.exception("voice_cloning.vapi_lazy_provision_failed", agency=str(agency_id))
            assistant_id = None

    s = dict(agency.settings or {})
    s["elevenlabs_voice_id"] = result.voice_id
    s["elevenlabs_voice_name"] = result.name
    s["voice_provider"] = "11labs"
    s["voice_id"] = result.voice_id
    s["voice_name"] = result.name
    if assistant_id:
        s["vapi_assistant_id"] = assistant_id
    agency.settings = s
    await db.commit()

    if app_settings.vapi_api_key.get_secret_value() and assistant_id:
        await _apply_voice_to_vapi(str(assistant_id), "11labs", result.voice_id)

    return {"voice_id": result.voice_id, "name": result.name}
