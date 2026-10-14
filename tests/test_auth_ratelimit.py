import time
import pytest
from starlette.testclient import TestClient
from app.main import app
from app.database import get_db_connection
from app.routes.auth import (
    check_login_rate_limit,
    record_failed_login,
    clear_failed_logins,
    MAX_FAILED_ATTEMPTS,
    LOCKOUT_WINDOW_SECONDS
)

@pytest.fixture(autouse=True)
def clean_rate_limits():
    conn = get_db_connection()
    conn.execute("DELETE FROM login_attempts")
    conn.commit()
    conn.close()
    yield
    conn = get_db_connection()
    conn.execute("DELETE FROM login_attempts")
    conn.commit()
    conn.close()

def test_ip_based_rate_limiting():
    ip = "192.168.1.100"
    user = "alice"
    
    # First 4 attempts allowed
    for _ in range(4):
        record_failed_login(ip, user)
        allowed, _ = check_login_rate_limit(ip, "another_user")
        assert allowed is True

    # 5th attempt triggers lockout for that IP
    record_failed_login(ip, user)
    allowed, wait_sec = check_login_rate_limit(ip, "another_user")
    assert allowed is False
    assert 0 < wait_sec <= LOCKOUT_WINDOW_SECONDS

def test_account_based_rate_limiting_across_ips():
    # Attacker rotates IPs to guess password for 'admin'
    target_user = "admin"
    for i in range(MAX_FAILED_ATTEMPTS):
        spoofed_ip = f"10.0.0.{i+1}"
        record_failed_login(spoofed_ip, target_user)

    # Now an attempt from a completely fresh IP against target_user must be blocked!
    fresh_ip = "10.0.0.99"
    allowed, wait_sec = check_login_rate_limit(fresh_ip, target_user)
    assert allowed is False
    assert wait_sec > 0

def test_successful_login_clears_records():
    ip = "192.168.1.50"
    user = "bob"
    for _ in range(3):
        record_failed_login(ip, user)

    # Verify attempts recorded
    conn = get_db_connection()
    count = conn.execute("SELECT COUNT(*) as c FROM login_attempts WHERE key_value = ?", (user,)).fetchone()["c"]
    conn.close()
    assert count == 3

    # Clear upon successful login
    clear_failed_logins(ip, user)
    conn = get_db_connection()
    count_after = conn.execute("SELECT COUNT(*) as c FROM login_attempts WHERE key_value = ?", (user,)).fetchone()["c"]
    conn.close()
    assert count_after == 0

def test_rate_limit_persistence_and_bounds():
    # Record 50 attempts
    for i in range(50):
        record_failed_login(f"192.168.2.{i%10}", f"user_{i%5}")

    conn = get_db_connection()
    total_count = conn.execute("SELECT COUNT(*) as c FROM login_attempts").fetchone()["c"]
    conn.close()
    assert total_count <= 1000
    assert total_count > 0

def test_untrusted_forwarded_for_header_ignored():
    client = TestClient(app)
    # Untrusted remote client attempts to bypass IP lockout by sending spoofed X-Forwarded-For
    for i in range(5):
        client.post(
            "/auth/login",
            data={"username": "admin", "password": "wrong"},
            headers={"X-Forwarded-For": f"1.1.1.{i}"}
        )

    # Next attempt should be blocked on username and peer IP
    resp = client.post(
        "/auth/login",
        data={"username": "admin", "password": "wrong"},
        headers={"X-Forwarded-For": "9.9.9.9"}
    )
    assert resp.status_code == 429
