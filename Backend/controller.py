from __future__ import annotations
from typing import List, Dict, Optional, Any
from decimal import Decimal
from datetime import date

from models import (
    FairPayState, ControllerDecision, BatchSummary,
    ResolutionStatus, Priority, RecommendedAction,
    PaymentCalculation, AuditFindings,
    ContractComplianceFinding, LegalRiskFinding, EconomicConcernFinding,
    FindingGrade
)

AMOUNT_TOLERANCE = Decimal("0.50")


def controller_node(state: FairPayState) -> FairPayState:
    """
    Layer 3: Controller / Decision & Escalation

    Consumes Layer 1 (reconciliation_predictions) and Layer 2 (audit_findings)
    to produce per-transaction operational decisions and a batch summary.
    """
    payment_calcs = state.payment_calculations
    audit_findings = state.audit_findings
    transactions = state.transactions
    partner_id = state.partner_id
    reconciliation_predictions = state.reconciliation_predictions

    if not audit_findings:
        # No findings - mark all transactions as resolved automatically
        decisions = _create_auto_resolved_decisions(partner_id, transactions, payment_calcs, reconciliation_predictions)
    else:
        decisions = _make_decisions(
            partner_id=partner_id,
            transactions=transactions,
            payment_calcs=payment_calcs,
            audit_findings=audit_findings,
            reconciliation_predictions=reconciliation_predictions
        )

    batch_summary = _build_batch_summary(decisions)

    return {
        **state.model_dump(),
        "controller_decisions": decisions,
        "batch_summary": batch_summary,
        "status": "complete"
    }


def _make_decisions(
    partner_id: str,
    transactions: List,
    payment_calcs: List[PaymentCalculation],
    audit_findings: AuditFindings,
    reconciliation_predictions: List[Dict[str, Any]] = None
) -> List[ControllerDecision]:
    """Make per-transaction decisions based on Layer 1 + Layer 2."""

    # Build lookup maps
    contract_findings_by_tx = _group_by_tx(audit_findings.contract_compliance_findings)
    legal_findings_by_tx = _group_by_tx(audit_findings.legal_risk_findings)
    economic_findings_by_tx = _group_by_tx(audit_findings.economic_concern_findings)
    calc_by_tx = {pc_index_to_tx_id(i, transactions): pc for i, pc in enumerate(payment_calcs)}
    predictions_by_tx = {p.get("transaction_id"): p for p in (reconciliation_predictions or [])}

    decisions = []

    for i, tx in enumerate(transactions):
        tx_id = tx.transaction_id

        # Get Layer 1 facts
        calc = calc_by_tx.get(tx_id)
        prediction = predictions_by_tx.get(tx_id)
        layer1_facts = _extract_layer1_facts(calc, tx, prediction)

        # Get Layer 2 findings for this transaction
        contract_f = contract_findings_by_tx.get(tx_id, [])
        legal_f = legal_findings_by_tx.get(tx_id, [])
        economic_f = economic_findings_by_tx.get(tx_id, [])

        all_findings = list(contract_f) + list(legal_f) + list(economic_f)
        finding_ids = [f.finding_id for f in all_findings]

        # Make decision
        decision = _decide_transaction(
            partner_id=partner_id,
            transaction_id=tx_id,
            layer1_facts=layer1_facts,
            contract_findings=contract_f,
            legal_findings=legal_f,
            economic_findings=economic_f,
            evidence_gaps=audit_findings.evidence_gaps
        )

        decision.layer1_facts = layer1_facts
        decision.layer2_findings = finding_ids
        decision.evidence_references = finding_ids + ([str(i)] if calc else [])
        decisions.append(decision)

    return decisions


def _group_by_tx(findings: List) -> Dict[str, List]:
    """Group findings by transaction_id."""
    result = {}
    for f in findings:
        tx_id = f.transaction_id
        result.setdefault(tx_id, []).append(f)
    return result


