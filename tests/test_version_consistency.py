"""
Automated Version Consistency Regression Suite.

Verifies that all internal and external release-facing version representations
coherently resolve from the single authoritative version source (app.version.__version__ = "1.0.0-rc.2").
"""
from __future__ import annotations

import os
import pytest
from starlette.testclient import TestClient

from app.version import __version__ as APP_VERSION
import app
from app.config import Settings, settings
from app.main import app as controller_app
from vps_agent import __version__ as AGENT_VERSION
from worker.vps_worker import app as worker_app


EXPECTED_RC2_VERSION = "1.0.0-rc.2"


def test_authoritative_version_declaration():
    """Verify that app.version and app expose the expected RC2 version string."""
    assert APP_VERSION == EXPECTED_RC2_VERSION
    assert app.__version__ == EXPECTED_RC2_VERSION


def test_settings_default_version():
    """Verify that default settings.app_version matches the authoritative declaration."""
    assert settings.app_version == EXPECTED_RC2_VERSION


def test_fastapi_metadata_version():
    """Verify that the FastAPI controller metadata reflects the authoritative version."""
    assert controller_app.version == EXPECTED_RC2_VERSION


def test_health_endpoints_version_identity():
    """Verify that /health and /api/v1/health return the authoritative version."""
    client = TestClient(controller_app)
    
    # 1. /health
    res1 = client.get("/health")
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["status"] == "healthy"
    assert data1["version"] == EXPECTED_RC2_VERSION
    assert data1["app"] == "Qwen Collective"

    # 2. /api/v1/health
    res2 = client.get("/api/v1/health")
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["version"] == EXPECTED_RC2_VERSION


def test_openapi_schema_version():
    """Verify that OpenAPI documentation schema reports the authoritative version."""
    client = TestClient(controller_app)
    res = client.get("/openapi.json")
    assert res.status_code == 200
    openapi = res.json()
    assert openapi["info"]["version"] == EXPECTED_RC2_VERSION


def test_worker_version_consistency():
    """Verify that worker agent daemon and companion service report the authoritative version."""
    assert AGENT_VERSION == EXPECTED_RC2_VERSION
    assert worker_app.version == EXPECTED_RC2_VERSION


def test_env_override_capability(monkeypatch):
    """Verify that QC_APP_VERSION environment variable cleanly overrides version when specified."""
    monkeypatch.setenv("QC_APP_VERSION", "1.0.0-custom.1")
    custom_settings = Settings()
    assert custom_settings.app_version == "1.0.0-custom.1"
