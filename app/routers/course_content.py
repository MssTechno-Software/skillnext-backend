# from fastapi import APIRouter, Depends, HTTPException, status
# from pydantic import TypeAdapter, ValidationError
# from sqlalchemy import func, select
# from sqlalchemy.orm import Session

# from app.content_blocks import Block, build_blocks, parse_text
# from app.database import get_db
# from app.dependencies import get_current_user, require_role
# from app.models import Course, CourseModule, ModuleContent, User
# from app.routers.courses import _get_or_404 as get_course_or_404
# from app.routers.courses import _require_owner
# from app.schemas import (
#     ApiResponse,
#     ContentCreate,
#     ContentOut,
#     ContentUpdate,
#     CourseContentData,
#     CourseOut,
#     ModuleCreate,
#     ModuleOut,
#     ModuleUpdate,
#     ModuleWithContent,
# )

# router = APIRouter(prefix="/api", tags=["course-content"])


# # ----------------------------------------------------------------- helpers
# def _is_teacher(user: User) -> bool:
#     return user.role == "teacher"


# def _get_module(db: Session, module_id: int, user: User) -> CourseModule:
#     module = db.get(CourseModule, module_id)
#     if module is None or (not _is_teacher(user) and not module.visible):
#         raise HTTPException(status.HTTP_404_NOT_FOUND, "Module not found.")
#     get_course_or_404(db, module.course_id, user)  # course/category visibility
#     return module


# def _get_content(db: Session, content_id: int, user: User) -> ModuleContent:
#     content = db.get(ModuleContent, content_id)
#     if content is None or (not _is_teacher(user) and not content.visible):
#         raise HTTPException(status.HTTP_404_NOT_FOUND, "Content not found.")
#     _get_module(db, content.module_id, user)
#     return content


# def _validate_content(content_type: str, body: str | None, url: str | None):
#     if content_type == "text":
#         return  # a text page may be empty - the teacher fills it in later
#     if not url:
#         raise HTTPException(
#             status.HTTP_422_UNPROCESSABLE_ENTITY,
#             f"URL is required for {content_type} content.",
#         )
#     if not url.lower().startswith(("http://", "https://")):
#         raise HTTPException(
#             status.HTTP_422_UNPROCESSABLE_ENTITY,
#             "URL must start with http:// or https://",
#         )


# def _next_position(db: Session, column, where) -> int:
#     return (db.scalar(select(func.max(column)).where(where)) or 0) + 1


# _blocks_adapter = TypeAdapter(list[Block])


# def _resolve_text(content_type: str, body: str | None, blocks):
#     """Validate text structure. Returns (body, blocks) ready to store."""
#     if content_type != "text":
#         if blocks:
#             raise HTTPException(
#                 status.HTTP_422_UNPROCESSABLE_ENTITY,
#                 "Blocks are only allowed for text content.",
#             )
#         return body, None
#     try:
#         return build_blocks(body, blocks)
#     except ValueError as exc:
#         raise HTTPException(
#             status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid content structure. {exc}"
#         )


# def content_out(content: ModuleContent) -> ContentOut:
#     """ContentOut, with blocks derived from `body` for items saved before blocks existed."""
#     out = ContentOut.model_validate(content)
#     if content.content_type == "text" and content.blocks is None and content.body:
#         try:
#             out.blocks = _blocks_adapter.validate_python(parse_text(content.body))
#         except ValidationError:
#             pass  # leave blocks empty; body is still returned
#     return out


# # ========================================================== MODULES (sections)
# @router.post(
#     "/courses/{course_id}/modules",
#     response_model=ApiResponse[ModuleOut],
#     status_code=status.HTTP_201_CREATED,
# )
# def create_module(
#     course_id: int,
#     payload: ModuleCreate,
#     db: Session = Depends(get_db),
#     user: User = Depends(require_role("teacher")),
# ):
#     course = get_course_or_404(db, course_id, user)
#     _require_owner(course, user)

