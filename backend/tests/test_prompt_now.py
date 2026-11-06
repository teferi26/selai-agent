"""(161) current date/time in the prompt + (174) proactive AI disclosure."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.agent.prompts import (
    FIRST_MESSAGE_DISCLOSURE_HEADER,
    NOW_HEADER,
    PROMPT_VERSION,
    RELATIVE_DATES_DIRECTIVE,
    build_now_line,
    render_first_message_block,
    render_now_block,
    render_setter_prompt,
)
from app.agent.runner import _agency_now_line, _is_first_agent_message
from app.db.models import MessageRole

MADRID = ZoneInfo("Europe/Madrid")


def _render(**overrides) -> str:
    return render_setter_prompt(
        agency_name="Inmo Sol",
        channel="whatsapp",
        lead_summary="Nombre: Juan",
        agency_context="Sobre la agencia: pisos en Madrid",
        **overrides,
    )


def test_prompt_version_is_v7():
    """Pinned on purpose: `agent_runs.prompt_version` is how we compare
    conversion across prompt changes, so every edit must bump it.
    v6 = per-channel style (214), language lock (215), objections (219).
    v7 = free-text agency instructions + per-conversation note (53)."""
    assert PROMPT_VERSION == "v7"


# ---------------------------------------------------------------------------
# (161) build_now_line
# ---------------------------------------------------------------------------


def test_now_line_is_spanish_and_locale_independent():
    line = build_now_line(datetime(2026, 7, 24, 18, 5, tzinfo=MADRID), "Europe/Madrid")
    assert line.startswith("Ahora mismo es viernes 24 de julio de 2026, 18:05 (Europe/Madrid).")


def test_now_line_carries_the_iso_reference_with_offset():
    line = build_now_line(datetime(2026, 1, 5, 9, 0, tzinfo=MADRID), "Europe/Madrid")
    assert "lunes 5 de enero de 2026, 09:00" in line
    assert "2026-01-05T09:00+01:00" in line


def test_now_line_uses_the_timezone_it_is_given():
    tz = ZoneInfo("America/New_York")
    line = build_now_line(datetime(2026, 7, 24, 12, 5, tzinfo=tz), "America/New_York")
    assert "(America/New_York)" in line
    assert "2026-07-24T12:05-04:00" in line


# ---------------------------------------------------------------------------
# (161) now block inside the prompt
# ---------------------------------------------------------------------------


def test_now_block_is_empty_without_a_now_line():
    assert render_now_block(None) == ""
    assert render_now_block("   ") == ""


def test_now_block_pairs_the_line_with_the_iso_directive():
    block = render_now_block("Ahora mismo es viernes ...")
    assert block.startswith(NOW_HEADER)
    assert "Ahora mismo es viernes ..." in block
    assert RELATIVE_DATES_DIRECTIVE in block


def test_prompt_without_now_line_never_fakes_a_date():
    prompt = _render()
    assert NOW_HEADER not in prompt
    assert "Ahora mismo es" not in prompt


def test_prompt_with_now_line_tells_the_agent_to_resolve_relative_dates():
    now_line = build_now_line(datetime(2026, 7, 24, 18, 5, tzinfo=MADRID), "Europe/Madrid")
    prompt = _render(now_line=now_line)
    assert NOW_HEADER in prompt
    assert now_line in prompt
    assert "schedule_visit" in prompt
    assert "ISO 8601" in prompt
    # The block sits before the lead data, inside the same system prompt.
    assert prompt.index(NOW_HEADER) < prompt.index("DATOS DEL LEAD")


# ---------------------------------------------------------------------------
# (174) AI disclosure
# ---------------------------------------------------------------------------


def test_the_old_hide_the_ai_rule_is_gone():
    prompt = _render()
    assert "Nunca digas que eres una IA" not in prompt
    assert "Nunca ocultes ni niegues que eres una IA" in prompt


def test_first_message_block_only_renders_on_the_first_message():
    assert render_first_message_block(agency_name="Inmo Sol", is_first_agent_message=False) == ""
    block = render_first_message_block(agency_name="Inmo Sol", is_first_agent_message=True)
    assert block.startswith(FIRST_MESSAGE_DISCLOSURE_HEADER)
    assert "Inmo Sol" in block


def test_prompt_asks_for_self_identification_on_the_first_message():
    prompt = _render(is_first_agent_message=True)
    assert FIRST_MESSAGE_DISCLOSURE_HEADER in prompt
    assert "Soy el asistente virtual de Inmo Sol" in prompt
    assert "art. 50" in prompt


def test_prompt_drops_the_directive_once_the_agent_has_spoken():
    assert FIRST_MESSAGE_DISCLOSURE_HEADER not in _render(is_first_agent_message=False)
    assert FIRST_MESSAGE_DISCLOSURE_HEADER not in _render()


def test_defaults_keep_the_prompt_identical_to_no_arguments():
    assert _render(now_line=None, is_first_agent_message=False) == _render()


# ---------------------------------------------------------------------------
# runner helpers
# ---------------------------------------------------------------------------


def _msg(role: MessageRole) -> SimpleNamespace:
    return SimpleNamespace(role=role)


def test_first_agent_message_on_an_empty_or_lead_only_history():
    assert _is_first_agent_message([]) is True
    assert _is_first_agent_message([_msg(MessageRole.LEAD)]) is True


def test_not_first_agent_message_once_the_ai_replied():
    history = [_msg(MessageRole.LEAD), _msg(MessageRole.AGENT_AI), _msg(MessageRole.LEAD)]
    assert _is_first_agent_message(history) is False


def test_a_human_agent_reply_does_not_count_as_the_ai_introducing_itself():
    history = [_msg(MessageRole.LEAD), _msg(MessageRole.AGENT_HUMAN)]
    assert _is_first_agent_message(history) is True


def test_agency_now_line_uses_the_agency_timezone():
    line = _agency_now_line(SimpleNamespace(timezone="America/New_York"))
    assert line.startswith("Ahora mismo es ")
    assert "(America/New_York)" in line


def test_agency_now_line_falls_back_to_madrid_on_a_broken_timezone():
    for bad in ("", None, "Mars/Olympus"):
        assert "(Europe/Madrid)" in _agency_now_line(SimpleNamespace(timezone=bad))
