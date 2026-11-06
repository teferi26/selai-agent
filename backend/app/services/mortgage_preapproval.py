"""Mortgage pre-approval via partner brokers.

Real integration target: Trioteca / Hipoo / iAhorro APIs (none of which
have publicly documented endpoints today). For now this returns a
plausible "indicative" response based on simple Spanish bank criteria
plus a deep link to the partner with the lead's data prefilled. When
the partner enables their API we swap `_call_provider` with the real
HTTP call without touching callers.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class PreapprovalResult:
    indicative_max_loan_eur: int
    indicative_monthly_payment_eur: float
    confidence: str            # "high" | "medium" | "low"
    notes: list[str]
    partner_deep_link: str


# Conservative defaults — real banks vary widely.
DEBT_TO_INCOME_MAX = 0.35  # cuota máx / ingreso mensual neto
DEFAULT_RATE = 3.5
DEFAULT_YEARS = 25
MIN_MONTHLY_INCOME = 1000  # por debajo, ningún banco español concede hipoteca


async def request_preapproval(
    *,
    monthly_income_eur: int,
    other_monthly_debts_eur: int = 0,
    savings_eur: int,
    has_indefinite_contract: bool,
    age: int,
    target_property_price_eur: int | None = None,
) -> PreapprovalResult:
    notes: list[str] = []

    if age >= 70:
        notes.append("Edad avanzada limita el plazo a max 80-edad años.")
    years = min(DEFAULT_YEARS, max(5, 80 - age))

    if monthly_income_eur < MIN_MONTHLY_INCOME:
        return PreapprovalResult(
            indicative_max_loan_eur=0,
            indicative_monthly_payment_eur=0.0,
            confidence="low",
            notes=notes + [
                "Ingresos por debajo del mínimo habitual exigido por la banca "
                f"(~{MIN_MONTHLY_INCOME}€/mes netos)."
            ],
            partner_deep_link="https://www.trioteca.com",
        )

    monthly_capacity = monthly_income_eur * DEBT_TO_INCOME_MAX - other_monthly_debts_eur
    if monthly_capacity <= 50:
        return PreapprovalResult(
            indicative_max_loan_eur=0,
            indicative_monthly_payment_eur=0.0,
            confidence="low",
            notes=notes + [
                "Capacidad de endeudamiento estimada insuficiente con los datos aportados."
            ],
            partner_deep_link="https://www.trioteca.com",
        )

    monthly_rate = (DEFAULT_RATE / 100.0) / 12.0
    n = years * 12
    if monthly_rate > 0:
        max_loan = monthly_capacity * ((1 + monthly_rate) ** n - 1) / (
            monthly_rate * (1 + monthly_rate) ** n
        )
    else:
        max_loan = monthly_capacity * n

    confidence = "medium" if has_indefinite_contract else "low"
    if savings_eur < 0.10 * (target_property_price_eur or max_loan):
        notes.append(
            "El ahorro disponible suele tener que cubrir 20-30% del precio + gastos. "
            "Confirmar."
        )
        confidence = "low" if confidence == "low" else "medium"

    if (
        target_property_price_eur
        and target_property_price_eur * 0.80 > max_loan
    ):
        notes.append("La capacidad estimada no cubre el 80% del precio objetivo.")

    notes.append(
        "Estimación informativa basada en ratio cuota/ingresos del 35% y tipo medio "
        f"{DEFAULT_RATE}% a {years} años. La oferta real depende del banco."
    )

    deep_link = (
        f"https://www.trioteca.com/inicio?ingresos={monthly_income_eur}"
        f"&ahorro={savings_eur}&precio={target_property_price_eur or ''}"
    )

    return PreapprovalResult(
        indicative_max_loan_eur=int(max_loan),
        indicative_monthly_payment_eur=round(monthly_capacity, 2),
        confidence=confidence,
        notes=notes,
        partner_deep_link=deep_link,
    )
