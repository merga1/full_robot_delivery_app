"""Authenticated image uploads used by the administrator catalogue."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from ..dependencies import Principal, require_admin

router = APIRouter(prefix="/api/v1/uploads", tags=["uploads"])

MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def _web_root() -> Path:
    """Find the project web directory in both source and Docker layouts."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "web"
        if candidate.is_dir():
            return candidate
    raise RuntimeError("The web directory is not available")


@router.post("/menu-image", status_code=status.HTTP_201_CREATED)
async def upload_menu_image(
    file: UploadFile = File(...),
    principal: Principal = Depends(require_admin),
) -> dict[str, str]:
    """Store a menu image and return its public static URL."""
    del principal
    original_name = file.filename or ""
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Use a JPG, PNG, or WEBP image.",
        )
    # Some PowerShell multipart clients send valid image files as
    # ``application/octet-stream``.  The extension has already been
    # restricted above, so accept that fallback while rejecting other types.
    if file.content_type and not (
        file.content_type.startswith("image/")
        or file.content_type == "application/octet-stream"
    ):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="The uploaded file must be an image.",
        )

    content = await file.read(MAX_IMAGE_BYTES + 1)
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Images must be 5 MB or smaller.",
        )

    upload_dir = _web_root() / "assets" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}{extension}"
    (upload_dir / filename).write_bytes(content)
    return {"image_url": f"/assets/uploads/{filename}", "filename": filename}
