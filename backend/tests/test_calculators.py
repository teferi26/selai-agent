"""Mortgage and total-cost calculators."""
import pytest

from app.services.calculators import (
    ITP_RATES_SECOND_HAND,
    mortgage_quote,
    total_purchase_cost,
)


def test_mortgage_quote_basic():
    q = mortgage_quote(price_eur=300_000, down_payment_pct=20, annual_rate_pct=3.5, years=25)
    assert q.loan_amount_eur == 240_000.0
    # Roughly 1200 €/mes for these inputs.
    assert 1100 < q.monthly_payment_eur < 1300
    assert q.total_paid_eur > q.loan_amount_eur
    assert q.total_interest_eur == pytest.approx(q.total_paid_eur - q.loan_amount_eur, rel=1e-3)


def test_mortgage_quote_zero_rate():
    q = mortgage_quote(price_eur=120_000, down_payment_pct=20, annual_rate_pct=0.0, years=10)
    assert q.monthly_payment_eur == pytest.approx(96_000 / 120, rel=1e-3)


def test_mortgage_quote_validates_inputs():
    with pytest.raises(ValueError):
        mortgage_quote(price_eur=-1)
    with pytest.raises(ValueError):
        mortgage_quote(price_eur=100_000, down_payment_pct=120)


def test_purchase_cost_madrid_second_hand():
    c = total_purchase_cost(price_eur=300_000, region="madrid", new_build=False)
    assert c.region == "madrid"
    assert c.tax_eur == pytest.approx(300_000 * 6.0 / 100)
    assert c.total_eur > 300_000


def test_purchase_cost_new_build_includes_iva_and_ajd():
    c = total_purchase_cost(price_eur=300_000, region="madrid", new_build=True)
    assert c.operation_type == "new_build"
    # IVA 10% + AJD ~1.2% ≈ 33,600
    assert c.tax_eur == pytest.approx(300_000 * 11.2 / 100)


def test_purchase_cost_unknown_region_falls_back():
    c = total_purchase_cost(price_eur=200_000, region="atlantis")
    assert any("Región" in n for n in c.notes)


def test_all_ccaa_have_rates():
    expected = {
        "andalucia", "aragon", "asturias", "baleares", "canarias",
        "cantabria", "castilla-la-mancha", "castilla-y-leon", "cataluna",
        "comunidad-valenciana", "extremadura", "galicia", "la-rioja", "madrid",
        "murcia", "navarra", "pais-vasco",
    }
    assert expected.issubset(ITP_RATES_SECOND_HAND.keys())