#     data = payload.model_dump()
#     if data["position"] is None:
#         data["position"] = _next_position(
#             db, CourseModule.position, CourseModule.course_id == course.id
#         )
#     module = CourseModule(course_id=course.id, **data)
#     db.add(module)
#     db.commit()
#     return ApiResponse(message="Module created.", data=ModuleOut.model_validate(module))


# @router.get("/courses/{course_id}/modules", response_model=ApiResponse[list[ModuleOut]])
# def list_modules(
#     course_id: int,
#     db: Session = Depends(get_db),
#     user: User = Depends(get_current_user),
# ):
#     course = get_course_or_404(db, course_id, user)
#     q = select(CourseModule).where(CourseModule.course_id == course.id)
#     if not _is_teacher(user):
#         q = q.where(CourseModule.visible.is_(True))
#     q = q.order_by(CourseModule.position, CourseModule.id)
#     return ApiResponse(data=[ModuleOut.model_validate(m) for m in db.scalars(q)])


# @router.get("/courses/{course_id}/content", response_model=ApiResponse[CourseContentData])
# def course_content(
#     course_id: int,
#     db: Session = Depends(get_db),
#     user: User = Depends(get_current_user),
# ):
#     """The full course page: every module with its content items, in order."""
#     course = get_course_or_404(db, course_id, user)
#     teacher = _is_teacher(user)

#     modules = []
#     for m in course.modules:  # already ordered by position
#         if not teacher and not m.visible:
#             continue
#         items = [
#             content_out(c)
#             for c in m.contents
#             if teacher or c.visible
#         ]
#         modules.append(
#             ModuleWithContent(**ModuleOut.model_validate(m).model_dump(), contents=items)
#         )
#     return ApiResponse(
#         data=CourseContentData(course=CourseOut.model_validate(course), modules=modules)
#     )


# @router.get("/modules/{module_id}", response_model=ApiResponse[ModuleWithContent])
# def get_module(
#     module_id: int,
#     db: Session = Depends(get_db),
#     user: User = Depends(get_current_user),
# ):
#     m = _get_module(db, module_id, user)
#     teacher = _is_teacher(user)
#     items = [content_out(c) for c in m.contents if teacher or c.visible]
#     return ApiResponse(
#         data=ModuleWithContent(**ModuleOut.model_validate(m).model_dump(), contents=items)
#     )


# @router.patch("/modules/{module_id}", response_model=ApiResponse[ModuleOut])
# def update_module(
#     module_id: int,
#     payload: ModuleUpdate,
#     db: Session = Depends(get_db),
#     user: User = Depends(require_role("teacher")),
# ):
#     module = _get_module(db, module_id, user)
#     _require_owner(get_course_or_404(db, module.course_id, user), user)

#     changes = payload.model_dump(exclude_unset=True)
#     for field in ("name", "position", "visible"):
#         if field in changes and changes[field] is None:
#             raise HTTPException(
#                 status.HTTP_422_UNPROCESSABLE_ENTITY, f"{field} cannot be null."
#             )
#     for key, value in changes.items():
#         setattr(module, key, value)
#     db.commit()
#     return ApiResponse(message="Module updated.", data=ModuleOut.model_validate(module))


# @router.delete("/modules/{module_id}")
# def delete_module(
#     module_id: int,
#     db: Session = Depends(get_db),
#     user: User = Depends(require_role("teacher")),
# ):
#     module = _get_module(db, module_id, user)
#     _require_owner(get_course_or_404(db, module.course_id, user), user)
#     db.delete(module)  # its content items are deleted with it
#     db.commit()
#     return {"success": True, "message": "Module deleted."}


