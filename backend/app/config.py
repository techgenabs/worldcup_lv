from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
BASE_DIR = Path(__file__).resolve().parents[2]
class Settings(BaseSettings):
    app_name: str = "WorldCup AI 2026"
    database_url: str = f"sqlite:///{BASE_DIR / 'worldcup_ai.db'}"
    jwt_secret: str = "change-this-secret-before-production"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 1440
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = "worldcup2026abs@gmail.com"
    smtp_password: str = ""
    smtp_from: str = "worldcup2026abs@gmail.com"
    enable_email: bool = False
    export_dir: Path = BASE_DIR / "exports"
    app_timezone: str = "Asia/Katmandu"
    live_api_provider: str = ""
    live_api_key: str = ""
    football_data_api_key: str = ""
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8")
settings = Settings()
