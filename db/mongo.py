"""
db/mongo.py — Async MongoDB connection using Motor (singleton pattern).

Usage:
    from db.mongo import init_db, close_db, get_db

    # In FastAPI lifespan:
    await init_db()   # startup
    await close_db()  # shutdown

    # Anywhere else:
    db = get_db()     # returns the active AsyncIOMotorDatabase instance
"""

import os
import logging
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

log = logging.getLogger("db.mongo")

# ── Module-level singletons ───────────────────────────────────────────────────

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


# ── Public helpers ────────────────────────────────────────────────────────────

async def init_db() -> None:
    """
    Open the Motor connection and ping the server to confirm reachability.
    Call once at application startup (inside FastAPI lifespan).
    """
    global _client, _db

    uri  = os.getenv("MONGODB_URI")
    name = os.getenv("MONGODB_DB_NAME", "students_corner")

    if not uri:
        raise RuntimeError(
            "MONGODB_URI is not set. "
            "Add it to your .env file before starting the server."
        )

    log.info("Connecting to MongoDB  db=%s  uri=%.40s…", name, uri)

    _client = AsyncIOMotorClient(
        uri,
        serverSelectionTimeoutMS=5_000,   # fail fast on misconfiguration
        connectTimeoutMS=5_000,
        socketTimeoutMS=10_000,
    )

    # Ping verifies the URI and credentials before the first real request.
    await _client.admin.command("ping")

    _db = _client[name]
    log.info("MongoDB connection established — database: %s", name)

    # Ensure indexes exist (idempotent — safe to call on every startup)
    await _ensure_indexes()


async def close_db() -> None:
    """
    Close the Motor connection pool.
    Call once at application shutdown (inside FastAPI lifespan).
    """
    global _client, _db
    if _client is not None:
        _client.close()
        _client = None
        _db = None
        log.info("MongoDB connection closed.")


def get_db() -> AsyncIOMotorDatabase:
    """
    Return the active database handle.

    Raises RuntimeError if called before init_db() has completed successfully.
    """
    if _db is None:
        raise RuntimeError(
            "MongoDB is not initialised. "
            "Ensure init_db() is awaited during application startup."
        )
    return _db


# ── Index bootstrap ───────────────────────────────────────────────────────────

async def _ensure_indexes() -> None:
    """
    Create indexes on the sessions collection if they do not already exist.
    All index-creation calls are idempotent.
    """
    db = get_db()
    col = db["sessions"]

    # Primary look-up: by session_id (unique)
    await col.create_index("session_id", unique=True, name="idx_session_id")

    # Resume look-up: find in-progress sessions for a given user + app
    await col.create_index(
        [("user_id", 1), ("app_id", 1), ("status", 1)],
        name="idx_user_app_status",
    )

    # Historical look-up: all sessions for a user
    await col.create_index("user_id", name="idx_user_id")

    # TTL index: auto-delete stale in-progress sessions after 24 h
    # (adjust expireAfterSeconds to suit your retention policy)
    await col.create_index(
        "last_activity_at",
        expireAfterSeconds=86_400,
        name="idx_ttl_last_activity",
        partialFilterExpression={"status": "in_progress"},
    )

    log.info("MongoDB session indexes verified.")