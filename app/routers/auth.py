import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.email_service import send_verification_email
from app.models import (
    EmailVerificationToken,
    StudentProfile,
    TeacherProfile,
    User,
)
from app.schemas import (
    ErrorResponse,
    LoginData,
    LoginRequest,
    LoginResponse,
    LoginUser,
    RegisterData,
    RegisterRequest,
    RegisterResponse,
    ResendVerificationRequest,
    SetPasswordRequest,
    StudentRegister,
    TeacherRegister,
)
from app.security import (
    DUMMY_HASH,
    create_access_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def _error(code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=code, content={"success": False, "message": message})


def _issue_token(db: Session, user: User) -> str:
    """Invalidate older unused tokens and create a fresh one. Returns the raw token."""
    db.execute(
        update(EmailVerificationToken)
        .where(
            EmailVerificationToken.user_id == user.id,
            EmailVerificationToken.used.is_(False),
        )
        .values(used=True)
    )
    raw_token = secrets.token_urlsafe(32)
    db.add(
        EmailVerificationToken(
            user_id=user.id,
            token_hash=_hash_token(raw_token),
            expires_at=datetime.now(timezone.utc)
            + timedelta(hours=settings.VERIFICATION_TOKEN_HOURS),
        )
    )
    return raw_token


def _is_expired(record: EmailVerificationToken) -> bool:
    exp = record.expires_at
    if exp.tzinfo is None:  # some DBs return naive datetimes
        exp = exp.replace(tzinfo=timezone.utc)
    return exp < datetime.now(timezone.utc)


def _get_valid_token(db: Session, token: str) -> EmailVerificationToken | None:
    record = db.scalar(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == _hash_token(token)
        )
    )
    if not record or record.used or _is_expired(record):
        return None
    return record


def _conflict() -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"success": False, "message": "Email already registered."},
    )


# ---------------------------------------------------------------- REGISTER
@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": ErrorResponse}},
)
def register(
    payload: RegisterRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    email = payload.email.lower()

    if db.scalar(select(User.id).where(User.email == email)):
        return _conflict()

    user = User(
        role=payload.role,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=email,
        phone=payload.phone,
        country=payload.country,
        country_code=payload.country_code,
        state=payload.state,
        state_code=payload.state_code,
        city=payload.city,
        institution=payload.institution,
        password_hash=None,  # user sets it via the emailed link
        email_verified=False,
    )

    if isinstance(payload, StudentRegister):
        user.student_profile = StudentProfile(
            education_level=payload.education_level,
            course=payload.course,
            study_year=payload.study_year,
        )
    elif isinstance(payload, TeacherRegister):
        user.teacher_profile = TeacherProfile(
            department=payload.department,
            subject=payload.subject,
            designation=payload.designation,
        )

    raw_token = secrets.token_urlsafe(32)

    try:
        db.add(user)
        db.flush()
        db.add(
            EmailVerificationToken(
                user_id=user.id,
                token_hash=_hash_token(raw_token),
                expires_at=datetime.now(timezone.utc)
                + timedelta(hours=settings.VERIFICATION_TOKEN_HOURS),
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        return _conflict()

    background.add_task(send_verification_email, email, user.first_name, raw_token)

    return RegisterResponse(
        message="Registration successful. Check your email to set your password.",
        data=RegisterData(
            user_id=user.id,
            email=user.email,
            role=user.role,
            email_verified=user.email_verified,
        ),
    )


# ------------------------------------------------------------ VERIFY EMAIL
@router.get("/verify-email")
def verify_email(token: str = Query(...), db: Session = Depends(get_db)):
    record = db.scalar(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == _hash_token(token)
        )
    )

    if (
        not record
        or record.used
        or _is_expired(record)
    ):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"success": False, "message": "Invalid or expired verification link."},
        )

    user = db.get(User, record.user_id)
    user.email_verified = True
    record.used = True
    db.commit()

    return {"success": True, "message": "Email verified successfully."}


# ------------------------------------------------------------ CHECK LINK
@router.get("/verify-token")
def verify_token(token: str = Query(...), db: Session = Depends(get_db)):
    record = _get_valid_token(db, token)
    if not record:
        return _error(400, "Invalid or expired link. Please request a new one.")

    user = db.get(User, record.user_id)
    return {
        "success": True,
        "message": "Token is valid.",
        "data": {"email": user.email, "firstName": user.first_name},
    }


# ------------------------------------------------------------ SET PASSWORD
@router.post("/set-password")
def set_password(payload: SetPasswordRequest, db: Session = Depends(get_db)):
    record = _get_valid_token(db, payload.token)
    if not record:
        return _error(400, "Invalid or expired link. Please request a new one.")

    user = db.get(User, record.user_id)
    user.password_hash = hash_password(payload.password)
    user.email_verified = True
    record.used = True
    db.commit()

    return {"success": True, "message": "Password set successfully. You can now log in."}


# ------------------------------------------------------------ RESEND LINK
@router.post("/resend-verification")
def resend_verification(
    payload: ResendVerificationRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.email == payload.email.lower()))

    # Only accounts that haven't set a password yet get a new link
    if user and user.password_hash is None:
        raw_token = _issue_token(db, user)
        db.commit()
        background.add_task(send_verification_email, user.email, user.first_name, raw_token)

    # Same response either way, so nobody can probe which emails exist
    return {
        "success": True,
        "message": "If this account is awaiting activation, a new link has been sent.",
    }


# ------------------------------------------------------------------- LOGIN
@router.post(
    "/login",
    response_model=LoginResponse,
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email.lower()))

    # Always run one bcrypt check so timing doesn't reveal if the email exists
    hashed = user.password_hash if user and user.password_hash else DUMMY_HASH
    password_ok = verify_password(payload.password, hashed)

    if not user or not user.password_hash or not password_ok:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"success": False, "message": "Invalid email or password."},
        )

    if not user.email_verified:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "success": False,
                "message": "Please verify your email before logging in.",
            },
        )

    token = create_access_token(user.id, user.role)

    return LoginResponse(
        message="Login successful.",
        data=LoginData(
            access_token=token,
            expires_in=settings.ACCESS_TOKEN_MINUTES * 60,
            user=LoginUser(
                user_id=user.id,
                email=user.email,
                role=user.role,
                first_name=user.first_name,
                last_name=user.last_name,
            ),
        ),
    )


# ---------------------------------------------------------------------- ME
@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return {
        "success": True,
        "data": {
            "userId": current_user.id,
            "email": current_user.email,
            "role": current_user.role,
            "firstName": current_user.first_name,
            "lastName": current_user.last_name,
            "emailVerified": current_user.email_verified,
        },
    }