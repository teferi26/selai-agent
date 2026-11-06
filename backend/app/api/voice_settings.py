"""Voice selection for the AI phone agent.

Two routers:

- `catalog_router` — static curated catalog of Spanish-capable voices
  (Cartesia + ElevenLabs multilingual). No agency in the path, no guard:
  it exposes nothing tenant-specific. Every entry carries `previewable`,
  which tells the UI up front whether `POST /voice/preview` can play it.
- `router` — agency-scoped PUT that stores the chosen voice in
  `agency.settings` and best-effort applies it to the agency's Vapi
  assistant. A Vapi failure never fails the request — the choice is
  persisted and can be re-applied later.
"""
from __future__ import annotations

import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, computed_field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AgencyMemberDep
from app.core.logging import get_logger
from app.core.settings import settings
from app.db.models import Agency
from app.db.session import get_db
from app.services.plan_limits import ensure_feature

catalog_router = APIRouter(prefix="/api/v1/voice", tags=["voice-settings"])
router = APIRouter(
    prefix="/api/v1/agencies/{agency_id}/voice",
    tags=["voice-settings"],
    dependencies=[AgencyMemberDep],
)
log = get_logger(__name__)

_VAPI_BASE = "https://api.vapi.ai"


# (3)(20) Providers `voice_preview` below can actually synthesize. Declared
# once and consumed both by the endpoint and by `CatalogVoice.previewable`, so
# the flag the dashboard trusts cannot drift from what the endpoint does.
#
# Cartesia is deliberately absent: its TTS (`POST https://api.cartesia.ai/tts/
# bytes`) and even its `preview_file_url` both require an `sk_car_...` key, and
# there is no cartesia key in `app/core/settings.py`. Implementing the preview
# would mean shipping dead code behind a credential we do not have.
PREVIEWABLE_PROVIDERS = frozenset({"11labs"})


class CatalogVoice(BaseModel):
    id: str
    provider: str
    name: str
    language: str
    gender: str
    description: str
    preview_url: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def previewable(self) -> bool:
        """Whether the UI can actually let the agency hear this voice.

        Derived, not stored: a hand-written flag is exactly the kind of thing
        that goes stale the day a provider is added. The dashboard used to
        hardcode `provider === "11labs"` to decide whether to draw the play
        button, which put the same rule in two repos.
        """
        return self.preview_url is not None or self.provider in PREVIEWABLE_PROVIDERS


# Curated static catalog.
#
# (3)(20) The catalog used to carry three "Sonic" voices. Two of them,
# `sonic-multilingual-es-male` and `sonic-multilingual-es-neutral`, are not
# Cartesia voices at all: Cartesia identifies voices by UUID (see its List
# Voices API) and those two strings appear in no Cartesia library or doc —
# picking one PATCHed the agency's Vapi assistant with a voiceId nothing can
# resolve. They are replaced here by real ElevenLabs voices, which the preview
# endpoint can synthesize, keeping the same male/female pair.
#
# `sonic-multilingual` stays: it is the voice `_provision_vapi_assistant`
# assigns to every new assistant (services/onboarding.py), so dropping it would
# leave freshly-onboarded agencies with no catalog entry matching their own
# assistant. It is the one voice that honestly reports `previewable: false`.
_VOICE_CATALOG: list[CatalogVoice] = [
    CatalogVoice(
        id="sonic-multilingual",
        provider="cartesia",
        name="Sonic — Lucía",
        language="es",
        gender="female",
        description="Cálida y cercana, ideal para atención al cliente de la agencia.",
    ),
    CatalogVoice(
        id="JBFqnCBsd6RMkjVDRZzb",
        provider="11labs",
        name="George",
        language="es",
        gender="male",
        description="Pausada y de registro grave, transmite confianza al cualificar leads.",
    ),
    CatalogVoice(
        id="XrExE9yKIg1WjnnlVkGX",
        provider="11labs",
        name="Matilda",
        language="es",
        gender="female",
        description="Profesional y amable, perfecta para inmobiliarias de perfil premium.",
    ),
    CatalogVoice(
        id="EXAVITQu4vr4xnSDxMaL",
        provider="11labs",
        name="Sarah",
        language="es",
        gender="female",
        description="Cálida y cercana, ideal para primeras llamadas y seguimiento de leads.",
    ),
    CatalogVoice(
        id="onwK4e9ZLuTAKqWW03F9",
        provider="11labs",
        name="Daniel",
        language="es",
        gender="male",
        description="Grave y tranquila, aporta seriedad en operaciones de venta.",
    ),
    CatalogVoice(
        id="XB0fDUnXU5powFXDhCwa",
        provider="11labs",
        name="Charlotte",
        language="es",
        gender="female",
        description="Joven y dinámica, encaja con alquiler y público internacional.",
    ),
    CatalogVoice(
        id="pNInz6obpgDQGcFmaJgB",
        provider="11labs",
        name="Adam",
        language="es",
        gender="male",
        description="Enérgica y comercial, buena para campañas salientes.",
    ),
]



# (3)(20) Ids that WERE in the catalog above and are not any more, mapped to
# the entry that replaces each one.
#
# Removing a catalog entry does not remove it from the agencies that already
# chose it: their `settings["voice_id"]` still points here, and their Vapi
# assistant was PATCHed with it. Both panels (dashboard VoiceClient.tsx,
# mobile StepVoice.tsx) preselect by matching `settings.voice_id` against this
# catalog, so an agency that picked "Sonic — Mateo" would open the voice screen
# and be told it has chosen nothing — a worse place than before the fix, and
# with the dead voiceId still live in Vapi.
#
# `app/scripts/fix_retired_voices.py` walks this map to repair both. It stays
# here, next to the catalog, so retiring a voice and providing its successor is
# one edit: a test asserts every value is a real catalog id and that no key is.
#
# Gender is preserved on purpose — it is the one attribute of a voice an
# agency picked deliberately.
RETIRED_VOICE_IDS: dict[str, str] = {
    # Both were invented Cartesia ids (Cartesia identifies voices by UUID), so
    # nothing could resolve them; replaced by real ElevenLabs voices of the
    # same gender, which the preview endpoint can actually synthesize.
    "sonic-multilingual-es-male": "JBFqnCBsd6RMkjVDRZzb",  # Sonic — Mateo -> George
    "sonic-multilingual-es-neutral": "XrExE9yKIg1WjnnlVkGX",  # Sonic — Carmen -> Matilda
}


