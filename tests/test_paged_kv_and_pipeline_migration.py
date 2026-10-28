import sqlite3
import pytest
from app.database import create_baseline_schema
from app.migrations import apply_migrations, current_version, MIGRATIONS


@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_baseline_schema(conn)
    apply_migrations(conn, MIGRATIONS)
    yield conn
    conn.close()


def test_migration_19_tables_exist(memory_db):
    assert current_version(memory_db) >= 19

    cursor = memory_db.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('kv_cache_pages', 'pipeline_cluster_stages')")
    found = {r[0] for r in cursor.fetchall()}
    assert found == {"kv_cache_pages", "pipeline_cluster_stages"}


def test_kv_cache_pages_crud_and_indexes(memory_db):
    cursor = memory_db.cursor()

    # Insert page
    cursor.execute("""
        INSERT INTO kv_cache_pages (
            page_hash, parent_page_hash, model_name, layer_start, layer_end,
            token_count, tokens_json, storage_path, byte_size, tensor_dtype,
            created_at, last_accessed_at
        ) VALUES (
            'hash_root_123', NULL, 'qwen2.5:7b', 0, 14,
            16, '[10, 20, 30, 40]', 'data/kv_cache/ha/hash_root_123.bin', 4096, 'float16',
            '2026-10-28T00:00:00Z', '2026-10-28T00:00:00Z'
        )
    """)

    # Insert child page
    cursor.execute("""
        INSERT INTO kv_cache_pages (
            page_hash, parent_page_hash, model_name, layer_start, layer_end,
            token_count, tokens_json, storage_path, byte_size, tensor_dtype,
            created_at, last_accessed_at
        ) VALUES (
            'hash_child_456', 'hash_root_123', 'qwen2.5:7b', 0, 14,
            16, '[50, 60, 70, 80]', 'data/kv_cache/ha/hash_child_456.bin', 4096, 'float16',
            '2026-10-28T00:01:00Z', '2026-10-28T00:01:00Z'
        )
    """)
    memory_db.commit()

    # Query child by parent hash
    cursor.execute("SELECT page_hash FROM kv_cache_pages WHERE parent_page_hash = 'hash_root_123'")
    rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["page_hash"] == "hash_child_456"

    # Token count bounds constraint (must be between 1 and 128)
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute("""
            INSERT INTO kv_cache_pages (
                page_hash, parent_page_hash, model_name, layer_start, layer_end,
                token_count, tokens_json, storage_path, byte_size, tensor_dtype,
                created_at, last_accessed_at
            ) VALUES (
                'hash_bad', NULL, 'qwen2.5:7b', 0, 14,
                0, '[]', 'path', 100, 'float16',
                '2026-10-28T00:00:00Z', '2026-10-28T00:00:00Z'
            )
        """)


def test_pipeline_cluster_stages_crud_and_unique_constraint(memory_db):
    cursor = memory_db.cursor()

    # Ensure foreign key machine exists
    cursor.execute("""
        INSERT OR IGNORE INTO machines (id, hostname, ip_address, role, is_enabled, created_at, updated_at)
        VALUES ('node-laptop', 'qwen-laptopt', '100.101.187.115', 'distributed_worker', 1, '2026-10-28T00:00:00Z', '2026-10-28T00:00:00Z')
    """)
    cursor.execute("""
        INSERT OR IGNORE INTO machines (id, hostname, ip_address, role, is_enabled, created_at, updated_at)
        VALUES ('node-vps', 'qwen-vps', '100.71.194.68', 'vps_worker', 1, '2026-10-28T00:00:00Z', '2026-10-28T00:00:00Z')
    """)

    # Stage 0 on Laptop
    cursor.execute("""
        INSERT INTO pipeline_cluster_stages (
            stage_id, pipeline_name, stage_index, node_id, model_name,
            layer_start, layer_end, is_active, endpoint_url, created_at, updated_at
        ) VALUES (
            'stg_laptop_0', 'qwen2.5-7b-split', 0, 'node-laptop', 'qwen2.5:7b',
            0, 14, 1, 'http://100.101.187.115:8000', '2026-10-28T00:00:00Z', '2026-10-28T00:00:00Z'
        )
    """)

    # Stage 1 on VPS
    cursor.execute("""
        INSERT INTO pipeline_cluster_stages (
            stage_id, pipeline_name, stage_index, node_id, model_name,
            layer_start, layer_end, is_active, endpoint_url, created_at, updated_at
        ) VALUES (
            'stg_vps_1', 'qwen2.5-7b-split', 1, 'node-vps', 'qwen2.5:7b',
            14, 28, 1, 'http://100.71.194.68:8000', '2026-10-28T00:00:00Z', '2026-10-28T00:00:00Z'
        )
    """)
    memory_db.commit()

    # Query stages ordered by index
    cursor.execute("SELECT stage_index, node_id, layer_start, layer_end FROM pipeline_cluster_stages WHERE pipeline_name = 'qwen2.5-7b-split' ORDER BY stage_index")
    stages = [dict(r) for r in cursor.fetchall()]
    assert len(stages) == 2
    assert stages[0]["stage_index"] == 0
    assert stages[0]["node_id"] == "node-laptop"
    assert stages[1]["stage_index"] == 1
    assert stages[1]["node_id"] == "node-vps"

    # Enforce UNIQUE(pipeline_name, stage_index)
    with pytest.raises(sqlite3.IntegrityError):
        cursor.execute("""
            INSERT INTO pipeline_cluster_stages (
                stage_id, pipeline_name, stage_index, node_id, model_name,
                layer_start, layer_end, is_active, endpoint_url, created_at, updated_at
            ) VALUES (
                'stg_duplicate', 'qwen2.5-7b-split', 0, 'node-vps', 'qwen2.5:7b',
                0, 14, 1, 'http://100.71.194.68:8000', '2026-10-28T00:00:00Z', '2026-10-28T00:00:00Z'
            )
        """)
