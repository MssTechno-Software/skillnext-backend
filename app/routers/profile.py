from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas import PHONE_RE, CamelModel

router = APIRouter(prefix="/api/profile", tags=["profile"])

STUDENT_FIELDS = {"education_level", "course", "study_year"}
TEACHER_FIELDS = {"department", "subject", "designation"}


def _error(code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=code, content={"success": False, "message": message})


class ProfileUpdate(CamelModel):
    """All fields optional (partial update). Email and role are intentionally absent,
    so they can never be changed through this endpoint (an 'email' key is ignored)."""

    # common
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = None
    country: str | None = Field(default=None, min_length=1, max_length=100)
    country_code: str | None = Field(default=None, min_length=2, max_length=3)
    state: str | None = Field(default=None, min_length=1, max_length=100)
    state_code: str | None = Field(default=None, min_length=1, max_length=10)
    city: str | None = Field(default=None, min_length=1, max_length=100)
    institution: str | None = Field(default=None, min_length=1, max_length=255)

    # student only
    education_level: str | None = Field(default=None, min_length=1, max_length=100)
    course: str | None = Field(default=None, min_length=1, max_length=150)
    study_year: str | None = Field(default=None, max_length=50)  # nullable column

    # teacher only
    department: str | None = Field(default=None, min_length=1, max_length=150)
    subject: str | None = Field(default=None, min_length=1, max_length=150)
    designation: str | None = Field(default=None, min_length=1, max_length=150)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v):
        if v is not None and not PHONE_RE.match(v):
            raise ValueError("Invalid phone number format")
        return v

    @field_validator("country_code", "state_code")
    @classmethod
    def upper_codes(cls, v):
        return v.upper() if v else v


def _serialize(user: User) -> dict:
    data = {
        "userId": user.id,
        "role": user.role,
        "firstName": user.first_name,
        "lastName": user.last_name,
        "email": user.email,
        "phone": user.phone,
        "country": user.country,
        "countryCode": user.country_code,
        "state": user.state,
        "stateCode": user.state_code,
        "city": user.city,
        "institution": user.institution,
        "emailVerified": user.email_verified,
        "createdAt": user.created_at.isoformat() if user.created_at else None,
    }
    sp, tp = user.student_profile, user.teacher_profile
    if user.role == "student" and sp:
        data.update(
            {
                "educationLevel": sp.education_level,
                "course": sp.course,
                "studyYear": sp.study_year,
            }
        )
    elif user.role == "teacher" and tp:
        data.update(
            {
                "department": tp.department,
                "subject": tp.subject,
                "designation": tp.designation,
            }
        )
    return data


# ---------------------------------------------------------------- GET
@router.get("")
def get_profile(current_user: User = Depends(get_current_user)):
    return {"success": True, "data": _serialize(current_user)}


# ---------------------------------------------------------------- UPDATE
@router.put("")
def update_profile(
    payload: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    changes = payload.model_dump(exclude_unset=True)

    # Explicit null is only allowed for study_year
    for key, value in changes.items():
        if value is None and key != "study_year":
            return _error(422, f"{key} cannot be null.")

    allowed_role_fields = STUDENT_FIELDS if current_user.role == "student" else TEACHER_FIELDS
    other_role_fields = TEACHER_FIELDS if current_user.role == "student" else STUDENT_FIELDS
    bad = sorted(set(changes) & other_role_fields)
    if bad:
        return _error(400, f"Fields not applicable to a {current_user.role}: {', '.join(bad)}")

    profile = (
        current_user.student_profile
        if current_user.role == "student"
        else current_user.teacher_profile
    )

    for key, value in changes.items():
        if key in allowed_role_fields:
            if profile is None:
                return _error(404, "Profile record not found for this user.")
            setattr(profile, key, value)
        else:
            setattr(current_user, key, value)

    db.commit()
    db.refresh(current_user)
    return {
        "success": True,
        "message": "Profile updated successfully.",
        "data": _serialize(current_user),
    }