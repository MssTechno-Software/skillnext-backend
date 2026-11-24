"""POST /api/translation/translate - English -> French text helper (Google Cloud Translation v3).

Stateless: it only translates the strings it receives. No existing API, stored content or
auth logic is touched. Google credentials stay on the server (Application Default
Credentials = the Cloud Run service account).
"""

import hashlib
import logging
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import Field, field_validator, model_validator
from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.config import settings
from app.database import Base, get_db
from app.dependencies import get_current_user
from app.models import User, utcnow
from app.schemas import CamelModel

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/translation", tags=["translation"])

# ------------------------------------------------------------------ limits
SOURCE_LANGUAGES = {"en"}
TARGET_LANGUAGES = {"fr"}  # add more codes here later
MIME_TYPES = {"text/plain", "text/html"}
MAX_TEXTS = 200  # strings per request
MAX_CHARS_PER_TEXT = 5000
MAX_TOTAL_CHARS = 30000  # all strings of one request together
GOOGLE_BATCH_TEXTS = 100  # strings per Google call
GOOGLE_BATCH_CHARS = 20000  # characters per Google call
RATE_LIMIT_REQUESTS = 60  # per user ...
RATE_LIMIT_WINDOW_SECONDS = 60  # ... per minute (per Cloud Run instance)


# ------------------------------------------------------------------ cache table
class TextTranslation(Base):
    """One translated string. Key = hash of (mime type, source, target, original text)."""

    __tablename__ = "text_translations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    translated_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow)


# ------------------------------------------------------------------ schemas
class TranslateRequest(CamelModel):
    texts: list[str] = Field(min_length=1, max_length=MAX_TEXTS)
    source_language: str = "en"
    target_language: str = "fr"
    mime_type: str = "text/plain"

    @field_validator("source_language")
    @classmethod
    def _source(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in SOURCE_LANGUAGES:
            raise ValueError(f"sourceLanguage must be one of: {', '.join(sorted(SOURCE_LANGUAGES))}")
        return v

    @field_validator("target_language")
    @classmethod
    def _target(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in TARGET_LANGUAGES:
            raise ValueError(f"targetLanguage must be one of: {', '.join(sorted(TARGET_LANGUAGES))}")
        return v

    @field_validator("mime_type")
    @classmethod
    def _mime(cls, v: str) -> str:
        if v not in MIME_TYPES:
            raise ValueError(f"mimeType must be one of: {', '.join(sorted(MIME_TYPES))}")
        return v

    @model_validator(mode="after")
    def _sizes(self):
        for i, t in enumerate(self.texts):
            if len(t) > MAX_CHARS_PER_TEXT:
                raise ValueError(f"texts[{i}] is longer than {MAX_CHARS_PER_TEXT} characters")
        if sum(len(t) for t in self.texts) > MAX_TOTAL_CHARS:
            raise ValueError(f"texts together are longer than {MAX_TOTAL_CHARS} characters")
        return self


class TranslatedText(CamelModel):
    translated_text: str


class TranslateResponse(CamelModel):
    translations: list[TranslatedText]


# ------------------------------------------------------------------ rate limit (per user)
_hits: dict[int, deque] = defaultdict(deque)
_hits_lock = threading.Lock()


def _rate_limit(user_id: int) -> None:
    now = time.monotonic()
    with _hits_lock:
        q = _hits[user_id]
        while q and now - q[0] > RATE_LIMIT_WINDOW_SECONDS:
            q.popleft()
        if len(q) >= RATE_LIMIT_REQUESTS:
            retry = int(RATE_LIMIT_WINDOW_SECONDS - (now - q[0])) + 1
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many translation requests. Please wait a moment.",
                headers={"Retry-After": str(retry)},
            )
        q.append(now)


# ------------------------------------------------------------------ Google Cloud Translation v3
_client = None
_project_id: str | None = None
_google_lock = threading.Lock()


def _client_and_parent() -> tuple[object, str]:
    """Created on first use (imports are lazy, so a missing package never breaks the other APIs)."""
    global _client, _project_id
    with _google_lock:
        if _client is None:
            from google.cloud import translate_v3

            _client = translate_v3.TranslationServiceClient()  # Application Default Credentials
        if _project_id is None:
            project = settings.GOOGLE_CLOUD_PROJECT
            if not project:
                import google.auth

                _, project = google.auth.default()
            if not project:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE, "Translation is not configured."
                )
            _project_id = project
    return _client, f"projects/{_project_id}/locations/global"


