from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models import Category, Course, User
from app.routers.categories import descendant_ids
from app.schemas import (
    ApiResponse,
    CourseCreate,
    CourseListData,
    CourseOut,
    CourseUpdate,
)

router = APIRouter(prefix="/api/courses", tags=["courses"])


# ----------------------------------------------------------------- helpers
def _is_teacher(user: User) -> bool:
    return user.role == "teacher"


def _get_or_404(db: Session, course_id: int, user: User) -> Course:
    course = db.get(Course, course_id)
    if course is None or (
        not _is_teacher(user) and not (course.visible and course.category.visible)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Course not found.")
    return course


def _require_owner(course: Course, user: User):
    if course.created_by != user.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only the teacher who created this course can change it."
        )


def _check_category(db: Session, category_id: int):
    if db.get(Category, category_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found.")


def _check_unique(db: Session, short_name, id_number, exclude_id: int | None = None):
    if short_name:
        q = select(Course.id).where(func.lower(Course.short_name) == short_name.lower())
        if exclude_id:
            q = q.where(Course.id != exclude_id)
        if db.scalar(q):
            raise HTTPException(status.HTTP_409_CONFLICT, "Course short name already exists.")
    if id_number:
        q = select(Course.id).where(Course.id_number == id_number)
        if exclude_id:
            q = q.where(Course.id != exclude_id)
        if db.scalar(q):
            raise HTTPException(status.HTTP_409_CONFLICT, "Course ID number already in use.")


# ------------------------------------------------------------------- CREATE
@router.post(
    "",
    response_model=ApiResponse[CourseOut],
    status_code=status.HTTP_201_CREATED,
)
def create_course(
    payload: CourseCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    _check_category(db, payload.category_id)
    _check_unique(db, payload.short_name, payload.id_number)

    course = Course(**payload.model_dump(), created_by=user.id)
    try:
        db.add(course)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Course already exists.")
    return ApiResponse(message="Course created.", data=CourseOut.model_validate(course))


# --------------------------------------------------------------------- LIST
@router.get("", response_model=ApiResponse[CourseListData])
def list_courses(
    category_id: int | None = Query(None, alias="categoryId"),
    include_subcategories: bool = Query(True, alias="includeSubcategories"),
    search: str | None = Query(None, min_length=1, max_length=100),
    mine: bool = Query(False, description="Teachers: only courses I created"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = select(Course)

    if category_id is not None:
        _check_category(db, category_id)
        ids = descendant_ids(db, category_id) if include_subcategories else [category_id]
        q = q.where(Course.category_id.in_(ids))

    if search:
        like = f"%{search.lower()}%"
        q = q.where(
            or_(
                func.lower(Course.full_name).like(like),
                func.lower(Course.short_name).like(like),
            )
        )

    if _is_teacher(user):
        if mine:
            q = q.where(Course.created_by == user.id)
    else:
        q = q.join(Category, Category.id == Course.category_id).where(
            Course.visible.is_(True), Category.visible.is_(True)
        )

    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(
        q.order_by(Course.full_name).offset((page - 1) * page_size).limit(page_size)
    ).all()

    return ApiResponse(
        data=CourseListData(
            items=[CourseOut.model_validate(c) for c in rows],
            total=total,
            page=page,
            page_size=page_size,
        )
    )


# ---------------------------------------------------------------------- GET
@router.get("/{course_id}", response_model=ApiResponse[CourseOut])
def get_course(
    course_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return ApiResponse(data=CourseOut.model_validate(_get_or_404(db, course_id, user)))


# ------------------------------------------------------------------- UPDATE
@router.patch("/{course_id}", response_model=ApiResponse[CourseOut])
def update_course(
    course_id: int,
    payload: CourseUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    course = _get_or_404(db, course_id, user)
    _require_owner(course, user)
    changes = payload.model_dump(exclude_unset=True)

    for field in ("category_id", "full_name", "short_name", "visible"):
        if field in changes and changes[field] is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"{field} cannot be null."
            )

    if "category_id" in changes:
        _check_category(db, changes["category_id"])
    _check_unique(
        db, changes.get("short_name"), changes.get("id_number"), exclude_id=course.id
    )

    start = changes.get("start_date", course.start_date)
    end = changes.get("end_date", course.end_date)
    if start and end and end < start:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "End date cannot be before start date."
        )

    for key, value in changes.items():
        setattr(course, key, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Course update conflicts with existing data.")
    return ApiResponse(message="Course updated.", data=CourseOut.model_validate(course))


# ------------------------------------------------------------------- DELETE
@router.delete("/{course_id}")
def delete_course(
    course_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    course = _get_or_404(db, course_id, user)
    _require_owner(course, user)
    db.delete(course)
    db.commit()
    return {"success": True, "message": "Course deleted."}