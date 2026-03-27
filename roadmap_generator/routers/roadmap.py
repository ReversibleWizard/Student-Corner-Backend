from fastapi import APIRouter, HTTPException, BackgroundTasks
import json
from agents import Runner
from ..models import RoadmapInitRequest, ChatRequest, RoadmapResponse, TerminateResponse
from ..roadmap_agent import claude_client, GENERATOR_SYSTEM, UPDATER_SYSTEM, validator_agent
from ..session_store import store
from ..logger import logger
from ..exceptions import RoadmapValidationError, SessionNotFoundError
from ..services.middleware import send_to_middleware, fetch_from_middleware
from ..services.tools import build_roadmap_and_graph

router = APIRouter(prefix="/roadmap", tags=["Roadmap Generator"])

async def _validate_json_output(raw_output: str) -> dict:
    """Uses the OpenAI Agent SDK to validate the output."""
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
        logger.error(f"Validation failed on output: {clean_json}")
        raise RoadmapValidationError("Validator failed to output parsable JSON.")

@router.post("/generate", response_model=RoadmapResponse)
async def generate_initial_roadmap(request: RoadmapInitRequest):
    session_id = store.create_session()
    
    try:
        logger.info(f"Generating initial roadmap for session {session_id}")
        
        # 1. Run the deterministic Python tool first
        base_roadmap_json = build_roadmap_and_graph(request.goal, request.known_skills)
        if "error" in base_roadmap_json:
            raise HTTPException(status_code=404, detail=json.loads(base_roadmap_json)["error"])

        # 2. Use Claude via direct API call to finalize it
        user_message = f"Please finalize this base roadmap into the required JSON schema:\n{base_roadmap_json}"
        
        response = claude_client.chat.completions.create(
            model="claude-sonnet-4-6",
            messages=[
                {"role": "system", "content": GENERATOR_SYSTEM},
                {"role": "user", "content": user_message},
            ],
        )
        raw_output = response.choices[0].message.content

        # 3. Validate using the OpenAI SDK
        final_data = await _validate_json_output(raw_output)
            
        store.update_roadmap(session_id, final_data, user_message=f"Initial Generation: {request.goal}")
        session = store.get_session(session_id)
        
        return {**final_data, "session_id": session_id, "version": session["version"]}

    except Exception as e:
        store.delete_session(session_id)
        logger.error(f"Generation failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/chat/{session_id}", response_model=RoadmapResponse)
async def chat_and_update_roadmap(session_id: str, request: ChatRequest):
    try:
        session = store.get_session(session_id)
        current_roadmap = session["roadmap"]
        
        logger.info(f"Updating roadmap for session {session_id}")
        
        # 1. Direct API call to Claude
        user_message = f"CURRENT ROADMAP:\n{json.dumps(current_roadmap)}\n\nUSER REQUEST: {request.message}"
        
        response = claude_client.chat.completions.create(
            model="claude-sonnet-4-6",
            messages=[
                {"role": "system", "content": UPDATER_SYSTEM},
                {"role": "user", "content": user_message},
            ],
        )
        raw_output = response.choices[0].message.content
        
        # 2. Validate
        final_data = await _validate_json_output(raw_output)
        
        store.update_roadmap(session_id, final_data, user_message=request.message)
        session = store.get_session(session_id)
        
        return {**final_data, "session_id": session_id, "version": session["version"]}

    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Update failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/terminate/{session_id}", response_model=TerminateResponse)
async def terminate_session(session_id: str, background_tasks: BackgroundTasks):
    try:
        session = store.get_session(session_id)
        logger.info(f"Terminating session {session_id}")
        
        background_tasks.add_task(
            send_to_middleware,
            session_id=session_id,
            final_roadmap=session["roadmap"],
            chat_history=session["chat_history"],
            version=session["version"]
        )
        
        store.delete_session(session_id)
        return {"status": "success", "message": f"Session {session_id} terminated."}

    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.post("/restore/{session_id}", response_model=RoadmapResponse)
async def restore_old_session(session_id: str):
    """Fetches a terminated session from middleware and re-activates it for chatting."""
    
    # 1. Check if it's mysteriously still in active memory first
    try:
        session = store.get_session(session_id)
        logger.info(f"Session {session_id} is already active.")
        return {**session["roadmap"], "session_id": session_id, "version": session["version"]}
    except SessionNotFoundError:
        pass # Expected behavior, we need to fetch it
        
    # 2. Fetch it from the middleware
    try:
        history_data = await fetch_from_middleware(session_id)
        
        if not history_data:
            raise HTTPException(status_code=404, detail="Session not found in active memory or history.")
            
        # 3. Load it back into the backend's active memory
        store.restore_session(
            session_id=session_id,
            roadmap=history_data["roadmap"],
            chat_history=history_data.get("chat_history", []),
            version=history_data.get("final_version", 1)
        )
        
        # 4. Return it so the frontend can render it
        return {**history_data["roadmap"], "session_id": session_id, "version": history_data.get("final_version", 1)}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to restore session: {str(e)}")