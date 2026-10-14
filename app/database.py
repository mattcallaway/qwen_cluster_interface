import sqlite3
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from app.config import settings

logger = logging.getLogger("qwen_collective.db")

def get_db_connection() -> sqlite3.Connection:
    """Return a configured SQLite connection with row factories and foreign keys enabled."""
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def db_transaction() -> Iterator[sqlite3.Connection]:
    """Yield a connection inside BEGIN IMMEDIATE; commit on success, roll back on any error."""
    conn = get_db_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create the baseline schema, back up an existing DB if migrations are pending, then migrate."""
    from app.migrations import apply_migrations, backup_before_migration, current_version, latest_version

    db_existed = settings.db_path.exists() and settings.db_path.stat().st_size > 0
    create_baseline_schema()

    conn = get_db_connection()
    try:
        if db_existed and current_version(conn) < latest_version():
            backup_before_migration(conn, settings.db_path)
        apply_migrations(conn)
    finally:
        conn.close()
    logger.info("Database initialized successfully.")


def create_baseline_schema(conn: Optional[sqlite3.Connection] = None):
    """Schema version 0: the original tables. Do not modify; add migrations instead."""
    own_conn = conn is None
    if own_conn:
        conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Users table for authentication
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_login TEXT
    );
    """)
    
    # 2. Machines table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS machines (
        id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        hostname TEXT,
        ip_address TEXT NOT NULL,
        connection_type TEXT NOT NULL DEFAULT 'ssh', -- ssh, local, api
        os_info TEXT,
        cpu_info TEXT,
        ram_total_mb INTEGER,
        ram_avail_mb INTEGER,
        disk_info TEXT,
        gpu_info TEXT,
        network_info TEXT,
        role TEXT NOT NULL DEFAULT 'unassigned', -- control_server, inference_coordinator, distributed_worker, standalone_server, vps_worker, general_worker, unassigned
        connection_status TEXT NOT NULL DEFAULT 'unknown', -- online, offline, unreachable, auth_failed, error, unknown
        last_checked_at TEXT,
        last_successful_connection TEXT,
        capabilities_json TEXT DEFAULT '[]',
        notes TEXT DEFAULT '',
        is_enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)
    
    # 3. Machine Audit log
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS machine_audits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        machine_id TEXT NOT NULL,
        action TEXT NOT NULL,
        details TEXT,
        timestamp TEXT NOT NULL,
        FOREIGN KEY (machine_id) REFERENCES machines(id) ON DELETE CASCADE
    );
    """)
    
    # 4. Inference backends (standalone or group entrypoints)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS inference_backends (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        backend_type TEXT NOT NULL, -- llama_cpp, ollama
        endpoint_url TEXT NOT NULL,
        api_key TEXT,
        model_name TEXT,
        status TEXT NOT NULL DEFAULT 'untested', -- ready, offline, error, untested
        context_window INTEGER DEFAULT 4096,
        last_checked_at TEXT,
        error_details TEXT,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );
    """)
    
    # 5. Distributed inference groups (e.g., llama.cpp RPC coordination)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS distributed_groups (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        coordinator_machine_id TEXT,
        backend_type TEXT NOT NULL DEFAULT 'llama_cpp_rpc',
        model_file TEXT NOT NULL,
        context_window INTEGER DEFAULT 4096,
        runtime_parameters TEXT DEFAULT '',
        rpc_endpoints_json TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'unconfigured', -- unconfigured, ready_for_testing, running, unavailable, error
        notes TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)
    
    # 6. Benchmark records (genuine recorded metrics, never fabricated)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS benchmarks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_or_backend_name TEXT NOT NULL,
        model_name TEXT NOT NULL,
        quantization TEXT,
        context_length INTEGER,
        machines_involved TEXT,
        prompt_tokens_sec REAL,
        gen_tokens_sec REAL,
        memory_usage_mb REAL,
        notes TEXT,
        created_at TEXT NOT NULL
    );
    """)
    
    # 7. Tasks queue with approval and status tracking
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT,
        task_type TEXT NOT NULL DEFAULT 'general', -- probe, research, inference, vps_action, maintenance
        status TEXT NOT NULL DEFAULT 'draft', -- draft, queued, running, awaiting_approval, completed, failed, cancelled
        assigned_worker TEXT,
        attempt_count INTEGER DEFAULT 0,
        max_retries INTEGER DEFAULT 3,
        approval_required INTEGER DEFAULT 0,
        idempotency_key TEXT UNIQUE,
        input_data_json TEXT DEFAULT '{}',
        result_data_json TEXT DEFAULT '{}',
        error_details TEXT,
        related_knowledge_path TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)
    
    # 8. VPS Worker registrations
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS vps_nodes (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        endpoint_url TEXT NOT NULL,
        auth_token TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'unknown', -- online, offline, error, unknown
        capabilities_json TEXT DEFAULT '["health_check", "test_task"]',
        last_checked_at TEXT,
        permission_policy TEXT NOT NULL DEFAULT 'approval_required', -- automatically_permitted, approval_required, disabled
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)
    
    # 9. Chat conversations and message history
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chat_conversations (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL, -- user, assistant, system
        content TEXT NOT NULL,
        backend_used TEXT,
        model_used TEXT,
        created_at TEXT NOT NULL
    );
    """)
    
    # 10. SQLite FTS5 for knowledge search
    cursor.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
        path UNINDEXED,
        title,
        category,
        content,
        updated_at UNINDEXED
    );
    """)
    
    # 11. Bounded persistent rate-limiting table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS login_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key_type TEXT NOT NULL, -- 'ip' or 'user'
        key_value TEXT NOT NULL,
        attempt_time REAL NOT NULL
    );
    """)

    # Create indexes for performance
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_machines_status ON machines(connection_status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_conv_user ON chat_conversations(user_id, updated_at);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_messages(session_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_login_attempts ON login_attempts(key_type, key_value, attempt_time);")
    
    conn.commit()
    if own_conn:
        conn.close()
