"""Adversarial checks for property-photo materialization.

Photo references are tenant data and the describe endpoint also accepts an
override supplied directly by the caller.  Neither is authority to read a
path from the backend host.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.settings import settings
from app.db.session import get_db
from app.main import app
from app.services.property_vision import _materialize_images, _safe_remote_image_url


@pytest.mark.parametrize(
    "value",
    [
        r"C:\\secrets\\backend.env",
        "/etc/passwd",
        "../.env",
        "file:///etc/passwd",
        "data:image/png;base64,ZmFrZQ==",
        "ftp://cdn.example/photo.jpg",
        "http://127.0.0.1/photo.jpg",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/photo.jpg",
        "http://localhost/photo.jpg",
        "https://user:password@cdn.example/photo.jpg",
    ],
)
def test_local_and_unsafe_resources_are_not_image_urls(value):
    assert _safe_remote_image_url(value) is None


def test_public_remote_image_is_preserved():
    url = "https://cdn.example/tenant-a/property-12/front.jpg?signature=short-lived"
    assert _safe_remote_image_url(url) == url
    assert _materialize_images([url]) == [
        {"type": "image", "source": {"type": "url", "url": url}}
    ]


def test_one_unsafe_value_fails_the_whole_photo_set():
    with pytest.raises(ValueError, match="unsafe_image_url"):
        _materialize_images(
            ["https://cdn.example/ok.jpg", r"C:\\run\\secrets\\token"]
        )


class _Session:
    def __init__(self, property_):
        self.property = property_

    async def get(self, model, pk):
        return self.property if pk == self.property.id else None


@pytest.fixture
def vision_client(monkeypatch):
    agency_id = uuid.uuid4()
    property_id = uuid.uuid4()
    property_ = SimpleNamespace(
        id=property_id,
        agency_id=agency_id,
        image_urls=["https://cdn.example/stored.jpg"],
    )
    session = _Session(property_)

    async def _db():
        yield session

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")
    app.dependency_overrides[get_db] = _db
    yield SimpleNamespace(
        http=TestClient(app, raise_server_exceptions=False),
        agency_id=agency_id,
        property_id=property_id,
        property=property_,
    )
    app.dependency_overrides.pop(get_db, None)


def _describe_url(ctx) -> str:
    return (
        f"/api/v1/agencies/{ctx.agency_id}/properties/"
        f"{ctx.property_id}/describe-from-photos"
    )


def test_a_client_cannot_submit_a_backend_path(vision_client):
    response = vision_client.http.post(
        _describe_url(vision_client),
        json={"image_urls": [r"C:\\app\\.env"], "apply": False},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "unsafe_image_url"


def test_an_unsafe_stored_catalog_value_is_also_never_read(vision_client):
    vision_client.property.image_urls = ["/proc/self/environ"]
    response = vision_client.http.post(
        _describe_url(vision_client), json={"apply": False}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "unsafe_image_url"
