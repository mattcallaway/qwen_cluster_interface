import time
from collections import defaultdict
from typing import Dict, List, Tuple
from fastapi import APIRouter, Request, Response, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.auth import verify_password, create_session_token, get_current_user, get_csrf_token, verify_csrf
from app.database import get_db_connection

router = APIRouter(prefix="/auth", tags=["auth"])
templates = Jinja2Templates(directory="app/templates")

# Rate limiting configuration
_FAILED_LOGINS: Dict[str, List[float]] = defaultdict(list)
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW_SECONDS = 300 # 5 minutes
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

def check_login_rate_limit(client_ip: str) -> Tuple[bool, int]:
    """Check if client IP is currently rate-limited due to excessive failed attempts."""
    now = time.time()
    cutoff = now - LOCKOUT_WINDOW_SECONDS
    attempts = [t for t in _FAILED_LOGINS[client_ip] if t > cutoff]
    _FAILED_LOGINS[client_ip] = attempts
    if len(attempts) >= MAX_FAILED_ATTEMPTS:
        remaining = int(LOCKOUT_WINDOW_SECONDS - (now - attempts[0]))
        return False, max(1, remaining)
    return True, 0

def record_failed_login(client_ip: str):
    """Record a failed login attempt timestamp."""
    _FAILED_LOGINS[client_ip].append(time.time())

def clear_failed_logins(client_ip: str):
    """Clear failed login attempts upon successful authentication."""
    _FAILED_LOGINS.pop(client_ip, None)

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request=request, name="login.html", context={"error": None})

@router.post("/login")
def login_submit(request: Request, response: Response, username: str = Form(...), password: str = Form(...)):
    client_ip = get_client_ip(request)
    allowed, wait_sec = check_login_rate_limit(client_ip)
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
        clear_failed_logins(client_ip)
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
        record_failed_login(client_ip)
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
