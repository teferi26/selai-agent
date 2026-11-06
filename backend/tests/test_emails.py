"""Product email service — tolerant sender + HTML builders."""
from __future__ import annotations

import uuid

import httpx
import pytest
from pydantic import SecretStr

from app.core.settings import settings
from app.services import emails
from app.services.emails import (
    BRAND_COLOR,
    SETUP_URL,
    WELCOME_STEPS,
    build_first_week_email_html,
    build_handoff_email_html,
    build_hot_lead_email_html,
    build_office_hours_email_html,
    build_phone_number_request_email_html,
    build_setup_rescue_email_html,
    build_trial_ending_email_html,
    build_voice_usage_alert_email_html,
    build_weekly_digest_email_html,
    build_welcome_email_html,
    send_email,
)

# ---------------------------------------------------------------------------
# send_email tolerance
# ---------------------------------------------------------------------------


async def test_send_email_without_key_returns_false(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", SecretStr(""))
    assert await send_email("a@b.es", "Hola", "<p>hola</p>") is False


class _FakeResponse:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code
        self.text = "{}"

    def json(self) -> dict:
        return {"id": "email_123"}


class _FakeClient:
    """Records the payload; configurable to succeed, 4xx, or blow up."""

    last_payload: dict | None = None
    mode = "ok"

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        type(self).last_payload = json
        if type(self).mode == "boom":
            raise httpx.ConnectError("network down")
        if type(self).mode == "error":
            return _FakeResponse(422)
        return _FakeResponse(200)


@pytest.fixture
def fake_resend(monkeypatch):
    monkeypatch.setattr(settings, "resend_api_key", SecretStr("re_test"))
    monkeypatch.setattr(emails.httpx, "AsyncClient", _FakeClient)
    _FakeClient.last_payload = None
    _FakeClient.mode = "ok"
    return _FakeClient


async def test_send_email_success_builds_resend_payload(fake_resend):
    ok = await send_email("agencia@example.es", "Asunto", "<p>cuerpo</p>")
    assert ok is True
    payload = fake_resend.last_payload
    assert payload["from"] == "selai <hola@selai.es>"
    assert payload["to"] == ["agencia@example.es"]
    assert payload["subject"] == "Asunto"
    assert payload["html"] == "<p>cuerpo</p>"


async def test_send_email_provider_error_returns_false(fake_resend):
    fake_resend.mode = "error"
    assert await send_email("a@b.es", "x", "<p>x</p>") is False


async def test_send_email_network_error_never_raises(fake_resend):
    fake_resend.mode = "boom"
    assert await send_email("a@b.es", "x", "<p>x</p>") is False


# ---------------------------------------------------------------------------
# HTML builders
# ---------------------------------------------------------------------------


def test_welcome_email_has_seven_steps_and_setup_link():
    html = build_welcome_email_html("Inmobiliaria Sol", "Marta García")
    assert len(WELCOME_STEPS) == 7
    for step, mins in WELCOME_STEPS:
        assert step in html
        assert mins in html
    assert SETUP_URL in html
    assert BRAND_COLOR in html
    assert "Marta" in html
    assert "Inmobiliaria Sol" in html


def test_handoff_email_links_to_the_conversation():
    conv_id = uuid.uuid4()
    html = build_handoff_email_html("Juan Pérez", conv_id)
    assert f"https://www.selai.es/conversations/{conv_id}" in html
    assert "Juan Pérez" in html


def test_handoff_email_without_name_uses_fallback():
    html = build_handoff_email_html(None, uuid.uuid4())
    assert "Un lead" in html


def test_trial_ending_email_mentions_days_and_billing():
    html = build_trial_ending_email_html("Inmo Norte", 3, 42, 17)
    assert "3 días" in html
    assert "42" in html
    assert "17" in html
    assert "https://www.selai.es/billing" in html


def test_weekly_digest_email_shows_the_three_counters():
    html = build_weekly_digest_email_html("Inmo Norte", 10, 5, 2)
    assert "conversaciones" in html
    assert "leads" in html
    assert "visitas" in html
    for n in ("10", "5", "2"):
        assert n in html


def test_office_hours_email_links_to_the_conversation():
    conv_id = uuid.uuid4()
    html = build_office_hours_email_html("Juan Pérez", conv_id)
    assert f"https://www.selai.es/conversations/{conv_id}" in html
    assert "Juan Pérez" in html
    assert "horario de oficina" in html


def test_hot_lead_email_shows_details_and_link():
    conv_id = uuid.uuid4()
    html = build_hot_lead_email_html(
        "Ana Ruiz", conv_id, ["Teléfono: +34600111222", "Score: 82"]
    )
    assert f"https://www.selai.es/conversations/{conv_id}" in html
    assert "Ana Ruiz" in html
    assert "+34600111222" in html
    assert "Score: 82" in html
    assert "lead caliente" in html


def test_hot_lead_email_without_name_or_details():
    html = build_hot_lead_email_html(None, uuid.uuid4(), None)
    assert "Un lead" in html


def test_voice_usage_alert_email_mentions_minutes_and_billing():
    html = build_voice_usage_alert_email_html("Inmo Norte", 250, 300)
    assert "250 de 300 minutos" in html
    assert "83%" in html
    assert "https://www.selai.es/billing" in html


def test_setup_rescue_email_links_setup_and_support():
    html = build_setup_rescue_email_html("Inmo Norte")
    assert SETUP_URL in html
    assert "hola@selai.es" in html
    assert "~10 min" in html


def test_phone_number_request_email_shows_agency_data():
    html = build_phone_number_request_email_html(
        agency_name="Inmo Sol", slug="inmo-sol", contact_email="dueno@inmosol.example.com"
    )
    assert "Solicitud de número de teléfono IA" in html
    assert "Inmo Sol" in html
    assert "inmo-sol" in html
    assert "dueno@inmosol.example.com" in html
    assert "24h" in html


def test_phone_number_request_email_without_contact_email():
    html = build_phone_number_request_email_html(
        agency_name="Inmo Norte", slug="inmo-norte", contact_email=None
    )
    assert "Inmo Norte" in html
    assert "sin email de contacto" in html


def test_first_week_email_shows_the_three_counters():
    html = build_first_week_email_html("Inmo Norte", 31, 14, 6)
    assert "primera semana" in html
    assert "conversaciones" in html
    assert "leads" in html
    assert "visitas" in html
    for n in ("31", "14", "6"):
        assert n in html


# ---------------------------------------------------------------------------
# El remitente de las respuestas de la IA (ResendClient)
# ---------------------------------------------------------------------------
#
# El canal entero puede funcionar — firma, agencia, cuerpo — y morir en el
# ultimo eslabon: Resend rechaza remitentes de dominios sin verificar, y el
# valor por defecto historico era el placeholder "noreply@example.com". Con la
# variable sin poner en produccion, cada respuesta de la IA a un lead fallaba
# con 403 dentro de una BackgroundTask: el lead escribio y nunca recibio nada,
# y nadie vio ningun error.


def _client_from(monkeypatch, value):
    from app.channels.email.client import ResendClient

    monkeypatch.setattr(settings, "email_from", value)
    monkeypatch.setattr(settings, "resend_from", "selai <hola@selai.es>")
    return ResendClient()


def test_reply_sender_falls_back_to_the_verified_identity(monkeypatch):
    assert _client_from(monkeypatch, "").from_addr == "selai <hola@selai.es>"


def test_the_placeholder_domain_counts_as_unset(monkeypatch):
    # "noreply@example.com" parece configurado y no lo esta: Resend lo rechaza.
    assert _client_from(monkeypatch, "noreply@example.com").from_addr == "selai <hola@selai.es>"
    assert _client_from(monkeypatch, "  otro@example.com ").from_addr == "selai <hola@selai.es>"


def test_a_real_custom_sender_is_respected(monkeypatch):
    assert _client_from(monkeypatch, "Inmo <propio@selai.es>").from_addr == "Inmo <propio@selai.es>"


def test_no_settings_default_ships_a_placeholder_domain():
    """Ningun valor por defecto de settings puede llevar example.com."""
    from app.core.settings import Settings

    for name, field in Settings.model_fields.items():
        default = field.default
        if isinstance(default, str):
            assert "example.com" not in default, name
