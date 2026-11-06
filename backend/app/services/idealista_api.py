"""Idealista Partner API client (scaffold).

Idealista's partner API requires OAuth 2.0 client_credentials and approval.
This module is a thin async wrapper that:
- Exchanges client_id/client_secret for an access token (cached).
- Exposes `fetch_recent_leads(country, since)` and `fetch_listings(...)`.

Until the agency has been onboarded as Idealista partner, these calls
return empty lists and log a warning. Wire `IDEALISTA_*` env vars to enable.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from app.core.logging import get_logger
from app.core.settings import settings

log = get_logger(__name__)
_BASE = "https://api.idealista.com"


@dataclass
class IdealistaToken:
    access_token: str
    expires_at: float


_token: IdealistaToken | None = None


def _credentials() -> tuple[str, str]:
    return (
        settings.idealista_client_id,
        settings.idealista_client_secret.get_secret_value(),
    )


async def _get_token() -> str | None:
    global _token
    cid, secret = _credentials()
    if not (cid and secret):
        return None
    if _token and _token.expires_at - 30 > time.time():
        return _token.access_token

    auth = httpx.BasicAuth(cid, secret)
    data = {"grant_type": "client_credentials", "scope": "read"}
    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.post(f"{_BASE}/oauth/token", data=data, auth=auth)
    if resp.status_code >= 400:
        log.error("idealista.token.error", status=resp.status_code, body=resp.text[:200])
        return None
    body = resp.json()
    _token = IdealistaToken(
        access_token=body["access_token"],
        expires_at=time.time() + int(body.get("expires_in", 3600)),
    )
    return _token.access_token


async def fetch_recent_leads(*, country: str = "es", limit: int = 50) -> list[dict[str, Any]]:
    """Returns leads as dicts. Empty list if not configured."""
    token = await _get_token()
    if token is None:
        log.info("idealista.skipped.not_configured")
        return []

    headers = {"Authorization": f"Bearer {token}"}
    params = {"country": country, "maxItems": limit}
    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.get(
            f"{_BASE}/3.5/{country}/leads", headers=headers, params=params
        )
    if resp.status_code >= 400:
        log.error("idealista.leads.error", status=resp.status_code, body=resp.text[:200])
        return []
    return resp.json().get("elementList", []) or []


async def fetch_listing(listing_id: str, *, country: str = "es") -> dict[str, Any] | None:
    token = await _get_token()
    if token is None:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.get(
            f"{_BASE}/3.5/{country}/listings/{listing_id}", headers=headers
        )
    if resp.status_code >= 400:
        return None
    return resp.json()


# Suppress unused
_ = datetime
