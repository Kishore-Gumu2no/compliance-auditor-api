from __future__ import annotations
from typing import List, Optional
from datetime import date
from decimal import Decimal
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct, Filter, FieldCondition, MatchValue, MatchAny, Range, PayloadSchemaType
from fastembed import TextEmbedding
import os
import json
import time
from dotenv import load_dotenv

from models import ContractVersion, LegalBasis, TransactionRecord, PartnerProfile

load_dotenv()

# Lazy initialization to avoid import-time failures
_client = None
_embedding_model = None
_laws_loaded = False

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

def _ensure_laws_loaded():
    global _laws_loaded, laws, points
    if not _laws_loaded:
        import pathlib
        _LAWS_PATH = pathlib.Path(__file__).with_name("real_laws.json")
        with open(_LAWS_PATH) as f:
            laws = json.load(f)

        embedding_model = _get_embedding_model()
        points = []
        for i, law in enumerate(laws):
            text_to_embed = [law["text"]]
            vector = list(embedding_model.embed(text_to_embed))[0]
            # Add timestamp for date filtering - use datetime to avoid mktime overflow
            payload = dict(law)
            try:
                eff_date = date.fromisoformat(law.get("effective_from", "1900-01-01"))
                # Use datetime.timestamp() which handles wider range
                from datetime import datetime
                eff_dt = datetime(eff_date.year, eff_date.month, eff_date.day)
                payload["effective_from_ts"] = int(eff_dt.timestamp())
            except (ValueError, OverflowError, OSError):
                payload["effective_from_ts"] = 0
            points.append(PointStruct(id=i, vector=vector, payload=payload))

        _laws_loaded = True

def _ensure_collection():
    """Ensure Qdrant collection exists with payload indexes. Returns False if Qdrant unavailable."""
    try:
        client = _get_client()
        _ensure_laws_loaded()
        if not client.collection_exists("labor_laws"):
            client.create_collection(
                collection_name="labor_laws",
                vectors_config=VectorParams(size=384, distance=Distance.COSINE)
            )
            # Create payload indexes for filtered fields
            client.create_payload_index(
                collection_name="labor_laws",
                field_name="jurisdiction",
                field_schema=PayloadSchemaType.KEYWORD
            )
            client.create_payload_index(
                collection_name="labor_laws",
                field_name="status",
                field_schema=PayloadSchemaType.KEYWORD
            )
            client.create_payload_index(
                collection_name="labor_laws",
                field_name="applicability",
                field_schema=PayloadSchemaType.KEYWORD
            )
            client.create_payload_index(
                collection_name="labor_laws",
                field_name="effective_from_ts",
                field_schema=PayloadSchemaType.INTEGER
            )
            print("[INFO] Created payload indexes for labor_laws collection")
        client.upsert(collection_name="labor_laws", points=points)
        return True
    except Exception as e:
        # Qdrant unavailable - log and continue without it
        print(f"[WARN] Qdrant unavailable, skipping vector operations: {e}")
        return False

# Initialize on first use
# laws and points will be populated by _ensure_laws_loaded()
laws = []
points = []


def resolve_contract_version(transaction: TransactionRecord, versions: List[ContractVersion]) -> Optional[ContractVersion]:
    """Match transaction to contract version by date."""
    tx_date = transaction.transaction_date
    for version in versions:
        if version.effective_from <= tx_date:
            if version.effective_to is None or version.effective_to >= tx_date:
                return version
    return None


def _zone_to_state(zone: str) -> str:
    """Map known cities/zones to states. All current synthetic data cities are in Karnataka."""
    karnataka_cities = {
        "bengaluru", "bangalore", "mysuru", "mysore", "mangaluru", "mangalore",
        "belagavi", "belgaum", "hubballi", "hubli", "dharwad", "shivamogga", "shimoga"
    }
    normalized = zone.strip().lower()
    return "Karnataka" if normalized in karnataka_cities else "India"


def _build_semantic_query(
    transaction: TransactionRecord,
    partner_profile: PartnerProfile,
    contract_version: Optional[ContractVersion],
    classification_prob: Decimal
) -> str:
    """Build semantic query from legal-concept signals only."""
    concepts = []

    # Worker classification → legal framework
    if classification_prob > Decimal("0.7"):
        concepts.extend(["employee", "minimum wage", "overtime", "provident fund", "esi", "wage protection"])
    elif classification_prob > Decimal("0.4"):
        concepts.extend(["gig worker", "platform worker", "social security", "welfare board", "aggregator obligations"])
    else:
        concepts.extend(["independent contractor", "commercial contract", "service agreement", "payment terms"])

    # Platform-specific regulatory context
    concepts.append(transaction.platform.lower())

    # Transaction-type signals from actual data
    if transaction.wait_time_minutes and transaction.wait_time_minutes > 30:
        concepts.append("waiting time compensation")
    if any(d.category == "penalty" for d in transaction.deductions):
        concepts.append("penalty deduction wage protection")
    if transaction.incentives:
        concepts.append("incentive bonus entitlement")
    if transaction.distance_km and transaction.distance_km > 0:
        concepts.append("distance allowance per kilometer")

    # Contract clauses ONLY if they express legal concepts (not rates)
    if contract_version:
        legal_clause_keywords = ["termination", "deactivation", "notice", "grievance", "dispute",
                                  "transparency", "algorithm", "discrimination", "safety", "welfare"]
        for clause in contract_version.clauses:
            if any(kw in clause.lower() for kw in legal_clause_keywords):
                concepts.append(clause)

    return " ".join(concepts)


