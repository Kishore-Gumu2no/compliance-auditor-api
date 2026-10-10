from __future__ import annotations
from typing import List, Optional, Dict, Any
from decimal import Decimal
from datetime import date

from models import (
    FindingGrade, ContractComplianceFinding, LegalRiskFinding,
    EconomicConcernFinding, AuditFindings, PaymentCalculation,
    BusinessCostCalculation, TransactionRecord, LegalBasis,
    ContractVersion, PartnerProfile
)


# ============================================================
# Contract Compliance Analysis (Deterministic)
# ============================================================

AMOUNT_TOLERANCE = Decimal("0.50")


def _documented_expected_net(tx: TransactionRecord) -> Decimal:
    """Return the independently documented net amount for a transaction.

    Gross earnings less the deductions listed on the earning record are the
    amount the settlement must be compared with.  This deliberately does not
    infer a deduction from a payment shortfall.
    """
    return tx.gross_amount - sum(d.amount for d in tx.deductions)


def _explicitly_omitted_contract_component(
    tx: TransactionRecord,
    calc: PaymentCalculation
) -> tuple[Optional[str], Decimal]:
    """Return a contract component only when the shortfall proves its omission.

    An aggregate settlement shortfall cannot identify a contract breach by
    itself.  It must equal a positive component whose governing clause was
    explicitly parsed from the applicable contract.
    """
    shortfall = -calc.variance
    breakdown = calc.breakdown
    candidates = (
        ("contract_incentives", "contract_has_incentive_clause", "Incentive"),
        ("contract_distance", "contract_has_distance_clause", "Distance pay"),
        ("contract_wait", "contract_has_wait_clause", "Wait time compensation"),
        ("contract_base", "contract_has_base_clause", "Base pay"),
    )
    for amount_key, clause_key, label in candidates:
        component = breakdown.get(amount_key, Decimal("0"))
        has_clause = breakdown.get(clause_key, Decimal("0")) == Decimal("1")
        if has_clause and component > AMOUNT_TOLERANCE and abs(shortfall - component) <= AMOUNT_TOLERANCE:
            return f"{label} clause for {tx.platform} {tx.contract_version}", component
    return None, Decimal("0")

def analyze_contract_compliance(
    payment_calculations: List[PaymentCalculation],
    transactions: List[TransactionRecord]
) -> List[ContractComplianceFinding]:
    """
    Deterministic contract compliance analysis.
    Compare platform_net vs contractual_net using actual payment calculations.
    Only generate findings where the contractual amount is corroborated by the
    recorded earning and the platform actually paid less than that obligation.
    Skip missing payments (platform_net == 0) - those are a different category.
    """
    findings = []

    for i, (calc, tx) in enumerate(zip(payment_calculations, transactions)):
        variance = calc.variance
        variance_pct = calc.variance_pct

        # Skip missing payments (platform paid nothing)
        if calc.platform_net == 0:
            continue

        # An aggregate shortfall, even when it agrees with an earning record,
        # does not establish which contract term was violated.  Require the
        # shortfall to prove that one explicitly contracted component was
        # omitted in full.
        documented_net = _documented_expected_net(tx)
        contract_is_corroborated = (
            abs(calc.contractual_net - documented_net) <= AMOUNT_TOLERANCE
        )
        if variance >= -AMOUNT_TOLERANCE or not contract_is_corroborated:
            continue

        clause_breached, clause_amount = _explicitly_omitted_contract_component(tx, calc)
        if clause_breached is None:
            continue

        shortfall = abs(variance)
        evidence_strength = min(
            Decimal("1.0"),
            shortfall / clause_amount if clause_amount > 0 else Decimal("0")
        )
        severity = "critical" if abs(variance_pct) >= Decimal("80") else "high"

        findings.append(ContractComplianceFinding(
            finding_id=f"contract_{tx.transaction_id}",
            grade=FindingGrade.CONFIRMED_VIOLATION,
            transaction_id=tx.transaction_id,
            contract_clause=clause_breached,
            expected_amount=calc.contractual_net,
            actual_amount=calc.platform_net,
            variance=variance,
            evidence_strength=evidence_strength,
            notes=(
                f"Recorded earnings corroborate the contractual {clause_breached} "
                f"amount of {clause_amount:.2f}; the settlement is lower by that "
                f"same amount ({shortfall:.2f})."
            ),
            severity=severity,
            human_review_required=True
        ))

    return findings


