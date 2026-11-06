"""Local file serving — DEV ONLY.

In production we never hit this; storage.py returns S3 presigned URLs.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from app.core.settings import settings
from app.services.storage import LOCAL_ROOT

router = APIRouter(prefix="/files", tags=["files"])


@router.get("/{file_path:path}")
async def serve(file_path: str) -> FileResponse:
    if settings.app_env == "production":
        # Prod serves files via S3 presigned URLs; this route must stay dark.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    target = (LOCAL_ROOT / file_path).resolve()
    # Path-traversal guard: must stay under LOCAL_ROOT.
    try:
        target.relative_to(LOCAL_ROOT)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad_path") from None
    if not target.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")

    media = "application/pdf" if target.suffix == ".pdf" else "application/octet-stream"
    return FileResponse(str(target), media_type=media)


# Suppress: Path imported for documentation context
_ = Path
