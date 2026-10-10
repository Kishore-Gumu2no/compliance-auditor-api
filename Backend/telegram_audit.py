"""Bridge between Telegram evidence batches and the existing audit engine.

This module deliberately contains **no** financial logic. It saves the files
the partner sent, calls the existing ``run_audit`` pipeline once per announced
platform (the same pipeline the website upload uses: OCR ingestion, PII
masking, extraction, reconciliation, calculations, findings, controller), then
merges the serialized states so the website can display them.

The only thing added on top is presentation: fields the engine reports as
missing are labelled for the partner, and the audit is never stopped because
of them.

A submission takes one of two branches:

* incentive_audit - "Why didn't I get my incentive?" (incentive_audit.py),
  never the run_audit pipeline.
* full_audit (or contract_spot_check) - the existing run_audit pipeline, with
  the sample boundary, contract questions and evidence limits added on top.

Documents that show only totals (an earnings summary or bank credits) are
not checked: the comparison of totals with bank credits was removed on
2026-10-08. Such a platform is reported back with a plain explanation.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from typing import Any, Callable, Dict, List, Optional

from audit_context import serialize_audit_state
from evidence_extractor import NOT_CHECKED_PREFIX, TOTALS_ONLY_MESSAGE
from evidence_modes import (
    CONTRACT_SPOT_CHECK,
    FULL_AUDIT,
    MODE_LABELS,
    build_contract_questions,
    build_spot_check,
    order_limits,
    working_time_limit,
)
from orchestrator import run_audit
from real_data import load_partner_profile, load_platform_contracts

logger = logging.getLogger("fairpay.telegram")

# Exact marker requested for fields the engine could have used but did not get.
MISSING_MARKER = "MISSING ---- FOR BETTER CALCULATION"

# The incentive check ("Why didn't I get my incentive?"). Kept apart from the
# evidence modes above: it never runs run_audit and its results are never
# merged into the order-level findings.
INCENTIVE_AUDIT = "incentive_audit"
INCENTIVE_LABEL = "Why didn't I get my incentive?"

# Shown when an app's documents hold only totals or bank credits.
TOTALS_NOT_CHECKED = (
    "These files show only totals (an earnings summary or bank credits), not single orders. "
    "FairPay checks single orders, so they were not checked."
)

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


# A platform batch with at most this many orders is treated as a sample (spot
# check); larger batches get the full audit presentation.
SPOT_CHECK_MAX_ORDERS = 20


def choose_order_mode(results_by_platform: Dict[str, Any]) -> str:
    """Spot check for a handful of orders, full audit for a fuller record."""
    largest = max(
        (len(s.get("order_observations") or []) for s in results_by_platform.values()
         if s.get("status") != "error"),
        default=0,
    )
    return CONTRACT_SPOT_CHECK if largest <= SPOT_CHECK_MAX_ORDERS else FULL_AUDIT


def run_submission_audit(
    partner_id: str,
    batches: Dict[str, List[Dict[str, Any]]],
    mode: Optional[str] = None,
    notify: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Run the submission through the branch its evidence calls for.

    ``batches`` maps a platform name to the documents announced under it, each
    ``{"kind": "pdf"|"image", "content": bytes}``.

    The partner chooses the incentive check or the full audit in Telegram.
    For an order-level audit the existing pipeline runs per platform. A
    platform whose documents show only totals, or nothing readable, is
    recorded as a failed platform and does not stop the others.

    The whole submission shares one AI reader choice: if Groq's daily
    allowance runs out on one app's files, the rest of the submission goes
    straight to the fallback reader and ``notify`` tells the partner once.
    """
    from reader_fallback import reader_switch_listener

    with reader_switch_listener(notify):
        return _run_submission_audit(partner_id, batches, mode, notify)


