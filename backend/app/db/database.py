"""Database initialization and access for PKP Seat Optimizer."""
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional, Set

logger = logging.getLogger(__name__)

DB_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = DB_DIR / "schema.sql"
DEFAULT_DB_PATH = DB_DIR / "pkp_optimizer.db"

_INITIALIZED_DBS: Set[str] = set()


def _resolve_db_path(db_path: Optional[str] = None) -> str:
    return str(Path(db_path).resolve()) if db_path else str(DEFAULT_DB_PATH.resolve())


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Return a SQLite connection, creating the DB file if needed."""
    path = db_path or str(DEFAULT_DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[str] = None, force: bool = False) -> None:
    """Create tables from schema.sql once per database path (or when force=True / file missing)."""
    resolved = _resolve_db_path(db_path)
    if not force and resolved in _INITIALIZED_DBS and Path(resolved).exists():
        return

    conn = get_connection(db_path)
    try:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            schema_sql = f.read()
        conn.executescript(schema_sql)
        conn.commit()
        _INITIALIZED_DBS.add(resolved)
    finally:
        conn.close()


def ensure_stations_seeded(db_path: Optional[str] = None) -> int:
    """Ensure DB schema exists and seed official stations if the stations table is empty."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM stations")
        count = cur.fetchone()[0]
        if count > 0:
            return count
    finally:
        conn.close()

    try:
        from backend.app.db.seed_stations import seed_stations_table

        seeded = seed_stations_table(db_path=db_path)
        logger.info(f"[DB] Auto-seeded {seeded} stations on startup.")
        return seeded
    except Exception as e:
        logger.warning(f"[DB] Could not auto-seed stations table: {e}")
        return 0


def cleanup_expired_cache(ttl_seconds: int = 600, db_path: Optional[str] = None) -> int:
    """Delete expired cache entries from cached_seatmaps to prevent unbounded DB growth."""
    init_db(db_path)
    cutoff = time.time() - max(0, ttl_seconds)
    conn = get_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM cached_seatmaps WHERE typeof(created_at) IN ('real', 'integer') AND created_at < ?",
            (cutoff,),
        )
        deleted = cur.rowcount
        conn.commit()
        return deleted
    finally:
        conn.close()


def get_cached_item(cache_key: str, ttl_seconds: int = 600, db_path: Optional[str] = None) -> Optional[Any]:
    """Retrieve an item from cached_seatmaps if it hasn't expired; evicts expired entries."""
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

            # Opportunistically evict expired entry
            if ttl_seconds > 0:
                cur.execute("DELETE FROM cached_seatmaps WHERE cache_key = ?", (cache_key,))
                conn.commit()
        return None
    finally:
        conn.close()


def set_cached_item(cache_key: str, data: Any, db_path: Optional[str] = None) -> None:
    """Store or update an item in cached_seatmaps with a precise UNIX timestamp."""
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
        # Opportunistically purge stale items older than 24h to keep DB small
        cur.execute(
            "DELETE FROM cached_seatmaps WHERE typeof(created_at) IN ('real', 'integer') AND created_at < ?",
            (now - 86400,),
        )
        conn.commit()
    finally:
        conn.close()


def get_segment_cached_item(cache_key: str, ttl_seconds: int = 600, db_path: Optional[str] = None) -> Optional[Any]:
    """Alias for get_cached_item using the single unified cached_seatmaps table."""
    return get_cached_item(cache_key, ttl_seconds=ttl_seconds, db_path=db_path)


def set_segment_cached_item(cache_key: str, data: Any, db_path: Optional[str] = None) -> None:
    """Alias for set_cached_item using the single unified cached_seatmaps table."""
    set_cached_item(cache_key, data, db_path=db_path)
