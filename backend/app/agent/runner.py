"""Agent runner — orchestrates a single agent turn.

Given an inbound message, builds the prompt, replays history, lets Claude
decide tool calls, executes them, loops until Claude emits a final text
response, and returns it. Logs everything to `agent_runs`.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from anthropic import APITimeoutError, AsyncAnthropic, BadRequestError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.guardrail import (
    FORBIDDEN_PHRASE_REASON_PREFIX,
    safe_handoff_text,
    validate_output,
)
from app.agent.prompts import (
    CONVERSATION_NOTE_EXTRA_KEY,
    MONTHS_ES,
    PROMPT_VERSION,
    WEEKDAYS_ES,
    build_now_line,
    render_setter_prompt,
)
from app.agent.tools import (
    HANDOFF_AT_KEY,
    HANDOFF_REASON_KEY,
    HANDOFF_URGENCY_KEY,
    MORTGAGE_PREAPPROVAL_EXTRA_KEY,
    TOOL_SCHEMAS,
    AgentContext,
    execute_tool,
)
from app.core.logging import get_logger
from app.core.settings import settings
from app.db.models import (
    Agency,
    AgentRun,
    Conversation,
    ConversationStatus,
    Lead,
    LeadStatus,
    LeadTemperature,
    Message,
    MessageRole,
    Property,
    Visit,
    VisitStatus,
)
from app.services.agency_few_shot import get_cached_few_shot
from app.services.lead_memory import build_cross_channel_memory
from app.services.pii import REDACTION_VERSION, redact, redact_dict

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# (227) Anthropic client — explicit timeout and retries per channel.
#
# The SDK defaults to a 600 s timeout, and this runner executes inline inside
# the WhatsApp webhook and `/voice/llm`. A degraded API would hold the request
# open for ten minutes, make Meta retry the webhook and leave a lead listening
# to silence on the phone. `timeout` is per HTTP request; `max_retries` is the
# SDK's own retry of connection errors and 429/5xx.
# ---------------------------------------------------------------------------

DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
CHANNEL_REQUEST_TIMEOUT_SECONDS: dict[str, float] = {"voice": 10.0}
ANTHROPIC_MAX_RETRIES = 2

# A per-request timeout is not enough on its own: a turn can spend up to
# MAX_TOOL_ITERATIONS requests, so the caller's real worst case is the sum.
# This is the budget for the whole turn, tool calls included.
DEFAULT_TURN_BUDGET_SECONDS = 60.0
CHANNEL_TURN_BUDGET_SECONDS: dict[str, float] = {"voice": 20.0}

DEFAULT_TIMEOUT_FALLBACK = (
    "Perdona, estoy tardando más de lo normal en responderte. ¿Me lo repites en un minuto? "
    "Si te corre prisa, dímelo y aviso a un compañero del equipo."
)
# Voice is read out loud: no emoji, no lists, short sentences (214).
CHANNEL_TIMEOUT_FALLBACK: dict[str, str] = {
    "voice": "Perdona, me está costando responderte. ¿Me lo repites, por favor?",
}

_clients: dict[tuple[float, int], AsyncAnthropic] = {}


def request_timeout_for(channel: str | None) -> float:
    return CHANNEL_REQUEST_TIMEOUT_SECONDS.get(
        (channel or "").strip().lower(), DEFAULT_REQUEST_TIMEOUT_SECONDS
    )


def turn_budget_for(channel: str | None) -> float:
    return CHANNEL_TURN_BUDGET_SECONDS.get(
        (channel or "").strip().lower(), DEFAULT_TURN_BUDGET_SECONDS
    )


def timeout_fallback_for(channel: str | None) -> str:
    return CHANNEL_TIMEOUT_FALLBACK.get(
        (channel or "").strip().lower(), DEFAULT_TIMEOUT_FALLBACK
    )


def _anthropic_client(
    *,
    timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    max_retries: int = ANTHROPIC_MAX_RETRIES,
) -> AsyncAnthropic:
    """One cached client per (timeout, retries) pair.

    Cached because the SDK holds an httpx connection pool: building one per
    turn would throw away every keep-alive connection.
    """
    key = (timeout_seconds, max_retries)
    client = _clients.get(key)
    if client is None:
        client = AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            timeout=timeout_seconds,
            max_retries=max_retries,
        )
        _clients[key] = client
    return client


MAX_TOOL_ITERATIONS = 6
HISTORY_LIMIT = 30

DEFAULT_TIMEZONE = "Europe/Madrid"

# (174) The voice agent already identifies itself in its scripted firstMessage,
# so the disclosure directive only applies to text conversations.
CHANNELS_WITH_SCRIPTED_INTRO = {"voice"}

# (218) Channels that never get the smart model, whatever the lead is worth:
# a voice turn has a ~20 s budget end to end and Opus would blow through it
# while the lead listens to silence.
CHANNELS_WITHOUT_SMART_MODEL = {"voice"}


# A pre-approval is written to `lead.extra` once and never removed. HOT
# expires after 48 h and NEGOTIATING is a status somebody moves on, but
# without a window this flag alone would keep a lead on the expensive model
# for the rest of their life over a mortgage question they asked last spring.
MORTGAGE_PREAPPROVAL_SMART_DAYS = 30


def _preapproval_is_live(blob: Any, now: datetime) -> bool:
    """Was the pre-approval recent enough to still mean "buying right now"?

    An entry we cannot date is treated as live: downgrading a genuine buyer
    because of a malformed blob costs more than a few expensive turns.
    """
    if not blob:
        return False
    if not isinstance(blob, dict):
        return True
    raw = blob.get("requested_at")
    if not raw:
        return True
    try:
        requested_at = datetime.fromisoformat(str(raw))
    except ValueError:
        return True
    if requested_at.tzinfo is None:
        requested_at = requested_at.replace(tzinfo=UTC)
    return now - requested_at <= timedelta(days=MORTGAGE_PREAPPROVAL_SMART_DAYS)


def should_use_smart_model(lead: Lead, channel: str | None, now: datetime | None = None) -> bool:
    """(218) Is this conversation worth the expensive model?

    Pure. CLAUDE.md promises "Opus for setter reasoning" but nothing ever
    passed `use_smart_model=True`, so a 300.000 € negotiation ran on the same
    fast model as "¿estáis abiertos?". These three signals mean money is on
    the table right now — "right now" being the operative words: each of them
    expires.
    """
    if (channel or "").strip().lower() in CHANNELS_WITHOUT_SMART_MODEL:
        return False
    if lead.temperature == LeadTemperature.HOT:
        return True
    if lead.status == LeadStatus.NEGOTIATING:
        return True
    return _preapproval_is_live(
        (lead.extra or {}).get(MORTGAGE_PREAPPROVAL_EXTRA_KEY), now or datetime.now(UTC)
    )


GUARDRAIL_HANDOFF_REASON = (
    "el control de calidad ha bloqueado la respuesta de la IA "
    "(posible dato confidencial o promesa que no podemos cumplir)"
)
# (53) A blocked forbidden phrase is not a vague "quality" problem: the owner
# banned that exact wording, so the alert says which one and stops guessing.
GUARDRAIL_FORBIDDEN_HANDOFF_REASON = (
    "la IA iba a decir una frase que tienes prohibida («{phrase}»); he parado el "
    "mensaje antes de enviarlo y te paso la conversación"
)
MAX_GUARDRAIL_REASON_CHARS = 200
MAX_GUARDRAIL_PHRASE_CHARS = 120

# What the lead reads when the guardrail stops a message. Kept honest: a
# person really is about to take over, because the conversation just changed
# to HANDED_OFF and the agency got a push.
GUARDRAIL_ESCALATION_REPLY = "Voy a pasarte con un compañero del equipo, te atiende enseguida."


def guardrail_handoff_detail(judge_reason: str | None) -> str:
    """The sentence the agency reads in the push, the email and the panel.

    Pure. `escalate_to_human` writes a reason a human wrote; the guardrail
    only has its own verdict, so this turns it into something an owner can
    act on instead of an internal code.
    """
    reason = " ".join(str(judge_reason or "").split())
    if reason.startswith(FORBIDDEN_PHRASE_REASON_PREFIX):
        phrase = reason[len(FORBIDDEN_PHRASE_REASON_PREFIX) :].strip()
        return GUARDRAIL_FORBIDDEN_HANDOFF_REASON.format(
            phrase=phrase[:MAX_GUARDRAIL_PHRASE_CHARS]
        )
    reason = reason[:MAX_GUARDRAIL_REASON_CHARS]
    return f"{GUARDRAIL_HANDOFF_REASON}: {reason}" if reason else GUARDRAIL_HANDOFF_REASON


def _mark_handed_off_by_guardrail(conv: Conversation, judge_reason: str | None) -> None:
    """Close the conversation to the AI when the guardrail escalates.

    The guardrail path used to set `handed_off` on the reply and nothing else:
    the conversation stayed ACTIVE, so the AI kept answering after promising
    "te paso con un compañero", the agency got a new push on every turn, and
    the 24h/72h sweep (which only skips non-ACTIVE threads) would nudge a lead
    a human had already been asked to take over. `escalate_to_human` does
    exactly this; the guardrail has to do it too.
    """
    detail = guardrail_handoff_detail(judge_reason)

    conv.status = ConversationStatus.HANDED_OFF
    # Whole-dict reassignment: SQLAlchemy does not track in-place JSON edits.
    conv.extra = {
        **(conv.extra or {}),
        HANDOFF_REASON_KEY: detail,
        HANDOFF_URGENCY_KEY: "high",
        HANDOFF_AT_KEY: datetime.now(UTC).isoformat(),
    }


def read_conversation_note(conv: Conversation) -> str | None:
    """The one-off instruction the agency left on this thread, if any.

    Stored by `POST /conversations/{id}/note` as
    `{"text": ..., "updated_at": ...}`; a bare string is accepted too so a
    hand-edited blob (or an older shape) still reaches the model instead of
    silently doing nothing.
    """
    blob = (conv.extra or {}).get(CONVERSATION_NOTE_EXTRA_KEY)
    if isinstance(blob, str):
        return blob or None
    if isinstance(blob, dict):
        text = blob.get("text")
        return str(text) if isinstance(text, str) and text.strip() else None
    return None


@dataclass
class AgentReply:
    text: str
    tool_calls: list[dict] = field(default_factory=list)
    handed_off: bool = False
    run_id: uuid.UUID | None = None
    # (n33) The turn failed on our side and the lead got a canned apology.
    # NOT the same as `handed_off`: the conversation stays with the AI (a bad
    # deploy would otherwise flip every live conversation to "needs a human"
    # at once), but the agency is told, because a lead who is asked to repeat
    # themselves and then hears nothing useful is a lead about to leave.
    failed: bool = False


async def run_agent(
    *,
    db: AsyncSession,
    conversation_id: uuid.UUID,
    use_smart_model: bool = False,
) -> AgentReply:
    """Run one turn of the agent for a conversation."""
    started = time.perf_counter()

    conv = await db.get(Conversation, conversation_id)
    if conv is None:
        raise ValueError(f"conversation {conversation_id} not found")

    lead = await db.get(Lead, conv.lead_id)
    agency = await db.get(Agency, conv.agency_id)
    if lead is None or agency is None:
        raise ValueError("lead or agency missing")

    history = await _load_history(
        db, conversation_id, agency_id=agency.id, limit=HISTORY_LIMIT
    )
    messages = _to_anthropic_messages(history)

    if not messages or messages[-1]["role"] != "user":
        log.warning("agent.run.no_user_message", conv=str(conversation_id))
        return AgentReply(text="")

    cross_memory = await build_cross_channel_memory(
        db,
        lead_id=lead.id,
        exclude_conversation_id=conv.id,
        allow_first_name_of=lead.full_name,
    )
    lead_summary = _summarize_lead(lead)

    # (220) A human agent would never propose a visit to someone who already
    # has one booked for tomorrow, nor ignore that they stood us up yesterday.
    visits_block = await _summarize_visits(
        db, agency_id=agency.id, lead_id=lead.id, tz=resolve_timezone(agency.timezone)[0]
    )
    if visits_block:
        lead_summary += "\n\n" + visits_block

    if cross_memory:
        lead_summary += "\n\nINTERACCIONES PREVIAS EN OTROS CANALES:\n" + cross_memory

    extra = lead.extra or {}
    if extra.get("suspected_competitor"):
        lead_summary += (
            "\n\nALERTA: este interlocutor parece un agente de la competencia recopilando "
            "información sobre nosotros. NO compartas comisiones, métodos de captación, "
            "tamaño de cartera, métricas internas ni nombres de personal. Responde brevemente "
            "y reconduce a 'si buscas piso, dime presupuesto y zona y miramos qué tenemos'. "
            "Si insiste, escala a humano."
        )

    if extra.get("foreign_buyer_mode"):
        lead_summary += (
            "\n\nMODO COMPRADOR EXTRANJERO: el lead es no residente en España. "
            "Sé especialmente útil con: cómo obtener NIE, bancos que abren cuenta a no "
            "residentes, hipotecas para no residentes (suelen pedir 30-40% entrada), "
            "procesos notariales con poder, tasas adicionales. No prometas plazos legales "
            "sin contexto."
        )

    agency_context = _render_agency_context(agency)
    few_shot = get_cached_few_shot(agency)
    if few_shot:
        agency_context += "\n\n" + few_shot

    agency_blob = agency.settings or {}
    ai_tone = agency_blob.get("ai_tone")
    forbidden_phrases = agency_blob.get("forbidden_phrases")
    forbidden_phrases = forbidden_phrases if isinstance(forbidden_phrases, list) else None
    ai_instructions = agency_blob.get("ai_instructions")
    conversation_note = read_conversation_note(conv)

    # (174) Identify as an AI the first time the assistant speaks in this
    # conversation (AI Act art. 50). The history window may be truncated, so
    # confirm with a cheap count before assuming it's the first message.
    first_agent_message = _is_first_agent_message(history)
    if first_agent_message and len(history) >= HISTORY_LIMIT:
        first_agent_message = await _count_ai_messages(db, conversation_id, agency.id) == 0
    if conv.channel.value in CHANNELS_WITH_SCRIPTED_INTRO:
        first_agent_message = False

    system_prompt = render_setter_prompt(
        agency_name=agency.name,
        channel=conv.channel.value,
        lead_summary=lead_summary,
        agency_context=agency_context,
        ai_tone=ai_tone if isinstance(ai_tone, str) else None,
        forbidden_phrases=forbidden_phrases,
        ai_instructions=ai_instructions if isinstance(ai_instructions, str) else None,
        conversation_note=conversation_note,
        now_line=_agency_now_line(agency),
        is_first_agent_message=first_agent_message,
        language=lead.language,
    )

    channel = conv.channel.value
    ctx = AgentContext(
        db=db,
        agency_id=agency.id,
        lead_id=lead.id,
        conversation_id=conv.id,
        channel=channel,
    )

    # (218) Callers can still force the smart model; when they don't, the
    # lead's own state decides.
    smart = use_smart_model or should_use_smart_model(lead, channel)
    model = settings.agent_model_smart if smart else settings.agent_model_fast
    client = _anthropic_client(timeout_seconds=request_timeout_for(channel))

    tool_calls_log: list[dict] = []
    final_text = ""
    handed_off = False
    input_tokens = 0
    output_tokens = 0
    cache_read = 0
    cache_write = 0
    error: str | None = None

    try:
        async with asyncio.timeout(turn_budget_for(channel)):
            for _ in range(MAX_TOOL_ITERATIONS):
                response = await client.messages.create(
                    model=model,
                    max_tokens=1024,
                    system=[
                        {
                            "type": "text",
                            "text": system_prompt,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    tools=TOOL_SCHEMAS,
                    messages=messages,
                )

                usage = response.usage
                input_tokens += usage.input_tokens
                output_tokens += usage.output_tokens
                cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0
                cache_write += getattr(usage, "cache_creation_input_tokens", 0) or 0

                tool_uses = [b for b in response.content if b.type == "tool_use"]
                text_blocks = [b for b in response.content if b.type == "text"]

                if not tool_uses:
                    final_text = "\n".join(b.text for b in text_blocks).strip()
                    break

                assistant_blocks: list[dict] = []
                for b in text_blocks:
                    assistant_blocks.append({"type": "text", "text": b.text})
                for tu in tool_uses:
                    assistant_blocks.append(
                        {"type": "tool_use", "id": tu.id, "name": tu.name, "input": tu.input}
                    )
                messages.append({"role": "assistant", "content": assistant_blocks})

                tool_results: list[dict] = []
                for tu in tool_uses:
                    result_text = await execute_tool(tu.name, tu.input, ctx)
                    tool_calls_log.append(
                        {"name": tu.name, "input": tu.input, "result": result_text}
                    )
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": tu.id, "content": result_text}
                    )
                    if tu.name == "escalate_to_human":
                        handed_off = True
                messages.append({"role": "user", "content": tool_results})

                if response.stop_reason == "end_turn" and not tool_uses:
                    final_text = "\n".join(b.text for b in text_blocks).strip()
                    break
            else:
                log.warning("agent.run.max_iterations", conv=str(conversation_id))
                final_text = (
                    final_text
                    or "Disculpa, dame un momento — ya te respondo."
                )

    except (TimeoutError, APITimeoutError):
        # (227) Either a single request or the whole turn ran out of budget.
        # We answer something kind instead of holding the webhook open.
        error = "timeout"
        log.warning(
            "agent.run.timeout",
            conv=str(conversation_id),
            channel=channel,
            budget_s=turn_budget_for(channel),
        )
        final_text = timeout_fallback_for(channel)

    except BadRequestError as exc:
        # A 400 from Anthropic is *our* bug (a malformed request), never the
        # lead's fault — and it must not take the channel down. Logged apart
        # from the generic branch so Sentry groups it as what it is: a
        # payload we built wrong, with the thread id to reproduce it.
        error = f"bad_request: {exc}"
        log.exception(
            "agent.run.bad_request",
            conv=str(conversation_id),
            channel=channel,
            message_count=len(messages),
        )
        final_text = (
            "Disculpa, no he podido procesar ese mensaje. ¿Puedes escribírmelo "
            "de otra forma? Si prefieres, aviso a un agente humano."
        )

    except Exception as exc:
        error = str(exc)
        log.exception("agent.run.failed", conv=str(conversation_id))
        final_text = (
            "Disculpa, he tenido un problema técnico. ¿Puedes repetirme tu mensaje? "
            "Si urge, dímelo y aviso a un agente humano."
        )

    # Output guardrail — only run on a successful turn (skip if we errored
    # since the canned error message is already safe).
    guardrail_action: str | None = None
    if final_text and not error:
        verdict = await validate_output(
            final_text,
            suspected_competitor=bool(extra.get("suspected_competitor")),
            # (53) The list the panel promises the AI "nunca" says. Checked
            # against the real outgoing text, not just injected in the prompt.
            forbidden_phrases=forbidden_phrases,
            # ...and against the seam with the previous message, so the phrase
            # cannot get through by arriving in two halves.
            preceding_text=last_ai_text(history),
        )
        guardrail_action = verdict.action
        if verdict.action == "escalate":
            handed_off = True
            _mark_handed_off_by_guardrail(conv, verdict.reason)
            # The holding message is checked against the agency's own list
            # too: an agency that banned "compañero" must not get it back in
            # the very message that enforces its list. "" means we send
            # nothing at all — the human takes it from here either way.
            final_text = safe_handoff_text(
                forbidden_phrases, preferred=GUARDRAIL_ESCALATION_REPLY
            )
        else:
            final_text = verdict.text

    duration_ms = int((time.perf_counter() - started) * 1000)

    # Redact PII before persisting. The current lead's first name survives so
    # operators can still scan logs by name; everything else (emails, phones,
    # IBANs, addresses) is replaced with placeholders. `search_properties`
    # tool calls keep their result intact — no lead PII there, and we need
    # observability for catalog matching.
    redacted_output = redact(final_text, allow_first_name_of=lead.full_name)
    redacted_tool_calls: list[dict] = []
    for tc in tool_calls_log:
        if tc.get("name") == "search_properties":
            redacted_tool_calls.append(tc)
        else:
            redacted_tool_calls.append(
                {
                    "name": tc.get("name"),
                    "input": redact_dict(tc.get("input"), allow_first_name_of=lead.full_name),
                    "result": redact(tc.get("result", ""), allow_first_name_of=lead.full_name),
                }
            )

    run = AgentRun(
        agency_id=agency.id,
        conversation_id=conv.id,
        lead_id=lead.id,
        model=model,
        prompt_version=PROMPT_VERSION,
        output_text=redacted_output,
        tool_calls=redacted_tool_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        duration_ms=duration_ms,
        error=error,
        output_redacted=True,
        redaction_version=REDACTION_VERSION,
        guardrail_action=guardrail_action,
    )
    db.add(run)
    await db.flush()

    log.info(
        "agent.run.done",
        conv=str(conversation_id),
        duration_ms=duration_ms,
        tools=len(tool_calls_log),
        in_tok=input_tokens,
        out_tok=output_tokens,
    )

    return AgentReply(
        text=final_text,
        tool_calls=tool_calls_log,
        handed_off=handed_off,
        run_id=run.id,
        failed=bool(error),
    )


async def _load_history(
    db: AsyncSession, conv_id: uuid.UUID, *, agency_id: uuid.UUID, limit: int
) -> list[Message]:
    """The last `limit` messages of the thread.

    `agency_id` is redundant against a UUID primary key but the project rule
    is "every query filters by agency_id" precisely so nobody has to reason
    about which ids happen to be globally unique.
    """
    stmt = (
        select(Message)
        .where(Message.agency_id == agency_id, Message.conversation_id == conv_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    rows.reverse()
    return rows


async def _count_ai_messages(
    db: AsyncSession, conv_id: uuid.UUID, agency_id: uuid.UUID
) -> int:
    stmt = (
        select(func.count())
        .select_from(Message)
        .where(
            Message.agency_id == agency_id,
            Message.conversation_id == conv_id,
            Message.role == MessageRole.AGENT_AI,
        )
    )
    return int((await db.execute(stmt)).scalar_one() or 0)


def last_ai_text(history: list[Message]) -> str | None:
    """What the AI said last in this thread, or None.

    Only `AGENT_AI` messages: the guardrail polices what the AI writes, and a
    human agent's manual reply is not something we can (or should) block a
    later AI message for continuing.
    """
    for message in reversed(history):
        if message.role == MessageRole.AGENT_AI:
            content = (message.content or "").strip()
            return content or None
    return None


def _is_first_agent_message(history: list[Message]) -> bool:
    """True when the AI has not spoken yet in this conversation.

    Messages written by a human agent don't count: the AI still has to
    introduce itself the first time *it* answers.
    """
    return not any(m.role == MessageRole.AGENT_AI for m in history)


def resolve_timezone(name: str | None) -> tuple[tzinfo, str]:
    """Best-effort timezone lookup that can never raise.

    A bad `agency.timezone` falls back to Madrid; a machine with no timezone
    database at all (a slim image without `tzdata`) falls back to UTC, because
    losing the current time is far better than failing the whole turn.
    """
    candidates = [(name or "").strip(), DEFAULT_TIMEZONE]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate), candidate
        except Exception:
            log.warning("agent.run.bad_timezone", tz=candidate)
    return UTC, "UTC"


def _agency_now_line(agency: Agency) -> str:
    """(161) "Ahora mismo es ..." in the agency's own timezone."""
    tz, tz_name = resolve_timezone(agency.timezone)
    return build_now_line(datetime.now(tz), tz_name)


