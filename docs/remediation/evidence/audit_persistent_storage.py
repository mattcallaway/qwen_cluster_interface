import os
import sqlite3
from pathlib import Path

user_home_db = Path.home() / '.qwen-collective' / 'data' / 'qwen_collective.db'
local_db = Path('./data/qwen_collective.db').resolve()

print(f"Checking user_home_db: {user_home_db} (exists: {user_home_db.exists()})")
if user_home_db.exists():
    conn = sqlite3.connect(user_home_db)
    workers = conn.execute("SELECT id, name, role FROM infrastructure_nodes WHERE id IN ('worker-A', 'worker-B')").fetchall()
    print("user_home_db infrastructure_nodes:", workers)
    tokens = conn.execute("SELECT id, node_id FROM worker_auth_tokens WHERE node_id IN ('worker-A', 'worker-B')").fetchall()
    print("user_home_db worker_auth_tokens:", tokens)
    projects = conn.execute("SELECT id, name FROM projects WHERE name LIKE 'Test Project%'").fetchall()
    print("user_home_db projects:", projects)
    tasks = conn.execute("SELECT id, title FROM tasks WHERE id = 'task-assigned-to-A'").fetchall()
    print("user_home_db tasks:", tasks)
    leases = conn.execute("SELECT id, task_id, worker_id FROM worker_task_leases WHERE id = 'lease-expired'").fetchall()
    print("user_home_db leases:", leases)
    art_cols = [c[1] for c in conn.execute("PRAGMA table_info(artifacts)").fetchall()]
    print("artifacts table columns:", art_cols)
    if "task_id" in art_cols:
        artifacts = conn.execute("SELECT * FROM artifacts WHERE task_id = 'task-assigned-to-A'").fetchall()
    else:
        artifacts = conn.execute("SELECT * FROM artifacts").fetchall()
    print("user_home_db artifacts count for test:", len(artifacts))
    conn.close()

user_home_evidence = Path.home() / '.qwen-collective' / 'data' / 'evidence' / 'task_task-assigned-to-A'
print(f"Checking user_home_evidence: {user_home_evidence} (exists: {user_home_evidence.exists()})")
if user_home_evidence.exists():
    files = [str(f.name) for f in user_home_evidence.iterdir()]
    print("Files in user_home_evidence:", files)

print(f"Checking local_db: {local_db} (exists: {local_db.exists()})")
if local_db.exists():
    conn = sqlite3.connect(local_db)
    workers = conn.execute("SELECT id, name, role FROM infrastructure_nodes WHERE id IN ('worker-A', 'worker-B')").fetchall()
    print("local_db infrastructure_nodes:", workers)
    projects = conn.execute("SELECT id, name FROM projects WHERE name LIKE 'Test Project%'").fetchall()
    print("local_db projects:", projects)
    conn.close()
