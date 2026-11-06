"""(53) The agency's forbidden phrases are enforced on the way OUT.

The panel says, in so many words, "el asistente nunca dirá estas frases,
aunque el cliente insista". Until now the list was injected into the system
prompt and never checked again: a model that ignored the bullet (or was
talked into ignoring it) sent the phrase straight to the lead and nobody
found out. These tests pin the enforcement, not the prompt.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.agent import runner as runner_mod
from app.agent.guardrail import (
    CANNED_REDIRECT,
    FORBIDDEN_PHRASE_FALLBACK,
    FORBIDDEN_PHRASE_REASON_PREFIX,
    GuardrailVerdict,
    find_forbidden_phrase,
    normalize_for_match,
    validate_output,
)
from app.agent.runner import guardrail_handoff_detail
from app.agent.tools import HANDOFF_REASON_KEY
from app.db.models import (
    Agency,
    ConversationChannel,
    ConversationStatus,
    Lead,
    LeadStatus,
    LeadTemperature,
    MessageRole,
)

BANNED = "regalamos la comisión"


async def _check(text: str, phrases: list[str] | None = None) -> GuardrailVerdict:
    """The guardrail as the runner calls it, with the LLM judge off."""
    return await validate_output(
        text,
        suspected_competitor=False,
        forbidden_phrases=phrases if phrases is not None else [BANNED],
        use_llm_judge=False,
    )


# ---------------------------------------------------------------------------
# The phrase never reaches the lead, and a human takes over
# ---------------------------------------------------------------------------


async def test_a_forbidden_phrase_is_not_sent_to_the_lead():
    verdict = await _check("Tranquilo, regalamos la comisión y firmamos mañana.")
    assert verdict.action == "escalate"
    assert verdict.should_send is False
    # Even a caller that ignored `action` and sent `text` cannot leak it.
    assert "comisión" not in verdict.text
    assert verdict.text == FORBIDDEN_PHRASE_FALLBACK


async def test_the_reason_names_the_phrase_so_the_owner_can_see_why():
    verdict = await _check(f"Te lo digo claro: {BANNED}.")
    assert verdict.reason == f"{FORBIDDEN_PHRASE_REASON_PREFIX}{BANNED}"


async def test_a_clean_message_still_goes_out_untouched():
    text = "Te paso tres pisos en Malasaña que encajan con tu presupuesto."
    verdict = await _check(text)
    assert verdict.action == "pass"
    assert verdict.text == text


async def test_an_agency_without_a_list_behaves_exactly_as_before():
    text = f"Tranquilo, {BANNED}."
    assert (await _check(text, phrases=[])).action == "pass"
    # `forbidden_phrases` omitted entirely — the call every other caller makes.
    assert (await validate_output(text, use_llm_judge=False)).action == "pass"


# ---------------------------------------------------------------------------
# Variants: the owner types one way, the model writes another
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "written",
    [
        "REGALAMOS LA COMISIÓN",
        "Regalamos La Comisión",
        "regalamos la comision",          # sin tildes
        "REGALAMOS LA COMISION",
        "regalamos  la   comisión",       # espacios de más
        "regalamos\nla comisión",         # partida en dos líneas
    ],
)
async def test_case_accent_and_spacing_variants_are_all_blocked(written):
    verdict = await _check(f"Mira, {written} y listo.")
    assert verdict.action == "escalate", written


async def test_the_owner_can_type_it_with_or_without_accents():
    """The list itself is normalised too, not only the model's output."""
    verdict = await _check("Te digo que regalamos la comisión.", phrases=["REGALAMOS LA COMISION"])
    assert verdict.action == "escalate"


async def test_it_matches_inside_a_longer_sentence():
    """Substring, not word list: "sin comisión" must catch "sin comisiones"."""
    verdict = await _check("Va totalmente sin comisiones para ti.", phrases=["sin comisión"])
    assert verdict.action == "escalate"


def test_normalisation_is_the_documented_one():
    assert normalize_for_match("  Regalamos   LA Comisión\n") == "regalamos la comision"


def test_a_two_letter_phrase_is_ignored_instead_of_blocking_everything():
    """"no" inside "nosotros" would block every message the agent writes."""
    assert find_forbidden_phrase("Nosotros nos encargamos de todo.", ["no"]) is None


def test_a_malformed_list_does_not_break_the_turn():
    assert find_forbidden_phrase("Hola", [None, 7, {"a": 1}]) is None  # type: ignore[list-item]


# ---------------------------------------------------------------------------
# The replacement text is checked too
# ---------------------------------------------------------------------------


async def test_the_canned_redirect_is_re_checked_against_the_list():
    """A leak pattern swaps in CANNED_REDIRECT, which knows nothing about
    this agency's list — so it gets the same check."""
    banned_bit = "te paso con uno"
    assert banned_bit in CANNED_REDIRECT
    verdict = await validate_output(
        "Nuestra comisión de la agencia es del 3%.",
        forbidden_phrases=[banned_bit],
        use_llm_judge=False,
    )
    assert verdict.action == "escalate"
    assert verdict.text == FORBIDDEN_PHRASE_FALLBACK


async def test_a_judge_rewrite_is_re_checked_against_the_list(monkeypatch):
    async def _judge(text, **_kwargs):
        return GuardrailVerdict(
            text=f"Perdona, {BANNED} y ya está.", action="rewrite", reason="judge"
        )

    monkeypatch.setattr("app.agent.guardrail._llm_judge", _judge)
    verdict = await validate_output(
        "Un mensaje cualquiera.", forbidden_phrases=[BANNED], use_llm_judge=True
    )
    assert verdict.action == "escalate"
    assert BANNED not in verdict.text


