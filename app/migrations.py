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

# ---------------------------------------------------------------------------
# Migration 5 — Historical learning and performance evaluation (Milestone 5)
# ---------------------------------------------------------------------------
_M5: List[str] = [
    # --- Project retrospectives (Lifecycle synthesis and collective lessons)
    """
    CREATE TABLE project_retrospectives (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        title TEXT NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 200),
        summary TEXT NOT NULL CHECK (length(trim(summary)) > 0),
        executive_summary TEXT NOT NULL DEFAULT '',
        key_achievements TEXT NOT NULL DEFAULT '',
        challenges_and_blockers TEXT NOT NULL DEFAULT '',
        lessons_learned TEXT NOT NULL DEFAULT '',
        recommendations TEXT NOT NULL DEFAULT '',
        model_performance_summary TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','reviewed','published')),
        knowledge_path TEXT,
        ai_model TEXT NOT NULL DEFAULT 'qwen',
        created_by TEXT NOT NULL,
        reviewed_by TEXT,
        reviewed_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (project_id, title)
    )
    """,
    "CREATE INDEX idx_retro_project ON project_retrospectives(project_id, status)",

    # --- Deliverable outcome evaluations (Long-term impact verification) ---
    """
    CREATE TABLE deliverable_evaluations (
        id TEXT PRIMARY KEY,
        deliverable_id TEXT NOT NULL REFERENCES deliverables(id) ON DELETE CASCADE,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        evaluation_type TEXT NOT NULL CHECK (evaluation_type IN (
            'immediate_acceptance','post_delivery_impact','operational_audit')),
        outcome_status TEXT NOT NULL CHECK (outcome_status IN (
            'exceeded_expectations','met_expectations','partially_met','failed_to_impact','inconclusive')),
        impact_summary TEXT NOT NULL CHECK (length(trim(impact_summary)) > 0),
        metric_observation_id TEXT REFERENCES metric_observations(id),
        evidence_reference TEXT NOT NULL DEFAULT '',
        lessons_learned TEXT NOT NULL DEFAULT '',
        evaluated_by TEXT NOT NULL,
        evaluated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_deval_deliv ON deliverable_evaluations(deliverable_id)",
    "CREATE INDEX idx_deval_proj ON deliverable_evaluations(project_id)",

    # --- Rebuild project_decisions with retrospective_publication and deliverable_outcome_evaluation ---
    """
    CREATE TABLE project_decisions_m5 (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id),
        decision_type TEXT NOT NULL CHECK (decision_type IN (
            'path_selection','path_status_change','project_status_change','project_parent_change',
            'stage_advancement','stage_status_change','deliverable_acceptance','path_revision','knowledge_promotion',
            'proposal_acceptance','retrospective_publication','deliverable_outcome_evaluation')),
        subject_id TEXT,
        from_value TEXT,
        to_value TEXT,
        rationale TEXT NOT NULL CHECK (length(trim(rationale)) > 0),
        decided_by TEXT NOT NULL,
        decided_at TEXT NOT NULL,
        evidence_knowledge_id TEXT REFERENCES knowledge_metadata(id)
    )
    """,
    "INSERT INTO project_decisions_m5 SELECT * FROM project_decisions",
    "DROP TABLE project_decisions",
    "ALTER TABLE project_decisions_m5 RENAME TO project_decisions",
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

# --- Milestone 1 (Infrastructure Awareness & Capability Registry) -----------
_M6 = [
    # 1. Infrastructure Nodes
    """
    CREATE TABLE infrastructure_nodes (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN (
            'controller', 'vps_worker', 'inference_node', 'compute_worker', 'general_worker'
        )),
        hostname TEXT NOT NULL DEFAULT '',
        ip_address TEXT NOT NULL DEFAULT '',
        tailscale_ip TEXT NOT NULL DEFAULT '',
        tailscale_status TEXT NOT NULL DEFAULT 'unknown' CHECK (tailscale_status IN (
            'connected', 'disconnected', 'unknown'
        )),
        os_name TEXT NOT NULL DEFAULT '',
        os_version TEXT NOT NULL DEFAULT '',
        architecture TEXT NOT NULL DEFAULT '',
        cpu_info TEXT NOT NULL DEFAULT '',
        installed_ram_mb INTEGER,
        available_ram_mb INTEGER,
        storage_total_gb REAL,
        storage_free_gb REAL,
        installed_runtimes_json TEXT NOT NULL DEFAULT '[]',
        registered_services_json TEXT NOT NULL DEFAULT '[]',
        available_models_json TEXT NOT NULL DEFAULT '[]',
        health_status TEXT NOT NULL DEFAULT 'unknown' CHECK (health_status IN (
            'online', 'offline', 'degraded', 'unknown'
        )),
        last_contact_at TEXT,
        last_inventory_at TEXT,
        capability_restrictions_json TEXT NOT NULL DEFAULT '{}',
        notes TEXT NOT NULL DEFAULT '',
        is_enabled INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0, 1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_infra_nodes_role ON infrastructure_nodes(role)",
    "CREATE INDEX idx_infra_nodes_health ON infrastructure_nodes(health_status)",

    # 2. Infrastructure Capabilities
    """
    CREATE TABLE infrastructure_capabilities (
        id TEXT PRIMARY KEY,
        node_id TEXT NOT NULL REFERENCES infrastructure_nodes(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL CHECK (category IN (
            'ai_inference', 'web_retrieval', 'search', 'browser_automation',
            'python_execution', 'approved_shell_execution', 'file_processing',
            'document_extraction', 'data_transformation', 'api_integration',
            'scheduled_execution', 'persistent_service', 'monitoring',
            'software_deployment', 'reporting'
        )),
        required_software TEXT NOT NULL DEFAULT '',
        supported_operations_json TEXT NOT NULL DEFAULT '[]',
        input_schema_json TEXT NOT NULL DEFAULT '{}',
        output_schema_json TEXT NOT NULL DEFAULT '{}',
        version TEXT NOT NULL DEFAULT '1.0.0',
        discovery_status TEXT NOT NULL DEFAULT 'declared' CHECK (discovery_status IN (
            'declared', 'discovered', 'missing'
        )),
        discovery_method TEXT NOT NULL DEFAULT 'config_declared' CHECK (discovery_method IN (
            'config_declared', 'agent_probe', 'hardware_scan', 'local_inspection'
        )),
        discovered_at TEXT,
        verification_status TEXT NOT NULL DEFAULT 'unverified' CHECK (verification_status IN (
            'unverified', 'verified', 'failed', 'revoked'
        )),
        verification_method TEXT NOT NULL DEFAULT 'side_effect_free_probe' CHECK (verification_method IN (
            'dry_run_test', 'side_effect_free_probe', 'manual_audit', 'health_check'
        )),
        last_verified_at TEXT,
        verification_error TEXT NOT NULL DEFAULT '',
        operational_status TEXT NOT NULL DEFAULT 'active' CHECK (operational_status IN (
            'active', 'disabled', 'degraded', 'unavailable'
        )),
        required_permissions_json TEXT NOT NULL DEFAULT '[]',
        approval_policy TEXT NOT NULL DEFAULT 'always_requires_human_approval' CHECK (approval_policy IN (
            'always_requires_human_approval', 'pre_authorized', 'restricted'
        )),
        resource_limits_json TEXT NOT NULL DEFAULT '{}',
        security_classification TEXT NOT NULL DEFAULT 'isolated_sandbox' CHECK (security_classification IN (
            'trusted_internal', 'isolated_sandbox', 'untrusted_external'
        )),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_infra_caps_node ON infrastructure_capabilities(node_id)",
    "CREATE INDEX idx_infra_caps_category ON infrastructure_capabilities(category)",
    "CREATE INDEX idx_infra_caps_verification ON infrastructure_capabilities(verification_status)",
    "CREATE INDEX idx_infra_caps_operational ON infrastructure_capabilities(operational_status)",

    # 3. Infrastructure Reconciliations (Architecture Drift & Discrepancies)
    """
    CREATE TABLE infrastructure_reconciliations (
        id TEXT PRIMARY KEY,
        node_id TEXT REFERENCES infrastructure_nodes(id) ON DELETE SET NULL,
        capability_id TEXT REFERENCES infrastructure_capabilities(id) ON DELETE SET NULL,
        discrepancy_type TEXT NOT NULL CHECK (discrepancy_type IN (
            'missing_capability', 'unexpected_service', 'version_drift',
            'health_degraded', 'unregistered_job', 'verification_failure'
        )),
        description TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'warning' CHECK (severity IN ('info', 'warning', 'critical')),
        status TEXT NOT NULL DEFAULT 'detected' CHECK (status IN ('detected', 'reviewed', 'resolved', 'ignored')),
        detected_at TEXT NOT NULL,
        resolved_at TEXT,
        resolution_notes TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX idx_infra_recon_status ON infrastructure_reconciliations(status)",
    "CREATE INDEX idx_infra_recon_node ON infrastructure_reconciliations(node_id)",

    # 4. Infrastructure Capability Verifications (Audit Log of Side-Effect Free Probes)
    """
    CREATE TABLE infrastructure_verifications (
        id TEXT PRIMARY KEY,
        capability_id TEXT NOT NULL REFERENCES infrastructure_capabilities(id) ON DELETE CASCADE,
        node_id TEXT NOT NULL REFERENCES infrastructure_nodes(id) ON DELETE CASCADE,
        verification_test TEXT NOT NULL,
        result_status TEXT NOT NULL CHECK (result_status IN ('passed', 'failed', 'error')),
        details TEXT NOT NULL DEFAULT '',
        verified_at TEXT NOT NULL,
        verified_by TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_infra_verif_cap ON infrastructure_verifications(capability_id, verified_at)",
]

# ---------------------------------------------------------------------------
# Migration 7 — VPS Worker Communication, Task Leasing & Execution History (Milestone 2)
# ---------------------------------------------------------------------------
_M7: List[str] = [
    # 1. Dedicated Worker Authentication Tokens (Scoped Bearer Tokens per Machine)
    """
    CREATE TABLE worker_auth_tokens (
        id TEXT PRIMARY KEY,
        node_id TEXT NOT NULL REFERENCES infrastructure_nodes(id) ON DELETE CASCADE,
        token_hash TEXT NOT NULL,
        token_prefix TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_used_at TEXT,
        revoked INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0, 1))
    )
    """,
    "CREATE INDEX idx_worker_tokens_node ON worker_auth_tokens(node_id)",
    "CREATE INDEX idx_worker_tokens_hash ON worker_auth_tokens(token_hash)",

    # 2. Worker Task Leases (Durable, Atomic Leasing Protocol)
    """
    CREATE TABLE worker_task_leases (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        worker_id TEXT NOT NULL REFERENCES infrastructure_nodes(id) ON DELETE CASCADE,
        idempotency_key TEXT UNIQUE NOT NULL,
        status TEXT NOT NULL DEFAULT 'leased' CHECK (status IN (
            'leased', 'active', 'completed', 'failed', 'expired', 'cancelled'
        )),
        leased_at TEXT NOT NULL,
        lease_expires_at TEXT NOT NULL,
        heartbeat_at TEXT NOT NULL,
        attempt_count INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_task_leases_task ON worker_task_leases(task_id)",
    "CREATE INDEX idx_task_leases_worker ON worker_task_leases(worker_id)",
    "CREATE INDEX idx_task_leases_status ON worker_task_leases(status, lease_expires_at)",

    # 3. Worker Execution Records (Auditable History of Completed Operations)
    """
    CREATE TABLE worker_executions (
        id TEXT PRIMARY KEY,
        task_id TEXT,
        worker_id TEXT NOT NULL REFERENCES infrastructure_nodes(id) ON DELETE CASCADE,
        lease_id TEXT REFERENCES worker_task_leases(id) ON DELETE SET NULL,
        idempotency_key TEXT,
        status TEXT NOT NULL CHECK (status IN ('success', 'error', 'timeout', 'cancelled')),
        exit_code INTEGER,
        started_at TEXT NOT NULL,
        completed_at TEXT NOT NULL,
        runtime_seconds REAL NOT NULL DEFAULT 0.0,
        cpu_percent REAL,
        memory_mb REAL,
        stdout_summary TEXT NOT NULL DEFAULT '',
        stderr_summary TEXT NOT NULL DEFAULT '',
        error_category TEXT,
        deliverable_ids_json TEXT NOT NULL DEFAULT '[]',
        result_payload_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_worker_exec_worker ON worker_executions(worker_id, started_at)",
    "CREATE INDEX idx_worker_exec_task ON worker_executions(task_id)",
    "CREATE INDEX idx_worker_exec_status ON worker_executions(status)",
    """
    CREATE TRIGGER trg_worker_exec_no_update BEFORE UPDATE ON worker_executions
    BEGIN SELECT RAISE(ABORT, 'worker_executions is append-only'); END
    """,
    """
    CREATE TRIGGER trg_worker_exec_no_delete BEFORE DELETE ON worker_executions
    BEGIN SELECT RAISE(ABORT, 'worker_executions is append-only'); END
    """,
]

# ---------------------------------------------------------------------------
# Migration 8 — Worker Enrollment Security & Chat Project Context (Integration Repair)
# ---------------------------------------------------------------------------
_M8: List[str] = [
    # 1. Administrator-Issued Worker Enrollment Tokens
    """
    CREATE TABLE worker_enrollment_tokens (
        id TEXT PRIMARY KEY,
        token_hash TEXT NOT NULL UNIQUE,
        token_prefix TEXT NOT NULL,
        allowed_node_id TEXT,
        created_by TEXT NOT NULL DEFAULT 'admin',
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        used_at TEXT,
        used_by_node_id TEXT,
        revoked INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0, 1))
    )
    """,
    "CREATE INDEX idx_enrollment_tokens_hash ON worker_enrollment_tokens(token_hash)",
    "CREATE INDEX idx_enrollment_tokens_status ON worker_enrollment_tokens(revoked, used_at, expires_at)",

    # 2. Chat Conversation to Project Association
    "ALTER TABLE chat_conversations ADD COLUMN project_id TEXT REFERENCES projects(id) ON DELETE SET NULL",
    "CREATE INDEX idx_chat_conv_project ON chat_conversations(project_id)",
]

# --- Milestone 9 (Autonomous Project Orchestrator) ---------------------------
# Additive only: no existing table is altered. Orchestration sits ON TOP of the existing
# projects / project_paths / path_stages / tasks / deliverables / worker_* tables.
_M9: List[str] = [
    # One orchestration run per project (durable state machine; optimistic 'version' for idempotent transitions)
    """
    CREATE TABLE orchestration_runs (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL UNIQUE REFERENCES projects(id),
        state TEXT NOT NULL DEFAULT 'DRAFT' CHECK (state IN (
            'DRAFT','ANALYZING','AWAITING_CLARIFICATION','PLANNING','AWAITING_PLAN_APPROVAL','READY',
            'RUNNING','EVALUATING','COMPLETED','PAUSED','BLOCKED','REPLANNING','FAILED','CANCELLED')),
        resume_state TEXT,
        autonomy_level INTEGER NOT NULL DEFAULT 1 CHECK (autonomy_level BETWEEN 0 AND 3),
        intake_json TEXT NOT NULL DEFAULT '{}',
        analysis_json TEXT NOT NULL DEFAULT '{}',
        current_revision_id TEXT,
        block_reason TEXT NOT NULL DEFAULT '',
        planning_error TEXT NOT NULL DEFAULT '',
        version INTEGER NOT NULL DEFAULT 0,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT
    )
    """,
    "CREATE INDEX idx_orch_runs_state ON orchestration_runs(state)",

    # Immutable plan revisions: once approved/superseded the plan body can never change.
    """
    CREATE TABLE orchestration_plan_revisions (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES orchestration_runs(id),
        revision_number INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','approved','superseded','rejected')),
        source TEXT NOT NULL CHECK (source IN ('model','template','manual_edit')),
        plan_json TEXT NOT NULL,
        validation_json TEXT NOT NULL DEFAULT '{}',
        critique_json TEXT NOT NULL DEFAULT '{}',
        model_info_json TEXT NOT NULL DEFAULT '{}',
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        approved_by TEXT,
        approved_at TEXT,
        UNIQUE (run_id, revision_number)
    )
    """,
    """
    CREATE TRIGGER trg_orch_plan_immutable BEFORE UPDATE OF plan_json, source, revision_number, run_id
    ON orchestration_plan_revisions
    WHEN OLD.status IN ('approved','superseded')
    BEGIN
        SELECT RAISE(ABORT, 'approved plan revisions are immutable; create a new revision');
    END
    """,
    """
    CREATE TRIGGER trg_orch_plan_no_delete BEFORE DELETE ON orchestration_plan_revisions
    BEGIN
        SELECT RAISE(ABORT, 'plan revisions cannot be deleted');
    END
    """,

    # Append-only audit log
    """
    CREATE TABLE orchestration_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES orchestration_runs(id),
        project_id TEXT NOT NULL,
        ts TEXT NOT NULL,
        event_type TEXT NOT NULL,
        previous_state TEXT,
        new_state TEXT,
        actor TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        task_id TEXT,
        stage_id TEXT,
        detail_json TEXT NOT NULL DEFAULT '{}'
    )
    """,
    "CREATE INDEX idx_orch_events_run ON orchestration_events(run_id, id)",
    """
    CREATE TRIGGER trg_orch_events_no_update BEFORE UPDATE ON orchestration_events
    BEGIN SELECT RAISE(ABORT, 'orchestration events are append-only'); END
    """,
    """
    CREATE TRIGGER trg_orch_events_no_delete BEFORE DELETE ON orchestration_events
    BEGIN SELECT RAISE(ABORT, 'orchestration events are append-only'); END
    """,

    # Human approval gates (specific to the proposed action)
    """
    CREATE TABLE orchestration_approvals (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES orchestration_runs(id),
        kind TEXT NOT NULL CHECK (kind IN ('plan','task','budget','deliverable','action','clarification')),
        subject_id TEXT NOT NULL,
        requested_action TEXT NOT NULL,
        risk TEXT NOT NULL DEFAULT 'low' CHECK (risk IN ('low','medium','high')),
        status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','denied','superseded')),
        requested_at TEXT NOT NULL,
        decided_by TEXT,
        decided_at TEXT,
        rationale TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE UNIQUE INDEX ux_orch_approval_pending ON orchestration_approvals(run_id, kind, subject_id) WHERE status = 'pending'",
    "CREATE INDEX idx_orch_approvals_run ON orchestration_approvals(run_id, status)",

    # Plan task -> real task mapping, per-task orchestration metadata
    """
    CREATE TABLE orchestration_tasks (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES orchestration_runs(id),
        revision_id TEXT NOT NULL REFERENCES orchestration_plan_revisions(id),
        plan_key TEXT NOT NULL,
        stage_id TEXT REFERENCES path_stages(id),
        title TEXT NOT NULL,
        agent_role TEXT NOT NULL,
        action_json TEXT NOT NULL,
        acceptance_json TEXT NOT NULL DEFAULT '{}',
        required_capabilities_json TEXT NOT NULL DEFAULT '[]',
        preferred_models_json TEXT NOT NULL DEFAULT '[]',
        allowed_nodes_json TEXT NOT NULL DEFAULT '[]',
        priority INTEGER NOT NULL DEFAULT 3,
        max_attempts INTEGER NOT NULL DEFAULT 2,
        timeout_seconds INTEGER NOT NULL DEFAULT 600,
        approval_mode TEXT NOT NULL DEFAULT 'auto' CHECK (approval_mode IN ('auto','required')),
        risk TEXT NOT NULL DEFAULT 'low' CHECK (risk IN ('low','medium','high')),
        state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN (
            'pending','dispatched','accepted','failed','blocked','cancelled','superseded')),
        attempts INTEGER NOT NULL DEFAULT 0,
        current_task_id TEXT REFERENCES tasks(id),
        dispatched_node TEXT,
        dispatched_model TEXT,
        dispatched_at TEXT,
        backoff_until TEXT,
        failure_class TEXT,
        last_error TEXT NOT NULL DEFAULT '',
        accepted_result_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (run_id, revision_id, plan_key)
    )
    """,
    "CREATE INDEX idx_orch_tasks_run ON orchestration_tasks(run_id, state)",
    "CREATE INDEX idx_orch_tasks_current ON orchestration_tasks(current_task_id)",
    """
    CREATE TABLE orchestration_task_deps (
        orch_task_id TEXT NOT NULL REFERENCES orchestration_tasks(id) ON DELETE CASCADE,
        depends_on_id TEXT NOT NULL REFERENCES orchestration_tasks(id) ON DELETE CASCADE,
        PRIMARY KEY (orch_task_id, depends_on_id),
        CHECK (orch_task_id <> depends_on_id)
    )
    """,
    # One row per execution attempt (retries create new underlying tasks; history is preserved)
    """
    CREATE TABLE orchestration_attempts (
        id TEXT PRIMARY KEY,
        orch_task_id TEXT NOT NULL REFERENCES orchestration_tasks(id),
        attempt_number INTEGER NOT NULL,
        task_id TEXT REFERENCES tasks(id),
        node_id TEXT,
        model TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        outcome TEXT CHECK (outcome IN ('accepted','rejected','failed','timeout','cancelled')),
        failure_class TEXT,
        evaluation_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE (orch_task_id, attempt_number)
    )
    """,

    # Central artifact metadata (files themselves stay in the existing evidence directory)
    """
    CREATE TABLE artifact_metadata (
        id TEXT PRIMARY KEY,
        run_id TEXT REFERENCES orchestration_runs(id),
        project_id TEXT NOT NULL REFERENCES projects(id),
        task_id TEXT REFERENCES tasks(id),
        orch_task_id TEXT REFERENCES orchestration_tasks(id),
        worker_id TEXT,
        filename TEXT NOT NULL,
        mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',
        size_bytes INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        storage_path TEXT NOT NULL,
        provenance TEXT NOT NULL CHECK (provenance IN ('worker_telemetry','model_output','controller_generated','user_supplied')),
        validation_state TEXT NOT NULL DEFAULT 'pending' CHECK (validation_state IN ('pending','valid','invalid')),
        validation_detail TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE (storage_path)
    )
    """,
    "CREATE INDEX idx_artifact_meta_project ON artifact_metadata(project_id)",

    # Budgets and usage counters
    """
    CREATE TABLE resource_budgets (
        run_id TEXT PRIMARY KEY REFERENCES orchestration_runs(id),
        max_tasks INTEGER NOT NULL DEFAULT 40,
        max_concurrent INTEGER NOT NULL DEFAULT 2,
        max_inference_requests INTEGER NOT NULL DEFAULT 30,
        max_tokens INTEGER NOT NULL DEFAULT 60000,
        max_runtime_seconds INTEGER NOT NULL DEFAULT 7200,
        max_retries_total INTEGER NOT NULL DEFAULT 12,
        max_storage_bytes INTEGER NOT NULL DEFAULT 104857600,
        used_tasks INTEGER NOT NULL DEFAULT 0,
        used_inference_requests INTEGER NOT NULL DEFAULT 0,
        used_tokens INTEGER NOT NULL DEFAULT 0,
        used_retries INTEGER NOT NULL DEFAULT 0,
        used_storage_bytes INTEGER NOT NULL DEFAULT 0,
        used_runtime_seconds REAL NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )
    """,

    # Inference accounting (planning, critique, task and evaluation requests routed through workers)
    """
    CREATE TABLE model_inference_requests (
        id TEXT PRIMARY KEY,
        run_id TEXT REFERENCES orchestration_runs(id),
        orch_task_id TEXT REFERENCES orchestration_tasks(id),
        purpose TEXT NOT NULL CHECK (purpose IN ('analysis','planning','plan_repair','plan_critique','task','evaluation')),
        node_id TEXT NOT NULL,
        model TEXT NOT NULL,
        task_id TEXT REFERENCES tasks(id),
        status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted','completed','failed','cancelled','timeout')),
        prompt_chars INTEGER NOT NULL DEFAULT 0,
        prompt_tokens INTEGER,
        gen_tokens INTEGER,
        latency_s REAL,
        error_class TEXT,
        response_text TEXT,
        created_at TEXT NOT NULL,
        completed_at TEXT
    )
    """,
    "CREATE INDEX idx_model_inf_run ON model_inference_requests(run_id, status)",

    # Single-holder scheduler lease (prevents two scheduler instances dispatching the same work)
    """
    CREATE TABLE orchestration_scheduler_lock (
        name TEXT PRIMARY KEY,
        holder TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE orchestration_checkpoints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES orchestration_runs(id),
        ts TEXT NOT NULL,
        state TEXT NOT NULL,
        snapshot_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_orch_checkpoints_run ON orchestration_checkpoints(run_id, id)",
]

# --- Milestone 10 (System Observatory: Telemetry, Rollups, Inference Accounting & Alerting) ---
_M10: List[str] = [
    # 1. Telemetry Samples (Raw time-series samples from workers and controller)
    """
    CREATE TABLE telemetry_samples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        node_id TEXT NOT NULL REFERENCES infrastructure_nodes(id) ON DELETE CASCADE,
        timestamp TEXT NOT NULL,
        cpu_percent REAL,
        per_core_json TEXT NOT NULL DEFAULT '[]',
        cpu_freq_mhz REAL,
        load_1m REAL,
        load_5m REAL,
        load_15m REAL,
        ram_total_mb INTEGER,
        ram_used_mb INTEGER,
        ram_available_mb INTEGER,
        ram_percent REAL,
        swap_total_mb INTEGER,
        swap_used_mb INTEGER,
        swap_percent REAL,
        disk_total_gb REAL,
        disk_used_gb REAL,
        disk_free_gb REAL,
        disk_read_mbs REAL,
        disk_write_mbs REAL,
        disk_iops REAL,
        net_rx_kbs REAL,
        net_tx_kbs REAL,
        cpu_temp_c REAL,
        battery_percent REAL,
        is_ac_online INTEGER NOT NULL DEFAULT -1,
        worker_res_mb REAL,
        ollama_res_mb REAL,
        worker_cpu_percent REAL,
        ollama_cpu_percent REAL,
        gpu_info_json TEXT NOT NULL DEFAULT '{}',
        latency_to_controller_ms REAL,
        os_uptime_s INTEGER,
        worker_service_status TEXT NOT NULL DEFAULT 'running',
        ollama_service_status TEXT NOT NULL DEFAULT 'unknown'
    )
    """,
    "CREATE INDEX idx_telemetry_samples_node_ts ON telemetry_samples(node_id, timestamp)",
    "CREATE INDEX idx_telemetry_samples_ts ON telemetry_samples(timestamp)",

    # 2. Telemetry Minute Rollups (30-day retention)
    """
    CREATE TABLE telemetry_rollups_minute (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        node_id TEXT NOT NULL REFERENCES infrastructure_nodes(id) ON DELETE CASCADE,
        bucket_time TEXT NOT NULL,
        sample_count INTEGER NOT NULL,
        cpu_avg REAL,
        cpu_min REAL,
        cpu_max REAL,
        ram_used_avg REAL,
        ram_used_min REAL,
        ram_used_max REAL,
        swap_used_avg REAL,
        swap_used_min REAL,
        swap_used_max REAL,
        temp_avg REAL,
        temp_max REAL,
        net_rx_avg REAL,
        net_tx_avg REAL,
        disk_read_avg REAL,
        disk_write_avg REAL,
        latency_avg REAL,
        UNIQUE (node_id, bucket_time)
    )
    """,
    "CREATE INDEX idx_telemetry_min_bucket ON telemetry_rollups_minute(bucket_time)",
    "CREATE INDEX idx_telemetry_min_node ON telemetry_rollups_minute(node_id, bucket_time)",

    # 3. Telemetry Hourly Rollups (1-year retention)
    """
    CREATE TABLE telemetry_rollups_hour (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        node_id TEXT NOT NULL REFERENCES infrastructure_nodes(id) ON DELETE CASCADE,
        bucket_time TEXT NOT NULL,
        sample_count INTEGER NOT NULL,
        cpu_avg REAL,
        cpu_min REAL,
        cpu_max REAL,
        ram_used_avg REAL,
        ram_used_min REAL,
        ram_used_max REAL,
        swap_used_avg REAL,
        swap_used_min REAL,
        swap_used_max REAL,
        temp_avg REAL,
        temp_max REAL,
        net_rx_avg REAL,
        net_tx_avg REAL,
        disk_read_avg REAL,
        disk_write_avg REAL,
        latency_avg REAL,
        UNIQUE (node_id, bucket_time)
    )
    """,
    "CREATE INDEX idx_telemetry_hour_bucket ON telemetry_rollups_hour(bucket_time)",
    "CREATE INDEX idx_telemetry_hour_node ON telemetry_rollups_hour(node_id, bucket_time)",

    # 4. Comprehensive Inference Telemetry (authoritative engine token accounting)
    """
    CREATE TABLE inference_telemetry (
        id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        run_id TEXT REFERENCES orchestration_runs(id) ON DELETE SET NULL,
        project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
        task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
        node_id TEXT NOT NULL REFERENCES infrastructure_nodes(id) ON DELETE CASCADE,
        model TEXT NOT NULL,
        provider TEXT NOT NULL DEFAULT 'ollama',
        status TEXT NOT NULL DEFAULT 'success' CHECK (status IN ('success', 'error', 'timeout')),
        prompt_tokens INTEGER NOT NULL DEFAULT 0,
        gen_tokens INTEGER NOT NULL DEFAULT 0,
        total_tokens INTEGER NOT NULL DEFAULT 0,
        prompt_eval_duration_ms REAL NOT NULL DEFAULT 0.0,
        eval_duration_ms REAL NOT NULL DEFAULT 0.0,
        load_duration_ms REAL NOT NULL DEFAULT 0.0,
        total_duration_ms REAL NOT NULL DEFAULT 0.0,
        tokens_per_second REAL NOT NULL DEFAULT 0.0,
        time_to_first_token_ms REAL,
        error_category TEXT,
        retry_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        completed_at TEXT
    )
    """,
    "CREATE INDEX idx_inf_tel_node ON inference_telemetry(node_id, created_at)",
    "CREATE INDEX idx_inf_tel_model ON inference_telemetry(model, created_at)",
    "CREATE INDEX idx_inf_tel_project ON inference_telemetry(project_id, created_at)",
    "CREATE INDEX idx_inf_tel_task ON inference_telemetry(task_id)",

    # 5. Alert Definitions / Rules
    """
    CREATE TABLE observatory_alert_rules (
        id TEXT PRIMARY KEY,
        name TEXT UNIQUE NOT NULL,
        category TEXT NOT NULL CHECK (category IN (
            'cpu', 'memory', 'swap', 'temperature', 'storage', 'worker', 'model', 'controller'
        )),
        metric_name TEXT NOT NULL,
        threshold REAL NOT NULL,
        operator TEXT NOT NULL CHECK (operator IN ('>', '>=', '<', '<=', '==', '!=')),
        duration_seconds INTEGER NOT NULL DEFAULT 30,
        severity TEXT NOT NULL DEFAULT 'warning' CHECK (severity IN ('info', 'warning', 'critical')),
        is_enabled INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0, 1)),
        created_at TEXT NOT NULL
    )
    """,

    # 6. Active and Historical Alert Instances
    """
    CREATE TABLE observatory_alerts (
        id TEXT PRIMARY KEY,
        rule_name TEXT NOT NULL,
        category TEXT NOT NULL,
        node_id TEXT REFERENCES infrastructure_nodes(id) ON DELETE CASCADE,
        severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        metrics_json TEXT NOT NULL DEFAULT '{}',
        state TEXT NOT NULL DEFAULT 'firing' CHECK (state IN ('firing', 'acknowledged', 'resolved')),
        fired_at TEXT NOT NULL,
        acknowledged_at TEXT,
        acknowledged_by TEXT,
        resolved_at TEXT,
        cooldown_until TEXT
    )
    """,
    "CREATE INDEX idx_obs_alerts_state ON observatory_alerts(state, severity)",
    "CREATE INDEX idx_obs_alerts_node ON observatory_alerts(node_id, state)",
    "CREATE INDEX idx_obs_alerts_fired ON observatory_alerts(fired_at)",
]

# ---------------------------------------------------------------------------
# Migration 11 — Autonomous Web Research & Browsing Engine
# ---------------------------------------------------------------------------
_M11: List[str] = [
    # 1. Research runs (per-project autonomous research execution records)
    """
    CREATE TABLE research_runs (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        orchestration_run_id TEXT REFERENCES orchestration_runs(id) ON DELETE SET NULL,
        status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'planning', 'active', 'paused', 'completed', 'failed', 'cancelled')),
        objective TEXT NOT NULL DEFAULT '',
        strategy_json TEXT NOT NULL DEFAULT '{}',
        queries_count INTEGER NOT NULL DEFAULT 0,
        sources_count INTEGER NOT NULL DEFAULT 0,
        claims_count INTEGER NOT NULL DEFAULT 0,
        citations_count INTEGER NOT NULL DEFAULT 0,
        budget_json TEXT NOT NULL DEFAULT '{}',
        error_message TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT
    )
    """,
    "CREATE INDEX idx_research_runs_proj ON research_runs(project_id, status)",
    "CREATE INDEX idx_research_runs_orch ON research_runs(orchestration_run_id)",

    # 2. Research search queries (audit trail of queries executed)
    """
    CREATE TABLE research_queries (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        research_run_id TEXT REFERENCES research_runs(id) ON DELETE CASCADE,
        task_id TEXT,
        query_text TEXT NOT NULL,
        original_query TEXT NOT NULL,
        provider TEXT NOT NULL DEFAULT 'duckduckgo',
        status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed')),
        results_count INTEGER NOT NULL DEFAULT 0,
        duration_ms REAL NOT NULL DEFAULT 0.0,
        error_message TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_research_queries_run ON research_queries(research_run_id, created_at)",
    "CREATE INDEX idx_research_queries_proj ON research_queries(project_id)",

    # 3. Research search results (items returned by search providers)
    """
    CREATE TABLE research_search_results (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        query_id TEXT NOT NULL REFERENCES research_queries(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        url TEXT NOT NULL,
        canonical_url TEXT NOT NULL,
        snippet TEXT NOT NULL DEFAULT '',
        provider TEXT NOT NULL DEFAULT 'duckduckgo',
        rank INTEGER NOT NULL DEFAULT 1,
        published_at TEXT,
        retrieved_at TEXT NOT NULL,
        selected_for_retrieval INTEGER NOT NULL DEFAULT 0 CHECK (selected_for_retrieval IN (0, 1))
    )
    """,
    "CREATE INDEX idx_research_results_query ON research_search_results(query_id, rank)",
    "CREATE INDEX idx_research_results_proj ON research_search_results(project_id)",
    "CREATE INDEX idx_research_results_canon ON research_search_results(canonical_url)",

    # 4. Research sources (persistent project source library)
    """
    CREATE TABLE research_sources (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        canonical_url TEXT NOT NULL,
        original_url TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        author TEXT,
        publisher TEXT,
        publication_date TEXT,
        modified_date TEXT,
        retrieved_at TEXT NOT NULL,
        content_type TEXT NOT NULL DEFAULT 'text/html',
        content_hash TEXT NOT NULL,
        content_length_bytes INTEGER NOT NULL DEFAULT 0,
        storage_path TEXT,
        retrieval_method TEXT NOT NULL DEFAULT 'http' CHECK (retrieval_method IN ('http', 'browser')),
        status TEXT NOT NULL DEFAULT 'fetched' CHECK (status IN ('fetched', 'failed', 'extracted')),
        quality_score REAL NOT NULL DEFAULT 1.0,
        reliability_assessment_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (project_id, canonical_url)
    )
    """,
    "CREATE INDEX idx_research_sources_proj ON research_sources(project_id, status)",
    "CREATE INDEX idx_research_sources_hash ON research_sources(content_hash)",
    "CREATE INDEX idx_research_sources_canon ON research_sources(canonical_url)",

    # 5. Research source versions (tracking content changes over time)
    """
    CREATE TABLE research_source_versions (
        id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL REFERENCES research_sources(id) ON DELETE CASCADE,
        version_number INTEGER NOT NULL DEFAULT 1,
        content_hash TEXT NOT NULL,
        content_length_bytes INTEGER NOT NULL DEFAULT 0,
        storage_path TEXT,
        retrieved_at TEXT NOT NULL,
        UNIQUE (source_id, version_number)
    )
    """,
    "CREATE INDEX idx_research_source_versions ON research_source_versions(source_id, version_number)",

    # 6. Research document extractions (clean extracted text, headings, tables, links)
    """
    CREATE TABLE research_extractions (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        source_id TEXT NOT NULL REFERENCES research_sources(id) ON DELETE CASCADE,
        clean_text TEXT NOT NULL DEFAULT '',
        headings_json TEXT NOT NULL DEFAULT '[]',
        tables_json TEXT NOT NULL DEFAULT '[]',
        outbound_urls_json TEXT NOT NULL DEFAULT '[]',
        word_count INTEGER NOT NULL DEFAULT 0,
        extracted_at TEXT NOT NULL,
        UNIQUE (source_id)
    )
    """,
    "CREATE INDEX idx_research_extractions_proj ON research_extractions(project_id)",
    "CREATE INDEX idx_research_extractions_src ON research_extractions(source_id)",

    # 7. Research claims (structured claim-level evidence tracking)
    """
    CREATE TABLE research_claims (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        research_run_id TEXT REFERENCES research_runs(id) ON DELETE CASCADE,
        claim_text TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'fact' CHECK (category IN ('fact', 'inference', 'estimate', 'opinion', 'unknown')),
        verification_status TEXT NOT NULL DEFAULT 'unverified' CHECK (verification_status IN ('unverified', 'supported', 'corroborated', 'contradicted', 'insufficient_evidence', 'outdated')),
        confidence_score REAL NOT NULL DEFAULT 0.0,
        contradiction_details TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_research_claims_proj ON research_claims(project_id, verification_status)",
    "CREATE INDEX idx_research_claims_run ON research_claims(research_run_id)",

    # 8. Research evidence (linking claims directly to source passages)
    """
    CREATE TABLE research_evidence (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        claim_id TEXT NOT NULL REFERENCES research_claims(id) ON DELETE CASCADE,
        source_id TEXT NOT NULL REFERENCES research_sources(id) ON DELETE CASCADE,
        supporting_excerpt TEXT NOT NULL,
        exact_quote TEXT,
        context_before TEXT,
        context_after TEXT,
        evidence_strength TEXT NOT NULL DEFAULT 'moderate' CHECK (evidence_strength IN ('weak', 'moderate', 'strong')),
        relevance_score REAL NOT NULL DEFAULT 1.0,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_research_evidence_claim ON research_evidence(claim_id)",
    "CREATE INDEX idx_research_evidence_source ON research_evidence(source_id)",
    "CREATE INDEX idx_research_evidence_proj ON research_evidence(project_id)",

    # 9. Research citations (structured citations resolving to real source records)
    """
    CREATE TABLE research_citations (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        research_run_id TEXT REFERENCES research_runs(id) ON DELETE CASCADE,
        citation_num INTEGER NOT NULL,
        source_id TEXT NOT NULL REFERENCES research_sources(id) ON DELETE CASCADE,
        claim_id TEXT REFERENCES research_claims(id) ON DELETE SET NULL,
        deliverable_id TEXT REFERENCES deliverables(id) ON DELETE SET NULL,
        anchor_text TEXT NOT NULL DEFAULT '',
        formatted_citation TEXT NOT NULL DEFAULT '',
        is_verified INTEGER NOT NULL DEFAULT 1 CHECK (is_verified IN (0, 1)),
        validation_notes TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_research_citations_run ON research_citations(research_run_id, citation_num)",
    "CREATE INDEX idx_research_citations_src ON research_citations(source_id)",
    "CREATE INDEX idx_research_citations_proj ON research_citations(project_id)",

    # 10. Research provider status (health, tracking, rate-limiting)
    """
    CREATE TABLE research_provider_status (
        provider_name TEXT PRIMARY KEY,
        is_available INTEGER NOT NULL DEFAULT 1 CHECK (is_available IN (0, 1)),
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        last_success_at TEXT,
        total_queries INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )
    """,

    # 11. Research budgets (enforceable resource limits)
    """
    CREATE TABLE research_budgets (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        research_run_id TEXT REFERENCES research_runs(id) ON DELETE CASCADE,
        max_queries INTEGER NOT NULL DEFAULT 10,
        max_docs INTEGER NOT NULL DEFAULT 20,
        max_browser_seconds INTEGER NOT NULL DEFAULT 120,
        max_tokens INTEGER NOT NULL DEFAULT 10000,
        queries_used INTEGER NOT NULL DEFAULT 0,
        docs_used INTEGER NOT NULL DEFAULT 0,
        browser_seconds_used REAL NOT NULL DEFAULT 0.0,
        tokens_used INTEGER NOT NULL DEFAULT 0,
        is_exceeded INTEGER NOT NULL DEFAULT 0 CHECK (is_exceeded IN (0, 1)),
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_research_budgets_proj ON research_budgets(project_id)",
    "CREATE INDEX idx_research_budgets_run ON research_budgets(research_run_id)",
]

# ---------------------------------------------------------------------------
# Migration 12 — Continuous research and persistent knowledge integration
# ---------------------------------------------------------------------------
_M12: List[str] = [
    # 1. Canonical structured knowledge records (Project & Collective scopes)
    """
    CREATE TABLE knowledge_records (
        id TEXT PRIMARY KEY,
        scope TEXT NOT NULL CHECK (scope IN ('project', 'collective')),
        project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
        source_project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
        subject TEXT NOT NULL,
        knowledge_type TEXT NOT NULL CHECK (knowledge_type IN (
            'fact', 'source_observation', 'inference', 'estimate', 'hypothesis',
            'decision', 'procedure', 'lesson_learned', 'contradiction', 'open_question'
        )),
        statement TEXT NOT NULL,
        summary TEXT NOT NULL DEFAULT '',
        source_ids_json TEXT NOT NULL DEFAULT '[]',
        evidence_ids_json TEXT NOT NULL DEFAULT '[]',
        verification_status TEXT NOT NULL DEFAULT 'unverified' CHECK (verification_status IN (
            'unverified', 'supported', 'corroborated', 'contradicted', 'insufficient_evidence', 'outdated'
        )),
        verification_method TEXT NOT NULL DEFAULT 'deterministic',
        confidence_assessment TEXT NOT NULL DEFAULT 'moderate' CHECK (confidence_assessment IN (
            'low', 'moderate', 'high', 'verified'
        )),
        created_at TEXT NOT NULL,
        last_verified_at TEXT,
        valid_from TEXT,
        valid_until TEXT,
        superseded_by TEXT REFERENCES knowledge_records(id) ON DELETE SET NULL,
        contradicts TEXT REFERENCES knowledge_records(id) ON DELETE SET NULL,
        related_entities_json TEXT NOT NULL DEFAULT '[]',
        topics_json TEXT NOT NULL DEFAULT '[]',
        keywords_json TEXT NOT NULL DEFAULT '[]',
        embedding_reference TEXT,
        promoted_by TEXT,
        promoted_at TEXT,
        promotion_policy TEXT,
        access_policy TEXT NOT NULL DEFAULT 'standard',
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_knowledge_records_scope ON knowledge_records(scope, project_id)",
    "CREATE INDEX idx_knowledge_records_type ON knowledge_records(knowledge_type)",
    "CREATE INDEX idx_knowledge_records_status ON knowledge_records(verification_status)",
    "CREATE INDEX idx_knowledge_records_created ON knowledge_records(created_at)",

    # 2. Knowledge records FTS5 virtual table for fast multi-field search
    """
    CREATE VIRTUAL TABLE knowledge_records_fts USING fts5(
        record_id UNINDEXED,
        subject,
        statement,
        summary,
        topics,
        keywords,
        tokenize='porter unicode61'
    )
    """,
    """
    CREATE TRIGGER trg_knowledge_records_ai AFTER INSERT ON knowledge_records BEGIN
        INSERT INTO knowledge_records_fts(record_id, subject, statement, summary, topics, keywords)
        VALUES (new.id, new.subject, new.statement, new.summary, new.topics_json, new.keywords_json);
    END
    """,
    """
    CREATE TRIGGER trg_knowledge_records_ad AFTER DELETE ON knowledge_records BEGIN
        DELETE FROM knowledge_records_fts WHERE record_id = old.id;
    END
    """,
    """
    CREATE TRIGGER trg_knowledge_records_au AFTER UPDATE ON knowledge_records BEGIN
        DELETE FROM knowledge_records_fts WHERE record_id = old.id;
        INSERT INTO knowledge_records_fts(record_id, subject, statement, summary, topics, keywords)
        VALUES (new.id, new.subject, new.statement, new.summary, new.topics_json, new.keywords_json);
    END
    """,

    # 3. Knowledge impact tracking (records where knowledge was used, flags affected assets if superseded/contradicted)
    """
    CREATE TABLE knowledge_impacts (
        id TEXT PRIMARY KEY,
        knowledge_id TEXT NOT NULL REFERENCES knowledge_records(id) ON DELETE CASCADE,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        run_id TEXT REFERENCES orchestration_runs(id) ON DELETE CASCADE,
        stage_id TEXT REFERENCES path_stages(id) ON DELETE SET NULL,
        task_id TEXT REFERENCES orchestration_tasks(id) ON DELETE SET NULL,
        deliverable_id TEXT REFERENCES deliverables(id) ON DELETE SET NULL,
        usage_type TEXT NOT NULL CHECK (usage_type IN (
            'planning_assumption', 'task_context', 'verification_evidence',
            'deliverable_claim', 'decision_basis', 'retrospective'
        )),
        impact_summary TEXT NOT NULL DEFAULT '',
        is_affected INTEGER NOT NULL DEFAULT 0 CHECK (is_affected IN (0, 1)),
        affected_reason TEXT,
        created_at TEXT NOT NULL,
        flagged_at TEXT
    )
    """,
    "CREATE INDEX idx_knowledge_impacts_k ON knowledge_impacts(knowledge_id)",
    "CREATE INDEX idx_knowledge_impacts_proj ON knowledge_impacts(project_id, is_affected)",
    "CREATE INDEX idx_knowledge_impacts_run ON knowledge_impacts(run_id)",
    "CREATE INDEX idx_knowledge_impacts_task ON knowledge_impacts(task_id)",

    # 4. Mandatory stage research checkpoints
    """
    CREATE TABLE stage_research_checkpoints (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        stage_id TEXT REFERENCES path_stages(id) ON DELETE SET NULL,
        stage_name TEXT NOT NULL DEFAULT '',
        run_id TEXT REFERENCES orchestration_runs(id) ON DELETE CASCADE,
        stage_phase TEXT NOT NULL CHECK (stage_phase IN (
            'intake', 'planning', 'execution', 'evaluation', 'replanning', 'final_reporting'
        )),
        status TEXT NOT NULL DEFAULT 'completed' CHECK (status IN (
            'pending', 'running', 'completed', 'failed', 'degraded'
        )),
        information_needs TEXT NOT NULL DEFAULT '',
        knowledge_queries_json TEXT NOT NULL DEFAULT '[]',
        existing_sources_considered_json TEXT NOT NULL DEFAULT '[]',
        freshness_assessment TEXT NOT NULL DEFAULT '',
        research_needed INTEGER NOT NULL DEFAULT 0 CHECK (research_needed IN (0, 1)),
        decision_reason TEXT NOT NULL DEFAULT '',
        external_queries_json TEXT NOT NULL DEFAULT '[]',
        sources_retrieved_count INTEGER NOT NULL DEFAULT 0,
        claims_verified_count INTEGER NOT NULL DEFAULT 0,
        knowledge_records_created_count INTEGER NOT NULL DEFAULT 0,
        findings_summary TEXT NOT NULL DEFAULT '',
        evidence_gaps TEXT NOT NULL DEFAULT '',
        budget_used_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        completed_at TEXT
    )
    """,
    "CREATE INDEX idx_stage_checkpoints_proj ON stage_research_checkpoints(project_id, stage_phase)",
    "CREATE INDEX idx_stage_checkpoints_run ON stage_research_checkpoints(run_id)",

    # 5. Knowledge promotion governance (project discovery -> collective knowledge promotion queue)
    """
    CREATE TABLE knowledge_promotions (
        id TEXT PRIMARY KEY,
        knowledge_id TEXT NOT NULL REFERENCES knowledge_records(id) ON DELETE CASCADE,
        source_project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN (
            'proposed', 'approved', 'rejected', 'superseded'
        )),
        promotion_policy TEXT NOT NULL DEFAULT 'public_technical',
        quality_assessment TEXT NOT NULL DEFAULT '',
        privacy_check_passed INTEGER NOT NULL DEFAULT 1 CHECK (privacy_check_passed IN (0, 1)),
        rationale TEXT NOT NULL DEFAULT '',
        reviewed_by TEXT,
        reviewed_at TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_knowledge_promotions_status ON knowledge_promotions(status)",
    "CREATE INDEX idx_knowledge_promotions_kid ON knowledge_promotions(knowledge_id)",

    # 6. Knowledge freshness policies
    """
    CREATE TABLE knowledge_freshness_policies (
        subject_pattern TEXT PRIMARY KEY,
        max_age_days INTEGER NOT NULL DEFAULT 30,
        policy_type TEXT NOT NULL DEFAULT 'standard' CHECK (policy_type IN (
            'breaking_news', 'law_regulation', 'software_api', 'product_pricing',
            'scientific_research', 'historical_fact', 'internal_decision', 'standard'
        )),
        refresh_strategy TEXT NOT NULL DEFAULT 'on_stage_checkpoint' CHECK (refresh_strategy IN (
            'on_stage_checkpoint', 'periodic', 'on_reference', 'manual'
        )),
        description TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
    )
    """,
    """
    INSERT OR IGNORE INTO knowledge_freshness_policies (subject_pattern, max_age_days, policy_type, refresh_strategy, description, updated_at)
    VALUES
        ('breaking_news', 1, 'breaking_news', 'on_stage_checkpoint', 'Frequently refreshing events and fast-breaking news', datetime('now')),
        ('software_api', 14, 'software_api', 'on_stage_checkpoint', 'Current version documentation and library APIs', datetime('now')),
        ('product_pricing', 7, 'product_pricing', 'on_stage_checkpoint', 'Product pricing and commercial quotes', datetime('now')),
        ('law_regulation', 90, 'law_regulation', 'on_stage_checkpoint', 'Official statutory text, regulation, and policies', datetime('now')),
        ('scientific_research', 180, 'scientific_research', 'on_stage_checkpoint', 'Academic research papers and scientific preprints', datetime('now')),
        ('historical_fact', 3650, 'historical_fact', 'manual', 'Historical facts; refresh only when disputed', datetime('now')),
        ('internal_decision', 365, 'internal_decision', 'manual', 'Architecture decisions; preserve until explicitly revised', datetime('now')),
        ('standard', 30, 'standard', 'on_stage_checkpoint', 'Standard technical and domain knowledge', datetime('now'));
    """,
]

# Migration 13: Unified Deliverables & Artifact Management
_M13 = [
    # 1. Canonical Artifact Identity & Metadata Table
    """
    CREATE TABLE artifacts (
        id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        artifact_type TEXT NOT NULL DEFAULT 'other' CHECK (artifact_type IN (
            'document', 'dataset', 'report', 'code', 'image', 'chart',
            'json', 'log', 'research_notes', 'presentation', 'archive', 'other'
        )),
        mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',
        file_extension TEXT NOT NULL DEFAULT '',
        size_bytes INTEGER NOT NULL DEFAULT 0,
        checksum_sha256 TEXT NOT NULL,
        storage_backend TEXT NOT NULL DEFAULT 'local_filesystem',
        storage_key TEXT NOT NULL UNIQUE,
        current_version_number INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'available' CHECK (status IN (
            'generating', 'uploading', 'validating', 'available',
            'needs_review', 'accepted', 'failed', 'missing', 'archived'
        )),
        validation_status TEXT NOT NULL DEFAULT 'valid' CHECK (validation_status IN (
            'pending', 'valid', 'invalid', 'unverified'
        )),
        validation_detail TEXT NOT NULL DEFAULT '',
        knowledge_status TEXT NOT NULL DEFAULT 'not_processed' CHECK (knowledge_status IN (
            'not_processed', 'processing', 'extracted', 'needs_verification',
            'verified', 'promotion_eligible', 'promoted', 'rejected'
        )),
        visibility TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN (
            'private', 'shared', 'collective'
        )),
        retention_policy TEXT NOT NULL DEFAULT 'standard' CHECK (retention_policy IN (
            'temporary', 'standard', 'indefinite', 'compliance_locked'
        )),
        origin_project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
        origin_stage_id TEXT,
        origin_task_id TEXT,
        origin_worker_id TEXT,
        origin_model_id TEXT,
        parent_artifact_id TEXT REFERENCES artifacts(id) ON DELETE SET NULL,
        created_by TEXT NOT NULL DEFAULT 'system',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_artifacts_proj ON artifacts(origin_project_id, status)",
    "CREATE INDEX idx_artifacts_type ON artifacts(artifact_type)",
    "CREATE INDEX idx_artifacts_sha ON artifacts(checksum_sha256)",
    "CREATE INDEX idx_artifacts_knowledge_status ON artifacts(knowledge_status)",

    # 2. Immutable Artifact Version History
    """
    CREATE TABLE artifact_versions (
        id TEXT PRIMARY KEY,
        artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
        version_number INTEGER NOT NULL,
        storage_backend TEXT NOT NULL DEFAULT 'local_filesystem',
        storage_key TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        checksum_sha256 TEXT NOT NULL,
        change_description TEXT NOT NULL DEFAULT '',
        producing_task_id TEXT,
        producing_worker_id TEXT,
        producing_model_id TEXT,
        validation_status TEXT NOT NULL DEFAULT 'valid',
        created_by TEXT NOT NULL DEFAULT 'system',
        created_at TEXT NOT NULL,
        UNIQUE(artifact_id, version_number)
    )
    """,
    "CREATE INDEX idx_artifact_versions_art ON artifact_versions(artifact_id, version_number)",

    # 3. Artifact Lineage Graph
    """
    CREATE TABLE artifact_lineage (
        id TEXT PRIMARY KEY,
        source_artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
        target_artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
        relationship_type TEXT NOT NULL CHECK (relationship_type IN (
            'derived_from', 'revised_from', 'summarizes', 'transforms',
            'analyzes', 'compares', 'cites', 'uses_as_input', 'incorporated_into'
        )),
        source_version_number INTEGER,
        target_version_number INTEGER,
        context_description TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_artifact_lineage_src ON artifact_lineage(source_artifact_id)",
    "CREATE INDEX idx_artifact_lineage_tgt ON artifact_lineage(target_artifact_id)",

    # 4. Project Associations (Direct, Shared, or Imported)
    """
    CREATE TABLE artifact_project_links (
        id TEXT PRIMARY KEY,
        artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        role TEXT NOT NULL DEFAULT 'output' CHECK (role IN (
            'output', 'input', 'shared_reference', 'imported', 'deliverable_backing'
        )),
        added_at TEXT NOT NULL,
        added_by TEXT NOT NULL DEFAULT 'system',
        UNIQUE(artifact_id, project_id, role)
    )
    """,
    "CREATE INDEX idx_art_proj_links_proj ON artifact_project_links(project_id)",

    # 5. Task Input & Output Linkages
    """
    CREATE TABLE artifact_task_links (
        id TEXT PRIMARY KEY,
        artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
        version_number INTEGER NOT NULL DEFAULT 1,
        task_id TEXT,
        orch_task_id TEXT,
        direction TEXT NOT NULL CHECK (direction IN ('input', 'output')),
        role TEXT NOT NULL DEFAULT 'primary',
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_art_task_links_task ON artifact_task_links(task_id)",

    # 6. Cross-Project Access Grants
    """
    CREATE TABLE artifact_access_grants (
        id TEXT PRIMARY KEY,
        artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
        source_project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        target_project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        granted_by TEXT NOT NULL,
        granted_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked')),
        UNIQUE(artifact_id, target_project_id)
    )
    """,
    "CREATE INDEX idx_art_grants_target ON artifact_access_grants(target_project_id, status)",

    # 7. Artifact Knowledge Extraction Linkage
    """
    CREATE TABLE artifact_knowledge_links (
        id TEXT PRIMARY KEY,
        artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
        artifact_version_number INTEGER NOT NULL DEFAULT 1,
        knowledge_record_id TEXT NOT NULL REFERENCES knowledge_records(id) ON DELETE CASCADE,
        extraction_method TEXT NOT NULL DEFAULT 'automated_nlp',
        extracted_claim TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_art_know_links_art ON artifact_knowledge_links(artifact_id)",
    "CREATE INDEX idx_art_know_links_know ON artifact_knowledge_links(knowledge_record_id)",

    # 8. Deliverable schema extension: Direct pointer to primary artifact & version
    "ALTER TABLE deliverables ADD COLUMN primary_artifact_id TEXT REFERENCES artifacts(id) ON DELETE SET NULL",
    "ALTER TABLE deliverables ADD COLUMN primary_artifact_version INTEGER DEFAULT 1",

    # 9. SQLite FTS5 Full-Text Search Table for Artifacts
    """
    CREATE VIRTUAL TABLE artifacts_fts USING fts5(
        artifact_id UNINDEXED,
        display_name,
        description,
        content_summary,
        tokenize='porter unicode61'
    )
    """,

    # 10. FTS Triggers for Artifacts
    """
    CREATE TRIGGER trg_artifacts_fts_insert AFTER INSERT ON artifacts BEGIN
        INSERT INTO artifacts_fts(artifact_id, display_name, description, content_summary)
        VALUES (new.id, new.display_name, new.description, new.display_name || ' ' || new.description || ' ' || new.artifact_type);
    END;
    """,
    """
    CREATE TRIGGER trg_artifacts_fts_delete AFTER DELETE ON artifacts BEGIN
        DELETE FROM artifacts_fts WHERE artifact_id = old.id;
    END;
    """,
    """
    CREATE TRIGGER trg_artifacts_fts_update AFTER UPDATE ON artifacts BEGIN
        DELETE FROM artifacts_fts WHERE artifact_id = old.id;
        INSERT INTO artifacts_fts(artifact_id, display_name, description, content_summary)
        VALUES (new.id, new.display_name, new.description, new.display_name || ' ' || new.description || ' ' || new.artifact_type);
    END;
    """,

    # 11. Historical Migration of Existing Artifact Metadata
    """
    INSERT OR IGNORE INTO artifacts (
        id, display_name, description, artifact_type, mime_type, file_extension,
        size_bytes, checksum_sha256, storage_backend, storage_key, current_version_number,
        status, validation_status, validation_detail, knowledge_status, visibility,
        retention_policy, origin_project_id, origin_task_id, origin_worker_id,
        created_by, created_at, updated_at
    )
    SELECT
        id,
        filename,
        'Historical artifact registered from prior task execution',
        CASE
            WHEN filename LIKE '%.json' THEN 'json'
            WHEN filename LIKE '%.md' THEN 'document'
            WHEN filename LIKE '%.txt' THEN 'document'
            WHEN filename LIKE '%.csv' THEN 'dataset'
            WHEN filename LIKE '%.py' OR filename LIKE '%.sh' THEN 'code'
            WHEN filename LIKE '%.png' OR filename LIKE '%.jpg' THEN 'image'
            ELSE 'other'
        END,
        mime_type,
        CASE WHEN instr(filename, '.') > 0 THEN substr(filename, instr(filename, '.')) ELSE '' END,
        size_bytes,
        sha256,
        'local_filesystem',
        storage_path,
        1,
        'available',
        validation_state,
        validation_detail,
        'not_processed',
        'private',
        'standard',
        project_id,
        task_id,
        worker_id,
        'migration',
        created_at,
        created_at
    FROM artifact_metadata;
    """,
    """
    INSERT OR IGNORE INTO artifact_versions (
        id, artifact_id, version_number, storage_backend, storage_key,
        size_bytes, checksum_sha256, change_description,
        producing_task_id, producing_worker_id, validation_status,
        created_by, created_at
    )
    SELECT
        'artver-' || substr(id, 5),
        id,
        1,
        'local_filesystem',
        storage_path,
        size_bytes,
        sha256,
        'Initial historical version from migration',
        task_id,
        worker_id,
        validation_state,
        'migration',
        created_at
    FROM artifact_metadata;
    """,
    """
    INSERT OR IGNORE INTO artifact_project_links (
        id, artifact_id, project_id, role, added_at, added_by
    )
    SELECT
        'apl-' || substr(id, 5),
        id,
        project_id,
        'output',
        created_at,
        'migration'
    FROM artifact_metadata
    WHERE project_id IS NOT NULL;
    """,
    """
    UPDATE deliverables
    SET primary_artifact_id = (
        SELECT a.id FROM artifacts a
        WHERE a.origin_project_id = deliverables.project_id
          AND instr(deliverables.artifact_references, a.id) > 0
        LIMIT 1
    )
    WHERE primary_artifact_id IS NULL AND artifact_references != '';
    """,
]

MIGRATIONS: List[Migration] = [
    (1, "project intelligence foundation", _M1),
    (2, "execution and evidence", _M2),
    (3, "measurement and forecasting", _M3),
    (4, "project-aware intelligence", _M4),
    (5, "historical learning and performance evaluation", _M5),
    (6, "infrastructure awareness and capability registry", _M6),
    (7, "worker communication, task leasing, and execution history", _M7),
    (8, "worker enrollment security and chat project context", _M8),
    (9, "autonomous project orchestrator", _M9),
    (10, "system observatory telemetry, rollups, inference accounting, and alerting", _M10),
    (11, "autonomous web research and browsing engine", _M11),
    (12, "continuous research and persistent knowledge integration", _M12),
    (13, "unified deliverables and artifact management refactor", _M13),
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
