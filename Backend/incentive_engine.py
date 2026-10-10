"""Deterministic incentive evaluation: counting, conditions, payment, outcome.

Given one documented rule (from ``incentive_rules``) and the evidence (from
``incentive_evidence``), this module decides - with no LLM and no estimates:

* which orders fall in the incentive period, and how many count towards the
  target. When the rule does not document how a kind of order counts
  (batched, multi-pickup, cancelled, incomplete, crossing midnight), the count
  becomes a range ``[min, max]`` and the orders involved are listed;
* each eligibility condition as PASS, FAIL or UNKNOWN. UNKNOWN is never turned
  into PASS or FAIL;
* whether the incentive payment is visible;
* one primary outcome for the period.

Outcomes
--------
INCENTIVE_RECEIVED, COUNTING_MISMATCH, ELIGIBILITY_FAILURE,
PAYMENT_NOT_OBSERVED, POSSIBLE_PAYMENT_FAILURE, INSUFFICIENT_EVIDENCE,
AMBIGUOUS. A counting mismatch is not an underpayment, an eligibility
failure is not wrongdoing, and nothing here is described as either.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from incentive_evidence import (
    CoverageRange,
    IncentiveEvidence,
    MetricObservation,
    PlatformCountObservation,
    Provenance,
    TIP_NOT_COUNTED,
    TripObservation,
    covered,
)
from incentive_rules import (
    ESTABLISHED,
    RULE_AMBIGUOUS,
    RULE_NOT_ESTABLISHED_TEXT,
    IncentiveRule,
    RuleResolution,
)

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"

INCENTIVE_RECEIVED = "INCENTIVE_RECEIVED"
COUNTING_MISMATCH = "COUNTING_MISMATCH"
ELIGIBILITY_FAILURE = "ELIGIBILITY_FAILURE"
PAYMENT_NOT_OBSERVED = "PAYMENT_NOT_OBSERVED"
POSSIBLE_PAYMENT_FAILURE = "POSSIBLE_PAYMENT_FAILURE"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
AMBIGUOUS = "AMBIGUOUS"
OUTCOMES = (INCENTIVE_RECEIVED, COUNTING_MISMATCH, ELIGIBILITY_FAILURE, PAYMENT_NOT_OBSERVED,
            POSSIBLE_PAYMENT_FAILURE, INSUFFICIENT_EVIDENCE, AMBIGUOUS)

# When an incentive is paid comes from the rule's documented payout timing
# (``payout_window_days`` in the knowledge base). If it is not documented,
# FairPay does not assume a waiting time.


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------

class CountedOrder(BaseModel):
    order_id: str
    units_min: int
    units_max: int
    reason: str
    provenance: Provenance
    when: Optional[str] = None
    status: str = ""


class CountResult(BaseModel):
    min_count: int
    max_count: int
    definite: List[CountedOrder] = Field(default_factory=list, description="Count under any reading of the rule")
    uncertain: List[CountedOrder] = Field(default_factory=list, description="Count only under some readings")
    excluded: List[CountedOrder] = Field(default_factory=list, description="Do not count, with the reason")
    outside_period: int = 0
    open_questions: List[str] = Field(default_factory=list, description="Undocumented counting rules that matter here")
    inferred: bool = False


class ConditionResult(BaseModel):
    condition: str
    label: str
    required: str
    observed: str
    status: Literal["PASS", "FAIL", "UNKNOWN"]
    provenance: Provenance
    detail: str = ""


class PaymentCheck(BaseModel):
    status: Literal["paid", "paid_different_amount", "not_visible", "not_checkable"]
    amount: Optional[Decimal] = None
    detail: str = ""
    sources: List[str] = Field(default_factory=list)


class CountingMismatch(BaseModel):
    platform_count: int
    fairpay_min: int
    fairpay_max: int
    difference: int
    basis: Literal["documented_rule", "undocumented_counting_rule", "unidentified"]
    orders: List[str] = Field(default_factory=list, description="Orders that appear not to have been counted")
    explanation: str
    decisive: bool = Field(description="True when it is why the target was not reached")


class PeriodEvaluation(BaseModel):
    platform: str
    period_start: date
    period_end: date
    incentive_name: str = ""
    incentive_type: str = ""
    rule_status: str
    rule: Optional[IncentiveRule] = None
    rule_note: str = ""
    retrieval_backend: str = "local"
    count: Optional[CountResult] = None
    platform_count: Optional[int] = None
    conditions: List[ConditionResult] = Field(default_factory=list)
    payment: Optional[PaymentCheck] = None
    mismatch: Optional[CountingMismatch] = None
    conflicts: List[str] = Field(default_factory=list)
    outcome: str
    target_orders: Optional[int] = None
    reward_amount: Optional[Decimal] = None
    target_source: Literal["rule", "offer_in_evidence", "not_documented"] = "rule"
    near_miss: bool = False
    shortfall: Optional[int] = None
    trips_complete: bool = False
    notes: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------

def period_for(day: date, rule: IncentiveRule) -> Tuple[date, date]:
    if rule.period_scope == "day":
        return day, day
    if rule.period_scope == "week_mon_sun":
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=6)
    return rule.effective_from, rule.effective_to or rule.effective_from


def _in(day: Optional[date], start: date, end: date) -> bool:
    return day is not None and start <= day <= end


def _clock_minutes(value: Optional[str]) -> Optional[int]:
    """'18:00' -> 1080. None when it cannot be read."""
    try:
        hours, minutes = (int(part) for part in (value or "").strip().split(":"))
    except ValueError:
        return None
    return hours * 60 + minutes


def window_bounds(window: str) -> Optional[Tuple[int, int]]:
    """A window as minutes from midnight, with the end after the start.

    '18:00-00:00' becomes (1080, 1440): a window ending at or before its start
    is taken to run into the next day.
    """
    parts = (window or "").split("-")
    if len(parts) != 2:
        return None
    start, end = (_clock_minutes(part) for part in parts)
    if start is None or end is None:
        return None
    return start, end if end > start else end + 24 * 60


def time_in_window(when: datetime, window: str) -> bool:
    """Is this local time inside the window, counting a window that wraps midnight."""
    bounds = window_bounds(window)
    if bounds is None:
        return False
    start, end = bounds
    minute = when.hour * 60 + when.minute
    return start <= minute <= end or start <= minute + 24 * 60 <= end


def time_in_any_window(when: datetime, windows: List[str]) -> bool:
    return any(time_in_window(when, w) for w in windows or [])


def _fmt(dt: Optional[datetime]) -> Optional[str]:
    return dt.strftime("%d %b %H:%M").lstrip("0") if dt else None


def _period_membership(trip: TripObservation, rule: IncentiveRule, start: date, end: date
                       ) -> Tuple[str, Provenance, str]:
    """'in', 'out' or 'uncertain' for this period, with provenance and reason."""
    dates = trip.dates()
    basis = rule.counting.day_boundary
    if basis and dates.get(basis):
        inside = _in(dates[basis], start, end)
        return ("in" if inside else "out"), "RETRIEVED_RULE", f"the rule counts orders by their {basis.replace('_', ' ')} time"
    candidates = {d for d in (dates["accepted_at"], dates["completed_at"]) if d}
    if not candidates and dates["printed"]:
        candidates = {dates["printed"]}
    if not candidates:
        return "uncertain", "UNKNOWN", "no date is shown for this order"
    inside = {_in(d, start, end) for d in candidates}
    if inside == {True}:
        return "in", "OBSERVED", ""
    if inside == {False}:
        return "out", "OBSERVED", ""
    return ("uncertain", "UNKNOWN",
            "it was accepted and delivered on different days, and the rules on file do not say which day counts")


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------

_STATUS_TEXT = {
    "cancelled": "cancelled",
    "incomplete": "not completed",
}


def count_orders(trips: List[TripObservation], rule: IncentiveRule, start: date, end: date) -> CountResult:
    """How many orders in [start, end] count towards the target, as a range."""
    result = CountResult(min_count=0, max_count=0)
    questions: set[str] = set()
    batches: Dict[str, List[TripObservation]] = defaultdict(list)
    singles: List[TripObservation] = []

    for trip in trips:
        membership, prov, why = _period_membership(trip, rule, start, end)
        if membership == "out":
            result.outside_period += 1
            continue
        if membership == "uncertain":
            questions.add("Which day an order crossing midnight counts for")
            result.uncertain.append(CountedOrder(
                order_id=trip.order_id, units_min=0, units_max=1, reason=f"May belong to this period: {why}.",
                provenance="UNKNOWN", when=_fmt(trip.order_time()), status=trip.status))
            continue
        if trip.batch_id and trip.batch_kind and trip.status == "delivered":
            batches[trip.batch_id].append(trip)
        else:
            singles.append(trip)

    def _eligible_by_terms(trip: TripObservation) -> Tuple[Optional[bool], str, Provenance]:
        """Time-window and distance conditions of the rule for one order."""
        if rule.time_windows:
            when = trip.order_time()
            if when is None:
                return None, "no order time is shown, and only orders inside the rule's time windows count", "UNKNOWN"
            if not time_in_any_window(when, rule.time_windows):
                return False, f"outside the rule's time windows ({', '.join(rule.time_windows)})", "RETRIEVED_RULE"
        if rule.min_distance_km is not None:
            if trip.distance_km is None:
                return None, f"no distance is shown, and only orders of at least {rule.min_distance_km} km count", "UNKNOWN"
            if trip.distance_km < rule.min_distance_km:
                return False, f"shorter than the rule's minimum of {rule.min_distance_km} km", "RETRIEVED_RULE"
        return True, "", "OBSERVED"

    for trip in singles:
        when = _fmt(trip.order_time())
        if trip.status == "rejected":
            result.excluded.append(CountedOrder(order_id=trip.order_id, units_min=0, units_max=0,
                                                reason="Not accepted, so it is not an order you did.",
                                                provenance="OBSERVED", when=when, status=trip.status))
            continue
        if trip.status in ("cancelled", "incomplete"):
            treatment = getattr(rule.counting, trip.status)
            text = _STATUS_TEXT[trip.status]
            if treatment == "excluded":
                result.excluded.append(CountedOrder(order_id=trip.order_id, units_min=0, units_max=0,
                                                    reason=f"It was {text}; the rule excludes {text} orders.",
                                                    provenance="RETRIEVED_RULE", when=when, status=trip.status))
                continue
            if treatment is None:
                questions.add(f"Whether {text} orders count")
                result.uncertain.append(CountedOrder(
                    order_id=trip.order_id, units_min=0, units_max=1,
                    reason=f"It was {text}; the rules on file do not say whether {text} orders count.",
                    provenance="UNKNOWN", when=when, status=trip.status))
                continue
            # documented as counted: falls through to the terms check
        if trip.status == "unknown":
            result.uncertain.append(CountedOrder(order_id=trip.order_id, units_min=0, units_max=1,
                                                 reason="The documents do not show whether it was delivered.",
                                                 provenance="UNKNOWN", when=when, status=trip.status))
            continue
        ok, why, prov = _eligible_by_terms(trip)
        if ok is False:
            result.excluded.append(CountedOrder(order_id=trip.order_id, units_min=0, units_max=0,
                                                reason=f"It was {why}.", provenance=prov, when=when, status=trip.status))
        elif ok is None:
            result.uncertain.append(CountedOrder(order_id=trip.order_id, units_min=0, units_max=1,
                                                 reason=f"Cannot tell whether it counts: {why}.", provenance="UNKNOWN",
                                                 when=when, status=trip.status))
        else:
            prov = "INFERRED" if trip.status_provenance == "INFERRED" else "OBSERVED"
            result.inferred = result.inferred or prov == "INFERRED"
            result.definite.append(CountedOrder(order_id=trip.order_id, units_min=1, units_max=1,
                                                reason="Delivered in the period" + (" (delivery inferred)" if prov == "INFERRED" else "") + ".",
                                                provenance=prov, when=when, status=trip.status))

    for batch_id, members in sorted(batches.items()):
        members = sorted(members, key=lambda t: (t.order_time() or datetime.min, t.order_id))
        kind = members[0].batch_kind or "multi_order"
        treatment = rule.counting.multi_order if kind == "multi_order" else rule.counting.multi_pickup
        label = "batched trip (several orders delivered together)" if kind == "multi_order" else "multi-pickup trip"
        eligible = []
        for trip in members:
            ok, why, prov = _eligible_by_terms(trip)
            if ok is False:
                result.excluded.append(CountedOrder(order_id=trip.order_id, units_min=0, units_max=0,
                                                    reason=f"It was {why}.", provenance=prov,
                                                    when=_fmt(trip.order_time()), status=trip.status))
            elif ok is None:
                result.uncertain.append(CountedOrder(order_id=trip.order_id, units_min=0, units_max=1,
                                                     reason=f"Cannot tell whether it counts: {why}.", provenance="UNKNOWN",
                                                     when=_fmt(trip.order_time()), status=trip.status))
            else:
                eligible.append(trip)
        if not eligible:
            continue
        first, rest = eligible[0], eligible[1:]
        result.definite.append(CountedOrder(order_id=first.order_id, units_min=1, units_max=1,
                                            reason=f"Delivered in the period, part of a {label}.",
                                            provenance="OBSERVED", when=_fmt(first.order_time()), status=first.status))
        for trip in rest:
            when = _fmt(trip.order_time())
            if treatment == "each_order":
                result.definite.append(CountedOrder(order_id=trip.order_id, units_min=1, units_max=1,
                                                    reason=f"Part of a {label}; the rule counts each order.",
                                                    provenance="RETRIEVED_RULE", when=when, status=trip.status))
            elif treatment == "once_per_trip":
                result.excluded.append(CountedOrder(order_id=trip.order_id, units_min=0, units_max=0,
                                                    reason=f"Part of a {label}; the rule counts the trip once.",
                                                    provenance="RETRIEVED_RULE", when=when, status=trip.status))
            else:
                questions.add("Whether each order in a batched trip counts" if kind == "multi_order"
                              else "Whether each order in a multi-pickup trip counts")
                result.uncertain.append(CountedOrder(
                    order_id=trip.order_id, units_min=0, units_max=1,
                    reason=f"Part of a {label}; the rules on file do not say whether it counts as one order or each order.",
                    provenance="UNKNOWN", when=when, status=trip.status))

    result.min_count = sum(o.units_min for o in result.definite)
    result.max_count = result.min_count + sum(o.units_max for o in result.uncertain)
    result.open_questions = sorted(questions)
    return result


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------

def _metric_for(metrics: List[MetricObservation], name: str, start: date, end: date) -> Tuple[List[MetricObservation], List[MetricObservation]]:
    """Figures for exactly this period, and figures for other periods."""
    same = [m for m in metrics if m.metric == name and m.period_start == start and m.period_end == end]
    other = [m for m in metrics if m.metric == name and not (m.period_start == start and m.period_end == end)]
    return same, other


def _pct(value: Decimal) -> str:
    return f"{(value * 100).quantize(Decimal('1'))}%"


def _rate_condition(rule_value: Optional[Decimal], metrics: List[MetricObservation], name: str,
                    start: date, end: date, minimum: bool) -> Optional[ConditionResult]:
    if rule_value is None:
        return None
    label = "Acceptance rate" if name == "acceptance_rate" else "Rejection rate"
    required = f"{'at least' if minimum else 'at most'} {_pct(rule_value)}"
    same, other = _metric_for(metrics, name, start, end)
    values = sorted({m.value for m in same})
    if len(values) > 1:
        return ConditionResult(condition=name, label=label, required=required,
                               observed="different figures: " + ", ".join(_pct(v) for v in values),
                               status=UNKNOWN, provenance="UNKNOWN",
                               detail="The documents show conflicting figures for this period.")
    if values:
        value = values[0]
        ok = value >= rule_value if minimum else value <= rule_value
        return ConditionResult(condition=name, label=label, required=required, observed=_pct(value),
                               status=PASS if ok else FAIL, provenance="OBSERVED",
                               detail=f"Shown on {same[0].source}.")
    detail = "Not shown in the evidence for this period."
    if other:
        detail = (f"The evidence shows {label.lower()} only for other periods "
                  f"({', '.join(f'{m.period_start} to {m.period_end}' for m in other[:2])}), not this one.")
    return ConditionResult(condition=name, label=label, required=required, observed="not available",
                           status=UNKNOWN, provenance="UNKNOWN", detail=detail)


def _login_condition(rule: IncentiveRule, metrics: List[MetricObservation], start: date, end: date,
                     login_coverage: Optional[List[CoverageRange]] = None) -> Optional[ConditionResult]:
    if rule.min_login_minutes is None:
        return None
    required = f"at least {_hours(rule.min_login_minutes)}"
    exact = [m for m in metrics if m.metric == "login_minutes" and m.period_start == start and m.period_end == end]
    inside = [m for m in metrics if m.metric == "login_minutes" and start <= m.period_start and m.period_end <= end]
    if len({m.value for m in exact}) == 1 and len(inside) == len(exact):
        value = int(exact[0].value)
        return ConditionResult(condition="login_minutes", label="Login time", required=required,
                               observed=_hours(value), status=PASS if value >= rule.min_login_minutes else FAIL,
                               provenance="OBSERVED", detail=f"Shown on {exact[0].source}.")
    lower = 0
    if inside:
        by_day: Dict[date, List[int]] = defaultdict(list)
        for m in inside:
            by_day[m.period_start].append(int(m.value))
        lower = sum(max(v) for v in by_day.values())
        if lower >= rule.min_login_minutes:
            return ConditionResult(condition="login_minutes", label="Login time", required=required,
                                   observed=f"at least {_hours(lower)}", status=PASS, provenance="CALCULATED",
                                   detail="Added up from the login times shown.")
        # Every day of the period has a login figure, so this is the real total.
        days_in_period = (end - start).days + 1
        if len(by_day) >= days_in_period:
            return ConditionResult(condition="login_minutes", label="Login time", required=required,
                                   observed=_hours(lower), status=FAIL, provenance="CALCULATED",
                                   detail=("Added up from the login times shown, which cover every day of this "
                                           "period."))
    # Login sessions on file cover all apps together: time on this app can be
    # at most that total, so a total below the requirement is a FAIL; a total
    # above it says nothing about this app.
    if login_coverage and covered(login_coverage, start, end):
        sessions = [m for m in metrics if m.metric == "login_minutes_all_apps" and start <= m.period_start <= end]
        total = sum(int(m.value) for m in sessions)
        if total < rule.min_login_minutes:
            return ConditionResult(
                condition="login_minutes", label="Login time", required=required,
                observed=f"{_hours(total)} across all apps", status=FAIL, provenance="CALCULATED",
                detail=(f"Your login sessions on file add up to {_hours(total)} across all apps in this period. "
                        "Time on one app cannot be more than that."))
        return ConditionResult(
            condition="login_minutes", label="Login time", required=required,
            observed=f"{_hours(total)} across all apps", status=UNKNOWN, provenance="UNKNOWN",
            detail=("Your login sessions on file cover all apps together, so they do not show how much of that time "
                    "was on this app."))
    if inside:
        return ConditionResult(condition="login_minutes", label="Login time", required=required,
                               observed=f"at least {_hours(lower)} shown", status=UNKNOWN, provenance="UNKNOWN",
                               detail="The documents show some login time, but not a total for the whole period.")
    return ConditionResult(condition="login_minutes", label="Login time", required=required, observed="not available",
                           status=UNKNOWN, provenance="UNKNOWN", detail="Not shown in the evidence for this period.")


def _clock(value: Optional[str]) -> Optional[int]:
    try:
        h, m = map(int, (value or "").split(":"))
        return h * 60 + m
    except ValueError:
        return None


def _ranges(metrics: List[MetricObservation], name: str, day: date) -> List[Tuple[int, int]]:
    out = []
    for m in metrics:
        if m.metric == name and m.period_start == day:
            a, b = _clock(m.start_time), _clock(m.end_time)
            if a is not None and b is not None:
                out.append((a, b if b > a else b + 24 * 60))
    return out


def _window_covered(ranges: List[Tuple[int, int]], window: str) -> bool:
    a, b = (_clock(x) for x in window.split("-"))
    if a is None or b is None:
        return False
    b = b if b > a else b + 24 * 60
    cursor = a
    for start, finish in sorted(ranges):
        if start <= cursor < finish:
            cursor = finish
        if cursor >= b:
            return True
    return cursor >= b


def _login_window_condition(rule: IncentiveRule, metrics: List[MetricObservation], start: date, end: date,
                            login_coverage: Optional[List[CoverageRange]]) -> Optional[ConditionResult]:
    """Logged in for the whole of each required window, on every day of the period."""
    if not rule.required_login_windows:
        return None
    required = "logged in " + " and ".join(rule.required_login_windows)
    statuses: List[str] = []
    day = start
    while day <= end:
        own = _ranges(metrics, "login_minutes", day)
        anywhere = _ranges(metrics, "login_minutes_all_apps", day)
        for window in rule.required_login_windows:
            if own and _window_covered(own, window):
                statuses.append(PASS)
            elif login_coverage and covered(login_coverage, day, day) and not _window_covered(anywhere, window):
                statuses.append(FAIL)  # not logged in to any app for part of the window
            else:
                statuses.append(UNKNOWN)
        day += timedelta(days=1)
    if FAIL in statuses:
        return ConditionResult(condition="login_windows", label="Required login hours", required=required,
                               observed="not logged in for the whole window on at least one day", status=FAIL,
                               provenance="CALCULATED",
                               detail="Your login sessions on file (all apps) do not cover the whole required window.")
    if statuses and all(x == PASS for x in statuses):
        return ConditionResult(condition="login_windows", label="Required login hours", required=required,
                               observed="logged in for the whole window", status=PASS, provenance="OBSERVED")
    return ConditionResult(condition="login_windows", label="Required login hours", required=required,
                           observed="not available", status=UNKNOWN, provenance="UNKNOWN",
                           detail="The evidence does not show this app's login times for the whole window.")


def _rating_condition(rule: IncentiveRule, metrics: List[MetricObservation], start: date, end: date
                      ) -> Optional[ConditionResult]:
    if rule.min_rating is None:
        return None
    values = sorted({m.value for m in metrics if m.metric == "rating" and m.period_start <= end and m.period_end >= start})
    required = f"at least {rule.min_rating}"
    if len(values) == 1:
        return ConditionResult(condition="rating", label="Rating", required=required, observed=str(values[0]),
                               status=PASS if values[0] >= rule.min_rating else FAIL, provenance="OBSERVED")
    return ConditionResult(condition="rating", label="Rating", required=required,
                           observed="different figures" if values else "not available", status=UNKNOWN,
                           provenance="UNKNOWN", detail="Your rating for this period is not shown." if not values
                           else "The documents show different ratings for this period.")


def _rejections_condition(rule: IncentiveRule, trips: List[TripObservation], start: date, end: date
                          ) -> Optional[ConditionResult]:
    """How many offered orders were rejected in the period, against the rule's cap."""
    if rule.max_rejections_in_period is None:
        return None
    required = (f"at most {rule.max_rejections_in_period} rejected "
                + ("order" if rule.max_rejections_in_period == 1 else "orders"))
    in_period, unknown_date = [], 0
    for trip in trips:
        day = trip.service_date or (trip.accepted_at.date() if trip.accepted_at else None)
        if day is None:
            unknown_date += 1
        elif start <= day <= end:
            in_period.append(trip)
    rejected = [t for t in in_period if t.status == "rejected"]
    # A trip list shows what the rider did, not what they were offered and
    # declined, so "no rejection seen" is not evidence of none.
    if not any(t.status == "rejected" for t in trips):
        return ConditionResult(
            condition="max_rejections", label="Orders rejected", required=required, observed="not available",
            status=UNKNOWN, provenance="UNKNOWN",
            detail=("Your records show the orders you did, not the offers you declined, so FairPay cannot count "
                    "rejections."))
    if len(rejected) > rule.max_rejections_in_period:
        return ConditionResult(condition="max_rejections", label="Orders rejected", required=required,
                               observed=f"{len(rejected)} rejected", status=FAIL, provenance="OBSERVED",
                               detail="Counted from the orders marked as rejected in your records.")
    if unknown_date:
        return ConditionResult(condition="max_rejections", label="Orders rejected", required=required,
                               observed=f"at least {len(rejected)} rejected", status=UNKNOWN, provenance="UNKNOWN",
                               detail=f"{unknown_date} rejected orders have no date, so they could not be placed in this period.")
    return ConditionResult(condition="max_rejections", label="Orders rejected", required=required,
                           observed=f"{len(rejected)} rejected", status=PASS, provenance="OBSERVED",
                           detail="Counted from the orders marked as rejected in your records.")


