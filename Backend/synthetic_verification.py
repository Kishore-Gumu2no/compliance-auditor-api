"""Synthetic Data Verification Module for AI Finance Controller.

Runs the REAL audit engine on synthetic data, then evaluates predictions against ground truth.

Architecture:
  Earnings + Settlements + Partner/Contract Info
                    ↓
              AUDIT ENGINE (orchestrator)
                    ↓
              PREDICTION (audit_findings + payment_calculations)
                    ↕
              GROUND TRUTH (evaluation only)
                    ↓
  Layer 1: Transaction Reconciliation (anomaly_type) ← PRIMARY
  Layer 2: Contract/Legal/Economic Findings          ← SECONDARY
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from decimal import Decimal
from datetime import date, datetime
from dataclasses import dataclass, field
from reconciliation import AnomalyType, classify_single as _classify_single, AMOUNT_TOLERANCE, DATE_TOLERANCE_DAYS

from models import (
    PartnerProfile, TransactionRecord, ContractVersion, Deduction, Incentive,
    PaymentCalculation, BusinessCostCalculation, AuditFindings,
    ContractComplianceFinding, LegalRiskFinding, EconomicConcernFinding,
    FindingGrade, LegalBasis, FairPayState,
    ResolutionStatus, Priority, RecommendedAction, ControllerDecision, BatchSummary
)
from orchestrator import run_audit, app as audit_graph
from versioning import resolve_applicable_laws, extract_classification_probability, resolve_contract_version


# ============================================================
# Data Loading
# ============================================================

@dataclass
class SyntheticDataset:
    partners: List[Dict[str, Any]]
    settlements: List[Dict[str, Any]]
    earnings: List[Dict[str, Any]]
    ground_truth: List[Dict[str, Any]]
    ground_truth_layer2: Dict[str, Any]
    partners_by_id: Dict[str, Dict] = field(default_factory=dict)
    settlements_by_partner: Dict[str, List[Dict]] = field(default_factory=dict)
    earnings_by_partner: Dict[str, List[Dict]] = field(default_factory=dict)
    ground_truth_by_partner: Dict[str, List[Dict]] = field(default_factory=dict)
    ground_truth_layer2_by_partner: Dict[str, Dict] = field(default_factory=dict)
    settlements_by_order: Dict[str, List[Dict]] = field(default_factory=dict)


def load_synthetic_data(data_dir: str = "synthetic_data") -> SyntheticDataset:
    base_path = Path(data_dir)
    with open(base_path / "partners.json") as f:
        partners = json.load(f)
    with open(base_path / "settlements.json") as f:
        settlements = json.load(f)
    with open(base_path / "earnings.json") as f:
        earnings = json.load(f)
    with open(base_path / "ground_truth.json") as f:
        ground_truth = json.load(f)

    # Load Layer 2 ground truth (separate file, evaluation only)
    ground_truth_layer2 = {"contract_compliance_findings": [], "legal_risk_findings": [], "economic_concern_findings": []}
    layer2_path = base_path / "ground_truth_layer2.json"
    if layer2_path.exists():
        with open(layer2_path) as f:
            ground_truth_layer2 = json.load(f)

    dataset = SyntheticDataset(partners, settlements, earnings, ground_truth, ground_truth_layer2)
    dataset.partners_by_id = {p["partner_id"]: p for p in partners}
    for s in settlements:
        pid = s.get("partner_id")
        if pid:
            dataset.settlements_by_partner.setdefault(pid, []).append(s)
        # Index by order_id for duplicate detection
        oid = s.get("order_id")
        if oid:
            dataset.settlements_by_order.setdefault(oid, []).append(s)
    for e in earnings:
        pid = e.get("partner_id")
        if pid:
            dataset.earnings_by_partner.setdefault(pid, []).append(e)
    for g in ground_truth:
        pid = g.get("partner_id")
        if pid:
            dataset.ground_truth_by_partner.setdefault(pid, []).append(g)
    # Index Layer 2 ground truth by partner
    for category in ["contract_compliance_findings", "legal_risk_findings", "economic_concern_findings"]:
        for finding in ground_truth_layer2.get(category, []):
            pid = finding.get("partner_id")
            if pid:
                dataset.ground_truth_layer2_by_partner.setdefault(pid, {})
                dataset.ground_truth_layer2_by_partner[pid].setdefault(category, []).append(finding)
    return dataset


# ============================================================
# Data Conversion: Synthetic JSON → Engine Input Models
# ============================================================

def parse_date(date_str: str) -> date:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unable to parse date: {date_str}")


def json_to_partner_profile(partner_json: Dict[str, Any]) -> PartnerProfile:
    # Map city to zone for jurisdiction resolution
    city = partner_json.get("city", "Bengaluru")
    return PartnerProfile(
        partner_id=partner_json["partner_id"],
        name=partner_json.get("name", f"Partner {partner_json['partner_id']}"),
        phone=partner_json.get("phone", "0000000000"),
        vehicle_type=partner_json.get("vehicle_type", "bike"),
        petrol_per_km=Decimal(str(partner_json.get("petrol_per_km", "8.5"))),
        zone=city,  # Use city for jurisdiction mapping
        platform_accounts=partner_json.get("platforms_enrolled", ["Zomato", "Swiggy"]),
        bank_account_last4=partner_json.get("bank_account_last4"),
        active_since=parse_date(partner_json.get("join_date", "2024-01-01"))
    )


def json_to_transaction_record(settlement: Dict[str, Any], earnings_map: Dict[str, Dict]) -> TransactionRecord:
    """Convert settlement + matching earnings into a TransactionRecord for the engine.

    CRITICAL: net_amount must be the ACTUAL settlement amount_paid (what platform paid),
    NOT earnings.expected_amount. The engine compares platform_net (from tx.net_amount)
    vs contractual_net (from contract). The earnings expected_amount informs the
    contractual_net calculation via incentives/deductions in the transaction.
    """
    tx_id = settlement["settlement_id"]
    platform = settlement["platform"]
    tx_date = parse_date(settlement["date"])
    settlement_amount = Decimal(str(settlement["amount_paid"]))

    # Find matching earnings record by order_id + platform + partner
    earn_key = (settlement["order_id"], platform)
    earnings = earnings_map.get(earn_key)

    if earnings:
        base_pay = Decimal(str(earnings.get("base_pay", 0)))
        incentive_amt = Decimal(str(earnings.get("incentive", 0)))
        bonus_amt = Decimal(str(earnings.get("bonus", 0)))
        deduction_amt = Decimal(str(earnings.get("deduction", 0)))

        gross = base_pay + incentive_amt + bonus_amt

        deductions = [Deduction(
            name="platform_fee",
            amount=deduction_amt,
            policy_reference="platform_policy",
            category="fee"
        )] if deduction_amt > 0 else []

        incentives = [Incentive(
            name="incentive_bonus",
            amount=incentive_amt + bonus_amt,
            policy_reference="incentive_policy",
            adherence_rate=Decimal("1.0")
        )] if (incentive_amt + bonus_amt) > 0 else []

        # CRITICAL FIX: net_amount = actual settlement paid, NOT expected_amount
        # The engine uses tx.net_amount as platform_net (what was actually paid)
        net = settlement_amount
    else:
        gross = settlement_amount
        deductions = []
        incentives = []
        net = settlement_amount

    return TransactionRecord(
        transaction_id=tx_id,
        platform=platform,
        transaction_date=tx_date,
        gross_amount=gross,
        deductions=deductions,
        incentives=incentives,
        net_amount=net,  # Actual payment received
        distance_km=None,
        duration_minutes=None,
        wait_time_minutes=None,
        contract_version="v1.0"
    )


def create_missing_payment_transaction(earning: Dict[str, Any]) -> TransactionRecord:
    """Create a TransactionRecord for an earning with no matching settlement (missing payment)."""
    tx_id = earning["earning_id"]  # Use earning_id as transaction_id
    platform = earning["platform"]
    tx_date = parse_date(earning["date"])

    base_pay = Decimal(str(earning.get("base_pay", 0)))
    incentive_amt = Decimal(str(earning.get("incentive", 0)))
    bonus_amt = Decimal(str(earning.get("bonus", 0)))
    deduction_amt = Decimal(str(earning.get("deduction", 0)))

    gross = base_pay + incentive_amt + bonus_amt

    deductions = [Deduction(
        name="platform_fee",
        amount=deduction_amt,
        policy_reference="platform_policy",
        category="fee"
    )] if deduction_amt > 0 else []

    incentives = [Incentive(
        name="incentive_bonus",
        amount=incentive_amt + bonus_amt,
        policy_reference="incentive_policy",
        adherence_rate=Decimal("1.0")
    )] if (incentive_amt + bonus_amt) > 0 else []

    # Missing payment: net_amount = 0 (nothing was paid)
    net = Decimal("0")

    return TransactionRecord(
        transaction_id=tx_id,
        platform=platform,
        transaction_date=tx_date,
        gross_amount=gross,
        deductions=deductions,
        incentives=incentives,
        net_amount=net,
        distance_km=None,
        duration_minutes=None,
        wait_time_minutes=None,
        contract_version="v1.0"
    )


def get_default_contract_versions() -> List[ContractVersion]:
    """Contract versions that match synthetic data's actual pay structures.
    These are tuned so calculate_contractual_net() produces values close to earnings.expected_amount.
    """
    return [
        # Standard contracts matching synthetic data averages
        ContractVersion(platform="Zomato", version_id="v2.1", effective_from=date(2024, 1, 1),
                        effective_to=None, source="synthetic", confidence=Decimal("0.9"),
                        clauses=["Base pay: 160/day", "Per km: 15", "Wait time: 50/hr", "Peak bonus: 100"]),
        ContractVersion(platform="Swiggy", version_id="v1.8", effective_from=date(2024, 1, 1),
                        effective_to=None, source="synthetic", confidence=Decimal("0.9"),
                        clauses=["Base pay: 195/day", "Per km: 12", "Wait time: 45/hr", "Completion bonus: 80"]),
        ContractVersion(platform="UberEats", version_id="v3.0", effective_from=date(2024, 1, 1),
                        effective_to=None, source="synthetic", confidence=Decimal("0.9"),
                        clauses=["Base pay: 95/day", "Per km: 10", "Wait time: 40/hr", "Surge: 1.5x"]),
        ContractVersion(platform="Instamart", version_id="v1.0", effective_from=date(2024, 1, 1),
                        effective_to=None, source="synthetic", confidence=Decimal("0.9"),
                        clauses=["Base pay: 100/day", "Per km: 10", "Wait time: 40/hr", "Incentives: variable"]),
        # Premium incentive contracts (for partners with contract_type=premium_incentive)
        ContractVersion(platform="Swiggy", version_id="v1.8-premium", effective_from=date(2024, 1, 1),
                        effective_to=None, source="synthetic", confidence=Decimal("0.9"),
                        clauses=["Base pay: 195/day", "Per km: 12", "Wait time: 45/hr", "Premium incentive: variable", "Completion bonus: 80"]),
        ContractVersion(platform="Instamart", version_id="v1.0-premium", effective_from=date(2024, 1, 1),
                        effective_to=None, source="synthetic", confidence=Decimal("0.9"),
                        clauses=["Base pay: 100/day", "Per km: 10", "Wait time: 40/hr", "Premium incentive: variable"]),
    ]


# ============================================================
# Run REAL Audit Engine on Synthetic Data
# ============================================================

def _invoke_node(name: str, state: Any) -> Dict[str, Any]:
    """Run one audit-pipeline step on this state and hand back the state it produced.

    The pipeline steps take a typed ``FairPayState`` and hand back a plain
    dictionary, so each step's output is turned back into a typed state before
    the next one runs. Without this the second step is given a dictionary and
    stops with ``'dict' object has no attribute 'transactions'``, and the
    findings come back as a dictionary instead of ``AuditFindings``.

    Keys the audit state does not define (``controller_decisions``,
    ``batch_summary``) are carried through untouched.
    """
    typed = state if isinstance(state, FairPayState) else FairPayState.model_validate(dict(state))
    produced = dict(audit_graph.nodes[name].invoke(typed))
    revalidated = FairPayState.model_validate(produced)
    for field in FairPayState.model_fields:
        produced[field] = getattr(revalidated, field)
    return produced


def run_engine_on_partner(
    partner_id: str,
    dataset: SyntheticDataset,
    contract_versions: List[ContractVersion]
) -> tuple[FairPayState, List[Any], Optional[Any]]:
    """Run the actual audit engine (orchestrator) on synthetic partner data."""

    partner_json = dataset.partners_by_id[partner_id]
    partner_profile = json_to_partner_profile(partner_json)

    # Build transactions from settlements + earnings
    settlement_jsons = dataset.settlements_by_partner.get(partner_id, [])
    earnings_jsons = dataset.earnings_by_partner.get(partner_id, [])
    earnings_map = {(e["order_id"], e["platform"]): e for e in earnings_jsons}

    transactions = [
        json_to_transaction_record(s, earnings_map)
        for s in settlement_jsons
    ]

    # Also create transactions for earnings with NO matching settlement (missing payments)
    settlement_keys = {(s["order_id"], s["platform"]) for s in settlement_jsons}
    for earning in earnings_jsons:
        key = (earning["order_id"], earning["platform"])
        if key not in settlement_keys:
            # Missing payment - create transaction with net_amount=0 (nothing paid)
            tx = create_missing_payment_transaction(earning)
            transactions.append(tx)

    # Filter contract versions by partner's contract_type
    partner_contract_type = partner_json.get("contract_type", "standard")
    filtered_versions = []
    for cv in contract_versions:
        if partner_contract_type == "premium_incentive" and "-premium" in cv.version_id:
            filtered_versions.append(cv)
        elif partner_contract_type == "standard" and "-premium" not in cv.version_id:
            filtered_versions.append(cv)

    # Run the REAL audit engine (orchestrator graph)
    # Built as plain data; _invoke_node turns it into a typed FairPayState.
    initial_state: Dict[str, Any] = {
        "partner_id": partner_id,
        "partner_profile": partner_profile,
        "pdf_paths": [],
        "image_paths": [],
        "platform": "multi",
        "contract_versions": filtered_versions,
        "transactions": transactions,
        "applicable_laws": [],
        "payment_calculations": [],
        "business_cost_calculations": [],
        "audit_findings": None,
        "evidence_gaps": [],
        "classification_probability": Decimal("0.5"),
        "counterfactual_scenarios": None,
        "status": "ingesting",
        "error": None,
        "use_local_laws": True  # Use local law fixture, no Qdrant network calls
    }

    # Run versioning → calculation → findings → controller nodes directly (skip evidence node)
    try:
        state = initial_state
        state = _invoke_node("versioning", state)
        print(f"    [DEBUG] {partner_id}: versioning done, applicable_laws={len(state.get('applicable_laws', []))}")
        state = _invoke_node("calculation", state)
        print(f"    [DEBUG] {partner_id}: calculation done, payment_calcs={len(state.get('payment_calculations', []))}")
        state = _invoke_node("findings", state)
        findings = state.get("audit_findings")
        print(f"    [DEBUG] {partner_id}: findings done, findings={findings is not None}")
        state = _invoke_node("controller", state)
        controller_decisions = state.get("controller_decisions", [])
        batch_summary = state.get("batch_summary")
        print(f"    [DEBUG] {partner_id}: controller done, decisions={len(controller_decisions)}, summary={batch_summary is not None}")
        # Mark as complete on successful execution
        state = {**state, "status": "complete", "error": None}
    except Exception as e:
        import traceback
        print(f"    [ERROR] {partner_id}: {e}")
        traceback.print_exc()
        state = {**initial_state, "status": "failed", "error": str(e)}
        controller_decisions = []
        batch_summary = None

    return state, controller_decisions, batch_summary


# ============================================================
# Layer 1: Transaction Reconciliation Evaluation (PRIMARY)
# ============================================================

@dataclass
class ReconciliationPrediction:
    """Engine's prediction for a single transaction reconciliation."""
    earning_id: str
    transaction_id: str  # settlement_id or earning_id
    platform: str
    order_id: str
    predicted_anomaly: AnomalyType
    predicted_amount: Decimal
    expected_amount: Decimal
    settlement_amount: Decimal
    variance: Decimal
    confidence: Decimal
    notes: str


