"""Personalized incentive fit: which platform's incentive structure fits this rider?

A separate recommendation layer, shown only after the incentive audit. It is
not a finding and it is never merged into one.

What it answers: "Taking the orders you did on ONE day, which platform's
*documented* daily incentive would that day's work have reached, and which of
those has the highest documented reward?"

What it never answers: who pays more. Earnings depend on order availability,
base pay, demand and area, none of which this compares. Every statement is
checked against :data:`EARNINGS_CLAIM_PATTERNS` before it is returned, and the
result always carries the disclaimer.

Method (deterministic)
----------------------
1. The day: the day the audit was about (or, for a weekly audit, the latest
   day in that week with orders). The rider's delivered orders on that day on
   the platform in their evidence are the whole pattern - one day is enough.
2. For every platform with rules in the knowledge base (Qdrant), the daily
   incentive in force on that same day in the rider's region. A platform that
   does not publish its daily target and reward cannot be compared and is
   listed with that reason.
3. For each comparable platform: the day's orders counted under that
   platform's own counting rules, whether its target is reached, how many
   orders short if not, and its documented reward.
4. Ranking weighs the reward: targets reached come first, highest documented
   reward first; then targets not reached, fewest orders short first, higher
   reward breaking ties. Two platforms with the same position mean no single
   platform stands out.
5. Evidence quality travels with every line. Each platform's rule carries how
   strongly it is sourced (HIGH, MEDIUM or LOW, and its source tier), and a
   rule from FairPay's demonstration dataset is always labelled as such, so a
   comparison is never read as stronger than the evidence under it.

Two flows, two windows (kept apart on purpose)
----------------------------------------------
* "Didn't get your incentive?" (Telegram, one day sent): the fit compares that
  ONE day - INCENTIVE_CHECK_FIT_DAYS. This is the method above.
* "Check your 30 day audit" (records on file): the fit compares FIVE eligible
  days of the audited history - FULL_AUDIT_FIT_DAYS. An eligible day has
  delivered orders on the audited platform and a complete trip list. The five
  are the latest such days up to the end of the audited period. Each day is
  checked exactly as in the one-day method, against the rules in force that
  day; per platform the days reached, the documented rewards of those days and
  the average orders short are added up, and the ranking weighs the reward the
  same way: more documented reward on the days reached first, then more days
  reached, then fewest orders short on average.
"""

from __future__ import annotations

import re
import math
from datetime import date
from decimal import Decimal
from statistics import median_low
from typing import Any, Dict, List, Optional, Tuple

from incentive_engine import count_orders
from incentive_evidence import IncentiveEvidence, covered
from incentive_rules import (
    ESTABLISHED,
    IncentiveRule,
    IncentiveRuleStore,
    RuleQuery,
    RuleStoreUnavailable,
    platform_key,
    resolve_program,
)

QUESTION = "Which incentive structure fits you better?"

# How many days the fit compares, per flow. Never shared between the flows.
INCENTIVE_CHECK_FIT_DAYS = 1  # "Didn't get your incentive?": the one day the partner sent
FULL_AUDIT_FIT_DAYS = 5       # "Check your 30 day audit": five days of the audited history

FIT_FOUND = "FIT_FOUND"
NO_CLEAR_DIFFERENCE = "NO_CLEAR_DIFFERENCE"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

CANNOT_COMPARE = "FairPay cannot reliably compare the incentive structures for you yet."

# Statements (other than the disclaimer) must never claim higher pay.
EARNINGS_CLAIM_PATTERNS = [
    r"\bearn(s|ing)? more\b", r"\bwill earn\b", r"\bmore money\b", r"\bhigher (earnings|income|pay)\b",
    r"\bpays? (you )?more\b", r"\bbetter pay\b", r"\bguarantee", r"\bmore profitable\b",
    r"\bincrease your (earnings|income)\b", r"\bis better\b", r"\byou will get\b",
]


def assert_no_earnings_claim(texts: List[str]) -> None:
    for text in texts:
        for pattern in EARNINGS_CLAIM_PATTERNS:
            if re.search(pattern, text, flags=re.IGNORECASE):
                raise ValueError(f"Incentive fit text makes an earnings claim ({pattern!r}): {text}")


