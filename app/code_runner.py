"""Run a code example for the "Try it yourself" editor.

* html / css / javascript run in the learner's browser (sandboxed iframe) -
  the frontend never calls this endpoint for them.
* python / java / c / cpp / csharp / sql / php / go / ruby are executed by a
  separate sandbox (Judge0). Never run learner code inside this API process.
"""
import base64
import time
from collections import defaultdict, deque

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import Field, field_validator

from app.config import settings
from app.content_blocks import CODE_LANGUAGES, MAX_CODE, normalize_language
from app.dependencies import get_current_user
from app.models import User
from app.schemas import ApiResponse, CamelModel

router = APIRouter(prefix="/api/code", tags=["code-runner"])

BROWSER_ONLY = {"html", "css", "javascript"}

# Judge0 CE language ids - confirm against GET {CODE_RUNNER_URL}/languages
JUDGE0_IDS = {
    "python": 71, "java": 62, "c": 50, "cpp": 54, "csharp": 51,
    "sql": 82, "php": 68, "go": 60, "ruby": 72,
}


class RunRequest(CamelModel):
    language: str
    code: str = Field(min_length=1, max_length=MAX_CODE)
    stdin: str = Field(default="", max_length=2000)

    @field_validator("language")
    @classmethod
    def _lang(cls, v: str) -> str:
        v = normalize_language(v)
        if v not in CODE_LANGUAGES:
            raise ValueError("Unsupported language.")
        return v


class RunResult(CamelModel):
    status: str
    stdout: str = ""
    stderr: str = ""
    compile_output: str = ""
    time: str | None = None
    memory_kb: int | None = None
    success: bool


_hits: dict[int, deque] = defaultdict(deque)


def _rate_limit(user_id: int) -> None:
    """Per-user, per-process limit. Use Redis/Cloud Armor if you run many instances."""
    now, q = time.monotonic(), _hits[user_id]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= settings.CODE_RUN_LIMIT_PER_MIN:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many runs. Wait a minute and try again.")
    q.append(now)


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _unb64(s: str | None) -> str:
    return base64.b64decode(s).decode(errors="replace") if s else ""


@router.post("/run", response_model=ApiResponse[RunResult])
def run_code(payload: RunRequest, user: User = Depends(get_current_user)):
    if payload.language in BROWSER_ONLY:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{payload.language} runs in the browser. Use the sandboxed preview.",
        )
    if not settings.CODE_RUNNER_URL:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Code runner is not configured.")
    _rate_limit(user.id)

    headers = {"Content-Type": "application/json"}
    if settings.CODE_RUNNER_API_KEY:
        headers[settings.CODE_RUNNER_AUTH_HEADER] = settings.CODE_RUNNER_API_KEY

    body = {
        "language_id": JUDGE0_IDS[payload.language],
        "source_code": _b64(payload.code),
        "stdin": _b64(payload.stdin),
        "cpu_time_limit": settings.CODE_RUN_TIMEOUT_SECONDS,
        "memory_limit": 128000,
    }
    try:
        r = httpx.post(
            f"{settings.CODE_RUNNER_URL.rstrip('/')}/submissions",
            params={"base64_encoded": "true", "wait": "true"},
            json=body,
            headers=headers,
            timeout=settings.CODE_RUN_TIMEOUT_SECONDS + 10,
        )
        r.raise_for_status()
        d = r.json()
    except httpx.HTTPError:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Code runner is unavailable. Try again shortly.")

    st = (d.get("status") or {})
    return ApiResponse(
        data=RunResult(
            status=st.get("description", "Unknown"),
            stdout=_unb64(d.get("stdout")),
            stderr=_unb64(d.get("stderr")),
            compile_output=_unb64(d.get("compile_output")),
            time=d.get("time"),
            memory_kb=d.get("memory"),
            success=st.get("id") == 3,  # 3 = Accepted
        )
    )