def classify_single(settlement: Dict[str, Any], earning: Dict[str, Any]) -> AnomalyType:
    """Compatibility wrapper around the shared benchmarked classifier."""
    return _classify_single(settlement, earning, parse_date)


def derive_reconciliation_predictions(
    partner_id: str,
    dataset: SyntheticDataset,
    state: FairPayState
) -> List[ReconciliationPrediction]:
    """Derive transaction-level anomaly predictions from settlements and earnings."""
    from collections import defaultdict

    predictions = []
    settlement_jsons = dataset.settlements_by_partner.get(partner_id, [])
    earnings_jsons = dataset.earnings_by_partner.get(partner_id, [])

    # Group settlements by (order_id, platform) for duplicate detection
    settlements_by_key = defaultdict(list)
    for s in settlement_jsons:
        key = (s["order_id"], s["platform"])
        settlements_by_key[key].append(s)

    matched_settlement_ids = set()

    # Process each earning: find its matching settlements
    for earning in earnings_jsons:
        key = (earning["order_id"], earning["platform"])
        matching_settlements = settlements_by_key.get(key, [])

        if not matching_settlements:
            # No settlement for this earning
            predictions.append(ReconciliationPrediction(
                earning_id=earning["earning_id"],
                transaction_id=earning["earning_id"],
                platform=earning["platform"],
                order_id=earning["order_id"],
                predicted_anomaly=AnomalyType.MISSING_PAYMENT,
                predicted_amount=Decimal("0"),
                expected_amount=Decimal(str(earning.get("expected_amount", 0))),
                settlement_amount=Decimal("0"),
                variance=Decimal(str(earning.get("expected_amount", 0))),
                confidence=Decimal("0.9"),
                notes="No settlement found for this earning"
            ))
            continue

        # Select primary settlement (closest to expected_amount)
        expected_amt = Decimal(str(earning.get("expected_amount", 0)))
        primary_settlement = min(
            matching_settlements,
            key=lambda s: abs(Decimal(str(s["amount_paid"])) - expected_amt)
        )

        # If multiple settlements for this earning, classify entire earning as DUPLICATE_SETTLEMENT
        if len(matching_settlements) > 1:
            # Mark all settlements as matched
            for s in matching_settlements:
                matched_settlement_ids.add(s["settlement_id"])

            # Emit single DUPLICATE_SETTLEMENT prediction for this earning
            total_settled = sum(Decimal(str(s["amount_paid"])) for s in matching_settlements)
            primary_settlement_id = primary_settlement["settlement_id"]

            predictions.append(ReconciliationPrediction(
                earning_id=earning["earning_id"],
                transaction_id=primary_settlement_id,
                platform=primary_settlement["platform"],
                order_id=primary_settlement["order_id"],
                predicted_anomaly=AnomalyType.DUPLICATE_SETTLEMENT,
                predicted_amount=total_settled,
                expected_amount=expected_amt,
                settlement_amount=total_settled,
                variance=total_settled - expected_amt,
                confidence=Decimal("0.9"),
                notes=f"Duplicate settlement: {len(matching_settlements)} settlements total={total_settled}, expected={expected_amt}"
            ))
            continue

        # Single settlement case - classify normally
        matched_settlement_ids.add(primary_settlement["settlement_id"])
        pred_anomaly = classify_single(primary_settlement, earning)
        settlement_amt = Decimal(str(primary_settlement["amount_paid"]))
        diff = settlement_amt - expected_amt

        predictions.append(ReconciliationPrediction(
            earning_id=earning["earning_id"],
            transaction_id=primary_settlement["settlement_id"],
            platform=primary_settlement["platform"],
            order_id=primary_settlement["order_id"],
            predicted_anomaly=pred_anomaly,
            predicted_amount=settlement_amt,
            expected_amount=expected_amt,
            settlement_amount=settlement_amt,
            variance=diff,
            confidence=Decimal("0.8"),
            notes=f"Evidence: expected={expected_amt}, settle={settlement_amt}, diff={diff}"
        ))

    # Unmatched settlements (no earning) → EXTRA_UNEXPLAINED_SETTLEMENT
    for s in settlement_jsons:
        if s["settlement_id"] not in matched_settlement_ids:
            predictions.append(ReconciliationPrediction(
                earning_id=f"NONE_{s['settlement_id']}",
                transaction_id=s["settlement_id"],
                platform=s["platform"],
                order_id=s["order_id"],
                predicted_anomaly=AnomalyType.EXTRA_UNEXPLAINED_SETTLEMENT,
                predicted_amount=Decimal(str(s["amount_paid"])),
                expected_amount=Decimal("0"),
                settlement_amount=Decimal(str(s["amount_paid"])),
                variance=Decimal(str(s["amount_paid"])),
                confidence=Decimal("0.9"),
                notes="Settlement with no matching earning"
            ))

    return predictions


