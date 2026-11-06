"""Tests for the versioned legal documents loader."""

from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api import legal as legal_api
from app.core.auth import AuthContext
from app.core.settings import settings
from app.db.models import UserRole
from app.services.legal_documents import (
    KNOWN_SLUGS,
    LEGAL_ROOT,
    LegalEvidenceError,
    accepted_document_from_evidence,
    consent_evidence,
    current_version,
    current_versions,
    list_documents,
    list_versions,
    load_document,
)

# Current version per document. They advance independently: publishing a new
# folder with only the document that changed keeps the other two on their last
# signed version instead of forcing a pointless re-consent.
EXPECTED_CURRENT = {
    "terms_of_service": "v6",
    "dpa": "v3",
    "privacy_policy": "v3",
}


def test_all_known_slugs_have_a_current_version():
    versions = current_versions()
    assert set(versions.keys()) == set(KNOWN_SLUGS)
    assert versions == EXPECTED_CURRENT


def test_load_terms_renders_company_slots():
    doc = load_document("terms_of_service", "v1")
    rendered = doc.render()
    # Development uses visible pending labels rather than inventing a provider.
    assert "[Razón social pendiente]" in rendered
    assert "{{COMPANY_NAME}}" not in rendered  # template was substituted


def test_load_dpa_uses_global_provider_not_tenant_settings(monkeypatch):
    monkeypatch.setattr(settings, "legal_company_name", "Selai Provider Test SL")
    doc = load_document("dpa", "v1")
    rendered = doc.render()
    assert "Selai Provider Test SL" in rendered
    assert "Inmobiliaria Sol SL" not in rendered
    assert "{{COMPANY_NAME}}" not in rendered


def test_unknown_slug_raises():
    with pytest.raises(ValueError, match="unknown legal document slug"):
        load_document("not_a_real_slug", "v1")


def test_missing_version_raises():
    with pytest.raises(FileNotFoundError):
        load_document("terms_of_service", "v999")


def test_list_documents_returns_three_known_slugs():
    catalog = list_documents()
    slugs = {doc["slug"] for doc in catalog}
    assert slugs == set(KNOWN_SLUGS)
    for entry in catalog:
        assert entry["language"] == "es"
        assert re.match(r"v\d+", entry["version"])
        assert entry["document_hash"] == load_document(
            entry["slug"], entry["version"]
        ).hash


def test_document_hash_is_deterministic():
    doc1 = load_document("dpa", "v1")
    doc2 = load_document("dpa", "v1")
    assert doc1.hash == doc2.hash
    assert len(doc1.hash) == 64  # SHA-256 hex


def test_consent_evidence_rejects_a_hash_other_than_the_one_displayed():
    with pytest.raises(LegalEvidenceError, match="accepted hash no longer matches"):
        consent_evidence(
            slug="dpa",
            version="v1",
            expected_hash="0" * 64,
            user_agent="Mozilla/5.0 (test)",
            accepted_text="He leído y acepto",
        )


def test_consent_evidence_includes_document_hash():
    evidence = consent_evidence(
        slug="dpa",
        version="v1",
        expected_hash=load_document("dpa", "v1").hash,
        user_agent="Mozilla/5.0 (test)",
        accepted_text="He leído y acepto",
    )
    assert "DPA" in evidence or "dpa" in evidence.lower() or "Acuerdo" in evidence
    assert "He leído y acepto" in evidence
    assert "Mozilla/5.0 (test)" in evidence
    # The rendered hash and body travel together so the evidence is
    # self-verifying and can reproduce the accepted PDF later.
    doc = load_document("dpa", "v1")
    assert doc.hash in evidence
    accepted = accepted_document_from_evidence(evidence)
    assert accepted is not None
    assert accepted.document_hash == doc.hash
    assert accepted.rendered_markdown == doc.render()


