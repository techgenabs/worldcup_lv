import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import settings


def _get_database_url() -> str:
    url = settings.database_url
    # Fix Render's postgres:// to postgresql://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def _is_postgres() -> bool:
    url = _get_database_url()
    return url.startswith("postgresql://") or url.startswith("postgresql+")


def _db_path() -> Path:
    url = settings.database_url
    if not url.startswith("sqlite:///"):
        raise RuntimeError("This starter uses SQLite. Set DATABASE_URL=sqlite:///path/to.db")
    return Path(url.replace("sqlite:///", "", 1))


# ──────────────────────────────────────────────
# PostgreSQL support
# ──────────────────────────────────────────────
def _get_pg_connection():
    import psycopg2
    import psycopg2.extras
    conn = psycopg2.connect(_get_database_url())
    conn.autocommit = False
    return conn


class _PgCursor:
    """Wraps psycopg2 cursor to behave like sqlite3.Row (dict access)."""
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql, params=()):
        # Convert SQLite-style ? placeholders to PostgreSQL %s
        pg_sql = sql.replace("?", "%s")
        # Convert SQLite AUTOINCREMENT → handled by SERIAL in schema
        self._cursor.execute(pg_sql, params)
        return self

    def executescript(self, sql):
        # executescript not available in psycopg2; run statements one by one
        import re
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        for stmt in statements:
            try:
                self._cursor.execute(stmt)
            except Exception:
                pass  # skip already-existing tables/columns
        return self

    def fetchall(self):
        rows = self._cursor.fetchall()
        if rows and self._cursor.description:
            cols = [d[0] for d in self._cursor.description]
            return [dict(zip(cols, row)) for row in rows]
        return rows

    def fetchone(self):
        row = self._cursor.fetchone()
        if row and self._cursor.description:
            cols = [d[0] for d in self._cursor.description]
            return dict(zip(cols, row))
        return row

    @property
    def lastrowid(self):
        self._cursor.execute("SELECT lastval()")
        return self._cursor.fetchone()[0]


class _PgConnection:
    """Wraps psycopg2 connection to behave like sqlite3.Connection."""
    def __init__(self, conn):
        self._conn = conn
        self._cursor = conn.cursor()
        self._pg = _PgCursor(self._cursor)

    def execute(self, sql, params=()):
        self._pg.execute(sql, params)
        return self._pg

    def executescript(self, sql):
        self._pg.executescript(sql)
        return self._pg

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._cursor.close()
        self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


