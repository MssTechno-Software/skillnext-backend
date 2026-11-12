import re
from typing import Annotated, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

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