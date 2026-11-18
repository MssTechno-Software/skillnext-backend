
# import re
# from typing import Annotated, Literal, Union
# import re
# from datetime import date, datetime
# from typing import Annotated, Generic, Literal, TypeVar, Union

# from pydantic import (
#     BaseModel,
#     ConfigDict,
#     EmailStr,
#     Field,
#     field_validator,
#     model_validator,
# )
# from pydantic.alias_generators import to_camel

# from app.content_blocks import Block

# PHONE_RE = re.compile(r"^\+?[0-9\s\-()]{7,20}$")


# class CamelModel(BaseModel):
#     model_config = ConfigDict(
#         alias_generator=to_camel,
#         populate_by_name=True,
#         str_strip_whitespace=True,
#     )


# # ---------- Registration ----------

# class RegisterBase(CamelModel):
#     first_name: str = Field(min_length=1, max_length=100)
#     last_name: str = Field(min_length=1, max_length=100)
#     email: EmailStr
#     phone: str
#     country: str = Field(min_length=1, max_length=100)
#     country_code: str = Field(min_length=2, max_length=3)
#     state: str = Field(min_length=1, max_length=100)
#     state_code: str = Field(min_length=1, max_length=10)
#     city: str = Field(min_length=1, max_length=100)
#     institution: str = Field(min_length=1, max_length=255)

#     @field_validator("phone")
#     @classmethod
#     def validate_phone(cls, v: str) -> str:
#         if not PHONE_RE.match(v):
#             raise ValueError("Invalid phone number format")
#         return v

#     @field_validator("country_code", "state_code")
#     @classmethod
#     def upper_codes(cls, v: str) -> str:
#         return v.upper()


# class StudentRegister(RegisterBase):
#     role: Literal["student"]
#     education_level: str = Field(min_length=1, max_length=100)
#     course: str = Field(min_length=1, max_length=150)
#     study_year: str | None = Field(default=None, max_length=50)


# class TeacherRegister(RegisterBase):
#     role: Literal["teacher"]
#     department: str = Field(min_length=1, max_length=150)
#     subject: str = Field(min_length=1, max_length=150)
#     designation: str = Field(min_length=1, max_length=150)


# RegisterRequest = Annotated[
#     Union[StudentRegister, TeacherRegister], Field(discriminator="role")
# ]


# class RegisterData(CamelModel):
#     user_id: int
#     email: str
#     role: str
#     email_verified: bool


# class RegisterResponse(CamelModel):
#     success: bool = True
#     message: str
#     data: RegisterData


# class ErrorResponse(CamelModel):
#     success: bool = False
#     message: str


# # ---------- Set password / resend ----------

# class SetPasswordRequest(CamelModel):
#     token: str = Field(min_length=10)
#     password: str = Field(min_length=8, max_length=72)
#     confirm_password: str

#     @field_validator("password")
#     @classmethod
#     def strong_password(cls, v: str) -> str:
#         if len(v.encode()) > 72:
#             raise ValueError("Password is too long")
#         if not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
#             raise ValueError("Password must contain at least one letter and one number")
#         return v

#     @model_validator(mode="after")
#     def passwords_match(self):
#         if self.password != self.confirm_password:
#             raise ValueError("Passwords do not match")
#         return self


# class ResendVerificationRequest(CamelModel):
#     email: EmailStr


# class LoginRequest(CamelModel):
#     email: EmailStr
#     password: str = Field(min_length=1, max_length=72)


# class LoginUser(CamelModel):
#     user_id: int
#     email: str
#     role: str
#     first_name: str
#     last_name: str


# class LoginData(CamelModel):
#     access_token: str
#     token_type: str = "bearer"
#     expires_in: int  # seconds
#     user: LoginUser


# class LoginResponse(CamelModel):
#     success: bool = True
#     message: str
#     data: LoginData

# # ====================== Generic envelope + Categories + Courses ======================

# T = TypeVar("T")


# def _blank_to_none(v):
#     if isinstance(v, str) and not v.strip():
#         return None
#     return v


# class ApiResponse(CamelModel, Generic[T]):
#     success: bool = True
#     message: str | None = None
#     data: T


# # ---------- Category ----------