@dataclass
class ReconciliationMetrics:
    anomaly_type: str
    expected: int
    detected: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float


def evaluate_reconciliation(
    partner_id: str,
    dataset: SyntheticDataset,
    state: FairPayState
) -> Dict[str, ReconciliationMetrics]:
    """Evaluate transaction-level reconciliation predictions against ground truth anomaly_types."""

    predictions = derive_reconciliation_predictions(partner_id, dataset, state)
    gt_list = dataset.ground_truth_by_partner.get(partner_id, [])

    # Group by anomaly type
    all_anomaly_types = [a.value for a in AnomalyType]
    results = {}

    for anomaly_type in all_anomaly_types:
        # Expected: ground truth records with this anomaly_type
        expected_records = [g for g in gt_list if g.get("anomaly_type") == anomaly_type]
        expected_count = len(expected_records)

        # Detected: predictions with this anomaly_type
        detected_predictions = [p for p in predictions if p.predicted_anomaly.value == anomaly_type]
        detected_count = len(detected_predictions)

        # Match by earning_id (default), but for extra_unexplained_settlement match on orphan_settlement_id
        matched = 0
        matched_indices = set()

        for exp in expected_records:
            exp_eid = exp.get("earning_id")
            # For extra_unexplained_settlement, ground truth has orphan_settlement_id, prediction uses settlement_id as transaction_id
            if anomaly_type == AnomalyType.EXTRA_UNEXPLAINED_SETTLEMENT.value:
                exp_eid = exp.get("orphan_settlement_id")
                pred_key = lambda p: p.earning_id.removeprefix("NONE_")
            else:
                pred_key = lambda p: p.earning_id

            for i, pred in enumerate(detected_predictions):
                if i in matched_indices:
                    continue
                if pred_key(pred) == exp_eid:
                    matched += 1
                    matched_indices.add(i)
                    break

        fp = detected_count - matched
        fn = expected_count - matched
        precision = matched / detected_count if detected_count > 0 else 0.0
        recall = matched / expected_count if expected_count > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        results[anomaly_type] = ReconciliationMetrics(
            anomaly_type=anomaly_type,
            expected=expected_count,
            detected=detected_count,
            true_positives=matched,
            false_positives=fp,
            false_negatives=fn,
            precision=precision,
            recall=recall,
            f1=f1
        )

    return results


