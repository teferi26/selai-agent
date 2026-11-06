"""Normalize Twilio inbound SMS webhook to InboundPayload."""
from __future__ import annotations

from app.db.models import ConversationChannel
from app.services.conversation_service import InboundPayload
from app.services.lead_resolver import InboundIdentity


def parse_twilio_sms(form: dict) -> InboundPayload | None:
    from_number = form.get("From")
    body = (form.get("Body") or "").strip()
    if not from_number or not body:
        return None

    identity = InboundIdentity(
        channel=ConversationChannel.SMS,
        external_thread_id=from_number,
        phone_e164=from_number,
        display_name=form.get("ProfileName"),
    )
    return InboundPayload(
        identity=identity,
        agency_id=None,  # type: ignore[arg-type]  # set by route
        text=body,
        external_message_id=form.get("MessageSid"),
        media=[],
        raw={"to": form.get("To"), "twilio": form},
    )
