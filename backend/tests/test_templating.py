"""Templating placeholder substitution."""
from app.services.templating import render_template


class _Fake:
    """Stand-in for an ORM row: unset attributes read as None."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __getattr__(self, _name):
        return None


class _Lead(_Fake):
    pass


class _Property(_Fake):
    pass


def _lead():
    return _Lead(
        full_name="Juan Pérez",
        email="juan@example.com",
        phone_e164="+34600111222",
        budget_min=200_000,
        budget_max=350_000,
        language="es",
    )


def _property():
    return _Property(
        title="Piso en Malasaña",
        reference="MAD-001",
        price=320_000,
        bedrooms=2,
        bathrooms=1,
        area_sqm=70,
        address="Calle X 1",
        neighborhood="Malasaña",
        city="Madrid",
    )


def test_renders_lead_first_name():
    out = render_template("Hola {{lead.first_name}}, te escribo de la inmobiliaria.", lead=_lead())
    assert out == "Hola Juan, te escribo de la inmobiliaria."


def test_renders_property_title_and_price():
    out = render_template(
        "El piso {{property.title}} cuesta {{property.price}}.",
        property_=_property(),
    )
    assert "Malasaña" in out
    assert "€320,000" in out


def test_missing_value_renders_empty():
    out = render_template("Hola {{lead.first_name}}!", lead=_Lead(full_name=None))
    # full_name is None → first_name is empty string
    assert out == "Hola !"


def test_unknown_placeholder_renders_empty():
    out = render_template("Hola {{lead.unknown}} fin", lead=_lead())
    assert out == "Hola  fin"
