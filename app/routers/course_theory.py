"""Course theory interview.

Same flow as Pathway's topic-specific theory module, but questions belong to a
COURSE (course_id) instead of technology -> sub-topic. A student only gets the
questions of courses they are enrolled in.
"""

import logging
from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_evaluators import evaluate_theory_answer
from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.interview_common import (
    check_student,
    get_limit,
    get_state,
    set_limit,
    utc_now,
    utc_today,
)
from app.interview_models import CourseQuestion, CourseQuestionResponse
from app.models import Course, User
from app.routers.assignments import _require_enrolled
from app.routers.courses import _get_or_404 as get_course_or_404

logger = logging.getLogger("course_theory")

router = APIRouter(prefix="/api/course-theory", tags=["course-theory"])

DAILY_LIMIT_KEY = "course_theory_daily_limit"
DAILY_LIMIT_DEFAULT = 15


# ---------------------------------------------------------------- schemas
class CourseQuestionItem(BaseModel):
    question_text: str = Field(min_length=1)
    reference_answer: str = Field(min_length=1)
    difficulty_level: str = Field(min_length=1, max_length=30)
    display_order: int = 0
    is_active: bool = True


class BulkCourseQuestionCreate(BaseModel):
    questions: list[CourseQuestionItem] = Field(min_length=1)


class CourseQuestionUpdate(BaseModel):
    question_text: str | None = None
    reference_answer: str | None = None
    difficulty_level: str | None = None
    display_order: int | None = None
    is_active: bool | None = None


class SubmitAnswerRequest(BaseModel):
    answer_text: str


# ---------------------------------------------------------------- helpers
def _question_or_404(db: Session, question_id: int) -> CourseQuestion:
    q = db.get(CourseQuestion, question_id)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    return q


def _active_questions(db: Session, course_id: int, difficulty_level: str | None):
    stmt = select(CourseQuestion).where(
        CourseQuestion.course_id == course_id, CourseQuestion.is_active.is_(True)
    )
    if difficulty_level:
        stmt = stmt.where(CourseQuestion.difficulty_level == difficulty_level)
    return db.scalars(stmt.order_by(CourseQuestion.display_order, CourseQuestion.id)).all()


def _response_map(db: Session, student_id: int, questions) -> dict[int, CourseQuestionResponse]:
    if not questions:
        return {}
    rows = db.scalars(
        select(CourseQuestionResponse).where(
            CourseQuestionResponse.student_id == student_id,
            CourseQuestionResponse.question_id.in_([q.id for q in questions]),
        )
    ).all()
    return {r.question_id: r for r in rows}


def _stepper(questions, response_map) -> list[dict]:
    """completed / skipped for answered ones, first untouched one is 'current'."""
    steps, current_assigned = [], False
    for q in questions:
        r = response_map.get(q.id)
        if r:
            state = r.question_status
        elif not current_assigned:
            state, current_assigned = "current", True
        else:
            state = "not_attempted"
        steps.append(
            {
                "question_id": q.id,
                "question_order": q.display_order,
                "question_text": q.question_text,
                "attempted_status": state,
            }
        )
    return steps