def _build_qdrant_filter(
    transaction: TransactionRecord,
    partner_city: str,
    classification_prob: Decimal
) -> Filter:
    """Build exact metadata filters for Qdrant."""
    jurisdiction = _zone_to_state(partner_city)
    # Convert date to timestamp for Qdrant range filter - use datetime to avoid overflow
    from datetime import datetime
    tx_dt = datetime(transaction.transaction_date.year, transaction.transaction_date.month, transaction.transaction_date.day)
    tx_timestamp = int(tx_dt.timestamp())

    # For classification probability > 0.4, include BOTH employee and gig/platform frameworks
    # because there's genuine ambiguity (ground truth: P=0.55 "creates ambiguity")
    # Below 0.4, default to employee framework
    if classification_prob > Decimal("0.4"):
        applicability_values = [
            "employee_wage_framework_subject_to_applicability",
            "gig_and_platform_worker_social_security",
            "platform_based_gig_workers",
        ]
    else:
        applicability_values = [
            "employee_wage_framework_subject_to_applicability",
        ]

    # Qdrant filter: include both state-specific laws AND Central Acts (jurisdiction="India")
    # Constitutional principles excluded - they are interpretive, not actionable obligations
    # We use should with min_should_match=1 to accept either jurisdiction
    from qdrant_client.models import FieldCondition, MatchValue, MatchAny, Range, Filter

    return Filter(
        must=[
            FieldCondition(key="status", match=MatchValue(value="in_force")),
            FieldCondition(key="effective_from_ts", range=Range(lte=tx_timestamp)),
            FieldCondition(key="applicability", match=MatchAny(any=applicability_values))
        ],
        should=[
            FieldCondition(key="jurisdiction", match=MatchValue(value=jurisdiction)),
            FieldCondition(key="jurisdiction", match=MatchValue(value="India"))
        ],
        min_should_match=1
    )


def _filter_laws_in_memory(
    transaction: TransactionRecord,
    partner_city: str,
    classification_prob: Decimal
) -> List[dict]:
    """In-memory fallback with identical filtering logic."""
    _ensure_laws_loaded()
    state = _zone_to_state(partner_city)
    tx_date = transaction.transaction_date

    # For classification probability > 0.4, include BOTH employee and gig/platform frameworks
    # because there's genuine ambiguity (ground truth: P=0.55 "creates ambiguity")
    if classification_prob > Decimal("0.4"):
        applicability_values = [
            "employee_wage_framework_subject_to_applicability",
            "gig_and_platform_worker_social_security",
            "platform_based_gig_workers",
        ]
    else:
        applicability_values = [
            "employee_wage_framework_subject_to_applicability",
        ]

    filtered = []
    for law in laws:
        law_jurisdiction = law.get("jurisdiction", "")
        # Accept laws for this state OR Central Acts (India)
        if law_jurisdiction != state and law_jurisdiction != "India":
            continue
        if law.get("status") != "in_force":
            continue
        # Parse effective_from date
        try:
            eff_from = date.fromisoformat(law.get("effective_from", "1900-01-01"))
        except ValueError:
            eff_from = date(1900, 1, 1)
        if eff_from > tx_date:
            continue
        if law.get("applicability") not in applicability_values:
            continue
        filtered.append(law)
    return filtered


def _payload_to_legal_basis(payload: dict, classification_prob: Decimal) -> LegalBasis:
    """Convert full Qdrant payload to LegalBasis preserving all fields."""
    # Parse effective_date from effective_from
    try:
        effective_date = date.fromisoformat(payload.get("effective_from", "1900-01-01"))
    except ValueError:
        effective_date = date(payload.get("year", 1900), 1, 1)

    return LegalBasis(
        statute=payload.get("statute", ""),
        section=payload.get("section", ""),
        jurisdiction=payload.get("jurisdiction", ""),
        effective_date=effective_date,
        applicability_note=f"{payload.get('applicability', '')} (P(employee)={classification_prob})",
        source_type=payload.get("source_type", ""),
        applicability_category=payload.get("applicability", ""),
        full_text=payload.get("text", ""),
        law_id=payload.get("id", ""),
        year=payload.get("year", 0),
        status=payload.get("status", "in_force"),
        version=payload.get("version", "original"),
        parent_act=payload.get("parent_act", "")
    )


