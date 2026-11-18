# from datetime import datetime, timezone

# from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String
# from sqlalchemy.orm import Mapped, mapped_column, relationship

# from sqlalchemy import JSON, UniqueConstraint
# from sqlalchemy.dialects.postgresql import JSONB

# from app.database import Base
# from datetime import date, datetime, timezone

# from sqlalchemy import (
#     Boolean,
#     CheckConstraint,
#     Date,
#     DateTime,
#     ForeignKey,
#     Integer,
#     String,
#     Text,
# )

# def utcnow() -> datetime:
#     return datetime.now(timezone.utc)


# class User(Base):
#     __tablename__ = "users"
#     __table_args__ = (
#         CheckConstraint("role IN ('student','teacher')", name="ck_users_role"),
#     )

#     id: Mapped[int] = mapped_column(Integer, primary_key=True)
#     role: Mapped[str] = mapped_column(String(20), index=True)

#     first_name: Mapped[str] = mapped_column(String(100))
#     last_name: Mapped[str] = mapped_column(String(100))
#     email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
#     phone: Mapped[str] = mapped_column(String(25))

#     country: Mapped[str] = mapped_column(String(100))
#     country_code: Mapped[str] = mapped_column(String(3))
#     state: Mapped[str] = mapped_column(String(100))
#     state_code: Mapped[str] = mapped_column(String(10))
#     city: Mapped[str] = mapped_column(String(100))
#     institution: Mapped[str] = mapped_column(String(255))

#     password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
#     email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
#     created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

#     student_profile: Mapped["StudentProfile | None"] = relationship(
#         back_populates="user", uselist=False, cascade="all, delete-orphan"
#     )
#     teacher_profile: Mapped["TeacherProfile | None"] = relationship(
#         back_populates="user", uselist=False, cascade="all, delete-orphan"
#     )


# class StudentProfile(Base):
#     __tablename__ = "student_profiles"

#     id: Mapped[int] = mapped_column(Integer, primary_key=True)
#     user_id: Mapped[int] = mapped_column(
#         ForeignKey("users.id", ondelete="CASCADE"), unique=True
#     )
#     education_level: Mapped[str] = mapped_column(String(100))
#     course: Mapped[str] = mapped_column(String(150))
#     study_year: Mapped[str | None] = mapped_column(String(50), nullable=True)

#     user: Mapped[User] = relationship(back_populates="student_profile")


# class TeacherProfile(Base):
#     __tablename__ = "teacher_profiles"

#     id: Mapped[int] = mapped_column(Integer, primary_key=True)
#     user_id: Mapped[int] = mapped_column(
#         ForeignKey("users.id", ondelete="CASCADE"), unique=True
#     )
#     department: Mapped[str] = mapped_column(String(150))
#     subject: Mapped[str] = mapped_column(String(150))
#     designation: Mapped[str] = mapped_column(String(150))

#     user: Mapped[User] = relationship(back_populates="teacher_profile")


# class EmailVerificationToken(Base):
#     __tablename__ = "email_verification_tokens"

#     id: Mapped[int] = mapped_column(Integer, primary_key=True)
#     user_id: Mapped[int] = mapped_column(
#         ForeignKey("users.id", ondelete="CASCADE"), index=True
#     )
#     token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
#     expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
#     used: Mapped[bool] = mapped_column(Boolean, default=False)


# class Category(Base):
#     """Course category. Can be nested via parent_id (like Moodle)."""

#     __tablename__ = "categories"

#     id: Mapped[int] = mapped_column(Integer, primary_key=True)
#     name: Mapped[str] = mapped_column(String(255))
#     id_number: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
#     description: Mapped[str | None] = mapped_column(Text, nullable=True)
#     parent_id: Mapped[int | None] = mapped_column(
#         ForeignKey("categories.id", ondelete="RESTRICT"), nullable=True, index=True
#     )
#     visible: Mapped[bool] = mapped_column(Boolean, default=True)
#     sort_order: Mapped[int] = mapped_column(Integer, default=0)
#     created_by: Mapped[int | None] = mapped_column(
#         ForeignKey("users.id", ondelete="SET NULL"), nullable=True
#     )
#     created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

#     parent: Mapped["Category | None"] = relationship(
#         back_populates="children", remote_side=[id]
#     )
#     children: Mapped[list["Category"]] = relationship(back_populates="parent")
#     courses: Mapped[list["Course"]] = relationship(back_populates="category")


# class Course(Base):
#     __tablename__ = "courses"
#     __table_args__ = (
#         CheckConstraint(
#             "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
#             name="ck_courses_dates",
#         ),
#     )