# ============================================================
# Layer 2: Contract/Legal/Economic Findings Evaluation (SECONDARY)
# ============================================================

@dataclass
class FindingMetrics:
    finding_type: str
    expected: int
    detected: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float


def compare_findings(
    expected: List[Dict[str, Any]],
    actual: List[Any],
    finding_type: str
) -> FindingMetrics:
    """Compare expected (ground truth) vs actual (engine predictions) findings.
    Match by category + transaction_id + reference field."""
    expected_count = len(expected)
    actual_count = len(actual)

    matched = 0
    matched_indices = set()

    for exp in expected:
        exp_tx_id = exp.get("transaction_id")
        # For legal findings, also match by statute/section if available
        exp_statute = exp.get("legal_basis", {}).get("statute", "")
        exp_section = exp.get("legal_basis", {}).get("section", "")

        for i, act in enumerate(actual):
            if i in matched_indices:
                continue
            act_tx_id = getattr(act, "transaction_id", None)
            if exp_tx_id and act_tx_id and str(exp_tx_id) == str(act_tx_id):
                # For legal findings, also check law match
                if finding_type == "legal_risks":
                    act_statute = getattr(act.legal_basis, "statute", "") if hasattr(act, 'legal_basis') else ""
                    act_section = getattr(act.legal_basis, "section", "") if hasattr(act, 'legal_basis') else ""
                    if exp_statute and act_statute and exp_statute != act_statute:
                        continue
                    if exp_section and act_section and exp_section != act_section:
                        continue
                matched += 1
                matched_indices.add(i)
                break

    false_positives = actual_count - matched
    false_negatives = expected_count - matched
    precision = matched / actual_count if actual_count > 0 else 1.0
    recall = matched / expected_count if expected_count > 0 else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return FindingMetrics(
        finding_type=finding_type,
        expected=expected_count,
        detected=actual_count,
        true_positives=matched,
        false_positives=false_positives,
        false_negatives=false_negatives,
        precision=precision,
        recall=recall,
        f1=f1
    )


