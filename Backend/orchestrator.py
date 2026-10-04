from __future__ import annotations
from langgraph.graph import StateGraph, START, END
from typing import List, Set
from decimal import Decimal

from models import FairPayState, PartnerProfile, LegalBasis
from evidence_extractor import process_evidence, EvidenceExtractionError
from versioning import (
    resolve_contract_version,
    resolve_applicable_laws,
    resolve_applicable_laws_local,
    extract_classification_probability
)
from calculations import calculate_payments, calculate_business_costs
from findings import (
    analyze_contract_compliance,
    analyze_legal_risks,
    analyze_economic_concerns,
    aggregate_findings
)
from controller import controller_node
from real_data import normalize_real_records


def evidence_node(state: FairPayState) -> FairPayState:
    try:
        records, extraction_gaps, work_time = process_evidence(
            state.pdf_paths,
            state.image_paths,
            state.platform,
        )
    except EvidenceExtractionError as exc:
        return {
            **state.model_dump(),
            "status": "error",
            "error": str(exc),
            "evidence_gaps": list(state.evidence_gaps) + [str(exc)],
        }

    normalized = normalize_real_records(records)
    return {
        **state.model_dump(),
        "transactions": normalized["transactions"],
        "reconciliation_payment_calculations": normalized["payment_calculations"],
        "reconciliation_predictions": normalized["predictions"],
        "evidence_gaps": extraction_gaps + normalized["evidence_gaps"],
        "work_time": work_time,
    }


def error_node(state: FairPayState) -> FairPayState:
    return {
        **state.model_dump(),
        "status": "error",
        "error": state.error or "Audit pipeline failed before completion.",
    }


def versioning_node(state: FairPayState) -> FairPayState:
    transactions = state.transactions
    contract_versions = state.contract_versions
    partner_profile = state.partner_profile

    resolved_contracts = []
    for tx in transactions:
        cv = resolve_contract_version(tx, contract_versions)
        if cv:
            resolved_contracts.append(cv)

    classification_prob = extract_classification_probability(transactions)
    # Store laws per transaction to preserve association for legal reasoning
    applicable_laws_per_tx = []
    for tx in transactions:
        tx_contract = resolve_contract_version(tx, contract_versions)
        # Use local law resolution for synthetic verification (no Qdrant network calls)
        # Check if we're in test/synthetic mode via state flag or environment
        use_local = state.use_local_laws
        if use_local:
            laws = resolve_applicable_laws_local(tx, classification_prob, partner_profile, tx_contract)
        else:
            laws = resolve_applicable_laws(tx, classification_prob, partner_profile, tx_contract)
        applicable_laws_per_tx.append({
            "transaction_id": tx.transaction_id,
            "laws": laws
        })

    # Also keep flat list for backward compatibility
    flat_laws = []
    for entry in applicable_laws_per_tx:
        flat_laws.extend(entry["laws"])

    return {
        **state.model_dump(),
        "contract_versions": resolved_contracts,
        "applicable_laws": flat_laws,
        "applicable_laws_per_tx": applicable_laws_per_tx,
        "classification_probability": classification_prob
    }


def calculation_node(state: FairPayState) -> FairPayState:
    transactions = state.transactions
    contract_versions = state.contract_versions
    partner_profile = state.partner_profile

    payment_calcs = state.reconciliation_payment_calculations or calculate_payments(transactions, contract_versions)
    business_cost_calcs = []

    if partner_profile:
        business_cost_calcs = calculate_business_costs(transactions, partner_profile, payment_calcs)

    return {
        **state.model_dump(),
        "payment_calculations": payment_calcs,
        "business_cost_calculations": business_cost_calcs
    }


def findings_node(state: FairPayState) -> FairPayState:
    payment_calcs = state.payment_calculations
    business_cost_calcs = state.business_cost_calculations
    transactions = state.transactions
    applicable_laws = state.applicable_laws
    applicable_laws_per_tx = [
        {
            **entry,
            "laws": [LegalBasis.model_validate(law) for law in entry.get("laws", [])],
        }
        for entry in state.applicable_laws_per_tx
    ]
    classification_prob = state.classification_probability
    evidence_gaps = state.evidence_gaps

    compliance = analyze_contract_compliance(payment_calcs, transactions)
    legal = analyze_legal_risks(transactions, applicable_laws_per_tx, classification_prob, payment_calcs)
    economic = analyze_economic_concerns(business_cost_calcs, transactions, payment_calcs)

    audit_findings = aggregate_findings(compliance, legal, economic, evidence_gaps)
    audit_findings.partner_id = state.partner_id

    return {**state.model_dump(), "audit_findings": audit_findings}


def route_from_start(state: FairPayState) -> str:
    if state.transactions:
        return "versioning"
    if state.pdf_paths or state.image_paths:
        return "evidence"
    return "error"


def route_after_evidence(state: FairPayState) -> str:
    if state.status == "error":
        return "error"
    return "versioning"


# Build the graph
workflow = StateGraph(FairPayState)

workflow.add_node("evidence", evidence_node)
workflow.add_node("error", error_node)
workflow.add_node("versioning", versioning_node)
workflow.add_node("calculation", calculation_node)
workflow.add_node("findings", findings_node)
workflow.add_node("controller", controller_node)

workflow.add_conditional_edges(START, route_from_start, {"evidence": "evidence", "error": "error", "versioning": "versioning"})
workflow.add_conditional_edges("evidence", route_after_evidence, {"error": "error", "versioning": "versioning"})
workflow.add_edge("error", END)
workflow.add_edge("versioning", "calculation")
workflow.add_edge("calculation", "findings")
workflow.add_edge("findings", "controller")
workflow.add_edge("controller", END)

app = workflow.compile()


def run_audit(
    partner_id: str,
    partner_profile: PartnerProfile,
    pdf_paths: List[str],
    image_paths: List[str],
    platform: str,
    contract_versions: List,
    transactions: List = None,
    reconciliation_predictions: List = None,
    reconciliation_payment_calculations: List = None,
) -> FairPayState:
    initial_state: FairPayState = {
        "partner_id": partner_id,
        "partner_profile": partner_profile,
        "pdf_paths": pdf_paths,
        "image_paths": image_paths,
        "platform": platform,
        "contract_versions": contract_versions,
        "transactions": transactions or [],
        "applicable_laws": [],
        "payment_calculations": [],
        "business_cost_calculations": [],
        "audit_findings": None,
        "evidence_gaps": [],
        "classification_probability": Decimal("0.5"),
        "counterfactual_scenarios": None,
        "reconciliation_predictions": reconciliation_predictions or [],
        "reconciliation_payment_calculations": reconciliation_payment_calculations or [],
        "use_local_laws": True,
        "status": "ingesting",
        "error": None,
    }
    final_state = app.invoke(initial_state)
    return final_state
