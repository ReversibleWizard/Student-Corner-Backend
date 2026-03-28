"""
Code Reviewer Agent — Central orchestrator for the multi-agent pipeline.

Flow:
  Claude Understander → Claude Technical Reviewer → GPT Quality Reviewer
  → Claude Optimizer (validated by GPT) → Claude Chat Refiner (validated by GPT)
"""
import os
import json
import re
from openai import OpenAI
from agents import Runner, Agent

from code_reviewer.models import (
    CodeUnderstanding,
    TechnicalReview,
    QualityReview,
    OptimizationResult,
    ChatRefinementResult,
    ValidationResult,
)
from code_reviewer.exceptions import AgentError, AgentTimeoutError
from code_reviewer.logger import get_logger

log = get_logger("reviewer_agent")


# ─── Helper Utilities ─────────────────────────────────────────────────────────

def clean_json_output(text: str) -> str:
    """Strip markdown code fences from LLM JSON responses."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # drop first line (```json or ```) and last line (```)
        text = "\n".join(lines[1:-1]).strip()
    return text


def extract_json(text: str) -> str:
    """Extract first JSON object from text, even if surrounded by prose."""
    text = clean_json_output(text)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else text


def safe_json_parse(text: str) -> dict | None:
    """Parse JSON safely — tries clean → extract → fail gracefully."""
    for fn in (clean_json_output, extract_json):
        try:
            return json.loads(fn(text))
        except json.JSONDecodeError:
            pass
    log.error("JSON parse failed. Raw output: %s", text)
    return None


# ─── Agent System Prompts ─────────────────────────────────────────────────────

UNDERSTANDER_SYSTEM = """
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

CORRECTOR_SYSTEM = """
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
- confidence must be 0-1
"""

QUALITY_SYSTEM = """
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
- readability_score: 0-10
- Be critical and realistic
- final_summary: 2-3 lines
- confidence: 0-1
"""

OPTIMIZER_SYSTEM = """
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

CHAT_SYSTEM = """
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

