"""Application settings loaded from environment variables (.env file or shell)."""
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """All app config in one place. Set via env vars or .env file."""

    # --- Auth ---
    # CHANGE THESE BEFORE PRODUCTION. Generate SECRET_KEY with:
    #   python -c "import secrets; print(secrets.token_urlsafe(48))"
    secret_key: str = Field(default="change-me-in-production-please")
    admin_username: str = Field(default="admin")
    # Bcrypt hash of the admin password. Default below is for "changeme" — REPLACE IT.
    # Generate a new one with:
    #   python -c "from app.auth import hash_password; print(hash_password('your-password'))"
    admin_password_hash: str = Field(
        default="$2b$12$Cs7eb2AhZNkc0XbXJ1zG9.JgxflihUE8t/CmJUO3s4DEdZ7WWHrkG"
    )
    session_hours: int = 24

    # --- Database ---
    # SQLite by default. Switch to Postgres by setting DATABASE_URL:
    #   DATABASE_URL=postgresql://user:pass@host:5432/dbname
    database_url: str = Field(default="sqlite:///./data/portal.db")

    # --- Storage ---
    upload_dir: str = Field(default="./uploads")

    # --- Email (SMTP) — used for sending coaching reports to solvers ---
    # Leave smtp_host blank to disable email entirely (the buttons will
    # still appear in the UI but return a clear "email not configured" error).
    smtp_host: str = Field(default="")
    smtp_port: int = Field(default=587)
    smtp_username: str = Field(default="")
    smtp_password: str = Field(default="")
    smtp_use_tls: bool = Field(default=True)
    smtp_from_email: str = Field(default="")
    smtp_from_name: str = Field(default="Solvit Operations")

    # --- Misc ---
    app_name: str = "Solvit Performance Portal"
    debug: bool = False

    # --- CORS ---
    # Comma-separated list of allowed origins.
    # Example: https://yourapp.onrender.com,https://portal.solvit.co.ke
    allowed_origins: str = Field(default="http://localhost:5173,http://localhost:8000")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