def _to_anthropic_messages(history: list[Message]) -> list[dict[str, Any]]:
    """Map stored messages onto the Messages API shape.

    The API enforces three invariants that a real inbox violates every day —
    together they were the source of the `invalid_request_error` 400s Sentry
    kept reporting from the webhooks:

    - **Content cannot be empty.** An image with no caption, an email with an
      empty body, an SMS that arrives blank or a transcription that came back
      empty all reach us as "" and the API rejects the *whole* request, so one
      odd message killed the answer to a perfectly good one.
    - **The first message must be `user`.** A thread can legitimately start
      with our own message (the 24h/72h follow-ups are proactive outbounds),
      and the history window can also cut right after one.
    - **Roles must alternate.** Two follow-ups in a row, or a human agent
      writing twice before the lead answers, produce consecutive assistant
      turns.

    Normalising here rather than at write time keeps the stored thread
    faithful to what actually happened — the panel still shows every message.
    """
    out: list[dict[str, Any]] = []
    for m in history:
        if m.role == MessageRole.LEAD:
            role = "user"
        elif m.role in (MessageRole.AGENT_AI, MessageRole.AGENT_HUMAN):
            role = "assistant"
        else:
            continue

        content = (m.content or "").strip()
        if not content:
            continue
        if not out and role == "assistant":
            # Nothing of ours to answer yet: drop the leading outbound.
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"] = f"{out[-1]['content']}\n{content}"
            continue
        out.append({"role": role, "content": content})
    return out


