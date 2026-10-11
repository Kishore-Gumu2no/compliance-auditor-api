"""Earnings Opportunity Simulator.

Deterministic "what if I change my working pattern" projections for a delivery
partner, plus persistence of completed simulations.

The baseline comes from one of two sources, both producing the same fields so
the same projection code serves both:

* the partner's own historical rows in ``v_daily_business_summary`` (the
  30-day dataset the audit uses), or
* a completed Telegram audit - earnings, dates and working time read from the
  documents the partner sent.

When the documents do not show the hours or days worked, no baseline can be
built and the simulation is refused with a plain explanation. Nothing here
touches the audit or reconciliation calculations - it only reads their output.

Three scenarios are supported:

* ``hours``    - change the number of online hours worked per day.
* ``days``     - change the number of days worked in the period.
* ``expenses`` - change estimated operating (fuel/energy) expenses.

Every projection is a plain arithmetic function of the partner's observed
averages, so the same inputs always produce the same numbers. Projections are
estimates, never guaranteed income, and each one carries the assumptions that
produced it.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

import db
from platforms import platform_key
from real_data import load_partner_profile

SCENARIO_TYPES = ("hours", "days", "expenses")

# Shown above the simulator when the documents cannot support a projection.
# Working time is never estimated: without dated evidence there is no baseline.
UNKNOWN_HOURS_AND_DAYS = (
    "We don't have enough evidence to calculate your working hours or the days you worked."
)
UNKNOWN_HOURS = "We don't have enough evidence to calculate your working hours."
UNKNOWN_DAYS = (
    "We don't have enough evidence to work out which days you worked and what you earned on them."
)
SAMPLE_ONLY = (
    "A spot check covers only a sample of your orders, so it cannot show what you earn "
    "per hour or per day."
)
TOTALS_ONLY = (
    "A settlement check compares totals only, so it does not show what you earned on each "
    "day or how many hours you worked."
)

# What would make a simulation possible, shown with each refusal.
_NEED_WORK_TIME = (
    "Screenshots that show your login, online or shift time with the date - either a time "
    "range (for example 11:00 AM - 3:00 PM) or a duration (for example 6h 20m) for each day."
)
_NEED_DAILY_EARNINGS = (
    "Your order earnings for those same days, each with its date, together with the "
    "payments for them (a full audit)."
)

# Telegram evidence often covers only a few days, so the days scenario is
# allowed to project up to a full month from those days.
TELEGRAM_MAX_WORKING_DAYS = 31

# Input guard rails. These bound what a partner can ask for; they are not
# earnings assumptions.
MAX_HOURS_PER_DAY = Decimal("16")
MIN_HOURS_PER_DAY = Decimal("0.5")
MAX_DAYS_MULTIPLIER = Decimal("1.5")  # at most 50% more days than the period length
MIN_EXPENSE_CHANGE_PCT = Decimal("-100")
MAX_EXPENSE_CHANGE_PCT = Decimal("100")

_MONEY = Decimal("0.01")
_RATE = Decimal("0.0001")


def _dec(value: Any) -> Decimal:
    """Coerce a DB numeric / float / None into a Decimal."""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY, rounding=ROUND_HALF_UP)


def _rate(value: Decimal) -> Decimal:
    return value.quantize(_RATE, rounding=ROUND_HALF_UP)


def _hrs(value: Any) -> str:
    """Hours, rounded to one decimal for partner-facing text."""
    return f"{_dec(value).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}"


def _rs(value: Any) -> str:
    """Rupee amount for partner-facing text."""
    return f"Rs {_money(_dec(value))}"


# ---------------------------------------------------------------------------
# Baseline - the partner's actual historical numbers
# ---------------------------------------------------------------------------

def load_earnings_baseline(partner_id: str, conn: Optional[Connection] = None) -> Dict[str, Any]:
    """Aggregate the partner's own daily history into a deterministic baseline.

    Reads ``v_daily_business_summary`` (one row per day actually worked), which
    already rolls up income, fuel/energy cost and online hours per day.
    """
    with db.session(conn) as cx, cx.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT day, orders, online_hours, business_hours, active_hours,
                   petrol_cost, energy_cost, income_received,
                   net_after_operating_cost
            FROM v_daily_business_summary
            WHERE partner_id = %s
            ORDER BY day
            """,
            (partner_id,),
        )
        rows = cur.fetchall()

    if not rows:
        raise ValueError(f"No historical earnings found for partner {partner_id}")

    days_worked = len(rows)
    total_income = sum((_dec(r["income_received"]) for r in rows), Decimal("0"))
    total_operating_cost = sum(
        (_dec(r["petrol_cost"]) + _dec(r["energy_cost"]) for r in rows), Decimal("0")
    )
    total_online_hours = sum((_dec(r["online_hours"]) for r in rows), Decimal("0"))
    total_orders = sum(int(r["orders"] or 0) for r in rows)

    # Observed per-day hourly rates, used for the central estimate and to show
    # how much the rate actually varied day to day.
    daily_rates = [
        _dec(r["income_received"]) / _dec(r["online_hours"])
        for r in rows
        if _dec(r["online_hours"]) > 0
    ]

    income_per_hour = (
        total_income / total_online_hours if total_online_hours > 0 else Decimal("0")
    )
    avg_income_per_day = total_income / Decimal(days_worked)
    avg_hours_per_day = total_online_hours / Decimal(days_worked)

    return {
        "partner_id": partner_id,
        "period_start": rows[0]["day"].isoformat(),
        "period_end": rows[-1]["day"].isoformat(),
        "days_worked": days_worked,
        "total_orders": total_orders,
        "total_income": _money(total_income),
        "total_operating_cost": _money(total_operating_cost),
        "net_after_expenses": _money(total_income - total_operating_cost),
        "total_online_hours": _rate(total_online_hours),
        "avg_income_per_day": _money(avg_income_per_day),
        "avg_hours_per_day": _rate(avg_hours_per_day),
        "income_per_hour": _money(income_per_hour),
        "lowest_daily_hourly_rate": _money(min(daily_rates)) if daily_rates else Decimal("0"),
        "highest_daily_hourly_rate": _money(max(daily_rates)) if daily_rates else Decimal("0"),
        # Source metadata shared with the Telegram baseline.
        "source": "history",
        "submission_id": None,
        "simulation_possible": True,
        "unavailable_message": None,
        "days_with_earnings": days_worked,
        "max_working_days": int((Decimal(days_worked) * MAX_DAYS_MULTIPLIER).to_integral_value(rounding=ROUND_HALF_UP)),
        "expenses_available": True,
        "expenses_unavailable_reason": None,
        "evidence_label": "your past 30 days",
        "cost_note": "Only fuel and energy costs recorded in your history are counted.",
        "baseline_notes": [],
        "evidence_needed": [],
        "value_sources": [
            _source("Days worked", "observed", "Days with orders in your 30-day history."),
            _source("Online hours", "observed", "Online hours recorded in your history."),
            _source("Earnings", "observed", "Income received in your history."),
            _source("Earnings per hour", "calculated", "Earnings divided by online hours."),
            _source("Fuel or energy cost", "calculated", "Recorded in your history from distance and your cost per km."),
            _source("Extra hours earn your average rate", "assumption", "Used by the projections, not by the baseline."),
        ],
    }


