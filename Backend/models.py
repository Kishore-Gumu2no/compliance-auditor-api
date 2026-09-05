from __future__ import annotations
from enum import Enum
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List, Literal, Dict, Any
from pydantic import BaseModel, Field, computed_field, model_validator

class FindingGrade(str, Enum):
    CONFIRMED_VIOLATION = "confirmed_violation"
    POTENTIAL_ISSUE = "potential_issue"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    ECONOMIC_CONCERN = "economic_concern"


# ============================================================
# Layer 3: Controller / Decision & Escalation
# ============================================================

class ResolutionStatus(str, Enum):
    RESOLVED_AUTOMATICALLY = "resolved_automatically"
    ACTION_REQUIRED = "action_required"
    HUMAN_REVIEW = "human_review"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RecommendedAction(str, Enum):
    NO_ACTION = "no_action"
    REQUEST_EXPLANATION = "request_explanation"
    RAISE_PAYMENT_DISPUTE = "raise_payment_dispute"
    REVIEW_CONTRACT = "review_contract"
    LEGAL_REVIEW = "legal_review"


class ControllerDecision(BaseModel):
    """Layer 3 operational decision for a single transaction."""
    transaction_id: str = Field(description="Transaction this decision applies to")
    partner_id: str = Field(description="Partner this decision applies to")
    resolution_status: ResolutionStatus = Field(description="Operational resolution status")
    priority: Priority = Field(description="Priority for action")
    recommended_action: RecommendedAction = Field(description="Recommended operational action")
    human_review_required: bool = Field(description="Whether human review is required")
    reason: str = Field(description="Human-readable explanation of the decision")
    evidence_references: List[str] = Field(default_factory=list, description="Finding IDs and payment calc indices that drove this decision")
    layer1_facts: Dict[str, Any] = Field(default_factory=dict, description="Snapshot of Layer 1 facts: variance, anomaly_type")
    layer2_findings: List[str] = Field(default_factory=list, description="Layer 2 finding IDs that drove this decision")


class BatchSummary(BaseModel):
    """Aggregate operational summary for batch reporting."""
    records_processed: int = Field(default=0, description="Number of transaction-level decisions")
    automatically_resolved: int = Field(default=0, description="Count of RESOLVED_AUTOMATICALLY")
    action_required: int = Field(default=0, description="Count of ACTION_REQUIRED")
    human_review: int = Field(default=0, description="Count of HUMAN_REVIEW")
    insufficient_evidence: int = Field(default=0, description="Count of INSUFFICIENT_EVIDENCE")
    exception_rate: float = Field(default=0.0, description="Proportion requiring action or review")
    resolution_rate: float = Field(default=0.0, description="Proportion resolved automatically")
    by_priority: Dict[str, int] = Field(default_factory=dict, description="Counts by priority level")
    by_action: Dict[str, int] = Field(default_factory=dict, description="Counts by recommended action")

class LegalBasis(BaseModel):
    # Explained BaseModel in orchestrator.py
    statute: str = Field(description="Law name such as, example: Karnataka Minimum Wage Act, etc")
    section: str = Field(description="Specific section such as section 12(1)")
    jurisdiction: str = Field(description="Where it applies such as Karnataka, Kerala,etc")
    effective_date: date = Field(description="From when it started taking place")
    applicability_note: Optional[str] = Field(description="When or how it can be applied, OPTIONAL")

    # Full payload preservation for legal reasoning
    source_type: str = Field(default="", description="Central Act, State Act, Constitution, State Rules")
    applicability_category: str = Field(default="", description="Exact applicability enum from payload")
    full_text: str = Field(default="", description="Complete provision text for reasoning")
    law_id: str = Field(default="", description="Unique law identifier")
    year: int = Field(default=0, description="Law year")
    status: str = Field(default="in_force", description="Law status")
    version: str = Field(default="original", description="Law version")
    parent_act: str = Field(default="", description="Parent act identifier")

class ContractVersion(BaseModel):
    #region Why should this class exist?
    """Let's say a delivery partner works in zomato and uber simultaneously, every platform has it's
    own version such as v1, v2, v2.1, etc. In each and every version, new changes in rules, platform 
    changes are committed. Therefore we need to capture each and every version in every platforms. It
    also includes from which date the rules are valid and till when the rules will be valid. Therefore 
    we need to capture all that information, along with specific clauses."""
    #endregion
    platform: str = Field(description="Uber, Zomato,etc")
    version_id: str = Field(description="v2.1, v1.1, etc")
    effective_from: date = Field(description="From when this version starts")
    effective_to: Optional[date] = Field(default = None, description="When it ends, might be None as well")
    source: str = Field(description="If the source was from pdf, images")
    confidence: Decimal = Field(ge=0, le=1, description="Confidence from 0 to 1") #ge = greater than or equal to, le = lesser than or equal to
    clauses: List[str] = Field(default_factory=list, description="Key Contract Clauses")
    #we use 'default_list=list' because every platform requires it's own personal List, if we don't include this all platform's clauses will be stored in a single list.