# ---------------------------------------------------------------------------
# What the agency reads when it fires
# ---------------------------------------------------------------------------


def test_the_handoff_detail_tells_the_owner_which_phrase_it_was():
    detail = guardrail_handoff_detail(f"{FORBIDDEN_PHRASE_REASON_PREFIX}{BANNED}")
    assert BANNED in detail
    assert "prohibida" in detail
    assert FORBIDDEN_PHRASE_REASON_PREFIX not in detail  # nada de códigos internos


def test_other_guardrail_reasons_keep_the_generic_wording():
    detail = guardrail_handoff_detail("leak_pattern:algo")
    assert detail.startswith(runner_mod.GUARDRAIL_HANDOFF_REASON)


def test_a_novel_length_phrase_does_not_blow_up_the_push():
    detail = guardrail_handoff_detail(f"{FORBIDDEN_PHRASE_REASON_PREFIX}{'x' * 5000}")
    assert len(detail) < 300


# ---------------------------------------------------------------------------
# End to end: one agent turn with the phrase in the model's answer
# ---------------------------------------------------------------------------


class _RunnerDB:
    def __init__(self, *, conv, lead, agency, history):
        self.conv = conv
        self.lead = lead
        self.agency = agency
        self.history = history
        self.added: list = []

    async def get(self, model, pk):
        from app.db.models import Conversation

        if model is Conversation:
            return self.conv
        if model is Lead:
            return self.lead
        if model is Agency:
            return self.agency
        return None

    async def execute(self, stmt):
        rows = self.history if "FROM messages" in str(stmt) else []

        class _Result:
            def scalars(self):
                return self

            def all(self):
                return rows

        return _Result()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None


def _doubles(*, agency_settings: dict, conv_extra: dict | None = None):
    agency = SimpleNamespace(
        id=uuid.uuid4(), name="Inmo Sol", timezone="Europe/Madrid", settings=agency_settings
    )
    lead = SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=agency.id,
        full_name="Marta López",
        budget_min=None,
        budget_max=None,
        operation=None,
        preferred_cities=None,
        preferred_neighborhoods=None,
        min_bedrooms=None,
        must_haves=None,
        deal_breakers=None,
        qualification_notes="",
        detected_objections=None,
        language="es",
        status=LeadStatus.ENGAGED,
        temperature=LeadTemperature.WARM,
        extra={},
    )
    conv = SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=agency.id,
        lead_id=lead.id,
        channel=ConversationChannel.WHATSAPP,
        status=ConversationStatus.ACTIVE,
        extra=conv_extra if conv_extra is not None else {},
    )
    history = [SimpleNamespace(role=MessageRole.LEAD, content="¿Me hacéis un precio?")]
    return _RunnerDB(conv=conv, lead=lead, agency=agency, history=history), conv


async def _run_turn(monkeypatch, *, model_says: str, agency_settings: dict):
    async def _create(**_kwargs):
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=model_says)],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )

    monkeypatch.setattr(
        runner_mod,
        "_anthropic_client",
        lambda **_kw: SimpleNamespace(messages=SimpleNamespace(create=_create)),
    )
    db, conv = _doubles(agency_settings=agency_settings)
    reply = await runner_mod.run_agent(db=db, conversation_id=conv.id)
    return reply, conv


async def test_the_lead_never_receives_the_banned_sentence(monkeypatch):
    reply, conv = await _run_turn(
        monkeypatch,
        model_says=f"¡Claro! {BANNED.capitalize()} si firmas esta semana.",
        agency_settings={"forbidden_phrases": [BANNED]},
    )
    assert "comisión" not in reply.text
    assert reply.handed_off is True
    assert conv.status is ConversationStatus.HANDED_OFF


async def test_the_agency_is_told_which_phrase_was_blocked(monkeypatch):
    _, conv = await _run_turn(
        monkeypatch,
        model_says=f"Tranquila, {BANNED}.",
        agency_settings={"forbidden_phrases": [BANNED]},
    )
    assert BANNED in conv.extra[HANDOFF_REASON_KEY]


async def test_the_holding_message_respects_the_list_too(monkeypatch):
    """An agency that banned "compañero" must not read it in the very reply
    that enforces its own list."""
    reply, _ = await _run_turn(
        monkeypatch,
        model_says=f"Claro, {BANNED}.",
        agency_settings={"forbidden_phrases": [BANNED, "compañero"]},
    )
    assert "compañero" not in reply.text.lower()
    assert reply.handed_off is True


async def test_when_nothing_can_be_said_we_say_nothing(monkeypatch):
    """Every holding message blocked → silence + a human, never the phrase."""
    banned_everything = [BANNED, "persona", "compañero", "momento"]
    reply, conv = await _run_turn(
        monkeypatch,
        model_says=f"Claro, {BANNED}.",
        agency_settings={"forbidden_phrases": banned_everything},
    )
    assert reply.text == ""
    assert conv.status is ConversationStatus.HANDED_OFF


async def test_the_guardrail_action_is_logged_on_the_run(monkeypatch):
    db, conv = _doubles(agency_settings={"forbidden_phrases": [BANNED]})

    async def _create(**_kwargs):
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=f"Va, {BANNED}.")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )

    monkeypatch.setattr(
        runner_mod,
        "_anthropic_client",
        lambda **_kw: SimpleNamespace(messages=SimpleNamespace(create=_create)),
    )
    await runner_mod.run_agent(db=db, conversation_id=conv.id)
    run = db.added[-1]
    assert run.guardrail_action == "escalate"
    assert BANNED not in (run.output_text or "")