def _orders_in_window_condition(rule: IncentiveRule, trips: List[TripObservation], start: date, end: date
                                ) -> Optional[ConditionResult]:
    """Orders delivered inside the rule's login window."""
    if rule.min_orders_in_window is None or not rule.login_window:
        return None
    required = f"at least {rule.min_orders_in_window} orders between {rule.login_window.replace('-', ' and ')}"
    inside, untimed = 0, 0
    for trip in trips:
        day = trip.service_date or (trip.accepted_at.date() if trip.accepted_at else None)
        if day is None or not (start <= day <= end) or trip.status != "delivered":
            continue
        when = trip.order_time()
        if when is None:
            untimed += 1
        elif time_in_window(when, rule.login_window):
            inside += 1
    if inside >= rule.min_orders_in_window:
        return ConditionResult(condition="orders_in_window", label="Orders inside the required hours",
                               required=required, observed=f"{inside} orders", status=PASS, provenance="CALCULATED")
    if untimed:
        return ConditionResult(condition="orders_in_window", label="Orders inside the required hours",
                               required=required, observed=f"{inside} orders with a time shown", status=UNKNOWN,
                               provenance="UNKNOWN",
                               detail=f"{untimed} orders in this period have no time, so FairPay cannot place them in the window.")
    return ConditionResult(condition="orders_in_window", label="Orders inside the required hours", required=required,
                           observed=f"{inside} orders", status=FAIL, provenance="CALCULATED")