# # ============================================================ CONTENT ITEMS
# @router.post(
#     "/modules/{module_id}/contents",
#     response_model=ApiResponse[ContentOut],
#     status_code=status.HTTP_201_CREATED,
# )
# def create_content(
#     module_id: int,
#     payload: ContentCreate,
#     db: Session = Depends(get_db),
#     user: User = Depends(require_role("teacher")),
# ):
#     module = _get_module(db, module_id, user)
#     _require_owner(get_course_or_404(db, module.course_id, user), user)
#     _validate_content(payload.content_type, payload.body, payload.url)

#     data = payload.model_dump(exclude={"blocks"})
#     data["body"], data["blocks"] = _resolve_text(
#         payload.content_type, payload.body, payload.blocks
#     )
#     if data["position"] is None:
#         data["position"] = _next_position(
#             db, ModuleContent.position, ModuleContent.module_id == module.id
#         )
#     content = ModuleContent(module_id=module.id, **data)
#     db.add(content)
#     db.commit()
#     return ApiResponse(message="Content added.", data=content_out(content))


# @router.get("/contents/{content_id}", response_model=ApiResponse[ContentOut])
# def get_content(
#     content_id: int,
#     db: Session = Depends(get_db),
#     user: User = Depends(get_current_user),
# ):
#     return ApiResponse(data=content_out(_get_content(db, content_id, user)))


# @router.patch("/contents/{content_id}", response_model=ApiResponse[ContentOut])
# def update_content(
#     content_id: int,
#     payload: ContentUpdate,
#     db: Session = Depends(get_db),
#     user: User = Depends(require_role("teacher")),
# ):
#     content = _get_content(db, content_id, user)
#     module = db.get(CourseModule, content.module_id)
#     _require_owner(get_course_or_404(db, module.course_id, user), user)

#     changes = payload.model_dump(exclude_unset=True, exclude={"blocks"})
#     for field in ("title", "content_type", "position", "visible"):
#         if field in changes and changes[field] is None:
#             raise HTTPException(
#                 status.HTTP_422_UNPROCESSABLE_ENTITY, f"{field} cannot be null."
#             )

#     _validate_content(
#         changes.get("content_type", content.content_type),
#         changes.get("body", content.body),
#         changes.get("url", content.url),
#     )

#     # Re-validate the text structure whenever body, blocks or the type changes.
#     new_type = changes.get("content_type", content.content_type)
#     if payload.blocks is not None:
#         changes["body"], changes["blocks"] = _resolve_text(new_type, None, payload.blocks)
#     elif "body" in changes or "content_type" in changes:
#         changes["body"], changes["blocks"] = _resolve_text(
#             new_type, changes.get("body", content.body), None
#         )

#     for key, value in changes.items():
#         setattr(content, key, value)
#     db.commit()
#     return ApiResponse(message="Content updated.", data=content_out(content))


# @router.delete("/contents/{content_id}")
# def delete_content(
#     content_id: int,
#     db: Session = Depends(get_db),
#     user: User = Depends(require_role("teacher")),
# ):
#     content = _get_content(db, content_id, user)
#     module = db.get(CourseModule, content.module_id)
#     _require_owner(get_course_or_404(db, module.course_id, user), user)
#     db.delete(content)
#     db.commit()
#     return {"success": True, "message": "Content deleted."}

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import drive
from app.content_blocks import Block, build_blocks, parse_text
from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models import Course, CourseModule, ModuleContent, User
from app.routers.courses import _get_or_404 as get_course_or_404
from app.routers.courses import _require_owner
from app.schemas import (
    ApiResponse,
    ContentCreate,
    ContentOut,
    ContentUpdate,
    CourseContentData,
    CourseOut,
    ModuleCreate,
    ModuleOut,
    ModuleUpdate,
    ModuleWithContent,
)

router = APIRouter(prefix="/api", tags=["course-content"])


# ----------------------------------------------------------------- helpers
def _is_teacher(user: User) -> bool:
    return user.role == "teacher"


