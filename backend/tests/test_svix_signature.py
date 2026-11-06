"""Svix signature verification — the gate in front of Resend Inbound.

Everything here is the pure verifier: a wrong secret, a stale capture or a
missing header must never let a stranger create leads in an agency.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

import pytest

from app.channels.email.svix import (
    TOLERANCE_SECONDS,
    expected_signature,
    timestamp_is_fresh,
    verify,
)

SECRET = "whsec_" + base64.b64encode(b"selai-inbound-signing-key").decode()
SVIX_ID = "msg_2abcDEF"
BODY = b'{"type":"email.received","data":{"subject":"Consulta piso"}}'


def _sign(body: bytes, ts: str, secret: str = SECRET, svix_id: str = SVIX_ID) -> str:
    key = base64.b64decode(secret.removeprefix("whsec_"))
    signed = b"%s.%s.%s" % (svix_id.encode(), ts.encode(), body)
    return "v1," + base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()


def _now_ts() -> str:
    return str(int(time.time()))


def _verify(**overrides):
    """A genuine delivery, with the fields under test overridden.

    The signature is always built from a *usable* timestamp so that dropping
    the `svix-timestamp` header tests the missing header alone, and not our
    ability to sign with None.
    """
    ts = overrides.pop("timestamp", _now_ts())
    body = overrides.pop("body", BODY)
    kwargs = {
        "secret": SECRET,
        "svix_id": SVIX_ID,
        "timestamp": ts,
        "signature_header": _sign(body, ts if ts is not None else _now_ts()),
        "body": body,
    }
    kwargs.update(overrides)
    return verify(**kwargs)


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_a_genuine_delivery_is_accepted():
    assert _verify() is True


def test_several_signatures_in_the_header_accepts_the_matching_one():
    # Svix sends every valid signature while a secret is being rotated.
    ts = _now_ts()
    header = "v1,AAAAdefinitelynotit= " + _sign(BODY, ts)
    assert _verify(timestamp=ts, signature_header=header) is True


# ---------------------------------------------------------------------------
# Forgeries
# ---------------------------------------------------------------------------


def test_a_tampered_body_is_rejected():
    ts = _now_ts()
    good = _sign(BODY, ts)
    assert verify(
        secret=SECRET,
        svix_id=SVIX_ID,
        timestamp=ts,
        signature_header=good,
        body=BODY + b" ",
    ) is False


def test_another_secret_is_rejected():
    ts = _now_ts()
    other = "whsec_" + base64.b64encode(b"someone-elses-key").decode()
    assert _verify(timestamp=ts, signature_header=_sign(BODY, ts, secret=other)) is False


def test_a_different_svix_id_is_rejected():
    ts = _now_ts()
    assert _verify(timestamp=ts, signature_header=_sign(BODY, ts, svix_id="msg_other")) is False


@pytest.mark.parametrize("version", ["v0", "v2", "", "1"])
def test_unknown_signature_versions_are_ignored(version):
    ts = _now_ts()
    sig = _sign(BODY, ts).split(",", 1)[1]
    assert _verify(timestamp=ts, signature_header=f"{version},{sig}") is False


@pytest.mark.parametrize(
    "missing", ["svix_id", "timestamp", "signature_header"]
)
def test_missing_headers_are_rejected(missing):
    assert _verify(**{missing: None}) is False


def test_empty_signature_value_is_rejected():
    assert _verify(signature_header="v1,") is False


# ---------------------------------------------------------------------------
# Replay window
# ---------------------------------------------------------------------------


def test_an_old_capture_is_rejected():
    old = str(int(time.time()) - TOLERANCE_SECONDS - 30)
    assert _verify(timestamp=old) is False


def test_a_timestamp_from_the_future_is_rejected():
    ahead = str(int(time.time()) + TOLERANCE_SECONDS + 30)
    assert _verify(timestamp=ahead) is False


def test_inside_the_window_is_fresh():
    now = 1_700_000_000
    assert timestamp_is_fresh(str(now - TOLERANCE_SECONDS + 5), now=now) is True
    assert timestamp_is_fresh(str(now + TOLERANCE_SECONDS - 5), now=now) is True


@pytest.mark.parametrize("bad", ["", "ayer", "16e9", None])
def test_non_numeric_timestamps_are_never_fresh(bad):
    assert timestamp_is_fresh(bad) is False


# ---------------------------------------------------------------------------
# Misconfiguration must fail closed, never open
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("secret", ["", "not-base64!!", "whsec_@@@@"])
def test_an_unusable_secret_never_verifies(secret):
    assert expected_signature(
        secret=secret, svix_id=SVIX_ID, timestamp=_now_ts(), body=BODY
    ) is None
    assert _verify(secret=secret) is False


def test_secret_without_the_whsec_prefix_still_works():
    # Some dashboards show the value already stripped; both must behave.
    bare = SECRET.removeprefix("whsec_")
    ts = _now_ts()
    assert verify(
        secret=bare,
        svix_id=SVIX_ID,
        timestamp=ts,
        signature_header=_sign(BODY, ts),
        body=BODY,
    ) is True


@pytest.mark.parametrize(
    "envuelto",
    [
        "{s}\n",       # el clásico: pegar arrastra el salto de línea
        "{s} ",
        " {s}",
        "\t{s}\r\n",
        "whsec_ {b}",  # un espacio colado tras el prefijo
    ],
)
def test_whitespace_around_the_secret_does_not_break_it(envuelto):
    """El secreto se pega a mano en un panel de hosting.

    Un salto de línea o un espacio invisible al final hacía que base64 lo
    rechazara y que TODOS los webhooks respondieran 401, con un valor que en
    pantalla se ve perfecto. Es el peor tipo de fallo de configuración: nada
    apunta a dónde mirar.
    """
    sucio = envuelto.format(s=SECRET, b=SECRET.removeprefix("whsec_"))
    ts = _now_ts()
    assert verify(
        secret=sucio,
        svix_id=SVIX_ID,
        timestamp=ts,
        signature_header=_sign(BODY, ts),
        body=BODY,
    ) is True


def test_a_secret_that_is_only_whitespace_still_fails_closed():
    """Tolerar espacios no puede convertirse en tolerar un secreto vacío."""
    for vacio in ("", "   ", "\n", "whsec_", "whsec_   "):
        assert _verify(secret=vacio) is False
