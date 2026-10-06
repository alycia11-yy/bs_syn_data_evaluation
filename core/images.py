"""Serve image files to the browser without exposing the file system."""
from __future__ import annotations

import io
from pathlib import Path

from starlette.responses import FileResponse, Response

_BROWSER_SAFE = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_NO_STORE = {"Cache-Control": "private, no-store"}


def image_response(path: Path, root: Path) -> Response:
    """Return the image at `path` if it lies inside `root`; TIFF is converted to PNG."""
    try:
        resolved = path.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return Response(status_code=404)
    if not resolved.is_file():
        return Response(status_code=404)
    if resolved.suffix.lower() in _BROWSER_SAFE:
        return FileResponse(resolved, headers=_NO_STORE)
    return Response(to_png_bytes(resolved), media_type="image/png", headers=_NO_STORE)


def to_png_bytes(path: Path) -> bytes:
    from PIL import Image

    with Image.open(path) as img:
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()