def _login_in_window_condition(rule: IncentiveRule, metrics: List[MetricObservation], start: date, end: date,
                               login_coverage: Optional[List[CoverageRange]]) -> Optional[ConditionResult]:
    """Minutes logged in inside the rule's window, e.g. 3.5 hours between 18:00 and 00:00."""
    if rule.min_login_minutes_in_window is None or not rule.login_window:
        return None
    required = (f"at least {_hours(rule.min_login_minutes_in_window)} logged in between "
                f"{rule.login_window.replace('-', ' and ')}")
    bounds = window_bounds(rule.login_window)
    if bounds is None:
        return ConditionResult(condition="login_in_window", label="Login time in the required hours",
                               required=required, observed="not available", status=UNKNOWN, provenance="UNKNOWN",
                               detail="The window in the rule could not be read.")

    def overlap(metric: MetricObservation) -> Optional[int]:
        a, b = _clock_minutes(metric.start_time), _clock_minutes(metric.end_time)
        if a is None or b is None:
            return None
        if b <= a:
            b += 24 * 60
        return max(0, min(b, bounds[1]) - max(a, bounds[0]))

    own = [m for m in metrics if m.metric == "login_minutes" and start <= m.period_start <= end]
    all_apps = [m for m in metrics if m.metric == "login_minutes_all_apps" and start <= m.period_start <= end]
    own_overlaps = [x for x in (overlap(m) for m in own) if x is not None]
    own_minutes = sum(own_overlaps)
    if own_overlaps:
        ok = own_minutes >= rule.min_login_minutes_in_window
        return ConditionResult(condition="login_in_window", label="Login time in the required hours",
                               required=required, observed=_hours(own_minutes), status=PASS if ok else FAIL,
                               provenance="CALCULATED", detail="Measured from the login times shown for this app.")
    # Sessions on file cover all apps at once: time on one app cannot exceed them.
    if all_apps and login_coverage and covered(login_coverage, start, end):
        total = sum(x for x in (overlap(m) for m in all_apps) if x is not None)
        if total < rule.min_login_minutes_in_window:
            return ConditionResult(condition="login_in_window", label="Login time in the required hours",
                                   required=required, observed=f"{_hours(total)} across all apps", status=FAIL,
                                   provenance="CALCULATED",
                                   detail=("Your login sessions on file fall inside this window for only "
                                           f"{_hours(total)} across all apps. Time on one app cannot be more than that."))
        return ConditionResult(condition="login_in_window", label="Login time in the required hours",
                               required=required, observed=f"{_hours(total)} across all apps", status=UNKNOWN,
                               provenance="UNKNOWN",
                               detail="Your login sessions cover all apps together, so FairPay cannot tell how much was on this app.")
    return ConditionResult(condition="login_in_window", label="Login time in the required hours", required=required,
                           observed="not available", status=UNKNOWN, provenance="UNKNOWN",
                           detail="The evidence does not show your login times for this window.")


