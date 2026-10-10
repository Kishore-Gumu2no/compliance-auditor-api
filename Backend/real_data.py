"""Normalization of extracted real-world earning and settlement records."""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, Literal
import os
import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from models import (
    ContractVersion,
    Deduction,
    Incentive,
    PartnerProfile,
    PaymentCalculation,
    TransactionRecord,
)
from reconciliation import DATE_TOLERANCE_DAYS, AnomalyType, classify_single

from dotenv import load_dotenv

load_dotenv()

# Database connection settings come from the environment. The defaults are the
# local values used when running directly on the host (Postgres published on
# localhost:54322); docker-compose overrides them with the `db` service.
conn = psycopg.connect(
    host=os.getenv("DB_HOST", "localhost"),
    port=int(os.getenv("DB_PORT", "54322")),
    dbname=os.getenv("DB_NAME", "postgres"),
    user=os.getenv("DB_USER", "postgres"),
    password=os.getenv("DB_PASSWORD", "postgres"),
)

class ExtractedFinancialRecord(BaseModel):
    record_type: Literal["earning", "settlement"]
    record_id: str
    transaction_id: str  # TXN-... for earnings, STL-... for settlements
    platform: str
    order_id: str
    record_date: date
    amount: Decimal = Field(ge=0, description="Expected earning or actual settlement amount")
    gross_amount: Decimal | None = Field(default=None, ge=0)
    incentive: Decimal | None = Field(default=None, ge=0)
    bonus: Decimal | None = Field(default=None, ge=0)
    deduction: Decimal | None = Field(default=None, ge=0)
    deduction_policy_reference: str | None = None
    distance_km: Decimal | None = Field(default=None, ge=0)
    duration_minutes: int | None = Field(default=None, ge=0)
    wait_time_minutes: int | None = Field(default=None, ge=0)
    contract_version: str | None = None


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _classifier_earning_payload(earning: ExtractedFinancialRecord) -> dict[str, Any]:
    """Payload for shared classify_single; omit absent source fields."""
    payload: dict[str, Any] = {
        "date": earning.record_date.isoformat(),
        "expected_amount": earning.amount,
    }
    if earning.incentive is not None:
        payload["incentive"] = earning.incentive
    if earning.bonus is not None:
        payload["bonus"] = earning.bonus
    if earning.deduction is not None:
        payload["deduction"] = earning.deduction
    return payload


def normalize_real_records(records: list[ExtractedFinancialRecord]) -> dict[str, Any]:
    """Build existing engine inputs without fabricating absent source fields."""
    earnings = [r for r in records if r.record_type == "earning"]
    settlements = [r for r in records if r.record_type == "settlement"]
    if not earnings or not settlements:
        missing = "earnings" if not earnings else "settlements"
        return {
            "transactions": [], "payment_calculations": [], "predictions": [],
            "evidence_gaps": [f"Insufficient reconciliation evidence: no {missing} records were extracted."],
        }

    by_key: dict[tuple[str, str], list[ExtractedFinancialRecord]] = defaultdict(list)
    for settlement in settlements:
        by_key[(settlement.order_id, settlement.platform)].append(settlement)

    transactions: list[TransactionRecord] = []
    calculations: list[PaymentCalculation] = []
    predictions: list[dict[str, Any]] = []
    gaps: list[str] = []
    matched_ids: set[str] = set()

    for earning in earnings:
        matches = [
            settlement for settlement in by_key[(earning.order_id, earning.platform)]
            if abs((settlement.record_date - earning.record_date).days) <= DATE_TOLERANCE_DAYS
        ]
        earning_payload = _classifier_earning_payload(earning)
        if not matches:
            gaps.append(f"No settlement found for earning {earning.record_id}.")
            transactions.append(_transaction_from_earning(earning, Decimal("0"), earning.transaction_id))
            calculations.append(PaymentCalculation(platform_net=Decimal("0"), contractual_net=earning.amount))
            predictions.append(_prediction(earning, earning.transaction_id, AnomalyType.MISSING_PAYMENT, Decimal("0")))
            continue

        settlement = min(matches, key=lambda s: abs(s.amount - earning.amount))
        matched_ids.update(s.record_id for s in matches)
        if len(matches) > 1:
            total = sum((s.amount for s in matches), Decimal("0"))
            transactions.append(_transaction_from_earning(earning, total, earning.transaction_id))
            calculations.append(PaymentCalculation(platform_net=total, contractual_net=earning.amount))
            predictions.append(_prediction(earning, earning.transaction_id, AnomalyType.DUPLICATE_SETTLEMENT, total))
            continue

        settlement_payload = {"date": settlement.record_date.isoformat(), "amount_paid": settlement.amount}
        anomaly = classify_single(settlement_payload, earning_payload, _date)
        transactions.append(_transaction_from_earning(earning, settlement.amount, earning.transaction_id))
        calculations.append(PaymentCalculation(platform_net=settlement.amount, contractual_net=earning.amount))
        predictions.append(_prediction(earning, earning.transaction_id, anomaly, settlement.amount))

    for settlement in settlements:
        if settlement.record_id not in matched_ids:
            gaps.append(f"Settlement {settlement.record_id} has no matching earning record.")
            # Find the earning for this settlement to get transaction_id
            txn_id = settlement.record_id  # fallback
            predictions.append({"transaction_id": txn_id, "order_id": settlement.order_id,
                                "anomaly_type": AnomalyType.EXTRA_UNEXPLAINED_SETTLEMENT.value,
                                "expected_amount": "0", "settlement_amount": str(settlement.amount)})
    return {"transactions": transactions, "payment_calculations": calculations,
            "predictions": predictions, "evidence_gaps": gaps}


