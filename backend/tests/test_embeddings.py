"""Cheap tests for the embeddings helper that don't hit the network."""
from app.services.embeddings import EMBEDDING_DIM, property_text_for_embedding


class _StubEnum:
    def __init__(self, value: str) -> None:
        self.value = value


class _StubProperty:
    def __init__(self, **kwargs):
        self.title = kwargs.get("title", "Piso")
        self.property_type = _StubEnum(kwargs.get("property_type", "apartment"))
        self.operation = kwargs.get("operation", "sale")
        self.neighborhood = kwargs.get("neighborhood")
        self.city = kwargs.get("city", "Madrid")
        self.bedrooms = kwargs.get("bedrooms", 2)
        self.bathrooms = kwargs.get("bathrooms", 1)
        self.area_sqm = kwargs.get("area_sqm", 70)
        self.price = kwargs.get("price", 300_000)
        self.has_elevator = kwargs.get("has_elevator", False)
        self.has_parking = kwargs.get("has_parking", False)
        self.has_terrace = kwargs.get("has_terrace", False)
        self.has_pool = kwargs.get("has_pool", False)
        self.has_ac = kwargs.get("has_ac", False)
        self.has_heating = kwargs.get("has_heating", False)
        self.furnished = kwargs.get("furnished", False)
        self.pet_friendly = kwargs.get("pet_friendly", False)
        self.description = kwargs.get("description", "")


def test_dim_is_voyage3():
    assert EMBEDDING_DIM == 1024


def test_property_text_includes_key_facts():
    p = _StubProperty(title="Ático en Chamberí", neighborhood="Chamberí", has_terrace=True)
    text = property_text_for_embedding(p)
    assert "Chamberí" in text
    assert "terraza" in text
    assert "Madrid" in text
    assert "70 m²" in text
