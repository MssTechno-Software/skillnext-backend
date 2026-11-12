# Skill Next LMS – Signup API

FastAPI + SQLAlchemy 2.0 + PostgreSQL. Single endpoint `POST /api/auth/register`
for `student` and `teacher` roles, plus email verification.

## Setup

```bash
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then edit DATABASE_URL
createdb skillnext          # or create the DB in Neon
uvicorn app.main:app --reload
```

Docs: http://localhost:8000/docs

## Endpoints

- `POST /api/auth/register` – 201 on success, 409 if email exists, 422 on validation errors
- `GET  /api/auth/verify-email?token=...` – verifies the email
- `GET  /health`

## Notes

- Tables are created on startup via `create_all` (dev only). Use Alembic for production.
- `password` is optional in the payload; if sent it is hashed with bcrypt.
- With `SMTP_HOST` empty, the verification link is logged to the console.
