"""
AI Interview Platform — FastAPI root entry point.

Run:        uvicorn main:app --reload --port 8000
Production: uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
Docs:       http://127.0.0.1:8000/docs

Architecture note:
  - Resume parsing is the Spring Boot middleware's responsibility.
    The middleware sends pre-extracted plain text in `resume_text`
    when calling POST /interview/start.
  - On session completion the backend automatically POSTs the full
    session payload (questions, answers, scores, voice reviews, summary)
    to the middleware via MIDDLEWARE_URL for persistent storage.
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

log = get_logger("main")


# ── Startup / shutdown ────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate critical environment variables on startup."""
    log.info("=" * 60)
    log.info("AI Interview Platform starting up")

    required = {
        "OPENAI_API_KEY":      os.getenv("OPENAI_API_KEY"),
        "ELEVENLABS_API_KEY":  os.getenv("ELEVENLABS_API_KEY"),
        "ELEVENLABS_AGENT_ID": os.getenv("ELEVENLABS_AGENT_ID"),
    }
    optional = {
        "ELEVENLABS_VOICE_ID":   os.getenv("ELEVENLABS_VOICE_ID", "(using default Daniel)"),
        "MIDDLEWARE_URL":        os.getenv("MIDDLEWARE_URL", "(not set — dispatch disabled)"),
        "MIDDLEWARE_AUTH_TOKEN": "(set)" if os.getenv("MIDDLEWARE_AUTH_TOKEN") else "(not set)",
    }

    missing = [k for k, v in required.items() if not v]
    if missing:
        log.error("Missing required env vars: %s — check your .env file.", ", ".join(missing))
    else:
        log.info("All required environment variables are present.")

    for key, val in optional.items():
        log.info("  %-25s %s", key, val)

    log.info("=" * 60)
    yield
    log.info("AI Interview Platform shutting down.")


app = FastAPI(
    title="🎙️ AI Interview Platform",
    description=(
        "Multi-agent AI interview system.\n\n"
        "**Features:**\n"
        "- Voice input via ElevenLabs Conversational Agent (transcript + delivery review)\n"
        "- Text fallback input\n"
        "- Adaptive question generation based on candidate performance\n"
        "- Automatic session storage dispatch to Spring Boot middleware on completion\n\n"
        "**Resume:** Pre-parsed plain text sent by the middleware in `resume_text` — "
        "no PDF handling in this service."
    ),
    version="1.0.0",
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

# Add other agent routers here as the platform grows:
# from resume_agent.routers  import router as resume_router
# from roadmap_agent.routers import router as roadmap_router
# app.include_router(resume_router)
# app.include_router(roadmap_router)


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
    return {"status": "ok", "message": "AI Interview Platform is running 🎙️"}


@app.get("/health", tags=["Health"])
async def health():
    """
    Returns service health and current active session count.
    Used by the middleware and load balancer for liveness checks.
    """
    from ai_interviewer.session_store import session_store
    return {
        "status":          "healthy",
        "active_sessions": len(session_store),
        "middleware_url":  os.getenv("MIDDLEWARE_URL", "not configured"),
    }