def _run_submission_audit(
    partner_id: str,
    batches: Dict[str, List[Dict[str, Any]]],
    mode: Optional[str],
    notify: Optional[Callable[[str], None]],
) -> Dict[str, Any]:
    profile = load_partner_profile(partner_id)
    if mode == INCENTIVE_AUDIT:
        return run_incentive_submission(partner_id, profile.name, SUBMISSION_REGION, batches, notify)
    if mode not in (None, CONTRACT_SPOT_CHECK, FULL_AUDIT):
        raise ValueError(f"Unknown evidence mode: {mode}")

    from reader_fallback import reader_switch_listener

    results_by_platform: Dict[str, Any] = {}
    for platform, files in batches.items():
        if not files:
            continue
        with tempfile.TemporaryDirectory(prefix="fairpay_tg_") as tmpdir, reader_switch_listener(notify):
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

    # Documents that hold only totals cannot be checked order by order.
    for state in results_by_platform.values():
        if state.get("status") == "error" and TOTALS_ONLY_MESSAGE in (state.get("error") or ""):
            state["error"] = TOTALS_NOT_CHECKED
    if mode is None:
        mode = choose_order_mode(results_by_platform)

    merged = _merge(partner_id, profile.name, results_by_platform)
    merged["evidence_mode"] = mode
    merged["evidence_mode_label"] = MODE_LABELS[mode]
    _add_order_level_evidence(merged, results_by_platform, mode)
    merged["evidence_modes"] = {p: mode for p in merged["partner_metadata"]["platforms"]}
    merged["counterfactual_context"] = build_merged_counterfactual_context(
        results_by_platform, merged
    )
    merged["counterfactual_context"]["evidence_mode"] = mode
    return {
        "results_by_platform": results_by_platform,
        "merged_result": merged,
        "missing_fields": merged["missing_fields"],
    }


def _contracts_on_file(platform: str) -> List[Dict[str, Any]]:
    """Contract terms on file for this platform; none if they cannot be read."""
    try:
        return load_platform_contracts(platform)
    except Exception:
        logger.exception("Could not load contract terms for %s", platform)
        return []


