"""(214) per-channel style, (215) language lock, (219) objection playbook.

All three are pure prompt rendering, so these tests never touch the network
or the database — they assert the exact text the model will receive.
"""
from __future__ import annotations

import re

from app.agent.prompts import (
    CHANNEL_STYLE_DIRECTIVES,
    CHANNEL_STYLE_HEADER,
    LANGUAGE_HEADER,
    OBJECTIONS_HEADER,
    PROMPT_VERSION,
    render_channel_style,
    render_language_directive,
    render_setter_prompt,
)

_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def _render(**overrides) -> str:
    base = {
        "agency_name": "Inmo Sol",
        "channel": "whatsapp",
        "lead_summary": "Nombre: Juan",
        "agency_context": "Pisos en Madrid",
    }
    return render_setter_prompt(**{**base, **overrides})


# ---------------------------------------------------------------------------
# (214) Channel style
# ---------------------------------------------------------------------------


def test_voice_forbids_emojis_and_demands_numbers_in_words():
    directive = render_channel_style("voice")
    assert CHANNEL_STYLE_HEADER in directive
    assert "emojis" in directive
    assert "palabras" in directive
    # The exact anti-pattern the TTS would read out loud character by character.
    assert "2026-07-28T18:30" in directive


def test_whatsapp_caps_lines_and_emojis():
    directive = render_channel_style("whatsapp")
    assert "Máximo 3 líneas" in directive
    assert "Un emoji por mensaje como mucho" in directive


def test_email_asks_for_greeting_and_sign_off():
    directive = render_channel_style("email")
    assert "saludo" in directive
    assert "despedida" in directive


def test_each_channel_gets_its_own_directive_not_a_shared_one():
    voice = render_channel_style("voice")
    whatsapp = render_channel_style("whatsapp")
    email = render_channel_style("email")
    assert len({voice, whatsapp, email}) == 3


def test_channel_lookup_is_case_and_space_insensitive():
    assert render_channel_style(" VOICE ") == CHANNEL_STYLE_DIRECTIVES["voice"]


def test_unknown_channels_add_nothing_rather_than_inventing_rules():
    for channel in ("sms", "web", "", None, "telegram"):
        assert render_channel_style(channel) == ""


def test_the_prompt_carries_the_directive_of_its_own_channel_only():
    prompt = _render(channel="voice")
    assert CHANNEL_STYLE_DIRECTIVES["voice"] in prompt
    assert CHANNEL_STYLE_DIRECTIVES["whatsapp"] not in prompt
    assert CHANNEL_STYLE_DIRECTIVES["email"] not in prompt


def test_an_unknown_channel_still_renders_a_complete_prompt():
    prompt = _render(channel="sms")
    assert CHANNEL_STYLE_HEADER not in prompt
    assert not _PLACEHOLDER.search(prompt)


# ---------------------------------------------------------------------------
# (215) Language lock
# ---------------------------------------------------------------------------


def test_known_language_codes_render_the_spanish_name():
    assert "inglés" in render_language_directive("en")
    assert "francés" in render_language_directive("fr")
    assert "alemán" in render_language_directive("de")


def test_the_directive_says_always():
    assert "SIEMPRE" in render_language_directive("en")


def test_an_unknown_code_is_named_explicitly_instead_of_dropped():
    directive = render_language_directive("sw")
    assert "sw" in directive
    assert LANGUAGE_HEADER in directive


def test_no_language_means_no_directive():
    for value in (None, "", "   "):
        assert render_language_directive(value) == ""


def test_the_lock_still_lets_the_client_switch_language():
    """`lead.language` defaults to "es" for leads we never confirmed, so a
    hard lock with no escape hatch would trap an English speaker in Spanish."""
    directive = render_language_directive("es")
    assert "cámbiate a ese idioma" in directive


def test_the_language_directive_reaches_the_prompt():
    prompt = _render(language="en")
    assert LANGUAGE_HEADER in prompt
    assert "inglés" in prompt


def test_a_prompt_without_language_has_no_language_section():
    assert LANGUAGE_HEADER not in _render()


# ---------------------------------------------------------------------------
# (219) Objection playbook
# ---------------------------------------------------------------------------


def test_the_four_objections_are_in_the_prompt():
    prompt = _render()
    assert OBJECTIONS_HEADER in prompt
    for objection in (
        "Ya trabajo con otra agencia",
        "Esperaré a que bajen los precios",
        "La comisión es cara",
        "Solo estoy mirando",
    ):
        assert objection in prompt


def test_every_objection_answer_ends_in_a_question():
    """A rebuttal without a counter-question kills the conversation."""
    prompt = _render()
    block = prompt.split(OBJECTIONS_HEADER, 1)[1].split("\n\n", 1)[0]
    bullets = [line for line in block.split("\n") if line.startswith('- "')]
    assert len(bullets) == 4
    for bullet in bullets:
        assert bullet.rstrip().endswith("?"), bullet


def test_the_playbook_tells_the_agent_not_to_push():
    prompt = _render()
    assert "nunca discutas" in prompt
    assert "no insistas dos veces" in prompt


def test_the_playbook_does_not_break_prompt_formatting():
    for channel in ("voice", "whatsapp", "email", "sms"):
        for language in (None, "en", "zz"):
            prompt = _render(channel=channel, language=language, ai_tone="formal")
            assert not _PLACEHOLDER.search(prompt), (channel, language)


def test_prompt_version_bumped_for_this_wave():
    # v7: instrucciones libres de la agencia + nota por conversación.
    assert PROMPT_VERSION == "v7"
