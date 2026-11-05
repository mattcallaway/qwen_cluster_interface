"""Research telemetry and resource accounting service.

Tracks searches, fetches, browser sessions, extraction volume, latencies,
and errors for the System Observatory and Project Orchestrator.
Does not log raw secrets or private query content in telemetry records.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.database import get_db_connection

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ResearchTelemetryRecord:
    event_type: str  # search, fetch, browser, extract, verify, cite, error
    project_id: str
    task_id: Optional[str] = None
    provider: Optional[str] = None
    worker_id: Optional[str] = None
    duration_ms: float = 0.0
    status: str = "success"  # success, failure, rate_limited, timeout
    bytes_processed: int = 0
    tokens_consumed: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_now_iso)


class ResearchTelemetryService:
    """Manages research telemetry events and aggregates for observability."""

    def record_event(
        self,
        event_type: str,
        project_id: str,
        task_id: Optional[str] = None,
        provider: Optional[str] = None,
        worker_id: Optional[str] = None,
        duration_ms: float = 0.0,
        status: str = "success",
        bytes_processed: int = 0,
        tokens_consumed: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Records a research telemetry event."""
        # Sanitize metadata to exclude raw search queries or full page text
        safe_meta = dict(metadata or {})
        safe_meta.pop("query", None)
        safe_meta.pop("raw_query", None)
        safe_meta.pop("html", None)
        safe_meta.pop("text", None)
        safe_meta.pop("extracted_text", None)

        record = ResearchTelemetryRecord(
            event_type=event_type,
            project_id=project_id,
            task_id=task_id,
            provider=provider,
            worker_id=worker_id,
            duration_ms=duration_ms,
            status=status,
            bytes_processed=bytes_processed,
            tokens_consumed=tokens_consumed,
            metadata=safe_meta,
        )

        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            # If research_provider_status or research_budgets updates apply, record them
            if provider:
                now_str = _now_iso()
                is_avail = 1 if status == "success" else 0
                cursor.execute(
                    """
                    INSERT INTO research_provider_status (
                        provider_name, is_available, consecutive_failures, last_error, last_success_at, total_queries, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider_name) DO UPDATE SET
                        is_available = excluded.is_available,
                        consecutive_failures = CASE WHEN excluded.is_available = 1 THEN 0 ELSE research_provider_status.consecutive_failures + 1 END,
                        last_error = excluded.last_error,
                        last_success_at = CASE WHEN excluded.is_available = 1 THEN excluded.last_success_at ELSE research_provider_status.last_success_at END,
                        total_queries = research_provider_status.total_queries + 1,
                        updated_at = excluded.updated_at
                    """,
                    (
                        provider,
                        is_avail,
                        0 if is_avail == 1 else 1,
                        None if is_avail == 1 else f"Status: {status}",
                        now_str if is_avail == 1 else None,
                        1,
                        now_str,
                    ),
                )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("Failed to record provider telemetry to DB: %s", e)

    def get_project_stats(self, project_id: str) -> Dict[str, Any]:
        """Calculates aggregate research statistics for a specific project."""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            # Queries count
            cursor.execute("SELECT COUNT(*) FROM research_queries WHERE project_id = ?", (project_id,))
            queries_count = cursor.fetchone()[0] or 0

            # Sources count and total bytes
            cursor.execute("SELECT COUNT(*), COALESCE(SUM(content_length_bytes), 0) FROM research_sources WHERE project_id = ?", (project_id,))
            row = cursor.fetchone()
            sources_count = row[0] or 0
            total_bytes = row[1] or 0

            # Verified claims count
            cursor.execute("SELECT COUNT(*) FROM research_claims WHERE project_id = ? AND LOWER(verification_status) IN ('supported', 'corroborated')", (project_id,))
            verified_claims_count = cursor.fetchone()[0] or 0

            # Total claims
            cursor.execute("SELECT COUNT(*) FROM research_claims WHERE project_id = ?", (project_id,))
            total_claims_count = cursor.fetchone()[0] or 0

            # Citations count
            cursor.execute("SELECT COUNT(*) FROM research_citations WHERE project_id = ?", (project_id,))
            citations_count = cursor.fetchone()[0] or 0

            # Budget
            cursor.execute("SELECT max_queries, max_docs, queries_used, docs_used, browser_seconds_used, tokens_used FROM research_budgets WHERE project_id = ?", (project_id,))
            budget_row = cursor.fetchone()
            budget_info = {}
            if budget_row:
                budget_info = {
                    "max_search_queries": budget_row[0],
                    "max_retrieved_documents": budget_row[1],
                    "queries_executed": budget_row[2],
                    "documents_retrieved": budget_row[3],
                    "browsing_seconds_used": budget_row[4],
                    "tokens_consumed": budget_row[5],
                }

            return {
                "project_id": project_id,
                "queries_executed": queries_count,
                "sources_retrieved": sources_count,
                "total_bytes_processed": total_bytes,
                "verified_claims": verified_claims_count,
                "total_claims": total_claims_count,
                "citations_generated": citations_count,
                "budget": budget_info,
            }
        except Exception as e:
            logger.error("Failed to fetch project research stats: %s", e)
            return {"project_id": project_id, "error": str(e)}
        finally:
            conn.close()

    def get_cluster_research_summary(self) -> Dict[str, Any]:
        """Provides cluster-wide research metrics for the System Observatory."""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT COUNT(*) FROM research_queries")
            total_queries = cursor.fetchone()[0] or 0

            cursor.execute("SELECT COUNT(*) FROM research_sources")
            total_sources = cursor.fetchone()[0] or 0

            cursor.execute("SELECT COUNT(*) FROM research_claims")
            total_claims = cursor.fetchone()[0] or 0

            cursor.execute("SELECT provider_name, is_available, consecutive_failures, last_error, last_success_at, total_queries, updated_at FROM research_provider_status")
            providers = []
            for r in cursor.fetchall():
                providers.append({
                    "provider": r[0],
                    "is_available": bool(r[1]),
                    "consecutive_failures": r[2],
                    "last_error": r[3],
                    "last_success_at": r[4],
                    "total_queries": r[5],
                    "updated_at": r[6],
                })

            return {
                "total_research_queries": total_queries,
                "total_sources_ingested": total_sources,
                "total_claims_tracked": total_claims,
                "providers": providers,
            }
        except Exception as e:
            logger.error("Failed to query cluster research summary: %s", e)
            return {"error": str(e)}
        finally:
            conn.close()
