"""PDF rendering with WeasyPrint + Jinja2 templates.

Two templates for now:
- `property_card.html` — personalized one-pager for (lead, property).
- `offer.html` — formal purchase/rent offer.

We keep CSS inline in the template so WeasyPrint doesn't need to fetch
external assets. Images are referenced by absolute URL — WeasyPrint will
download them at render time.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.logging import get_logger

log = get_logger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_pdf(template_name: str, context: dict[str, Any]) -> bytes:
    """Render a Jinja template to a PDF byte string."""
    from weasyprint import HTML  # imported lazily — heavy native deps

    template = _env.get_template(template_name)
    html = template.render(**context)
    return HTML(string=html).write_pdf()
