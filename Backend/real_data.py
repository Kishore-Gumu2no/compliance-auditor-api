"""Normalization of extracted real-world earning and settlement records."""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from models import Deduction, Incentive, PaymentCalculation, TransactionRecord
from reconciliation import AnomalyType, classify_single


class ExtractedFinancialRecord(BaseModel):
    record_type: Literal["earning", "settlement"]
    record_id: str
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
        matches = by_key[(earning.order_id, earning.platform)]
        earning_payload = _classifier_earning_payload(earning)
        if not matches:
            gaps.append(f"No settlement found for earning {earning.record_id}.")
            transactions.append(_transaction_from_earning(earning, Decimal("0"), earning.record_id))
            calculations.append(PaymentCalculation(platform_net=Decimal("0"), contractual_net=earning.amount))
            predictions.append(_prediction(earning, earning.record_id, AnomalyType.MISSING_PAYMENT, Decimal("0")))
            continue

        settlement = min(matches, key=lambda s: abs(s.amount - earning.amount))
        matched_ids.update(s.record_id for s in matches)
        if len(matches) > 1:
            total = sum((s.amount for s in matches), Decimal("0"))
            transactions.append(_transaction_from_earning(earning, total, settlement.record_id))
            calculations.append(PaymentCalculation(platform_net=total, contractual_net=earning.amount))
            predictions.append(_prediction(earning, settlement.record_id, AnomalyType.DUPLICATE_SETTLEMENT, total))
            continue

        settlement_payload = {"date": settlement.record_date.isoformat(), "amount_paid": settlement.amount}
        anomaly = classify_single(settlement_payload, earning_payload, _date)
        transactions.append(_transaction_from_earning(earning, settlement.amount, settlement.record_id))
        calculations.append(PaymentCalculation(platform_net=settlement.amount, contractual_net=earning.amount))
        predictions.append(_prediction(earning, settlement.record_id, anomaly, settlement.amount))

    for settlement in settlements:
        if settlement.record_id not in matched_ids:
            gaps.append(f"Settlement {settlement.record_id} has no matching earning record.")
            predictions.append({"transaction_id": settlement.record_id, "order_id": settlement.order_id,
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
