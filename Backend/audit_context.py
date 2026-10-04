"""Serialize audit state and build sanitized counterfactual debate context."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from debate import DebateState


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    return str(value)


def build_counterfactual_context(state: Dict[str, Any]) -> Dict[str, Any]:
    """Structured, PII-free audit context for the counterfactual simulator."""
    predictions = state.get("reconciliation_predictions") or []
    anomaly_counts: Dict[str, int] = {}
    for prediction in predictions:
        anomaly_type = prediction.get("anomaly_type", "unknown")
        anomaly_counts[anomaly_type] = anomaly_counts.get(anomaly_type, 0) + 1

    context: Dict[str, Any] = {
        "partner_id": state.get("partner_id"),
        "platform": state.get("platform"),
        "transaction_count": len(state.get("transactions") or []),
        "reconciliation_summary": anomaly_counts,
        "evidence_gaps": list(state.get("evidence_gaps") or []),
    }

    findings = state.get("audit_findings")
    if findings is not None:
        context["findings_summary"] = {
            "overall_grade": _json_safe(getattr(findings, "overall_grade", None)),
            "summary": getattr(findings, "summary", ""),
            "contract_findings": len(getattr(findings, "contract_compliance_findings", []) or []),
            "legal_findings": len(getattr(findings, "legal_risk_findings", []) or []),
            "economic_findings": len(getattr(findings, "economic_concern_findings", []) or []),
        }

    decisions = state.get("controller_decisions") or []
    if decisions:
        by_status: Dict[str, int] = {}
        for decision in decisions:
            status = getattr(decision.resolution_status, "value", str(decision.resolution_status))
            by_status[status] = by_status.get(status, 0) + 1
        context["controller_summary"] = {
            "total": len(decisions),
            "human_review": sum(1 for d in decisions if d.human_review_required),
            "by_status": by_status,
        }

    batch_summary = state.get("batch_summary")
    if batch_summary is not None:
        context["batch_summary"] = _json_safe(batch_summary)

    return context


def debate_state_from_audit_context(
    audit_context: Dict[str, Any],
    *,
    platform: str,
    description: str,
) -> DebateState:
    """Build debate input from sanitized audit context (no raw document text)."""
    evidence_parts = [f"Platform: {platform}", f"Scenario: {description}"]
    if audit_context.get("reconciliation_summary"):
        evidence_parts.append(f"Reconciliation summary: {audit_context['reconciliation_summary']}")
    if audit_context.get("evidence_gaps"):
        evidence_parts.append(f"Evidence gaps: {len(audit_context['evidence_gaps'])} items")
    if audit_context.get("findings_summary"):
        evidence_parts.append(f"Findings summary: {audit_context['findings_summary']}")

    version_info = "Contract versions supplied by partner upload" if audit_context.get("transaction_count") else "No contract version context"
    calculations = f"Processed {audit_context.get('transaction_count', 0)} transactions"
    findings = audit_context.get("findings_summary", {}).get("summary") or "No findings summary available"

    return {
        "evidence": ". ".join(evidence_parts),
        "version_info": version_info,
        "calculations": calculations,
        "findings": findings,
    }


def serialize_audit_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """Convert orchestrator output into a JSON-safe API payload."""
    payload: Dict[str, Any] = {
        "status": state.get("status"),
        "error": state.get("error"),
        "partner_id": state.get("partner_id"),
        "platform": state.get("platform"),
        "evidence_gaps": list(state.get("evidence_gaps") or []),
        "reconciliation_predictions": _json_safe(state.get("reconciliation_predictions") or []),
        "transactions": _json_safe(state.get("transactions") or []),
        "payment_calculations": _json_safe(state.get("payment_calculations") or []),
        "business_cost_calculations": _json_safe(state.get("business_cost_calculations") or []),
        "controller_decisions": _json_safe(state.get("controller_decisions") or []),
        "batch_summary": _json_safe(state.get("batch_summary")),
        "work_time": _json_safe(state.get("work_time") or []),
        "counterfactual_context": build_counterfactual_context(state),
    }

    findings = state.get("audit_findings")
    if findings is not None:
        payload["audit_findings"] = _json_safe(findings)
    else:
        payload["audit_findings"] = None

    return payload
