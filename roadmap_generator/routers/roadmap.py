"""
roadmap_generator/routers/roadmap.py

MIGRATION NOTE
--------------
REMOVED:
  - send_to_middleware()   — terminate now persists directly to MongoDB
  - fetch_from_middleware() — restore now reads directly from MongoDB

CHANGED:
  /generate  — after building roadmap, calls store.db_create() + db_update_step()
  /chat      — after each update, calls store.db_update_step()
  /terminate — background task now calls store.db_complete() instead of send_to_middleware
  /restore   — fetches from MongoDB via store.db_get() instead of fetch_from_middleware

The in-memory `store` object (create_session / update_roadmap / get_session /
delete_session / restore_session) is untouched.
"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, BackgroundTasks
from agents import Runner

from ..models import RoadmapInitRequest, ChatRequest, RoadmapResponse, TerminateResponse
from ..roadmap_agent import claude_client, GENERATOR_SYSTEM, UPDATER_SYSTEM, validator_agent
from ..session_store import store
from ..logger import logger
from ..exceptions import RoadmapValidationError, SessionNotFoundError
from ..services.tools import build_roadmap_and_graph

router = APIRouter(prefix="/roadmap", tags=["Roadmap Generator"])


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _validate_json_output(raw_output: str) -> dict:
    """Use the OpenAI Agent SDK validator to clean and parse JSON output."""
    validator_prompt = f"Validate and clean this JSON: {raw_output}"
    validator_result = await Runner.run(validator_agent, input=validator_prompt)
    clean_json = validator_result.final_output.strip()

    if clean_json.startswith("```json"):
        clean_json = clean_json[7:]
    if clean_json.endswith("```"):
        clean_json = clean_json[:-3]

    try:
        return json.loads(clean_json.strip())
    except json.JSONDecodeError:
        logger.error("Validation failed on output: %s", clean_json)
        raise RoadmapValidationError("Validator failed to output parsable JSON.")


async def _persist_completion(
    session_id: str,
    final_roadmap: dict,
    chat_history: list,
    version: int,
    user_id: str = "anonymous",
) -> None:
    """
    Persist a terminated roadmap session to MongoDB.
    Designed to run as a BackgroundTasks task — errors are logged, not raised.
    """
    try:
        await store.db_complete(
            session_id,
            final_response={
                "roadmap":       final_roadmap,
                "chat_history":  chat_history,
                "final_version": version,
            },
            status="completed",
        )
        logger.info("Roadmap session persisted to MongoDB  session_id=%s", session_id)
    except Exception as exc:
        logger.error(
            "MongoDB persistence failed for roadmap session %s: %s", session_id, exc
        )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/generate", response_model=RoadmapResponse)
async def generate_initial_roadmap(request: RoadmapInitRequest):
    """
    Generate a new learning roadmap from a goal + known skills.

    Steps:
    1. Deterministic Python tool builds the base roadmap graph.
    2. Claude finalises it into the required JSON schema.
    3. OpenAI validator cleans the output.
    4. Session is created in memory + MongoDB.
    """
    session_id = store.create_session()

    try:
        logger.info("Generating initial roadmap  session_id=%s", session_id)

        # 1. Deterministic base roadmap
        base_roadmap_json = build_roadmap_and_graph(request.goal, request.known_skills)
        if "error" in base_roadmap_json:
            raise HTTPException(
                status_code=404,
                detail=json.loads(base_roadmap_json)["error"],
            )

        # 2. Claude finalisation
        user_message = (
            f"Please finalize this base roadmap into the required JSON schema:\n"
            f"{base_roadmap_json}"
        )
        response = claude_client.chat.completions.create(
            model="claude-sonnet-4-6",
            messages=[
                {"role": "system", "content": GENERATOR_SYSTEM},
                {"role": "user",   "content": user_message},
            ],
        )
        raw_output = response.choices[0].message.content

        # 3. Validate
        final_data = await _validate_json_output(raw_output)

        # 4. Update in-memory store
        store.update_roadmap(
            session_id,
            final_data,
            user_message=f"Initial Generation: {request.goal}",
        )
        session = store.get_session(session_id)

        # 5. Persist to MongoDB (non-blocking errors)
        try:
            user_id = getattr(request, "user_id", None) or "anonymous"
            await store.db_create(
                session_id = session_id,
                user_id    = user_id,
                input_data = {
                    "goal":         request.goal,
                    "known_skills": request.known_skills,
                },
            )
            await store.db_update_step(
                session_id,
                "pipeline.roadmap_generation",
                {
                    "output":    final_data,
                    "version":   session["version"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception as db_exc:
            logger.warning(
                "MongoDB persistence failed for /generate  session_id=%s: %s",
                session_id, db_exc,
            )

        return {**final_data, "session_id": session_id, "version": session["version"]}

    except HTTPException:
        store.delete_session(session_id)
        raise
    except Exception as exc:
        store.delete_session(session_id)
        logger.error("Generation failed  session_id=%s: %s", session_id, exc)
        try:
            await store.db_fail(session_id, reason=str(exc))
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/chat/{session_id}", response_model=RoadmapResponse)
async def chat_and_update_roadmap(session_id: str, request: ChatRequest):
    """
    Refine an existing roadmap via a chat instruction.
    Updates both in-memory state and MongoDB.
    """
    try:
        session        = store.get_session(session_id)
        current_roadmap = session["roadmap"]

        logger.info("Updating roadmap  session_id=%s", session_id)

        user_message = (
            f"CURRENT ROADMAP:\n{json.dumps(current_roadmap)}\n\n"
            f"USER REQUEST: {request.message}"
        )
        response = claude_client.chat.completions.create(
            model="claude-sonnet-4-6",
            messages=[
                {"role": "system", "content": UPDATER_SYSTEM},
                {"role": "user",   "content": user_message},
            ],
        )
        raw_output = response.choices[0].message.content

        final_data = await _validate_json_output(raw_output)

        store.update_roadmap(session_id, final_data, user_message=request.message)
        session = store.get_session(session_id)

        # Persist chat update step to MongoDB
        try:
            await store.db_update_step(
                session_id,
                "pipeline.chat_update",
                {
                    "message":   request.message,
                    "output":    final_data,
                    "version":   session["version"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception as db_exc:
            logger.warning(
                "MongoDB persistence failed for /chat  session_id=%s: %s",
                session_id, db_exc,
            )

        return {**final_data, "session_id": session_id, "version": session["version"]}

    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("Roadmap update failed  session_id=%s: %s", session_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/terminate/{session_id}", response_model=TerminateResponse)
async def terminate_session(session_id: str, background_tasks: BackgroundTasks):
    """
    End a roadmap session and persist the final state to MongoDB.

    Previously called send_to_middleware() — now calls store.db_complete()
    as a BackgroundTask so the response is immediate.
    """
    try:
        session = store.get_session(session_id)
        logger.info("Terminating roadmap session  session_id=%s", session_id)

        background_tasks.add_task(
            _persist_completion,
            session_id    = session_id,
            final_roadmap = session["roadmap"],
            chat_history  = session["chat_history"],
            version       = session["version"],
        )

        store.delete_session(session_id)
        return {"status": "success", "message": f"Session {session_id} terminated."}

    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/restore/{session_id}", response_model=RoadmapResponse)
async def restore_old_session(session_id: str):
    """
    Re-activate a terminated session for further chatting.

    Previously fetched from middleware — now reads from MongoDB.
    """
    # 1. Fast path — already in memory
    try:
        session = store.get_session(session_id)
        logger.info("Session %s is already active in memory.", session_id)
        return {
            **session["roadmap"],
            "session_id": session_id,
            "version":    session["version"],
        }
    except SessionNotFoundError:
        pass  # expected — need to fetch from DB

    # 2. Fetch from MongoDB
    try:
        history_data = await store.db_get(session_id)

        if not history_data:
            raise HTTPException(
                status_code=404,
                detail="Session not found in active memory or database.",
            )

        final_response = history_data.get("final_response") or {}
        roadmap        = final_response.get("roadmap") or history_data.get("roadmap", {})
        chat_history   = final_response.get("chat_history", [])
        version        = final_response.get("final_version", 1)

        # 3. Re-hydrate in-memory store
        store.restore_session(
            session_id   = session_id,
            roadmap      = roadmap,
            chat_history = chat_history,
            version      = version,
        )

        logger.info(
            "Roadmap session restored from MongoDB  session_id=%s  version=%s",
            session_id, version,
        )
        return {**roadmap, "session_id": session_id, "version": version}

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to restore session: {exc}",
        )