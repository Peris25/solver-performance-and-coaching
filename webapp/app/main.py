"""FastAPI application entry point.

Creates the app, registers routes, mounts the frontend as static files.
Run locally with:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db, SessionLocal
from app.routes import router as api_router
from app.compensation_routes import router as compensation_router


def _seed_compensation() -> None:
    """Populate the compensation roster once, on first boot.

    Mirrors what the standalone engine did in its own lifespan: if the
    ``solver_compensation`` table is empty, load the 116-solver seed with
    historical averages. Idempotent — does nothing once rows exist.
    """
    from app import models
    from app.compensation_seed import SOLVER_SEED

    db = SessionLocal()
    try:
        already = db.query(models.SolverCompensation).count()
        if already == 0:
            db.add_all(models.SolverCompensation(**row) for row in SOLVER_SEED)
            db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create DB tables on startup, then seed the compensation roster."""
    init_db()
    _seed_compensation()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(api_router)
app.include_router(compensation_router)

# CORS — origins are read from the ALLOWED_ORIGINS env var so you can
# add your Render URL without touching code.
_origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the frontend
STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/login")
def login_page():
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/compensation")
def compensation_page():
    """The compensation engine UI. Served as its own self-contained page and
    embedded in the portal via an iframe. Its API calls hit /api/comp/*, which
    require a valid admin session, so the data stays protected."""
    return FileResponse(STATIC_DIR / "compensation.html")


# Mount /static/* for assets (kept narrow so /api/* still routes correctly)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
