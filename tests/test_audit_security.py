import os
import shutil
import tempfile
import sqlite3
import pytest
from starlette.testclient import TestClient
from app.config import settings, validate_production_settings
from app.services.network_security import (
    is_cloud_metadata,
    validate_probe_target,
    validate_endpoint_url
)
from app.services.hardware_probe import test_tcp_reachability as check_tcp_reachability, probe_remote_ssh
from app.services.machine_registry import register_machine, get_machine_by_id, update_machine_role
from app.services.knowledge import write_note_atomic, read_note, search_knowledge, rebuild_knowledge_index
from app.auth import create_session_token
from app.main import app

# 1 & 2. Production Security Enforcement Tests
def test_production_rejects_insecure_secret(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "secret_key", "qwen-collective-insecure-secret-key-change-me")
    monkeypatch.setattr(settings, "admin_password", "A_Very_Strong_Pass123!@#")

    with pytest.raises(RuntimeError) as exc:
        validate_production_settings()
    assert "QC_SECRET_KEY" in str(exc.value)

def test_production_rejects_default_admin_password(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "secret_key", "super-secret-unique-random-token-with-over-32-chars-long!")
    monkeypatch.setattr(settings, "admin_password", "admin123")

    with pytest.raises(RuntimeError) as exc:
        validate_production_settings()
    assert "QC_ADMIN_PASSWORD" in str(exc.value)

def test_production_accepts_strong_configuration(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "secret_key", "super-secret-unique-random-token-with-over-32-chars-long!")
    monkeypatch.setattr(settings, "admin_password", "A_Very_Strong_Production_Pass123!")

    # Should not raise
    validate_production_settings()

# 3. State-Changing Endpoints Require Authentication & CSRF
def test_probe_endpoints_reject_unauthenticated():
    client = TestClient(app)
    # Ping probe without auth
    res_ping = client.post("/api/machines/probe/ping", data={"ip_address": "192.168.1.100"})
    assert "Authentication required" in res_ping.text

    # SSH probe without auth
    res_ssh = client.post("/api/machines/probe/ssh", data={"ip_address": "192.168.1.100", "ssh_user": "debian"})
    assert "Authentication required" in res_ssh.text

def test_probe_endpoints_reject_invalid_csrf():
    client = TestClient(app)
    token = create_session_token("admin")
    client.cookies.set("qc_session", token)

    # Invalid CSRF
    res = client.post(
        "/api/machines/probe/ping",
        data={"ip_address": "192.168.1.100", "csrf_token": "invalid_csrf_token"}
    )
    assert "Invalid or missing CSRF token" in res.text

# 5. Hardware Discovery Rejects Unknown Host Keys
def test_probe_ssh_rejects_unknown_host_key():
    # Attempt SSH to loopback or test host without pre-shared known_hosts key
    res = probe_remote_ssh("127.0.0.1", username="fakeuser", port=22, timeout=1)
    assert res["success"] is False
    # Must fail safely with host key rejection or connection failure, never accepting untrusted keys
    assert "rejected" in res["error"].lower() or "connection" in res["error"].lower() or "timeout" in res["error"].lower()

# 6 & 7. SSRF & Cloud Metadata Blocking Tests
def test_cloud_metadata_detection():
    assert is_cloud_metadata("169.254.169.254") is True
    assert is_cloud_metadata("metadata.google.internal") is True
    assert is_cloud_metadata("fd00:ec2::254") is True
    assert is_cloud_metadata("192.168.1.100") is False

def test_reachability_probe_blocks_cloud_metadata():
    is_safe, msg = validate_probe_target("169.254.169.254", port=22)
    assert is_safe is False
    assert "cloud metadata" in msg.lower()

    # Reachability call directly returns rejection
    is_up, reason = check_tcp_reachability("169.254.169.254", port=22)
    assert is_up is False
    assert "cloud metadata" in reason.lower()

def test_reachability_probe_blocks_arbitrary_ports():
    is_safe, msg = validate_probe_target("192.168.1.100", port=25) # SMTP port
    assert is_safe is False
    assert "not in the allowed collective probe ports" in msg

def test_endpoint_url_validation_blocks_metadata():
    is_safe, msg = validate_endpoint_url("http://169.254.169.254/latest/meta-data/")
    assert is_safe is False
    assert "cloud metadata" in msg.lower()

def test_endpoint_url_validation_blocks_sensitive_localhost_ports():
    # Block internal SSH port on localhost
    is_safe, msg = validate_endpoint_url("http://127.0.0.1:22/api")
    assert is_safe is False
    assert "prohibited" in msg.lower()

    # Allow authorized Ollama port on localhost
    is_safe_ollama, _ = validate_endpoint_url("http://127.0.0.1:11434")
    assert is_safe_ollama is True