class Deduction(BaseModel):
    name: str = Field(description="Such as platform_fee, penalty_fee")
    amount: Decimal = Field(ge=0, description="The amount that has been deducted, must be >=0")
    policy_reference: str = Field(description="To which policy or clause it belongs to")
    category: str = Field(description="fee, penalty, adjustment")


class Incentive(BaseModel):
    name: str = Field(description="Such as peak_bonus, completion_hours")
    amount: Decimal = Field(ge=0, description="The amount that has been deducted, must be >=0")
    policy_reference: str = Field(description="To which policy or clause it belongs to")
    adherence_rate: Optional[Decimal] = Field(default=None, ge=0, le=1, description="0.0 to 1.0 (if applicable)")

class TransactionRecord(BaseModel):
    transaction_id: str = Field(description="Unique ID")
    platform: str = Field(description="Uber,Zomato")
    transaction_date: date = Field(description="The date of Transaction")
    gross_amount: Decimal = Field(description="Amount before deductions")
    deductions: List[Deduction] = Field(default_factory = list, description="List of deductions from one or many platforms")
    incentives: List[Incentive] = Field(default_factory = list,description="List of incentives from one or many platforms")
    net_amount: Decimal = Field(description="The amount you get after Deductions and Incentives")
    distance_km: Optional[Decimal] = Field(description="Distance if applicable")
    duration_minutes: Optional[int] = Field(description="Duration if applicable")
    wait_time_minutes: Optional[int] = Field(description="Wait time if applicable")
    contract_version: Optional[str] = Field(default=None, description="Which contract version is evidenced by the source")

class PaymentCalculation(BaseModel):
    platform_net: Decimal = Field(description="What Platform actually paid")
    contractual_net: Decimal = Field(description="What contract says should be paid")
    @computed_field
    @property
    def variance(self) -> Decimal:
        return self.platform_net - self.contractual_net

    @computed_field
    @property
    def variance_pct(self) -> Decimal:
        if self.contractual_net == 0:
            return Decimal("0")
        return (self.variance/self.contractual_net)*Decimal("100")
    breakdown: Dict[str,Decimal] = Field(default_factory = dict, description="Line by line comparision")

class BusinessCostCalculation(BaseModel):
    petrol_cost: Decimal = Field(ge = 0,description="Distance X petrol_per_km")
    business_hourly_rate: Decimal = Field(ge = 0, description="True Profit per hour after petrol.")
    active_hourly_rate: Optional[Decimal] = Field(default = None,ge=0, description="Earnings per active delivery hour.")
    online_hourly_rate: Optional[Decimal] = Field(default= None, ge=0, description="Earnings per logged-in hour.")

class ContractComplianceFinding(BaseModel):
    finding_id: str = Field(description="Unique ID for this finding.")
    grade: FindingGrade = Field(description="To get an idea of how concering the compliance is with class FindingGrade enum.")
    transaction_id: str = Field(description="Links to TransactionRecord to define which particular transaction was not compliant.")
    contract_clause: str = Field(description="This tells us which contract clause was violated.")
    expected_amount: Decimal = Field(ge=0,description="The amount which is expected by the delivery partner to be paid.")
    actual_amount: Decimal = Field(ge=0,description="The actual amount that was paid to the delivery partner.")
    variance: Decimal = Field(description="The difference between expected amount and paid amount, it can be negative as well (underpaid).")
    evidence_strength: Decimal = Field(ge=0, le=1, description="This tells us how sure the compliance audit is about the difference, it shows the confidence in this finding based on evidence quality")
    notes: str = Field(description="This is the explanation which will be presented to delivery partners about their audit.")
    category: Literal["contract"] = Field(default="contract", description="Finding category for evaluation matching")
    severity: Optional[Literal["low","medium","high","critical"]] = Field(default=None, description="Severity level")
    human_review_required: bool = Field(default=False, description="Whether human review is required")

class LegalRiskFinding(BaseModel):
    finding_id: str = Field(description="Unique ID for finding non compliant audit")
    grade: FindingGrade = Field(description="To get an idea of how concerning the non compliace is, with class FindingGrade enum.")
    transaction_id: str = Field(description="This links to TransactionRecord class to be more specific about the transaction")
    legal_basis: LegalBasis = Field(description="Nested Object (statute,section,etc)")
    classification_probability: Decimal = Field(ge=0, le=1, description="0.0 - 1.0 is the probability")
    applicable_law_version: str = Field(description="Which law version is being applied.")
    risk_if_employee: str = Field(description="what the law requires if employee")
    risk_if_contractor: str = Field(description="what the law requires if contractor")
    evidence_strength: Decimal = Field(ge=0, le=1, description="How concrete the evidence is out of 0 to 1")
    notes: str = Field(description="Human readable explanantion.")
    category: Literal["legal"] = Field(default="legal", description="Finding category for evaluation matching")
    severity: Optional[Literal["low","medium","high","critical"]] = Field(default=None, description="Severity level")
    human_review_required: bool = Field(default=False, description="Whether human review is required")

