"""
ai_interviewer/services/middleware_client.py

HTTP client for communicating with the Spring Boot middleware.

CHANGE SUMMARY (MongoDB migration)
------------------------------------
REMOVED:
  - dispatch_session_result()  — session data is now stored in MongoDB by
                                  session_repository; no longer sent here.
  - Any calls that POST completed session payloads to MIDDLEWARE_URL/sessions.

KEPT:
  - forward_request()   — forwards auth-bearing requests from the middleware
                          to FastAPI agent endpoints when needed.
  - verify_token()      — validates JWT tokens issued by the middleware.
  - get_headers()       — builds the standard auth header dict.

If your project does not use forward_request() or verify_token(), this file
can be reduced to just the header helper.  The important constraint is:
DO NOT delete the file — other services may import from it.
"""

import os
import logging

import httpx

log = logging.getLogger("ai_interviewer.middleware_client")

MIDDLEWARE_URL        = os.getenv("MIDDLEWARE_URL", "")
MIDDLEWARE_AUTH_TOKEN = os.getenv("MIDDLEWARE_AUTH_TOKEN", "")
REQUEST_TIMEOUT       = float(os.getenv("MIDDLEWARE_TIMEOUT", "10"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_headers() -> dict[str, str]:
    """Return the standard auth header for outbound middleware requests."""
    return {
        "Authorization": f"Bearer {MIDDLEWARE_AUTH_TOKEN}",
        "Content-Type":  "application/json",
    }


# ── Auth forwarding ───────────────────────────────────────────────────────────

async def verify_token(token: str) -> dict | None:
    """
    Validate a JWT with the Spring Boot middleware.

    Returns the decoded claims dict on success, or None on failure.
    """
    if not MIDDLEWARE_URL:
        log.warning("MIDDLEWARE_URL not configured — token verification skipped.")
        return None

    url = f"{MIDDLEWARE_URL}/auth/verify"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.post(
                url,
                json={"token": token},
                headers=get_headers(),
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        log.warning(
            "Token verification failed  status=%s  url=%s",
            exc.response.status_code, url,
        )
        return None
    except Exception as exc:
        log.error("Middleware request error during token verification: %s", exc)
        return None


async def forward_request(
    path: str,
    payload: dict,
    method: str = "POST",
) -> dict | None:
    """
    Generic helper to forward a request to the middleware.

    Use this for operations that still require middleware involvement
    (e.g. writing audit logs, triggering notifications).

    Session storage is NOT one of those operations — it is handled entirely
    by MongoDB via session_repository.
    """
    if not MIDDLEWARE_URL:
        log.debug("MIDDLEWARE_URL not set — skipping forward_request to %s", path)
        return None

    url = f"{MIDDLEWARE_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            if method.upper() == "POST":
                response = await client.post(url, json=payload, headers=get_headers())
            elif method.upper() == "PUT":
                response = await client.put(url, json=payload, headers=get_headers())
            else:
                response = await client.get(url, headers=get_headers())

            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        log.warning(
            "Middleware request failed  status=%s  path=%s", exc.response.status_code, path
        )
    except Exception as exc:
        log.error("Middleware request error  path=%s  error=%s", path, exc)

    return None