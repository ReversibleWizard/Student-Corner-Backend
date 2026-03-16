"""
All Pydantic models for the AI Interviewer module.

Sections:
  1. Agent output models        — structured outputs from LLM agents
  2. Session state              — InterviewContext (mutable session data)
  3. API request / response     — FastAPI endpoint schemas
  4. Middleware payload         — contract for Spring Boot storage endpoint
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Agent output models
# ═══════════════════════════════════════════════════════════════════════════════

class AnswerReview(BaseModel):
    """Output of AnswerReviewAgent — scored feedback on one candidate answer."""
    question:           str = Field(description="The question that was asked")
    user_answer:        str = Field(description="The raw answer provided by the candidate")
    score:              int = Field(description="Score from 0 to 10")
    strengths:          str = Field(description="What the candidate did well")
    weaknesses:         str = Field(description="Specific areas where the answer was lacking")
    user_answer_review: str = Field(description="Detailed, constructive feedback")
    topic_covered:      str = Field(description="Main topic/skill this question tested")
    difficulty:         str = Field(description="'easy', 'medium', or 'hard'")


class NextQuestion(BaseModel):
    """Output of QuestionGeneratorAgent — the next interview question."""
    question:   str = Field(description="The next interview question to ask")
    topic:      str = Field(description="Topic/skill this question targets")
    difficulty: str = Field(description="'easy', 'medium', or 'hard'")
    reasoning:  str = Field(description="Why this question was chosen")


class InterviewSummary(BaseModel):
    """Output of SummaryAgent — end-of-session performance report."""
    overall_score:         float = Field(description="Average score across all answers (0–10)")
    total_questions:       int   = Field(description="Total number of questions answered")
    strong_topics:         str   = Field(description="Topics the candidate answered well")
    weak_topics:           str   = Field(description="Topics the candidate struggled with")
    hiring_recommendation: str   = Field(description="'Strong Yes', 'Yes', 'Maybe', or 'No'")
    detailed_summary:      str   = Field(description="Full narrative performance summary")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Session state
# ═══════════════════════════════════════════════════════════════════════════════

class InterviewContext(BaseModel):
    """
    Mutable state for one interview session.
    Stored in SessionStore and updated on every answer.
    """
    history:             list[AnswerReview] = Field(default_factory=list)
    voice_reviews:       list[str]          = Field(
        default_factory=list,
        description=(
            "Parallel list to history — voice delivery review string for each answer. "
            "Empty string for text-only answers that have no voice review."
        ),
    )
    current_question:    str  = Field(default="Introduce yourself")
    current_topic:       str  = Field(default="Introduction")
    current_difficulty:  str  = Field(default="easy")
    question_count:      int  = Field(default=0)
    topics_covered:      list[str] = Field(default_factory=list)
    is_completed:        bool = Field(default=False)
    summary_text:        str  = Field(
        default="",
        description="Cached final summary text — populated when session ends.",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. API request / response schemas
# ═══════════════════════════════════════════════════════════════════════════════

class StartSessionRequest(BaseModel):
    name:                str       = "Candidate"
    experience_level:    str       = "Student"
    work_experience:     str       = "0 years"
    confidence_level:    str       = "Medium"
    target_role:         str       = "Software Engineer"
    duration_minutes:    int       = 20
    max_questions:       int       = 8
    priority_topics:     list[str] = Field(default_factory=lambda: [
        "Introduction", "Projects", "Python / Backend",
        "Algorithms & Data Structures", "Databases",
        "System Design", "AI / LLMs", "Behavioural",
    ])
    resume_text: str = Field(
        default="",
        description=(
            "Plain text of the candidate's resume, pre-parsed by the middleware. "
            "The backend uses this directly — PDF parsing is the middleware's responsibility."
        ),
    )
    resume_text: str = Field(
        default="",
        description=(
            "Plain text of the candidate's resume, pre-parsed by the middleware. "
            "The backend uses this directly — PDF parsing is the middleware's responsibility."
        ),
    )


class StartSessionResponse(BaseModel):
    session_id:      str
    opening_message: str


class TextAnswerRequest(BaseModel):
    session_id: str
    answer:     str


class AnswerReviewResult(BaseModel):
    """Structured review of the candidate's answer — returned to the frontend."""
    score:      int
    score_bar:  str = Field(description="Visual score bar e.g. '██████░░░░'")
    strengths:  str
    weaknesses: str
    feedback:   str
    topic:      str
    difficulty: str


class NextQuestionResult(BaseModel):
    """The next interview question — returned to the frontend."""
    question_number: int
    question:        str
    topic:           str
    difficulty:      str


class SummaryResult(BaseModel):
    """Final interview summary — returned when the session is complete."""
    overall_score:         float
    score_bar:             str
    total_questions:       int
    strong_topics:         str
    weak_topics:           str
    hiring_recommendation: str
    detailed_summary:      str


class InterviewerReply(BaseModel):
    """
    Response for text answers.
    Ongoing:   review + next_question populated, summary is null.
    Completed: summary populated, review + next_question are null.
    """
    is_completed:  bool
    review:        Optional[AnswerReviewResult] = None
    next_question: Optional[NextQuestionResult] = None
    summary:       Optional[SummaryResult]      = None


class VoiceAnswerResponse(BaseModel):
    """
    Response for voice answers — same structure as InterviewerReply
    plus transcript and voice delivery review.
    """
    is_completed:  bool
    transcript:    str
    voice_review:  str
    review:        Optional[AnswerReviewResult] = None
    next_question: Optional[NextQuestionResult] = None
    summary:       Optional[SummaryResult]      = None


class SessionSummaryResponse(BaseModel):
    """Response for manual /end call."""
    summary: SummaryResult


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Middleware payload — contract for Spring Boot storage endpoint
# ═══════════════════════════════════════════════════════════════════════════════

class QuestionRecord(BaseModel):
    """
    Single Q&A record sent to the middleware.
    Combines AnswerReview fields with the voice delivery review.
    """
    question_number:    int
    question:           str
    topic:              str
    difficulty:         str
    user_answer:        str
    score:              int
    strengths:          str
    weaknesses:         str
    feedback:           str
    voice_review:       Optional[str] = None   # None for text-only answers


class SessionPayload(BaseModel):
    """
    Full session data POSTed to the Spring Boot middleware
    once an interview session is completed.

    The middleware is responsible for persisting this to the database.
    """
    # ── Identity ──────────────────────────────────────────────────────────────
    session_id:        str
    candidate_name:    str
    target_role:       str
    experience_level:  str
    work_experience:   str
    confidence_level:  str

    # ── Timing ────────────────────────────────────────────────────────────────
    completed_at:      str   = Field(
        description="ISO 8601 UTC timestamp of when the session ended.",
    )
    duration_minutes:  int

    # ── Results ───────────────────────────────────────────────────────────────
    questions:         list[QuestionRecord]
    overall_score:     float
    total_questions:   int
    strong_topics:     str
    weak_topics:       str
    hiring_recommendation: str
    summary:           str

    # ── Meta ──────────────────────────────────────────────────────────────────
    source:            str = "ai_interviewer"   # identifies the sending service


class MiddlewareResponse(BaseModel):
    """Expected acknowledgement shape from the Spring Boot endpoint."""
    status:     str              # "ok" or "error"
    record_id:  Optional[str] = None   # DB primary key assigned by Spring Boot
    message:    Optional[str] = None