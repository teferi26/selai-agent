"""Normalize inbound email payloads to InboundPayload.

Supports a "common JSON" format that fits both Postmark and a custom
Cloudflare Email Worker we control. SES SNS payloads are also accepted
when wrapped beforehand. Avoids hard-coupling to one provider.

Common fields:
    From, FromName, To, Subject, MessageID, InReplyTo, References,
    TextBody, HtmlBody, StrippedTextReply

Resend Inbound webhooks are also accepted natively. Their envelope is
`{"type": "email.received", "data": {"from": ..., "to": [...], ...}}`;
we detect it by the presence of "type" + "data" and normalize `data`
into the common shape above before parsing.
"""
from __future__ import annotations

import re
from email.utils import parseaddr
from typing import Any

from app.db.models import ConversationChannel
from app.services.conversation_service import InboundPayload
from app.services.lead_resolver import InboundIdentity

_QUOTE_PATTERNS = [
    re.compile(r"\nOn .+ wrote:\n", re.IGNORECASE),
    re.compile(r"\nEl .+ escribió:\n", re.IGNORECASE),
    re.compile(r"\n-----+ ?Original Message ?-----+\n", re.IGNORECASE),
    re.compile(r"\nDe: .+\nEnviado: .+\n", re.IGNORECASE),
]


def strip_quoted_reply(body: str) -> str:
    """Cheap inline-reply detector. Postmark gives us this for free in
    `StrippedTextReply` — we use that when present."""
    for pat in _QUOTE_PATTERNS:
        m = pat.search(body)
        if m:
            return body[: m.start()].strip()
    return body.strip()


def _addr_to_str(value: Any) -> str:
    """Tolerate both `"Name <a@b>"` strings and `{"name": ..., "email": ...}`."""
    if isinstance(value, dict):
        email_addr = str(value.get("email") or "")
        name = str(value.get("name") or "")
        return f"{name} <{email_addr}>" if name and email_addr else email_addr
    return str(value or "")


def _headers_to_dict(headers: Any) -> dict[str, Any]:
    """Resend may ship headers as a dict or as `[{"name": ..., "value": ...}]`."""
    if isinstance(headers, dict):
        return headers
    if isinstance(headers, list):
        return {
            str(h["name"]): h.get("value")
            for h in headers
            if isinstance(h, dict) and h.get("name")
        }
    return {}


def _header(headers: dict[str, Any], name: str) -> Any:
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return value
    return None


def is_resend_inbound(payload: dict[str, Any]) -> bool:
    return "type" in payload and isinstance(payload.get("data"), dict)


def _normalize_resend_inbound(payload: dict[str, Any]) -> dict[str, Any]:
    """Map a Resend Inbound webhook (`{"type": "email.received", "data": ...}`)
    onto the common field names the parser already understands."""
    data = payload.get("data") or {}
    headers = _headers_to_dict(data.get("headers"))

    to_value = data.get("to")
    if isinstance(to_value, list):
        to_addr = next((_addr_to_str(t) for t in to_value if t), "")
    else:
        to_addr = _addr_to_str(to_value)

    return {
        "From": _addr_to_str(data.get("from")),
        "To": to_addr,
        "Subject": data.get("subject") or "",
        "TextBody": data.get("text") or "",
        "HtmlBody": data.get("html") or "",
        "MessageID": (
            data.get("message_id") or _header(headers, "Message-ID") or data.get("email_id") or ""
        ),
        "InReplyTo": _header(headers, "In-Reply-To"),
        "References": _header(headers, "References") or "",
    }


def parse_inbound_email(payload: dict[str, Any]) -> InboundPayload | None:
    if is_resend_inbound(payload):
        payload = _normalize_resend_inbound(payload)

    from_addr_raw = payload.get("From") or payload.get("from") or ""
    display_name, email_addr = parseaddr(from_addr_raw)
    if not email_addr:
        return None

    to_addr_raw = payload.get("To") or payload.get("to") or ""
    _, to_addr = parseaddr(to_addr_raw)

    text_body = (
        payload.get("StrippedTextReply")
        or payload.get("TextBody")
        or payload.get("text")
        or ""
    )
    if not text_body and (html := payload.get("HtmlBody") or payload.get("html")):
        # Crude HTML-to-text fallback.
        text_body = re.sub(r"<[^>]+>", " ", html)
    text_body = strip_quoted_reply(text_body)

    subject = payload.get("Subject") or payload.get("subject") or ""
    if subject and not text_body.startswith(subject):
        text_body = f"[{subject}]\n\n{text_body}"

    message_id = payload.get("MessageID") or payload.get("messageId") or ""
    in_reply_to = payload.get("InReplyTo") or payload.get("inReplyTo")
    references = payload.get("References") or payload.get("references") or ""
    thread_root = (in_reply_to or "").strip() or _first_reference(references) or message_id

    identity = InboundIdentity(
        channel=ConversationChannel.EMAIL,
        external_thread_id=thread_root,
        email=email_addr.lower(),
        display_name=display_name or None,
    )

    return InboundPayload(
        identity=identity,
        agency_id=None,  # type: ignore[arg-type]  # set by route
        text=text_body or "(sin contenido)",
        external_message_id=message_id,
        media=[],
        raw={
            "subject": subject,
            "to": to_addr,
            "headers": {
                "In-Reply-To": in_reply_to,
                "References": references,
                "Message-ID": message_id,
            },
        },
    )


def _first_reference(references: str) -> str | None:
    references = (references or "").strip()
    if not references:
        return None
    parts = [p.strip() for p in references.split() if p.strip()]
    return parts[0] if parts else None