# class CategoryCreate(CamelModel):
#     name: str = Field(min_length=1, max_length=255)
#     id_number: str | None = Field(default=None, max_length=100)
#     description: str | None = None
#     parent_id: int | None = None
#     visible: bool = True
#     sort_order: int = 0

#     _clean = field_validator("id_number", "description", mode="before")(_blank_to_none)


# class CategoryUpdate(CamelModel):
#     # Only fields that are sent get updated. Send parentId: null to move to top level.
#     name: str | None = Field(default=None, min_length=1, max_length=255)
#     id_number: str | None = Field(default=None, max_length=100)
#     description: str | None = None
#     parent_id: int | None = None
#     visible: bool | None = None
#     sort_order: int | None = None

#     _clean = field_validator("id_number", "description", mode="before")(_blank_to_none)


# class CategoryOut(CamelModel):
#     model_config = ConfigDict(from_attributes=True)

#     id: int
#     name: str
#     id_number: str | None
#     description: str | None
#     parent_id: int | None
#     visible: bool
#     sort_order: int
#     created_at: datetime


# class CategoryTreeNode(CategoryOut):
#     course_count: int = 0
#     children: list["CategoryTreeNode"] = []


# # ---------- Course ----------

# class CourseCreate(CamelModel):
#     category_id: int
#     full_name: str = Field(min_length=1, max_length=255)
#     short_name: str = Field(min_length=1, max_length=100)
#     id_number: str | None = Field(default=None, max_length=100)
#     summary: str | None = None
#     start_date: date | None = None
#     end_date: date | None = None
#     visible: bool = True

#     _clean = field_validator("id_number", "summary", mode="before")(_blank_to_none)

#     @model_validator(mode="after")
#     def dates_ok(self):
#         if self.start_date and self.end_date and self.end_date < self.start_date:
#             raise ValueError("End date cannot be before start date")
#         return self


# class CourseUpdate(CamelModel):
#     category_id: int | None = None
#     full_name: str | None = Field(default=None, min_length=1, max_length=255)
#     short_name: str | None = Field(default=None, min_length=1, max_length=100)
#     id_number: str | None = Field(default=None, max_length=100)
#     summary: str | None = None
#     start_date: date | None = None
#     end_date: date | None = None
#     visible: bool | None = None

#     _clean = field_validator("id_number", "summary", mode="before")(_blank_to_none)


# class CourseOut(CamelModel):
#     model_config = ConfigDict(from_attributes=True)

#     id: int
#     category_id: int
#     full_name: str
#     short_name: str
#     id_number: str | None
#     summary: str | None
#     start_date: date | None
#     end_date: date | None
#     visible: bool
#     created_by: int | None
#     created_at: datetime
#     updated_at: datetime


# class CourseListData(CamelModel):
#     items: list[CourseOut]
#     total: int
#     page: int
#     page_size: int


# # ====================== Course modules + content ======================

# ContentType = Literal["text", "link", "video", "document"]


# class ModuleCreate(CamelModel):
#     name: str = Field(min_length=1, max_length=255)
#     summary: str | None = None
#     position: int | None = Field(default=None, ge=1)  # omitted = added at the end
#     visible: bool = True

#     _clean = field_validator("summary", mode="before")(_blank_to_none)


# class ModuleUpdate(CamelModel):
#     name: str | None = Field(default=None, min_length=1, max_length=255)
#     summary: str | None = None
#     position: int | None = Field(default=None, ge=1)
#     visible: bool | None = None

#     _clean = field_validator("summary", mode="before")(_blank_to_none)


# class ModuleOut(CamelModel):
#     model_config = ConfigDict(from_attributes=True)

#     id: int
#     course_id: int
#     name: str
#     summary: str | None
#     position: int
#     visible: bool
#     created_at: datetime


# class ContentCreate(CamelModel):
#     title: str = Field(min_length=1, max_length=255)
#     content_type: ContentType
#     body: str | None = None  # text type: plain/markdown text, parsed into blocks
#     blocks: list[Block] | None = None  # text type: structured content (wins over body)
#     url: str | None = Field(default=None, max_length=1000)  # required for other types
#     position: int | None = Field(default=None, ge=1)
#     visible: bool = True

#     _clean = field_validator("body", "url", mode="before")(_blank_to_none)


