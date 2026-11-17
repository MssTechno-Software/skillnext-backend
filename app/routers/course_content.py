from fastapi import APIRouter, Depends, HTTPException, status,Response
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from typing import Counter
from app import drive
from app.content_blocks import Block, build_blocks, parse_text
from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models import Course, CourseModule, ModuleContent, User
from app.routers.courses import _get_or_404 as get_course_or_404
from app.routers.courses import _require_owner
from pydantic import Field
# from app.models import Course, CourseModule, Enrollment, ModuleContent, User, ContentProgress
from sqlalchemy.exc import IntegrityError
from app.models import Course, CourseModule, Enrollment, ModuleContent, User
from app.schemas import CamelModel, MyCourseOut  # add to your existing schemas import
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
    ContentSummary,
    ModuleOverview,
    CourseOverviewData
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

@router.get(
    "/courses/{course_id}/overview",
    response_model=ApiResponse[CourseOverviewData],
)
def course_overview(
    course_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Course summary: totals, per-type counts, and every module with its content list."""
    course = get_course_or_404(db, course_id, user)
    teacher = _is_teacher(user)

    modules = []
    total_contents = 0
    hidden_modules = 0
    hidden_contents = 0
    course_types: Counter = Counter()

    for m in course.modules:  # already ordered by position
        if not m.visible:
            hidden_modules += 1
            if not teacher:
                continue

        items = []
        module_types: Counter = Counter()
        for c in m.contents:
            if not c.visible:
                hidden_contents += 1
                if not teacher:
                    continue
            summary = ContentSummary.model_validate(c)
            if c.drive_file_id:
                summary.download_url = drive.download_url(c.drive_file_id)
                summary.preview_url = drive.preview_url(c.drive_file_id)
            items.append(summary)
            module_types[c.content_type] += 1

        course_types.update(module_types)
        total_contents += len(items)
        modules.append(
            ModuleOverview(
                **ModuleOut.model_validate(m).model_dump(),
                total_contents=len(items),
                contents_by_type=dict(module_types),
                contents=items,
            )
        )

    return ApiResponse(
        data=CourseOverviewData(
            course=CourseOut.model_validate(course),
            total_modules=len(modules),
            total_contents=total_contents,
            contents_by_type=dict(course_types),
            hidden_modules=hidden_modules if teacher else None,
            hidden_contents=hidden_contents if teacher else None,
            modules=modules,
        )
    )


# ===================================================== SELF-ENROLL (student)
class SelfEnrollRequest(CamelModel):
    course_ids: list[int] = Field(min_length=1, max_length=50)


class SelfEnrollSkipped(CamelModel):
    course_id: int
    reason: str


class SelfEnrollResult(CamelModel):
    enrolled: list[MyCourseOut]
    already_enrolled: list[int]
    skipped: list[SelfEnrollSkipped]


@router.post(
    "/courses/enroll/me",
    response_model=ApiResponse[SelfEnrollResult],
    status_code=status.HTTP_201_CREATED,
)
def self_enroll(
    payload: SelfEnrollRequest,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    """Student adds one or many courses of their choice to their own account.

    Never half-fails: unknown/hidden courses and ones already added are reported
    back and the rest are enrolled.
    """
    course_ids = list(dict.fromkeys(payload.course_ids))

    courses = {c.id: c for c in db.scalars(select(Course).where(Course.id.in_(course_ids)))}
    existing = set(
        db.scalars(
            select(Enrollment.course_id).where(
                Enrollment.student_id == user.id,
                Enrollment.course_id.in_(course_ids),
            )
        )
    )

    new_rows: list[Enrollment] = []
    already: list[int] = []
    skipped: list[SelfEnrollSkipped] = []

    for cid in course_ids:
        course = courses.get(cid)
        if course is None or not (course.visible and course.category.visible):
            skipped.append(SelfEnrollSkipped(course_id=cid, reason="Course not found."))
        elif cid in existing:
            already.append(cid)
        else:
            new_rows.append(
                Enrollment(course_id=cid, student_id=user.id, enrolled_by=user.id)
            )

    try:
        db.add_all(new_rows)
        db.commit()
    except IntegrityError:  # same course added twice at the same moment
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Enrollment changed while saving, please retry."
        )

    if not new_rows:
        response.status_code = status.HTTP_200_OK

    message = f"{len(new_rows)} course(s) added."
    if already:
        message += f" {len(already)} already added."
    if skipped:
        message += f" {len(skipped)} skipped."

    return ApiResponse(
        message=message,
        data=SelfEnrollResult(
            enrolled=[MyCourseOut.model_validate(e) for e in new_rows],
            already_enrolled=already,
            skipped=skipped,
        ),
    )


from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import Field
from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, contains_eager, mapped_column

from app.database import Base
from app.models import Category, Course, CourseModule, Enrollment, ModuleContent, User, utcnow
from app.schemas import ApiResponse, CamelModel, CourseOut  # keep your other schema imports too


# ================================================== PROGRESS / MY LEARNINGS
# class ContentProgress(Base):
#     """A student marked one content item as completed (one row per student per item)."""

#     __tablename__ = "content_progress"
#     __table_args__ = (
#         UniqueConstraint("student_id", "content_id", name="uq_content_progress_student_content"),
#     )

#     id: Mapped[int] = mapped_column(Integer, primary_key=True)
#     student_id: Mapped[int] = mapped_column(
#         ForeignKey("users.id", ondelete="CASCADE"), index=True
#     )
#     content_id: Mapped[int] = mapped_column(
#         ForeignKey("module_contents.id", ondelete="CASCADE"), index=True
#     )
#     course_id: Mapped[int] = mapped_column(
#         ForeignKey("courses.id", ondelete="CASCADE"), index=True
#     )
#     completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CourseProgress(CamelModel):
    course_id: int
    total_items: int
    completed_items: int
    percentage: float
    status: str  # not_started | in_progress | completed
    completed_content_ids: list[int] = []


class LearningOut(CamelModel):
    course: CourseOut
    enrolled_at: datetime
    total_items: int
    completed_items: int
    percentage: float
    status: str


def _status(done: int, total: int) -> str:
    if total > 0 and done >= total:
        return "completed"
    return "in_progress" if done > 0 else "not_started"


def _percentage(done: int, total: int) -> float:
    return round(done * 100 / total, 1) if total else 0.0


def _progress_counts(db: Session, student_id: int, course_ids: list[int]) -> dict[int, tuple[int, int]]:
    """{course_id: (total_items, completed_items)} - only visible modules/items count."""
    if not course_ids:
        return {}
    visible_items = (
        select(CourseModule.course_id, ModuleContent.id.label("content_id"))
        .join(ModuleContent, ModuleContent.module_id == CourseModule.id)
        .where(
            CourseModule.course_id.in_(course_ids),
            CourseModule.visible.is_(True),
            ModuleContent.visible.is_(True),
        )
        .subquery()
    )
    totals = dict(
        db.execute(
            select(visible_items.c.course_id, func.count()).group_by(visible_items.c.course_id)
        ).all()
    )
    done = dict(
        db.execute(
            select(visible_items.c.course_id, func.count())
            .join(ContentProgress, ContentProgress.content_id == visible_items.c.content_id)
            .where(ContentProgress.student_id == student_id)
            .group_by(visible_items.c.course_id)
        ).all()
    )
    return {cid: (totals.get(cid, 0), done.get(cid, 0)) for cid in course_ids}


def _course_progress(db: Session, student_id: int, course_id: int) -> CourseProgress:
    total, done = _progress_counts(db, student_id, [course_id])[course_id]
    ids = list(
        db.scalars(
            select(ContentProgress.content_id)
            .join(ModuleContent, ModuleContent.id == ContentProgress.content_id)
            .join(CourseModule, CourseModule.id == ModuleContent.module_id)
            .where(
                ContentProgress.student_id == student_id,
                CourseModule.course_id == course_id,
                CourseModule.visible.is_(True),
                ModuleContent.visible.is_(True),
            )
        )
    )
    return CourseProgress(
        course_id=course_id,
        total_items=total,
        completed_items=done,
        percentage=_percentage(done, total),
        status=_status(done, total),
        completed_content_ids=ids,
    )


def _enrolled_content(db: Session, content_id: int, user: User) -> ModuleContent:
    content = _get_content(db, content_id, user)  # 404 for hidden items
    module = db.get(CourseModule, content.module_id)
    enrolled = db.scalar(
        select(Enrollment.id).where(
            Enrollment.course_id == module.course_id, Enrollment.student_id == user.id
        )
    )
    if enrolled is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Enroll in this course first.")
    return content


# @router.post("/contents/{content_id}/complete", response_model=ApiResponse[CourseProgress])
# def mark_content_complete(
#     content_id: int,
#     db: Session = Depends(get_db),
#     user: User = Depends(require_role("student")),
# ):
#     """Student marks one lesson/video/document as done. Safe to call twice."""
#     content = _enrolled_content(db, content_id, user)
#     course_id = db.get(CourseModule, content.module_id).course_id

#     already = db.scalar(
#         select(ContentProgress.id).where(
#             ContentProgress.student_id == user.id, ContentProgress.content_id == content.id
#         )
#     )
#     if already is None:
#         try:
#             db.add(ContentProgress(student_id=user.id, content_id=content.id, course_id=course_id))
#             db.commit()
#         except IntegrityError:  # double click - already saved
#             db.rollback()

#     progress = _course_progress(db, user.id, course_id)
#     msg = "Course completed." if progress.status == "completed" else "Marked as complete."
#     return ApiResponse(message=msg, data=progress)


@router.delete("/contents/{content_id}/complete", response_model=ApiResponse[CourseProgress])
def mark_content_incomplete(
    content_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    content = _enrolled_content(db, content_id, user)
    course_id = db.get(CourseModule, content.module_id).course_id
    row = db.scalar(
        select(ContentProgress).where(
            ContentProgress.student_id == user.id, ContentProgress.content_id == content.id
        )
    )
    if row is not None:
        db.delete(row)
        db.commit()
    return ApiResponse(
        message="Marked as not complete.", data=_course_progress(db, user.id, course_id)
    )


@router.get("/courses/{course_id}/progress", response_model=ApiResponse[CourseProgress])
def course_progress(
    course_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    """Percentage plus the ids of finished items (use them to tick items on the course page)."""
    course = get_course_or_404(db, course_id, user)
    return ApiResponse(data=_course_progress(db, user.id, course.id))


@router.get("/my-learnings", response_model=ApiResponse[list[LearningOut]])
def my_learnings(
    status_filter: str | None = Query(
        None, alias="status", pattern="^(not_started|in_progress|completed)$"
    ),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    """Enrolled courses with completion percentage (optional ?status=in_progress|completed)."""
    rows = db.scalars(
        select(Enrollment)
        .join(Course, Course.id == Enrollment.course_id)
        .join(Category, Category.id == Course.category_id)
        .where(
            Enrollment.student_id == user.id,
            Course.visible.is_(True),
            Category.visible.is_(True),
        )
        .options(contains_eager(Enrollment.course))
        .order_by(Enrollment.enrolled_at.desc(), Enrollment.id.desc())
    ).all()

    counts = _progress_counts(db, user.id, [e.course_id for e in rows])
    items = []
    for e in rows:
        total, done = counts[e.course_id]
        st = _status(done, total)
        if status_filter and st != status_filter:
            continue
        items.append(
            LearningOut(
                course=CourseOut.model_validate(e.course),
                enrolled_at=e.enrolled_at,
                total_items=total,
                completed_items=done,
                percentage=_percentage(done, total),
                status=st,
            )
        )
    return ApiResponse(data=items)


# ================================================== MODULE COMPLETE (student)
class ModuleProgressOut(CamelModel):
    module_id: int
    name: str
    total_contents: int
    completed_contents: int
    percentage: float
    status: str  # not_started | in_progress | completed


class CourseModuleProgressOut(CamelModel):
    course_id: int
    total_modules: int
    completed_modules: int
    percentage: float
    status: str  # not_started | in_progress | completed
    modules: list[ModuleProgressOut]


def _pct(done: int, total: int) -> float:
    return round(done * 100 / total, 1) if total else 0.0


def _status(done: int, total: int) -> str:
    if total and done >= total:
        return "completed"
    return "in_progress" if done else "not_started"


def _build_course_progress(db: Session, student_id: int, course: Course) -> CourseModuleProgressOut:
    done_ids = set(
        db.scalars(
            select(ContentProgress.content_id).where(
                ContentProgress.student_id == student_id,
                ContentProgress.course_id == course.id,
            )
        )
    )
    modules_out: list[ModuleProgressOut] = []
    for m in course.modules:  # ordered by position
        if not m.visible:
            continue
        items = [c for c in m.contents if c.visible]
        if not items:  # empty modules can't be completed, so leave them out of the count
            continue
        done = sum(1 for c in items if c.id in done_ids)
        modules_out.append(
            ModuleProgressOut(
                module_id=m.id,
                name=m.name,
                total_contents=len(items),
                completed_contents=done,
                percentage=_pct(done, len(items)),
                status=_status(done, len(items)),
            )
        )

    completed = sum(1 for m in modules_out if m.status == "completed")
    return CourseModuleProgressOut(
        course_id=course.id,
        total_modules=len(modules_out),
        completed_modules=completed,
        percentage=_pct(completed, len(modules_out)),
        status=_status(completed, len(modules_out)),
        modules=modules_out,
    )


@router.post(
    "/modules/{module_id}/complete",
    response_model=ApiResponse[CourseModuleProgressOut],
)
def mark_module_complete(
    module_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    """Student marks a whole module as done. Safe to call twice.

    Returns the course completion % (completed modules / total modules)
    and the completion % of every module in the course.
    """
    module = _get_module(db, module_id, user)
    course = module.course

    enrolled = db.scalar(
        select(Enrollment.id).where(
            Enrollment.course_id == course.id, Enrollment.student_id == user.id
        )
    )
    if enrolled is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not enrolled in this course.")

    contents = [c for c in module.contents if c.visible]
    if not contents:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "This module has no content to complete."
        )

    done_ids = set(
        db.scalars(
            select(ContentProgress.content_id).where(
                ContentProgress.student_id == user.id,
                ContentProgress.content_id.in_([c.id for c in contents]),
            )
        )
    )
    new_rows = [
        ContentProgress(student_id=user.id, content_id=c.id, course_id=course.id)
        for c in contents
        if c.id not in done_ids
    ]
    try:
        db.add_all(new_rows)
        db.commit()
    except IntegrityError:  # double click - already saved
        db.rollback()

    progress = _build_course_progress(db, user.id, course)
    msg = "Course completed." if progress.status == "completed" else "Module marked as complete."
    return ApiResponse(message=msg, data=progress)