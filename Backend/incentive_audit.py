"""Incentive audit: "Why didn't I get my incentive?"

Separate from the full audit. It never runs the Layer 1/2/3 pipeline and its
results are never merged into contract, legal or economic findings.

Flow::

    IncentiveEvidence (incentive_evidence)
        -> rule retrieval per platform / incentive type / period (incentive_rules)
        -> deterministic evaluation per period (incentive_engine)
        -> one primary finding + plain-language result (this module)
        -> personalized incentive fit, as a separate block (incentive_fit)

All wording here is generated from the deterministic results with fixed
templates. No LLM decides or phrases a conclusion, so no number in the text
can drift from the evaluation behind it.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from incentive_engine import (
    AMBIGUOUS,
    COUNTING_MISMATCH,
    ELIGIBILITY_FAILURE,
    FAIL,
    INCENTIVE_RECEIVED,
    INSUFFICIENT_EVIDENCE,
    PASS,
    PAYMENT_NOT_OBSERVED,
    POSSIBLE_PAYMENT_FAILURE,
    UNKNOWN,
    PeriodEvaluation,
    evaluate_period,
    period_for,
)
from incentive_evidence import IncentiveEvidence, PlatformCountObservation
from incentive_rules import (
    ESTABLISHED,
    NOT_ESTABLISHED,
    RULE_NOT_ESTABLISHED_TEXT,
    IncentiveRule,
    IncentiveRuleStore,
    RuleQuery,
    RuleResolution,
    platform_key,
    RuleStoreUnavailable,
    KB_UNAVAILABLE_TEXT,
    resolve_program,
)

QUESTION = "Why didn't I get my incentive?"

# Said only when every condition of the incentive was met and its payment is not seen.
ALL_CONDITIONS_MET_TEXT = ("You have met all the necessary conditions required. If you still haven't received your "
                           "incentive, do not panic, you might receive it.")

# Which outcome to put in front when the partner did not say which incentive
# they mean: the ones that need their action or evidence first, then the most
# recent period.
_PRIORITY = {
    COUNTING_MISMATCH: 0,
    POSSIBLE_PAYMENT_FAILURE: 1,
    AMBIGUOUS: 2,
    ELIGIBILITY_FAILURE: 3,
    PAYMENT_NOT_OBSERVED: 4,
    INSUFFICIENT_EVIDENCE: 5,
    INCENTIVE_RECEIVED: 9,
}

STATUS_TITLES = {
    INCENTIVE_RECEIVED: "Incentive received",
    COUNTING_MISMATCH: "The platform appears to have counted fewer orders",
    ELIGIBILITY_FAILURE: "A condition of the incentive was not met",
    PAYMENT_NOT_OBSERVED: "Conditions met, payment not visible in what you sent",
    POSSIBLE_PAYMENT_FAILURE: "Conditions met, but the payment is not in your records",
    INSUFFICIENT_EVIDENCE: "Not enough evidence to decide",
    AMBIGUOUS: "The evidence does not give one answer",
}


# ---------------------------------------------------------------------------
# Small text helpers
# ---------------------------------------------------------------------------

def inr(amount: Optional[Decimal]) -> str:
    if amount is None:
        return "an unknown amount"
    text = f"{Decimal(amount).quantize(Decimal('0.01')):,.2f}"
    return f"₹{text[:-3] if text.endswith('.00') else text}"


def _day(d: date) -> str:
    return d.strftime("%d %b %Y").lstrip("0")


def period_label(start: date, end: date) -> str:
    if start == end:
        return d_label(start)
    return f"{_day(start)} to {_day(end)}"


def d_label(d: date) -> str:
    return d.strftime("%a %d %b %Y").replace(" 0", " ")


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


# ---------------------------------------------------------------------------
# Periods to evaluate
# ---------------------------------------------------------------------------

def _trip_days(evidence: IncentiveEvidence) -> List[date]:
    days = set()
    for trip in evidence.trips:
        for d in trip.dates().values():
            if d:
                days.add(d)
    return sorted(days)


def _evidence_span(evidence: IncentiveEvidence) -> Optional[Tuple[date, date]]:
    days = _trip_days(evidence)
    for c in evidence.platform_counts:
        days += [c.period_start, c.period_end]
    days += [r.start for r in evidence.trip_coverage] + [r.end for r in evidence.trip_coverage]
    return (min(days), max(days)) if days else None


def _offer_rule(offer: PlatformCountObservation) -> IncentiveRule:
    """An incentive whose terms are known only from the offer printed in the evidence."""
    return IncentiveRule(
        rule_id=f"offer:{platform_key(offer.platform)}:{offer.period_start}:{offer.period_end}",
        program_id=f"offer:{platform_key(offer.platform)}", version="as shown",
        platform=offer.platform, incentive_name=offer.incentive_name or "Incentive shown in your documents",
        incentive_type="period_target", period_scope="custom",
        effective_from=offer.period_start, effective_to=offer.period_end,
        target_orders=offer.target_orders, reward_amount=offer.reward_amount or Decimal("0"),
        source_type="offer_shown_in_evidence", source_reference=f"the offer shown on {offer.source}",
        confidence=Decimal("0.7"), conditions_complete=False,
        description="Terms copied from the offer printed in the partner's documents.",
    )


def _offer_shape(offer: PlatformCountObservation) -> str:
    if offer.period_start == offer.period_end:
        return "daily_target"
    if offer.period_start.weekday() == 0 and (offer.period_end - offer.period_start).days == 6:
        return "weekly_target"
    return "period_target"


def evaluate_evidence(evidence: IncentiveEvidence, store: IncentiveRuleStore,
                      platform: Optional[str] = None) -> Tuple[List[PeriodEvaluation], List[str]]:
    """Every incentive period the evidence touches, on every platform, evaluated.

    Rules come from Qdrant only. Each incentive programme is resolved on its
    own. A programme whose target is not published is evaluated only when
    the partner's documents show the target (their incentive screen);
    otherwise it is listed as not checked, with the reason.
    Returns ``(evaluations, notes)``.
    """
    platforms = [platform] if platform else (evidence.platforms or sorted({t.platform for t in evidence.trips}))
    evaluations: List[PeriodEvaluation] = []
    notes: List[str] = []
    for name in platforms:
        ev = evidence.for_platform(name)
        span = _evidence_span(ev)
        if span is None:
            continue
        try:
            rules = store.retrieve(RuleQuery(
                platform=name, period_start=span[0] - timedelta(days=6), period_end=span[1] + timedelta(days=6),
                region=evidence.region,
                evidence_text=" ".join(filter(None, [c.incentive_name for c in ev.platform_counts]))))
        except RuleStoreUnavailable as exc:
            notes.append(f"{KB_UNAVAILABLE_TEXT} ({name}: {exc})")
            continue
        programs: Dict[str, List[IncentiveRule]] = defaultdict(list)
        for rule in rules:
            programs[rule.program_id].append(rule)
        offers = list(ev.platform_counts)
        used_offers: set = set()
        days = _trip_days(ev)

        def offer_for(start: date, end: date) -> List[PlatformCountObservation]:
            return [o for o in offers if o.period_start == start and o.period_end == end]

        def run(versions: List[IncentiveRule], start: date, end: date, kind: str) -> None:
            resolution = resolve_program(versions, start, end)
            if resolution.status == NOT_ESTABLISHED and not resolution.versions:
                return
            here = offer_for(start, end)
            rule = resolution.rule
            if (rule is not None and rule.target_orders is None and not any(o.target_orders for o in here)
                    and not rule.measurable_without_order_target()):
                notes.append(
                    f"{rule.incentive_name} ({rule.source_reference}): {name} does not publish the order target or any "
                    f"condition FairPay can check, so it needs the incentive screen from your app for "
                    f"{period_label(start, end)}.")
                return
            used_offers.update(id(o) for o in here)
            evaluations.append(evaluate_period(ev, resolution, name, start, end, kind))

        # Day and Monday-Sunday programmes: every period the evidence touches.
        for program_id, versions in sorted(programs.items()):
            kind = versions[0].incentive_type
            if versions[0].period_scope not in ("day", "week_mon_sun"):
                continue
            if kind not in ("daily_target", "weekly_target", "minimum_guarantee", "period_target", "per_order_bonus"):
                continue
            for start, end in sorted({period_for(d, versions[0]) for d in days}):
                run(versions, start, end, kind)

        # Programmes with no fixed period (or not saying daily/weekly) need the
        # period printed on the partner's incentive screen.
        flexible = {pid: v for pid, v in programs.items() if v[0].incentive_type in ("period_target", "unspecified")}
        for offer in offers:
            if id(offer) in used_offers:
                continue
            matched = False
            for program_id, versions in sorted(flexible.items()):
                resolution = resolve_program(versions, offer.period_start, offer.period_end)
                if resolution.status == NOT_ESTABLISHED:
                    continue
                run(versions, offer.period_start, offer.period_end, _offer_shape(offer))
                matched = True
                break
            if not matched and offer.target_orders:
                rule = _offer_rule(offer)
                evaluations.append(evaluate_period(ev, RuleResolution(
                    status=ESTABLISHED, rule=rule, versions=[rule], retrieval_backend="evidence",
                    note="No documented rule matches this incentive; the terms printed on your incentive screen were used."),
                    name, offer.period_start, offer.period_end, _offer_shape(offer)))
            used_offers.add(id(offer))

        for program_id, versions in sorted(programs.items()):
            kind = versions[0].incentive_type
            if kind in ("joining_bonus", "referral_bonus"):
                notes.append(f"{versions[-1].incentive_name} ({versions[-1].source_reference}) is a one-off bonus, not "
                             "a period incentive, so it was not part of this check.")
            elif kind in ("per_order_bonus", "earnings_target") and not any(
                    e.rule and e.rule.program_id == program_id for e in evaluations):
                notes.append(f"{versions[-1].incentive_name} ({versions[-1].source_reference}) is paid per order rather "
                             "than for reaching a target, so there is no target to check.")
            elif kind in ("period_target", "unspecified") and not any(
                    e.rule and e.rule.program_id == program_id for e in evaluations):
                notes.append(f"{versions[-1].incentive_name} ({versions[-1].source_reference}): {name} does not publish "
                             "its target or period, so FairPay needs the incentive screen from your app to check it.")
    return evaluations, list(dict.fromkeys(notes))


def _select(evaluations: List[PeriodEvaluation], period_date: Optional[date],
            incentive_type: Optional[str]) -> List[PeriodEvaluation]:
    pool = evaluations
    if period_date:
        pool = [e for e in pool if e.period_start <= period_date <= e.period_end]
    if incentive_type:
        pool = [e for e in pool if e.incentive_type == incentive_type]
    return sorted(pool, key=lambda e: (_PRIORITY.get(e.outcome, 8), -e.period_end.toordinal(),
                                       0 if e.incentive_type == "daily_target" else 1))


# ---------------------------------------------------------------------------
# Presentation of one evaluation
# ---------------------------------------------------------------------------

def _conditions_split(ev: PeriodEvaluation) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {"passed": [], "failed": [], "unknown": []}
    key = {PASS: "passed", FAIL: "failed", UNKNOWN: "unknown"}
    for c in ev.conditions:
        out[key[c.status]].append(c.model_dump(mode="json"))
    return out


def _required_lines(ev: PeriodEvaluation) -> List[str]:
    rule = ev.rule
    if rule is None:
        return []
    scope = {"day": "in one day", "week_mon_sun": "in one week (Monday to Sunday)"}.get(
        rule.period_scope, f"between {period_label(ev.period_start, ev.period_end)}")
    if ev.target_orders is not None:
        reward = inr(ev.reward_amount) if ev.reward_amount is not None else (rule.reward_text or "the reward (not published)")
        lines = [f"Complete {ev.target_orders} orders on {ev.platform} {scope} to get {reward}."]
    else:
        lines = [f"{ev.platform} does not publish the order target or reward for this incentive."]
    if ev.target_source == "offer_in_evidence":
        lines.append("The target is the one shown on your incentive screen.")
    if rule.min_acceptance_rate is not None:
        lines.append(f"Keep your acceptance rate at or above {rule.min_acceptance_rate * 100:.0f}%.")
    if rule.max_rejection_rate is not None:
        lines.append(f"Keep your rejection rate at or below {rule.max_rejection_rate * 100:.0f}%.")
    if rule.min_login_minutes is not None:
        h, m = divmod(rule.min_login_minutes, 60)
        lines.append(f"Stay logged in for at least {h}h" + (f" {m}m" if m else "") + ".")
    if rule.required_login_windows:
        lines.append("Be logged in during " + " and ".join(rule.required_login_windows) + ".")
    if rule.min_rating is not None:
        lines.append(f"Keep a rating of at least {rule.min_rating}.")
    if rule.opt_in_required:
        lines.append("Sign up for the incentive in the app before it starts.")
    lines.extend(rule.other_conditions)
    if rule.payout_timing:
        lines.append(f"Payment: {rule.payout_timing}.")
    if rule.time_windows:
        lines.append("Only orders between " + " or ".join(rule.time_windows) + " count.")
    if rule.min_distance_km is not None:
        lines.append(f"Only orders of at least {rule.min_distance_km} km count.")
    if rule.counting.multi_order:
        lines.append("A batched trip counts as "
                     + ("one order for each order delivered." if rule.counting.multi_order == "each_order" else "one order."))
    for name, text in (("cancelled", "Cancelled orders"), ("incomplete", "Orders not completed")):
        treatment = getattr(rule.counting, name)
        if treatment:
            lines.append(f"{text} {'count' if treatment == 'counted' else 'do not count'}.")
    if rule.counting.day_boundary:
        lines.append("An order counts on the day it was "
                     + ("accepted." if rule.counting.day_boundary == "accepted_at" else "delivered."))
    return lines


def _found_lines(ev: PeriodEvaluation) -> List[str]:
    lines: List[str] = []
    c = ev.count
    if c is None:
        return lines
    if c.min_count == c.max_count:
        lines.append(f"FairPay counted {c.min_count} qualifying {_plural(c.min_count, 'order', 'orders')} "
                     f"on {ev.platform} for {period_label(ev.period_start, ev.period_end)}.")
    else:
        lines.append(f"FairPay counted {c.min_count} orders that certainly qualify and {c.max_count - c.min_count} "
                     f"more that may qualify ({c.min_count} to {c.max_count}).")
    if ev.platform_count is not None:
        lines.append(f"The platform's progress screen shows {ev.platform_count} orders counted.")
    if c.excluded:
        lines.append(f"{len(c.excluded)} {_plural(len(c.excluded), 'order does', 'orders do')} not count "
                     "(see the orders below for the reason).")
    for q in c.open_questions:
        lines.append(f"Not documented in the rules: {q.lower()}.")
    if not ev.trips_complete and ev.platform_count is None and ev.target_orders is not None and c.max_count < ev.target_orders:
        lines.append("The evidence may not include every order of this period.")
    if ev.payment and (ev.payment.status in ("paid", "paid_different_amount")
                       or ev.outcome in (POSSIBLE_PAYMENT_FAILURE, PAYMENT_NOT_OBSERVED)):
        lines.append(ev.payment.detail)
    return lines


def _headline(ev: PeriodEvaluation) -> str:
    rule, c = ev.rule, ev.count
    when = period_label(ev.period_start, ev.period_end)
    if ev.outcome == INCENTIVE_RECEIVED:
        return (f"You received the {rule.incentive_name} for {when}: {inr(ev.payment.amount)} "
                f"is shown in your earnings records.")
    if ev.outcome == COUNTING_MISMATCH and ev.mismatch:
        m = ev.mismatch
        n = m.fairpay_min if m.basis != "undocumented_counting_rule" else m.fairpay_max
        word = "qualifying" if m.basis != "undocumented_counting_rule" else "potentially qualifying"
        return (f"You completed {n} {word} orders for {when}, but {ev.platform} appears to have counted "
                f"{m.platform_count}. The target was {ev.target_orders}.")
    if ev.outcome == ELIGIBILITY_FAILURE:
        failed = [x for x in ev.conditions if x.status == FAIL]
        target_failed = any(x.condition == "order_target" for x in failed)
        others = [x for x in failed if x.condition != "order_target"]
        if target_failed and not others:
            reached = max(c.max_count, ev.platform_count or 0)
            amount = str(reached) if c.min_count == c.max_count else f"at most {reached}"
            text = (f"You completed {amount} qualifying {_plural(reached, 'order', 'orders')} on {ev.platform} for "
                    f"{when}, and the target was {ev.target_orders}.")
            if ev.platform_count is not None and ev.mismatch:
                text = (f"{ev.platform} counted {ev.platform_count} of the {ev.target_orders} orders needed for "
                        f"{when}. FairPay found up to {ev.mismatch.fairpay_max} orders that could count, which is "
                        f"still short of the target, so the counting difference does not explain the missed incentive.")
            elif ev.platform_count is not None:
                text = (f"{ev.platform} counted {ev.platform_count} of the {ev.target_orders} orders needed for "
                        f"{when}, and FairPay found no qualifying order it missed.")
            if ev.shortfall:
                text += f" You were {ev.shortfall} {_plural(ev.shortfall, 'order', 'orders')} short."
            return text
        parts = [f"you earned {x.observed} (required: {x.required})" if x.condition == "payout_target"
                 else f"your {x.label.lower()} was {x.observed} (required: {x.required})" for x in others]
        target_cond = ev.conditions[0]
        if target_failed:
            parts.insert(0, f"the order target was not reached ({target_cond.observed}; required {ev.target_orders})")
            return "The incentive was not earned: " + "; ".join(parts) + "."
        if target_cond.status == PASS and ev.target_orders is None:
            return "This incentive has no order target. The available evidence shows " + "; ".join(parts) + "."
        if target_cond.status == PASS:
            return (f"You reached the {ev.target_orders}-order target, but the available evidence shows "
                    + "; ".join(parts) + ".")
        return "The available evidence shows " + "; ".join(parts) + ", so the incentive was not earned."
    if ev.outcome in (POSSIBLE_PAYMENT_FAILURE, PAYMENT_NOT_OBSERVED):
        # Only these two outcomes mean every condition passed: no failure, nothing unknown, no conflict.
        return ALL_CONDITIONS_MET_TEXT
    if ev.outcome == AMBIGUOUS:
        if ev.conflicts:
            return "FairPay cannot give one answer because the evidence conflicts: " + ev.conflicts[0]
        if ev.payment and ev.payment.status == "paid_different_amount":
            return ev.payment.detail
        return ev.rule_note or "FairPay cannot give one answer for this incentive."
    # INSUFFICIENT_EVIDENCE
    if rule is None:
        return RULE_NOT_ESTABLISHED_TEXT
    unknown = [x for x in ev.conditions if x.status == UNKNOWN]
    target = ev.conditions[0]
    if target.status == PASS and unknown and ev.target_orders is None:
        return ("This incentive has no order target, but the available evidence does not show your "
                + " or ".join(x.label.lower() for x in unknown)
                + ". FairPay cannot determine whether you qualified.")
    if target.status == PASS and unknown:
        return ("Your order count meets the target, but the available evidence does not show your "
                + " or ".join(x.label.lower() for x in unknown)
                + ". FairPay cannot determine whether you qualified.")
    if target.status == UNKNOWN and ev.target_orders is None:
        return f"FairPay cannot tell whether you qualified for {when}. {target.detail}"
    if target.status == UNKNOWN:
        return f"FairPay cannot tell whether you reached the {ev.target_orders}-order target for {when}. {target.detail}"
    return "FairPay cannot determine whether you qualified from the evidence available."


def _next_action(ev: PeriodEvaluation) -> str:
    when = period_label(ev.period_start, ev.period_end)
    name = ev.rule.incentive_name if ev.rule else "incentive"
    if ev.outcome == INCENTIVE_RECEIVED:
        return "No action needed for this incentive."
    if ev.outcome == COUNTING_MISMATCH and ev.mismatch:
        ids = ", ".join(ev.mismatch.orders[:6])
        return (f"Ask {ev.platform} support how your orders were counted for the {name} on {when}"
                + (f", in particular orders {ids}" if ids else "")
                + ". Keep the progress screen and trip history as proof.")
    if ev.outcome == ELIGIBILITY_FAILURE:
        failed = [x for x in ev.conditions if x.status == FAIL and x.condition != "order_target"]
        if failed:
            return (f"If the {failed[0].label.lower()} shown does not match your own records, ask {ev.platform} "
                    f"support how it was measured for {when}.")
        return (f"Nothing appears to be missing: the documented target was not reached. If you did more orders "
                f"on {when} than these records show, send the full trip history for that period.")
    if ev.outcome == POSSIBLE_PAYMENT_FAILURE:
        return (f"Ask {ev.platform} support whether the {name} for {when} was paid and where it appears in your "
                "statement. Keep these records as proof.")
    if ev.outcome == PAYMENT_NOT_OBSERVED:
        days = ev.rule.payout_window_days if ev.rule else None
        end = ev.period_end + timedelta(days=days or 0)
        return f"Send your {ev.platform} earnings statement or payout record covering {period_label(ev.period_start, end)}."
    if ev.outcome == AMBIGUOUS:
        return "Send the screen that shows the incentive's terms and your progress for this period, or ask the platform which figure is correct."
    return "Send the evidence listed below so FairPay can finish the check."


def _evidence_requests(ev: PeriodEvaluation) -> List[str]:
    when = period_label(ev.period_start, ev.period_end)
    requests: List[str] = []
    if ev.rule is None:
        requests.append(f"A screenshot of the {ev.platform} incentive offer for {when}, showing its target, reward and conditions.")
        return requests
    if ev.outcome in (INCENTIVE_RECEIVED, ELIGIBILITY_FAILURE, COUNTING_MISMATCH) and not ev.conflicts:
        return requests
    for cond in ev.conditions:
        if cond.status != UNKNOWN:
            continue
        if cond.condition == "order_target":
            if ev.count and ev.count.open_questions and ev.platform_count is None:
                requests.append(f"The {ev.platform} incentive progress screen for {when}, which shows how many orders the app counted.")
            elif not ev.trips_complete:
                requests.append(f"Your complete {ev.platform} trip history for {when}, including the total number of orders shown.")
        elif cond.condition == "acceptance_rate":
            requests.append(f"The {ev.platform} screen showing your acceptance rate for {when}.")
        elif cond.condition == "rejection_rate":
            requests.append(f"The {ev.platform} screen showing your rejection rate for {when}.")
        elif cond.condition == "login_minutes":
            requests.append(f"The {ev.platform} screen showing your login (online) hours for {when}.")
        elif cond.condition == "other_terms":
            requests.append(f"The full terms of the {ev.platform} incentive for {when} (the offer's details screen).")
    if ev.outcome == PAYMENT_NOT_OBSERVED:
        requests.append(f"Your {ev.platform} earnings statement or payout record covering "
                        f"{period_label(ev.period_start, ev.period_end + timedelta(days=(ev.rule.payout_window_days or 0)))}.")
    return list(dict.fromkeys(requests))


def _evidence_trail(ev: PeriodEvaluation) -> List[Dict[str, str]]:
    trail: List[Dict[str, str]] = []
    if ev.rule:
        prov = "OBSERVED" if ev.rule.source_type == "offer_shown_in_evidence" else "RETRIEVED_RULE"
        value = (f"{ev.target_orders} orders for {inr(ev.reward_amount)}" if ev.target_orders is not None
                 else "not published")
        if ev.target_source == "offer_in_evidence":
            prov = "OBSERVED"
        trail.append({"fact": "Target and reward", "value": value, "provenance": prov,
                      "source": ev.rule.source_reference + (" and your incentive screen" if ev.target_source == "offer_in_evidence" else "")})
        trail.append({
            "fact": "Where this incentive's terms come from",
            "value": f"{ev.rule.source_publisher or ev.rule.source_type} - {ev.rule.confidence_label} confidence"
                     + (" - FairPay demonstration data" if ev.rule.evidence_class == "demonstration_dataset" else ""),
            "provenance": prov,
            "source": (ev.rule.source_title or ev.rule.source_reference)
                      + (f" ({ev.rule.source_date})" if ev.rule.source_date else "")
                      + f", tier {ev.rule.source_tier} evidence, status {ev.rule.evidence_status}",
        })
    else:
        trail.append({"fact": "Incentive rule", "value": "not established", "provenance": "UNKNOWN",
                      "source": ev.rule_note or RULE_NOT_ESTABLISHED_TEXT})
    if ev.count:
        prov = "INFERRED" if ev.count.inferred else "CALCULATED"
        value = str(ev.count.min_count) if ev.count.min_count == ev.count.max_count else f"{ev.count.min_count} to {ev.count.max_count}"
        trail.append({"fact": "Orders counted by FairPay", "value": value, "provenance": prov,
                      "source": "the orders in the evidence, counted under the rule"})
    if ev.platform_count is not None:
        trail.append({"fact": "Orders counted by the platform", "value": str(ev.platform_count),
                      "provenance": "OBSERVED", "source": "the incentive progress screen"})
    for cond in ev.conditions[1:]:
        trail.append({"fact": cond.label, "value": cond.observed, "provenance": cond.provenance, "source": cond.detail})
    if ev.mismatch and ev.mismatch.orders:
        trail.append({"fact": "Orders that appear not to have been counted", "value": ", ".join(ev.mismatch.orders),
                      "provenance": "INFERRED", "source": ev.mismatch.explanation})
    if ev.payment:
        prov = {"paid": "OBSERVED", "paid_different_amount": "OBSERVED", "not_visible": "CALCULATED",
                "not_checkable": "UNKNOWN"}[ev.payment.status]
        trail.append({"fact": "Incentive payment", "value": ev.payment.status.replace("_", " "),
                      "provenance": prov, "source": ev.payment.detail})
    return trail


def _transactions(ev: PeriodEvaluation, limit: int = 40) -> List[Dict[str, Any]]:
    if not ev.count:
        return []
    rows = []
    for counted, items in (("uncertain", ev.count.uncertain), ("no", ev.count.excluded), ("yes", ev.count.definite)):
        for o in items:
            rows.append({"order_id": o.order_id, "when": o.when, "status": o.status, "counts": counted,
                         "reason": o.reason, "provenance": o.provenance,
                         "flagged": bool(ev.mismatch and o.order_id in ev.mismatch.orders)})
    return rows[:limit]


def present(ev: PeriodEvaluation) -> Dict[str, Any]:
    """The partner-facing result for one incentive period."""
    rule = ev.rule
    return {
        "question": QUESTION,
        "status": ev.outcome,
        "status_title": STATUS_TITLES[ev.outcome],
        "headline": _headline(ev),
        "platform": ev.platform,
        "period_start": ev.period_start.isoformat(),
        "period_end": ev.period_end.isoformat(),
        "period_label": period_label(ev.period_start, ev.period_end),
        "near_miss": ev.near_miss,
        "shortfall": ev.shortfall,
        "incentive": None if rule is None else {
            "name": rule.incentive_name,
            "type": rule.incentive_type,
            "target_orders": ev.target_orders,
            "reward_amount": None if ev.reward_amount is None else str(ev.reward_amount),
            "target_source": ev.target_source,
            "payout_timing": rule.payout_timing,
            "source_reference": rule.source_reference,
            "source_type": rule.source_type,
            "version": rule.version,
        },
        "rule_status": ev.rule_status,
        # How the rule was looked up (vector index or the identical local filter); not shown to partners.
        "rule_lookup": ev.retrieval_backend,
        "what_it_required": _required_lines(ev),
        "what_fairpay_found": _found_lines(ev),
        "conditions": _conditions_split(ev),
        "counting": None if ev.count is None else {
            "min_count": ev.count.min_count,
            "max_count": ev.count.max_count,
            "platform_count": ev.platform_count,
            "open_questions": ev.count.open_questions,
            "trips_complete": ev.trips_complete,
        },
        "counting_mismatch": ev.mismatch.model_dump(mode="json") if ev.mismatch else None,
        "payment": ev.payment.model_dump(mode="json") if ev.payment else None,
        "relevant_transactions": _transactions(ev),
        "next_action": _next_action(ev),
        "evidence_requests": _evidence_requests(ev),
        "evidence_trail": _evidence_trail(ev),
        "conflicts": ev.conflicts,
        "notes": ev.notes,
    }


def _summary_row(ev: PeriodEvaluation) -> Dict[str, Any]:
    c = ev.count
    return {
        "platform": ev.platform,
        "period_start": ev.period_start.isoformat(),
        "period_end": ev.period_end.isoformat(),
        "period_label": period_label(ev.period_start, ev.period_end),
        "incentive_type": ev.incentive_type,
        "incentive_name": ev.incentive_name,
        "status": ev.outcome,
        "status_title": STATUS_TITLES[ev.outcome],
        "target_orders": ev.target_orders,
        "orders_counted": None if c is None else (str(c.min_count) if c.min_count == c.max_count else f"{c.min_count}-{c.max_count}"),
        "near_miss": ev.near_miss,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_incentive_audit(
    evidence: IncentiveEvidence,
    store: IncentiveRuleStore,
    platform: Optional[str] = None,
    period_date: Optional[date] = None,
    incentive_type: Optional[str] = None,
    include_fit: bool = True,
) -> Dict[str, Any]:
    """Answer "Why didn't I get my incentive?" from the evidence, then the fit.

    ``period_date`` / ``incentive_type`` / ``platform`` narrow the question
    when the partner said which incentive they mean; otherwise the period that
    most needs attention is answered and every other period is listed.
    """
    evaluations, rule_notes = evaluate_evidence(evidence, store, platform)
    limitations: List[str] = list(evidence.read_gaps) + rule_notes

    if not evidence.trips and not evidence.platform_counts:
        primary = {
            "question": QUESTION, "status": INSUFFICIENT_EVIDENCE, "status_title": STATUS_TITLES[INSUFFICIENT_EVIDENCE],
            "headline": "FairPay could not find any orders or incentive progress in the evidence.",
            "next_action": "Send your trip history or earnings screenshot for the period of the incentive.",
            "evidence_requests": ["Your trip history or earnings screenshot for the period of the incentive."],
        }
        return _result(evidence, primary, [], limitations, None)

    selected = _select(evaluations, period_date, incentive_type)
    evidence_days = set(_trip_days(evidence)) | {
        c.period_start + timedelta(days=i) for c in evidence.platform_counts
        for i in range((c.period_end - c.period_start).days + 1)}
    if period_date and not selected and period_date in evidence_days:
        # The evidence covers the date; what is missing is a rule for it, or
        # the target of a rule the platform does not publish.
        unpublished = [n for n in rule_notes if "does not publish the order target" in n]
        headline = RULE_NOT_ESTABLISHED_TEXT
        if unpublished:
            headline = ("The platform does not publish the order target for this incentive, so FairPay cannot check "
                        f"it without the incentive screen from your app for {d_label(period_date)}.")
        primary = {
            "question": QUESTION, "status": INSUFFICIENT_EVIDENCE, "status_title": STATUS_TITLES[INSUFFICIENT_EVIDENCE],
            "headline": headline,
            "next_action": "Send a screenshot of the incentive offer showing its target, reward and dates.",
            "evidence_requests": [f"A screenshot of the incentive offer for {d_label(period_date)}, showing its target, reward and conditions."],
            "period_start": period_date.isoformat(), "period_end": period_date.isoformat(),
        }
        return _result(evidence, primary, evaluations, limitations, None)
    if period_date and not selected:
        span = _evidence_span(evidence)
        whose = "The orders you sent are" if evidence.source == "telegram" else "Your records here are"
        text = (f"{whose} from {period_label(*span)}, not from {d_label(period_date)}."
                if span else "The evidence has no orders for that date.")
        primary = {
            "question": QUESTION, "status": INSUFFICIENT_EVIDENCE, "status_title": STATUS_TITLES[INSUFFICIENT_EVIDENCE],
            "headline": f"{text} FairPay cannot check an incentive for a period the evidence does not cover.",
            "next_action": f"Send your trip history for {d_label(period_date)}.",
            "evidence_requests": [f"Your trip history or earnings screenshot for {d_label(period_date)}."],
            "period_start": period_date.isoformat(), "period_end": period_date.isoformat(),
        }
        return _result(evidence, primary, evaluations, limitations, None)
    if not selected:
        primary = {
            "question": QUESTION, "status": INSUFFICIENT_EVIDENCE, "status_title": STATUS_TITLES[INSUFFICIENT_EVIDENCE],
            "headline": RULE_NOT_ESTABLISHED_TEXT,
            "next_action": "Send a screenshot of the incentive offer showing its target, reward and dates.",
            "evidence_requests": ["A screenshot of the incentive offer showing its target, reward and dates."],
        }
        return _result(evidence, primary, evaluations, limitations, None)

    chosen = selected[0]
    primary = present(chosen)
    received = [e for e in evaluations if e.outcome == INCENTIVE_RECEIVED]
    if not period_date and chosen.outcome == INCENTIVE_RECEIVED:
        primary["headline"] = ("Every incentive FairPay could check in this evidence was received. " + primary["headline"])
    elif not period_date and len(evaluations) > 1:
        primary["selection_note"] = (
            f"FairPay checked {len(evaluations)} incentive {_plural(len(evaluations), 'period', 'periods')} "
            f"in your evidence ({len(received)} received). This is the one that most needs attention; "
            "the others are listed below.")
    return _result(evidence, primary, evaluations, limitations, chosen)


def _result(evidence: IncentiveEvidence, primary: Dict[str, Any], evaluations: List[PeriodEvaluation],
            limitations: List[str], chosen: Optional[PeriodEvaluation]) -> Dict[str, Any]:
    others = sorted(evaluations, key=lambda e: (e.platform, e.period_start, e.incentive_type))
    counts: Dict[str, int] = defaultdict(int)
    for e in evaluations:
        counts[e.outcome] += 1
    return {
        "kind": "incentive_audit",
        "partner_id": evidence.partner_id,
        "evidence_source": evidence.source,
        "primary": primary,
        "periods_checked": len(evaluations),
        "outcome_counts": dict(counts),
        "other_periods": [_summary_row(e) for e in others if e is not chosen],
        "limitations": limitations,
        "_chosen": chosen,  # removed before serialisation; used by the fit
    }


def build_incentive_report(
    evidence: IncentiveEvidence,
    store: IncentiveRuleStore,
    platform: Optional[str] = None,
    period_date: Optional[date] = None,
    incentive_type: Optional[str] = None,
    fit_days: Optional[int] = None,
) -> Dict[str, Any]:
    """The audit first, then - as a separate block - the personalized fit.

    The fit never changes the audit's answer; it only reuses which platform
    and period the audit was about. ``fit_days`` is how many days the fit
    compares: one day for "Didn't get your incentive?" (the default) and
    five for the 30-day audit. The audit itself is the same in both flows.
    """
    from incentive_fit import INCENTIVE_CHECK_FIT_DAYS, personalized_fit

    audit = run_incentive_audit(evidence, store, platform, period_date, incentive_type)
    chosen: Optional[PeriodEvaluation] = audit.pop("_chosen", None)
    if chosen is not None:
        audited = chosen.platform
    else:
        counts: Dict[str, int] = defaultdict(int)
        for trip in evidence.trips:
            counts[trip.platform] += 1
        audited = platform or (max(counts, key=counts.get) if counts else (evidence.platforms or [""])[0])
    period = (chosen.period_start, chosen.period_end) if chosen is not None else (
        (period_date, period_date) if period_date else None)
    days = fit_days or INCENTIVE_CHECK_FIT_DAYS
    fit = personalized_fit(evidence, store, audited, period, days) if audited else None
    return {"incentive_audit": audit, "incentive_fit": fit}