def _payout_target_condition(rule: IncentiveRule, trips: List[TripObservation], start: date, end: date
                             ) -> Optional[ConditionResult]:
    """A target expressed as the period's earnings rather than an order count."""
    if rule.target_payout_amount is None:
        return None
    required = f"₹{rule.target_payout_amount} earned"
    counted, missing = Decimal("0"), 0
    for trip in trips:
        day = trip.service_date or (trip.accepted_at.date() if trip.accepted_at else None)
        if day is None or not (start <= day <= end) or trip.status != "delivered":
            continue
        if trip.earning_amount is None:
            missing += 1
        else:
            counted += trip.earning_amount
    if missing:
        return ConditionResult(condition="payout_target", label="Earnings target", required=required,
                               observed=f"at least ₹{counted} shown", status=PASS if counted >= rule.target_payout_amount else UNKNOWN,
                               provenance="CALCULATED" if counted >= rule.target_payout_amount else "UNKNOWN",
                               detail=f"{missing} orders in this period do not show what they earned.")
    if counted == 0:
        return ConditionResult(condition="payout_target", label="Earnings target", required=required,
                               observed="not available", status=UNKNOWN, provenance="UNKNOWN",
                               detail="Your records do not show what these orders earned.")
    tips = any(TIP_NOT_COUNTED in n for t in trips for n in t.notes)
    return ConditionResult(condition="payout_target", label="Earnings target", required=required,
                           observed=f"₹{counted}", status=PASS if counted >= rule.target_payout_amount else FAIL,
                           provenance="CALCULATED", detail="Added up from the orders in this period"
                           + (", without customer tips." if tips else "."))