#     id: Mapped[int] = mapped_column(Integer, primary_key=True)
#     category_id: Mapped[int] = mapped_column(
#         ForeignKey("categories.id", ondelete="RESTRICT"), index=True
#     )
#     full_name: Mapped[str] = mapped_column(String(255))
#     short_name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
#     id_number: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
#     summary: Mapped[str | None] = mapped_column(Text, nullable=True)
#     start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
#     end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
#     visible: Mapped[bool] = mapped_column(Boolean, default=True)
#     created_by: Mapped[int | None] = mapped_column(
#         ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
#     )
#     created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
#     updated_at: Mapped[datetime] = mapped_column(
#         DateTime(timezone=True), default=utcnow, onupdate=utcnow
#     )

#     category: Mapped[Category] = relationship(back_populates="courses")

#     modules: Mapped[list["CourseModule"]] = relationship(
#         back_populates="course",
#         cascade="all, delete-orphan",
#         order_by="CourseModule.position, CourseModule.id",
#     )
#     enrollments: Mapped[list["Enrollment"]] = relationship(
#         back_populates="course", cascade="all, delete-orphan"
#     )

# class CourseModule(Base):
#     """A module (Moodle 'section' / topic) inside a course, e.g. 'Week 1 - Basics'."""

#     __tablename__ = "course_modules"

#     id: Mapped[int] = mapped_column(Integer, primary_key=True)
#     course_id: Mapped[int] = mapped_column(
#         ForeignKey("courses.id", ondelete="CASCADE"), index=True
#     )
#     name: Mapped[str] = mapped_column(String(255))
#     summary: Mapped[str | None] = mapped_column(Text, nullable=True)
#     position: Mapped[int] = mapped_column(Integer, default=1)
#     visible: Mapped[bool] = mapped_column(Boolean, default=True)
#     created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

#     course: Mapped[Course] = relationship(back_populates="modules")
#     contents: Mapped[list["ModuleContent"]] = relationship(
#         back_populates="module",
#         cascade="all, delete-orphan",
#         order_by="ModuleContent.position, ModuleContent.id",
#     )


# class ModuleContent(Base):
#     """One item inside a module: text page, link, video URL or document URL."""

#     __tablename__ = "module_contents"
#     __table_args__ = (
#         CheckConstraint(
#             "content_type IN ('text','link','video','document')",
#             name="ck_module_contents_type",
#         ),
#     )

#     id: Mapped[int] = mapped_column(Integer, primary_key=True)
#     module_id: Mapped[int] = mapped_column(
#         ForeignKey("course_modules.id", ondelete="CASCADE"), index=True
#     )
#     title: Mapped[str] = mapped_column(String(255))
#     content_type: Mapped[str] = mapped_column(String(20))
#     body: Mapped[str | None] = mapped_column(Text, nullable=True)
#     # Structured text: ordered list of heading/subheading/paragraph/list blocks.
#     blocks: Mapped[list | None] = mapped_column(
#         JSON().with_variant(JSONB(), "postgresql"), nullable=True
#     )
#     url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
#     drive_file_id: Mapped[str | None] = mapped_column(String(100), nullable=True)  # set for Drive uploads
#     file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # original uploaded name
#     position: Mapped[int] = mapped_column(Integer, default=1)
#     visible: Mapped[bool] = mapped_column(Boolean, default=True)
#     created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
#     updated_at: Mapped[datetime] = mapped_column(
#         DateTime(timezone=True), default=utcnow, onupdate=utcnow
#     )

#     module: Mapped[CourseModule] = relationship(back_populates="contents")


# class Enrollment(Base):
#     """A student enrolled in a course (one row per student per course)."""

#     __tablename__ = "enrollments"
#     __table_args__ = (
#         UniqueConstraint("course_id", "student_id", name="uq_enrollments_course_student"),
#     )

#     id: Mapped[int] = mapped_column(Integer, primary_key=True)
#     course_id: Mapped[int] = mapped_column(
#         ForeignKey("courses.id", ondelete="CASCADE"), index=True
#     )
#     student_id: Mapped[int] = mapped_column(
#         ForeignKey("users.id", ondelete="CASCADE"), index=True
#     )
#     enrolled_by: Mapped[int | None] = mapped_column(
#         ForeignKey("users.id", ondelete="SET NULL"), nullable=True
#     )
#     enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

#     course: Mapped[Course] = relationship(back_populates="enrollments")
#     student: Mapped[User] = relationship(foreign_keys=[student_id])


# class ContentProgress(Base):
#     """One row = one student has finished one lesson/video/document."""

