"""Tables for the AI interview features (self-introduction + course theory)."""

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models import utcnow

JsonType = JSON().with_variant(JSONB(), "postgresql")


class AppConfig(Base):
    """Small key/value settings table (used for the daily submission limits)."""

    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)


class StudentInterviewState(Base):
    """Per-student counters (replaces the extra columns Pathway keeps on Client)."""

    __tablename__ = "student_interview_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )

    # self-introduction
    self_intro_daily_count: Mapped[int] = mapped_column(Integer, default=0)
    self_intro_daily_count_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    self_intro_attempt_count: Mapped[int] = mapped_column(Integer, default=0)  # finished rounds
    self_intro_attempted_questions: Mapped[dict | None] = mapped_column(JsonType, nullable=True)

    # course theory
    course_theory_daily_count: Mapped[int] = mapped_column(Integer, default=0)
    course_theory_daily_count_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# ------------------------------------------------------------ self introduction
class SelfIntroQuestion(Base):
    __tablename__ = "self_intro_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_text: Mapped[str] = mapped_column(String(1000), unique=True)
    expected_points: Mapped[str] = mapped_column(Text)  # the reference answer
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SelfIntroResponse(Base):
    __tablename__ = "self_intro_responses"
    __table_args__ = (
        UniqueConstraint("student_id", "question_id", name="uq_self_intro_student_question"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("self_intro_questions.id", ondelete="CASCADE"), index=True
    )
    answer_text: Mapped[str] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    strengths: Mapped[list | None] = mapped_column(JsonType, nullable=True)
    improvement_areas: Mapped[list | None] = mapped_column(JsonType, nullable=True)
    evaluation: Mapped[dict | None] = mapped_column(JsonType, nullable=True)  # full AI result
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------- course theory
class CourseQuestion(Base):
    """A theory question that belongs to one course (served to students of that course)."""

    __tablename__ = "course_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    question_text: Mapped[str] = mapped_column(Text)
    reference_answer: Mapped[str] = mapped_column(Text)
    difficulty_level: Mapped[str] = mapped_column(String(30), index=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CourseQuestionResponse(Base):
    __tablename__ = "course_question_responses"
    __table_args__ = (
        UniqueConstraint("student_id", "question_id", name="uq_course_question_student_question"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("course_questions.id", ondelete="CASCADE"), index=True
    )
    answer_text: Mapped[str] = mapped_column(Text, default="")
    question_status: Mapped[str] = mapped_column(String(20), default="completed")  # completed | skipped
    attempted_status: Mapped[bool] = mapped_column(Boolean, default=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    overall_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comprehensive_strengths: Mapped[list | None] = mapped_column(JsonType, nullable=True)
    strategic_improvements: Mapped[list | None] = mapped_column(JsonType, nullable=True)
    evaluation: Mapped[dict | None] = mapped_column(JsonType, nullable=True)  # full AI result
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)