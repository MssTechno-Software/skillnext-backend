from datetime import datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.schemas import CamelModel, StudentBrief

QuestionType = Literal["multiple_choice", "true_false", "text", "coding"]


# ============================================================ ASSIGNMENT (teacher input)
class AssignmentCreate(CamelModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    module_id: int | None = None
    time_limit_minutes: int = Field(gt=0, le=1440, description="Total time for the whole assignment")
    pass_percentage: float = Field(ge=0, le=100)
    max_attempts: int = Field(default=1, ge=1, le=20)
    visible: bool = False

    @field_validator("description", mode="before")
    @classmethod
    def _blank(cls, v):
        return None if isinstance(v, str) and not v.strip() else v


class AssignmentUpdate(CamelModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    module_id: int | None = None
    time_limit_minutes: int | None = Field(default=None, gt=0, le=1440)
    pass_percentage: float | None = Field(default=None, ge=0, le=100)
    max_attempts: int | None = Field(default=None, ge=1, le=20)
    visible: bool | None = None


# ============================================================ QUESTION (teacher input)
class OptionIn(CamelModel):
    text: str = Field(min_length=1, max_length=1000)
    is_correct: bool = False


class TestCase(CamelModel):
    input: str = ""
    expected_output: str = ""


class QuestionIn(CamelModel):
    """Same shape for create and update (update replaces the whole question)."""

    question_type: QuestionType
    text: str = Field(min_length=1)
    score: float = Field(gt=0, le=1000, description="Marks for this question")
    position: int | None = Field(default=None, ge=1)

    # multiple_choice
    options: list[OptionIn] | None = None
    # true_false
    correct_answer: bool | None = None
    # text ("enter the answer")
    accepted_answers: list[str] | None = None
    case_sensitive: bool = False
    # coding
    language: str | None = Field(default=None, max_length=30)
    starter_code: str | None = None
    test_cases: list[TestCase] | None = None

    @model_validator(mode="after")
    def _check_by_type(self):
        t = self.question_type
        if t == "multiple_choice":
            if not self.options or len(self.options) < 2:
                raise ValueError("multiple_choice needs at least 2 options.")
            if len(self.options) > 10:
                raise ValueError("multiple_choice allows at most 10 options.")
            if not any(o.is_correct for o in self.options):
                raise ValueError("Mark at least one option as correct.")
            texts = [o.text.lower() for o in self.options]
            if len(set(texts)) != len(texts):
                raise ValueError("Options must be different from each other.")
        elif t == "true_false":
            if self.correct_answer is None:
                raise ValueError("true_false needs correctAnswer (true or false).")
        elif t == "text":
            cleaned = [a.strip() for a in (self.accepted_answers or []) if a and a.strip()]
            if not cleaned:
                raise ValueError("text needs at least one value in acceptedAnswers.")
            self.accepted_answers = cleaned
        elif t == "coding":
            if not self.language or not self.language.strip():
                raise ValueError("coding needs a language, e.g. python or java.")
        return self


class GradeItem(CamelModel):
    question_id: int
    score: float = Field(ge=0)
    feedback: str | None = Field(default=None, max_length=2000)


class GradeRequest(CamelModel):
    grades: list[GradeItem] = Field(min_length=1)


# ============================================================ STUDENT input
class AnswerIn(CamelModel):
    question_id: int
    # multiple_choice: [option ids]  | true_false: true/false | text/coding: string
    answer: Any = None


class SubmitRequest(CamelModel):
    answers: list[AnswerIn] = []


# ============================================================ OUTPUT
class OptionOut(CamelModel):
    id: int
    text: str
    is_correct: bool | None = None  # only sent to the teacher


class QuestionOut(CamelModel):
    id: int
    question_type: QuestionType
    text: str
    score: float
    position: int
    options: list[OptionOut] | None = None
    multiple_answers: bool | None = None  # multiple_choice: more than one correct option
    language: str | None = None
    starter_code: str | None = None
    # teacher only
    correct_answer: bool | None = None
    accepted_answers: list[str] | None = None
    case_sensitive: bool | None = None
    test_cases: list[TestCase] | None = None


class AssignmentOut(CamelModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    course_id: int
    module_id: int | None
    title: str
    description: str | None
    time_limit_minutes: int
    pass_percentage: float
    max_attempts: int
    visible: bool
    total_score: float
    question_count: int
    created_at: datetime


class AssignmentDetail(AssignmentOut):
    questions: list[QuestionOut]


class QuestionResult(CamelModel):
    question_id: int
    score: float
    max_score: float
    is_correct: bool | None = None  # None = waiting for teacher (coding)
    needs_review: bool = False
    feedback: str | None = None


class AttemptOut(CamelModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    assignment_id: int
    attempt_no: int
    status: str
    started_at: datetime
    deadline: datetime
    submitted_at: datetime | None
    score: float | None
    total_score: float | None
    percentage: float | None
    passed: bool | None
    timed_out: bool
    results: list[QuestionResult] | None = None


class AttemptWithStudent(AttemptOut):
    student: StudentBrief


class AttemptStartOut(CamelModel):
    attempt: AttemptOut
    assignment: AssignmentOut
    questions: list[QuestionOut]
    seconds_left: int


class AttemptReviewOut(AttemptWithStudent):
    """Teacher view of one attempt: includes what the student answered."""

    answers: dict | None = None