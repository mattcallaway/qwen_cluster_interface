"""Versioned, transactional SQLite schema migrations.

The schema version is stored in ``PRAGMA user_version``. Version 0 is the baseline
schema created by ``app.database.create_baseline_schema`` (the pre-existing tables).

Each migration runs inside a single ``BEGIN IMMEDIATE ... COMMIT`` together with the
``user_version`` bump, followed by ``PRAGMA foreign_key_check``. Any failure rolls the
whole migration back, leaving the previous version intact.

Rules for adding migrations:
  * Never edit a migration that has shipped; add a new one.
  * Only additive changes to existing tables (``ALTER TABLE ... ADD COLUMN``) unless a
    table rebuild is unavoidable and separately reviewed.
"""
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger("qwen_collective.migrations")

Migration = Tuple[int, str, Sequence[str]]

# ---------------------------------------------------------------------------
# Migration 1 — Project Intelligence foundation (Milestone 1)
# ---------------------------------------------------------------------------
_M1: List[str] = [
    # --- Projects ---------------------------------------------------------
    """
    CREATE TABLE projects (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 200),
        description TEXT NOT NULL DEFAULT '',
        objective TEXT NOT NULL DEFAULT '',
        scope TEXT NOT NULL DEFAULT '',
        constraints_text TEXT NOT NULL DEFAULT '',
        success_criteria TEXT NOT NULL DEFAULT '',
        priority TEXT NOT NULL DEFAULT 'medium'
            CHECK (priority IN ('low','medium','high','critical')),
        status TEXT NOT NULL DEFAULT 'draft'
            CHECK (status IN ('draft','planning','active','paused','completed','cancelled','archived')),
        parent_id TEXT REFERENCES projects(id),
        owner TEXT NOT NULL,
        selected_path_id TEXT REFERENCES project_paths(id),
        knowledge_dir TEXT NOT NULL UNIQUE,
        start_date TEXT,
        target_date TEXT,
        completed_date TEXT,
        review_date TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_activity_at TEXT NOT NULL,
        CHECK (parent_id IS NULL OR parent_id <> id)
    )
    """,
    "CREATE UNIQUE INDEX ux_projects_name ON projects(name COLLATE NOCASE)",
    "CREATE INDEX idx_projects_status ON projects(status)",
    "CREATE INDEX idx_projects_parent ON projects(parent_id)",
    # --- Paths ------------------------------------------------------------
    """
    CREATE TABLE project_paths (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id),
        name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 200),
        rationale TEXT NOT NULL CHECK (length(trim(rationale)) > 0),
        description TEXT NOT NULL DEFAULT '',
        assumptions TEXT NOT NULL DEFAULT '',
        constraints_text TEXT NOT NULL DEFAULT '',
        dependencies TEXT NOT NULL DEFAULT '',
        resource_requirements TEXT NOT NULL DEFAULT '',
        risks TEXT NOT NULL DEFAULT '',
        estimated_cost REAL CHECK (estimated_cost IS NULL OR estimated_cost >= 0),
        estimated_effort_hours REAL CHECK (estimated_effort_hours IS NULL OR estimated_effort_hours >= 0),
        expected_outcome TEXT NOT NULL DEFAULT '',
        expected_completion_date TEXT,
        forecast_confidence REAL
            CHECK (forecast_confidence IS NULL OR (forecast_confidence >= 0 AND forecast_confidence <= 1)),
        status TEXT NOT NULL DEFAULT 'proposed'
            CHECK (status IN ('proposed','under_review','approved','active','paused','superseded','completed','rejected')),
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (project_id, name)
    )
    """,
    # Only one active Path per project, enforced by the database itself.
    "CREATE UNIQUE INDEX ux_one_active_path_per_project ON project_paths(project_id) WHERE status = 'active'",
    "CREATE INDEX idx_paths_project ON project_paths(project_id, status)",
    """
    CREATE TRIGGER trg_projects_selected_path_same_project
    BEFORE UPDATE OF selected_path_id ON projects
    WHEN NEW.selected_path_id IS NOT NULL
     AND (SELECT project_id FROM project_paths WHERE id = NEW.selected_path_id) IS NOT NEW.id
    BEGIN
        SELECT RAISE(ABORT, 'selected_path_id must reference a path of the same project');
    END
    """,
    # --- Stages -----------------------------------------------------------
    """
    CREATE TABLE path_stages (
        id TEXT PRIMARY KEY,
        path_id TEXT NOT NULL REFERENCES project_paths(id),
        position INTEGER NOT NULL,
        name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 200),
        objective TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        prerequisites TEXT NOT NULL DEFAULT '',
        expected_outputs TEXT NOT NULL DEFAULT '',
        completion_criteria TEXT NOT NULL DEFAULT '',
        estimated_effort_hours REAL CHECK (estimated_effort_hours IS NULL OR estimated_effort_hours >= 0),
        actual_effort_hours REAL CHECK (actual_effort_hours IS NULL OR actual_effort_hours >= 0),
        status TEXT NOT NULL DEFAULT 'planned'
            CHECK (status IN ('planned','active','blocked','awaiting_review','completed','skipped')),
        started_at TEXT,
        completed_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (path_id, position)
    )
    """,
    # --- Tasks: optional project traceability (additive only) --------------
    "ALTER TABLE tasks ADD COLUMN project_id TEXT REFERENCES projects(id)",
    "ALTER TABLE tasks ADD COLUMN path_id TEXT REFERENCES project_paths(id)",
    "ALTER TABLE tasks ADD COLUMN stage_id TEXT REFERENCES path_stages(id)",
    "ALTER TABLE tasks ADD COLUMN purpose TEXT",
    "ALTER TABLE tasks ADD COLUMN expected_output TEXT",
    "ALTER TABLE tasks ADD COLUMN completion_criteria TEXT",
    "CREATE INDEX idx_tasks_project ON tasks(project_id, status)",
    "CREATE INDEX idx_tasks_stage ON tasks(stage_id)",
    """
    CREATE TRIGGER trg_tasks_trace_insert BEFORE INSERT ON tasks
    WHEN (NEW.stage_id IS NOT NULL AND (NEW.path_id IS NULL
            OR (SELECT path_id FROM path_stages WHERE id = NEW.stage_id) IS NOT NEW.path_id))
      OR (NEW.path_id IS NOT NULL AND (NEW.project_id IS NULL
            OR (SELECT project_id FROM project_paths WHERE id = NEW.path_id) IS NOT NEW.project_id))
    BEGIN
        SELECT RAISE(ABORT, 'task project/path/stage references are inconsistent');
    END
    """,
    """
    CREATE TRIGGER trg_tasks_trace_update BEFORE UPDATE OF project_id, path_id, stage_id ON tasks
    WHEN (NEW.stage_id IS NOT NULL AND (NEW.path_id IS NULL
            OR (SELECT path_id FROM path_stages WHERE id = NEW.stage_id) IS NOT NEW.path_id))
      OR (NEW.path_id IS NOT NULL AND (NEW.project_id IS NULL
            OR (SELECT project_id FROM project_paths WHERE id = NEW.path_id) IS NOT NEW.project_id))
    BEGIN
        SELECT RAISE(ABORT, 'task project/path/stage references are inconsistent');
    END
    """,
    # --- Knowledge metadata (narrative stays in Markdown) -----------------
    """
    CREATE TABLE knowledge_metadata (
        id TEXT PRIMARY KEY,
        note_path TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        knowledge_type TEXT NOT NULL CHECK (knowledge_type IN (
            'source','finding','assumption','decision','procedure','task_result',
            'lesson_learned','forecast_rationale','observation','project_brief','general_reference')),
        origin TEXT NOT NULL CHECK (origin IN (
            'human','ai_generated','external_source','vps_extracted','machine_measurement','system')),
        trust_status TEXT NOT NULL DEFAULT 'captured' CHECK (trust_status IN (
            'captured','proposed','verified','accepted','disputed','superseded')),
        source_url TEXT,
        source_retrieved_at TEXT,
        source_citation TEXT,
        project_id TEXT REFERENCES projects(id),
        author TEXT NOT NULL,
        tags TEXT NOT NULL DEFAULT '',
        superseded_by TEXT REFERENCES knowledge_metadata(id),
        reviewed_by TEXT,
        reviewed_at TEXT,
        review_note TEXT,
        review_count INTEGER NOT NULL DEFAULT 0,
        last_verified_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK (superseded_by IS NULL OR superseded_by <> id),
        CHECK ((trust_status = 'superseded') = (superseded_by IS NOT NULL))
    )
    """,
    "CREATE INDEX idx_kmeta_project ON knowledge_metadata(project_id, trust_status)",
    # Every entry, regardless of origin, enters the staging states. Promotion is a review action.
    """
    CREATE TRIGGER trg_kmeta_insert_staged BEFORE INSERT ON knowledge_metadata
    WHEN NEW.trust_status NOT IN ('captured','proposed')
    BEGIN
        SELECT RAISE(ABORT, 'knowledge must enter as captured or proposed; promotion requires review');
    END
    """,
    """
    CREATE TRIGGER trg_kmeta_promotion_requires_reviewer BEFORE UPDATE OF trust_status ON knowledge_metadata
    WHEN NEW.trust_status IN ('verified','accepted')
     AND (NEW.reviewed_by IS NULL OR NEW.reviewed_at IS NULL OR NEW.review_count <> OLD.review_count + 1)
    BEGIN
        SELECT RAISE(ABORT, 'promotion to verified/accepted requires a new recorded review');
    END
    """,
    # --- Knowledge relations (explicit, typed) ----------------------------
    """
    CREATE TABLE knowledge_relations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        knowledge_id TEXT NOT NULL REFERENCES knowledge_metadata(id),
        target_type TEXT NOT NULL CHECK (target_type IN ('project','path','stage','task','knowledge')),
        target_id TEXT NOT NULL,
        relation_type TEXT NOT NULL CHECK (relation_type IN (
            'about','supports','challenges','evidence_for','rationale_for',
            'result_of','procedure_for','derived_from','related_to')),
        note TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (knowledge_id, target_type, target_id, relation_type),
        CHECK (NOT (target_type = 'knowledge' AND target_id = knowledge_id))
    )
    """,
    "CREATE INDEX idx_krel_target ON knowledge_relations(target_type, target_id)",
    # --- Append-only project decisions -------------------------------------
    """
    CREATE TABLE project_decisions (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id),
        decision_type TEXT NOT NULL CHECK (decision_type IN (
            'path_selection','path_status_change','project_status_change','project_parent_change')),
        subject_id TEXT,
        from_value TEXT,
        to_value TEXT,
        rationale TEXT NOT NULL CHECK (length(trim(rationale)) > 0),
        decided_by TEXT NOT NULL,
        decided_at TEXT NOT NULL,
        evidence_knowledge_id TEXT REFERENCES knowledge_metadata(id)
    )
    """,
    "CREATE INDEX idx_decisions_project ON project_decisions(project_id, decided_at)",
    """
    CREATE TRIGGER trg_decisions_no_update BEFORE UPDATE ON project_decisions
    BEGIN SELECT RAISE(ABORT, 'project_decisions is append-only'); END
    """,
    """
    CREATE TRIGGER trg_decisions_no_delete BEFORE DELETE ON project_decisions
    BEGIN SELECT RAISE(ABORT, 'project_decisions is append-only'); END
    """,
]