# ---------------------------------------------------- question bank (teacher)
@router.post("/courses/{course_id}/questions", status_code=status.HTTP_201_CREATED)
def create_course_questions(
    course_id: int,
    payload: BulkCourseQuestionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    if db.get(Course, course_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Course not found.")

    created = [
        CourseQuestion(
            course_id=course_id,
            question_text=item.question_text.strip(),
            reference_answer=item.reference_answer.strip(),
            difficulty_level=item.difficulty_level.strip(),
            display_order=item.display_order,
            is_active=item.is_active,
            created_by=user.id,
        )
        for item in payload.questions
    ]
    if any(not q.question_text or not q.reference_answer or not q.difficulty_level for q in created):
        raise HTTPException(400, "Question text, reference answer and difficulty level cannot be empty")

    db.add_all(created)
    db.commit()
    return {
        "message": "Questions created successfully",
        "course_id": course_id,
        "total_questions": len(created),
        "question_ids": [q.id for q in created],
    }


@router.patch("/questions/{question_id}")
def update_course_question(
    question_id: int,
    payload: CourseQuestionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    q = _question_or_404(db, question_id)
    data = payload.model_dump(exclude_unset=True)
    for field in ("question_text", "reference_answer", "difficulty_level"):
        if data.get(field) is not None:
            setattr(q, field, data[field].strip())
    if data.get("display_order") is not None:
        q.display_order = data["display_order"]
    if data.get("is_active") is not None:
        q.is_active = data["is_active"]
    db.commit()
    return {
        "message": "Question updated successfully",
        "question": {
            "id": q.id,
            "course_id": q.course_id,
            "question_text": q.question_text,
            "reference_answer": q.reference_answer,
            "difficulty_level": q.difficulty_level,
            "display_order": q.display_order,
            "is_active": q.is_active,
        },
    }


@router.delete("/questions/{question_id}")
def delete_course_question(
    question_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    q = _question_or_404(db, question_id)
    db.delete(q)
    db.commit()
    return {"message": "Question deleted successfully"}


# ----------------------------------------------------- get a course's questions
@router.get("/courses/{course_id}/questions")
def get_course_questions(
    course_id: int,
    difficulty_level: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    course = get_course_or_404(db, course_id, user)  # 404 if hidden from students

    if user.role == "teacher":
        stmt = select(CourseQuestion).where(CourseQuestion.course_id == course_id)
        if difficulty_level:
            stmt = stmt.where(CourseQuestion.difficulty_level == difficulty_level)
        rows = db.scalars(stmt.order_by(CourseQuestion.display_order, CourseQuestion.id)).all()
        items = [
            {
                "question_id": q.id,
                "question_text": q.question_text,
                "reference_answer": q.reference_answer,
                "difficulty_level": q.difficulty_level,
                "display_order": q.display_order,
                "is_active": q.is_active,
            }
            for q in rows
        ]
    else:
        _require_enrolled(db, course_id, user)
        rows = _active_questions(db, course_id, difficulty_level)
        steps = _stepper(rows, _response_map(db, user.id, rows))
        # students never receive the reference answer
        items = [
            {
                "question_id": s["question_id"],
                "question_text": s["question_text"],
                "difficulty_level": q.difficulty_level,
                "display_order": s["question_order"],
                "is_active": True,
                "attempted_status": s["attempted_status"],
            }
            for s, q in zip(steps, rows)
        ]

    return {
        "course_id": course.id,
        "course_name": course.full_name,
        "difficulty_level": difficulty_level,
        "total_questions": len(items),
        "questions": items,
    }


# ------------------------------------------------------------ daily limit
def _daily_count(state) -> int:
    today = utc_today()
    if state.course_theory_daily_count_date != today:
        state.course_theory_daily_count = 0
        state.course_theory_daily_count_date = today
    return state.course_theory_daily_count or 0


@router.get("/students/{student_id}/daily-limit")
def get_daily_status(
    student_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    check_student(db, student_id, user)
    today = utc_today()
    daily_limit = get_limit(db, DAILY_LIMIT_KEY, DAILY_LIMIT_DEFAULT)
    state = get_state(db, student_id)
    submitted_today = _daily_count(state)
    db.commit()
    tomorrow_midnight = datetime.combine(today + timedelta(days=1), time.min)
    return {
        "student_id": student_id,
        "date": today.strftime("%b %d %Y"),
        "daily_limit": daily_limit,
        "submitted_today": submitted_today,
        "remaining": max(0, daily_limit - submitted_today),
        "limit_reached": submitted_today >= daily_limit,
        "resets_at": tomorrow_midnight.strftime("%b %d %Y 00:00 UTC"),
    }


@router.put("/admin/daily-limit")
def set_daily_limit(
    limit: int = Query(..., ge=1, le=100, description="New daily limit (1-100)"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    set_limit(db, DAILY_LIMIT_KEY, limit, "Maximum course theory questions a student can submit per day")
    return {"message": f"Daily limit updated to {limit}", "key": DAILY_LIMIT_KEY, "daily_limit": limit}


# ------------------------------------------------------------ submit / skip
@router.post("/students/{student_id}/questions/{question_id}/submit-answer")
def submit_answer(
    student_id: int,
    question_id: int,
    payload: SubmitAnswerRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    check_student(db, student_id, user)

    question = _question_or_404(db, question_id)
    if not question.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    get_course_or_404(db, question.course_id, user)
    _require_enrolled(db, question.course_id, user)

    answer_text = (payload.answer_text or "").strip()
    if not answer_text:
        raise HTTPException(400, "Answer cannot be empty")

    existing = db.scalar(
        select(CourseQuestionResponse).where(
            CourseQuestionResponse.student_id == student_id,
            CourseQuestionResponse.question_id == question_id,
        )
    )
    previous_attempts = existing.attempt_count if existing else 0

    # ---- daily limit (counts every accepted submit, including resubmits)
    daily_limit = get_limit(db, DAILY_LIMIT_KEY, DAILY_LIMIT_DEFAULT)
    state = get_state(db, student_id)
    current_daily = _daily_count(state)
    if current_daily >= daily_limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Daily limit reached. You can submit a maximum of {daily_limit} course theory "
            f"questions per day. You have submitted {current_daily} today. Try again tomorrow.",
        )

    # ---- evaluate first: a failed AI call does not use up the daily quota
    try:
        evaluation, _resp = evaluate_theory_answer(
            question=question.question_text,
            reference_answer=question.reference_answer,
            answer=answer_text,
        )
    except Exception:
        logger.exception("Course theory evaluation failed student_id=%s question_id=%s", student_id, question_id)
        raise HTTPException(500, "Evaluation failed. Please try again.")

    strengths = evaluation.get("strengths", [])
    improvement_areas = evaluation.get("improvement_areas", [])

    if existing:
        existing.answer_text = answer_text
        existing.attempted_status = True
        existing.question_status = "completed"
        existing.attempt_count = previous_attempts + 1
        existing.submitted_at = utc_now()
        existing.overall_score = evaluation.get("overall_score")
        existing.comprehensive_strengths = strengths
        existing.strategic_improvements = improvement_areas
        existing.evaluation = evaluation
        record = existing
    else:
        record = CourseQuestionResponse(
            student_id=student_id,
            question_id=question_id,
            answer_text=answer_text,
            attempted_status=True,
            question_status="completed",
            attempt_count=1,
            submitted_at=utc_now(),
            overall_score=evaluation.get("overall_score"),
            comprehensive_strengths=strengths,
            strategic_improvements=improvement_areas,
            evaluation=evaluation,
        )
        db.add(record)

    state.course_theory_daily_count = current_daily + 1
    db.commit()
    db.refresh(record)

    # ---- stepper for this course + difficulty
    questions = _active_questions(db, question.course_id, question.difficulty_level)
    rmap = _response_map(db, student_id, questions)
    round_complete = bool(questions) and all(
        rmap.get(q.id) and rmap[q.id].question_status == "completed" for q in questions
    )

    return {
        "message": "Answer submitted successfully",
        "response_id": record.id,
        "student_id": student_id,
        "question_id": question.id,
        "course_id": question.course_id,
        "difficulty_level": question.difficulty_level,
        "attempted_status": True,
        "attempt_count": record.attempt_count,
        "daily_submissions": state.course_theory_daily_count,
        "remaining_today": max(0, daily_limit - state.course_theory_daily_count),
        "round_completed": round_complete,
        "evaluation": {
            "overall_score": evaluation.get("overall_score"),
            "scores": evaluation.get("scores", {}),
            "matched_points": evaluation.get("matched_points", []),
            "missing_points": evaluation.get("missing_points", []),
            "strengths": strengths,
            "improvement_areas": improvement_areas,
            "overall_feedback": evaluation.get("overall_feedback"),
            "candidate_tier": evaluation.get("candidate_tier"),
        },
        "question_stepper": _stepper(questions, rmap),
    }


@router.post("/students/{student_id}/questions/{question_id}/skip")
def skip_question(
    student_id: int,
    question_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("student")),
):
    check_student(db, student_id, user)
    question = _question_or_404(db, question_id)
    _require_enrolled(db, question.course_id, user)

    record = db.scalar(
        select(CourseQuestionResponse).where(
            CourseQuestionResponse.student_id == student_id,
            CourseQuestionResponse.question_id == question_id,
        )
    )
    if record:
        record.question_status = "skipped"
        record.attempted_status = False
        record.submitted_at = utc_now()
    else:
        db.add(
            CourseQuestionResponse(
                student_id=student_id,
                question_id=question_id,
                answer_text="",
                attempted_status=False,
                question_status="skipped",
                attempt_count=0,
                submitted_at=utc_now(),
            )
        )
    db.commit()
    return {
        "message": "Question skipped successfully",
        "question_id": question_id,
        "question_status": "skipped",
    }