def _source(label: str, kind: str, detail: str) -> Dict[str, str]:
    """One baseline value and where it came from: observed, calculated,
    assumption or unavailable."""
    return {"label": label, "kind": kind, "detail": detail}


# ---------------------------------------------------------------------------
# Baseline from a Telegram audit
# ---------------------------------------------------------------------------

def _clock_minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _union_minutes(intervals: List[Tuple[int, int]]) -> int:
    """Length of the union of [start, end) minute intervals - overlaps count once."""
    total = 0
    current_start: Optional[int] = None
    current_end = 0
    for start, end in sorted(intervals):
        if current_start is None or start > current_end:
            if current_start is not None:
                total += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    if current_start is not None:
        total += current_end - current_start
    return total


def _minutes_for_day(entries: List[Dict[str, Any]]) -> Tuple[int, bool, bool]:
    """Working minutes for one day across every platform.

    Printed login ranges are merged, so time logged into two apps at once
    counts once. Where only a total duration is printed, the longest total is
    used as a conservative estimate. Returns (minutes, estimated, overlapped).
    """
    intervals: List[Tuple[int, int]] = []
    totals: List[int] = []
    for entry in entries:
        if entry.get("start"):
            start = _clock_minutes(entry["start"])
            intervals.append((start, start + int(entry["minutes"])))
        else:
            totals.append(int(entry["minutes"]))
    union = _union_minutes(intervals)
    overlapped = union < sum(end - start for start, end in intervals)
    longest_total = max(totals, default=0)
    return max(union, longest_total), bool(totals), overlapped