VALIDATOR_SYSTEM = """
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


# ─── Agent Instances ──────────────────────────────────────────────────────────

# GPT agents using OpenAI Agents SDK
code_quality_agent = Agent(
    name="Code Quality Reviewer",
    instructions=QUALITY_SYSTEM,
    model="gpt-4o-mini",
)

gpt_validator_agent = Agent(
    name="GPT Code Validator",
    instructions=VALIDATOR_SYSTEM,
    model="gpt-4o-mini",
)


# ─── Agent Functions ──────────────────────────────────────────────────────────

def run_code_understander(code: str) -> CodeUnderstanding:
    """Run Claude Sonnet for code understanding."""
    try:
        client = OpenAI(
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            base_url="https://api.anthropic.com/v1/",
        )
        
        user_message = f"Analyze the following code:\n\nCode:\n{code}"
        
        response = client.chat.completions.create(
            model="claude-sonnet-4-6",
            messages=[
                {"role": "system", "content": UNDERSTANDER_SYSTEM},
                {"role": "user", "content": user_message},
            ],
        )
        
        output = response.choices[0].message.content
        parsed = safe_json_parse(output)
        
        if not parsed:
            raise AgentError("Code Understander", "Failed to parse JSON output")
        
        return CodeUnderstanding(**parsed)
    
    except Exception as e:
        log.exception("Code Understander failed")
        raise AgentError("Code Understander", str(e))


def run_technical_reviewer(code: str, understanding: CodeUnderstanding) -> TechnicalReview:
    """Run Claude Opus for technical review and bug detection."""
    try:
        client = OpenAI(
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            base_url="https://api.anthropic.com/v1/",
        )
        
        user_message = (
            f"Code understanding from previous agent:\n{understanding.model_dump_json(indent=2)}\n\n"
            f"Code:\n{code}"
        )
        
        response = client.chat.completions.create(
            model="claude-opus-4-6",
            messages=[
                {"role": "system", "content": CORRECTOR_SYSTEM},
                {"role": "user", "content": user_message},
            ],
        )
        
        output = response.choices[0].message.content
        parsed = safe_json_parse(output)
        
        if not parsed:
            raise AgentError("Technical Reviewer", "Failed to parse JSON output")
        
        return TechnicalReview(**parsed)
    
    except Exception as e:
        log.exception("Technical Reviewer failed")
        raise AgentError("Technical Reviewer", str(e))


async def run_quality_reviewer(
    code: str,
    understanding: CodeUnderstanding,
    technical_review: TechnicalReview
) -> QualityReview:
    """Run GPT for code quality and maintainability review."""
    try:
        input_text = (
            f"Code:\n{code}\n\n"
            f"Understanding:\n{understanding.model_dump_json(indent=2)}\n\n"
            f"Technical Review:\n{technical_review.model_dump_json(indent=2)}"
        )
        
        result = await Runner.run(code_quality_agent, input=input_text)
        parsed = safe_json_parse(result.final_output)
        
        if not parsed:
            raise AgentError("Quality Reviewer", "Failed to parse JSON output")
        
        return QualityReview(**parsed)
    
    except Exception as e:
        log.exception("Quality Reviewer failed")
        raise AgentError("Quality Reviewer", str(e))


def call_claude_optimizer(user_message: str) -> str:
    """Call Claude Opus with the optimizer system prompt."""
    client = OpenAI(
        api_key=os.environ.get("ANTHROPIC_API_KEY"),
        base_url="https://api.anthropic.com/v1/",
    )
    
    response = client.chat.completions.create(
        model="claude-opus-4-6",
        messages=[
            {"role": "system", "content": OPTIMIZER_SYSTEM},
            {"role": "user", "content": user_message},
        ],
    )
    
    return response.choices[0].message.content


async def optimize_code_with_validation(
    code: str,
    understanding: CodeUnderstanding,
    technical_review: TechnicalReview,
    quality_review: QualityReview,
    max_iters: int = 3
) -> OptimizationResult:
    """
    Iteratively optimize code with Claude and validate with GPT.
    
    Returns the final OptimizationResult after validation.
    """
    feedback = None
    
    for i in range(max_iters):
        log.info(f"Claude Optimization Iteration {i + 1}")
        
        user_prompt = (
            f"Optimize this code to production level based on the following reviews.\n\n"
            f"Understanding:\n{understanding.model_dump_json(indent=2)}\n\n"
            f"Technical Review:\n{technical_review.model_dump_json(indent=2)}\n\n"
            f"Quality Review:\n{quality_review.model_dump_json(indent=2)}\n\n"
            f"Previous Feedback (if any):\n{feedback}\n\n"
            f"Code:\n{code}"
        )
        
        claude_output = call_claude_optimizer(user_prompt)
        claude_json = safe_json_parse(claude_output)
        
        if not claude_json:
            log.error("Claude returned unparseable output")
            raise AgentError("Optimizer", "Failed to parse JSON output")
        
        optimized_code = claude_json["optimized_code"]
        log.info(f"Changes made: {claude_json.get('changes_made')}")
        
        # Validate with GPT
        log.info("GPT Validation...")
        gpt_prompt = (
            f"Check if the optimized code satisfies:\n"
            f"- correctness\n- improvements suggested earlier\n- is runnable\n\n"
            f"Return JSON: {{\"valid\": true/false, \"issues\": [...], \"feedback\": \"...\"}}\n\n"
            f"Code:\n{optimized_code}"
        )
        
        gpt_result = await Runner.run(gpt_validator_agent, input=gpt_prompt)
        gpt_json = safe_json_parse(gpt_result.final_output)
        
        if gpt_json and gpt_json.get("valid"):
            log.info("Optimization successful")
            return OptimizationResult(**claude_json)
        
        issues = gpt_json.get("issues") if gpt_json else "parse error"
        log.warning(f"Optimization needs improvement: {issues}")
        feedback = gpt_json.get("feedback") if gpt_json else ""
        code = optimized_code  # iterative refinement
    
    log.warning("Max iterations reached — returning last version")
    return OptimizationResult(**claude_json)


def call_claude_chat(user_message: str) -> str:
    """Call Claude Opus with the chat-refinement system prompt."""
    client = OpenAI(
        api_key=os.environ.get("ANTHROPIC_API_KEY"),
        base_url="https://api.anthropic.com/v1/",
    )
    
    response = client.chat.completions.create(
        model="claude-opus-4-6",
        messages=[
            {"role": "system", "content": CHAT_SYSTEM},
            {"role": "user", "content": user_message},
        ],
    )
    
    return response.choices[0].message.content


async def refine_code_with_chat(
    code: str,
    understanding: CodeUnderstanding,
    technical_review: TechnicalReview,
    user_instruction: str,
    max_iters: int = 3
) -> ChatRefinementResult:
    """
    Apply user-requested changes with Claude, validate with GPT.
    
    Args:
        code: Current code to refine
        understanding: Initial code understanding
        technical_review: Initial technical review
        user_instruction: User's custom instruction (e.g., 'Convert to Java')
        max_iters: Maximum validation iterations
        
    Returns:
        ChatRefinementResult with updated code
    """
    feedback = None
    
    for i in range(max_iters):
        log.info(f"Claude Chat Iteration {i + 1}")
        
        user_prompt = (
            f"Modify the code based on the user request.\n\n"
            f"User Request:\n{user_instruction}\n\n"
            f"Understanding:\n{understanding.model_dump_json(indent=2)}\n\n"
            f"Technical Review:\n{technical_review.model_dump_json(indent=2)}\n\n"
            f"Previous Feedback (if any):\n{feedback}\n\n"
            f"Code:\n{code}"
        )
        
        claude_output = call_claude_chat(user_prompt)
        claude_json = safe_json_parse(claude_output)
        
        if not claude_json:
            log.error("Claude returned unparseable output")
            raise AgentError("Chat Refiner", "Failed to parse JSON output")
        
        updated_code = claude_json["updated_code"]
        log.info(f"Changes: {claude_json.get('changes_made')}")
        
        # Validate with GPT
        log.info("GPT Validation...")
        gpt_prompt = (
            f"Check:\n- Is this code correct?\n- Is it runnable?\n"
            f"- Does it satisfy the user request: '{user_instruction}'?\n\n"
            f"Return JSON: {{\"valid\": true/false, \"issues\": [...], \"feedback\": \"...\"}}\n\n"
            f"Code:\n{updated_code}"
        )
        
        gpt_result = await Runner.run(gpt_validator_agent, input=gpt_prompt)
        gpt_json = safe_json_parse(gpt_result.final_output)
        
        if gpt_json and gpt_json.get("valid"):
            log.info("Chat refinement successful")
            return ChatRefinementResult(**claude_json)
        
        issues = gpt_json.get("issues") if gpt_json else "parse error"
        log.warning(f"Chat refinement needs fix: {issues}")
        feedback = gpt_json.get("feedback") if gpt_json else ""
        code = updated_code
    
    log.warning("Max iterations reached — returning last version")
    return ChatRefinementResult(**claude_json)