def _weight_condition(rule: IncentiveRule) -> Optional[ConditionResult]:
    if rule.min_weight_kg is None:
        return None
    return ConditionResult(condition="min_weight", label="Order weight", required=f"at least {rule.min_weight_kg} kg",
                           observed="not available", status=UNKNOWN, provenance="UNKNOWN",
                           detail="Trip records do not show how heavy an order was.")


def _opt_in_condition(rule: IncentiveRule, offers: List[PlatformCountObservation]) -> Optional[ConditionResult]:
    if not rule.opt_in_required:
        return None
    if offers:
        return ConditionResult(condition="opt_in", label="Signed up before it started", required="opt in",
                               observed="your progress for it is shown", status=PASS, provenance="INFERRED",
                               detail="The app shows your progress for this incentive, so you were signed up.")
    return ConditionResult(condition="opt_in", label="Signed up before it started", required="opt in",
                           observed="not available", status=UNKNOWN, provenance="UNKNOWN",
                           detail="The evidence does not show that you signed up for it.")


def _documented_text_conditions(rule: IncentiveRule) -> List[ConditionResult]:
    return [ConditionResult(condition=f"documented_{i}", label="Other documented condition", required=text,
                            observed="not checkable from the evidence", status=UNKNOWN, provenance="UNKNOWN",
                            detail="FairPay cannot check this condition from trip records.")
            for i, text in enumerate(rule.other_conditions, start=1)]


