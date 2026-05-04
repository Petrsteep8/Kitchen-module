import datetime
import logging
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from functools import wraps
from typing import Optional, List, Any, Callable

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sqlite_database_client_integrated")

def retry_on_lock(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        max_retries = 3
        retry_delay = 0.1

        for attempt in range(max_retries):
            try:
                return func(self, *args, **kwargs)
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) and attempt < max_retries - 1:
                    logger.warning(f"Database locked, retry {attempt + 1}/{max_retries}")
                    time.sleep(retry_delay * (attempt + 1))
                    continue
                raise
        return None
    return wrapper


class DBQueue:
    def __init__(self, db_path: str = './kitchen_gateway/queue.db'):
        self.db_path = db_path
        self._local = threading.local()
        self._lock = threading.RLock()
        self._last_time_ttl_check = datetime.datetime.now()
        self._init_database()

    def _init_database(self) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""SELECT name FROM sqlite_master WHERE type='table' AND name='event_queue'""")

            if not cursor.fetchone():
                logger.info("Database does not exist, creating...")
                cursor.execute("""
                    CREATE TABLE event_queue (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_type TEXT NOT NULL,
                        json_content TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('a_pending', 'b_completed', 'c_failed')) DEFAULT 'a_pending',
                        timestamp_added TEXT DEFAULT CURRENT_TIMESTAMP,
                        timestamp_updated TEXT DEFAULT CURRENT_TIMESTAMP)""")
                cursor.execute("""CREATE INDEX idx_status_timestamp ON event_queue(status, timestamp_added)""")
                logger.info("Database has been successfully created")
            else:
                logger.info("Database exists")

    @contextmanager
    def _get_connection(self):
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
            conn.commit()
        except sqlite3.Error as e:
            print(self.db_path)
            if conn:
                conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def _get_cursor(self):
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path, timeout=10.0)
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.cursor = self._local.conn.cursor()
        return self._local.cursor

    def _close_thread_connection(self):
        if hasattr(self._local, 'conn') and self._local.conn:
            try:
                self._local.conn.close()
            except:
                pass
            self._local.conn = None
            self._local.cursor = None

    @retry_on_lock
    def update_completed(self, idf: int) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE event_queue SET status = 'b_completed' WHERE id = ?",(idf,))
            cursor.execute("UPDATE event_queue SET timestamp_updated = CURRENT_TIMESTAMP")
            logger.info(f"Updated event {idf} to completed")

    @retry_on_lock
    def update_failed(self, idf: int) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE event_queue SET status = 'c_failed' WHERE id = ?",(idf,))
            cursor.execute("UPDATE event_queue SET timestamp_updated = CURRENT_TIMESTAMP")
            logger.info(f"Updated event {idf} to failed")

    @retry_on_lock
    def add_database_event(self, pkt: Any) -> Optional[int]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            timestamp_added = datetime.datetime.now().isoformat()

            try:
                cursor.execute("""INSERT INTO event_queue (event_type, json_content, timestamp_added, timestamp_updated) 
                    VALUES (?, ?, ?, ?)""",(pkt['event_type'], str(pkt), timestamp_added, timestamp_added))
                event_id = cursor.lastrowid
                logger.info(f"Added event {event_id} to queue")
                return event_id
            except sqlite3.Error as e:
                logger.error(f"INSERT error: {e}")
                raise

    @retry_on_lock
    def get_database_events(self, limit: int = 5) -> List[sqlite3.Row]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""SELECT * FROM event_queue WHERE status != 'b_completed' ORDER BY timestamp_updated
            LIMIT ?""", (limit,))
            return cursor.fetchall()

    @retry_on_lock
    def check_size_and_cleanup(self, max_size_mb: float = 5000, max_age_hours: int = 1) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            logger.info("Checking size and cleaning up started")

            cursor.execute("SELECT page_count * page_size as size FROM pragma_page_count(), pragma_page_size()")
            size_bytes = cursor.fetchone()[0]
            size_mb = size_bytes / (1024 * 1024)

            logger.info(f"Current queue size: {size_mb}MB")
            cutoff_time = datetime.datetime.now() - datetime.timedelta(hours=max_age_hours)

            if size_mb > max_size_mb:
                logger.warning(f"Queue size {size_mb}MB exceeds limit {max_size_mb}MB, cleaning up")

                cursor.execute("""DELETE FROM event_queue WHERE event_type = ACTION_EVENT OR
                    event_type = HYGIENE_CHECK OR event_type = ENVIRONMENT_EVENT
                    OR timestamp_added < ? OR status = 'b_completed'"""
                )
                conn.commit()

                deleted_count = cursor.rowcount
                logger.info(f"Deleted {deleted_count} records")
                cursor.execute("VACUUM")
                logger.info("Database vacuumed")
            else:
                logger.warning(f"Queue TTL check (max TTL = {max_age_hours} hour(s))")

                cursor.execute("""DELETE FROM event_queue WHERE timestamp_added < ? OR status = 'b_completed'""",
                    (cutoff_time.isoformat(),)
                )
                conn.commit()

                deleted_count = cursor.rowcount
                logger.info(f"Deleted {deleted_count} records")
                cursor.execute("VACUUM")
                logger.info("Database vacuumed")

            logger.info("Checking size and cleaning up finished")

    def cleanup_thread_connection(self):
        self._close_thread_connection()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._close_thread_connection()


# TESTS
if __name__ == '__main__':
    db_queue = DBQueue(db_path='../queue.db')
    db_queue.add_database_event(pkt={"event_type": "SANITARY_EVENT", "action_id": str(uuid.uuid4())})
    db_queue.check_size_and_cleanup()