from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = "admin"
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class AuthStatus(BaseModel):
    authenticated: bool
    username: str | None = None
    csrf_token: str | None = None


class SettingUpdate(BaseModel):
    values: dict[str, Any]


class SettingsResponse(BaseModel):
    values: dict[str, Any]


class InstalledPackageOut(BaseModel):
    id: int
    source: str
    full_name: str
    name: str
    owner: str | None = None
    version: str | None = None
    plugin_guid: str | None = None
    install_path: str
    managed: bool
    enabled: bool
    pinned: bool
    auto_update: bool
    description: str | None = None
    icon_url: str | None = None
    package_url: str | None = None
    dependencies: list[str] = []
    config_files: list[str] = []
    owned_files: list[str] = []
    update_available: str | None = None
    last_scanned_at: datetime | None = None
    live_only: bool = False

    model_config = {"from_attributes": True}


class DashboardStats(BaseModel):
    installed_count: int
    unmanaged_count: int
    updates_count: int
    disabled_count: int
    last_scan_at: datetime | None = None
    last_update_check_at: datetime | None = None
    maintenance_enabled: bool
    next_maintenance_window: str | None = None
    supervisor_status: str | None = None
    supervisor_configured: bool
    restart_required: bool
    recent_activity: list["ActivityOut"] = []
    pending_updates: list["PendingUpdateOut"] = []


class ActivityOut(BaseModel):
    id: int
    timestamp: datetime
    action: str
    package: str | None = None
    source: str | None = None
    result: str
    message: str | None = None

    model_config = {"from_attributes": True}


class PendingUpdateOut(BaseModel):
    id: int
    source: str
    full_name: str
    current_version: str | None = None
    target_version: str
    status: str
    detected_at: datetime

    model_config = {"from_attributes": True}


class PackageVersionOut(BaseModel):
    version_number: str
    download_url: str
    dependencies: list[str] = []
    description: str | None = None
    icon: str | None = None
    date_created: str | None = None
    downloads: int = 0
    file_size: int | None = None


class PackageOut(BaseModel):
    source: str
    name: str
    full_name: str
    owner: str
    package_url: str | None = None
    description: str | None = None
    icon_url: str | None = None
    date_updated: str | None = None
    rating_score: int = 0
    downloads: int = 0
    categories: list[str] = []
    is_deprecated: bool = False
    installed: bool = False
    installed_version: str | None = None
    latest_version: str | None = None
    versions: list[PackageVersionOut] = []
    dependencies: list[str] = []


class InstallRequest(BaseModel):
    source: str
    full_name: str
    version: str | None = None


class InstallPreview(BaseModel):
    packages: list[dict[str, Any]]
    conflicts: list[str] = []
    warnings: list[str] = []


class LinkPackageRequest(BaseModel):
    source: str
    full_name: str
    version: str | None = None


class PackageActionRequest(BaseModel):
    pinned: bool | None = None
    auto_update: bool | None = None


class ConfigFileOut(BaseModel):
    path: str
    name: str
    size: int
    modified_at: datetime | None = None


class ConfigContentOut(BaseModel):
    path: str
    raw: str
    structured: dict[str, Any] | None = None
    parse_ok: bool
    restart_required: bool = True


class ConfigSaveRequest(BaseModel):
    raw: str | None = None
    structured: dict[str, Any] | None = None


class BackupOut(BaseModel):
    id: int
    created_at: datetime
    label: str
    reason: str
    path: str
    package_names: list[str] = []
    notes: str | None = None
    success: bool

    model_config = {"from_attributes": True}


class CreateBackupRequest(BaseModel):
    label: str | None = None
    notes: str | None = None
    package_ids: list[int] | None = None


class ScanResult(BaseModel):
    scanned: int
    managed: int
    unmanaged: int
    packages: list[InstalledPackageOut]


class HealthOut(BaseModel):
    status: str
    version: str
    bepinex_root: str
    plugins_exist: bool
