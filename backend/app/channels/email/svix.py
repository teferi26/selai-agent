"""Svix webhook signature verification (Resend Inbound).

Resend delivers inbound email through Svix, which signs every request with
three headers:

    svix-id         unique delivery id
    svix-timestamp  unix seconds
    svix-signature  space-separated list of "v1,<base64>" — a payload can
                    carry several while a secret is being rotated

The signed content is literally `{id}.{timestamp}.{raw body}` and the key is
the base64 body of the `whsec_...` secret. Everything here is pure so the
rules are testable without a network or a live secret.

Why not the `svix` package: it is one function, and a dependency that can
verify signatures is a dependency that can also be a supply-chain problem in
the one place that decides whether a stranger may inject leads into any
agency.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import time

# Svix's own tolerance. Bounded on both sides: an old capture cannot be
# replayed, and a request "from the future" (a badly-synced clock, or a
# forged timestamp meant to buy a long replay window) is refused too.
TOLERANCE_SECONDS = 5 * 60

SIGNATURE_VERSION = "v1"


def _key_from_secret(secret: str) -> bytes | None:
    """`whsec_<base64>` → raw key bytes. None when it isn't a Svix secret.

    Trimmed first. The secret is pasted by hand into a hosting dashboard, and
    a trailing newline or space rides along more often than not. Base64 with
    `validate=True` refuses it, so every webhook would 401 with a value that
    looks perfectly right on screen — the worst kind of configuration bug,
    because nothing about it suggests where to look.
    """
    if not secret:
        return None
    raw = secret.strip().removeprefix("whsec_").strip()
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return None


def expected_signature(*, secret: str, svix_id: str, timestamp: str, body: bytes) -> str | None:
    """The base64 signature Svix should have sent, or None if unusable."""
    key = _key_from_secret(secret)
    if key is None:
        return None
    signed = b"%s.%s.%s" % (svix_id.encode(), timestamp.encode(), body)
    digest = hmac.new(key, signed, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def timestamp_is_fresh(timestamp: str, *, now: float | None = None) -> bool:
    """Within ±TOLERANCE_SECONDS of now. Non-numeric is never fresh."""
    try:
        sent = int(timestamp)
    except (TypeError, ValueError):
        return False
    current = time.time() if now is None else now
    return abs(current - sent) <= TOLERANCE_SECONDS


def verify(
    *,
    secret: str,
    svix_id: str | None,
    timestamp: str | None,
    signature_header: str | None,
    body: bytes,
    now: float | None = None,
) -> bool:
    """True when `body` really came from Svix with `secret`.

    Missing headers, an unparseable secret or a stale timestamp all fail
    closed — this is the only gate in front of "create a lead in any agency".
    """
    if not (svix_id and timestamp and signature_header):
        return False
    if not timestamp_is_fresh(timestamp, now=now):
        return False

    expected = expected_signature(
        secret=secret, svix_id=svix_id, timestamp=timestamp, body=body
    )
    if expected is None:
        return False

    # The header holds every currently valid signature, so one match is
    # enough; comparing them all in constant time keeps a mismatch from
    # timing out sooner than a match.
    matched = False
    for part in signature_header.split():
        version, _, candidate = part.partition(",")
        if version != SIGNATURE_VERSION or not candidate:
            continue
        if hmac.compare_digest(candidate, expected):
            matched = True
    return matched
