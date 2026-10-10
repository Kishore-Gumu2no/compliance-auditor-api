"""Laws live in Qdrant: read from there, chosen by date, changeable without code.

Every test uses a real Qdrant (in memory) - the same store code as
production - with a deterministic embedder, so no network or model download.

Run:  python -m pytest test_law_store.py -q
"""

from __future__ import annotations

import hashlib
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from findings import CONCEPT_EVIDENCE, _evaluate_law_relevance, _minimum_wage_from_laws, analyze_economic_concerns  # noqa: E402
from law_store import (  # noqa: E402
    NATIONWIDE,
    LawStore,
    LawStoreUnavailable,
    load_seed_laws,
    load_seed_zones,
    state_for_zone,
)
from models import BusinessCostCalculation, TransactionRecord  # noqa: E402
from versioning import laws_for_transaction  # noqa: E402

WAGES_S5 = "code_wages_2019_section_5"
WAGES_S18 = "code_wages_2019_section_18"


class HashEmbedder:
    def embed(self, texts):
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            yield [((digest[i % 32] + i) % 97) / 97.0 + 0.01 for i in range(384)]


@pytest.fixture()
def store() -> LawStore:
    from qdrant_client import QdrantClient

    s = LawStore(client=QdrantClient(":memory:"), embedder=HashEmbedder())
    s.ingest_laws(load_seed_laws())
    s.ingest_zones(load_seed_zones())
    return s


def trip(day: date, platform: str = "Swiggy"):
    return SimpleNamespace(transaction_date=day, platform=platform)


MUMBAI_RIDER = SimpleNamespace(zone="Andheri West, Mumbai", platform_accounts=["Swiggy"])
BENGALURU_RIDER = SimpleNamespace(zone="Bengaluru", platform_accounts=["Swiggy"])
PROB = Decimal("0.577")


def law_ids(store, day, rider=MUMBAI_RIDER):
    return [l.law_id for l in laws_for_transaction(trip(day), PROB, rider, store.current_laws(), store.zone_map())]


def _point(store, law_id):
    return next(p for p in store._versions(law_id) if p.payload["record_status"] == "current")


# ---------------------------------------------------------------------------
# The seed and the selection
# ---------------------------------------------------------------------------

def test_seed_carries_concepts_and_figures_as_data():
    laws = {l["id"]: l for l in load_seed_laws()}
    assert len(laws) == 30
    used = {c["concept"] for l in laws.values() for c in l["concepts"]}
    assert used <= set(CONCEPT_EVIDENCE)  # every concept in the data has an evidence check
    assert laws[WAGES_S5]["parameters"]["minimum_wage_hourly"] == "650"
    assert "notified" in laws[WAGES_S5]["parameters"]["minimum_wage_hourly_basis"]


def test_bobby_gets_the_same_seven_laws(store):
    assert law_ids(store, date(2026, 9, 10)) == [
        "code_wages_2019_section_3", WAGES_S5, "code_wages_2019_section_13", "code_wages_2019_section_14",
        WAGES_S18, "social_security_2020_section_113", "social_security_2020_section_114"]
    # Karnataka's gig laws apply only in Karnataka.
    assert "karnataka_gig_act_section_15" in law_ids(store, date(2026, 9, 10), BENGALURU_RIDER)
    # A law not yet in force on the trip's date is not used.
    assert WAGES_S5 not in law_ids(store, date(2025, 11, 20))


def test_zone_to_state_comes_from_the_map(store):
    zones = store.zone_map()
    assert state_for_zone("Andheri West, Mumbai", zones) == "Maharashtra"
    assert state_for_zone("Bengaluru", zones) == "Karnataka"
    assert state_for_zone("Somewhere Else", zones) == NATIONWIDE


# ---------------------------------------------------------------------------
# Temporal: an amendment applies from its date, the old text before it
# ---------------------------------------------------------------------------

def test_amendment_applies_from_its_date(store):
    old = next(l for l in load_seed_laws() if l["id"] == WAGES_S5)
    amended = {**old, "version": "amended-2027-03", "effective_from": "2027-03-01",
               "parameters": {**old["parameters"], "minimum_wage_hourly": "700"}}
    report = store.ingest_laws([amended])
    assert report["added"] == [f"{WAGES_S5}@amended-2027-03"]
    assert report["closed_by_amendment"] == [f"{WAGES_S5}@original -> 2027-02-28"]

    def wage_on(day):
        laws = laws_for_transaction(trip(day), PROB, MUMBAI_RIDER, store.current_laws(), store.zone_map())
        return _minimum_wage_from_laws(laws)

    assert wage_on(date(2027, 2, 15)) == Decimal("650")
    assert wage_on(date(2027, 3, 15)) == Decimal("700")
    assert law_ids(store, date(2027, 3, 15)).count(WAGES_S5) == 1  # never both versions at once