def load_telegram_baseline(
    partner_id: str, submission_id: str, merged: Dict[str, Any]
) -> Dict[str, Any]:
    """Build a baseline from a completed Telegram audit.

    Uses what the platform said the partner earned per order, the dates on
    those orders, and the working time printed on the documents. Only days
    that show both earnings and working time are used.
    """
    unavailable: Dict[str, Any] = {
        "partner_id": partner_id,
        "source": "telegram",
        "submission_id": submission_id,
        "simulation_possible": False,
        "value_sources": [],
    }
    # A sample or a pair of totals cannot stand in for daily earnings.
    mode = merged.get("evidence_mode")
    if mode == "contract_spot_check":
        return {**unavailable, "days_with_earnings": None, "unavailable_message": SAMPLE_ONLY,
                "evidence_needed": [_NEED_DAILY_EARNINGS, _NEED_WORK_TIME]}
    if mode == "settlement_reconciliation":
        return {**unavailable, "days_with_earnings": None, "unavailable_message": TOTALS_ONLY,
                "evidence_needed": [_NEED_DAILY_EARNINGS, _NEED_WORK_TIME]}

    transactions = {t.get("transaction_id"): t for t in merged.get("transactions") or []}
    income_by_day: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    orders_by_day: Dict[str, int] = defaultdict(int)
    distance_by_day: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    days_missing_distance: set = set()

    for prediction in merged.get("reconciliation_predictions") or []:
        if prediction.get("anomaly_type") == "extra_unexplained_settlement":
            continue  # a payment with no earning is not something the partner earned
        transaction = transactions.get(prediction.get("transaction_id")) or {}
        day = transaction.get("transaction_date")
        if not day:
            continue
        income_by_day[day] += _dec(prediction.get("expected_amount"))
        orders_by_day[day] += 1
        distance = transaction.get("distance_km")
        if distance in (None, "", "None"):
            days_missing_distance.add(day)
        else:
            distance_by_day[day] += _dec(distance)

    work_by_day: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for entry in merged.get("work_time") or []:
        if entry.get("date") and entry.get("minutes"):
            work_by_day[entry["date"]].append(entry)

    days_with_earnings = sorted(income_by_day)
    minutes_by_day: Dict[str, Tuple[int, bool, bool]] = {}
    for day in days_with_earnings:
        if work_by_day.get(day):
            minutes, estimated, overlapped = _minutes_for_day(work_by_day[day])
            if minutes > 0:
                minutes_by_day[day] = (minutes, estimated, overlapped)
    used_days = [day for day in days_with_earnings if day in minutes_by_day]

    unavailable["days_with_earnings"] = len(days_with_earnings)
    if not days_with_earnings and not work_by_day:
        return {**unavailable, "unavailable_message": UNKNOWN_HOURS_AND_DAYS,
                "evidence_needed": [_NEED_WORK_TIME, _NEED_DAILY_EARNINGS]}
    if not days_with_earnings:
        return {**unavailable, "unavailable_message": UNKNOWN_DAYS,
                "evidence_needed": [_NEED_DAILY_EARNINGS]}
    if not used_days:
        return {**unavailable, "unavailable_message": UNKNOWN_HOURS,
                "evidence_needed": [
                    _NEED_WORK_TIME.replace("for each day.", "for the days your orders are dated."),
                ]}

    total_income = sum((income_by_day[d] for d in used_days), Decimal("0"))
    total_minutes = sum(minutes_by_day[d][0] for d in used_days)
    total_online_hours = Decimal(total_minutes) / Decimal(60)
    total_orders = sum(orders_by_day[d] for d in used_days)
    days_worked = len(used_days)
    daily_rates = [
        income_by_day[d] / (Decimal(minutes_by_day[d][0]) / Decimal(60)) for d in used_days
    ]

    notes: List[str] = []
    if days_worked < len(days_with_earnings):
        notes.append(
            f"Working hours were shown for {days_worked} of the {len(days_with_earnings)} "
            f"days with earnings, so only those {days_worked} days are used."
        )
    estimated_days = sum(1 for d in used_days if minutes_by_day[d][1])
    if estimated_days:
        notes.append(
            f"On {estimated_days} day(s) only a total login time was shown, without start "
            "and end times, so the longest one was used. Your real working hours may be higher."
        )
    if any(minutes_by_day[d][2] for d in used_days):
        notes.append("Login times that overlapped across platforms were counted only once.")

    # Fuel cost: same formula as the database view (distance x petrol cost per km).
    operating_cost: Optional[Decimal] = None
    cost_reason: Optional[str] = None
    try:
        profile = load_partner_profile(partner_id)
    except Exception:
        profile = None
    if profile is None:
        cost_reason = "Fuel cost is unknown because the partner profile could not be loaded."
    elif _dec(profile.petrol_per_km) <= 0:
        # The partners table forces petrol_per_km = 0 for electric vehicles.
        cost_reason = "Energy cost for an electric vehicle is not shown on the documents."
    elif any(d in days_missing_distance for d in used_days):
        cost_reason = "Fuel cost is unknown because some orders do not show a distance."
    else:
        distance = sum((distance_by_day[d] for d in used_days), Decimal("0"))
        operating_cost = distance * _dec(profile.petrol_per_km)

    return {
        "partner_id": partner_id,
        "period_start": used_days[0],
        "period_end": used_days[-1],
        "days_worked": days_worked,
        "total_orders": total_orders,
        "total_income": _money(total_income),
        "total_operating_cost": _money(operating_cost) if operating_cost is not None else None,
        "net_after_expenses": _money(total_income - operating_cost) if operating_cost is not None else None,
        "total_online_hours": _rate(total_online_hours),
        "avg_income_per_day": _money(total_income / Decimal(days_worked)),
        "avg_hours_per_day": _rate(total_online_hours / Decimal(days_worked)),
        "income_per_hour": _money(total_income / total_online_hours),
        "lowest_daily_hourly_rate": _money(min(daily_rates)),
        "highest_daily_hourly_rate": _money(max(daily_rates)),
        "source": "telegram",
        "submission_id": submission_id,
        "simulation_possible": True,
        "unavailable_message": None,
        "days_with_earnings": len(days_with_earnings),
        "max_working_days": TELEGRAM_MAX_WORKING_DAYS,
        "expenses_available": operating_cost is not None,
        "expenses_unavailable_reason": cost_reason,
        "evidence_label": "the documents you sent",
        "cost_note": "Fuel cost is estimated from the distances on your documents and your petrol cost per km.",
        "baseline_notes": notes,
        "evidence_needed": [],
        "value_sources": [
            _source("Days worked", "observed", f"{days_worked} dated day(s) on your documents with both earnings and working time."),
            _source(
                "Working hours",
                "assumption" if estimated_days else "observed",
                "Login or online time printed on your documents"
                + (f"; on {estimated_days} day(s) only totals were shown and the longest was used." if estimated_days else "."),
            ),
            _source("Earnings", "observed", "Order earnings printed on your documents for those days."),
            _source("Earnings per hour", "calculated", "Earnings divided by working hours."),
            _source("Fuel cost", "calculated", "Printed distance x the petrol cost per km in your FairPay profile.")
            if operating_cost is not None
            else _source("Fuel cost", "unavailable", cost_reason or "Not shown on your documents."),
            _source("Extra hours earn your average rate", "assumption", "Used by the projections, not by the baseline."),
        ],
    }