def catalog_voice(voice_id: str) -> CatalogVoice | None:
    """The catalog entry for an id, or None if it is not one of ours."""
    return next((v for v in _VOICE_CATALOG if v.id == voice_id), None)


def replacement_for(voice_id: str | None) -> CatalogVoice | None:
    """The voice that supersedes a retired id, or None if nothing to do.

    Pure, so the repair script's decision is unit-testable without a database.
    """
    if not voice_id:
        return None
    replacement_id = RETIRED_VOICE_IDS.get(voice_id)
    return catalog_voice(replacement_id) if replacement_id else None


@catalog_router.get("/catalog", response_model=list[CatalogVoice])
async def voice_catalog() -> list[CatalogVoice]:
    return _VOICE_CATALOG


_ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"


class VoicePreviewBody(BaseModel):
    provider: str
    voice_id: str
    text: str = Field(min_length=1, max_length=300)


@router.post("/preview")
async def voice_preview(
    agency_id: uuid.UUID,
    body: VoicePreviewBody,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Synthesize a short sample so the agency can hear a voice before
    choosing it. Only `PREVIEWABLE_PROVIDERS` are supported; anything else (or
    a missing key) is a clean 501 the dashboard can render as 'no preview'.

    The catalog reports the same rule up front via `previewable`, so the UI
    never has to discover it by getting a 501.
    """
    # Each preview is a real ElevenLabs synthesis billed to selai, on a
    # channel Starter does not buy (seed_plans.py: starter has `voice:
    # False`). Checked before the provider call, not after.
    await ensure_feature(db, agency_id, "voice")

    api_key = settings.elevenlabs_api_key.get_secret_value()
    if body.provider not in PREVIEWABLE_PROVIDERS or not api_key:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "preview_unavailable")

    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(
                f"{_ELEVENLABS_TTS_URL}/{body.voice_id}",
                json={"text": body.text, "model_id": "eleven_multilingual_v2"},
                headers={"xi-api-key": api_key},
            )
    except Exception as exc:
        log.exception("voice.preview_error", voice=body.voice_id)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "preview_provider_failed") from exc

    if resp.status_code >= 400:
        log.error(
            "voice.preview_failed",
            voice=body.voice_id,
            status=resp.status_code,
            body=resp.text[:200],
        )
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "preview_provider_failed")

    return Response(content=resp.content, media_type="audio/mpeg")


class VoiceSelection(BaseModel):
    provider: str
    voice_id: str
    voice_name: str


class VoiceSelectionOut(BaseModel):
    ok: bool
    applied_to_vapi: bool


async def _apply_voice_to_vapi(assistant_id: str, provider: str, voice_id: str) -> bool:
    """PATCH the Vapi assistant's voice. Returns False on any failure."""
    api_key = settings.vapi_api_key.get_secret_value()
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.patch(
                f"{_VAPI_BASE}/assistant/{assistant_id}",
                json={"voice": {"provider": provider, "voiceId": voice_id}},
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code >= 400:
            log.error(
                "voice.vapi_patch_failed",
                assistant=assistant_id,
                status=resp.status_code,
                body=resp.text[:200],
            )
            return False
        return True
    except Exception:
        log.exception("voice.vapi_patch_error", assistant=assistant_id)
        return False


@router.put("", response_model=VoiceSelectionOut)
async def set_agency_voice(
    agency_id: uuid.UUID,
    body: VoiceSelection,
    db: AsyncSession = Depends(get_db),
) -> VoiceSelectionOut:
    agency = await db.get(Agency, agency_id)
    if agency is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agency_not_found")

    # Agencies onboarded before the Vapi key was configured have no
    # assistant yet — provision it lazily so choosing a voice "just works".
    assistant_id = (agency.settings or {}).get("vapi_assistant_id")
    if settings.vapi_api_key.get_secret_value() and not assistant_id:
        try:
            from app.services.onboarding import _provision_vapi_assistant

            assistant_id = await _provision_vapi_assistant(
                agency_name=agency.name,
                slug=agency.slug,
                # Without this the new assistant carries no agency_id and
                # every inbound call through it is unroutable (see
                # `_resolve_agency` in app/api/voice.py).
                agency_id=agency.id,
                recording_notice=(
                    (agency.settings or {}).get("recording_notice", True) is not False
                ),
            )
        except Exception:
            log.exception("voice.vapi_lazy_provision_failed", agency=str(agency_id))
            assistant_id = None

    # Reassign the dict so SQLAlchemy detects the JSON change.
    agency.settings = {
        **dict(agency.settings or {}),
        "voice_provider": body.provider,
        "voice_id": body.voice_id,
        "voice_name": body.voice_name,
        **({"vapi_assistant_id": assistant_id} if assistant_id else {}),
    }
    await db.commit()

    applied = False
    if settings.vapi_api_key.get_secret_value() and assistant_id:
        applied = await _apply_voice_to_vapi(str(assistant_id), body.provider, body.voice_id)

    return VoiceSelectionOut(ok=True, applied_to_vapi=applied)
