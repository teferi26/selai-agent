"""Versioned loader for the legal documents (TdS, DPA, Privacy Policy).

Documents live under `app/legal/<version>/<slug>.es.md` with a YAML-like
frontmatter that records `slug`, `version`, `effective_date`, `title`,
`language`. Adding a new version means dropping a new folder (`v2/`); old
versions stay readable so signed consents remain auditable.

Legal identity belongs to the selai provider and comes only from global
environment settings. At acceptance time we store both the fully rendered
markdown and its SHA-256 in `consent_logs.evidence`; later changes to provider
configuration therefore cannot rewrite what a customer signed or its PDF.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from app.core.settings import settings

LEGAL_ROOT = Path(__file__).resolve().parent.parent / "legal"

# Slugs we expect to find. Adding to this list means we'll fail closed if a
# version doesn't ship that document.
KNOWN_SLUGS: tuple[str, ...] = ("terms_of_service", "dpa", "privacy_policy")

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_VERSION_DIR_RE = re.compile(r"^v\d+$")
_JINJA = Environment(undefined=StrictUndefined, autoescape=False)
EVIDENCE_SCHEMA = "selai.legal-consent.v2"


class LegalEvidenceError(ValueError):
    """Stored consent evidence is present but cannot prove its own document."""


@dataclass(frozen=True)
class AcceptedLegalDocument:
    slug: str
    version: str
    title: str
    rendered_markdown: str
    document_hash: str


@dataclass(frozen=True)
class LegalDocument:
    slug: str
    version: str
    title: str
    effective_date: str
    language: str
    body_template: str  # raw markdown including Jinja `{{slots}}`

    def render(self) -> str:
        """Render with the global provider identity, never tenant settings."""
        return _JINJA.from_string(self.body_template).render(**_build_slots())

    @property
    def hash(self) -> str:
        """SHA-256 of the rendered bytes a customer sees and accepts."""
        return _document_hash(self.render())


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    raw = match.group(1)
    body = text[match.end() :]
    meta: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, body


def _build_slots() -> dict[str, str]:
    return {
        "COMPANY_NAME": settings.legal_company_name,
        "COMPANY_VAT": settings.legal_company_vat,
        "ADDRESS": settings.legal_company_address,
        "CITY": settings.legal_company_city,
        "DPO_EMAIL": settings.legal_dpo_email,
    }


def _document_hash(rendered_markdown: str) -> str:
    return hashlib.sha256(rendered_markdown.encode("utf-8")).hexdigest()


@lru_cache(maxsize=64)
def load_document(slug: str, version: str) -> LegalDocument:
    if slug not in KNOWN_SLUGS:
        raise ValueError(f"unknown legal document slug: {slug}")
    path = LEGAL_ROOT / version / f"{slug}.es.md"
    if not path.exists():
        raise FileNotFoundError(f"legal document not found: {path}")
    # `utf-8-sig` strips a leading BOM if there is one. A Windows editor (or
    # PowerShell's `Set-Content -Encoding UTF8`) writes it silently, and with a
    # BOM in front the `^---` of `_FRONTMATTER_RE` stops matching: the document
    # would still load, but with no title, no effective date and the whole
    # frontmatter leaking into the body. It fails quietly, so read it away.
    canonical = path.read_text(encoding="utf-8-sig")
    meta, body = _parse_frontmatter(canonical)
    return LegalDocument(
        slug=meta.get("slug", slug),
        version=meta.get("version", version),
        title=meta.get("title", slug),
        effective_date=meta.get("effective_date", ""),
        language=meta.get("language", "es"),
        body_template=canonical,
    )


def list_versions(slug: str) -> list[str]:
    """All versions present on disk for the given slug, oldest first."""
    versions: list[str] = []
    if not LEGAL_ROOT.is_dir():
        return versions
    for child in LEGAL_ROOT.iterdir():
        if not child.is_dir() or not _VERSION_DIR_RE.match(child.name):
            continue
        if (child / f"{slug}.es.md").exists():
            versions.append(child.name)
    versions.sort(key=lambda v: int(v.lstrip("v")))
    return versions


def current_version(slug: str) -> str:
    """The latest version present for the slug. Raises if none exists."""
    versions = list_versions(slug)
    if not versions:
        raise FileNotFoundError(f"no version found for {slug}")
    return versions[-1]


def current_versions() -> dict[str, str]:
    """Map of slug → current version. Raises if any KNOWN_SLUG is missing."""
    return {slug: current_version(slug) for slug in KNOWN_SLUGS}


def list_documents() -> list[dict[str, str]]:
    """Catalog of available documents at their current version."""
    out: list[dict[str, str]] = []
    for slug in KNOWN_SLUGS:
        try:
            version = current_version(slug)
        except FileNotFoundError:
            continue
        doc = load_document(slug, version)
        out.append(
            {
                "slug": doc.slug,
                "version": doc.version,
                "title": doc.title,
                "effective_date": doc.effective_date,
                "language": doc.language,
                "document_hash": doc.hash,
            }
        )
    return out


def consent_evidence(
    *,
    slug: str,
    version: str,
    expected_hash: str,
    user_agent: str | None,
    accepted_text: str,
) -> str:
    """Serialize the exact rendered document accepted by the owner.

    Keeping the rendered markdown beside its digest is deliberate. Provider
    identity can legitimately change later, and template versions are meant to
    be immutable but files can still be damaged. Neither event may rewrite an
    earlier acceptance or the PDF generated from it.
    """
    doc = load_document(slug, version)
    rendered = doc.render()
    rendered_hash = _document_hash(rendered)
    if rendered_hash != expected_hash:
        raise LegalEvidenceError(
            f"accepted hash no longer matches rendered document: {slug} {version}"
        )
    payload = {
        "schema": EVIDENCE_SCHEMA,
        "slug": doc.slug,
        "version": doc.version,
        "title": doc.title,
        "accepted_at_url": settings.public_base_url,
        "accepted_text": accepted_text,
        "user_agent": user_agent or "desconocido",
        "document_sha256": rendered_hash,
        "rendered_markdown": rendered,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def accepted_document_from_evidence(evidence: str) -> AcceptedLegalDocument | None:
    """Return the immutable rendered snapshot stored by `consent_evidence`.

    Old human-readable evidence returns ``None``: it only hashed the template
    and cannot prove which provider identity was rendered. Malformed or
    self-inconsistent v2 evidence raises instead of producing a false PDF.
    """
    try:
        payload = json.loads(evidence)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != EVIDENCE_SCHEMA:
        return None

    required = ("slug", "version", "title", "rendered_markdown", "document_sha256")
    if any(not isinstance(payload.get(key), str) or not payload[key] for key in required):
        raise LegalEvidenceError("incomplete rendered legal evidence")

    rendered = payload["rendered_markdown"]
    expected_hash = _document_hash(rendered)
    if payload["document_sha256"] != expected_hash:
        raise LegalEvidenceError("rendered legal evidence hash mismatch")

    return AcceptedLegalDocument(
        slug=payload["slug"],
        version=payload["version"],
        title=payload["title"],
        rendered_markdown=rendered,
        document_hash=expected_hash,
    )