# ---------------------------------------------------------------------------
# Deterministic projections
# ---------------------------------------------------------------------------

def compute_opportunity(
    baseline: Dict[str, Any], scenario_type: str, value: Decimal
) -> Dict[str, Any]:
    """Project one scenario from the baseline. Pure arithmetic, no LLM, no I/O."""
    if not baseline.get("simulation_possible", True):
        raise ValueError(baseline.get("unavailable_message") or UNKNOWN_HOURS_AND_DAYS)
    if scenario_type not in SCENARIO_TYPES:
        raise ValueError(
            f"scenario_type must be one of {', '.join(SCENARIO_TYPES)}; got '{scenario_type}'"
        )

    value = _dec(value)
    days_worked = Decimal(baseline["days_worked"])
    income_per_hour = _dec(baseline["income_per_hour"])
    avg_income_per_day = _dec(baseline["avg_income_per_day"])
    avg_hours_per_day = _dec(baseline["avg_hours_per_day"])
    total_income = _dec(baseline["total_income"])
    total_operating_cost = _dec(baseline["total_operating_cost"])
    low_rate = _dec(baseline["lowest_daily_hourly_rate"])
    period = f"{baseline['period_start']} to {baseline['period_end']}"
    evidence = baseline.get("evidence_label", "your past 30 days")
    source_notes = list(baseline.get("baseline_notes") or [])

    if scenario_type == "hours":
        if not (MIN_HOURS_PER_DAY <= value <= MAX_HOURS_PER_DAY):
            raise ValueError(
                f"hours_per_day must be between {MIN_HOURS_PER_DAY} and {MAX_HOURS_PER_DAY}"
            )
        baseline_earnings = total_income
        projected_earnings = income_per_hour * value * days_worked
        conservative = low_rate * value * days_worked
        scenario_inputs = {
            "hours_per_day": float(_rate(value)),
            "baseline_hours_per_day": float(avg_hours_per_day),
            "days_worked": int(days_worked),
        }
        basis = f"Earnings before expenses over {int(days_worked)} working days ({period})"
        notes = [
            f"You actually worked about {_hrs(avg_hours_per_day)} hours a day and earned "
            f"{_rs(total_income)} in total over this period.",
            f"This estimate assumes every hour earns the same as your average so far: "
            f"{_rs(income_per_hour)} per hour.",
            "Extra hours may earn less than this. Demand is not the same at all hours, "
            "so quieter hours can pay less than your average.",
            f"On your slowest days you earned {_rs(low_rate)} per hour. At that rate the same "
            f"{_hrs(value)} hours a day would come to about {_rs(conservative)}.",
            *source_notes,
            f"This is an estimate based on {evidence}, not guaranteed income.",
        ]

    elif scenario_type == "days":
        max_days = Decimal(baseline.get("max_working_days") or (days_worked * MAX_DAYS_MULTIPLIER).to_integral_value(rounding=ROUND_HALF_UP))
        if not (Decimal("1") <= value <= max_days):
            raise ValueError(f"working_days must be between 1 and {int(max_days)}")
        baseline_earnings = total_income
        projected_earnings = avg_income_per_day * value
        conservative = low_rate * avg_hours_per_day * value
        scenario_inputs = {
            "working_days": int(value),
            "baseline_working_days": int(days_worked),
            "avg_hours_per_day": float(avg_hours_per_day),
        }
        basis = f"Earnings before expenses over the period {period}"
        notes = [
            f"You actually worked {int(days_worked)} days and earned {_rs(total_income)} in total.",
            f"This estimate assumes each extra day earns the same as your average day so far: "
            f"{_rs(avg_income_per_day)} per day, working about {_hrs(avg_hours_per_day)} hours.",
            "Earnings per day are not fixed. A new day could be busier or quieter than your average.",
            f"On your slowest days the rate was {_rs(low_rate)} per hour, which would make "
            f"{int(value)} days closer to {_rs(conservative)}.",
            *source_notes,
            f"This is an estimate based on {evidence}, not guaranteed income.",
        ]

    else:  # expenses
        if not baseline.get("expenses_available", True) or baseline.get("total_operating_cost") is None:
            raise ValueError(
                baseline.get("expenses_unavailable_reason") or "Fuel cost is unknown, so expenses cannot be simulated."
            )
        if not (MIN_EXPENSE_CHANGE_PCT <= value <= MAX_EXPENSE_CHANGE_PCT):
            raise ValueError(
                f"expense_change_percent must be between {MIN_EXPENSE_CHANGE_PCT} "
                f"and {MAX_EXPENSE_CHANGE_PCT}"
            )
        projected_cost = total_operating_cost * (Decimal("1") + value / Decimal("100"))
        baseline_earnings = total_income - total_operating_cost
        projected_earnings = total_income - projected_cost
        conservative = None
        scenario_inputs = {
            "expense_change_percent": float(_rate(value)),
            "baseline_operating_cost": float(total_operating_cost),
            "projected_operating_cost": float(_money(projected_cost)),
        }
        basis = f"Earnings after fuel and energy expenses over the period {period}"
        notes = [
            f"You earned {_rs(total_income)} and spent about {_rs(total_operating_cost)} on fuel "
            f"or energy, leaving {_rs(baseline_earnings)}.",
            f"This estimate changes only your fuel or energy cost, to "
            f"{_rs(projected_cost)}. Your earnings are kept the same.",
            f"{baseline.get('cost_note', 'Only fuel and energy costs recorded in your history are counted.')} "
            "Repairs, insurance, phone and data costs are not included.",
            *source_notes,
            f"This is an estimate based on {evidence}, not guaranteed savings.",
        ]

    baseline_earnings = _money(baseline_earnings)
    projected_earnings = _money(projected_earnings)

    result = {
        "partner_id": baseline["partner_id"],
        "scenario_type": scenario_type,
        "scenario_inputs": scenario_inputs,
        "baseline_earnings": baseline_earnings,
        "projected_earnings": projected_earnings,
        "estimated_difference": _money(projected_earnings - baseline_earnings),
        "assumptions": {"basis": basis, "notes": notes},
    }
    if conservative is not None:
        result["conservative_estimate"] = _money(conservative)
    return result