# ──────────────────────────────────────────────
# Unified get_db context manager
# ──────────────────────────────────────────────
@contextmanager
def get_db():
    if _is_postgres():
        raw_conn = _get_pg_connection()
        conn = _PgConnection(raw_conn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        import sqlite3
        path = _db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = OFF")
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


# ──────────────────────────────────────────────
# Helper functions (unchanged)
# ──────────────────────────────────────────────
def rows(cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]


def row(cursor) -> dict[str, Any] | None:
    item = cursor.fetchone()
    return dict(item) if item else None


def _columns(db, table: str) -> set[str]:
    if _is_postgres():
        db.execute(
            "SELECT column_name as name FROM information_schema.columns WHERE table_name = %s",
            (table,)
        )
        return {r["name"] for r in db._pg.fetchall()}
    else:
        return {item["name"] for item in db.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column(db, table: str, column: str, definition: str) -> None:
    if column not in _columns(db, table):
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


# ──────────────────────────────────────────────
# PostgreSQL schema (SERIAL instead of AUTOINCREMENT)
# ──────────────────────────────────────────────
PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    mobile TEXT UNIQUE,
    country TEXT DEFAULT 'Global',
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    is_active INTEGER NOT NULL DEFAULT 1,
    otp_code TEXT,
    otp_verified INTEGER NOT NULL DEFAULT 0,
    last_login_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tournaments (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    sport TEXT NOT NULL DEFAULT 'football',
    country TEXT DEFAULT 'Global',
    status TEXT NOT NULL DEFAULT 'draft',
    start_date TEXT,
    end_date TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS teams (
    id SERIAL PRIMARY KEY,
    tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    country TEXT NOT NULL,
    flag TEXT DEFAULT '🏆',
    ranking INTEGER DEFAULT 50,
    home_advantage REAL DEFAULT 0,
    strength_score REAL DEFAULT 50,
    UNIQUE(tournament_id, name)
);

CREATE TABLE IF NOT EXISTS matches (
    id SERIAL PRIMARY KEY,
    tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
    game_no TEXT,
    sport TEXT DEFAULT 'FIFA World Cup',
    round TEXT NOT NULL DEFAULT 'Group',
    match_date TEXT,
    lock_at TEXT,
    locked_at TEXT,
    stadium TEXT DEFAULT 'TBD',
    home_team_id INTEGER NOT NULL REFERENCES teams(id),
    away_team_id INTEGER NOT NULL REFERENCES teams(id),
    home_score INTEGER,
    away_score INTEGER,
    status TEXT NOT NULL DEFAULT 'scheduled',
    predictions_open INTEGER NOT NULL DEFAULT 1,
    result_mode TEXT NOT NULL DEFAULT 'manual',
    external_match_id TEXT,
    live_source TEXT,
    winner_team_id INTEGER REFERENCES teams(id),
    loser_team_id INTEGER REFERENCES teams(id),
    ai_home_probability REAL,
    ai_away_probability REAL,
    ai_draw_probability REAL,
    commentary TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS predictions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    predicted_team_id INTEGER REFERENCES teams(id),
    predicted_draw INTEGER NOT NULL DEFAULT 0,
    predicted_home_score INTEGER DEFAULT 0,
    predicted_away_score INTEGER DEFAULT 0,
    confidence REAL DEFAULT 50,
    confidence_level TEXT NOT NULL DEFAULT 'Medium',
    points_awarded INTEGER DEFAULT 0,
    is_correct INTEGER,
    scoring_reason TEXT,
    locked_at TEXT,
    scored_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, match_id)
);

CREATE TABLE IF NOT EXISTS leaderboards (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    season TEXT NOT NULL DEFAULT '2026',
    total_points INTEGER NOT NULL DEFAULT 0,
    exact_matches INTEGER NOT NULL DEFAULT 0,
    winner_count INTEGER NOT NULL DEFAULT 0,
    predictions_count INTEGER NOT NULL DEFAULT 0,
    accuracy REAL NOT NULL DEFAULT 0,
    rank INTEGER,
    badges TEXT DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, season)
);

CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    channel TEXT NOT NULL DEFAULT 'email',
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at TEXT
);

CREATE TABLE IF NOT EXISTS match_history (
    id SERIAL PRIMARY KEY,
    match_id INTEGER,
    tournament_id INTEGER,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    actor_user_id INTEGER,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER,
    detail TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    mobile TEXT UNIQUE,
    country TEXT DEFAULT 'Global',
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    is_active INTEGER NOT NULL DEFAULT 1,
    otp_code TEXT,
    otp_verified INTEGER NOT NULL DEFAULT 0,
    last_login_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tournaments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sport TEXT NOT NULL DEFAULT 'football',
    country TEXT DEFAULT 'Global',
    status TEXT NOT NULL DEFAULT 'draft',
    start_date TEXT,
    end_date TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    country TEXT NOT NULL,
    flag TEXT DEFAULT '🏆',
    ranking INTEGER DEFAULT 50,
    home_advantage REAL DEFAULT 0,
    strength_score REAL DEFAULT 50,
    UNIQUE(tournament_id, name)
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
    game_no TEXT,
    sport TEXT DEFAULT 'FIFA World Cup',
    round TEXT NOT NULL DEFAULT 'Group',
    match_date TEXT,
    lock_at TEXT,
    locked_at TEXT,
    stadium TEXT DEFAULT 'TBD',
    home_team_id INTEGER NOT NULL REFERENCES teams(id),
    away_team_id INTEGER NOT NULL REFERENCES teams(id),
    home_score INTEGER,
    away_score INTEGER,
    status TEXT NOT NULL DEFAULT 'scheduled',
    predictions_open INTEGER NOT NULL DEFAULT 1,
    result_mode TEXT NOT NULL DEFAULT 'manual',
    external_match_id TEXT,
    live_source TEXT,
    winner_team_id INTEGER REFERENCES teams(id),
    loser_team_id INTEGER REFERENCES teams(id),
    ai_home_probability REAL,
    ai_away_probability REAL,
    ai_draw_probability REAL,
    commentary TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    predicted_team_id INTEGER REFERENCES teams(id),
    predicted_draw INTEGER NOT NULL DEFAULT 0,
    predicted_home_score INTEGER DEFAULT 0,
    predicted_away_score INTEGER DEFAULT 0,
    confidence REAL DEFAULT 50,
    confidence_level TEXT NOT NULL DEFAULT 'Medium',
    points_awarded INTEGER DEFAULT 0,
    is_correct INTEGER,
    scoring_reason TEXT,
    locked_at TEXT,
    scored_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, match_id)
);

