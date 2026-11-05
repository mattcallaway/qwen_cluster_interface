# Knowledge Base Schema Specification

## 1. Overview & Migration Architecture

Persistent knowledge and continuous research entities are defined in **Migration 12** (`app/migrations.py`). The schema provides strict relational integrity, SQLite FTS5 full-text indexing, append-safe impact tracking, and configurable freshness policies.

---

## 2. Table Definitions

### 2.1 `knowledge_records`
Canonical structured knowledge records scoped to either a project or the collective memory.

```sql
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
);
```

#### Indexes
- `idx_knowledge_records_scope`: `(scope, project_id)`
- `idx_knowledge_records_type`: `(knowledge_type)`
- `idx_knowledge_records_status`: `(verification_status)`
- `idx_knowledge_records_created`: `(created_at)`

---

### 2.2 `knowledge_records_fts`
SQLite FTS5 virtual table providing full-text search across subject, statement, summary, topics, and keywords using the Porter stemmer and unicode tokenizer.

```sql
CREATE VIRTUAL TABLE knowledge_records_fts USING fts5(
    record_id UNINDEXED,
    subject,
    statement,
    summary,
    topics,
    keywords,
    tokenize='porter unicode61'
);
```

#### Synchronization Triggers
- `trg_knowledge_records_ai`: Automatically inserts indexed fields upon record creation.
- `trg_knowledge_records_ad`: Automatically removes index entries upon record deletion.
- `trg_knowledge_records_au`: Automatically updates index entries upon record modification.

---

### 2.3 `knowledge_impacts`
Tracks active reliance on knowledge records by planning assumptions, tasks, and deliverables. Supports immediate contradiction and supersession flagging.

```sql
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
);
```

#### Indexes
- `idx_knowledge_impacts_k`: `(knowledge_id)`
- `idx_knowledge_impacts_proj`: `(project_id, is_affected)`
- `idx_knowledge_impacts_run`: `(run_id)`
- `idx_knowledge_impacts_task`: `(task_id)`

---

### 2.4 `stage_research_checkpoints`
Audit record of mandatory research checkpoints executed across project lifecycle stages.

```sql
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
);
```

#### Indexes
- `idx_stage_checkpoints_proj`: `(project_id, stage_phase)`
- `idx_stage_checkpoints_run`: `(run_id)`

---

### 2.5 `knowledge_promotions`
Governance queue for elevating project-scoped knowledge into the shared collective knowledge base.

```sql
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
);
```

#### Indexes
- `idx_knowledge_promotions_status`: `(status)`
- `idx_knowledge_promotions_kid`: `(knowledge_id)`

---

### 2.6 `knowledge_freshness_policies`
Configurable freshness TTL thresholds by information domain.

```sql
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
);
```

#### Seeded Defaults
- `breaking_news`: 1 day TTL
- `product_pricing`: 7 days TTL
- `software_api`: 14 days TTL
- `standard`: 30 days TTL
- `law_regulation`: 90 days TTL
- `scientific_research`: 180 days TTL
- `internal_decision`: 365 days TTL (manual refresh)
- `historical_fact`: 3650 days TTL (manual refresh)
