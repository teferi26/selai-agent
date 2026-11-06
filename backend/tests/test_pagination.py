"""Keyset pagination for /inbox and /leads, and the CSV export that used to
lie about how many leads it contained.

Three levels, because there is no Postgres in this suite:

* the keyset predicate itself runs for real, against a throwaway SQLite table
  built with the same SQLAlchemy expression the endpoints use, so the
  "no overlaps, no gaps when timestamps tie" claim is executed rather than
  argued;
* the cursor codec is exercised directly (round trip, another agency's
  cursor, garbage);
* the endpoints run through TestClient with a fake session, which is what
  pins the compatibility promise: no `paginated`, no `cursor` → the same bare
  array the mobile app and the panel have always parsed.
"""
from __future__ import annotations

import base64
import csv
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

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

from app.api import dashboard as dashboard_module
from app.api.dashboard import (
    CSV_HEADER,
    CSV_SEPARATOR,
    TEMPERATURE_RANK,
    UTF8_BOM,
    decode_cursor,
    encode_cursor,
    keyset_before,
    lead_csv_row,
)
from app.core.settings import settings
from app.db.models import ConversationChannel, ConversationStatus, LeadTemperature, MessageRole
from app.db.session import get_db
from app.main import app

AGENCY_ID = uuid.uuid4()
OTHER_AGENCY_ID = uuid.uuid4()

T0 = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# The keyset predicate, executed for real
# ---------------------------------------------------------------------------


def _sqlite_table_with(rows: list[tuple[datetime, str]]) -> tuple[object, Table]:
    """A two-column table holding `rows`, on an in-memory SQLite engine.

    Not the real `leads` table: its UUID/JSON columns are Postgres-only. What
    matters here is the ordering algebra — `(created_at, id)` descending with
    ties — and that is identical on both engines, because canonical UUID text
    sorts the same way Postgres sorts the uuid type.
    """
    metadata = MetaData()
    table = Table(
        "rows",
        metadata,
        Column("created_at", DateTime, nullable=False),
        Column("id", String, nullable=False),
    )
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(table), [{"created_at": ts, "id": rid} for ts, rid in rows])
    return engine, table


def _all_pages(engine, table, page_size: int) -> list[str]:
    """Walk the whole table with the cursor, exactly as the endpoint does."""
    seen: list[str] = []
    last: tuple[datetime, str] | None = None
    with engine.connect() as conn:
        while True:
            stmt = select(table.c.created_at, table.c.id)
            if last is not None:
                # The endpoint passes a uuid.UUID here; SQLite stores the id
                # as text, so the test passes the same value as a string.
                stmt = stmt.where(keyset_before(table.c.created_at, table.c.id, last[0], last[1]))
            stmt = stmt.order_by(desc(table.c.created_at), desc(table.c.id)).limit(page_size)
            page = conn.execute(stmt).all()
            if not page:
                return seen
            seen.extend(row.id for row in page)
            last = (page[-1].created_at, page[-1].id)
            if len(page) < page_size:
                return seen


def _tied_rows() -> list[tuple[datetime, str]]:
    """30 rows where 20 of them share only two distinct timestamps.

    A cursor that only remembers `created_at` loses (or repeats) every row in
    those two blocks, which is exactly the case a batch import produces.
    """
    rows: list[tuple[datetime, str]] = [(T0, str(uuid.uuid4())) for _ in range(10)]
    rows += [(T0 - timedelta(minutes=5), str(uuid.uuid4())) for _ in range(10)]
    rows += [(T0 - timedelta(hours=i + 1), str(uuid.uuid4())) for i in range(10)]
    return rows


def test_cursor_pages_cover_every_row_exactly_once_with_ties():
    rows = _tied_rows()
    engine, table = _sqlite_table_with(rows)

    for page_size in (1, 3, 7, 30, 100):
        seen = _all_pages(engine, table, page_size)
        assert len(seen) == len(rows), f"page_size={page_size} lost or duplicated rows"
        assert len(set(seen)) == len(rows), f"page_size={page_size} returned a row twice"
        assert set(seen) == {rid for _, rid in rows}


