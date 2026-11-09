"""Local persistent storage and analytics cache for Antigravity Observer.

Maintains ~/.antigravity-observer/observer.db to support fast historical queries,
analytics aggregations, search filtering, and diagnostic exports.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("antigravity_observer.storage")


class ObserverDatabase:
    """Manages the local SQLite database for historical observability records."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path:
            self.db_path = os.path.expanduser(db_path)
        else:
            base_dir = os.path.expanduser("~/.antigravity-observer")
            os.makedirs(base_dir, exist_ok=True)
            self.db_path = os.path.join(base_dir, "observer.db")

        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Initializes tables and indexes."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
            CREATE TABLE IF NOT EXISTS observed_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                conversation_id TEXT,
                trace_id TEXT UNIQUE,
                response_id TEXT,
                endpoint TEXT,
                operation TEXT,
                model TEXT,
                prompt_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                thinking_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                latency_ms REAL DEFAULT 0,
                status_code INTEGER DEFAULT 200,
                metadata_json TEXT
            );
            """)

            # sqlite3's execute() accepts a single statement only, so each index
            # must be created with its own call.
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_requests_conv ON observed_requests(conversation_id);"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_requests_resp ON observed_requests(response_id);"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_requests_time ON observed_requests(timestamp);"
            )

            cur.execute("""
            CREATE TABLE IF NOT EXISTS quota_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                available_credits REAL,
                monthly_credits REAL,
                used_percentage REAL,
                gemini_weekly_fraction REAL,
                gemini_5h_fraction REAL,
                claude_weekly_fraction REAL,
                claude_5h_fraction REAL,
                raw_json TEXT
            );
            """)
            conn.commit()

    def record_request(self, req: Dict[str, Any]):
        """Inserts or updates an observed LLM request record."""
        trace_id = req.get("trace_id")
        if not trace_id:
            return

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO observed_requests (
                timestamp, conversation_id, trace_id, response_id, endpoint, operation,
                model, prompt_tokens, output_tokens, thinking_tokens, total_tokens,
                latency_ms, status_code, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trace_id) DO UPDATE SET
                response_id = excluded.response_id,
                model = excluded.model,
                prompt_tokens = excluded.prompt_tokens,
                output_tokens = excluded.output_tokens,
                thinking_tokens = excluded.thinking_tokens,
                total_tokens = excluded.total_tokens,
                metadata_json = excluded.metadata_json
            """, (
                req.get("timestamp", datetime.now(timezone.utc).isoformat()),
                req.get("conversation_id", ""),
                trace_id,
                req.get("response_id", ""),
                req.get("endpoint", ""),
                req.get("operation", ""),
                req.get("model", "Gemini 3.8 Flash (High)"),
                req.get("prompt_tokens", 0),
                req.get("output_tokens", 0),
                req.get("thinking_tokens", 0),
                req.get("total_tokens", 0),
                req.get("latency_ms", 0.0),
                req.get("status_code", 200),
                json.dumps(req.get("metadata", {})),
            ))
            conn.commit()

    def record_quota_snapshot(self, snapshot: Dict[str, Any]):
        """Records a periodic quota status snapshot."""
        credits = snapshot.get("prompt_credits") or {}
        groups = snapshot.get("quota_groups") or []

        g_weekly = None
        g_5h = None
        c_weekly = None
        c_5h = None

        for g in groups:
            gname = g.get("display_name", "").lower()
            buckets = g.get("buckets", [])
            for b in buckets:
                bid = b.get("bucket_id", "").lower()
                frac = b.get("remaining_fraction")
                if "gemini" in gname:
                    if "weekly" in bid:
                        g_weekly = frac
                    elif "5h" in bid:
                        g_5h = frac
                elif "claude" in gname:
                    if "weekly" in bid:
                        c_weekly = frac
                    elif "5h" in bid:
                        c_5h = frac

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO quota_snapshots (
                timestamp, available_credits, monthly_credits, used_percentage,
                gemini_weekly_fraction, gemini_5h_fraction, claude_weekly_fraction,
                claude_5h_fraction, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                snapshot.get("timestamp", datetime.now(timezone.utc).isoformat()),
                credits.get("available"),
                credits.get("monthly"),
                credits.get("used_percentage"),
                g_weekly,
                g_5h,
                c_weekly,
                c_5h,
                json.dumps(snapshot),
            ))
            conn.commit()

    def get_recent_requests(self, limit: int = 100, query: Optional[str] = None) -> List[Dict[str, Any]]:
        """Queries recent observed requests with optional text search."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            if query:
                like_q = f"%{query}%"
                cur.execute("""
                SELECT * FROM observed_requests
                WHERE trace_id LIKE ? OR response_id LIKE ? OR endpoint LIKE ? OR model LIKE ?
                ORDER BY id DESC LIMIT ?
                """, (like_q, like_q, like_q, like_q, limit))
            else:
                cur.execute("SELECT * FROM observed_requests ORDER BY id DESC LIMIT ?", (limit,))

            rows = cur.fetchall()
            return [dict(r) for r in rows]

    def get_analytics_summary(self) -> Dict[str, Any]:
        """Calculates global analytics summary."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
            SELECT
                COUNT(*) as total_requests,
                SUM(prompt_tokens) as sum_prompt_tokens,
                SUM(output_tokens) as sum_output_tokens,
                SUM(thinking_tokens) as sum_thinking_tokens,
                SUM(total_tokens) as sum_total_tokens,
                AVG(latency_ms) as avg_latency_ms
            FROM observed_requests
            """)
            row = cur.fetchone()

            # Model breakdown
            cur.execute("""
            SELECT model, COUNT(*) as count, SUM(total_tokens) as tokens
            FROM observed_requests
            GROUP BY model
            ORDER BY count DESC
            """)
            model_breakdown = [dict(r) for r in cur.fetchall()]

            return {
                "total_requests": row["total_requests"] or 0,
                "total_prompt_tokens": row["sum_prompt_tokens"] or 0,
                "total_output_tokens": row["sum_output_tokens"] or 0,
                "total_thinking_tokens": row["sum_thinking_tokens"] or 0,
                "total_tokens": row["sum_total_tokens"] or 0,
                "avg_latency_ms": round(row["avg_latency_ms"] or 0.0, 1),
                "model_breakdown": model_breakdown,
            }
