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
from sqlalchemy import delete, select

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


class AssessmentCard(AssignmentOut):
    kind: str  # "module" | "final"
    attempts_used: int
    attempts_left: int
    best_percentage: float | None
    passed: bool | None
    pending_review: bool  # a coding answer is still waiting for the teacher
    can_restart: bool  # all attempts used and none passed -> student may restart the module

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

def _failed_all_attempts(a: Assignment, attempts: list[AssignmentAttempt]) -> bool:
    """True when every attempt is used, all are graded and none passed.

    An attempt that is still running or waiting for the teacher to grade a
    coding answer is not a failure yet, so it blocks the restart.
    """
    return (
        len(attempts) >= a.max_attempts
        and all(t.status == "graded" for t in attempts)
        and not any(t.passed for t in attempts)
    )

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
        can_restart=a.module_id is not None and _failed_all_attempts(a, attempts),
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


class ModuleRestartOut(CamelModel):
    module_id: int
    contents_reset: int  # content items un-marked, the module is "not completed" again
    assessments_reset: int  # assessments whose failed attempts were cleared


@router.post(
    "/modules/{module_id}/restart",
    response_model=ApiResponse[ModuleRestartOut],
)
def restart_module(
    module_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    """Restart a module after failing all attempts of its assessment."""
    module = db.get(CourseModule, module_id)
    if module is None or not module.visible:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Module not found.")
    course = _visible_course(db, module.course_id, user)

    failed = [
        a
        for a in _visible_assignments(db, course.id)
        if a.module_id == module.id
        and _failed_all_attempts(a, _student_attempts(db, a.id, user.id))
    ]
    if not failed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "You can restart a module only after using all attempts of its assessment without passing.",
        )

    content_ids = [c.id for c in module.contents]
    contents_reset = 0
    if content_ids:
        contents_reset = db.execute(
            delete(ContentProgress).where(
                ContentProgress.student_id == user.id,
                ContentProgress.content_id.in_(content_ids),
            )
        ).rowcount
    db.execute(
        delete(AssignmentAttempt).where(
            AssignmentAttempt.student_id == user.id,
            AssignmentAttempt.assignment_id.in_([a.id for a in failed]),
        )
    )
    db.commit()

    return ApiResponse(
        message="Module restarted. Read it again and mark it complete to unlock the assessment.",
        data=ModuleRestartOut(
            module_id=module.id,
            contents_reset=contents_reset or 0,
            assessments_reset=len(failed),
        ),
    )

from sqlalchemy.orm import Session, selectinload

# ================================================================ STUDENT: my analytics page
class NextStepOut(CamelModel):
    step: int  # position of the topic in the course (1-based)
    type: str  # "lesson" | "final_assessment"
    course_id: int
    course_name: str
    module_id: int | None
    assignment_id: int | None
    title: str
    question_count: int | None  # questions in that topic's / final assessment


class RoadmapNodeOut(CamelModel):
    kind: str  # "topic" | "final"
    module_id: int | None
    assignment_id: int | None
    title: str
    order: int
    state: str  # "completed" | "current" | "locked"


class MarksPart(CamelModel):
    marks: float
    out_of: float


class MarksBreakdownOut(CamelModel):
    topic_quizzes: MarksPart
    final_assessment: MarksPart
    overall_marks: float  # out of 100


class ChapterPerformanceOut(CamelModel):
    combined_marks: float
    out_of: float
    overall_percentage: float
    pass_mark: float | None  # pass percentage of the final assessment
    is_passed: bool


class QuizTopicOut(CamelModel):
    module_id: int
    name: str
    best_percentage: float | None  # None = no graded attempt yet
    passed: bool | None


class AttemptTrackerOut(CamelModel):
    assignment_id: int
    unlocked: bool
    max_attempts: int
    attempts_used: int
    attempts_remaining: int
    best_percentage: float | None
    passed: bool | None
    pending_review: bool


class ChapterOut(CamelModel):
    chapter_no: int
    course_id: int
    course_name: str
    short_name: str
    category_name: str
    status: str  # "not_started" | "in_progress" | "completed"
    topics_completed: int
    total_topics: int
    next_topic_name: str | None
    performance: ChapterPerformanceOut
    roadmap: list[RoadmapNodeOut]
    next_step: NextStepOut | None
    marks_breakdown: MarksBreakdownOut
    quiz_performance: list[QuizTopicOut]
    attempt_tracker: AttemptTrackerOut | None  # None if the course has no final assessment


