from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# Absolute path used inside the Valheim container for BepInEx config.
# Symlink targets must use this prefix so they resolve after bootstrap copies them.
VALHEIM_CONFIG_BEPINEX = Path("/config/bepinex")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Valheim Mod Manager"
    data_dir: Path = Path("/data")
    bepinex_root: Path = Path("/valheim/bepinex")
    # Deprecated: do not mount data/bepinex. Kept for env compatibility; always unused when None.
    live_plugins_root: Path | None = None
    host: str = "0.0.0.0"
    port: int = 8090
    secret_key: str = "change-me-in-production"
    admin_password: str = "changeme"
    session_cookie: str = "vmm_session"
    session_max_age: int = 60 * 60 * 24 * 7
    timezone: str = "America/Los_Angeles"
    supervisor_url: str = ""
    supervisor_user: str = "admin"
    supervisor_password: str = ""
    supervisor_program: str = "valheim-server"
    container_display_name: str = "valheim"
    thunderstore_api: str = "https://thunderstore.io/c/valheim/api/v1/package/"
    hexium_api: str = "https://valheim.hexium.gg/api/v1/package/"
    package_refresh_minutes: int = 60
    update_check_minutes: int = 60
    maintenance_hour: int = 4
    maintenance_minute: int = 0
    cors_origins: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "modmanager.db"

    @property
    def downloads_dir(self) -> Path:
        return self.data_dir / "downloads"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def staging_dir(self) -> Path:
        return self.data_dir / "staging"

    @property
    def plugins_dir(self) -> Path:
        return self.bepinex_root / "plugins"

    @property
    def patchers_dir(self) -> Path:
        return self.bepinex_root / "patchers"

    @property
    def config_dir(self) -> Path:
        return self.bepinex_root / "config"

    @property
    def persistent_dir(self) -> Path:
        """Bulky packages live here; plugins/<name> is a symlink into this tree."""
        return self.bepinex_root / ".persistent"


@lru_cache
def get_settings() -> Settings:
    return Settings()
