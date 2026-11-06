"""Dos fugas de datos entre agencias (hallazgos n5 y n6 de la auditoría).

n5 — El registro de consentimiento no comprobaba que el lead perteneciera a
la agencia de la ruta: la agencia A podía escribir filas de consentimiento
sobre leads de la B. Un registro de auditoría contaminable que PARECE completo
es peor que uno vacío — y el gate del marketplace leía esas filas como
válidas para publicar los datos de la persona.

n6 — Con menos de 5 agencias en la muestra, los "percentiles" del benchmark
eran las cifras exactas de competidoras identificables: con una sola otra
agencia, p25 = p50 = p75 = SU número. La página de precios vende "datos
anónimos".
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.benchmarking import MIN_SAMPLE, _blur
from app.core.auth import AuthContext, require_auth
from app.db.session import get_db
from app.main import app

AGENCY_A = uuid.uuid4()
AGENCY_B = uuid.uuid4()
LEAD_OF_B = uuid.uuid4()


# ---------------------------------------------------------------------------
# n5 — consentimiento cross-tenant
# ---------------------------------------------------------------------------


class _Session:
    """El lead existe pero es de la agencia B."""

    def __init__(self):
        self.added: list = []
        self.commits = 0

    async def get(self, model, pk):
        if pk == LEAD_OF_B:
            return SimpleNamespace(id=LEAD_OF_B, agency_id=AGENCY_B)
        return None

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


@pytest.fixture
def http():
    session = _Session()

    async def _db():
        yield session

    def _auth():
        return AuthContext(
            clerk_user_id="user_a",
            email="a@inmoa.example.com",
            raw_claims={},
            user=SimpleNamespace(
                id=uuid.uuid4(), agency_id=AGENCY_A, role=None, is_active=True
            ),
            dev_bypass=True,  # el gate de agencia no es lo que se prueba aquí
        )

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[require_auth] = _auth
    yield TestClient(app), session
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_auth, None)


def test_consent_cannot_be_written_onto_another_agencys_lead(http):
    client, session = http
    resp = client.post(
        f"/gdpr/agencies/{AGENCY_A}/leads/{LEAD_OF_B}/consent",
        json={"scope": "marketing", "action": "granted"},
    )
    assert resp.status_code == 404
    assert session.added == []  # ni una fila escrita
    assert session.commits == 0


# ---------------------------------------------------------------------------
# n6 — percentiles con muestra pequeña
# ---------------------------------------------------------------------------


def test_the_privacy_floor_is_five():
    assert MIN_SAMPLE == 5


def test_blur_rounds_to_two_significant_figures():
    assert _blur(147.0) == 150.0
    assert _blur(8.31) == 8.3
    assert _blur(0.0) == 0.0
    assert _blur(2431.0) == 2400.0
    # Lo que importa: el valor difuminado ya no es la cifra exacta de nadie.
    assert _blur(147.0) != 147.0


def test_a_small_sample_yields_no_percentiles_via_the_endpoint(monkeypatch):
    """Con n=1, los percentiles ERAN el número exacto de la única competidora.

    Por el endpoint real: una sola "otra" agencia con 147 leads. Antes, cada
    percentil devolvía 147.0 — su cifra, identificable. Ahora, null.
    """
    from app.api import benchmarking as mod

    async def _no_tier_gate(db, agency_id, tier):
        return None

    monkeypatch.setattr(mod, "ensure_report_tier", _no_tier_gate)

    rows = [(AGENCY_A, 10), (AGENCY_B, 147)]

    class _Db:
        async def execute(self, stmt):
            class _R:
                def all(self):
                    return rows

            return _R()

    async def _db():
        yield _Db()

    def _auth():
        return AuthContext(
            clerk_user_id="user_a", email="a@inmoa.example.com", raw_claims={},
            user=None, dev_bypass=True,
        )

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[require_auth] = _auth
    try:
        resp = TestClient(app).get(f"/api/v1/agencies/{AGENCY_A}/benchmarks")
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(require_auth, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["benchmarks"], "el endpoint no devolvió ninguna métrica"
    for b in body["benchmarks"]:
        assert b["p25"] is None and b["p50"] is None and b["p75"] is None, b
        assert b["sample_size"] == 1
        # Y en ningún sitio de la respuesta viaja la cifra de la competidora.
        assert 147 not in (b["p25"], b["p50"], b["p75"])