def _transaction_from_earning(earning: ExtractedFinancialRecord, paid: Decimal, transaction_id: str) -> TransactionRecord:
    deductions = []
    if earning.deduction is not None:
        deductions.append(Deduction(name="documented_deduction", amount=earning.deduction,
                                    policy_reference=earning.deduction_policy_reference or "", category="fee"))
    incentives: list[Incentive] = []
    if earning.incentive is not None:
        incentives.append(Incentive(name="documented_incentive", amount=earning.incentive,
                                    policy_reference="", adherence_rate=None))
    if earning.bonus is not None:
        incentives.append(Incentive(name="documented_bonus", amount=earning.bonus,
                                    policy_reference="", adherence_rate=None))
    return TransactionRecord(
        transaction_id=transaction_id, platform=earning.platform, transaction_date=earning.record_date,
        gross_amount=earning.gross_amount if earning.gross_amount is not None else earning.amount,
        deductions=deductions, incentives=incentives, net_amount=paid,
        distance_km=earning.distance_km, duration_minutes=earning.duration_minutes,
        wait_time_minutes=earning.wait_time_minutes, contract_version=earning.contract_version,
    )


def _prediction(earning: ExtractedFinancialRecord, transaction_id: str, anomaly: AnomalyType, paid: Decimal) -> dict[str, Any]:
    return {"transaction_id": transaction_id, "earning_id": earning.record_id, "order_id": earning.order_id,
            "anomaly_type": anomaly.value, "expected_amount": str(earning.amount),
            "settlement_amount": str(paid), "variance": str(paid - earning.amount)}


# =====================================================================
# Load partner history from PostgreSQL for Milestone 2 CTA
# =====================================================================


