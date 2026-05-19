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
from app.database import init_db
from app.routes import router as api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create DB tables on startup; nothing to do on shutdown."""
    init_db()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(api_router)

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


# Mount /static/* for assets (kept narrow so /api/* still routes correctly)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
