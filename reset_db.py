"""
WorldCup 2026 — Database Reset Script
======================================
Run from your project root:
    python reset_db.py

This will:
  1. Find your SQLite database automatically
  2. Wipe ALL game data (tournaments, teams, matches, predictions, leaderboards)
  3. Keep all user accounts (so everyone can still log in)
  4. Reset auto-increment counters
  5. Print a summary of what was cleared

To ALSO wipe users, set WIPE_USERS = True below.
"""

import sqlite3
import os
import sys
from pathlib import Path

# ── CONFIG ──────────────────────────────────────────────────────────────────
WIPE_USERS = False   # Set True to also delete all user accounts

# Common DB locations — script tries each one
DB_CANDIDATES = [
    "worldcup.db",
    "app.db",
    "backend/app/worldcup.db",
    "backend/app/app.db",
    "backend/worldcup.db",
    "data/worldcup.db",
    "data/app.db",
]
# ────────────────────────────────────────────────────────────────────────────


def find_db() -> Path | None:
    """Try candidate paths, then search recursively."""
    for p in DB_CANDIDATES:
        path = Path(p)
        if path.exists():
            return path
    # Recursive search (max depth 4)
    for root, dirs, files in os.walk("."):
        # Skip node_modules, .git etc
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__", ".venv", "venv")]
        depth = root.count(os.sep)
        if depth > 4:
            continue
        for f in files:
            if f.endswith((".db", ".sqlite", ".sqlite3")):
                return Path(root) / f
    return None


def reset(db_path: Path) -> None:
    print(f"\n🗄️  Database: {db_path.resolve()}")
    print(f"   Size before: {db_path.stat().st_size / 1024:.1f} KB\n")

    con = sqlite3.connect(db_path)
    cur = con.cursor()

    # Count before
    def count(table):
        try:
            return cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except Exception:
            return "N/A"

    before = {
        "tournaments":   count("tournaments"),
        "teams":         count("teams"),
        "matches":       count("matches"),
        "predictions":   count("predictions"),
        "leaderboards":  count("leaderboards"),
        "match_history": count("match_history"),
        "audit_logs":    count("audit_logs"),
        "notifications": count("notifications"),
        "users":         count("users"),
    }

    print("📊 Before reset:")
    for k, v in before.items():
        print(f"   {k:<18} {v:>6} rows")

    print()

    # ── WIPE TABLES (order matters — foreign keys) ──
    tables_to_clear = [
        "predictions",
        "leaderboards",
        "match_history",
        "audit_logs",
        "notifications",
        "matches",
        "teams",
        "tournaments",
    ]
    if WIPE_USERS:
        tables_to_clear.append("users")

    con.execute("PRAGMA foreign_keys = OFF")

    for table in tables_to_clear:
        try:
            rows_deleted = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            cur.execute(f"DELETE FROM {table}")
            # Reset auto-increment counter
            cur.execute(f"DELETE FROM sqlite_sequence WHERE name=?", (table,))
            print(f"   ✅ Cleared {table:<18} ({rows_deleted} rows deleted)")
        except sqlite3.OperationalError as e:
            print(f"   ⚠️  {table}: {e}")

    con.execute("PRAGMA foreign_keys = ON")

    # Vacuum to reclaim space
    con.commit()
    cur.execute("VACUUM")
    con.commit()
    con.close()

    print(f"\n   Size after:  {db_path.stat().st_size / 1024:.1f} KB")

    if WIPE_USERS:
        print("\n⚠️  Users also wiped — admin will be re-seeded on next server startup.")
    else:
        print(f"\n👥 User accounts kept ({before['users']} users intact).")
        print("   Everyone can still log in with their existing password.")

    print("\n🎉 Database reset complete!")
    print("   Restart your server:  python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8039")
    print("   The server will re-seed demo data automatically on startup.\n")


if __name__ == "__main__":
    db = find_db()
    if not db:
        print("❌ Could not find database file.")
        print("   Please set the path manually in DB_CANDIDATES at the top of this script.")
        sys.exit(1)

    print("=" * 60)
    print("  WorldCup 2026 — Database Reset")
    print("=" * 60)

    confirm = input(f"\n⚠️  This will wipe all game data from:\n   {db.resolve()}\n\nType YES to confirm: ").strip()
    if confirm != "YES":
        print("❌ Cancelled.")
        sys.exit(0)

    reset(db)
