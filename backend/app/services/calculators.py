"""Mortgage and total-cost calculators (Spain).

Pure functions, no DB. Used both as agent tools and from the dashboard.
Numbers below are reasonable defaults; the agency can override per-tenant
in its `settings` JSON later.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MortgageQuote:
    monthly_payment_eur: float
    total_paid_eur: float
    total_interest_eur: float
    loan_amount_eur: float
    years: int
    annual_rate_pct: float


def mortgage_quote(
    *,
    price_eur: int,
    down_payment_pct: float = 20.0,
    annual_rate_pct: float = 3.5,
    years: int = 25,
) -> MortgageQuote:
    """Standard French amortization (cuota constante) — typical Spain."""
    if price_eur <= 0:
        raise ValueError("price must be positive")
    if not 0 < down_payment_pct < 100:
        raise ValueError("down_payment_pct must be 0-100")

    loan = price_eur * (1 - down_payment_pct / 100.0)
    n = years * 12
    monthly_rate = (annual_rate_pct / 100.0) / 12.0

    if monthly_rate == 0:
        monthly = loan / n
    else:
        monthly = loan * (monthly_rate * (1 + monthly_rate) ** n) / ((1 + monthly_rate) ** n - 1)

    total = monthly * n
    return MortgageQuote(
        monthly_payment_eur=round(monthly, 2),
        total_paid_eur=round(total, 2),
        total_interest_eur=round(total - loan, 2),
        loan_amount_eur=round(loan, 2),
        years=years,
        annual_rate_pct=annual_rate_pct,
    )


# ---------------------------------------------------------------------------
# Total purchase cost — Spain.
# ITP (Impuesto Transmisiones Patrimoniales) for second-hand homes varies
# by autonomous community. New-build pays IVA 10% + AJD ~1-1.5%. Notary,
# registry and gestoría are roughly fixed-curve.
# ---------------------------------------------------------------------------

# Approximate ITP rate per CCAA for second-hand residential. These are
# defaults — the real number depends on price brackets and personal factors
# (under 35, large family, etc.). Always disclose this is informativa.
ITP_RATES_SECOND_HAND: dict[str, float] = {
    "andalucia": 7.0,
    "aragon": 8.0,
    "asturias": 8.0,
    "baleares": 8.0,
    "canarias": 6.5,
    "cantabria": 9.0,
    "castilla-la-mancha": 9.0,
    "castilla-y-leon": 8.0,
    "cataluna": 10.0,
    "ceuta": 6.0,
    "comunidad-valenciana": 10.0,
    "extremadura": 8.0,
    "galicia": 9.0,
    "la-rioja": 7.0,
    "madrid": 6.0,
    "melilla": 6.0,
    "murcia": 8.0,
    "navarra": 6.0,
    "pais-vasco": 4.0,
}

NEW_BUILD_IVA_PCT = 10.0
NEW_BUILD_AJD_PCT = 1.2  # average


@dataclass
class CostBreakdown:
    operation_type: str
    region: str
    price_eur: int
    tax_eur: float
    notary_eur: float
    registry_eur: float
    gestoria_eur: float
    appraisal_eur: float
    total_extras_eur: float
    total_eur: float
    notes: list[str]


def _notary_fee(price: int) -> float:
    """Rough public-deed fees by price bracket (€)."""
    base = 600
    if price > 600_000:
        base = 1200
    elif price > 300_000:
        base = 900
    elif price > 150_000:
        base = 750
    return float(base)


def _registry_fee(price: int) -> float:
    base = 400
    if price > 600_000:
        base = 800
    elif price > 300_000:
        base = 600
    elif price > 150_000:
        base = 500
    return float(base)


def total_purchase_cost(
    *,
    price_eur: int,
    region: str = "madrid",
    new_build: bool = False,
    needs_mortgage: bool = True,
) -> CostBreakdown:
    region_key = region.lower().replace(" ", "-").replace("ñ", "n")
    notes: list[str] = []

    if new_build:
        tax = price_eur * (NEW_BUILD_IVA_PCT + NEW_BUILD_AJD_PCT) / 100.0
        op_type = "new_build"
    else:
        rate = ITP_RATES_SECOND_HAND.get(region_key)
        if rate is None:
            rate = 7.0
            notes.append(f"Región '{region}' no encontrada — usando ITP 7% por defecto.")
        tax = price_eur * rate / 100.0
        op_type = "second_hand"

    notary = _notary_fee(price_eur)
    registry = _registry_fee(price_eur)
    gestoria = 350.0
    appraisal = 350.0 if needs_mortgage else 0.0
    if needs_mortgage:
        notes.append("Incluye tasación (~350 €) por hipoteca; gastos hipotecarios suelen ir a cargo del banco desde 2019.")
    notes.append("Estimaciones informativas — los importes finales dependen del notario, registro y banco concretos.")

    extras = notary + registry + gestoria + appraisal
    total = price_eur + tax + extras

    return CostBreakdown(
        operation_type=op_type,
        region=region_key,
        price_eur=price_eur,
        tax_eur=round(tax, 2),
        notary_eur=notary,
        registry_eur=registry,
        gestoria_eur=gestoria,
        appraisal_eur=appraisal,
        total_extras_eur=round(extras, 2),
        total_eur=round(total, 2),
        notes=notes,
    )
