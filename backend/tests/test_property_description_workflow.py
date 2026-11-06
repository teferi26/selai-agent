"""Reviewed property-description workflow: draft, exact apply and safe undo."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import properties
from app.core.auth import AuthContext, require_auth
from app.core.settings import settings
from app.db.models.user import UserRole
from app.db.session import get_db
from app.main import app
from app.services.property_vision import PropertyVisionResult


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, prop):
        self.prop = prop
        self.commits = 0
        self.statements = []

    async def get(self, model, pk):
        return self.prop if pk == self.prop.id else None

    async def execute(self, stmt):
        self.statements.append(stmt)
        values = set(stmt.compile().params.values())
        if self.prop.id not in values or self.prop.agency_id not in values:
            return _ScalarResult(None)
        return _ScalarResult(self.prop)

    async def commit(self):
        self.commits += 1


@pytest.fixture
def workflow(monkeypatch):
    agency_id = uuid.uuid4()
    property_id = uuid.uuid4()
    prop = SimpleNamespace(
        id=property_id,
        agency_id=agency_id,
        description="Descripción anterior",
        image_urls=["https://cdn.example/front.jpg"],
        extra={},
    )
    db = _Session(prop)

    async def _db():
        yield db

    async def _describe(*args, **kwargs):
        return PropertyVisionResult(
            description="Borrador generado por IA",
            highlights=["Mucha luz"],
            weaknesses=["Falta una foto del baño"],
        )

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")
    monkeypatch.setattr(properties, "describe_property_from_photos", _describe)
    app.dependency_overrides[get_db] = _db
    yield SimpleNamespace(
        http=TestClient(app, raise_server_exceptions=False),
        db=db,
        prop=prop,
        agency_id=agency_id,
        property_id=property_id,
    )
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_auth, None)


def _base(ctx) -> str:
    return f"/api/v1/agencies/{ctx.agency_id}/properties/{ctx.property_id}"


def _generate(ctx, **extra):
    return ctx.http.post(
        f"{_base(ctx)}/describe-from-photos",
        json={"target_language": "es", **extra},
    )


def _as_role(ctx, role: UserRole):
    def _auth():
        return AuthContext(
            clerk_user_id=f"{role.value}-1",
            email=f"{role.value}@example.com",
            raw_claims={},
            user=SimpleNamespace(
                id=uuid.uuid4(),
                agency_id=ctx.agency_id,
                role=role,
                is_active=True,
            ),
            dev_bypass=False,
        )

    app.dependency_overrides[require_auth] = _auth


def test_generation_persists_a_draft_without_touching_the_live_description(workflow):
    response = _generate(workflow)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["description"] == "Borrador generado por IA"
    assert body["base_description"] == "Descripción anterior"
    assert workflow.prop.description == "Descripción anterior"
    assert workflow.prop.extra[properties.DESCRIPTION_DRAFT_KEY]["id"] == body["id"]
    assert workflow.db.commits == 1


def test_old_direct_apply_is_refused_before_calling_the_model(workflow, monkeypatch):
    called = False

    async def _must_not_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider should not run")

    monkeypatch.setattr(properties, "describe_property_from_photos", _must_not_run)
    response = _generate(workflow, apply=True)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "direct_apply_disabled"
    assert called is False
    assert workflow.prop.description == "Descripción anterior"


def test_existing_draft_requires_an_explicit_regeneration(workflow, monkeypatch):
    first = _generate(workflow)
    called = False

    async def _must_not_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider should not run")

    monkeypatch.setattr(properties, "describe_property_from_photos", _must_not_run)
    second = _generate(workflow)

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "active_description_draft_exists"
    assert called is False


def test_draft_can_be_edited_and_recovered_exactly(workflow):
    draft = _generate(workflow).json()
    reviewed = "  Texto revisado por una persona\nSegunda línea.  "

    saved = workflow.http.put(
        f"{_base(workflow)}/description-draft/{draft['id']}",
        json={"description": reviewed},
    )
    recovered = workflow.http.get(f"{_base(workflow)}/description-draft")

    assert saved.status_code == 200, saved.text
    assert saved.json()["description"] == reviewed
    assert recovered.status_code == 200
    assert recovered.json()["description"] == reviewed
    assert recovered.json()["generated_description"] == "Borrador generado por IA"
    assert workflow.prop.description == "Descripción anterior"


@pytest.mark.parametrize("role", [UserRole.OWNER, UserRole.ADMIN])
def test_owner_or_admin_applies_the_exact_reviewed_text_and_can_undo(workflow, role):
    draft = _generate(workflow).json()
    reviewed = "  Versión final exacta\nCon salto de línea.  "
    _as_role(workflow, role)

    applied = workflow.http.put(
        f"{_base(workflow)}/description",
        json={"draft_id": draft["id"], "description": reviewed},
    )

    assert applied.status_code == 200, applied.text
    assert applied.json()["description"] == reviewed
    assert workflow.prop.description == reviewed
    assert properties.DESCRIPTION_DRAFT_KEY not in workflow.prop.extra
    undo_id = applied.json()["undo_id"]
    assert undo_id

    restored = workflow.http.post(
        f"{_base(workflow)}/description-history/{undo_id}/restore", json={}
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["description"] == "Descripción anterior"
    assert workflow.prop.description == "Descripción anterior"


def test_apply_and_restore_retries_are_idempotent_after_an_uncertain_response(workflow):
    draft = _generate(workflow).json()
    _as_role(workflow, UserRole.OWNER)
    payload = {"draft_id": draft["id"], "description": "Texto aprobado una sola vez"}

    first = workflow.http.put(f"{_base(workflow)}/description", json=payload)
    commits_after_apply = workflow.db.commits
    history_after_apply = list(
        workflow.prop.extra[properties.DESCRIPTION_HISTORY_KEY]
    )
    retried = workflow.http.put(f"{_base(workflow)}/description", json=payload)

    assert first.status_code == retried.status_code == 200
    assert retried.json() == first.json()
    assert workflow.db.commits == commits_after_apply
    assert workflow.prop.extra[properties.DESCRIPTION_HISTORY_KEY] == history_after_apply

    undo_id = first.json()["undo_id"]
    restored = workflow.http.post(
        f"{_base(workflow)}/description-history/{undo_id}/restore", json={}
    )
    commits_after_restore = workflow.db.commits
    restored_retry = workflow.http.post(
        f"{_base(workflow)}/description-history/{undo_id}/restore", json={}
    )

    assert restored.status_code == restored_retry.status_code == 200
    assert restored_retry.json() == restored.json()
    assert workflow.db.commits == commits_after_restore


def test_description_history_is_bounded_to_three_safe_undo_points(workflow):
    _as_role(workflow, UserRole.ADMIN)
    for index in range(5):
        draft = _generate(workflow).json()
        applied = workflow.http.put(
            f"{_base(workflow)}/description",
            json={"draft_id": draft["id"], "description": f"Versión {index}"},
        )
        assert applied.status_code == 200, applied.text

    history = workflow.prop.extra[properties.DESCRIPTION_HISTORY_KEY]
    assert len(history) == properties.DESCRIPTION_HISTORY_LIMIT == 3
    assert [item["description"] for item in history] == [
        "Versión 1",
        "Versión 2",
        "Versión 3",
    ]


def test_stale_apply_is_rejected_and_keeps_the_draft(workflow):
    draft = _generate(workflow).json()
    workflow.prop.description = "Cambio de otra persona"

    response = workflow.http.put(
        f"{_base(workflow)}/description",
        json={"draft_id": draft["id"], "description": "Mi borrador"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "description_changed_since_draft",
        "current_description": "Cambio de otra persona",
    }
    assert workflow.prop.description == "Cambio de otra persona"
    assert workflow.prop.extra[properties.DESCRIPTION_DRAFT_KEY]["id"] == draft["id"]


def test_force_apply_is_explicit_and_still_creates_undo_history(workflow):
    draft = _generate(workflow).json()
    workflow.prop.description = "Cambio concurrente revisado en pantalla"

    response = workflow.http.put(
        f"{_base(workflow)}/description",
        json={
            "draft_id": draft["id"],
            "description": "Texto aprobado después del conflicto",
            "force": True,
            "confirmed_current_description": "Cambio concurrente revisado en pantalla",
        },
    )

    assert response.status_code == 200, response.text
    assert workflow.prop.description == "Texto aprobado después del conflicto"
    history = workflow.prop.extra[properties.DESCRIPTION_HISTORY_KEY]
    assert history[-1]["description"] == "Cambio concurrente revisado en pantalla"


def test_force_apply_refuses_a_second_unseen_concurrent_change(workflow):
    draft = _generate(workflow).json()
    workflow.prop.description = "Cambio mostrado a la persona revisora"
    initial_conflict = workflow.http.put(
        f"{_base(workflow)}/description",
        json={"draft_id": draft["id"], "description": "Texto revisado"},
    )
    assert initial_conflict.status_code == 409

    workflow.prop.description = "Cambio aún más reciente"
    forced = workflow.http.put(
        f"{_base(workflow)}/description",
        json={
            "draft_id": draft["id"],
            "description": "Texto revisado",
            "force": True,
            "confirmed_current_description": "Cambio mostrado a la persona revisora",
        },
    )

    assert forced.status_code == 409
    assert forced.json()["detail"] == {
        "code": "description_changed_since_draft",
        "current_description": "Cambio aún más reciente",
    }
    assert workflow.prop.description == "Cambio aún más reciente"
    assert workflow.prop.extra[properties.DESCRIPTION_DRAFT_KEY]["id"] == draft["id"]


def test_agent_can_prepare_but_cannot_apply_final_copy(workflow):
    draft = _generate(workflow).json()
    _as_role(workflow, UserRole.AGENT)

    prepared = workflow.http.put(
        f"{_base(workflow)}/description-draft/{draft['id']}",
        json={"description": "Borrador preparado por agente"},
    )
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["description"] == "Borrador preparado por agente"

    response = workflow.http.put(
        f"{_base(workflow)}/description",
        json={"draft_id": draft["id"], "description": "No debe aplicarse"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "role_required"
    assert workflow.prop.description == "Descripción anterior"
    assert workflow.prop.extra[properties.DESCRIPTION_DRAFT_KEY]["id"] == draft["id"]


def test_cancel_only_removes_the_draft(workflow):
    draft = _generate(workflow).json()
    response = workflow.http.delete(
        f"{_base(workflow)}/description-draft/{draft['id']}"
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert workflow.prop.description == "Descripción anterior"
    assert properties.DESCRIPTION_DRAFT_KEY not in workflow.prop.extra

    # A retry after a lost response is idempotent.
    commits_after_cancel = workflow.db.commits
    retried = workflow.http.delete(
        f"{_base(workflow)}/description-draft/{draft['id']}"
    )
    assert retried.status_code == 200
    assert retried.json() == {"ok": True}
    assert workflow.db.commits == commits_after_cancel


def test_cross_tenant_property_is_not_visible(workflow):
    other_agency = uuid.uuid4()
    response = workflow.http.post(
        f"/api/v1/agencies/{other_agency}/properties/{workflow.property_id}/describe-from-photos",
        json={"target_language": "es"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "property_not_found"
    assert workflow.prop.extra == {}


def test_apply_uses_a_tenant_scoped_locked_query(workflow):
    draft = _generate(workflow).json()
    _as_role(workflow, UserRole.OWNER)

    other_tenant = workflow.http.put(
        f"/api/v1/agencies/{uuid.uuid4()}/properties/{workflow.property_id}/description",
        json={"draft_id": draft["id"], "description": "No debe cruzar de tenant"},
    )
    # Membership is rejected before the property row is queried, so the
    # tenant cannot infer whether that property id exists.
    assert other_tenant.status_code == 403
    assert workflow.prop.description == "Descripción anterior"

    own_tenant = workflow.http.put(
        f"{_base(workflow)}/description",
        json={"draft_id": draft["id"], "description": "Texto del tenant correcto"},
    )
    assert own_tenant.status_code == 200, own_tenant.text
    statement = str(workflow.db.statements[-1])
    assert "properties.id" in statement
    assert "properties.agency_id" in statement
    assert "FOR UPDATE" in statement


@pytest.mark.parametrize(
    "target_language",
    ["../es", "es<script", "es_ES", "x" * 17],
)
def test_target_language_is_bounded_before_calling_the_provider(
    workflow, monkeypatch, target_language
):
    called = False

    async def _must_not_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider should not run")

    monkeypatch.setattr(properties, "describe_property_from_photos", _must_not_run)
    response = _generate(workflow, target_language=target_language)

    assert response.status_code == 422
    assert called is False
    assert workflow.prop.extra == {}


@pytest.mark.parametrize("generated", ["   ", "x" * 12_001])
def test_invalid_provider_copy_never_becomes_a_draft(workflow, monkeypatch, generated):
    async def _invalid_result(*args, **kwargs):
        return PropertyVisionResult(
            description=generated,
            highlights=[],
            weaknesses=[],
        )

    monkeypatch.setattr(properties, "describe_property_from_photos", _invalid_result)
    response = _generate(workflow)

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "vision_invalid_description"
    assert workflow.prop.description == "Descripción anterior"
    assert workflow.prop.extra == {}
    assert workflow.db.commits == 0


def test_invalid_provider_audit_never_persists_a_corrupt_draft(workflow, monkeypatch):
    async def _invalid_result(*args, **kwargs):
        return PropertyVisionResult(
            description="Descripción válida",
            highlights=None,  # type: ignore[arg-type]
            weaknesses=[],
        )

    monkeypatch.setattr(properties, "describe_property_from_photos", _invalid_result)
    response = _generate(workflow)

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "vision_invalid_response"
    assert workflow.prop.extra == {}
    assert workflow.db.commits == 0


@pytest.mark.parametrize("description", ["", "   ", "x" * 12_001])
def test_blank_or_oversized_reviewed_text_is_rejected(workflow, description):
    draft = _generate(workflow).json()
    response = workflow.http.put(
        f"{_base(workflow)}/description",
        json={"draft_id": draft["id"], "description": description},
    )

    assert response.status_code == 422
    assert workflow.prop.description == "Descripción anterior"
    assert workflow.prop.extra[properties.DESCRIPTION_DRAFT_KEY]["id"] == draft["id"]


def test_undo_refuses_to_overwrite_a_later_manual_change(workflow):
    draft = _generate(workflow).json()
    applied = workflow.http.put(
        f"{_base(workflow)}/description",
        json={"draft_id": draft["id"], "description": "Texto aplicado"},
    ).json()
    workflow.prop.description = "Edición manual posterior"

    response = workflow.http.post(
        f"{_base(workflow)}/description-history/{applied['undo_id']}/restore",
        json={},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "description_changed_since_history"
    assert workflow.prop.description == "Edición manual posterior"