def disclaimer(platform: Optional[str]) -> str:
    if platform:
        return (f"This does not mean {platform} will pay you more. Actual earnings depend on order availability, "
                "base pay, incentives and other conditions.")
    return ("This is not a prediction of earnings. Actual earnings depend on order availability, base pay, "
            "incentives and other conditions.")


def _day(d: date) -> str:
    return d.strftime("%a %d %b %Y").replace(" 0", " ")


def _inr(amount: Decimal) -> str:
    text = f"{Decimal(amount).quantize(Decimal('0.01')):,.2f}"
    return f"₹{text[:-3] if text.endswith('.00') else text}"


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


# ---------------------------------------------------------------------------
# The day being compared
# ---------------------------------------------------------------------------

def _trip_day(trip) -> Optional[date]:
    when = trip.accepted_at or trip.completed_at
    return when.date() if when else trip.service_date


def choose_day(evidence: IncentiveEvidence, period: Optional[Tuple[date, date]]) -> Optional[date]:
    """The audited day, or the latest day with delivered orders inside the audited period."""
    days = sorted({_trip_day(t) for t in evidence.trips if t.status == "delivered" and _trip_day(t)})
    if period:
        inside = [d for d in days if period[0] <= d <= period[1]]
        if inside:
            return inside[-1]
    return days[-1] if days else None


def _conditions_not_checked(rule: IncentiveRule) -> List[str]:
    items: List[str] = []
    if rule.min_acceptance_rate is not None:
        items.append(f"acceptance rate of at least {rule.min_acceptance_rate * 100:.0f}%")
    if rule.max_rejection_rate is not None:
        items.append(f"rejection rate of at most {rule.max_rejection_rate * 100:.0f}%")
    if rule.min_login_minutes is not None:
        h, m = divmod(rule.min_login_minutes, 60)
        items.append(f"login time of at least {h}h" + (f" {m}m" if m else ""))
    if rule.required_login_windows:
        items.append("login during " + " and ".join(rule.required_login_windows))
    if rule.min_rating is not None:
        items.append(f"rating of at least {rule.min_rating}")
    if rule.opt_in_required:
        items.append("signing up before it starts")
    items.extend(rule.other_conditions)
    if not rule.conditions_complete:
        items.append("terms not fully published")
    return items


# ---------------------------------------------------------------------------
# One platform's daily incentive against that day
# ---------------------------------------------------------------------------

def _assess(platform: str, day: date, trips, store: IncentiveRuleStore, region: Optional[str],
            real_rules_only: bool = False) -> Dict[str, Any]:
    rules = store.retrieve(RuleQuery(platform=platform, period_start=day, period_end=day, region=region,
                                     incentive_type="daily_target"))
    if real_rules_only:  # a real rider is never compared against FairPay's demonstration dataset
        rules = [r for r in rules if r.evidence_class != "demonstration_dataset"]
    programs: Dict[str, List[IncentiveRule]] = {}
    for rule in rules:
        programs.setdefault(rule.program_id, []).append(rule)
    comparable: List[Dict[str, Any]] = []
    not_comparable: List[str] = []
    relabelled = [t.model_copy(update={"platform": platform}) for t in trips]
    for program_id, versions in sorted(programs.items()):
        resolution = resolve_program(versions, day, day)
        if resolution.status != ESTABLISHED or resolution.rule is None:
            not_comparable.append(f"{versions[-1].incentive_name}: {resolution.note}")
            continue
        rule = resolution.rule
        if rule.target_orders is None or rule.reward_amount is None:
            not_comparable.append(f"{rule.incentive_name} ({rule.source_reference}): the daily target or reward is not published")
            continue
        result = count_orders(relabelled, rule, day, day)
        reached = result.min_count >= rule.target_orders
        uncertain = not reached and result.max_count >= rule.target_orders
        comparable.append({
            "rule": rule,
            "evidence": (f"{rule.confidence_label.lower()} confidence, tier {rule.source_tier}"
                         + (", FairPay demonstration data" if rule.evidence_class == "demonstration_dataset"
                            else f", {rule.source_publisher or rule.source_type}")),
            "orders_counted": (str(result.min_count) if result.min_count == result.max_count
                               else f"{result.min_count}-{result.max_count}"),
            "reached": reached,
            "uncertain": uncertain,
            "shortfall": 0 if (reached or uncertain) else rule.target_orders - result.max_count,
        })
    return {"platform": platform, "comparable": comparable, "not_comparable": not_comparable}


