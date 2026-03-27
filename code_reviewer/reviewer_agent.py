"""Orchestrator for the multi-agent code review pipeline.

Pipeline:
  Code Understanding (Claude) →
  Technical Review / Fix (Claude) →
  Code Quality Review (GPT) →
  [optional] Optimize (Claude ↔ GPT loop) →
  [optional] Chat Refinement (Claude ↔ GPT loop)
"""

from __future__ import annotations

import json
import os
import re

from agents import Agent, Runner, trace
from openai import OpenAI

from .exceptions import AgentExecutionError, JSONParseError
from .logger import get_logger
from .models import (
    ChatRefinementResult,
    OptimizeResult,
    QualityReviewResult,
    ReviewSession,
    TechnicalReviewResult,
    UnderstandingResult,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _clean_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]).strip()
    return text


def _extract_json(text: str) -> str:
    text = _clean_json(text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else text


def _safe_parse(text: str, agent_name: str) -> dict:
    for fn in (_clean_json, _extract_json):
        try:
            return json.loads(fn(text))
        except json.JSONDecodeError:
            pass
    raise JSONParseError(agent_name, text)


# ---------------------------------------------------------------------------
# Shared OpenAI-compat client (points at Anthropic)
# ---------------------------------------------------------------------------


def _claude_client() -> OpenAI:
    return OpenAI(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        base_url="https://api.anthropic.com/v1/",
    )


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_UNDERSTANDER_SYSTEM = """
You are an experienced developer specialized in understanding code written by others.

Your job is to analyze code and return structured JSON with:

- programming_language_used  (Python, Java, C++, Go, Kotlin, Javascript, etc.)
- problem_summary
- approach
- key_constructs
- complexity
    - time
    - space
- confidence  (0 to 1)

Rules:
- Be concise
- Do NOT explain anything outside JSON
- Always return valid JSON

-- IMPORTANT --
Response format:
{
    "programming_language_used": "...",
    "problem_summary": "...",
    "approach": "...",
    "key_constructs": ["..."],
    "complexity": {
        "time": "...",
        "space": "..."
    },
    "confidence": 0.0
}
"""

_CORRECTOR_SYSTEM = """
You are a senior software engineer and expert code reviewer.

Your job is NOT to understand the code from scratch.
You will be provided with:
  1. The original code
  2. A structured analysis from a previous agent

Use this to perform a deep technical review and improve the code.

---

Your tasks:
  1. Validate correctness
  2. Identify logical bugs
  3. Detect missing edge case handling
  4. Analyze time and space complexity
  5. Suggest optimizations
  6. Recommend better tools / libraries (unbiased)
  7. Provide corrected code (minimal fix — keep structure similar)

---

Return ONLY valid JSON. Do NOT wrap in ```json or ```.

FORMAT:
{
  "correctness": "Correct | Partially Correct | Incorrect",
  "bugs": ["..."],
  "edge_cases": ["..."],
  "complexity": { "time": "...", "space": "..." },
  "optimizations": ["..."],
  "improved_approach": "...",
  "tools_recommendation": [
    { "current": "...", "suggested": "...", "reason": "..." }
  ],
  "corrected_code": "...",
  "confidence": 0.0
}

STRICT RULES:
- Be critical and precise (like a real interviewer)
- corrected_code must be valid and runnable
- If no tool improvement needed, return []
- confidence must be 0–1
"""

_QUALITY_SYSTEM = """
You are a senior software engineer performing a professional code review.

Your job is to evaluate code quality, maintainability, and production readiness.

You will be provided with:
  1. Original code
  2. Code understanding analysis
  3. Technical review from another agent

---

Your tasks:
  1. Evaluate readability and clarity
  2. Identify code quality issues
  3. Assess maintainability and scalability
  4. Check adherence to best practices
  5. Suggest improvements for production-level code

---

Return ONLY valid JSON. Do NOT wrap in ```json or ```.

FORMAT:
{
  "readability_score": 0,
  "code_quality_issues": ["..."],
  "maintainability_issues": ["..."],
  "best_practice_violations": ["..."],
  "strengths": ["..."],
  "improvement_suggestions": ["..."],
  "production_readiness": {
    "status": "Low | Medium | High",
    "issues": ["..."]
  },
  "final_summary": "...",
  "confidence": 0.0
}

STRICT RULES:
- readability_score: 0–10
- Be critical and realistic
- final_summary: 2–3 lines
- confidence: 0–1
"""

_OPTIMIZER_SYSTEM = """
You are an expert software engineer specializing in code optimization.

Your role is to produce a fully optimized, production-ready version of the code
using the feedback and reviews provided.

Responsibilities:
  - Apply all feedback and review suggestions first
  - Improve time and space complexity where possible
  - Use optimal data structures and algorithms
  - Ensure code is clean, readable, and maintainable
  - Apply best practices
  - Ensure correctness

Output Requirements:
  - Return ONLY valid JSON
  - Do NOT wrap in ``` or ```json

FORMAT:
{
  "optimized_code": "...",
  "changes_made": ["..."],
  "optimization_summary": "..."
}
"""

_VALIDATOR_SYSTEM = """
You are a strict code validator.

You will receive code and must check:
  - Correctness and logical validity
  - Whether requested improvements were applied
  - Whether the code is runnable

Return ONLY valid JSON. Do NOT wrap in ```json or ```.

FORMAT:
{
  "valid": true,
  "issues": ["..."],
  "feedback": "..."
}
"""

_CHAT_SYSTEM = """
You are an expert software engineer helping refine and modify code based on user requests.

Responsibilities:
  - Apply the requested changes to the code
  - Keep context from previous agent reviews
  - Maintain correctness and readability
  - Ensure the code remains functional

Output Requirements:
  - Return ONLY valid JSON
  - Do NOT wrap in ``` or ```json

FORMAT:
{
  "updated_code": "...",
  "changes_made": ["..."],
  "explanation": "..."
}
"""

# ---------------------------------------------------------------------------
# GPT agents (OpenAI Agents SDK)
# ---------------------------------------------------------------------------

_gpt_quality_agent = Agent(
    name="Code Quality Reviewer",
    instructions=_QUALITY_SYSTEM,
    model="gpt-4o-mini",
)

_gpt_validator_agent = Agent(
    name="GPT Code Validator",
    instructions=_VALIDATOR_SYSTEM,
    model="gpt-4o-mini",
)


# ---------------------------------------------------------------------------
# Individual agent runners
# ---------------------------------------------------------------------------


def run_understanding(code: str) -> UnderstandingResult:
    client = _claude_client()
    response = client.chat.completions.create(
        model="claude-sonnet-4-6",
        messages=[
            {"role": "system", "content": _UNDERSTANDER_SYSTEM},
            {"role": "user", "content": f"Analyze the following code:\n\nCode:\n{code}"},
        ],
    )
    raw = response.choices[0].message.content
    data = _safe_parse(raw, "CodeUnderstander")
    return UnderstandingResult(**data)


def run_technical_review(code: str, understanding: UnderstandingResult) -> TechnicalReviewResult:
    client = _claude_client()
    user_message = (
        f"Code understanding from previous agent:\n{understanding.model_dump()}\n\n"
        f"Code:\n{code}"
    )
    response = client.chat.completions.create(
        model="claude-opus-4-6",
        messages=[
            {"role": "system", "content": _CORRECTOR_SYSTEM},
            {"role": "user", "content": user_message},
        ],
    )
    raw = response.choices[0].message.content
    data = _safe_parse(raw, "TechnicalReviewer")
    return TechnicalReviewResult(**data)


async def run_quality_review(
    code: str,
    understanding: UnderstandingResult,
    technical_review: TechnicalReviewResult,
) -> QualityReviewResult:
    input_text = (
        f"Code:\n{code}\n\n"
        f"Understanding:\n{understanding.model_dump()}\n\n"
        f"Technical Review:\n{technical_review.model_dump()}"
    )
    with trace("CodeQualityAgent"):
        result = await Runner.run(_gpt_quality_agent, input=input_text)
    data = _safe_parse(result.final_output, "QualityReviewer")
    return QualityReviewResult(**data)


async def run_optimizer(
    code: str,
    session: ReviewSession,
    max_iters: int = 3,
) -> str:
    """Claude optimizes; GPT validates. Returns final optimized code."""
    client = _claude_client()
    feedback: str | None = None
    current = code

    for i in range(max_iters):
        logger.info("Optimizer iteration %d/%d", i + 1, max_iters)

        user_prompt = (
            f"Optimize this code to production level based on the following reviews.\n\n"
            f"Understanding:\n{session.understanding.model_dump() if session.understanding else 'N/A'}\n\n"
            f"Technical Review:\n{session.technical_review.model_dump() if session.technical_review else 'N/A'}\n\n"
            f"Quality Review:\n{session.quality_review.model_dump() if session.quality_review else 'N/A'}\n\n"
            f"Previous Feedback (if any):\n{feedback}\n\n"
            f"Code:\n{current}"
        )

        resp = client.chat.completions.create(
            model="claude-opus-4-6",
            messages=[
                {"role": "system", "content": _OPTIMIZER_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw = resp.choices[0].message.content
        parsed = _safe_parse(raw, "Optimizer")
        opt_result = OptimizeResult(**parsed)
        optimized_code = opt_result.optimized_code

        gpt_prompt = (
            f"Check if the optimized code satisfies:\n"
            f"- correctness\n- improvements suggested earlier\n- is runnable\n\n"
            f'Return JSON: {{"valid": true/false, "issues": [...], "feedback": "..."}}\n\n'
            f"Code:\n{optimized_code}"
        )
        gpt_result = await Runner.run(_gpt_validator_agent, input=gpt_prompt)
        gpt_json = _safe_parse(gpt_result.final_output, "GPTValidator")

        if gpt_json.get("valid"):
            logger.info("Optimization validated on iteration %d", i + 1)
            return optimized_code

        feedback = gpt_json.get("feedback", "")
        current = optimized_code

    logger.warning("Optimizer hit max iterations — returning last version")
    return current


async def run_chat_refinement(
    code: str,
    session: ReviewSession,
    user_query: str,
    max_iters: int = 3,
) -> ChatRefinementResult:
    """Apply user-requested changes with Claude, validate with GPT."""
    client = _claude_client()
    feedback: str | None = None
    current = code

    for i in range(max_iters):
        logger.info("Chat refinement iteration %d/%d", i + 1, max_iters)

        user_prompt = (
            f"Modify the code based on the user request.\n\n"
            f"User Request:\n{user_query}\n\n"
            f"Understanding:\n{session.understanding.model_dump() if session.understanding else 'N/A'}\n\n"
            f"Technical Review:\n{session.technical_review.model_dump() if session.technical_review else 'N/A'}\n\n"
            f"Previous Feedback (if any):\n{feedback}\n\n"
            f"Code:\n{current}"
        )

        resp = client.chat.completions.create(
            model="claude-opus-4-6",
            messages=[
                {"role": "system", "content": _CHAT_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw = resp.choices[0].message.content
        parsed = _safe_parse(raw, "ChatRefiner")
        chat_result = ChatRefinementResult(**parsed)
        updated_code = chat_result.updated_code

        gpt_prompt = (
            f"Check:\n- Is this code correct?\n- Is it runnable?\n"
            f"- Does it satisfy the user request: '{user_query}'?\n\n"
            f'Return JSON: {{"valid": true/false, "issues": [...], "feedback": "..."}}\n\n'
            f"Code:\n{updated_code}"
        )
        gpt_result = await Runner.run(_gpt_validator_agent, input=gpt_prompt)
        gpt_json = _safe_parse(gpt_result.final_output, "GPTValidator")

        if gpt_json.get("valid"):
            logger.info("Chat refinement validated on iteration %d", i + 1)
            return ChatRefinementResult(
                updated_code=updated_code,
                changes_made=chat_result.changes_made,
                explanation=chat_result.explanation,
            )

        feedback = gpt_json.get("feedback", "")
        current = updated_code

    logger.warning("Chat refinement hit max iterations — returning last version")
    return chat_result


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


async def run_full_review(code: str, session: ReviewSession) -> ReviewSession:
    """Run the 3-agent initial pipeline and mutate session in place."""
    logger.info("[%s] Starting full review pipeline", session.session_id)

    session.understanding = run_understanding(code)
    logger.info("[%s] Understanding done", session.session_id)

    session.technical_review = run_technical_review(code, session.understanding)
    logger.info("[%s] Technical review done", session.session_id)

    session.quality_review = await run_quality_review(
        code, session.understanding, session.technical_review
    )
    logger.info("[%s] Quality review done", session.session_id)

    return session