def test_current_version_picks_latest():
    # Several versions exist on disk; the picker must return the newest one for
    # each slug while keeping the older ones loadable so signed consents remain
    # auditable.
    for slug, expected in EXPECTED_CURRENT.items():
        assert current_version(slug) == expected
    for slug in KNOWN_SLUGS:
        assert load_document(slug, "v1").version == "v1"


def test_v2_documents_render_with_slots(monkeypatch):
    monkeypatch.setattr(settings, "legal_company_name", "Selai Test SL")
    for slug in KNOWN_SLUGS:
        doc = load_document(slug, "v2")
        assert doc.version == "v2"
        assert doc.effective_date == "2026-07-25"
        rendered = doc.render()
        assert "Selai Test SL" in rendered
        assert "{{" not in rendered  # every slot was substituted


def test_no_published_document_starts_with_a_bom():
    """A BOM silently kills the frontmatter parse (`^---` stops matching).

    The loader reads with `utf-8-sig` so it survives one, but the files
    themselves must stay clean: the BOM is part of the SHA-256 stored as
    consent evidence, so adding or removing it later changes the hash of a
    document that was already signed.
    """
    for slug in KNOWN_SLUGS:
        for version in list_versions(slug):
            path = LEGAL_ROOT / version / f"{slug}.es.md"
            assert not path.read_bytes().startswith(b"\xef\xbb\xbf"), path


def test_every_published_version_has_readable_frontmatter():
    """Guards against the failure mode above: a title and a date, always."""
    for slug in KNOWN_SLUGS:
        for version in list_versions(slug):
            doc = load_document(slug, version)
            assert doc.title and doc.title != slug, (slug, version)
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", doc.effective_date), (slug, version)
            assert doc.language == "es"


def test_current_documents_show_the_version_declared_in_frontmatter():
    """The visible contract and its audit metadata must identify the same text."""
    for slug, version in EXPECTED_CURRENT.items():
        rendered = load_document(slug, version).render()
        assert f"**Versión:** {version}" in rendered, slug


def test_every_published_version_still_loads():
    """Old versions must stay readable: consents point at them by version."""
    for slug in KNOWN_SLUGS:
        for version in list_versions(slug):
            doc = load_document(slug, version)
            assert doc.version == version
            assert "{{" not in doc.render()


def test_current_terms_do_not_promise_a_price_other_than_the_one_charged():
    """Guard the fixed final-price and invoice-treatment contract."""
    doc = load_document("terms_of_service", current_version("terms_of_service"))
    rendered = doc.render()
    assert "sin IVA" not in rendered
    assert "IVA no incluido" not in rendered
    assert "precio final constante" in rendered.lower()
    assert "cantidad exacta que se carga" in rendered.lower()
    assert "incluido en ese importe" in rendered.lower()
    assert "se desglosará en la factura" in rendered.lower()
    assert "inversión del sujeto pasivo" in rendered.lower()
    assert "pendiente de revisión final por asesoría jurídica colegiada" in rendered.lower()
    # The three published prices must still be the ones in the document.
    for price in ("99 €", "199 €", "399 €"):
        assert price in rendered


def test_current_terms_cover_yearly_billing_and_immediate_activation():
    rendered = load_document("terms_of_service", current_version("terms_of_service")).render()

    for yearly_total in ("950,40 €", "1.910,40 €", "3.830,40 €"):
        assert yearly_total in rendered
    assert "facturación es **mensual o anual y por adelantado**" in rendered
    assert "activar un plan de pago de forma inmediata" in rendered
    assert "termina al completar el pago" in rendered


def test_current_documents_render_complete_global_provider_identity(monkeypatch):
    values = {
        "legal_company_name": "Selai Provider Test SL",
        "legal_company_vat": "B12345678",
        "legal_company_address": "Calle Prueba 1, 03001 Alicante",
        "legal_company_city": "Alicante",
        "legal_dpo_email": "legal@selai.test",
    }
    for key, value in values.items():
        monkeypatch.setattr(settings, key, value)

    rendered = {
        slug: load_document(slug, version).render() for slug, version in EXPECTED_CURRENT.items()
    }
    combined = "\n".join(rendered.values())
    for value in values.values():
        assert value in combined
    assert "{{" not in combined
    assert "[CIUDAD PENDIENTE" not in combined