@dataclass
class PartnerEvaluation:
    partner_id: str
    partner_name: str

    # Layer 1: Transaction Reconciliation (PRIMARY)
    reconciliation_metrics: Dict[str, ReconciliationMetrics]
    overall_reconciliation_f1: float

    # Layer 2: Findings (SECONDARY)
    contract_compliance: FindingMetrics
    legal_risks: FindingMetrics
    economic_concerns: FindingMetrics

    # Engine health
    engine_status: str
    engine_error: Optional[str]
    predictions: List[ReconciliationPrediction]

    # Layer 3: Controller Decisions (OPERATIONAL)
    controller_decisions: List[Any] = field(default_factory=list)
    batch_summary: Optional[Any] = None


def evaluate_partner(
    partner_id: str,
    dataset: SyntheticDataset,
    contract_versions: List[ContractVersion]
) -> PartnerEvaluation:
    """Run engine → get predictions → evaluate Layer 1 (reconciliation) and Layer 2 (findings)."""

    print(f"  Running audit engine on {partner_id}...")
    state, controller_decisions, batch_summary = run_engine_on_partner(partner_id, dataset, contract_versions)
    findings = state.get("audit_findings")
    engine_status = state.get("status", "unknown")
    engine_error = state.get("error")

    if not findings:
        print(f"  [WARNING] No findings returned for {partner_id}")
        empty_metrics = {a.value: ReconciliationMetrics(a.value, 0, 0, 0, 0, 0, 1.0, 1.0, 1.0) for a in AnomalyType}
        return PartnerEvaluation(
            partner_id=partner_id,
            partner_name=dataset.partners_by_id[partner_id].get("name", ""),
            reconciliation_metrics=empty_metrics,
            overall_reconciliation_f1=0.0,
            contract_compliance=FindingMetrics("contract_compliance", 0, 0, 0, 0, 0, 1.0, 1.0, 1.0),
            legal_risks=FindingMetrics("legal_risks", 0, 0, 0, 0, 0, 1.0, 1.0, 1.0),
            economic_concerns=FindingMetrics("economic_concerns", 0, 0, 0, 0, 0, 1.0, 1.0, 1.0),
            controller_decisions=controller_decisions,
            batch_summary=batch_summary,
            engine_status=engine_status,
            engine_error=engine_error,
            predictions=[]
        )

    # Layer 1: Transaction Reconciliation Evaluation
    recon_metrics = evaluate_reconciliation(partner_id, dataset, state)
    predictions = derive_reconciliation_predictions(partner_id, dataset, state)

    # Overall reconciliation F1 (macro average across anomaly types with expected > 0)
    f1_scores = [m.f1 for m in recon_metrics.values() if m.expected > 0]
    overall_f1 = sum(f1_scores) / len(f1_scores) if f1_scores else 0.0

    # Layer 2: Findings Evaluation against ground_truth_layer2.json
    gt_l2 = dataset.ground_truth_layer2_by_partner.get(partner_id, {})

    # Contract compliance
    expected_contract = gt_l2.get("contract_compliance_findings", [])
    actual_contract = findings.contract_compliance_findings
    contract_comp = compare_findings(expected_contract, actual_contract, "contract_compliance")

    # Legal risks
    expected_legal = gt_l2.get("legal_risk_findings", [])
    actual_legal = findings.legal_risk_findings
    legal_risk = compare_findings(expected_legal, actual_legal, "legal_risks")

    # Economic concerns
    expected_economic = gt_l2.get("economic_concern_findings", [])
    actual_economic = findings.economic_concern_findings
    economic = compare_findings(expected_economic, actual_economic, "economic_concerns")

    if findings:
        print(f"  [DEBUG] {partner_id}: Contract compliance predictions: {len(findings.contract_compliance_findings)} (expected: {len(expected_contract)})")
        print(f"  [DEBUG] {partner_id}: Legal risk predictions: {len(findings.legal_risk_findings)} (expected: {len(expected_legal)})")
        print(f"  [DEBUG] {partner_id}: Economic concern predictions: {len(findings.economic_concern_findings)} (expected: {len(expected_economic)})")

    return PartnerEvaluation(
        partner_id=partner_id,
        partner_name=dataset.partners_by_id[partner_id].get("name", ""),
        reconciliation_metrics=recon_metrics,
        overall_reconciliation_f1=overall_f1,
        contract_compliance=contract_comp,
        legal_risks=legal_risk,
        economic_concerns=economic,
        controller_decisions=controller_decisions,
        batch_summary=batch_summary,
        engine_status=engine_status,
        engine_error=engine_error,
        predictions=predictions
    )


# ============================================================
# Aggregate Report (Finance Controller Proof)
# ============================================================

