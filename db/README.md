# 🗄️ Shared Database Layer — `db/`

A lightweight, shared MongoDB persistence layer used by all AI agents in the platform.  
It replaces middleware-based session storage with direct, async MongoDB writes via the Motor driver.

---

## How It Fits in the Platform

```
FastAPI (main.py)
      │
      │  lifespan startup / shutdown
      ▼
   db/mongo.py              ← opens / closes Motor connection
      │
      ▼
   db/session_repository.py ← CRUD operations on the sessions collection
      │
      ├── ai_interviewer/session_store.py
      ├── code_reviewer/session_store.py
      └── roadmap_generator/session_store.py
```

Each agent's `session_store.py` calls `session_repository` directly.  
No agent module talks to MongoDB on its own — all DB access goes through this layer.

---

## Architecture

```
                 ┌─────────────────────────────────┐
                 │          main.py  lifespan       │
                 │   await init_db()  /  close_db() │
                 └──────────────┬──────────────────┘
                                │
                                ▼
                    ┌────────────────────┐
                    │     db/mongo.py    │
                    │  Motor singleton   │
                    │  AsyncIOMotorClient│
                    │  Index bootstrap   │
                    └─────────┬──────────┘
                              │  get_db()
                              ▼
                 ┌────────────────────────────┐
                 │  db/session_repository.py  │
                 │                            │
                 │  create_session()          │
                 │  update_session_step()     │
                 │  complete_session()        │
                 │  fail_session()            │
                 │  get_session_by_id()       │
                 │  get_active_session()      │
                 │  get_sessions_by_user()    │
                 └────────────────────────────┘
                              │
             ┌────────────────┼─────────────────┐
             ▼                ▼                  ▼
   ai_interviewer/   code_reviewer/    roadmap_generator/
   session_store.py  session_store.py  session_store.py
```

---

## Setup

### 1. Install dependencies

Add to your existing `requirements.txt`:

```text
motor>=3.4.0
pymongo>=4.7.0
```

Then install:

```bash
pip install -r requirements.txt
# or
uv pip install -r requirements.txt
```

### 2. Configure environment

Add to your `.env` file:

| Variable | Required | Description |
|---|---|---|
| `MONGODB_URI` | ✅ | Full connection string — local or Atlas SRV |
| `MONGODB_DB_NAME` | Optional | Database name (default: `students_corner`) |

Examples:

```bash
# Local
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB_NAME=students_corner

# MongoDB Atlas
MONGODB_URI=mongodb+srv://<user>:<pass>@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
MONGODB_DB_NAME=students_corner
```

### 3. That's it

`main.py` calls `init_db()` and `close_db()` automatically in its lifespan handler.  
No manual wiring is required beyond setting the env vars.

---

## API Reference

### `db/mongo.py`

Manages the Motor connection singleton. Import and call these only from `main.py`.

```python
from db.mongo import init_db, close_db, get_db
```

| Function | Signature | Description |
|---|---|---|
| `init_db` | `async () → None` | Opens connection, pings server, bootstraps indexes. Raises `RuntimeError` if `MONGODB_URI` is unset. |
| `close_db` | `async () → None` | Closes the Motor connection pool gracefully. |
| `get_db` | `() → AsyncIOMotorDatabase` | Returns the active DB handle. Raises `RuntimeError` if called before `init_db()`. |

**Index bootstrap** (runs automatically on every startup — idempotent):

| Index | Fields | Type | Purpose |
|---|---|---|---|
| `idx_session_id` | `session_id` | Unique | Primary look-up |
| `idx_user_app_status` | `user_id, app_id, status` | Compound | Resume logic |
| `idx_user_id` | `user_id` | Standard | User history queries |
| `idx_ttl_last_activity` | `last_activity_at` | TTL (24 h) | Auto-expire stale `in_progress` sessions |

---

### `db/session_repository.py`

All CRUD operations on the `sessions` collection. Import the singleton `session_repository`:

```python
from db.session_repository import session_repository
```

---

#### `create_session(data)`

Insert a new session document. `status` defaults to `"in_progress"`.  
Timestamps (`created_at`, `updated_at`, `last_activity_at`) are injected automatically.

```python
await session_repository.create_session({
    "session_id": "S1001",
    "user_id":    "U123",
    "app_id":     "code_reviewer",   # "ai_interviewer" | "code_reviewer" | "roadmap_generator"
    "input":      { "type": "code", "content": "class LRUCache: ..." },
})
```

Raises `DuplicateKeyError` if `session_id` already exists.

---

#### `update_session_step(session_id, field_path, value)`

Write the output of a single pipeline step using dot-notation.  
`updated_at` and `last_activity_at` are refreshed on every call.

```python
# Pipeline steps
await session_repository.update_session_step(
    "S1001",
    "pipeline.code_understanding",
    {
        "model":     "claude-sonnet",
        "output":    { "language": "Python", "complexity": "O(1)" },
        "timestamp": "2026-04-07T10:00:01Z",
    }
)

# Optional steps
await session_repository.update_session_step(
    "S1001",
    "optional_steps.optimize",
    {
        "enabled":      True,
        "iterations":   [...],
        "final_output": "...",
    }
)
```

Returns the updated document, or `None` if `session_id` was not found.

---

#### `complete_session(session_id, final_response, status="completed")`

Mark a session as finished and persist the final agent output.

