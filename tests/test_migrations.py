"""Migration correctness tests. All run against temporary databases, never production."""
import sqlite3

import pytest

from app import migrations as mig
from app.config import settings
from app.database import create_baseline_schema


def _connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _make_v0_db(path):
    """A database exactly as the pre-migration application would have left it, with data."""
    conn = _connect(path)
    create_baseline_schema(conn)
    conn.execute("INSERT INTO users (username, password_hash, created_at) VALUES ('admin','x$y','2026-01-01')")
    conn.execute("""INSERT INTO tasks (id, title, description, task_type, status, approval_required,
                    idempotency_key, input_data_json, created_at, updated_at)
                    VALUES ('task-legacy01','Legacy task','kept','general','completed',0,'idem-1','{"a":1}',
                    '2026-01-01','2026-01-02')""")
    conn.execute("""INSERT INTO knowledge_fts (path, title, category, content, updated_at)
                    VALUES ('system/welcome.md','Welcome','system','Welcome albatross text','2026-01-01')""")
    conn.commit()
    return conn


def test_fresh_database_reaches_latest_version(tmp_path):
    conn = _connect(tmp_path / "fresh.db")
    create_baseline_schema(conn)
    applied = mig.apply_migrations(conn)
    assert applied == [1, 2]
    assert mig.current_version(conn) == mig.latest_version()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("projects", "project_paths", "path_stages", "knowledge_metadata",
              "knowledge_relations", "project_decisions", "task_dependencies",
              "deliverables", "deliverable_revisions", "task_execution_events", "path_revisions"):
        assert t in tables


def test_existing_v0_data_preserved(tmp_path):
    conn = _make_v0_db(tmp_path / "v0.db")
    before = dict(conn.execute("SELECT * FROM tasks WHERE id='task-legacy01'").fetchone())
    mig.apply_migrations(conn)

    after = dict(conn.execute("SELECT * FROM tasks WHERE id='task-legacy01'").fetchone())
    for k, v in before.items():
        assert after[k] == v, f"column {k} changed"
    for new_col in ("project_id", "path_id", "stage_id", "purpose", "expected_output", "completion_criteria", "deliverable_id"):
        assert after[new_col] is None
    assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_fts5_still_works_after_migration(tmp_path):
    conn = _make_v0_db(tmp_path / "fts.db")
    mig.apply_migrations(conn)
    rows = conn.execute("SELECT path FROM knowledge_fts WHERE knowledge_fts MATCH 'albatross'").fetchall()
    assert [r[0] for r in rows] == ["system/welcome.md"]


def test_migrations_are_idempotent(tmp_path):
    conn = _connect(tmp_path / "idem.db")
    create_baseline_schema(conn)
    assert mig.apply_migrations(conn) == [1, 2]
    assert mig.apply_migrations(conn) == []
    create_baseline_schema(conn)  # startup re-runs baseline CREATE IF NOT EXISTS safely
    assert mig.current_version(conn) == 2


def test_upgrade_from_v1_to_v2(tmp_path):
    conn = _connect(tmp_path / "v1.db")
    create_baseline_schema(conn)
    # Apply only M1
    applied1 = mig.apply_migrations(conn, [mig.MIGRATIONS[0]])
    assert applied1 == [1]
    assert mig.current_version(conn) == 1

    # Insert sample M1 project and decision
    conn.execute("INSERT INTO projects (id, name, status, owner, knowledge_dir, created_at, updated_at, last_activity_at) VALUES ('prj-1','P1','draft','admin','projects/p1','2026-01-01','2026-01-01','2026-01-01')")
    conn.execute("INSERT INTO project_decisions (id, project_id, decision_type, rationale, decided_by, decided_at) VALUES ('dec-1','prj-1','project_status_change','init','admin','2026-01-01')")
    conn.commit()

    # Now upgrade with full migrations suite
    applied2 = mig.apply_migrations(conn)
    assert applied2 == [2]
    assert mig.current_version(conn) == 2

    # Verify M1 data preserved and new decision types allowed
    assert conn.execute("SELECT name FROM projects WHERE id='prj-1'").fetchone()[0] == "P1"
    assert conn.execute("SELECT rationale FROM project_decisions WHERE id='dec-1'").fetchone()[0] == "init"
    # Insert M2 decision type
    conn.execute("INSERT INTO project_decisions (id, project_id, decision_type, rationale, decided_by, decided_at) VALUES ('dec-2','prj-1','deliverable_acceptance','looks great','admin','2026-01-02')")
    conn.commit()
    assert conn.execute("SELECT count(*) FROM project_decisions WHERE project_id='prj-1'").fetchone()[0] == 2
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_failed_migration_rolls_back_completely(tmp_path):
    conn = _connect(tmp_path / "fail.db")
    create_baseline_schema(conn)
    bad = [(1, "deliberately broken", [
        "CREATE TABLE should_not_exist (x INTEGER)",
        "ALTER TABLE tasks ADD COLUMN should_not_exist_col TEXT",
        "THIS IS NOT VALID SQL",
    ])]
    with pytest.raises(sqlite3.OperationalError):
        mig.apply_migrations(conn, bad)
    assert mig.current_version(conn) == 0
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "should_not_exist" not in tables
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    assert "should_not_exist_col" not in cols


def test_backup_before_migration_is_readable_copy(tmp_path):
    db = tmp_path / "prod_like.db"
    conn = _make_v0_db(db)
    backup = mig.backup_before_migration(conn, db)
    assert backup is not None and backup.exists()
    b = _connect(backup)
    assert b.execute("SELECT title FROM tasks WHERE id='task-legacy01'").fetchone()[0] == "Legacy task"
    assert mig.current_version(b) == 0
    # Nothing to back up once current
    mig.apply_migrations(conn)
    assert mig.backup_before_migration(conn, db) is None


def test_init_db_backs_up_existing_database(tmp_path, monkeypatch):
    from app import database
    conn = _make_v0_db(tmp_path / "qwen_collective.db")
    conn.close()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    database.init_db()
    backups = list(tmp_path.glob("qwen_collective.pre-migration-v0-to-v*.db"))
    assert len(backups) == 1
    c = _connect(tmp_path / "qwen_collective.db")
    assert mig.current_version(c) == mig.latest_version()
    assert c.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
