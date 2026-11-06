"""Detect non-resident foreign buyers and flag the lead.

Triggers when:
- Language detected != "es" / "ca" AND
- Lead mentions buying / investing / relocating to Spain.

When flagged we set `lead.extra.foreign_buyer_mode = True` and the runner
appends extra instructions to the system prompt (NIE, non-resident
mortgages, notary by power, etc.).
"""
from __future__ import annotations

import re

NON_LOCAL_LANGS = {"en", "fr", "de", "it", "pt", "nl", "ru"}

_FOREIGN_INTENT_PATTERNS = [
    r"\bnon[- ]?resident",
    r"\brelocat",
    r"\bmove to spain",
    r"\bbuy(?:ing)? (?:a |an )?(?:flat|home|apartment) in",
    r"\bprop[ée]rt[ée] espagne",
    r"\bd[ée]m[ée]nag",
    r"\bspanish (?:mortgage|home)",
    r"\bnie\b",
    r"\bextranjero",
    r"\bsoy de fuera",
    r"\bestoy fuera de espa[ñn]a",
    r"\bauslander",
]
_compiled = [re.compile(p, re.IGNORECASE) for p in _FOREIGN_INTENT_PATTERNS]


def is_foreign_buyer_signal(text: str, language: str | None) -> bool:
    if not text:
        return False
    if language and language in NON_LOCAL_LANGS:
        return True
    return any(p.search(text) for p in _compiled)