@dataclass
class AggregateReport:
    total_partners: int
    total_transactions_processed: int

    # Layer 1: Reconciliation aggregates
    reconciliation_by_type: Dict[str, ReconciliationMetrics]
    overall_reconciliation_f1: float
    overall_reconciliation_precision: float
    overall_reconciliation_recall: float

    # Layer 2: Finding aggregates
    total_expected_findings: int
    total_detected_findings: int
    total_true_positives: int
    total_false_positives: int
    total_false_negatives: int
    findings_precision: float
    findings_recall: float
    findings_f1: float

    # Layer 2: Per-category metrics
    contract_metrics: FindingMetrics
    legal_metrics: FindingMetrics
    economic_metrics: FindingMetrics

    # Engine health
    successful_runs: int
    failed_runs: int
    partners_with_errors: List[str]

    # Exception handling
    human_review_cases: int
    partners_needing_review: List[str]

    # Layer 3 aggregate (computed in build_aggregate_report)
    layer3_total_records: int = 0
    layer3_auto_resolved: int = 0
    layer3_action_required: int = 0
    layer3_human_review: int = 0
    layer3_insufficient_evidence: int = 0
    layer3_exception_rate: float = 0.0
    layer3_resolution_rate: float = 0.0
    layer3_by_priority: Dict[str, int] = field(default_factory=dict)
    layer3_by_action: Dict[str, int] = field(default_factory=dict)
    layer3_human_review_queue: List[Dict[str, Any]] = field(default_factory=list)

    # Layer 3 data for printing
    evaluations: List[PartnerEvaluation] = field(default_factory=list)

    def print_report(self):
        print(f"\n{'='*70}")
        print(f"FAIRPAY SYNTHETIC VERIFICATION REPORT")
        print(f"{'='*70}")
        print(f"Partners Processed:  {self.total_partners}")
        print(f"Transactions Input:  {self.total_transactions_processed}")
        print(f"Engine Success Rate: {self.successful_runs}/{self.total_partners} "
              f"({self.successful_runs/self.total_partners*100:.1f}%)")
        print(f"{'-'*70}")
        print(f"LAYER 1: TRANSACTION RECONCILIATION (anomaly_type)")
        print(f"{'-'*70}")
        for anomaly_type, m in self.reconciliation_by_type.items():
            if m.expected > 0 or m.detected > 0:
                print(f"  {anomaly_type:30s} | Exp:{m.expected:2d} Det:{m.detected:2d} TP:{m.true_positives:2d} "
                      f"FP:{m.false_positives:2d} FN:{m.false_negatives:2d} | "
                      f"P:{m.precision:.0%} R:{m.recall:.0%} F1:{m.f1:.0%}")
        print(f"{'-'*70}")
        print(f"  OVERALL Reconciliation F1:  {self.overall_reconciliation_f1:.2%}")
        print(f"  OVERALL Precision:          {self.overall_reconciliation_precision:.2%}")
        print(f"  OVERALL Recall:             {self.overall_reconciliation_recall:.2%}")
        print(f"{'-'*70}")
        print(f"LAYER 2: CONTRACT/LEGAL/ECONOMIC FINDINGS")
        print(f"{'-'*70}")
        print(f"  Overall:")
        print(f"    Expected Findings:  {self.total_expected_findings}")
        print(f"    Detected Findings:  {self.total_detected_findings}")
        print(f"    True Positives:     {self.total_true_positives}")
        print(f"    False Positives:    {self.total_false_positives}")
        print(f"    False Negatives:    {self.total_false_negatives}")
        print(f"    Precision:          {self.findings_precision:.2%}")
        print(f"    Recall:             {self.findings_recall:.2%}")
        print(f"    F1 Score:           {self.findings_f1:.2%}")
        print(f"  By Category:")
        print(f"    Contract Compliance:  Exp:{self.contract_metrics.expected} Det:{self.contract_metrics.detected} "
              f"TP:{self.contract_metrics.true_positives} P:{self.contract_metrics.precision:.0%} "
              f"R:{self.contract_metrics.recall:.0%} F1:{self.contract_metrics.f1:.0%}")
        print(f"    Legal Risks:          Exp:{self.legal_metrics.expected} Det:{self.legal_metrics.detected} "
              f"TP:{self.legal_metrics.true_positives} P:{self.legal_metrics.precision:.0%} "
              f"R:{self.legal_metrics.recall:.0%} F1:{self.legal_metrics.f1:.0%}")
        print(f"    Economic Concerns:    Exp:{self.economic_metrics.expected} Det:{self.economic_metrics.detected} "
              f"TP:{self.economic_metrics.true_positives} P:{self.economic_metrics.precision:.0%} "
              f"R:{self.economic_metrics.recall:.0%} F1:{self.economic_metrics.f1:.0%}")
        print(f"{'-'*70}")
        if self.partners_with_errors:
            print(f"Engine Errors:      {self.failed_runs} partners")
            for p in self.partners_with_errors:
                print(f"  - {p}")
        print(f"{'-'*70}")
        print(f"Layer 2 Review Queue (reconciliation F1/engine health):  {self.human_review_cases} partners")
        if self.partners_needing_review:
            for p in self.partners_needing_review:
                print(f"  - {p}")
        print(f"{'-'*70}")
        print(f"LAYER 3 HUMAN REVIEW QUEUE (controller decisions)")
        print(f"{'-'*70}")
        if self.layer3_human_review_queue:
            for entry in self.layer3_human_review_queue:
                print(f"  Partner:    {entry['partner_id']} ({entry['partner_name']})")
                print(f"  Transaction: {entry['transaction_id']}")
                print(f"  Priority:    {entry['priority']}")
                print(f"  Action:      {entry['recommended_action']}")
                print(f"  Reason:      {entry['reason']}")
                print()
        else:
            print("  (empty)")
        print(f"{'-'*70}")
        print(f"LAYER 3 AGGREGATE SUMMARY (controller decisions)")
        print(f"{'-'*70}")
        print(f"  Total Records Processed:  {self.layer3_total_records}")
        print(f"  Automatically Resolved:   {self.layer3_auto_resolved}")
        print(f"  Action Required:          {self.layer3_action_required}")
        print(f"  Human Review:             {self.layer3_human_review}")
        print(f"  Insufficient Evidence:    {self.layer3_insufficient_evidence}")
        print(f"  Exception Rate:           {self.layer3_exception_rate:.2%}")
        print(f"  Resolution Rate:          {self.layer3_resolution_rate:.2%}")
        print(f"  By Priority:              {self.layer3_by_priority}")
        print(f"  By Recommended Action:    {self.layer3_by_action}")
        print(f"{'-'*70}")
        print(f"LAYER 3: CONTROLLER DECISIONS (per-partner detail)")
        print(f"{'-'*70}")
        for eval in self.evaluations:
            if not eval.controller_decisions:
                continue
            print(f"  Partner: {eval.partner_id} ({eval.partner_name})")
            for dec in eval.controller_decisions:
                print(f"    Transaction: {dec.transaction_id}")
                print(f"      Resolution:     {dec.resolution_status}")
                print(f"      Priority:       {dec.priority}")
                print(f"      Action:         {dec.recommended_action}")
                print(f"      Human Review:   {dec.human_review_required}")
                print(f"      Reason:         {dec.reason}")
                print(f"      Evidence Refs:  {dec.evidence_references}")
            if eval.batch_summary:
                bs = eval.batch_summary
                print(f"    Batch Summary:")
                print(f"      Records Processed:  {bs.records_processed}")
                print(f"      Auto Resolved:      {bs.automatically_resolved}")
                print(f"      Action Required:    {bs.action_required}")
                print(f"      Human Review:       {bs.human_review}")
                print(f"      Insufficient Evid:  {bs.insufficient_evidence}")
                print(f"      Exception Rate:     {bs.exception_rate:.2%}")
                print(f"      Resolution Rate:    {bs.resolution_rate:.2%}")
                print(f"      By Priority:        {bs.by_priority}")
                print(f"      By Action:          {bs.by_action}")
        print(f"{'='*70}")


