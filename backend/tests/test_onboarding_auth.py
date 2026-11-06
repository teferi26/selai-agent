"""El alta exige identidad verificada (hallazgo n3 de la auditoría).

`POST /api/v1/onboarding` era público y se fiaba del cuerpo: cualquiera podía
crear una agencia para cualquier email y atar al owner un `clerk_user_id`
AJENO. Como `require_auth` resuelve al usuario primero por `clerk_user_id` y
después por email, eso secuestraba la cuenta de esa persona en su primer
login: aterrizaba dentro de una agencia que no era suya.

Estos tests fijan el cierre: sin sesión no hay alta; con sesión, el email del
alta tiene que ser el de la sesión; y la identidad persistida es la del token,
nunca la del cuerpo (el campo ya ni existe en el schema).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.auth import AuthContext, require_auth
from app.core.settings import settings
from app.db.session import get_db
from app.main import app
from app.services.legal_documents import current_versions


def _body(email: str = "ana@solymar.example.com", versions: dict | None = None) -> dict:
    return {
        "agency_name": "Inmobiliaria Sol y Mar",
        "owner_email": email,
        "owner_full_name": "Ana Pérez",
        "accepted_terms": True,
        "accepted_dpa": True,
        "accepted_privacy": True,
        # Por defecto versiones VACÍAS a propósito: si la petición supera el
        # control de identidad, choca con el 422 de stale_consent antes de
        # tocar la base de datos. Ese 422 es la prueba de "identidad aceptada"
        # sin necesidad de montar una sesión real.
        "accepted_versions": versions or {},
    }


@pytest.fixture
def http():
    async def _no_db():
        yield None

    app.dependency_overrides[get_db] = _no_db
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_auth, None)


def _as_session(email, *, dev_bypass=False):
    def _override():
        return AuthContext(
            clerk_user_id="user_sesion_real",
            email=email,
            raw_claims={},
            user=None,
            dev_bypass=dev_bypass,
        )

    app.dependency_overrides[require_auth] = _override


def test_without_a_session_there_is_no_signup(http, monkeypatch):
    # Con Clerk configurado (como en producción) el atajo de desarrollo de
    # require_auth no aplica y una petición sin Bearer muere en 401.
    monkeypatch.setattr(settings, "clerk_jwks_url", "https://clerk.selai.es/jwks.json")
    resp = http.post("/api/v1/onboarding", json=_body())
    assert resp.status_code == 401


def test_the_signup_email_must_be_the_session_email(http):
    _as_session("otra-persona@mail.example.com")
    resp = http.post("/api/v1/onboarding", json=_body("ana@solymar.example.com"))
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "email_mismatch"


def test_a_session_without_email_claim_cannot_sign_up(http):
    _as_session(None)
    resp = http.post("/api/v1/onboarding", json=_body())
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "email_claim_missing"


def test_matching_email_passes_identity_and_reaches_consent_check(http):
    _as_session("Ana@SolyMar.example.com")  # distinto casing: debe dar igual
    resp = http.post("/api/v1/onboarding", json=_body("ana@solymar.example.com"))
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "stale_consent"


def test_current_versions_without_displayed_hashes_are_still_rejected(http):
    _as_session("ana@solymar.example.com")
    resp = http.post(
        "/api/v1/onboarding",
        json=_body("ana@solymar.example.com", versions=current_versions()),
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "stale_consent"
    assert all(document["sent_hash"] == "" for document in detail["documents"])


def test_dev_bypass_still_works_for_local_and_tests(http):
    _as_session(None, dev_bypass=True)
    resp = http.post("/api/v1/onboarding", json=_body())
    assert resp.status_code == 422  # stale_consent: superó la identidad
    assert resp.json()["detail"]["code"] == "stale_consent"


def test_the_body_can_no_longer_carry_a_clerk_identity(http):
    """El campo se eliminó del schema; un cliente viejo que lo mande no debe
    romperse (se ignora), pero JAMÁS debe llegar al servicio."""
    from app.api.onboarding import OnboardingBody

    body = OnboardingBody.model_validate(
        {**_body(versions=current_versions()), "clerk_user_id": "user_de_otra_persona"}
    )
    assert not hasattr(body, "clerk_user_id")
