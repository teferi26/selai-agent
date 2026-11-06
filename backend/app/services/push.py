"""Expo push notifications to the agency's mobile devices.

Best-effort by design: a push failing must never break the flow that
triggered it (usually the agent escalating a conversation), so this module
logs and swallows every error.
"""
from __future__ import annotations

import re
import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import DeviceToken

log = get_logger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
_BATCH_SIZE = 100  # Expo's documented max messages per request
LEAD_ACTIONS_CATEGORY = "lead_actions"
_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def _message_payload(
    token: str,
    title: str,
    body: str,
    data: dict | None,
) -> dict:
    """Build one Expo message and enable call actions only for a safe E.164."""
    message = {
        "to": token,
        "title": title,
        "body": body,
        "data": data or {},
        "sound": "default",
    }
    phone = (data or {}).get("phone")
    if isinstance(phone, str) and _E164_RE.fullmatch(phone):
        message["categoryId"] = LEAD_ACTIONS_CATEGORY
    return message


async def send_push(
    db: AsyncSession,
    agency_id: uuid.UUID,
    title: str,
    body: str,
    data: dict | None = None,
) -> int:
    """Send a push to every registered device of the agency.

    Returns the number of messages attempted. Never raises.
    """
    try:
        tokens = [
            t
            for (t,) in (
                await db.execute(
                    select(DeviceToken.expo_push_token).where(
                        DeviceToken.agency_id == agency_id
                    )
                )
            ).all()
        ]
    except Exception:
        log.exception("push.token_load_failed", agency=str(agency_id))
        return 0

    if not tokens:
        return 0

    sent = 0
    for start in range(0, len(tokens), _BATCH_SIZE):
        batch = tokens[start : start + _BATCH_SIZE]
        payload = [_message_payload(token, title, body, data) for token in batch]
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                resp = await http.post(EXPO_PUSH_URL, json=payload)
            if resp.status_code >= 400:
                log.error(
                    "push.expo_error",
                    agency=str(agency_id),
                    status=resp.status_code,
                    body=resp.text[:200],
                )
                continue
            sent += len(batch)
        except Exception:
            log.exception("push.send_failed", agency=str(agency_id))

    return sent