#     __tablename__ = "content_progress"
#     __table_args__ = (
#         UniqueConstraint("student_id", "content_id", name="uq_content_progress_student_content"),
#     )

#     id: Mapped[int] = mapped_column(Integer, primary_key=True)
#     student_id: Mapped[int] = mapped_column(
#         ForeignKey("users.id", ondelete="CASCADE"), index=True
#     )
#     content_id: Mapped[int] = mapped_column(
#         ForeignKey("module_contents.id", ondelete="CASCADE"), index=True
#     )
#     course_id: Mapped[int] = mapped_column(
#         ForeignKey("courses.id", ondelete="CASCADE"), index=True
#     )
#     completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sqlalchemy import JSON, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('student','teacher')", name="ck_users_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role: Mapped[str] = mapped_column(String(20), index=True)

    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    phone: Mapped[str] = mapped_column(String(25))

    country: Mapped[str] = mapped_column(String(100))
    country_code: Mapped[str] = mapped_column(String(3))
    state: Mapped[str] = mapped_column(String(100))
    state_code: Mapped[str] = mapped_column(String(10))
    city: Mapped[str] = mapped_column(String(100))
    institution: Mapped[str] = mapped_column(String(255))

    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    student_profile: Mapped["StudentProfile | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    teacher_profile: Mapped["TeacherProfile | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class StudentProfile(Base):
    __tablename__ = "student_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    education_level: Mapped[str] = mapped_column(String(100))
    course: Mapped[str] = mapped_column(String(150))
    study_year: Mapped[str | None] = mapped_column(String(50), nullable=True)

    user: Mapped[User] = relationship(back_populates="student_profile")


class TeacherProfile(Base):
    __tablename__ = "teacher_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    department: Mapped[str] = mapped_column(String(150))
    subject: Mapped[str] = mapped_column(String(150))
    designation: Mapped[str] = mapped_column(String(150))

    user: Mapped[User] = relationship(back_populates="teacher_profile")


class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class Category(Base):
    """Course category. Can be nested via parent_id (like Moodle)."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    id_number: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    visible: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    parent: Mapped["Category | None"] = relationship(
        back_populates="children", remote_side=[id]
    )
    children: Mapped[list["Category"]] = relationship(back_populates="parent")
    courses: Mapped[list["Course"]] = relationship(back_populates="category")


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="ck_courses_dates",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"), index=True
    )
    full_name: Mapped[str] = mapped_column(String(255))
    short_name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    id_number: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    visible: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    category: Mapped[Category] = relationship(back_populates="courses")

    modules: Mapped[list["CourseModule"]] = relationship(
        back_populates="course",
        cascade="all, delete-orphan",
        order_by="CourseModule.position, CourseModule.id",
    )
    enrollments: Mapped[list["Enrollment"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )

class CourseModule(Base):
    """A module (Moodle 'section' / topic) inside a course, e.g. 'Week 1 - Basics'."""

    __tablename__ = "course_modules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=1)
    visible: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    course: Mapped[Course] = relationship(back_populates="modules")
    contents: Mapped[list["ModuleContent"]] = relationship(
        back_populates="module",
        cascade="all, delete-orphan",
        order_by="ModuleContent.position, ModuleContent.id",
    )


class ModuleContent(Base):
    """One item inside a module: text page, link, video URL or document URL."""

    __tablename__ = "module_contents"
    __table_args__ = (
        CheckConstraint(
            "content_type IN ('text','link','video','document')",
            name="ck_module_contents_type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id: Mapped[int] = mapped_column(
        ForeignKey("course_modules.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(20))
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Structured text: ordered list of heading/subheading/paragraph/list blocks.
    blocks: Mapped[list | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    drive_file_id: Mapped[str | None] = mapped_column(String(100), nullable=True)  # set for Drive uploads
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # original uploaded name
    position: Mapped[int] = mapped_column(Integer, default=1)
    visible: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    module: Mapped[CourseModule] = relationship(back_populates="contents")


class Enrollment(Base):
    """A student enrolled in a course (one row per student per course)."""

    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint("course_id", "student_id", name="uq_enrollments_course_student"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    enrolled_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    course: Mapped[Course] = relationship(back_populates="enrollments")
    student: Mapped[User] = relationship(foreign_keys=[student_id])


class ContentProgress(Base):
    """One row = one student has finished one lesson/video/document."""

    __tablename__ = "content_progress"
    __table_args__ = (
        UniqueConstraint("student_id", "content_id", name="uq_content_progress_student_content"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    content_id: Mapped[int] = mapped_column(
        ForeignKey("module_contents.id", ondelete="CASCADE"), index=True
    )
    course_id: Mapped[int] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)