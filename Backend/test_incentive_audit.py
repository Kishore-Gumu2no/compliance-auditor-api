"""Deterministic tests for the incentive audit and the personalized incentive fit.

All evidence here is SYNTHETIC (built in this file). Rules are always read
from Qdrant - an in-memory Qdrant instance with a deterministic embedder - so
the same code path as production is exercised, with no network, database or
LLM. Real-data validation against Bobby's records and the populated cloud
knowledge base is in validate_incentive_audit.py.

Run:  python -m pytest test_incentive_audit.py -q
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from incentive_audit import build_incentive_report, run_incentive_audit  # noqa: E402
from incentive_engine import (  # noqa: E402
    AMBIGUOUS,
    COUNTING_MISMATCH,
    ELIGIBILITY_FAILURE,
    INCENTIVE_RECEIVED,
    INSUFFICIENT_EVIDENCE,
    PAYMENT_NOT_OBSERVED,
    POSSIBLE_PAYMENT_FAILURE,
    count_orders,
)
from incentive_evidence import (  # noqa: E402
    ALL_APPS,
    CoverageRange,
    IncentiveEvidence,
    IncentivePaymentObservation,
    MetricObservation,
    PlatformCountObservation,
    TripObservation,
    evidence_from_extraction,
)
from incentive_fit import (  # noqa: E402
    CANNOT_COMPARE,
    FIT_FOUND,
    INSUFFICIENT_DATA,
    FULL_AUDIT_FIT_DAYS,
    INCENTIVE_CHECK_FIT_DAYS,
    NO_CLEAR_DIFFERENCE,
    assert_no_earnings_claim,
    personalized_fit,
)
from incentive_rules import (  # noqa: E402
    ESTABLISHED,
    KB_UNAVAILABLE_TEXT,
    NOT_DOCUMENTED,
    NOT_ESTABLISHED,
    RULE_AMBIGUOUS,
    RULE_NOT_ESTABLISHED_TEXT,
    CountingRules,
    IncentiveRule,
    IncentiveRuleStore,
    RuleQuery,
    region_scopes,
    resolve_rule_for_period,
)

DAY = date(2026, 9, 10)  # a Thursday
BACKEND = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Synthetic builders
# ---------------------------------------------------------------------------

class HashEmbedder:
    """Deterministic 384-d vectors so Qdrant runs without a model download."""

    def embed(self, texts):
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            yield [((digest[i % 32] + i) % 97) / 97.0 + 0.01 for i in range(384)]


def qdrant_store(*rules: IncentiveRule) -> IncentiveRuleStore:
    """A real Qdrant (in memory) holding exactly these rules."""
    from qdrant_client import QdrantClient

    s = IncentiveRuleStore(client=QdrantClient(":memory:"), embedder=HashEmbedder())
    s.ingest(list(rules))
    return s


store = qdrant_store


def rule(**kw) -> IncentiveRule:
    base = dict(
        rule_id=kw.get("rule_id", "SYN-SWG-DAILY-1"), program_id="syn-swiggy-daily", version="SYN-1",
        platform="Swiggy", incentive_name="Daily target bonus", incentive_type="daily_target", period_scope="day",
        effective_from=date(2026, 9, 1), effective_to=None, target_orders=10, reward_amount=Decimal("100"),
        source_type="synthetic", source_reference="synthetic rule",
    )
    base.update(kw)
    if "rule_id" in kw and "program_id" not in kw:
        base["program_id"] = f"syn-{kw['rule_id']}"
    return IncentiveRule(**base)


def trip(oid: str, day: date = DAY, hh: int = 12, mm: int = 0, status: str = "delivered", platform: str = "Swiggy",
         batch: str | None = None, kind: str | None = None, completed: datetime | None = None,
         distance: str | None = None) -> TripObservation:
    accepted = datetime(day.year, day.month, day.day, hh, mm)
    return TripObservation(
        order_id=oid, platform=platform, accepted_at=accepted,
        completed_at=completed or accepted + timedelta(minutes=25), status=status, batch_id=batch,
        batch_kind=kind, distance_km=Decimal(distance) if distance else None, source="synthetic",
    )


def trips(n: int, day: date = DAY, platform: str = "Swiggy", prefix: str = "O") -> list[TripObservation]:
    return [trip(f"{prefix}{day:%m%d}-{i:02d}", day, 10 + i % 10, (i * 7) % 60, platform=platform) for i in range(n)]


def evidence(trip_list, platform="Swiggy", complete=(DAY, DAY), payments=(), metrics=(), counts=(),
             pay_coverage=None, conflicts=(), offers=(), login_coverage=None, region=None) -> IncentiveEvidence:
    coverage = [CoverageRange(platform=platform, start=complete[0], end=complete[1], basis="synthetic")] if complete else []
    if pay_coverage is None:
        pay_coverage = (complete[0], complete[1] + timedelta(days=7)) if complete else None
    pcov = [CoverageRange(platform=platform, start=pay_coverage[0], end=pay_coverage[1], basis="synthetic")] if pay_coverage else []
    lcov = [CoverageRange(platform=ALL_APPS, start=login_coverage[0], end=login_coverage[1], basis="synthetic")] if login_coverage else []
    return IncentiveEvidence(
        partner_id="synthetic-rider", source="synthetic", region=region, platforms=[platform], trips=list(trip_list),
        payments=list(payments), metrics=list(metrics), platform_counts=list(counts), trip_coverage=coverage,
        payment_coverage=pcov, conflicts=list(conflicts), printed_offers=list(offers), login_coverage=lcov,
    )


def paid(amount="100", day=DAY, platform="Swiggy", label="Daily target bonus") -> IncentivePaymentObservation:
    return IncentivePaymentObservation(platform=platform, amount=Decimal(amount), paid_on=day, label=label,
                                       source="synthetic statement")


def metric(name, value, start=DAY, end=DAY, platform="Swiggy", start_time=None, end_time=None) -> MetricObservation:
    return MetricObservation(platform=platform, metric=name, value=Decimal(str(value)), period_start=start,
                             period_end=end, start_time=start_time, end_time=end_time, source="synthetic screen")


def progress(counted, start=DAY, end=DAY, platform="Swiggy", target=None, reward=None) -> PlatformCountObservation:
    return PlatformCountObservation(platform=platform, period_start=start, period_end=end, counted_orders=counted,
                                    target_orders=target, reward_amount=Decimal(reward) if reward else None,
                                    source="synthetic progress screen")


def audit(ev, rules_store, **kw):
    result = run_incentive_audit(ev, rules_store, period_date=kw.pop("period_date", DAY), **kw)
    result.pop("_chosen", None)
    return result


def primary(ev, rules_store, **kw):
    return audit(ev, rules_store, **kw)["primary"]


def statuses(p, bucket):
    return [c["condition"] for c in p["conditions"][bucket]]


FORBIDDEN = re.compile(r"\b(fraud|illegal|underpaid|underpayment|withh[eo]ld|cheat|stole|theft)\w*", re.I)


def all_text(obj) -> str:
    if isinstance(obj, dict):
        return " ".join(all_text(v) for v in obj.values())
    if isinstance(obj, list):
        return " ".join(all_text(v) for v in obj)
    return str(obj) if isinstance(obj, str) else ""


# ---------------------------------------------------------------------------
# 1-3. Target reached / missed / near miss
# ---------------------------------------------------------------------------

def test_01_target_reached_and_incentive_received():
    p = primary(evidence(trips(10), payments=[paid()]), store(rule()))
    assert p["status"] == INCENTIVE_RECEIVED
    assert p["counting"]["min_count"] == 10
    assert "order_target" in statuses(p, "passed")
    assert p["rule_lookup"] == "qdrant"


def test_02_target_missed():
    p = primary(evidence(trips(6)), store(rule()))
    assert p["status"] == ELIGIBILITY_FAILURE
    assert "order_target" in statuses(p, "failed")
    assert p["shortfall"] == 4 and p["near_miss"] is False


def test_03_one_order_near_miss():
    p = primary(evidence(trips(9)), store(rule()))
    assert p["status"] == ELIGIBILITY_FAILURE
    assert p["near_miss"] is True and p["shortfall"] == 1
    assert "1 order short" in p["headline"]


# ---------------------------------------------------------------------------
# 4-7. Counting rules
# ---------------------------------------------------------------------------

def _batched():
    return trips(8) + [trip("B1", hh=19, batch="T1", kind="multi_order"), trip("B2", hh=19, mm=1, batch="T1", kind="multi_order")]


def test_04_multi_order_counting():
    each = count_orders(_batched(), rule(counting=CountingRules(multi_order="each_order")), DAY, DAY)
    once = count_orders(_batched(), rule(counting=CountingRules(multi_order="once_per_trip")), DAY, DAY)
    undoc = count_orders(_batched(), rule(), DAY, DAY)
    assert (each.min_count, each.max_count) == (10, 10)
    assert (once.min_count, once.max_count) == (9, 9)
    assert (undoc.min_count, undoc.max_count) == (9, 10) and [o.order_id for o in undoc.uncertain] == ["B2"]
    p = primary(evidence(_batched()), store(rule()))
    assert p["status"] == INSUFFICIENT_EVIDENCE and "order_target" in statuses(p, "unknown")


def test_05_multi_pickup_counting():
    pick = trips(9) + [trip("P1", hh=20, batch="T9", kind="multi_pickup"), trip("P2", hh=20, mm=2, batch="T9", kind="multi_pickup")]
    c = count_orders(pick, rule(counting=CountingRules(multi_pickup="once_per_trip")), DAY, DAY)
    assert (c.min_count, c.max_count) == (10, 10) and any(o.order_id == "P2" for o in c.excluded)
    c2 = count_orders(pick, rule(counting=CountingRules(multi_order="each_order")), DAY, DAY)
    assert (c2.min_count, c2.max_count) == (10, 11)


def test_06_incomplete_order_exclusion():
    with_incomplete = trips(9) + [trip("X1", hh=21, status="incomplete")]
    documented = count_orders(with_incomplete, rule(counting=CountingRules(incomplete="excluded")), DAY, DAY)
    assert (documented.min_count, documented.max_count) == (9, 9)
    assert documented.excluded[0].provenance == "RETRIEVED_RULE"
    p = primary(evidence(with_incomplete), store(rule(counting=CountingRules(incomplete="excluded"))))
    assert p["status"] == ELIGIBILITY_FAILURE and p["near_miss"]
    undocumented = count_orders(with_incomplete, rule(), DAY, DAY)
    assert (undocumented.min_count, undocumented.max_count) == (9, 10)


def test_07_midnight_boundary():
    late = trip("M1", hh=23, mm=50, completed=datetime(2026, 9, 11, 0, 15))
    day_trips = trips(9) + [late]
    assert count_orders(day_trips, rule(counting=CountingRules(day_boundary="accepted_at")), DAY, DAY).min_count == 10
    assert count_orders(day_trips, rule(counting=CountingRules(day_boundary="completed_at")), DAY, DAY).max_count == 9
    undoc = count_orders(day_trips, rule(), DAY, DAY)
    assert (undoc.min_count, undoc.max_count) == (9, 10) and "midnight" in undoc.open_questions[0]


# ---------------------------------------------------------------------------
# 8. Wrong incentive period
# ---------------------------------------------------------------------------

def test_08_wrong_incentive_period():
    p = primary(evidence(trips(10), payments=[paid()]), store(rule()), period_date=date(2026, 9, 20))
    assert p["status"] == INSUFFICIENT_EVIDENCE and "not from" in p["headline"]
    mixed = trips(6) + trips(5, day=DAY + timedelta(days=1), prefix="N")
    c = count_orders(mixed, rule(), DAY, DAY)
    assert (c.min_count, c.max_count, c.outside_period) == (6, 6, 5)


# ---------------------------------------------------------------------------
# 9-12. Conditions
# ---------------------------------------------------------------------------

STRICT = dict(min_acceptance_rate=Decimal("0.80"), min_login_minutes=240)


def test_09_acceptance_rate_failure():
    ev = evidence(trips(10), metrics=[metric("acceptance_rate", "0.74"), metric("login_minutes", 300)])
    p = primary(ev, store(rule(**STRICT)))
    assert p["status"] == ELIGIBILITY_FAILURE and statuses(p, "failed") == ["acceptance_rate"]
    assert "You reached the 10-order target" in p["headline"]


def test_10_login_time_failure():
    ev = evidence(trips(10), metrics=[metric("acceptance_rate", "0.90"), metric("login_minutes", 180)])
    p = primary(ev, store(rule(**STRICT)))
    assert p["status"] == ELIGIBILITY_FAILURE and statuses(p, "failed") == ["login_minutes"]


def test_11_multiple_eligibility_failures():
    ev = evidence(trips(7), metrics=[metric("acceptance_rate", "0.60"), metric("login_minutes", 100)])
    p = primary(ev, store(rule(**STRICT)))
    assert set(statuses(p, "failed")) == {"order_target", "acceptance_rate", "login_minutes"}


def test_12_unknown_condition_stays_unknown():
    p = primary(evidence(trips(10), metrics=[metric("login_minutes", 300)]), store(rule(**STRICT)))
    assert p["status"] == INSUFFICIENT_EVIDENCE and statuses(p, "unknown") == ["acceptance_rate"]
    assert "cannot determine whether you qualified" in p["headline"]
    other = evidence(trips(10), metrics=[metric("acceptance_rate", "0.95", DAY - timedelta(days=7), DAY - timedelta(days=1)),
                                         metric("login_minutes", 300)])
    assert statuses(primary(other, store(rule(**STRICT))), "unknown") == ["acceptance_rate"]


# ---------------------------------------------------------------------------
# 13-17. Conflicts, rules, mismatch, payment, insufficient evidence
# ---------------------------------------------------------------------------

def test_13_conflicting_evidence_is_ambiguous():
    assert primary(evidence(trips(10), counts=[progress(10), progress(8)]), store(rule()))["status"] == AMBIGUOUS
    assert primary(evidence(trips(10), offers=[progress(10, target=12, reward="150")]), store(rule()))["status"] == AMBIGUOUS


def test_14_missing_incentive_rule():
    p = primary(evidence(trips(10)), store(rule(platform="Zomato")))
    assert p["status"] == INSUFFICIENT_EVIDENCE and p["headline"] == RULE_NOT_ESTABLISHED_TEXT


def test_15_counting_mismatch_identifies_orders():
    batched = trips(16) + [trip("B1", hh=21, batch="T1", kind="multi_order"), trip("B2", hh=21, mm=1, batch="T1", kind="multi_order"),
                           trip("B3", hh=21, mm=30, batch="T2", kind="multi_order"), trip("B4", hh=21, mm=31, batch="T2", kind="multi_order")]
    p = primary(evidence(batched, counts=[progress(18)]), store(rule(target_orders=20)))
    assert p["status"] == COUNTING_MISMATCH
    m = p["counting_mismatch"]
    assert (m["platform_count"], m["fairpay_max"], m["difference"], m["orders"]) == (18, 20, 2, ["B2", "B4"])


def test_16_possible_payment_failure_uses_documented_payout_wait():
    metrics = [metric("acceptance_rate", "0.9"), metric("login_minutes", 300)]
    p = primary(evidence(trips(10), metrics=metrics), store(rule(**STRICT, payout_window_days=7)))
    assert p["status"] == POSSIBLE_PAYMENT_FAILURE
    no_records = evidence(trips(10), metrics=metrics, pay_coverage=False)
    assert primary(no_records, store(rule(**STRICT, payout_window_days=7)))["status"] == PAYMENT_NOT_OBSERVED
    # Payment wait not documented in the knowledge base: FairPay assumes no waiting time.
    p2 = primary(evidence(trips(10), metrics=metrics), store(rule(**STRICT)))
    assert p2["status"] == PAYMENT_NOT_OBSERVED and "does not document how soon" in p2["payment"]["detail"]


def test_17_insufficient_evidence_without_complete_trip_list():
    p = primary(evidence(trips(6), complete=None), store(rule()))
    assert p["status"] == INSUFFICIENT_EVIDENCE and "order_target" in statuses(p, "unknown")


# ---------------------------------------------------------------------------
# 18-20. Personalized incentive fit: one day, reward weighed
# ---------------------------------------------------------------------------

def _fit_store():
    return store(
        rule(rule_id="A", platform="Swiggy", target_orders=8, reward_amount=Decimal("90")),
        rule(rule_id="B", platform="Zomato", target_orders=7, reward_amount=Decimal("120")),
        rule(rule_id="C", platform="UberEats", target_orders=4, reward_amount=Decimal("40"), min_acceptance_rate=Decimal("0.9")),
        rule(rule_id="D", platform="Blinkit", target_orders=None, reward_amount=None, conditions_complete=False),
    )


def test_18_fit_single_day_weighs_reward():
    fit = personalized_fit(evidence(trips(8)), _fit_store(), "Swiggy", (DAY, DAY))
    assert fit["status"] == FIT_FOUND and fit["comparison_day"] == DAY.isoformat()
    # 8 orders reach all three published targets; Zomato's documented reward is highest.
    assert fit["recommended_platform"] == "Zomato"
    assert "highest among the targets your day reaches" in fit["headline"]
    assert [p["platform"] for p in fit["platforms"]] == ["Zomato", "Swiggy", "UberEats"]
    assert any("Blinkit" in n and "not published" in n for n in fit["not_compared"])
    uber = next(p for p in fit["platforms"] if p["platform"] == "UberEats")
    assert uber["conditions_not_checked"]
    # With 5 orders only UberEats' target is reached, whatever the rewards.
    fit5 = personalized_fit(evidence(trips(5)), _fit_store(), "Swiggy", (DAY, DAY))
    assert fit5["recommended_platform"] == "UberEats" and "would not be reached" in fit5["headline"]
    # With 3 orders nothing is reached: the closest target wins, reward breaks ties.
    fit3 = personalized_fit(evidence(trips(3)), _fit_store(), "Swiggy", (DAY, DAY))
    assert fit3["recommended_platform"] == "UberEats" and fit3["headline"].startswith("None of the documented")
    assert fit["disclaimer"].startswith("This does not mean Zomato will pay you more.")


def test_19_fit_with_insufficient_data():
    only_one = store(rule(rule_id="A", platform="Swiggy"), rule(rule_id="D", platform="Blinkit", target_orders=None,
                                                                reward_amount=None))
    fit = personalized_fit(evidence(trips(8)), only_one, "Swiggy", (DAY, DAY))
    assert fit["status"] == INSUFFICIENT_DATA and fit["headline"] == CANNOT_COMPARE
    assert personalized_fit(evidence([]), _fit_store(), "Swiggy")["status"] == INSUFFICIENT_DATA
    tie = store(rule(rule_id="A", platform="Swiggy", target_orders=5, reward_amount=Decimal("50")),
                rule(rule_id="B", platform="Zomato", target_orders=6, reward_amount=Decimal("50")))
    assert personalized_fit(evidence(trips(8)), tie, "Swiggy", (DAY, DAY))["status"] == NO_CLEAR_DIFFERENCE


# ---------------------------------------------------------------------------
# 21-24. Two flows, two windows: 30-day audit -> 5 days, incentive check -> 1 day
# ---------------------------------------------------------------------------

WEEK = [DAY - timedelta(days=i) for i in range(6, -1, -1)]  # DAY-6 .. DAY


def _week_evidence(counts):
    """One synthetic Swiggy day per count, ending on DAY; every day has a complete trip list."""
    days = WEEK[-len(counts):]
    all_trips = [t for d, n in zip(days, counts) for t in trips(n, d)]
    return evidence(all_trips, complete=(days[0], DAY))


# Two early days with many orders (outside the five), then five days ending on DAY.
WEEK_COUNTS = [20, 20, 8, 8, 8, 8, 4]


def test_21_flow_windows_are_fixed_and_separate():
    assert INCENTIVE_CHECK_FIT_DAYS == 1
    assert FULL_AUDIT_FIT_DAYS == 5


def test_22_full_audit_fit_compares_five_eligible_days():
    fit = personalized_fit(_week_evidence(WEEK_COUNTS), _fit_store(), "Swiggy", (DAY, DAY), FULL_AUDIT_FIT_DAYS)
    five = [d.isoformat() for d in WEEK[-5:]]
    assert fit["comparison_unit"] == "days" and fit["comparison_days"] == five
    assert fit["rider_pattern"]["periods_observed"] == 5
    assert list(fit["rider_pattern"]["orders_per_day"].values()) == [8, 8, 8, 8, 4]  # the 20-order days are left out
    by = {p["platform"]: p for p in fit["platforms"]}
    assert all(p["periods_compared"] == 5 for p in by.values())
    # Zomato (7 orders, Rs 120) is reached on 4 days, Swiggy (8, Rs 90) on 4, UberEats (4, Rs 40) on 5.
    assert (by["Zomato"]["days_reached"], by["Swiggy"]["days_reached"], by["UberEats"]["days_reached"]) == (4, 4, 5)
    assert by["Zomato"]["documented_reward_total"] == "480"
    # Reward weighed across the five days: Zomato first.
    assert fit["status"] == FIT_FOUND and fit["recommended_platform"] == "Zomato"
    assert [p["platform"] for p in fit["platforms"]] == ["Zomato", "Swiggy", "UberEats"]
    assert "4 of 5 days" in fit["headline"]
    assert_no_earnings_claim([fit["headline"], *fit["why"], *fit["assumptions"]])
    # The same evidence through the single-day check looks at DAY only (4 orders): only UberEats is reached.
    one = personalized_fit(_week_evidence(WEEK_COUNTS), _fit_store(), "Swiggy", (DAY, DAY))
    assert one["comparison_unit"] == "day" and one["comparison_day"] == DAY.isoformat()
    assert one["rider_pattern"]["periods_observed"] == 1 and one["recommended_platform"] == "UberEats"


def test_23_full_audit_fit_uses_only_days_with_a_complete_trip_list():
    ev = _week_evidence(WEEK_COUNTS)
    ev = ev.model_copy(update={"trip_coverage": [c.model_copy(update={"start": DAY - timedelta(days=2)})
                                                 for c in ev.trip_coverage]})
    fit = personalized_fit(ev, _fit_store(), "Swiggy", (DAY, DAY), FULL_AUDIT_FIT_DAYS)
    assert fit["comparison_days"] == [d.isoformat() for d in WEEK[-3:]]
    assert any("compared 3 instead of 5" in a for a in fit["assumptions"])
    # Five days up to the end of the audited period, not after it.
    earlier = personalized_fit(_week_evidence(WEEK_COUNTS), _fit_store(), "Swiggy",
                               (DAY - timedelta(days=2), DAY - timedelta(days=2)), FULL_AUDIT_FIT_DAYS)
    assert earlier["comparison_days"][-1] == (DAY - timedelta(days=2)).isoformat()
    assert len(earlier["comparison_days"]) == 5


def test_24_report_window_follows_the_flow_and_the_audit_is_unchanged():
    ev = _week_evidence(WEEK_COUNTS)
    single = build_incentive_report(ev, _fit_store(), period_date=DAY)  # Telegram default
    full = build_incentive_report(ev, _fit_store(), period_date=DAY, fit_days=FULL_AUDIT_FIT_DAYS)
    assert single["incentive_fit"]["comparison_unit"] == "day"
    assert full["incentive_fit"]["comparison_unit"] == "days" and len(full["incentive_fit"]["comparison_days"]) == 5
    # The "Why didn't I get my incentive?" answer is the same in both flows.
    assert single["incentive_audit"] == full["incentive_audit"]


def test_25_api_uses_five_days_for_history_and_one_for_telegram(monkeypatch):
    _load_backend_env()
    try:
        import api
    except Exception as exc:  # pragma: no cover - environment without the DB
        pytest.skip(f"backend modules unavailable: {exc}")
    import asyncio

    ev = _week_evidence(WEEK_COUNTS)
    kb = _fit_store()
    monkeypatch.setattr(api, "_incentive_evidence_for", lambda req: ev)
    monkeypatch.setattr(api, "default_store", lambda: kb)
    history = asyncio.run(api.audit_incentive(api.IncentiveAuditRequest(partner_id="synthetic-rider")))
    telegram = asyncio.run(api.audit_incentive(api.IncentiveAuditRequest(
        partner_id="synthetic-rider", source="telegram", submission_id="s1")))
    assert history["incentive_fit"]["comparison_unit"] == "days"
    assert len(history["incentive_fit"]["comparison_days"]) == FULL_AUDIT_FIT_DAYS
    assert telegram["incentive_fit"]["comparison_unit"] == "day"
    assert telegram["incentive_fit"]["rider_pattern"]["periods_observed"] == INCENTIVE_CHECK_FIT_DAYS


def test_20_no_guaranteed_higher_earnings_claim():
    for n in (3, 5, 8):
        fit = personalized_fit(evidence(trips(n)), _fit_store(), "Swiggy", (DAY, DAY))
        statements = [fit["headline"], *fit["why"], *fit["assumptions"]]
        assert_no_earnings_claim(statements)
        text = " ".join(statements).lower()
        for phrase in ("earn more", "will earn", "more money", "higher earnings", "better pay", "pay you more", "guarantee"):
            assert phrase not in text
    with pytest.raises(ValueError):
        assert_no_earnings_claim(["You will earn more on Zomato."])


# ---------------------------------------------------------------------------
# Knowledge base: Qdrant only, versioning, regions, ingestion
# ---------------------------------------------------------------------------

def test_rules_come_from_qdrant_only_no_fallback():
    class Broken:
        def query_points(self, *a, **k):
            raise ConnectionError("cluster unreachable")

        def scroll(self, *a, **k):
            raise ConnectionError("cluster unreachable")

    broken = IncentiveRuleStore(client=Broken(), embedder=HashEmbedder())
    res = resolve_rule_for_period(broken, RuleQuery(platform="Swiggy", period_start=DAY, period_end=DAY))
    assert res.status == NOT_ESTABLISHED and res.retrieval_backend == "unavailable" and KB_UNAVAILABLE_TEXT in res.note
    result = audit(evidence(trips(10)), broken)
    assert result["primary"]["status"] == INSUFFICIENT_EVIDENCE
    assert any(KB_UNAVAILABLE_TEXT in x for x in result["limitations"])
    import incentive_rules
    assert not hasattr(incentive_rules, "load_rules_on_file")  # no local rule source at audit time


def test_rule_versioning_never_applies_current_rule_historically():
    old = rule(rule_id="V1", program_id="p", version="V1", effective_from=date(2026, 8, 1), effective_to=date(2026, 9, 5), target_orders=8)
    new = rule(rule_id="V2", program_id="p", version="V2", effective_from=date(2026, 9, 6), target_orders=12)
    s = store(old, new)
    q = lambda d: RuleQuery(platform="Swiggy", period_start=d, period_end=d)  # noqa: E731
    assert resolve_rule_for_period(s, q(date(2026, 9, 2))).rule.version == "V1"
    assert resolve_rule_for_period(s, q(DAY)).rule.version == "V2"
    assert resolve_rule_for_period(s, q(date(2026, 7, 1))).status == NOT_ESTABLISHED


def test_rule_change_inside_a_weekly_period():
    w1 = rule(rule_id="W1", program_id="w", version="W1", incentive_type="weekly_target", period_scope="week_mon_sun",
              effective_from=date(2026, 8, 1), effective_to=date(2026, 9, 8), target_orders=25)
    w2_same = w1.model_copy(update={"rule_id": "W2", "version": "W2", "effective_from": date(2026, 9, 9), "effective_to": None})
    w2_diff = w2_same.model_copy(update={"target_orders": 30})
    q = RuleQuery(platform="Swiggy", period_start=date(2026, 9, 7), period_end=date(2026, 9, 13), incentive_type="weekly_target")
    same = resolve_rule_for_period(store(w1, w2_same), q)
    assert same.status == ESTABLISHED and "identical terms" in same.note
    assert resolve_rule_for_period(store(w1, w2_diff), q).status == RULE_AMBIGUOUS


def test_region_selection_city_state_country():
    assert {"mumbai", "maharashtra", "india", "all"} <= set(region_scopes("Andheri West, Mumbai"))
    india = rule(rule_id="IN", region="India", region_level="country", target_orders=10)
    hyd = rule(rule_id="HYD", region="Hyderabad", region_level="city", target_orders=14)
    us = rule(rule_id="US", region="United States", region_level="country", target_orders=20)
    s = store(india, hyd, us)
    found = s.retrieve(RuleQuery(platform="Swiggy", period_start=DAY, period_end=DAY, region="Andheri West, Mumbai"))
    assert [r.rule_id for r in found] == ["IN"]
    found_hyd = s.retrieve(RuleQuery(platform="Swiggy", period_start=DAY, period_end=DAY, region="Hyderabad"))
    assert sorted(r.rule_id for r in found_hyd) == ["HYD", "IN"]


def test_ingestion_dedupes_and_versions_without_overwriting():
    s = store(rule())
    assert s.ingest([rule()]) == {"added": [], "unchanged": ["SYN-SWG-DAILY-1"], "revised": []}
    changed = rule(target_orders=12)
    report = s.ingest([changed])
    assert report["revised"] == ["SYN-SWG-DAILY-1"]
    points, _ = s.client().scroll(s.collection, limit=10, with_payload=True)
    by_status = sorted((p.payload["record_status"], p.payload["revision"], p.payload["target_orders"]) for p in points)
    assert by_status == [("current", 2, 12), ("replaced_revision", 1, 10)]
    assert [r.target_orders for r in s.retrieve(RuleQuery(platform="Swiggy", period_start=DAY, period_end=DAY))] == [12]


def test_field_status_marks_not_documented():
    r = rule(target_orders=None, reward_amount=None).with_field_status()
    assert r.field_status["target_orders"] == NOT_DOCUMENTED
    assert r.field_status["min_login_minutes"] == NOT_DOCUMENTED
    assert r.field_status["counting.multi_order"] == NOT_DOCUMENTED


def test_research_record_is_sourced_and_tiered():
    """Every stored rule names a real source, quotes it, and is tiered honestly."""
    data = json.loads((BACKEND / "incentive_kb" / "research_2026-10-08.json").read_text(encoding="utf-8"))
    allowed = {
        1: ("play.google.com", "uber.com"),
        2: ("deccanherald.com", "upstox.com", "inc42.com", "theprint.in"),
    }
    quoted_fields = ("target_orders", "reward_amount", "per_order_amount", "min_login_minutes",
                     "min_login_minutes_in_window", "max_rejections_in_period", "min_rating",
                     "target_payout_amount", "min_distance_km", "min_weight_kg")
    platforms, tiers = set(), set()
    for item in data["rules"]:
        r = IncentiveRule.model_validate(item).with_field_status()
        platforms.add(r.platform)
        tiers.add(r.source_tier)
        assert r.source_tier in allowed, f"{r.rule_id} has tier {r.source_tier}"
        assert any(d in r.source_url for d in allowed[r.source_tier]), f"{r.rule_id}: {r.source_url}"
        assert r.evidence_class == "real_world_evidence"
        assert r.evidence and all(e.quote and e.source_url for e in r.evidence)
        assert r.source_date and r.retrieved_on and r.confidence <= 1
        assert r.confidence_label in ("HIGH", "MEDIUM", "LOW")
        assert r.evidence_status in ("VERIFIED", "OBSERVED", "SUPERSEDED", "CONTRADICTED", "UNKNOWN")
        # Every stored value is backed by a quote that names the field it supports.
        supported = {f.strip() for e in r.evidence for f in e.field.split(",")}
        for field in quoted_fields:
            if getattr(r, field) is not None:
                assert field in supported, f"{r.rule_id}: {field} has no evidence quote"
        # An inference is context only, and is always weak by construction.
        for inf in r.inferences:
            assert inf.confidence_label == "LOW" and inf.basis
    assert platforms == {"Swiggy", "Zomato", "Blinkit", "UberEats"}
    # Nothing weaker than reputable journalism is stored as a rule.
    assert tiers <= {1, 2}, tiers
    assert all("alphareach" not in item.get("source_url", "") for item in data["rules"])
    # Uber Quest evidence is never labelled India.
    for item in data["rules"]:
        if item["platform"] == "UberEats" and item["source_tier"] == 1:
            assert item["region"] == "United States", item["rule_id"]
    # The weak claims left out are recorded with the reason they were not used.
    assert any("alphareach" in u for n in data["not_ingested"] for u in n["found_at"])
    assert any("Uber Eats" in n["claim"] for n in data["not_ingested"])


def test_contract_rule_mapping_keeps_undocumented_fields_undocumented():
    from incentive_kb_ingest import contract_rule

    row = {"rule_id": "ZOM-3.2-R-DAILY", "platform": "Zomato", "rule_type": "BONUS_DAILY_TARGET",
           "rule_version": "ZOM-RULES-3.2", "contract_version": "ZOM-3.2", "clause_id": "ZOM-3.2-C06",
           "effective_from": date(2026, 9, 8), "effective_to": None, "status": "ACTIVE",
           "parameters": {"threshold_orders": 7, "amount": 80.0, "awarded_on": "threshold_crossing_order"},
           "description": "Daily order-count target bonus"}
    r = contract_rule(row, {"status": "ACTIVE", "confidence": "0.95", "document_title": "Zomato terms 3.2"})
    assert (r.target_orders, r.reward_amount, r.payout_window_days) == (7, Decimal("80.0"), 0)
    assert r.min_acceptance_rate is None and r.counting == CountingRules()
    assert contract_rule({**row, "status": "DRAFT"}, {}) is None


# ---------------------------------------------------------------------------
# Changes of 2026-10-08
# ---------------------------------------------------------------------------

def test_trips_sent_are_the_whole_day_without_a_printed_total():
    raw = {"trips": [{"order_id": f"T{i}", "date": "2026-09-10", "accepted_time": f"{12 + i}:00",
                      "status_label": "Delivered"} for i in range(6)], "work_time": [], "read_gaps": []}
    ev = evidence_from_extraction("rider", "Swiggy", raw)
    assert [(c.start, c.end) for c in ev.trip_coverage] == [(DAY, DAY)]
    p = primary(ev, store(rule()))
    assert p["status"] == ELIGIBILITY_FAILURE and p["shortfall"] == 4  # 6 sent = 6 done that day


def test_target_not_published_uses_the_offer_screen_or_asks_for_it():
    unpublished = rule(target_orders=None, reward_amount=None, conditions_complete=False, opt_in_required=True,
                       payout_timing="weekly payouts")
    with_offer = evidence(trips(9), counts=[progress(9, target=10, reward="150")],
                          offers=[progress(9, target=10, reward="150")])
    p = primary(with_offer, store(unpublished))
    assert p["incentive"]["target_source"] == "offer_in_evidence" and p["incentive"]["target_orders"] == 10
    assert p["status"] == ELIGIBILITY_FAILURE and p["near_miss"]
    assert "opt_in" in statuses(p, "passed")  # progress shown -> signed up (inferred)
    result = audit(evidence(trips(9)), store(unpublished))
    assert result["primary"]["status"] == INSUFFICIENT_EVIDENCE
    assert any("does not publish the order target" in x for x in result["limitations"])


def test_login_sessions_on_file_are_an_upper_bound_for_one_app():
    r = rule(min_login_minutes=300)
    sessions = [metric("login_minutes_all_apps", 120, platform=ALL_APPS, start_time="12:00", end_time="14:00"),
                metric("login_minutes_all_apps", 100, platform=ALL_APPS, start_time="19:00", end_time="20:40")]
    p = primary(evidence(trips(10), metrics=sessions, login_coverage=(DAY, DAY)), store(r))
    assert p["status"] == ELIGIBILITY_FAILURE and statuses(p, "failed") == ["login_minutes"]
    long_sessions = [metric("login_minutes_all_apps", 400, platform=ALL_APPS, start_time="10:00", end_time="16:40")]
    p2 = primary(evidence(trips(10), metrics=long_sessions, login_coverage=(DAY, DAY)), store(r))
    assert statuses(p2, "unknown") == ["login_minutes"]  # all-app time cannot be split by app


def test_required_login_window():
    r = rule(required_login_windows=["19:00-23:00"])
    gap = [metric("login_minutes_all_apps", 120, platform=ALL_APPS, start_time="19:00", end_time="21:00")]
    p = primary(evidence(trips(10), metrics=gap, login_coverage=(DAY, DAY)), store(r))
    assert statuses(p, "failed") == ["login_windows"]
    own = [metric("login_minutes", 260, start_time="18:30", end_time="23:10")]
    assert "login_windows" in statuses(primary(evidence(trips(10), metrics=own), store(r)), "passed")


def test_totals_comparison_feature_is_removed():
    import evidence_modes

    for name in ("reconcile_settlement", "settlement_limits", "SETTLEMENT_RECONCILIATION", "is_payout_label"):
        assert not hasattr(evidence_modes, name)
    assert evidence_modes.EVIDENCE_MODES == ("contract_spot_check", "full_audit")


def test_outputs_never_allege_wrongdoing():
    batched = trips(16) + [trip("B1", hh=21, batch="T1", kind="multi_order"), trip("B2", hh=21, mm=1, batch="T1", kind="multi_order")]
    scenarios = [
        (evidence(trips(10), payments=[paid()]), rule()),
        (evidence(trips(9)), rule()),
        (evidence(batched, counts=[progress(16)]), rule(target_orders=18)),
        (evidence(trips(10), metrics=[metric("acceptance_rate", "0.9"), metric("login_minutes", 300)]), rule(**STRICT, payout_window_days=7)),
        (evidence(trips(10), counts=[progress(10), progress(8)]), rule()),
    ]
    for ev, r in scenarios:
        report = build_incentive_report(ev, store(r, rule(rule_id="Z", platform="Zomato", target_orders=8)), period_date=DAY)
        assert not FORBIDDEN.search(all_text(report)), all_text(report)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def _load_backend_env():
    from dotenv import load_dotenv

    load_dotenv(BACKEND / ".env")
    load_dotenv(BACKEND.parent / ".env")


# ---------------------------------------------------------------------------
# Privacy: the trip details are deleted once the partner has been shown the result
# ---------------------------------------------------------------------------

def _shown_result():
    """A stored Telegram result, as it looks before the details are deleted."""
    return {
        "evidence_mode": "incentive_audit",
        "partner_id": "rider",
        "work_time": [{"date": "2026-10-08", "start": "18:00", "end": "23:59", "minutes": 359}],
        "transactions": [{"transaction_id": "T1", "gross_amount": "24.05"}],
        "reconciliation_predictions": [{"order_id": "O1", "anomaly_type": "missing_payment"}],
        "incentive_evidence": {"trips": [{"order_id": "8 Oct 18:04", "accepted_at": "2026-10-08T18:04:00",
                                          "earning_amount": "24.05"}], "region": "Bengaluru"},
        "spot_check": [{"platform": "Swiggy", "sample_orders_checked": 3, "orders_with_findings": 1,
                        "orders": [{"order_id": "O1", "status": "possible_issue"}]}],
        "incentive_audit": {
            "primary": {"status": ELIGIBILITY_FAILURE, "headline": "You were 2 orders short.",
                        "counting": {"min_count": 6, "max_count": 6},
                        "counting_mismatch": {"platform_count": 6, "fairpay_max": 8, "difference": 2,
                                              "orders": ["O7", "O8"]},
                        "conditions": {"passed": [], "failed": [], "unknown": []},
                        "relevant_transactions": [{"order_id": "O1", "when": "8 Oct 18:04", "counts": "yes"}]},
            "other_periods": [{"platform": "Swiggy", "status": INCENTIVE_RECEIVED,
                               "relevant_transactions": [{"order_id": "O2", "when": "7 Oct 19:10"}]}],
        },
        "incentive_fit": {"status": "FIT_FOUND", "recommended_platform": "Zomato",
                          "platforms": [{"platform": "Zomato", "target_orders": [7]}],
                          "rider_pattern": {"median_orders": 6, "busiest_hours": ["18:00", "19:00"],
                                            "orders_per_day": {"2026-10-08": 6}}},
    }


def test_trip_details_are_deleted_but_the_answer_is_kept():
    _load_backend_env()
    try:
        import telegram_store
    except Exception as exc:  # pragma: no cover - environment without the DB
        pytest.skip(f"backend modules unavailable: {exc}")

    before = _shown_result()
    after = telegram_store.purge_trip_details(before)

    # Nothing describing single orders, their times or their money survives.
    assert after["incentive_evidence"] is None
    assert after["transactions"] == [] and after["reconciliation_predictions"] == []
    assert after["work_time"] == []
    assert "relevant_transactions" not in after["incentive_audit"]["primary"]
    assert "relevant_transactions" not in after["incentive_audit"]["other_periods"][0]
    assert "orders" not in after["incentive_audit"]["primary"]["counting_mismatch"]
    assert "orders" not in after["spot_check"][0]
    assert "busiest_hours" not in after["incentive_fit"]["rider_pattern"]
    assert "orders_per_day" not in after["incentive_fit"]["rider_pattern"]
    text = json.dumps(after)
    for trace in ("8 Oct 18:04", "24.05", "18:00", "O7", "O8"):
        assert trace not in text, trace

    # The answer the website shows is untouched.
    primary = after["incentive_audit"]["primary"]
    assert primary["status"] == ELIGIBILITY_FAILURE and primary["headline"] == "You were 2 orders short."
    assert primary["counting"] == {"min_count": 6, "max_count": 6}
    assert primary["counting_mismatch"]["difference"] == 2
    assert after["incentive_fit"]["recommended_platform"] == "Zomato"
    assert after["incentive_fit"]["rider_pattern"]["median_orders"] == 6
    assert after["spot_check"][0]["orders_with_findings"] == 1
    assert after[telegram_store.PURGED_FLAG] is True

    # The original is left alone, and deleting twice changes nothing more.
    assert before["incentive_evidence"]["trips"], "purge_trip_details must not edit its input"
    assert telegram_store.purge_trip_details(after) == after


def test_only_whether_each_platform_was_audited_is_kept():
    _load_backend_env()
    try:
        import telegram_store
    except Exception as exc:  # pragma: no cover - environment without the DB
        pytest.skip(f"backend modules unavailable: {exc}")

    kept = telegram_store._purge_results_by_platform({
        "Swiggy": {"status": "complete", "platform": "Swiggy",
                   "incentive_evidence": {"trips": [{"order_id": "O1", "earning_amount": "24.05"}]}},
        "Zomato": {"status": "error", "platform": "Zomato", "error": "not read in full",
                   "message": "Please send them again.", "unread": [{"document": "image 1"}]},
    })
    assert kept["Swiggy"] == {"status": "complete", "platform": "Swiggy"}
    assert kept["Zomato"] == {"status": "error", "platform": "Zomato", "error": "not read in full",
                              "message": "Please send them again."}


def test_telegram_incentive_submission_never_runs_full_audit(monkeypatch):
    _load_backend_env()
    try:
        import telegram_audit
    except Exception as exc:  # pragma: no cover - environment without the DB
        pytest.skip(f"backend modules unavailable: {exc}")
    from types import SimpleNamespace

    import incentive_extraction
    import incentive_rules

    reading = {"trips": [{"order_id": f"T{i}", "date": "2026-09-10", "accepted_time": f"{12 + i % 8}:00",
                          "status_label": "Delivered"} for i in range(9)], "work_time": [], "read_gaps": []}
    monkeypatch.setattr(incentive_extraction, "extract_incentive_evidence", lambda pdfs, imgs, platform: reading)
    monkeypatch.setattr(telegram_audit, "load_partner_profile",
                        lambda pid: SimpleNamespace(name="Synthetic Rider", zone="Andheri West, Mumbai"))
    synthetic_kb = store(rule(), rule(rule_id="Z", platform="Zomato", target_orders=8, reward_amount=Decimal("70")))
    monkeypatch.setattr(incentive_rules, "default_store", lambda: synthetic_kb)
    monkeypatch.setattr(telegram_audit, "run_audit", lambda **kw: (_ for _ in ()).throw(AssertionError("full audit ran")))

    outcome = telegram_audit.run_submission_audit(
        "synthetic-rider", {"Swiggy": [{"kind": "image", "content": b"x"}]}, telegram_audit.INCENTIVE_AUDIT)
    merged = outcome["merged_result"]
    assert merged["incentive_audit"]["primary"]["status"] == ELIGIBILITY_FAILURE
    assert merged["incentive_audit"]["primary"]["near_miss"] is True
    assert merged["incentive_fit"]["comparison_day"] == "2026-09-10"
    # Every Telegram incentive check is checked for Bengaluru, whatever the profile says.
    assert merged["incentive_evidence"]["region"] == "Bengaluru"
    # "Didn't get your incentive?" compares the one day sent, never five.
    assert merged["incentive_fit"]["comparison_unit"] == "day"
    assert "comparison_days" not in merged["incentive_fit"]
    text = telegram_audit.summarize_for_partner(merged)
    assert "1 order short" in text and "Qdrant" not in text
    with pytest.raises(ValueError):
        telegram_audit.run_submission_audit("synthetic-rider", {"Swiggy": []}, "settlement_reconciliation")


# ---------------------------------------------------------------------------
# Changes of 2026-10-09: reading a real day of screenshots
# ---------------------------------------------------------------------------

# Five overlapping trip-history screenshots of one day (8 Oct 2026), read the
# way the reader returned them: no order ID is printed, so the store code
# "ES313" was copied as the order ID; four screens print only "8 Oct"; the
# 8:09pm card is cut off at the top of one screen; two trips include a tip.
SCREENS = [
    [("11:42pm", "24.96"), ("11:24pm", "21.54"), ("11:04pm", "28.69"), ("10:45pm", "60.62", "30"), ("10:32pm", "19.96")],
    [("10:32pm", "19.96"), ("10:03pm", "30.62"), ("9:39pm", "27.34"), ("9:28pm", "19.55"), ("9:01pm", "19.73")],
    [("9:01pm", "19.73"), ("8:41pm", "32.9", "10"), ("8:31pm", "16.28"), ("8:18pm", "18.42"), ("8:09pm", "16.38")],
    [("8:09pm", None), ("8:02pm", "15"), ("7:23pm", "28.5"), ("7:05pm", "21.05"), ("6:53pm", "15.24"), ("6:38pm", "19.35")],
    [("7:05pm", "21.05"), ("6:53pm", "15.24"), ("6:38pm", "19.35"), ("6:21pm", "21.61"), ("6:04pm", "24.05")],
]


def _screens_reading():
    rows = []
    for n, screen in enumerate(SCREENS, start=1):
        printed = "08 Oct, 2026" if n == 1 else "8 Oct"
        for t, amount, *tip in screen:
            rows.append({"order_id": "ES313", "date_text": printed, "date": None if n == 1 else "2023-10-08",
                         "accepted_time": t, "amount": amount, "tip_amount": tip[0] if tip else None,
                         "source": f"image {n}"})
    return {"trips": rows, "work_time": [], "read_gaps": []}


SWIGGY_2021 = dict(rule_id="SWG-2021-FT", program_id="swiggy-daily-fulltime", platform="Swiggy",
                   incentive_name="Swiggy daily incentive, full-time riders", target_orders=None,
                   target_payout_amount=Decimal("575"), reward_amount=Decimal("200"), source_tier=2,
                   effective_from=date(2021, 9, 15), source_date=date(2021, 9, 15),
                   source_reference="Inc42's report of 15 Sep 2021")
OCT8 = date(2026, 10, 8)


def test_screenshots_of_one_day_read_as_twenty_trips_without_tips():
    ev = evidence_from_extraction("rider", "Swiggy", _screens_reading(), region="Bengaluru")
    assert len(ev.trips) == 20
    assert {t.service_date for t in ev.trips} == {OCT8}  # the guessed 2023 is not used
    assert all(t.status == "delivered" for t in ev.trips)
    assert sum(t.earning_amount for t in ev.trips) == Decimal("441.79")  # ₹481.79 shown, minus ₹40 tips
    assert ev.conflicts == []
    assert any("'ES313'" in g and "not an order ID" in g for g in ev.read_gaps)
    cut_off = next(t for t in ev.trips if t.accepted_at == datetime(2026, 10, 8, 20, 9))
    assert cut_off.earning_amount == Decimal("16.38")  # the full copy of the 8:09pm card fills the cut one
    tipped = next(t for t in ev.trips if t.accepted_at == datetime(2026, 10, 8, 22, 45))
    assert tipped.earning_amount == Decimal("30.62") and "not counted as earnings" in tipped.notes[-1]


def test_screenshot_day_answer_says_what_was_earned():
    ev = evidence_from_extraction("rider", "Swiggy", _screens_reading(), region="Bengaluru")
    p = primary(ev, store(rule(**SWIGGY_2021)), period_date=OCT8)
    assert p["status"] == ELIGIBILITY_FAILURE
    assert p["headline"] == ("This incentive has no order target. The available evidence shows you earned "
                             "₹441.79 (required: ₹575 earned).")
    payout = next(c for c in p["conditions"]["failed"] if c["condition"] == "payout_target")
    assert payout["detail"].endswith("without customer tips.")
    assert "None" not in p["headline"]
    assert any("from September 2021, more than a year before" in n for n in p["notes"])


def test_no_year_printed_defaults_to_2026():
    raw = {"trips": [{"date_text": "8 Oct", "date": "2023-10-08", "accepted_time": "6:04pm", "amount": "24.05"}],
           "work_time": [], "read_gaps": []}
    ev = evidence_from_extraction("rider", "Swiggy", raw)
    assert ev.trips[0].service_date == date(2026, 10, 8)
    assert any("No year is printed for 8 Oct" in g for g in ev.read_gaps)


def test_same_time_different_amounts_are_two_trips_and_conflicts_are_listed_once():
    raw = {"trips": [{"date_text": "8 Oct", "accepted_time": "6:04pm", "amount": "24.05"},
                     {"date_text": "8 Oct", "accepted_time": "6:04pm", "amount": "18.00"},
                     {"date_text": "8 Oct", "accepted_time": "6:04pm", "amount": "24.05"}],
           "work_time": [], "read_gaps": []}
    assert len(evidence_from_extraction("rider", "Swiggy", raw).trips) == 2
    twice = {"trips": [{"order_id": "A1", "date": "2026-09-10", "accepted_time": "12:00", "status_label": "Delivered"},
                       {"order_id": "A1", "date": "2026-09-10", "accepted_time": "12:00", "status_label": "Cancelled"},
                       {"order_id": "A1", "date": "2026-09-10", "accepted_time": "12:00", "status_label": "Cancelled"}],
             "work_time": [], "read_gaps": []}
    ev = evidence_from_extraction("rider", "Swiggy", twice)
    assert len(ev.trips) == 1 and len(ev.conflicts) == 1


def test_fit_for_a_telegram_check_uses_real_rules_only():
    raw = {"trips": [{"order_id": f"T{i}", "date": "2026-09-10", "accepted_time": f"{10 + i}:00",
                      "status_label": "Delivered"} for i in range(8)], "work_time": [], "read_gaps": []}
    ev = evidence_from_extraction("rider", "Swiggy", raw)
    demo = store(rule(rule_id="A", platform="Swiggy", evidence_class="demonstration_dataset"),
                 rule(rule_id="B", platform="Zomato", target_orders=7, evidence_class="demonstration_dataset"))
    fit = personalized_fit(ev, demo, "Swiggy", (DAY, DAY))
    assert fit["status"] == INSUFFICIENT_DATA and fit["platforms"] == []
    real = store(rule(rule_id="A", platform="Swiggy", target_orders=8),
                 rule(rule_id="B", platform="Zomato", target_orders=7, reward_amount=Decimal("120")))
    assert personalized_fit(ev, real, "Swiggy", (DAY, DAY))["recommended_platform"] == "Zomato"


def test_all_conditions_met_message_only_when_every_condition_passed():
    from incentive_audit import ALL_CONDITIONS_MET_TEXT

    met = [metric("acceptance_rate", "0.9"), metric("login_minutes", 300)]
    scenarios = [
        (evidence(trips(10), metrics=met), rule(**STRICT, payout_window_days=7)),          # met, not paid
        (evidence(trips(10), metrics=met, pay_coverage=False), rule(**STRICT)),            # met, no records
        (evidence(trips(10), payments=[paid()]), rule()),                                  # paid
        (evidence(trips(9)), rule()),                                                      # short
        (evidence(trips(10), metrics=[metric("acceptance_rate", "0.9")]), rule(**STRICT)),  # login unknown
        (evidence(trips(10), counts=[progress(10), progress(8)]), rule()),                 # conflict
    ]
    for ev, r in scenarios:
        p = primary(ev, store(r))
        all_met = p["status"] in (POSSIBLE_PAYMENT_FAILURE, PAYMENT_NOT_OBSERVED)
        assert (p["headline"] == ALL_CONDITIONS_MET_TEXT) is all_met, (p["status"], p["headline"])
        if all_met:
            assert not p["conditions"]["failed"] and not p["conditions"]["unknown"]
    assert [primary(ev, store(r))["status"] for ev, r in scenarios[:2]] == [POSSIBLE_PAYMENT_FAILURE,
                                                                             PAYMENT_NOT_OBSERVED]


# ---------------------------------------------------------------------------
# Reading reliability (2026-10-09): wait on limits, check completeness, never judge half a day
# ---------------------------------------------------------------------------

def _limit_error(wait_text: str) -> Exception:
    inner = RuntimeError("Error code: 429 - {'error': {'message': 'Rate limit reached for model on tokens per "
                         f"minute. Please try again in {wait_text}.', 'code': 'rate_limit_exceeded'}}}}")
    outer = RuntimeError("Max retries exceeded.")
    outer.__cause__ = inner
    return outer


def test_rate_limit_wait_is_read_from_the_error():
    import incentive_extraction as ix

    assert ix._limit_wait(_limit_error("9m25.056s")) == pytest.approx(565.056)
    assert ix._limit_wait(_limit_error("12.5s")) == pytest.approx(12.5)
    assert ix._limit_wait(RuntimeError("Invalid JSON")) is None


def test_short_limit_is_waited_out_and_long_limit_reported(monkeypatch):
    import incentive_extraction as ix

    slept = []
    monkeypatch.setattr(ix.time, "sleep", slept.append)
    calls = iter([_limit_error("12s"), "read"])

    def call():
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    assert ix._call_patiently(call, "failed") == "read" and slept == [13]

    def daily():
        raise _limit_error("9m25s")

    with pytest.raises(ix.ReaderBusy) as busy:
        ix._call_patiently(daily, "failed")
    assert busy.value.wait_minutes == 10
    with pytest.raises(ix.EvidenceExtractionError):
        ix._call_patiently(lambda: (_ for _ in ()).throw(RuntimeError("bad output")), "failed")


def test_model_over_capacity_is_retried_then_reported_as_busy(monkeypatch):
    import incentive_extraction as ix

    slept = []
    monkeypatch.setattr(ix.time, "sleep", slept.append)
    over = RuntimeError("Error code: 503 - {'error': {'message': 'qwen/qwen3.8-27b is currently over capacity. "
                        "Please try again and back off exponentially.', 'type': 'internal_server_error'}}")
    calls = iter([over, over, "read"])

    def call():
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    assert ix._call_patiently(call, "failed") == "read" and slept == [6, 16]

    def always_over():
        raise over

    with pytest.raises(ix.ReaderBusy) as busy:  # not "unreadable": the rider is not asked to resend
        ix._call_patiently(always_over, "failed")
    assert busy.value.wait_minutes == 1


class _FakeReader:
    """Stands in for a reading service: either fails the way Groq does, or answers."""

    def __init__(self, error=None, answer=None):
        self.calls = 0
        self.error, self.answer = error, answer
        reader = self

        class _Completions:
            def create(self, **kwargs):
                reader.calls += 1
                reader.last_model = kwargs.get("model")
                if reader.error is not None:
                    raise reader.error
                return reader.answer

        self.chat = type("Chat", (), {"completions": _Completions()})()


def _daily_limit_error() -> Exception:
    inner = RuntimeError("Error code: 429 - {'error': {'message': 'Rate limit reached for model `qwen/qwen3.8-27b` "
                         "on tokens per day (TPD): Limit 200000, Used 199000. Please try again in 9m25s.', "
                         "'code': 'rate_limit_exceeded'}}")
    outer = RuntimeError("Max retries exceeded.")
    outer.__cause__ = inner
    return outer


def test_reader_switches_to_the_fallback_once_groq_is_out_for_the_day(monkeypatch):
    import incentive_extraction as ix
    import reader_fallback

    groq = _FakeReader(error=_daily_limit_error())
    nvidia = _FakeReader(answer=_reading(["6:04pm"]))
    monkeypatch.setattr(ix, "client", groq)
    monkeypatch.setattr(reader_fallback, "_fallback_reader", lambda: nvidia)
    monkeypatch.setattr(ix.time, "sleep", lambda seconds: None)
    told = []

    def three_documents(pdfs, imgs, platform):
        return {"reads": [ix._read_text(f"page {i}", platform) for i in range(3)]}

    monkeypatch.setattr(ix, "_extract_incentive_evidence", three_documents)
    with ix.reader_switch_listener(told.append):
        out = ix.extract_incentive_evidence([], [], "Swiggy")

    assert len(out["reads"]) == 3 and all(r.trips for r in out["reads"])
    assert groq.calls == 1  # once Groq is out for the day it is not asked again
    assert nvidia.calls == 3 and nvidia.last_model == ix.FALLBACK_VISION_MODEL
    assert told == [ix.FALLBACK_NOTICE]  # the partner is told once, before the slow reads
    assert told[0].startswith("THIS WILL TAKE MORE THAN A FEW MINUTES")


def test_no_switch_for_a_passing_busy_moment_or_without_a_key(monkeypatch):
    import incentive_extraction as ix
    import reader_fallback

    monkeypatch.setattr(ix.time, "sleep", lambda seconds: None)
    nvidia = _FakeReader(answer=_reading(["6:04pm"]))

    # A per-minute limit that never clears: reported as busy, no switch.
    minute = RuntimeError("Error code: 429 - Rate limit reached on tokens per minute (TPM). Please try again in 12s.")
    monkeypatch.setattr(ix, "client", _FakeReader(error=minute))
    monkeypatch.setattr(reader_fallback, "_fallback_reader", lambda: nvidia)
    with pytest.raises(ix.ReaderBusy) as busy:
        ix._read_text("page", "Swiggy")
    assert busy.value.daily is False and nvidia.calls == 0

    # Out for the day but no NVIDIA key: as before, the partner is asked to try later.
    monkeypatch.setattr(ix, "client", _FakeReader(error=_daily_limit_error()))
    monkeypatch.setattr(reader_fallback, "_fallback_reader", lambda: None)
    with pytest.raises(ix.ReaderBusy) as later:
        ix._read_text("page", "Swiggy")
    assert later.value.daily is True


def test_the_telegram_check_passes_the_notice_to_the_partner(monkeypatch):
    _load_backend_env()
    try:
        import telegram_audit
    except Exception as exc:  # pragma: no cover - environment without the DB
        pytest.skip(f"backend modules unavailable: {exc}")
    from types import SimpleNamespace

    import incentive_extraction
    import incentive_rules

    reading = {"trips": [{"order_id": f"T{i}", "date": "2026-09-10", "accepted_time": f"{12 + i % 8}:00",
                          "status_label": "Delivered"} for i in range(9)], "work_time": [], "read_gaps": []}

    def switching_extract(pdfs, imgs, platform):
        incentive_extraction._SWITCH_LISTENER.get()(incentive_extraction.FALLBACK_NOTICE)
        return reading

    monkeypatch.setattr(incentive_extraction, "extract_incentive_evidence", switching_extract)
    monkeypatch.setattr(telegram_audit, "load_partner_profile", lambda pid: SimpleNamespace(name="Rider", zone="X"))
    monkeypatch.setattr(incentive_rules, "default_store", lambda: store(rule()))
    told = []
    telegram_audit.run_submission_audit("synthetic-rider", {"Swiggy": [{"kind": "image", "content": b"x"}]},
                                        telegram_audit.INCENTIVE_AUDIT, told.append)
    assert told == [incentive_extraction.FALLBACK_NOTICE]


def test_full_audit_reader_switches_too_and_tells_the_partner_once(monkeypatch):
    """The full audit's reads go through the same switch as the incentive check."""
    import evidence_extractor
    import reader_fallback

    groq = _FakeReader(error=_daily_limit_error())
    nvidia = _FakeReader(answer=evidence_extractor.DocumentExtraction())
    monkeypatch.setattr(evidence_extractor, "client", groq)
    monkeypatch.setattr(reader_fallback, "_fallback_reader", lambda: nvidia)
    monkeypatch.setattr(reader_fallback.time, "sleep", lambda seconds: None)
    told = []
    with reader_fallback.reader_switch_listener(told.append):
        evidence_extractor.extract_transactions("statement page 1", "Swiggy")
        evidence_extractor.extract_transactions("statement page 2", "Swiggy")
        # A later app in the same submission keeps the choice already made.
        with reader_fallback.reader_switch_listener(told.append):
            evidence_extractor.extract_transactions("other app", "Zomato")
    assert groq.calls == 1 and nvidia.calls == 3
    assert nvidia.last_model == reader_fallback.FALLBACK_VISION_MODEL
    assert told == [reader_fallback.FALLBACK_NOTICE]

    # A real failure (not a limit) still fails the read, exactly as before.
    monkeypatch.setattr(evidence_extractor, "client", _FakeReader(error=RuntimeError("Invalid JSON")))
    with pytest.raises(evidence_extractor.EvidenceExtractionError, match="Structured document extraction failed"):
        evidence_extractor.extract_transactions("page", "Swiggy")


