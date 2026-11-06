"""The Messages API payload we build from a real inbox.

These are the three shapes that produced `invalid_request_error` 400s in
production (Sentry, webhooks): empty content, a thread starting with our own
message, and consecutive same-role turns.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.runner import _to_anthropic_messages
from app.db.models import MessageRole


def _msg(role: MessageRole, content: str) -> SimpleNamespace:
    return SimpleNamespace(role=role, content=content)


def _lead(text: str) -> SimpleNamespace:
    return _msg(MessageRole.LEAD, text)


def _ai(text: str) -> SimpleNamespace:
    return _msg(MessageRole.AGENT_AI, text)


# ---------------------------------------------------------------------------
# Empty content — the API rejects the whole request over one blank message
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank", ["", "   ", "\n", "\t\n  "])
def test_blank_messages_are_dropped(blank):
    out = _to_anthropic_messages([_lead(blank), _lead("Hola, busco piso")])
    assert out == [{"role": "user", "content": "Hola, busco piso"}]


def test_none_content_is_dropped():
    out = _to_anthropic_messages([_msg(MessageRole.LEAD, None), _lead("Hola")])
    assert out == [{"role": "user", "content": "Hola"}]


def test_every_message_blank_yields_nothing():
    # The caller's guard ("last message must be user") then skips the run
    # instead of sending an empty `messages` array.
    assert _to_anthropic_messages([_lead(""), _ai("   ")]) == []


# ---------------------------------------------------------------------------
# First message must be `user` — proactive follow-ups start threads
# ---------------------------------------------------------------------------


def test_leading_assistant_message_is_dropped():
    out = _to_anthropic_messages([_ai("¿Sigues buscando piso?"), _lead("Sí, perdona")])
    assert out == [{"role": "user", "content": "Sí, perdona"}]


def test_several_leading_assistant_messages_are_dropped():
    out = _to_anthropic_messages(
        [_ai("Seguimiento 24h"), _ai("Seguimiento 72h"), _lead("Aquí estoy")]
    )
    assert out == [{"role": "user", "content": "Aquí estoy"}]


def test_a_thread_with_only_our_messages_yields_nothing():
    assert _to_anthropic_messages([_ai("Seguimiento 24h"), _ai("Seguimiento 72h")]) == []


# ---------------------------------------------------------------------------
# Alternating roles
# ---------------------------------------------------------------------------


def test_consecutive_lead_messages_are_merged():
    out = _to_anthropic_messages([_lead("Hola"), _lead("Busco en Valencia")])
    assert out == [{"role": "user", "content": "Hola\nBusco en Valencia"}]


def test_consecutive_assistant_messages_are_merged():
    out = _to_anthropic_messages(
        [_lead("Hola"), _ai("¡Hola!"), _ai("¿Qué zona te interesa?"), _lead("Ruzafa")]
    )
    assert out == [
        {"role": "user", "content": "Hola"},
        {"role": "assistant", "content": "¡Hola!\n¿Qué zona te interesa?"},
        {"role": "user", "content": "Ruzafa"},
    ]


def test_human_agent_counts_as_assistant_and_merges():
    out = _to_anthropic_messages(
        [_lead("Hola"), _ai("Te atiendo"), _msg(MessageRole.AGENT_HUMAN, "Soy Marta")]
    )
    assert out == [
        {"role": "user", "content": "Hola"},
        {"role": "assistant", "content": "Te atiendo\nSoy Marta"},
    ]


def test_blank_between_two_lead_messages_does_not_break_the_merge():
    # The blank must not act as a separator that leaves two user turns in a row.
    out = _to_anthropic_messages([_lead("Hola"), _ai("  "), _lead("¿Hay algo?")])
    assert out == [{"role": "user", "content": "Hola\n¿Hay algo?"}]


def test_roles_always_alternate_and_start_with_user():
    history = [
        _ai("Proactivo"),
        _lead(""),
        _lead("Hola"),
        _lead("¿Hay pisos?"),
        _ai("Sí"),
        _ai("Te paso tres"),
        _lead("Genial"),
    ]
    out = _to_anthropic_messages(history)
    assert out[0]["role"] == "user"
    assert all(m["content"].strip() for m in out)
    roles = [m["role"] for m in out]
    assert all(a != b for a, b in zip(roles, roles[1:], strict=False))