def load_partner_profile(partner_id: str) -> PartnerProfile:
    """Build a PartnerProfile from the partners table.

    Shared by the 30-day history loader and by submission channels (such as
    Telegram) that have a partner_id but no client-supplied profile JSON.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM partners WHERE partner_id = %s",
            (partner_id,)
        )
        partner_row = cur.fetchone()
    if not partner_row:
        raise ValueError(f"Partner {partner_id} not found")

    # Normalize vehicle_type: "MOTORCYCLE" → "bike"
    _vehicle_map = {"MOTORCYCLE": "bike"}
    vehicle_type = _vehicle_map.get(partner_row["vehicle_type"], partner_row["vehicle_type"])

    # Normalize platform_accounts: JSONB dict → list of account ID values
    platform_accounts = list(partner_row["platform_accounts"].values())

    return PartnerProfile(
        partner_id=partner_row["partner_id"],
        name=partner_row["name"],
        phone=partner_row["phone"],
        vehicle_type=vehicle_type,
        propulsion_type=partner_row["propulsion_type"],
        petrol_per_km=Decimal(str(partner_row["petrol_per_km"])),
        zone=partner_row["zone"],
        platform_accounts=platform_accounts,
        bank_account_last4=partner_row["bank_account_last4"],
        active_since=partner_row["active_since"],
    )


# Contract builders shared by the 30-day history loader and the evidence-mode
# layer (contract terms on file for a platform).

def _populate_contract_params(cv: ContractVersion, rules: list[dict]):
    """Populate structured contract parameters from platform_rules."""
    for rule in rules:
        rule_type = rule.get("rule_type")
        params = rule.get("parameters", {})
        if rule_type == "BASE_PAY":
            cv.base_pay_per_order = Decimal(str(params.get("amount", 0)))
        elif rule_type == "DISTANCE":
            cv.distance_rate_per_km = Decimal(str(params.get("rate_per_km", 0)))
        elif rule_type == "WAIT":
            cv.wait_free_minutes = params.get("free_minutes", 0)
            cv.wait_rate_per_min = Decimal(str(params.get("rate_per_min", 0)))
            cv.wait_max_billable_minutes = params.get("max_billable_minutes", 0)
        elif rule_type == "INCENTIVE_PEAK":
            cv.peak_amount = Decimal(str(params.get("amount", 0)))
            cv.peak_windows = params.get("windows", [])
        elif rule_type == "INCENTIVE_WEEKEND":
            cv.weekend_amount = Decimal(str(params.get("amount", 0)))
            cv.weekend_days = params.get("days", [])
        elif rule_type == "BONUS_DAILY_TARGET":
            cv.daily_threshold = params.get("threshold_orders", 0)
            cv.daily_bonus = Decimal(str(params.get("amount", 0)))
        elif rule_type == "BONUS_WEEKLY_TARGET":
            cv.weekly_threshold = params.get("threshold_orders", 0)
            cv.weekly_bonus = Decimal(str(params.get("amount", 0)))
        elif rule_type == "DEDUCTION_FEE":
            calc = params.get("calc", "")
            if calc == "PERCENT_OF_BASE_DISTANCE_WAIT":
                cv.platform_fee_rate = Decimal(str(params.get("rate", 0)))
                cv.platform_fee_name = params.get("name", "Platform Service Fee")
            elif calc == "FLAT_PER_ORDER":
                cv.insurance_amount = Decimal(str(params.get("amount", 0)))
                cv.insurance_name = params.get("name", "Accident Insurance Premium")
        elif rule_type == "DEDUCTION_PENALTY":
            cv.penalty_trigger_minutes = params.get("sla_minutes", 0)
            cv.penalty_amount = Decimal(str(params.get("amount", 0)))
            cv.penalty_name = params.get("name", "Late Delivery Penalty")
        elif rule_type == "INCENTIVE_CAMPAIGN":
            cv.campaign_amount = Decimal(str(params.get("amount", 0)))
            cv.campaign_name = params.get("campaign", "")
            cv.campaign_weekdays_only = params.get("weekdays_only", False)
            cv.campaign_windows = params.get("windows", [])
            if "min_distance_km" in params:
                cv.campaign_min_distance_km = Decimal(str(params.get("min_distance_km", 0)))

def _normalize_clauses(db_clauses: list[dict]) -> list[str]:
    """Convert database clause objects to engine-compatible string format."""
    normalized = []
    for clause in db_clauses:
        clause_type = clause.get("clause_type", "")
        params = clause.get("parameters", {})
        title = clause.get("title", "")

        if clause_type == "BASE_RATE":
            amount = params.get("amount")
            unit = params.get("unit", "per_order")
            if amount is not None:
                if "per_order" in unit:
                    normalized.append(f"Base pay: {amount}/order")
                elif "per_day" in unit or "daily" in unit:
                    normalized.append(f"Base pay: {amount}/day")
        elif clause_type == "PER_KM_RATE":
            rate = params.get("rate_per_km")
            if rate is not None:
                normalized.append(f"Per km: {rate}")
        elif clause_type == "WAIT_TIME_RATE":
            rate = params.get("rate_per_min")
            free = params.get("free_minutes")
            cap = params.get("max_billable_minutes")
            if rate is not None:
                normalized.append(f"Wait time: {rate}/min (free {free} min, cap {cap} min)")
        elif clause_type == "INCENTIVE":
            amount = params.get("amount")
            incentive_kind = params.get("basis", "incentive")
            if "daily" in incentive_kind or "target" in incentive_kind:
                normalized.append(f"Daily target bonus: {amount}")
            elif "weekly" in incentive_kind:
                normalized.append(f"Weekly target bonus: {amount}")
            elif "campaign" in incentive_kind:
                campaign = params.get("campaign", "campaign")
                normalized.append(f"Campaign incentive ({campaign}): {amount}")
            else:
                normalized.append(f"Incentive: {amount}")
        elif clause_type == "DEDUCTION":
            name = params.get("name", "deduction")
            category = params.get("category", "fee")
            if "calc" in params:
                calc = params["calc"]
                if calc == "PERCENT_OF_BASE_DISTANCE_WAIT":
                    rate = params.get("rate", 0)
                    normalized.append(f"Deduction ({name}, {category}): {rate*100}% of base+distance+wait")
                elif calc == "FLAT_PER_ORDER":
                    amount = params.get("amount", 0)
                    normalized.append(f"Deduction ({name}, {category}): {amount}/order")
            else:
                normalized.append(f"Deduction ({name}, {category})")
    return normalized


def _platform_key(name: str) -> str:
    """'Uber Eats', 'ubereats' and 'UberEats' name the same platform."""
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def load_platform_contracts(platform: str) -> list[dict[str, Any]]:
    """Contract terms on file for one platform, built the same way the history
    audit builds them.

    Returns ``[{"contract": ContractVersion, "title", "status", "source"}]``.
    Draft contracts are left out. An empty list means no terms are on file.
    """
    key = _platform_key(platform)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM contracts WHERE status <> 'DRAFT'")
        rows = [c for c in cur.fetchall() if _platform_key(c["platform"]) == key]
    if not rows:
        return []
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM platform_rules WHERE contract_version = ANY(%s)",
            ([c["version_id"] for c in rows],),
        )
        rules = cur.fetchall() or []
    rules_by_contract = defaultdict(list)
    for rule in rules:
        rules_by_contract[rule["contract_version"]].append(rule)

    result = []
    for c in rows:
        cv = ContractVersion(
            platform=c["platform"],
            version_id=c["version_id"],
            effective_from=c["effective_from"],
            effective_to=c["effective_to"] if c["effective_to"] else None,
            source=c["source"],
            confidence=Decimal(str(c["confidence"])),
            clauses=_normalize_clauses(c["clauses"]),
        )
        _populate_contract_params(cv, rules_by_contract.get(c["version_id"], []))
        result.append({
            "contract": cv,
            "title": c["document_title"],
            "status": c["status"],
            "source": c["source"],
        })
    return result


def load_partner_history_from_db(partner_id: str) -> dict[str, Any]:
    """
    Load a partner's 30-day transaction history from Supabase views/tables
    and build the inputs needed for run_audit().
    """
    # 1. Fetch transaction records from the API-facing view
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM v_transaction_records WHERE partner_id = %s ORDER BY completed_timestamp",
            (partner_id,)
        )
        rows = cur.fetchall()
    tx_rows = rows

    if not tx_rows:
        raise ValueError(f"No transaction records found for partner {partner_id}")

    # 2. Fetch contracts used by this partner's transactions
    platforms = list(set(tx["platform"] for tx in tx_rows))
    contract_versions_used = list(set(tx["contract_version"] for tx in tx_rows))

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM contracts WHERE platform = ANY(%s)",
            (platforms,)
        )
        all_contracts = cur.fetchall()

    # Filter to only the contract versions actually used
    contracts = [c for c in all_contracts if c["version_id"] in contract_versions_used]

    # 3. Fetch platform rules for those contract versions
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM platform_rules WHERE contract_version = ANY(%s)",
            (contract_versions_used,)
        )
        platform_rules = cur.fetchall() or []

    # 4./5. Fetch partner profile (shared loader, single source of truth)
    partner_profile = load_partner_profile(partner_id)

    # 6. Build ContractVersion objects (helpers are module-level, shared)
    contract_versions = []
    # Group platform_rules by contract_version
    rules_by_contract = defaultdict(list)
    for rule in platform_rules:
        rules_by_contract[rule["contract_version"]].append(rule)

    for c in contracts:
        cv = ContractVersion(
            platform=c["platform"],
            version_id=c["version_id"],
            effective_from=c["effective_from"],
            effective_to=c["effective_to"] if c["effective_to"] else None,
            source=c["source"],
            confidence=Decimal(str(c["confidence"])),
            document_title=c["document_title"],
            status=c["status"],
            clauses=_normalize_clauses(c["clauses"]),
        )
        # Populate structured parameters from platform_rules
        _populate_contract_params(cv, rules_by_contract.get(c["version_id"], []))
        contract_versions.append(cv)

    # 7. Build TransactionRecord objects from v_transaction_records
    transactions = []
    for tx in tx_rows:
        # Parse deductions JSONB
        deductions = []
        for d in tx.get("deductions", []):
            deductions.append(Deduction(
                name=d["name"],
                amount=Decimal(str(d["amount"])),
                policy_reference=d["policy_reference"],
                category=d["category"],
            ))

        # Parse incentives JSONB
        incentives = []
        for i in tx.get("incentives", []):
            incentives.append(Incentive(
                name=i["name"],
                amount=Decimal(str(i["amount"])),
                policy_reference=i["policy_reference"],
                adherence_rate=None,
            ))

        transactions.append(TransactionRecord(
            transaction_id=tx["transaction_id"],
            platform=tx["platform"],
            transaction_date=tx["transaction_date"],
            gross_amount=Decimal(str(tx["gross_amount"])),
            deductions=deductions,
            incentives=incentives,
            net_amount=Decimal(str(tx["net_amount"])),
            distance_km=Decimal(str(tx["distance_km"])) if tx.get("distance_km") else None,
            duration_minutes=tx.get("duration_minutes"),
            wait_time_minutes=tx.get("wait_time_minutes"),
            contract_version=tx.get("contract_version"),
            order_timestamp=tx.get("order_timestamp"),
            pickup_timestamp=tx.get("pickup_timestamp"),
            completed_timestamp=tx.get("completed_timestamp"),
        ))

    # 8. Determine primary platform (most transactions)
    from collections import Counter
    platform_counts = Counter(tx["platform"] for tx in tx_rows)
    primary_platform = platform_counts.most_common(1)[0][0]

    # 9. Build reconciliation predictions from raw earnings/settlements
    # Query raw earnings and settlements separately to preserve anomaly detection
    reconciliation_predictions = _build_reconciliation_predictions(partner_id, conn)

    return {
        "partner_id": partner_id,
        "partner_profile": partner_profile,
        "transactions": transactions,
        "platform": primary_platform,
        "platforms": sorted(platforms),
        "contract_versions": contract_versions,
        "platform_rules": platform_rules,
        "reconciliation_predictions": reconciliation_predictions,
    }


def _build_reconciliation_predictions(partner_id: str, conn) -> list[dict[str, Any]]:
    """
    Build reconciliation predictions by querying earnings, settlements, incentives, deductions
    separately and feeding them to the existing normalize_real_records() function.
    """
    from collections import defaultdict

    # Fetch earnings for this partner
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT e.*, o.distance_km, o.duration_minutes, o.wait_time_minutes,
                   o.order_timestamp, o.pickup_timestamp, o.completed_timestamp
            FROM earnings e
            JOIN orders o ON o.order_id = e.order_id
            WHERE e.partner_id = %s
            ORDER BY e.transaction_date
            """,
            (partner_id,)
        )
        earnings_rows = cur.fetchall()

    if not earnings_rows:
        return []

    # Fetch settlements for this partner
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT s.*, e.order_id
            FROM settlements s
            JOIN earnings e ON e.earning_id = s.earning_id
            WHERE e.partner_id = %s
            ORDER BY s.settlement_date
            """,
            (partner_id,)
        )
        settlements_rows = cur.fetchall()

    # Fetch incentives for these earnings
    earning_ids = [e["earning_id"] for e in earnings_rows]
    if earning_ids:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM incentives WHERE earning_id = ANY(%s)",
                (earning_ids,)
            )
            incentives_rows = cur.fetchall()
    else:
        incentives_rows = []

    # Fetch deductions for these earnings
    if earning_ids:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM deductions WHERE earning_id = ANY(%s)",
                (earning_ids,)
            )
            deductions_rows = cur.fetchall()
    else:
        deductions_rows = []

    # Group incentives and deductions by earning_id
    incentives_by_earning = defaultdict(list)
    for inc in incentives_rows:
        incentives_by_earning[inc["earning_id"]].append(inc)

    deductions_by_earning = defaultdict(list)
    for ded in deductions_rows:
        deductions_by_earning[ded["earning_id"]].append(ded)

    # Build ExtractedFinancialRecord objects for earnings
    earning_records = []
    for e in earnings_rows:
        incs = incentives_by_earning.get(e["earning_id"], [])
        deductions_list = deductions_by_earning.get(e["earning_id"], [])

        incentive_total = sum(Decimal(str(i["amount"])) for i in incs if i.get("kind") == "incentive")
        bonus_total = sum(Decimal(str(i["amount"])) for i in incs if i.get("kind") == "bonus")
        deduction_total = sum(Decimal(str(d["amount"])) for d in deductions_list)

        # Build deduction policy reference
        ded_policy_ref = ""
        if deductions_list:
            ded_policy_ref = deductions_list[0].get("policy_reference", "")

        earning_records.append(ExtractedFinancialRecord(
            record_type="earning",
            record_id=e["earning_id"],
            transaction_id=e["transaction_id"],  # TXN-...
            platform=e["platform"],
            order_id=e["order_id"],
            record_date=e["transaction_date"],
            amount=Decimal(str(e["net_amount"])),  # expected earning = net_amount
            gross_amount=Decimal(str(e["gross_amount"])),
            incentive=incentive_total if incentive_total > 0 else None,
            bonus=bonus_total if bonus_total > 0 else None,
            deduction=deduction_total if deduction_total > 0 else None,
            deduction_policy_reference=ded_policy_ref if ded_policy_ref else None,
            distance_km=Decimal(str(e["distance_km"])) if e.get("distance_km") else None,
            duration_minutes=e.get("duration_minutes"),
            wait_time_minutes=e.get("wait_time_minutes"),
            contract_version=e.get("contract_version"),
        ))

    # Build ExtractedFinancialRecord objects for settlements
    settlement_records = []
    for s in settlements_rows:
        settlement_records.append(ExtractedFinancialRecord(
            record_type="settlement",
            record_id=s["settlement_id"],
            transaction_id=s["settlement_id"],  # STL-... (for record_id), but we'll map in normalize
            platform=s["platform"],
            order_id=s["order_id"],
            record_date=s["settlement_date"],
            amount=Decimal(str(s["amount_paid"])),
            gross_amount=None,
            incentive=None,
            bonus=None,
            deduction=None,
            deduction_policy_reference=None,
            distance_km=None,
            duration_minutes=None,
            wait_time_minutes=None,
            contract_version=None,
        ))

    # Call existing reconciliation logic
    all_records = earning_records + settlement_records
    result = normalize_real_records(all_records)
    predictions = result.get("predictions", [])

    # Tag each prediction with its platform from the rows already fetched above
    # (metadata only; reconciliation outcomes are untouched).
    platform_by_id = {r.transaction_id: r.platform for r in earning_records}
    platform_by_id.update({r.record_id: r.platform for r in settlement_records})
    for p in predictions:
        platform = platform_by_id.get(p.get("transaction_id"))
        if platform:
            p["platform"] = platform
    return predictions