def test_telegram_full_audit_tells_the_partner_once_across_apps(monkeypatch):
    _load_backend_env()
    try:
        import telegram_audit
    except Exception as exc:  # pragma: no cover - environment without the DB
        pytest.skip(f"backend modules unavailable: {exc}")
    from types import SimpleNamespace

    import evidence_extractor
    import reader_fallback

    groq = _FakeReader(error=_daily_limit_error())
    nvidia = _FakeReader(answer=evidence_extractor.DocumentExtraction())
    monkeypatch.setattr(evidence_extractor, "client", groq)
    monkeypatch.setattr(reader_fallback, "_fallback_reader", lambda: nvidia)
    monkeypatch.setattr(telegram_audit, "load_partner_profile", lambda pid: SimpleNamespace(name="Rider", zone="X"))

    def reading_audit(**kwargs):
        evidence_extractor.extract_transactions("statement", kwargs["platform"])
        raise RuntimeError("stop after reading")  # one app failing does not stop the others

    monkeypatch.setattr(telegram_audit, "run_audit", reading_audit)
    told = []
    files = {"Swiggy": [{"kind": "image", "content": b"x"}], "Zomato": [{"kind": "image", "content": b"y"}]}
    telegram_audit.run_submission_audit("synthetic-rider", files, telegram_audit.FULL_AUDIT, told.append)
    assert groq.calls == 1 and nvidia.calls == 2  # Groq is not asked again for the second app
    assert told == [reader_fallback.FALLBACK_NOTICE]