def build_aggregate_report(
    evaluations: List[PartnerEvaluation],
    dataset: SyntheticDataset
) -> AggregateReport:
    total_partners = len(evaluations)
    total_transactions = sum(
        len(dataset.settlements_by_partner.get(e.partner_id, []))
        for e in evaluations
    )

    # Aggregate Layer 1: Reconciliation
    all_anomaly_types = [a.value for a in AnomalyType]
    recon_aggregate = {}
    for anomaly_type in all_anomaly_types:
        total_expected = sum(e.reconciliation_metrics.get(anomaly_type, ReconciliationMetrics(anomaly_type, 0,0,0,0,0,0,0,0)).expected for e in evaluations)
        total_detected = sum(e.reconciliation_metrics.get(anomaly_type, ReconciliationMetrics(anomaly_type, 0,0,0,0,0,0,0,0)).detected for e in evaluations)
        total_tp = sum(e.reconciliation_metrics.get(anomaly_type, ReconciliationMetrics(anomaly_type, 0,0,0,0,0,0,0,0)).true_positives for e in evaluations)
        total_fp = sum(e.reconciliation_metrics.get(anomaly_type, ReconciliationMetrics(anomaly_type, 0,0,0,0,0,0,0,0)).false_positives for e in evaluations)
        total_fn = sum(e.reconciliation_metrics.get(anomaly_type, ReconciliationMetrics(anomaly_type, 0,0,0,0,0,0,0,0)).false_negatives for e in evaluations)

        precision = total_tp / total_detected if total_detected > 0 else 1.0
        recall = total_tp / total_expected if total_expected > 0 else 1.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        recon_aggregate[anomaly_type] = ReconciliationMetrics(
            anomaly_type=anomaly_type,
            expected=total_expected,
            detected=total_detected,
            true_positives=total_tp,
            false_positives=total_fp,
            false_negatives=total_fn,
            precision=precision,
            recall=recall,
            f1=f1
        )

    # Overall reconciliation metrics (macro avg over types with expected > 0)
    f1_scores = [m.f1 for m in recon_aggregate.values() if m.expected > 0]
    overall_recon_f1 = sum(f1_scores) / len(f1_scores) if f1_scores else 0.0

    prec_scores = [m.precision for m in recon_aggregate.values() if m.expected > 0]
    overall_prec = sum(prec_scores) / len(prec_scores) if prec_scores else 0.0

    rec_scores = [m.recall for m in recon_aggregate.values() if m.expected > 0]
    overall_rec = sum(rec_scores) / len(rec_scores) if rec_scores else 0.0

    # Aggregate Layer 2: Findings per category
    contract_expected = sum(e.contract_compliance.expected for e in evaluations)
    contract_detected = sum(e.contract_compliance.detected for e in evaluations)
    contract_tp = sum(e.contract_compliance.true_positives for e in evaluations)
    contract_fp = sum(e.contract_compliance.false_positives for e in evaluations)
    contract_fn = sum(e.contract_compliance.false_negatives for e in evaluations)
    contract_prec = contract_tp / contract_detected if contract_detected > 0 else 1.0
    contract_rec = contract_tp / contract_expected if contract_expected > 0 else 1.0
    contract_f1 = 2 * contract_prec * contract_rec / (contract_prec + contract_rec) if (contract_prec + contract_rec) > 0 else 0.0
    contract_metrics = FindingMetrics("contract_compliance", contract_expected, contract_detected, contract_tp, contract_fp, contract_fn, contract_prec, contract_rec, contract_f1)

    legal_expected = sum(e.legal_risks.expected for e in evaluations)
    legal_detected = sum(e.legal_risks.detected for e in evaluations)
    legal_tp = sum(e.legal_risks.true_positives for e in evaluations)
    legal_fp = sum(e.legal_risks.false_positives for e in evaluations)
    legal_fn = sum(e.legal_risks.false_negatives for e in evaluations)
    legal_prec = legal_tp / legal_detected if legal_detected > 0 else 1.0
    legal_rec = legal_tp / legal_expected if legal_expected > 0 else 1.0
    legal_f1 = 2 * legal_prec * legal_rec / (legal_prec + legal_rec) if (legal_prec + legal_rec) > 0 else 0.0
    legal_metrics = FindingMetrics("legal_risks", legal_expected, legal_detected, legal_tp, legal_fp, legal_fn, legal_prec, legal_rec, legal_f1)

    economic_expected = sum(e.economic_concerns.expected for e in evaluations)
    economic_detected = sum(e.economic_concerns.detected for e in evaluations)
    economic_tp = sum(e.economic_concerns.true_positives for e in evaluations)
    economic_fp = sum(e.economic_concerns.false_positives for e in evaluations)
    economic_fn = sum(e.economic_concerns.false_negatives for e in evaluations)
    economic_prec = economic_tp / economic_detected if economic_detected > 0 else 1.0
    economic_rec = economic_tp / economic_expected if economic_expected > 0 else 1.0
    economic_f1 = 2 * economic_prec * economic_rec / (economic_prec + economic_rec) if (economic_prec + economic_rec) > 0 else 0.0
    economic_metrics = FindingMetrics("economic_concerns", economic_expected, economic_detected, economic_tp, economic_fp, economic_fn, economic_prec, economic_rec, economic_f1)

    # Overall Layer 2
    all_expected = contract_expected + legal_expected + economic_expected
    all_detected = contract_detected + legal_detected + economic_detected
    all_tp = contract_tp + legal_tp + economic_tp
    all_fp = contract_fp + legal_fp + economic_fp
    all_fn = contract_fn + legal_fn + economic_fn

    findings_precision = all_tp / all_detected if all_detected > 0 else 1.0
    findings_recall = all_tp / all_expected if all_expected > 0 else 1.0
    findings_f1 = 2 * findings_precision * findings_recall / (findings_precision + findings_recall) if (findings_precision + findings_recall) > 0 else 0.0

    successful = sum(1 for e in evaluations if e.engine_status == "complete" and not e.engine_error)
    failed = total_partners - successful
    partners_with_errors = [e.partner_id for e in evaluations if e.engine_error or e.engine_status != "complete"]

    # Partners needing review: low reconciliation F1 or engine errors
    review_partners = []
    for e in evaluations:
        if e.overall_reconciliation_f1 < 0.7 or e.engine_error or e.engine_status != "complete":
            review_partners.append(e.partner_id)

    # Layer 3 aggregate from controller decisions
    layer3_total_records = 0
    layer3_auto_resolved = 0
    layer3_action_required = 0
    layer3_human_review = 0
    layer3_insufficient_evidence = 0
    layer3_by_priority = {}
    layer3_by_action = {}
    layer3_human_review_queue = []

    for e in evaluations:
        for dec in e.controller_decisions:
            layer3_total_records += 1
            status = dec.resolution_status.value if hasattr(dec.resolution_status, 'value') else str(dec.resolution_status)
            priority = dec.priority.value if hasattr(dec.priority, 'value') else str(dec.priority)
            action = dec.recommended_action.value if hasattr(dec.recommended_action, 'value') else str(dec.recommended_action)

            if status == ResolutionStatus.RESOLVED_AUTOMATICALLY.value:
                layer3_auto_resolved += 1
            elif status == ResolutionStatus.ACTION_REQUIRED.value:
                layer3_action_required += 1
            elif status == ResolutionStatus.HUMAN_REVIEW.value:
                layer3_human_review += 1
            elif status == ResolutionStatus.INSUFFICIENT_EVIDENCE.value:
                layer3_insufficient_evidence += 1

            layer3_by_priority[priority] = layer3_by_priority.get(priority, 0) + 1
            layer3_by_action[action] = layer3_by_action.get(action, 0) + 1

            if dec.human_review_required or status == ResolutionStatus.HUMAN_REVIEW.value:
                layer3_human_review_queue.append({
                    "partner_id": e.partner_id,
                    "partner_name": e.partner_name,
                    "transaction_id": dec.transaction_id,
                    "priority": priority,
                    "recommended_action": action,
                    "reason": dec.reason
                })

    layer3_exception_rate = (layer3_action_required + layer3_human_review + layer3_insufficient_evidence) / layer3_total_records if layer3_total_records > 0 else 0.0
    layer3_resolution_rate = layer3_auto_resolved / layer3_total_records if layer3_total_records > 0 else 0.0

    return AggregateReport(
        total_partners=total_partners,
        total_transactions_processed=total_transactions,
        reconciliation_by_type=recon_aggregate,
        overall_reconciliation_f1=overall_recon_f1,
        overall_reconciliation_precision=overall_prec,
        overall_reconciliation_recall=overall_rec,
        total_expected_findings=all_expected,
        total_detected_findings=all_detected,
        total_true_positives=all_tp,
        total_false_positives=all_fp,
        total_false_negatives=all_fn,
        findings_precision=findings_precision,
        findings_recall=findings_recall,
        findings_f1=findings_f1,
        contract_metrics=contract_metrics,
        legal_metrics=legal_metrics,
        economic_metrics=economic_metrics,
        successful_runs=successful,
        failed_runs=failed,
        partners_with_errors=partners_with_errors,
        human_review_cases=len(review_partners),
        partners_needing_review=review_partners,
        evaluations=evaluations,
        layer3_total_records=layer3_total_records,
        layer3_auto_resolved=layer3_auto_resolved,
        layer3_action_required=layer3_action_required,
        layer3_human_review=layer3_human_review,
        layer3_insufficient_evidence=layer3_insufficient_evidence,
        layer3_exception_rate=layer3_exception_rate,
        layer3_resolution_rate=layer3_resolution_rate,
        layer3_by_priority=layer3_by_priority,
        layer3_by_action=layer3_by_action,
        layer3_human_review_queue=layer3_human_review_queue
    )


