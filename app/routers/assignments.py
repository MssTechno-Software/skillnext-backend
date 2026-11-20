from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.assignment_models import Assignment, AssignmentAttempt, AssignmentQuestion
from app.assignments_schemas import (
    AssignmentCreate,
    AssignmentDetail,
    AssignmentOut,
    AssignmentUpdate,
    AttemptOut,
    AttemptReviewOut,
    AttemptStartOut,
    GradeRequest,
    OptionOut,
    QuestionIn,
    QuestionOut,
    BulkQuestionsIn,
    QuestionResult,
    SubmitRequest,
    TestCase,
)
from app.database import get_db
from app.dependencies import require_role
from app.models import Course, CourseModule, Enrollment, User, utcnow
from app.routers.courses import _get_or_404 as get_course_or_404
from app.routers.courses import _require_owner
from app.schemas import ApiResponse

router = APIRouter(prefix="/api", tags=["assignments"])

GRACE_SECONDS = 30  # network delay allowed after the timer hits zero


# ================================================================== helpers
def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _teacher_assignment(db: Session, assignment_id: int, user: User) -> Assignment:
    a = db.get(Assignment, assignment_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assignment not found.")
    _require_owner(db.get(Course, a.course_id), user)
    return a


def _student_assignment(db: Session, assignment_id: int, user: User) -> Assignment:
    a = db.get(Assignment, assignment_id)
    if a is None or not a.visible:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assignment not found.")
    get_course_or_404(db, a.course_id, user)  # course + category must be visible
    _require_enrolled(db, a.course_id, user)
    return a


def _require_enrolled(db: Session, course_id: int, user: User):
    ok = db.scalar(
        select(Enrollment.id).where(
            Enrollment.course_id == course_id, Enrollment.student_id == user.id
        )
    )
    if not ok:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You are not enrolled in this course.")


def _check_module(db: Session, module_id: int | None, course_id: int):
    if module_id is None:
        return
    m = db.get(CourseModule, module_id)
    if m is None or m.course_id != course_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Module not found in this course.")


def _locked(db: Session, assignment_id: int) -> bool:
    """Questions/marks can't change once any student has started an attempt."""
    return bool(
        db.scalar(
            select(func.count(AssignmentAttempt.id)).where(
                AssignmentAttempt.assignment_id == assignment_id
            )
        )
    )


def _require_unlocked(db: Session, assignment_id: int):
    if _locked(db, assignment_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Students have already attempted this assignment, so its questions can't be changed.",
        )


def _question_fields(p: QuestionIn) -> dict:
    """Map validated input to DB columns, keeping only what this question type uses."""
    f: dict = dict(
        question_type=p.question_type,
        text=p.text,
        score=p.score,
        options=None,
        correct_answer=None,
        language=None,
        starter_code=None,
        test_cases=None,
        correct_reason=p.correct_reason,       # ADD
        incorrect_reason=p.incorrect_reason,
    )
    if p.question_type == "multiple_choice":
        f["options"] = [
            {"id": i, "text": o.text, "is_correct": o.is_correct}
            for i, o in enumerate(p.options, start=1)
        ]
    elif p.question_type == "true_false":
        f["correct_answer"] = p.correct_answer
    elif p.question_type == "text":
        f["correct_answer"] = {"values": p.accepted_answers, "case_sensitive": p.case_sensitive}
    else:  # coding
        f["language"] = p.language.strip().lower()
        f["starter_code"] = p.starter_code
        f["test_cases"] = [t.model_dump() for t in p.test_cases] if p.test_cases else None
    return f


def question_out(q: AssignmentQuestion, reveal: bool) -> QuestionOut:
    """reveal=True is the teacher view (with answers); False hides every answer."""
    out = QuestionOut(
        id=q.id,
        question_type=q.question_type,
        text=q.text,
        score=q.score,
        position=q.position,
        language=q.language,
        starter_code=q.starter_code,
    )
    if q.question_type == "multiple_choice":
        out.options = [
            OptionOut(id=o["id"], text=o["text"], is_correct=o["is_correct"] if reveal else None)
            for o in q.options
        ]
        out.multiple_answers = sum(1 for o in q.options if o["is_correct"]) > 1
    if reveal:
        out.correct_reason = q.correct_reason        # ADD
        out.incorrect_reason = q.incorrect_reason
        if q.question_type == "true_false":
            out.correct_answer = q.correct_answer
        elif q.question_type == "text":
            out.accepted_answers = q.correct_answer["values"]
            out.case_sensitive = q.correct_answer["case_sensitive"]
        elif q.question_type == "coding":
            out.test_cases = [TestCase(**t) for t in q.test_cases] if q.test_cases else None
    return out


def _detail(a: Assignment, reveal: bool) -> AssignmentDetail:
    base = AssignmentOut.model_validate(a).model_dump()
    return AssignmentDetail(**base, questions=[question_out(q, reveal) for q in a.questions])


def _next_position(db: Session, assignment_id: int) -> int:
    return (
        db.scalar(
            select(func.max(AssignmentQuestion.position)).where(
                AssignmentQuestion.assignment_id == assignment_id
            )
        )
        or 0
    ) + 1


# ------------------------------------------------------------------ grading
def _norm(s: str, case_sensitive: bool) -> str:
    s = " ".join(str(s).split())
    return s if case_sensitive else s.casefold()


# def _grade_question(q: AssignmentQuestion, answer) -> tuple[float, bool | None, bool]:
#     """Returns (score, is_correct, needs_review)."""
#     if q.question_type == "coding":
#         if not isinstance(answer, str) or not answer.strip():
#             return 0.0, False, False  # nothing submitted
#         return 0.0, None, True  # teacher grades it

def _grade_question(q: AssignmentQuestion, answer) -> tuple[float, bool | None, bool]:
    """Returns (score, is_correct, needs_review)."""
    if q.question_type == "coding":
        if not isinstance(answer, str) or not answer.strip():
            return 0.0, False, False  # nothing submitted
        # no manual review: any submitted code earns full marks, so the attempt is graded at once
        return q.score, True, False

    if answer is None:
        return 0.0, False, False

    if q.question_type == "true_false":
        ok = isinstance(answer, bool) and answer == q.correct_answer
    elif q.question_type == "multiple_choice":
        correct = {o["id"] for o in q.options if o["is_correct"]}
        try:
            chosen = set(answer) if isinstance(answer, list) else {answer}
            ok = chosen == correct  # all-or-nothing
        except TypeError:
            ok = False
    else:  # text
        cs = q.correct_answer["case_sensitive"]
        accepted = {_norm(v, cs) for v in q.correct_answer["values"]}
        ok = isinstance(answer, str) and _norm(answer, cs) in accepted
    return (q.score if ok else 0.0), ok, False


def _finalize(attempt: AssignmentAttempt, questions: list[AssignmentQuestion]):
    """(Re)compute totals from attempt.results and set status/passed."""
    results = attempt.results or []
    total = round(sum(q.score for q in questions), 2)
    got = round(sum(r["score"] for r in results), 2)
    pct = round(got / total * 100, 2) if total else 0.0
    attempt.score, attempt.total_score, attempt.percentage = got, total, pct
    pending = any(r["needs_review"] for r in results)
    attempt.status = "submitted" if pending else "graded"
    attempt.passed = None if pending else pct >= attempt.assignment.pass_percentage


def _close_attempt(db: Session, attempt: AssignmentAttempt, answers: dict, timed_out: bool):
    a = attempt.assignment
    results = []
    for q in a.questions:
        score, correct, review = _grade_question(q, answers.get(q.id))
        results.append(
            QuestionResult(
                question_id=q.id,
                score=score,
                max_score=q.score,
                is_correct=correct,
                needs_review=review,
                reason=_reason(q, correct),
            ).model_dump(by_alias=False)
        )
    attempt.answers = {str(k): v for k, v in answers.items()}
    attempt.results = results
    attempt.submitted_at = utcnow()
    attempt.timed_out = timed_out
    _finalize(attempt, a.questions)
    db.commit()


def _expire_if_needed(db: Session, attempt: AssignmentAttempt) -> bool:
    """Close an in-progress attempt whose time is over. Returns True if it was closed."""
    if attempt.status != "in_progress":
        return False
    if utcnow() > _aware(attempt.deadline) + timedelta(seconds=GRACE_SECONDS):
        _close_attempt(db, attempt, {}, timed_out=True)
        return True
    return False


# ================================================================== TEACHER: assignments
@router.post(
    "/courses/{course_id}/assignments",
    response_model=ApiResponse[AssignmentOut],
    status_code=status.HTTP_201_CREATED,
)
def create_assignment(
    course_id: int,
    payload: AssignmentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    course = get_course_or_404(db, course_id, user)
    _require_owner(course, user)
    _check_module(db, payload.module_id, course.id)
    a = Assignment(course_id=course.id, created_by=user.id, **payload.model_dump())
    db.add(a)
    db.commit()
    return ApiResponse(message="Assignment created.", data=AssignmentOut.model_validate(a))


@router.get("/courses/{course_id}/assignments", response_model=ApiResponse[list[AssignmentOut]])
def list_assignments(
    course_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher", "student")),
):
    course = get_course_or_404(db, course_id, user)
    q = select(Assignment).where(Assignment.course_id == course.id)
    if user.role == "teacher":
        _require_owner(course, user)
    else:
        _require_enrolled(db, course.id, user)
        q = q.where(Assignment.visible.is_(True))
    rows = db.scalars(q.order_by(Assignment.created_at, Assignment.id)).all()
    return ApiResponse(data=[AssignmentOut.model_validate(a) for a in rows])


@router.get("/assignments/{assignment_id}", response_model=ApiResponse[AssignmentDetail])
def get_assignment(
    assignment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    """Teacher view, including correct answers. Students use /start instead."""
    a = _teacher_assignment(db, assignment_id, user)
    return ApiResponse(data=_detail(a, reveal=True))


@router.patch("/assignments/{assignment_id}", response_model=ApiResponse[AssignmentOut])
def update_assignment(
    assignment_id: int,
    payload: AssignmentUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    a = _teacher_assignment(db, assignment_id, user)
    data = payload.model_dump(exclude_unset=True)
    for required in ("title", "time_limit_minutes", "pass_percentage", "max_attempts", "visible"):
        if required in data and data[required] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{required} cannot be null.")
    if "module_id" in data:
        _check_module(db, data["module_id"], a.course_id)
    if data.get("visible") is True and not a.questions:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Add at least one question before publishing."
        )
    for k, v in data.items():
        setattr(a, k, v)
    db.commit()
    return ApiResponse(message="Assignment updated.", data=AssignmentOut.model_validate(a))


@router.delete("/assignments/{assignment_id}")
def delete_assignment(
    assignment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    a = _teacher_assignment(db, assignment_id, user)
    db.delete(a)  # questions and attempts are deleted with it
    db.commit()
    return {"success": True, "message": "Assignment deleted."}


# ================================================================== TEACHER: questions
@router.post(
    "/assignments/{assignment_id}/questions",
    response_model=ApiResponse[QuestionOut],
    status_code=status.HTTP_201_CREATED,
)
def add_question(
    assignment_id: int,
    payload: QuestionIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    a = _teacher_assignment(db, assignment_id, user)
    _require_unlocked(db, a.id)
    pos = payload.position or _next_position(db, a.id)
    q = AssignmentQuestion(assignment_id=a.id, position=pos, **_question_fields(payload))
    db.add(q)
    db.commit()
    return ApiResponse(message="Question added.", data=question_out(q, reveal=True))


def _teacher_question(db: Session, question_id: int, user: User) -> AssignmentQuestion:
    q = db.get(AssignmentQuestion, question_id)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found.")
    _teacher_assignment(db, q.assignment_id, user)
    return q


@router.put("/questions/{question_id}", response_model=ApiResponse[QuestionOut])
def replace_question(
    question_id: int,
    payload: QuestionIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    """Edit a question: send the full question again (same body as create)."""
    q = _teacher_question(db, question_id, user)
    _require_unlocked(db, q.assignment_id)
    for k, v in _question_fields(payload).items():
        setattr(q, k, v)
    if payload.position:
        q.position = payload.position
    db.commit()
    return ApiResponse(message="Question updated.", data=question_out(q, reveal=True))


@router.delete("/questions/{question_id}")
def delete_question(
    question_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    q = _teacher_question(db, question_id, user)
    _require_unlocked(db, q.assignment_id)
    a = q.assignment
    db.delete(q)
    db.flush()
    db.refresh(a)
    if a.visible and not a.questions:
        a.visible = False  # an empty assignment can't stay published
    db.commit()
    return {"success": True, "message": "Question deleted."}


# ================================================================== TEACHER: attempts & grading
@router.get(
    "/assignments/{assignment_id}/attempts", response_model=ApiResponse[list[AttemptReviewOut]]
)
def list_attempts(
    assignment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    a = _teacher_assignment(db, assignment_id, user)
    rows = db.scalars(
        select(AssignmentAttempt)
        .where(AssignmentAttempt.assignment_id == a.id)
        .order_by(AssignmentAttempt.started_at.desc())
    ).all()
    for r in rows:
        _expire_if_needed(db, r)
    return ApiResponse(data=[AttemptReviewOut.model_validate(r) for r in rows])


@router.patch("/attempts/{attempt_id}/grade", response_model=ApiResponse[AttemptReviewOut])
def grade_attempt(
    attempt_id: int,
    payload: GradeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    """Give marks for coding questions (the only type that isn't auto-graded)."""
    attempt = db.get(AssignmentAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attempt not found.")
    a = _teacher_assignment(db, attempt.assignment_id, user)
    if attempt.status == "in_progress":
        raise HTTPException(status.HTTP_409_CONFLICT, "The student hasn't submitted yet.")

    by_q = {q.id: q for q in a.questions}
    results = [dict(r) for r in (attempt.results or [])]
    by_r = {r["question_id"]: r for r in results}
    for g in payload.grades:
        q, r = by_q.get(g.question_id), by_r.get(g.question_id)
        if q is None or r is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"Question {g.question_id} is not part of this attempt."
            )
        if q.question_type != "coding":
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Question {q.id} is auto-graded; only coding questions can be graded by hand.",
            )
        if g.score > q.score:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Score for question {q.id} can't be more than {q.score}.",
            )
        r["score"] = g.score
        r["is_correct"] = g.score >= q.score
        r["reason"] = _reason(q, r["is_correct"]) 
        r["needs_review"] = False
        r["feedback"] = g.feedback
    attempt.results = results  # reassign so SQLAlchemy sees the JSON change
    _finalize(attempt, a.questions)
    db.commit()
    return ApiResponse(message="Attempt graded.", data=AttemptReviewOut.model_validate(attempt))


# ================================================================== STUDENT
@router.post(
    "/assignments/{assignment_id}/start",
    response_model=ApiResponse[AttemptStartOut],
    status_code=status.HTTP_201_CREATED,
)
def start_attempt(
    assignment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    """Starts the timer. If an attempt is already running, it is returned (resume)."""
    a = _student_assignment(db, assignment_id, user)
    attempts = db.scalars(
        select(AssignmentAttempt)
        .where(AssignmentAttempt.assignment_id == a.id, AssignmentAttempt.student_id == user.id)
        .order_by(AssignmentAttempt.attempt_no)
    ).all()

    current = None
    for t in attempts:
        if not _expire_if_needed(db, t) and t.status == "in_progress":
            current = t
    if current is None:
        if len(attempts) >= a.max_attempts:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "You have used all attempts for this assignment."
            )
        now = utcnow()
        current = AssignmentAttempt(
            assignment_id=a.id,
            student_id=user.id,
            attempt_no=len(attempts) + 1,
            started_at=now,
            deadline=now + timedelta(minutes=a.time_limit_minutes),
        )
        db.add(current)
        try:
            db.commit()
        except IntegrityError:  # double click: two starts at the same moment
            db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, "Attempt already started, please retry.")

    left = max(0, int((_aware(current.deadline) - utcnow()).total_seconds()))
    return ApiResponse(
        data=AttemptStartOut(
            attempt=AttemptOut.model_validate(current),
            assignment=AssignmentOut.model_validate(a),
            questions=[question_out(q, reveal=False) for q in a.questions],
            seconds_left=left,
        )
    )


@router.post("/attempts/{attempt_id}/submit", response_model=ApiResponse[AttemptOut])
def submit_attempt(
    attempt_id: int,
    payload: SubmitRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    attempt = db.get(AssignmentAttempt, attempt_id)
    if attempt is None or attempt.student_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attempt not found.")
    if attempt.status != "in_progress":
        raise HTTPException(status.HTTP_409_CONFLICT, "This attempt was already submitted.")
    if _expire_if_needed(db, attempt):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Time is up. This attempt was closed without your answers."
        )

    valid_ids = {q.id for q in attempt.assignment.questions}
    answers: dict[int, object] = {}
    for item in payload.answers:
        if item.question_id not in valid_ids:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Question {item.question_id} is not part of this assignment.",
            )
        if item.question_id in answers:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Question {item.question_id} was answered twice.",
            )
        answers[item.question_id] = item.answer

    _close_attempt(db, attempt, answers, timed_out=False)
    msg = (
        "Submitted. Coding answers will be graded by your teacher."
        if attempt.status == "submitted"
        else "Submitted."
    )
    return ApiResponse(message=msg, data=AttemptOut.model_validate(attempt))


@router.get(
    "/assignments/{assignment_id}/attempts/me", response_model=ApiResponse[list[AttemptOut]]
)
def my_attempts(
    assignment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    a = _student_assignment(db, assignment_id, user)
    rows = db.scalars(
        select(AssignmentAttempt)
        .where(AssignmentAttempt.assignment_id == a.id, AssignmentAttempt.student_id == user.id)
        .order_by(AssignmentAttempt.attempt_no)
    ).all()
    for r in rows:
        _expire_if_needed(db, r)
    return ApiResponse(data=[AttemptOut.model_validate(r) for r in rows])


from pydantic import ValidationError
from app.assignments_schemas import (
    CodingQuestionIn,
    MultipleChoiceQuestionIn,
    TextQuestionIn,
    TrueFalseQuestionIn,
)

def _reason(q: AssignmentQuestion, is_correct: bool | None) -> str | None:
    """Correct -> correct_reason, wrong -> incorrect_reason, waiting for teacher -> None."""
    if is_correct is None:
        return None
    return q.correct_reason if is_correct else q.incorrect_reason


def _create_question(db: Session, assignment_id: int, body, user: User):
    try:
        payload = body.to_question()
    except ValidationError as exc:
        msg = exc.errors()[0]["msg"].removeprefix("Value error, ")
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, msg)

    a = _teacher_assignment(db, assignment_id, user)
    _require_unlocked(db, a.id)
    pos = payload.position or _next_position(db, a.id)
    q = AssignmentQuestion(assignment_id=a.id, position=pos, **_question_fields(payload))
    db.add(q)
    db.commit()
    return ApiResponse(message="Question added.", data=question_out(q, reveal=True))


@router.post("/assignments/{assignment_id}/questions/multiple-choice",
             response_model=ApiResponse[QuestionOut], status_code=status.HTTP_201_CREATED)
def add_multiple_choice(assignment_id: int, payload: MultipleChoiceQuestionIn,
                        db: Session = Depends(get_db),
                        user: User = Depends(require_role("teacher"))):
    return _create_question(db, assignment_id, payload, user)


@router.post("/assignments/{assignment_id}/questions/true-false",
             response_model=ApiResponse[QuestionOut], status_code=status.HTTP_201_CREATED)
def add_true_false(assignment_id: int, payload: TrueFalseQuestionIn,
                   db: Session = Depends(get_db),
                   user: User = Depends(require_role("teacher"))):
    return _create_question(db, assignment_id, payload, user)


@router.post("/assignments/{assignment_id}/questions/text",
             response_model=ApiResponse[QuestionOut], status_code=status.HTTP_201_CREATED)
def add_text_question(assignment_id: int, payload: TextQuestionIn,
                      db: Session = Depends(get_db),
                      user: User = Depends(require_role("teacher"))):
    return _create_question(db, assignment_id, payload, user)


@router.post("/assignments/{assignment_id}/questions/coding",
             response_model=ApiResponse[QuestionOut], status_code=status.HTTP_201_CREATED)
def add_coding_question(assignment_id: int, payload: CodingQuestionIn,
                        db: Session = Depends(get_db),
                        user: User = Depends(require_role("teacher"))):
    return _create_question(db, assignment_id, payload, user)


@router.post(
    "/assignments/{assignment_id}/questions/bulk",
    response_model=ApiResponse[list[QuestionOut]],
    status_code=status.HTTP_201_CREATED,
)
def add_questions_bulk(
    assignment_id: int,
    payload: BulkQuestionsIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    """Add many questions (multiple_choice / true_false / text / coding, mixed) in one call.

    All-or-nothing: every question is validated first (a bad one is reported as
    questions.<index>... with a 422) and nothing is saved unless all of them are valid.
    Questions without `position` are appended after the existing ones, in the order sent.
    """
    a = _teacher_assignment(db, assignment_id, user)
    _require_unlocked(db, a.id)

    next_pos = _next_position(db, a.id)
    created: list[AssignmentQuestion] = []
    for item in payload.questions:
        pos = item.position or next_pos
        next_pos = max(next_pos, pos) + 1
        created.append(
            AssignmentQuestion(assignment_id=a.id, position=pos, **_question_fields(item))
        )
    db.add_all(created)
    db.commit()
    return ApiResponse(
        message=f"{len(created)} questions added.",
        data=[question_out(q, reveal=True) for q in created],
    )