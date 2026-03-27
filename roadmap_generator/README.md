# 🗺️ Adaptive Roadmap Generator Agent

A self-contained multi-agent module inside the AI Platform.
It generates dynamic, highly personalized learning roadmaps (for both skills and roles), formats them into visual graph data (nodes and edges), and allows candidates to iteratively customize their curriculum via a chat interface.

---

## How It Fits in the Platform

```text
Frontend (React Flow / UI)
      │
      │  REST / JSON
      ▼
Spring Boot Middleware        ← handles user auth, history DB
      │
      │  REST / JSON  
      ▼
FastAPI — roadmap_generator   ← this module
      │
      ├── Anthropic API       (Generator Agent & Updater Agent)
      └── OpenAI API          (Strict JSON Validator Agent)
```

The middleware acts as the bridge. It calls this backend to generate the curriculum and relies on a webhook from this module to store the final roadmap and chat history when a session ends.

---

## Agent Architecture

```text
                  Generate / Chat Request
                        │
                        ▼
               ┌─────────────────────┐
               │    SessionStore     │  ← Manages state, versions,
               │  (in-memory state)  │     and chat history
               └──────────┬──────────┘     
                          │
               ┌──────────┴──────────┐
               ▼                     ▼
      ┌──────────────┐     ┌──────────────────────┐
      │  Generator / │     │  Python Tools Base   │
      │  Updater     │  ↔  │  (Fetch roadmap DB,  │
      │  (Claude)    │     │   calc skill gaps)   │
      └──────────────┘     └──────────────────────┘
               │
               ▼  (Raw JSON output)
      ┌──────────────┐
      │  Validator   │ ← Enforces strict JSON schema, strips markdown,
      │  (OpenAI)    │   ensures nodes/edges are mathematically valid.
      └──────────────┘
               │
               ▼  (On /terminate)
      ┌──────────────────────┐
      │  MiddlewareClient    │  → POST final roadmap & chat to Spring Boot
      └──────────────────────┘
```

---

## Setup

### 1. Install dependencies

**Using uv (recommended — significantly faster):**

```bash
# Install uv if you don't have it
curl -Lsf https://astral.sh/uv/install.sh | sh    # macOS / Linux

# Create virtual environment and install
uv venv
uv pip install fastapi uvicorn pydantic openai openai-agents httpx
```

**Using standard pip:**

```bash
python -m venv .venv
source .venv/bin/activate
pip install fastapi uvicorn pydantic openai openai-agents httpx
```

### 2. Configure environment

Create a `.env` file in the root directory:

| Variable            | Required | Description                                                                                                |
| ------------------- | -------- | ---------------------------------------------------------------------------------------------------------- |
| `ANTHROPIC_API_KEY` | ✅        | Powers the Generator and Updater agents (Claude 3.5 Sonnet)                                                |
| `OPENAI_API_KEY`    | ✅        | Powers the strict JSON formatting Validator agent (GPT-4o-mini)                                            |
| `MIDDLEWARE_URL`    | Optional | Spring Boot endpoint for session history storage (defaults to `http://localhost:8080/api/roadmap-history`) |

### 3. Run the Server

**Development (auto-reload):**

```bash
uv run uvicorn main:app --reload --port 8000
```

**Production:**

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

Interactive API docs → http://127.0.0.1:8000/docs

---

## API Reference

All routes are prefixed `/roadmap`.

---

### POST `/roadmap/generate`

Start a new roadmap session. Generates Version 1 of the curriculum and graph structure.

**Request body:**

```json
{
  "goal_type": "skill",
  "goal": "Machine Learning",
  "current_level": "beginner",
  "known_skills": ["Python"],
  "timeline": "3 months"
}
```