# ============================================================
# Legal Risk Analysis (Evidence-Based, Conservative)
# ============================================================

class _TransactionEvidence:
    """Extracted evidence signals from a transaction and its payment calculation."""
    def __init__(self, tx: TransactionRecord, calc: Optional[PaymentCalculation] = None, anomaly_type: Optional[str] = None):
        self.tx_id = tx.transaction_id
        self.platform = tx.platform
        self.date = tx.transaction_date
        self.anomaly_type = anomaly_type

        # Financial signals from earnings data
        self.has_incentives = bool(tx.incentives)
        self.incentive_amount = sum(inc.amount for inc in tx.incentives) if tx.incentives else Decimal("0")
        self.has_deductions = bool(tx.deductions)
        self.deduction_amount = sum(ded.amount for ded in tx.deductions) if tx.deductions else Decimal("0")
        self.deduction_categories = [ded.category for ded in tx.deductions] if tx.deductions else []
        self.has_penalty_deductions = any(d.category == "penalty" for d in tx.deductions)

        # Operational signals
        self.has_wait_time = tx.wait_time_minutes is not None and tx.wait_time_minutes > 0
        self.wait_time_minutes = tx.wait_time_minutes or 0
        self.has_distance = tx.distance_km is not None and tx.distance_km > 0
        self.distance_km = tx.distance_km or Decimal("0")
        self.gross_amount = tx.gross_amount
        self.net_amount = tx.net_amount

        # Contract/payment signals from calculation
        if calc:
            self.contractual_net = calc.contractual_net
            self.platform_net = calc.platform_net
            self.variance = calc.variance
            self.variance_pct = calc.variance_pct
            self.underpaid = calc.variance < -AMOUNT_TOLERANCE
            self.overpaid = calc.variance > AMOUNT_TOLERANCE
            # Actual financial evidence: earning.expected_amount (tx.net_amount) vs settlement.amount_paid (calc.platform_net)
            self.actual_variance = tx.net_amount - calc.platform_net
            self.actually_underpaid = self.actual_variance < -AMOUNT_TOLERANCE
        else:
            self.contractual_net = tx.net_amount
            self.platform_net = tx.net_amount
            self.variance = Decimal("0")
            self.variance_pct = Decimal("0")
            self.underpaid = False
            self.overpaid = False
            self.actual_variance = Decimal("0")
            self.actually_underpaid = False


class _RelevanceResult:
    """Result of evaluating law relevance to transaction evidence."""
    def __init__(self, triggered: bool, evidence_score: Decimal, matched_concepts: List[str], notes: str):
        self.triggered = triggered
        self.evidence_score = evidence_score
        self.matched_concepts = matched_concepts
        self.notes = notes


# Legal concepts and the transaction evidence each one needs.
#
# Which provision covers which concept, and the words in its text that show
# it, come from the law's own document in Qdrant (LegalBasis.concepts, see
# law_store.py) - not from this file. A provision is matched to a concept only
# when its document lists that concept, its text contains one of the listed
# words, AND the transaction carries the evidence below. CONSERVATIVE: the
# evidence checks are deliberately narrow.
CONCEPT_EVIDENCE = {
    # Code on Wages s.5 in the seed. The gate sits in the economic concern
    # layer: a legal finding only if that layer flags the minimum wage.
    "minimum_wage": lambda ev: False,
    "overtime": lambda ev: (ev.has_distance and ev.distance_km > Decimal("20")) or (ev.has_wait_time and ev.wait_time_minutes > 60),
    # Only for an incorrect_deduction anomaly (unsupported deduction > tolerance).
    "deduction_notice": lambda ev: ev.anomaly_type == "incorrect_deduction",
    # Disabled - too generic.
    "payout_cycle": lambda ev: False,
    # Deductions from wages only when authorised: incorrect_deduction anomaly only.
    "wage_protection": lambda ev: ev.anomaly_type == "incorrect_deduction",
    # A missing incentive does not, on its own, establish that a payment was
    # deducted or that the statutory invoice/notice was absent. The current
    # transaction model has no deduction-notice evidence field.
    "incentive_bonus": lambda ev: False,
    # A duplicate payment is not evidence that a payment record was omitted,
    # inaccurate, or not reported. The current transaction model has no
    # record/reporting-failure evidence field.
    "payment_recording": lambda ev: False,
}