def test_cursor_pages_preserve_the_unpaginated_order():
    rows = _tied_rows()
    engine, table = _sqlite_table_with(rows)

    with engine.connect() as conn:
        one_shot = [
            r.id
            for r in conn.execute(
                select(table.c.id).order_by(desc(table.c.created_at), desc(table.c.id))
            ).all()
        ]

    assert _all_pages(engine, table, 7) == one_shot


def test_keyset_boundary_row_is_not_returned_again():
    """The cursor row itself must be excluded — strictly `<`, never `<=`."""
    rows = _tied_rows()
    engine, table = _sqlite_table_with(rows)

    with engine.connect() as conn:
        first = conn.execute(
            select(table.c.created_at, table.c.id)
            .order_by(desc(table.c.created_at), desc(table.c.id))
            .limit(1)
        ).one()
        rest = conn.execute(
            select(table.c.id).where(
                keyset_before(table.c.created_at, table.c.id, first.created_at, first.id)
            )
        ).all()

    assert first.id not in {r.id for r in rest}
    assert len(rest) == len(rows) - 1


# ---------------------------------------------------------------------------
# The cursor codec
# ---------------------------------------------------------------------------


def test_cursor_round_trip():
    key = [T0.isoformat(), str(uuid.uuid4())]
    cursor = encode_cursor(AGENCY_ID, key)

    assert decode_cursor(cursor, AGENCY_ID, expected_len=2) == key


def test_cursor_is_url_safe_and_unpadded():
    cursor = encode_cursor(AGENCY_ID, [T0.isoformat(), str(uuid.uuid4())])

    assert "=" not in cursor
    assert "+" not in cursor and "/" not in cursor


def test_a_cursor_from_another_agency_is_rejected():
    cursor = encode_cursor(OTHER_AGENCY_ID, [T0.isoformat(), str(uuid.uuid4())])

    with pytest.raises(Exception) as exc:
        decode_cursor(cursor, AGENCY_ID, expected_len=2)
    assert exc.value.status_code == 400
    assert exc.value.detail == "invalid_cursor"


def test_cross_agency_cursor_is_rejected_by_the_endpoint(client, db):
    cursor = encode_cursor(OTHER_AGENCY_ID, [T0.isoformat(), str(uuid.uuid4())])

    resp = client.get(f"/api/v1/agencies/{AGENCY_ID}/leads", params={"cursor": cursor})

    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_cursor"


@pytest.mark.parametrize(
    "cursor",
    [
        "not-base64-at-all!!",
        base64.urlsafe_b64encode(b"[]").decode().rstrip("="),
        base64.urlsafe_b64encode(b'{"v":999,"a":"x","k":[]}').decode().rstrip("="),
        base64.urlsafe_b64encode(b"not json").decode().rstrip("="),
    ],
)
def test_garbage_cursors_are_400_not_500(cursor):
    with pytest.raises(Exception) as exc:
        decode_cursor(cursor, AGENCY_ID, expected_len=2)
    assert exc.value.status_code == 400


def test_a_cursor_with_the_wrong_shape_is_rejected():
    raw = json.dumps({"v": 1, "a": str(AGENCY_ID), "k": [T0.isoformat()]}).encode()
    cursor = base64.urlsafe_b64encode(raw).decode().rstrip("=")

    with pytest.raises(Exception) as exc:
        decode_cursor(cursor, AGENCY_ID, expected_len=2)
    assert exc.value.status_code == 400


def test_temperature_rank_matches_the_sql_case():
    """The cursor carries the rank; the query recomputes it in SQL. If the two
    ever disagree the inbox silently skips a whole temperature band."""
    assert TEMPERATURE_RANK[LeadTemperature.HOT] == 0
    assert TEMPERATURE_RANK[LeadTemperature.WARM] == 1
    assert dashboard_module._temperature_rank(LeadTemperature.COLD) == 2