def _reading(times):
    import incentive_extraction as ix

    return ix.IncentiveDocumentExtraction(trips=[ix.IncentiveTrip(accepted_time=t, amount=Decimal("20")) for t in times])


def test_screenshot_is_read_again_until_every_printed_time_is_read(monkeypatch):
    import incentive_extraction as ix

    printed = {(18, 4), (18, 21), (18, 38), (18, 53), (19, 5)}
    monkeypatch.setattr(ix, "printed_times", lambda image: printed)
    reads = iter([_reading(["6:04pm", "6:21pm"]), _reading(["6:21pm", "6:38pm", "6:53pm", "7:05pm"])])
    monkeypatch.setattr(ix, "_read_image", lambda image, platform, seed=0: next(reads))
    extraction, missing = ix._read_image_completely(None, "Swiggy")
    assert missing == 0 and len(extraction.trips) == 5  # the 6:21pm trip is not added twice

    monkeypatch.setattr(ix, "_read_image", lambda image, platform, seed=0: _reading(["6:04pm", "6:21pm"]))
    extraction, missing = ix._read_image_completely(None, "Swiggy")
    assert missing == 3  # still short after three reads

    # A screen with no trips (an incentive screen) is not held to the printed times.
    monkeypatch.setattr(ix, "printed_times", lambda image: {(18, 0), (0, 0)})
    monkeypatch.setattr(ix, "_read_image", lambda image, platform, seed=0: ix.IncentiveDocumentExtraction())
    assert ix._read_image_completely(None, "Swiggy")[1] == 0