# ---------------------------------------------------------------------------
# Changed in Qdrant, without touching code
# ---------------------------------------------------------------------------

def test_figure_changed_in_qdrant_is_used_by_the_economic_check(store):
    tx = TransactionRecord(transaction_id="T1", platform="Swiggy", transaction_date=date(2026, 9, 10),
                           gross_amount=Decimal("100"), net_amount=Decimal("80"), distance_km=Decimal("3"),
                           duration_minutes=30, wait_time_minutes=0)
    calc = BusinessCostCalculation(petrol_cost=Decimal("6"), business_hourly_rate=Decimal("660"))

    def economic():
        laws = laws_for_transaction(tx, PROB, MUMBAI_RIDER, store.current_laws(), store.zone_map())
        return analyze_economic_concerns([calc], [tx], None, [{"transaction_id": "T1", "laws": laws}])

    assert economic() == []  # 660/hr is not below the 650 in the law
    store.client().set_payload(store.collection, payload={"parameters": {"minimum_wage_hourly": "700"}},
                               points=[_point(store, WAGES_S5).id])
    found = economic()
    assert len(found) == 1 and found[0].min_wage_hourly_benchmark == Decimal("700")
    # No law in force sets a minimum wage: no comparison is made at all.
    assert analyze_economic_concerns([calc], [tx], None, [{"transaction_id": "T1", "laws": []}]) == []


def _evidence(anomaly):
    return SimpleNamespace(tx_id="T1", anomaly_type=anomaly, has_distance=False, distance_km=Decimal("0"),
                           has_wait_time=False, wait_time_minutes=0, variance=Decimal("0"), variance_pct=Decimal("0"),
                           incentive_amount=Decimal("0"), deduction_amount=Decimal("5"))


def test_concept_given_in_qdrant_decides_what_a_law_can_flag(store):
    def s18():
        return next(l for l in laws_for_transaction(trip(date(2026, 9, 10)), PROB, MUMBAI_RIDER,
                                                    store.current_laws(), store.zone_map()) if l.law_id == WAGES_S18)

    assert _evaluate_law_relevance(_evidence("incorrect_deduction"), s18(), PROB).matched_concepts == ["wage_protection"]
    store.client().set_payload(store.collection, payload={"concepts": []}, points=[_point(store, WAGES_S18).id])
    assert _evaluate_law_relevance(_evidence("incorrect_deduction"), s18(), PROB).triggered is False


def test_import_never_overwrites_what_is_in_qdrant(store):
    store.client().set_payload(store.collection, payload={"text": "Edited in Qdrant."}, points=[_point(store, WAGES_S5).id])
    report = store.ingest_laws(load_seed_laws())
    assert report["kept_qdrant_version"] == [f"{WAGES_S5}@original"] and len(report["unchanged"]) == 29
    assert _point(store, WAGES_S5).payload["text"] == "Edited in Qdrant."
    # Only when asked: the seed becomes a new revision; the edited one is kept.
    revised = store.ingest_laws(load_seed_laws(), update_existing=True)
    assert revised["revised"] == [f"{WAGES_S5}@original"]
    statuses = sorted(p.payload["record_status"] for p in store._versions(WAGES_S5))
    assert statuses == ["current", "replaced_revision"]


def test_legacy_documents_stop_being_served(store):
    from qdrant_client.models import PointStruct

    store.client().upsert(store.collection, points=[PointStruct(id=999, vector=[0.1] * 384, payload={
        "id": "old_law", "jurisdiction": "India", "status": "in_force", "applicability": "gig_and_platform_worker_social_security",
        "effective_from": "2000-01-01"})])
    assert store.retire_legacy() == ["old_law"]
    assert store.retire_legacy() == []  # already marked
    assert "old_law" not in [l["id"] for l in store.current_laws()]


def test_laws_unreadable_means_no_legal_check_said_plainly(monkeypatch):
    import orchestrator
    from models import FairPayState, PartnerProfile

    class Down:
        def current_laws(self):
            raise LawStoreUnavailable("connection refused")

        def zone_map(self):
            return {}

    monkeypatch.setattr(orchestrator, "LawStore", Down)
    profile = PartnerProfile(partner_id="p", name="Rider", phone="0", vehicle_type="bike", petrol_per_km=Decimal("2"),
                             zone="Bengaluru", platform_accounts=["Swiggy"], active_since=date(2026, 1, 1))
    state = FairPayState(partner_id="p", partner_profile=profile, use_local_laws=False,
                         classification_probability=Decimal("0.5"), status="ingesting")
    out = orchestrator.versioning_node(state)
    assert out["applicable_laws"] == [] and orchestrator.LAWS_UNAVAILABLE_GAP in out["evidence_gaps"]
