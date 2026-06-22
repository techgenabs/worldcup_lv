from pathlib import Path
from shutil import copy2

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except ImportError:
    BackgroundScheduler = None

from ..config import settings
from ..database import get_db
from .live_scores import fetch_live_scores
from .reports import export_reports
from .scoring import lock_due_predictions, update_leaderboard


scheduler = BackgroundScheduler(timezone="UTC") if BackgroundScheduler else None


def scheduler_tick() -> None:
    with get_db() as db:
        lock_due_predictions(db)
        fetch_live_scores(db)
        update_leaderboard(db)


def daily_backup() -> None:
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    backup_dir = Path(settings.export_dir) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    with get_db() as db:
        export_reports(db, season="2026")
    db_path = settings.database_url.replace("sqlite:///", "")
    path = Path(db_path)
    if path.exists():
        copy2(path, backup_dir / f"{path.stem}_backup.db")


def start_scheduler() -> None:
    if scheduler is None:
        return
    if scheduler.running:
        return
    scheduler.add_job(scheduler_tick, "interval", minutes=5, id="worldcup_tick", replace_existing=True)
    scheduler.add_job(daily_backup, "cron", hour=0, minute=15, id="daily_backup", replace_existing=True)
    scheduler.start()