```python
await session_repository.complete_session(
    "S1001",
    final_response={
        "summary":    "Code reviewed and optimised",
        "final_code": "class LRUCache: ...",
        "highlights": ["Fixed bug for capacity=0", "Improved readability"],
    },
    status="completed",   # or "failed"
)
```

---

#### `fail_session(session_id, reason)`

Convenience wrapper — marks a session as `"failed"` with an error note.  
Call this from `except` blocks so stale `in_progress` sessions don't block the resume logic.

```python
try:
    ...
except Exception as exc:
    await session_repository.fail_session(session_id, reason=str(exc))
```

---

#### `get_session_by_id(session_id)`

Fetch one session document by its `session_id`. Returns `None` if not found.

```python
doc = await session_repository.get_session_by_id("S1001")
```

---

#### `get_active_session(user_id, app_id)`

Return the most-recent `in_progress` session for a user + app combination.  
This is the core of the **resume logic** — call it before creating a new session.

```python
existing = await session_repository.get_active_session(
    user_id="U123",
    app_id="code_reviewer",
)

if existing:
    session_id = existing["session_id"]   # resume
else:
    session_id = generate_new_id()        # start fresh
```

Returns `None` when no matching session exists.

---

#### `get_sessions_by_user(user_id, app_id=None, limit=20)`

Retrieve a paginated list of sessions for a user, newest first.

```python
history = await session_repository.get_sessions_by_user("U123", app_id="ai_interviewer")
```

---

## MongoDB Document Schema

One document per session. Stored in the `sessions` collection.

```json
{
  "_id": "ObjectId",

  "session_id":        "S1001",
  "parent_session_id": null,

  "user_id": "U123",
  "app_id":  "code_reviewer",

  "input": {
    "type":    "code",
    "content": "class LRUCache: ..."
  },

  "pipeline": {
    "code_understanding": {
      "model":  "claude-sonnet",
      "output": { "language": "Python", "complexity": "O(1)" },
      "timestamp": "2026-04-07T10:00:01Z"
    },
    "technical_review": {
      "model":  "claude-opus",
      "output": { "bugs": ["Missing edge case for capacity=0"] },
      "timestamp": "2026-04-07T10:00:03Z"
    },
    "quality_review": {
      "model":  "gpt-4",
      "output": { "readability": "Good", "best_practices": ["Add docstrings"] },
      "timestamp": "2026-04-07T10:00:05Z"
    }
  },

  "optional_steps": {
    "optimize": {
      "enabled":      true,
      "iterations":   [{ "model": "claude", "output": "...", "timestamp": "..." }],
      "final_output": "class LRUCache: ..."
    },
    "chat_refine": {
      "enabled": false
    }
  },

  "final_response": {
    "summary":    "Code reviewed and optimised",
    "final_code": "class LRUCache: ...",
    "highlights": ["Fixed bug for capacity=0"]
  },

  "status": "completed",

  "created_at":       "2026-04-07T10:00:00Z",
  "updated_at":       "2026-04-07T10:00:10Z",
  "last_activity_at": "2026-04-07T10:00:10Z"
}
```

**`app_id` values by agent:**

| Agent | `app_id` |
|---|---|
| AI Interviewer | `ai_interviewer` |
| Code Reviewer | `code_reviewer` |
| Roadmap Generator | `roadmap_generator` |

**`status` values:**

| Value | Meaning |
|---|---|
| `in_progress` | Session is active |
| `completed` | Agent finished successfully |
| `failed` | Agent encountered an unrecoverable error |

---

## Session Lifecycle

```
Request arrives at agent router
        │
        ▼
get_active_session(user_id, app_id)
        │
   found? ──Yes──▶ resume existing session_id
        │
       No
        │
        ▼
create_session({ session_id, user_id, app_id, input })
        │                        status = "in_progress"
        ▼
agent runs pipeline step 1
        │
        ▼
update_session_step("pipeline.step_1", output)
        │
     (repeat for each step)
        │
        ▼
update_session_step("optional_steps.X", output)   ← if applicable
        │
        ▼
complete_session(final_response)
        │                        status = "completed"
        ▼
agent returns HTTP response
```

On any unhandled exception:
```
except Exception as exc:
    await session_repository.fail_session(session_id, reason=str(exc))
    #                                      status = "failed"
```

---

## Error Handling

All repository methods wrap Motor driver calls and re-raise on failure so the calling agent can decide the recovery strategy. The recommended pattern in every agent router:

```python
try:
    await session_repository.create_session({...})
except DuplicateKeyError:
    # session_id collision — generate a new one and retry
    ...
except Exception as exc:
    log.error("DB error: %s", exc)
    raise HTTPException(status_code=500, detail="Session storage unavailable.")
```

Non-critical persistence steps (e.g. saving an optional pipeline step) may catch and log rather than re-raise, since the agent's in-memory state is still valid:

```python
try:
    await session_repository.update_session_step(sid, "optional_steps.chat_refine", value)
except Exception as exc:
    log.warning("Non-fatal DB persistence failure: %s", exc)
    # session continues — in-memory state is unaffected
```

---

## File Structure

```
db/
├── __init__.py              ← exports init_db, close_db, get_db, session_repository
├── mongo.py                 ← Motor singleton, ping-on-startup, index bootstrap
└── session_repository.py   ← SessionRepository class + module-level singleton
```