def analyze_legal_risks(
    transactions: List[TransactionRecord],
    applicable_laws_per_tx: List[Dict[str, Any]],
    classification_probability: Decimal,
    payment_calculations: Optional[List[PaymentCalculation]] = None
) -> List[LegalRiskFinding]:
    """
    Evidence-based legal risk analysis.

    For each transaction/law pair, evaluates whether the transaction facts
    trigger the retrieved legal provision. Conservative: only creates findings
    when evidence supports a violation.
    """
    findings = []

    if not applicable_laws_per_tx:
        return findings

    # Build transaction lookup
    tx_by_id = {tx.transaction_id: tx for tx in transactions}
    calc_by_id = {}
    if payment_calculations:
        for i, (tx, calc) in enumerate(zip(transactions, payment_calculations)):
            calc_by_id[tx.transaction_id] = calc

    finding_counter = 0

    for entry in applicable_laws_per_tx:
        tx_id = entry.get("transaction_id")
        laws = entry.get("laws", [])
        tx = tx_by_id.get(tx_id)
        calc = calc_by_id.get(tx_id)

        if not tx or not laws:
            continue

        # Extract transaction evidence signals
        tx_evidence = _extract_transaction_evidence(tx, calc, transactions)

        # Track which law sections we've already created findings for (per transaction)
        # to avoid duplicates for same statute+section
        seen_sections = set()

        for law in laws:
            # Skip duplicate statute+section for same transaction
            law_key = (law.statute, law.section)
            if law_key in seen_sections:
                continue
            seen_sections.add(law_key)

            # Evaluate relevance: does transaction evidence trigger this law?
            relevance = _evaluate_law_relevance(tx_evidence, law, classification_probability)

            # Only create a finding if the law is TRIGGERED (relevant + evidence supports)
            if not relevance.triggered:
                continue  # Irrelevant law - no finding

            # Determine grade based on evidence (conservative)
            grade = _grade_from_evidence(relevance, law, classification_probability)

            # Skip INSUFFICIENT_EVIDENCE - only create actionable findings
            if grade == FindingGrade.INSUFFICIENT_EVIDENCE:
                continue

            # Build risk narratives using full law text
            risk_if_employee = _build_risk_narrative(law, "employee", relevance)
            risk_if_contractor = _build_risk_narrative(law, "contractor", relevance)

            # Determine severity
            severity = _determine_severity(relevance, grade, law)

            findings.append(LegalRiskFinding(
                finding_id=f"legal_{tx_id}_{finding_counter}",
                grade=grade,
                transaction_id=tx_id,
                legal_basis=law,
                classification_probability=classification_probability,
                applicable_law_version=law.section,
                risk_if_employee=risk_if_employee,
                risk_if_contractor=risk_if_contractor,
                evidence_strength=relevance.evidence_score,
                notes=relevance.notes,
                severity=severity,
                human_review_required=grade in [FindingGrade.CONFIRMED_VIOLATION, FindingGrade.POTENTIAL_ISSUE]
            ))
            finding_counter += 1

    return findings


