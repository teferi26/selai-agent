"""Detect & extract lead info from portal notification emails.

Idealista, Fotocasa and Habitaclia send standardized emails when a
visitor contacts an agency through their portal. This module recognizes
those formats and pulls out:
  - Lead's name, email, phone
  - Their message
  - The referenced property (anuncio id / reference) when present

Returns a dict that callers (the email channel) merge into the
InboundPayload before handing off to the agent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Any

PORTAL_FROM_DOMAINS = {
    "idealista.com": "idealista",
    "no-reply.idealista.com": "idealista",
    "fotocasa.es": "fotocasa",
    "noreply.fotocasa.es": "fotocasa",
    "habitaclia.com": "habitaclia",
    "noreply.habitaclia.com": "habitaclia",
    # (193) The other three portals a Spanish agency actually uses. Without
    # them their notifications were treated as an ordinary email: the agent
    # answered the portal's noreply address, which nobody reads, and the
    # real buyer never heard back.
    "pisos.com": "pisos.com",
    "mail.pisos.com": "pisos.com",
    "yaencontre.com": "yaencontre",
    "noreply.yaencontre.com": "yaencontre",
    "milanuncios.com": "milanuncios",
    "no-reply.milanuncios.com": "milanuncios",
}

# Domains whose addresses must never be quoted back at a lead: they belong
# to the portal, not to the person.
_PORTAL_DOMAIN_HINTS = tuple(PORTAL_FROM_DOMAINS)

# Local-parts that mean "this mailbox is a dead end". Matched only to decide
# whether to *reply*, never to decide whether to process the lead.
_NOREPLY_LOCAL_PARTS = (
    "noreply",
    "no-reply",
    "no_reply",
    "donotreply",
    "do-not-reply",
    "do_not_reply",
    "notificaciones",
    "notifications",
    "notificacion",
    "mailer-daemon",
    "postmaster",
    "bounce",
)


@dataclass
class PortalLead:
    portal: str
    name: str | None
    email: str | None
    phone: str | None
    message: str
    property_reference: str | None
    property_url: str | None


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?\d[\s\-]?){8,15}\d")
# The reference must contain a digit ("anuncio idealista 99887766" should
# capture the number, not the portal name); filler words in between are skipped.
_REF_RE = re.compile(
    r"(?:ref(?:erencia)?|anuncio)"
    r"(?:[\s:#]+(?:idealista|fotocasa|habitaclia|anuncio|en|del?|n[ºo]\.?|num\.?))*"
    r"[\s:#]*((?=[A-Z0-9\-]*\d)[A-Z0-9\-]{4,})",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s>\)\"]+")


def _domain_matches(domain: str, known: str) -> bool:
    """The domain *is* the portal, or is a subdomain of it.

    On the dot boundary, not on the bare suffix: `endswith("idealista.com")`
    also matches `notidealista.com`, which is both a way to get an ordinary
    sender treated as a portal (and never answered) and a way to spoof one.
    """
    return domain == known or domain.endswith(f".{known}")


def detect_portal(from_addr_raw: str) -> str | None:
    _, addr = parseaddr(from_addr_raw or "")
    if not addr or "@" not in addr:
        return None
    domain = addr.rsplit("@", 1)[1].lower()
    for known, name in PORTAL_FROM_DOMAINS.items():
        if _domain_matches(domain, known):
            return name
    return None


def is_noreply_address(addr_raw: str) -> bool:
    """True for mailboxes that exist only to send: portals and noreply@.

    (193) Answering one of these is worse than silence — the buyer waits for
    a reply that went to a machine, and the agency looks like it ignored
    them.
    """
    _, addr = parseaddr(addr_raw or "")
    if not addr or "@" not in addr:
        return True
    local, domain = addr.rsplit("@", 1)
    local = local.lower()
    domain = domain.lower()
    if any(_domain_matches(domain, known) for known in _PORTAL_DOMAIN_HINTS):
        return True
    return any(part in local for part in _NOREPLY_LOCAL_PARTS)


def can_reply_to(addr_raw: str) -> bool:
    """The inverse of `is_noreply_address`, for readability at call sites."""
    return not is_noreply_address(addr_raw)


def portal_lead_is_usable(portal_lead: PortalLead | None) -> bool:
    """True when we pulled a real way to reach the person out of the email.

    (193) Without an email or a phone the "lead" is just the portal's
    notification with no one behind it: the agency has to be told, because
    the alternative is a lost lead nobody noticed.
    """
    if portal_lead is None:
        return False
    return bool(portal_lead.email or portal_lead.phone)


def parse_portal_email(body: str, *, from_addr_raw: str) -> PortalLead | None:
    portal = detect_portal(from_addr_raw)
    if portal is None:
        return None

    body = body or ""
    name = _extract_name(body, portal)
    # The portal's own addresses are all over its template; the one we want
    # is the buyer's, so anything on a portal domain is skipped.
    email = _first_lead_email(body)
    phone = _normalize_phone(_first_match(_PHONE_RE, body))

    ref_match = _REF_RE.search(body)
    reference = ref_match.group(1) if ref_match else None

    url_match = None
    for u in _URL_RE.findall(body):
        if any(d in u.lower() for d in _PORTAL_DOMAIN_HINTS):
            url_match = u
            break

    message = _extract_message(body, portal)

    return PortalLead(
        portal=portal,
        name=name,
        email=email,
        phone=phone,
        message=message,
        property_reference=reference,
        property_url=url_match,
    )


def _extract_name(body: str, portal: str) -> str | None:
    # Idealista: "Nombre y apellidos: Juan Pérez"
    m = re.search(r"(?:Nombre(?: y apellidos?)?|De(?: parte de)?|From)[\s:]+([^\n\r]{2,80})", body, re.IGNORECASE)
    if m:
        candidate = m.group(1).strip(" .,-:")
        if candidate and not _looks_like_email_or_phone(candidate):
            return candidate
    return None


def _extract_message(body: str, portal: str) -> str:
    # Try to find the explicit "Mensaje" / "Comentario" block.
    for header in ("Mensaje", "Comentario", "Comments", "Message"):
        m = re.search(rf"{header}[\s:]+(.+?)(?=\n[A-ZÑ][a-zñ]+[: ]|$)", body, re.IGNORECASE | re.DOTALL)
        if m:
            text = m.group(1).strip()
            if 5 < len(text) < 4000:
                return text
    # Fallback: first 1000 chars of the body, stripping signatures/HTML noise.
    return body.strip()[:1000]


def _looks_like_email_or_phone(s: str) -> bool:
    return bool(_EMAIL_RE.fullmatch(s)) or bool(_PHONE_RE.fullmatch(s))


def _first_lead_email(body: str) -> str | None:
    """The first address in the body that is not the portal's own.

    Domain-aware on purpose: a plain substring test would drop
    `juan.pisos.company@mail.example.com` because "pisos.com" happens to appear in
    it, and that address is the whole point of the email.
    """
    for match in _EMAIL_RE.finditer(body or ""):
        candidate = match.group(0).strip()
        if is_noreply_address(candidate):
            continue
        return candidate
    return None


def _first_match(pattern: re.Pattern, body: str, *, exclude_domains: tuple = ()) -> str | None:
    for m in pattern.finditer(body):
        candidate = m.group(0).strip()
        if exclude_domains and any(d in candidate for d in exclude_domains):
            continue
        return candidate
    return None


def _normalize_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 9:
        return None
    # Default Spanish prefix when missing — best-effort.
    if not raw.lstrip().startswith("+") and len(digits) == 9:
        return f"+34{digits}"
    return f"+{digits}"


def merge_portal_lead_into_payload(portal_lead: PortalLead, payload: Any) -> None:
    """Mutate payload.identity and payload.text to use the real lead's data
    (extracted from the portal body) rather than the portal's noreply address.
    """
    identity = payload.identity
    if portal_lead.email:
        identity.email = portal_lead.email
    if portal_lead.phone:
        identity.phone_e164 = portal_lead.phone
    if portal_lead.name and not identity.display_name:
        identity.display_name = portal_lead.name
    identity.source_campaign = portal_lead.portal

    extra = portal_lead.message
    if portal_lead.property_reference:
        extra = f"[Anuncio {portal_lead.portal} ref {portal_lead.property_reference}]\n\n{extra}"
    if portal_lead.property_url:
        extra += f"\n\n{portal_lead.property_url}"
    payload.text = extra
    raw = dict(payload.raw or {})
    raw["portal"] = portal_lead.portal
    raw["portal_property_reference"] = portal_lead.property_reference
    raw["portal_property_url"] = portal_lead.property_url
    payload.raw = raw
