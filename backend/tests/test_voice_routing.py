"""Voz entrante: a qué agencia pertenece la llamada.

`_resolve_agency` solo miraba `metadata.agency_id` — que ningún asistente ya
creado en Vapi lleva — y, si no lo encontraba, "si hay exactamente una
agencia, esa". Con dos agencias toda llamada entrante se perdía: /voice/llm
contestaba una excusa y los otros tres webhooks hacían `return` mudo, sin
lead, sin transcripción y sin minutos.

La cobertura de esta función era cero, y los tests de voz que ya existen la
parchean para que acierte. Aquí no se parchea: se ejerce la cascada real
contra una sesión falsa que responde exactamente las consultas que hace.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import voice as voice_module
from app.core.settings import settings
from app.db.models import ConversationStatus
from app.db.session import get_db
from app.main import app
from app.scripts import backfill_vapi_metadata as backfill
from app.services import onboarding
from app.services.legal_documents import KNOWN_SLUGS, load_document

# ---------------------------------------------------------------------------
# Dobles
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows: list):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        assert len(self._rows) == 1, f"scalar_one sobre {len(self._rows)} filas"
        return self._rows[0]

    def scalar_one_or_none(self):
        assert len(self._rows) <= 1
        return self._rows[0] if self._rows else None


class FakeAgencyDB:
    """Responde las consultas de la cascada contra agencias en memoria.

    Los valores se leen de los parámetros compilados (`settings_1`, `slug_1`,
    `param_1`) y no del texto SQL, así que si el resolutor empieza a preguntar
    otra cosa el doble falla en alto en vez de mentir.
    """

    def __init__(self, agencies: list, subscription=None, message_count: int = 0):
        self.agencies = list(agencies)
        self.subscription = subscription
        self.message_count = message_count
        self.queries: list[str] = []
        self.added: list = []

    async def execute(self, stmt):
        sql = " ".join(str(stmt).split())
        params = stmt.compile().params
        self.queries.append(sql)

        if "FROM conversations" in sql:
            return _Result([])
        if "FROM subscriptions" in sql:
            return _Result([self.subscription] if self.subscription is not None else [])
        if "FROM messages" in sql:
            return _Result([self.message_count])
        if "FROM agencies" not in sql:
            raise AssertionError(f"consulta inesperada: {sql}")

        if "count(*)" in sql:
            return _Result([len(self.agencies)])
        if "agencies.settings[" in sql:
            key, value = params["settings_1"], params["param_1"]
            return _Result([a.id for a in self.agencies if (a.settings or {}).get(key) == value])
        if "agencies.slug = " in sql:
            return _Result([a.id for a in self.agencies if a.slug == params["slug_1"]])
        if "agencies.id = " in sql:  # comprobación de existencia
            return _Result([a.id for a in self.agencies if a.id == params["id_1"]])
        return _Result(self.agencies)  # select(Agency).limit(1)

    async def get(self, model, pk):
        return None

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def rollback(self):
        pass


class LogRecorder:
    """Sustituye al logger del módulo y guarda evento + nivel + campos."""

    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    def _record(self, level: str):
        def _log(event, **fields):
            self.events.append((level, event, fields))

        return _log

    def __getattr__(self, name):
        return self._record(name)

    def fields_of(self, event: str) -> list[dict]:
        return [fields for _, ev, fields in self.events if ev == event]

    def levels_of(self, event: str) -> list[str]:
        return [level for level, ev, _ in self.events if ev == event]


@pytest.fixture
def logs(monkeypatch):
    recorder = LogRecorder()
    monkeypatch.setattr(voice_module, "log", recorder)
    return recorder


def _agency(slug: str, **agency_settings):
    return SimpleNamespace(id=uuid.uuid4(), slug=slug, settings=dict(agency_settings))


def _call(**fields):
    return {"id": "call_1", "customer": {"number": "+34600111222"}, **fields}


# ---------------------------------------------------------------------------
# call_routing: de dónde sale cada pista
# ---------------------------------------------------------------------------


def test_routing_reads_metadata_and_call_handles():
    routing = voice_module.call_routing(
        {
            "metadata": {"agency_id": "abc", "slug": "inmo-sol"},
            "call": _call(assistantId="asst_1", phoneNumberId="pn_1"),
        }
    )
    assert routing.agency_id == "abc"
    assert routing.slug == "inmo-sol"
    assert routing.assistant_id == "asst_1"
    assert routing.phone_number_id == "pn_1"
    assert routing.call_id == "call_1"


def test_routing_finds_metadata_where_vapi_actually_puts_it():
    """Vapi no manda la metadata del asistente en la misma clave en todos los
    eventos; leer solo `message.metadata` la perdía."""
    from_overrides = voice_module.call_routing(
        {"call": _call(assistantOverrides={"metadata": {"agency_id": "x1"}})}
    )
    assert from_overrides.agency_id == "x1"

    from_assistant = voice_module.call_routing(
        {"call": _call(assistant={"id": "asst_9", "metadata": {"slug": "inmo-luna"}})}
    )
    assert from_assistant.slug == "inmo-luna"
    assert from_assistant.assistant_id == "asst_9"


def test_routing_of_an_empty_payload_is_all_none():
    routing = voice_module.call_routing({})
    assert routing == voice_module.CallRouting()


# ---------------------------------------------------------------------------
# La cascada, estrategia a estrategia (siempre con DOS agencias, que es donde
# el fallback de agencia única deja de tapar el problema)
# ---------------------------------------------------------------------------


async def test_metadata_agency_id_resolves_the_right_agency_of_two():
    uno, dos = _agency("inmo-sol"), _agency("inmo-luna")
    db = FakeAgencyDB([uno, dos])

    routing = voice_module.call_routing({"metadata": {"agency_id": str(dos.id)}})

    assert await voice_module._resolve_agency(db, routing) == dos.id


async def test_metadata_slug_resolves_the_right_agency_of_two():
    """Los asistentes creados antes de que escribiéramos `agency_id` llevan
    solo el slug: esta rama los arregla sin tocar Vapi."""
    uno, dos = _agency("inmo-sol"), _agency("inmo-luna")
    db = FakeAgencyDB([uno, dos])

    routing = voice_module.call_routing({"metadata": {"slug": "inmo-luna"}, "call": _call()})

    assert await voice_module._resolve_agency(db, routing) == dos.id


async def test_assistant_id_resolves_when_there_is_no_usable_metadata():
    uno = _agency("inmo-sol", vapi_assistant_id="asst_sol")
    dos = _agency("inmo-luna", vapi_assistant_id="asst_luna")
    db = FakeAgencyDB([uno, dos])

    routing = voice_module.call_routing({"call": _call(assistantId="asst_luna")})

    assert await voice_module._resolve_agency(db, routing) == dos.id


async def test_phone_number_id_resolves_when_the_assistant_is_unknown():
    uno = _agency("inmo-sol", vapi_phone_number_id="pn_sol")
    dos = _agency("inmo-luna", vapi_phone_number_id="pn_luna")
    db = FakeAgencyDB([uno, dos])

    routing = voice_module.call_routing(
        {"call": _call(assistantId="asst_desconocido", phoneNumberId="pn_luna")}
    )

    assert await voice_module._resolve_agency(db, routing) == dos.id


async def test_metadata_agency_id_beats_a_slug_pointing_elsewhere():
    uno, dos = _agency("inmo-sol"), _agency("inmo-luna")
    db = FakeAgencyDB([uno, dos])

    routing = voice_module.call_routing(
        {"metadata": {"agency_id": str(dos.id), "slug": "inmo-sol"}}
    )

    assert await voice_module._resolve_agency(db, routing) == dos.id


async def test_a_corrupt_agency_id_does_not_shadow_the_other_handles(logs):
    uno, dos = _agency("inmo-sol"), _agency("inmo-luna")
    db = FakeAgencyDB([uno, dos])

    routing = voice_module.call_routing(
        {"metadata": {"agency_id": "no-soy-un-uuid", "slug": "inmo-luna"}}
    )

    assert await voice_module._resolve_agency(db, routing) == dos.id
    assert logs.fields_of("voice.routing.bad_agency_id")


async def test_a_slug_that_matches_nobody_does_not_break_the_routing():
    """El slug que onboarding escribió mal ("privacy_policy") no apunta a
    ninguna agencia: la llamada se enruta igual por el assistantId."""
    uno = _agency("inmo-sol", vapi_assistant_id="asst_sol")
    dos = _agency("inmo-luna", vapi_assistant_id="asst_luna")
    db = FakeAgencyDB([uno, dos])

    routing = voice_module.call_routing(
        {"metadata": {"slug": "privacy_policy"}, "call": _call(assistantId="asst_luna")}
    )

    assert await voice_module._resolve_agency(db, routing) == dos.id


# ---------------------------------------------------------------------------
# Enrutar a la agencia EQUIVOCADA es peor que no enrutar: la llamada de un
# lead acaba en el CRM de un competidor y nadie se entera.
# ---------------------------------------------------------------------------


def _duplicated_assistant_call(other) -> dict:
    """Lo que llega cuando el asistente de una agencia se creó duplicando el
    de otra en el panel de Vapi: la copia arrastra la metadata del original."""
    return {
        "call": _call(
            assistantId="asst_luna",
            phoneNumberId="pn_luna",
            assistant={"metadata": {"agency_id": str(other.id), "slug": other.slug}},
        )
    }


def _two_agencies():
    uno = _agency("inmo-sol", vapi_assistant_id="asst_sol", vapi_phone_number_id="pn_sol")
    dos = _agency("inmo-luna", vapi_assistant_id="asst_luna", vapi_phone_number_id="pn_luna")
    return uno, dos


async def test_metadata_of_a_duplicated_assistant_does_not_steal_the_call(logs):
    """El assistantId lo emite Vapi y lo mapeamos nosotros; la metadata vive
    en el panel de Vapi y se copia al duplicar. Manda nuestro registro."""
    uno, dos = _two_agencies()
    db = FakeAgencyDB([uno, dos])

    routing = voice_module.call_routing(_duplicated_assistant_call(uno))

    assert await voice_module._resolve_agency(db, routing) == dos.id
    conflicts = logs.fields_of("voice.routing.conflict")
    assert logs.levels_of("voice.routing.conflict") == ["error"]
    assert conflicts[0]["metadata_agency_id"] == str(uno.id)
    assert conflicts[0]["resolved_agency_id"] == str(dos.id)


async def test_a_copied_slug_does_not_steal_the_call_either():
    """Mismo escenario cuando la copia solo arrastra el slug."""
    uno, dos = _two_agencies()
    db = FakeAgencyDB([uno, dos])

    routing = voice_module.call_routing(
        {"call": _call(assistantId="asst_luna", assistant={"metadata": {"slug": "inmo-sol"}})}
    )

    assert await voice_module._resolve_agency(db, routing) == dos.id


async def test_an_agency_id_that_is_not_in_this_database_is_not_returned(logs):
    """Un UUID válido de una agencia que aquí no existe (metadata copiada de
    staging, tenant borrado) se devolvía tal cual: todo lo de después fallaba
    contra la clave ajena y NADA registraba la llamada como no enrutada."""
    uno, dos = _two_agencies()
    db = FakeAgencyDB([uno, dos])
    fantasma = uuid.uuid4()

    routing = voice_module.call_routing(
        {"metadata": {"agency_id": str(fantasma)}, "call": _call(assistantId="asst_luna")}
    )

    assert await voice_module._resolve_agency(db, routing) == dos.id
    assert logs.fields_of("voice.routing.unknown_agency_id")[0]["agency_id"] == str(fantasma)


async def test_an_unknown_agency_id_alone_resolves_to_nobody(logs):
    uno, dos = _two_agencies()
    db = FakeAgencyDB([uno, dos])

    routing = voice_module.call_routing({"metadata": {"agency_id": str(uuid.uuid4())}})

    assert await voice_module._resolve_agency(db, routing) is None


async def test_two_agencies_claiming_one_assistant_resolve_to_nobody(logs):
    """Un settings copiado a mano deja el mismo assistantId en dos agencias.
    Elegir la primera fila es jugarse a cara o cruz el CRM del competidor."""
    uno = _agency("inmo-sol", vapi_assistant_id="asst_compartido")
    dos = _agency("inmo-luna", vapi_assistant_id="asst_compartido")
    db = FakeAgencyDB([uno, dos])

    routing = voice_module.call_routing({"call": _call(assistantId="asst_compartido")})

    assert await voice_module._resolve_agency(db, routing) is None
    assert logs.levels_of("voice.routing.ambiguous_handle") == ["error"]
    assert logs.fields_of("voice.routing.ambiguous_handle")[0]["handle"] == "vapi_assistant_id"


async def test_two_agencies_claiming_one_phone_number_resolve_to_nobody(logs):
    uno = _agency("inmo-sol", vapi_phone_number_id="pn_compartido")
    dos = _agency("inmo-luna", vapi_phone_number_id="pn_compartido")
    db = FakeAgencyDB([uno, dos])

    routing = voice_module.call_routing({"call": _call(phoneNumberId="pn_compartido")})

    assert await voice_module._resolve_agency(db, routing) is None
    assert logs.fields_of("voice.routing.ambiguous_handle")[0]["handle"] == "vapi_phone_number_id"


async def test_an_ambiguous_assistant_still_lets_the_phone_number_decide(logs):
    """La ambigüedad tumba esa pista, no toda la llamada: el número, que sí
    es de una sola agencia, sigue valiendo."""
    uno = _agency("inmo-sol", vapi_assistant_id="asst_compartido")
    dos = _agency("inmo-luna", vapi_assistant_id="asst_compartido", vapi_phone_number_id="pn_luna")
    db = FakeAgencyDB([uno, dos])

    routing = voice_module.call_routing(
        {"call": _call(assistantId="asst_compartido", phoneNumberId="pn_luna")}
    )

    assert await voice_module._resolve_agency(db, routing) == dos.id


async def test_assistant_and_phone_number_pointing_at_different_agencies_refuse_to_guess(logs):
    """Nuestros propios registros se contradicen: no hay elección segura."""
    uno = _agency("inmo-sol", vapi_assistant_id="asst_sol", vapi_phone_number_id="pn_sol")
    dos = _agency("inmo-luna", vapi_phone_number_id="pn_luna")
    db = FakeAgencyDB([uno, dos])

    routing = voice_module.call_routing(
        {"call": _call(assistantId="asst_sol", phoneNumberId="pn_luna")}
    )

    assert await voice_module._resolve_agency(db, routing) is None
    assert logs.levels_of("voice.routing.conflict") == ["error"]


async def test_a_legitimate_outbound_call_still_resolves(logs):
    """Regresión de lo que YA funcionaba: la saliente inyecta agency_id por
    llamada y usa el asistente de esa misma agencia (workers/tasks.py)."""
    uno, dos = _two_agencies()
    db = FakeAgencyDB([uno, dos])

    routing = voice_module.call_routing(
        {
            "call": _call(
                assistantId="asst_luna",
                metadata={"agency_id": str(dos.id), "lead_id": "x"},
            )
        }
    )

    assert await voice_module._resolve_agency(db, routing) == dos.id
    assert logs.fields_of("voice.routing.conflict") == []


async def test_nothing_matches_with_two_agencies_is_none():
    db = FakeAgencyDB([_agency("inmo-sol"), _agency("inmo-luna")])

    routing = voice_module.call_routing({"call": _call(assistantId="asst_huerfano")})

    assert await voice_module._resolve_agency(db, routing) is None


async def test_single_agency_fallback_still_works(logs):
    """Regresión: la única estrategia que había antes sigue viva, y sigue
    siendo la última. Deja rastro porque deja de funcionar sola en cuanto
    entre la segunda agencia."""
    sola = _agency("inmo-sol")
    db = FakeAgencyDB([sola])

    assert await voice_module._resolve_agency(db, voice_module.CallRouting()) == sola.id
    assert logs.levels_of("voice.routing.single_agency_fallback") == ["info"]


# ---------------------------------------------------------------------------
# Observabilidad: una llamada sin enrutar deja rastro en los cuatro webhooks
# ---------------------------------------------------------------------------


def _unroutable_message(event_type: str) -> dict:
    return {
        "message": {
            "type": event_type,
            "call": _call(assistantId="asst_huerfano", phoneNumberId="pn_huerfano"),
            "messages": [],
            "durationSeconds": 61,
            "status": "ended",
            "endedReason": "customer-did-not-answer",
        }
    }


@pytest.mark.parametrize(
    ("event_type", "handler_name"),
    [
        ("call-started", "_on_call_start"),
        ("status-update", "_on_status_update"),
        ("end-of-call-report", "_on_call_end"),
    ],
)
async def test_unroutable_webhooks_log_an_error_instead_of_returning_in_silence(
    logs, event_type, handler_name
):
    db = FakeAgencyDB([_agency("inmo-sol"), _agency("inmo-luna")])
    handler = getattr(voice_module, handler_name)

    await handler(db, _unroutable_message(event_type))

    assert logs.levels_of("voice.unrouted_call") == ["error"], (
        f"{event_type} se perdió sin dejar rastro"
    )
    fields = logs.fields_of("voice.unrouted_call")[0]
    # `vapi_event`: structlog ya usa `event` para el nombre del evento, así que
    # este campo no puede llamarse así (el doble lo reproduce y lo detectó).
    assert fields["vapi_event"] == event_type
    assert fields["call_id"] == "call_1"
    assert fields["assistant_id"] == "asst_huerfano"
    assert fields["phone_number_id"] == "pn_huerfano"
    assert db.added == []


async def test_end_of_call_report_routes_by_assistant_id_and_persists(monkeypatch, logs):
    """El arreglo completo: el informe de una llamada real de una agencia con
    vecinas acaba en SU conversación."""
    uno = _agency("inmo-sol", vapi_assistant_id="asst_sol")
    dos = _agency("inmo-luna", vapi_assistant_id="asst_luna")
    db = FakeAgencyDB([uno, dos])
    seen: dict = {}

    async def _resolve_lead(session, agency_id, identity):
        seen["agency_id"] = agency_id
        conv = SimpleNamespace(
            id=uuid.uuid4(), lead_id=uuid.uuid4(), extra={}, status=ConversationStatus.CLOSED
        )
        return SimpleNamespace(id=conv.lead_id, extra={}), conv

    monkeypatch.setattr(voice_module, "resolve_lead_and_conversation", _resolve_lead)

    body = {
        "message": {
            "type": "end-of-call-report",
            "call": _call(assistantId="asst_luna"),
            "messages": [{"role": "user", "message": "Quiero ver el ático."}],
            "durationSeconds": 61,
            "summary": "Pide visita.",
        }
    }
    await voice_module._on_call_end(db, body)

    assert seen["agency_id"] == dos.id
    assert logs.fields_of("voice.unrouted_call") == []
    assert [m.agency_id for m in db.added] == [dos.id]


# ---------------------------------------------------------------------------
# /voice/llm: lo que oye quien llama cuando no sabemos de quién es la llamada
# ---------------------------------------------------------------------------


@pytest.fixture
def voice_client(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")  # salta _verify_vapi
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def test_unroutable_llm_call_does_not_promise_a_callback_nobody_will_make(
    voice_client, monkeypatch, logs
):
    """El texto anterior ("Te llama un agente humano") era imposible: sin
    agencia no hay lead, ni conversación, ni nadie a quien avisar."""
    db = FakeAgencyDB([_agency("inmo-sol"), _agency("inmo-luna")])

    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override

    resp = voice_client.post(
        "/voice/llm",
        json={
            "messages": [{"role": "user", "content": "¿Tenéis pisos en Chamberí?"}],
            "call": _call(assistantId="asst_huerfano"),
        },
    )

    assert resp.status_code == 200
    reply = resp.json()["choices"][0]["message"]["content"]
    assert reply == voice_module.UNROUTED_CALL_REPLY
    # Ni promete que llame nadie ni promete cuándo volverá a funcionar.
    assert "agente" not in reply.lower()
    assert "minutos" not in reply.lower()
    assert logs.levels_of("voice.unrouted_call") == ["error"]


def test_the_webhook_tells_vapi_the_call_was_dropped(voice_client, logs):
    """El cuerpo de nuestra respuesta es lo que Vapi enseña junto a cada
    entrega. Contestar "ok" a una llamada que hemos tirado engaña a quien
    investiga por qué no aparece en el CRM."""
    db = FakeAgencyDB([_agency("inmo-sol"), _agency("inmo-luna")])

    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override

    resp = voice_client.post("/webhooks/voice", json=_unroutable_message("end-of-call-report"))

    assert resp.status_code == 200
    assert resp.json() == {"status": "unrouted"}
    assert logs.levels_of("voice.unrouted_call") == ["error"]


# ---------------------------------------------------------------------------
# Los asistentes nuevos nacen enrutables
# ---------------------------------------------------------------------------


class _FakeVapiClient:
    calls: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        _FakeVapiClient.calls.append({"url": url, "json": json})
        return SimpleNamespace(status_code=200, text="", json=lambda: {"id": "asst_new"})


@pytest.fixture
def vapi(monkeypatch):
    monkeypatch.setattr(settings, "vapi_api_key", SecretStr("vapi-test"))
    monkeypatch.setattr(onboarding.httpx, "AsyncClient", _FakeVapiClient)
    _FakeVapiClient.calls.clear()
    return _FakeVapiClient


async def test_new_assistant_is_created_with_agency_id_in_its_metadata(vapi):
    agency_id = uuid.uuid4()

    assistant_id = await onboarding._provision_vapi_assistant(
        agency_name="Inmo Sol", slug="inmo-sol", agency_id=agency_id
    )

    assert assistant_id == "asst_new"
    assert vapi.calls[0]["json"]["metadata"] == {
        "slug": "inmo-sol",
        "agency_id": str(agency_id),
    }


async def test_an_assistant_created_this_way_resolves_by_metadata(vapi):
    agencia = _agency("inmo-sol")
    otra = _agency("inmo-luna")
    await onboarding._provision_vapi_assistant(
        agency_name="Inmo Sol", slug="inmo-sol", agency_id=agencia.id
    )
    metadata = vapi.calls[0]["json"]["metadata"]

    routing = voice_module.call_routing({"call": _call(assistant={"metadata": metadata})})

    assert await voice_module._resolve_agency(FakeAgencyDB([agencia, otra]), routing) == agencia.id


def _provisioning_calls_without_agency_id() -> set[str]:
    """Ficheros de `app/` que llaman a `_provision_vapi_assistant` sin
    `agency_id=`. Se lee el AST en vez de grepear porque un grep se dejó uno
    (app/api/voice_cloning.py) y el asistente nacía sin agency_id."""
    import ast
    from pathlib import Path

    offenders: set[str] = set()
    app_dir = Path(__file__).resolve().parents[1] / "app"
    for path in app_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name != "_provision_vapi_assistant":
                continue
            if not any(kw.arg == "agency_id" for kw in node.keywords):
                offenders.add(path.name)
    return offenders


def test_no_new_caller_provisions_an_assistant_without_its_agency():
    """channels.py es el único que queda (pista whatsapp, ver informe). Este
    test pasa cuando se arregle y falla si aparece otro."""
    assert _provisioning_calls_without_agency_id() <= {"channels.py"}


async def test_provisioning_without_agency_id_is_logged_as_the_defect_it_is(vapi, monkeypatch):
    """app/api/channels.py todavía llama sin `agency_id`: el asistente sale
    enrutable solo por slug, y eso tiene que verse en los logs."""
    recorder = LogRecorder()
    monkeypatch.setattr(onboarding, "log", recorder)

    await onboarding._provision_vapi_assistant(agency_name="Inmo Sol", slug="inmo-sol")

    assert vapi.calls[0]["json"]["metadata"] == {"slug": "inmo-sol"}
    assert recorder.levels_of("vapi.create_assistant.without_agency_id") == ["warning"]


# ---------------------------------------------------------------------------
# onboard_agency: el slug que llega a Vapi es el de la agencia
# ---------------------------------------------------------------------------


class FakeOnboardSession:
    def __init__(self):
        self.added: list = []

    async def execute(self, stmt):
        sql = " ".join(str(stmt).split())
        if "FROM agencies" in sql:
            return _Result([])  # el slug está libre
        if "FROM plans" in sql:
            return _Result([])  # sin planes sembrados: no se crea suscripción
        raise AssertionError(f"consulta inesperada: {sql}")

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()


async def test_onboarding_sends_the_agency_slug_and_id_not_a_legal_document_slug(monkeypatch):
    """Regresión: el bucle de consentimientos reutilizaba la variable `slug`,
    así que el asistente se creaba con `{"slug": "privacy_policy"}` y ninguna
    llamada entrante podía enrutarse por metadata."""
    captured: dict = {}

    async def _provision(**kwargs):
        captured.update(kwargs)
        return "asst_new"

    monkeypatch.setattr(onboarding, "_provision_vapi_assistant", _provision)
    db = FakeOnboardSession()

    result = await onboarding.onboard_agency(
        db,
        agency_name="Inmo Sol",
        owner_email="owner@inmosol.example.com",
        owner_full_name="Ana Ruiz",
        owner_phone=None,
        accepted_versions={"terms_of_service": "v1", "dpa": "v1", "privacy_policy": "v1"},
        accepted_hashes={
            slug: load_document(slug, "v1").hash for slug in KNOWN_SLUGS
        },
    )

    assert captured["slug"] == "inmo-sol"
    assert captured["slug"] not in KNOWN_SLUGS
    assert captured["agency_id"] == result.agency_id


# ---------------------------------------------------------------------------
# Backfill de metadata en Vapi
# ---------------------------------------------------------------------------


def test_backfill_adds_agency_id_without_dropping_other_keys():
    current = {"slug": "privacy_policy", "creado_por": "onboarding"}

    wanted = backfill.desired_metadata(current, agency_id="a-1", slug="inmo-sol")

    assert wanted == {"slug": "inmo-sol", "agency_id": "a-1", "creado_por": "onboarding"}


def test_backfill_is_idempotent():
    """Segunda pasada: el resultado no cambia, así que el script no escribe."""
    once = backfill.desired_metadata({"slug": "inmo-sol"}, agency_id="a-1", slug="inmo-sol")
    twice = backfill.desired_metadata(once, agency_id="a-1", slug="inmo-sol")

    assert twice == once


def test_backfill_refuses_to_stamp_over_another_agencys_id():
    """Una metadata que ya nombra a OTRA agencia es la huella de un asistente
    duplicado: escribir encima convierte el desvío en permanente."""
    current = {"agency_id": "a-otra", "slug": "inmo-sol"}

    assert backfill.conflicting_agency_id(current, agency_id="a-1") == "a-otra"
    assert backfill.conflicting_agency_id({"slug": "x"}, agency_id="a-1") is None
    assert backfill.conflicting_agency_id({"agency_id": "a-1"}, agency_id="a-1") is None


def test_backfill_spots_an_assistant_claimed_by_two_agencies():
    uno, dos = _agency("inmo-sol"), _agency("inmo-luna")
    tres = _agency("inmo-mar")

    shared = backfill.assistants_claimed_by_several(
        [(uno, "asst_compartido"), (dos, "asst_compartido"), (tres, "asst_propio")]
    )

    assert shared == {"asst_compartido": ["inmo-sol", "inmo-luna"]}


async def test_backfill_without_api_key_does_nothing_and_says_so(monkeypatch, capsys):
    monkeypatch.setattr(settings, "vapi_api_key", SecretStr(""))

    code = await backfill.run()

    assert code == 1
    assert "VAPI_API_KEY" in capsys.readouterr().out


class _FakeVapiAPI:
    """Vapi de mentira con la metadata de cada asistente en memoria.

    `honours_patch=False` imita a una API que acepta el PATCH y no guarda lo
    que le mandas: el script tiene que verlo y no cantar "actualizado".
    """

    def __init__(self, store: dict, *, honours_patch: bool = True):
        self.store = store
        self.honours_patch = honours_patch
        self.patched: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def _resp(self, assistant_id: str):
        return SimpleNamespace(
            status_code=200,
            text="",
            json=lambda: {"id": assistant_id, "metadata": self.store[assistant_id]},
        )

    async def get(self, url):
        return self._resp(url.rsplit("/", 1)[-1])

    async def patch(self, url, json=None):
        assistant_id = url.rsplit("/", 1)[-1]
        self.patched.append(assistant_id)
        if self.honours_patch:
            self.store[assistant_id] = json["metadata"]
        return self._resp(assistant_id)


class _FakeEngine:
    async def dispose(self):
        pass


class _FakeBackfillSession:
    def __init__(self, agencies):
        self.agencies = agencies

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, stmt):
        return _Result(self.agencies)


@pytest.fixture
def backfill_world(monkeypatch):
    def _install(agencies, api: _FakeVapiAPI) -> _FakeVapiAPI:
        monkeypatch.setattr(settings, "vapi_api_key", SecretStr("vapi-test"))
        monkeypatch.setattr(backfill, "create_async_engine", lambda *a, **k: _FakeEngine())
        monkeypatch.setattr(
            backfill,
            "async_sessionmaker",
            lambda *a, **k: (lambda: _FakeBackfillSession(agencies)),
        )
        monkeypatch.setattr(backfill.httpx, "AsyncClient", lambda *a, **k: api)
        return api

    return _install


async def test_backfill_fixes_the_slug_keeps_the_rest_and_is_idempotent(backfill_world, capsys):
    sol = _agency("inmo-sol", vapi_assistant_id="asst_sol")
    api = backfill_world(
        [sol], _FakeVapiAPI({"asst_sol": {"slug": "privacy_policy", "creado_por": "onboarding"}})
    )

    assert await backfill.run() == 0
    assert api.store["asst_sol"] == {
        "slug": "inmo-sol",
        "agency_id": str(sol.id),
        "creado_por": "onboarding",
    }

    capsys.readouterr()
    assert await backfill.run() == 0  # segunda pasada
    assert api.patched == ["asst_sol"]  # no ha vuelto a escribir
    assert "ya está al día" in capsys.readouterr().out


async def test_backfill_does_not_touch_an_assistant_two_agencies_claim(backfill_world, capsys):
    sol = _agency("inmo-sol", vapi_assistant_id="asst_compartido")
    luna = _agency("inmo-luna", vapi_assistant_id="asst_compartido")
    api = backfill_world([sol, luna], _FakeVapiAPI({"asst_compartido": {"slug": "inmo-sol"}}))

    code = await backfill.run()

    assert code == 1, "salir 0 diría que el backfill quedó hecho"
    assert api.patched == []
    assert "SIN TOCAR" in capsys.readouterr().out


async def test_backfill_does_not_stamp_over_another_agencys_id(backfill_world, capsys):
    otra_id = str(uuid.uuid4())
    sol = _agency("inmo-sol", vapi_assistant_id="asst_sol")
    api = backfill_world([sol], _FakeVapiAPI({"asst_sol": {"agency_id": otra_id}}))

    assert await backfill.run() == 1
    assert api.store["asst_sol"] == {"agency_id": otra_id}
    assert "SIN TOCAR" in capsys.readouterr().out

    # --force es la vía consciente, para cuando alguien ha comprobado de quién
    # es el asistente.
    assert await backfill.run(force=True) == 0
    assert api.store["asst_sol"]["agency_id"] == str(sol.id)


async def test_backfill_does_not_claim_success_if_vapi_did_not_store_it(backfill_world, capsys):
    sol = _agency("inmo-sol", vapi_assistant_id="asst_sol")
    backfill_world([sol], _FakeVapiAPI({"asst_sol": {"slug": "inmo-sol"}}, honours_patch=False))

    code = await backfill.run()

    out = capsys.readouterr().out
    assert code == 1
    assert "guardó otra cosa" in out
    assert "1 actualizados" not in out
