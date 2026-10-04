"""Persistence for Telegram evidence submissions.

Two additive tables:

* ``telegram_links``       - which Telegram chat belongs to which partner.
* ``telegram_submissions`` - one row per "PLEASE AUDIT" run, holding the
  serialized output of the existing audit pipeline so the website can display
  it and notify the partner that a Telegram audit finished.

Nothing here computes anything financial. It only stores what the existing
engine produced.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from real_data import conn  # reuse the existing PostgreSQL connection

# Single shared implementation; re-exported here for telegram_bot.
from earnings_opportunity import partner_exists  # noqa: F401

_DDL = """
CREATE TABLE IF NOT EXISTS telegram_links (
  chat_id    BIGINT PRIMARY KEY,
  partner_id TEXT NOT NULL REFERENCES partners(partner_id),
  linked_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS telegram_submissions (
  submission_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  partner_id          TEXT NOT NULL REFERENCES partners(partner_id),
  chat_id             BIGINT NOT NULL,
  status              TEXT NOT NULL CHECK (status IN ('processing','complete','failed')),
  platforms           JSONB NOT NULL DEFAULT '[]'::jsonb,
  document_counts     JSONB NOT NULL DEFAULT '{}'::jsonb,
  missing_fields      JSONB NOT NULL DEFAULT '[]'::jsonb,
  results_by_platform JSONB,
  merged_result       JSONB,
  error               TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at        TIMESTAMPTZ,
  seen_on_website     BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_telegram_submissions_partner_created
  ON telegram_submissions (partner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_telegram_links_partner
  ON telegram_links (partner_id);
"""

_ready = False


def ensure_tables() -> None:
    """Create the Telegram tables once per process. Idempotent and additive."""
    global _ready
    if _ready:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(_DDL)
        conn.commit()
        _ready = True
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------------------
# Chat <-> partner links
# ---------------------------------------------------------------------------

def link_chat(chat_id: int, partner_id: str) -> None:
    ensure_tables()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO telegram_links (chat_id, partner_id)
                VALUES (%s, %s)
                ON CONFLICT (chat_id) DO UPDATE
                  SET partner_id = EXCLUDED.partner_id, linked_at = now()
                """,
                (chat_id, partner_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_linked_partner(chat_id: int) -> Optional[str]:
    ensure_tables()
    with conn.cursor() as cur:
        cur.execute("SELECT partner_id FROM telegram_links WHERE chat_id = %s", (chat_id,))
        row = cur.fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Submissions
# ---------------------------------------------------------------------------

def create_submission(
    partner_id: str, chat_id: int, platforms: List[str], document_counts: Dict[str, Any]
) -> str:
    """Record that an audit run has started. Returns the submission id."""
    ensure_tables()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO telegram_submissions
                    (partner_id, chat_id, status, platforms, document_counts)
                VALUES (%s, %s, 'processing', %s, %s)
                RETURNING submission_id
                """,
                (partner_id, chat_id, Jsonb(platforms), Jsonb(document_counts)),
            )
            submission_id = cur.fetchone()[0]
        conn.commit()
        return str(submission_id)
    except Exception:
        conn.rollback()
        raise


def complete_submission(
    submission_id: str,
    results_by_platform: Dict[str, Any],
    merged_result: Dict[str, Any],
    missing_fields: List[Dict[str, Any]],
) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE telegram_submissions
                   SET status = 'complete',
                       results_by_platform = %s,
                       merged_result = %s,
                       missing_fields = %s,
                       completed_at = now()
                 WHERE submission_id = %s
                """,
                (
                    Jsonb(results_by_platform),
                    Jsonb(merged_result),
                    Jsonb(missing_fields),
                    submission_id,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def fail_submission(submission_id: str, error: str) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE telegram_submissions
                   SET status = 'failed', error = %s, completed_at = now()
                 WHERE submission_id = %s
                """,
                (error, submission_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


_SELECT_COLUMNS = """
    submission_id, partner_id, chat_id, status, platforms, document_counts,
    missing_fields, error, created_at, completed_at, seen_on_website
"""


def list_submissions(partner_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Summaries for this partner only, newest first (no heavy result blobs)."""
    ensure_tables()
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT {_SELECT_COLUMNS}
            FROM telegram_submissions
            WHERE partner_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (partner_id, limit),
        )
        rows = cur.fetchall()
    return [_summary(r) for r in rows]


def get_latest_complete(partner_id: str) -> Optional[Dict[str, Any]]:
    """The newest completed submission for this partner, including its results."""
    ensure_tables()
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT {_SELECT_COLUMNS}, results_by_platform, merged_result
            FROM telegram_submissions
            WHERE partner_id = %s AND status = 'complete'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (partner_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    payload = _summary(row)
    payload["results_by_platform"] = row["results_by_platform"]
    payload["merged_result"] = row["merged_result"]
    return payload


def get_submission_result(partner_id: str, submission_id: str) -> Optional[Dict[str, Any]]:
    """One completed submission's merged result, only if it belongs to this partner."""
    ensure_tables()
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT merged_result
                FROM telegram_submissions
                WHERE submission_id = %s AND partner_id = %s AND status = 'complete'
                """,
                (submission_id, partner_id),
            )
            row = cur.fetchone()
    except Exception:
        conn.rollback()  # e.g. a malformed UUID must not poison the shared connection
        raise
    return row["merged_result"] if row else None


def mark_seen(submission_id: str, partner_id: str) -> bool:
    """Mark one submission as seen. Scoped by partner so a stray id cannot
    touch another partner's row."""
    ensure_tables()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE telegram_submissions
                   SET seen_on_website = TRUE
                 WHERE submission_id = %s AND partner_id = %s
                """,
                (submission_id, partner_id),
            )
            updated = cur.rowcount
        conn.commit()
        return updated > 0
    except Exception:
        conn.rollback()
        raise


def _summary(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "submission_id": str(row["submission_id"]),
        "partner_id": row["partner_id"],
        "chat_id": row["chat_id"],
        "status": row["status"],
        "platforms": row["platforms"],
        "document_counts": row["document_counts"],
        "missing_fields": row["missing_fields"],
        "error": row["error"],
        "created_at": row["created_at"].isoformat(),
        "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
        "seen_on_website": row["seen_on_website"],
    }
