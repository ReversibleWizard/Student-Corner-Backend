# 🧠 AI Code Reviewer Agent

A self-contained multi-agent module inside the AI Code Review Platform.  
It performs deep code analysis, bug detection, quality assessment, and automated optimization using a sophisticated Claude ↔ GPT validation pipeline.

---

## How It Fits in the Platform

```
Frontend (Next.js)
      │
      │  REST / JSON
      ▼
Spring Boot Middleware        ← handles auth, stores review data
      │
      │  REST / JSON
      ▼
FastAPI — code_reviewer       ← this module
      │
      ├── Claude (Anthropic)  (Code Understanding, Technical Review, Optimization, Chat)
      └── GPT (OpenAI)        (Quality Review, Validation)
```

The middleware is the single entry point from the frontend. It calls this backend directly and is responsible for:
- Authenticating the user
- Storing completed review sessions
- Receiving the full review payload (code, analysis, optimizations, chat history)

---

## Agent Architecture

```
                  StartReviewRequest
                  (with code)
                        │
                        ▼
          ┌─────────────────────────────┐
          │  1. Code Understanding      │  ← Claude Sonnet
          │     (Language, Complexity)  │
          └──────────┬──────────────────┘
                     │
                     ▼
          ┌─────────────────────────────┐
          │  2. Technical Review        │  ← Claude Opus
          │     (Bugs, Optimizations)   │
          └──────────┬──────────────────┘
                     │
                     ▼
          ┌─────────────────────────────┐
          │  3. Quality Review          │  ← GPT
          │     (Readability, Best      │
          │      Practices)             │
          └──────────┬──────────────────┘
                     │
                     ▼
          ┌─────────────────────────────┐
          │  Session Created            │
          │  (All 3 reviews complete)   │
          └──────────┬──────────────────┘
                     │
        ┌────────────┴────────────┐
        │                         │
        ▼                         ▼
┌──────────────┐         ┌──────────────────┐
│  OPTIMIZE    │         │   CHAT REFINE    │
│  (optional)  │         │   (optional)     │
└──────┬───────┘         └────────┬─────────┘
       │                          │
       │  Claude Optimizer        │  Claude Chat Refiner
       │  validated by GPT        │  validated by GPT
       │  (3 iteration loop)      │  (3 iteration loop)
       │                          │
       ▼                          ▼
┌──────────────────────────────────────┐
│  Application DB                      │  
│                                      │  
└──────────────────────────────────────┘
```

**Validation Loop Pattern:**
```
Claude generates → GPT validates → if invalid: feedback → Claude fixes → repeat (max 3x)
```

---

## Setup

### 1. Install dependencies

**Using pip:**
```bash
pip install -r requirements.txt
```

**Using uv (recommended — significantly faster):**
```bash
# Install uv if you don't have it
curl -Lsf https://astral.sh/uv/install.sh | sh    # macOS / Linux
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"  # Windows

# Create virtual environment and install
uv venv
uv pip install -r requirements.txt
```

### 2. Activate the virtual environment

**pip / venv:**
```bash
source .venv/bin/activate   # macOS / Linux
.venv\Scripts\activate      # Windows
```

**uv** creates and manages the venv automatically, but to activate manually:
```bash
source .venv/bin/activate   # macOS / Linux
.venv\Scripts\activate      # Windows
```

### 3. Configure environment
```bash
cp .env.example .env
```

Fill in `.env`:

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | ✅ | Powers GPT Quality Reviewer and Validator agents |
| `ANTHROPIC_API_KEY` | ✅ | Powers Claude Understander, Technical Reviewer, Optimizer, Chat |
| `MIDDLEWARE_URL` | Optional | Spring Boot endpoint for session storage |
| `MIDDLEWARE_AUTH_TOKEN` | Optional | Bearer token if middleware requires auth |

### 4. Run

**Using pip / uvicorn directly:**
```bash
# Development (auto-reload on file changes)
uvicorn main:app --reload --port 8001

# Production
uvicorn main:app --host 0.0.0.0 --port 8001 --workers 4
```

**Using uv:**
```bash
# Development
uv run uvicorn main:app --reload --port 8001

# Production
uv run uvicorn main:app --host 0.0.0.0 --port 8001 --workers 4
```

Interactive API docs → http://127.0.0.1:8001/docs

---

## API Reference

All routes are prefixed `/review`.

---

### POST `/review/start`

Start a new code review session. Runs the complete initial pipeline:
1. **Code Understanding** (Claude Sonnet) — Language detection, complexity analysis
2. **Technical Review** (Claude Opus) — Bug detection, optimization suggestions
3. **Quality Review** (GPT) — Readability, maintainability, best practices

**Request body:**
```json
{
  "code": "class LRUCache:\n    def __init__(self, capacity):\n        ...",
  "user_id": "user-123",
  "metadata": {
    "filename": "lru_cache.py",
    "project": "algorithms"
  }
}
```

