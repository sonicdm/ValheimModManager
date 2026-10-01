from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api.routes import router
from .auth.security import ensure_admin_user
from .config import get_settings
from .database import get_session, init_db
from .models import Setting
from .services.settings_service import set_setting
from .services.updates import (
    configure_scheduler,
    job_startup,
    scheduler,
    shutdown_scheduler,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("valheim_mod_manager")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    db = get_session()
    try:
        ensure_admin_user(db)
        # Env wins on every boot so .env / compose changes apply without wiping the DB.
        if settings.supervisor_url:
            set_setting(db, "supervisor_url", settings.supervisor_url)
        if settings.supervisor_user:
            set_setting(db, "supervisor_user", settings.supervisor_user)
        # Allow clearing password with empty env only when explicitly set; otherwise keep DB value
        # when SUPERVISOR_PASSWORD is omitted. If the var is present (even empty), sync it.
        import os

        if "SUPERVISOR_PASSWORD" in os.environ:
            set_setting(db, "supervisor_password", settings.supervisor_password)
        elif settings.supervisor_password and db.get(Setting, "supervisor_password") is None:
            set_setting(db, "supervisor_password", settings.supervisor_password)
        if settings.supervisor_program:
            set_setting(db, "supervisor_program", settings.supervisor_program)
        if db.get(Setting, "container_display_name") is None:
            set_setting(db, "container_display_name", settings.container_display_name)
        # TIMEZONE from compose/.env always wins so cron tracks the host zone.
        if settings.timezone:
            set_setting(db, "timezone", settings.timezone)
    finally:
        db.close()

    configure_scheduler()
    # One-shot boot work — scan then refresh, sequential (avoid SQLite lock races).
    try:
        scheduler.add_job(job_startup, id="startup", replace_existing=True)
    except Exception:
        logger.exception("Could not schedule startup scan/refresh")
    logger.info("Valheim Mod Manager %s started", __version__)
    yield
    shutdown_scheduler()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.include_router(router)

    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.is_dir():
        assets = static_dir / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}")
        async def spa(full_path: str):
            if full_path.startswith("api/"):
                return {"detail": "Not Found"}
            index = static_dir / "index.html"
            file_path = static_dir / full_path
            if full_path and file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(index)

    return app


app = create_app()
