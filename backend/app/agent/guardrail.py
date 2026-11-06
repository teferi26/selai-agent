"""Last line of defence before the agent's reply leaves the building.

The agent prompt is robust but Claude can still — under prompt injection,
unusual phrasing, or pure bad luck — say something we never want a lead to
read: another lead's PII, an internal commission, a confident lie about a
property that doesn't exist, a confidential capture method when the
interlocutor is a snooping competitor.

This module runs after `final_text` is produced and before it's sent.
Three layers:

1. **The agency's own forbidden phrases** (zero latency): the panel promises
   "el asistente nunca dirá estas frases", and until now that promise was a
   bullet list in the system prompt and nothing else — a model that ignored it
   (or was talked into ignoring it) sent the phrase straight to the lead. Now
   it is checked against the text that is actually about to leave.
2. **Deterministic rules** (zero latency): regex blacklists keyed off the
   conversation context (e.g. `lead.extra.suspected_competitor`). When a rule
   trips we replace the text with a canned response — Claude never gets a
   second chance.
3. **LLM judge** (Haiku, ~400-700ms, feature-flagged): single JSON-only call
   that classifies the message as `pass | rewrite | escalate`. Fail-open with
   a 1.5s timeout so we don't make the agent slower than industry SLA.

The verdict is logged to `agent_runs.guardrail_action` so we can audit how
often the guardrail fires and tune.
"""
from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from anthropic import AsyncAnthropic

from app.core.logging import get_logger
from app.core.settings import settings

log = get_logger(__name__)

GuardrailAction = Literal["pass", "rewrite", "escalate", "block"]

# Trip-wires for confidential / commercially sensitive data leaks.
# These run REGARDLESS of who's on the other side; the goal is to never
# disclose internal pricing of our services to anyone via the agent.
_LEAK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bcomisi[oó]n(?:es)?\s+(?:de|del?|por)\s+(?:la\s+)?(?:agencia|inmobiliaria|venta)", re.I),
    re.compile(r"\bm[eé]todos?\s+de\s+captaci[oó]n", re.I),
    re.compile(r"\bcartera\s+(?:de\s+)?(?:propiedades|pisos|inmuebles)\s+(?:total|completa)", re.I),
    re.compile(r"\bporcentaje\s+de\s+conversi[oó]n", re.I),
)

# Stronger patterns that fire ONLY when the lead is flagged as a suspected
# competitor by `competitor_detector`. These otherwise are fine to discuss.
_COMPETITOR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b\d{1,2}[.,]?\d{0,2}\s*%\s*(?:de\s+)?(?:comisi[oó]n|honorarios|venta)", re.I),
    re.compile(r"\bnuestros?\s+(?:agentes?|comerciales?|equipo)\s+(?:son|tienen|cobran)", re.I),
    re.compile(r"\b(?:CRM|software|sistema)\s+(?:que\s+)?(?:usamos|tenemos|empleamos)", re.I),
    re.compile(r"\bcu[aá]nto\s+(?:nos\s+)?(?:cuesta|pagamos)[\s\w]{0,20}\blead\b", re.I),
)

CANNED_REDIRECT = (
    "Si buscas piso, dime presupuesto y zona y miramos juntos qué tenemos. "
    "Para preguntas sobre el funcionamiento de la inmobiliaria, mejor lo hablas con un compañero — te paso con uno."
)

# ---------------------------------------------------------------------------
# Agency forbidden phrases (53) — the promise the panel makes, enforced.
#
# `agency.settings["forbidden_phrases"]` used to be injected into the system
# prompt and nothing more, so "nunca lo dirá" meant "casi nunca lo dirá". The
# check below is the enforcement, and it deliberately does NOT try to fix the
# message: there is no second generation at this point in the turn, and
# silently deleting the phrase would leave a mutilated answer whose meaning we
# can't vouch for. So we swap in a safe holding reply and hand the thread to a
# human — the one outcome that can never break the promise.
#
# Matching is deliberately blunt in both directions: accents, case, punctuation
# and plurals are all normalised away (so the owner's "Regalamos la comisión."
# catches "regalamos las comisiones"), but a phrase only matches whole words
# (so "IVA" does not fire on "atractiva" and leave the lead waiting for a human
# on every single message).
# ---------------------------------------------------------------------------

