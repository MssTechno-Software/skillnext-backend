"""Self-introduction interview: add questions, submit an answer, get AI evaluation."""

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai_evaluators import evaluate_self_introduction
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
from app.interview_models import SelfIntroQuestion, SelfIntroResponse
from app.models import User

logger = logging.getLogger("self_introduction")

router = APIRouter(prefix="/api/self-introduction", tags=["self-introduction"])

DAILY_LIMIT_KEY = "self_intro_daily_limit"
DAILY_LIMIT_DEFAULT = 10


# ---------------------------------------------------------------- schemas
class SelfIntroQuestionItem(BaseModel):
    question_text: str
    reference_answer: str
    display_order: int = 0


class BulkSelfIntroQuestionCreate(BaseModel):
    questions: list[SelfIntroQuestionItem] = Field(min_length=1)


class SelfIntroQuestionUpdate(BaseModel):
    question_text: str | None = None
    reference_answer: str | None = None
    display_order: int | None = None
    is_active: bool | None = None


class SubmitAnswerRequest(BaseModel):
    answer_text: str


# ---------------------------------------------------------------- helpers
def _daily_count(state) -> int:
    """Today's submission count; rolls the counter over when the date changed."""
    today = utc_today()
    if state.self_intro_daily_count_date != today:
        state.self_intro_daily_count = 0
        state.self_intro_daily_count_date = today
    return state.self_intro_daily_count or 0


def _question_or_404(db: Session, question_id: int) -> SelfIntroQuestion:
    q = db.get(SelfIntroQuestion, question_id)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    return q


