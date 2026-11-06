"""Revisión adversarial de la pista ESCALA Y EVENTOS.

Tres cosas que la implementación anterior daba por buenas y no lo eran:

1. **El inbox repetía conversaciones.** La consulta agrupaba por conversación,
   se quedaba con `max(created_at)` y volvía a unir contra `messages` por ese
   timestamp. `Message.created_at` es `server_default=func.now()` y el `now()`
   de Postgres es la hora de *la transacción*: el mensaje del lead y la
   respuesta del agente, escritos en el mismo turno y en la misma transacción
   (`task_session`, sin commit intermedio), llevan el mismo microsegundo. El
   join devolvía los dos. No era un caso raro: era el caso normal.

2. **Los eventos de lead se perdían para siempre.** La guarda anti-backfill
   fechaba el hecho con `Lead.updated_at`, que es `onupdate=func.now()`. Eso
   significa dos cosas malas a la vez: mientras la transacción no confirma,
   el valor en Python es el de la escritura *anterior* (un lead que estuvo
   callado una semana y cualifica ahora parece de hace una semana), y en
   cuanto alguna tool hace `flush()` la columna queda **expirada**, así que
   leerla desde una `AsyncSession` lanza `MissingGreenlet` — que el
   `try/except` de `_announce_crm_events` se tragaba entera.

3. **/estado preguntaba a `https://api.selai.es` desde el navegador.** Fuera
   de producción comprobaba la salud de otro backend, y cualquier fallo de
   CORS pintaba "Incidencia" con todo funcionando.

Y el export, que ningún test ejecutaba de verdad (los que había sustituían
`iter_export_chunks` por una lista).
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import (
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    create_engine,
    desc,
    insert,
    select,
)
from sqlalchemy.exc import MissingGreenlet

from app.api.dashboard import EXPORT_CHUNK, iter_export_chunks, latest_message_rank
from app.core.settings import settings
from app.db.models import LeadStatus, LeadTemperature, VisitStatus
from app.db.session import get_db
from app.main import app
from app.services import event_dispatcher, lead_scoring
from app.services.event_dispatcher import (
    FIRST_SIGHT_KEY,
    emit_lead_qualified,
    emit_visit_scheduled,
    is_crm_backfill,
)

AGENCY_ID = uuid.uuid4()
NOW = datetime.now(UTC)
T0 = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# 1. El inbox devuelve una fila por conversación
# ---------------------------------------------------------------------------


def _messages_table(rows: list[tuple[str, str, datetime]]):
    """Tabla `(id, conversation_id, created_at)` en SQLite con `rows` dentro.

    No es la tabla real (sus columnas UUID/JSON son de Postgres). Lo que se
    ejecuta aquí es el álgebra del ranking, que es idéntica en los dos motores.
    """
    metadata = MetaData()
    table = Table(
        "messages",
        metadata,
        Column("id", String, nullable=False),
        Column("conversation_id", String, nullable=False),
        Column("created_at", DateTime, nullable=False),
    )
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            insert(table),
            [{"id": i, "conversation_id": c, "created_at": ts} for i, c, ts in rows],
        )
    return engine, table


def _latest_rows(engine, table) -> list[tuple[str, str]]:
    """`(conversation_id, message_id)` del mensaje que representa cada
    conversación, con la misma expresión que usa el endpoint."""
    ranked = select(
        table.c.id.label("message_id"),
        table.c.conversation_id.label("conversation_id"),
        table.c.created_at.label("last_at"),
        latest_message_rank(table.c.conversation_id, table.c.created_at, table.c.id).label("rn"),
    ).subquery()
    stmt = (
        select(ranked.c.conversation_id, ranked.c.message_id)
        .where(ranked.c.rn == 1)
        .order_by(desc(ranked.c.last_at), desc(ranked.c.conversation_id))
    )
    with engine.connect() as conn:
        return [(r.conversation_id, r.message_id) for r in conn.execute(stmt).all()]


def _one_turn(conv: str, ts: datetime) -> list[tuple[str, str, datetime]]:
    """Un turno: mensaje entrante + respuesta, misma transacción, mismo
    `created_at` al microsegundo. Es lo que escribe `handle_inbound`."""
    return [
        (f"{conv}-in", conv, ts),
        (f"{conv}-out", conv, ts),
    ]


def test_a_conversation_appears_once_even_when_its_last_two_messages_tie():
    rows = _one_turn("c1", T0) + _one_turn("c2", T0 - timedelta(minutes=5))

    latest = _latest_rows(*_messages_table(rows))

    assert [conv for conv, _ in latest] == ["c2", "c1"] or [
        conv for conv, _ in latest
    ] == ["c1", "c2"]
    assert len({conv for conv, _ in latest}) == 2, "una conversación salió dos veces"
    assert len(latest) == 2


def test_the_old_max_join_did_return_the_conversation_twice():
    """El fallo que se arregla, ejecutado. Si alguien vuelve a la formulación
    con `max(created_at)` sin desempate, esto describe lo que pasaría."""
    from sqlalchemy import func

    engine, table = _messages_table(_one_turn("c1", T0))
    sub = (
        select(table.c.conversation_id, func.max(table.c.created_at).label("last_at"))
        .group_by(table.c.conversation_id)
        .subquery()
    )
    stmt = select(table.c.id).join(
        sub,
        (sub.c.conversation_id == table.c.conversation_id)
        & (table.c.created_at == sub.c.last_at),
    )
    with engine.connect() as conn:
        duplicated = conn.execute(stmt).all()

    assert len(duplicated) == 2  # el bug
    assert len(_latest_rows(engine, table)) == 1  # el arreglo


def test_a_page_of_n_conversations_holds_n_distinct_conversations():
    """Con el join antiguo una página de 20 traía 10 conversaciones y `has_more`
    contaba los duplicados, así que "Cargar más" saltaba filas."""
    rows: list[tuple[str, str, datetime]] = []
    for i in range(20):
        rows += _one_turn(f"c{i:02d}", T0 - timedelta(minutes=i))

    latest = _latest_rows(*_messages_table(rows))

    assert len(latest) == 20
    assert len({conv for conv, _ in latest}) == 20


def test_the_message_that_represents_a_conversation_is_the_agent_reply():
    """De los dos empatados gana el de id mayor, siempre el mismo. Antes lo
    elegía el planificador, así que la vista previa del inbox podía cambiar
    entre dos recargas sin que pasara nada."""
    engine, table = _messages_table(
        [
            ("m-aaa", "c1", T0),
            ("m-zzz", "c1", T0),
            ("m-mmm", "c1", T0 - timedelta(hours=1)),
        ]
    )

    assert _latest_rows(engine, table) == [("c1", "m-zzz")]


# ---------------------------------------------------------------------------
# El endpoint sigue en pie
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, payload):
        self._payload = payload

    def all(self):
        return list(self._payload)

    def scalars(self):
        return self

    def scalar_one(self):
        return self._payload


class FakeSession:
    def __init__(self, *results):
        self.results = list(results)
        self.statements: list = []

    def queue(self, *results):
        self.results = list(results)

    async def execute(self, stmt):
        self.statements.append(stmt)
        payload = self.results.pop(0) if len(self.results) > 1 else (self.results or [[]])[0]
        return _FakeResult(payload)

    async def get(self, model, pk):
        return None

    async def commit(self):
        pass

    async def rollback(self):
        pass


@pytest.fixture
def db():
    session = FakeSession([])

    async def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    yield session
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client(monkeypatch, db):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")
    return TestClient(app)


def test_the_inbox_query_no_longer_groups_by_max_created_at(client, db):
    db.queue([])

    client.get(f"/api/v1/agencies/{AGENCY_ID}/inbox")

    sql = str(db.statements[-1])
    assert "row_number() OVER (PARTITION BY messages.conversation_id" in sql
    assert "max(messages.created_at)" not in sql


def test_a_cursor_can_only_narrow_a_listing_never_widen_it(client, db):
    """El cursor no va firmado, y no hace falta: lo que decide qué filas se
    pueden ver es el `agency_id` del path, que `AgencyMemberDep` ya autorizó.
    Un cursor manipulado sólo puede recortar. Este test fija esa propiedad —
    si alguien mueve el filtro de agencia detrás del cursor, falla."""
    from app.api.dashboard import encode_cursor

    forged = encode_cursor(AGENCY_ID, [T0.isoformat(), str(uuid.uuid4())])
    db.queue([], 0)

    resp = client.get(f"/api/v1/agencies/{AGENCY_ID}/leads", params={"cursor": forged})

    assert resp.status_code == 200
    sql = str(db.statements[0])
    assert "leads.agency_id = " in sql
    assert "leads.created_at < " in sql and "leads.id < " in sql


# ---------------------------------------------------------------------------
# 2. Los eventos de lead ya no dependen de `updated_at`
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, payload):
        self._payload = payload

    def all(self):
        return list(self._payload)

    def scalars(self):
        return self


class FakeDB:
    def __init__(self, *results, rows: dict | None = None):
        self.results = list(results)
        self.rows = rows or {}

    async def execute(self, stmt):
        return _Result(self.results.pop(0) if self.results else [])

    async def get(self, model, pk):
        return self.rows.get(pk)

    async def commit(self):
        pass


@pytest.fixture
def sent(monkeypatch) -> list[tuple[str, uuid.UUID, dict]]:
    captured: list[tuple[str, uuid.UUID, dict]] = []

    async def _emit(db, event, agency_id, payload):
        captured.append((event, agency_id, payload))

    monkeypatch.setattr(event_dispatcher, "emit", _emit)
    return captured


def _events(sent) -> list[str]:
    return [event for event, _agency, _payload in sent]


def _lead(
    *,
    status: LeadStatus = LeadStatus.NEW,
    temperature: LeadTemperature = LeadTemperature.COLD,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    seen: bool = False,
) -> SimpleNamespace:
    extra: dict = {}
    if seen:
        extra["emitted_events"] = {FIRST_SIGHT_KEY: NOW.isoformat()}
    return SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=AGENCY_ID,
        full_name="Ana Gómez",
        phone_e164="+34600222333",
        email="ana@example.com",
        status=status,
        temperature=temperature,
        score=61,
        budget_min=180_000,
        budget_max=260_000,
        operation="sale",
        preferred_cities=["Valencia"],
        preferred_neighborhoods=None,
        min_bedrooms=2,
        must_haves=None,
        interested_property_ids=None,
        qualification_notes="",
        extra=extra,
        created_at=created_at or NOW,
        updated_at=updated_at or NOW,
    )


async def test_a_qualification_after_a_week_of_silence_still_reaches_the_crm(sent):
    """El caso que se perdía. `updated_at` es la escritura anterior mientras la
    transacción no confirma, así que un lead que llevaba una semana callado y
    cualifica en este turno parecía de hace una semana — se marcaba como visto
    y no se anunciaba nunca más."""
    lead = _lead(
        status=LeadStatus.QUALIFIED,
        created_at=NOW - timedelta(days=40),
        updated_at=NOW - timedelta(days=7),
        seen=True,
    )
    db = FakeDB([], [], rows={lead.id: lead})

    await lead_scoring.recompute_lead_score(db, lead.id)

    assert "lead.qualified" in _events(sent)


async def test_a_temperature_jump_on_a_dormant_lead_is_announced(sent):
    lead = _lead(
        temperature=LeadTemperature.COLD,
        created_at=NOW - timedelta(days=90),
        updated_at=NOW - timedelta(days=30),
        seen=True,
    )
    # Una visita agendada vale 20 puntos y calienta al lead en este turno.
    visit = SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=AGENCY_ID,
        lead_id=lead.id,
        property_id=uuid.uuid4(),
        scheduled_at=NOW + timedelta(days=2),
        status=VisitStatus.CONFIRMED,
        post_visit_notes="",
        extra={},
        created_at=NOW,
        updated_at=NOW,
    )
    db = FakeDB([visit], [], rows={lead.id: lead})

    await lead_scoring.recompute_lead_score(db, lead.id)

    assert "lead.temperature_changed" in _events(sent)


async def test_an_expired_timestamp_column_never_silences_an_event(sent):
    """Tras un `flush()`, `created_at`/`updated_at` quedan expiradas y leerlas
    en una AsyncSession lanza MissingGreenlet. Antes eso reventaba dentro del
    `try/except` de `_announce_crm_events` y se perdían los cuatro eventos del
    turno con una línea de log."""

    class _Expired(SimpleNamespace):
        @property
        def updated_at(self):
            raise MissingGreenlet("greenlet_spawn has not been called")

        @property
        def created_at(self):
            raise MissingGreenlet("greenlet_spawn has not been called")

    lead = _Expired(**{
        k: v
        for k, v in vars(_lead(status=LeadStatus.QUALIFIED)).items()
        if k not in ("created_at", "updated_at")
    })
    db = FakeDB([], [], rows={lead.id: lead})

    await lead_scoring.recompute_lead_score(db, lead.id)

    assert "lead.qualified" in _events(sent)


async def test_a_visit_whose_created_at_is_expired_is_still_announced(sent):
    """La visita que acaba de crear `schedule_visit` en este mismo turno: la
    columna está expirada, y "no sé cuándo fue" no puede significar "cállate"."""

    class _Expired(SimpleNamespace):
        @property
        def created_at(self):
            raise MissingGreenlet("greenlet_spawn has not been called")

    visit = _Expired(
        id=uuid.uuid4(),
        agency_id=AGENCY_ID,
        lead_id=uuid.uuid4(),
        property_id=uuid.uuid4(),
        scheduled_at=NOW + timedelta(days=1),
        status=VisitStatus.CONFIRMED,
        post_visit_notes="",
        extra={},
    )

    assert await emit_visit_scheduled(FakeDB(), visit, max_age=timedelta(hours=24)) is True


# --- y la protección anti-backfill sigue en pie -----------------------------


async def test_an_old_lead_seen_for_the_first_time_is_backfill(sent):
    """El día del despliegue: leads que ya existían y ya estaban cualificados.
    Anunciarlos duplicaría media base de datos en el CRM del cliente."""
    lead = _lead(status=LeadStatus.QUALIFIED, created_at=NOW - timedelta(days=30))
    db = FakeDB([], [], rows={lead.id: lead})

    await lead_scoring.recompute_lead_score(db, lead.id)

    assert sent == []
    assert FIRST_SIGHT_KEY in lead.extra["emitted_events"]


async def test_a_brand_new_lead_is_never_treated_as_backfill(sent):
    lead = _lead(status=LeadStatus.QUALIFIED, created_at=NOW - timedelta(minutes=3))
    db = FakeDB([], [], rows={lead.id: lead})

    await lead_scoring.recompute_lead_score(db, lead.id)

    assert "lead.qualified" in _events(sent)


async def test_the_sight_stamp_is_written_even_when_there_is_nothing_to_say(sent):
    """Un lead frío y sin cualificar no produce ningún evento en su primer
    turno. Si no dejáramos la marca seguiría contando como "nunca visto" y el
    día que cualifique lo trataríamos como historia."""
    lead = _lead(created_at=NOW - timedelta(days=10))
    db = FakeDB([], [], rows={lead.id: lead})

    await lead_scoring.recompute_lead_score(db, lead.id)
    assert sent == []
    assert FIRST_SIGHT_KEY in lead.extra["emitted_events"]

    lead.status = LeadStatus.QUALIFIED
    await lead_scoring.recompute_lead_score(FakeDB([], [], rows={lead.id: lead}), lead.id)

    assert "lead.qualified" in _events(sent)


async def test_backfill_is_decided_before_the_stamp_is_written():
    lead = _lead(status=LeadStatus.QUALIFIED, created_at=NOW - timedelta(days=30))

    assert is_crm_backfill(lead, max_age=timedelta(hours=24)) is True
    event_dispatcher.mark_crm_sight(lead)
    # Ya visto: a partir de aquí todo lo que cambie es noticia.
    assert is_crm_backfill(lead, max_age=timedelta(hours=24)) is False


async def test_backfill_still_never_fires_twice(sent):
    """Silenciar no puede dejar el hecho pendiente: queda marcado."""
    lead = _lead(status=LeadStatus.QUALIFIED, created_at=NOW - timedelta(days=30))

    assert await emit_lead_qualified(FakeDB(), lead, backfill=True) is False
    assert await emit_lead_qualified(FakeDB(), lead) is False
    assert sent == []


async def test_old_visits_are_still_not_replayed(sent):
    visit = SimpleNamespace(
        id=uuid.uuid4(),
        agency_id=AGENCY_ID,
        lead_id=uuid.uuid4(),
        property_id=uuid.uuid4(),
        scheduled_at=NOW - timedelta(days=25),
        status=VisitStatus.CONFIRMED,
        post_visit_notes="",
        extra={},
        created_at=NOW - timedelta(days=30),
        updated_at=NOW - timedelta(days=30),
    )

    assert await emit_visit_scheduled(FakeDB(), visit, max_age=timedelta(hours=24)) is False
    assert sent == []


# ---------------------------------------------------------------------------
# 3. El export, ejecutado de verdad
# ---------------------------------------------------------------------------


class _RecordingSession:
    """Sirve leads en trozos y guarda cada sentencia que recibe."""

    def __init__(self, leads: list[SimpleNamespace]):
        self.leads = leads
        self.statements: list[str] = []
        self.cursor = 0
        self.max_live_rows = 0

    async def execute(self, stmt):
        self.statements.append(str(stmt))
        window = self.leads[self.cursor : self.cursor + EXPORT_CHUNK]
        self.cursor += len(window)
        self.max_live_rows = max(self.max_live_rows, len(window))
        return _Result(window)


def _export_session(monkeypatch, leads):
    from contextlib import asynccontextmanager

    session = _RecordingSession(leads)

    @asynccontextmanager
    async def _scope():
        yield session

    import app.db.session as session_module

    monkeypatch.setattr(session_module, "session_scope", _scope)
    return session


def _export_lead(i: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        full_name=f"Lead {i}",
        created_at=T0 - timedelta(minutes=i),
    )


async def _drain(agency_id) -> list[SimpleNamespace]:
    out: list[SimpleNamespace] = []
    async for chunk in iter_export_chunks(agency_id, None, None):
        out.extend(chunk)
    return out


async def test_the_export_walks_the_whole_table_in_bounded_chunks(monkeypatch):
    """El generador real, no un doble. 50.000 leads salen enteros y el servidor
    nunca tiene más de un trozo en la mano."""
    leads = [_export_lead(i) for i in range(EXPORT_CHUNK * 3 + 7)]
    session = _export_session(monkeypatch, leads)

    drained = await _drain(AGENCY_ID)

    assert [lead.full_name for lead in drained] == [lead.full_name for lead in leads]
    assert session.max_live_rows <= EXPORT_CHUNK


async def test_the_export_stops_instead_of_looping_forever(monkeypatch):
    """Un trozo corto termina el recorrido; un trozo exacto pide otro y para
    con el vacío. Sin esto un export de 500 leads justos gira sin fin."""
    for total in (0, 1, EXPORT_CHUNK, EXPORT_CHUNK * 2):
        session = _export_session(monkeypatch, [_export_lead(i) for i in range(total)])
        drained = await _drain(AGENCY_ID)
        assert len(drained) == total
        assert len(session.statements) <= total // EXPORT_CHUNK + 2


async def test_every_export_query_is_scoped_to_the_agency(monkeypatch):
    """El export corre en una sesión propia y *después* de que la respuesta
    haya empezado. La autorización ya pasó, pero el filtro de inquilino tiene
    que ir en cada trozo, no sólo en el primero."""
    session = _export_session(monkeypatch, [_export_lead(i) for i in range(EXPORT_CHUNK + 3)])

    await _drain(AGENCY_ID)

    assert len(session.statements) >= 2
    assert all("leads.agency_id = " in sql for sql in session.statements)


async def test_the_export_pages_with_the_keyset_and_never_with_offset(monkeypatch):
    session = _export_session(monkeypatch, [_export_lead(i) for i in range(EXPORT_CHUNK + 1)])

    await _drain(AGENCY_ID)

    assert len(session.statements) >= 2
    assert all("OFFSET" not in sql for sql in session.statements)
    # A partir del segundo trozo la consulta arrastra el cursor, con su
    # desempate por id: sin él los leads empatados en created_at se pierden.
    later = session.statements[1]
    assert "leads.created_at <" in later
    assert "leads.id <" in later


# ---------------------------------------------------------------------------
# 4. /estado: mismo origen, y las claves que lee existen
# ---------------------------------------------------------------------------

_ESTADO_DIR = (
    Path(__file__).resolve().parents[2] / "dashboard" / "src" / "app" / "(app)" / "estado"
)
# The page was split when /estado went public (15): `page.tsx` is the server
# half that decides whether the viewer has a session, and `EstadoClient.tsx`
# is the half that actually polls readiness. The fetch — which is what these
# two tests are about — lives in the client file.
ESTADO_PAGE = _ESTADO_DIR / "EstadoClient.tsx"
ESTADO_SERVER_PAGE = _ESTADO_DIR / "page.tsx"


@pytest.mark.skipif(not ESTADO_PAGE.exists(), reason="el panel no está en este árbol")
def test_estado_asks_its_own_backend_not_a_hardcoded_domain():
    """Una URL absoluta aquí significa dos cosas malas: en local y en pruebas
    informa de la salud de producción, y depende de que CORS incluya el
    dominio desde el que se sirve el panel — si no, "Incidencia" con todo bien."""
    source = ESTADO_PAGE.read_text(encoding="utf-8")
    url = re.search(r'const READY_URL = "([^"]+)"', source)

    assert url is not None
    assert url.group(1).startswith("/"), "la comprobación tiene que ir por el proxy del panel"
    code = source.split('"use client"', 1)[1]
    assert "https://api." not in code
    # La mitad de servidor tampoco puede colarse un dominio absoluto: el
    # reparto entre los dos ficheros no debe abrir una puerta trasera.
    if ESTADO_SERVER_PAGE.exists():
        server = ESTADO_SERVER_PAGE.read_text(encoding="utf-8")
        assert "https://api." not in server


@pytest.mark.skipif(not ESTADO_PAGE.exists(), reason="el panel no está en este árbol")
async def test_estado_reads_only_fields_that_readyz_actually_returns(monkeypatch):
    """Si el backend deja de mandar una clave, la página la pinta como
    "No se ha podido comprobar" sin que nadie se entere. Este test ata las dos
    listas."""
    from fastapi import Response

    from app.api import health

    async def _redis_ok():
        return True

    async def _celery_ok():
        return True

    monkeypatch.setattr(health, "_check_redis", _redis_ok)
    monkeypatch.setattr(health, "_check_celery", _celery_ok)

    class _DB:
        async def execute(self, stmt):
            return None

    body = await health.readiness(Response(), _DB())

    source = ESTADO_PAGE.read_text(encoding="utf-8")
    declared = set(
        re.findall(
            r"^\s*(\w+):",
            source.split("type Readiness = {", 1)[1].split("};", 1)[0],
            flags=re.MULTILINE,
        )
    )

    assert declared, "no se ha podido leer el tipo Readiness"
    assert declared <= set(body), f"la página lee claves que /readyz no manda: {declared - set(body)}"
