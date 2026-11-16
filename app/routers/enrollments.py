from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, contains_eager

from app.database import get_db
from app.dependencies import require_role
from app.models import Category, Course, Enrollment, User
from app.routers.courses import _get_or_404 as get_course_or_404
from app.routers.courses import _require_owner
from app.schemas import (
    ApiResponse,
    EnrollmentListData,
    EnrollmentOut,
    EnrollRequest,
    EnrollResult,
    EnrollSkipped,
    MyCourseOut,
    StudentBrief,
    StudentListData,
)

router = APIRouter(prefix="/api", tags=["enrollments"])


def _owned_course(db: Session, course_id: int, user: User) -> Course:
    course = get_course_or_404(db, course_id, user)
    _require_owner(course, user)
    return course


def _name_email_filter(q, search: str):
    like = f"%{search.lower()}%"
    return q.where(
        or_(
            func.lower(User.first_name).like(like),
            func.lower(User.last_name).like(like),
            func.lower(User.email).like(like),
        )
    )


# ============================================================ ENROLL (teacher)
@router.post(
    "/courses/{course_id}/enrollments",
    response_model=ApiResponse[EnrollResult],
    status_code=status.HTTP_201_CREATED,
)
def enroll_students(
    course_id: int,
    payload: EnrollRequest,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    """Enroll one or many students (by id and/or email) in a course you created.

    Never half-fails: students that are unknown, not students, or already enrolled
    are reported back in the response and the rest are enrolled.
    """
    course = _owned_course(db, course_id, user)

    ids = list(dict.fromkeys(payload.student_ids))
    emails = list(dict.fromkeys(e.lower() for e in payload.emails))

    users: dict[int, User] = {}
    skipped: list[EnrollSkipped] = []

    if ids:
        found = {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids)))}
        for sid in ids:
            if sid in found:
                users[sid] = found[sid]
            else:
                skipped.append(EnrollSkipped(student_id=sid, reason="User not found."))
    if emails:
        found_by_email = {
            u.email.lower(): u
            for u in db.scalars(select(User).where(func.lower(User.email).in_(emails)))
        }
        for email in emails:
            if email in found_by_email:
                users[found_by_email[email].id] = found_by_email[email]
            else:
                skipped.append(EnrollSkipped(email=email, reason="No user with this email."))

    valid: list[User] = []
    for u in users.values():
        if u.role != "student":
            skipped.append(
                EnrollSkipped(student_id=u.id, email=u.email, reason="Only students can be enrolled.")
            )
        else:
            valid.append(u)

    existing = set(
        db.scalars(
            select(Enrollment.student_id).where(
                Enrollment.course_id == course.id,
                Enrollment.student_id.in_([u.id for u in valid]),
            )
        )
    )
    new_rows = [
        Enrollment(course_id=course.id, student_id=u.id, enrolled_by=user.id)
        for u in valid
        if u.id not in existing
    ]
    already = [u for u in valid if u.id in existing]

    try:
        db.add_all(new_rows)
        db.commit()
    except IntegrityError:  # someone enrolled the same student at the same moment
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Enrollment changed while saving, please retry."
        )

    if not new_rows:
        response.status_code = status.HTTP_200_OK
    message = f"{len(new_rows)} student(s) enrolled."
    if already:
        message += f" {len(already)} already enrolled."
    if skipped:
        message += f" {len(skipped)} skipped."
    return ApiResponse(
        message=message,
        data=EnrollResult(
            enrolled=[EnrollmentOut.model_validate(e) for e in new_rows],
            already_enrolled=[StudentBrief.model_validate(u) for u in already],
            skipped=skipped,
        ),
    )


# ====================================================== LIST ENROLLED (teacher)
@router.get(
    "/courses/{course_id}/enrollments", response_model=ApiResponse[EnrollmentListData]
)
def list_enrollments(
    course_id: int,
    search: str | None = Query(None, min_length=1, max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    course = _owned_course(db, course_id, user)

    base = (
        select(Enrollment)
        .join(User, User.id == Enrollment.student_id)
        .where(Enrollment.course_id == course.id)
    )
    if search:
        base = _name_email_filter(base, search)

    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(
        base.options(contains_eager(Enrollment.student))
        .order_by(User.first_name, User.last_name, Enrollment.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return ApiResponse(
        data=EnrollmentListData(
            items=[EnrollmentOut.model_validate(e) for e in rows],
            total=total,
            page=page,
            page_size=page_size,
        )
    )


# ============================================================ UNENROLL (teacher)
@router.delete("/courses/{course_id}/enrollments/{student_id}")
def unenroll_student(
    course_id: int,
    student_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    course = _owned_course(db, course_id, user)
    enrollment = db.scalar(
        select(Enrollment).where(
            Enrollment.course_id == course.id, Enrollment.student_id == student_id
        )
    )
    if enrollment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Student is not enrolled in this course.")
    db.delete(enrollment)
    db.commit()
    return {"success": True, "message": "Student unenrolled."}


# ================================================== FIND STUDENTS TO ENROLL (teacher)
@router.get("/students", response_model=ApiResponse[StudentListData])
def list_students(
    search: str | None = Query(None, min_length=1, max_length=100),
    not_enrolled_in: int | None = Query(
        None, alias="notEnrolledInCourseId", description="Hide students already in this course"
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    """Student picker for the enroll screen: search by name or email."""
    q = select(User).where(User.role == "student")
    if search:
        q = _name_email_filter(q, search)
    if not_enrolled_in is not None:
        course = _owned_course(db, not_enrolled_in, user)
        q = q.where(
            User.id.not_in(select(Enrollment.student_id).where(Enrollment.course_id == course.id))
        )

    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(
        q.order_by(User.first_name, User.last_name, User.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return ApiResponse(
        data=StudentListData(
            items=[StudentBrief.model_validate(u) for u in rows],
            total=total,
            page=page,
            page_size=page_size,
        )
    )


# ===================================================== MY COURSES (student)
@router.get("/enrollments/me", response_model=ApiResponse[list[MyCourseOut]])
def my_courses(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    """Courses the logged-in student is enrolled in (hidden courses are left out)."""
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
    return ApiResponse(data=[MyCourseOut.model_validate(e) for e in rows])