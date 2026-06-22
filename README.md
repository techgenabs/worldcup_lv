<<<<<<< HEAD
# WorldCup AI 2026

AI-powered World Cup tournament management platform for football, cricket, and tournament-style competitions.

## Features

- Email or mobile login with JWT authentication and OTP-ready registration.
- Role-based access for Admin and User accounts.
- Tournament, team, fixture, and match management.
- Automatic round-robin fixture generation.
- Automatic point table calculation with wins, losses, draws, points, goal difference, and net run rate.
- Winner and loser tracking after score updates.
- AI match prediction using a logistic team-strength model with extension points for Scikit-learn models.
- User prediction game with confidence scoring and global/country leaderboard.
- Analytics dashboard for users, tournaments, matches, predictions, participation, and accuracy.
- Email notification queue for registration, results, reminders, and tournament updates.
- CSV, Excel, and TXT report exports.
- Historical match storage and audit logs.
- Docker-ready deployment.

## Quick Start

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`.

Demo accounts:

- Admin: `admin@worldcup.ai` / `Admin@2026`
- User: `fan@worldcup.ai` / `Fan@2026`

## Configuration

Copy `.env.example` to `.env` and update secrets.

Important values:

- `JWT_SECRET`: use a long random production secret.
- `DATABASE_URL`: SQLite path for this starter.
- `ENABLE_EMAIL`: set to `true` after adding SMTP credentials.
- `SMTP_USER`: defaults to `worldcup2026abs@gmail.com`.
- `SMTP_PASSWORD`: use a Gmail app password, not the account password.
- `LIVE_API_PROVIDER` and `LIVE_API_KEY`: reserved for API-Football, CricAPI, or FIFA-style integrations.

## Docker

```bash
copy .env.example .env
docker compose up --build
```

The app runs on `http://localhost:8000`.

## API Documentation

FastAPI generates live API docs:

- Swagger UI: `http://127.0.0.1:8000/docs`
- OpenAPI JSON: `http://127.0.0.1:8000/openapi.json`

Core endpoints:

- `POST /api/auth/register`
- `POST /api/auth/login`
- `POST /api/auth/verify-otp`
- `GET /api/tournaments`
- `POST /api/tournaments`
- `POST /api/tournaments/{id}/teams`
- `POST /api/tournaments/{id}/fixtures`
- `GET /api/tournaments/{id}/standings`
- `GET /api/tournaments/{id}/forecast`
- `GET /api/matches`
- `POST /api/matches`
- `GET /api/matches/{id}/predict`
- `PUT /api/matches/{id}/score`
- `POST /api/predictions`
- `GET /api/predictions/mine`
- `GET /api/predictions/leaderboard`
- `GET /api/admin/analytics`
- `POST /api/admin/exports/{tournament_id}`

## Production Notes

- Replace the default JWT secret before deployment.
- Set `ENABLE_EMAIL=true` only after SMTP credentials are configured.
- Use HTTPS and a reverse proxy in production.
- Move from SQLite to PostgreSQL for multi-instance deployments.
- Add Redis-backed rate limiting for public auth endpoints.
- Store OTPs with expiry timestamps for production SMS/email flows.
- Integrate live scores through a provider adapter in `backend/app/services`.
- Add Alembic migrations if the schema will evolve frequently.

## Deployment Targets

- Render or Railway: run `uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT`.
- AWS or Azure: deploy the Docker image behind a managed load balancer.
- Vercel: host the frontend separately and point it at the FastAPI backend.
=======
# worldcup_lv
>>>>>>> 176850bf06e6e5b7e1772ff9cfc30b12b4f37e22
