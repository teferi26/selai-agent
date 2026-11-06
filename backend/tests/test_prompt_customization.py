"""Per-agency prompt customization — ai_tone + forbidden_phrases (53)(77)."""
from __future__ import annotations

from app.agent.prompts import (
    FORBIDDEN_PHRASES_HEADER,
    FORMAL_TONE_DIRECTIVE,
    render_setter_prompt,
    render_style_directives,
)


def _render(**overrides) -> str:
    return render_setter_prompt(
        agency_name="Inmo Sol",
        channel="whatsapp",
        lead_summary="Nombre: Juan",
        agency_context="Sobre la agencia: pisos en Madrid",
        **overrides,
    )


def test_default_prompt_has_no_style_overrides():
    prompt = _render()
    assert FORMAL_TONE_DIRECTIVE not in prompt
    assert FORBIDDEN_PHRASES_HEADER not in prompt


def test_formal_tone_injects_usted_directive():
    prompt = _render(ai_tone="formal")
    assert FORMAL_TONE_DIRECTIVE in prompt
    assert "usted" in prompt


def test_cercano_tone_is_the_default_and_injects_nothing():
    assert _render(ai_tone="cercano") == _render()


def test_forbidden_phrases_appear_in_the_prompt():
    phrases = ["te regalamos la reforma", "el precio es negociable seguro"]
    prompt = _render(forbidden_phrases=phrases)
    assert FORBIDDEN_PHRASES_HEADER in prompt
    for phrase in phrases:
        assert f"- {phrase}" in prompt


def test_forbidden_phrases_empty_or_blank_inject_nothing():
    assert FORBIDDEN_PHRASES_HEADER not in _render(forbidden_phrases=[])
    assert FORBIDDEN_PHRASES_HEADER not in _render(forbidden_phrases=["   ", ""])


def test_style_directives_combines_tone_and_phrases():
    text = render_style_directives(ai_tone="formal", forbidden_phrases=["hipoteca gratis"])
    assert FORMAL_TONE_DIRECTIVE in text
    assert "- hipoteca gratis" in text


def test_style_directives_caps_phrases_at_50():
    phrases = [f"frase {i}" for i in range(80)]
    text = render_style_directives(forbidden_phrases=phrases)
    assert "- frase 49" in text
    assert "- frase 50" not in text
