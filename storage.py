import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

DDL = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    service TEXT,
    src_ip TEXT,
    src_port INTEGER,
    username TEXT,
    password TEXT,
    detail TEXT,
    extra TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_src_ip ON events(src_ip);
"""


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class Database:
    def __init__(self, path):
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._conn.executescript(DDL)
            self._conn.commit()

    def log(self, kind, service=None, src_ip=None, src_port=None,
            username=None, password=None, detail=None, extra=None):
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (ts, kind, service, src_ip, src_port, username, password, detail, extra) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    utcnow(), kind, service, src_ip, src_port,
                    username, password, detail,
                    json.dumps(extra) if extra else None,
                ),
            )
            self._conn.commit()

    def recent(self, limit=50):
        rows = self._query(
            "SELECT id, ts, kind, service, src_ip, src_port, username, password, detail "
            "FROM events ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]

    def stats(self):
        totals = {
            "connections": self._scalar("SELECT COUNT(*) FROM events WHERE kind='connection'"),
            "unique_ips": self._scalar(
                "SELECT COUNT(DISTINCT src_ip) FROM events WHERE src_ip IS NOT NULL"
            ),
            "auth_attempts": self._scalar("SELECT COUNT(*) FROM events WHERE kind='auth'"),
            "logins": self._scalar(
                "SELECT COUNT(*) FROM events WHERE kind='login' AND detail='accepted'"
            ),
            "commands": self._scalar("SELECT COUNT(*) FROM events WHERE kind='command'"),
            "downloads": self._scalar("SELECT COUNT(*) FROM events WHERE kind='download'"),
            "http_requests": self._scalar(
                "SELECT COUNT(*) FROM events WHERE kind='http_request'"
            ),
        }
        return {
            "totals": totals,
            "top_ips": self._top(
                "SELECT src_ip AS label, COUNT(*) AS count FROM events "
                "WHERE src_ip IS NOT NULL GROUP BY src_ip ORDER BY count DESC LIMIT 10"
            ),
            "top_usernames": self._top(
                "SELECT username AS label, COUNT(*) AS count FROM events "
                "WHERE kind='auth' AND username IS NOT NULL AND username != '' "
                "GROUP BY username ORDER BY count DESC LIMIT 10"
            ),
            "top_passwords": self._top(
                "SELECT password AS label, COUNT(*) AS count FROM events "
                "WHERE kind='auth' AND password IS NOT NULL AND password != '' "
                "GROUP BY password ORDER BY count DESC LIMIT 10"
            ),
            "top_commands": self._top(
                "SELECT detail AS label, COUNT(*) AS count FROM events "
                "WHERE kind='command' AND detail IS NOT NULL "
                "GROUP BY detail ORDER BY count DESC LIMIT 10"
            ),
            "timeline": self._timeline(),
        }

    def _top(self, query):
        return [{"label": r["label"], "count": r["count"]} for r in self._query(query)]

    def _timeline(self):
        rows = self._query(
            "SELECT strftime('%Y-%m-%d %H:00', ts) AS hour, COUNT(*) AS count "
            "FROM events WHERE ts >= datetime('now', '-1 day') "
            "GROUP BY hour ORDER BY hour"
        )
        return [{"hour": r["hour"], "count": r["count"]} for r in rows]

    def _query(self, query, params=()):
        with self._lock:
            return self._conn.execute(query, params).fetchall()

    def _scalar(self, query):
        row = self._query(query)
        return row[0][0] if row else 0

    def close(self):
        with self._lock:
            self._conn.close()