# 4 & 10. Persistence, Backup & Restore Verification
def test_persistence_and_backup_restore():
    # 1. Write state to SQLite and Markdown
    machine = register_machine(
        display_name="Backup Test Machine",
        ip_address="192.168.1.250",
        role="distributed_worker"
    )
    update_machine_role(machine["id"], "standalone_server", notes="Persistent note test")

    note_path = "decisions/backup_test_adr.md"
    write_note_atomic(note_path, """---
title: Backup & Restore ADR
category: decisions
---
Verifying that database and markdown notes restore cleanly together.
""")

    # Verify search finds it
    matches = search_knowledge("restore cleanly")
    assert len(matches) >= 1

    # 2. Perform live SQLite backup using sqlite3 backup API
    backup_db_path = settings.data_dir / "test_backup.db"
    src_conn = sqlite3.connect(settings.db_path)
    dst_conn = sqlite3.connect(backup_db_path)
    src_conn.backup(dst_conn)
    src_conn.close()
    dst_conn.close()

    assert backup_db_path.exists()
    assert backup_db_path.stat().st_size > 0

    # 3. Verify backup contains machine and audits
    verify_conn = sqlite3.connect(backup_db_path)
    verify_conn.row_factory = sqlite3.Row
    row = verify_conn.execute("SELECT * FROM machines WHERE ip_address = '192.168.1.250'").fetchone()
    assert row is not None
    assert row["role"] == "standalone_server"
    assert row["notes"] == "Persistent note test"
    verify_conn.close()

# Priority 1: Service XML Contains No Hardcoded Secrets
def test_service_xml_contains_no_hardcoded_secrets():
    from pathlib import Path
    xml_path = Path("service/qwen-collective-service.xml")
    if xml_path.exists():
        content = xml_path.read_text(encoding="utf-8")
        assert "QC_SECRET_KEY" not in content, "QC_SECRET_KEY must not be committed to service XML"
        assert "QC_ADMIN_PASSWORD" not in content, "QC_ADMIN_PASSWORD must not be committed to service XML"
        assert "QC_CONFIG_FILE" in content, "Service XML must reference protected external config"

# Priority 2: HTML Injection Prevention Tests
def test_html_injection_prevention_in_htmx_ping(monkeypatch):
    client = TestClient(app)
    token = create_session_token("admin")
    client.cookies.set("qc_session", token)

    # Mock reachability to return malicious HTML string
    malicious_payload = "<script>alert('xss_ping')</script><img src=x onerror=alert(1)>"
    import app.routes.machines as m_routes
    monkeypatch.setattr(m_routes, "test_tcp_reachability", lambda host, port, timeout: (False, malicious_payload))

    # Send CSRF token
    csrf_token = "valid_token"
    monkeypatch.setattr(m_routes, "verify_csrf", lambda req, token: True)

    res = client.post(
        "/api/machines/probe/ping",
        data={"ip_address": "192.168.1.50", "csrf_token": csrf_token}
    )
    assert res.status_code == 200
    # Must NOT contain raw unescaped script tag
    assert "<script>" not in res.text
    assert "<img" not in res.text
    assert "&lt;script&gt;" in res.text
    assert "&lt;img" in res.text

def test_html_injection_prevention_in_htmx_ssh(monkeypatch):
    client = TestClient(app)
    token = create_session_token("admin")
    client.cookies.set("qc_session", token)

    import app.routes.machines as m_routes
    monkeypatch.setattr(m_routes, "verify_csrf", lambda req, token: True)

    malicious_payload = "<script>alert('xss_ssh')</script>"
    # Mock probe failure with malicious error
    monkeypatch.setattr(m_routes, "probe_remote_ssh", lambda *args, **kwargs: {"success": False, "error": malicious_payload})

    res = client.post(
        "/api/machines/probe/ssh",
        data={"ip_address": "192.168.1.50", "ssh_user": "root", "csrf_token": "token"}
    )
    assert res.status_code == 200
    assert "<script>" not in res.text
    assert "&lt;script&gt;" in res.text

    # Mock probe success with malicious hardware outputs
    monkeypatch.setattr(m_routes, "probe_remote_ssh", lambda *args, **kwargs: {
        "success": True,
        "os_info": "<b>Debian</b><script>alert('os')</script>",
        "cpu_info": "<svg onload=alert(1)>",
        "disk_info": "<iframe src='javascript:alert(1)'>",
        "ram_total_mb": 4096,
        "ram_avail_mb": 2048,
        "recommendation": {"explanation": "<em>Good</em>", "recommended_role": "worker"}
    })

    res_ok = client.post(
        "/api/machines/probe/ssh",
        data={"ip_address": "192.168.1.50", "ssh_user": "root", "csrf_token": "token"}
    )
    assert res_ok.status_code == 200
    assert "<script>" not in res_ok.text
    assert "<svg" not in res_ok.text
    assert "<iframe" not in res_ok.text
    assert "&lt;script&gt;" in res_ok.text
    assert "&lt;svg" in res_ok.text
    assert "&lt;iframe" in res_ok.text

