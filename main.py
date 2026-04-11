"""
Student's Corner — FastAPI Backend Entry Point

Run:        uvicorn main:app --reload --port 8000
Production: uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
Docs:       http://127.0.0.1:8000/docs

Platform Overview:
  - Student's Corner is a multi-agent AI backend providing:
      • AI Interview System (voice + text evaluation)
      • Code Review & Optimization
      • Adaptive Learning Roadmap Generation

Architecture Notes:
  - Spring Boot Middleware acts as the central gateway:
      • Handles authentication
      • Parses resume PDFs → sends plain text
      • Forwards structured requests to this FastAPI service

  - FastAPI Backend routes requests to domain-specific agents:
      • /interview → AI Interviewer Agent
      • /review    → Code Reviewer Agent
      • /roadmap   → Roadmap Generator Agent

  - Session persistence is now handled by MongoDB (Motor async driver).
    Each agent writes its pipeline steps and final response directly to the
    Application DB.  The middleware no longer receives session-storage calls.
"""
import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv(override=True)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from ai_interviewer.routers import session, answer
from ai_interviewer.exceptions import InterviewerBaseError
from ai_interviewer.logger import get_logger

from code_reviewer.routers import review, chat
from code_reviewer.exceptions import CodeReviewBaseError
from code_reviewer.logger import get_logger

from roadmap_generator.routers.roadmap import router as roadmap_router

# ── MongoDB layer ─────────────────────────────────────────────────────────────
from db.mongo import init_db, close_db

log = get_logger("main")


# ── Startup / shutdown ────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate critical environment variables and initialise DB on startup."""
    log.info("=" * 60)
    log.info("Student's Corner starting up")

    # ── Env-var validation ────────────────────────────────────────────────────
    required = {
        "OPENAI_API_KEY":      os.getenv("OPENAI_API_KEY"),
        "ANTHROPIC_API_KEY":   os.getenv("ANTHROPIC_API_KEY"),
        "ELEVENLABS_API_KEY":  os.getenv("ELEVENLABS_API_KEY"),
        "ELEVENLABS_AGENT_ID": os.getenv("ELEVENLABS_AGENT_ID"),
        # MongoDB is now required — session persistence depends on it.
        "MONGODB_URI":         os.getenv("MONGODB_URI"),
    }
    optional = {
        "MONGODB_DB_NAME":       os.getenv("MONGODB_DB_NAME", "students_corner"),
        "ELEVENLABS_VOICE_ID":   os.getenv("ELEVENLABS_VOICE_ID", "(using default Daniel)"),
        # MIDDLEWARE_URL is still read but no longer used for session storage.
        "MIDDLEWARE_URL":        os.getenv("MIDDLEWARE_URL", "(not set — auth-forwarding disabled)"),
        "MIDDLEWARE_AUTH_TOKEN": "(set)" if os.getenv("MIDDLEWARE_AUTH_TOKEN") else "(not set)",
    }

    missing = [k for k, v in required.items() if not v]
    if missing:
        log.error(
            "Missing required env vars: %s — check your .env file.",
            ", ".join(missing),
        )
    else:
        log.info("All required environment variables are present.")

    for key, val in optional.items():
        log.info("  %-25s %s", key, val)

    # ── MongoDB startup ───────────────────────────────────────────────────────
    try:
        await init_db()
    except Exception as exc:
        # Log prominently but still yield so the app can serve non-DB endpoints
        # and return a clear error rather than crashing silently.
        log.critical(
            "MongoDB initialisation FAILED: %s  — "
            "session persistence will be unavailable.",
            exc,
        )

    log.info("=" * 60)

    yield   # ← application runs here

    # ── Shutdown ──────────────────────────────────────────────────────────────
    await close_db()
    log.info("Student's Corner shutting down.")


app = FastAPI(
    title="🎓 Student's Corner — AI Career & Learning Platform",
    description=(
        "A multi-agent AI platform designed as a one-stop solution for student needs.\n\n"

        "🚀 **Core Features:**\n"
        "- 🎙️ AI Interview System (voice + text, real-time evaluation)\n"
        "- 💻 Code Review & Optimization (Claude ↔ GPT validation loop)\n"
        "- 🗺️ Adaptive Roadmap Generation (personalized learning paths)\n\n"

        "🏗️ **Architecture:**\n"
        "- Spring Boot middleware for authentication, resume parsing, and request forwarding\n"
        "- FastAPI backend orchestrating multiple AI agents\n"
        "- MongoDB (Motor) for async session persistence\n"
        "- Integration with OpenAI, Anthropic, and ElevenLabs APIs\n\n"

        "📌 **Note:** Resume parsing is handled by the middleware. "
        "This service operates purely on structured input and AI-driven processing."
    ),
    version="1.1.0",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# Update allow_origins with your actual frontend / middleware URLs in production.

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",   # Next.js / React dev
        "http://127.0.0.1:3000",
        "http://localhost:8080",   # Spring Boot middleware
        "http://127.0.0.1:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(session.router)  # /interview/start  /{id}/end  /{id}/status
app.include_router(answer.router)   # /interview/answer/text  /voice  /tts

app.include_router(review.router)   # /review/start  /{id}/status  /{id}/optimize
app.include_router(chat.router)     # /review/{id}/chat

app.include_router(roadmap_router)


# ── Global exception handlers ─────────────────────────────────────────────────

@app.exception_handler(InterviewerBaseError)
async def interviewer_error_handler(request: Request, exc: InterviewerBaseError):
    """All typed InterviewerBaseError subclasses → consistent JSON envelope."""
    log.warning(
        "[%s] %s %s — %s",
        exc.error_code, request.method, request.url.path, exc.message,
    )
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "error":   exc.error_code,
            "message": exc.message,
            "path":    str(request.url.path),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    errors = [
        {"field": " → ".join(str(l) for l in e["loc"]), "issue": e["msg"]}
        for e in exc.errors()
    ]
    log.warning("Validation error on %s %s: %s", request.method, request.url.path, errors)
    return JSONResponse(
        status_code=422,
        content={
            "error":   "VALIDATION_ERROR",
            "message": "Request validation failed.",
            "errors":  errors,
            "path":    str(request.url.path),
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    log.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error":   "INTERNAL_SERVER_ERROR",
            "message": "An unexpected error occurred. Please try again.",
            "path":    str(request.url.path),
        },
    )


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/", tags=["Health"])
async def root():
    return {"status": "ok", "message": "Student's Corner is running 🎙️"}


@app.get("/health", tags=["Health"])
async def health():
    """
    Returns service health, active in-memory session count, and DB status.
    Used by the middleware and load balancer for liveness checks.
    """
    from ai_interviewer.session_store import session_store

    db_status = "unknown"
    try:
        from db.mongo import get_db
        await get_db().command("ping")
        db_status = "connected"
    except Exception as exc:
        db_status = f"error: {exc}"

    return {
        "status":          "healthy",
        "active_sessions": len(session_store),
        "db_status":       db_status,
        "middleware_url":  os.getenv("MIDDLEWARE_URL", "not configured"),
    }