def _summarize_lead(lead: Lead) -> str:
    bits: list[str] = []
    if lead.full_name:
        bits.append(f"Nombre: {lead.full_name}")
    if lead.budget_min or lead.budget_max:
        bits.append(f"Presupuesto: €{lead.budget_min or '?'} - €{lead.budget_max or '?'}")
    if lead.operation:
        bits.append(f"Operación: {lead.operation}")
    if lead.preferred_cities:
        bits.append(f"Ciudades: {', '.join(lead.preferred_cities)}")
    if lead.preferred_neighborhoods:
        bits.append(f"Barrios: {', '.join(lead.preferred_neighborhoods)}")
    if lead.min_bedrooms:
        bits.append(f"Mín habitaciones: {lead.min_bedrooms}")
    if lead.must_haves:
        bits.append(f"Imprescindibles: {', '.join(lead.must_haves)}")
    if lead.deal_breakers:
        bits.append(f"Descartes: {', '.join(lead.deal_breakers)}")
    if lead.qualification_notes:
        bits.append(f"Notas: {lead.qualification_notes}")
    if lead.detected_objections:
        bits.append(f"Objeciones detectadas: {', '.join(lead.detected_objections)}")
    bits.append(f"Estado actual: {lead.status.value} / temperatura: {lead.temperature.value}")
    return "\n".join(bits)