def test_evidence_keeps_exact_render_after_provider_identity_changes(monkeypatch):
    monkeypatch.setattr(settings, "legal_company_name", "Proveedor Original SL")
    evidence = consent_evidence(
        slug="dpa",
        version=current_version("dpa"),
        expected_hash=load_document("dpa", current_version("dpa")).hash,
        user_agent="test-agent",
        accepted_text="Acepto",
    )
    accepted = accepted_document_from_evidence(evidence)
    assert accepted is not None
    original_hash = accepted.document_hash

    monkeypatch.setattr(settings, "legal_company_name", "Proveedor Posterior SL")
    assert "Proveedor Original SL" in accepted.rendered_markdown
    assert "Proveedor Posterior SL" not in accepted.rendered_markdown
    assert accepted.document_hash == original_hash
    assert load_document("dpa", current_version("dpa")).hash != original_hash


def test_tampered_rendered_evidence_fails_closed():
    evidence = consent_evidence(
        slug="privacy_policy",
        version=current_version("privacy_policy"),
        expected_hash=load_document(
            "privacy_policy", current_version("privacy_policy")
        ).hash,
        user_agent=None,
        accepted_text="Acepto",
    )
    payload = json.loads(evidence)
    payload["rendered_markdown"] += "\ntexto alterado"

    with pytest.raises(LegalEvidenceError, match="hash mismatch"):
        accepted_document_from_evidence(json.dumps(payload))


def test_legacy_template_only_evidence_requires_fresh_consent():
    assert accepted_document_from_evidence("SHA-256 documento: deadbeef.") is None


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _ConsentDb:
    def __init__(self, consent):
        self.consent = consent

    async def execute(self, _statement):
        return _ScalarResult(self.consent)


class _ConsentStatusDb:
    def __init__(self, owners, consents):
        self.owners = owners
        self.consents = iter(consents)
        self.owner_query_pending = True

    async def get(self, _model, identifier):
        return SimpleNamespace(id=identifier)

    async def execute(self, _statement):
        if self.owner_query_pending:
            self.owner_query_pending = False
            return _ScalarListResult(self.owners)
        return _ScalarResult(next(self.consents))


class _ScalarListResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


class _LegacyConsentDb:
    def __init__(self, consents):
        self.consents = iter(consents)
        self.statements = []
        self.added = []
        self.owner = SimpleNamespace(id=uuid.uuid4())

    async def get(self, _model, _identifier):
        return SimpleNamespace(id=_identifier)

    async def execute(self, statement):
        self.statements.append(statement)
        if "FROM users" in str(statement):
            return _ScalarResult(self.owner)
        return _ScalarResult(next(self.consents))

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        return None


def _auth_for_agency(
    agency_id: uuid.UUID,
    *,
    role: UserRole = UserRole.OWNER,
    dev_bypass: bool = False,
) -> AuthContext:
    user = SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=agency_id,
        role=role,
        is_active=True,
    )
    return AuthContext(
        clerk_user_id="user_authenticated",
        email="owner@test.es",
        raw_claims={},
        user=user,
        dev_bypass=dev_bypass,
    )


def _legacy_body() -> legal_api.LegacyConsentBody:
    versions = current_versions()
    return legal_api.LegacyConsentBody(
        accepted_terms=True,
        accepted_dpa=True,
        accepted_privacy=True,
        accepted_versions=versions,
        accepted_hashes={
            slug: load_document(slug, version).hash
            for slug, version in versions.items()
        },
    )


def _post_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


