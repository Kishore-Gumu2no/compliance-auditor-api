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


def unlink_chat(chat_id: int) -> Dict[str, int]:
    """Forget this Telegram chat: the link to the partner and its submissions.

    What a partner asked FairPay to remember about them, they can ask it to
    forget. Returns how many rows were removed.
    """
    ensure_tables()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM telegram_submissions WHERE chat_id = %s", (chat_id,))
            submissions = cur.rowcount
            cur.execute("DELETE FROM telegram_links WHERE chat_id = %s", (chat_id,))
            links = cur.rowcount
        conn.commit()
        return {"links": links, "submissions": submissions}
    except Exception:
        conn.rollback()
        raise


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
    touch another partner's row.

    Seeing the result on the website is the second place a partner is shown it,
    so the trip-level details are dropped here too (see
    :func:`purge_submission_details`). Rows stored before purging existed are
    cleaned up the first time they are opened.
    """
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
    except Exception:
        conn.rollback()
        raise
    if updated > 0:
        purge_submission_details(submission_id, partner_id)
    return updated > 0


# ---------------------------------------------------------------------------
# Deleting the trip-level details once the partner has been shown the result
# ---------------------------------------------------------------------------
# The website promises that only the outcome is kept. So once a partner has
# been shown their result, everything that describes their individual orders -
# order ids, times of day, per-order amounts, login sessions - is deleted from
# the row. What stays is the answer itself: the status, the headline, the
# counts, the conditions and the comparison, which is what the website shows.

# Whole blocks of per-order evidence, removed outright.
_TRIP_LEVEL_BLOCKS = (
    "incentive_evidence",          # every trip read off the documents
    "transactions",                # per-order records (full audit)
    "reconciliation_predictions",  # per-order predictions (full audit)
    "work_time",                   # login/online sessions, with their times
    "evidence_summaries",          # printed totals read off documents
    "order_observations",          # per-order observations
)

PURGED_FLAG = "details_purged"


def _purge_incentive_period(period: Dict[str, Any]) -> None:
    """Drop the per-trip rows from one incentive period, keeping its verdict."""
    period.pop("relevant_transactions", None)  # one row per trip, with its time
    mismatch = period.get("counting_mismatch")
    if isinstance(mismatch, dict):
        mismatch.pop("orders", None)  # the order ids behind the count difference


def purge_trip_details(payload: Any) -> Any:
    """Return ``payload`` with every per-order detail removed.

    Pure, so it can be tested without a database. Safe to run twice.
    """
    if not isinstance(payload, dict):
        return payload
    result = dict(payload)
    for key in _TRIP_LEVEL_BLOCKS:
        if key in result:
            result[key] = [] if isinstance(result[key], list) else None

    audit = result.get("incentive_audit")
    if isinstance(audit, dict):
        audit = dict(audit)
        if isinstance(audit.get("primary"), dict):
            audit["primary"] = dict(audit["primary"])
            _purge_incentive_period(audit["primary"])
        if isinstance(audit.get("other_periods"), list):
            audit["other_periods"] = [dict(p) for p in audit["other_periods"] if isinstance(p, dict)]
            for period in audit["other_periods"]:
                _purge_incentive_period(period)
        result["incentive_audit"] = audit

    fit = result.get("incentive_fit")
    if isinstance(fit, dict) and isinstance(fit.get("rider_pattern"), dict):
        fit = dict(fit)
        pattern = dict(fit["rider_pattern"])
        pattern.pop("busiest_hours", None)   # which hours of the day they worked
        pattern.pop("orders_per_day", None)  # the per-day order counts
        fit["rider_pattern"] = pattern
        result["incentive_fit"] = fit

    if isinstance(result.get("spot_check"), list):
        checks = []
        for check in result["spot_check"]:
            check = dict(check) if isinstance(check, dict) else check
            if isinstance(check, dict):
                check.pop("orders", None)  # the sampled orders themselves
            checks.append(check)
        result["spot_check"] = checks

    result[PURGED_FLAG] = True
    return result


def _purge_results_by_platform(payload: Any) -> Any:
    """Keep only whether each platform was audited, not what was read from it."""
    if not isinstance(payload, dict):
        return payload
    kept = {}
    for platform, outcome in payload.items():
        if not isinstance(outcome, dict):
            kept[platform] = outcome
            continue
        kept[platform] = {k: v for k, v in outcome.items()
                          if k in ("status", "platform", "error", "message")}
    return kept


def purge_submission_details(submission_id: str, partner_id: Optional[str] = None) -> bool:
    """Delete the trip-level details of one submission the partner has been shown.

    Returns True when a row was rewritten. Running it again changes nothing.
    """
    ensure_tables()
    where = "submission_id = %s" + (" AND partner_id = %s" if partner_id else "")
    params: tuple = (submission_id, partner_id) if partner_id else (submission_id,)
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT merged_result, results_by_platform FROM telegram_submissions WHERE {where}", params)
            row = cur.fetchone()
            if not row or (row["merged_result"] or {}).get(PURGED_FLAG):
                conn.commit()
                return False
            cur.execute(
                f"""
                UPDATE telegram_submissions
                   SET merged_result = %s, results_by_platform = %s
                 WHERE {where}
                """,
                (Jsonb(purge_trip_details(row["merged_result"] or {})),
                 Jsonb(_purge_results_by_platform(row["results_by_platform"])),
                 *params),
            )
            updated = cur.rowcount
        conn.commit()
        return updated > 0
    except Exception:
        conn.rollback()
        raise


def purge_shown_submission_details() -> List[str]:
    """Clean up every completed submission that still holds trip-level details.

    For rows stored before purging existed. Returns the ids that were cleaned.
    """
    ensure_tables()
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT submission_id
              FROM telegram_submissions
             WHERE status = 'complete'
               AND merged_result IS NOT NULL
               AND COALESCE((merged_result->>%s)::boolean, FALSE) = FALSE
            """,
            (PURGED_FLAG,),
        )
        ids = [str(r["submission_id"]) for r in cur.fetchall()]
    return [sid for sid in ids if purge_submission_details(sid)]


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
