"""Detect competitor agents posing as leads.

Real buyers ask about pisos, timing, financing. Competitor agents ask
about *us*: comisiones, captación, métodos comerciales, lista de
inmuebles, cobertura geográfica. Combined signals + LLM verdict.

When triggered, the agent runner:
  - Marks lead.extra.suspected_competitor = True
  - Switches to a "neutral" prompt mode that politely deflects
  - Notifies the agency owner via dashboard alert
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from anthropic import AsyncAnthropic

from app.core.logging import get_logger
from app.core.settings import settings

log = get_logger(__name__)
_client: AsyncAnthropic | None = None


# Heuristic patterns that often appear in competitor reconnaissance.
RECON_PATTERNS = [
    r"\bcomisi[oó]n",
    r"\bhonorarios? de la inmobiliaria",
    r"\bm[ée]todo (de )?captaci[oó]n",
    r"\bvuestros (mejores )?(agentes|comerciales)",
    r"\bn[uú]mero de (clientes|inmuebles) (en )?cartera",
    r"\bcu[aá]ntos pisos (vend[ée]is|llev[aá]is)",
    r"\bqu[ée] crm us[aá]is",
    r"\bcomo capt[aá]is",
    r"\bcoste por lead",
    r"\btasa de (cierre|conversi[oó]n)",
]
_compiled = [re.compile(p, re.IGNORECASE) for p in RECON_PATTERNS]


@dataclass
class CompetitorVerdict:
    suspected: bool
    reason: str
    confidence: float


def _client_singleton() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
    return _client


def quick_heuristic_score(messages: list[str]) -> int:
    """Returns a count of suspicious patterns matched across all messages."""
    score = 0
    for m in messages:
        for r in _compiled:
            if r.search(m):
                score += 1
    return score


async def classify_competitor(messages: list[str]) -> CompetitorVerdict:
    """Two-stage detection: cheap regex first, LLM second only if signal.

    Cheap when the conversation is normal. Spends 1 LLM call on suspicious
    leads. The LLM call uses Haiku to keep cost low.
    """
    if not messages:
        return CompetitorVerdict(False, "no_messages", 0.0)

    heur = quick_heuristic_score(messages)
    if heur == 0:
        return CompetitorVerdict(False, "no_recon_patterns", 0.0)

    transcript = "\n".join(f"- {m[:300]}" for m in messages[-12:])
    prompt = f"""\
Eres un clasificador. Lee estos mensajes que ha enviado un supuesto cliente a una inmobiliaria \
y dime si es UN COMPRADOR REAL o UN AGENTE DE LA COMPETENCIA tratando de obtener información \
comercial sobre nosotros (comisiones, métodos, cartera, CRM, métricas).

Mensajes del usuario:
{transcript}

Responde EXACTAMENTE en este formato JSON, sin texto alrededor:
{{"competitor": true/false, "reason": "<una frase>", "confidence": 0.0-1.0}}"""

    try:
        client = _client_singleton()
        resp = await client.messages.create(
            model=settings.agent_model_fast,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = "\n".join(b.text for b in resp.content if b.type == "text").strip()
        import json

        raw = re.sub(r"^```(?:json)?\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw)
        data = json.loads(raw)
        return CompetitorVerdict(
            suspected=bool(data.get("competitor", False)),
            reason=str(data.get("reason", "")),
            confidence=float(data.get("confidence", 0.0)),
        )
    except Exception:
        log.exception("competitor.classify_failed")
        # Fall back to heuristic only — high pattern count is enough on its own.
        return CompetitorVerdict(
            suspected=heur >= 2,
            reason=f"heuristic_only matches={heur}",
            confidence=min(0.4 + 0.1 * heur, 0.7),
        )
