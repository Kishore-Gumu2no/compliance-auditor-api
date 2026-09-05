"""Shared deterministic earnings-to-settlement reconciliation primitives."""

from decimal import Decimal
from enum import Enum
from typing import Any, Mapping


AMOUNT_TOLERANCE = Decimal("0.50")
DATE_TOLERANCE_DAYS = 3


class AnomalyType(Enum):
    PERFECT_MATCH = "perfect_match"
    PARTIAL_PAYMENT = "partial_payment"
    MISSING_PAYMENT = "missing_payment"
    DUPLICATE_SETTLEMENT = "duplicate_settlement"
    MISSING_INCENTIVE = "missing_incentive"
    INCORRECT_DEDUCTION = "incorrect_deduction"
    DATE_OUT_OF_TOLERANCE = "date_out_of_tolerance"
    AMBIGUOUS_MATCH = "ambiguous_match"
    EXTRA_UNEXPLAINED_SETTLEMENT = "extra_unexplained_settlement"


def classify_single(settlement: Mapping[str, Any], earning: Mapping[str, Any], parse_date) -> AnomalyType:
    """Classify one matched earning/settlement pair using the benchmarked rules."""
    settlement_amt = Decimal(str(settlement["amount_paid"]))
    expected_amt = Decimal(str(earning.get("expected_amount", 0)))
    incentive_component = Decimal(str(earning.get("incentive", 0) + earning.get("bonus", 0)))
    declared_deduction = Decimal(str(earning.get("deduction", 0)))

    earnings_date = parse_date(earning["date"])
    settlement_date = parse_date(settlement["date"])
    if abs((settlement_date - earnings_date).days) > DATE_TOLERANCE_DAYS:
        return AnomalyType.DATE_OUT_OF_TOLERANCE

    diff = settlement_amt - expected_amt
    if abs(diff) <= AMOUNT_TOLERANCE:
        return AnomalyType.PERFECT_MATCH
    if diff < -AMOUNT_TOLERANCE:
        if incentive_component > 0 and abs(diff - (-incentive_component)) <= AMOUNT_TOLERANCE:
            return AnomalyType.MISSING_INCENTIVE
        if declared_deduction > 0 and -diff - declared_deduction > Decimal("50.00"):
            return AnomalyType.INCORRECT_DEDUCTION
        return AnomalyType.PARTIAL_PAYMENT
    return AnomalyType.AMBIGUOUS_MATCH