def _add_order_level_evidence(merged: Dict[str, Any], results: Dict[str, Any], mode: str) -> None:
    """Sample boundary (spot check), contract questions and evidence limits.

    Read-only over the pipeline's output: nothing here changes a
    reconciliation result or a finding.
    """
    spot_checks: List[Dict[str, Any]] = []
    questions: List[Dict[str, Any]] = []
    limits: List[Dict[str, Any]] = []
    notes: List[str] = []

    for platform in (merged.get("partner_metadata") or {}).get("platforms") or []:
        state = results.get(platform) or {}
        observations = state.get("order_observations") or []
        predictions = state.get("reconciliation_predictions") or []
        contracts = _contracts_on_file(platform)

        spot_check = None
        if mode == CONTRACT_SPOT_CHECK:
            spot_check = build_spot_check(platform, observations, predictions, contracts)
            spot_checks.append(spot_check)
        questions.extend(build_contract_questions(platform, observations, contracts))
        limits.extend(order_limits(mode, platform, observations, contracts, bool(predictions), spot_check))

        if state.get("evidence_summaries"):
            notes.append(
                f"Your {platform} documents also include an earnings total or bank credit. FairPay checks "
                "single orders, so it was not used."
            )

    if not merged.get("work_time"):
        limits.append(working_time_limit())

    if mode == CONTRACT_SPOT_CHECK:
        merged["spot_check"] = spot_checks
    merged["contract_questions"] = questions
    merged["could_not_check"] = limits
    merged["mode_notes"] = notes


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

    Written for someone with no technical background: no internal names for
    the checks FairPay chose. Detailed findings stay on the website.
    """
    if merged.get("evidence_mode") == INCENTIVE_AUDIT:
        return summarize_incentive_for_partner(merged)
    lines = ["✅ Done! I checked your files."]

    mode = merged.get("evidence_mode")
    if mode == CONTRACT_SPOT_CHECK:
        lines.extend(_spot_check_lines(merged))
    else:
        lines.extend(_order_lines(merged))

    for item in merged.get("failed_platforms") or []:
        lines.append("")
        if item.get("error") == TOTALS_NOT_CHECKED:
            lines.append(f"⚠️ Your {item['platform']} files show only totals, not single orders. "
                         "Please send your order or trip history instead.")
        else:
            lines.append(f"⚠️ I could not read your {item['platform']} files. Please send clearer pictures.")

    questions = merged.get("contract_questions") or []
    if questions:
        lines.append("")
        lines.append(f"Things to ask the app ({len(questions)}). These are questions, not proof of missing money:")
        for q in questions[:3]:
            lines.append(f"- {q['question']}")
        if len(questions) > 3:
            lines.append(f"- {len(questions) - 3} more on the website.")
    for note in merged.get("mode_notes") or []:
        lines.append("")
        lines.append(note)
    if merged.get("could_not_check"):
        lines.append("")
        lines.append("Some things could not be checked from these files. The website shows what they are.")

    lines.append("")
    lines.append("These results are only about the files you sent.")
    lines.append("See all the details on the FairPay website.")
    return "\n".join(lines)


def _spot_check_lines(merged: Dict[str, Any]) -> List[str]:
    """A few orders, each checked on its own. Never turned into a monthly number."""
    lines: List[str] = []
    for check in merged.get("spot_check") or []:
        lines.append("")
        checked, flagged_count = check["sample_orders_checked"], check["orders_with_findings"]
        lines.append(f"{check['platform']}: I checked {checked} {'order' if checked == 1 else 'orders'}.")
        if checked:
            lines.append(
                f"{flagged_count} {'order may have' if flagged_count == 1 else 'orders may have'} a problem."
                if flagged_count else "I did not find a problem with them."
            )
        flagged = [o for o in check["orders"] if o["status"] == "possible_issue"]
        for order in flagged[:5]:
            lines.append(f"- Order {order['order_id']}")
        if len(flagged) > 5:
            lines.append(f"- {len(flagged) - 5} more on the website.")
        if check["orders_not_checked"]:
            lines.append(f"I could not check {check['orders_not_checked']} order(s).")
        lines.append("This is only about these orders, not your whole month.")
    return lines


def _order_lines(merged: Dict[str, Any]) -> List[str]:
    """Order earnings vs payments, for a fuller set of orders."""
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

    lines: List[str] = [""]
    platforms = merged.get("partner_metadata", {}).get("platforms") or []
    if platforms:
        lines.append(f"Apps: {', '.join(platforms)}")

    if earning_predictions:
        lines.append(f"Orders I could match with a payment: {len(earning_predictions)}")
        lines.append(f"The app said you earned: Rs {expected:,.2f}")
        lines.append(f"You were paid: Rs {received:,.2f}")
        if shortfall > 0:
            lines.append(f"You may be missing: Rs {shortfall:,.2f}")
        elif shortfall < 0:
            lines.append(f"You were paid Rs {abs(shortfall):,.2f} more than the app said.")
        else:
            lines.append("No money is missing for these orders.")
    else:
        lines.append("I could not match any order with a payment.")

    # Never drop these silently: they are files the partner sent.
    not_checked = [
        gap.split(NOT_CHECKED_PREFIX, 1)[1].strip()
        for gap in merged.get("evidence_gaps") or []
        if NOT_CHECKED_PREFIX in gap
    ]
    if not_checked:
        lines.append("")
        lines.append("Not checked, because there is no date on them:")
        for item in not_checked:
            lines.append(f"- {item}")

    if unmatched_payments:
        lines.append("")
        lines.append("Payments I could not match with an order:")
        for p in unmatched_payments:
            order = p.get("order_id") or "unknown order"
            lines.append(f"- {order}: Rs {_num(p.get('settlement_amount')):,.2f} paid")
        lines.append("Send the earnings for these orders, with their dates, so I can check them.")

    missing = merged.get("missing_fields") or []
    if missing:
        lines.append("")
        lines.append("Some details were not in your files:")
        for item in missing:
            lines.append(f"- {item['field']}: {MISSING_MARKER}")
        lines.append("I checked what you sent.")
    return lines


# ---------------------------------------------------------------------------
# Incentive check ("Why didn't I get my incentive?")
# ---------------------------------------------------------------------------

class IncompleteReading(Exception):
    """No platform's documents were read in full, so no answer is given."""

    def __init__(self, rider_message: str, keep_documents: bool):
        super().__init__(rider_message)
        self.rider_message = rider_message
        # True when the files are fine and only the reader was busy: the
        # partner can simply ask again later without sending them again.
        self.keep_documents = keep_documents