def _extract_layer1_facts(calc: Optional[PaymentCalculation], tx, prediction: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Extract Layer 1 facts snapshot."""
    # Use anomaly_type from Layer 1 prediction if available
    anomaly_type = "unknown"
    if prediction:
        anomaly_type = prediction.get("anomaly_type", "unknown")

    if not calc:
        # Fallback for anomaly_type if prediction was missing
        if anomaly_type == "unknown":
            anomaly_type = "missing_payment" if tx.net_amount == 0 else "unknown"

        return {
            "variance": 0,
            "variance_pct": 0,
            "platform_net": float(tx.net_amount),
            "contractual_net": 0,
            "anomaly_type": anomaly_type
        }

    # When prediction exists, use reconciliation variance (settlement vs expected)
    # instead of contract variance (contractual_net vs platform_net)
    variance = float(calc.variance)
    variance_pct = float(calc.variance_pct)

    if prediction:
        pred_variance = Decimal(str(prediction.get("variance", 0)))
        pred_expected = Decimal(str(prediction.get("expected_amount", 0)))
        if pred_expected != 0:
            variance = float(pred_variance)
            variance_pct = float((pred_variance / pred_expected) * Decimal("100"))
        else:
            variance = float(pred_variance)
            variance_pct = 0.0

    return {
        "variance": variance,
        "variance_pct": variance_pct,
        "platform_net": float(calc.platform_net),
        "contractual_net": float(calc.contractual_net),
        "anomaly_type": anomaly_type
    }


def pc_index_to_tx_id(index: int, transactions: List) -> Optional[str]:
    """Map payment_calculation index to transaction_id."""
    if 0 <= index < len(transactions):
        return transactions[index].transaction_id
    return None


def _get_evidence_strength(finding) -> Decimal:
    """Safely extract evidence strength from any finding type.

    ContractComplianceFinding and LegalRiskFinding have evidence_strength.
    EconomicConcernFinding does not - derive conservative proxy from severity.
    """
    if hasattr(finding, 'evidence_strength'):
        return finding.evidence_strength
    # EconomicConcernFinding: no evidence_strength field
    # Conservative fallback: map severity to evidence proxy
    # critical=0.9, high=0.7, medium=0.5, low=0.3
    severity_map = {"critical": Decimal("0.9"), "high": Decimal("0.7"),
                    "medium": Decimal("0.5"), "low": Decimal("0.3")}
    return severity_map.get(finding.severity, Decimal("0.3"))


def _decide_transaction(
    partner_id: str,
    transaction_id: str,
    layer1_facts: Dict[str, Any],
    contract_findings: List[ContractComplianceFinding],
    legal_findings: List[LegalRiskFinding],
    economic_findings: List[EconomicConcernFinding],
    evidence_gaps: List[str]
) -> ControllerDecision:
    """
    Deterministic decision logic for a single transaction.

    Priority order:
    1. If human_review_required=True on any finding → HUMAN_REVIEW
    2. If legal findings exist with CONFIRMED_VIOLATION → LEGAL_REVIEW or HUMAN_REVIEW
    3. If contract finding with CONFIRMED_VIOLATION → REVIEW_CONTRACT
    4. If economic finding with severity >= high → ACTION_REQUIRED
    5. If payment shortfall with evidence → RAISE_PAYMENT_DISPUTE
    6. If perfect match and no findings → RESOLVED_AUTOMATICALLY
    7. If insufficient evidence → INSUFFICIENT_EVIDENCE
    """

    anomaly_type = layer1_facts.get("anomaly_type", "unknown")
    variance = Decimal(str(layer1_facts.get("variance", 0)))

    # Collect all findings
    all_findings = contract_findings + legal_findings + economic_findings

    # Check if any finding requires human review
    any_human_review = any(f.human_review_required for f in all_findings)

    # Check for high-severity findings
    has_critical = any(f.severity == "critical" for f in all_findings if hasattr(f, 'severity') and f.severity)
    has_high = any(f.severity == "high" for f in all_findings if hasattr(f, 'severity') and f.severity)
    has_confirmed_violation = any(f.grade == FindingGrade.CONFIRMED_VIOLATION for f in all_findings)
    has_potential_issue = any(f.grade == FindingGrade.POTENTIAL_ISSUE for f in all_findings)

    # Check evidence strength - safe for all finding types
    max_evidence = max((_get_evidence_strength(f) for f in all_findings), default=Decimal("0"))

    # --- DECISION LOGIC ---

    # Case 1: No findings at all - perfect match
    if not all_findings and anomaly_type == "perfect_match":
        return ControllerDecision(
            transaction_id=transaction_id,
            partner_id=partner_id,
            resolution_status=ResolutionStatus.RESOLVED_AUTOMATICALLY,
            priority=Priority.LOW,
            recommended_action=RecommendedAction.NO_ACTION,
            human_review_required=False,
            reason="Settlement matches expected amount exactly. No findings detected."
        )

    # Case 2: Missing payment (no settlement at all)
    if anomaly_type == "missing_payment":
        if any_human_review:
            return ControllerDecision(
                transaction_id=transaction_id,
                partner_id=partner_id,
                resolution_status=ResolutionStatus.HUMAN_REVIEW,
                priority=Priority.HIGH,
                recommended_action=RecommendedAction.LEGAL_REVIEW,
                human_review_required=True,
                reason="No settlement received for this earning. Human review required for missing payment."
            )
        return ControllerDecision(
            transaction_id=transaction_id,
            partner_id=partner_id,
            resolution_status=ResolutionStatus.ACTION_REQUIRED,
            priority=Priority.HIGH,
            recommended_action=RecommendedAction.RAISE_PAYMENT_DISPUTE,
            human_review_required=False,
            reason="No settlement received for this earning. Payment dispute recommended."
        )

    # Case 3: Duplicate settlement
    if anomaly_type == "duplicate_settlement":
        if any_human_review or has_confirmed_violation:
            return ControllerDecision(
                transaction_id=transaction_id,
                partner_id=partner_id,
                resolution_status=ResolutionStatus.HUMAN_REVIEW,
                priority=Priority.HIGH,
                recommended_action=RecommendedAction.LEGAL_REVIEW,
                human_review_required=True,
                reason="Duplicate settlement detected. Legal review required for payment recording compliance."
            )
        return ControllerDecision(
            transaction_id=transaction_id,
            partner_id=partner_id,
            resolution_status=ResolutionStatus.ACTION_REQUIRED,
            priority=Priority.MEDIUM,
            recommended_action=RecommendedAction.REQUEST_EXPLANATION,
            human_review_required=False,
            reason="Duplicate settlement detected. Request explanation from platform."
        )

    # Case 4: Legal findings with CONFIRMED_VIOLATION
    legal_confirmed = [f for f in legal_findings if f.grade == FindingGrade.CONFIRMED_VIOLATION]
    if legal_confirmed:
        # If human review required on legal finding, escalate
        if any(f.human_review_required for f in legal_confirmed):
            return ControllerDecision(
                transaction_id=transaction_id,
                partner_id=partner_id,
                resolution_status=ResolutionStatus.HUMAN_REVIEW,
                priority=Priority.CRITICAL if has_critical else Priority.HIGH,
                recommended_action=RecommendedAction.LEGAL_REVIEW,
                human_review_required=True,
                reason=f"Confirmed legal violation: {legal_confirmed[0].legal_basis.statute} {legal_confirmed[0].legal_basis.section}. Human review required."
            )
        # Otherwise legal review action
        return ControllerDecision(
            transaction_id=transaction_id,
            partner_id=partner_id,
            resolution_status=ResolutionStatus.ACTION_REQUIRED,
            priority=Priority.HIGH,
            recommended_action=RecommendedAction.LEGAL_REVIEW,
            human_review_required=False,
            reason=f"Legal risk identified: {legal_confirmed[0].legal_basis.statute} {legal_confirmed[0].legal_basis.section}. Legal review recommended."
        )

    # Case 5: Contract findings with CONFIRMED_VIOLATION
    contract_confirmed = [f for f in contract_findings if f.grade == FindingGrade.CONFIRMED_VIOLATION]
    if contract_confirmed:
        if any(f.human_review_required for f in contract_confirmed):
            return ControllerDecision(
                transaction_id=transaction_id,
                partner_id=partner_id,
                resolution_status=ResolutionStatus.HUMAN_REVIEW,
                priority=Priority.HIGH,
                recommended_action=RecommendedAction.REVIEW_CONTRACT,
                human_review_required=True,
                reason=f"Contract violation confirmed: {contract_confirmed[0].contract_clause}. Human review required."
            )
        return ControllerDecision(
            transaction_id=transaction_id,
            partner_id=partner_id,
            resolution_status=ResolutionStatus.ACTION_REQUIRED,
            priority=Priority.HIGH if has_high else Priority.MEDIUM,
            recommended_action=RecommendedAction.REVIEW_CONTRACT,
            human_review_required=False,
            reason=f"Contract violation: {contract_confirmed[0].contract_clause}. Contract review recommended."
        )

    # Case 6: Economic concerns with high/critical severity
    econ_severe = [f for f in economic_findings if f.severity in ["critical", "high"]]
    if econ_severe:
        if any(f.human_review_required for f in econ_severe):
            return ControllerDecision(
                transaction_id=transaction_id,
                partner_id=partner_id,
                resolution_status=ResolutionStatus.HUMAN_REVIEW,
                priority=Priority.CRITICAL if has_critical else Priority.HIGH,
                recommended_action=RecommendedAction.REQUEST_EXPLANATION,
                human_review_required=True,
                reason=f"Economic concern: business hourly rate {econ_severe[0].business_hourly_rate:.2f} below benchmarks. Human review required."
            )
        return ControllerDecision(
            transaction_id=transaction_id,
            partner_id=partner_id,
            resolution_status=ResolutionStatus.ACTION_REQUIRED,
            priority=Priority.HIGH if has_critical else Priority.MEDIUM,
            recommended_action=RecommendedAction.REQUEST_EXPLANATION,
            human_review_required=False,
            reason=f"Economic concern: business hourly rate {econ_severe[0].business_hourly_rate:.2f} significantly below benchmarks."
        )

    # Case 7: Payment shortfall (partial_payment or missing_incentive) with evidence
    if anomaly_type in ["partial_payment", "missing_incentive"] and variance < -AMOUNT_TOLERANCE:
        if has_confirmed_violation or has_potential_issue:
            # Evidence supports the shortfall
            return ControllerDecision(
                transaction_id=transaction_id,
                partner_id=partner_id,
                resolution_status=ResolutionStatus.ACTION_REQUIRED,
                priority=Priority.HIGH if abs(variance) > Decimal("50") else Priority.MEDIUM,
                recommended_action=RecommendedAction.RAISE_PAYMENT_DISPUTE,
                human_review_required=any_human_review,
                reason=f"Settlement shortfall of {abs(variance):.2f} ({layer1_facts.get('variance_pct', 0):.1f}%) supported by findings. Payment dispute recommended."
            )
        elif max_evidence >= Decimal("0.5"):
            return ControllerDecision(
                transaction_id=transaction_id,
                partner_id=partner_id,
                resolution_status=ResolutionStatus.ACTION_REQUIRED,
                priority=Priority.MEDIUM,
                recommended_action=RecommendedAction.REQUEST_EXPLANATION,
                human_review_required=False,
                reason=f"Settlement shortfall of {abs(variance):.2f} with moderate evidence. Request explanation from platform."
            )
        else:
            return ControllerDecision(
                transaction_id=transaction_id,
                partner_id=partner_id,
                resolution_status=ResolutionStatus.INSUFFICIENT_EVIDENCE,
                priority=Priority.LOW,
                recommended_action=RecommendedAction.REQUEST_EXPLANATION,
                human_review_required=False,
                reason=f"Settlement shortfall of {abs(variance):.2f} but insufficient evidence for action. Request explanation."
            )

    # Case 8: Overpayment
    if anomaly_type == "overpayment":
        return ControllerDecision(
            transaction_id=transaction_id,
            partner_id=partner_id,
            resolution_status=ResolutionStatus.RESOLVED_AUTOMATICALLY,
            priority=Priority.LOW,
            recommended_action=RecommendedAction.NO_ACTION,
            human_review_required=False,
            reason=f"Overpayment of {variance:.2f} detected. No action required (platform overpaid)."
        )

    # Case 9: Findings exist but no clear action path
    if all_findings:
        if any_human_review:
            return ControllerDecision(
                transaction_id=transaction_id,
                partner_id=partner_id,
                resolution_status=ResolutionStatus.HUMAN_REVIEW,
                priority=Priority.MEDIUM,
                recommended_action=RecommendedAction.REQUEST_EXPLANATION,
                human_review_required=True,
                reason="Findings require human review."
            )
        if max_evidence >= Decimal("0.5"):
            return ControllerDecision(
                transaction_id=transaction_id,
                partner_id=partner_id,
                resolution_status=ResolutionStatus.ACTION_REQUIRED,
                priority=Priority.MEDIUM,
                recommended_action=RecommendedAction.REQUEST_EXPLANATION,
                human_review_required=False,
                reason="Findings detected with moderate evidence. Request explanation."
            )

    # Case 10: Insufficient evidence / unclear
    return ControllerDecision(
        transaction_id=transaction_id,
        partner_id=partner_id,
        resolution_status=ResolutionStatus.INSUFFICIENT_EVIDENCE,
        priority=Priority.LOW,
        recommended_action=RecommendedAction.REQUEST_EXPLANATION,
        human_review_required=False,
        reason="Insufficient evidence for definitive action. Request explanation."
    )


def _create_auto_resolved_decisions(
    partner_id: str,
    transactions: List,
    payment_calcs: List[PaymentCalculation],
    reconciliation_predictions: List[Dict[str, Any]] = None
) -> List[ControllerDecision]:
    """Create RESOLVED_AUTOMATICALLY decisions when no findings exist."""
    predictions_by_tx = {p.get("transaction_id"): p for p in (reconciliation_predictions or [])}
    decisions = []
    for i, tx in enumerate(transactions):
        calc = payment_calcs[i] if i < len(payment_calcs) else None
        prediction = predictions_by_tx.get(tx.transaction_id)
        layer1_facts = _extract_layer1_facts(calc, tx, prediction)

        decisions.append(ControllerDecision(
            transaction_id=tx.transaction_id,
            partner_id=partner_id,
            resolution_status=ResolutionStatus.RESOLVED_AUTOMATICALLY,
            priority=Priority.LOW,
            recommended_action=RecommendedAction.NO_ACTION,
            human_review_required=False,
            reason="No findings detected. Transaction reconciled.",
            layer1_facts=layer1_facts,
            layer2_findings=[],
            evidence_references=[str(i)] if calc else []
        ))
    return decisions


def _build_batch_summary(decisions: List[ControllerDecision]) -> BatchSummary:
    """Build aggregate operational summary from decisions."""
    if not decisions:
        return BatchSummary()

    total = len(decisions)
    status_counts = {}
    priority_counts = {}
    action_counts = {}

    for d in decisions:
        status_counts[d.resolution_status.value] = status_counts.get(d.resolution_status.value, 0) + 1
        priority_counts[d.priority.value] = priority_counts.get(d.priority.value, 0) + 1
        action_counts[d.recommended_action.value] = action_counts.get(d.recommended_action.value, 0) + 1

    auto_resolved = status_counts.get(ResolutionStatus.RESOLVED_AUTOMATICALLY.value, 0)
    action_req = status_counts.get(ResolutionStatus.ACTION_REQUIRED.value, 0)
    human_rev = status_counts.get(ResolutionStatus.HUMAN_REVIEW.value, 0)
    insuff = status_counts.get(ResolutionStatus.INSUFFICIENT_EVIDENCE.value, 0)

    exception_rate = (action_req + human_rev + insuff) / total if total > 0 else 0.0
    resolution_rate = auto_resolved / total if total > 0 else 0.0

    return BatchSummary(
        records_processed=total,
        automatically_resolved=auto_resolved,
        action_required=action_req,
        human_review=human_rev,
        insufficient_evidence=insuff,
        exception_rate=round(exception_rate, 4),
        resolution_rate=round(resolution_rate, 4),
        by_priority=priority_counts,
        by_action=action_counts
    )