# ---------------------------------------------------------------------------
# Agent input - the three personas assess the scenario, not the audit report
# ---------------------------------------------------------------------------

def debate_state_from_opportunity(
    projection: Dict[str, Any], baseline: Dict[str, Any]
) -> Dict[str, Any]:
    """Build the debate graph input describing this scenario and its numbers."""
    labels = {
        "hours": "changing daily working hours",
        "days": "changing the number of working days",
        "expenses": "changing operating expenses",
    }
    inputs = ", ".join(f"{k}={v}" for k, v in projection["scenario_inputs"].items())
    scenario = (
        f"The delivery partner is considering {labels[projection['scenario_type']]}. "
        f"Selected inputs: {inputs}."
    )
    calculations = (
        f"Basis: {projection['assumptions']['basis']}. "
        f"Actual historical earnings: {projection['baseline_earnings']}. "
        f"Estimated earnings under this scenario: {projection['projected_earnings']}. "
        f"Estimated change: {projection['estimated_difference']}. "
        f"Historical reference: {baseline['days_worked']} days worked, "
        f"{baseline['total_orders']} orders, {baseline['avg_hours_per_day']} hours per day on "
        f"average, {baseline['income_per_hour']} earned per hour on average "
        f"(day-to-day range {baseline['lowest_daily_hourly_rate']} to "
        f"{baseline['highest_daily_hourly_rate']} per hour), "
        f"{baseline['total_operating_cost']} total fuel or energy cost. "
        f"Assumptions: {' '.join(projection['assumptions']['notes'])}"
    )
    return {"scenario": scenario, "calculations": calculations}


