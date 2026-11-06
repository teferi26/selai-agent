"""Stripe webhook signature verification.

NB: `settings` is a module-level singleton loaded once at import; patching
environment variables after that has no effect. Patch the settings object.
"""

import hashlib
import hmac
import time

import pytest
from pydantic import SecretStr

from app.core.settings import settings
from app.services import stripe_billing
from app.services.stripe_billing import verify_webhook_signature


def _sign(secret: str, payload: bytes) -> str:
    ts = str(int(time.time()))
    signed = f"{ts}.".encode() + payload
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def test_signature_verifies(monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", SecretStr("whsec_test"))
    payload = b'{"id":"evt_1"}'
    header = _sign("whsec_test", payload)
    assert verify_webhook_signature(payload, header) is True


def test_signature_rejects_wrong_secret(monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", SecretStr("whsec_real"))
    payload = b'{"id":"evt_1"}'
    header = _sign("whsec_attacker", payload)
    assert verify_webhook_signature(payload, header) is False


def test_signature_rejects_no_header(monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", SecretStr("whsec_test"))
    assert verify_webhook_signature(b"{}", None) is False


def test_signature_rejects_when_no_secret(monkeypatch):
    monkeypatch.setattr(settings, "stripe_webhook_secret", SecretStr(""))
    assert verify_webhook_signature(b"{}", "t=1,v1=abc") is False


@pytest.mark.asyncio
async def test_billing_portal_session_pins_the_reviewed_configuration(monkeypatch):
    seen: dict = {}

    async def _post(path, form, *, idempotency_key=None):
        seen.update({"path": path, "form": form, "key": idempotency_key})
        return {"id": "bps_1", "url": "https://billing.stripe.com/p/session"}

    monkeypatch.setattr(settings, "stripe_billing_portal_configuration_id", "bpc_live_selai")
    monkeypatch.setattr(stripe_billing, "_post", _post)

    result = await stripe_billing.create_billing_portal_session(
        customer_id="cus_1",
        return_url="https://selai.es/billing",
    )

    assert result["id"] == "bps_1"
    assert seen == {
        "path": "/billing_portal/sessions",
        "form": {
            "customer": "cus_1",
            "return_url": "https://selai.es/billing",
            "configuration": "bpc_live_selai",
        },
        "key": None,
    }


@pytest.mark.asyncio
async def test_billing_portal_configuration_remains_optional_in_development(monkeypatch):
    seen: dict = {}

    async def _post(path, form, *, idempotency_key=None):
        seen.update({"path": path, "form": form})
        return {"id": "bps_dev"}

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "stripe_billing_portal_configuration_id", "")
    monkeypatch.setattr(stripe_billing, "_post", _post)

    await stripe_billing.create_billing_portal_session(
        customer_id="cus_dev",
        return_url="http://localhost:3000/billing",
    )

    assert seen["form"] == {
        "customer": "cus_dev",
        "return_url": "http://localhost:3000/billing",
    }


@pytest.mark.asyncio
async def test_billing_portal_refuses_an_unpinned_production_configuration(monkeypatch):
    async def _post(path, form, *, idempotency_key=None):
        raise AssertionError("Stripe must not be called without a pinned portal")

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "stripe_billing_portal_configuration_id", "")
    monkeypatch.setattr(stripe_billing, "_post", _post)

    with pytest.raises(RuntimeError, match="STRIPE_BILLING_PORTAL_CONFIGURATION_ID"):
        await stripe_billing.create_billing_portal_session(
            customer_id="cus_live",
            return_url="https://selai.es/billing",
        )