# ---------------------------------------------------------------------------
# Migration 2 — Execution and evidence (Milestone 2)
# ---------------------------------------------------------------------------
_M2: List[str] = [
    # --- Task dependencies ---
    """
    CREATE TABLE task_dependencies (
        task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        depends_on_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        PRIMARY KEY (task_id, depends_on_task_id),
        CHECK (task_id <> depends_on_task_id)
    )
    """,
    "CREATE INDEX idx_task_deps_dep ON task_dependencies(depends_on_task_id)",

    # --- Deliverables ---
    """
    CREATE TABLE deliverables (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id),
        path_id TEXT REFERENCES project_paths(id),
        stage_id TEXT REFERENCES path_stages(id),
        name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 200),
        description TEXT NOT NULL DEFAULT '',
        deliverable_type TEXT NOT NULL CHECK (deliverable_type IN (
            'document','dataset','report','software_artifact','configuration',
            'research_result','published_material','completed_workflow','other')),
        acceptance_criteria TEXT NOT NULL DEFAULT '',
        due_date TEXT,
        status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN (
            'planned','in_progress','submitted','changes_requested','accepted','rejected')),
        artifact_references TEXT NOT NULL DEFAULT '',
        verification_results TEXT NOT NULL DEFAULT '',
        acceptance_decision TEXT NOT NULL DEFAULT '',
        accepted_by TEXT,
        accepted_at TEXT,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_deliverables_proj ON deliverables(project_id, status)",
    "CREATE INDEX idx_deliverables_stage ON deliverables(stage_id)",

    # --- Deliverable revision history ---
    """
    CREATE TABLE deliverable_revisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        deliverable_id TEXT NOT NULL REFERENCES deliverables(id) ON DELETE CASCADE,
        revision_number INTEGER NOT NULL,
        status TEXT NOT NULL,
        artifact_references TEXT NOT NULL DEFAULT '',
        verification_results TEXT NOT NULL DEFAULT '',
        decision_rationale TEXT NOT NULL DEFAULT '',
        decided_by TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_deliv_rev_del ON deliverable_revisions(deliverable_id, revision_number)",

    # --- Task reference to deliverable ---
    "ALTER TABLE tasks ADD COLUMN deliverable_id TEXT REFERENCES deliverables(id)",
    "CREATE INDEX idx_tasks_deliverable ON tasks(deliverable_id)",

    # --- Task execution operational events ---
    """
    CREATE TABLE task_execution_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        attempt_number INTEGER NOT NULL DEFAULT 1,
        outcome TEXT NOT NULL CHECK (outcome IN ('completed','failed','aborted')),
        attempted TEXT NOT NULL DEFAULT '',
        actual_result TEXT NOT NULL DEFAULT '',
        evidence_summary TEXT NOT NULL DEFAULT '',
        assumptions_feedback TEXT NOT NULL DEFAULT '',
        error_details TEXT NOT NULL DEFAULT '',
        reusable_lessons TEXT NOT NULL DEFAULT '',
        resulting_knowledge_id TEXT REFERENCES knowledge_metadata(id),
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_task_exec_task ON task_execution_events(task_id)",

    # --- Path revision history ---
    """
    CREATE TABLE path_revisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        path_id TEXT NOT NULL REFERENCES project_paths(id) ON DELETE CASCADE,
        revision_number INTEGER NOT NULL,
        name TEXT NOT NULL,
        rationale TEXT NOT NULL,
        assumptions TEXT NOT NULL DEFAULT '',
        risks TEXT NOT NULL DEFAULT '',
        dependencies TEXT NOT NULL DEFAULT '',
        resource_requirements TEXT NOT NULL DEFAULT '',
        estimated_cost REAL,
        estimated_effort_hours REAL,
        expected_outcome TEXT NOT NULL DEFAULT '',
        forecast_confidence REAL,
        revision_reason TEXT NOT NULL,
        revised_by TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_path_rev_path ON path_revisions(path_id, revision_number)",

    # --- Knowledge promotion to collective memory ---
    "ALTER TABLE knowledge_metadata ADD COLUMN is_collective INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE knowledge_metadata ADD COLUMN promoted_by TEXT",
    "ALTER TABLE knowledge_metadata ADD COLUMN promoted_at TEXT",
    "CREATE INDEX idx_kmeta_collective ON knowledge_metadata(is_collective) WHERE is_collective = 1",

    # --- Rebuild project_decisions with expanded decision types ---
    """
    CREATE TABLE project_decisions_new (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id),
        decision_type TEXT NOT NULL CHECK (decision_type IN (
            'path_selection','path_status_change','project_status_change','project_parent_change',
            'stage_advancement','stage_status_change','deliverable_acceptance','path_revision','knowledge_promotion')),
        subject_id TEXT,
        from_value TEXT,
        to_value TEXT,
        rationale TEXT NOT NULL CHECK (length(trim(rationale)) > 0),
        decided_by TEXT NOT NULL,
        decided_at TEXT NOT NULL,
        evidence_knowledge_id TEXT REFERENCES knowledge_metadata(id)
    )
    """,
    "INSERT INTO project_decisions_new SELECT * FROM project_decisions",
    "DROP TABLE project_decisions",
    "ALTER TABLE project_decisions_new RENAME TO project_decisions",
    "CREATE INDEX idx_decisions_project ON project_decisions(project_id, decided_at)",
    """
    CREATE TRIGGER trg_decisions_no_update BEFORE UPDATE ON project_decisions
    BEGIN SELECT RAISE(ABORT, 'project_decisions is append-only'); END
    """,
    """
    CREATE TRIGGER trg_decisions_no_delete BEFORE DELETE ON project_decisions
    BEGIN SELECT RAISE(ABORT, 'project_decisions is append-only'); END
    """,
]