def _translate_google(texts: list[str], source: str, target: str, mime: str) -> list[str]:
    """Translate `texts` (same order back). Splits into Google-sized batches."""
    from google.api_core import exceptions as gexc
    from google.auth.exceptions import DefaultCredentialsError

    batches, cur, size = [], [], 0
    for t in texts:
        if cur and (len(cur) >= GOOGLE_BATCH_TEXTS or size + len(t) > GOOGLE_BATCH_CHARS):
            batches.append(cur)
            cur, size = [], 0
        cur.append(t)
        size += len(t)
    if cur:
        batches.append(cur)

    out: list[str] = []
    try:
        client, parent = _client_and_parent()
        for batch in batches:
            resp = client.translate_text(
                request={
                    "parent": parent,
                    "contents": batch,
                    "mime_type": mime,
                    "source_language_code": source,
                    "target_language_code": target,
                },
                timeout=20,
            )
            out.extend(t.translated_text for t in resp.translations)
    except HTTPException:
        raise
    except gexc.InvalidArgument:
        log.warning("Translation rejected the request", exc_info=True)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The text could not be translated.")
    except gexc.ResourceExhausted:
        log.error("Translation quota exceeded", exc_info=True)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Translation limit reached. Try again later."
        )
    except (gexc.PermissionDenied, gexc.Unauthenticated, DefaultCredentialsError):
        log.error("Translation credentials/permissions problem", exc_info=True)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Translation service is not available."
        )
    except (gexc.DeadlineExceeded, gexc.ServiceUnavailable):
        log.error("Translation service timed out / unavailable", exc_info=True)
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT, "Translation service timed out. Try again."
        )
    except gexc.GoogleAPICallError:
        log.error("Translation API error", exc_info=True)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Translation service failed.")
    except Exception:
        log.exception("Unexpected translation error")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Translation service failed.")

    if len(out) != len(texts):  # should never happen; never return misaligned text
        log.error("Translation count mismatch: %s != %s", len(out), len(texts))
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Translation service failed.")
    return out


def _cache_key(text: str, source: str, target: str, mime: str) -> str:
    return hashlib.sha256(f"{mime}\x1f{source}\x1f{target}\x1f{text}".encode()).hexdigest()


# ------------------------------------------------------------------ endpoint
@router.post("/translate", response_model=TranslateResponse)
def translate(
    payload: TranslateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),  # any logged-in user (student / teacher)
):
    """Translate a batch of strings. Result i is the translation of texts[i]."""
    _rate_limit(user.id)

    src, tgt, mime = payload.source_language, payload.target_language, payload.mime_type
    keys = [_cache_key(t, src, tgt, mime) if t.strip() else None for t in payload.texts]
    unique = {k: t for k, t in zip(keys, payload.texts) if k is not None}  # repeated texts once

    done: dict[str, str] = {}
    if unique:
        done = {
            r.cache_key: r.translated_text
            for r in db.scalars(
                select(TextTranslation).where(TextTranslation.cache_key.in_(list(unique)))
            )
        }
        missing = [k for k in unique if k not in done]
        if missing:
            fresh = dict(zip(missing, _translate_google([unique[k] for k in missing], src, tgt, mime)))
            done.update(fresh)
            try:
                db.add_all(
                    TextTranslation(cache_key=k, translated_text=v) for k, v in fresh.items()
                )
                db.commit()
            except IntegrityError:  # another request cached the same text at the same time
                db.rollback()

    return TranslateResponse(
        translations=[
            TranslatedText(translated_text=done[k] if k is not None else t)
            for k, t in zip(keys, payload.texts)
        ]
    )