# ---------------------------------------------------------------------------
# (220) Visit history in the lead summary
# ---------------------------------------------------------------------------

VISITS_HEADER = "VISITAS DE ESTE LEAD:"

MAX_VISITS_IN_SUMMARY = 8

# Statuses that still represent a visit that is going to happen.
UPCOMING_VISIT_STATUSES = (
    VisitStatus.REQUESTED,
    VisitStatus.CONFIRMED,
    VisitStatus.RESCHEDULED,
)

VISIT_STATUS_ES: dict[VisitStatus, str] = {
    VisitStatus.REQUESTED: "propuesta, pendiente de confirmar",
    VisitStatus.CONFIRMED: "confirmada",
    VisitStatus.RESCHEDULED: "reprogramada",
    VisitStatus.CANCELLED: "cancelada",
    VisitStatus.NO_SHOW: "no se presentó",
    VisitStatus.COMPLETED: "realizada",
}

VISITS_DIRECTIVE = (
    "No propongas una visita que ya está agendada ni la agendes dos veces. Si hay un no-show, "
    "retómalo con tacto antes de ofrecer otra fecha; si ya visitó algo, pregunta qué le pareció."
)


def format_visit_datetime_es(when_local: datetime) -> str:
    """"martes 28 de julio a las 17:30" — same spelling as the prompt clock."""
    return (
        f"{WEEKDAYS_ES[when_local.weekday()]} {when_local.day} de "
        f"{MONTHS_ES[when_local.month - 1]} a las {when_local:%H:%M}"
    )


