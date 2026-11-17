from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models import utcnow

JsonType = JSON().with_variant(JSONB(), "postgresql")

QUESTION_TYPES = ("multiple_choice", "true_false", "text", "coding")


class Assignment(Base):
    """A graded quiz-style assignment inside a course (optionally inside a module)."""

    __tablename__ = "assignments"
    __table_args__ = (
        CheckConstraint("time_limit_minutes > 0", name="ck_assignments_time_limit"),
        CheckConstraint(
            "pass_percentage >= 0 AND pass_percentage <= 100", name="ck_assignments_pass_pct"
        ),
        CheckConstraint("max_attempts >= 1", name="ck_assignments_max_attempts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    module_id: Mapped[int | None] = mapped_column(
        ForeignKey("course_modules.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_limit_minutes: Mapped[int] = mapped_column(Integer)
    pass_percentage: Mapped[float] = mapped_column(Float)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1)
    visible: Mapped[bool] = mapped_column(Boolean, default=False)  # draft until teacher publishes
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    questions: Mapped[list["AssignmentQuestion"]] = relationship(
        back_populates="assignment",
        cascade="all, delete-orphan",
        order_by="AssignmentQuestion.position, AssignmentQuestion.id",
    )
    attempts: Mapped[list["AssignmentAttempt"]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan"
    )

    @property
    def total_score(self) -> float:
        return round(sum(q.score for q in self.questions), 2)

    @property
    def question_count(self) -> int:
        return len(self.questions)


class AssignmentQuestion(Base):
    __tablename__ = "assignment_questions"
    __table_args__ = (
        CheckConstraint(
            "question_type IN ('multiple_choice','true_false','text','coding')",
            name="ck_assignment_questions_type",
        ),
        CheckConstraint("score > 0", name="ck_assignment_questions_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), index=True
    )
    question_type: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    score: Mapped[float] = mapped_column(Float)  # marks for this question
    position: Mapped[int] = mapped_column(Integer, default=1)

    # multiple_choice: [{"id": 1, "text": "...", "is_correct": true}, ...]
    options: Mapped[list | None] = mapped_column(JsonType, nullable=True)
    # true_false: true / false
    # text: {"values": ["answer1", "answer2"], "case_sensitive": false}
    correct_answer: Mapped[dict | bool | None] = mapped_column(JsonType, nullable=True)
    # coding (graded manually by the teacher)
    language: Mapped[str | None] = mapped_column(String(30), nullable=True)
    starter_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    test_cases: Mapped[list | None] = mapped_column(JsonType, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    assignment: Mapped[Assignment] = relationship(back_populates="questions")
    correct_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    incorrect_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

class AssignmentAttempt(Base):
    """One try by one student. Created on /start, closed on /submit (or when time runs out)."""

    __tablename__ = "assignment_attempts"
    __table_args__ = (
        UniqueConstraint(
            "assignment_id", "student_id", "attempt_no", name="uq_attempts_assignment_student_no"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    # in_progress -> submitted (waiting for teacher to grade coding) -> graded
    status: Mapped[str] = mapped_column(String(20), default="in_progress")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    answers: Mapped[dict | None] = mapped_column(JsonType, nullable=True)  # {question_id: answer}
    results: Mapped[list | None] = mapped_column(JsonType, nullable=True)  # per-question marks
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    timed_out: Mapped[bool] = mapped_column(Boolean, default=False)

    assignment: Mapped[Assignment] = relationship(back_populates="attempts")
    student: Mapped["User"] = relationship(foreign_keys=[student_id])  # noqa: F821