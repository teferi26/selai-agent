"""Seed (upsert) the three public plans.

Idempotent: run as many times as you want; matches by `code` and updates
prices/limits in place. Stripe product/price ids are filled in later by
`sync_stripe_plans.py`.

    python -m app.scripts.seed_plans
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db.models import Plan
from app.db.session import session_scope

PLANS: list[dict] = [
    {
        "code": "starter",
        "name": "Starter",
        "description": "Para agencias pequeñas: WhatsApp + email atendidos 24/7.",
        "monthly_price_eur": 99,
        "max_agents": 2,
        "max_properties": 100,
        "max_voice_minutes": 0,
        "features": {
            "whatsapp": True,
            "email": True,
            "web_chat": True,
            "sms": False,
            "voice": False,
            "portales": True,
            "informes": "básicos",
        },
    },
    {
        "code": "pro",
        "name": "Pro",
        "description": "El más elegido: todos los canales, incluida la voz.",
        "monthly_price_eur": 199,
        "max_agents": 5,
        "max_properties": 500,
        "max_voice_minutes": 600,
        "features": {
            "whatsapp": True,
            "email": True,
            "web_chat": True,
            "sms": True,
            "voice": True,
            "portales": True,
            "informes": "completos",
            "plantillas": True,
            "ofertas_pdf": True,
        },
    },
    {
        "code": "agencia",
        "name": "Agencia",
        "description": "Para grupos y franquicias: volumen alto y soporte prioritario.",
        "monthly_price_eur": 399,
        "max_agents": 15,
        "max_properties": 2000,
        "max_voice_minutes": 1500,
        "features": {
            "whatsapp": True,
            "email": True,
            "web_chat": True,
            "sms": True,
            "voice": True,
            "portales": True,
            "informes": "completos",
            "plantillas": True,
            "ofertas_pdf": True,
            "voice_cloning": True,
            "marketplace": True,
            "soporte_prioritario": True,
            # "Varias oficinas: cada contacto va a la que le corresponde"
            # (Pricing.tsx / pricing-data.ts, plan Agencia). El nombre exacto
            # lo lee `ensure_feature` desde `app/api/offices.py`
            # (MULTI_OFFICE_FEATURE): sin esta clave, la agencia que paga 399
            # recibe 403 en la función que su propia página de precios vende.
            "multi_oficina": True,
        },
    },
]


async def main() -> None:
    async with session_scope() as db:
        for spec in PLANS:
            existing = (
                await db.execute(select(Plan).where(Plan.code == spec["code"]))
            ).scalar_one_or_none()
            if existing is None:
                db.add(Plan(**spec))
                print(f"created plan {spec['code']} ({spec['monthly_price_eur']}€/mes)")
            else:
                for key, value in spec.items():
                    setattr(existing, key, value)
                print(f"updated plan {spec['code']} ({spec['monthly_price_eur']}€/mes)")
    print("done.")


if __name__ == "__main__":
    asyncio.run(main())