CREATE TABLE IF NOT EXISTS leaderboards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    season TEXT NOT NULL DEFAULT '2026',
    total_points INTEGER NOT NULL DEFAULT 0,
    exact_matches INTEGER NOT NULL DEFAULT 0,
    winner_count INTEGER NOT NULL DEFAULT 0,
    predictions_count INTEGER NOT NULL DEFAULT 0,
    accuracy REAL NOT NULL DEFAULT 0,
    rank INTEGER,
    badges TEXT DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, season)
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    channel TEXT NOT NULL DEFAULT 'email',
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at TEXT
);

CREATE TABLE IF NOT EXISTS match_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER,
    tournament_id INTEGER,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER,
    detail TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


# ──────────────────────────────────────────────
# init_db — works for both SQLite and PostgreSQL
# ──────────────────────────────────────────────
def init_db() -> None:
    with get_db() as db:
        if _is_postgres():
            # Run each statement individually for PostgreSQL
            import psycopg2
            statements = [s.strip() for s in PG_SCHEMA.split(";") if s.strip()]
            for stmt in statements:
                try:
                    db.execute(stmt)
                except Exception as e:
                    print(f"Migration warning (ignored): {e}")
        else:
            db.executescript(SQLITE_SCHEMA)

        # Default app settings
        try:
            db.execute(
                """
                INSERT INTO app_settings (key, value)
                VALUES ('registration_requirements', '{"email_required": true, "mobile_required": false, "otp_required": false}')
                ON CONFLICT (key) DO NOTHING
                """ if _is_postgres() else
                """
                INSERT OR IGNORE INTO app_settings (key, value)
                VALUES ('registration_requirements', '{"email_required": true, "mobile_required": false, "otp_required": false}')
                """
            )
        except Exception:
            pass

        # Column migrations (SQLite only — PostgreSQL schema already has all columns)
        if not _is_postgres():
            migrations = {
                "users": {
                    "last_login_at": "TEXT",
                },
                "matches": {
                    "game_no": "TEXT",
                    "sport": "TEXT DEFAULT 'FIFA World Cup'",
                    "lock_at": "TEXT",
                    "locked_at": "TEXT",
                    "predictions_open": "INTEGER NOT NULL DEFAULT 1",
                    "result_mode": "TEXT NOT NULL DEFAULT 'manual'",
                    "external_match_id": "TEXT",
                    "live_source": "TEXT",
                },
                "predictions": {
                    "predicted_home_score": "INTEGER DEFAULT 0",
                    "predicted_away_score": "INTEGER DEFAULT 0",
                    "confidence_level": "TEXT NOT NULL DEFAULT 'Medium'",
                    "scoring_reason": "TEXT",
                    "locked_at": "TEXT",
                    "scored_at": "TEXT",
                    "updated_at": "TEXT",
                },
            }
            for table, columns in migrations.items():
                for name, definition in columns.items():
                    _add_column(db, table, name, definition)
