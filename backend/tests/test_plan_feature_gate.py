"""ensure_feature — plan feature flags gate Agencia-only capabilities
(voice cloning is sold as Agencia-only on the pricing page)."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import plan_limits

AGENCY_ID = uuid.uuid4()


def _patch_plan(monkeypatch, plan):
    async def fake_get_plan_state(db, agency_id):
        return (None, plan)

    monkeypatch.setattr(plan_limits, "get_plan_state", fake_get_plan_state)


@pytest.mark.asyncio
async def test_feature_missing_from_plan_raises_403(monkeypatch):
    _patch_plan(monkeypatch, SimpleNamespace(code="pro", features={"voice": True}))
    with pytest.raises(HTTPException) as exc:
        await plan_limits.ensure_feature(None, AGENCY_ID, "voice_cloning")
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "feature_not_in_plan"
    assert exc.value.detail["feature"] == "voice_cloning"


@pytest.mark.asyncio
async def test_feature_present_passes(monkeypatch):
    _patch_plan(
        monkeypatch, SimpleNamespace(code="agencia", features={"voice_cloning": True})
    )
    await plan_limits.ensure_feature(None, AGENCY_ID, "voice_cloning")


@pytest.mark.asyncio
async def test_no_plan_does_not_block(monkeypatch):
    _patch_plan(monkeypatch, None)
    await plan_limits.ensure_feature(None, AGENCY_ID, "voice_cloning")


@pytest.mark.asyncio
async def test_null_features_blob_raises(monkeypatch):
    _patch_plan(monkeypatch, SimpleNamespace(code="starter", features=None))
    with pytest.raises(HTTPException):
        await plan_limits.ensure_feature(None, AGENCY_ID, "voice_cloning")