FORBIDDEN_PHRASE_REASON_PREFIX = "forbidden_phrase:"

# What the lead gets instead of the blocked message. Deliberately vague: the
# lead must not learn which phrase the agency banned.
FORBIDDEN_PHRASE_FALLBACK = (
    "Prefiero que esto te lo confirme un compañero del equipo. Le aviso ahora y "
    "te contesta enseguida."
)

# ...and the replacement is not above the rule either. An agency that banned
# "compañero" (some brands hate the word) would otherwise get it back in the
# very message that enforces its own list. Shorter and shorter until one is
# clean; if none is, we say nothing at all and let the human take it — silence
# plus a real person beats breaking the promise we just enforced.
SAFE_HANDOFF_TEXTS: tuple[str, ...] = (
    FORBIDDEN_PHRASE_FALLBACK,
    "Dame un momento y te escribe una persona del equipo.",
    "Ahora te responde una persona.",
)

# Below this length (letters/digits only) a "phrase" is noise: "no", "ok" or
# "3%" would either block every message or never mean what the owner meant.
# The panel lets the owner type anything, so the floor lives here.
MIN_FORBIDDEN_PHRASE_CHARS = 3

# Mirrors the API cap (agency_settings.MAX_FORBIDDEN_PHRASES): a blob written
# before that cap existed must not turn every turn into 5.000 substring scans.
MAX_FORBIDDEN_PHRASES_CHECKED = 50

# How much of the previous outgoing message we glue in front of the current one
# to catch a phrase split across two turns. Two lines of WhatsApp is plenty:
# a longer tail only adds ways for two innocent messages to form a match by
# accident.
SPLIT_LOOKBACK_CHARS = 160

# A needle at least this long may also match as a prefix of a longer word, so
# banning "comision" also catches "comisionista". Shorter than this it starts
# eating unrelated words ("grati" would swallow "gratitud"), so it doesn't.
MIN_PREFIX_MATCH_CHARS = 6


def normalize_for_match(text: str) -> str:
    """Comparable view of `text`: no accents, no case, no punctuation.

    Owners type "Regalamos la comisión." in the panel — with a full stop, or
    inside «comillas» — and the model writes "*regalamos la comision*" with
    WhatsApp bold. Comparing raw strings lets every one of those through, so
    anything that is not a letter or a digit becomes a separator here and the
    two sides meet in the middle.

    Accents are removed by decomposing and dropping the combining marks, which
    also merges ñ→n — stricter than needed, never laxer, and that is the right
    side to err on for a promise like this one. Zero-width and other format
    characters are deleted rather than turned into separators: they are
    invisible, so they must not split a word that the reader sees as one.
    """
    decomposed = unicodedata.normalize("NFD", text)
    kept = [
        ch
        for ch in decomposed
        if not unicodedata.combining(ch) and unicodedata.category(ch) != "Cf"
    ]
    folded = "".join(kept).casefold()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in folded).split())


def _singular(token: str) -> str:
    """Crude Spanish de-pluraliser, applied to both sides of the comparison.

    "comisiones"→"comision", "pisos"→"piso". It is not a real stemmer and does
    not need to be: it runs on the owner's phrase and on the model's text
    alike, so a wrong-but-consistent stem still compares equal. Word-final -s
    is the only thing it touches, which is why banning "comisión" now also
    catches "comisiones" *in the middle* of a phrase — plain substring matching
    only ever caught it at the end.
    """
    if len(token) >= 5 and token.endswith("es"):
        return token[:-2]
    if len(token) >= 4 and token.endswith("s"):
        return token[:-1]
    return token


