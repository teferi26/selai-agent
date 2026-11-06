"""What `onboard_agency` actually writes to `consent_logs`.

The audit found that the consent loop reused the name `slug`, leaving it bound
to "privacy_policy" and sending that to Vapi as the agency slug. The loop
variable is now `document_slug` (app/services/onboarding.py) and
tests/test_voice_routing.py pins the Vapi half of that fix.

The *other* half had no test at all. Every plausible way of getting the rename
wrong lands here instead of there, and two of them are silent:

  - `versions.get(slug)` -> None -> `continue`, so signup succeeds and not one
    document consent is stored. An RGPD audit trail that is quietly empty is
    worse than a crash.
  - `consent_evidence(slug=slug, ...)` -> the evidence attests a different
    document than the one the scope names.

So these tests read the rows, not the loop: the exact set of scopes, the
version stored against each, and the self-verifying evidence string (title +
SHA-256 of that document's file).

No Postgres: a fake session collects what `db.add()` receives.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.db.models import Agency, ConsentAction, ConsentLog, ConsentScope, User
from app.services import onboarding
from app.services.legal_documents import (
    KNOWN_SLUGS,
    consent_evidence,
    current_versions,
    load_document,
)

AGENCY_NAME = "Inmobiliaria Sol y Mar"
EXPECTED_SLUG = "inmobiliaria-sol-y-mar"
IP = "203.0.113.7"
UA = "Mozilla/5.0 (Macintosh) Safari/605.1"

SCOPE_OF_DOCUMENT = {
    "terms_of_service": ConsentScope.TERMS_OF_SERVICE,
    "dpa": ConsentScope.DPA,
    "privacy_policy": ConsentScope.PRIVACY_POLICY,
}


class _Result:
    """`select(...)` result double: nothing exists, so signup takes every
    happy path (slug free, no plan seeded -> no subscription)."""

    def scalars(self):
        return self

    def first(self):
        return None

    def scalar_one_or_none(self):
        return None


class FakeSession:
    def __init__(self):
        self.added: list = []

    async def execute(self, stmt):
        sql = " ".join(str(stmt).split())
        assert "FROM agencies" in sql or "FROM plans" in sql, sql
        return _Result()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    def of_type(self, model) -> list:
        return [o for o in self.added if isinstance(o, model)]


@pytest.fixture
def no_vapi(monkeypatch):
    """Signup must not reach the network. Returning None is the same thing
    that happens in production without a VAPI_API_KEY."""

    async def _provision(**kwargs):
        return None

    monkeypatch.setattr(onboarding, "_provision_vapi_assistant", _provision)


async def _onboard(db: FakeSession, **overrides) -> SimpleNamespace:
    kwargs = {
        "agency_name": AGENCY_NAME,
        "owner_email": "ana@solymar.example.com",
        "owner_full_name": "Ana Ruiz",
        "owner_phone": None,
        "accepted_versions": current_versions(),
        "accepted_hashes": {
            slug: load_document(slug, version).hash
            for slug, version in current_versions().items()
        },
        "request_ip": IP,
        "request_ua": UA,
    }
    kwargs.update(overrides)
    return await onboarding.onboard_agency(db, **kwargs)


# ---------------------------------------------------------------------------
# The three signed documents
# ---------------------------------------------------------------------------


async def test_the_three_mandatory_consents_are_stored(no_vapi):
    db = FakeSession()

    await _onboard(db)

    document_scopes = {SCOPE_OF_DOCUMENT[s] for s in KNOWN_SLUGS}
    stored = {c.scope for c in db.of_type(ConsentLog)}
    assert document_scopes <= stored, "a signup stored fewer legal consents than it collected"


async def test_each_document_is_stored_at_the_version_that_was_accepted(no_vapi):
    """The version travels from the form to the row unchanged; reading it off
    the wrong key would store the current one and make a stale acceptance look
    fresh."""
    db = FakeSession()
    versions = current_versions()

    await _onboard(db)

    by_scope = {c.scope: c for c in db.of_type(ConsentLog)}
    for document_slug, scope in SCOPE_OF_DOCUMENT.items():
        assert by_scope[scope].document_version == versions[document_slug], document_slug


async def test_the_evidence_attests_the_document_its_scope_names(no_vapi):
    """`consent_evidence` embeds the title and the SHA-256 of the file, so a
    row whose evidence belongs to another document is a forged audit trail —
    exactly what passing the wrong slug into the loop would produce."""
    db = FakeSession()
    versions = current_versions()

    await _onboard(db)

    by_scope = {c.scope: c for c in db.of_type(ConsentLog)}
    for document_slug, scope in SCOPE_OF_DOCUMENT.items():
        expected = consent_evidence(
            slug=document_slug,
            version=versions[document_slug],
            expected_hash=load_document(document_slug, versions[document_slug]).hash,
            user_agent=UA,
            accepted_text=onboarding.DEFAULT_CONSENT_TEXT,
        )
        assert by_scope[scope].evidence == expected, document_slug


async def test_no_two_documents_share_a_hash_in_the_evidence(no_vapi):
    """Guards the assertion above from passing vacuously if the three files
    ever became identical."""
    db = FakeSession()
    versions = current_versions()

    await _onboard(db)

    hashes = {load_document(s, versions[s]).hash for s in KNOWN_SLUGS}
    assert len(hashes) == len(KNOWN_SLUGS)


async def test_a_legal_document_slug_never_ends_up_as_the_agency_slug(no_vapi):
    """The original defect, seen from the agency's side."""
    db = FakeSession()

    await _onboard(db)

    agency = db.of_type(Agency)[0]
    assert agency.slug == EXPECTED_SLUG
    assert agency.slug not in KNOWN_SLUGS


