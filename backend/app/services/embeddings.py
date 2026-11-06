"""Voyage AI embeddings — recommended pairing for Anthropic Claude.

Single source of truth for the embedding model + dimension. If we ever
swap providers (OpenAI, Cohere, local) only this module changes.
"""
from __future__ import annotations

from typing import Literal

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from app.core.logging import get_logger
from app.core.settings import settings

log = get_logger(__name__)

EMBEDDING_MODEL = "voyage-3"  # 1024 dims, multilingual, recommended pairing for Claude
EMBEDDING_DIM = 1024
_API_URL = "https://api.voyageai.com/v1/embeddings"


def _api_key() -> str:
    return settings.voyage_api_key.get_secret_value()


@retry(stop=stop_after_attempt(3), wait=wait_exponential_jitter(initial=1, max=8), reraise=True)
async def embed(
    texts: list[str],
    *,
    input_type: Literal["document", "query"] = "document",
) -> list[list[float]]:
    """Embed a batch of texts. Returns one vector per text in the same order."""
    if not texts:
        return []

    api_key = _api_key()
    if not api_key:
        log.warning("embeddings.no_api_key.returning_zeros", count=len(texts))
        return [[0.0] * EMBEDDING_DIM for _ in texts]

    payload = {"input": texts, "model": EMBEDDING_MODEL, "input_type": input_type}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(_API_URL, json=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    return [item["embedding"] for item in data["data"]]


async def embed_query(text: str) -> list[float]:
    out = await embed([text], input_type="query")
    return out[0]


def property_text_for_embedding(p) -> str:
    """Compose the text we vectorize for a property listing.

    Order matters slightly — put the strongest discriminators first.
    """
    bits = [
        f"{p.title}.",
        f"Tipo: {p.property_type.value}. Operación: {p.operation}.",
        f"Ubicación: {p.neighborhood or ''} {p.city}.",
        f"{p.bedrooms or 0} habitaciones, {p.bathrooms or 0} baños, {p.area_sqm or 0} m².",
        f"Precio: {p.price} EUR.",
    ]
    features = []
    flags = [
        (p.has_elevator, "ascensor"),
        (p.has_parking, "parking"),
        (p.has_terrace, "terraza"),
        (p.has_pool, "piscina"),
        (p.has_ac, "aire acondicionado"),
        (p.has_heating, "calefacción"),
        (p.furnished, "amueblado"),
        (p.pet_friendly, "admite mascotas"),
    ]
    features.extend(label for flag, label in flags if flag)
    if features:
        bits.append("Características: " + ", ".join(features) + ".")
    if p.description:
        bits.append(p.description)
    return " ".join(bits).strip()
