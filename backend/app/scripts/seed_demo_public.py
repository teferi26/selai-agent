"""Seed the public demo agency (slug "demo").

Backs the public chat demo on the website: anyone can talk to the agent and
it needs a believable catalogue to answer with. Deliberately has **no
subscription row** — it must never be metered or billed, and plan_limits
lets agencies without a subscription through.

Idempotent: the agency slug and the property references are the natural
keys, so re-running on every container boot is a no-op.

    python -m app.scripts.seed_demo_public
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db.models import Agency, Property, PropertyStatus, PropertyType
from app.db.session import session_scope

AGENCY_SLUG = "demo"
AGENCY_NAME = "Inmobiliaria Demo selai"

DEMO_PROPERTIES: list[dict] = [
    dict(
        reference="DEMO-MAD-001",
        title="Piso reformado con balcón en Chamberí",
        description=(
            "Vivienda exterior de 3 habitaciones en una finca clásica de Chamberí, "
            "reformada íntegramente en 2023: instalación eléctrica y fontanería nuevas, "
            "suelos de roble, cocina abierta con isla y armarios empotrados. El salón da "
            "a un balcón francés sobre calle tranquila y la habitación principal tiene "
            "baño en suite. Portal con ascensor y portero físico de mañanas. A cinco "
            "minutos andando del metro de Iglesia y del mercado de Chamberí."
        ),
        property_type=PropertyType.APARTMENT,
        operation="sale",
        price=545000,
        address="Calle Trafalgar 28",
        neighborhood="Chamberí",
        city="Madrid",
        postal_code="28010",
        bedrooms=3, bathrooms=2, area_sqm=98, year_built=1935,
        floor="4º", energy_rating="D",
        has_elevator=True, has_ac=True, has_heating=True,
    ),
    dict(
        reference="DEMO-MAD-002",
        title="Ático con terraza de 30 m² en Arganzuela",
        description=(
            "Ático de dos habitaciones en edificio de 2008 con terraza de 30 m² orientada "
            "al oeste, ideal para comer fuera de abril a octubre. Salón con salida directa "
            "a la terraza, cocina independiente equipada y baño con ducha de obra. Incluye "
            "plaza de garaje y trastero en la misma finca. La urbanización tiene piscina y "
            "zona ajardinada comunitaria. Junto a Madrid Río y la estación de Legazpi."
        ),
        property_type=PropertyType.PENTHOUSE,
        operation="sale",
        price=428000,
        address="Paseo de las Delicias 112",
        neighborhood="Arganzuela",
        city="Madrid",
        postal_code="28045",
        bedrooms=2, bathrooms=1, area_sqm=76, year_built=2008,
        floor="6º", energy_rating="C",
        has_elevator=True, has_parking=True, has_terrace=True, has_pool=True,
        has_ac=True, has_heating=True,
    ),
    dict(
        reference="DEMO-MAD-003",
        title="Estudio amueblado en Malasaña, listo para entrar",
        description=(
            "Estudio de 34 m² totalmente amueblado y equipado en el corazón de Malasaña: "
            "cama abatible, mesa de trabajo, cocina americana con lavavajillas y baño "
            "reformado. Ventana a patio interior, así que se duerme en silencio pese a "
            "estar a un paso de la plaza del Dos de Mayo. Gastos de comunidad incluidos "
            "en la renta. Se admiten mascotas pequeñas. Contrato mínimo de un año."
        ),
        property_type=PropertyType.STUDIO,
        operation="rent",
        price=1150,
        address="Calle del Espíritu Santo 19",
        neighborhood="Malasaña",
        city="Madrid",
        postal_code="28004",
        bedrooms=0, bathrooms=1, area_sqm=34, year_built=1955,
        floor="2º", energy_rating="E",
        furnished=True, pet_friendly=True, has_ac=True, has_heating=True,
    ),
    dict(
        reference="DEMO-VLC-001",
        title="Piso modernista junto al Mercado Central",
        description=(
            "Vivienda señorial de 120 m² en finca modernista de 1912, a dos calles del "
            "Mercado Central. Conserva molduras, suelos hidráulicos originales y balcones "
            "de forja a la calle. Tres habitaciones, dos baños y un salón de 30 m² con "
            "techos de 3,5 metros. Necesita actualizar la cocina, y el precio ya lo "
            "refleja. Zona de aparcamiento regulado y muy bien comunicada en metro."
        ),
        property_type=PropertyType.APARTMENT,
        operation="sale",
        price=395000,
        address="Calle Vieja de la Paja 7",
        neighborhood="Ciutat Vella",
        city="Valencia",
        postal_code="46001",
        bedrooms=3, bathrooms=2, area_sqm=120, year_built=1912,
        floor="1º", energy_rating="F",
        has_elevator=True, has_heating=True,
    ),
    dict(
        reference="DEMO-VLC-002",
        title="Apartamento en primera línea de playa en Patacona",
        description=(
            "Apartamento de dos habitaciones con vistas frontales al mar en la Patacona, "
            "a diez minutos en tranvía del centro de Valencia. Terraza acristalada de 12 m² "
            "que se usa todo el año, salón luminoso y cocina independiente. La finca es de "
            "2015, con ascensor, piscina comunitaria y plaza de garaje incluida. Se alquila "
            "sin muebles, con opción de dejar los electrodomésticos."
        ),
        property_type=PropertyType.APARTMENT,
        operation="rent",
        price=1400,
        address="Avenida Mare Nostrum 44",
        neighborhood="Patacona",
        city="Valencia",
        postal_code="46120",
        bedrooms=2, bathrooms=2, area_sqm=85, year_built=2015,
        floor="5º", energy_rating="B",
        has_elevator=True, has_parking=True, has_terrace=True, has_pool=True,
        has_ac=True, has_heating=True,
    ),
    dict(
        reference="DEMO-VLC-003",
        title="Chalet con jardín y piscina en La Cañada",
        description=(
            "Chalet independiente de 240 m² sobre parcela de 800 m² en La Cañada (Paterna), "
            "una de las zonas residenciales más buscadas del área metropolitana. Cuatro "
            "habitaciones, tres baños, salón con chimenea y cocina office abierta al jardín. "
            "Piscina privada, porche cubierto, garaje para dos coches y placas solares "
            "instaladas en 2022. Colegios internacionales y centro deportivo a cinco minutos."
        ),
        property_type=PropertyType.HOUSE,
        operation="sale",
        price=685000,
        address="Calle 253 · La Cañada",
        neighborhood="La Cañada",
        city="Paterna",
        postal_code="46182",
        bedrooms=4, bathrooms=3, area_sqm=240, year_built=1998,
        energy_rating="C",
        has_parking=True, has_terrace=True, has_pool=True, pet_friendly=True,
        has_ac=True, has_heating=True,
    ),
]


async def seed() -> None:
    async with session_scope() as db:
        agency = (
            await db.execute(select(Agency).where(Agency.slug == AGENCY_SLUG))
        ).scalar_one_or_none()

        if agency is None:
            agency = Agency(
                name=AGENCY_NAME,
                slug=AGENCY_SLUG,
                contact_email="hola@selai.es",
                timezone="Europe/Madrid",
                default_language="es",
                settings={
                    "intro": (
                        "Agencia de demostración de selai. Trabajamos vivienda "
                        "residencial en Madrid y Valencia, en venta y alquiler."
                    ),
                    "office_hours": "L-V 9:00-20:00, Sáb 10:00-14:00",
                    "operating_zones": "Madrid capital y Valencia (ciudad y área metropolitana)",
                    "specialty": "Venta y alquiler de viviendas residenciales",
                    "is_public_demo": True,
                },
            )
            db.add(agency)
            await db.flush()
            print(f"agencia demo creada (slug={AGENCY_SLUG})")
        else:
            print(f"agencia demo ya existía (slug={AGENCY_SLUG})")

        existing_refs = {
            r
            for (r,) in (
                await db.execute(
                    select(Property.reference).where(Property.agency_id == agency.id)
                )
            )
        }

        created = 0
        for spec in DEMO_PROPERTIES:
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
            created += 1

        print(f"seed_demo_public: {created} propiedades nuevas, {len(existing_refs)} ya presentes.")


if __name__ == "__main__":
    asyncio.run(seed())
