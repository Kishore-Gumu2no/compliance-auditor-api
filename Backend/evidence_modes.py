"""Evidence modes for order-level checks: what the orders supplied can prove.

* ``contract_spot_check``
  "Do these specific orders match the contract?" - a small sample of orders.
  Results are bounded to the orders checked and never extrapolated.
* ``full_audit``
  The existing rich per-order audit, unchanged.

(The comparison of a platform-declared total with bank credits was removed
on 2026-10-08 and is no longer a FairPay feature.)

Everything here is deterministic. The LLM only read the documents; every
amount, comparison and question below is computed from what was printed and
from contract terms on file. Missing evidence stays missing.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from calculations import _is_weekend, _time_in_windows, build_breakdown
from models import ContractVersion, TransactionRecord
from reconciliation import AMOUNT_TOLERANCE

CONTRACT_SPOT_CHECK = "contract_spot_check"
FULL_AUDIT = "full_audit"
EVIDENCE_MODES = (CONTRACT_SPOT_CHECK, FULL_AUDIT)

MODE_LABELS = {
    CONTRACT_SPOT_CHECK: "Contract Spot Check",
    FULL_AUDIT: "Full Audit",
}

_PAYMENT_LABELS = {
    "perfect_match": "Paid as shown",
    "partial_payment": "Paid less than shown",
    "missing_payment": "No payment found in your documents",
    "duplicate_settlement": "Paid more than once",
    "missing_incentive": "Incentive not paid",
    "incorrect_deduction": "Deduction larger than shown",
    "date_out_of_tolerance": "Paid outside the expected dates",
    "ambiguous_match": "Paid more than shown",
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _dec(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _day(value: Any) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def inr(amount: Optional[Decimal]) -> str:
    """₹4,210 or ₹4,210.50 - for partner-facing text."""
    if amount is None:
        return "an unreadable amount"
    amount = amount.quantize(Decimal("0.01"))
    text = f"{amount:,.2f}"
    return f"₹{text[:-3] if text.endswith('.00') else text}"


def _money(amount: Optional[Decimal]) -> Optional[str]:
    return None if amount is None else str(amount.quantize(Decimal("0.01")))


def _fmt_day(value: Optional[date]) -> str:
    return value.strftime("%d %b %Y").lstrip("0") if value else "an unknown date"


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


# ---------------------------------------------------------------------------
# Contract terms on file
# ---------------------------------------------------------------------------

def _resolve_contract(contracts: List[Dict[str, Any]], day: date) -> Optional[Dict[str, Any]]:
    """Same effective-date rule as calculations.calculate_payments."""
    for entry in contracts:
        cv: ContractVersion = entry["contract"]
        if cv.effective_from <= day and (cv.effective_to is None or cv.effective_to >= day):
            return entry
    return None


def _contract_reference(entry: Dict[str, Any]) -> str:
    cv: ContractVersion = entry["contract"]
    return f"{entry.get('title') or cv.version_id} (version {cv.version_id}, contract on file)"


def _order_datetime(obs: Dict[str, Any]) -> Optional[datetime]:
    day, clock = _day(obs.get("date")), obs.get("time")
    if not day or not clock:
        return None
    hours, minutes = clock.split(":")
    return datetime(day.year, day.month, day.day, int(hours), int(minutes))


def _incentive_shown(obs: Dict[str, Any]) -> bool:
    return any((_dec(obs.get(k)) or Decimal("0")) > 0 for k in ("incentive", "bonus"))


# ---------------------------------------------------------------------------
# Contract spot check (bounded to the orders supplied)
# ---------------------------------------------------------------------------

def _contract_check(obs: Dict[str, Any], entry: Dict[str, Any]) -> Dict[str, Any]:
    """Compare one order's printed earning with the contract terms we can evaluate.

    Uses the existing ``build_breakdown`` for base, distance, wait, fees and
    penalty. Components the evidence cannot support are left out, which only
    lowers the contract figure, so it is a floor: a shortfall below it is real
    for the parts checked, and nothing is claimed about the parts not checked.
    Time-based incentives are not part of the floor; they become questions.
    """
    cv: ContractVersion = entry["contract"]
    day = _day(obs["date"])
    amount = _dec(obs["amount"]) or Decimal("0")
    distance = _dec(obs.get("distance_km"))
    wait = obs.get("wait_time_minutes")
    duration = obs.get("duration_minutes")

    core = cv.model_copy(update={
        "peak_amount": None, "peak_windows": [],
        "weekend_amount": None, "weekend_days": [],
        "campaign_amount": None, "campaign_windows": [],
    })
    tx = TransactionRecord(
        transaction_id=obs.get("transaction_id") or obs["order_id"],
        platform=obs.get("platform") or cv.platform,
        transaction_date=day,
        gross_amount=_dec(obs.get("gross_amount")) or amount,
        net_amount=amount,
        distance_km=distance,
        duration_minutes=duration,
        wait_time_minutes=wait if wait is not None else 0,
    )
    breakdown = build_breakdown(tx, core)
    floor = breakdown["contractual_net"]

    included = [f"base pay {inr(breakdown['contract_base'])}"]
    not_checked: List[str] = []
    if (cv.distance_rate_per_km or 0) > 0:
        if distance is not None:
            included.append(f"distance pay {inr(breakdown['contract_distance'])} for {distance} km")
        else:
            not_checked.append("distance pay (no distance shown)")
    if (cv.wait_rate_per_min or 0) > 0:
        if wait is not None:
            included.append(f"wait pay {inr(breakdown['contract_wait'])} for {wait} min")
        else:
            not_checked.append("wait-time pay (no wait time shown)")
    if breakdown["contract_platform_fee"] > 0:
        included.append(f"minus platform fee {inr(breakdown['contract_platform_fee'])}")
    if breakdown["contract_insurance"] > 0:
        included.append(f"minus insurance {inr(breakdown['contract_insurance'])}")
    if breakdown["contract_penalty"] > 0:
        included.append(f"minus late penalty {inr(breakdown['contract_penalty'])}")
    if duration is None and (cv.penalty_trigger_minutes or 0) > 0 and (cv.penalty_amount or 0) > 0:
        # Unknown trip time: allow for the penalty so it cannot create a false shortfall.
        floor = max(floor - cv.penalty_amount, Decimal("0"))
        included.append(f"minus a possible late penalty {inr(cv.penalty_amount)} (trip time not shown)")
    if ((cv.peak_amount or 0) > 0 and cv.peak_windows) or ((cv.campaign_amount or 0) > 0 and cv.campaign_windows):
        if not obs.get("time"):
            not_checked.append("time-based incentives (no order time shown)")

    printed_incentives = (_dec(obs.get("incentive")) or Decimal("0")) + (_dec(obs.get("bonus")) or Decimal("0"))
    printed_core = amount - printed_incentives
    shortfall = floor - printed_core
    issue = shortfall > AMOUNT_TOLERANCE
    shown = f"this order shows {inr(amount)}" + (
        f" ({inr(printed_core)} excluding {inr(printed_incentives)} of incentives shown)" if printed_incentives else ""
    )
    if issue:
        text = (
            f"Possible shortfall of {inr(shortfall)}: under {_contract_reference(entry)}, the parts we could "
            f"check come to at least {inr(floor)} ({', '.join(included)}), but {shown}."
        )
    else:
        text = (
            f"No shortfall in the parts we could check: under {_contract_reference(entry)} they come to at least "
            f"{inr(floor)} ({', '.join(included)}), and {shown}."
        )
    return {
        "contract_version": cv.version_id,
        "contract_floor": _money(floor),
        "printed_amount_excluding_incentives": _money(printed_core),
        "possible_shortfall": _money(shortfall) if issue else None,
        "issue": issue,
        "explanation": text,
        "components_checked": included,
        "components_not_checked": not_checked,
        "distance_used": distance is not None and (cv.distance_rate_per_km or 0) > 0,
    }


def build_spot_check(
    platform: str,
    observations: List[Dict[str, Any]],
    predictions: List[Dict[str, Any]],
    contracts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Check each supplied order and keep the sample boundary explicit.

    An order counts as checked when it was compared with a payment (existing
    reconciliation) or with contract terms on file. Nothing is extrapolated
    beyond the orders checked, and amounts are kept per order.
    """
    payments = {
        p.get("order_id"): p for p in predictions
        if p.get("anomaly_type") != "extra_unexplained_settlement" and p.get("order_id")
    }
    rows: List[Dict[str, Any]] = []
    used_contracts: Dict[str, str] = {}
    for obs in observations:
        day = _day(obs.get("date"))
        row: Dict[str, Any] = {
            "order_id": obs["order_id"],
            "platform": obs.get("platform") or platform,
            "date": obs.get("date"),
            "time": obs.get("time"),
            "printed_amount": obs.get("amount"),
            "payment_check": None,
            "contract_check": None,
            "findings": [],
            "not_checked": [],
        }
        payment = payments.get(obs["order_id"])
        if payment:
            anomaly = payment.get("anomaly_type")
            label = _PAYMENT_LABELS.get(anomaly, anomaly)
            row["payment_check"] = {
                "result": anomaly,
                "label": label,
                "expected_amount": payment.get("expected_amount"),
                "settlement_amount": payment.get("settlement_amount"),
                "issue": anomaly != "perfect_match",
            }
            if anomaly != "perfect_match":
                row["findings"].append(
                    f"{label}: shown as earned {inr(_dec(payment.get('expected_amount')))}, "
                    f"payment found {inr(_dec(payment.get('settlement_amount')))}."
                )
        else:
            row["not_checked"].append("payment (no payment record for this order in your documents)")

        entry = _resolve_contract(contracts, day) if day else None
        if entry:
            check = _contract_check(obs, entry)
            row["contract_check"] = check
            used_contracts[check["contract_version"]] = _contract_reference(entry)
            if check["issue"]:
                row["findings"].append(check["explanation"])
            row["not_checked"].extend(check["components_not_checked"])
        else:
            row["not_checked"].append("contract terms (no contract on file for this platform and date)")

        checked = row["payment_check"] is not None or row["contract_check"] is not None
        has_issue = bool(row["findings"])
        row["status"] = "possible_issue" if has_issue else ("no_issue_found" if checked else "not_checked")
        rows.append(row)

    checked_rows = [r for r in rows if r["status"] != "not_checked"]
    with_findings = [r for r in checked_rows if r["status"] == "possible_issue"]
    n_checked, n_issue = len(checked_rows), len(with_findings)

    if n_checked:
        statement = (
            f"We checked {n_checked} {_plural(n_checked, 'order', 'orders')}. "
            + (f"{n_issue} showed {_plural(n_issue, 'a possible discrepancy', 'possible discrepancies')}."
               if n_issue else "None showed a discrepancy in the parts we could check.")
        )
    else:
        statement = (
            f"We received {len(rows)} {_plural(len(rows), 'order', 'orders')} but could not check any: "
            "there was no payment record and no contract on file to compare them with."
        )
    return {
        "mode": CONTRACT_SPOT_CHECK,
        "platform": platform,
        "orders_received": len(rows),
        "sample_orders_checked": n_checked,
        "orders_with_findings": n_issue,
        "orders_without_findings": n_checked - n_issue,
        "orders_not_checked": len(rows) - n_checked,
        "statement": statement,
        "sample_boundary": (
            f"This covers only the {n_checked} {_plural(n_checked, 'order', 'orders')} checked. "
            "FairPay does not estimate the rest of your month from a sample."
        ),
        "contracts_used": [{"version": v, "reference": r} for v, r in sorted(used_contracts.items())],
        "orders": rows,
    }


