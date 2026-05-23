# TODO Person 3
# create_token(label) -> raw_token  (store hashed in DB)
# verify_token(raw_token) -> identity or None
# require_auth(request) -> identity or raise 401

"""
auth.py — Bearer token management for the Forge registry.
"""

import hashlib
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from fastapi import Header, HTTPException

_db_path: str = ""


def init_auth(db_path: str) -> None:
    global _db_path
    _db_path = db_path
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tokens (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                label       TEXT    NOT NULL,
                token_hash  TEXT    NOT NULL UNIQUE,
                created_at  TEXT    NOT NULL
            )
        """)
        conn.commit()



def _get_conn() -> sqlite3.Connection:
    if not _db_path:
        raise RuntimeError("auth not initialised — call init_auth() first")
    return sqlite3.connect(_db_path)


def _hash(raw_token: str) -> str:
    """Return the SHA-256 hex digest of a raw token string."""
    return hashlib.sha256(raw_token.encode()).hexdigest()




def create_token(label: str) -> str:
    """Generate a new bearer token, persist its hash, and return the raw token.

    The raw token is returned exactly once

    Args:
        label: Human-readable name for this token (e.g. "ci-runner", "alice").

    Returns:
        The raw token string, e.g. ``forge-4a8f3c…``.
    """
    raw_token = "forge-" + secrets.token_hex(32)
    token_hash = _hash(raw_token)
    created_at = datetime.now(timezone.utc).isoformat()

    with _get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO tokens (label, token_hash, created_at) VALUES (?, ?, ?)",
                (label, token_hash, created_at),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return create_token(label)

    return raw_token


def verify_token(raw_token: str) -> Optional[str]:
    """Verify a raw bearer token and return its label, or None if invalid.

    Args:
        raw_token: The token string exactly as sent in the Authorization header.

    Returns:
        The label associated with the token, or ``None`` if the token is not
        found in the database.
    """
    token_hash = _hash(raw_token)
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT label FROM tokens WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
    return row[0] if row else None


def list_tokens() -> list[dict]:
    """Return all tokens (label + created_at only — never the hash).

    Useful for the ``forge token ls`` CLI command.
    """
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT label, created_at FROM tokens ORDER BY created_at"
        ).fetchall()
    return [{"label": row[0], "created_at": row[1]} for row in rows]


def revoke_token(label: str) -> bool:
    """Delete all tokens with the given label.

    Returns True if at least one token was removed, False if none matched.
    """
    with _get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM tokens WHERE label = ?", (label,)
        )
        conn.commit()
    return cursor.rowcount > 0



def require_auth(authorization: str = Header(...)) -> str:
    """FastAPI dependency — validates the Bearer token on every write request.

    Usage::

        @app.post("/artifacts/{name}/{version}")
        def upload(name: str, version: str, publisher: str = Depends(require_auth)):
            ...

    Returns:
        The token label (identity) of the authenticated caller.

    Raises:
        HTTPException 401: if the header is missing, malformed, or unknown.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authorization header must be: Bearer <token>",
        )

    raw_token = authorization[len("Bearer "):]
    identity = verify_token(raw_token)

    if identity is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or unknown token",
        )

    return identity


if __name__ == "__main__":
    import sys
    import os
    import yaml

    if len(sys.argv) < 2:
        print("Usage: python -m registry.auth [create|list|revoke] [args]")
        sys.exit(1)

    cmd = sys.argv[1]

    # Load config to get db_path
    config_path = os.environ.get("CONFIG_PATH", "config.yaml")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
    else:
        config = {}
    
    registry_config = config.get("registry", {})
    db_path = registry_config.get("db_path", "./data/forge.db")

    # Ensure parent directories exist
    from pathlib import Path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    init_auth(db_path)

    if cmd in ("create", "create-token"):
        # Support both:  create <label>  AND  create-token --publisher <label>
        label = None
        if "--publisher" in sys.argv:
            idx = sys.argv.index("--publisher")
            if idx + 1 < len(sys.argv):
                label = sys.argv[idx + 1]
        if not label:
            # Positional: python -m registry.auth create <label>
            for arg in sys.argv[2:]:
                if not arg.startswith("-"):
                    label = arg
                    break
        if not label:
            print("Usage: python -m registry.auth create <label>")
            print("   or: python -m registry.auth create-token --publisher <label>")
            sys.exit(1)
        token = create_token(label)
        print(f"Created token for '{label}': {token}")
        print(f"Token: {token}")
        print("IMPORTANT: Save this token. It will not be shown again.")
    elif cmd == "list":
        tokens = list_tokens()
        print("Active tokens:")
        for t in tokens:
            print(f"- {t['label']} (created: {t['created_at']})")
    elif cmd == "revoke":
        if len(sys.argv) < 3:
            print("Usage: python -m registry.auth revoke <label>")
            sys.exit(1)
        label = sys.argv[2]
        if revoke_token(label):
            print(f"Revoked token(s) for '{label}'")
        else:
            print(f"No token found for '{label}'")
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)