@pytest.mark.asyncio
async def test_consent_status_marks_same_version_with_changed_render_as_stale(monkeypatch):
    agency_id = uuid.uuid4()
    owner = SimpleNamespace(id=uuid.uuid4())
    versions = current_versions()
    monkeypatch.setattr(settings, "legal_company_name", "Proveedor Original SL")
    consents = [
        SimpleNamespace(
            document_version=versions[slug],
            evidence=consent_evidence(
                slug=slug,
                version=versions[slug],
                expected_hash=load_document(slug, versions[slug]).hash,
                user_agent="test-agent",
                accepted_text="Acepto",
            ),
            created_at=datetime(2026, 8, 30, tzinfo=UTC),
            ip_address="127.0.0.1",
            user_id=owner.id,
        )
        for slug in ("terms_of_service", "dpa", "privacy_policy")
    ]
    monkeypatch.setattr(settings, "legal_company_name", "Proveedor Posterior SL")

    result = await legal_api.consent_status(
        agency_id=agency_id,
        db=_ConsentStatusDb([owner], consents),
    )

    assert result.missing == []
    assert {item["slug"] for item in result.stale} == {
        "terms_of_service",
        "dpa",
        "privacy_policy",
    }
    assert {item["reason"] for item in result.stale} == {
        "rendered_document_changed"
    }


@pytest.mark.asyncio
async def test_consent_status_accepts_signature_from_any_active_owner():
    agency_id = uuid.uuid4()
    first_owner = SimpleNamespace(id=uuid.uuid4())
    signing_owner = SimpleNamespace(id=uuid.uuid4())
    versions = current_versions()
    consents = [
        SimpleNamespace(
            document_version=versions[slug],
            evidence=consent_evidence(
                slug=slug,
                version=versions[slug],
                expected_hash=load_document(slug, versions[slug]).hash,
                user_agent="test-agent",
                accepted_text="Acepto",
            ),
            created_at=datetime(2026, 8, 30, tzinfo=UTC),
            ip_address="127.0.0.1",
            user_id=signing_owner.id,
        )
        for slug in ("terms_of_service", "dpa", "privacy_policy")
    ]

    result = await legal_api.consent_status(
        agency_id=agency_id,
        db=_ConsentStatusDb([first_owner, signing_owner], consents),
    )

    assert result.owner_user_id == str(first_owner.id)
    assert result.missing == []
    assert result.stale == []


@pytest.mark.asyncio
async def test_legacy_consent_uses_latest_same_version_evidence_for_idempotency():
    versions = current_versions()
    consents = [
        SimpleNamespace(
            evidence=consent_evidence(
                slug=slug,
                version=versions[slug],
                expected_hash=load_document(slug, versions[slug]).hash,
                user_agent="test-agent",
                accepted_text="Acepto",
            )
        )
        for slug in ("terms_of_service", "dpa", "privacy_policy")
    ]
    db = _LegacyConsentDb(consents)
    agency_id = uuid.uuid4()

    result = await legal_api.legacy_consent(
        agency_id=agency_id,
        body=_legacy_body(),
        request=_post_request(),
        auth=_auth_for_agency(agency_id),
        db=db,
    )

    consent_statements = [
        statement for statement in db.statements if "FROM consent_logs" in str(statement)
    ]
    assert result["created"] == 0
    assert db.added == []
    assert len(consent_statements) == 3
    assert all(
        "ORDER BY consent_logs.created_at DESC" in str(statement)
        for statement in consent_statements
    )
    assert all("consent_logs.user_id" in str(statement) for statement in consent_statements)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.AGENT, UserRole.ADMIN])
