"""
Pydantic models for the code review system.

Includes:
- API request/response schemas
- Agent output schemas
- Session state models
- Middleware payload schema
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID


# ─── API Request Models ───────────────────────────────────────────────────────

class StartReviewRequest(BaseModel):
    """Request to start a new code review session."""
    
    code: str = Field(..., description="Code to review (any programming language)")
    user_id: Optional[str] = Field(None, description="Optional user identifier")
    metadata: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        description="Optional metadata (e.g., filename, project name)"
    )


class ChatRequest(BaseModel):
    """Request to refine code via chat."""
    
    instruction: str = Field(..., description="User instruction (e.g., 'Convert to Java', 'Make it O(1)')")


# ─── Agent Output Models ──────────────────────────────────────────────────────

class ComplexityAnalysis(BaseModel):
    """Time and space complexity analysis."""
    
    time: str
    space: str


class CodeUnderstanding(BaseModel):
    """Output from the Code Understanding agent (Claude)."""
    
    programming_language_used: str
    problem_summary: str
    approach: str
    key_constructs: List[str]
    complexity: ComplexityAnalysis
    confidence: float = Field(..., ge=0.0, le=1.0)


class ToolRecommendation(BaseModel):
    """Suggestion to use a better library/tool."""
    
    current: str
    suggested: str
    reason: str


class TechnicalReview(BaseModel):
    """Output from the Technical Review agent (Claude)."""
    
    correctness: str  # "Correct" | "Partially Correct" | "Incorrect"
    bugs: List[str]
    edge_cases: List[str]
    complexity: ComplexityAnalysis
    optimizations: List[str]
    improved_approach: str
    tools_recommendation: List[ToolRecommendation]
    corrected_code: str
    confidence: float = Field(..., ge=0.0, le=1.0)


class ProductionReadiness(BaseModel):
    """Production readiness assessment."""
    
    status: str  # "Low" | "Medium" | "High"
    issues: List[str]


class QualityReview(BaseModel):
    """Output from the Code Quality agent (GPT)."""
    
    readability_score: int = Field(..., ge=0, le=10)
    code_quality_issues: List[str]
    maintainability_issues: List[str]
    best_practice_violations: List[str]
    strengths: List[str]
    improvement_suggestions: List[str]
    production_readiness: ProductionReadiness
    final_summary: str
    confidence: float = Field(..., ge=0.0, le=1.0)


class OptimizationResult(BaseModel):
    """Output from the Optimizer agent (Claude)."""
    
    optimized_code: str
    changes_made: List[str]
    optimization_summary: str


class ChatRefinementResult(BaseModel):
    """Output from the Chat Refinement agent (Claude)."""
    
    updated_code: str
    changes_made: List[str]
    explanation: str


class ValidationResult(BaseModel):
    """Output from the GPT Validator agent."""
    
    valid: bool
    issues: List[str]
    feedback: str


# ─── Session State ────────────────────────────────────────────────────────────

class ReviewSession(BaseModel):
    """Complete state of a code review session."""
    
    session_id: UUID
    original_code: str
    user_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    # Agent outputs
    understanding: Optional[CodeUnderstanding] = None
    technical_review: Optional[TechnicalReview] = None
    quality_review: Optional[QualityReview] = None
    optimized_code: Optional[str] = None
    optimization_details: Optional[OptimizationResult] = None
    
    # Chat history
    chat_history: List[Dict[str, str]] = Field(default_factory=list)
    # Format: [{"instruction": "...", "result": "...", "code": "..."}, ...]
    
    # Timestamps
    started_at: datetime
    completed_at: Optional[datetime] = None
    
    # Flags
    is_completed: bool = False
    is_optimized: bool = False


# ─── API Response Models ──────────────────────────────────────────────────────

class StartReviewResponse(BaseModel):
    """Response after starting a code review."""
    
    session_id: UUID
    understanding: CodeUnderstanding
    technical_review: TechnicalReview
    quality_review: QualityReview
    message: str = "Initial code review complete. Use /review/{session_id}/optimize to get optimized code or /review/{session_id}/chat to refine further."


class OptimizeResponse(BaseModel):
    """Response after optimizing code."""
    
    optimized_code: str
    changes_made: List[str]
    optimization_summary: str
    message: str = "Code optimization complete. Use /review/{session_id}/chat for further refinements."


class ChatResponse(BaseModel):
    """Response after chat refinement."""
    
    updated_code: str
    changes_made: List[str]
    explanation: str
    message: str = "Chat refinement complete."


class SessionStatusResponse(BaseModel):
    """Current status of a review session."""
    
    session_id: UUID
    is_completed: bool
    is_optimized: bool
    has_understanding: bool
    has_technical_review: bool
    has_quality_review: bool
    chat_turns: int
    started_at: datetime
    completed_at: Optional[datetime] = None


# ─── Middleware Payload ───────────────────────────────────────────────────────

class MiddlewarePayload(BaseModel):
    """Complete session payload sent to middleware for storage."""
    
    session_id: str
    user_id: Optional[str]
    metadata: Dict[str, Any]
    
    # Code artifacts
    original_code: str
    optimized_code: Optional[str]
    
    # Agent outputs
    understanding: Optional[Dict[str, Any]]
    technical_review: Optional[Dict[str, Any]]
    quality_review: Optional[Dict[str, Any]]
    optimization_details: Optional[Dict[str, Any]]
    
    # Chat history
    chat_history: List[Dict[str, str]]
    
    # Timestamps
    started_at: str
    completed_at: str
    
    # Source
    source: str = "code_reviewer"