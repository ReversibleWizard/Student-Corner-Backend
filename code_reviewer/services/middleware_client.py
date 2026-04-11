"""
code_reviewer/services/middleware_client.py

HTTP client for communicating with the Spring Boot middleware.

CHANGE SUMMARY (MongoDB migration)
------------------------------------
REMOVED:
  - dispatch_review_result()  — review session data is now stored in MongoDB
                                 by session_repository; no longer sent here.
  - Any calls that POST completed review payloads to MIDDLEWARE_URL/sessions
    or MIDDLEWARE_URL/reviews.

KEPT:
  - forward_request()   — generic passthrough for non-storage middleware ops.
  - verify_token()      — JWT validation against the middleware.
  - get_headers()       — standard auth header builder.

DO NOT delete this file — it is imported by other parts of the agent.
"""

import os
import logging

import httpx

log = logging.getLogger("code_reviewer.middleware_client")

MIDDLEWARE_URL        = os.getenv("MIDDLEWARE_URL", "")
MIDDLEWARE_AUTH_TOKEN = os.getenv("MIDDLEWARE_AUTH_TOKEN", "")
REQUEST_TIMEOUT       = float(os.getenv("MIDDLEWARE_TIMEOUT", "10"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {MIDDLEWARE_AUTH_TOKEN}",
        "Content-Type":  "application/json",
    }


# ── Auth ──────────────────────────────────────────────────────────────────────

async def verify_token(token: str) -> dict | None:
    """Validate a JWT with the Spring Boot middleware."""
    if not MIDDLEWARE_URL:
        log.warning("MIDDLEWARE_URL not configured — token verification skipped.")
        return None

    url = f"{MIDDLEWARE_URL}/auth/verify"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(url, json={"token": token}, headers=get_headers())
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        log.warning("Token verification failed  status=%s", exc.response.status_code)
        return None
    except Exception as exc:
        log.error("Middleware error during token verification: %s", exc)
        return None


# ── Generic forwarding ────────────────────────────────────────────────────────

async def forward_request(
    path: str,
    payload: dict,
    method: str = "POST",
) -> dict | None:
    """
    Forward a request to the middleware for non-storage purposes
    (audit logs, notifications, etc.).

    Session / review data storage goes to MongoDB — not here.
    """
    if not MIDDLEWARE_URL:
        log.debug("MIDDLEWARE_URL not set — skipping forward_request to %s", path)
        return None

    url = f"{MIDDLEWARE_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            if method.upper() == "POST":
                resp = await client.post(url, json=payload, headers=get_headers())
            elif method.upper() == "PUT":
                resp = await client.put(url, json=payload, headers=get_headers())
            else:
                resp = await client.get(url, headers=get_headers())
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        log.warning(
            "Middleware request failed  status=%s  path=%s", exc.response.status_code, path
        )
    except Exception as exc:
        log.error("Middleware request error  path=%s  error=%s", path, exc)

    return None