def _extract_transaction_evidence(
    tx: TransactionRecord,
    calc: Optional[PaymentCalculation] = None,
    all_transactions: Optional[List[TransactionRecord]] = None
) -> _TransactionEvidence:
    """Extract relevant evidence signals from transaction and payment calculation."""
    anomaly_type = None
    documented_net = _documented_expected_net(tx)
    documented_shortfall = documented_net - tx.net_amount

    # Use the recorded earning and settlement for payment evidence.  A
    # contract-calculation variance can include model assumptions and must not
    # decide whether a documented incentive or payment was actually omitted.
    if documented_net > AMOUNT_TOLERANCE and tx.net_amount == 0:
        anomaly_type = "missing_payment"
    elif documented_shortfall > AMOUNT_TOLERANCE:
        if tx.incentives:
            incentive_amt = sum(inc.amount for inc in tx.incentives)
            if incentive_amt > 0 and abs(documented_shortfall - incentive_amt) <= AMOUNT_TOLERANCE:
                anomaly_type = "missing_incentive"
        anomaly_type = anomaly_type or "partial_payment"
    elif calc:
        if calc.variance > AMOUNT_TOLERANCE:
            anomaly_type = "overpayment"
        else:
            anomaly_type = "perfect_match"

    # Check for duplicate settlement: same platform, same date, same net_amount
    # (indicates multiple settlements for same order)
    # Only the canonical transaction (lowest transaction_id) gets duplicate_settlement anomaly
    # to ensure exactly ONE payment_recording legal finding per duplicate event.
    if all_transactions:
        duplicate_group = [
            t for t in all_transactions
            if t is not tx
            and t.platform == tx.platform
            and t.transaction_date == tx.transaction_date
            and t.net_amount == tx.net_amount
            and t.net_amount > 0
        ]
        if duplicate_group:
            # Canonical = lowest transaction_id in the duplicate set (including self)
            canonical_id = min([tx.transaction_id] + [t.transaction_id for t in duplicate_group])
            if tx.transaction_id == canonical_id:
                anomaly_type = "duplicate_settlement"
            else:
                # Non-canonical duplicate: suppress duplicate anomaly, keep original
                pass  # anomaly_type remains as determined from variance

    return _TransactionEvidence(tx, calc, anomaly_type)


def _evaluate_law_relevance(
    evidence: _TransactionEvidence,
    law: LegalBasis,
    classification_prob: Decimal
) -> _RelevanceResult:
    """
    Evaluate whether transaction evidence triggers the legal provision.
    Requires BOTH:
      1. Substantive relevance: provision text matches the specific legal obligation
      2. Concrete evidence: transaction facts support that specific obligation
    """
    matched_concepts = []
    text = law.full_text.lower() if law.full_text else ""
    applicability = law.applicability_category.lower()
    statute = law.statute.lower()
    section = law.section.lower()

    # Check applicability category - must match worker type
    is_gig_applicable = "gig_worker" in applicability or "platform_worker" in applicability
    is_employee_applicable = "employee" in applicability

    # Employee-only laws need classification probability support
    # For P(employee) > 0.4, include both frameworks (genuine ambiguity)
    if is_employee_applicable and not is_gig_applicable and classification_prob < Decimal("0.4"):
        # Employee-only law but low probability of employee classification - reject
        notes = (
            f"Transaction {evidence.tx_id}: {evidence.anomaly_type or 'no anomaly'} | "
            f"Employee-only law {law.section} rejected: P(employee)={classification_prob:.0%} < 0.4"
        )
        return _RelevanceResult(False, Decimal("0.0"), [], notes)

    # Match the concepts the law's own document lists (from Qdrant) to
    # transaction signals, in the fixed order of CONCEPT_EVIDENCE.
    for concept, requires_fn in CONCEPT_EVIDENCE.items():
        keywords = [kw.lower() for entry in (law.concepts or []) if entry.get("concept") == concept
                    for kw in entry.get("keywords") or []]
        if not keywords:
            continue  # this provision does not cover this concept

        # Check if provision text actually contains the concept keywords
        if not any(kw in text for kw in keywords):
            continue

        # Check if transaction has corresponding evidence for THIS specific concept
        if requires_fn(evidence):
            matched_concepts.append(concept)

    # A law is "triggered" if at least one concept matches AND its evidence requirement is met
    triggered = len(matched_concepts) > 0

    # Compute evidence score - ONLY if triggered
    if triggered:
        # Source weight (law hierarchy)
        source_weight_map = {
            "Constitution": Decimal("1.0"),
            "Central Act": Decimal("0.8"),
            "State Act": Decimal("0.7"),
            "State Rules": Decimal("0.6")
        }
        source_weight = source_weight_map.get(law.source_type, Decimal("0.5"))

        # Score = concept match strength * source weight
        # Each matched concept contributes 0.45 base
        concept_boost = min(Decimal(str(len(matched_concepts))) * Decimal("0.45"), Decimal("0.9"))

        # Classification probability provides minor boost (max 0.15)
        class_boost = min(classification_prob * Decimal("0.15"), Decimal("0.15"))

        evidence_score = min(Decimal("1.0"), (concept_boost + class_boost) * source_weight)
    else:
        # Not triggered = no evidence score
        evidence_score = Decimal("0.0")

    # Build notes
    if triggered:
        notes = (
            f"Transaction {evidence.tx_id}: {evidence.anomaly_type or 'no anomaly'} | "
            f"Variance={evidence.variance:.2f} ({evidence.variance_pct:.1f}%) | "
            f"Incentives={evidence.incentive_amount:.2f} | Deductions={evidence.deduction_amount:.2f} | "
            f"Matched concepts: {matched_concepts} | "
            f"Law={law.section} ({law.applicability_category}) | "
            f"Source={law.source_type} | P(employee)={classification_prob:.0%}"
        )
    else:
        notes = (
            f"Transaction {evidence.tx_id}: {evidence.anomaly_type or 'no anomaly'} | "
            f"No matching concepts for {law.section} ({law.applicability_category}) | "
            f"Source={law.source_type} | P(employee)={classification_prob:.0%}"
        )

    return _RelevanceResult(triggered, evidence_score, matched_concepts, notes)


