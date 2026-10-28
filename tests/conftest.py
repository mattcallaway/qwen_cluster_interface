import os
import sys
import shutil
import tempfile
import pytest
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Override config to use a temporary directory for tests
test_dir = tempfile.mkdtemp(prefix="qc_test_")
empty_cfg = Path(test_dir) / "empty.env"
empty_cfg.touch()
os.environ["QC_CONFIG_FILE"] = str(empty_cfg)
os.environ["QC_DATA_DIR"] = test_dir
os.environ["QC_SECRET_KEY"] = "test-secret-key-12345"

from app.config import settings, ensure_directories
from app.database import init_db
from app.auth import ensure_default_admin
from app.services.knowledge import seed_initial_knowledge, rebuild_knowledge_index

@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    # Hard isolation pre-flight assertion guard (Stage 0 correction)
    resolved_test_dir = Path(test_dir).resolve()
    resolved_data_dir = Path(settings.data_dir).resolve()
    resolved_db_path = Path(settings.db_path).resolve()
    resolved_evidence_dir = Path(settings.evidence_dir).resolve()

    assert resolved_data_dir == resolved_test_dir, (
        f"CRITICAL TEST ISOLATION FAILURE: settings.data_dir '{resolved_data_dir}' "
        f"does not match designated test root '{resolved_test_dir}'."
    )
    assert resolved_db_path.is_relative_to(resolved_test_dir), (
        f"CRITICAL TEST ISOLATION FAILURE: settings.db_path '{resolved_db_path}' "
        f"is outside designated test root '{resolved_test_dir}'."
    )
    assert resolved_evidence_dir.is_relative_to(resolved_test_dir), (
        f"CRITICAL TEST ISOLATION FAILURE: settings.evidence_dir '{resolved_evidence_dir}' "
        f"is outside designated test root '{resolved_test_dir}'."
    )
    assert ".qwen-collective" not in str(resolved_data_dir).lower(), (
        f"CRITICAL TEST ISOLATION FAILURE: settings.data_dir points to persistent storage: {resolved_data_dir}"
    )

    ensure_directories()
    init_db()
    ensure_default_admin()
    seed_initial_knowledge()
    rebuild_knowledge_index()
    yield
    # Cleanup
    shutil.rmtree(test_dir, ignore_errors=True)


@pytest.fixture
def db_conn():
    from app.database import get_db_connection
    conn = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def preserve_settings_state():
    """Ensure mutable settings such as data_dir are restored after every test."""
    orig_data_dir = settings.data_dir
    yield
    settings.data_dir = orig_data_dir


@pytest.fixture(autouse=True)
def foreign_key_guard():
    """Verify that foreign-key integrity is intact at the conclusion of every test."""
    yield
    from app.database import get_db_connection
    with get_db_connection() as conn:
        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        assert not fk_violations, f"Foreign key check violation detected at fixture boundary: {fk_violations}"



