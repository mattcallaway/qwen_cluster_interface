import os
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(".").resolve()))

backup_dir = Path("data/backups/stage06_preflight_backup")
backup_dir.mkdir(parents=True, exist_ok=True)

# 1. Backup SQLite database if exists
from app.config import settings
from app.database import init_db, get_db_connection

live_db = settings.db_path
print(f"Configured database path: {live_db}")

if not live_db.exists():
    print(f"Database {live_db} does not exist yet. Initializing schema to ensure clean verified baseline...")
    init_db()

db_backup = backup_dir / f"{live_db.name}.preflight.db"
src_conn = sqlite3.connect(live_db)
dst_conn = sqlite3.connect(db_backup)
with dst_conn:
    src_conn.backup(dst_conn)
src_conn.close()
dst_conn.close()
print(f"Database backed up to {db_backup} ({db_backup.stat().st_size} bytes)")

# 2. Backup existing artifacts, evidence, and research sources
artifacts_dir = Path("data/artifacts")
evidence_dir = Path("data/evidence")
sources_dir = Path("data/research_sources")

if artifacts_dir.exists():
    dst = backup_dir / "artifacts"
    shutil.copytree(artifacts_dir, dst, dirs_exist_ok=True)
    print(f"Artifacts backed up to {dst}")

if evidence_dir.exists():
    dst = backup_dir / "evidence"
    shutil.copytree(evidence_dir, dst, dirs_exist_ok=True)
    print(f"Evidence backed up to {dst}")

if sources_dir.exists():
    dst = backup_dir / "research_sources"
    shutil.copytree(sources_dir, dst, dirs_exist_ok=True)
    print(f"Research sources backed up to {dst}")

# 3. Verify sample restore into a separate scratch db
test_restore_db = backup_dir / "test_verify_restore.db"
rst_conn = sqlite3.connect(test_restore_db)
src_bk = sqlite3.connect(db_backup)
with rst_conn:
    src_bk.backup(rst_conn)
src_bk.close()

# Verify integrity and tables
cur = rst_conn.cursor()
tables = [row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
runs_cnt = cur.execute("SELECT count(*) FROM orchestration_runs").fetchone()[0]
srcs_cnt = cur.execute("SELECT count(*) FROM research_sources").fetchone()[0]
schema_ver = cur.execute("PRAGMA user_version").fetchone()[0]
rst_conn.close()

print(f"Sample restore verification SUCCESS: Schema v{schema_ver}, {len(tables)} tables, {runs_cnt} runs, {srcs_cnt} sources.")

