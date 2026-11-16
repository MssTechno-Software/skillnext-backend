import logging
import mimetypes
import os
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app import drive
from app.config import settings
from app.database import get_db
from app.dependencies import require_role
from app.models import ModuleContent, User
from app.routers.course_content import _get_module, _next_position, content_out
from app.routers.courses import _get_or_404 as get_course_or_404
from app.routers.courses import _require_owner
from app.schemas import ApiResponse, ContentOut

router = APIRouter(prefix="/api", tags=["module-videos"])

ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".mkv"}
log = logging.getLogger(__name__)

# upload-files: anything is a document unless it is a video or can run on a machine
VIDEO_EXTENSIONS = ALLOWED_VIDEO_EXTENSIONS | {".avi", ".m4v", ".wmv", ".flv", ".mpeg", ".mpg", ".3gp"}
BLOCKED_EXTENSIONS = {
    ".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".dll", ".sh", ".ps1", ".vbs",
    ".jar", ".apk", ".app", ".php", ".html", ".htm",
}


def _size_mb(upload: UploadFile) -> float:
    upload.file.seek(0, os.SEEK_END)
    size = upload.file.tell()
    upload.file.seek(0)
    return size / (1024 * 1024)


@router.post(
    "/modules/{module_id}/upload-videos",
    response_model=ApiResponse[list[ContentOut]],
    status_code=status.HTTP_201_CREATED,
)
def upload_module_videos(
    module_id: int,
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    """Upload videos for a module to Drive and add each as a `video` content item."""
    module = _get_module(db, module_id, user)
    course = get_course_or_404(db, module.course_id, user)
    _require_owner(course, user)

    # Validate every file first, so nothing is uploaded if one of them is bad.
    for f in files:
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in ALLOWED_VIDEO_EXTENSIONS:
            raise HTTPException(400, f"Unsupported video type '{ext}'. Allowed: mp4, webm, mov, mkv.")
        if _size_mb(f) > settings.MAX_VIDEO_SIZE_MB:
            raise HTTPException(400, f"{f.filename} exceeds {settings.MAX_VIDEO_SIZE_MB}MB limit.")

    uploaded_ids: list[str] = []
    rows: list[ModuleContent] = []
    try:
        folder_id = drive.get_module_folder(
            f"{course.short_name}_{course.id}", f"{module.name}_{module.id}"
        )
        position = _next_position(db, ModuleContent.position, ModuleContent.module_id == module.id)
        for f in files:
            ext = os.path.splitext(f.filename)[1].lower()
            file_id = drive.upload_file(
                f.file, f"{Path(f.filename).stem}{ext}", f.content_type, folder_id
            )
            uploaded_ids.append(file_id)
            drive.make_public(file_id)  # students must be able to play it
            rows.append(
                ModuleContent(
                    module_id=module.id,
                    title=Path(f.filename).stem[:255],
                    content_type="video",
                    url=drive.view_url(file_id),
                    drive_file_id=file_id,
                    position=position + len(rows),
                    visible=True,
                )
            )
        db.add_all(rows)
        db.commit()
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        for fid in uploaded_ids:  # don't leave orphan files in Drive
            try:
                drive.delete_file(fid)
            except Exception:
                pass
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Video upload to Drive failed: {exc}")

    return ApiResponse(
        message=f"{len(rows)} video(s) uploaded.", data=[content_out(r) for r in rows]
    )


def _plan_file(f: UploadFile, title: str | None) -> dict:
    """Validate one file (nothing is uploaded yet) and decide video vs document."""
    name = os.path.basename(f.filename or "")
    ext = os.path.splitext(name)[1].lower()
    if not ext:
        raise HTTPException(400, f"'{name or 'file'}' has no file extension.")
    if ext in BLOCKED_EXTENSIONS:
        raise HTTPException(400, f"{ext} files are not allowed ({name}).")

    mimetype = f.content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
    kind = "video" if ext in VIDEO_EXTENSIONS or mimetype.startswith("video/") else "document"

    size_mb = _size_mb(f)
    if size_mb == 0:
        raise HTTPException(400, f"{name} is empty.")
    limit = settings.MAX_VIDEO_SIZE_MB if kind == "video" else settings.MAX_DOCUMENT_SIZE_MB
    if size_mb > limit:
        raise HTTPException(400, f"{name} exceeds {limit}MB limit for {kind} files.")

    title = (title or Path(name).stem).strip()
    if not title or len(title) > 255:
        raise HTTPException(422, f"Title for {name} must be 1-255 characters.")
    return {"file": f.file, "name": name[:255], "mimetype": mimetype, "kind": kind, "title": title}


@router.post(
    "/modules/{module_id}/upload-files",
    response_model=ApiResponse[list[ContentOut]],
    status_code=status.HTTP_201_CREATED,
)
def upload_module_files(
    module_id: int,
    files: List[UploadFile] = File(...),
    titles: List[str] = Form(default=[]),
    visible: bool = Form(default=True),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    """Upload videos AND documents for a module in one call (any document format).

    multipart/form-data: `files` (many, mixed), optional `titles` (one per file, same
    order), optional `visible`. Each file is added as a `video` or `document` item.
    """
    module = _get_module(db, module_id, user)
    course = get_course_or_404(db, module.course_id, user)
    _require_owner(course, user)

    if titles and len(titles) != len(files):
        raise HTTPException(422, "Send either no titles or one title per file.")
    plans = [_plan_file(f, titles[i] if titles else None) for i, f in enumerate(files)]

    uploaded_ids: list[str] = []
    rows: list[ModuleContent] = []
    try:
        folder_id = drive.get_module_folder(
            f"{course.short_name}_{course.id}", f"{module.name}_{module.id}"
        )
        position = _next_position(db, ModuleContent.position, ModuleContent.module_id == module.id)
        for plan in plans:
            file_id = drive.upload_file(plan["file"], plan["name"], plan["mimetype"], folder_id)
            uploaded_ids.append(file_id)
            drive.make_public(file_id)  # students must be able to open it
            rows.append(
                ModuleContent(
                    module_id=module.id,
                    title=plan["title"],
                    content_type=plan["kind"],
                    url=drive.view_url(file_id),
                    drive_file_id=file_id,
                    file_name=plan["name"],
                    position=position + len(rows),
                    visible=visible,
                )
            )
        db.add_all(rows)
        db.commit()
    except Exception as exc:
        db.rollback()
        for fid in uploaded_ids:  # all-or-nothing: don't leave orphan files in Drive
            try:
                drive.delete_file(fid)
            except Exception:
                log.warning("Could not clean up Drive file %s", fid)
        if isinstance(exc, HTTPException):
            raise
        log.exception("Drive upload failed")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Upload to Drive failed. Nothing was saved, please retry.")

    videos = sum(r.content_type == "video" for r in rows)
    return ApiResponse(
        message=f"{videos} video(s) and {len(rows) - videos} document(s) uploaded.",
        data=[content_out(r) for r in rows],
    )