# ---------------------------------------------------------------------------
# Questions: known contract term + observed evidence + missing evidence
# ---------------------------------------------------------------------------

def _windows_text(windows: List[str]) -> str:
    return " or ".join(w.replace("-", "–") for w in windows)


def build_contract_questions(
    platform: str,
    observations: List[Dict[str, Any]],
    contracts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Turn 'cannot prove a breach' into a specific question for the platform.

    Each question rests on three deterministic facts: a contract term on file,
    what the evidence shows, and what it does not show. None of them is a
    confirmed underpayment.
    """
    peak: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    weekend: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_day: Dict[Tuple[str, date], List[Dict[str, Any]]] = defaultdict(list)
    entries: Dict[str, Dict[str, Any]] = {}

    for obs in observations:
        day = _day(obs.get("date"))
        entry = _resolve_contract(contracts, day) if day else None
        if not entry:
            continue
        cv: ContractVersion = entry["contract"]
        entries[cv.version_id] = entry
        by_day[(cv.version_id, day)].append(obs)
        if _incentive_shown(obs):
            continue
        when = _order_datetime(obs)
        if (cv.peak_amount or 0) > 0 and cv.peak_windows and when and _time_in_windows(when, cv.peak_windows):
            peak[cv.version_id].append(obs)
        if (cv.weekend_amount or 0) > 0 and cv.weekend_days and _is_weekend(day, cv.weekend_days):
            weekend[cv.version_id].append(obs)

    questions: List[Dict[str, Any]] = []
    for version, orders in sorted(peak.items()):
        entry, cv = entries[version], entries[version]["contract"]
        examples = ", ".join(
            f"{o['order_id']} at {o['time']}" + (f" ('{o['time_label']}')" if o.get("time_label") else "")
            for o in orders[:3]
        )
        questions.append({
            "topic": "Peak-hour incentive",
            "platform": platform,
            "contract_term": f"{inr(cv.peak_amount)} peak-hour incentive per order between {_windows_text(cv.peak_windows)}, under {_contract_reference(entry)}.",
            "observed": f"{len(orders)} {_plural(len(orders), 'order has', 'orders have')} a printed time inside that window (e.g. {examples}).",
            "not_shown": "The evidence does not show an incentive on these orders.",
            "question": (
                f"These orders fall within the contract's peak-hour window, but the supplied evidence does not "
                f"show the incentive. Ask {platform} whether the peak-hour incentive was applied and where it "
                "appears in the statement."
            ),
            "order_ids": [o["order_id"] for o in orders],
            "status": "question_not_confirmed_underpayment",
        })
    for version, orders in sorted(weekend.items()):
        entry, cv = entries[version], entries[version]["contract"]
        questions.append({
            "topic": "Weekend incentive",
            "platform": platform,
            "contract_term": f"{inr(cv.weekend_amount)} weekend incentive per order on {', '.join(cv.weekend_days)}, under {_contract_reference(entry)}.",
            "observed": f"{len(orders)} {_plural(len(orders), 'order is', 'orders are')} dated on those days.",
            "not_shown": "The evidence does not show an incentive on these orders.",
            "question": (
                f"These orders are on the contract's weekend days, but the supplied evidence does not show the "
                f"weekend incentive. Ask {platform} whether it was applied and where it appears in the statement."
            ),
            "order_ids": [o["order_id"] for o in orders],
            "status": "question_not_confirmed_underpayment",
        })
    for (version, day), orders in sorted(by_day.items()):
        entry, cv = entries[version], entries[version]["contract"]
        threshold = cv.daily_threshold or 0
        if threshold <= 0 or (cv.daily_bonus or 0) <= 0 or len(orders) < threshold:
            continue
        if any((_dec(o.get("bonus")) or Decimal("0")) > 0 for o in orders):
            continue
        questions.append({
            "topic": "Daily target bonus",
            "platform": platform,
            "contract_term": f"{inr(cv.daily_bonus)} bonus after {threshold} orders in a day, under {_contract_reference(entry)}.",
            "observed": f"Your evidence shows at least {len(orders)} orders on {_fmt_day(day)}.",
            "not_shown": "The evidence does not show the daily bonus for that day.",
            "question": (
                f"Your documents show at least {len(orders)} orders on {_fmt_day(day)}, which meets the contract's "
                f"daily target of {threshold}, but they do not show the bonus. Ask {platform} whether the daily "
                "target bonus was paid for that day and where it appears in the statement."
            ),
            "order_ids": [o["order_id"] for o in orders],
            "status": "question_not_confirmed_underpayment",
        })
    return questions


# ---------------------------------------------------------------------------
# "What FairPay could not check" - only what is actually unavailable
# ---------------------------------------------------------------------------

def _limit(item: str, reason: str, platform: Optional[str] = None) -> Dict[str, Any]:
    return {"item": item, "reason": reason, "platform": platform}


def order_limits(
    mode: str,
    platform: str,
    observations: List[Dict[str, Any]],
    contracts: List[Dict[str, Any]],
    has_payments: bool,
    spot_check: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Limits for an order-level audit (spot check or full audit) of one platform."""
    total = len(observations)
    limits: List[Dict[str, Any]] = []
    if not total:
        return limits

    def missing(field: str) -> int:
        return sum(1 for o in observations if o.get(field) in (None, ""))

    on_file = [e for e in contracts]
    any_contract = bool(on_file)
    wait_clause = any((e["contract"].wait_rate_per_min or 0) > 0 for e in on_file)
    distance_clause = any((e["contract"].distance_rate_per_km or 0) > 0 for e in on_file)
    timed_clause = any(
        ((e["contract"].peak_amount or 0) > 0 and e["contract"].peak_windows)
        or ((e["contract"].campaign_amount or 0) > 0 and e["contract"].campaign_windows)
        for e in on_file
    )
    penalty_clause = any((e["contract"].penalty_amount or 0) > 0 for e in on_file)

    if mode == CONTRACT_SPOT_CHECK and spot_check:
        n = spot_check["sample_orders_checked"]
        limits.append(_limit(
            "Orders you did not send",
            f"Only the {n} {_plural(n, 'order', 'orders')} checked are covered. FairPay does not estimate the rest of your month from them.",
            platform,
        ))

    n_wait = missing("wait_time_minutes")
    if n_wait and (wait_clause or mode == FULL_AUDIT):
        limits.append(_limit("Wait-time compensation",
                             f"Not shown in the supplied evidence for {n_wait} of {total} orders.", platform))
    n_dist = missing("distance_km")
    if n_dist and (distance_clause or mode == FULL_AUDIT):
        limits.append(_limit("Distance pay", f"No distance shown for {n_dist} of {total} orders.", platform))
    if mode == CONTRACT_SPOT_CHECK and spot_check and any(
        (r.get("contract_check") or {}).get("distance_used") for r in spot_check["orders"]
    ):
        limits.append(_limit(
            "Distance accuracy",
            "We verified the platform's calculation using the distance printed on your documents, but cannot "
            "independently verify the physical distance.",
            platform,
        ))
    n_time = missing("time")
    if n_time and timed_clause:
        limits.append(_limit("Peak-hour and other time-based incentives",
                             f"No order time shown for {n_time} of {total} orders.", platform))
    n_duration = missing("duration_minutes")
    if mode == CONTRACT_SPOT_CHECK and n_duration and penalty_clause:
        limits.append(_limit("Late-delivery penalties",
                             f"Trip time not shown for {n_duration} of {total} orders, so a late penalty was allowed for rather than checked.",
                             platform))
    if not has_payments:
        limits.append(_limit("Whether these orders were paid",
                             "No payment records for these orders were supplied.", platform))

    if not any_contract:
        limits.append(_limit(
            "Contract terms",
            f"No contract or rate card for {platform} was supplied or is on file, so order breakdowns could not be "
            "compared with contract rates and no contract questions could be generated.",
            platform,
        ))
    else:
        titles = ", ".join(sorted({e.get("title") or e["contract"].version_id for e in on_file}))
        use = "compared your orders with" if mode == CONTRACT_SPOT_CHECK else "based the questions below on"
        limits.append(_limit(
            "Contract legitimacy",
            f"Contract/rate-card evidence was not supplied. We {use} the contract on file ({titles}); your "
            "documents do not confirm these were the terms that applied to you.",
            platform,
        ))
        if mode == FULL_AUDIT:
            limits.append(_limit(
                "Contract rates",
                "This audit compares what each order earned with what was paid; it does not recalculate orders from contract rates.",
                platform,
            ))
    return limits


def working_time_limit() -> Dict[str, Any]:
    return _limit(
        "Working hours and time between orders",
        "Your documents do not show login or online hours with dates, so working time and the time between orders could not be measured.",
    )