async def _summarize_visits(
    db: AsyncSession,
    *,
    agency_id: uuid.UUID,
    lead_id: uuid.UUID,
    tz: tzinfo,
    now: datetime | None = None,
) -> str:
    """Upcoming and past visits of this lead, or "" when there are none."""
    now = now or datetime.now(UTC)
    stmt = (
        select(Visit)
        .where(Visit.agency_id == agency_id, Visit.lead_id == lead_id)
        .order_by(Visit.scheduled_at.desc())
        .limit(MAX_VISITS_IN_SUMMARY)
    )
    visits = list((await db.execute(stmt)).scalars().all())
    if not visits:
        return ""

    labels = await _property_labels(db, agency_id, {v.property_id for v in visits})

    upcoming: list[str] = []
    past: list[str] = []
    for v in visits:
        when = v.scheduled_at
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        label = labels.get(v.property_id, "propiedad")
        status = VISIT_STATUS_ES.get(v.status, v.status.value)
        line = f"- {format_visit_datetime_es(when.astimezone(tz))} — {label} ({status})"
        if when >= now and v.status in UPCOMING_VISIT_STATUSES:
            upcoming.append(line)
        else:
            past.append(line)

    blocks = [VISITS_HEADER]
    if upcoming:
        blocks.append("Próximas:")
        blocks.extend(reversed(upcoming))
    if past:
        blocks.append("Anteriores:")
        blocks.extend(past)
    blocks.append(VISITS_DIRECTIVE)
    return "\n".join(blocks)