# ---------------------------------------------------------------------------
# Hours worked, per platform (for the audit result display)
# ---------------------------------------------------------------------------
# Two different things, never mixed up:
# * online hours - time logged in, from login/work sessions. A partner is often
#   online on several apps at once, so shared sessions cannot be split by app.
# * order hours  - time spent on one app's orders, from order accepted to
#   delivered. Overlapping orders on the same app count once.

def _hours(minutes: float) -> str:
    return str(_rate(Decimal(str(minutes)) / Decimal(60)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _union_seconds(intervals: List[Tuple[float, float]]) -> float:
    total, current_start, current_end = 0.0, None, 0.0
    for start, end in sorted(intervals):
        if current_start is None or start > current_end:
            if current_start is not None:
                total += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    if current_start is not None:
        total += current_end - current_start
    return total


def history_hours_by_platform(
    partner_id: str, platforms: List[str], conn: Optional[Connection] = None
) -> Dict[str, Any]:
    """Hours worked from the 30-day history: order hours per app, online hours overall."""
    with db.session(conn) as cx, cx.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT platform, order_timestamp, completed_timestamp FROM orders WHERE partner_id = %s",
            (partner_id,),
        )
        orders = cur.fetchall()
        cur.execute(
            "SELECT session_date, online_start, online_end FROM work_sessions WHERE partner_id = %s",
            (partner_id,),
        )
        sessions = cur.fetchall()

    by_platform: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
    days_by_platform: Dict[str, set] = defaultdict(set)
    for row in orders:
        start, end = row["order_timestamp"], row["completed_timestamp"]
        if start and end and end > start:
            key = platform_key(row["platform"])
            by_platform[key].append((start.timestamp(), end.timestamp()))
            days_by_platform[key].add(start.date())

    online_seconds = _union_seconds(
        [(s["online_start"].timestamp(), s["online_end"].timestamp()) for s in sessions
         if s["online_start"] and s["online_end"]]
    )
    online_days = len({s["session_date"] for s in sessions})

    rows = []
    for platform in platforms:
        key = platform_key(platform)
        intervals = by_platform.get(key) or []
        rows.append({
            "platform": platform,
            "order_hours": _hours(_union_seconds(intervals) / 60) if intervals else None,
            "order_hours_note": (
                "From order accepted to delivered, on the dates of your orders."
                if intervals else "No order times are recorded for this app."
            ),
            "online_hours": None,
            "online_hours_note": (
                "Your login time is shared by all apps you were online on at the same time, "
                "so it cannot be split by app."
            ),
            "days": len(days_by_platform.get(key) or []),
        })
    return {
        "source": "history",
        "platforms": rows,
        "all_apps_online_hours": _hours(online_seconds / 60) if sessions else None,
        "all_apps_online_days": online_days,
        "all_apps_note": (
            "Total time logged in across all apps (overlapping time counted once)."
            if sessions else "No login sessions are recorded."
        ),
    }


def work_time_hours_by_platform(work_time: List[Dict[str, Any]], platforms: List[str]) -> Dict[str, Any]:
    """Hours worked from a Telegram audit: login time printed on each app's own documents.

    Uses the same per-day rule as the simulator baseline (login ranges merged,
    longest printed total when only totals are shown). Never estimated.
    """
    def _total(entries: List[Dict[str, Any]]) -> Tuple[int, int, bool]:
        by_day: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for entry in entries:
            if entry.get("date") and entry.get("minutes"):
                by_day[entry["date"]].append(entry)
        minutes, estimated = 0, False
        for day_entries in by_day.values():
            day_minutes, day_estimated, _ = _minutes_for_day(day_entries)
            minutes += day_minutes
            estimated = estimated or day_estimated
        return minutes, len(by_day), estimated

    rows = []
    for platform in platforms:
        entries = [e for e in work_time if platform_key(e.get("platform", "")) == platform_key(platform)]
        minutes, days, estimated = _total(entries)
        rows.append({
            "platform": platform,
            "online_hours": _hours(minutes) if minutes else None,
            "online_hours_note": (
                ("Login or online time shown on your documents."
                 + (" Where only a total was shown, that total was used." if estimated else ""))
                if minutes else
                "Not available: your documents for this app do not show login or online hours with dates."
            ),
            "order_hours": None,
            "order_hours_note": "Your documents do not show when each order started and ended.",
            "days": days,
        })
    all_minutes, all_days, _ = _total(work_time)
    return {
        "source": "telegram",
        "platforms": rows,
        "all_apps_online_hours": _hours(all_minutes) if all_minutes else None,
        "all_apps_online_days": all_days,
        "all_apps_note": (
            "Login time from all your documents, with time on two apps at once counted once."
            if all_minutes else "Not available: no login or online hours with dates were found in your documents."
        ),
    }


# ---------------------------------------------------------------------------
# Figures a typed "what if" scenario can be measured against
# ---------------------------------------------------------------------------

def _figure(label: str, value: Any, money: bool = True) -> Dict[str, Any]:
    return {"label": label, "value": _dec(value), "money": money}


def load_scenario_figures(
    partner_id: str,
    platform: str,
    submission_id: Optional[str] = None,
    merged: Optional[Dict[str, Any]] = None,
    conn: Optional[Connection] = None,
) -> Dict[str, Any]:
    """The partner's real numbers, named so a scenario can be turned into a sum.

    Returns ``{"figures": {name: {label, value, money}}, "period": str | None}``.
    Only observed or already-calculated values are included; a missing number
    is simply absent.
    """
    figures: Dict[str, Dict[str, Any]] = {}
    period: Optional[str] = None
    try:
        baseline = load_telegram_baseline(partner_id, submission_id, merged) if merged is not None \
            else load_earnings_baseline(partner_id)
    except Exception:
        baseline = {"simulation_possible": False}
    if baseline.get("simulation_possible"):
        period = f"{baseline['days_worked']} days ({baseline['period_start']} to {baseline['period_end']})"
        figures.update({
            "total_income": _figure("Total earnings, all apps", baseline["total_income"]),
            "days_worked": _figure("Days worked", baseline["days_worked"], money=False),
            "total_orders": _figure("Orders, all apps", baseline["total_orders"], money=False),
            "total_online_hours": _figure("Hours online, all apps", baseline["total_online_hours"], money=False),
            "avg_hours_per_day": _figure("Average hours online per day", baseline["avg_hours_per_day"], money=False),
            "avg_income_per_day": _figure("Average earnings per day", baseline["avg_income_per_day"]),
            "income_per_hour": _figure("Average earnings per hour online", baseline["income_per_hour"]),
        })
        if baseline.get("total_operating_cost") is not None:
            figures["total_fuel_cost"] = _figure("Fuel or energy cost, all apps", baseline["total_operating_cost"])

    key = platform_key(platform)
    if merged is None:
        try:
            with db.session(conn) as cx, cx.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT platform, SUM(net_amount) AS income, COUNT(*) AS orders "
                    "FROM earnings WHERE partner_id = %s GROUP BY platform",
                    (partner_id,),
                )
                for row in cur.fetchall():
                    if platform_key(row["platform"]) == key:
                        figures["platform_income"] = _figure(f"Earnings on {platform}", row["income"])
                        figures["platform_orders"] = _figure(f"Orders on {platform}", row["orders"], money=False)
                cur.execute(
                    "SELECT platform, incentive_type, SUM(amount) AS total FROM incentives "
                    "WHERE partner_id = %s GROUP BY platform, incentive_type",
                    (partner_id,),
                )
                for row in cur.fetchall():
                    if platform_key(row["platform"]) == key:
                        kind = row["incentive_type"]
                        figures[f"platform_{kind}_incentives"] = _figure(
                            f"{kind.replace('_', ' ').capitalize()} incentives on {platform}", row["total"]
                        )
                cur.execute(
                    "SELECT platform, category, SUM(amount) AS total FROM deductions "
                    "WHERE partner_id = %s GROUP BY platform, category",
                    (partner_id,),
                )
                for row in cur.fetchall():
                    if platform_key(row["platform"]) == key:
                        figures[f"platform_{row['category']}_deductions"] = _figure(
                            f"{row['category'].capitalize()} deductions on {platform}", row["total"]
                        )
        except Exception:
            # These per-platform figures are optional detail: the scenario still
            # has the baseline above. The pool has already rolled the borrowed
            # connection back, so the failure cannot reach another caller.
            pass
    else:
        transactions = {t.get("transaction_id"): t for t in merged.get("transactions") or []}
        income, orders, incentives = Decimal("0"), 0, Decimal("0")
        for prediction in merged.get("reconciliation_predictions") or []:
            if prediction.get("anomaly_type") == "extra_unexplained_settlement":
                continue
            if platform_key(prediction.get("platform", "")) != key:
                continue
            income += _dec(prediction.get("expected_amount"))
            orders += 1
            tx = transactions.get(prediction.get("transaction_id")) or {}
            incentives += sum((_dec(i.get("amount")) for i in tx.get("incentives") or []), Decimal("0"))
        if orders:
            figures["platform_income"] = _figure(f"Earnings on {platform} in your documents", income)
            figures["platform_orders"] = _figure(f"Orders on {platform} in your documents", orders, money=False)
            if incentives > 0:
                figures["platform_incentives"] = _figure(f"Incentives on {platform} in your documents", incentives)
    return {"figures": figures, "period": period}