# ---------------------------------------------------------------------------
# The consents that are not documents
# ---------------------------------------------------------------------------


async def test_data_processing_is_always_recorded(no_vapi):
    db = FakeSession()

    await _onboard(db)

    rows = [c for c in db.of_type(ConsentLog) if c.scope is ConsentScope.DATA_PROCESSING]
    assert len(rows) == 1
    # Not a document, so no version and no hash — just the checkbox text.
    assert rows[0].document_version is None
    assert rows[0].evidence == onboarding.DEFAULT_CONSENT_TEXT


async def test_marketing_is_only_recorded_when_it_was_ticked(no_vapi):
    opted_out, opted_in = FakeSession(), FakeSession()

    await _onboard(opted_out, marketing_opt_in=False)
    await _onboard(opted_in, marketing_opt_in=True)

    def marketing(db: FakeSession) -> list:
        return [c for c in db.of_type(ConsentLog) if c.scope is ConsentScope.MARKETING]

    assert marketing(opted_out) == []
    assert len(marketing(opted_in)) == 1


async def test_the_exact_set_of_consents_of_a_plain_signup(no_vapi):
    """Pinned as a set so an extra scope has to be a deliberate change: every
    row here is a legal claim about what the owner agreed to."""
    db = FakeSession()

    await _onboard(db)

    assert {c.scope for c in db.of_type(ConsentLog)} == {
        ConsentScope.TERMS_OF_SERVICE,
        ConsentScope.DPA,
        ConsentScope.PRIVACY_POLICY,
        ConsentScope.DATA_PROCESSING,
    }


# ---------------------------------------------------------------------------
# Everything a consent row needs to be worth anything
# ---------------------------------------------------------------------------


async def test_every_consent_is_attributed_and_timestamped_by_request(no_vapi):
    db = FakeSession()

    result = await _onboard(db)
    owner = db.of_type(User)[0]

    for consent in db.of_type(ConsentLog):
        assert consent.agency_id == result.agency_id, consent.scope
        assert consent.user_id == owner.id, consent.scope
        assert consent.lead_id is None, consent.scope
        assert consent.action is ConsentAction.GRANTED, consent.scope
        assert consent.source == "onboarding", consent.scope
        assert consent.ip_address == IP, consent.scope
        assert consent.user_agent == UA, consent.scope


async def test_a_custom_checkbox_text_is_the_one_stored(no_vapi):
    """What the user actually read is the whole point of the evidence; the
    default is a fallback, not an override."""
    db = FakeSession()
    text = "Acepto los tres documentos legales de selai."

    await _onboard(db, accepted_text=text)

    for consent in db.of_type(ConsentLog):
        if consent.scope is ConsentScope.MARKETING:
            continue
        assert text in consent.evidence, consent.scope
