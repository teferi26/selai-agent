"""Tests for the output guardrail.

The LLM layer is force-disabled here so tests stay deterministic and fast.
"""
from __future__ import annotations

import pytest

from app.agent.guardrail import CANNED_REDIRECT, validate_output


@pytest.mark.asyncio
async def test_normal_message_passes():
    text = "¡Hola! Te paso una ficha de tres pisos en Malasaña que encajan con tu presupuesto."
    verdict = await validate_output(text, suspected_competitor=False, use_llm_judge=False)
    assert verdict.action == "pass"
    assert verdict.text == text
    assert verdict.should_send is True


@pytest.mark.asyncio
async def test_leak_pattern_blocks_commission_disclosure():
    text = "Nuestra comisión de la agencia es del 3% para los propietarios."
    verdict = await validate_output(text, suspected_competitor=False, use_llm_judge=False)
    assert verdict.action == "rewrite"
    assert verdict.text == CANNED_REDIRECT
    assert verdict.reason.startswith("leak_pattern:")


@pytest.mark.asyncio
async def test_leak_pattern_blocks_capture_methods():
    text = "Te explico nuestros métodos de captación de propietarios."
    verdict = await validate_output(text, suspected_competitor=False, use_llm_judge=False)
    assert verdict.action == "rewrite"
    assert verdict.text == CANNED_REDIRECT


@pytest.mark.asyncio
async def test_competitor_pattern_only_fires_when_flagged():
    # Same text, two contexts: only the competitor one trips the rule.
    text = "Cobramos un 4% de comisión por venta."
    benign = await validate_output(text, suspected_competitor=False, use_llm_judge=False)
    # benign matches a leak pattern too — but the competitor-only patterns
    # must additionally fire when flagged
    competitor_text = "Cuanto pagamos por cada lead a Idealista no te lo puedo decir."
    benign_v = await validate_output(competitor_text, suspected_competitor=False, use_llm_judge=False)
    assert benign_v.action == "pass"
    flagged = await validate_output(competitor_text, suspected_competitor=True, use_llm_judge=False)
    assert flagged.action == "rewrite"
    assert flagged.reason.startswith("competitor_pattern:")
    # And the leak pattern still trips regardless of flag
    assert benign.action == "rewrite"


@pytest.mark.asyncio
async def test_empty_message_passes_silently():
    verdict = await validate_output("", suspected_competitor=False, use_llm_judge=False)
    assert verdict.action == "pass"
    assert verdict.text == ""


@pytest.mark.asyncio
async def test_judge_disabled_returns_pass_for_clean_text():
    text = "Encantado, te paso ficha."
    verdict = await validate_output(text, suspected_competitor=False, use_llm_judge=False)
    assert verdict.action == "pass"
    assert verdict.reason == "judge_disabled"


@pytest.mark.asyncio
async def test_competitor_brand_question_blocked():
    # A competitor asking about our internal CRM
    text = "El CRM que usamos es Hubspot, va perfecto."
    verdict = await validate_output(text, suspected_competitor=True, use_llm_judge=False)
    assert verdict.action == "rewrite"
    assert verdict.text == CANNED_REDIRECT
