"""`save_lead_info` escribe SOLO los campos de su contrato (hallazgo n7).

El bucle antiguo hacía `setattr(lead, campo, valor)` para cualquier atributo
existente. Los argumentos los decide el modelo, y el modelo lee lo que el lead
escribe: un lead con labia (o con una inyección de prompt) podía acabar
poniendo `consent_marketing=true` sin consentir nada, `status=won`,
`score=100`, `extra={...}` o — el peor — `agency_id` de otra agencia,
moviendo su ficha de tenant por chat.

La allowlist hace que el radio de daño sea exactamente lo que el schema de la
tool promete, emita lo que emita el modelo.
"""
from __future__ import annotations

import uuid

import pytest

from app.agent.tools import _save_lead_info
from app.db.models import Lead, LeadStatus

pytestmark = pytest.mark.anyio


class _Db:
    def __init__(self, lead):
        self._lead = lead

    async def get(self, model, pk):
        return self._lead

    async def flush(self):
        pass


def _lead(**overrides) -> Lead:
    values = {
        "agency_id": uuid.uuid4(),
        "status": LeadStatus.NEW,
        "qualification_notes": "",
        "score": 0,
        "consent_marketing": False,
        "language": "es",
        "extra": {},
    }
    values.update(overrides)
    return Lead(**values)


def _ctx(lead):
    from types import SimpleNamespace

    return SimpleNamespace(
        db=_Db(lead),
        agency_id=lead.agency_id,
        lead_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        channel="whatsapp",
    )


async def test_columns_outside_the_contract_never_persist():
    agency = uuid.uuid4()
    lead = _lead(agency_id=agency)
    out = await _save_lead_info(
        {
            # Legítimos:
            "full_name": "Lucía Martín",
            "budget_max": 250000,
            # El ataque — todos existen como columnas de Lead:
            "consent_marketing": True,
            "status": "won",
            "score": 100,
            "temperature": "hot",
            "agency_id": str(uuid.uuid4()),
            "extra": {"inyectado": True},
            "source_channel": "hackeado",
        },
        _ctx(lead),
    )
    assert out.startswith("OK")
    assert lead.full_name == "Lucía Martín"
    assert lead.budget_max == 250000
    assert lead.consent_marketing is False
    assert lead.score == 0
    assert lead.agency_id == agency
    assert lead.extra == {}
    assert lead.source_channel is None
    # El único cambio de status es la transición legítima NEW -> ENGAGED.
    assert lead.status == LeadStatus.ENGAGED


async def test_money_as_text_is_coerced_not_crashed():
    lead = _lead()
    await _save_lead_info({"budget_max": "320.000 €", "min_bedrooms": "3"}, _ctx(lead))
    assert lead.budget_max == 320000
    assert lead.min_bedrooms == 3


async def test_an_uncoercible_int_is_dropped_not_written():
    lead = _lead()
    await _save_lead_info({"budget_max": "no lo sé aún"}, _ctx(lead))
    assert lead.budget_max is None


async def test_operation_only_accepts_the_two_real_values():
    lead = _lead()
    await _save_lead_info({"operation": "SALE"}, _ctx(lead))
    assert lead.operation == "sale"
    await _save_lead_info({"operation": "regalo'; DROP TABLE"}, _ctx(lead))
    assert lead.operation == "sale"  # sigue como estaba


async def test_notes_still_append():
    lead = _lead(qualification_notes="Primera nota.")
    await _save_lead_info(
        {"qualification_notes_append": "Busca terraza."}, _ctx(lead)
    )
    assert lead.qualification_notes == "Primera nota.\nBusca terraza."