def test_one_printed_time_is_used_whichever_field_it_was_read_into():
    raw = {"trips": [{"date_text": "8 Oct", "completed_time": "8:09pm", "amount": "16.38"},
                     {"date_text": "8 Oct", "accepted_time": "8:09pm", "amount": "16.38"},
                     {"date_text": "8 Oct", "completed_time": "8:02pm", "amount": "15"}],
           "work_time": [], "read_gaps": []}
    ev = evidence_from_extraction("rider", "Swiggy", raw)
    assert len(ev.trips) == 2
    assert all(t.status == "delivered" for t in ev.trips)


def test_a_day_not_read_in_full_is_never_judged(monkeypatch):
    _load_backend_env()
    try:
        import telegram_audit
    except Exception as exc:  # pragma: no cover - environment without the DB
        pytest.skip(f"backend modules unavailable: {exc}")
    from types import SimpleNamespace

    import incentive_extraction

    monkeypatch.setattr(telegram_audit, "load_partner_profile", lambda pid: SimpleNamespace(name="Rider", zone="X"))
    monkeypatch.setattr(telegram_audit, "run_audit", lambda **kw: (_ for _ in ()).throw(AssertionError("full audit ran")))
    files = {"Uber Eats": [{"kind": "image", "content": b"x"}]}
    some_trips = [{"order_id": f"T{i}", "date": "2026-09-10", "accepted_time": "12:00", "status_label": "Delivered"}
                  for i in range(3)]

    incomplete = {"trips": some_trips, "work_time": [], "read_gaps": [],
                  "unread": [{"document": "image 3", "reason": "incomplete", "missing_times": 2}]}
    monkeypatch.setattr(incentive_extraction, "extract_incentive_evidence", lambda pdfs, imgs, platform: incomplete)
    with pytest.raises(telegram_audit.IncompleteReading) as stop:
        telegram_audit.run_submission_audit("synthetic-rider", files, telegram_audit.INCENTIVE_AUDIT)
    assert "screenshot 3" in stop.value.rider_message and "did not check this day" in stop.value.rider_message
    assert stop.value.keep_documents is False

    busy = {**incomplete, "unread": [{"document": "image 1", "reason": "busy", "wait_minutes": 10}]}
    monkeypatch.setattr(incentive_extraction, "extract_incentive_evidence", lambda pdfs, imgs, platform: busy)
    with pytest.raises(telegram_audit.IncompleteReading) as later:
        telegram_audit.run_submission_audit("synthetic-rider", files, telegram_audit.INCENTIVE_AUDIT)
    assert "about 10 minutes" in later.value.rider_message and later.value.keep_documents is True


