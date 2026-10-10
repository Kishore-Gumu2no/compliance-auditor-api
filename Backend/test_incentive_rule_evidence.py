"""Tests for the researched rule shapes added on 2026-10-08.

Covers the evidence fields (confidence, source tier, evidence class, evidence
status, inferences) and the conditions the researched offers introduced:
a rejection cap, login time inside a window, orders inside a window, an
earnings target, a minimum guarantee, and windows that cross midnight.

Rules always come from Qdrant (in memory, with a deterministic embedder), so
the production retrieval path is exercised with no network, database or LLM.

Run:  python -m pytest test_incentive_rule_evidence.py -q
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

from incentive_audit import run_incentive_audit  # noqa: E402
from incentive_engine import (  # noqa: E402
    AMBIGUOUS,
    ELIGIBILITY_FAILURE,
    INSUFFICIENT_EVIDENCE,
    count_orders,
    time_in_window,
    window_bounds,
)
from incentive_evidence import ALL_APPS  # noqa: E402
from incentive_rules import FieldInference, IncentiveRule  # noqa: E402

# The synthetic builders already used by the main suite.
from test_incentive_audit import (  # noqa: E402
    DAY,
    evidence,
    metric,
    paid,
    rule,
    statuses,
    store,
    trip,
    trips,
)

NYE = date(2025, 12, 31)


def primary(ev, kb, **kw):
    result = run_incentive_audit(ev, kb, period_date=kw.pop("period_date", DAY), **kw)
    result.pop("_chosen", None)
    return result["primary"]


def full(ev, kb, **kw):
    result = run_incentive_audit(ev, kb, period_date=kw.pop("period_date", DAY), **kw)
    result.pop("_chosen", None)
    return result


# ---------------------------------------------------------------------------
# Windows that cross midnight
# ---------------------------------------------------------------------------

def test_window_bounds_and_membership():
    assert window_bounds("18:00-00:00") == (1080, 1440)
    assert window_bounds("12:00-15:00") == (720, 900)
    assert window_bounds("nonsense") is None
    from datetime import datetime
    inside = [datetime(2025, 12, 31, 18, 0), datetime(2025, 12, 31, 23, 59)]
    outside = [datetime(2025, 12, 31, 17, 59), datetime(2025, 12, 31, 9, 0)]
    assert all(time_in_window(t, "18:00-00:00") for t in inside)
    assert not any(time_in_window(t, "18:00-00:00") for t in outside)


def test_counting_inside_a_window_that_crosses_midnight():
    r = rule(rule_id="WIN", target_orders=2, time_windows=["18:00-00:00"], effective_from=NYE)
    in_window = [trip("A", NYE, 18, 30), trip("B", NYE, 23, 0)]
    out_window = [trip("C", NYE, 17, 59), trip("D", NYE, 9, 0)]
    result = count_orders(in_window + out_window, r, NYE, NYE)
    assert (result.min_count, result.max_count) == (2, 2)
    assert {o.order_id for o in result.excluded} == {"C", "D"}
    # An order accepted at 23:50 and delivered after midnight is not silently
    # counted: the rule does not say which day it belongs to.
    straddles = count_orders([trip("A", NYE, 18, 30), trip("LATE", NYE, 23, 50)], r, NYE, NYE)
    assert (straddles.min_count, straddles.max_count) == (1, 2)
    assert [o.order_id for o in straddles.uncertain] == ["LATE"]


# ---------------------------------------------------------------------------
# The researched Swiggy Bengaluru offer, end to end
# ---------------------------------------------------------------------------

def _double_earnings_rule() -> IncentiveRule:
    import incentive_kb_ingest

    found = [r for r in incentive_kb_ingest.load_research_rules()
             if r.program_id == "swiggy-campaign-double-earnings-blr"]
    assert len(found) == 1, "the researched Swiggy Bengaluru offer should be a single rule"
    return found[0]


def test_reported_offer_fields_match_the_research_record():
    offer = _double_earnings_rule()
    assert offer.target_orders == 19
    assert offer.reward_amount == Decimal("2175")
    assert offer.max_rejections_in_period == 1
    assert offer.time_windows == ["18:00-00:00"] and offer.login_window == "18:00-00:00"
    assert (offer.city, offer.region_level) == ("Bengaluru", "city")
    assert (offer.source_tier, offer.confidence_label, offer.evidence_status) == (2, "HIGH", "OBSERVED")
    assert offer.effective_from == NYE and offer.effective_to == NYE


def test_reported_offer_is_evaluated_end_to_end():
    offer = _double_earnings_rule()
    kb = store(offer)
    inside = [trip(f"W{i}", NYE, 18 + i % 5, (i * 7) % 60) for i in range(18)]
    outside = [trip("EARLY", NYE, 14, 0)]

    short = primary(evidence(inside + outside, complete=(NYE, NYE), region="Bengaluru"), kb, period_date=NYE)
    assert short["status"] == ELIGIBILITY_FAILURE and short["near_miss"] is True
    assert short["counting"]["min_count"] == 18 and short["incentive"]["target_orders"] == 19
    assert any("Deccan Herald" in t["source"] for t in short["evidence_trail"])
    assert any("does not publish these terms" in n for n in short["notes"])
    # The order outside 18:00-00:00 does not count towards the 19.
    assert any(t["order_id"] == "EARLY" and t["counts"] == "no" for t in short["relevant_transactions"])

    reached = primary(evidence(inside + [trip("W18", NYE, 21, 5)] + outside,
                               complete=(NYE, NYE), region="Bengaluru"), kb, period_date=NYE)
    assert "order_target" in statuses(reached, "passed")
    # The rejection cap cannot be confirmed from a trip list.
    assert "max_rejections" in statuses(reached, "unknown")


def test_offer_is_not_applied_outside_its_city_or_its_day():
    offer = _double_earnings_rule()
    kb = store(offer)
    inside = [trip(f"W{i}", NYE, 19, i) for i in range(19)]
    other_city = full(evidence(inside, complete=(NYE, NYE), region="Hyderabad"), kb, period_date=NYE)
    assert other_city["primary"]["status"] == INSUFFICIENT_EVIDENCE
    later = date(2026, 1, 15)
    other_day = full(evidence([trip(f"L{i}", later, 19, i) for i in range(19)],
                              complete=(later, later), region="Bengaluru"), kb, period_date=later)
    assert other_day["primary"]["status"] == INSUFFICIENT_EVIDENCE


# ---------------------------------------------------------------------------
# New conditions
# ---------------------------------------------------------------------------

def test_rejection_cap():
    r = rule(rule_id="REJ", target_orders=3, max_rejections_in_period=1, effective_from=NYE)
    delivered = [trip(f"D{i}", NYE, 19, i) for i in range(3)]
    over = delivered + [trip("R1", NYE, 20, 0, status="rejected"), trip("R2", NYE, 20, 5, status="rejected")]
    p = primary(evidence(over, complete=(NYE, NYE)), store(r), period_date=NYE)
    assert p["status"] == ELIGIBILITY_FAILURE and statuses(p, "failed") == ["max_rejections"]

    within = delivered + [trip("R1", NYE, 20, 0, status="rejected")]
    assert "max_rejections" in statuses(
        primary(evidence(within, complete=(NYE, NYE)), store(r), period_date=NYE), "passed")

    # No rejection in the records is not evidence that none happened.
    none_seen = primary(evidence(delivered, complete=(NYE, NYE)), store(r), period_date=NYE)
    assert "max_rejections" in statuses(none_seen, "unknown")
    assert none_seen["status"] == INSUFFICIENT_EVIDENCE


def test_login_minutes_inside_a_window():
    r = rule(rule_id="LW", target_orders=1, login_window="18:00-00:00", min_login_minutes_in_window=210,
             effective_from=NYE)
    one_order = [trip("A", NYE, 19)]

    short = [metric("login_minutes", 120, NYE, NYE, start_time="18:00", end_time="20:00")]
    assert statuses(primary(evidence(one_order, complete=(NYE, NYE), metrics=short), store(r), period_date=NYE),
                    "failed") == ["login_in_window"]

    enough = [metric("login_minutes", 300, NYE, NYE, start_time="18:00", end_time="23:00")]
    assert "login_in_window" in statuses(
        primary(evidence(one_order, complete=(NYE, NYE), metrics=enough), store(r), period_date=NYE), "passed")

    # Only the part of a session inside the window counts.
    before = [metric("login_minutes", 300, NYE, NYE, start_time="12:00", end_time="17:00")]
    assert statuses(primary(evidence(one_order, complete=(NYE, NYE), metrics=before), store(r), period_date=NYE),
                    "failed") == ["login_in_window"]

    # All-app sessions are an upper bound: too little is a FAIL, enough is UNKNOWN.
    thin_shared = [metric("login_minutes_all_apps", 60, NYE, NYE, platform=ALL_APPS, start_time="18:00", end_time="19:00")]
    assert statuses(primary(evidence(one_order, complete=(NYE, NYE), metrics=thin_shared, login_coverage=(NYE, NYE)),
                            store(r), period_date=NYE), "failed") == ["login_in_window"]
    big_shared = [metric("login_minutes_all_apps", 300, NYE, NYE, platform=ALL_APPS, start_time="18:00", end_time="23:00")]
    assert "login_in_window" in statuses(
        primary(evidence(one_order, complete=(NYE, NYE), metrics=big_shared, login_coverage=(NYE, NYE)),
                store(r), period_date=NYE), "unknown")


def test_minimum_orders_inside_a_window():
    r = rule(rule_id="OIW", target_orders=4, login_window="18:00-00:00", min_orders_in_window=3, effective_from=NYE)
    mixed = [trip("A", NYE, 19), trip("B", NYE, 20), trip("C", NYE, 12), trip("D", NYE, 13)]
    p = primary(evidence(mixed, complete=(NYE, NYE)), store(r), period_date=NYE)
    assert statuses(p, "failed") == ["orders_in_window"]  # only 2 of the 4 are in the window
    inside = [trip("A", NYE, 19), trip("B", NYE, 20), trip("C", NYE, 21), trip("D", NYE, 22)]
    assert "orders_in_window" in statuses(
        primary(evidence(inside, complete=(NYE, NYE)), store(r), period_date=NYE), "passed")


def test_earnings_target_instead_of_an_order_target():
    r = rule(rule_id="PAY", target_orders=None, reward_amount=Decimal("100"),
             target_payout_amount=Decimal("300"), earning_basis="payout_amount",
             min_login_minutes=360, min_rating=Decimal("4.6"))
    metrics = [metric("login_minutes", 400), metric("rating", "4.8")]
    earned = [trip(f"E{i}", DAY, 12 + i).model_copy(update={"earning_amount": Decimal("60")}) for i in range(6)]
    p = primary(evidence(earned, metrics=metrics), store(r))
    target_cond = next(c for c in p["conditions"]["passed"] if c["condition"] == "order_target")
    assert target_cond["required"] == "no order target"
    assert "payout_target" in statuses(p, "passed")  # 6 x 60 = 360 >= 300

    thin = [trip(f"E{i}", DAY, 12 + i).model_copy(update={"earning_amount": Decimal("40")}) for i in range(6)]
    short = primary(evidence(thin, metrics=metrics), store(r))
    assert statuses(short, "failed") == ["payout_target"] and short["status"] == ELIGIBILITY_FAILURE

    # Without per-order amounts the target stays unknown, never assumed.
    blank = primary(evidence([trip(f"E{i}", DAY, 12 + i) for i in range(6)], metrics=metrics), store(r))
    assert "payout_target" in statuses(blank, "unknown")


def test_minimum_guarantee_is_checked_on_login_time():
    week = date(2026, 9, 7)
    r = rule(rule_id="MG", program_id="mg", incentive_type="minimum_guarantee", period_scope="week_mon_sun",
             earning_basis="login_time", target_orders=None, reward_amount=None,
             reward_text="INR 4,000-5,000 a week", min_login_minutes=3600, effective_from=date(2026, 9, 1))
    days = [trip(f"T{i}", week + timedelta(days=i % 7), 12 + i % 8) for i in range(20)]
    thin = [metric("login_minutes", 300, week + timedelta(days=i), week + timedelta(days=i)) for i in range(7)]
    p = primary(evidence(days, complete=(week, week + timedelta(days=6)), metrics=thin), store(r), period_date=week)
    assert p["incentive"]["target_orders"] is None
    assert "order_target" in statuses(p, "passed")      # this rule sets no order target
    assert statuses(p, "failed") == ["login_minutes"]   # 35 hours against the 60 required
    assert p["status"] == ELIGIBILITY_FAILURE


def test_weight_requirement_is_never_assumed():
    r = rule(rule_id="WT", target_orders=2, min_weight_kg=Decimal("8"))
    p = primary(evidence(trips(4)), store(r))
    assert "min_weight" in statuses(p, "unknown")
    assert p["status"] == INSUFFICIENT_EVIDENCE


# ---------------------------------------------------------------------------
# Evidence quality is surfaced, never silently resolved
# ---------------------------------------------------------------------------

def test_inferences_never_become_assumptions():
    r = rule(rule_id="INF", target_orders=None, reward_amount=None, conditions_complete=False,
             inferences=[FieldInference(field="target_orders", inferred_behaviour="probably about 15 orders",
                                        confidence_label="LOW", basis="third-party blog")])
    result = full(evidence(trips(14)), store(r))
    assert result["primary"]["status"] == INSUFFICIENT_EVIDENCE
    assert "15" not in result["primary"]["headline"]
    assert any("does not publish the order target" in x for x in result["limitations"])


def test_demonstration_rules_are_labelled_as_such():
    demo = rule(rule_id="DEMO", evidence_class="demonstration_dataset", source_type="contract_on_file",
                source_reference="SWG-2.4 clause SWG-2.4-C05", target_orders=8, reward_amount=Decimal("90"),
                payout_window_days=0)
    p = primary(evidence(trips(8), payments=[paid(amount="90")]), store(demo))
    assert any("demonstration dataset" in n for n in p["notes"])
    assert any("demonstration data" in t["value"] for t in p["evidence_trail"])


def test_tier_and_confidence_reach_the_result():
    reported = rule(rule_id="REP", source_type="reported_offer", source_tier=2, confidence_label="MEDIUM",
                    evidence_status="OBSERVED", source_publisher="Deccan Herald",
                    source_title="Gig workers strike fizzles out in Bengaluru", source_date=date(2025, 12, 31),
                    target_orders=9)
    p = primary(evidence(trips(9)), store(reported))
    trail = next(t for t in p["evidence_trail"] if t["fact"] == "Where this incentive's terms come from")
    assert "MEDIUM confidence" in trail["value"] and "Deccan Herald" in trail["value"]
    assert "tier 2 evidence" in trail["source"] and "status OBSERVED" in trail["source"]


def test_contradicted_rules_are_not_silently_resolved():
    blr = rule(rule_id="Z-BLR", program_id="zomato-blr", platform="Zomato", region="Bengaluru",
               region_level="city", city="Bengaluru", incentive_type="per_order_bonus", target_orders=None,
               reward_amount=None, per_order_amount=Decimal("90"), login_window="18:00-00:00",
               min_login_minutes_in_window=210, effective_from=NYE, effective_to=NYE,
               evidence_status="CONTRADICTED")
    national = rule(rule_id="Z-IN", program_id="zomato-in", platform="Zomato", region="India",
                    region_level="country", incentive_type="per_order_bonus", target_orders=None,
                    reward_amount=None, reward_text="Rs 120-150 per order", login_window="18:00-00:00",
                    min_login_minutes_in_window=210, effective_from=NYE, effective_to=NYE,
                    evidence_status="CONTRADICTED")
    ev = evidence([trip("A", NYE, 19, platform="Zomato")], platform="Zomato", complete=(NYE, NYE),
                  region="Bengaluru")
    p = primary(ev, store(blr, national), period_date=NYE)
    assert p["status"] == AMBIGUOUS
    text = (p["headline"] + " " + " ".join(p.get("notes") or [])).lower()
    assert "sources disagree about the terms" in text
    assert "cannot be sure which applied" in text
    # Neither amount is presented as the one that applied.
    assert "90" not in p["headline"] and "120" not in p["headline"]


def test_superseded_version_is_not_preferred_over_the_current_one():
    """Blinkit's reported payout structures: the April 2023 one wins for 2023 dates."""
    import incentive_kb_ingest

    versions = [r for r in incentive_kb_ingest.load_research_rules()
                if r.program_id == "blinkit-ncr-per-order-payout"]
    assert len(versions) == 3
    from incentive_rules import ESTABLISHED, RuleQuery, resolve_rule_for_period

    kb = store(*versions)
    for day, expected in ((date(2023, 6, 1), "15"), (date(2022, 6, 1), "25"), (date(2021, 6, 1), "50")):
        res = resolve_rule_for_period(kb, RuleQuery(platform="Blinkit", period_start=day, period_end=day,
                                                    region="Delhi NCR"))
        assert res.status == ESTABLISHED, (day, res.note)
        assert res.rule.per_order_amount == Decimal(expected), (day, res.rule.rule_id)
    statuses_seen = {r.evidence_status for r in versions}
    assert statuses_seen == {"SUPERSEDED", "OBSERVED"}
