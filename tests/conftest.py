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
os.environ["QC_DATA_DIR"] = test_dir
os.environ["QC_SECRET_KEY"] = "test-secret-key-12345"

from app.config import settings, ensure_directories
from app.database import init_db
from app.auth import ensure_default_admin
from app.services.knowledge import seed_initial_knowledge, rebuild_knowledge_index

@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
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

