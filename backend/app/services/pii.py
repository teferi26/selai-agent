"""PII redaction for prompts, logs, and historical data.

Used in two places:
- Building cross-channel memory and few-shot examples — the LLM never sees
  another lead's email/phone/IBAN/DNI/address. Only the current lead's
  first name is preserved (via `allow_first_name_of`).
- Persisting `agent_runs.output_text` and `tool_calls` — the database stops
  being a PII honeypot for whoever queries it.

The redaction is one-way and deterministic. Tokens like `[EMAIL]`, `[PHONE]`,
`[IBAN]` survive the round-trip so debugging keeps the narrative shape of the
message even after redaction.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

import phonenumbers

REDACTION_VERSION = "v1"

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# IBAN: country (2 letters) + check (2 digits) + BBAN (up to 30 alnum).
# Spanish IBAN is 24 chars; this matches any valid-shaped IBAN.
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")

# DNI / NIE / CIF (Spain).
_DNI_RE = re.compile(r"\b[XYZ]?\d{7,8}[A-Z]\b")

# Postal address indicator (loose; pairs with a number after).
_ADDRESS_RE = re.compile(
    r"\b(?:Calle|C/|C\.|Avda\.?|Avenida|Plaza|Plza\.?|Pza\.?|Pje\.?|Pasaje|Camino|Carrera|Ronda|Paseo|Pº|Travesía)\s+"
    r"[A-ZÁÉÍÓÚÑa-záéíóúñ0-9'\-\.\s]{2,60}?(?:\s+(?:n[ºoº\.]?\s*)?\d{1,4}\s*[A-Za-z]?)?",
    re.IGNORECASE,
)

# External URLs. Internal handlers should keep their own dominio whitelist.
_URL_RE = re.compile(r"https?://[^\s)>\]\"]+", re.IGNORECASE)

# Phone numbers: prefer phonenumbers library (handles +34, 0034, formatted).
# Fallback regex catches loose Spanish mobiles.
_LOOSE_PHONE_RE = re.compile(r"(?<!\d)(?:\+?34[\s\-]?)?[6-9](?:[\s\-]?\d){8}(?!\d)")

# Capitalized name tokens (very loose). Filtered with a small stoplist so
# we don't redact city / brand / weekday names. Used only when no NER lib.
_CAP_NAME_RE = re.compile(r"\b([A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,})(?:\s+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}))?\b")

# Geographic / temporal / domain words that LOOK like names but aren't PII.
# Keep this stoplist short and additive; false negatives on names are
# acceptable, false positives on places break the prompt's usefulness.
_NAME_STOPLIST: frozenset[str] = frozenset(
    w.lower()
    for w in (
        # Spanish weekdays and months
        "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo",
        "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
        # Major cities and regions in Spain
        "Madrid", "Barcelona", "Valencia", "Sevilla", "Zaragoza", "Málaga",
        "Bilbao", "Alicante", "Murcia", "Granada", "Vigo", "Gijón", "Córdoba",
        "Cádiz", "Coruña", "Pamplona", "Salamanca", "Toledo", "Burgos", "León",
        "Lleida", "Tarragona", "Castellón", "Almería", "Huelva", "Jaén", "Cuenca",
        "Mallorca", "Tenerife", "Canarias", "Baleares", "Cataluña", "Galicia",
        "Andalucía", "Aragón", "Asturias", "Cantabria", "Navarra", "Valencia",
        "Euskadi", "Rioja", "Madrid", "Murcia", "Extremadura",
        # Common neighborhoods worth keeping
        "Salamanca", "Chamberí", "Malasaña", "Chueca", "Lavapiés", "Centro",
        "Eixample", "Gracia", "Gòtic", "Born", "Sants", "Poblenou",
        # Property-related words capitalised at start of sentence
        "Hola", "Buenas", "Buenos", "Gracias", "Perdón", "Disculpa",
        "Quiero", "Busco", "Necesito", "Tengo", "Estoy", "Vivo",
        "Sí", "No", "Vale", "Perfecto", "Genial",
        # Channel / brand words
        "WhatsApp", "Instagram", "Facebook", "Twitter", "Email", "Gmail",
        "Idealista", "Fotocasa", "Habitaclia", "Pisos", "Spotahome",
        "Anthropic", "Claude", "OpenAI", "Google", "Microsoft",
        # Money / property words
        "Euros", "Euro", "EUR", "Habitación", "Habitaciones", "Baño", "Baños",
        "Dormitorio", "Cocina", "Salón", "Terraza", "Garaje", "Trastero",
        "Ascensor", "Calefacción", "Aire", "Acondicionado", "Piscina",
    )
)


def _redact_phones(text: str) -> str:
    """Use phonenumbers for canonical detection, regex fallback for loose
    Spanish formats it misses."""
    out: list[tuple[int, int]] = []
    for match in phonenumbers.PhoneNumberMatcher(text, "ES"):
        out.append((match.start, match.end))
    for m in _LOOSE_PHONE_RE.finditer(text):
        out.append((m.start(), m.end()))
    if not out:
        return text
    out.sort()
    merged: list[tuple[int, int]] = []
    for start, end in out:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    pieces: list[str] = []
    cursor = 0
    for start, end in merged:
        pieces.append(text[cursor:start])
        pieces.append("[PHONE]")
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def _redact_names(text: str, *, allow: Iterable[str]) -> str:
    """Redact capitalized tokens that look like names. Preserves anything in
    `allow` (e.g. the current lead's first name) and the geographic stoplist.

    Heuristic: only match patterns of two consecutive capitalized words
    ("María García") or a single capitalized word that follows a name marker
    like "Soy", "Llamo", "Hablo con". Single-word capitalization triggers too
    many false positives (sentence starts, proper places).
    """
    allow_lower = {a.strip().lower() for a in allow if a and isinstance(a, str)}

    def _ok(token: str) -> bool:
        return token.lower() not in _NAME_STOPLIST and token.lower() not in allow_lower

    def _replace(m: re.Match[str]) -> str:
        first = m.group(1)
        second = m.group(2)
        if second:  # Two-word capitalized → likely person name
            if _ok(first) and _ok(second):
                return "[NAME]"
            return m.group(0)
        return m.group(0)  # Single-word capitalisation: leave it.

    return _CAP_NAME_RE.sub(_replace, text)


def redact(text: str | None, *, allow_first_name_of: str | None = None) -> str:
    """Return `text` with PII replaced by tagged placeholders.

    `allow_first_name_of` is the FULL NAME of an entity whose first name should
    survive redaction (typically the current lead). Pass `None` to redact all.
    """
    if not text:
        return text or ""
    out = text
    out = _EMAIL_RE.sub("[EMAIL]", out)
    out = _IBAN_RE.sub("[IBAN]", out)
    out = _DNI_RE.sub("[ID]", out)
    out = _URL_RE.sub("[URL]", out)
    out = _ADDRESS_RE.sub("[ADDR]", out)
    out = _redact_phones(out)
    allow: list[str] = []
    if allow_first_name_of:
        parts = allow_first_name_of.strip().split()
        if parts:
            allow.append(parts[0])
    out = _redact_names(out, allow=allow)
    return out


def redact_dict(value, *, allow_first_name_of: str | None = None):
    """Recursively redact strings inside dict/list values. Non-string leaves
    are returned untouched."""
    if isinstance(value, str):
        return redact(value, allow_first_name_of=allow_first_name_of)
    if isinstance(value, dict):
        return {k: redact_dict(v, allow_first_name_of=allow_first_name_of) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_dict(v, allow_first_name_of=allow_first_name_of) for v in value]
    return value