# ---------------------------------------------------------------------------
# Migration 3 — Measurement and forecasting (Milestone 3)
# ---------------------------------------------------------------------------
_M3: List[str] = [
    # --- Project Metrics --------------------------------------------------
    """
    CREATE TABLE project_metrics (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id),
        path_id TEXT REFERENCES project_paths(id),
        name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 200),
        description TEXT NOT NULL DEFAULT '',
        metric_type TEXT NOT NULL CHECK (metric_type IN (
            'count','percentage','rate','duration','currency','quality','reliability','other')),
        unit TEXT NOT NULL CHECK (length(trim(unit)) BETWEEN 1 AND 50),
        measurement_method TEXT NOT NULL DEFAULT 'manual' CHECK (measurement_method IN (
            'automated_query','manual_audit','task_telemetry','benchmark','manual')),
        source TEXT NOT NULL DEFAULT '',
        collection_frequency TEXT NOT NULL DEFAULT 'ad_hoc' CHECK (collection_frequency IN (
            'per_task','daily','weekly','stage_gate','ad_hoc')),
        target_value REAL,
        target_date TEXT,
        direction_of_improvement TEXT NOT NULL DEFAULT 'higher_is_better' CHECK (direction_of_improvement IN (
            'higher_is_better','lower_is_better','target_is_optimal')),
        owner TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (project_id, name)
    )
    """,
    "CREATE INDEX idx_metrics_proj ON project_metrics(project_id, status)",
    "CREATE INDEX idx_metrics_path ON project_metrics(path_id)",

    # --- Actual Observations (Immutable, Timestamped, Auditable) ----------
    """
    CREATE TABLE metric_observations (
        id TEXT PRIMARY KEY,
        metric_id TEXT NOT NULL REFERENCES project_metrics(id),
        value REAL NOT NULL,
        measured_at TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '',
        collection_method TEXT NOT NULL DEFAULT '',
        verification_status TEXT NOT NULL DEFAULT 'unverified' CHECK (verification_status IN (
            'unverified','verified','rejected')),
        evidence_reference TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        supersedes_id TEXT REFERENCES metric_observations(id),
        correction_reason TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL,
        CHECK (supersedes_id IS NULL OR supersedes_id <> id),
        CHECK ((supersedes_id IS NOT NULL) = (length(trim(correction_reason)) > 0))
    )
    """,
    "CREATE INDEX idx_obs_metric ON metric_observations(metric_id, measured_at)",
    "CREATE INDEX idx_obs_supersedes ON metric_observations(supersedes_id)",
    """
    CREATE TRIGGER trg_observations_no_update BEFORE UPDATE ON metric_observations
    BEGIN SELECT RAISE(ABORT, 'metric_observations are immutable; record a correction that supersedes'); END
    """,
    """
    CREATE TRIGGER trg_observations_no_delete BEFORE DELETE ON metric_observations
    BEGIN SELECT RAISE(ABORT, 'metric_observations are immutable'); END
    """,

    # --- Versioned Forecasts (Predictions, Preserved History) -------------
    """
    CREATE TABLE path_forecasts (
        id TEXT PRIMARY KEY,
        metric_id TEXT NOT NULL REFERENCES project_metrics(id),
        path_id TEXT REFERENCES project_paths(id),
        version INTEGER NOT NULL CHECK (version >= 1),
        prediction_period TEXT NOT NULL DEFAULT '',
        target_date TEXT,
        predicted_value REAL NOT NULL,
        method TEXT NOT NULL CHECK (method IN (
            'linear_trend','rolling_average','rate_extrapolation','manual_estimate','model_estimate')),
        assumptions TEXT NOT NULL DEFAULT '',
        evidence TEXT NOT NULL DEFAULT '',
        confidence_score REAL CHECK (confidence_score IS NULL OR (confidence_score >= 0.0 AND confidence_score <= 1.0)),
        confidence_level TEXT NOT NULL DEFAULT 'medium' CHECK (confidence_level IN ('low','medium','high')),
        revision_reason TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        CHECK (version = 1 OR length(trim(revision_reason)) > 0)
    )
    """,
    "CREATE UNIQUE INDEX ux_forecast_version ON path_forecasts(metric_id, COALESCE(path_id, ''), version)",
    "CREATE INDEX idx_forecasts_metric ON path_forecasts(metric_id, version)",
    "CREATE INDEX idx_forecasts_path ON path_forecasts(path_id)",
    """
    CREATE TRIGGER trg_forecasts_no_update BEFORE UPDATE ON path_forecasts
    BEGIN SELECT RAISE(ABORT, 'path_forecasts are versioned and immutable'); END
    """,
    """
    CREATE TRIGGER trg_forecasts_no_delete BEFORE DELETE ON path_forecasts
    BEGIN SELECT RAISE(ABORT, 'path_forecasts are versioned and immutable'); END
    """,

    # --- Historical Forecast Evaluations & Accuracy Tracking -------------
    """
    CREATE TABLE forecast_evaluations (
        id TEXT PRIMARY KEY,
        forecast_id TEXT NOT NULL REFERENCES path_forecasts(id),
        metric_id TEXT NOT NULL REFERENCES project_metrics(id),
        path_id TEXT REFERENCES project_paths(id),
        actual_observation_id TEXT REFERENCES metric_observations(id),
        target_value REAL,
        predicted_value REAL NOT NULL,
        actual_value REAL NOT NULL,
        variance REAL NOT NULL,
        absolute_error REAL NOT NULL,
        percentage_error REAL,
        accuracy_score REAL NOT NULL CHECK (accuracy_score >= 0.0 AND accuracy_score <= 1.0),
        status_assessment TEXT NOT NULL CHECK (status_assessment IN (
            'on_target','ahead_of_forecast','behind_forecast','at_risk','insufficient_data')),
        evaluation_notes TEXT NOT NULL DEFAULT '',
        evaluated_by TEXT NOT NULL,
        evaluated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_eval_metric ON forecast_evaluations(metric_id, evaluated_at)",
    "CREATE INDEX idx_eval_path ON forecast_evaluations(path_id)",
    "CREATE INDEX idx_eval_forecast ON forecast_evaluations(forecast_id)",
]

# ---------------------------------------------------------------------------
# Migration 4 — Project-aware intelligence (Milestone 4)
# ---------------------------------------------------------------------------
_M4: List[str] = [
    # --- Project proposals (AI proposals requiring human authorization) ----
    """
    CREATE TABLE project_proposals (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id),
        path_id TEXT REFERENCES project_paths(id),
        stage_id TEXT REFERENCES path_stages(id),
        proposal_type TEXT NOT NULL CHECK (proposal_type IN (
            'path_proposal','task_decomposition','metric_suggestion','path_review')),
        title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 200),
        summary TEXT NOT NULL DEFAULT '',
        proposed_data_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN (
            'proposed','under_review','accepted','rejected','cancelled')),
        ai_model TEXT NOT NULL DEFAULT 'qwen',
        prompt_tokens INTEGER,
        completion_tokens INTEGER,
        context_packet_summary TEXT NOT NULL DEFAULT '',
        review_notes TEXT NOT NULL DEFAULT '',
        reviewed_by TEXT,
        reviewed_at TEXT,
        created_by TEXT NOT NULL DEFAULT 'ai_assistant',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_prop_project ON project_proposals(project_id, status)",
    "CREATE INDEX idx_prop_type ON project_proposals(project_id, proposal_type)",

    # --- Rebuild project_decisions with proposal_acceptance ---
    """
    CREATE TABLE project_decisions_m4 (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id),
        decision_type TEXT NOT NULL CHECK (decision_type IN (
            'path_selection','path_status_change','project_status_change','project_parent_change',
            'stage_advancement','stage_status_change','deliverable_acceptance','path_revision','knowledge_promotion',
            'proposal_acceptance')),
        subject_id TEXT,
        from_value TEXT,
        to_value TEXT,
        rationale TEXT NOT NULL CHECK (length(trim(rationale)) > 0),
        decided_by TEXT NOT NULL,
        decided_at TEXT NOT NULL,
        evidence_knowledge_id TEXT REFERENCES knowledge_metadata(id)
    )
    """,
    "INSERT INTO project_decisions_m4 SELECT * FROM project_decisions",
    "DROP TABLE project_decisions",
    "ALTER TABLE project_decisions_m4 RENAME TO project_decisions",
    "CREATE INDEX idx_decisions_project ON project_decisions(project_id, decided_at)",
    """
    CREATE TRIGGER trg_decisions_no_update BEFORE UPDATE ON project_decisions
    BEGIN SELECT RAISE(ABORT, 'project_decisions is append-only'); END
    """,
    """
    CREATE TRIGGER trg_decisions_no_delete BEFORE DELETE ON project_decisions
    BEGIN SELECT RAISE(ABORT, 'project_decisions is append-only'); END
    """,
]