async def _property_labels(
    db: AsyncSession, agency_id: uuid.UUID, property_ids: set[uuid.UUID]
) -> dict[uuid.UUID, str]:
    if not property_ids:
        return {}
    stmt = select(Property).where(
        Property.agency_id == agency_id, Property.id.in_(property_ids)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return {p.id: f"{p.reference or 'sin ref'} {p.title or ''}".strip() for p in rows}


def _render_agency_context(agency: Agency) -> str:
    s = agency.settings or {}
    lines = []
    # Profile keys filled in by the /setup wizard (step 1).
    if business := s.get("business_name"):
        lines.append(f"Nombre comercial: {business}")
    if intro := s.get("intro"):
        lines.append(f"Sobre la agencia: {intro}")
    if schedule := s.get("schedule_text"):
        lines.append(f"Horario de atención: {schedule}")
    elif hours := s.get("office_hours"):
        lines.append(f"Horario: {hours}")
    wizard_zones = s.get("zones")
    if isinstance(wizard_zones, list) and wizard_zones:
        lines.append("Zonas en las que opera: " + ", ".join(str(z) for z in wizard_zones))
    elif zones := s.get("operating_zones"):
        lines.append(f"Zonas en las que opera: {zones}")
    if specialty := s.get("specialty"):
        lines.append(f"Especialidad: {specialty}")
    if website := s.get("website"):
        lines.append(f"Web de la agencia: {website}")
    return "\n".join(lines)
