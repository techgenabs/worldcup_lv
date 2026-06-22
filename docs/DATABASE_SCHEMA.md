# Database Schema

The starter uses SQLite and can be migrated to PostgreSQL. Tables are created in `backend/app/database.py`.

## Tables

- `users`: profile, login identity, password hash, OTP state, role, active status.
- `tournaments`: tournament metadata, sport, country, schedule, status.
- `teams`: tournament teams, country, flag, ranking, home advantage, AI strength score.
- `matches`: fixture data, teams, scores, status, winner/loser, AI probability fields, commentary.
- `predictions`: user picks, draw/team prediction, confidence, correctness, awarded points.
- `notifications`: email notification queue and delivery status.
- `match_history`: immutable match result snapshots.
- `audit_logs`: admin action trail.

## Point Rules

Football:

- Win: 3 points
- Draw: 1 point
- Loss: 0 points
- Ranking sort: points, goal difference, goals scored, ranking

Cricket:

- Win: 2 points
- Draw/tie/no result: 1 point
- Net run rate approximation is included for dashboard ranking support.
