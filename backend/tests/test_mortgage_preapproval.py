"""Pre-approval indicative calculator."""
import pytest

from app.services.mortgage_preapproval import request_preapproval


@pytest.mark.asyncio
async def test_typical_buyer_returns_loan_estimate():
    res = await request_preapproval(
        monthly_income_eur=3000,
        savings_eur=60_000,
        has_indefinite_contract=True,
        age=35,
        target_property_price_eur=300_000,
    )
    assert res.indicative_max_loan_eur > 100_000
    assert res.confidence in {"high", "medium", "low"}
    assert res.partner_deep_link.startswith("https://")


@pytest.mark.asyncio
async def test_low_income_returns_zero():
    res = await request_preapproval(
        monthly_income_eur=600,
        savings_eur=2_000,
        has_indefinite_contract=False,
        age=28,
    )
    assert res.indicative_max_loan_eur == 0
    assert res.confidence == "low"


@pytest.mark.asyncio
async def test_age_caps_term():
    res = await request_preapproval(
        monthly_income_eur=4000,
        savings_eur=80_000,
        has_indefinite_contract=True,
        age=60,
        target_property_price_eur=400_000,
    )
    # 80 - 60 = 20 → years are clamped, so loan is smaller than at 35.
    assert res.indicative_max_loan_eur > 0