def _get_module(db: Session, module_id: int, user: User) -> CourseModule:
    module = db.get(CourseModule, module_id)
    if module is None or (not _is_teacher(user) and not module.visible):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Module not found.")
    get_course_or_404(db, module.course_id, user)  # course/category visibility
    return module


def _get_content(db: Session, content_id: int, user: User) -> ModuleContent:
    content = db.get(ModuleContent, content_id)
    if content is None or (not _is_teacher(user) and not content.visible):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Content not found.")
    _get_module(db, content.module_id, user)
    return content


def _validate_content(content_type: str, body: str | None, url: str | None):
    if content_type == "text":
        return  # a text page may be empty - the teacher fills it in later
    if not url:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"URL is required for {content_type} content.",
        )
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "URL must start with http:// or https://",
        )


def _next_position(db: Session, column, where) -> int:
    return (db.scalar(select(func.max(column)).where(where)) or 0) + 1


_blocks_adapter = TypeAdapter(list[Block])


def _drop_drive_files(contents):
    for c in contents:
        if c.drive_file_id:
            try:
                drive.delete_file(c.drive_file_id)
            except Exception:
                pass  # already gone or Drive unavailable; the DB row is removed anyway


def _resolve_text(content_type: str, body: str | None, blocks):
    """Validate text structure. Returns (body, blocks) ready to store."""
    if content_type != "text":
        if blocks:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "Blocks are only allowed for text content.",
            )
        return body, None
    try:
        return build_blocks(body, blocks)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid content structure. {exc}"
        )


def content_out(content: ModuleContent) -> ContentOut:
    """ContentOut, with blocks derived from `body` for items saved before blocks existed."""
    out = ContentOut.model_validate(content)
    if content.drive_file_id:
        out.download_url = drive.download_url(content.drive_file_id)
        out.preview_url = drive.preview_url(content.drive_file_id)
    if content.content_type == "text" and content.blocks is None and content.body:
        try:
            out.blocks = _blocks_adapter.validate_python(parse_text(content.body))
        except ValidationError:
            pass  # leave blocks empty; body is still returned
    return out