def _token_matches(hay: str, needle: str) -> bool:
    if hay == needle:
        return True
    if _singular(hay) == _singular(needle):
        return True
    return len(needle) >= MIN_PREFIX_MATCH_CHARS and hay.startswith(needle)


def _contains_phrase(hay_tokens: list[str], needle_tokens: list[str]) -> bool:
    """Is `needle_tokens` a run of whole words inside `hay_tokens`?

    Whole words, not substrings: an agency that bans "IVA" must not have every
    message with "atractiva" in it blocked and handed to a human. Plurals are
    handled by `_singular`, not by matching inside words.
    """
    span = len(needle_tokens)
    for start in range(len(hay_tokens) - span + 1):
        if all(
            _token_matches(hay_tokens[start + offset], needle_tokens[offset])
            for offset in range(span)
        ):
            return True
    return False


def find_forbidden_phrase(text: str, phrases: Sequence[str] | None) -> str | None:
    """The first agency-forbidden phrase present in `text`, or None. Pure."""
    if not phrases or not text:
        return None
    haystack = normalize_for_match(text)
    if not haystack:
        return None
    hay_tokens = haystack.split()
    hay_glued = haystack.replace(" ", "")
    for raw in list(phrases)[:MAX_FORBIDDEN_PHRASES_CHECKED]:
        if not isinstance(raw, str):
            continue
        needle = normalize_for_match(raw)
        needle_tokens = needle.split()
        if len(needle.replace(" ", "")) < MIN_FORBIDDEN_PHRASE_CHARS:
            continue
        if _contains_phrase(hay_tokens, needle_tokens):
            return raw.strip()
        # Multi-word phrases also match with every separator removed, so
        # "sin comisión" still catches "sin-comisión" or "sin 💰 comisión".
        # Single words are left out of this pass on purpose: it is exactly
        # the substring matching that produced the "IVA"/"atractiva" problem.
        if len(needle_tokens) > 1 and needle.replace(" ", "") in hay_glued:
            return raw.strip()
    return None


def find_split_forbidden_phrase(
    previous_text: str | None, text: str, phrases: Sequence[str] | None
) -> str | None:
    """A banned phrase the agent is completing across two messages.

    "Te confirmo que te regalamos la" + "comisión entera si firmas" reads to
    the lead as one sentence and to a per-message check as two clean ones. We
    glue the tail of the previous outgoing message in front of this one and
    look again.

    Only hits that *span* the seam are reported: a phrase already fully inside
    the previous message was that message's problem, and blocking every reply
    after it would leave the lead talking to nobody.
    """
    tail = (previous_text or "")[-SPLIT_LOOKBACK_CHARS:]
    if not tail.strip() or not text.strip() or not phrases:
        return None
    hit = find_forbidden_phrase(f"{tail} {text}", phrases)
    if hit is None or find_forbidden_phrase(tail, phrases) is not None:
        return None
    return hit

_JUDGE_PROMPT = """\
Eres un revisor de seguridad de mensajes. Analizas el mensaje que el asistente \
de IA de una inmobiliaria está a punto de enviar a un cliente potencial. \
Tu trabajo es detectar SOLO problemas graves antes de que salga.

Problemas graves:
- Datos personales de OTROS clientes (nombres, teléfonos, emails, direcciones de terceros).
- Información comercial confidencial (comisiones internas, márgenes, métodos de captación, métricas).
- Promesas legales o financieras que no podemos cumplir (rebajas garantizadas, plazos legales sin contexto).
- Inventos sobre propiedades concretas (precios, características o disponibilidad sin tool call).

NO son problemas:
- Tono informal o demasiado breve.
- Faltas ortográficas menores.
- No haber respondido todo lo que el cliente preguntó.
- Pedir más datos al cliente.

Devuelve SOLO un objeto JSON con la forma:
{"action": "pass", "reason": "..."}
o
{"action": "rewrite", "reason": "...", "rewritten": "..."}
o
{"action": "escalate", "reason": "..."}

Donde:
- pass: el mensaje puede enviarse tal cual.
- rewrite: hay un problema corregible — devuelve el mensaje saneado en `rewritten`.
- escalate: el caso requiere humano, no enviamos el mensaje.

Mensaje a revisar:
\"\"\"
{message}
\"\"\"
"""


