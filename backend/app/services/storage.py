"""File storage abstraction.

Local filesystem in dev (writes to `./data/storage/<key>`), S3-compatible
in prod (set `S3_BUCKET` and `S3_ENDPOINT_URL`). The returned URL is what
we send to the lead — for local dev that's a `/files/<key>` route served
by FastAPI; for prod it's a presigned S3 URL.
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from app.core.logging import get_logger
from app.core.settings import settings

log = get_logger(__name__)

LOCAL_ROOT = Path(settings.local_storage_root).resolve()


def save_bytes(key: str, data: bytes, *, content_type: str = "application/pdf") -> str:
    """Save bytes under `key` and return a public URL the lead can open."""
    if _s3_enabled():
        return _save_s3(key, data, content_type=content_type)
    return _save_local(key, data)


def _save_local(key: str, data: bytes) -> str:
    path = LOCAL_ROOT / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    public_base = settings.public_base_url
    return f"{public_base.rstrip('/')}/files/{key}"


def _s3_enabled() -> bool:
    return bool(settings.s3_bucket.strip())


def _save_s3(key: str, data: bytes, *, content_type: str) -> str:
    import boto3

    bucket = settings.s3_bucket.strip()
    endpoint = settings.s3_endpoint_url or None
    region = settings.s3_region
    client = boto3.client("s3", endpoint_url=endpoint, region_name=region)
    client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=60 * 60 * 24 * 7,
    )


def delete_prefix(prefix: str) -> int:
    """Delete every stored object below a tenant-scoped prefix.

    The caller supplies a key such as ``agencies/<uuid>``. Empty, absolute or
    parent-traversing prefixes are rejected because this function is used by
    the retention sweep and therefore has deliberately destructive semantics.
    The operation is idempotent and returns the number of removed objects.
    """
    normalized = prefix.strip().strip("/").replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError("storage deletion requires a safe non-empty prefix")
    if _s3_enabled():
        return _delete_s3_prefix("/".join(parts) + "/")
    return _delete_local_prefix(parts)


def delete_tenant_key_prefix(agency_id: uuid.UUID, key_prefix: str) -> int:
    """Delete objects whose key starts with a lead-scoped tenant prefix.

    Unlike :func:`delete_prefix`, this deliberately does not append ``/``:
    generated property cards are named ``cards/<lead>_<property>.pdf`` and
    there is no database row for each card.  Binding the agency id as a typed
    argument keeps this narrower primitive from becoming a cross-tenant
    wildcard deletion API.
    """
    suffix = key_prefix.strip().strip("/").replace("\\", "/")
    parts = [part for part in suffix.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError("storage key-prefix deletion requires a safe suffix")
    tenant_parts = ["agencies", str(agency_id), *parts]
    prefix = "/".join(tenant_parts)
    if _s3_enabled():
        return _delete_s3_prefix(prefix)
    return _delete_local_key_prefix(tenant_parts)


def _delete_local_prefix(parts: list[str]) -> int:
    target = LOCAL_ROOT.joinpath(*parts).resolve()
    try:
        target.relative_to(LOCAL_ROOT)
    except ValueError as exc:
        raise ValueError("storage deletion escaped LOCAL_ROOT") from exc
    if not target.exists():
        return 0
    files = sum(1 for path in target.rglob("*") if path.is_file())
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return files


def _delete_local_key_prefix(parts: list[str]) -> int:
    parent = LOCAL_ROOT.joinpath(*parts[:-1]).resolve()
    try:
        parent.relative_to(LOCAL_ROOT)
    except ValueError as exc:
        raise ValueError("storage deletion escaped LOCAL_ROOT") from exc
    if not parent.is_dir():
        return 0

    name_prefix = parts[-1]
    matches = [child for child in parent.iterdir() if child.name.startswith(name_prefix)]
    deleted = 0
    for child in matches:
        if child.is_dir():
            deleted += sum(1 for path in child.rglob("*") if path.is_file())
            shutil.rmtree(child)
        else:
            child.unlink()
            deleted += 1
    return deleted


def _delete_s3_prefix(prefix: str) -> int:
    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        region_name=settings.s3_region,
    )
    bucket = settings.s3_bucket.strip()
    deleted = 0
    continuation_token: str | None = None
    while True:
        request: dict[str, str] = {
            "Bucket": bucket,
            "Prefix": prefix,
        }
        if continuation_token:
            request["ContinuationToken"] = continuation_token
        page = client.list_objects_v2(**request)
        keys = [
            item.get("Key")
            for item in page.get("Contents") or []
            if isinstance(item, dict) and item.get("Key")
        ]
        if keys:
            result = client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": key} for key in keys], "Quiet": True},
            )
            errors = result.get("Errors") if isinstance(result, dict) else None
            if errors:
                error_codes = sorted(
                    {
                        str(item.get("Code") or "unknown")
                        for item in errors
                        if isinstance(item, dict)
                    }
                )
                detail = ", ".join(error_codes) or "unknown"
                raise RuntimeError(
                    f"S3 failed to delete {len(errors)} object(s) ({detail})"
                )
            deleted += len(keys)
        if not page.get("IsTruncated"):
            return deleted
        continuation_token = page.get("NextContinuationToken")
        if not continuation_token:
            raise RuntimeError("S3 pagination did not provide a continuation token")