# ---------------------------------------------------------------------------
# Endpoint harness
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
    """Hands back canned results in order; the last one repeats."""

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


def _lead_row(created_at: datetime, name: str = "Juan Pérez") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        full_name=name,
        phone_e164="+34600111222",
        email="juan@example.com",
        status="new",
        temperature="warm",
        score=42,
        language="es",
        budget_min=200000,
        budget_max=300000,
        operation="sale",
        source_channel="whatsapp",
        created_at=created_at,
    )


def _inbox_row(last_at: datetime, temperature: LeadTemperature = LeadTemperature.WARM):
    conv = SimpleNamespace(
        id=uuid.uuid4(),
        channel=ConversationChannel.WHATSAPP,
        status=ConversationStatus.ACTIVE,
        assigned_user_id=None,
    )
    lead = SimpleNamespace(id=uuid.uuid4(), full_name="Juan", temperature=temperature)
    msg = SimpleNamespace(role=MessageRole.LEAD, content="Hola")
    return (conv, lead, msg, last_at)


# ---------------------------------------------------------------------------
# Compatibility: the old callers must not notice anything
# ---------------------------------------------------------------------------


def test_leads_without_pagination_still_returns_a_bare_array(client, db):
    db.queue([_lead_row(T0), _lead_row(T0 - timedelta(hours=1))])

    resp = client.get(f"/api/v1/agencies/{AGENCY_ID}/leads")

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 2


def test_inbox_without_pagination_still_returns_a_bare_array(client, db):
    db.queue([_inbox_row(T0), _inbox_row(T0 - timedelta(minutes=1))])

    resp = client.get(f"/api/v1/agencies/{AGENCY_ID}/inbox")

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 2
    assert set(body[0]) >= {"conversation_id", "lead_name", "last_message_at"}


def test_the_bare_array_never_exceeds_the_requested_limit(client, db):
    # The query asks for limit+1 to detect a next page; that extra row must
    # not leak into the response of a caller who did not ask to paginate.
    db.queue([_lead_row(T0 - timedelta(hours=i)) for i in range(4)])

    resp = client.get(f"/api/v1/agencies/{AGENCY_ID}/leads", params={"limit": 3})

    assert len(resp.json()) == 3


# ---------------------------------------------------------------------------
# Paginated mode
# ---------------------------------------------------------------------------


def test_leads_paginated_returns_envelope_with_cursor_and_total(client, db):
    rows = [_lead_row(T0 - timedelta(hours=i)) for i in range(4)]
    db.queue(rows, 137)

    resp = client.get(
        f"/api/v1/agencies/{AGENCY_ID}/leads", params={"paginated": "true", "limit": 3}
    )

    body = resp.json()
    assert len(body["items"]) == 3
    assert body["total"] == 137
    assert body["next_cursor"]
    # The cursor points at the last row *returned*, not at the probe row.
    key = decode_cursor(body["next_cursor"], AGENCY_ID, expected_len=2)
    assert key[1] == str(rows[2].id)


def test_last_page_has_no_next_cursor(client, db):
    db.queue([_lead_row(T0), _lead_row(T0 - timedelta(hours=1))], 2)

    resp = client.get(
        f"/api/v1/agencies/{AGENCY_ID}/leads", params={"paginated": "true", "limit": 10}
    )

    body = resp.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"] is None


def test_an_empty_listing_paginates_without_a_cursor(client, db):
    db.queue([], 0)

    resp = client.get(f"/api/v1/agencies/{AGENCY_ID}/leads", params={"paginated": "true"})

    assert resp.json() == {"items": [], "next_cursor": None, "total": 0}


def test_sending_a_cursor_switches_to_the_envelope_without_the_flag(client, db):
    rows = [_lead_row(T0 - timedelta(hours=i)) for i in range(2)]
    cursor = encode_cursor(AGENCY_ID, [T0.isoformat(), str(uuid.uuid4())])
    db.queue(rows, 2)

    resp = client.get(f"/api/v1/agencies/{AGENCY_ID}/leads", params={"cursor": cursor})

    assert resp.status_code == 200
    assert "items" in resp.json()


