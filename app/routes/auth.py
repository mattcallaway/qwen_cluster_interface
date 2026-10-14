import time
from typing import Dict, List, Tuple, Optional
from fastapi import APIRouter, Request, Response, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.auth import verify_password, create_session_token, get_current_user, get_csrf_token, verify_csrf
from app.database import get_db_connection

router = APIRouter(prefix="/auth", tags=["auth"])
templates = Jinja2Templates(directory="app/templates")

# Rate limiting configuration
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW_SECONDS = 300 # 5 minutes
MAX_RECORDED_ATTEMPTS = 1000 # Strict storage bound against DoS
TRUSTED_PROXIES = {"127.0.0.1", "::1", "localhost", "testclient"}

def get_client_ip(request: Request) -> str:
    """Safely determine client IP, trusting proxy headers only from localhost/trusted proxies."""
    peer_ip = request.client.host if request.client else "127.0.0.1"
    if peer_ip in TRUSTED_PROXIES:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return peer_ip

def is_request_https(request: Request) -> bool:
    """Determine if request arrived via HTTPS directly or through an authorized proxy (e.g., Tailscale Serve)."""
    if request.url.scheme == "https":
        return True
    peer_ip = request.client.host if request.client else "127.0.0.1"
    if peer_ip in TRUSTED_PROXIES:
        return request.headers.get("x-forwarded-proto") == "https"
    return False

def check_login_rate_limit(client_ip: str, username: str) -> Tuple[bool, int]:
    """
    Check if client IP or account identity is currently rate-limited.
    Uses bounded SQLite persistence to survive application restarts and prevent brute-force attacks.
    """
    now = time.time()
    cutoff = now - LOCKOUT_WINDOW_SECONDS
    user_clean = username.strip().lower()

    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Prune expired entries to maintain bounded storage
    cursor.execute("DELETE FROM login_attempts WHERE attempt_time < ?", (cutoff,))
    
    # 2. Check IP-based failed attempts
    cursor.execute(
        "SELECT attempt_time FROM login_attempts WHERE key_type = 'ip' AND key_value = ? ORDER BY attempt_time ASC",
        (client_ip,)
    )
    ip_attempts = [row["attempt_time"] for row in cursor.fetchall()]

    # 3. Check Account-based failed attempts
    cursor.execute(
        "SELECT attempt_time FROM login_attempts WHERE key_type = 'user' AND key_value = ? ORDER BY attempt_time ASC",
        (user_clean,)
    )
    user_attempts = [row["attempt_time"] for row in cursor.fetchall()]
    conn.commit()
    conn.close()

    # If either IP or account has exceeded limit, enforce lockout
    if len(ip_attempts) >= MAX_FAILED_ATTEMPTS:
        remaining = int(LOCKOUT_WINDOW_SECONDS - (now - ip_attempts[0]))
        return False, max(1, remaining)

    if len(user_attempts) >= MAX_FAILED_ATTEMPTS:
        remaining = int(LOCKOUT_WINDOW_SECONDS - (now - user_attempts[0]))
        return False, max(1, remaining)

    return True, 0

def record_failed_login(client_ip: str, username: str):
    """Record a failed login attempt timestamp for both IP and target account."""
    now = time.time()
    user_clean = username.strip().lower()

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO login_attempts (key_type, key_value, attempt_time) VALUES ('ip', ?, ?)",
        (client_ip, now)
    )
    cursor.execute(
        "INSERT INTO login_attempts (key_type, key_value, attempt_time) VALUES ('user', ?, ?)",
        (user_clean, now)
    )
    # Ensure total table size is strictly bounded
    cursor.execute("""
    DELETE FROM login_attempts WHERE id NOT IN (
        SELECT id FROM login_attempts ORDER BY id DESC LIMIT ?
    )
    """, (MAX_RECORDED_ATTEMPTS,))
    conn.commit()
    conn.close()

def clear_failed_logins(client_ip: str, username: Optional[str] = None):
    """Clear failed login attempts upon successful authentication."""
    conn = get_db_connection()
    cursor = conn.cursor()
    if username:
        user_clean = username.strip().lower()
        cursor.execute(
            "DELETE FROM login_attempts WHERE (key_type = 'ip' AND key_value = ?) OR (key_type = 'user' AND key_value = ?)",
            (client_ip, user_clean)
        )
    else:
        cursor.execute("DELETE FROM login_attempts WHERE key_type = 'ip' AND key_value = ?", (client_ip,))
    conn.commit()
    conn.close()

class _FailedLoginsCompat:
    """Compatibility helper for tests and legacy callers inspecting failed logins."""
    def clear(self):
        conn = get_db_connection()
        conn.execute("DELETE FROM login_attempts")
        conn.commit()
        conn.close()

_FAILED_LOGINS = _FailedLoginsCompat()

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request=request, name="login.html", context={"error": None})

@router.post("/login")
def login_submit(request: Request, response: Response, username: str = Form(...), password: str = Form(...)):
    client_ip = get_client_ip(request)
    allowed, wait_sec = check_login_rate_limit(client_ip, username)
    if not allowed:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": f"Too many failed login attempts. Please wait {wait_sec} seconds before retrying."},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS
        )

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash FROM users WHERE username = ?", (username.strip(),))
    row = cursor.fetchone()
    conn.close()

    if row and verify_password(row["password_hash"], password):
        clear_failed_logins(client_ip, username)
        token = create_session_token(username.strip())
        res = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
        is_https = is_request_https(request)
        res.set_cookie(
            key="qc_session",
            value=token,
            httponly=True,
            secure=is_https,
            samesite="lax",
            max_age=72 * 3600
        )
        return res
    else:
        record_failed_login(client_ip, username)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Invalid username or password."},
            status_code=status.HTTP_401_UNAUTHORIZED
        )

@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    if not verify_csrf(request, csrf_token):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    res = RedirectResponse(url="/auth/login", status_code=status.HTTP_302_FOUND)
    res.delete_cookie(key="qc_session")
    return res
