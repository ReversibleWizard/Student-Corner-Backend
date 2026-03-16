# 🎙️ AI Interviewer Agent

A self-contained multi-agent module inside the AI Interview Platform.  
It conducts adaptive technical interviews over voice or text, reviews answers in real time, and dispatches the complete session record to the Spring Boot middleware on completion.

---

## How It Fits in the Platform

```
Frontend (Next.js)
      │
      │  REST / JSON
      ▼
Spring Boot Middleware        ← parses PDF, stores data, handles auth
      │
      │  REST / JSON  (resume_text sent on start)
      ▼
FastAPI — ai_interviewer      ← this module
      │
      ├── OpenAI Agents SDK   (AnswerReview, QuestionGenerator, Summary)
      └── ElevenLabs          (TTS for questions, Conversational Agent for voice input)
```

The middleware is the single entry point from the frontend. It calls this backend directly and is responsible for:
- Authenticating the user
- Fetching the candidate's resume from its database
- Parsing the PDF and extracting plain text
- Passing that text to this backend in `resume_text`
- Receiving and storing the completed session payload

---

## Agent Architecture

```
                  StartSessionRequest
                  (with resume_text)
                        │
                        ▼
               ┌─────────────────────┐
               │   InterviewerAgent  │  ← Central Orchestrator
               │   (session state)   │     owns context, history,
               └──────────┬──────────┘     scores, difficulty
                          │
               ┌──────────┴──────────┐
               ▼                     ▼
      ┌──────────────┐     ┌──────────────────────┐
      │ AnswerReview │     │  QuestionGenerator   │
      │    Agent     │     │      Agent           │
      └──────────────┘     └──────────────────────┘
               │
               ▼  (when session ends)
      ┌──────────────┐
      │   Summary    │
      │    Agent     │
      └──────────────┘
               │
               ▼
      ┌──────────────────────┐
      │  MiddlewareClient    │  → POST session payload to Spring Boot
      └──────────────────────┘
```

**Voice path adds:**
```
User audio
    │
    ▼
ElevenLabs Conversational Agent
    ├── callback_user_transcript  → transcript  → InterviewerAgent
    └── callback_agent_response   → delivery review  → stored in context
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
| `OPENAI_API_KEY` | ✅ | Powers all three LLM agents |
| `ELEVENLABS_API_KEY` | ✅ | TTS + Conversational Agent |
| `ELEVENLABS_AGENT_ID` | ✅ | Your Voice Delivery Coach agent ID |
| `ELEVENLABS_VOICE_ID` | Optional | TTS voice (defaults to Daniel) |
| `MIDDLEWARE_URL` | Optional | Spring Boot endpoint for session storage |
| `MIDDLEWARE_AUTH_TOKEN` | Optional | Bearer token if middleware requires auth |

### 4. Run

**Using pip / uvicorn directly:**
```bash
# Development (auto-reload on file changes)
uvicorn main:app --reload --port 8000

# Production
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