# ============================================================
# Main Runner
# ============================================================

def run_full_verification(data_dir: str = "synthetic_data") -> List[PartnerEvaluation]:
    dataset = load_synthetic_data(data_dir)
    contract_versions = get_default_contract_versions()

    evaluations = []

    print(f"\n{'='*70}")
    print(f"RUNNING SYNTHETIC VERIFICATION ON {len(dataset.partners)} PARTNERS")
    print(f"Architecture: Earnings+Settlements -> ENGINE -> PREDICTION <-> GROUND_TRUTH")
    print(f"Layer 1: Transaction Reconciliation (anomaly_type) <- PRIMARY")
    print(f"Layer 2: Contract/Legal/Economic Findings          <- SECONDARY")
    print(f"{'='*70}")

    for partner in dataset.partners:
        partner_id = partner["partner_id"]
        eval_result = evaluate_partner(partner_id, dataset, contract_versions)
        evaluations.append(eval_result)

    aggregate = build_aggregate_report(evaluations, dataset)
    aggregate.print_report()

    return evaluations


# ============================================================
# CLI Entry Point
# ============================================================

if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=str, default="synthetic_data", help="Path to synthetic data directory")
    args = parser.parse_args()

    print(f"Loading synthetic data from: {args.data_root}")
    evaluations = run_full_verification(args.data_root)

    # Exit code: 0 if all engine runs successful, 1 if any failures
    failed = sum(1 for e in evaluations if e.engine_error or e.engine_status != "complete")
    sys.exit(0 if failed == 0 else 1)
