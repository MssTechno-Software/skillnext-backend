# import logging
# import smtplib
# from email.message import EmailMessage

# from app.config import settings

# logger = logging.getLogger("skillnext.email")


# def send_verification_email(to_email: str, first_name: str, token: str) -> None:
#     # The set-password page is served by this FastAPI app itself
#     link = f"{settings.APP_BASE_URL}/set-password?token={token}"

#     if not settings.SMTP_HOST:
#         # Dev mode: no SMTP configured, just log the link
#         logger.warning("Set-password link for %s: %s", to_email, link)
#         return

#     msg = EmailMessage()
#     msg["Subject"] = "Verify your email and set your password - Skill Next"
#     msg["From"] = settings.SMTP_FROM
#     msg["To"] = to_email
#     msg.set_content(
#         f"Hi {first_name},\n\n"
#         f"Welcome to Skill Next! Click the link below to verify your email "
#         f"and set your password:\n\n{link}\n\n"
#         f"This link expires in {settings.VERIFICATION_TOKEN_HOURS} hours.\n"
#         f"If you didn't sign up, you can ignore this email."
#     )

#     try:
#         with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as s:
#             s.starttls()
#             if settings.SMTP_USER:
#                 s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
#             s.send_message(msg)
#     except Exception:
#         logger.exception("Failed to send email to %s", to_email)

import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger("skillnext.email")


def _frontend_link(path: str, token: str) -> str:
    base = settings.FRONTEND_URL.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}?token={token}"


def _send(to_email: str, msg: EmailMessage) -> None:
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as s:
            s.starttls()
            if settings.SMTP_USER:
                s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            s.send_message(msg)
    except Exception:
        logger.exception("Failed to send email to %s", to_email)


# def send_verification_email(to_email: str, first_name: str, token: str) -> None:
#     # The set-password page now lives on the front end
#     link = _frontend_link(settings.FRONTEND_SET_PASSWORD_PATH, token)

#     if not settings.SMTP_HOST:
#         # Dev mode: no SMTP configured, just log the link
#         logger.warning("Set-password link for %s: %s", to_email, link)
#         return

#     msg = EmailMessage()
#     msg["Subject"] = "Verify your email and set your password - Skill Next"
#     msg["From"] = settings.SMTP_FROM
#     msg["To"] = to_email
#     msg.set_content(
#         f"Hi {first_name},\n\n"
#         f"Welcome to Skill Next! Click the link below to verify your email "
#         f"and set your password:\n\n{link}\n\n"
#         f"This link expires in {settings.VERIFICATION_TOKEN_HOURS} hours.\n"
#         f"If you didn't sign up, you can ignore this email."
#     )
#     _send(to_email, msg)

def send_verification_email(to_email, first_name, token, forgot: bool = False):
    link = f"{settings.FRONTEND_URL.rstrip('/')}{settings.FRONTEND_SET_PASSWORD_PATH}?token={token}"

    subject = (
        "Reset your password - Skill Next" if forgot
        else "Verify your email and set your password - Skill Next"
    )
    intro = (
        "We received a request to reset your password. Click the link below to choose a new one:"
        if forgot
        else "Welcome to Skill Next! Click the link below to verify your email and set your password:"
    )

    if not settings.SMTP_HOST:
        logger.warning("Set-password link for %s: %s", to_email, link)
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to_email
    msg.set_content(
        f"Hi {first_name},\n\n{intro}\n\n{link}\n\n"
        f"This link expires in {settings.VERIFICATION_TOKEN_HOURS} hours.\n"
        f"If you didn't request this, you can ignore this email."
    )
    _send(to_email, msg)
    
def send_password_reset_email(to_email: str, first_name: str, token: str) -> None:
    link = _frontend_link(settings.FRONTEND_RESET_PASSWORD_PATH, token)

    if not settings.SMTP_HOST:
        logger.warning("Reset-password link for %s: %s", to_email, link)
        return

    msg = EmailMessage()
    msg["Subject"] = "Reset your password - Skill Next"
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to_email
    msg.set_content(
        f"Hi {first_name},\n\n"
        f"We received a request to reset your Skill Next password. "
        f"Click the link below to choose a new one:\n\n{link}\n\n"
        f"This link expires in {settings.RESET_TOKEN_MINUTES} minutes and can be used once.\n"
        f"If you didn't request this, you can safely ignore this email."
    )
    _send(to_email, msg)