"""Solo owner/admin tocan el dinero (hallazgo n15).

`UserRole` existía desde la primera migración y ninguno de los tres endpoints
de facturación lo comprobaba: cualquier comercial dado de alta en el equipo
podía contratar un plan, subir a Agencia anual (cargo de 3.830 EUR en la
tarjeta de la agencia) o cancelar la suscripción entera desde un curl.

El 403 es tipado (`role_required`) para que el panel esconda los botones en
vez de enseñar un error crudo.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.auth import AuthContext, require_auth
from app.db.models.user import UserRole
from app.db.session import get_db
from app.main import app

AGENCY_ID = uuid.uuid4()


def _as_member(role: UserRole):
    def _override():
        return AuthContext(
            clerk_user_id="user_comercial",
            email="comercial@inmosol.example.com",
            raw_claims={},
            user=SimpleNamespace(
                id=uuid.uuid4(),
                agency_id=AGENCY_ID,
                role=role,
                is_active=True,
            ),
            dev_bypass=False,
        )

    app.dependency_overrides[require_auth] = _override


@pytest.fixture
def http():
    async def _no_db():
        yield None

    app.dependency_overrides[get_db] = _no_db
    # raise_server_exceptions=False: los tests de owner/admin dejan pasar la
    # petición hasta la lógica real, que sin base de datos revienta en 500.
    # Ese 500 ES el resultado esperado allí — lo que se afirma es que el gate
    # de rol no corta (no 403), no que el endpoint funcione sin DB.
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_auth, None)


RUTAS = [
    (
        "/api/v1/billing/checkout",
        {
            "agency_id": str(AGENCY_ID),
            "plan_code": "pro",
            "success_url": "https://www.selai.es/billing?ok=1",
            "cancel_url": "https://www.selai.es/billing",
        },
    ),
    (
        "/api/v1/billing/change-plan",
        {"agency_id": str(AGENCY_ID), "plan_code": "agencia"},
    ),
    (
        "/api/v1/billing/portal",
        {"agency_id": str(AGENCY_ID), "return_url": "https://www.selai.es/billing"},
    ),
]


@pytest.mark.parametrize("ruta,body", RUTAS)
def test_an_agent_gets_a_typed_403_and_stripe_is_never_called(http, ruta, body):
    _as_member(UserRole.AGENT)
    resp = http.post(ruta, json=body)
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "role_required"


@pytest.mark.parametrize("rol", [UserRole.OWNER, UserRole.ADMIN])
@pytest.mark.parametrize("ruta,body", RUTAS)
def test_owner_and_admin_pass_the_role_gate(http, ruta, body, rol):
    """No 403 de rol: la petición sigue hasta la lógica del endpoint (que con
    la base de datos anulada fallará DESPUÉS del control de rol, y eso es
    exactamente lo que se comprueba: el gate no corta a quien sí puede)."""
    _as_member(rol)
    resp = http.post(ruta, json=body)
    assert resp.status_code != 403, resp.text


# ---------------------------------------------------------------------------
# (n4) El marketplace no es un escaparate gratuito
# ---------------------------------------------------------------------------
#
# Una cuenta de Clerk valida SIN fila en users (registrado sin completar el
# alta, o expulsado de un equipo) llegaba al listado de ofertas: leads reales
# de agencias de pago, gratis, sin agencia y sin plan.


def test_a_bare_account_cannot_browse_the_marketplace(http):
    def _override():
        return AuthContext(
            clerk_user_id="user_sin_alta",
            email="curioso@mail.example.com",
            raw_claims={},
            user=None,       # sin fila en users -> sin agencia
            dev_bypass=False,
        )

    app.dependency_overrides[require_auth] = _override
    resp = http.get("/api/v1/marketplace/offers")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "not_onboarded"