def test_inbox_paginated_cursor_carries_the_temperature_rank(client, db):
    rows = [
        _inbox_row(T0, LeadTemperature.HOT),
        _inbox_row(T0 - timedelta(minutes=1), LeadTemperature.HOT),
        _inbox_row(T0 - timedelta(minutes=2), LeadTemperature.COLD),
    ]
    db.queue(rows)

    resp = client.get(
        f"/api/v1/agencies/{AGENCY_ID}/inbox", params={"paginated": "true", "limit": 2}
    )

    body = resp.json()
    assert len(body["items"]) == 2
    rank, ts, conv_id = decode_cursor(body["next_cursor"], AGENCY_ID, expected_len=3)
    assert rank == 0  # both returned rows are hot
    assert conv_id == str(rows[1][0].id)
    assert ts.startswith("2026-07-31")


def test_inbox_filters_survive_pagination(client, db):
    db.queue([_inbox_row(T0)])

    resp = client.get(
        f"/api/v1/agencies/{AGENCY_ID}/inbox",
        params={"paginated": "true", "channel": "whatsapp", "q": "Juan"},
    )

    assert resp.status_code == 200
    sql = str(db.statements[-1])
    assert "conversations.channel" in sql
    assert "lower(leads.full_name) LIKE lower" in sql


def test_inbox_orders_by_conversation_id_as_the_final_tie_break(client, db):
    db.queue([_inbox_row(T0)])

    client.get(f"/api/v1/agencies/{AGENCY_ID}/inbox", params={"paginated": "true"})

    sql = str(db.statements[-1])
    # `rsplit`, not `split`: the "one row per conversation" window carries an
    # ORDER BY of its own inside the subquery and it renders first. The one
    # the cursor depends on is the listing's, which is the last.
    order_by = sql.rsplit("ORDER BY", 1)[1]
    assert order_by.strip().split("\n")[0].endswith("last_at DESC, conversations.id DESC")


# ---------------------------------------------------------------------------
# CSV export — all of them
# ---------------------------------------------------------------------------


TOTAL_LEADS = 250
CHUNK = 100


def _fake_export_chunks(monkeypatch, leads: list[SimpleNamespace], chunk: int = CHUNK):
    async def _chunks(agency_id, temperature, status):
        for start in range(0, len(leads), chunk):
            yield leads[start : start + chunk]

    monkeypatch.setattr(dashboard_module, "iter_export_chunks", _chunks)


def _csv_lines(body: str) -> list[str]:
    assert body.startswith(UTF8_BOM)
    return [line for line in body[len(UTF8_BOM) :].split("\r\n") if line]


def test_export_contains_every_lead_exactly_once(client, monkeypatch):
    leads = [_lead_row(T0 - timedelta(minutes=i), name=f"Lead {i}") for i in range(TOTAL_LEADS)]
    _fake_export_chunks(monkeypatch, leads)

    resp = client.get(f"/api/v1/agencies/{AGENCY_ID}/leads.csv")

    assert resp.status_code == 200
    lines = _csv_lines(resp.text)
    assert lines[0] == CSV_SEPARATOR.join(CSV_HEADER)
    data = lines[1:]
    assert len(data) == TOTAL_LEADS
    names = [line.split(CSV_SEPARATOR)[0] for line in data]
    assert len(set(names)) == TOTAL_LEADS
    assert names[0] == "Lead 0" and names[-1] == f"Lead {TOTAL_LEADS - 1}"