def _hours(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m:02d}m" if m else f"{h}h"


# ---------------------------------------------------------------------------
# Payment
# ---------------------------------------------------------------------------

_INCENTIVE_WORDS = ("incentive", "bonus", "target", "milestone", "reward", "mbg", "guarantee")
# Words too common to tie a payment to one incentive by name alone.
_GENERIC_INCENTIVE_WORDS = {
    "incentive", "incentives", "bonus", "bonuses", "target", "daily", "weekly", "order", "orders",
    "riders", "rider", "pay", "payout", "offer", "with", "that", "this", "from", "year", "peak",
}


def _d(day: date) -> str:
    return day.strftime("%d %b %Y").lstrip("0")


def _span(start: date, end: date) -> str:
    return _d(start) if start == end else f"{_d(start)} to {_d(end)}"


def check_payment(evidence: IncentiveEvidence, resolution: RuleResolution, start: date, end: date,
                  reward: Optional[Decimal] = None) -> PaymentCheck:
    rule = resolution.rule
    assert rule is not None
    rule_ids = {r.rule_id for r in resolution.versions}
    window_days = 0 if rule.award_basis == "threshold_crossing_order" else rule.payout_window_days
    window_end = end + timedelta(days=window_days) if window_days is not None else None

    def names_this_rule(p) -> bool:
        """Does the payment's own label point at this incentive?

        Several incentives can run in one period, so a payment is only taken
        as this one's when its id matches, its label shares a distinctive word
        with the incentive's name, or its amount is the documented reward.
        Otherwise it is somebody else's payment and is left alone.
        """
        if reward is not None and p.amount == reward:
            return True
        words = {w for w in re.findall(r"[a-z]{4,}", (rule.incentive_name or "").lower())
                 if w not in _GENERIC_INCENTIVE_WORDS}
        label = (p.label or "").lower()
        return any(w in label for w in words)

    def in_period(p) -> bool:
        if p.period_start and p.period_end:
            return p.period_start == start and p.period_end == end
        label = (p.label or "").lower()
        if not any(w in label for w in _INCENTIVE_WORDS):
            return False
        return _in(p.paid_on, start, window_end or end)

    def relevant(p) -> bool:
        if p.rule_id:
            return p.rule_id in rule_ids and _in(p.paid_on, start, end)
        return in_period(p) and names_this_rule(p)

    matches = [p for p in evidence.payments if relevant(p)]
    # A payment in this period that belongs to a different incentive is not
    # evidence about this one either way.
    others = [p for p in evidence.payments
              if not p.rule_id and in_period(p) and not names_this_rule(p)]
    exact = [p for p in matches if reward is not None and p.amount == reward]
    if exact or (matches and reward is None):
        p = (exact or matches)[0]
        detail = f"{p.label or 'Incentive'} of ₹{p.amount} shown on {p.source}" + (f" ({_d(p.paid_on)})" if p.paid_on else "") + "."
        if reward is None:
            detail += " The reward amount is not published, so FairPay cannot confirm it is the full amount."
        return PaymentCheck(status="paid", amount=p.amount, sources=[p.source], detail=detail)
    if matches:
        total = sum((p.amount for p in matches), Decimal("0"))
        return PaymentCheck(status="paid_different_amount", amount=total, sources=[p.source for p in matches],
                            detail=(f"An incentive payment of ₹{total} is shown for this period, but the documented "
                                    f"reward is ₹{reward}. FairPay cannot tell whether it is this incentive."))
    if others:
        labels = ", ".join(sorted({p.label for p in others if p.label})) or "another incentive"
        return PaymentCheck(status="not_checkable", sources=[p.source for p in others],
                            detail=(f"Your records show an incentive payment for this period ({labels}), but it does "
                                    f"not match this incentive, so FairPay cannot tell whether this one was paid."))
    if window_end is None:
        timing = f" ({rule.payout_timing})" if rule.payout_timing else ""
        return PaymentCheck(status="not_checkable",
                            detail=(f"{rule.platform} does not document how soon this incentive is paid{timing}, "
                                    "so FairPay cannot tell whether a payment is late or missing."))
    if covered(evidence.payment_coverage, start, window_end):
        return PaymentCheck(status="not_visible",
                            detail=f"The earnings records for {_span(start, window_end)} list incentives, and this one is not among them.")
    return PaymentCheck(status="not_checkable",
                        detail=(f"The evidence does not include earnings records that list incentives for "
                                f"{_span(start, window_end)}, so FairPay cannot see whether it was paid."))


