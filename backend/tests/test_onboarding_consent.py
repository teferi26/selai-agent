"""Validation tests for the onboarding endpoint's body model.

The end-to-end signup → ConsentLog flow is verified manually via curl in
the README; these tests cover the Pydantic guards that reject malformed
requests before any DB work happens.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.onboarding import OnboardingBody
from app.services.legal_documents import load_document


def _valid_payload(**overrides):
    payload = {
        "agency_name": "Inmobiliaria Test",
        "owner_email": "owner@test.es",
        "owner_full_name": "Test Owner",
        "accepted_terms": True,
        "accepted_dpa": True,
        "accepted_privacy": True,
        "accepted_versions": {
            "terms_of_service": "v1",
            "dpa": "v1",
            "privacy_policy": "v1",
        },
        "accepted_hashes": {
            "terms_of_service": load_document("terms_of_service", "v1").hash,
            "dpa": load_document("dpa", "v1").hash,
            "privacy_policy": load_document("privacy_policy", "v1").hash,
        },
    }
    payload.update(overrides)
    return payload


def test_valid_payload_passes():
    body = OnboardingBody(**_valid_payload())
    assert body.accepted_terms is True
    assert body.marketing_opt_in is False  # default


def test_marketing_opt_in_optional():
    body = OnboardingBody(**_valid_payload(marketing_opt_in=True))
    assert body.marketing_opt_in is True


def test_missing_terms_rejected():
    with pytest.raises(ValidationError) as exc_info:
        OnboardingBody(**_valid_payload(accepted_terms=False))
    msg = str(exc_info.value)
    assert "accepted_terms" in msg


def test_missing_dpa_rejected():
    with pytest.raises(ValidationError) as exc_info:
        OnboardingBody(**_valid_payload(accepted_dpa=False))
    assert "accepted_dpa" in str(exc_info.value)


def test_missing_privacy_rejected():
    with pytest.raises(ValidationError) as exc_info:
        OnboardingBody(**_valid_payload(accepted_privacy=False))
    assert "accepted_privacy" in str(exc_info.value)


def test_all_three_missing_lists_all_three():
    with pytest.raises(ValidationError) as exc_info:
        OnboardingBody(
            **_valid_payload(
                accepted_terms=False, accepted_dpa=False, accepted_privacy=False
            )
        )
    msg = str(exc_info.value)
    assert "accepted_terms" in msg
    assert "accepted_dpa" in msg
    assert "accepted_privacy" in msg


def test_invalid_email_rejected():
    with pytest.raises(ValidationError):
        OnboardingBody(**_valid_payload(owner_email="not-an-email"))


def test_short_agency_name_rejected():
    with pytest.raises(ValidationError):
        OnboardingBody(**_valid_payload(agency_name="X"))


def test_default_when_consents_omitted():
    """Omitting checkbox fields entirely should also fail (defaults are False)."""
    base = _valid_payload()
    base.pop("accepted_terms")
    base.pop("accepted_dpa")
    base.pop("accepted_privacy")
    with pytest.raises(ValidationError):
        OnboardingBody(**base)


def test_accepted_versions_optional_in_body_but_required_for_endpoint():
    # The Pydantic model lets `accepted_versions` default to {}; the endpoint
    # logic separately enforces the version match. This test just confirms
    # the body model itself allows the empty case so we can return a clean
    # 422 with `code=stale_consent` instead of a Pydantic decode error.
    body = OnboardingBody(**_valid_payload(accepted_versions={}))
    assert body.accepted_versions == {}


def test_accepted_hashes_optional_in_body_but_required_for_endpoint():
    body = OnboardingBody(**_valid_payload(accepted_hashes={}))
    assert body.accepted_hashes == {}