def _rank_key(item: Dict[str, Any]) -> Tuple:
    """Reached first, highest reward first; otherwise fewest orders short, then higher reward."""
    reward = item["rule"].reward_amount
    if item["reached"]:
        return (0, -reward, 0)
    return (1, item["shortfall"], -reward)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def personalized_fit(evidence: IncentiveEvidence, store: IncentiveRuleStore, audited_platform: str,
                     period: Optional[Tuple[date, date]] = None,
                     days: int = INCENTIVE_CHECK_FIT_DAYS) -> Dict[str, Any]:
    """Compare the rider's work with each platform's documented daily incentive.

    ``days`` is 1 for the single-day incentive check (the default) and
    FULL_AUDIT_FIT_DAYS for the 30-day audit.
    """
    if days > 1:
        return _multi_day_fit(evidence, store, audited_platform, period, days)
    own = evidence.for_platform(audited_platform)
    day = choose_day(own, period)
    base: Dict[str, Any] = {
        "question": QUESTION,
        "kind": "incentive_fit",
        "audited_platform": audited_platform,
        "comparison_unit": "day",
        "comparison_day": day.isoformat() if day else None,
        "disclaimer": disclaimer(None),
        "assumptions": [],
        "platforms": [],
        "not_compared": [],
    }
    if day is None:
        return {**base, "status": INSUFFICIENT_DATA, "headline": CANNOT_COMPARE,
                "why": [f"Your evidence has no delivered {audited_platform} orders on any day."]}

    trips = [t for t in own.trips if _trip_day(t) == day and t.status == "delivered"]
    n = len(trips)
    try:
        real_only = evidence.source == "telegram"
        assessed = [_assess(p, day, trips, store, evidence.region, real_only) for p in store.platforms()]
    except RuleStoreUnavailable as exc:
        return {**base, "status": INSUFFICIENT_DATA, "headline": CANNOT_COMPARE,
                "why": [f"FairPay could not reach its incentive rule knowledge base ({exc})."]}

    items: List[Dict[str, Any]] = []
    not_compared: List[str] = []
    for a in assessed:
        if not a["comparable"]:
            reason = "; ".join(a["not_comparable"]) or "no daily incentive is on record for this day in your region"
            not_compared.append(f"{a['platform']}: {reason}")
            continue
        best = sorted(a["comparable"], key=_rank_key)[0]  # a platform's best daily programme that day
        items.append({**best, "platform": a["platform"]})
        not_compared.extend(f"{a['platform']}: {x}" for x in a["not_comparable"])

    hours = sorted({f"{(t.accepted_at or t.completed_at).hour:02d}:00" for t in trips if (t.accepted_at or t.completed_at)})
    pattern = {
        "platform": audited_platform, "unit": f"day ({_day(day)})", "periods_observed": 1,
        "period_start": day.isoformat(), "period_end": day.isoformat(), "typical_orders_low": n,
        "typical_orders_high": n, "median_orders": n, "busiest_hours": hours,
        "acceptance_rate": "available" if any(m.metric == "acceptance_rate" for m in own.metrics) else "not available",
        "login_time": "available" if any(m.metric.startswith("login") for m in own.metrics) else "not available",
        "provenance": "CALCULATED from the orders in your evidence for this day",
    }
    if len(items) < 2:
        why = [f"To compare, FairPay needs the published daily target and reward of at least two platforms for "
               f"{_day(day)} in your region; it has {len(items)}."]
        return {**base, "status": INSUFFICIENT_DATA, "headline": CANNOT_COMPARE, "why": why,
                "not_compared": not_compared, "rider_pattern": pattern}

    ranked = sorted(items, key=_rank_key)
    top, second = ranked[0], ranked[1]
    reached = [i for i in ranked if i["reached"]]
    why: List[str] = [f"You did {n} {audited_platform} {_plural(n, 'order', 'orders')} on {_day(day)}."]
    for i in ranked:
        r = i["rule"]
        line = (f"{i['platform']}'s documented daily target is {r.target_orders} orders with a documented reward "
                f"of {_inr(r.reward_amount)}; ")
        if i["reached"]:
            line += "your day would reach it."
        elif i["uncertain"]:
            line += f"whether your day reaches it depends on counting rules {i['platform']} does not publish."
        else:
            line += f"your day would be {i['shortfall']} {_plural(i['shortfall'], 'order', 'orders')} short."
        unchecked = _conditions_not_checked(r)
        if unchecked:
            line += " It also requires " + ", ".join(unchecked) + ", which FairPay could not check."
        line += f" Source: {i['evidence']}."
        why.append(line)

    if _rank_key(top) == _rank_key(second):
        status, recommended = NO_CLEAR_DIFFERENCE, None
        headline = (f"For the {n} {_plural(n, 'order', 'orders')} you did on {_day(day)}, "
                    f"{top['platform']} and {second['platform']} have documented daily incentives that fit equally; "
                    "no single platform stands out.")
    else:
        status, recommended = FIT_FOUND, top["platform"]
        own_best = platform_key(top["platform"]) == platform_key(audited_platform)
        lead = (f"{top['platform']}'s own documented daily incentive already fits" if own_best
                else f"{top['platform']}'s documented daily incentive fits")
        target, reward = top["rule"].target_orders, _inr(top["rule"].reward_amount)
        orders = f"the {n} {_plural(n, 'order', 'orders')} you did on {_day(day)}"
        if top["reached"] and len(reached) > 1:
            headline = (f"{lead} the day you worked best: {orders} would reach its {target}-order target, and its "
                        f"documented reward of {reward} is the highest among the targets your day reaches.")
        elif top["reached"]:
            headline = (f"{lead} the day you worked best: {orders} would reach its {target}-order target "
                        f"(documented reward {reward}); the other documented daily targets would not be reached.")
        else:
            headline = (f"None of the documented daily targets would be reached with {orders}. "
                        f"{top['platform']}'s is the closest: {top['shortfall']} "
                        f"{_plural(top['shortfall'], 'order', 'orders')} short, with a documented reward of {reward}.")

    assumptions = [
        f"This uses only the orders you did on {audited_platform} on {_day(day)}. It assumes you could do the same "
        "number of orders on another platform that day; demand, order availability and your area may be different there.",
        f"Every platform is compared using the daily incentive rules in force on {_day(day)} in your region.",
        "The ranking puts targets your day reaches first, then the highest documented reward. Rewards are listed as "
        "documented, not as amounts you would receive, and incentives are not offered every day.",
    ]
    weak = [i for i in ranked if i["rule"].confidence_label != "HIGH"]
    if weak:
        assumptions.append(
            "Some of these terms are not published by the platform: "
            + "; ".join(f"{i['platform']} ({i['evidence']})" for i in weak)
            + ". A comparison is only as good as the rules behind it.")
    demo = [i for i in ranked if i["rule"].evidence_class == "demonstration_dataset"]
    if demo:
        assumptions.append(
            "These targets come from FairPay's demonstration dataset, not from the platforms: "
            + ", ".join(i["platform"] for i in demo)
            + ". They show how the comparison works rather than what the platforms actually offer.")
    if not_compared:
        assumptions.append("Not compared: " + "; ".join(not_compared) + ".")

    public = []
    for i in ranked:
        r = i["rule"]
        possible = i["reached"] or i["uncertain"]
        public.append({
            "platform": i["platform"],
            "periods_compared": 1,
            "periods_without_rule": 0,
            "target_met_min": 1 if i["reached"] else 0,
            "target_met_max": 1 if possible else 0,
            "share_min": "1.00" if i["reached"] else "0.00",
            "share_max": "1.00" if possible else "0.00",
            "orders_counted": i["orders_counted"],
            "reached": i["reached"],
            "shortfall": i["shortfall"],
            "target_orders": [r.target_orders],
            "reward_amounts": [str(r.reward_amount)],
            "reward_text": f"{_inr(r.reward_amount)} per target reached (as documented)",
            "time_windows": r.time_windows,
            "conditions_not_checked": _conditions_not_checked(r),
            "rule_versions": [r.version],
            "rule_sources": [r.source_reference],
            "payout_timing": r.payout_timing,
            "confidence_label": r.confidence_label,
            "source_tier": r.source_tier,
            "evidence_class": r.evidence_class,
            "evidence_summary": i["evidence"],
            "source_reference": r.source_reference,
            "is_audited_platform": platform_key(i["platform"]) == platform_key(audited_platform),
        })

    assert_no_earnings_claim([headline, *why, *assumptions])
    return {
        **base,
        "status": status,
        "headline": headline,
        "recommended_platform": recommended,
        "why": why,
        "assumptions": assumptions,
        "platforms": public,
        "not_compared": not_compared,
        "rider_pattern": pattern,
        "disclaimer": disclaimer(recommended),
    }


