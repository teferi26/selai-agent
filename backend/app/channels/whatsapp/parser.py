"""Normalize WhatsApp webhook payloads to our InboundPayload shape."""
from __future__ import annotations

from typing import Any

import phonenumbers

from app.db.models import ConversationChannel
from app.services.conversation_service import InboundPayload
from app.services.lead_resolver import InboundIdentity


def to_e164(raw_phone: str) -> str | None:
    """Best-effort conversion to E.164. WA gives digits-only; assume ES default."""
    if not raw_phone:
        return None
    try:
        parsed = phonenumbers.parse(f"+{raw_phone}", None)
    except phonenumbers.NumberParseException:
        try:
            parsed = phonenumbers.parse(raw_phone, "ES")
        except phonenumbers.NumberParseException:
            return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def parse_whatsapp_webhook(body: dict[str, Any]) -> list[InboundPayload]:
    """Extract zero or more inbound messages. A single webhook may batch several."""
    out: list[InboundPayload] = []

    # Everything here is `isinstance`-checked: the payload is authenticated by
    # then, but "authenticated" only means an agency's own Meta app could have
    # sent it, and a shape we didn't expect must skip the message rather than
    # 500 the webhook (Meta would just retry it forever).
    for entry in body.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict) or "messages" not in value:
                continue  # status/echo event, ignore for now

            metadata = value.get("metadata")
            phone_number_id = (
                metadata.get("phone_number_id") if isinstance(metadata, dict) else None
            )
            contacts = {
                c.get("wa_id"): c
                for c in value.get("contacts") or []
                if isinstance(c, dict)
            }

            for msg in value.get("messages") or []:
                if not isinstance(msg, dict):
                    continue
                wa_id = msg.get("from")
                contact = contacts.get(wa_id) or {}
                profile = contact.get("profile")
                display_name = profile.get("name") if isinstance(profile, dict) else None

                text = _extract_text(msg)
                media = _extract_media(msg)

                identity = InboundIdentity(
                    channel=ConversationChannel.WHATSAPP,
                    external_thread_id=wa_id,
                    whatsapp_wa_id=wa_id,
                    phone_e164=to_e164(wa_id or ""),
                    display_name=display_name,
                )

                raw: dict[str, Any] = {
                    "message": msg,
                    "phone_number_id": phone_number_id,
                }
                # Voice notes: expose the media id so the route can download
                # and transcribe the audio before the agent sees the message.
                audio = _audio_ref(msg)
                if audio is not None:
                    raw["audio_media_id"] = audio[0]
                    raw["audio_mime_type"] = audio[1]

                out.append(
                    InboundPayload(
                        identity=identity,
                        # agency_id is resolved by the route from phone_number_id.
                        agency_id=None,  # type: ignore[arg-type]
                        text=text,
                        external_message_id=msg.get("id"),
                        media=media,
                        raw=raw,
                    )
                )
    return out


def _audio_ref(msg: dict[str, Any]) -> tuple[str, str | None] | None:
    """(media_id, mime_type) for an audio message, or None."""
    if msg.get("type") != "audio":
        return None
    audio = msg.get("audio") or {}
    media_id = audio.get("id")
    if not media_id:
        return None
    return str(media_id), audio.get("mime_type")


def _extract_text(msg: dict[str, Any]) -> str:
    t = msg.get("type")
    if t == "text":
        return (msg.get("text") or {}).get("body", "")
    if t == "button":
        return (msg.get("button") or {}).get("text", "")
    if t == "interactive":
        interactive = msg.get("interactive") or {}
        kind = interactive.get("type")
        if kind == "button_reply":
            return (interactive.get("button_reply") or {}).get("title", "")
        if kind == "list_reply":
            return (interactive.get("list_reply") or {}).get("title", "")
    if t == "image":
        return (msg.get("image") or {}).get("caption", "") or "[imagen]"
    if t == "audio":
        return "[audio]"
    if t == "document":
        return (msg.get("document") or {}).get("caption", "") or "[documento]"
    if t == "location":
        loc = msg.get("location") or {}
        return f"[ubicación: {loc.get('latitude')}, {loc.get('longitude')}]"
    return "[mensaje no soportado]"


def _extract_media(msg: dict[str, Any]) -> list[dict[str, Any]]:
    media = []
    for key in ("image", "audio", "document", "video"):
        if key in msg:
            item = msg[key]
            media.append({"type": key, "id": item.get("id"), "mime": item.get("mime_type")})
    return media