# class ContentUpdate(CamelModel):
#     title: str | None = Field(default=None, min_length=1, max_length=255)
#     content_type: ContentType | None = None
#     body: str | None = None
#     blocks: list[Block] | None = None
#     url: str | None = Field(default=None, max_length=1000)
#     position: int | None = Field(default=None, ge=1)
#     visible: bool | None = None

#     _clean = field_validator("body", "url", mode="before")(_blank_to_none)


# class ContentOut(CamelModel):
#     model_config = ConfigDict(from_attributes=True)

#     id: int
#     module_id: int
#     title: str
#     content_type: str
#     body: str | None
#     blocks: list[Block] | None = None
#     url: str | None
#     file_name: str | None = None
#     download_url: str | None = None
#     preview_url: str | None = None
#     position: int
#     visible: bool
#     created_at: datetime
#     updated_at: datetime


# class ModuleWithContent(ModuleOut):
#     contents: list[ContentOut] = []


# class CourseContentData(CamelModel):
#     course: CourseOut
#     modules: list[ModuleWithContent]


# # ============================== Enrollment ==============================

# class EnrollRequest(CamelModel):
#     """Send student ids, emails, or both (max 200 in total)."""

#     student_ids: list[int] = Field(default_factory=list, max_length=200)
#     emails: list[EmailStr] = Field(default_factory=list, max_length=200)

#     @model_validator(mode="after")
#     def at_least_one(self):
#         if not self.student_ids and not self.emails:
#             raise ValueError("Send at least one studentId or email")
#         return self


# class StudentBrief(CamelModel):
#     model_config = ConfigDict(from_attributes=True)

#     id: int
#     first_name: str
#     last_name: str
#     email: str
#     institution: str


# class EnrollmentOut(CamelModel):
#     model_config = ConfigDict(from_attributes=True)

#     id: int
#     course_id: int
#     student: StudentBrief
#     enrolled_at: datetime


# class EnrollSkipped(CamelModel):
#     student_id: int | None = None
#     email: str | None = None
#     reason: str


# class EnrollResult(CamelModel):
#     enrolled: list[EnrollmentOut]
#     already_enrolled: list[StudentBrief]
#     skipped: list[EnrollSkipped]


# class EnrollmentListData(CamelModel):
#     items: list[EnrollmentOut]
#     total: int
#     page: int
#     page_size: int


# class StudentListData(CamelModel):
#     items: list[StudentBrief]
#     total: int
#     page: int
#     page_size: int


# class MyCourseOut(CamelModel):
#     model_config = ConfigDict(from_attributes=True)

#     course: CourseOut
#     enrolled_at: datetime


# from pydantic import BaseModel, ConfigDict


# class ContentSummary(BaseModel):
#     model_config = ConfigDict(from_attributes=True)

#     id: int
#     title: str
#     content_type: str
#     position: int | None = None
#     visible: bool = True
#     url: str | None = None
#     download_url: str | None = None
#     preview_url: str | None = None


# class ModuleOverview(ModuleOut):
#     total_contents: int = 0
#     contents_by_type: dict[str, int] = {}
#     contents: list[ContentSummary] = []


# class CourseOverviewData(BaseModel):
#     course: CourseOut
#     total_modules: int
#     total_contents: int
#     contents_by_type: dict[str, int]
#     hidden_modules: int | None = None   # teacher only
#     hidden_contents: int | None = None  # teacher only
#     modules: list[ModuleOverview]


import re
from typing import Annotated, Literal, Union
import re
from datetime import date, datetime
from typing import Annotated, Generic, Literal, TypeVar, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

from app.content_blocks import Block

PHONE_RE = re.compile(r"^\+?[0-9\s\-()]{7,20}$")


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        str_strip_whitespace=True,
    )


# ---------- Registration ----------

