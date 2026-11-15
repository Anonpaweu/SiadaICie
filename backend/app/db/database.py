"""Database initialization and access for PKP Seat Optimizer."""
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional


DB_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = DB_DIR / "schema.sql"
DEFAULT_DB_PATH = DB_DIR / "pkp_optimizer.db"


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Return a SQLite connection, creating the DB file if needed."""
    path = db_path or str(DEFAULT_DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """Create tables from schema.sql if they do not exist yet."""
    conn = get_connection(db_path)
    try:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            schema_sql = f.read()
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()


def get_cached_item(cache_key: str, ttl_seconds: int = 600, db_path: Optional[str] = None) -> Optional[Any]:
    """Retrieve an item from cached_seatmaps if it hasn't expired."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT data_json, created_at
            FROM cached_seatmaps
            WHERE cache_key = ?
            """,
            (cache_key,),
        )
        row = cur.fetchone()
        if row:
            created_at = row["created_at"]
            # created_at can be float/int timestamp or string
            now = time.time()
            if isinstance(created_at, (int, float)):
                age = now - created_at
            else:
                try:
                    age = now - float(created_at)
                except (ValueError, TypeError):
                    age = 0.0

            if age < ttl_seconds:
                return json.loads(row["data_json"])
        return None
    finally:
        conn.close()


def set_cached_item(cache_key: str, data: Any, db_path: Optional[str] = None) -> None:
    """Store or update an item in cached_seatmaps and segment_cache with a precise UNIX timestamp."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        data_json = json.dumps(data, ensure_ascii=False)
        now = time.time()
        cur.execute(
            """
            INSERT INTO cached_seatmaps (cache_key, data_json, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                data_json = excluded.data_json,
                created_at = excluded.created_at
            """,
            (cache_key, data_json, now),
        )
        cur.execute(
            """
            INSERT INTO segment_cache (cache_key, data_json, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                data_json = excluded.data_json,
                created_at = excluded.created_at
            """,
            (cache_key, data_json, now),
        )
        conn.commit()
    finally:
        conn.close()


def get_segment_cached_item(cache_key: str, ttl_seconds: int = 600, db_path: Optional[str] = None) -> Optional[Any]:
    """Retrieve an item from segment_cache if it hasn't expired."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT data_json, created_at
            FROM segment_cache
            WHERE cache_key = ?
            """,
            (cache_key,),
        )
        row = cur.fetchone()
        if row:
            created_at = row["created_at"]
            now = time.time()
            if isinstance(created_at, (int, float)):
                age = now - created_at
            else:
                try:
                    age = now - float(created_at)
                except (ValueError, TypeError):
                    age = 0.0

            if age < ttl_seconds:
                return json.loads(row["data_json"])
        return None
    finally:
        conn.close()


def set_segment_cached_item(cache_key: str, data: Any, db_path: Optional[str] = None) -> None:
    """Store or update an item in segment_cache with a precise UNIX timestamp."""
    set_cached_item(cache_key, data, db_path=db_path)