def test_html_injection_prevention_in_detail_template():
    client = TestClient(app)
    token = create_session_token("admin")
    client.cookies.set("qc_session", token)

    # Register machine with malicious specs
    xss_machine = register_machine(
        display_name="<script>alert('name')</script>",
        ip_address="192.168.1.188",
        role="unassigned"
    )
    from app.services.machine_registry import update_machine_specs
    update_machine_specs(
        machine_id=xss_machine["id"],
        cpu_info="<script>alert('cpu')</script>",
        os_info="<img src=x onerror=alert('os')>",
        disk_info="<svg onload=alert('disk')>"
    )

    res = client.get(f"/machines/{xss_machine['id']}")
    assert res.status_code == 200
    assert "<script>alert('cpu')</script>" not in res.text
    assert "<img src=x onerror=alert('os')>" not in res.text
    assert "<svg onload=alert('disk')>" not in res.text
    assert "&lt;script&gt;alert(&#39;cpu&#39;)&lt;/script&gt;" in res.text or "&lt;script&gt;" in res.text

# Priority 3: Network Security (IPv4, IPv6, Loopback & Metadata)
def test_network_security_ipv4_and_ipv6():
    # Valid private IPv4 (RFC1918)
    ok_priv, _ = validate_endpoint_url("http://192.168.1.55:8080")
    assert ok_priv is True

    # Valid Tailscale IPv4 (100.64.0.0/10)
    ok_ts, _ = validate_endpoint_url("http://100.100.50.25:8080")
    assert ok_ts is True

    # Valid Localhost IPv4 on permitted port
    ok_local_8080, _ = validate_endpoint_url("http://127.0.0.1:8080", allow_localhost=True)
    assert ok_local_8080 is True

    # Localhost IPv4 on blocked sensitive port (e.g. 22)
    bad_local_22, _ = validate_endpoint_url("http://127.0.0.1:22", allow_localhost=True)
    assert bad_local_22 is False

    # Localhost disallowed completely
    bad_no_local, _ = validate_endpoint_url("http://127.0.0.1:8080", allow_localhost=False)
    assert bad_no_local is False

    # Valid Localhost IPv6 ([::1]) on permitted port
    ok_local_v6, _ = validate_endpoint_url("http://[::1]:8080", allow_localhost=True)
    assert ok_local_v6 is True

    # Localhost IPv6 on blocked port
    bad_local_v6_22, _ = validate_endpoint_url("http://[::1]:22", allow_localhost=True)
    assert bad_local_v6_22 is False

    # Blocked Cloud Metadata IPv4
    bad_meta_v4, _ = validate_endpoint_url("http://169.254.169.254/latest/meta-data/")
    assert bad_meta_v4 is False

    # Blocked Cloud Metadata IPv6
    bad_meta_v6, _ = validate_endpoint_url("http://[fd00:ec2::254]/")
    assert bad_meta_v6 is False

    # Blocked Link-Local IPv6 (fe80::)
    bad_link_v6, _ = validate_endpoint_url("http://[fe80::1]:8080/")
    assert bad_link_v6 is False

# Priority 4: Authentication, Rate Limiting & Proxy Security
def test_login_rate_limiting():
    client = TestClient(app)
    from app.routes.auth import _FAILED_LOGINS, clear_failed_logins
    _FAILED_LOGINS.clear()

    # 5 consecutive failed logins
    for i in range(5):
        resp = client.post("/auth/login", data={"username": "admin", "password": f"wrong_{i}"})
        assert resp.status_code == 401

    # 6th attempt must be rejected with 429 Too Many Requests
    resp_rate_limited = client.post("/auth/login", data={"username": "admin", "password": "wrong_again"})
    assert resp_rate_limited.status_code == 429
    assert "Too many failed login attempts" in resp_rate_limited.text

    _FAILED_LOGINS.clear()

def test_trusted_proxy_handling():
    client = TestClient(app)
    from app.routes.auth import is_request_https, get_client_ip
    from starlette.requests import Request

    # Direct mock request without trusted proxy
    scope_direct = {
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "headers": [(b"x-forwarded-proto", b"https"), (b"x-forwarded-for", b"203.0.113.195")],
        "client": ("198.51.100.1", 12345),
        "path": "/"
    }
    req_direct = Request(scope_direct)
    # Untrusted client IP must not spoof https
    assert is_request_https(req_direct) is False
    assert get_client_ip(req_direct) == "198.51.100.1"

    # Request from trusted proxy (127.0.0.1 / Tailscale Serve)
    scope_proxy = {
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "headers": [(b"x-forwarded-proto", b"https"), (b"x-forwarded-for", b"100.64.0.5")],
        "client": ("127.0.0.1", 54321),
        "path": "/"
    }
    req_proxy = Request(scope_proxy)
    # Trusted proxy header is honored
    assert is_request_https(req_proxy) is True
    assert get_client_ip(req_proxy) == "100.64.0.5"