def test_a_partner_can_be_forgotten(monkeypatch):
    """/unlink removes the chat-to-partner link and that chat's stored results."""
    _load_backend_env()
    try:
        import telegram_bot
        import telegram_store
    except Exception as exc:  # pragma: no cover - environment without the DB
        pytest.skip(f"backend modules unavailable: {exc}")

    assert any(c["command"] == "unlink" for c in telegram_bot.BOT_COMMANDS)
    assert "/unlink" in telegram_bot.GUIDE_TEXT  # partners are told it exists

    forgotten = {}
    monkeypatch.setattr(telegram_bot, "unlink_chat",
                        lambda chat_id: forgotten.setdefault(chat_id, {"links": 1, "submissions": 2}))
    sent = []

    class Bot:
        async def send(self, chat_id, text, keyboard=None):
            sent.append(text)

    session = telegram_bot._session(4242)
    session.partner_id = "rider"
    session.option = telegram_bot.INCENTIVE_AUDIT
    asyncio.run(telegram_bot.handle_command(Bot(), 4242, "/unlink"))

    assert forgotten == {4242: {"links": 1, "submissions": 2}}
    assert session.partner_id is None and session.option is None
    assert "forgotten" in sent[-1] and "2 past results" in sent[-1]

    # The real function removes the rows for that chat and nothing else.
    import inspect
    source = inspect.getsource(telegram_store.unlink_chat)
    assert "DELETE FROM telegram_submissions WHERE chat_id" in source
    assert "DELETE FROM telegram_links WHERE chat_id" in source


def test_telegram_bot_offers_exactly_two_options():
    _load_backend_env()
    try:
        import telegram_bot
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"backend modules unavailable: {exc}")
    assert [row[0]["text"] for row in telegram_bot.OPTION_KEYBOARD["keyboard"]] == [
        "Option 1: Didn't get your incentive?", "Option 2: Full Audit"]
    assert telegram_bot.OPTION_1_TEXT == ("Option 1: Didn't get your incentive? I can check why, just send me your single "
                                          "day's entire trip history, WITHOUT MISSING A SINGLE TRIP")
    assert telegram_bot.parse_option("1") == "incentive_audit" and telegram_bot.parse_option("Option 2: Full Audit") == "full_audit"
    assert telegram_bot.parse_option("A") is None  # the old spot-check option is gone
    assert len(telegram_bot.GUIDE_TEXT) <= 512
