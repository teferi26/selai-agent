"""Auto-generate property descriptions and quality audits from photos.

Uses Claude multimodal (vision). The agent uploads up to 12 photos and
gets back:
- A polished description in the agency's tone (300-400 words).
- A bullet list of detected highlights (good selling points).
- A bullet list of detected weaknesses (e.g. "kitchen photo is dark",
  "missing photo of bathroom") so the agency can re-shoot.

We send images as public HTTPS/HTTP URL references for Claude to fetch.  A
catalog value is never interpreted as a server-local path: property data can
ultimately come from customer-controlled imports, and turning an arbitrary
string into ``Path(...).read_bytes()`` exposes every readable file on the API
host (environment files and mounted credentials included).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from anthropic import AsyncAnthropic

from app.core.logging import get_logger
from app.core.settings import settings
from app.db.models import Property
from app.services.url_guard import is_obviously_private

log = get_logger(__name__)
_client: AsyncAnthropic | None = None
MAX_IMAGES = 12


def _client_singleton() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
    return _client


@dataclass
class PropertyVisionResult:
    description: str
    highlights: list[str]
    weaknesses: list[str]


async def describe_property_from_photos(
    property_: Property,
    *,
    image_urls: list[str] | None = None,
    agency_tone: str = "cercano y profesional",
    target_language: str = "es",
) -> PropertyVisionResult:
    urls = image_urls or list(property_.image_urls or [])
    if not urls:
        raise ValueError("no images provided")
    urls = urls[:MAX_IMAGES]

    image_blocks = _materialize_images(urls)

    facts = (
        f"- Tipo: {property_.property_type.value}\n"
        f"- Operación: {property_.operation}\n"
        f"- Ubicación: {property_.neighborhood or ''} {property_.city}\n"
        f"- {property_.bedrooms or '?'} hab, {property_.bathrooms or '?'} baños, "
        f"{property_.area_sqm or '?'} m²\n"
        f"- Precio: {property_.price} EUR\n"
    )

    prompt = (
        "Eres un copy de inmobiliaria experto. Te paso fotos y datos de un piso. "
        "Genera tres bloques separados por las cabeceras exactas que indico:\n\n"
        "DESCRIPTION:\n<descripción de 300-400 palabras en idioma "
        f"{target_language}, tono {agency_tone}, sin inventar datos que no se vean en las fotos>\n\n"
        "HIGHLIGHTS:\n- <punto fuerte 1>\n- <punto fuerte 2>\n...\n\n"
        "WEAKNESSES:\n- <foto floja, defecto o ausencia detectada que recomiendas mejorar>\n- ...\n\n"
        f"Datos del piso:\n{facts}\n\n"
        "Reglas: nunca inventes precios, dimensiones ni características que no estén en los datos "
        "o claramente visibles. Si una foto es mala (oscura, borrosa, mal encuadrada) márcalo "
        "en WEAKNESSES."
    )

    content_blocks: list[dict[str, Any]] = list(image_blocks)
    content_blocks.append({"type": "text", "text": prompt})

    client = _client_singleton()
    resp = await client.messages.create(
        model=settings.agent_model_smart,
        max_tokens=1500,
        messages=[{"role": "user", "content": content_blocks}],
    )
    text = "\n".join(b.text for b in resp.content if b.type == "text")
    return _parse_sections(text)


def _safe_remote_image_url(value: str) -> str | None:
    """Return a remote image URL, never a local/resource path.

    The vision provider, not this API process, downloads the image.  Apart
    from closing local-file disclosure, the checks reject URL credentials and
    obvious loopback/private literals so we do not ask a third party to fetch
    a target that could only be meaningful inside its own network.
    """
    candidate = str(value or "").strip()
    if not candidate or len(candidate) > 2_048:
        return None
    try:
        parsed = urlsplit(candidate)
        # Accessing `.port` validates malformed ports such as `:not-a-port`.
        parsed.port  # noqa: B018
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    if not parsed.hostname or parsed.username or parsed.password:
        return None

    hostname = parsed.hostname.rstrip(".").lower()
    if (
        hostname in {"localhost", "localhost.localdomain"}
        or hostname.endswith((".localhost", ".local", ".internal"))
        or is_obviously_private(candidate)
    ):
        return None
    return candidate


def _materialize_images(urls: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, value in enumerate(urls):
        url = _safe_remote_image_url(value)
        if url is None:
            # Fail closed instead of silently proceeding with fewer photos:
            # callers must fix the unsafe catalog value, and Claude must not
            # generate a supposedly photo-based description without photos.
            log.warning("vision.image_rejected", index=index)
            raise ValueError("unsafe_image_url")
        out.append(
            {
                "type": "image",
                "source": {"type": "url", "url": url},
            }
        )
    return out


_SECTION_RE = re.compile(
    r"DESCRIPTION:\s*(.+?)\s*HIGHLIGHTS:\s*(.+?)\s*WEAKNESSES:\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)


def _parse_sections(text: str) -> PropertyVisionResult:
    m = _SECTION_RE.search(text)
    if not m:
        return PropertyVisionResult(description=text.strip(), highlights=[], weaknesses=[])

    desc = m.group(1).strip()
    highlights = _parse_bullets(m.group(2))
    weaknesses = _parse_bullets(m.group(3))
    return PropertyVisionResult(description=desc, highlights=highlights, weaknesses=weaknesses)


def _parse_bullets(block: str) -> list[str]:
    out: list[str] = []
    for line in block.splitlines():
        line = line.strip()
        for prefix in ("- ", "* ", "• "):
            if line.startswith(prefix):
                line = line[len(prefix):]
                break
        if line and not line.startswith(("DESCRIPTION", "HIGHLIGHTS", "WEAKNESSES")):
            out.append(line)
    return out