**Response:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "understanding": {
    "programming_language_used": "Python",
    "problem_summary": "Implementation of LRU Cache...",
    "approach": "Uses dict + list...",
    "key_constructs": ["Dictionary", "List operations"],
    "complexity": {
      "time": "O(n)",
      "space": "O(capacity)"
    },
    "confidence": 0.99
  },
  "technical_review": {
    "correctness": "Partially Correct",
    "bugs": ["list.remove() is O(n)"],
    "edge_cases": ["Capacity of 0", "Negative capacity"],
    "complexity": {
      "time": "O(n)",
      "space": "O(capacity)"
    },
    "optimizations": ["Use OrderedDict for O(1) operations"],
    "improved_approach": "...",
    "tools_recommendation": [
      {
        "current": "dict + list",
        "suggested": "collections.OrderedDict",
        "reason": "O(1) operations instead of O(n)"
      }
    ],
    "corrected_code": "...",
    "confidence": 0.95
  },
  "quality_review": {
    "readability_score": 6,
    "code_quality_issues": ["Missing docstrings", "No type hints"],
    "maintainability_issues": ["O(n) operations limit scalability"],
    "best_practice_violations": ["No input validation"],
    "strengths": ["Clear variable names", "Simple logic"],
    "improvement_suggestions": ["Add type hints", "Use better data structure"],
    "production_readiness": {
      "status": "Medium",
      "issues": ["Performance issues at scale", "Missing error handling"]
    },
    "final_summary": "Functional but needs optimization...",
    "confidence": 0.92
  },
  "message": "Initial code review complete..."
}
```

> Save `session_id` — every subsequent request requires it.

---

### POST `/review/{session_id}/optimize`

Get optimized, production-ready code based on all reviews.  
Runs iterative optimization with validation:

1. **Claude Opus** generates optimized code
2. **GPT** validates correctness and improvements
3. Repeats up to 3 times until valid

On success, **automatically dispatches the complete session to middleware**.

**Response:**
```json
{
  "optimized_code": "from collections import OrderedDict\n\nclass LRUCache:\n    ...",
  "changes_made": [
    "Replaced dict + list with OrderedDict",
    "Used move_to_end() for O(1) access",
    "Added type hints and docstrings",
    "Added input validation",
    "Added __slots__ for memory efficiency"
  ],
  "optimization_summary": "Improved from O(n) to O(1) operations, added type safety...",
  "message": "Code optimization complete..."
}
```

**Idempotent:** Calling this endpoint multiple times returns the cached result.

---

### POST `/review/{session_id}/chat`

Refine code based on custom user instructions.  
Examples: *"Convert to Java"*, *"Make it async"*, *"Add error handling"*

Runs iterative refinement with validation:

1. **Claude Opus** applies the requested changes
2. **GPT** validates correctness
3. Repeats up to 3 times until valid

**Request body:**
```json
{
  "instruction": "Convert to Java and use HashMap + LinkedList"
}
```

**Response:**
```json
{
  "updated_code": "import java.util.*;\n\npublic class LRUCache {\n    ...",
  "changes_made": [
    "Converted from Python to Java",
    "Used HashMap<Integer, Integer> for storage",
    "Used LinkedHashMap for order tracking",
    "Maintained O(1) complexity"
  ],
  "explanation": "Converted the LRU Cache to Java using HashMap and LinkedHashMap...",
  "message": "Chat refinement complete."
}
```

**Multiple calls allowed:** Each chat turn is appended to `session.chat_history`.

On completion, **automatically dispatches the complete session to middleware**.

---

### GET `/review/{session_id}/status`

Get the current progress of a session.

**Response:**
```json
{
  "session_id": "550e8400-...",
  "is_completed": false,
  "is_optimized": false,
  "has_understanding": true,
  "has_technical_review": true,
  "has_quality_review": true,
  "chat_turns": 0,
  "started_at": "2025-03-26T10:30:00",
  "completed_at": null
}
```

---

## Typical Integration Flow

```
1.  Middleware  →  POST /review/start
                ←  { session_id, understanding, technical_review, quality_review }

2a. Option A: Optimize
      Middleware  →  POST /review/{session_id}/optimize
                  ←  { optimized_code, changes_made, optimization_summary }
                     [Backend auto-dispatches complete session to middleware]

2b. Option B: Chat refinement
      Middleware  →  POST /review/{session_id}/chat
                     { instruction: "Convert to Java" }
                  ←  { updated_code, changes_made, explanation }
                     [Backend auto-dispatches complete session to middleware]

3.  Optional: Chain multiple chat turns
      Middleware  →  POST /review/{session_id}/chat
                     { instruction: "Add async/await" }
                  ←  { updated_code, changes_made, explanation }
                     [Backend re-dispatches updated session to middleware]