**Using uv:**
```bash
# Development
uv run uvicorn main:app --reload --port 8000

# Production
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

Interactive API docs → http://127.0.0.1:8000/docs

---

## API Reference

All routes are prefixed `/interview`.

---

### POST `/interview/start`

Start a new interview session. Called by the Spring Boot middleware.  
The middleware must parse the candidate's PDF resume and send the extracted text in `resume_text` — this backend does no PDF handling.

**Request body:**
```json
{
  "name":               "Sayak",
  "experience_level":   "Student",
  "work_experience":    "0 years",
  "confidence_level":   "Low",
  "target_role":        "Backend Developer / AI Engineer",
  "duration_minutes":   20,
  "max_questions":      8,
  "priority_topics":    [
    "Introduction", "Projects", "Python / Backend",
    "Algorithms & Data Structures", "Databases",
    "System Design", "AI / LLMs", "Behavioural"
  ],
  "resume_text": "Sayak Mitra Majumder\nComputer Science student at KIIT..."
}
```

**Response:**
```json
{
  "session_id":      "550e8400-e29b-41d4-a716-446655440000",
  "opening_message": "👋 Welcome, Sayak! I'll be your interviewer today..."
}
```

> Save `session_id` — every subsequent request requires it.

---

### POST `/interview/answer/text`

Submit a typed answer. Use this when the candidate types instead of speaking.  
No voice delivery review is generated for text answers.

**Request body:**
```json
{
  "session_id": "550e8400-...",
  "answer":     "I am Sayak, a final-year CS student at KIIT..."
}
```

**Response:**
```json
{
  "ai_message":   "**📊 Score: 8/10** `████████░░`\n\n**✅ Strengths:** ...\n\n**🎯 Next [MEDIUM] — Projects**\n\nTell me about your Movie Recommender project.",
  "is_completed": false
}
```

When `is_completed` is `true`, `ai_message` contains the full interview summary instead of the next question.

---

### POST `/interview/answer/voice`

Submit a voice answer as an audio file.  
The ElevenLabs Conversational Agent processes the audio and returns both the transcript and a vocal delivery review (confidence, tone, pace, emotion) in a single call.

**Request:** `multipart/form-data`

| Field | Type | Description |
|---|---|---|
| `session_id` | string (form field) | Active session ID |
| `audio` | file | Recorded audio — webm, wav, or mp3 |

**Response:**
```json
{
  "transcript":   "I am Sayak, a final-year CS student...",
  "ai_message":   "**📊 Score: 8/10** `████████░░`\n\n...\n\n**🎯 Next [MEDIUM]**\n\n...",
  "voice_review": "### 🎙️ Voice Delivery Review\n\n> *Analysed by ElevenLabs...*\n\nConfidence: Medium. Tone: Calm...",
  "is_completed": false
}
```

**Graceful degradation:** if the voice agent fails (timeout, misconfiguration), a fallback message is placed in `voice_review` and the interview continues uninterrupted. The transcript failure is the only hard error.

---

### POST `/interview/answer/tts`

Convert the interviewer's reply text to MP3 audio bytes.  
The frontend calls this to play the AI's question aloud.

**Request body:**
```json
{
  "session_id": "550e8400-...",
  "answer":     "Tell me about your Movie Recommender project."
}
```

**Response:** `audio/mpeg` binary stream — pipe into an `<audio>` element or Web Audio API.

---

### POST `/interview/{session_id}/end`

Manually end the interview before all questions are answered.  
Generates the final summary and dispatches session data to the middleware.  
Requires at least one answered question.

**Response:**
```json
{
  "summary": "## 🏁 Interview Complete!\n\n**Overall Score: 7.5/10**..."
}
```

---

### GET `/interview/{session_id}/status`

Get the current progress of a session.

**Response:**
```json
{
  "session_id":         "550e8400-...",
  "question_count":     3,
  "max_questions":      8,
  "topics_covered":     ["Introduction", "Projects"],
  "current_topic":      "Python / Backend",
  "current_difficulty": "medium",
  "is_completed":       false
}
```

---

## Typical Integration Flow

```
1.  Middleware  →  POST /interview/start          (with resume_text)
                ←  { session_id, opening_message }

2.  Frontend plays opening_message via TTS or displays it.

3.  Loop until is_completed = true:

    Voice answer:
      Frontend  →  POST /interview/answer/voice   (multipart: session_id + audio)
                ←  { transcript, ai_message, voice_review, is_completed }

    Text answer:
      Frontend  →  POST /interview/answer/text    (json: session_id + answer)
                ←  { ai_message, is_completed }

    (Optional) Play AI reply:
      Frontend  →  POST /interview/answer/tts     (json: session_id + answer=ai_message)
                ←  MP3 bytes

4.  When is_completed = true:
      - Display ai_message as the final summary
      - Backend has already dispatched full session payload to middleware automatically

5.  (Optional early end):
      Middleware/Frontend  →  POST /interview/{id}/end
                           ←  { summary }