def _grade_from_evidence(
    relevance: _RelevanceResult,
    law: LegalBasis,
    classification_prob: Decimal
) -> FindingGrade:
    """Grade finding conservatively based on evidence."""
    if not relevance.triggered:
        return FindingGrade.INSUFFICIENT_EVIDENCE

    score = relevance.evidence_score

    # Thresholds calibrated for genuine legal findings:
    # For State Act (0.7) with 1 concept: (0.45 + 0.075) * 0.7 = 0.3675
    # For Central Act (0.8) with 1 concept: (0.45 + 0.075) * 0.8 = 0.42
    # With 2 concepts: (0.9 + 0.075) * 0.7 = 0.6825
    # We need thresholds that catch genuine violations but reject noise
    if score >= Decimal("0.60"):
        return FindingGrade.CONFIRMED_VIOLATION
    elif score >= Decimal("0.35"):
        return FindingGrade.POTENTIAL_ISSUE
    else:
        return FindingGrade.INSUFFICIENT_EVIDENCE


def _determine_severity(relevance: _RelevanceResult, grade: FindingGrade, law: LegalBasis) -> str:
    """Determine severity based on evidence and law."""
    if grade == FindingGrade.INSUFFICIENT_EVIDENCE:
        return "low"

    score = relevance.evidence_score
    source_weight = {"Constitution": 1.0, "Central Act": 0.8, "State Act": 0.7, "State Rules": 0.6}.get(law.source_type, 0.5)

    if score >= Decimal("0.75") and source_weight >= 0.8:
        return "critical"
    elif score >= Decimal("0.65") or source_weight >= 0.8:
        return "high"
    elif score >= Decimal("0.45"):
        return "medium"
    else:
        return "low"


def _build_risk_narrative(law: LegalBasis, perspective: str, relevance: _RelevanceResult) -> str:
    """Build risk narrative using full law text and matched concepts."""
    provision = law.full_text[:400] + "..." if law.full_text and len(law.full_text) > 400 else (law.full_text or "Provision text not available")

    if perspective == "employee":
        if relevance.triggered:
            return (f"As employee: {law.statute} ({law.section}) applies. "
                   f"Relevant provision: {provision} "
                   f"Matched transaction signals: {', '.join(relevance.matched_concepts)}")
        else:
            return (f"As employee: {law.statute} ({law.section}) may apply but no specific "
                   f"transaction trigger identified. Provision: {provision}")
    else:
        if relevance.triggered:
            return (f"As contractor: {law.statute} ({law.section}) may not directly apply, "
                   f"but contractual equivalent obligations exist. "
                   f"Provision context: {provision} "
                   f"Transaction signals: {', '.join(relevance.matched_concepts)}")
        else:
            return (f"As contractor: {law.statute} ({law.section}) likely does not apply; "
                   f"only contract terms govern. Provision context: {provision}")


# ============================================================
# Economic Concern Analysis
# ============================================================

def _minimum_wage_from_laws(laws: List[Any]) -> Optional[Decimal]:
    """The hourly minimum wage set by a provision in force for the trip, if any (from Qdrant)."""
    for law in laws or []:
        if not any(entry.get("concept") == "minimum_wage" for entry in (law.concepts or [])):
            continue
        value = (law.parameters or {}).get("minimum_wage_hourly")
        if value is not None:
            return Decimal(str(value))
    return None


