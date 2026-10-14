import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Optional
from fastapi import Request, HTTPException, status
from app.config import settings
from app.database import get_db_connection

def hash_password(password: str) -> str:
    """Hash a password using PBKDF2 with SHA-256 and a random salt."""
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100_000
    )
    return f"{salt}${key.hex()}"

def verify_password(stored_hash: str, password: str) -> bool:
    """Verify password against stored salt$key."""
    try:
        salt, key_hex = stored_hash.split('$')
        computed_key = hashlib.pbkdf2_hmac(
            'sha256',
            password.encode('utf-8'),
            salt.encode('utf-8'),
            100_000
        )
        return hmac.compare_digest(computed_key.hex(), key_hex)
    except Exception:
        return False

def ensure_default_admin():
    """Ensure at least one admin user exists and password reflects configured admin_password."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, password_hash FROM users WHERE username = 'admin'")
    row = cursor.fetchone()
    if not row:
        pw_hash = hash_password(settings.admin_password)
        now = datetime.now(timezone.utc).isoformat()
        cursor.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            ('admin', pw_hash, now)
        )
        conn.commit()
    else:
        # If configured admin_password doesn't match current hash, update it
        if not verify_password(row["password_hash"], settings.admin_password):
            pw_hash = hash_password(settings.admin_password)
            cursor.execute(
                "UPDATE users SET password_hash = ? WHERE username = 'admin'",
                (pw_hash,)
            )
            conn.commit()
    conn.close()

def create_session_token(username: str) -> str:
    """Create a signed, time-limited session token."""
    timestamp = str(int(time.time()))
    payload = f"{username}:{timestamp}"
    sig = hmac.new(
        settings.secret_key.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return f"{payload}:{sig}"

def verify_session_token(token: Optional[str]) -> Optional[str]:
    """Verify signed session token. Returns username if valid, None otherwise."""
    if not token or ":" not in token:
        return None
    parts = token.split(":")
    if len(parts) != 3:
        return None
    username, ts_str, sig = parts
    try:
        ts = int(ts_str)
        # Check expiration
        max_age = settings.session_expire_hours * 3600
        if time.time() - ts > max_age:
            return None
        payload = f"{username}:{ts_str}"
        expected_sig = hmac.new(
            settings.secret_key.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        if hmac.compare_digest(sig, expected_sig):
            return username
    except Exception:
        return None
    return None

def get_csrf_token(request: Request) -> str:
    """Generate or retrieve a CSRF token for the session."""
    session = request.cookies.get("qc_session")
    if not session:
        return ""
    # Deterministic CSRF token derived from session + secret
    return hmac.new(
        settings.secret_key.encode('utf-8'),
        f"csrf:{session}".encode('utf-8'),
        hashlib.sha256
    ).hexdigest()[:32]

def verify_csrf(request: Request, submitted_token: Optional[str]) -> bool:
    """Verify submitted CSRF token against expected session token."""
    expected = get_csrf_token(request)
    if not expected or not submitted_token:
        return False
    return hmac.compare_digest(expected, submitted_token)

def get_current_user(request: Request) -> Optional[str]:
    """Extract and verify authenticated user from request cookie."""
    token = request.cookies.get("qc_session")
    return verify_session_token(token)
