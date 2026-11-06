"""(49) Calls listing — flattening voice conversations into call rows, and
the Vapi first-message recording notice (50)."""
from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

from app.api.dashboard import _call_entries
from app.services.onboarding import RECORDING_NOTICE_PREFIX, build_first_message


def _conv(extra: dict | None = None, external_thread_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        extra=extra or {},
        external_thread_id=external_thread_id,
    )


def _lead(extra: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        full_name="Juan Pérez",
        phone_e164="+34600111222",
        extra=extra or {},
    )


def test_flattens_call_records_from_conversation_extra():
    conv = _conv(
        extra={
            "calls": [
                {
                    "call_id": "vapi-1",
                    "ended_at": "2026-07-20T10:00:00+00:00",
                    "duration_seconds": 95,
                    "recording_url": "https://vapi.example/rec1.mp3",
                    "summary": "Pidió visitar el piso de Chamberí.",
                },
                {
                    "call_id": "vapi-2",
                    "ended_at": "2026-07-21T16:30:00+00:00",
                    "duration_seconds": 40.5,
                    "recording_url": None,
                    "summary": None,
                },
            ]
        }
    )
    lead = _lead()

    calls = _call_entries(conv, lead)

    assert len(calls) == 2
    first = calls[0]
    assert first.conversation_id == conv.id
    assert first.lead_id == lead.id
    assert first.lead_name == "Juan Pérez"
    assert first.lead_phone == "+34600111222"
    assert first.call_id == "vapi-1"
    assert first.ended_at == datetime.fromisoformat("2026-07-20T10:00:00+00:00")
    assert first.duration_seconds == 95
    assert first.recording_url == "https://vapi.example/rec1.mp3"
    assert first.transcript_summary == "Pidió visitar el piso de Chamberí."
    assert calls[1].duration_seconds == 40.5


def test_falls_back_to_legacy_lead_extra_snapshot():
    conv = _conv(external_thread_id="call-legacy")
    lead = _lead(
        extra={
            "last_call_summary": "Preguntó por precios de alquiler.",
            "last_call_recording_url": "https://vapi.example/legacy.mp3",
            "last_call_duration_sec": 120,
            "last_call_at": "2026-07-01T09:00:00+00:00",
        }
    )

    calls = _call_entries(conv, lead)

    assert len(calls) == 1
    call = calls[0]
    assert call.call_id == "call-legacy"
    assert call.transcript_summary == "Preguntó por precios de alquiler."
    assert call.recording_url == "https://vapi.example/legacy.mp3"
    assert call.duration_seconds == 120


def test_no_call_data_yields_no_rows():
    assert _call_entries(_conv(), _lead()) == []


def test_malformed_records_and_dates_are_tolerated():
    conv = _conv(extra={"calls": ["garbage", {"call_id": "x", "ended_at": "not-a-date"}]})
    calls = _call_entries(conv, _lead())
    assert len(calls) == 1
    assert calls[0].call_id == "x"
    assert calls[0].ended_at is None


# ---------------------------------------------------------------------------
# (50) Vapi firstMessage recording notice
# ---------------------------------------------------------------------------


def test_first_message_includes_recording_notice_by_default():
    msg = build_first_message("Inmo Sol")
    assert msg.startswith(RECORDING_NOTICE_PREFIX)
    assert "Inmo Sol" in msg


def test_first_message_without_recording_notice():
    msg = build_first_message("Inmo Sol", recording_notice=False)
    assert RECORDING_NOTICE_PREFIX not in msg
    assert msg.startswith("Hola, soy el asistente virtual de Inmo Sol")