def _document_name(label: str) -> str:
    if label.startswith("image "):
        return "screenshot " + label.split(" ", 1)[1]
    if label.startswith("scanned PDF page "):
        return "PDF page " + label.rsplit(" ", 1)[1]
    return "PDF"


def _unread_message(platform: str, unread: List[Dict[str, Any]]) -> str:
    """What the partner is told when their documents were not read in full."""
    busy = [u for u in unread if u["reason"] == "busy"]
    if busy:
        wait = max(u.get("wait_minutes") or 1 for u in busy)
        minutes = f"{wait} {'minute' if wait == 1 else 'minutes'}"
        if any(u.get("daily") for u in busy):
            # The day's allowance, not a passing busy moment.
            return (f"⚠️ FairPay has used up today's reading allowance, so I could not read your {platform} files. "
                    f"Nothing was checked yet. Please tap PLEASE AUDIT again in about {minutes}, when more of "
                    "today's allowance frees up.")
        return (f"⚠️ FairPay's reader is busy right now, so I could not read your {platform} files. Nothing was "
                f"checked yet. Please tap PLEASE AUDIT again in about {minutes}.")
    names = [_document_name(u["document"]) for u in unread]
    named = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return (f"⚠️ I could not read every trip on your {platform} {named}. To avoid a wrong answer, I did not check "
            "this day yet. Please send all your screenshots for this day again, making sure each one is clear, "
            "then tap PLEASE AUDIT.")


# Every incentive check sent through Telegram is checked against Bengaluru's
# incentive rules (city rules plus India-wide ones), whatever the profile says.
SUBMISSION_REGION = "Bengaluru"


