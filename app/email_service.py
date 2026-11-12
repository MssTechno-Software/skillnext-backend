import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger("skillnext.email")


def send_verification_email(to_email: str, first_name: str, token: str) -> None:
    # The set-password page is served by this FastAPI app itself
    link = f"{settings.APP_BASE_URL}/set-password?token={token}"

    if not settings.SMTP_HOST:
        # Dev mode: no SMTP configured, just log the link
        logger.warning("Set-password link for %s: %s", to_email, link)
        return

    msg = EmailMessage()
    msg["Subject"] = "Verify your email and set your password - Skill Next"
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to_email
    msg.set_content(
        f"Hi {first_name},\n\n"
        f"Welcome to Skill Next! Click the link below to verify your email "
        f"and set your password:\n\n{link}\n\n"
        f"This link expires in {settings.VERIFICATION_TOKEN_HOURS} hours.\n"
        f"If you didn't sign up, you can ignore this email."
    )

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as s:
            s.starttls()
            if settings.SMTP_USER:
                s.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            s.send_message(msg)
    except Exception:
        logger.exception("Failed to send email to %s", to_email)