def analyze_economic_concerns(
    business_cost_calculations: List[BusinessCostCalculation],
    transactions: List[TransactionRecord],
    payment_calculations: Optional[List[PaymentCalculation]] = None,
    applicable_laws_per_tx: Optional[List[Dict[str, Any]]] = None,
) -> List[EconomicConcernFinding]:
    """
    Evidence-based economic concern analysis.
    Only generate findings when there's actual evidence of an economic issue:
    - Missing payment (no settlement at all)
    - Business rate below minimum wage with actual financial impact (payment shortfall)
    - INCORRECT_DEDUCTION anomaly (unsupported deduction reducing effective rate below min wage)

    Do NOT flag merely because rate is below generic benchmarks.
    Do NOT flag underpayment without minimum wage violation.
    """
    findings = []

    # Benchmarks (reference only - not triggers by themselves). These two are
    # FairPay's economic reference figures, not law. The minimum wage is law:
    # it comes from the provision in force for each trip (Qdrant).
    contracted_benchmark = Decimal("600")
    market_benchmark = Decimal("800")
    laws_by_tx = {entry.get("transaction_id"): entry.get("laws", []) for entry in (applicable_laws_per_tx or [])}

    for i, (calc, tx) in enumerate(zip(business_cost_calculations, transactions)):
        business_rate = calc.business_hourly_rate
        min_wage_benchmark = _minimum_wage_from_laws(laws_by_tx.get(tx.transaction_id, []))

        # A duration is essential to an hourly calculation.  Do not convert
        # absent duration data (or a zero/negative result) into economic
        # evidence; the one-hour calculation fallback is only a data model
        # fallback, not evidence of a low hourly rate.
        has_valid_rate_inputs = (
            tx.duration_minutes is not None
            and tx.duration_minutes > 0
            and tx.distance_km is not None
            and tx.distance_km >= 0
            and business_rate > 0
        )
        if not has_valid_rate_inputs:
            continue

        gap_contracted = max(Decimal("0"), (contracted_benchmark - business_rate) / contracted_benchmark * Decimal("100"))
        gap_market = max(Decimal("0"), (market_benchmark - business_rate) / market_benchmark * Decimal("100"))
        gap_min_wage = (max(Decimal("0"), (min_wage_benchmark - business_rate) / min_wage_benchmark * Decimal("100"))
                        if min_wage_benchmark else None)

        # Compare the actual settlement with the independently recorded
        # earning.  A contract estimate or a benchmark comparison is not
        # transaction evidence of economic harm.
        documented_net = _documented_expected_net(tx)
        actual_shortfall = documented_net - tx.net_amount
        missing_entire_payment = documented_net > AMOUNT_TOLERANCE and tx.net_amount == 0
        has_documented_shortfall = actual_shortfall > AMOUNT_TOLERANCE

        # ECONOMIC FINDING REQUIRES one of:
        # 1. Missing payment entirely (no settlement)
        # 2. A below-minimum-wage rate and a documented payment shortfall

        # A low rate becomes a finding only when the same transaction has a
        # documented settlement shortfall.  Benchmarks remain contextual.
        # No provision in force sets a minimum wage for this trip: no comparison.
        min_wage_violation = (
            min_wage_benchmark is not None and business_rate < min_wage_benchmark and has_documented_shortfall
        )

        # Only generate if there's genuine evidence of economic harm
        if not (missing_entire_payment or min_wage_violation):
            continue

        # Determine severity from actual evidence
        if missing_entire_payment:
            severity = "critical"
        elif min_wage_violation:
            severity = "critical"
        else:
            severity = "medium"

        grade_map = {
            "critical": FindingGrade.CONFIRMED_VIOLATION,
            "high": FindingGrade.POTENTIAL_ISSUE,
            "medium": FindingGrade.ECONOMIC_CONCERN,
            "low": FindingGrade.INSUFFICIENT_EVIDENCE,
        }

        # Build specific, evidence-based notes
        if missing_entire_payment:
            notes = f"No settlement received for expected earning of {documented_net:.2f}. Complete payment failure."
        elif min_wage_violation:
            notes = f"Business hourly rate {business_rate:.2f} is {gap_min_wage:.1f}% below minimum wage ({min_wage_benchmark:.0f}/hr) with documented settlement shortfall of {actual_shortfall:.2f}."

        findings.append(EconomicConcernFinding(
            finding_id=f"economic_{tx.transaction_id}",
            grade=grade_map[severity],
            transaction_id=tx.transaction_id,
            business_hourly_rate=business_rate,
            contracted_hourly_benchmark=contracted_benchmark,
            market_hourly_benchmark=market_benchmark,
            min_wage_hourly_benchmark=min_wage_benchmark,
            gap_vs_contracted_pct=gap_contracted,
            gap_vs_market_pct=gap_market,
            gap_vs_min_wage_pct=gap_min_wage,
            severity=severity,
            notes=notes,
            human_review_required=severity in ["high", "critical"]
        ))

    return findings