# ---------------------------------------------------------------------------
# The 30-day audit: five eligible days of the audited history
# ---------------------------------------------------------------------------

def choose_days(evidence: IncentiveEvidence, period: Optional[Tuple[date, date]], count: int) -> List[date]:
    """The latest ``count`` eligible days up to the end of the audited period.

    Eligible: delivered orders that day and a complete trip list for that day.
    """
    days = sorted({_trip_day(t) for t in evidence.trips if t.status == "delivered" and _trip_day(t)})
    eligible = [d for d in days if covered(evidence.trip_coverage, d, d)]
    if period:
        up_to = [d for d in eligible if d <= period[1]]
        if up_to:
            eligible = up_to
    return eligible[-count:]


def _multi_rank_key(item: Dict[str, Any]) -> Tuple:
    """More documented reward on the days reached, then more days reached, then fewest orders short."""
    return (-item["reward_total"], -item["days_reached"], item["avg_shortfall"], -item["best_reward"])


def _multi_day_fit(evidence: IncentiveEvidence, store: IncentiveRuleStore, audited_platform: str,
                   period: Optional[Tuple[date, date]], count: int) -> Dict[str, Any]:
    own = evidence.for_platform(audited_platform)
    days = choose_days(own, period, count)
    base: Dict[str, Any] = {
        "question": QUESTION,
        "kind": "incentive_fit",
        "audited_platform": audited_platform,
        "comparison_unit": "days",
        "comparison_day": None,
        "comparison_days": [d.isoformat() for d in days],
        "days_requested": count,
        "disclaimer": disclaimer(None),
        "assumptions": [],
        "platforms": [],
        "not_compared": [],
    }
    if not days:
        return {**base, "status": INSUFFICIENT_DATA, "headline": CANNOT_COMPARE,
                "why": [f"Your records have no day with a complete list of delivered {audited_platform} orders."]}

    by_day = {d: [t for t in own.trips if _trip_day(t) == d and t.status == "delivered"] for d in days}
    counts = [len(by_day[d]) for d in days]
    k = len(days)
    span = _day(days[0]) if k == 1 else f"{_day(days[0])} to {_day(days[-1])}"
    try:
        assessed = {p: [_assess(p, d, by_day[d], store, evidence.region) for d in days] for p in store.platforms()}
    except RuleStoreUnavailable as exc:
        return {**base, "status": INSUFFICIENT_DATA, "headline": CANNOT_COMPARE,
                "why": [f"FairPay could not reach its incentive rule knowledge base ({exc})."]}

    items: List[Dict[str, Any]] = []
    not_compared: List[str] = []
    for platform, per_day in assessed.items():
        picks = [(d, sorted(a["comparable"], key=_rank_key)[0]) for d, a in zip(days, per_day) if a["comparable"]]
        reasons = list(dict.fromkeys(x for a in per_day for x in a["not_comparable"]))
        if not picks:
            reason = "; ".join(reasons) or "no daily incentive is on record for these days in your region"
            not_compared.append(f"{platform}: {reason}")
            continue
        not_compared.extend(f"{platform}: {x}" for x in reasons)
        reached = [x for _, x in picks if x["reached"]]
        possible = [x for _, x in picks if x["reached"] or x["uncertain"]]
        items.append({
            "platform": platform,
            "picks": picks,
            "days_with_rule": len(picks),
            "days_reached": len(reached),
            "days_possible": len(possible),
            "reward_total": sum((x["rule"].reward_amount for x in reached), Decimal(0)),
            "avg_shortfall": Decimal(sum(x["shortfall"] for _, x in picks)) / len(picks),
            "best_reward": max(x["rule"].reward_amount for _, x in picks),
            "rule": picks[-1][1]["rule"],
        })

    hours = sorted({f"{(t.accepted_at or t.completed_at).hour:02d}:00"
                    for d in days for t in by_day[d] if (t.accepted_at or t.completed_at)})
    pattern = {
        "platform": audited_platform, "unit": f"{k} {_plural(k, 'day', 'days')} ({span})", "periods_observed": k,
        "period_start": days[0].isoformat(), "period_end": days[-1].isoformat(),
        "typical_orders_low": min(counts), "typical_orders_high": max(counts), "median_orders": median_low(counts),
        "orders_per_day": {d.isoformat(): len(by_day[d]) for d in days}, "busiest_hours": hours,
        "acceptance_rate": "available" if any(m.metric == "acceptance_rate" for m in own.metrics) else "not available",
        "login_time": "available" if any(m.metric.startswith("login") for m in own.metrics) else "not available",
        "provenance": "CALCULATED from the orders in your records for these days",
    }
    if len(items) < 2:
        why = [f"To compare, FairPay needs the published daily target and reward of at least two platforms for "
               f"{span} in your region; it has {len(items)}."]
        return {**base, "status": INSUFFICIENT_DATA, "headline": CANNOT_COMPARE, "why": why,
                "not_compared": list(dict.fromkeys(not_compared)), "rider_pattern": pattern}

    ranked = sorted(items, key=_multi_rank_key)
    top, second = ranked[0], ranked[1]
    daily = ", ".join(f"{_day(d)}: {len(by_day[d])}" for d in days)
    why: List[str] = [f"You did these {audited_platform} orders on the {k} {_plural(k, 'day', 'days')} compared - {daily}."]
    for i in ranked:
        targets = sorted({x["rule"].target_orders for _, x in i["picks"]})
        rewards = sorted({x["rule"].reward_amount for _, x in i["picks"]})
        line = (f"{i['platform']}'s documented daily target is {' or '.join(str(t) for t in targets)} orders with a "
                f"documented reward of {' or '.join(_inr(r) for r in rewards)}; your orders would reach it on "
                f"{i['days_reached']} of {i['days_with_rule']} {_plural(i['days_with_rule'], 'day', 'days')}")
        unsure = i["days_possible"] - i["days_reached"]
        if unsure:
            line += (f", and on {unsure} more {_plural(unsure, 'day', 'days')} it depends on counting rules "
                     f"{i['platform']} does not publish")
        line += "."
        if i["days_with_rule"] < k:
            line += f" No daily incentive of {i['platform']}'s is on record for the other {k - i['days_with_rule']}."
        unchecked = _conditions_not_checked(i["rule"])
        if unchecked:
            line += " It also requires " + ", ".join(unchecked) + ", which FairPay could not check."
        line += f" Source: {i['picks'][-1][1]['evidence']}."
        why.append(line)

    if _multi_rank_key(top) == _multi_rank_key(second):
        status, recommended = NO_CLEAR_DIFFERENCE, None
        headline = (f"Over the {k} days compared ({span}), {top['platform']} and {second['platform']} have documented "
                    "daily incentives that fit your days equally; no single platform stands out.")
    else:
        status, recommended = FIT_FOUND, top["platform"]
        if top["days_reached"]:
            headline = (f"Over the {k} days compared ({span}), {top['platform']}'s documented daily incentive fits "
                        f"your days best: your orders would reach its target on {top['days_reached']} of "
                        f"{top['days_with_rule']} days, and the documented rewards of those days add up to "
                        f"{_inr(top['reward_total'])}, the most among the platforms compared.")
        else:
            short = math.ceil(top["avg_shortfall"])
            headline = (f"None of the documented daily targets would be reached on any of the {k} days compared "
                        f"({span}). {top['platform']}'s is the closest: about {short} "
                        f"{_plural(short, 'order', 'orders')} short a day, with a documented reward of "
                        f"{_inr(top['rule'].reward_amount)}.")

    assumptions = [
        f"This uses {k} {_plural(k, 'day', 'days')} of your {audited_platform} orders from the audited history "
        f"({span}). It assumes you could do the same number of orders on another platform on those days; demand, "
        "order availability and your area may be different there.",
        "Every platform is compared day by day using the daily incentive rules in force on each day in your region.",
        "The ranking puts the most documented reward on the days your orders reach first, then the most days reached. "
        "Rewards are listed as documented, not as amounts you would receive, and incentives are not offered every day.",
    ]
    if k < count:
        assumptions.append(f"Only {k} {_plural(k, 'day', 'days')} in your history had a complete list of delivered "
                           f"orders, so FairPay compared {k} instead of {count}.")
    weak = [i for i in ranked if i["rule"].confidence_label != "HIGH"]
    if weak:
        assumptions.append(
            "Some of these terms are not published by the platform: "
            + "; ".join(f"{i['platform']} ({i['picks'][-1][1]['evidence']})" for i in weak)
            + ". A comparison is only as good as the rules behind it.")
    demo = [i for i in ranked if i["rule"].evidence_class == "demonstration_dataset"]
    if demo:
        assumptions.append(
            "These targets come from FairPay's demonstration dataset, not from the platforms: "
            + ", ".join(i["platform"] for i in demo)
            + ". They show how the comparison works rather than what the platforms actually offer.")
    not_compared = list(dict.fromkeys(not_compared))
    if not_compared:
        assumptions.append("Not compared: " + "; ".join(not_compared) + ".")

    public = []
    for i in ranked:
        r, n_rule = i["rule"], i["days_with_rule"]
        public.append({
            "platform": i["platform"],
            "periods_compared": n_rule,
            "periods_without_rule": k - n_rule,
            "target_met_min": i["days_reached"],
            "target_met_max": i["days_possible"],
            "share_min": f"{Decimal(i['days_reached']) / n_rule:.2f}",
            "share_max": f"{Decimal(i['days_possible']) / n_rule:.2f}",
            "orders_counted": ", ".join(x["orders_counted"] for _, x in i["picks"]),
            "reached": i["days_reached"] > 0,
            "shortfall": 0 if i["days_reached"] else math.ceil(i["avg_shortfall"]),
            "days_reached": i["days_reached"],
            "days_compared": n_rule,
            "documented_reward_total": str(i["reward_total"]),
            "target_orders": list(dict.fromkeys(x["rule"].target_orders for _, x in i["picks"])),
            "reward_amounts": list(dict.fromkeys(str(x["rule"].reward_amount) for _, x in i["picks"])),
            "reward_text": f"{_inr(r.reward_amount)} per target reached (as documented)",
            "time_windows": r.time_windows,
            "conditions_not_checked": _conditions_not_checked(r),
            "rule_versions": list(dict.fromkeys(x["rule"].version for _, x in i["picks"])),
            "rule_sources": list(dict.fromkeys(x["rule"].source_reference for _, x in i["picks"])),
            "payout_timing": r.payout_timing,
            "confidence_label": r.confidence_label,
            "source_tier": r.source_tier,
            "evidence_class": r.evidence_class,
            "evidence_summary": i["picks"][-1][1]["evidence"],
            "source_reference": r.source_reference,
            "is_audited_platform": platform_key(i["platform"]) == platform_key(audited_platform),
        })

    assert_no_earnings_claim([headline, *why, *assumptions])
    return {
        **base,
        "status": status,
        "headline": headline,
        "recommended_platform": recommended,
        "why": why,
        "assumptions": assumptions,
        "platforms": public,
        "not_compared": not_compared,
        "rider_pattern": pattern,
        "disclaimer": disclaimer(recommended),
    }