@dataclass
class GuardrailVerdict:
    """Result of running the guardrail on an outgoing message."""

    text: str  # The text that should actually be sent (rewritten if applicable)
    action: GuardrailAction
    reason: str

    @property
    def should_send(self) -> bool:
        """`escalate` blocks the message; everything else delivers `text`."""
        return self.action != "escalate"


@dataclass
class GuardrailContext:
    """Just enough of the agent context for the guardrail to apply rules."""

    suspected_competitor: bool = False
    forbidden_phrases: tuple[str, ...] = field(default_factory=tuple)
    # Last thing the agent said in this thread, for the split-phrase check.
    preceding_text: str | None = None


_judge_client: AsyncAnthropic | None = None


def _client() -> AsyncAnthropic:
    global _judge_client
    if _judge_client is None:
        _judge_client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
    return _judge_client


def safe_handoff_text(
    phrases: Sequence[str] | None, *, preferred: str | None = None
) -> str:
    """A holding message that does not itself trip the agency's own list.

    Pure. Returns "" when every candidate trips it — callers must treat that
    as "send nothing", not as a bug.
    """
    for candidate in ((preferred,) if preferred else ()) + SAFE_HANDOFF_TEXTS:
        if candidate and find_forbidden_phrase(candidate, phrases) is None:
            return candidate
    return ""


def _forbidden_phrase_verdict(phrase: str, ctx: GuardrailContext) -> GuardrailVerdict:
    """Block the message, hold the lead, hand the thread to a human.

    `text` carries the safe reply rather than the offending message even
    though `action == "escalate"` already means "do not send": a caller that
    only reads `verdict.text` (the runner does, on every other action) must
    not be able to leak the phrase by mistake.
    """
    log.warning("guardrail.forbidden_phrase", phrase=phrase[:120])
    return GuardrailVerdict(
        text=safe_handoff_text(ctx.forbidden_phrases),
        action="escalate",
        reason=f"{FORBIDDEN_PHRASE_REASON_PREFIX}{phrase}",
    )


def _enforce_forbidden_phrases(
    verdict: GuardrailVerdict, ctx: GuardrailContext
) -> GuardrailVerdict:
    """Re-check whatever we ended up deciding to send.

    The text that leaves is not always the text that came in: a leak pattern
    swaps in `CANNED_REDIRECT` and the LLM judge returns its own rewrite.
    Neither knows what this agency banned, so both get the same check as the
    original message.
    """
    if verdict.action == "escalate":
        return verdict
    hit = find_forbidden_phrase(verdict.text, ctx.forbidden_phrases) or (
        find_split_forbidden_phrase(ctx.preceding_text, verdict.text, ctx.forbidden_phrases)
    )
    return _forbidden_phrase_verdict(hit, ctx) if hit else verdict


