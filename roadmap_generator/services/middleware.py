import httpx
from ..logger import logger

MIDDLEWARE_URL = "http://localhost:8080/api/roadmap-history" # Ensure this matches your middleware URL

async def send_to_middleware(session_id: str, final_roadmap: dict, chat_history: list, version: int):
    """Sends the finalized session data to the middleware service asynchronously."""
    payload = {
        "session_id": session_id,
        "final_version": version,
        "chat_history": chat_history,
        "roadmap": final_roadmap
    }
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(MIDDLEWARE_URL, json=payload)
            response.raise_for_status()
            logger.info(f"Successfully synced session {session_id} to middleware.")
    except Exception as e:
        logger.error(f"Failed to sync session {session_id} to middleware: {str(e)}")

async def fetch_from_middleware(session_id: str) -> dict:
    """Retrieves a past session's data from the middleware."""
    # Ensure this matches your middleware's actual GET URL
    fetch_url = f"{MIDDLEWARE_URL}/{session_id}" 
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(fetch_url)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None # Not found in history
        logger.error(f"Middleware returned error {e.response.status_code}")
        raise e
    except Exception as e:
        logger.error(f"Failed to fetch session {session_id} from middleware: {str(e)}")
        raise e