# ============================================================
# Aggregation with Duplicate Suppression
# ============================================================

def aggregate_findings(
    compliance: List[ContractComplianceFinding],
    legal: List[LegalRiskFinding],
    economic: List[EconomicConcernFinding],
    evidence_gaps: List[str]
) -> AuditFindings:
    """Aggregate all findings into AuditFindings container with duplicate suppression."""

    # Suppress duplicates
    # Legal: unique by transaction_id + statute + section
    seen_legal = set()
    legal_unique = []
    for f in legal:
        key = (f.transaction_id, f.legal_basis.statute, f.legal_basis.section)
        if key not in seen_legal:
            seen_legal.add(key)
            legal_unique.append(f)

    # Contract: unique by transaction_id + contract_clause
    seen_contract = set()
    compliance_unique = []
    for f in compliance:
        key = (f.transaction_id, f.contract_clause)
        if key not in seen_contract:
            seen_contract.add(key)
            compliance_unique.append(f)

    # Economic: unique by transaction_id
    seen_economic = set()
    economic_unique = []
    for f in economic:
        key = f.transaction_id
        if key not in seen_economic:
            seen_economic.add(key)
            economic_unique.append(f)

    all_findings = compliance_unique + legal_unique + economic_unique

    # Determine overall grade (highest severity)
    grade_priority = {
        FindingGrade.CONFIRMED_VIOLATION: 4,
        FindingGrade.POTENTIAL_ISSUE: 3,
        FindingGrade.ECONOMIC_CONCERN: 2,
        FindingGrade.INSUFFICIENT_EVIDENCE: 1,
    }

    overall_grade = FindingGrade.INSUFFICIENT_EVIDENCE
    max_priority = 0
    for finding in all_findings:
        priority = grade_priority.get(finding.grade, 0)
        if priority > max_priority:
            max_priority = priority
            overall_grade = finding.grade

    # Build summary
    summary_parts = []
    if compliance_unique:
        summary_parts.append(f"{len(compliance_unique)} contract compliance issue(s)")
    if legal_unique:
        confirmed = sum(1 for f in legal_unique if f.grade == FindingGrade.CONFIRMED_VIOLATION)
        potential = sum(1 for f in legal_unique if f.grade == FindingGrade.POTENTIAL_ISSUE)
        insufficient = sum(1 for f in legal_unique if f.grade == FindingGrade.INSUFFICIENT_EVIDENCE)
        if confirmed or potential:
            summary_parts.append(f"{confirmed} confirmed legal violation(s), {potential} potential issue(s)")
        if insufficient:
            summary_parts.append(f"{insufficient} legal risk(s) with insufficient evidence")
    if economic_unique:
        critical_high = sum(1 for f in economic_unique if f.severity in ["critical", "high"])
        summary_parts.append(f"{critical_high} critical/high economic concern(s), {len(economic_unique) - critical_high} other")
    if evidence_gaps:
        summary_parts.append(f"{len(evidence_gaps)} evidence gap(s)")

    summary = "; ".join(summary_parts) if summary_parts else "No findings"

    return AuditFindings(
        partner_id="",
        audit_date=date.today(),
        contract_compliance_findings=compliance_unique,
        legal_risk_findings=legal_unique,
        economic_concern_findings=economic_unique,
        evidence_gaps=evidence_gaps,
        overall_grade=overall_grade,
        summary=summary
    )
