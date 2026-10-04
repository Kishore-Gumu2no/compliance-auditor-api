from __future__ import annotations
from typing import List, Optional
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime

from models import (
    TransactionRecord, ContractVersion, PaymentCalculation,
    BusinessCostCalculation, PartnerProfile, Deduction, Incentive
)


def _round_dp(value: Decimal) -> Decimal:
    """Round to 2 decimal places using half-up rounding."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _time_in_windows(order_time: datetime, windows: List[str]) -> bool:
    """Check if order time falls within any of the time windows."""
    if not windows:
        return False
    order_time_local = order_time.time()
    for window in windows:
        try:
            start_str, end_str = window.split("-")
            start_h, start_m = map(int, start_str.split(":"))
            end_h, end_m = map(int, end_str.split(":"))
            start = datetime(1, 1, 1, start_h, start_m).time()
            end = datetime(1, 1, 1, end_h, end_m).time()
            if start <= order_time_local <= end:
                return True
        except (ValueError, AttributeError):
            continue
    return False


def _is_weekend(transaction_date: date, weekend_days: List[str]) -> bool:
    """Check if transaction date is a weekend day."""
    if not weekend_days:
        return False
    day_name = transaction_date.strftime("%a").upper()[:3]
    return day_name in [d[:3] for d in weekend_days]


def _is_weekday(transaction_date: date) -> bool:
    """Check if transaction date is a weekday (Mon-Fri)."""
    return transaction_date.weekday() < 5


def calculate_contractual_net(tx: TransactionRecord, contract: ContractVersion | None) -> Decimal:
    """Calculate what contract says worker should earn for this transaction."""
    if contract is None:
        # Fallback: gross_amount as baseline - but log warning
        import logging
        logging.warning(f"No contract found for transaction {tx.transaction_id}, using gross_amount as fallback")
        return tx.gross_amount

    # Use structured contract parameters
    # Base pay (per order)
    base_pay = contract.base_pay_per_order or Decimal("0")

    # Distance pay
    distance_km = tx.distance_km or Decimal("0")
    distance_rate = contract.distance_rate_per_km or Decimal("0")
    distance_pay = _round_dp(distance_km * distance_rate)

    # Wait pay
    wait_time_minutes = tx.wait_time_minutes or 0
    free_minutes = contract.wait_free_minutes or 0
    max_billable = contract.wait_max_billable_minutes or 0
    wait_rate = contract.wait_rate_per_min or Decimal("0")

    billable_wait = max(0, wait_time_minutes - free_minutes)
    billable_wait = min(billable_wait, max_billable)
    wait_pay = _round_dp(Decimal(str(billable_wait)) * wait_rate)

    # Per-order incentives
    incentive_pay = Decimal("0")

    # Peak hour incentive
    if contract.peak_amount and contract.peak_amount > 0:
        if _time_in_windows(tx.order_timestamp, contract.peak_windows):
            incentive_pay += contract.peak_amount

    # Weekend incentive
    if contract.weekend_amount and contract.weekend_amount > 0:
        if _is_weekend(tx.transaction_date, contract.weekend_days):
            incentive_pay += contract.weekend_amount

    # Campaign incentive
    if (contract.campaign_amount and contract.campaign_amount > 0
        and contract.campaign_effective_from and contract.campaign_effective_to
        and contract.campaign_effective_from <= tx.transaction_date <= contract.campaign_effective_to):

        # Check weekdays only condition
        if contract.campaign_weekdays_only and not _is_weekday(tx.transaction_date):
            pass  # Skip campaign on weekends
        # Check time windows
        elif contract.campaign_windows and not _time_in_windows(tx.order_timestamp, contract.campaign_windows):
            pass  # Skip campaign outside windows
        # Check minimum distance
        elif contract.campaign_min_distance_km and distance_km < contract.campaign_min_distance_km:
            pass  # Skip campaign below minimum distance
        else:
            incentive_pay += contract.campaign_amount

    # Bonuses (daily/weekly target) - use from transaction record since threshold crossing is complex
    # The transaction record already has the correct bonus applied based on actual threshold crossing
    bonus_pay = sum(inc.amount for inc in tx.incentives if "bonus" in inc.name.lower() or "target" in inc.name.lower())

    # Gross earnings before deductions
    gross_before_deductions = base_pay + distance_pay + wait_pay + incentive_pay + bonus_pay

    # Deductions
    # Platform service fee: percent of (base + distance + wait)
    fee_base = base_pay + distance_pay + wait_pay
    platform_fee = Decimal("0")
    if contract.platform_fee_rate and contract.platform_fee_rate > 0:
        platform_fee = _round_dp(fee_base * contract.platform_fee_rate)

    # Insurance
    insurance = contract.insurance_amount or Decimal("0")

    # Penalty
    penalty = Decimal("0")
    if (contract.penalty_trigger_minutes and contract.penalty_trigger_minutes > 0
        and tx.duration_minutes and tx.duration_minutes > contract.penalty_trigger_minutes
        and contract.penalty_amount and contract.penalty_amount > 0):
        penalty = contract.penalty_amount

    total_deductions = platform_fee + insurance + penalty

    # Net amount
    net = gross_before_deductions - total_deductions
    return max(_round_dp(net), Decimal("0"))


def build_breakdown(tx: TransactionRecord, contract: ContractVersion | None) -> dict[str, Decimal]:
    """Build line-by-line comparison breakdown using structured contract rates."""
    if contract is None:
        # Fallback breakdown
        return {
            "gross_amount": tx.gross_amount,
            "platform_net": tx.net_amount,
            "contract_base": Decimal("0"),
            "contract_distance": Decimal("0"),
            "contract_wait": Decimal("0"),
            "contract_incentives": sum(inc.amount for inc in tx.incentives),
            "contract_deductions": sum(d.amount for d in tx.deductions if d.category in ["fee", "adjustment"]),
            "contractual_net": tx.gross_amount,
            "contract_has_base_clause": Decimal("0"),
            "contract_has_distance_clause": Decimal("0"),
            "contract_has_wait_clause": Decimal("0"),
            "contract_has_incentive_clause": Decimal("0"),
        }

    # Use structured contract parameters
    base_pay = contract.base_pay_per_order or Decimal("0")
    distance_km = tx.distance_km or Decimal("0")
    distance_rate = contract.distance_rate_per_km or Decimal("0")
    distance_pay = _round_dp(distance_km * distance_rate)

    wait_time_minutes = tx.wait_time_minutes or 0
    free_minutes = contract.wait_free_minutes or 0
    max_billable = contract.wait_max_billable_minutes or 0
    wait_rate = contract.wait_rate_per_min or Decimal("0")
    billable_wait = max(0, wait_time_minutes - free_minutes)
    billable_wait = min(billable_wait, max_billable)
    wait_pay = _round_dp(Decimal(str(billable_wait)) * wait_rate)

    # Incentives
    peak_amount = contract.peak_amount or Decimal("0")
    peak_pay = peak_amount if _time_in_windows(tx.order_timestamp, contract.peak_windows) else Decimal("0")

    weekend_amount = contract.weekend_amount or Decimal("0")
    weekend_pay = weekend_amount if _is_weekend(tx.transaction_date, contract.weekend_days) else Decimal("0")

    campaign_pay = Decimal("0")
    if (contract.campaign_amount and contract.campaign_amount > 0
        and contract.campaign_effective_from and contract.campaign_effective_to
        and contract.campaign_effective_from <= tx.transaction_date <= contract.campaign_effective_to):
        if contract.campaign_weekdays_only and not _is_weekday(tx.transaction_date):
            pass
        elif contract.campaign_windows and not _time_in_windows(tx.order_timestamp, contract.campaign_windows):
            pass
        elif contract.campaign_min_distance_km and distance_km < contract.campaign_min_distance_km:
            pass
        else:
            campaign_pay = contract.campaign_amount or Decimal("0")

    total_per_order_incentives = peak_pay + weekend_pay + campaign_pay

    bonus_pay = sum(inc.amount for inc in tx.incentives if "bonus" in inc.name.lower() or "target" in inc.name.lower())

    # Deductions
    fee_base = base_pay + distance_pay + wait_pay
    platform_fee = Decimal("0")
    if contract.platform_fee_rate and contract.platform_fee_rate > 0:
        platform_fee = _round_dp(fee_base * contract.platform_fee_rate)

    insurance = contract.insurance_amount or Decimal("0")

    penalty = Decimal("0")
    if (contract.penalty_trigger_minutes and contract.penalty_trigger_minutes > 0
        and tx.duration_minutes and tx.duration_minutes > contract.penalty_trigger_minutes
        and contract.penalty_amount and contract.penalty_amount > 0):
        penalty = contract.penalty_amount

    return {
        "gross_amount": tx.gross_amount,
        "platform_net": tx.net_amount,
        "contract_base": base_pay,
        "contract_distance": distance_pay,
        "contract_wait": wait_pay,
        "contract_peak_incentive": peak_pay,
        "contract_weekend_incentive": weekend_pay,
        "contract_campaign_incentive": campaign_pay,
        "contract_bonus": bonus_pay,
        "contract_platform_fee": platform_fee,
        "contract_insurance": insurance,
        "contract_penalty": penalty,
        "contract_incentives": total_per_order_incentives,
        "contract_deductions": platform_fee + insurance,  # fees and adjustments (not penalties)
        "contractual_net": calculate_contractual_net(tx, contract),
        "contract_has_base_clause": Decimal("1") if base_pay > 0 else Decimal("0"),
        "contract_has_distance_clause": Decimal("1") if distance_rate > 0 else Decimal("0"),
        "contract_has_wait_clause": Decimal("1") if wait_rate > 0 else Decimal("0"),
        "contract_has_incentive_clause": Decimal("1") if (peak_amount > 0 or weekend_amount > 0 or contract.campaign_amount) else Decimal("0"),
    }


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

        if contract is None:
            import logging
            logging.warning(f"Contract resolution failed for transaction {tx.transaction_id} (platform={tx.platform}, date={tx.transaction_date})")

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