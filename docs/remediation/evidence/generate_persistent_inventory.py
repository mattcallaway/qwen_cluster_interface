import os
import json
import sqlite3
from pathlib import Path

user_home = Path.home() / '.qwen-collective' / 'data'
db_path = user_home / 'qwen_collective.db'
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

inventory = {
    'database_path': str(db_path),
    'infrastructure_nodes': [dict(r) for r in conn.execute("SELECT * FROM infrastructure_nodes WHERE id IN ('worker-A', 'worker-B')").fetchall()],
    'worker_auth_tokens': [dict(r) for r in conn.execute("SELECT * FROM worker_auth_tokens WHERE node_id IN ('worker-A', 'worker-B')").fetchall()],
    'projects': [dict(r) for r in conn.execute("SELECT * FROM projects WHERE id IN ('prj-b369e83d93bf4680a4e4df3a97cab305', 'prj-82ca6f9c852142c7a3d3364cd17b9b83')").fetchall()],
    'tasks': [dict(r) for r in conn.execute("SELECT * FROM tasks WHERE id = 'task-assigned-to-A'").fetchall()],
    'worker_task_leases': [dict(r) for r in conn.execute("SELECT * FROM worker_task_leases WHERE id = 'lease-expired'").fetchall()],
    'artifacts': [dict(r) for r in conn.execute("SELECT id, display_name, checksum_sha256, origin_task_id, storage_key FROM artifacts WHERE origin_task_id = 'task-assigned-to-A'").fetchall()],
    'artifact_versions': [dict(r) for r in conn.execute("SELECT id, artifact_id, version_number, checksum_sha256 FROM artifact_versions WHERE artifact_id IN (SELECT id FROM artifacts WHERE origin_task_id = 'task-assigned-to-A')").fetchall()],
    'evidence_files': []
}
conn.close()

ev_dir = user_home / 'evidence' / 'task_task-assigned-to-A'
if ev_dir.exists():
    inventory['evidence_files'] = [str(f.resolve()) for f in ev_dir.iterdir()]

os.makedirs('docs/remediation/evidence', exist_ok=True)
with open('docs/remediation/evidence/persistent_test_inventory.json', 'w') as f:
    json.dump(inventory, f, indent=2)

print('Inventory written successfully. Summary:')
print('Nodes:', len(inventory['infrastructure_nodes']))
print('Tokens:', len(inventory['worker_auth_tokens']))
print('Projects:', len(inventory['projects']))
print('Tasks:', len(inventory['tasks']))
print('Leases:', len(inventory['worker_task_leases']))
print('Artifacts:', len(inventory['artifacts']))
print('Versions:', len(inventory['artifact_versions']))
print('Evidence files:', len(inventory['evidence_files']))
