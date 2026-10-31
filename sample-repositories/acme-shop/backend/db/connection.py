"""Database access: pool setup and transaction helper."""
import sqlite3
import queue
import logging

log = logging.getLogger("acme.db")
_POOL: "queue.Queue[sqlite3.Connection] | None" = None


class DatabaseConnectionError(RuntimeError):
    pass


def init_database_pool(database_url: str, size: int = 5) -> None:
    """Open the connection pool once at application start-up."""
    global _POOL
    _POOL = queue.Queue(maxsize=size)
    for _ in range(size):
        try:
            conn = sqlite3.connect(database_url, check_same_thread=False)
        except sqlite3.Error as exc:
            raise DatabaseConnectionError(f"cannot open database: {exc}") from exc
        conn.row_factory = sqlite3.Row
        _POOL.put(conn)
    log.info("database pool ready with %d connections", size)


def get_connection(timeout: float = 5.0) -> sqlite3.Connection:
    if _POOL is None:
        raise DatabaseConnectionError("pool has not been initialised")
    return _POOL.get(timeout=timeout)


def release_connection(conn: sqlite3.Connection) -> None:
    _POOL.put(conn)


def run_in_transaction(work):
    """Run `work(conn)`; commit on success, roll everything back if it raises."""
    conn = get_connection()
    try:
        result = work(conn)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        log.exception("transaction failed and was rolled back")
        raise
    finally:
        release_connection(conn)