class RegisterBase(CamelModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    phone: str
    country: str = Field(min_length=1, max_length=100)
    country_code: str = Field(min_length=2, max_length=3)
    state: str = Field(min_length=1, max_length=100)
    state_code: str = Field(min_length=1, max_length=10)
    city: str = Field(min_length=1, max_length=100)
    institution: str = Field(min_length=1, max_length=255)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        if not PHONE_RE.match(v):
            raise ValueError("Invalid phone number format")
        return v

    @field_validator("country_code", "state_code")
    @classmethod
    def upper_codes(cls, v: str) -> str:
        return v.upper()


class StudentRegister(RegisterBase):
    role: Literal["student"]
    education_level: str = Field(min_length=1, max_length=100)
    course: str = Field(min_length=1, max_length=150)
    study_year: str | None = Field(default=None, max_length=50)


class TeacherRegister(RegisterBase):
    role: Literal["teacher"]
    department: str = Field(min_length=1, max_length=150)
    subject: str = Field(min_length=1, max_length=150)
    designation: str = Field(min_length=1, max_length=150)


RegisterRequest = Annotated[
    Union[StudentRegister, TeacherRegister], Field(discriminator="role")
]


class RegisterData(CamelModel):
    user_id: int
    email: str
    role: str
    email_verified: bool


class RegisterResponse(CamelModel):
    success: bool = True
    message: str
    data: RegisterData


class ErrorResponse(CamelModel):
    success: bool = False
    message: str


# ---------- Set password / resend ----------

class SetPasswordRequest(CamelModel):
    token: str = Field(min_length=10)
    password: str = Field(min_length=8, max_length=72)
    confirm_password: str

    @field_validator("password")
    @classmethod
    def strong_password(cls, v: str) -> str:
        if len(v.encode()) > 72:
            raise ValueError("Password is too long")
        if not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
            raise ValueError("Password must contain at least one letter and one number")
        return v

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self


class ResendVerificationRequest(CamelModel):
    email: EmailStr


class ForgotPasswordRequest(CamelModel):
    email: EmailStr


class ResetPasswordRequest(CamelModel):
    token: str = Field(min_length=10)
    password: str = Field(min_length=8, max_length=72)
    confirm_password: str

    @field_validator("password")
    @classmethod
    def strong_password(cls, v: str) -> str:
        if len(v.encode()) > 72:
            raise ValueError("Password is too long")
        if not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
            raise ValueError("Password must contain at least one letter and one number")
        return v

    @model_validator(mode="after")
    def passwords_match(self):
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match")
        return self


class LoginRequest(CamelModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class LoginUser(CamelModel):
    user_id: int
    email: str
    role: str
    first_name: str
    last_name: str


class LoginData(CamelModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    user: LoginUser


class LoginResponse(CamelModel):
    success: bool = True
    message: str
    data: LoginData

# ====================== Generic envelope + Categories + Courses ======================

T = TypeVar("T")


def _blank_to_none(v):
    if isinstance(v, str) and not v.strip():
        return None
    return v


class ApiResponse(CamelModel, Generic[T]):
    success: bool = True
    message: str | None = None
    data: T


# ---------- Category ----------

class CategoryCreate(CamelModel):
    name: str = Field(min_length=1, max_length=255)
    id_number: str | None = Field(default=None, max_length=100)
    description: str | None = None
    parent_id: int | None = None
    visible: bool = True
    sort_order: int = 0

    _clean = field_validator("id_number", "description", mode="before")(_blank_to_none)


class CategoryUpdate(CamelModel):
    # Only fields that are sent get updated. Send parentId: null to move to top level.
    name: str | None = Field(default=None, min_length=1, max_length=255)
    id_number: str | None = Field(default=None, max_length=100)
    description: str | None = None
    parent_id: int | None = None
    visible: bool | None = None
    sort_order: int | None = None

    _clean = field_validator("id_number", "description", mode="before")(_blank_to_none)


class CategoryOut(CamelModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    id_number: str | None
    description: str | None
    parent_id: int | None
    visible: bool
    sort_order: int
    created_at: datetime


class CategoryTreeNode(CategoryOut):
    course_count: int = 0
    children: list["CategoryTreeNode"] = []


# ---------- Course ----------

class CourseCreate(CamelModel):
    category_id: int
    full_name: str = Field(min_length=1, max_length=255)
    short_name: str = Field(min_length=1, max_length=100)
    id_number: str | None = Field(default=None, max_length=100)
    summary: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    visible: bool = True

    _clean = field_validator("id_number", "summary", mode="before")(_blank_to_none)

    @model_validator(mode="after")
    def dates_ok(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("End date cannot be before start date")
        return self


class CourseUpdate(CamelModel):
    category_id: int | None = None
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    short_name: str | None = Field(default=None, min_length=1, max_length=100)
    id_number: str | None = Field(default=None, max_length=100)
    summary: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    visible: bool | None = None

    _clean = field_validator("id_number", "summary", mode="before")(_blank_to_none)


class CourseOut(CamelModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category_id: int
    full_name: str
    short_name: str
    id_number: str | None
    summary: str | None
    start_date: date | None
    end_date: date | None
    visible: bool
    created_by: int | None
    created_at: datetime
    updated_at: datetime


class CourseListData(CamelModel):
    items: list[CourseOut]
    total: int
    page: int
    page_size: int


# ====================== Course modules + content ======================

ContentType = Literal["text", "link", "video", "document"]


class ModuleCreate(CamelModel):
    name: str = Field(min_length=1, max_length=255)
    summary: str | None = None
    position: int | None = Field(default=None, ge=1)  # omitted = added at the end
    visible: bool = True

    _clean = field_validator("summary", mode="before")(_blank_to_none)


class ModuleUpdate(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    summary: str | None = None
    position: int | None = Field(default=None, ge=1)
    visible: bool | None = None

    _clean = field_validator("summary", mode="before")(_blank_to_none)


class ModuleOut(CamelModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    course_id: int
    name: str
    summary: str | None
    position: int
    visible: bool
    created_at: datetime


class ContentCreate(CamelModel):
    title: str = Field(min_length=1, max_length=255)
    content_type: ContentType
    body: str | None = None  # text type: plain/markdown text, parsed into blocks
    blocks: list[Block] | None = None  # text type: structured content (wins over body)
    url: str | None = Field(default=None, max_length=1000)  # required for other types
    position: int | None = Field(default=None, ge=1)
    visible: bool = True

    _clean = field_validator("body", "url", mode="before")(_blank_to_none)


class ContentUpdate(CamelModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content_type: ContentType | None = None
    body: str | None = None
    blocks: list[Block] | None = None
    url: str | None = Field(default=None, max_length=1000)
    position: int | None = Field(default=None, ge=1)
    visible: bool | None = None

    _clean = field_validator("body", "url", mode="before")(_blank_to_none)


class ContentOut(CamelModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    module_id: int
    title: str
    content_type: str
    body: str | None
    blocks: list[Block] | None = None
    url: str | None
    file_name: str | None = None
    download_url: str | None = None
    preview_url: str | None = None
    position: int
    visible: bool
    created_at: datetime
    updated_at: datetime


class ModuleWithContent(ModuleOut):
    contents: list[ContentOut] = []


class CourseContentData(CamelModel):
    course: CourseOut
    modules: list[ModuleWithContent]


# ============================== Enrollment ==============================

class EnrollRequest(CamelModel):
    """Send student ids, emails, or both (max 200 in total)."""

    student_ids: list[int] = Field(default_factory=list, max_length=200)
    emails: list[EmailStr] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def at_least_one(self):
        if not self.student_ids and not self.emails:
            raise ValueError("Send at least one studentId or email")
        return self


class StudentBrief(CamelModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    first_name: str
    last_name: str
    email: str
    institution: str


class EnrollmentOut(CamelModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    course_id: int
    student: StudentBrief
    enrolled_at: datetime


class EnrollSkipped(CamelModel):
    student_id: int | None = None
    email: str | None = None
    reason: str


class EnrollResult(CamelModel):
    enrolled: list[EnrollmentOut]
    already_enrolled: list[StudentBrief]
    skipped: list[EnrollSkipped]


class EnrollmentListData(CamelModel):
    items: list[EnrollmentOut]
    total: int
    page: int
    page_size: int


class StudentListData(CamelModel):
    items: list[StudentBrief]
    total: int
    page: int
    page_size: int


class MyCourseOut(CamelModel):
    model_config = ConfigDict(from_attributes=True)

    course: CourseOut
    enrolled_at: datetime


from pydantic import BaseModel, ConfigDict


class ContentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    content_type: str
    position: int | None = None
    visible: bool = True
    url: str | None = None
    download_url: str | None = None
    preview_url: str | None = None


class ModuleOverview(ModuleOut):
    total_contents: int = 0
    contents_by_type: dict[str, int] = {}
    contents: list[ContentSummary] = []


class CourseOverviewData(BaseModel):
    course: CourseOut
    total_modules: int
    total_contents: int
    contents_by_type: dict[str, int]
    hidden_modules: int | None = None   # teacher only
    hidden_contents: int | None = None  # teacher only
    modules: list[ModuleOverview]