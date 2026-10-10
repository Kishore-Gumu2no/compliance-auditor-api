from __future__ import annotations
from typing import Any, Dict, List, Optional
from decimal import Decimal
from qdrant_client import QdrantClient
from fastembed import TextEmbedding
import os
from dotenv import load_dotenv

from models import ContractVersion, LegalBasis, TransactionRecord, PartnerProfile
from law_store import LawStore, laws_in_force, seeded_local_store, state_for_zone

load_dotenv()

# Lazy initialization to avoid import-time failures
_client = None
_embedding_model = None

def _get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(
            url=os.getenv("QDRANT_URL"),
            api_key=os.getenv("QDRANT_API_KEY")
        )
    return _client

def _get_embedding_model() -> TextEmbedding:
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = TextEmbedding("BAAI/bge-small-en-v1.5")
    return _embedding_model


def resolve_contract_version(transaction: TransactionRecord, versions: List[ContractVersion]) -> Optional[ContractVersion]:
    """Match transaction to contract version by date."""
    tx_date = transaction.transaction_date
    for version in versions:
        if version.effective_from <= tx_date:
            if version.effective_to is None or version.effective_to >= tx_date:
                return version
    return None


def extract_classification_probability(transactions: List[TransactionRecord]) -> Decimal:
    """Extract control indicators to estimate P(employee)."""
    if not transactions:
        return Decimal("0.5")

    # Indicators from transaction patterns
    platforms = set(tx.platform for tx in transactions)
    total_tx = len(transactions)

    # Simple heuristic: more platforms = more contractor-like
    platform_diversity = min(len(platforms) / 3, 1.0)

    # Regular schedule = more employee-like
    dates = [tx.transaction_date for tx in transactions]
    unique_dates = len(set(dates))
    regularity = 1.0 - (unique_dates / max(total_tx, 1))

    # Base probability weighted
    prob = Decimal("0.5") + Decimal(str(regularity * 0.3)) - Decimal(str(platform_diversity * 0.2))
    return max(Decimal("0.1"), min(Decimal("0.9"), prob))


# ============================================================
# Laws: read from Qdrant (law_store), chosen per trip by date
# ============================================================
# Qdrant is the only source of laws at run time. real_laws.json is a seed
# file for law_kb_ingest.py; it is never written to Qdrant by the audit.

def _applicability_values(
    transaction: TransactionRecord,
    classification_prob: Decimal,
    partner_profile: Optional[PartnerProfile] = None,
) -> List[str]:
    """Which kinds of law can apply, from the worker-classification evidence.

    Constitutional principles are interpretive, not actionable obligations,
    so they are never selected. For P(employee) > 0.4 both the employee and the
    gig/platform frameworks apply (genuine ambiguity). Below that, the employee
    framework is kept and the gig/platform laws are added only when the trip is
    on an account registered to the partner - direct evidence of platform work.
    """
    if classification_prob > Decimal("0.4"):
        return [
            "employee_wage_framework_subject_to_applicability",
            "gig_and_platform_worker_social_security",
            "platform_based_gig_workers",
        ]
    values = ["employee_wage_framework_subject_to_applicability"]
    is_known_platform_worker = (
        partner_profile is not None
        and transaction.platform.lower()
        in {account.lower() for account in partner_profile.platform_accounts}
    )
    if is_known_platform_worker:
        values.extend(["gig_and_platform_worker_social_security", "platform_based_gig_workers"])
    return values


def _payload_to_legal_basis(payload: dict, classification_prob: Decimal) -> LegalBasis:
    """Convert a law document from Qdrant to LegalBasis, keeping every field."""
    from datetime import date

    try:
        effective_date = date.fromisoformat(payload.get("effective_from", "1900-01-01"))
    except ValueError:
        effective_date = date(payload.get("year", 1900), 1, 1)
    try:
        effective_to = date.fromisoformat(payload["effective_to"]) if payload.get("effective_to") else None
    except ValueError:
        effective_to = None

    return LegalBasis(
        statute=payload.get("statute", ""),
        section=payload.get("section", ""),
        jurisdiction=payload.get("jurisdiction", ""),
        effective_date=effective_date,
        effective_to=effective_to,
        applicability_note=f"{payload.get('applicability', '')} (P(employee)={classification_prob})",
        source_type=payload.get("source_type", ""),
        applicability_category=payload.get("applicability", ""),
        full_text=payload.get("text", ""),
        law_id=payload.get("id", ""),
        year=payload.get("year", 0),
        status=payload.get("status", "in_force"),
        version=payload.get("version", "original"),
        parent_act=payload.get("parent_act", ""),
        concepts=list(payload.get("concepts") or []),
        parameters=dict(payload.get("parameters") or {}),
    )


def laws_for_transaction(
    transaction: TransactionRecord,
    classification_prob: Decimal,
    partner_profile: Optional[PartnerProfile],
    laws: List[Dict[str, Any]],
    zones: Dict[str, str],
) -> List[LegalBasis]:
    """Every provision for the partner's state (or nationwide) in force on the trip's date.

    Selection is by filter, never by similarity: nothing that applies is
    left out.
    """
    partner_city = partner_profile.zone if partner_profile else "Bengaluru"
    state = state_for_zone(partner_city, zones)
    chosen = laws_in_force(laws, state, transaction.transaction_date,
                           _applicability_values(transaction, classification_prob, partner_profile))
    return [_payload_to_legal_basis(law, classification_prob) for law in chosen]


def resolve_applicable_laws(
    transaction: TransactionRecord,
    classification_prob: Decimal,
    partner_profile: Optional[PartnerProfile] = None,
    contract_version: Optional[ContractVersion] = None,
    store: Optional[LawStore] = None,
) -> List[LegalBasis]:
    """Laws for one trip, read from the Qdrant law store. Raises LawStoreUnavailable."""
    store = store or LawStore()
    return laws_for_transaction(transaction, classification_prob, partner_profile,
                                store.current_laws(), store.zone_map())


def resolve_applicable_laws_local(
    transaction: TransactionRecord,
    classification_prob: Decimal,
    partner_profile: Optional[PartnerProfile] = None,
    contract_version: Optional[ContractVersion] = None
) -> List[LegalBasis]:
    """Laws for one trip from an in-memory Qdrant holding the seed files.

    Same store and selection code as production, with no network - for the
    synthetic verification and tests.
    """
    return resolve_applicable_laws(transaction, classification_prob, partner_profile, contract_version,
                                   store=seeded_local_store())
