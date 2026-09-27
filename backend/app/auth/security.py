from __future__ import annotations

import secrets
import time
from collections import defaultdict
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import AdminUser

ph = PasswordHasher()
_login_attempts: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT_WINDOW = 300
_RATE_LIMIT_MAX = 10


def hash_password(password: str) -> str:
    return ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return ph.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="vmm-session")


def create_session_token(username: str) -> str:
    return _serializer().dumps({"u": username, "n": secrets.token_hex(8)})


def read_session_token(token: str) -> str | None:
    settings = get_settings()
    try:
        data = _serializer().loads(token, max_age=settings.session_max_age)
        return data.get("u")
    except (BadSignature, SignatureExpired, TypeError, AttributeError):
        return None


def create_csrf_token(username: str) -> str:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="vmm-csrf").dumps({"u": username})


def verify_csrf_token(token: str, username: str) -> bool:
    try:
        data = URLSafeTimedSerializer(get_settings().secret_key, salt="vmm-csrf").loads(
            token, max_age=get_settings().session_max_age
        )
        return data.get("u") == username
    except (BadSignature, SignatureExpired, TypeError, AttributeError):
        return False


def check_rate_limit(client_ip: str) -> None:
    now = time.time()
    attempts = [t for t in _login_attempts[client_ip] if now - t < _RATE_LIMIT_WINDOW]
    _login_attempts[client_ip] = attempts
    if len(attempts) >= _RATE_LIMIT_MAX:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many login attempts")


def record_login_attempt(client_ip: str) -> None:
    _login_attempts[client_ip].append(time.time())


def clear_login_attempts(client_ip: str) -> None:
    _login_attempts.pop(client_ip, None)


def ensure_admin_user(db: Session) -> AdminUser:
    user = db.query(AdminUser).filter(AdminUser.username == "admin").first()
    if user is None:
        settings = get_settings()
        user = AdminUser(username="admin", password_hash=hash_password(settings.admin_password))
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def get_current_user(request: Request, db: Annotated[Session, Depends(get_db)]) -> AdminUser:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    username = read_session_token(token)
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    user = db.query(AdminUser).filter(AdminUser.username == username).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def require_csrf(request: Request, user: AdminUser) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    token = request.headers.get("X-CSRF-Token")
    if not token or not verify_csrf_token(token, user.username):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed")


def set_session_cookie(response: Response, username: str) -> str:
    settings = get_settings()
    token = create_session_token(username)
    response.set_cookie(
        key=settings.session_cookie,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=settings.session_max_age,
        path="/",
    )
    return create_csrf_token(username)


def clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(settings.session_cookie, path="/")
