"""Helpers shared by the self-introduction and course-theory routers."""

from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.interview_models import AppConfig, StudentInterviewState
from app.models import User


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_today() -> date:
    return utc_now().date()


# --------------------------------------------------------------- daily limits
def get_limit(db: Session, key: str, default: int) -> int:
    row = db.get(AppConfig, key)
    if row:
        try:
            return int(row.value)
        except ValueError:
            pass
    return default


def set_limit(db: Session, key: str, value: int, description: str) -> None:
    row = db.get(AppConfig, key)
    if row:
        row.value = str(value)
    else:
        db.add(AppConfig(key=key, value=str(value), description=description))
    db.commit()


# ------------------------------------------------------------- student state
def get_state(db: Session, student_id: int) -> StudentInterviewState:
    state = db.scalar(
        select(StudentInterviewState).where(StudentInterviewState.student_id == student_id)
    )
    if state is None:
        state = StudentInterviewState(
            student_id=student_id,
            self_intro_daily_count=0,
            self_intro_attempt_count=0,
            self_intro_attempted_questions={},
            course_theory_daily_count=0,
        )
        db.add(state)
        db.flush()
    return state


def check_student(db: Session, student_id: int, user: User) -> User:
    """A student may only act as themselves; a teacher may look at any student."""
    if user.role == "student" and user.id != student_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only access your own data.")
    target = user if user.id == student_id else db.get(User, student_id)
    if target is None or target.role != "student":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Student not found.")
    return target