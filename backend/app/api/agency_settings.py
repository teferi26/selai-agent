"""Agency settings — the dashboard's "Configuración" page.

The `Agency.settings` JSON column stores arbitrary internal config (and may
grow secrets in the future), so the GET **whitelists** the keys it exposes
instead of dumping the raw dict. The PATCH accepts a small, fixed subset:
`timezone` / `default_language` map to real columns, the rest lives inside
the JSON blob. `setup_checklist` is merged key by key so concurrent clients
ticking different boxes don't clobber each other.
"""
from __future__ import annotations

import re
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, StringConstraints, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompts import MAX_AI_INSTRUCTIONS_CHARS
from app.core.auth import AgencyMemberDep
from app.core.logging import get_logger
from app.db.models import Agency
from app.db.session import get_db
from app.services.emails import (
    build_phone_active_email_html,
    email_prefs,
    send_email,
)
from app.services.onboarding import sync_vapi_first_message

router = APIRouter(
    prefix="/api/v1/agencies/{agency_id}/settings",
    tags=["agency-settings"],
    dependencies=[AgencyMemberDep],
)
log = get_logger(__name__)

# Only these keys of the raw JSON blob ever leave the API.
_EXPOSED_SETTINGS_KEYS = (
    "voice_provider",
    "voice_id",
    "voice_name",
    "elevenlabs_voice_id",
    "elevenlabs_voice_name",
    "ai_phone_number",
    "vapi_assistant_id",
    "ai_tone",
    "forbidden_phrases",
    "ai_instructions",
    "ai_mode",
    "business_hours",
    "recording_notice",
    "business_name",
    "zones",
    "schedule_text",
    "website",
    "email_prefs",
)

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

MAX_FORBIDDEN_PHRASES = 50
MAX_FORBIDDEN_PHRASE_LEN = 200
# Free-text instructions for the agent. Bounded because they are prepended to
# every single turn: 2.000 chars is roughly a page of "así trabajamos
# nosotros", enough for the real use cases and cheap enough to cache.
# Kept in sync with `prompts.MAX_AI_INSTRUCTIONS_CHARS`, which also truncates
# at render time for blobs written before this limit existed.
MAX_AI_INSTRUCTIONS_LEN = MAX_AI_INSTRUCTIONS_CHARS
# The AI phone number is set by the team by hand and then printed into an
# email (and into the GSM forwarding codes the customer has to dial), so it
# gets the same bound as every other free-text key in this file. E.164 tops
# out at 15 digits; 32 leaves room for spacing and a "+34 910 00 00 00" style.
MAX_PHONE_NUMBER_LEN = 32


class BusinessHours(BaseModel):
    """{"start": "09:00", "end": "19:00", "days": [0..6]} — days are
    Python weekday numbers (0 = Monday)."""

    start: str
    end: str
    days: list[int] = Field(min_length=1, max_length=7)

    @field_validator("start", "end")
    @classmethod
    def _valid_hhmm(cls, v: str) -> str:
        if not _HHMM_RE.match(v):
            raise ValueError("must be HH:MM (00:00-23:59)")
        return v

    @field_validator("days")
    @classmethod
    def _valid_days(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("days must be in 0-6 (0 = Monday)")
        return sorted(set(v))


class EmailPrefs(BaseModel):
    """(233) Which non-critical emails the agency wants. All default to True.

    Critical transactional mail (dunning, the payment confirmation, the "your
    AI number is live" notice) is not listed on purpose: there is no switch
    for it and it carries no unsubscribe header.
    """

    digest: bool = True
    alerts: bool = True
    lifecycle: bool = True


class EmailPrefsPatch(BaseModel):
    """Partial patch: only the switches actually sent are touched, so a
    client that only knows about `digest` cannot silently re-enable the
    other two."""

    digest: bool | None = None
    alerts: bool | None = None
    lifecycle: bool | None = None


class AgencySettingsView(BaseModel):
    voice_provider: str | None = None
    voice_id: str | None = None
    voice_name: str | None = None
    elevenlabs_voice_id: str | None = None
    elevenlabs_voice_name: str | None = None
    ai_phone_number: str | None = None
    vapi_assistant_id: str | None = None
    setup_checklist: dict[str, bool] = {}
    ai_tone: str | None = None
    forbidden_phrases: list[str] = []
    ai_instructions: str | None = None
    ai_mode: str = "always"
    business_hours: dict | None = None
    recording_notice: bool = True
    business_name: str | None = None
    zones: list[str] = []
    schedule_text: str | None = None
    website: str | None = None
    email_prefs: EmailPrefs = EmailPrefs()


class AgencySettingsOut(BaseModel):
    name: str
    slug: str
    timezone: str
    default_language: str
    settings: AgencySettingsView


class AgencySettingsPatch(BaseModel):
    timezone: str | None = None
    default_language: str | None = None
    ai_phone_number: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_PHONE_NUMBER_LEN)]
        | None
    ) = None
    setup_checklist: dict[str, bool] | None = None
    ai_tone: Literal["cercano", "formal"] | None = None
    forbidden_phrases: (
        list[Annotated[str, StringConstraints(max_length=MAX_FORBIDDEN_PHRASE_LEN)]] | None
    ) = Field(default=None, max_length=MAX_FORBIDDEN_PHRASES)
    ai_instructions: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_AI_INSTRUCTIONS_LEN)]
        | None
    ) = None
    ai_mode: Literal["always", "out_of_hours"] | None = None
    business_hours: BusinessHours | None = None
    recording_notice: bool | None = None
    business_name: Annotated[str, StringConstraints(max_length=120)] | None = None
    zones: list[Annotated[str, StringConstraints(max_length=80)]] | None = Field(
        default=None, max_length=30
    )
    schedule_text: Annotated[str, StringConstraints(max_length=200)] | None = None
    website: Annotated[str, StringConstraints(max_length=200)] | None = None
    email_prefs: EmailPrefsPatch | None = None


