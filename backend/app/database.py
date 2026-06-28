import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .config import settings


def _get_database_url() -> str:
    url = os.environ.get("DATABASE_URL", settings.database_url)
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def _is_postgres() -> bool:
    return _get_database_url().startswith("postgresql://")


@contextmanager
def get_db():
    if _is_postgres():
        import psycopg2
        conn = psycopg2.connect(_get_database_url())
        conn.autocommit = False
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        url = _get_database_url()
        path = Path(url.replace("sqlite:///", "", 1))
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


def rows(cursor) -> list[dict[str, Any]]:
    return [dict(row) for row in cursor.fetchall()]


def row(cursor) -> dict[str, Any] | None:
    item = cursor.fetchone()
    return dict(item) if item else None


def _columns(db, table: str) -> set[str]:
    if _is_postgres():
        cur = db.cursor()
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (table,)
        )
        return {r[0] for r in cur.fetchall()}
    return {item["name"] for item in db.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column(db, table: str, column: str, definition: str) -> None:
    if column not in _columns(db, table):
        if _is_postgres():
            cur = db.cursor()
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        else:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    with get_db() as db:
        if _is_postgres():
            cur = db.cursor()
            statements = [
                """CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
                    mobile TEXT UNIQUE, country TEXT DEFAULT 'Global', password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user', is_active INTEGER NOT NULL DEFAULT 1,
                    otp_code TEXT, otp_verified INTEGER NOT NULL DEFAULT 0,
                    last_login_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
                """CREATE TABLE IF NOT EXISTS tournaments (
                    id SERIAL PRIMARY KEY, name TEXT NOT NULL, sport TEXT NOT NULL DEFAULT 'football',
                    country TEXT DEFAULT 'Global', status TEXT NOT NULL DEFAULT 'draft',
                    start_date TEXT, end_date TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
                """CREATE TABLE IF NOT EXISTS teams (
                    id SERIAL PRIMARY KEY, tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
                    name TEXT NOT NULL, country TEXT NOT NULL, flag TEXT DEFAULT '🏆',
                    ranking INTEGER DEFAULT 50, home_advantage REAL DEFAULT 0, strength_score REAL DEFAULT 50,
                    UNIQUE(tournament_id, name))""",
                """CREATE TABLE IF NOT EXISTS matches (
                    id SERIAL PRIMARY KEY, tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
                    game_no TEXT, sport TEXT DEFAULT 'FIFA World Cup', round TEXT NOT NULL DEFAULT 'Group',
                    match_date TEXT, lock_at TEXT, locked_at TEXT, stadium TEXT DEFAULT 'TBD',
                    home_team_id INTEGER NOT NULL REFERENCES teams(id), away_team_id INTEGER NOT NULL REFERENCES teams(id),
                    home_score INTEGER, away_score INTEGER, status TEXT NOT NULL DEFAULT 'scheduled',
                    predictions_open INTEGER NOT NULL DEFAULT 1, result_mode TEXT NOT NULL DEFAULT 'manual',
                    external_match_id TEXT, live_source TEXT, winner_team_id INTEGER REFERENCES teams(id),
                    loser_team_id INTEGER REFERENCES teams(id), ai_home_probability REAL,
                    ai_away_probability REAL, ai_draw_probability REAL, commentary TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
                """CREATE TABLE IF NOT EXISTS predictions (
                    id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
                    predicted_team_id INTEGER REFERENCES teams(id), predicted_draw INTEGER NOT NULL DEFAULT 0,
                    predicted_home_score INTEGER DEFAULT 0, predicted_away_score INTEGER DEFAULT 0,
                    confidence REAL DEFAULT 50, confidence_level TEXT NOT NULL DEFAULT 'Medium',
                    points_awarded INTEGER DEFAULT 0, is_correct INTEGER, scoring_reason TEXT,
                    locked_at TEXT, scored_at TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id, match_id))""",
                """CREATE TABLE IF NOT EXISTS leaderboards (
                    id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    season TEXT NOT NULL DEFAULT '2026', total_points INTEGER NOT NULL DEFAULT 0,
                    exact_matches INTEGER NOT NULL DEFAULT 0, winner_count INTEGER NOT NULL DEFAULT 0,
                    predictions_count INTEGER NOT NULL DEFAULT 0, accuracy REAL NOT NULL DEFAULT 0,
                    rank INTEGER, badges TEXT DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, season))""",
                """CREATE TABLE IF NOT EXISTS notifications (
                    id SERIAL PRIMARY KEY, user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    channel TEXT NOT NULL DEFAULT 'email', recipient TEXT NOT NULL,
                    subject TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, sent_at TEXT)""",
                """CREATE TABLE IF NOT EXISTS match_history (
                    id SERIAL PRIMARY KEY, match_id INTEGER, tournament_id INTEGER,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
                """CREATE TABLE IF NOT EXISTS audit_logs (
                    id SERIAL PRIMARY KEY, actor_user_id INTEGER, action TEXT NOT NULL,
                    entity_type TEXT NOT NULL, entity_id INTEGER, detail TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
                """CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
                """INSERT INTO app_settings (key, value)
                    VALUES ('registration_requirements',
                    '{"email_required": true, "mobile_required": false, "otp_required": false}')
                    ON CONFLICT (key) DO NOTHING""",
            ]
            for stmt in statements:
                cur.execute(stmt)
        else:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
                    mobile TEXT UNIQUE, country TEXT DEFAULT 'Global', password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user', is_active INTEGER NOT NULL DEFAULT 1,
                    otp_code TEXT, otp_verified INTEGER NOT NULL DEFAULT 0,
                    last_login_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS tournaments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, sport TEXT NOT NULL DEFAULT 'football',
                    country TEXT DEFAULT 'Global', status TEXT NOT NULL DEFAULT 'draft',
                    start_date TEXT, end_date TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS teams (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
                    name TEXT NOT NULL, country TEXT NOT NULL, flag TEXT DEFAULT '🏆',
                    ranking INTEGER DEFAULT 50, home_advantage REAL DEFAULT 0, strength_score REAL DEFAULT 50,
                    UNIQUE(tournament_id, name));
                CREATE TABLE IF NOT EXISTS matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
                    game_no TEXT, sport TEXT DEFAULT 'FIFA World Cup', round TEXT NOT NULL DEFAULT 'Group',
                    match_date TEXT, lock_at TEXT, locked_at TEXT, stadium TEXT DEFAULT 'TBD',
                    home_team_id INTEGER NOT NULL REFERENCES teams(id), away_team_id INTEGER NOT NULL REFERENCES teams(id),
                    home_score INTEGER, away_score INTEGER, status TEXT NOT NULL DEFAULT 'scheduled',
                    predictions_open INTEGER NOT NULL DEFAULT 1, result_mode TEXT NOT NULL DEFAULT 'manual',
                    external_match_id TEXT, live_source TEXT, winner_team_id INTEGER REFERENCES teams(id),
                    loser_team_id INTEGER REFERENCES teams(id), ai_home_probability REAL,
                    ai_away_probability REAL, ai_draw_probability REAL, commentary TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
                    predicted_team_id INTEGER REFERENCES teams(id), predicted_draw INTEGER NOT NULL DEFAULT 0,
                    predicted_home_score INTEGER DEFAULT 0, predicted_away_score INTEGER DEFAULT 0,
                    confidence REAL DEFAULT 50, confidence_level TEXT NOT NULL DEFAULT 'Medium',
                    points_awarded INTEGER DEFAULT 0, is_correct INTEGER, scoring_reason TEXT,
                    locked_at TEXT, scored_at TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id, match_id));
                CREATE TABLE IF NOT EXISTS leaderboards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    season TEXT NOT NULL DEFAULT '2026', total_points INTEGER NOT NULL DEFAULT 0,
                    exact_matches INTEGER NOT NULL DEFAULT 0, winner_count INTEGER NOT NULL DEFAULT 0,
                    predictions_count INTEGER NOT NULL DEFAULT 0, accuracy REAL NOT NULL DEFAULT 0,
                    rank INTEGER, badges TEXT DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, season));
                CREATE TABLE IF NOT EXISTS notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                    channel TEXT NOT NULL DEFAULT 'email', recipient TEXT NOT NULL,
                    subject TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, sent_at TEXT);
                CREATE TABLE IF NOT EXISTS match_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, match_id INTEGER, tournament_id INTEGER,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, actor_user_id INTEGER, action TEXT NOT NULL,
                    entity_type TEXT NOT NULL, entity_id INTEGER, detail TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                INSERT OR IGNORE INTO app_settings (key, value)
                VALUES ('registration_requirements',
                '{"email_required": true, "mobile_required": false, "otp_required": false}');
            """)
            migrations = {
                "users": {"last_login_at": "TEXT"},
                "matches": {
                    "game_no": "TEXT", "sport": "TEXT DEFAULT 'FIFA World Cup'",
                    "lock_at": "TEXT", "locked_at": "TEXT",
                    "predictions_open": "INTEGER NOT NULL DEFAULT 1",
                    "result_mode": "TEXT NOT NULL DEFAULT 'manual'",
                    "external_match_id": "TEXT", "live_source": "TEXT",
                },
                "predictions": {
                    "predicted_home_score": "INTEGER DEFAULT 0",
                    "predicted_away_score": "INTEGER DEFAULT 0",
                    "confidence_level": "TEXT NOT NULL DEFAULT 'Medium'",
                    "scoring_reason": "TEXT", "locked_at": "TEXT",
                    "scored_at": "TEXT", "updated_at": "TEXT",
                },
            }
            for table, columns in migrations.items():
                for name, definition in columns.items():
                    _add_column(db, table, name, definition)