def test_export_row_count_matches_the_total_the_panel_shows(client, db, monkeypatch):
    """The number on the button and the number of rows in the file are the
    same number, which is the whole point of the change."""
    leads = [_lead_row(T0 - timedelta(minutes=i), name=f"Lead {i}") for i in range(TOTAL_LEADS)]
    db.queue(leads[:10], TOTAL_LEADS)
    page = client.get(
        f"/api/v1/agencies/{AGENCY_ID}/leads", params={"paginated": "true", "limit": 10}
    ).json()

    _fake_export_chunks(monkeypatch, leads)
    exported = _csv_lines(client.get(f"/api/v1/agencies/{AGENCY_ID}/leads.csv").text)[1:]

    assert page["total"] == len(exported) == TOTAL_LEADS


def test_export_is_a_download_with_the_right_content_type(client, monkeypatch):
    _fake_export_chunks(monkeypatch, [_lead_row(T0)])

    resp = client.get(f"/api/v1/agencies/{AGENCY_ID}/leads.csv")

    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    assert ".csv" in resp.headers["content-disposition"]


def test_export_of_an_empty_agency_is_a_header_only_file(client, monkeypatch):
    _fake_export_chunks(monkeypatch, [])

    resp = client.get(f"/api/v1/agencies/{AGENCY_ID}/leads.csv")

    assert _csv_lines(resp.text) == [CSV_SEPARATOR.join(CSV_HEADER)]


def test_csv_cells_with_separators_and_quotes_are_escaped():
    lead = _lead_row(T0, name='Pérez; "El Rápido"')

    row = lead_csv_row(lead)

    assert row[0] == '"Pérez; ""El Rápido"""'
    # A quoted cell must not split the line when re-read.
    assert CSV_SEPARATOR.join(row).count(CSV_SEPARATOR) >= len(CSV_HEADER) - 1


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("=HYPERLINK(\"https://evil.test\")", "'=HYPERLINK(\"https://evil.test\")"),
        ("+SUM(1,1)", "'+SUM(1,1)"),
        ("-2+3", "'-2+3"),
        ("@SUM(1,1)", "'@SUM(1,1)"),
        ("  =1+1", "'  =1+1"),
    ],
)
def test_csv_neutralizes_spreadsheet_formulas(name, expected):
    row = lead_csv_row(_lead_row(T0, name=name))

    # Decode the CSV quoting used for commas/quotes before asserting the cell
    # value. The leading apostrophe is what forces spreadsheets to keep text.
    decoded = next(csv.reader([row[0]], delimiter=CSV_SEPARATOR))[0]
    assert decoded == expected


def test_csv_keeps_international_phone_numbers_as_text():
    row = dict(zip(CSV_HEADER, lead_csv_row(_lead_row(T0)), strict=True))

    assert row["telefono"] == "'+34600111222"


def test_csv_reports_the_real_source_channel_not_the_operation():
    lead = _lead_row(T0)
    lead.source_channel = "idealista"
    lead.operation = "rent"

    row = dict(zip(CSV_HEADER, lead_csv_row(lead), strict=True))

    assert row["fuente"] == "idealista"
    assert row["operacion"] == "rent"


def test_csv_dates_are_rendered_in_the_agency_timezone():
    """A lead captured at 01:30 in Madrid belongs to that day, not to the
    previous one — the same reason the agenda carries a timezone."""
    lead = _lead_row(datetime(2026, 7, 30, 23, 30, tzinfo=UTC))

    utc_row = dict(zip(CSV_HEADER, lead_csv_row(lead), strict=True))
    madrid_row = dict(
        zip(CSV_HEADER, lead_csv_row(lead, ZoneInfo("Europe/Madrid")), strict=True)
    )

    assert utc_row["fecha_alta"] == "2026-07-30"
    assert madrid_row["fecha_alta"] == "2026-07-31"


def test_csv_survives_a_lead_with_everything_empty():
    lead = _lead_row(T0)
    for field in ("full_name", "phone_e164", "email", "budget_min", "budget_max", "operation"):
        setattr(lead, field, None)
    lead.source_channel = None

    row = dict(zip(CSV_HEADER, lead_csv_row(lead), strict=True))

    assert row["nombre"] == ""
    assert row["presupuesto_max"] == ""
    assert row["fecha_alta"] == "2026-07-31"