def run_incentive_submission(
    partner_id: str, partner_name: str, region: Optional[str], batches: Dict[str, List[Dict[str, Any]]],
    notify: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Read the documents for incentive facts, then run the incentive audit.

    Uses the same OCR / PII-redaction / LLM reading as every FairPay check
    (incentive_extraction), deterministic evaluation (incentive_engine), and
    stores only the normalised facts, never the files.
    """
    from incentive_audit import build_incentive_report
    from incentive_evidence import IncentiveEvidence, evidence_from_extraction
    from incentive_extraction import extract_incentive_evidence
    from reader_fallback import reader_switch_listener
    from incentive_rules import default_store

    per_platform: Dict[str, Any] = {}
    evidences: List[IncentiveEvidence] = []
    for platform, files in batches.items():
        if not files:
            continue
        with tempfile.TemporaryDirectory(prefix="fairpay_tg_") as tmpdir:
            pdf_paths, image_paths = _write_batch(files, tmpdir)
            try:
                # If the reader has to switch to the slower one, the partner hears it first.
                with reader_switch_listener(notify):
                    extraction = extract_incentive_evidence(pdf_paths, image_paths, platform)
                if extraction.get("unread"):
                    # Part of the day is missing: judging the rest would give a wrong answer.
                    per_platform[platform] = {"status": "error", "platform": platform, "error": "not read in full",
                                              "unread": extraction["unread"],
                                              "message": _unread_message(platform, extraction["unread"])}
                    continue
                evidence = evidence_from_extraction(partner_id, platform, extraction, region=region)
                evidences.append(evidence)
                per_platform[platform] = {"status": "complete", "platform": platform,
                                          "incentive_evidence": evidence.model_dump(mode="json")}
            except Exception as exc:  # one platform failing must not sink the rest
                per_platform[platform] = {"status": "error", "platform": platform,
                                          "error": f"{type(exc).__name__}: {exc}"}

    audited = [p for p, r in per_platform.items() if r["status"] != "error"]
    unread = [r for r in per_platform.values() if r.get("unread")]
    if unread and not audited:
        busy_only = all(u["reason"] == "busy" for r in unread for u in r["unread"])
        raise IncompleteReading("\n\n".join(r["message"] for r in unread), keep_documents=busy_only)
    failed = [{"platform": p, "error": r["error"], **({"message": r["message"]} if r.get("message") else {})}
              for p, r in per_platform.items() if r["status"] == "error"]
    merged: Dict[str, Any] = {
        "status": "complete" if audited else "error",
        "evidence_mode": INCENTIVE_AUDIT,
        "evidence_mode_label": INCENTIVE_LABEL,
        "partner_id": partner_id,
        "platform": ", ".join(audited) if audited else "unknown",
        "source": "telegram",
        "failed_platforms": failed,
        # Order-level fields stay empty: this check makes no order-payment claim.
        "reconciliation_predictions": [],
        "transactions": [],
        "missing_fields": [],
        "evidence_gaps": [],
        "work_time": [],
        "partner_metadata": {"partner_id": partner_id, "name": partner_name, "platforms": audited},
        "incentive_audit": None,
        "incentive_fit": None,
    }
    if evidences:
        combined = IncentiveEvidence(
            partner_id=partner_id, source="telegram", region=region,
            platforms=[p for e in evidences for p in e.platforms],
            trips=[t for e in evidences for t in e.trips],
            platform_counts=[c for e in evidences for c in e.platform_counts],
            payments=[x for e in evidences for x in e.payments],
            metrics=[m for e in evidences for m in e.metrics],
            trip_coverage=[c for e in evidences for c in e.trip_coverage],
            payment_coverage=[c for e in evidences for c in e.payment_coverage],
            printed_offers=[o for e in evidences for o in e.printed_offers],
            conflicts=[c for e in evidences for c in e.conflicts],
            read_gaps=[g for e in evidences for g in e.read_gaps],
        )
        report = build_incentive_report(combined, default_store())
        from audit_context import _json_safe
        merged.update(_json_safe(report))
        merged["incentive_evidence"] = combined.model_dump(mode="json")
        merged["evidence_gaps"] = list(combined.read_gaps)
    return {"results_by_platform": per_platform, "merged_result": merged, "missing_fields": []}


def summarize_incentive_for_partner(merged: Dict[str, Any]) -> str:
    """Short chat reply: why, what to do, what to send. Details stay on the website."""
    lines = ["✅ Done! I checked your files."]
    audit = merged.get("incentive_audit") or {}
    primary = audit.get("primary") or {}
    if primary:
        lines.append("")
        if primary.get("platform") and primary.get("period_label"):
            lines.append(f"{primary['platform']} incentive, {primary['period_label']}")
        lines.append(primary.get("headline", ""))
        if primary.get("next_action"):
            lines.append("")
            lines.append(f"What to do: {primary['next_action']}")
        requests = primary.get("evidence_requests") or []
        if requests:
            lines.append("")
            lines.append("To finish the check, please send:")
            lines.extend(f"- {r}" for r in requests)
    for item in merged.get("failed_platforms") or []:
        lines.append("")
        lines.append(item.get("message") or f"⚠️ I could not read your {item['platform']} files. Please send clearer pictures.")
    fit = merged.get("incentive_fit") or {}
    if fit.get("headline"):
        lines.append("")
        lines.append("Which incentive structure fits you better?")
        lines.append(fit["headline"])
        if fit.get("disclaimer"):
            lines.append(fit["disclaimer"])
    lines.append("")
    lines.append("These results are only about the files you sent.")
    lines.append("See all the details on the FairPay website.")
    return "\n".join(lines)