4.  Check status anytime:
      Middleware  →  GET /review/{session_id}/status
                  ←  { is_completed, is_optimized, chat_turns, ... }
```

---

## Middleware Storage Contract

When a session completes (after `/optimize` or `/chat`), this backend automatically POSTs the following JSON to `MIDDLEWARE_URL`:

```json
{
  "session_id": "550e8400-...",
  "user_id": "user-123",
  "metadata": {
    "filename": "lru_cache.py",
    "project": "algorithms"
  },
  "original_code": "class LRUCache:\n    ...",
  "optimized_code": "from collections import OrderedDict\n\nclass LRUCache:\n    ...",
  "understanding": {
    "programming_language_used": "Python",
    "problem_summary": "...",
    "approach": "...",
    "key_constructs": ["..."],
    "complexity": { "time": "...", "space": "..." },
    "confidence": 0.99
  },
  "technical_review": {
    "correctness": "Partially Correct",
    "bugs": ["..."],
    "edge_cases": ["..."],
    "complexity": { "time": "...", "space": "..." },
    "optimizations": ["..."],
    "improved_approach": "...",
    "tools_recommendation": [...],
    "corrected_code": "...",
    "confidence": 0.95
  },
  "quality_review": {
    "readability_score": 6,
    "code_quality_issues": ["..."],
    "maintainability_issues": ["..."],
    "best_practice_violations": ["..."],
    "strengths": ["..."],
    "improvement_suggestions": ["..."],
    "production_readiness": {
      "status": "Medium",
      "issues": ["..."]
    },
    "final_summary": "...",
    "confidence": 0.92
  },
  "optimization_details": {
    "optimized_code": "...",
    "changes_made": ["..."],
    "optimization_summary": "..."
  },
  "chat_history": [
    {
      "instruction": "Convert to Java",
      "result": "Converted successfully",
      "code": "import java.util.*;\n\npublic class LRUCache { ... }",
      "changes_made": ["Converted to Java", "Used HashMap"]
    }
  ],
  "started_at": "2025-03-26T10:30:00+00:00",
  "completed_at": "2025-03-26T10:35:00+00:00",
  "source": "code_reviewer"
}
```

The middleware should respond with:
```json
{
  "status": "ok",
  "record_id": "<database-id>"
}
```

**Retry policy:** Up to 4 attempts with exponential backoff (1s → 2s → 4s → 8s) on network errors. 4xx responses are not retried. If all retries fail, a warning is logged but the review result is still returned to the user — the session is never lost from the user's perspective.

---

## Error Response Format

All errors follow the same JSON envelope:

```json
{
  "error":   "SESSION_NOT_FOUND",
  "message": "Session '550e8400-...' not found.",
  "path":    "/review/550e8400-.../status"
}
```

| `error` code | HTTP | Meaning |
|---|---|---|
| `SESSION_NOT_FOUND` | 404 | Invalid or expired session ID |
| `SESSION_ALREADY_COMPLETED` | 409 | Session is done |
| `SESSION_CREATION_FAILED` | 500 | Could not create session |
| `AGENT_ERROR` | 502 | LLM agent failed |
| `AGENT_TIMEOUT` | 504 | LLM did not respond in time |
| `INVALID_INPUT` | 422 | Empty code, empty instruction, etc. |
| `MIDDLEWARE_DISPATCH_FAILED` | 502 | Session storage POST failed (non-fatal) |
| `VALIDATION_ERROR` | 422 | Malformed request body |
| `INTERNAL_SERVER_ERROR` | 500 | Unexpected unhandled error |

---

## File Structure

```
project_root/
├── main.py                    ← FastAPI app entry point
├── requirements.txt           ← Python dependencies
├── .env.example               ← Environment variable template
├── .env                       ← Your actual credentials (git-ignored)
└── code_reviewer/
    ├── __init__.py
    ├── exceptions.py          ← Typed error hierarchy
    ├── logger.py              ← Centralized get_logger()
    ├── models.py              ← All Pydantic models
    │                             (agent outputs, session state,
    │                              API schemas, middleware payload)
    ├── reviewer_agent.py      ← Core orchestrator
    │                             runs all agents, handles validation loops
    ├── session_store.py       ← Thread-safe in-memory registry
    ├── routers/
    │   ├── __init__.py
    │   ├── review.py          ← POST /review/start
    │   │                         POST /review/{id}/optimize
    │   │                         GET  /review/{id}/status
    │   └── chat.py            ← POST /review/{id}/chat
    └── services/
        ├── __init__.py
        └── middleware_client.py ← MiddlewareClient (httpx + tenacity retry)
