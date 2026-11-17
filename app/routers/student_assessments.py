"""Module assessments + final course assessment (separate from routers/assignments.py).

Two kinds of assessment, both stored in the existing `assignments` table:
  * module assessment : assignments.module_id = <module id>
  * final assessment  : assignments.module_id IS NULL (whole course)

Rules
  * only students enrolled in the course can see them
  * a module's assessments unlock when the student has completed that module
  * the final assessment unlocks when every module of the course is completed
  * score = 20% (all module assessments together) + 80% (final assessment)

Questions (including coding questions) are added with the existing
POST /api/assignments/{id}/questions, and coding answers are graded with the
existing PATCH /api/attempts/{id}/grade. Submit uses the existing
POST /api/attempts/{id}/submit.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from datetime import datetime
from app.models import Category, ContentProgress, Course, CourseModule, Enrollment, User
from app.assignment_models import Assignment, AssignmentAttempt
from app.assignments_schemas import AssignmentOut, AttemptStartOut
from app.database import get_db
from app.dependencies import require_role
from app.models import ContentProgress, Course, CourseModule, User
from app.routers.assignments import _expire_if_needed, _require_enrolled, start_attempt
from app.routers.courses import _get_or_404 as get_course_or_404
from app.routers.courses import _require_owner
from app.schemas import ApiResponse, CamelModel

router = APIRouter(prefix="/api", tags=["assessments"])

MODULE_WEIGHT = 20.0  # all module assessments together
FINAL_WEIGHT = 80.0  # final assessment after the course is completed


# ===================================================================== schemas
class AssessmentCreate(CamelModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    time_limit_minutes: int = Field(gt=0, le=1440, description="Total time for the assessment")
    pass_percentage: float = Field(ge=0, le=100)
    max_attempts: int = Field(default=1, ge=1, le=20, description="Tries each student gets")
    visible: bool = False  # draft until the teacher publishes


class AssessmentCard(AssignmentOut):
    kind: str  # "module" | "final"
    attempts_used: int
    attempts_left: int
    best_percentage: float | None
    passed: bool | None
    pending_review: bool  # a coding answer is still waiting for the teacher


class ModuleScoreOut(CamelModel):
    module_id: int
    name: str
    assessments: int
    percentage: float  # average of the best attempt of each assessment in this module


class CourseScoreOut(CamelModel):
    course_id: int
    module_weight: float
    final_weight: float
    modules_percentage: float
    module_points: float
    final_percentage: float
    final_points: float
    total_score: float  # out of 100
    final_unlocked: bool
    pending_review: bool  # score can still go up once the teacher grades coding answers
    modules: list[ModuleScoreOut]


# ===================================================================== helpers
def _module_completion(db: Session, student_id: int, course: Course) -> dict[int, bool]:
    """{module_id: completed?} for visible modules that have visible content.

    A module is completed when every visible content item in it has a
    ContentProgress row for this student.
    """
    done_ids = set(
        db.scalars(
            select(ContentProgress.content_id).where(
                ContentProgress.student_id == student_id,
                ContentProgress.course_id == course.id,
            )
        )
    )
    result: dict[int, bool] = {}
    for m in course.modules:
        if not m.visible:
            continue
        items = [c for c in m.contents if c.visible]
        if items:
            result[m.id] = all(c.id in done_ids for c in items)
    return result


def _visible_course(db: Session, course_id: int, user: User) -> Course:
    course = get_course_or_404(db, course_id, user)  # course + category must be visible
    _require_enrolled(db, course.id, user)
    return course


def _require_module_done(db: Session, course: Course, module_id: int, user: User):
    if not _module_completion(db, user.id, course).get(module_id, False):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Complete this module to unlock its assessment.",
        )


def _require_course_done(db: Session, course: Course, user: User):
    done = _module_completion(db, user.id, course)
    if not done or not all(done.values()):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Complete every module of the course to unlock the final assessment.",
        )


def _student_attempts(db: Session, assignment_id: int, student_id: int) -> list[AssignmentAttempt]:
    rows = db.scalars(
        select(AssignmentAttempt)
        .where(
            AssignmentAttempt.assignment_id == assignment_id,
            AssignmentAttempt.student_id == student_id,
        )
        .order_by(AssignmentAttempt.attempt_no)
    ).all()
    for r in rows:
        _expire_if_needed(db, r)  # close attempts whose timer ran out
    return rows


def _best_percentage(attempts: list[AssignmentAttempt]) -> float | None:
    graded = [a.percentage for a in attempts if a.status == "graded" and a.percentage is not None]
    return max(graded) if graded else None


def _card(db: Session, a: Assignment, student_id: int) -> AssessmentCard:
    attempts = _student_attempts(db, a.id, student_id)
    used = len(attempts)
    return AssessmentCard(
        **AssignmentOut.model_validate(a).model_dump(),
        kind="module" if a.module_id is not None else "final",
        attempts_used=used,
        attempts_left=max(0, a.max_attempts - used),
        best_percentage=_best_percentage(attempts),
        passed=any(t.passed for t in attempts if t.status == "graded") if used else None,
        pending_review=any(t.status == "submitted" for t in attempts),
    )


def _visible_assignments(db: Session, course_id: int) -> list[Assignment]:
    return list(
        db.scalars(
            select(Assignment)
            .where(Assignment.course_id == course_id, Assignment.visible.is_(True))
            .order_by(Assignment.created_at, Assignment.id)
        )
    )


# ===================================================== TEACHER: create / assign
@router.post(
    "/modules/{module_id}/assessments",
    response_model=ApiResponse[AssignmentOut],
    status_code=status.HTTP_201_CREATED,
)
def create_module_assessment(
    module_id: int,
    payload: AssessmentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    """Assign an assessment to one module. Attempts allowed = maxAttempts.

    Next: add questions (multiple_choice / true_false / text / coding) with
    POST /api/assignments/{id}/questions. Scores are calculated automatically.
    """
    module = db.get(CourseModule, module_id)
    if module is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Module not found.")
    course = get_course_or_404(db, module.course_id, user)
    _require_owner(course, user)
    if not any(c.visible for c in module.contents):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Add content to this module first, students unlock the assessment by completing it.",
        )

    a = Assignment(
        course_id=course.id, module_id=module.id, created_by=user.id, **payload.model_dump()
    )
    db.add(a)
    db.commit()
    return ApiResponse(message="Module assessment created.", data=AssignmentOut.model_validate(a))


@router.post(
    "/courses/{course_id}/final-assessment",
    response_model=ApiResponse[AssignmentOut],
    status_code=status.HTTP_201_CREATED,
)
def create_final_assessment(
    course_id: int,
    payload: AssessmentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    """Assign the final assessment of a course (one per course, worth 80%)."""
    course = get_course_or_404(db, course_id, user)
    _require_owner(course, user)

    exists = db.scalar(
        select(Assignment.id).where(
            Assignment.course_id == course.id, Assignment.module_id.is_(None)
        )
    )
    if exists:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This course already has a final assessment (id {exists}). Edit it instead.",
        )

    a = Assignment(course_id=course.id, module_id=None, created_by=user.id, **payload.model_dump())
    db.add(a)
    db.commit()
    return ApiResponse(message="Final assessment created.", data=AssignmentOut.model_validate(a))


# ============================================================ STUDENT: unlocked
@router.get(
    "/modules/{module_id}/assessments",
    response_model=ApiResponse[list[AssessmentCard]],
)
def module_assessments(
    module_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    """Assessments of one module. Only after the student completed that module."""
    module = db.get(CourseModule, module_id)
    if module is None or not module.visible:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Module not found.")
    course = _visible_course(db, module.course_id, user)
    _require_module_done(db, course, module.id, user)

    rows = [a for a in _visible_assignments(db, course.id) if a.module_id == module.id]
    return ApiResponse(data=[_card(db, a, user.id) for a in rows])


@router.get(
    "/courses/{course_id}/final-assessment",
    response_model=ApiResponse[AssessmentCard],
)
def final_assessment(
    course_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    """The final (whole-course) assessment. Only after every module is completed."""
    course = _visible_course(db, course_id, user)
    _require_course_done(db, course, user)

    final = next((a for a in _visible_assignments(db, course.id) if a.module_id is None), None)
    if final is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No final assessment for this course yet.")
    return ApiResponse(data=_card(db, final, user.id))


@router.post(
    "/assessments/{assignment_id}/start",
    response_model=ApiResponse[AttemptStartOut],
    status_code=status.HTTP_201_CREATED,
)
def start_assessment(
    assignment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    """Start (or resume) an attempt, only if the module/course is completed.

    Same result as POST /api/assignments/{id}/start, plus the unlock check.
    Submit with the existing POST /api/attempts/{attemptId}/submit.
    """
    a = db.get(Assignment, assignment_id)
    if a is None or not a.visible:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assessment not found.")
    course = _visible_course(db, a.course_id, user)
    if a.module_id is not None:
        _require_module_done(db, course, a.module_id, user)
    else:
        _require_course_done(db, course, user)
    return start_attempt(assignment_id, db=db, user=user)


# ================================================================ STUDENT: score
@router.get("/courses/{course_id}/score/me", response_model=ApiResponse[CourseScoreOut])
def my_course_score(
    course_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    """Automatic score: 20% module assessments + 80% final assessment.

    Each assessment counts its best graded attempt. A module/final assessment
    the student has not passed yet counts as 0 until they do it. If the course
    has no module assessments (or no final), the part that exists counts 100%.
    """
    course = _visible_course(db, course_id, user)
    names = {m.id: m.name for m in course.modules}
    rows = _visible_assignments(db, course.id)

    per_assignment: dict[int, float] = {}
    pending = False
    for a in rows:
        attempts = _student_attempts(db, a.id, user.id)
        per_assignment[a.id] = _best_percentage(attempts) or 0.0
        pending = pending or any(t.status == "submitted" for t in attempts)

    by_module: dict[int, list[float]] = {}
    final_pct: float | None = None
    for a in rows:
        if a.module_id is None:
            final_pct = per_assignment[a.id]
        else:
            by_module.setdefault(a.module_id, []).append(per_assignment[a.id])

    modules_out = [
        ModuleScoreOut(
            module_id=mid,
            name=names.get(mid, ""),
            assessments=len(vals),
            percentage=round(sum(vals) / len(vals), 2),
        )
        for mid, vals in by_module.items()
    ]
    modules_pct = (
        round(sum(m.percentage for m in modules_out) / len(modules_out), 2) if modules_out else 0.0
    )

    mw = MODULE_WEIGHT if modules_out else 0.0
    fw = FINAL_WEIGHT if final_pct is not None else 0.0
    total_w = mw + fw
    if total_w:  # a missing part hands its weight to the part that exists
        mw, fw = mw / total_w * 100, fw / total_w * 100
    module_points = round(modules_pct * mw / 100, 2)
    final_points = round((final_pct or 0.0) * fw / 100, 2)

    done = _module_completion(db, user.id, course)
    return ApiResponse(
        data=CourseScoreOut(
            course_id=course.id,
            module_weight=round(mw, 2),
            final_weight=round(fw, 2),
            modules_percentage=modules_pct,
            module_points=module_points,
            final_percentage=final_pct or 0.0,
            final_points=final_points,
            total_score=round(module_points + final_points, 2),
            final_unlocked=bool(done) and all(done.values()),
            pending_review=pending,
            modules=modules_out,
        )
    )

# ================================================================ STUDENT: dashboard
class DashboardCourseOut(CamelModel):
    course_id: int
    full_name: str
    short_name: str
    enrolled_at: datetime
    total_modules: int
    modules_completed: int
    total_items: int
    items_completed: int
    completion_percentage: float  # completed content items / all content items
    is_completed: bool


class DashboardOut(CamelModel):
    enrolled_courses: int
    modules_completed: int
    assignments_completed: int
    average_assessment_score: float  # average of the best graded % of each assessment
    learnings: list[DashboardCourseOut]


@router.get("/dashboard/me", response_model=ApiResponse[DashboardOut])
def student_dashboard(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    """Student dashboard: counts + the courses they enrolled in with completion %."""
    enrollments = db.scalars(
        select(Enrollment)
        .join(Course, Course.id == Enrollment.course_id)
        .join(Category, Category.id == Course.category_id)
        .where(
            Enrollment.student_id == user.id,
            Course.visible.is_(True),
            Category.visible.is_(True),
        )
        .order_by(Enrollment.enrolled_at.desc(), Enrollment.id.desc())
    ).all()

    learnings: list[DashboardCourseOut] = []
    total_modules_done = 0
    assessments_done = 0
    best_scores: list[float] = []

    for e in enrollments:
        course = e.course

        # ---- modules + content progress
        done_ids = set(
            db.scalars(
                select(ContentProgress.content_id).where(
                    ContentProgress.student_id == user.id,
                    ContentProgress.course_id == course.id,
                )
            )
        )
        n_modules = modules_done = n_items = items_done = 0
        for m in course.modules:
            if not m.visible:
                continue
            items = [c for c in m.contents if c.visible]
            if not items:
                continue
            n_modules += 1
            n_items += len(items)
            finished = sum(1 for c in items if c.id in done_ids)
            items_done += finished
            if finished == len(items):
                modules_done += 1
        total_modules_done += modules_done

        pct = round(items_done / n_items * 100, 2) if n_items else 0.0
        learnings.append(
            DashboardCourseOut(
                course_id=course.id,
                full_name=course.full_name,
                short_name=course.short_name,
                enrolled_at=e.enrolled_at,
                total_modules=n_modules,
                modules_completed=modules_done,
                total_items=n_items,
                items_completed=items_done,
                completion_percentage=pct,
                is_completed=bool(n_items) and items_done == n_items,
            )
        )

        # ---- assessments of this course (module assessments + final)
        for a in _visible_assignments(db, course.id):
            attempts = _student_attempts(db, a.id, user.id)
            if any(t.status in ("submitted", "graded") for t in attempts):
                assessments_done += 1
            best = _best_percentage(attempts)
            if best is not None:
                best_scores.append(best)

    average = round(sum(best_scores) / len(best_scores), 2) if best_scores else 0.0
    return ApiResponse(
        data=DashboardOut(
            enrolled_courses=len(enrollments),
            modules_completed=total_modules_done,
            assignments_completed=assessments_done,
            average_assessment_score=average,
            learnings=learnings,
        )
    )