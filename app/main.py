from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.database import Base, engine
from app.routers import auth, pages
from fastapi.middleware.cors import CORSMiddleware
from app.routers import auth, categories, courses, pages
from app.config import settings
from app.routers import auth, categories, course_content, courses, pages
from app.routers import module_videos
from app.routers import enrollments
from app.routers import assignments
from app.routers import student_assessments
from app.routers import profile
from app.routers import translation
import app.assignment_models 

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Skill Next LMS API")

# CORS: allow the frontend origin(s) to call this API from the browser
origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(auth.router)
app.include_router(categories.router)
app.include_router(courses.router)
app.include_router(pages.router)
app.include_router(courses.router)
app.include_router(course_content.router)
app.include_router(module_videos.router)
app.include_router(enrollments.router)
app.include_router(assignments.router)
app.include_router(student_assessments.router)
app.include_router(profile.router)
app.include_router(translation.router)
app.include_router(courses.search_router)

@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    errors = [
        {
            "field": ".".join(str(p) for p in e["loc"] if p != "body"),
            "message": e["msg"],
        }
        for e in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"success": False, "message": "Validation failed.", "errors": errors},
    )


@app.get("/health")
def health():
    return {"status": "ok"}

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "message": exc.detail},
        headers=getattr(exc, "headers", None),
    )