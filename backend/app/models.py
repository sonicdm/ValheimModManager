from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String(128), primary_key=True)
    value = Column(Text, nullable=False, default="")


class AdminUser(Base):
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, default="admin")
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class InstalledPackage(Base):
    __tablename__ = "installed_packages"
    __table_args__ = (UniqueConstraint("source", "full_name", name="uq_source_full_name"),)

    id = Column(Integer, primary_key=True)
    source = Column(String(32), nullable=False, default="local")  # thunderstore|hexium|local|unmanaged
    full_name = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    owner = Column(String(255), nullable=True)
    version = Column(String(64), nullable=True)
    plugin_guid = Column(String(255), nullable=True)
    install_path = Column(String(1024), nullable=False)
    managed = Column(Boolean, default=False)
    enabled = Column(Boolean, default=True)
    pinned = Column(Boolean, default=False)
    auto_update = Column(Boolean, default=True)
    description = Column(Text, nullable=True)
    icon_url = Column(String(1024), nullable=True)
    package_url = Column(String(1024), nullable=True)
    dependencies_json = Column(Text, nullable=True, default="[]")
    last_scanned_at = Column(DateTime(timezone=True), default=utcnow)
    installed_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    files = relationship("OwnedFile", back_populates="package", cascade="all, delete-orphan")
    configs = relationship("ConfigAssociation", back_populates="package", cascade="all, delete-orphan")


class OwnedFile(Base):
    __tablename__ = "owned_files"

    id = Column(Integer, primary_key=True)
    package_id = Column(Integer, ForeignKey("installed_packages.id", ondelete="CASCADE"), nullable=False)
    relative_path = Column(String(1024), nullable=False)
    sha256 = Column(String(64), nullable=True)
    size = Column(Integer, nullable=True)

    package = relationship("InstalledPackage", back_populates="files")


class ConfigAssociation(Base):
    __tablename__ = "config_associations"

    id = Column(Integer, primary_key=True)
    package_id = Column(Integer, ForeignKey("installed_packages.id", ondelete="CASCADE"), nullable=False)
    relative_path = Column(String(1024), nullable=False)

    package = relationship("InstalledPackage", back_populates="configs")


class ActivityEvent(Base):
    __tablename__ = "activity_events"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime(timezone=True), default=utcnow, index=True)
    action = Column(String(64), nullable=False, index=True)
    package = Column(String(255), nullable=True)
    source = Column(String(32), nullable=True)
    result = Column(String(32), nullable=False, default="ok")  # ok|error|deferred
    message = Column(Text, nullable=True)
    details_json = Column(Text, nullable=True)


class PendingUpdate(Base):
    __tablename__ = "pending_updates"
    __table_args__ = (UniqueConstraint("source", "full_name", name="uq_pending_source_full_name"),)

    id = Column(Integer, primary_key=True)
    source = Column(String(32), nullable=False)
    full_name = Column(String(255), nullable=False)
    current_version = Column(String(64), nullable=True)
    target_version = Column(String(64), nullable=False)
    download_url = Column(String(1024), nullable=True)
    detected_at = Column(DateTime(timezone=True), default=utcnow)
    status = Column(String(32), nullable=False, default="queued")  # queued|deferred|installing|done|failed


class BackupRecord(Base):
    __tablename__ = "backup_records"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    label = Column(String(255), nullable=False)
    reason = Column(String(64), nullable=False, default="manual")
    path = Column(String(1024), nullable=False)
    package_names_json = Column(Text, nullable=True, default="[]")
    notes = Column(Text, nullable=True)
    success = Column(Boolean, default=True)


class PackageCacheMeta(Base):
    __tablename__ = "package_cache_meta"

    source = Column(String(32), primary_key=True)
    last_refreshed_at = Column(DateTime(timezone=True), nullable=True)
    package_count = Column(Integer, default=0)
    enabled = Column(Boolean, default=True)