```

---

## Agent Details

### 1. Code Understanding Agent (Claude Sonnet 4.6)
- **Input:** Raw code
- **Output:** Language, problem summary, approach, key constructs, complexity, confidence
- **Model:** `claude-sonnet-4-6`
- **Speed:** Fast (~2-3s)

### 2. Technical Review Agent (Claude Opus 4.6)
- **Input:** Code + Understanding analysis
- **Output:** Correctness assessment, bugs, edge cases, complexity, optimizations, corrected code
- **Model:** `claude-opus-4-6`
- **Speed:** Moderate (~5-8s)

### 3. Quality Review Agent (GPT-4o-mini)
- **Input:** Code + Understanding + Technical Review
- **Output:** Readability score, quality issues, best practice violations, production readiness
- **Model:** `gpt-4o-mini` (via OpenAI Agents SDK)
- **Speed:** Fast (~3-5s)

### 4. Optimizer Agent (Claude Opus 4.6) + Validator (GPT-4o-mini)
- **Workflow:** 
  1. Claude generates optimized code based on all reviews
  2. GPT validates correctness and improvements
  3. If invalid → Claude fixes based on GPT feedback
  4. Repeats up to 3 times
- **Models:** `claude-opus-4-6` + `gpt-4o-mini`
- **Speed:** 10-30s (depending on iterations)

### 5. Chat Refiner Agent (Claude Opus 4.6) + Validator (GPT-4o-mini)
- **Workflow:**
  1. Claude applies user-requested changes
  2. GPT validates correctness and instruction compliance
  3. If invalid → Claude fixes based on GPT feedback
  4. Repeats up to 3 times
- **Models:** `claude-opus-4-6` + `gpt-4o-mini`
- **Speed:** 10-30s (depending on iterations)

---

## Why Claude ↔ GPT Validation?

**Claude (Anthropic):**
- Excellent at code generation, optimization, and following complex instructions
- Strong reasoning for architectural decisions
- Better at handling large context windows

**GPT (OpenAI):**
- Excellent at validation, edge case detection, and critique
- Fast inference for validation tasks
- Strong at checking correctness and best practices

**Validation Loop:**
- Prevents hallucinations and incorrect optimizations
- Ensures code is actually runnable before returning to user
- Combines strengths of both models

---

## Performance Considerations

**Initial Review** (`/start`):
- **Time:** ~10-15 seconds (3 agents in sequence)
- **Tokens:** ~2000-5000 depending on code size
- **Cost:** ~$0.02-0.05 per review

**Optimization** (`/optimize`):
- **Time:** ~15-30 seconds (iterative with validation)
- **Tokens:** ~3000-8000
- **Cost:** ~$0.03-0.08 per optimization

**Chat Refinement** (`/chat`):
- **Time:** ~15-30 seconds (iterative with validation)
- **Tokens:** ~2000-6000
- **Cost:** ~$0.02-0.06 per chat turn

**Session Storage:**
- In-memory (clears on restart)
- For persistence, ensure `MIDDLEWARE_URL` is configured
- Automatic retry with exponential backoff

---

## Production Deployment

1. **Use environment variables** for API keys (never hardcode)
2. **Configure MIDDLEWARE_URL** for persistent storage
3. **Use workers:** `uvicorn main:app --workers 4` for concurrency
4. **Add rate limiting** if exposed directly to the internet
5. **Monitor costs:** Track token usage via OpenAI/Anthropic dashboards
6. **Set timeouts:** Add request timeouts in production (default 30s)
7. **Use reverse proxy:** nginx or Caddy for HTTPS and load balancing

---

## Troubleshooting

**"Missing required env vars"**
- Check `.env` file exists and has `OPENAI_API_KEY` and `ANTHROPIC_API_KEY`

**"AGENT_ERROR: Failed to parse JSON output"**
- LLM returned invalid JSON — usually happens with extremely large code
- Reduce code size or increase model temperature

**"MIDDLEWARE_DISPATCH_FAILED"**
- Check `MIDDLEWARE_URL` is correct and reachable
- Verify middleware is running and accepting POST requests
- Check `MIDDLEWARE_AUTH_TOKEN` if required

**Slow response times**
- Normal for optimization and chat (10-30s due to validation loops)
- If consistently slow, check API rate limits

**Session not found**
- Sessions are in-memory — cleared on restart
- Check session_id is correct UUID format
- Ensure `/start` was called successfully

---

## Future Enhancements

- [ ] Add support for file uploads (analyze multiple files)
- [ ] Persistent session storage (Redis/PostgreSQL)
- [ ] Streaming responses for real-time feedback
- [ ] Language-specific analysis (custom agents per language)
- [ ] Security vulnerability scanning
- [ ] Test generation based on code analysis
- [ ] Complexity visualization
- [ ] Code comparison (before/after optimization)

---

## License

MIT

---

## Support

For issues or questions, please contact the platform team or create an issue in the repository.
