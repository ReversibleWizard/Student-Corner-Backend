"""All Pydantic models for the coder_reviewer agent."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Correctness(str, Enum):
    CORRECT = "Correct"
    PARTIALLY_CORRECT = "Partially Correct"
    INCORRECT = "Incorrect"


class ProductionStatus(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class ChatRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


# ---------------------------------------------------------------------------
# Agent output models
# ---------------------------------------------------------------------------


class ComplexityResult(BaseModel):
    time: str
    space: str


class UnderstandingResult(BaseModel):
    programming_language_used: str
    problem_summary: str
    approach: str
    key_constructs: list[str]
    complexity: ComplexityResult
    confidence: float


class ToolRecommendation(BaseModel):
    current: str
    suggested: str
    reason: str


class TechnicalReviewResult(BaseModel):
    correctness: Correctness
    bugs: list[str]
    edge_cases: list[str]
    complexity: ComplexityResult
    optimizations: list[str]
    improved_approach: str
    tools_recommendation: list[ToolRecommendation]
    corrected_code: str
    confidence: float


class ProductionReadiness(BaseModel):
    status: ProductionStatus
    issues: list[str]


class QualityReviewResult(BaseModel):
    readability_score: int = Field(ge=0, le=10)
    code_quality_issues: list[str]
    maintainability_issues: list[str]
    best_practice_violations: list[str]
    strengths: list[str]
    improvement_suggestions: list[str]
    production_readiness: ProductionReadiness
    final_summary: str
    confidence: float


class OptimizeResult(BaseModel):
    optimized_code: str
    changes_made: list[str]
    optimization_summary: str


class ChatRefinementResult(BaseModel):
    updated_code: str
    changes_made: list[str]
    explanation: str


# ---------------------------------------------------------------------------
# Session / pipeline models
# ---------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: ChatRole
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ReviewSession(BaseModel):
    """Tracks everything produced during one code-review session."""

    session_id: str
    original_code: str
    understanding: UnderstandingResult | None = None
    technical_review: TechnicalReviewResult | None = None
    quality_review: QualityReviewResult | None = None
    optimized_code: str | None = None
    current_code: str | None = None          # latest version after chat refinements
    chat_history: list[ChatMessage] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# API request / response models
# ---------------------------------------------------------------------------


class StartReviewRequest(BaseModel):
    code: str = Field(..., min_length=1, description="Source code to review")


class StartReviewResponse(BaseModel):
    session_id: str
    message: str = "Review session started"


class ReviewResultResponse(BaseModel):
    session_id: str
    understanding: UnderstandingResult | None
    technical_review: TechnicalReviewResult | None
    quality_review: QualityReviewResult | None
    optimized_code: str | None


class OptimizeRequest(BaseModel):
    session_id: str


class OptimizeResponse(BaseModel):
    session_id: str
    optimized_code: str


class ChatRequest(BaseModel):
    session_id: str
    message: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    session_id: str
    updated_code: str
    changes_made: list[str]
    explanation: str


# ---------------------------------------------------------------------------
# Middleware payload
# ---------------------------------------------------------------------------


class MiddlewareStorePayload(BaseModel):
    """Payload sent to the middleware to persist a completed session."""

    session_id: str
    original_code: str
    understanding: dict[str, Any] | None
    technical_review: dict[str, Any] | None
    quality_review: dict[str, Any] | None
    optimized_code: str | None
    final_code: str | None                   # current_code (latest after chat)
    chat_history: list[dict[str, Any]]       # serialised ChatMessage list
    created_at: str                          # ISO-8601
    updated_at: str                          # ISO-8601
    agent: str = "coder_reviewer"