# ---------------------------------------------------- question bank (teacher)
@router.post("/questions", status_code=status.HTTP_201_CREATED)
def create_questions(
    payload: BulkSelfIntroQuestionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    normalized = []
    for item in payload.questions:
        text = item.question_text.strip()
        reference = item.reference_answer.strip()
        if not text:
            raise HTTPException(400, "Question text cannot be empty")
        if not reference:
            raise HTTPException(400, f"Reference answer is required for: {text}")
        normalized.append((item, text, reference))

    lowered = [t.lower() for _, t, _ in normalized]
    existing = {
        t.lower()
        for t in db.scalars(
            select(SelfIntroQuestion.question_text).where(
                func.lower(SelfIntroQuestion.question_text).in_(lowered)
            )
        )
    }
    seen: set[str] = set()
    for _, text, _ in normalized:
        low = text.lower()
        if low in existing:
            raise HTTPException(400, f"Duplicate question: {text}")
        if low in seen:
            raise HTTPException(400, f"Duplicate question in request: {text}")
        seen.add(low)

    created = [
        SelfIntroQuestion(
            question_text=text,
            expected_points=reference,
            display_order=item.display_order,
            is_active=True,
        )
        for item, text, reference in normalized
    ]
    db.add_all(created)
    db.commit()

    return {
        "message": "Questions created successfully",
        "total_questions": len(created),
        "questions": [
            {
                "id": q.id,
                "question_text": q.question_text,
                "expected_points": q.expected_points,
                "display_order": q.display_order,
            }
            for q in created
        ],
    }


@router.get("/questions")
def list_questions(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    """Teacher view: includes the reference answer (expected_points)."""
    questions = db.scalars(
        select(SelfIntroQuestion).order_by(SelfIntroQuestion.display_order, SelfIntroQuestion.id)
    ).all()
    return {
        "total_questions": len(questions),
        "questions": [
            {
                "id": q.id,
                "display_order": q.display_order,
                "question_text": q.question_text,
                "expected_points": q.expected_points,
                "is_active": q.is_active,
            }
            for q in questions
        ],
    }


@router.put("/questions/{question_id}")
def update_question(
    question_id: int,
    payload: SelfIntroQuestionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    q = _question_or_404(db, question_id)
    if payload.question_text is not None:
        q.question_text = payload.question_text.strip()
    if payload.reference_answer is not None:
        q.expected_points = payload.reference_answer.strip()
    if payload.display_order is not None:
        q.display_order = payload.display_order
    if payload.is_active is not None:
        q.is_active = payload.is_active
    db.commit()
    return {
        "message": "Question updated successfully",
        "question": {
            "id": q.id,
            "question_text": q.question_text,
            "reference_answer": q.expected_points,
            "display_order": q.display_order,
            "is_active": q.is_active,
        },
    }


@router.delete("/questions/{question_id}")
def delete_question(
    question_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    q = _question_or_404(db, question_id)
    db.delete(q)
    db.commit()
    return {"message": "Question deleted successfully"}


# ------------------------------------------------------------- student side
@router.get("/students/{student_id}/questions")
def get_questions_for_student(
    student_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    check_student(db, student_id, user)
    questions = db.scalars(
        select(SelfIntroQuestion).order_by(SelfIntroQuestion.display_order, SelfIntroQuestion.id)
    ).all()
    attempted_ids = set(
        db.scalars(select(SelfIntroResponse.question_id).where(SelfIntroResponse.student_id == student_id))
    )
    rows = [
        {
            "question_id": q.id,
            "question_text": q.question_text,
            "display_order": q.display_order,
            "status": "enabled" if q.is_active else "disabled",
            "attempted_status": q.id in attempted_ids,
        }
        for q in questions
    ]
    return {"student_id": student_id, "total_questions": len(rows), "questions": rows}


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
    return {
        "student_id": student_id,
        "date": today.strftime("%b %d %Y"),
        "daily_limit": daily_limit,
        "submitted_today": submitted_today,
        "remaining": max(0, daily_limit - submitted_today),
        "limit_reached": submitted_today >= daily_limit,
        "resets_at": f"{(today + timedelta(days=1)).strftime('%b %d %Y')} 00:00 UTC",
    }


@router.put("/admin/daily-limit")
def set_daily_limit(
    limit: int = Query(..., ge=1, le=100, description="New daily submission limit (1-100)"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("teacher")),
):
    set_limit(db, DAILY_LIMIT_KEY, limit, "Max self-introduction questions a student can submit per day")
    return {"message": f"Self-introduction daily limit updated to {limit}"}


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

    answer_text = (payload.answer_text or "").strip()
    if not answer_text:
        raise HTTPException(400, "Answer cannot be empty")

    # ---- daily limit (counts every submission, including resubmits)
    daily_limit = get_limit(db, DAILY_LIMIT_KEY, DAILY_LIMIT_DEFAULT)
    state = get_state(db, student_id)
    current_daily = _daily_count(state)
    if current_daily >= daily_limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Daily limit reached. You can submit a maximum of {daily_limit} self introduction "
            f"questions per day. You have submitted {current_daily} today. Try again tomorrow.",
        )

    # ---- round management
    active_ids = list(db.scalars(select(SelfIntroQuestion.id).where(SelfIntroQuestion.is_active.is_(True))))
    attempted = dict(state.self_intro_attempted_questions or {})

    previous_round_complete = bool(active_ids) and all(
        attempted.get(f"Q{qid}") == "true" for qid in active_ids
    )
    if previous_round_complete:  # start a fresh round
        attempted = {f"Q{qid}": "false" for qid in active_ids}
    for qid in active_ids:  # newly added questions get a key
        attempted.setdefault(f"Q{qid}", "false")

    # ---- save the answer (one row per student+question, attempt_count carried over)
    record = db.scalar(
        select(SelfIntroResponse).where(
            SelfIntroResponse.student_id == student_id,
            SelfIntroResponse.question_id == question_id,
        )
    )
    if record:
        record.answer_text = answer_text
        record.attempt_count = (record.attempt_count or 0) + 1
        record.score = None
        record.feedback = None
        record.strengths = None
        record.improvement_areas = None
        record.evaluation = None
        record.submitted_at = utc_now()
    else:
        record = SelfIntroResponse(
            student_id=student_id,
            question_id=question_id,
            answer_text=answer_text,
            attempt_count=1,
            submitted_at=utc_now(),
        )
        db.add(record)

    attempted[f"Q{question_id}"] = "true"
    all_answered = all(attempted.get(f"Q{qid}") == "true" for qid in active_ids)
    if all_answered:
        state.self_intro_attempt_count = (state.self_intro_attempt_count or 0) + 1
    state.self_intro_daily_count = current_daily + 1
    state.self_intro_attempted_questions = attempted
    db.commit()
    db.refresh(record)

    # ---- evaluate synchronously and return the full result
    evaluation_result = None
    try:
        evaluation, _resp = evaluate_self_introduction(
            question=question.question_text,
            expected_points=question.expected_points,
            answer=record.answer_text,
        )
        record.score = evaluation.get("overall_score")
        record.feedback = evaluation.get("overall_feedback")
        record.strengths = evaluation.get("strengths", [])
        record.improvement_areas = evaluation.get("improvement_areas", [])
        record.evaluation = evaluation
        db.commit()

        evaluation_result = {
            "overall_score": evaluation.get("overall_score"),
            "scores": evaluation.get("scores", {}),
            "matched_points": evaluation.get("matched_points", []),
            "missing_points": evaluation.get("missing_points", []),
            "strengths": evaluation.get("strengths", []),
            "improvement_areas": evaluation.get("improvement_areas", []),
            "overall_feedback": evaluation.get("overall_feedback"),
            "candidate_tier": evaluation.get("candidate_tier"),
        }
    except Exception:
        logger.exception("Self-intro evaluation failed student_id=%s question_id=%s", student_id, question_id)

    return {
        "message": "Answer submitted successfully",
        "response_id": record.id,
        "student_id": student_id,
        "question_id": question_id,
        "interview_mode": "self_introduction",
        "attempted_status": True,
        "attempt_count": record.attempt_count,
        "daily_submissions": state.self_intro_daily_count,
        "remaining_today": max(0, daily_limit - state.self_intro_daily_count),
        "self_intro_round_count": state.self_intro_attempt_count,
        "self_intro_attempted_questions": state.self_intro_attempted_questions,
        "round_completed": all_answered,
        "evaluation": evaluation_result,
    }


@router.get("/students/{student_id}/questions/{question_id}/evaluation")
def get_evaluation(
    student_id: int,
    question_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    check_student(db, student_id, user)
    record = db.scalar(
        select(SelfIntroResponse).where(
            SelfIntroResponse.student_id == student_id,
            SelfIntroResponse.question_id == question_id,
        )
    )
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No submission found for this question")

    ready = record.score is not None
    return {
        "student_id": student_id,
        "question_id": question_id,
        "status": "ready" if ready else "processing",
        "evaluation": {
            "overall_score": record.score,
            "overall_feedback": record.feedback,
            "strengths": record.strengths or [],
            "improvement_areas": record.improvement_areas or [],
        }
        if ready
        else None,
    }