"""Webhook signature verification for WhatsApp.

Meta signs every webhook payload with HMAC-SHA256 using the app secret of
the app that owns the subscription. Reject any request with a mismatched
signature to prevent spoofed leads.

Each agency connects its *own* Meta app (dashboard → Canales → WhatsApp),
in which case Meta signs with **that** app's secret, not ours. The caller
resolves the agency from the payload first; for a number an agency
connected, that agency's secret is the **only** one accepted — see
`app/api/whatsapp.py`. There is no fallback to the global secret for such an
agency: the global one is shared by every tenant on our app, so accepting it
too would mean anyone holding it could forge messages into a tenant that
went out of its way to isolate itself.

The global secret is used for our own number and for payloads no agency
claims. `secret=""`/`None` below means exactly that — "check with ours" — and
is never how the caller handles a connected agency missing its secret; that
case is a 401, not a global check (POST /channels/whatsapp requires the
secret precisely so it cannot happen).

Separately, `agency_verify_token`/`verify_token_matches` cover the *other*
secret in this flow: the `hub.verify_token` Meta echoes back when an app
subscribes to a callback URL. See their docstrings for why the panel must
not be handed the raw global one.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import uuid

from app.core.settings import settings

# "sha256=" + 64 lowercase hex chars. Anything else can be thrown out before
# parsing the body or touching the database.
_SIGNATURE_RE = re.compile(r"^sha256=[0-9a-fA-F]{64}$")


def is_well_formed_signature(signature_header: str | None) -> bool:
    """Cheap shape check, no secrets involved.

    Used as the first gate of the webhook: the endpoint is exempt from the
    rate limiter, so garbage must be rejected before any parsing or query.
    """
    return bool(signature_header) and bool(_SIGNATURE_RE.match(signature_header or ""))


def verify_signature(
    raw_body: bytes,
    signature_header: str | None,
    secret: str | None = None,
) -> bool:
    """True when `signature_header` is a valid HMAC of `raw_body`.

    `secret` overrides the global app secret (per-agency Meta app). When it
    is falsy the global `settings.whatsapp_app_secret` is used.
    """
    if not signature_header:
        return False

    effective_secret = secret or settings.whatsapp_app_secret.get_secret_value()
    if not effective_secret:
        # Dev: allow if secret not configured. NEVER let this branch run in prod.
        return settings.is_dev

    # Meta sends "sha256=<hex>"
    prefix, _, received = signature_header.partition("=")
    if prefix != "sha256" or not received:
        return False

    expected = hmac.new(
        effective_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, received)


# ---------------------------------------------------------------------------
# hub.verify_token — the value the agency pastes into its own Meta app
# ---------------------------------------------------------------------------

# "<agency uuid hex>-<32 hex>". The agency id travels in the token so the
# check needs no database lookup: the callback URL is the same for everyone
# and Meta sends nothing but the token itself.
_AGENCY_TOKEN_RE = re.compile(r"^([0-9a-f]{32})-([0-9a-f]{32})$")


def _agency_token_digest(key: str, agency_hex: str) -> str:
    return hmac.new(key.encode("utf-8"), agency_hex.encode("ascii"), hashlib.sha256).hexdigest()[:32]


def agency_verify_token(agency_id: uuid.UUID | str) -> str:
    """The verify token *this* agency pastes into its own Meta app.

    `settings.whatsapp_verify_token` is a single secret shared by every
    tenant, and `GET /channels` prints it for every member of every agency
    (any role, connected or not). Handing the raw value out means it cannot
    be rotated for one tenant without breaking the rest, and a leak is
    unattributable — a cross-tenant secret in a multi-tenant panel.

    Deriving one per agency keeps the shared secret on the server: what the
    panel shows is specific to that agency, useless for deriving anyone
    else's, and identifies the source if it turns up somewhere it shouldn't.
    It is deliberately stateless (no column, no migration): the value must
    survive redeploys, because it lives inside the agency's Meta app.

    Empty when no global token is configured — an unconfigured deployment
    must not print a token that could never verify. `GET /webhooks/whatsapp`
    refuses everything in that state, and the panel says so.
    """
    key = settings.whatsapp_verify_token.get_secret_value()
    if not key:
        return ""
    try:
        agency_hex = uuid.UUID(str(agency_id)).hex
    except (ValueError, AttributeError, TypeError):
        return ""
    return f"{agency_hex}-{_agency_token_digest(key, agency_hex)}"


def verify_token_matches(token: str | None) -> bool:
    """True when `token` is one we handed out.

    Two forms are accepted: the per-agency token above, and the raw global
    one — apps configured before per-agency tokens existed (and our own Meta
    app, which is not any agency's) already have that value saved in Meta,
    and breaking their subscription to tidy this up would take the whole
    channel down.
    """
    key = settings.whatsapp_verify_token.get_secret_value()
    # An unset token must not mean "everyone passes": that would let anyone
    # point their own Meta app at this callback URL.
    if not key or not token:
        return False

    # Bytes, not str: `hmac.compare_digest` raises TypeError on non-ASCII
    # strings, and this value arrives straight from an unauthenticated query
    # string — comparing as str turns "?hub.verify_token=ñ" into a 500.
    if hmac.compare_digest(token.encode("utf-8", "replace"), key.encode("utf-8")):
        return True

    match = _AGENCY_TOKEN_RE.match(token)
    if match is None:
        return False
    # Both sides are hex here, so the str comparison is ASCII-safe.
    return hmac.compare_digest(_agency_token_digest(key, match.group(1)), match.group(2))
