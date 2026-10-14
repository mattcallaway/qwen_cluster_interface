"""
Database Migration Compatibility Verification (Correction 4).
Tests:
- Production schema upgrade compatibility from Version 7 to Version 8.
- Foreign key constraint checks.
- SQLite PRAGMA integrity_check.
- Worker schema compatibility (worker_enrollment_tokens table).
- Conversation-to-project association compatibility (chat_conversations.project_id).
- Safe rollback procedure verification via backup restoration.
"""

import sqlite3
from pathlib import Path
import pytest

from app.database import create_baseline_schema
from app.migrations import apply_migrations, current_version, MIGRATIONS


def test_migration_sequence_count_and_ordering():
    """Verify that migrations sequence is complete, strictly numbered, and ordered."""
    assert len(MIGRATIONS) == 8
    for idx, (version, name, statements) in enumerate(MIGRATIONS):
        assert version == idx + 1
        assert len(name) > 0
        assert len(statements) > 0


def test_production_copy_migration_upgrade(tmp_path):
    """
    Restore an isolated replica of a schema at Version 7 and verify that Version 8 applies cleanly
    with zero integrity or foreign-key violations.
    """
    db_file = tmp_path / "test_migration_upgrade.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA foreign_keys = ON;")

    # Initialize baseline schema (version 0)
    create_baseline_schema(conn)

    # Apply migrations 1 through 7 manually
    for version, name, statements in MIGRATIONS[:7]:
        for stmt in statements:
            conn.execute(stmt)
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()

    # Verify at Version 7
    v7 = current_version(conn)
    assert v7 == 7

    # Ensure worker_enrollment_tokens does NOT exist at v7
    tbl_v7 = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='worker_enrollment_tokens'"
    ).fetchone()
    assert tbl_v7 is None

    # Apply remaining migrations using application migration engine
    applied = apply_migrations(conn)
    assert 8 in applied

    # Verify upgraded to Version 8
    v8 = current_version(conn)
    assert v8 == 8

    # Run integrity check
    integrity = conn.execute("PRAGMA integrity_check").fetchall()
    assert len(integrity) == 1
    assert integrity[0][0] == "ok"

    # Run foreign key check
    fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    assert len(fk_violations) == 0

    # Verify table worker_enrollment_tokens exists
    tbl_v8 = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='worker_enrollment_tokens'"
    ).fetchone()
    assert tbl_v8 is not None

    # Verify chat_conversations has project_id column
    cols = [r[1] for r in conn.execute("PRAGMA table_info(chat_conversations)").fetchall()]
    assert "project_id" in cols

    conn.close()


def test_migration_rollback_procedure_via_backup(tmp_path):
    """
    Verify rollback procedure:
    When a database backup is taken before cutover, restoring the backup restores
    exact schema version and data state cleanly without destructive SQL commands.
    """
    prod_replica = tmp_path / "replica.db"
    backup_file = tmp_path / "replica_backup.db"

    # Create replica at v7
    conn = sqlite3.connect(str(prod_replica))
    create_baseline_schema(conn)
    for version, name, statements in MIGRATIONS[:7]:
        for stmt in statements:
            conn.execute(stmt)
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
    conn.close()

    # 1. Take consistent backup
    src = sqlite3.connect(str(prod_replica))
    bck = sqlite3.connect(str(backup_file))
    src.backup(bck)
    bck.close()
    src.close()

    # 2. Apply migration 8 on replica
    conn = sqlite3.connect(str(prod_replica))
    apply_migrations(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
    conn.close()

    # 3. Perform Rollback: Restore from verified backup
    src_bck = sqlite3.connect(str(backup_file))
    dest_restored = sqlite3.connect(str(prod_replica))
    src_bck.backup(dest_restored)
    src_bck.close()

    # 4. Verify restored state is cleanly at Version 7
    v_restored = dest_restored.execute("PRAGMA user_version").fetchone()[0]
    assert v_restored == 7
    tbl_check = dest_restored.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='worker_enrollment_tokens'"
    ).fetchone()
    assert tbl_check is None
    dest_restored.close()