**Response:**

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "version": 1,
  "goal": "Machine Learning",
  "prerequisites": ["Python", "Basic Math"],
  "missing_skills": ["Basic Math"],
  "roadmap": [
    {
      "phase": "Phase 1",
      "title": "Introduction",
      "topics": ["What is ML", "Types of ML"]
    }
  ],
  "graph": {
    "nodes": [
      {"id": "machine_learning", "label": "Machine Learning"},
      {"id": "introduction", "label": "Introduction"}
    ],
    "edges": [
      {"source": "machine_learning", "target": "introduction"}
    ]
  }
}
```

> **Important:** Save `session_id`. It is required for chatting and terminating the session.

---

### POST `/roadmap/chat/{session_id}`

Submit a natural language request to modify the existing roadmap (e.g., "Make Phase 2 harder" or "Remove the SQL section").

**Request body:**

```json
{
  "message": "Can you remove the Calculus section and make it more focused on deep learning?"
}
```

**Response:**
Returns the complete updated roadmap schema, with the `version` incremented (e.g., `version: 2`).

---

### POST `/roadmap/restore/{session_id}`

Rehydrates an old, terminated session from the Spring Boot middleware back into the FastAPI active memory so the user can chat with it again.

**Response:**
Returns the latest roadmap state for that session, ready to be rendered and updated.

---

### POST `/roadmap/terminate/{session_id}`

Manually end the session. Triggers a background task to dispatch the final roadmap and complete chat history to the Spring Boot middleware, then clears the backend memory.

**Response:**

```json
{
  "status": "success",
  "message": "Session 550e8400-... terminated and sent to middleware."
}
```

---

## Typical Integration Flow

```text
1.  Middleware  →  POST /roadmap/generate
                ←  { session_id, roadmap_data, graph_data, version: 1 }

2.  Frontend renders the flowchart using the `graph` array (e.g., React Flow).

3.  Loop until user is satisfied:
      User types  → "Add more focus on pandas"
      Frontend    → POST /roadmap/chat/{session_id}
                  ← { updated_roadmap, updated_graph, version: 2 }
      Frontend re-renders the graph seamlessly.

4.  User exits page:
      Frontend/Middleware → POST /roadmap/terminate/{session_id}
                          ← { status: "success" }
      (Backend automatically webhooks the final state to Middleware DB)

5.  (Optional) User returns 3 days later:
      Frontend    → POST /roadmap/restore/{session_id}
                  ← { previous_roadmap_data }
      User can now call /chat again.
```

---

## Middleware Storage Contract

When `/terminate/{session_id}` is called, this backend automatically `POST`s the following JSON to the `MIDDLEWARE_URL`:

```json
{
  "session_id": "550e8400-...",
  "final_version": 3,
  "chat_history": [
    {"role": "user", "content": "Initial Generation: Machine Learning"},
    {"role": "assistant", "content": "Updated roadmap to version 1"},
    {"role": "user", "content": "Remove calculus"},
    {"role": "assistant", "content": "Updated roadmap to version 2"}
  ],
  "roadmap": {
    "goal": "Machine Learning",
    "prerequisites": ["Python"],
    "missing_skills": [],
    "roadmap": [ ... phases array ... ],
    "graph": { ... nodes and edges ... }
  }
}
```

The middleware should catch this webhook and save it to the database for future retrieval.

---

## File Structure

```text
roadmap_generator/
├── __init__.py
├── exceptions.py          ← Typed error hierarchy (SessionNotFoundError, etc.)
├── logger.py              ← Centralized logging config
├── models.py              ← Pydantic schemas for inputs, outputs, and graphs
├── session_store.py       ← SessionStore (manages active roadmaps + versions in memory)
├── roadmap_agent.py       ← Instantiates Claude/OpenAI clients and defines Agent prompts
├── routers/
│   ├── __init__.py
│   └── roadmap.py         ← FastAPI endpoints (/generate, /chat, /restore, /terminate)
└── services/
    ├── __init__.py
    ├── data.py            ← ROADMAP_DB (Static knowledge base for curriculum structures)
    ├── tools.py           ← Deterministic graph builder and skill gap calculator
    └── middleware.py      ← Async HTTP client for sending webhooks/fetching history
```