# ---------------------------------------------------------------------------
# One period
# ---------------------------------------------------------------------------

def _offer_conflict(rule: IncentiveRule, offers: List[PlatformCountObservation]) -> Optional[str]:
    for offer in offers:
        if rule.target_orders is not None and offer.target_orders and offer.target_orders != rule.target_orders:
            return (f"The offer in your documents shows a target of {offer.target_orders} orders, but the rule on file "
                    f"({rule.source_reference}) sets {rule.target_orders}.")
        if rule.reward_amount is not None and offer.reward_amount is not None and offer.reward_amount != rule.reward_amount:
            return (f"The offer in your documents shows a reward of {offer.reward_amount}, but the rule on file "
                    f"({rule.source_reference}) sets {rule.reward_amount}.")
    return None


_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def _conflict_in_period(text: str, start: date, end: date) -> bool:
    """A conflict matters to a period when it names a date in it (or no date at all)."""
    days = [date.fromisoformat(d) for d in _ISO_DAY.findall(text)]
    return not days or any(start <= d <= end for d in days)


def evaluate_period(evidence: IncentiveEvidence, resolution: RuleResolution, platform: str,
                    start: date, end: date, incentive_type: str = "") -> PeriodEvaluation:
    """Everything the audit says about one incentive period on one platform."""
    base = dict(platform=platform, period_start=start, period_end=end, rule_status=resolution.status,
                rule_note=resolution.note, retrieval_backend=resolution.retrieval_backend,
                incentive_type=incentive_type)
    if resolution.status != ESTABLISHED or resolution.rule is None:
        outcome = AMBIGUOUS if resolution.status == RULE_AMBIGUOUS else INSUFFICIENT_EVIDENCE
        return PeriodEvaluation(**base, outcome=outcome,
                                notes=[resolution.note or RULE_NOT_ESTABLISHED_TEXT])

    rule = resolution.rule
    base.update(rule=rule, incentive_name=rule.incentive_name, incentive_type=rule.incentive_type)
    trips = [t for t in evidence.trips]
    count = count_orders(trips, rule, start, end)
    complete = covered(evidence.trip_coverage, start, end)

    counts = [c for c in evidence.platform_counts if c.period_start == start and c.period_end == end]
    platform_values = sorted({c.counted_orders for c in counts})
    conflicts = [c for c in evidence.conflicts if _conflict_in_period(c, start, end)]
    offers = [o for o in evidence.printed_offers if o.period_start == start and o.period_end == end]
    offer_conflict = _offer_conflict(rule, offers) if rule.source_type != "offer_shown_in_evidence" else None
    if offer_conflict:
        conflicts.append(offer_conflict)
    if len(platform_values) > 1:
        conflicts.append("The documents show different progress counts for this incentive: "
                         + ", ".join(map(str, platform_values)) + ".")
    if rule.evidence_status == "CONTRADICTED":
        others = ", ".join(rule.contradicted_by) or "another source"
        conflicts.append(f"Sources disagree about the terms of this incentive ({rule.source_reference} against "
                         f"{others}), so FairPay cannot tell which applied.")
    platform_count = platform_values[0] if len(platform_values) == 1 else None
    if platform_count is not None and platform_count > count.max_count:
        if complete:
            conflicts.append(f"The platform's progress shows {platform_count} orders, but every order for this "
                             f"period is in the evidence and only {count.max_count} could count.")

    notes: List[str] = []
    if resolution.note:
        notes.append(resolution.note)
    if rule.evidence_class == "demonstration_dataset":
        notes.append(f"These terms come from FairPay's demonstration dataset ({rule.source_reference}), not from "
                     f"anything {rule.platform} publishes. They show how the check works.")
    elif rule.source_tier >= 2:
        notes.append(f"{rule.platform} does not publish these terms. They come from {rule.source_reference} "
                     f"({rule.confidence_label.lower()} confidence), so they may not be the terms that applied to you.")
    if (rule.evidence_class != "demonstration_dataset" and rule.effective_to is None and rule.source_date
            and (start - rule.source_date).days > 365):
        notes.append(f"The source for these terms is from {rule.source_date:%B %Y}, more than a year before this "
                     "period. Nothing on record says they ended, so FairPay assumes they still apply, but they may "
                     "no longer be true today.")
    if rule.evidence_status == "CONTRADICTED":
        notes.append("Another source reports different terms for this same incentive, so FairPay cannot be sure "
                     "which applied.")
    if rule.inferences:
        notes.append("Some terms of this incentive are not documented anywhere. FairPay leaves those unknown rather "
                     "than assuming them.")
    if not rule.conditions_complete:
        notes.append(f"{rule.platform} does not publish the full terms of this incentive ({rule.source_reference}); "
                     "only the terms FairPay could find were checked.")

    # -- Order target ------------------------------------------------------
    target, reward, target_source = rule.target_orders, rule.reward_amount, "rule"
    if target is None:
        printed = sorted({o.target_orders for o in offers if o.target_orders})
        if len(printed) == 1:
            target, target_source = printed[0], "offer_in_evidence"
            notes.append(f"{rule.platform} does not publish this target; the {target}-order target is taken from "
                         "the incentive screen in your documents.")
        else:
            target_source = "not_documented"
    if reward is None:
        printed_rewards = sorted({o.reward_amount for o in offers if o.reward_amount is not None})
        if len(printed_rewards) == 1:
            reward = printed_rewards[0]
    count_text = (str(count.min_count) if count.min_count == count.max_count
                  else f"{count.min_count} to {count.max_count}")
    count_prov: Provenance = "INFERRED" if count.inferred else "CALCULATED"
    mismatch: Optional[CountingMismatch] = None
    if target is not None and platform_count is not None and platform_count < count.max_count:
        uncertain_ids = [o.order_id for o in count.uncertain]
        diff_definite = count.min_count - platform_count
        if diff_definite > 0:
            basis = "documented_rule"
            gap = diff_definite
            orders: List[str] = []
            explanation = (f"The platform appears to have counted {platform_count} orders. FairPay counts "
                           f"{count.min_count} orders that qualify under the rule, so {gap} "
                           f"{'order appears' if gap == 1 else 'orders appear'} not to have been counted.")
            if not uncertain_ids:
                explanation += " The evidence does not show which orders were left out."
                basis = "unidentified"
        else:
            basis = "undocumented_counting_rule"
            gap = count.max_count - platform_count
            orders = uncertain_ids if len(uncertain_ids) <= gap + 2 else []
            explanation = (f"The platform appears to have counted {platform_count} orders. FairPay found "
                           f"{count.max_count} orders that could qualify; {gap} of them "
                           f"{'depends' if gap == 1 else 'depend'} on counting rules the platform has not documented "
                           f"({'; '.join(q.lower() for q in count.open_questions)}).")
        decisive = platform_count < target <= (count.min_count if basis != "undocumented_counting_rule" else count.max_count)
        mismatch = CountingMismatch(platform_count=platform_count, fairpay_min=count.min_count,
                                    fairpay_max=count.max_count, difference=gap, basis=basis,
                                    orders=orders, explanation=explanation, decisive=decisive)

    if target is None and (rule.target_payout_amount is not None or rule.measurable_without_order_target()):
        # The rule is measured by earnings or by conditions, not by a count of
        # orders, so there is no order target to pass or fail.
        status, prov = PASS, "RETRIEVED_RULE"
        detail = (f"This incentive has no order target: {rule.platform} measures it another way. You did "
                  f"{count_text} {'order' if count_text == '1' else 'orders'} in this period.")
        observed = f"{count_text} orders (this incentive sets no order target)"
    elif target is None:
        status, prov = UNKNOWN, "UNKNOWN"
        detail = (f"{rule.platform} does not publish the order target for this incentive, and your documents do not "
                  "show it.")
        observed = f"{count_text} counted by FairPay"
    elif platform_count is not None:
        if platform_count >= target:
            status, prov, detail = PASS, "OBSERVED", f"The platform's progress shows {platform_count} of {target}."
        elif count.min_count >= target:
            status, prov = PASS, count_prov
            detail = f"FairPay counts {count.min_count}; the platform shows {platform_count}."
        elif count.max_count >= target:
            status, prov = UNKNOWN, "UNKNOWN"
            detail = (f"The platform shows {platform_count}. The target is reached only if orders whose counting is "
                      "undocumented are counted.")
        else:
            status, prov = FAIL, "OBSERVED"
            detail = f"The platform's progress shows {platform_count}, and FairPay counts {count_text}."
        observed = f"{platform_count} counted by the platform; {count_text} by FairPay"
    else:
        observed = f"{count_text} counted by FairPay"
        if count.min_count >= target:
            status, prov, detail = PASS, count_prov, ""
        elif count.max_count < target and complete:
            status, prov, detail = FAIL, count_prov, "Every order for this period is in the evidence."
        elif count.max_count < target:
            status, prov = UNKNOWN, "UNKNOWN"
            detail = ("The documents may not show every order of this period, so FairPay cannot tell whether "
                      "more orders were done.")
        else:
            status, prov = UNKNOWN, "UNKNOWN"
            detail = "The target is reached only if orders whose counting is undocumented are counted."
    target_required = (f"{target} orders" if target is not None
                       else ("no order target" if status == PASS else "not published"))
    conditions = [ConditionResult(condition="order_target", label="Order target",
                                  required=target_required,
                                  observed=observed, status=status, provenance=prov, detail=detail)]

    for cond in (
        _rate_condition(rule.min_acceptance_rate, evidence.metrics, "acceptance_rate", start, end, minimum=True),
        _rate_condition(rule.max_rejection_rate, evidence.metrics, "rejection_rate", start, end, minimum=False),
        _login_condition(rule, evidence.metrics, start, end, evidence.login_coverage),
        _login_window_condition(rule, evidence.metrics, start, end, evidence.login_coverage),
        _login_in_window_condition(rule, evidence.metrics, start, end, evidence.login_coverage),
        _orders_in_window_condition(rule, trips, start, end),
        _rejections_condition(rule, trips, start, end),
        _payout_target_condition(rule, trips, start, end),
        _rating_condition(rule, evidence.metrics, start, end),
        _weight_condition(rule),
        _opt_in_condition(rule, offers or [c for c in counts]),
    ):
        if cond:
            conditions.append(cond)
    conditions.extend(_documented_text_conditions(rule))
    if not rule.conditions_complete:
        conditions.append(ConditionResult(
            condition="other_terms", label="Other terms of the incentive", required="not published",
            observed="not available", status=UNKNOWN, provenance="UNKNOWN",
            detail=f"{rule.platform} does not publish the full terms of this incentive."))

    payment = check_payment(evidence, resolution, start, end, reward)

    # -- Outcome -----------------------------------------------------------
    target_cond = conditions[0]
    other_fails = [c for c in conditions[1:] if c.status == FAIL]
    unknowns = [c for c in conditions if c.status == UNKNOWN]
    shortfall = None
    if target_cond.status == FAIL and target is not None:
        shortfall = target - max(count.max_count, platform_count or 0)

    if conflicts:
        outcome = AMBIGUOUS
    elif payment.status == "paid":
        outcome = INCENTIVE_RECEIVED
    elif payment.status == "paid_different_amount":
        outcome = AMBIGUOUS
    elif other_fails:
        outcome = ELIGIBILITY_FAILURE
    elif mismatch and mismatch.decisive:
        outcome = COUNTING_MISMATCH
    elif target_cond.status == FAIL:
        outcome = ELIGIBILITY_FAILURE
    elif unknowns:
        outcome = INSUFFICIENT_EVIDENCE
    elif payment.status == "not_visible":
        outcome = POSSIBLE_PAYMENT_FAILURE
    else:
        outcome = PAYMENT_NOT_OBSERVED

    return PeriodEvaluation(
        **base, count=count, platform_count=platform_count, conditions=conditions, payment=payment,
        mismatch=mismatch, conflicts=conflicts, outcome=outcome, shortfall=shortfall,
        target_orders=target, reward_amount=reward, target_source=target_source,
        near_miss=shortfall == 1, trips_complete=complete, notes=notes,
    )