async def test_legacy_consent_rejects_non_owner_signers(role):
    agency_id = uuid.uuid4()

    with pytest.raises(HTTPException) as exc:
        await legal_api.legacy_consent(
            agency_id=agency_id,
            body=_legacy_body(),
            request=_post_request(),
            auth=_auth_for_agency(agency_id, role=role),
            db=_LegacyConsentDb([]),
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "owner_consent_required"


@pytest.mark.asyncio
async def test_legacy_consent_rejects_header_only_dev_identity():
    agency_id = uuid.uuid4()

    with pytest.raises(HTTPException) as exc:
        await legal_api.legacy_consent(
            agency_id=agency_id,
            body=_legacy_body(),
            request=_post_request(),
            auth=_auth_for_agency(agency_id, dev_bypass=True),
            db=_LegacyConsentDb([]),
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "owner_consent_required"


@pytest.mark.asyncio
async def test_legacy_consent_attributes_rows_to_authenticated_owner():
    agency_id = uuid.uuid4()
    auth = _auth_for_agency(agency_id)
    db = _LegacyConsentDb([None, None, None])

    result = await legal_api.legacy_consent(
        agency_id=agency_id,
        body=_legacy_body(),
        request=_post_request(),
        auth=auth,
        db=db,
    )

    assert result["created"] == 3
    assert len(db.added) == 3
    assert auth.user is not None
    assert {row.user_id for row in db.added} == {auth.user.id}


@pytest.mark.asyncio
async def test_consent_pdf_uses_accepted_snapshot_not_current_provider(monkeypatch):
    monkeypatch.setattr(settings, "legal_company_name", "Proveedor Aceptado SL")
    version = current_version("dpa")
    evidence = consent_evidence(
        slug="dpa",
        version=version,
        expected_hash=load_document("dpa", version).hash,
        user_agent="test-agent",
        accepted_text="Acepto",
    )
    accepted = accepted_document_from_evidence(evidence)
    assert accepted is not None

    consent = SimpleNamespace(
        document_version=version,
        evidence=evidence,
        created_at=datetime(2026, 8, 30, tzinfo=UTC),
        ip_address="127.0.0.1",
        user_agent="test-agent",
        user_id=uuid.uuid4(),
    )
    captured = {}

    def fake_render_pdf(**kwargs):
        captured.update(kwargs)
        return b"%PDF-test"

    monkeypatch.setattr(legal_api, "_render_pdf", fake_render_pdf)
    monkeypatch.setattr(settings, "legal_company_name", "Proveedor Posterior SL")

    response = await legal_api.consent_pdf(
        agency_id=uuid.uuid4(),
        scope="dpa",
        version=None,
        db=_ConsentDb(consent),
    )

    assert response.body == b"%PDF-test"
    assert "Proveedor Aceptado SL" in captured["markdown_body"]
    assert "Proveedor Posterior SL" not in captured["markdown_body"]
    assert captured["document_hash"] == accepted.document_hash


@pytest.mark.asyncio
async def test_consent_pdf_refuses_legacy_template_only_hash():
    consent = SimpleNamespace(
        document_version="v2",
        evidence="SHA-256 documento: deadbeef.",
        created_at=datetime(2026, 8, 30, tzinfo=UTC),
        ip_address=None,
        user_agent=None,
        user_id=None,
    )

    with pytest.raises(HTTPException) as exc:
        await legal_api.consent_pdf(
            agency_id=uuid.uuid4(),
            scope="dpa",
            version=None,
            db=_ConsentDb(consent),
        )
    assert exc.value.status_code == 409


def test_render_pdf_escapes_untrusted_consent_metadata(monkeypatch):
    captured: dict[str, str] = {}

    class FakeHTML:
        def __init__(self, *, string: str):
            captured["html"] = string

        def write_pdf(self) -> bytes:
            return b"%PDF-test"

    monkeypatch.setitem(sys.modules, "weasyprint", SimpleNamespace(HTML=FakeHTML))

    result = legal_api._render_pdf(
        title="<script>title()</script>",
        version='v6" onmouseover="alert(1)',
        markdown_body="<img src=x onerror=alert(1)>",
        granted_at=datetime(2026, 8, 30, tzinfo=UTC),
        ip_address="<b>127.0.0.1</b>",
        user_agent="<svg onload=alert(1)>",
        user_id=None,
        document_hash="abc<def",
    )

    assert result == b"%PDF-test"
    html = captured["html"]
    assert "<script>" not in html
    assert "<img src=x" not in html
    assert "<svg onload" not in html
    assert "<b>127.0.0.1</b>" not in html
    assert "&lt;script&gt;title()&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "&lt;svg onload=alert(1)&gt;" in html