def describe_figures(figures: Dict[str, Dict[str, Any]]) -> str:
    """Figures as plain lines for the LLM prompts (name, meaning, value)."""
    if not figures:
        return "No earnings figures are available for this partner."
    return "\n".join(
        f"{name}: {f['label']} = {_rs(f['value']) if f['money'] else f['value']}"
        for name, f in figures.items()
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

_SIMULATIONS_DDL = """
CREATE TABLE IF NOT EXISTS simulations (
  simulation_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  partner_id           TEXT NOT NULL REFERENCES partners(partner_id),
  scenario_type        TEXT NOT NULL CHECK (scenario_type IN ('hours','days','expenses')),
  scenario_inputs      JSONB NOT NULL,
  baseline_earnings    NUMERIC(12,2) NOT NULL,
  projected_earnings   NUMERIC(12,2) NOT NULL,
  estimated_difference NUMERIC(12,2) NOT NULL,
  assumptions          JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_simulations_partner_created
  ON simulations (partner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_simulations_created
  ON simulations (created_at DESC);
"""

_table_ready = False


def ensure_simulations_table() -> None:
    """Create the simulations table once per process if it is not there yet.

    Idempotent and additive - it never touches the earnings, settlements or
    audit tables.
    """
    global _table_ready
    if _table_ready:
        return
    # The pool commits when the block exits cleanly and rolls back if it
    # raises, so there is no explicit commit/rollback to get wrong here.
    with db.session() as cx, cx.cursor() as cur:
        cur.execute(_SIMULATIONS_DDL)
    _table_ready = True


def partner_exists(partner_id: str, conn: Optional[Connection] = None) -> bool:
    with db.session(conn) as cx, cx.cursor() as cur:
        cur.execute("SELECT 1 FROM partners WHERE partner_id = %s", (partner_id,))
        return cur.fetchone() is not None


def insert_simulation(projection: Dict[str, Any], conn: Optional[Connection] = None) -> Dict[str, Any]:
    """Persist one completed simulation and return the stored row."""
    ensure_simulations_table()
    with db.session(conn) as cx:
        with cx.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO simulations (
                    partner_id, scenario_type, scenario_inputs,
                    baseline_earnings, projected_earnings, estimated_difference,
                    assumptions
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING simulation_id, partner_id, scenario_type, scenario_inputs,
                          baseline_earnings, projected_earnings, estimated_difference,
                          assumptions, created_at
                """,
                (
                    projection["partner_id"],
                    projection["scenario_type"],
                    Jsonb(projection["scenario_inputs"]),
                    projection["baseline_earnings"],
                    projection["projected_earnings"],
                    projection["estimated_difference"],
                    Jsonb(projection["assumptions"]),
                ),
            )
            row = cur.fetchone()
    return _row_to_simulation(row)


def fetch_simulations(
    partner_id: str, limit: int = 50, conn: Optional[Connection] = None
) -> List[Dict[str, Any]]:
    """Return this partner's saved simulations, newest first."""
    ensure_simulations_table()
    with db.session(conn) as cx, cx.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT simulation_id, partner_id, scenario_type, scenario_inputs,
                   baseline_earnings, projected_earnings, estimated_difference,
                   assumptions, created_at
            FROM simulations
            WHERE partner_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (partner_id, limit),
        )
        rows = cur.fetchall()
    return [_row_to_simulation(r) for r in rows]


def _row_to_simulation(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "simulation_id": str(row["simulation_id"]),
        "partner_id": row["partner_id"],
        "scenario_type": row["scenario_type"],
        "scenario_inputs": row["scenario_inputs"],
        "baseline_earnings": _dec(row["baseline_earnings"]),
        "projected_earnings": _dec(row["projected_earnings"]),
        "estimated_difference": _dec(row["estimated_difference"]),
        "assumptions": row["assumptions"],
        "created_at": row["created_at"].isoformat(),
    }