# ========================================================== MODULES (sections)
@router.post(
    "/courses/{course_id}/modules",
    response_model=ApiResponse[ModuleOut],
    status_code=status.HTTP_201_CREATED,
)
def create_module(
    course_id: int,
    payload: ModuleCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    course = get_course_or_404(db, course_id, user)
    _require_owner(course, user)

    data = payload.model_dump()
    if data["position"] is None:
        data["position"] = _next_position(
            db, CourseModule.position, CourseModule.course_id == course.id
        )
    module = CourseModule(course_id=course.id, **data)
    db.add(module)
    db.commit()
    return ApiResponse(message="Module created.", data=ModuleOut.model_validate(module))


@router.get("/courses/{course_id}/modules", response_model=ApiResponse[list[ModuleOut]])
def list_modules(
    course_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    course = get_course_or_404(db, course_id, user)
    q = select(CourseModule).where(CourseModule.course_id == course.id)
    if not _is_teacher(user):
        q = q.where(CourseModule.visible.is_(True))
    q = q.order_by(CourseModule.position, CourseModule.id)
    return ApiResponse(data=[ModuleOut.model_validate(m) for m in db.scalars(q)])


@router.get("/courses/{course_id}/content", response_model=ApiResponse[CourseContentData])
def course_content(
    course_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The full course page: every module with its content items, in order."""
    course = get_course_or_404(db, course_id, user)
    teacher = _is_teacher(user)

    modules = []
    for m in course.modules:  # already ordered by position
        if not teacher and not m.visible:
            continue
        items = [
            content_out(c)
            for c in m.contents
            if teacher or c.visible
        ]
        modules.append(
            ModuleWithContent(**ModuleOut.model_validate(m).model_dump(), contents=items)
        )
    return ApiResponse(
        data=CourseContentData(course=CourseOut.model_validate(course), modules=modules)
    )


@router.get("/modules/{module_id}", response_model=ApiResponse[ModuleWithContent])
def get_module(
    module_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    m = _get_module(db, module_id, user)
    teacher = _is_teacher(user)
    items = [content_out(c) for c in m.contents if teacher or c.visible]
    return ApiResponse(
        data=ModuleWithContent(**ModuleOut.model_validate(m).model_dump(), contents=items)
    )


@router.patch("/modules/{module_id}", response_model=ApiResponse[ModuleOut])
def update_module(
    module_id: int,
    payload: ModuleUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    module = _get_module(db, module_id, user)
    _require_owner(get_course_or_404(db, module.course_id, user), user)

    changes = payload.model_dump(exclude_unset=True)
    for field in ("name", "position", "visible"):
        if field in changes and changes[field] is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"{field} cannot be null."
            )
    for key, value in changes.items():
        setattr(module, key, value)
    db.commit()
    return ApiResponse(message="Module updated.", data=ModuleOut.model_validate(module))


@router.delete("/modules/{module_id}")
def delete_module(
    module_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    module = _get_module(db, module_id, user)
    _require_owner(get_course_or_404(db, module.course_id, user), user)
    _drop_drive_files(module.contents)
    db.delete(module)  # its content items are deleted with it
    db.commit()
    return {"success": True, "message": "Module deleted."}


# ============================================================ CONTENT ITEMS
@router.post(
    "/modules/{module_id}/contents",
    response_model=ApiResponse[ContentOut],
    status_code=status.HTTP_201_CREATED,
)
def create_content(
    module_id: int,
    payload: ContentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    module = _get_module(db, module_id, user)
    _require_owner(get_course_or_404(db, module.course_id, user), user)
    _validate_content(payload.content_type, payload.body, payload.url)

    data = payload.model_dump(exclude={"blocks"})
    data["body"], data["blocks"] = _resolve_text(
        payload.content_type, payload.body, payload.blocks
    )
    if data["position"] is None:
        data["position"] = _next_position(
            db, ModuleContent.position, ModuleContent.module_id == module.id
        )
    content = ModuleContent(module_id=module.id, **data)
    db.add(content)
    db.commit()
    return ApiResponse(message="Content added.", data=content_out(content))


@router.get("/contents/{content_id}", response_model=ApiResponse[ContentOut])
def get_content(
    content_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return ApiResponse(data=content_out(_get_content(db, content_id, user)))


@router.patch("/contents/{content_id}", response_model=ApiResponse[ContentOut])
def update_content(
    content_id: int,
    payload: ContentUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    content = _get_content(db, content_id, user)
    module = db.get(CourseModule, content.module_id)
    _require_owner(get_course_or_404(db, module.course_id, user), user)

    changes = payload.model_dump(exclude_unset=True, exclude={"blocks"})
    for field in ("title", "content_type", "position", "visible"):
        if field in changes and changes[field] is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"{field} cannot be null."
            )

    _validate_content(
        changes.get("content_type", content.content_type),
        changes.get("body", content.body),
        changes.get("url", content.url),
    )

    # Re-validate the text structure whenever body, blocks or the type changes.
    new_type = changes.get("content_type", content.content_type)
    if payload.blocks is not None:
        changes["body"], changes["blocks"] = _resolve_text(new_type, None, payload.blocks)
    elif "body" in changes or "content_type" in changes:
        changes["body"], changes["blocks"] = _resolve_text(
            new_type, changes.get("body", content.body), None
        )

    for key, value in changes.items():
        setattr(content, key, value)
    db.commit()
    return ApiResponse(message="Content updated.", data=content_out(content))


@router.delete("/contents/{content_id}")
def delete_content(
    content_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    content = _get_content(db, content_id, user)
    module = db.get(CourseModule, content.module_id)
    _require_owner(get_course_or_404(db, module.course_id, user), user)
    _drop_drive_files([content])
    db.delete(content)
    db.commit()
    return {"success": True, "message": "Content deleted."}