def resolve_applicable_laws(
    transaction: TransactionRecord,
    classification_prob: Decimal,
    partner_profile: Optional[PartnerProfile] = None,
    contract_version: Optional[ContractVersion] = None
) -> List[LegalBasis]:
    """Query Qdrant for laws applicable to transaction date and classification."""
    # Build semantic query and metadata filters
    partner_city = partner_profile.zone if partner_profile else "Bengaluru"
    semantic_query = _build_semantic_query(transaction, partner_profile, contract_version, classification_prob)
    metadata_filter = _build_qdrant_filter(transaction, partner_city, classification_prob)

    # Try Qdrant first
    if _ensure_collection():
        query_vector = list(_get_embedding_model().embed([semantic_query]))[0]
        results = _get_client().query_points(
            collection_name="labor_laws",
            query=query_vector,
            query_filter=metadata_filter,
            limit=5
        ).points

        legal_bases = []
        for result in results:
            legal_bases.append(_payload_to_legal_basis(result.payload, classification_prob))

        if legal_bases:
            print(f"[INFO] Qdrant retrieved {len(legal_bases)} laws for {transaction.transaction_id}: "
                  f"{[lb.law_id for lb in legal_bases]}")
            return legal_bases
        else:
            print(f"[WARN] Qdrant returned 0 results for {transaction.transaction_id} "
                  f"(query='{semantic_query[:80]}...', filter: jurisdiction={_zone_to_state(partner_city)}, "
                  f"applicability={metadata_filter})")
            # Fall through to in-memory fallback

    # Fallback: in-memory with same filtering logic
    print(f"[INFO] Using in-memory fallback for {transaction.transaction_id}")
    filtered_laws = _filter_laws_in_memory(transaction, partner_city, classification_prob)
    legal_bases = [_payload_to_legal_basis(law, classification_prob) for law in filtered_laws]
    print(f"[INFO] In-memory retrieved {len(legal_bases)} laws for {transaction.transaction_id}: "
          f"{[lb.law_id for lb in legal_bases]}")
    return legal_bases


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
# Local Law Fixture for Synthetic Verification (No Qdrant)
# ============================================================

_local_laws_cache: Optional[List[dict]] = None

def _load_local_laws() -> List[dict]:
    """Load laws from real_laws.json for local deterministic use (no Qdrant)."""
    global _local_laws_cache
    if _local_laws_cache is None:
        import pathlib
        _LAWS_PATH = pathlib.Path(__file__).with_name("real_laws.json")
        with open(_LAWS_PATH) as f:
            _local_laws_cache = json.load(f)
    return _local_laws_cache


def resolve_applicable_laws_local(
    transaction: TransactionRecord,
    classification_prob: Decimal,
    partner_profile: Optional[PartnerProfile] = None,
    contract_version: Optional[ContractVersion] = None
) -> List[LegalBasis]:
    """
    Local deterministic law resolution for synthetic verification.
    Uses real_laws.json directly with same filtering logic as Qdrant path.
    NO network calls.

    Fixes for synthetic verification:
    - Central Acts (jurisdiction="India") apply nationwide, include them for all states
    - No arbitrary limit of 5 - return all matching laws for complete coverage
    - Constitutional principles are interpretive aids, not primary obligations - exclude by default
    """
    laws_data = _load_local_laws()

    # Build same filters as Qdrant path
    partner_city = partner_profile.zone if partner_profile else "Bengaluru"
    state = _zone_to_state(partner_city)
    tx_date = transaction.transaction_date

    # Central Acts (jurisdiction="India") apply nationwide - include for all states.
    # A transaction on an account registered to the partner is direct evidence
    # that the worker is a platform worker.  It is independent of the
    # employee-classification estimate, so retain the platform-worker
    # framework even when that estimate is below the ambiguity threshold.
    is_known_platform_worker = (
        partner_profile is not None
        and transaction.platform.lower()
        in {account.lower() for account in partner_profile.platform_accounts}
    )

    # Constitutional principles are interpretive, not actionable obligations - exclude.
    # For classification probability > 0.4, include BOTH employee and gig/platform frameworks
    # because there's genuine ambiguity.  Otherwise retain the employee
    # framework and add gig/platform laws only with the direct evidence above.
    if classification_prob > Decimal("0.4"):
        applicability_values = [
            "employee_wage_framework_subject_to_applicability",
            "gig_and_platform_worker_social_security",
            "platform_based_gig_workers",
        ]
    else:
        applicability_values = [
            "employee_wage_framework_subject_to_applicability",
        ]
        if is_known_platform_worker:
            applicability_values.extend([
                "gig_and_platform_worker_social_security",
                "platform_based_gig_workers",
            ])

    # Apply filtering - include both state-specific AND Central Acts (India)
    filtered = []
    for law in laws_data:
        law_jurisdiction = law.get("jurisdiction", "")
        # Accept laws for this state OR Central Acts (India)
        if law_jurisdiction != state and law_jurisdiction != "India":
            continue
        if law.get("status") != "in_force":
            continue
        try:
            eff_from = date.fromisoformat(law.get("effective_from", "1900-01-01"))
        except ValueError:
            eff_from = date(1900, 1, 1)
        if eff_from > tx_date:
            continue
        if law.get("applicability") not in applicability_values:
            continue
        filtered.append(law)

    # Return ALL matching laws (no arbitrary limit) for complete legal coverage
    legal_bases = [_payload_to_legal_basis(law, classification_prob) for law in filtered]
    return legal_bases
