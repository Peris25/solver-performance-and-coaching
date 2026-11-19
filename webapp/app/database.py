"""Database setup: SQLAlchemy engine + session factory."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings

# SQLite needs check_same_thread=False for FastAPI's threaded workers.
# Postgres doesn't need it.
connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(settings.database_url, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a DB session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables. Safe to call repeatedly."""
    # Import models so they register with Base
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine, checkfirst=True)
    _migrate_email_send_logs()
    _migrate_solver_phone()


def _migrate_email_send_logs():
    """Upgrade an existing email_send_logs table for broadcast messaging:
    adds subject/attachments columns and makes period_id nullable.
    create_all() never alters existing tables, so this runs on every start
    and is a no-op once applied."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if "email_send_logs" not in insp.get_table_names():
        return
    cols = {c["name"]: c for c in insp.get_columns("email_send_logs")}
    is_pg = engine.dialect.name == "postgresql"
    with engine.begin() as conn:
        if "subject" not in cols:
            conn.execute(text("ALTER TABLE email_send_logs ADD COLUMN subject VARCHAR(255)"))
        if "attachments" not in cols:
            conn.execute(text("ALTER TABLE email_send_logs ADD COLUMN attachments %s DEFAULT '[]'"
                              % ("JSON" if is_pg else "JSON")))
        if "period_id" in cols and not cols["period_id"].get("nullable", True):
            if is_pg:
                conn.execute(text("ALTER TABLE email_send_logs ALTER COLUMN period_id DROP NOT NULL"))
            else:
                # SQLite can't drop NOT NULL in place: rebuild the table.
                conn.execute(text("ALTER TABLE email_send_logs RENAME TO _esl_old"))
    if not is_pg and "_esl_old" in inspect(engine).get_table_names():
        Base.metadata.tables["email_send_logs"].create(bind=engine)
        with engine.begin() as conn:
            old = [c["name"] for c in inspect(engine).get_columns("_esl_old")]
            names = ", ".join(old)
            conn.execute(text(f"INSERT INTO email_send_logs ({names}) SELECT {names} FROM _esl_old"))
            conn.execute(text("DROP TABLE _esl_old"))


def _migrate_solver_phone():
    """Add solvers.phone to existing databases (create_all won't)."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if "solvers" not in insp.get_table_names():
        return
    if "phone" not in {c["name"] for c in insp.get_columns("solvers")}:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE solvers ADD COLUMN phone VARCHAR(64)"))