MIGRATIONS: List[Migration] = [
    (1, "project intelligence foundation", _M1),
    (2, "execution and evidence", _M2),
    (3, "measurement and forecasting", _M3),
    (4, "project-aware intelligence", _M4),
]



def current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def latest_version(migrations: Sequence[Migration] = MIGRATIONS) -> int:
    return max((m[0] for m in migrations), default=0)


def apply_migrations(conn: sqlite3.Connection, migrations: Sequence[Migration] = MIGRATIONS) -> List[int]:
    """Apply pending migrations in order. Each is atomic. Returns applied versions."""
    applied: List[int] = []
    previous_isolation = conn.isolation_level
    conn.isolation_level = None  # explicit transaction control
    try:
        for version, name, statements in sorted(migrations, key=lambda m: m[0]):
            if version <= current_version(conn):
                continue
            conn.execute("BEGIN IMMEDIATE")
            try:
                for stmt in statements:
                    conn.execute(stmt)
                conn.execute(f"PRAGMA user_version = {int(version)}")
                violations = conn.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise RuntimeError(f"foreign_key_check failed after migration {version}: {violations[:5]}")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                logger.error("Migration %s (%s) failed and was rolled back.", version, name)
                raise
            logger.info("Applied schema migration %s: %s", version, name)
            applied.append(version)
    finally:
        conn.isolation_level = previous_isolation
    return applied


def backup_before_migration(conn: sqlite3.Connection, db_path: Path,
                            migrations: Sequence[Migration] = MIGRATIONS) -> Optional[Path]:
    """Snapshot an existing database with the SQLite online backup API before migrating it."""
    cur, target = current_version(conn), latest_version(migrations)
    if cur >= target:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_path = db_path.with_name(f"{db_path.stem}.pre-migration-v{cur}-to-v{target}-{stamp}.db")
    dest = sqlite3.connect(dest_path)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    logger.info("Pre-migration backup written: %s", dest_path)
    return dest_path