def _deterministic_check(text: str, ctx: GuardrailContext) -> GuardrailVerdict | None:
    """Run blacklist regexes. Returns a verdict if anything trips, else None."""
    # The agency's own list first: it is the promise the panel makes by name,
    # and catching it here also spares the Haiku call on a message we already
    # know we are not sending.
    forbidden = find_forbidden_phrase(text, ctx.forbidden_phrases)
    if forbidden is None:
        forbidden = find_split_forbidden_phrase(
            ctx.preceding_text, text, ctx.forbidden_phrases
        )
    if forbidden:
        return _forbidden_phrase_verdict(forbidden, ctx)
    for pattern in _LEAK_PATTERNS:
        if pattern.search(text):
            log.warning("guardrail.leak_pattern", pattern=pattern.pattern)
            return GuardrailVerdict(
                text=CANNED_REDIRECT,
                action="rewrite",
                reason=f"leak_pattern:{pattern.pattern}",
            )
    if ctx.suspected_competitor:
        for pattern in _COMPETITOR_PATTERNS:
            if pattern.search(text):
                log.warning("guardrail.competitor_pattern", pattern=pattern.pattern)
                return GuardrailVerdict(
                    text=CANNED_REDIRECT,
                    action="rewrite",
                    reason=f"competitor_pattern:{pattern.pattern}",
                )
    return None


async def _llm_judge(text: str, *, timeout_s: float = 1.5) -> GuardrailVerdict:
    """Ask Haiku to classify the message. Fail-open on timeout/error."""
    if not settings.anthropic_api_key.get_secret_value():
        return GuardrailVerdict(text=text, action="pass", reason="no_api_key")

    prompt = _JUDGE_PROMPT.replace("{message}", text)
    try:
        response = await asyncio.wait_for(
            _client().messages.create(
                model=settings.agent_model_fast,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=timeout_s,
        )
    except TimeoutError:
        log.warning("guardrail.timeout", timeout_s=timeout_s)
        return GuardrailVerdict(text=text, action="pass", reason="judge_timeout")
    except Exception as exc:
        log.warning("guardrail.judge_error", error=str(exc))
        return GuardrailVerdict(text=text, action="pass", reason="judge_error")

    raw = "".join(b.text for b in response.content if b.type == "text").strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("guardrail.judge_invalid_json", raw=raw[:200])
        return GuardrailVerdict(text=text, action="pass", reason="judge_invalid_json")

    action = data.get("action", "pass")
    reason = (data.get("reason") or "")[:200]

    if action == "rewrite":
        rewritten = data.get("rewritten")
        if rewritten and isinstance(rewritten, str):
            return GuardrailVerdict(text=rewritten.strip(), action="rewrite", reason=reason)
        return GuardrailVerdict(text=text, action="pass", reason="rewrite_missing")
    if action == "escalate":
        return GuardrailVerdict(text=text, action="escalate", reason=reason)
    return GuardrailVerdict(text=text, action="pass", reason=reason)


async def validate_output(
    text: str,
    *,
    suspected_competitor: bool = False,
    forbidden_phrases: Sequence[str] | None = None,
    preceding_text: str | None = None,
    use_llm_judge: bool | None = None,
) -> GuardrailVerdict:
    """Run the full guardrail on the outgoing message.

    `forbidden_phrases` is the agency's own list (`agency.settings`); an
    agency that configured none behaves exactly as before.

    `preceding_text` is the previous message the *AI* sent in this thread, so
    a banned phrase started in one message and finished in the next is still
    caught. Optional: without it the check is per-message, as before.

    `use_llm_judge` defaults to the `GUARDRAIL_LLM_ENABLED` setting; pass an
    explicit bool to force on/off (used in tests and for the "demo" agency).
    """
    if not text or not text.strip():
        return GuardrailVerdict(text=text or "", action="pass", reason="empty")

    ctx = GuardrailContext(
        suspected_competitor=suspected_competitor,
        forbidden_phrases=tuple(
            p for p in (forbidden_phrases or ()) if isinstance(p, str) and p.strip()
        ),
        preceding_text=preceding_text,
    )
    deterministic = _deterministic_check(text, ctx)
    if deterministic is not None:
        return _enforce_forbidden_phrases(deterministic, ctx)

    if use_llm_judge is None:
        use_llm_judge = getattr(settings, "guardrail_llm_enabled", True)
    if not use_llm_judge:
        return GuardrailVerdict(text=text, action="pass", reason="judge_disabled")

    return _enforce_forbidden_phrases(await _llm_judge(text), ctx)
