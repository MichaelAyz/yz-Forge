# metadata.py — SQLite database layer for the Forge registry.

import sqlite3
import json
from datetime import datetime, timezone
from typing import Optional, Union


class ConflictError(Exception):
    pass


_db_path: str = ""

def init_db(db_path: str) -> None:
    global _db_path
    _db_path = db_path
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS artifacts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT    NOT NULL,
                version         TEXT    NOT NULL,
                sha256          TEXT    NOT NULL,
                size            INTEGER NOT NULL DEFAULT 0,
                publisher       TEXT    NOT NULL,
                deps_json       TEXT    NOT NULL,
                published_at    TEXT    NOT NULL,
                UNIQUE(name, version)
            )
        """)
        conn.commit()

        # Schema migration check: ensure size column exists if the table was previously created without it
        cursor = conn.execute("PRAGMA table_info(artifacts)")
        cols = [row[1] for row in cursor.fetchall()]
        if cols and "size" not in cols:
            conn.execute("ALTER TABLE artifacts ADD COLUMN size INTEGER NOT NULL DEFAULT 0")
            conn.commit()


def _get_conn() -> sqlite3.Connection:
    if not _db_path:
        raise RuntimeError("db not initialised — call init_db() first")
    return sqlite3.connect(_db_path)


def get_artifact(name: str, version: str) -> Optional[dict]:
    """Retrieve metadata for a specific artifact version.

    Returns:
        A dict with metadata, or None if not found.
    """
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT name, version, sha256, size, deps_json, publisher, published_at FROM artifacts WHERE name = ? AND version = ?",
            (name, version)
        ).fetchone()
        if row is None:
            return None
        
        return {
            "name": row[0],
            "version": row[1],
            "sha256": row[2],
            "size": row[3],
            "deps_json": row[4],
            "publisher": row[5],
            "published_at": row[6]
        }


def save_artifact(name: str, version: str, sha256: str, size: int, deps: Union[list, str], publisher: str) -> None: 
    """Save artifact metadata to the database.

    Raises:
        ConflictError: if an artifact with this name and version already exists.
    """
    existing_artifact = get_artifact(name, version)
    if existing_artifact is not None:
        raise ConflictError(f"Record already exists for package {name}@{version}")

    # Serialize deps to json if it's a Python list/dict
    if not isinstance(deps, str):
        deps_json = json.dumps(deps)
    else:
        deps_json = deps

    published_at = datetime.now(timezone.utc).isoformat()
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO artifacts (name, version, sha256, size, deps_json, publisher, published_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, version, sha256, size, deps_json, publisher, published_at),
        )
        conn.commit()


def list_versions(name: str) -> list[str]:
    """Return all stored versions for the given package name."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT version FROM artifacts WHERE name = ?", (name,)
        ).fetchall()
        return [row[0] for row in rows]


def artifact_exists(name: str, version: str) -> bool:
    return get_artifact(name, version) is not None