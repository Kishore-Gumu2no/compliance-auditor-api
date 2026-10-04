"""Bridge between Telegram evidence batches and the existing audit engine.

This module deliberately contains **no** financial logic. It saves the files
the partner sent, calls the existing ``run_audit`` pipeline once per announced
platform (the same pipeline the website upload uses: OCR ingestion, PII
masking, extraction, reconciliation, calculations, findings, controller), then
merges the serialized states so the website can display them.

The only thing added on top is presentation: fields the engine reports as
missing are labelled for the partner, and the audit is never stopped because
of them.
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import Any, Dict, List

from audit_context import serialize_audit_state
from evidence_extractor import NOT_CHECKED_PREFIX
from orchestrator import run_audit
from real_data import load_partner_profile

# Exact marker requested for fields the engine could have used but did not get.
MISSING_MARKER = "MISSING ---- FOR BETTER CALCULATION"

# The engine's own gap wording, e.g. "distance_km missing for record REC-1".
_GAP_RE = re.compile(r"^(?P<field>\w+) missing for record (?P<record>.+)$")


def mark_missing_fields(gaps: List[str]) -> tuple[List[str], List[Dict[str, Any]]]:
    """Label the engine's missing-field gaps; pass other gaps through untouched.

    Returns ``(labelled_gaps, summary)`` where summary counts each missing
    field. This is presentation only - no value is invented or filled in.
    """
    labelled: List[str] = []
    counts: Dict[str, int] = {}
    for gap in gaps:
        match = _GAP_RE.match(gap.strip())
        if match:
            field = match.group("field")
            record = match.group("record")
            counts[field] = counts.get(field, 0) + 1
            labelled.append(f"{field} - {MISSING_MARKER} (record {record})")
        else:
            labelled.append(gap)
    summary = [
        {"field": field, "records_affected": count, "status": MISSING_MARKER}
        for field, count in sorted(counts.items())
    ]
    return labelled, summary


def _write_batch(files: List[Dict[str, Any]], directory: str) -> tuple[List[str], List[str]]:
    """Persist downloaded bytes to a temp directory for the extractor to read."""
    pdf_paths: List[str] = []
    image_paths: List[str] = []
    for index, item in enumerate(files):
        suffix = ".pdf" if item["kind"] == "pdf" else ".png"
        path = os.path.join(directory, f"{item['kind']}_{index}{suffix}")
        with open(path, "wb") as handle:
            handle.write(item["content"])
        (pdf_paths if item["kind"] == "pdf" else image_paths).append(path)
    return pdf_paths, image_paths


def run_submission_audit(partner_id: str, batches: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Run the existing audit pipeline once per announced platform.

    ``batches`` maps a platform name to the documents announced under it, each
    ``{"kind": "pdf"|"image", "content": bytes}``.

    A platform whose documents yield nothing readable is recorded as a failed
    platform and does not stop the other platforms from being audited.
    """
    profile = load_partner_profile(partner_id)

    results_by_platform: Dict[str, Any] = {}
    for platform, files in batches.items():
        if not files:
            continue
        with tempfile.TemporaryDirectory(prefix="fairpay_tg_") as tmpdir:
            pdf_paths, image_paths = _write_batch(files, tmpdir)
            try:
                state = run_audit(
                    partner_id=partner_id,
                    partner_profile=profile,
                    pdf_paths=pdf_paths,
                    image_paths=image_paths,
                    platform=platform,
                    contract_versions=[],
                )
                serialized = serialize_audit_state(state)
            except Exception as exc:  # one platform failing must not sink the rest
                serialized = {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "platform": platform,
                    "evidence_gaps": [],
                    "reconciliation_predictions": [],
                    "transactions": [],
                    "audit_findings": None,
                }
        results_by_platform[platform] = serialized

    merged = _merge(partner_id, profile.name, results_by_platform)
    merged["counterfactual_context"] = build_merged_counterfactual_context(
        results_by_platform, merged
    )
    return {
        "results_by_platform": results_by_platform,
        "merged_result": merged,
        "missing_fields": merged["missing_fields"],
    }


def _merge(partner_id: str, partner_name: str, results: Dict[str, Any]) -> Dict[str, Any]:
    """Combine per-platform states into one payload the website can render.

    Each reconciliation prediction and transaction is tagged with the platform
    it was announced under, which is what the Audit Results platform dropdown
    filters on.
    """
    predictions: List[Dict[str, Any]] = []
    transactions: List[Dict[str, Any]] = []
    work_time: List[Dict[str, Any]] = []
    labelled_gaps: List[str] = []
    missing_counts: Dict[str, int] = {}
    audited_platforms: List[str] = []
    failed_platforms: List[Dict[str, str]] = []

    for platform, state in results.items():
        if state.get("status") == "error":
            failed_platforms.append({"platform": platform, "error": state.get("error") or "Audit failed"})
            continue
        audited_platforms.append(platform)

        for prediction in state.get("reconciliation_predictions") or []:
            item = dict(prediction)
            item.setdefault("platform", platform)
            predictions.append(item)

        for transaction in state.get("transactions") or []:
            item = dict(transaction)
            item.setdefault("platform", platform)
            transactions.append(item)

        for entry in state.get("work_time") or []:
            item = dict(entry)
            item["platform"] = platform
            work_time.append(item)

        # Mark first, then prefix: the marker matches the engine's own gap
        # wording, which the platform prefix would otherwise hide.
        platform_gaps, platform_missing = mark_missing_fields(
            state.get("evidence_gaps") or []
        )
        labelled_gaps.extend(f"[{platform}] {gap}" for gap in platform_gaps)
        for item in platform_missing:
            missing_counts[item["field"]] = (
                missing_counts.get(item["field"], 0) + item["records_affected"]
            )

    missing_summary = [
        {"field": field, "records_affected": count, "status": MISSING_MARKER}
        for field, count in sorted(missing_counts.items())
    ]

    return {
        "status": "complete" if audited_platforms else "error",
        "partner_id": partner_id,
        "platform": ", ".join(audited_platforms) if audited_platforms else "unknown",
        "source": "telegram",
        "reconciliation_predictions": predictions,
        "transactions": transactions,
        "work_time": work_time,
        "evidence_gaps": labelled_gaps,
        "missing_fields": missing_summary,
        "failed_platforms": failed_platforms,
        # Findings stay per platform; they are not arithmetically combinable.
        "audit_findings": (
            results[audited_platforms[0]].get("audit_findings")
            if len(audited_platforms) == 1
            else None
        ),
        "partner_metadata": {
            "partner_id": partner_id,
            "name": partner_name,
            "platforms": audited_platforms,
        },
    }