class AnalyticsSummaryOut(CamelModel):
    enrolled_courses: int
    topics_completed: int
    total_topics: int
    overall_percentage: float  # topics completed / all topics
    assessment_attempts: int  # submitted + graded attempts (module quizzes + finals)
    courses_passed: int  # final assessment passed
    average_assessment_score: float  # average of the best graded % of each assessment


class MyAnalyticsOut(CamelModel):
    summary: AnalyticsSummaryOut
    next_step: NextStepOut | None  # "Your next step" card at the top
    chapters: list[ChapterOut]


@router.get("/analytics/me", response_model=ApiResponse[MyAnalyticsOut])
def my_analytics(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    """Everything the My Analytics page shows, in one call (student only)."""
    enrollments = db.scalars(
        select(Enrollment)
        .join(Course, Course.id == Enrollment.course_id)
        .join(Category, Category.id == Course.category_id)
        .where(
            Enrollment.student_id == user.id,
            Course.visible.is_(True),
            Category.visible.is_(True),
        )
        .options(
            selectinload(Enrollment.course).selectinload(Course.category),
            selectinload(Enrollment.course)
            .selectinload(Course.modules)
            .selectinload(CourseModule.contents),
        )
        .order_by(Enrollment.id)  # chapter 01, 02, ... in enrollment order
    ).all()

    done_ids: set[int] = set()
    if enrollments:
        done_ids = set(
            db.scalars(
                select(ContentProgress.content_id).where(
                    ContentProgress.student_id == user.id,
                    ContentProgress.course_id.in_([e.course_id for e in enrollments]),
                )
            )
        )

    chapters: list[ChapterOut] = []
    all_topics = all_topics_done = attempts_total = passed_courses = 0
    best_scores: list[float] = []

    for no, e in enumerate(enrollments, start=1):
        course = e.course

        # ---------- topics (visible modules that have visible content)
        topics = []  # (module, finished_items, completed)
        for m in course.modules:
            if not m.visible:
                continue
            items = [c for c in m.contents if c.visible]
            if items:
                finished = sum(1 for c in items if c.id in done_ids)
                topics.append((m, finished, finished == len(items)))
        n_topics = len(topics)
        n_done = sum(1 for _, _, ok in topics if ok)
        all_topics += n_topics
        all_topics_done += n_done
        all_modules_done = bool(n_topics) and n_done == n_topics
        current = next((m for m, _, ok in topics if not ok), None)

        # ---------- assessments (best attempt of each)
        rows = _visible_assignments(db, course.id)
        attempts_by: dict[int, list[AssignmentAttempt]] = {}
        best_by: dict[int, float | None] = {}
        for a in rows:
            attempts = _student_attempts(db, a.id, user.id)
            attempts_by[a.id] = attempts
            best_by[a.id] = _best_percentage(attempts)
            attempts_total += sum(1 for t in attempts if t.status in ("submitted", "graded"))
            if best_by[a.id] is not None:
                best_scores.append(best_by[a.id])

        final = next((a for a in rows if a.module_id is None), None)
        module_rows: dict[int, list[Assignment]] = {}
        for a in rows:
            if a.module_id is not None:
                module_rows.setdefault(a.module_id, []).append(a)

        # same scoring rule as GET /courses/{id}/score/me
        module_pcts = {
            mid: sum(best_by[a.id] or 0.0 for a in lst) / len(lst)
            for mid, lst in module_rows.items()
        }
        modules_pct = sum(module_pcts.values()) / len(module_pcts) if module_pcts else 0.0
        final_pct = (best_by[final.id] or 0.0) if final else None
        mw = MODULE_WEIGHT if module_pcts else 0.0
        fw = FINAL_WEIGHT if final else 0.0
        if mw + fw:
            mw, fw = mw / (mw + fw) * 100, fw / (mw + fw) * 100
        module_points = round(modules_pct * mw / 100, 2)
        final_points = round((final_pct or 0.0) * fw / 100, 2)
        total_points = round(module_points + final_points, 2)

        # ---------- final assessment tracker
        tracker = None
        final_passed = False
        if final:
            f_attempts = attempts_by[final.id]
            final_passed = any(t.passed for t in f_attempts if t.status == "graded")
            tracker = AttemptTrackerOut(
                assignment_id=final.id,
                unlocked=all_modules_done,
                max_attempts=final.max_attempts,
                attempts_used=len(f_attempts),
                attempts_remaining=max(0, final.max_attempts - len(f_attempts)),
                best_percentage=best_by[final.id],
                passed=final_passed if f_attempts else None,
                pending_review=any(t.status == "submitted" for t in f_attempts),
            )
        passed_courses += int(final_passed)

        # ---------- roadmap nodes
        nodes: list[RoadmapNodeOut] = []
        for i, (m, _, ok) in enumerate(topics, start=1):
            nodes.append(
                RoadmapNodeOut(
                    kind="topic",
                    module_id=m.id,
                    assignment_id=None,
                    title=m.name,
                    order=i,
                    state="completed" if ok else ("current" if m is current else "locked"),
                )
            )
        if final:
            nodes.append(
                RoadmapNodeOut(
                    kind="final",
                    module_id=None,
                    assignment_id=final.id,
                    title=final.title,
                    order=n_topics + 1,
                    state="completed" if final_passed else ("current" if all_modules_done else "locked"),
                )
            )

        # ---------- next step for this chapter
        next_step = None
        if current is not None:
            next_step = NextStepOut(
                step=next(i for i, (m, _, _) in enumerate(topics, start=1) if m is current),
                type="lesson",
                course_id=course.id,
                course_name=course.full_name,
                module_id=current.id,
                assignment_id=None,
                title=current.name,
                question_count=sum(a.question_count for a in module_rows.get(current.id, [])) or None,
            )
        elif final and not final_passed and tracker and tracker.attempts_remaining > 0:
            next_step = NextStepOut(
                step=n_topics + 1,
                type="final_assessment",
                course_id=course.id,
                course_name=course.full_name,
                module_id=None,
                assignment_id=final.id,
                title=final.title,
                question_count=final.question_count,
            )

        # ---------- status
        started = any(c.id in done_ids for m, _, _ in topics for c in m.contents) or any(
            attempts_by.values()
        )
        if final_passed or (all_modules_done and final is None):
            status_ = "completed"
        elif started:
            status_ = "in_progress"
        else:
            status_ = "not_started"

        chapters.append(
            ChapterOut(
                chapter_no=no,
                course_id=course.id,
                course_name=course.full_name,
                short_name=course.short_name,
                category_name=course.category.name,
                status=status_,
                topics_completed=n_done,
                total_topics=n_topics,
                next_topic_name=current.name if current else None,
                performance=ChapterPerformanceOut(
                    combined_marks=total_points,
                    out_of=100.0,
                    overall_percentage=total_points,
                    pass_mark=final.pass_percentage if final else None,
                    is_passed=final_passed,
                ),
                roadmap=nodes,
                next_step=next_step,
                marks_breakdown=MarksBreakdownOut(
                    topic_quizzes=MarksPart(marks=module_points, out_of=round(mw, 2)),
                    final_assessment=MarksPart(marks=final_points, out_of=round(fw, 2)),
                    overall_marks=total_points,
                ),
                quiz_performance=[
                    QuizTopicOut(
                        module_id=m.id,
                        name=m.name,
                        best_percentage=(
                            round(
                                sum(best_by[a.id] or 0.0 for a in module_rows[m.id])
                                / len(module_rows[m.id]),
                                2,
                            )
                            if m.id in module_rows
                            and any(best_by[a.id] is not None for a in module_rows[m.id])
                            else None
                        ),
                        passed=(
                            all(
                                any(t.passed for t in attempts_by[a.id] if t.status == "graded")
                                for a in module_rows[m.id]
                            )
                            if m.id in module_rows and any(attempts_by[a.id] for a in module_rows[m.id])
                            else None
                        ),
                    )
                    for m, _, _ in topics
                ],
                attempt_tracker=tracker,
            )
        )

    # top card: first chapter in progress that has a next step, else first chapter that has one
    pick = next(
        (c for c in chapters if c.status == "in_progress" and c.next_step), None
    ) or next((c for c in chapters if c.next_step), None)

    return ApiResponse(
        data=MyAnalyticsOut(
            summary=AnalyticsSummaryOut(
                enrolled_courses=len(enrollments),
                topics_completed=all_topics_done,
                total_topics=all_topics,
                overall_percentage=round(all_topics_done / all_topics * 100, 2) if all_topics else 0.0,
                assessment_attempts=attempts_total,
                courses_passed=passed_courses,
                average_assessment_score=(
                    round(sum(best_scores) / len(best_scores), 2) if best_scores else 0.0
                ),
            ),
            next_step=pick.next_step if pick else None,
            chapters=chapters,
        )
    )



# ================================================================ STUDENT: learning path (one course)
class LearningPathCourseOut(CamelModel):
    id: int
    full_name: str
    short_name: str
    summary: str | None
    category_name: str


class LearningPathProgressOut(CamelModel):
    total_modules: int
    read_modules: int
    passed_modules: int
    remaining_modules: int
    completion_percentage: int
    final_assessment_unlocked: bool


class LearningPathModuleOut(CamelModel):
    id: int
    name: str
    summary: str | None
    position: int
    contents_count: int
    is_read: bool
    assessment_passed: bool
    is_unlocked: bool
    status: str  # "PASSED" | "READ" | "AVAILABLE" | "LOCKED"


class LearningPathFinalOut(CamelModel):
    id: int
    title: str
    is_unlocked: bool
    status: str  # "PASSED" | "AVAILABLE" | "LOCKED"


class LearningPathOut(CamelModel):
    course: LearningPathCourseOut
    progress: LearningPathProgressOut
    modules: list[LearningPathModuleOut]
    final_assessment: LearningPathFinalOut | None  # None if the course has no final assessment


@router.get(
    "/courses/{course_id}/learning-path/me",
    response_model=ApiResponse[LearningPathOut],
)
def my_learning_path(
    course_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    """Learning path of one enrolled course for the logged-in student.

    read   = every visible content item of the module is finished
    passed = read AND every assessment of that module is passed
             (a module without an assessment is passed once it is read)
    A module is unlocked when it is the first one, or the previous one is passed.
    The final assessment unlocks when every module is read (same rule as the start API).
    """
    course = _visible_course(db, course_id, user)
    read_map = _module_completion(db, user.id, course)  # {module_id: read?}

    rows = _visible_assignments(db, course.id)
    attempts_by = {a.id: _student_attempts(db, a.id, user.id) for a in rows}

    def _passed(a: Assignment) -> bool:
        return any(t.passed for t in attempts_by[a.id] if t.status == "graded")

    module_rows: dict[int, list[Assignment]] = {}
    for a in rows:
        if a.module_id is not None:
            module_rows.setdefault(a.module_id, []).append(a)

    modules: list[LearningPathModuleOut] = []
    prev_passed = True  # the first module is always open
    for m in course.modules:  # ordered by position, id
        if m.id not in read_map:  # hidden or empty modules are not part of the path
            continue
        is_read = read_map[m.id]
        mine = module_rows.get(m.id, [])
        assessment_passed = all(_passed(a) for a in mine) if mine else is_read
        passed = is_read and assessment_passed
        unlocked = prev_passed
        if passed:
            status_ = "PASSED"
        elif not unlocked:
            status_ = "LOCKED"
        elif is_read:
            status_ = "READ"  # content finished, assessment still to pass
        else:
            status_ = "AVAILABLE"
        modules.append(
            LearningPathModuleOut(
                id=m.id,
                name=m.name,
                summary=m.summary,
                position=m.position,
                contents_count=sum(1 for c in m.contents if c.visible),
                is_read=is_read,
                assessment_passed=assessment_passed,
                is_unlocked=unlocked,
                status=status_,
            )
        )
        prev_passed = passed

    total = len(modules)
    read_count = sum(1 for m in modules if m.is_read)
    passed_count = sum(1 for m in modules if m.status == "PASSED")
    final_unlocked = bool(read_map) and all(read_map.values())

    final = next((a for a in rows if a.module_id is None), None)
    final_out = None
    if final:
        final_out = LearningPathFinalOut(
            id=final.id,
            title=final.title,
            is_unlocked=final_unlocked,
            status="PASSED" if _passed(final) else ("AVAILABLE" if final_unlocked else "LOCKED"),
        )

    return ApiResponse(
        message="Learning path fetched successfully",
        data=LearningPathOut(
            course=LearningPathCourseOut(
                id=course.id,
                full_name=course.full_name,
                short_name=course.short_name,
                summary=course.summary,
                category_name=course.category.name,
            ),
            progress=LearningPathProgressOut(
                total_modules=total,
                read_modules=read_count,
                passed_modules=passed_count,
                remaining_modules=total - passed_count,
                completion_percentage=round(passed_count / total * 100) if total else 0,
                final_assessment_unlocked=final_unlocked,
            ),
            modules=modules,
            final_assessment=final_out,
        ),
    )