```

---

## Middleware Storage Contract

When a session completes (either max questions reached or `/end` called), this backend automatically POSTs the following JSON to `MIDDLEWARE_URL`:

```json
{
  "session_id":            "550e8400-...",
  "candidate_name":        "Sayak",
  "target_role":           "Backend Developer / AI Engineer",
  "experience_level":      "Student",
  "work_experience":       "0 years",
  "confidence_level":      "Low",
  "completed_at":          "2025-11-01T14:32:00+00:00",
  "duration_minutes":      20,
  "overall_score":         7.5,
  "total_questions":       8,
  "strong_topics":         "Python / Backend, Projects",
  "weak_topics":           "System Design",
  "hiring_recommendation": "Yes",
  "summary":               "Sayak demonstrated strong...",
  "source":                "ai_interviewer",
  "questions": [
    {
      "question_number": 1,
      "question":        "Introduce yourself.",
      "topic":           "Introduction",
      "difficulty":      "easy",
      "user_answer":     "I am Sayak...",
      "score":           8,
      "strengths":       "Clear introduction...",
      "weaknesses":      "Could mention projects earlier...",
      "feedback":        "Good concise intro...",
      "voice_review":    "### 🎙️ Voice Delivery Review\n\nConfidence: Medium..."
    }
  ]
}
```

`voice_review` is `null` for text-only answers.  
The middleware should respond with `{ "status": "ok", "record_id": "<db-id>" }`.

**Retry policy:** up to 4 attempts with exponential backoff (1s → 2s → 4s → 8s) on network errors. 4xx responses are not retried. If all retries fail, a warning is logged but the interview result is still returned to the user — the session is never lost from the candidate's perspective.

---

## Error Response Format

All errors follow the same JSON envelope:

```json
{
  "error":   "SESSION_NOT_FOUND",
  "message": "Session '550e8400-...' not found.",
  "path":    "/interview/550e8400-.../status"
}
```

| `error` code | HTTP | Meaning |
|---|---|---|
| `SESSION_NOT_FOUND` | 404 | Invalid or expired session ID |
| `SESSION_ALREADY_COMPLETED` | 409 | Session is done, no more answers |
| `SESSION_CREATION_FAILED` | 500 | Could not create session |
| `AGENT_ERROR` | 502 | LLM agent failed |
| `AGENT_TIMEOUT` | 504 | LLM did not respond in time |
| `TRANSCRIPTION_FAILED` | 422 | Audio produced no transcript |
| `VOICE_AGENT_FAILED` | 502 | ElevenLabs agent error |
| `VOICE_AGENT_NOT_CONFIGURED` | 503 | `ELEVENLABS_AGENT_ID` missing |
| `TTS_FAILED` | 502 | Text-to-speech conversion failed |
| `MIDDLEWARE_DISPATCH_FAILED` | 502 | Session storage POST failed (non-fatal) |
| `INVALID_INPUT` | 422 | Empty answer, oversized audio, etc. |
| `VALIDATION_ERROR` | 422 | Malformed request body |
| `INTERNAL_SERVER_ERROR` | 500 | Unexpected unhandled error |

---

## File Structure

```
ai_interviewer/
├── __init__.py
├── exceptions.py          ← typed error hierarchy
├── logger.py              ← get_logger() for all modules
├── models.py              ← all Pydantic models
│                             (agent outputs, session state,
│                              API schemas, middleware payload)
├── interviewer_agent.py   ← InterviewerAgent class
│                             builds + runs all 3 sub-agents
│                             caches last_summary for dispatch
├── session_store.py       ← SessionStore (thread-safe, in-memory)
├── resume.py              ← ResumeLoader (local testing only)
├── routers/
│   ├── session.py         ← POST /interview/start
│   │                         POST /interview/{id}/end
│   │                         GET  /interview/{id}/status
│   └── answer.py          ← POST /interview/answer/text
│                             POST /interview/answer/voice
│                             POST /interview/answer/tts
└── services/
    ├── tts.py             ← TTSService (ElevenLabs TTS, retry)
    ├── voice_agent.py     ← VoiceAgentService (ElevenLabs
    │                         Conversational Agent, FileAudioInterface)
    └── middleware_client.py ← MiddlewareClient (httpx + tenacity retry)
```