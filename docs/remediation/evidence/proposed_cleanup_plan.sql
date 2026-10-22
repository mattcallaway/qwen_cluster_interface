-- ============================================================================
-- PROPOSED CLEANUP SCRIPT FOR TEST-GENERATED ARTIFACTS IN PERSISTENT DATABASE
-- Target: C:\Users\Matt C\.qwen-collective\data\qwen_collective.db
-- STATUS: PENDING HUMAN REVIEW AND EXPLICIT APPROVAL
-- DO NOT RUN AUTOMATICALLY WITHOUT PRIOR BACKUP
-- ============================================================================

BEGIN TRANSACTION;

-- 1. Remove test artifact versions and artifacts
DELETE FROM artifact_versions WHERE artifact_id IN (
    SELECT id FROM artifacts WHERE origin_task_id = 'task-assigned-to-A'
);
DELETE FROM artifacts WHERE origin_task_id = 'task-assigned-to-A';

-- 2. Remove test worker leases and tasks
DELETE FROM worker_task_leases WHERE id = 'lease-expired' OR task_id = 'task-assigned-to-A';
DELETE FROM tasks WHERE id = 'task-assigned-to-A';

-- 3. Remove test projects
DELETE FROM projects WHERE id IN ('prj-b369e83d93bf4680a4e4df3a97cab305', 'prj-82ca6f9c852142c7a3d3364cd17b9b83');

-- 4. Remove test worker auth tokens and nodes
DELETE FROM worker_auth_tokens WHERE node_id IN ('worker-A', 'worker-B');
DELETE FROM infrastructure_nodes WHERE id IN ('worker-A', 'worker-B');

COMMIT;
