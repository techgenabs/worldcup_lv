from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import BASE_DIR, settings
from .database import get_db, init_db, row
from .routers import admin, auth, matches, predictions, tournaments
from .routers import auto_push_result
from .security import hash_password
from .services.scheduler import start_scheduler
from .services.scoring import lock_time


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="AI-powered tournament management for football, cricket, and World Cup style events.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── No-cache middleware for static assets (dev mode) ──────────────────────────
@app.middleware("http")
async def no_cache_assets(request: Request, call_next):
    response = await call_next(request)
    if "/assets/" in str(request.url):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

app.include_router(auth.router, prefix="/api")
app.include_router(tournaments.router, prefix="/api")
app.include_router(matches.router, prefix="/api")
app.include_router(predictions.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(auto_push_result.router, prefix="/api")

static_dir = BASE_DIR / "frontend"
app.mount("/assets", StaticFiles(directory=static_dir / "src"), name="assets")


# ── Favicon (stops 404 noise in logs) ─────────────────────────────────────────
@app.get("/favicon.ico")
async def favicon():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">&#x26BD;</text></svg>'
    return Response(content=svg, media_type="image/svg+xml")


def seed_admin_only() -> None:
    """
    Safe seed — only creates the admin user if it doesn't exist.
    Never touches tournaments, matches, teams, or predictions.
    This means NO DATA is ever lost on restart.
    """
    with get_db() as db:
        admin_exists = row(db.execute(
            "SELECT id FROM users WHERE email = 'admin@worldcup.ai'"
        ))
        if not admin_exists:
            db.execute(
                """
                INSERT INTO users
                    (name, email, mobile, country, password_hash, role, otp_verified)
                VALUES (?, ?, ?, ?, ?, 'admin', 1)
                """,
                (
                    "Admin Manager",
                    "admin@worldcup.ai",
                    "+10000000001",
                    "Global",
                    hash_password("Admin@2026"),
                ),
            )


@app.on_event("startup")
def startup() -> None:
    init_db()
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    seed_admin_only()   # ← safe: never deletes or overwrites existing data
    start_scheduler()


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name}


@app.get("/")
def index():
    return FileResponse(static_dir / "index.html")
