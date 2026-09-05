from __future__ import annotations
from typing import List, Optional
from decimal import Decimal
from datetime import date

from models import (
    TransactionRecord, ContractVersion, PaymentCalculation,
    BusinessCostCalculation, PartnerProfile, Deduction, Incentive
)


def calculate_payments(transactions: List[TransactionRecord], contract_versions: List[ContractVersion]) -> List[PaymentCalculation]:
    """Calculate platform_net vs contractual_net for each transaction."""
    results = []

    for tx in transactions:
        # Find matching contract version
        contract = None
        for cv in contract_versions:
            if cv.platform == tx.platform and cv.effective_from <= tx.transaction_date:
                if cv.effective_to is None or cv.effective_to >= tx.transaction_date:
                    contract = cv
                    break

        # Platform net = what was actually paid (net_amount)
        platform_net = tx.net_amount

        # Contractual net = what contract says should be paid
        contractual_net = calculate_contractual_net(tx, contract)

        # Build line-by-line breakdown
        breakdown = build_breakdown(tx, contract)

        results.append(PaymentCalculation(
            platform_net=platform_net,
            contractual_net=contractual_net,
            breakdown=breakdown
        ))

    return results


def calculate_contractual_net(tx: TransactionRecord, contract: ContractVersion | None) -> Decimal:
    """Calculate what contract says worker should earn for this transaction."""
    if contract is None:
        # Fallback: gross_amount as baseline
        return tx.gross_amount

    # Parse contract clauses for rates from clause text
    import re
    base_rate = Decimal("500")  # Default daily base
    per_km_rate = Decimal("15")  # Default per km
    wait_rate = Decimal("50")    # Default per hour wait

    for clause in contract.clauses:
        clause_lower = clause.lower()
        # Extract base pay: "Base pay: 160/day" or "Base pay: 500/day"
        if "base pay" in clause_lower or "base rate" in clause_lower:
            # Match numbers like 160, 195, 95, 100, 500, 450, 400
            match = re.search(r'(\d+(?:\.\d+)?)\s*/?\s*(?:day|per day|daily)', clause_lower)
            if match:
                base_rate = Decimal(match.group(1))
        # Extract per km: "Per km: 15" or "Per km: 12"
        elif "per km" in clause_lower:
            match = re.search(r'(\d+(?:\.\d+)?)', clause_lower)
            if match:
                per_km_rate = Decimal(match.group(1))
        # Extract wait time: "Wait time: 50/hr" or "Wait time: 45/hr"
        elif "wait time" in clause_lower or "wait:" in clause_lower:
            match = re.search(r'(\d+(?:\.\d+)?)\s*/?\s*(?:hr|hour|per hour)', clause_lower)
            if match:
                wait_rate = Decimal(match.group(1))

    # Calculate contractual pay
    daily_base = base_rate
    distance_pay = (tx.distance_km or Decimal("0")) * per_km_rate
    wait_pay = (Decimal(str(tx.wait_time_minutes or 0)) / Decimal("60")) * wait_rate

    # Incentives from contract
    incentive_pay = sum(inc.amount for inc in tx.incentives)

    # Deductions allowed by contract
    allowed_deductions = sum(
        d.amount for d in tx.deductions
        if d.category in ["fee", "adjustment"]  # Not penalties
    )

    contractual = daily_base + distance_pay + wait_pay + incentive_pay - allowed_deductions
    return max(Decimal("0"), contractual)


def build_breakdown(tx: TransactionRecord, contract: ContractVersion | None) -> dict[str, Decimal]:
    """Build line-by-line comparison breakdown using parsed contract rates."""
    import re
    base_rate = Decimal("500")
    per_km_rate = Decimal("15")
    wait_rate = Decimal("50")
    has_base_clause = False
    has_distance_clause = False
    has_wait_clause = False
    has_incentive_clause = False

    if contract:
        for clause in contract.clauses:
            clause_lower = clause.lower()
            if "base pay" in clause_lower or "base rate" in clause_lower:
                match = re.search(r'(\d+(?:\.\d+)?)\s*/?\s*(?:day|per day|daily)', clause_lower)
                if match:
                    base_rate = Decimal(match.group(1))
                    has_base_clause = True
            elif "per km" in clause_lower:
                match = re.search(r'(\d+(?:\.\d+)?)', clause_lower)
                if match:
                    per_km_rate = Decimal(match.group(1))
                    has_distance_clause = True
            elif "wait time" in clause_lower or "wait:" in clause_lower:
                match = re.search(r'(\d+(?:\.\d+)?)\s*/?\s*(?:hr|hour|per hour)', clause_lower)
                if match:
                    wait_rate = Decimal(match.group(1))
                    has_wait_clause = True
            if any(term in clause_lower for term in ("incentive", "bonus", "surge", "promotion")):
                has_incentive_clause = True

    return {
        "gross_amount": tx.gross_amount,
        "platform_net": tx.net_amount,
        "contract_base": base_rate,
        "contract_distance": (tx.distance_km or Decimal("0")) * per_km_rate,
        "contract_wait": (Decimal(str(tx.wait_time_minutes or 0)) / Decimal("60")) * wait_rate,
        "contract_incentives": sum(inc.amount for inc in tx.incentives),
        "contract_deductions": sum(d.amount for d in tx.deductions if d.category in ["fee", "adjustment"]),
        "contractual_net": calculate_contractual_net(tx, contract),
        "contract_has_base_clause": Decimal("1") if has_base_clause else Decimal("0"),
        "contract_has_distance_clause": Decimal("1") if has_distance_clause else Decimal("0"),
        "contract_has_wait_clause": Decimal("1") if has_wait_clause else Decimal("0"),
        "contract_has_incentive_clause": Decimal("1") if has_incentive_clause else Decimal("0"),
    }


def calculate_business_costs(
    transactions: List[TransactionRecord],
    profile: PartnerProfile,
    payment_calculations: Optional[List[PaymentCalculation]] = None
) -> List[BusinessCostCalculation]:
    """Calculate business costs and true hourly rates for each transaction."""
    results = []

    for i, tx in enumerate(transactions):
        distance = tx.distance_km or Decimal("0")

        # Petrol cost
        petrol_cost = distance * profile.petrol_per_km

        # Duration in hours
        duration_hours = Decimal(str(tx.duration_minutes or 0)) / Decimal("60")
        active_hours = duration_hours
        online_hours = duration_hours + (Decimal(str(tx.wait_time_minutes or 0)) / Decimal("60"))
        business_hours = online_hours + Decimal("1")  # + prep/travel buffer

        # Economic viability is based on money actually received, not the
        # contractual amount that should have been received.  The latter is
        # retained in PaymentCalculation for reconciliation and compliance.
        if payment_calculations and i < len(payment_calculations):
            income_received = payment_calculations[i].platform_net
        else:
            income_received = tx.net_amount

        active_hourly_rate = income_received / active_hours if active_hours > 0 else Decimal("0")
        online_hourly_rate = income_received / online_hours if online_hours > 0 else Decimal("0")
        business_hourly_rate = (income_received - petrol_cost) / business_hours if business_hours > 0 else Decimal("0")

        results.append(BusinessCostCalculation(
            petrol_cost=petrol_cost,
            business_hourly_rate=max(Decimal("0"), business_hourly_rate),
            active_hourly_rate=max(Decimal("0"), active_hourly_rate),
            online_hourly_rate=max(Decimal("0"), online_hourly_rate),
        ))

    return results
