"""Retrieval tests against the LIVE Qdrant incentive knowledge base.

These query the populated ``incentive_rules`` collection on the FairPay Qdrant
cluster (QDRANT_URL / QDRANT_API_KEY) by platform, geography, incentive type
and period, and check that exactly the right rule versions come back. They are
skipped when the cluster cannot be reached.

Run after ``python incentive_kb_ingest.py``:

    python -m pytest test_incentive_kb_live.py -q
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")
load_dotenv(BACKEND.parent / ".env")

from incentive_rules import (  # noqa: E402
    ESTABLISHED,
    NOT_DOCUMENTED,
    RULE_AMBIGUOUS,
    IncentiveRuleStore,
    RuleQuery,
    RuleStoreUnavailable,
    resolve_program,
    resolve_rule_for_period,
)

MUMBAI = "Andheri West, Mumbai"  # Bobby's zone
BENGALURU = "Bengaluru"
NYE = date(2025, 12, 31)


@pytest.fixture(scope="module")
def kb() -> IncentiveRuleStore:
    store = IncentiveRuleStore()
    try:
        store.platforms()
    except RuleStoreUnavailable as exc:
        pytest.skip(f"Qdrant cluster unreachable: {exc}")
    return store


def fetch(kb, platform, start, end=None, region=MUMBAI, incentive_type=None):
    rules = kb.retrieve(RuleQuery(platform=platform, period_start=start, period_end=end or start,
                                  region=region, incentive_type=incentive_type))
    return sorted({f"{r.program_id}@{r.version}" for r in rules}), rules


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

def test_all_four_platforms_are_in_the_knowledge_base(kb):
    assert set(kb.platforms()) == {"Swiggy", "Zomato", "Blinkit", "UberEats"}


def test_every_current_rule_is_sourced_and_tiered(kb):
    """No current document may be unsourced, untiered or weaker than tier 2."""
    seen = 0
    for platform in ("Swiggy", "Zomato", "Blinkit", "UberEats"):
        for region in (MUMBAI, BENGALURU, "Delhi NCR", "United States"):
            for day in (NYE, date(2021, 6, 1), date(2023, 6, 1), date(2026, 10, 5)):
                for rule in fetch(kb, platform, day, region=region)[1]:
                    seen += 1
                    assert rule.source_url and rule.source_reference
                    assert rule.source_tier in (1, 2), (rule.rule_id, rule.source_tier)
                    assert rule.confidence_label in ("HIGH", "MEDIUM", "LOW")
                    assert rule.evidence_status in ("VERIFIED", "OBSERVED", "SUPERSEDED", "CONTRADICTED", "UNKNOWN")
                    assert rule.evidence_class in ("real_world_evidence", "demonstration_dataset")
                    if rule.evidence_class == "real_world_evidence":
                        assert rule.evidence, rule.rule_id
    assert seen > 0


# ---------------------------------------------------------------------------
# Swiggy
# ---------------------------------------------------------------------------

def test_swiggy_researched_and_official_rules(kb):
    found, rules = fetch(kb, "Swiggy", date(2026, 10, 5))
    assert found == [
        "contract-swiggy-daily_target@SWG-RULES-2.4",
        "contract-swiggy-weekly_target@SWG-RULES-2.4",
        "swiggy-daily-incentive-fulltime@reported-2021-09-15",
        "swiggy-daily-incentive-parttime@reported-2021-09-15",
        "swiggy-in-daily-incentive@play-listing-2026-10-01",
        "swiggy-in-joining-bonus@play-listing-2026-10-01",
        "swiggy-in-peak-and-surge@play-listing-2026-10-01",
        "swiggy-in-referral-bonus@play-listing-2026-10-01",
    ]
    # The merged peak/surge document replaced the two that shared one quote.
    assert not any("peak-time-bonus" in f or "surge-earnings" in f for f in found)
    # The official daily incentive confirms the programme but no terms.
    official = next(r for r in rules if r.program_id == "swiggy-in-daily-incentive")
    assert official.source_tier == 1 and official.evidence_status == "VERIFIED"
    assert official.field_status["target_orders"] == NOT_DOCUMENTED
    assert official.inferences and official.inferences[0].confidence_label == "LOW"
    # The researched daily incentives carry a rupee target, login hours and a rating.
    parttime = next(r for r in rules if r.program_id == "swiggy-daily-incentive-parttime")
    assert (parttime.target_payout_amount, parttime.reward_amount) == (Decimal("300"), Decimal("100"))
    assert parttime.min_login_minutes == 360 and parttime.min_rating == Decimal("4.6")
    assert parttime.earning_basis == "payout_amount" and parttime.source_tier == 2


def test_swiggy_bengaluru_offer_is_scoped_to_its_city_and_day(kb):
    found, rules = fetch(kb, "Swiggy", NYE, region=BENGALURU, incentive_type="period_target")
    assert found == ["swiggy-campaign-double-earnings-blr@reported-2025-12-31"]
    offer = rules[0]
    assert (offer.target_orders, offer.reward_amount) == (19, Decimal("2175"))
    assert offer.max_rejections_in_period == 1 and offer.time_windows == ["18:00-00:00"]
    assert offer.city == "Bengaluru" and offer.confidence_label == "HIGH"
    # Not served to another city, nor to another day.
    assert fetch(kb, "Swiggy", NYE, region=MUMBAI, incentive_type="period_target")[0] == []
    assert fetch(kb, "Swiggy", date(2026, 1, 15), region=BENGALURU, incentive_type="period_target")[0] == []


def test_swiggy_official_rule_is_not_applied_before_its_source_date(kb):
    before, _ = fetch(kb, "Swiggy", date(2026, 9, 15))
    assert "swiggy-in-daily-incentive@play-listing-2026-10-01" not in before


# ---------------------------------------------------------------------------
# Zomato
# ---------------------------------------------------------------------------

def test_zomato_contract_versions_resolve_by_date(kb):
    early, _ = fetch(kb, "Zomato", date(2026, 9, 1), incentive_type="daily_target")
    assert early == ["contract-zomato-daily_target@ZOM-RULES-3.1"]
    late, _ = fetch(kb, "Zomato", date(2026, 9, 10), incentive_type="daily_target")
    assert late == ["contract-zomato-daily_target@ZOM-RULES-3.2"]
    # A week spanning the change: both versions, identical terms, one rule.
    week, rules = fetch(kb, "Zomato", date(2026, 9, 7), date(2026, 9, 13), incentive_type="weekly_target")
    assert week == ["contract-zomato-weekly_target@ZOM-RULES-3.1", "contract-zomato-weekly_target@ZOM-RULES-3.2"]
    resolution = resolve_program(rules, date(2026, 9, 7), date(2026, 9, 13))
    assert resolution.status == ESTABLISHED and "identical terms" in resolution.note


def test_zomato_contradicting_nye_reports_are_both_stored(kb):
    found, rules = fetch(kb, "Zomato", NYE, region=BENGALURU, incentive_type="per_order_bonus")
    assert "zomato-campaign-nye-peak-blr@reported-2025-12-31" in found
    assert "zomato-campaign-nye-peak-in@reported-2025-12-31" in found
    pair = [r for r in rules if r.program_id.startswith("zomato-campaign-nye")]
    assert {r.evidence_status for r in pair} == {"CONTRADICTED"}
    assert all(r.contradicted_by for r in pair)
    blr = next(r for r in pair if r.city == "Bengaluru")
    assert blr.per_order_amount == Decimal("90")
    assert blr.min_login_minutes_in_window == 210 and blr.login_window == "18:00-00:00"
    national = next(r for r in pair if r.region == "India")
    assert national.per_order_amount is None and "120" in (national.reward_text or "")
    # Asking for the whole programme space on that day cannot pick one.
    res = resolve_rule_for_period(kb, RuleQuery(platform="Zomato", period_start=NYE, period_end=NYE,
                                                region=BENGALURU, incentive_type="per_order_bonus"))
    assert res.status == RULE_AMBIGUOUS


def test_zomato_minimum_guarantee_is_delhi_scoped(kb):
    found, rules = fetch(kb, "Zomato", date(2021, 6, 1), region="Delhi", incentive_type="minimum_guarantee")
    assert found == ["zomato-minimum-guarantee-delhi@reported-2020-12-17"]
    mg = rules[0]
    assert mg.min_login_minutes == 3600 and mg.earning_basis == "login_time"
    assert mg.target_orders is None and "4,000" in (mg.reward_text or "")
    assert mg.city == "Delhi" and "Saket" in (mg.zone or "")
    # Not served to a rider in Mumbai.
    assert fetch(kb, "Zomato", date(2021, 6, 1), region=MUMBAI, incentive_type="minimum_guarantee")[0] == []


# ---------------------------------------------------------------------------
# Blinkit
# ---------------------------------------------------------------------------

def test_blinkit_payout_versions_do_not_overlap(kb):
    for day, amount, status in ((date(2021, 6, 1), "50", "SUPERSEDED"),
                                (date(2022, 6, 1), "25", "SUPERSEDED"),
                                (date(2023, 6, 1), "15", "OBSERVED")):
        res = resolve_rule_for_period(kb, RuleQuery(platform="Blinkit", period_start=day, period_end=day,
                                                    region="Delhi NCR", program_id="blinkit-ncr-per-order-payout"))
        assert res.status == ESTABLISHED, (day, res.note)
        assert res.rule.per_order_amount == Decimal(amount), (day, res.rule.rule_id)
        assert res.rule.evidence_status == status
    # The NCR structures are not served to a rider outside NCR.
    found, _ = fetch(kb, "Blinkit", date(2023, 6, 1), region=MUMBAI)
    assert not any("ncr-per-order-payout" in f for f in found)


def test_blinkit_official_rules(kb):
    found, rules = fetch(kb, "Blinkit", date(2026, 10, 5))
    assert found == [
        "blinkit-in-daily-incentive@play-listing-2026-08-11",
        "blinkit-in-joining-bonus@play-listing-2026-08-11",
        "blinkit-in-referral-bonus@play-listing-2026-08-11",
        "blinkit-in-surge-pay@play-listing-2026-08-11",
    ]
    assert all(r.region == "India" and r.source_tier == 1 for r in rules)
    daily = next(r for r in rules if r.program_id == "blinkit-in-daily-incentive")
    assert daily.field_status["target_orders"] == NOT_DOCUMENTED and daily.inferences


# ---------------------------------------------------------------------------
# Uber Eats
# ---------------------------------------------------------------------------

def test_uber_quest_is_us_only_and_versioned(kb):
    # India: Uber Eats left in 2020, so only the demonstration contract matches.
    india, _ = fetch(kb, "Uber Eats", date(2026, 9, 10))
    assert india == ["contract-ubereats-daily_target@UBE-RULES-1.8",
                     "contract-ubereats-weekly_target@UBE-RULES-1.8"]
    assert not any("uber-us-quest" in f for f in india)
    us_now, rules = fetch(kb, "UberEats", date(2026, 6, 1), region="United States", incentive_type="period_target")
    assert us_now == ["uber-us-quest-trips@delivery-quests-faq-2026-05-12"]
    assert rules[0].evidence_status == "VERIFIED"
    us_then, older = fetch(kb, "UberEats", date(2025, 1, 1), region="United States", incentive_type="period_target")
    assert us_then == ["uber-us-quest-trips@how-quest-works-2022-11-04"]
    assert older[0].evidence_status == "SUPERSEDED"
    earnings, eq = fetch(kb, "UberEats", date(2026, 6, 1), region="United States", incentive_type="earnings_target")
    assert earnings == ["uber-us-quest-earnings@quest-goals-2024-06-28"]
    assert eq[0].opt_in_required is True and eq[0].earning_basis == "payout_amount"


# ---------------------------------------------------------------------------
# Demonstration data is always distinguishable
# ---------------------------------------------------------------------------

def test_demonstration_rules_are_marked(kb):
    _, rules = fetch(kb, "Swiggy", date(2026, 10, 5))
    demo = [r for r in rules if r.source_type == "contract_on_file"]
    assert demo and all(r.evidence_class == "demonstration_dataset" for r in demo)
    assert all("demonstration dataset" in " ".join(r.notes) for r in demo)
    real = [r for r in rules if r.source_type != "contract_on_file"]
    assert real and all(r.evidence_class == "real_world_evidence" for r in real)


def test_withdrawn_and_superseded_documents_are_never_retrieved(kb):
    """Earlier revisions and withdrawn documents stay stored but are never served.

    A rule_id is shared by a document and its later revisions, so the check is
    that each programme version comes back exactly once, and that programmes
    withdrawn from the record (the two Swiggy documents merged into one) and
    the pre-schema documents never appear.
    """
    withdrawn_programs = {"swiggy-in-surge-earnings", "swiggy-in-peak-time-bonus"}
    for platform in ("Swiggy", "Zomato", "Blinkit", "UberEats"):
        for region in (MUMBAI, BENGALURU, "Delhi NCR", "United States"):
            for day in (NYE, date(2021, 6, 1), date(2023, 6, 1), date(2026, 10, 5)):
                rules = fetch(kb, platform, day, region=region)[1]
                pairs = [(r.program_id, r.version) for r in rules]
                assert len(pairs) == len(set(pairs)), f"a version came back twice: {pairs}"
                assert not (set(r.program_id for r in rules) & withdrawn_programs)
                # Pre-schema documents carry no program_id at all.
                assert all(r.program_id for r in rules)


# ---------------------------------------------------------------------------
# The target bonuses live in two places; they must say the same thing
# ---------------------------------------------------------------------------

def test_contract_target_bonuses_match_between_the_database_and_qdrant(kb):
    """The daily/weekly target bonuses exist both in `platform_rules` (used by
    the full audit) and in Qdrant (used by the incentive check). Changing one
    and forgetting the other would make the two halves of FairPay disagree,
    with nothing to show it. This fails as soon as they drift apart.

    Re-run `python incentive_kb_ingest.py` after changing a contract rule.
    """
    import incentive_kb_ingest
    from incentive_rules import RuleQuery

    try:
        from_database = incentive_kb_ingest.load_contract_rules()
    except Exception as exc:  # pragma: no cover - environment without the DB
        pytest.skip(f"contract rules unavailable: {exc}")
    assert from_database, "no target bonuses on file to compare"

    missing, different = [], []
    for rule in from_database:
        served = kb.retrieve(RuleQuery(platform=rule.platform, period_start=rule.effective_from,
                                       period_end=rule.effective_from, region=MUMBAI,
                                       incentive_type=rule.incentive_type))
        match = next((r for r in served if r.program_id == rule.program_id and r.version == rule.version), None)
        if match is None:
            missing.append(f"{rule.program_id}@{rule.version}")
            continue
        for field in ("target_orders", "reward_amount", "period_scope", "effective_from", "effective_to"):
            on_file, in_qdrant = getattr(rule, field), getattr(match, field)
            if on_file != in_qdrant:
                different.append(f"{rule.program_id}@{rule.version} {field}: database {on_file} vs Qdrant {in_qdrant}")

    assert not missing, "target bonuses on file that Qdrant does not serve: " + ", ".join(missing)
    assert not different, "target bonuses that disagree: " + "; ".join(different)