class EconomicConcernFinding(BaseModel):
    finding_id: str = Field(description="Unique ID for finding the pay when it's legal but economically unviable")
    grade: FindingGrade = Field(description="It is usual ECONOMIC_CONCERN")
    transaction_id: str = Field(description="Links to TransactionRecord")
    business_hourly_rate: Decimal = Field(ge=0,description="Links to BusinessCostCalculation")
    contracted_hourly_benchmark: Decimal = Field(ge=0,description="How much the contract is paying per hour")
    market_hourly_benchmark: Decimal = Field(ge=0, description="Average hourly benchmark in the city.")
    min_wage_hourly_benchmark: Decimal = Field(ge=0, description="Legal minimum wage")
    gap_vs_contracted_pct: Decimal = Field(ge=0, description="Percentage below contract rate.")
    gap_vs_market_pct: Decimal = Field(ge=0, description="Percentage below market rate.")
    gap_vs_min_wage_pct: Decimal = Field(ge=0, description="Percentage below minimum wage.")
    severity: Literal["low","medium","high","critical"] = Field(description="Based on gaps")
    notes: str = Field(description="Explanation in Human Readbale form")
    category: Literal["economic"] = Field(default="economic", description="Finding category for evaluation matching")
    human_review_required: bool = Field(default=False, description="Whether human review is required")

class AuditFindings(BaseModel):
    partner_id: str = Field(description="Who this audit is for")
    audit_date: date = Field(description="When the audit ran")
    contract_compliance_findings: List[ContractComplianceFinding] = Field(default_factory=list, description="All contract violations")
    legal_risk_findings: List[LegalRiskFinding] = Field(default_factory=list, description="All legal risks")
    economic_concern_findings: List[EconomicConcernFinding] = Field(default_factory=list, description="All economic concerns")
    evidence_gaps: List[str] = Field(default_factory=list, description="Missing data such as (e.g: wait_time_minutes missing for 12 transactions)")
    overall_grade: FindingGrade = Field(description="Highest severity across all findings")
    summary: str = Field(description="Human readable executive summary")

class PartnerProfile(BaseModel):
    partner_id: str = Field(description="Unique ID")
    name: str = Field(description="Worker name")
    phone: str = Field(description="Contact")
    vehicle_type: Literal["bike","scooter","EV","bicycle","walk"] = Field(description="Vehicle Category")
    petrol_per_km: Decimal = Field(description="Rupees per km based on Vehicle and Local Fuel Price")
    zone: str = Field(description="City zones such as Bannerghatta, Jaynagara, JP Nagara, Rajajinagara")
    platform_accounts: List[str] = Field(description="In which platforms the delivery partner is working in ")
    bank_account_last4: Optional[str] = Field(default = None, description="For payout verification.")
    active_since: date = Field(description="When they started.")

class FairPayState(BaseModel):
    partner_id: str = Field(description="Who we are Auditing")
    partner_profile: PartnerProfile = Field(description="Nested Partner's Profile form class PartnerProfile")
    transactions: List[TransactionRecord] = Field(default_factory=list, description="All reconstructed transactions")
    contract_versions: List[ContractVersion] = Field(default_factory=list, description="Applicable contract versions")
    applicable_laws: List[LegalBasis] = Field(default_factory=list, description="Law matched per transaction")
    payment_calculations: List[PaymentCalculation] = Field(default_factory=list, description="Per-transaction payment analysis")
    business_cost_calculations: List[BusinessCostCalculation] = Field(default_factory=list, description="Per-Transaction cost analysis")
    audit_findings: AuditFindings = Field(default = None, description="Aggregated findings container")
    evidence_gaps: List[str] = Field(default_factory=list, description="Missing data flags")
    classification_probability: Decimal = Field(ge=0,le=1,description="Probability of being legally classified as an employee (vs contractor)")
    counterfactual_scenarios: Optional[List[Dict]] = Field(default=None, description="For debate system(Phase 2)")
    reconciliation_predictions: List[Dict[str, Any]] = Field(default_factory=list, description="Shared reconciliation outputs")

    # Layer 3: Controller outputs
    controller_decisions: List[ControllerDecision] = Field(default_factory=list, description="Per-transaction operational decisions")
    batch_summary: Optional[BatchSummary] = Field(default=None, description="Aggregate operational summary")

    status: Literal["ingesting","extracting","calculating","finding","deciding","complete","error"] = Field(description="Pipeline stage")
    error: Optional[str] = Field(default=None, description="Error message if failed")