def _serialize(agency: Agency) -> AgencySettingsOut:
    raw = dict(agency.settings or {})
    view = {key: raw.get(key) for key in _EXPOSED_SETTINGS_KEYS}
    checklist = raw.get("setup_checklist")
    view["setup_checklist"] = dict(checklist) if isinstance(checklist, dict) else {}
    # Defaulted keys: a missing/None value must render as the default, not null.
    tone = raw.get("ai_tone")
    view["ai_tone"] = tone if isinstance(tone, str) else None
    phrases = raw.get("forbidden_phrases")
    view["forbidden_phrases"] = [str(p) for p in phrases] if isinstance(phrases, list) else []
    instructions = raw.get("ai_instructions")
    view["ai_instructions"] = instructions if isinstance(instructions, str) and instructions else None
    mode = raw.get("ai_mode")
    view["ai_mode"] = mode if isinstance(mode, str) and mode else "always"
    bh = raw.get("business_hours")
    view["business_hours"] = dict(bh) if isinstance(bh, dict) else None
    view["recording_notice"] = raw.get("recording_notice") is not False
    zones = raw.get("zones")
    view["zones"] = [str(z) for z in zones] if isinstance(zones, list) else []
    view["email_prefs"] = email_prefs(raw)
    return AgencySettingsOut(
        name=agency.name,
        slug=agency.slug,
        timezone=agency.timezone,
        default_language=agency.default_language,
        settings=AgencySettingsView(**view),
    )