def build_merged_counterfactual_context(
    results_by_platform: Dict[str, Any], merged: Dict[str, Any]
) -> Dict[str, Any]:
    """Combine each platform's counterfactual context into one for the simulator.

    Same PII-free shape as audit_context.build_counterfactual_context, so the
    website's "How to Earn More" simulator treats a Telegram audit like any
    other completed audit.
    """
    audited = (merged.get("partner_metadata") or {}).get("platforms") or []
    summary: Dict[str, int] = {}
    transaction_count = 0
    findings_summary = None
    for platform in audited:
        context = (results_by_platform.get(platform) or {}).get("counterfactual_context") or {}
        transaction_count += context.get("transaction_count") or 0
        for anomaly, count in (context.get("reconciliation_summary") or {}).items():
            summary[anomaly] = summary.get(anomaly, 0) + count
        if len(audited) == 1:
            findings_summary = context.get("findings_summary")

    merged_context: Dict[str, Any] = {
        "partner_id": merged.get("partner_id"),
        "platform": merged.get("platform"),
        "transaction_count": transaction_count,
        "reconciliation_summary": summary,
        "evidence_gaps": list(merged.get("evidence_gaps") or []),
        "source": "telegram",
    }
    if findings_summary:
        merged_context["findings_summary"] = findings_summary
    return merged_context


def summarize_for_partner(merged: Dict[str, Any]) -> str:
    """A short, plain-language result message for the Telegram chat.

    Detailed findings and dispute workflows stay on the website.
    """
    predictions = merged.get("reconciliation_predictions") or []
    earning_predictions = [
        p for p in predictions if p.get("anomaly_type") != "extra_unexplained_settlement"
    ]
    unmatched_payments = [
        p for p in predictions if p.get("anomaly_type") == "extra_unexplained_settlement"
    ]

    def _num(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    expected = sum(_num(p.get("expected_amount")) for p in earning_predictions)
    received = sum(_num(p.get("settlement_amount")) for p in earning_predictions)
    shortfall = expected - received

    lines = ["✅ Your audit is complete."]
    platforms = merged.get("partner_metadata", {}).get("platforms") or []
    if platforms:
        lines.append(f"Platforms checked: {', '.join(platforms)}")

    if earning_predictions:
        subject = "this order" if len(earning_predictions) == 1 else "these orders"
        lines.append(f"Orders fully checked: {len(earning_predictions)}")
        lines.append(f"Expected for {subject}: Rs {expected:,.2f}")
        lines.append(f"Received for {subject}: Rs {received:,.2f}")
        if shortfall > 0:
            lines.append(f"Possible shortfall: Rs {shortfall:,.2f}")
        elif shortfall < 0:
            lines.append(f"Paid more than expected by: Rs {abs(shortfall):,.2f}")
        else:
            lines.append(f"No difference found for {subject}.")
    else:
        lines.append("No order could be fully checked against a payment.")

    # Never drop these silently: they are evidence the partner sent.
    not_checked = [
        gap.split(NOT_CHECKED_PREFIX, 1)[1].strip()
        for gap in merged.get("evidence_gaps") or []
        if NOT_CHECKED_PREFIX in gap
    ]
    if not_checked:
        lines.append("")
        lines.append("Not checked, because your documents show no date for them:")
        for item in not_checked:
            lines.append(f"- {item}")

    if unmatched_payments:
        lines.append("")
        lines.append("Payments we could not match to an order's earnings:")
        for p in unmatched_payments:
            order = p.get("order_id") or "unknown order"
            lines.append(f"- {order}: Rs {_num(p.get('settlement_amount')):,.2f} paid")
        lines.append("Send the earning details for these orders, with their dates, to check them.")

    missing = merged.get("missing_fields") or []
    if missing:
        lines.append("")
        lines.append("Some details were not in your documents:")
        for item in missing:
            lines.append(f"- {item['field']}: {MISSING_MARKER}")
        lines.append("The audit ran with what you sent.")

    failed = merged.get("failed_platforms") or []
    for item in failed:
        lines.append(f"⚠️ Could not read the documents for {item['platform']}.")

    lines.append("")
    lines.append("These numbers are based only on the documents you sent.")
    lines.append("Open the FairPay website to see the full findings and raise a dispute.")
    return "\n".join(lines)
