"""Seed demo data for the pilot agency.

Run:
    python -m app.scripts.seed_demo

Idempotent: uses agency slug as natural key.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db.models import (
    Agency,
    Property,
    PropertyStatus,
    PropertyType,
    User,
    UserRole,
)
from app.db.session import session_scope

AGENCY_SLUG = "piloto"


async def seed() -> None:
    async with session_scope() as db:
        agency = (
            await db.execute(select(Agency).where(Agency.slug == AGENCY_SLUG))
        ).scalar_one_or_none()

        if agency is None:
            agency = Agency(
                name="Inmobiliaria Piloto",
                slug=AGENCY_SLUG,
                contact_email="hola@piloto.test",
                contact_phone="+34600000000",
                timezone="Europe/Madrid",
                default_language="es",
                settings={
                    "intro": "Inmobiliaria especializada en Madrid centro y alrededores.",
                    "office_hours": "L-V 9:00-20:00, Sáb 10:00-14:00",
                    "operating_zones": "Madrid capital y sur (Getafe, Leganés)",
                    "specialty": "Venta y alquiler de viviendas residenciales",
                    "whatsapp_phone_number_id": "",
                    "google_review_url": "https://g.page/r/EXAMPLE/review",
                },
            )
            db.add(agency)
            await db.flush()

        existing_user = (
            await db.execute(select(User).where(User.email == "owner@piloto.test"))
        ).scalar_one_or_none()
        if existing_user is None:
            db.add(
                User(
                    agency_id=agency.id,
                    email="owner@piloto.test",
                    full_name="Dueño Piloto",
                    role=UserRole.OWNER,
                    phone="+34600000001",
                )
            )

        demo_properties = [
            dict(
                reference="MAD-001",
                title="Piso luminoso en Malasaña",
                description=(
                    "Piso reformado de 2 habitaciones en pleno Malasaña. Cocina abierta, "
                    "suelos de madera originales restaurados, balcón a calle peatonal."
                ),
                property_type=PropertyType.APARTMENT,
                operation="sale",
                price=395000,
                address="Calle San Bernardo 40",
                neighborhood="Malasaña",
                city="Madrid",
                postal_code="28015",
                bedrooms=2, bathrooms=1, area_sqm=68, year_built=1920,
                floor="3º", energy_rating="D",
                has_elevator=False, has_ac=True, has_heating=True,
            ),
            dict(
                reference="MAD-002",
                title="Ático con terraza en Chamberí",
                description=(
                    "Ático reformado con terraza de 25 m² en Chamberí. 3 habitaciones, "
                    "2 baños, plaza de garaje incluida, vistas despejadas."
                ),
                property_type=PropertyType.PENTHOUSE,
                operation="sale",
                price=780000,
                address="Calle Ponzano 15",
                neighborhood="Chamberí",
                city="Madrid",
                postal_code="28003",
                bedrooms=3, bathrooms=2, area_sqm=110, year_built=1960,
                floor="7º", energy_rating="C",
                has_elevator=True, has_parking=True, has_terrace=True,
                has_ac=True, has_heating=True,
            ),
            dict(
                reference="MAD-003",
                title="Estudio acogedor en Lavapiés",
                description="Estudio ideal para inversión o primera vivienda. Amueblado.",
                property_type=PropertyType.STUDIO,
                operation="rent",
                price=950,
                address="Calle Mesón de Paredes 80",
                neighborhood="Lavapiés",
                city="Madrid",
                postal_code="28012",
                bedrooms=0, bathrooms=1, area_sqm=32,
                floor="2º", energy_rating="E",
                furnished=True, has_ac=True,
            ),
            dict(
                reference="MAD-004",
                title="Piso familiar en Arturo Soria",
                description=(
                    "Vivienda amplia de 4 habitaciones en urbanización con piscina y "
                    "zonas comunes. Plaza de garaje y trastero."
                ),
                property_type=PropertyType.APARTMENT,
                operation="sale",
                price=620000,
                address="Calle Arturo Soria 210",
                neighborhood="Arturo Soria",
                city="Madrid",
                postal_code="28033",
                bedrooms=4, bathrooms=2, area_sqm=140, year_built=1990,
                floor="5º", energy_rating="D",
                has_elevator=True, has_parking=True, has_pool=True,
                has_ac=True, has_heating=True,
            ),
            dict(
                reference="GET-001",
                title="Casa adosada en Getafe Norte",
                description="Adosado de 4 habitaciones con jardín y garaje privado.",
                property_type=PropertyType.HOUSE,
                operation="sale",
                price=345000,
                address="Calle de los Olivos 22",
                neighborhood="Getafe Norte",
                city="Getafe",
                postal_code="28905",
                bedrooms=4, bathrooms=3, area_sqm=180, year_built=2005,
                energy_rating="C",
                has_parking=True, has_terrace=True, pet_friendly=True,
                has_ac=True, has_heating=True,
            ),
        ]

        existing_refs = {
            r
            for (r,) in (
                await db.execute(
                    select(Property.reference).where(Property.agency_id == agency.id)
                )
            )
        }

        for spec in demo_properties:
            if spec["reference"] in existing_refs:
                continue
            db.add(
                Property(
                    agency_id=agency.id,
                    status=PropertyStatus.AVAILABLE,
                    country="ES",
                    **spec,
                )
            )

    print(f"Seed completo. Agencia slug={AGENCY_SLUG}.")


if __name__ == "__main__":
    asyncio.run(seed())