async def _get_agency(
    db: AsyncSession,
    agency_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Agency:
    # Serialise PATCHes against the agency row. Without the lock, two panels
    # saving different JSON keys could both read the same blob and the later
    # commit would erase the earlier change. It also keeps Vapi's greeting in
    # the same order as the committed recording-notice setting.
    agency = (
        await db.get(Agency, agency_id, with_for_update=True)
        if for_update
        else await db.get(Agency, agency_id)
    )
    if agency is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agency_not_found")
    return agency


@router.get("", response_model=AgencySettingsOut)
async def get_agency_settings(
    agency_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AgencySettingsOut:
    agency = await _get_agency(db, agency_id)
    return _serialize(agency)


@router.patch("", response_model=AgencySettingsOut)
async def patch_agency_settings(
    agency_id: uuid.UUID,
    body: AgencySettingsPatch,
    db: AsyncSession = Depends(get_db),
) -> AgencySettingsOut:
    agency = await _get_agency(db, agency_id, for_update=True)
    provided = body.model_dump(exclude_unset=True)
    before = dict(agency.settings or {})
    before_timezone = agency.timezone
    before_language = agency.default_language

    next_timezone = before_timezone
    next_language = before_language
    if "timezone" in provided and provided["timezone"] is not None:
        next_timezone = provided["timezone"]
    if "default_language" in provided and provided["default_language"] is not None:
        next_language = provided["default_language"]

    json_updates: dict = {}
    drop_keys: set[str] = set()
    new_number: str | None = None
    if "ai_phone_number" in provided:
        # Stripped by the validator, so a whitespace-only value arrives as ""
        # and means "clear it" — it must not be stored, and it certainly must
        # not trigger an email announcing a blank number.
        number = provided["ai_phone_number"] or None
        json_updates["ai_phone_number"] = number
        if number:
            # (190) The team just fulfilled a pending number request. The
            # wizard promised "te avisaremos por email", so this is where that
            # promise is kept — but only the *first* time the agency gets a
            # number. Replacing a number the customer already has (a port, a
            # fix) is not news and must not re-announce.
            if not before.get("ai_phone_number") and before.get("phone_number_requested"):
                new_number = number
            # Either way the request is over: a number is on file. Dropping
            # the flag whenever a number lands (not only on the announce
            # path) keeps the blob honest — `POST /channels/phone` sets
            # `ai_phone_number` directly and never clears it, so the two keys
            # can otherwise both be true at once. `GET /channels` happens to
            # read the number first and so hides that today; nothing
            # guarantees the next reader will.
            drop_keys.add("phone_number_requested")
    # Validated pass-through keys of the JSON blob. Explicit null clears the
    # key back to its default (the GET re-applies defaults).
    for key in (
        "ai_tone",
        "forbidden_phrases",
        # Stripped by the validator: a textarea the owner emptied arrives as
        # "" and means "quítalas", not "guarda una cadena vacía".
        "ai_instructions",
        "ai_mode",
        "business_hours",
        "recording_notice",
        "business_name",
        "zones",
        "schedule_text",
        "website",
    ):
        if key in provided:
            json_updates[key] = provided[key]
    if "ai_instructions" in json_updates and not json_updates["ai_instructions"]:
        # "" (a textarea the owner emptied) and None mean the same thing; store
        # one shape so `_serialize` and the prompt renderer never have to guess.
        json_updates["ai_instructions"] = None
    if "setup_checklist" in provided and provided["setup_checklist"] is not None:
        current = before.get("setup_checklist")
        merged = dict(current) if isinstance(current, dict) else {}
        merged.update(provided["setup_checklist"])
        json_updates["setup_checklist"] = merged
    if provided.get("email_prefs") is not None:
        # Merged like the checklist: only the switches the client sent move.
        prefs = email_prefs(before)
        prefs.update(
            {k: bool(v) for k, v in provided["email_prefs"].items() if v is not None}
        )
        json_updates["email_prefs"] = prefs

    previous_notice = before.get("recording_notice") is not False
    requested_notice = json_updates.get("recording_notice") is not False
    assistant_id = before.get("vapi_assistant_id")
    notice_synced = False
    if (
        "recording_notice" in json_updates
        and requested_notice != previous_notice
        and isinstance(assistant_id, str)
        and assistant_id
    ):
        notice_synced = await sync_vapi_first_message(
            assistant_id,
            agency_name=agency.name,
            recording_notice=requested_notice,
        )
        if not notice_synced:
            await db.rollback()
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "recording_notice_sync_failed",
            )

    if json_updates or drop_keys:
        # Reassign the whole dict so SQLAlchemy's change tracking notices —
        # mutating the JSON in place would silently not persist.
        blob = {**before, **json_updates}
        for key in drop_keys:
            blob.pop(key, None)
        agency.settings = blob

    agency.timezone = next_timezone
    agency.default_language = next_language

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        if notice_synced:
            compensated = await sync_vapi_first_message(
                str(assistant_id),
                agency_name=agency.name,
                recording_notice=previous_notice,
            )
            if not compensated:
                log.critical(
                    "settings.recording_notice.compensation_failed",
                    agency=str(agency.id),
                    assistant=str(assistant_id),
                )
        raise
    await db.refresh(agency)

    if new_number:
        await _notify_phone_number_active(db, agency, new_number)

    return _serialize(agency)


async def _notify_phone_number_active(db: AsyncSession, agency: Agency, number: str) -> None:
    """(190) Tell the owner their AI number is live, with the forwarding codes.

    Best effort and after the commit: the number is assigned whether or not
    Resend answers, and a settings PATCH must never fail because an email
    did. Transactional — no `category`, so no unsubscribe header: this is
    the answer to something the customer explicitly asked for.
    """
    try:
        # Imported here (not at module scope) to keep this module free of a
        # hard dependency on the billing API just for one owner lookup.
        from app.api.billing import agency_owner_email

        to = await agency_owner_email(db, agency)
        if not to:
            log.warning("settings.phone_active.no_recipient", agency=str(agency.id))
            return
        await send_email(
            to=to,
            subject="Tu número de IA ya está activo",
            html=build_phone_active_email_html(agency.name, number),
        )
        log.info("settings.phone_active.notified", agency=str(agency.id))
    except Exception:
        log.exception("settings.phone_active.email_failed", agency=str(agency.id))
