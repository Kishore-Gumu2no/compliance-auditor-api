"""Validation of the incentive audit against the LIVE Qdrant knowledge base.

    python validate_incentive_audit.py                # Bobby's real records, then the demos
    python validate_incentive_audit.py --demos-only   # the demos only

Part 1 (REAL DATA) calls POST /audit/incentive in-process (FastAPI TestClient,
so no background streams) for partner 'bobby'. Rules come from Qdrant, as in
production. Nothing is adjusted to produce a finding.

Part 2 (DEMOS) runs synthetic single-day trip histories against the REAL
researched rules in Qdrant, including the Swiggy Bengaluru offer of
31 December 2025 reported by Deccan Herald (19 orders between 6 pm and
midnight for Rs 2,175, with at most one order rejected). The trip histories
are synthetic; the incentive rules they are judged against are real, sourced
evidence.

Each condition line carries where the finding came from:
  OBSERVED    - seen in the rider's records, or in the reported offer
  CALCULATED  - worked out from the records
  RETRIEVED_RULE - a term of the rule itself
  INFERRED    - a reading FairPay had to make, flagged as such
  UNKNOWN     - not established, and never turned into an assumption
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")
load_dotenv(BACKEND.parent / ".env")

from incentive_audit import build_incentive_report  # noqa: E402
from incentive_evidence import evidence_from_extraction  # noqa: E402
from incentive_rules import default_store  # noqa: E402

NYE = date(2025, 12, 31)  # the day of the researched Swiggy and Zomato offers
BENGALURU = "Bengaluru"


def show(title: str, report: dict, with_fit: bool = True) -> None:
    audit, fit = report["incentive_audit"], report["incentive_fit"]
    p = audit["primary"]
    print(f"\n--- {title}")
    print(f"status:   {p['status']}")
    print(f"headline: {p['headline']}")
    if p.get("platform") and p.get("period_label"):
        print(f"period:   {p['platform']} {p['period_label']}")
    if p.get("incentive"):
        inc = p["incentive"]
        print(f"rule:     {inc['name']} | target {inc['target_orders']} | reward {inc['reward_amount']}")
        print(f"          from {inc['source_reference']}")
    for bucket, label in (("passed", "met"), ("failed", "NOT met"), ("unknown", "unverifiable")):
        items = (p.get("conditions") or {}).get(bucket) or []
        for c in items:
            print(f"{label:13} {c['label']}: {c['observed']} (needed {c['required']}) [{c['provenance']}]")
    if p.get("counting_mismatch"):
        m = p["counting_mismatch"]
        print(f"mismatch: {m['explanation']}")
        if m["orders"]:
            print(f"          orders named: {', '.join(m['orders'])}")
    if p.get("payment"):
        print(f"payment:  {p['payment']['status']} - {p['payment']['detail']}")
    print(f"next:     {p['next_action']}")
    for r in p.get("evidence_requests") or []:
        print(f"request:  {r}")
    for t in p.get("evidence_trail") or []:
        if t["fact"].startswith("Where this"):
            print(f"evidence: {t['value']}")
            print(f"          {t['source']}")
    for n in p.get("notes") or []:
        print(f"note:     {n}")
    if audit.get("outcome_counts"):
        print(f"periods:  {audit['periods_checked']} checked {audit['outcome_counts']}")
    for n in (audit.get("limitations") or [])[:3]:
        print(f"limit:    {n}")
    if fit and with_fit:
        print(f"FIT:      [{fit['status']}] {fit['headline']}")
        for line in fit.get("why") or []:
            print(f"          - {line}")
        for a in fit.get("assumptions") or []:
            print(f"          assumption: {a}")
        print(f"          {fit['disclaimer']}")


# ---------------------------------------------------------------------------
# Part 1: REAL DATA (Bobby)
# ---------------------------------------------------------------------------

def real_data() -> None:
    from fastapi.testclient import TestClient

    from api import server

    print("=" * 78)
    print("PART 1 - REAL DATA: partner 'bobby', records on file, rules from Qdrant")
    print("=" * 78)
    client = TestClient(server)  # not a context manager: no lifespan, no stream simulators
    for title, body in (
        ("Bobby - no period given (FairPay picks the period most needing attention)",
         {"partner_id": "bobby", "source": "history"}),
        ("Bobby - Swiggy daily, 19 Sep 2026 (6 orders, about Rs 766 earned that day)",
         {"partner_id": "bobby", "source": "history", "platform": "Swiggy", "period_date": "2026-09-19",
          "incentive_type": "daily_target"}),
        ("Bobby - UberEats weekly target, week of 24 Aug 2026 (one order short)",
         {"partner_id": "bobby", "source": "history", "platform": "UberEats",
          "period_date": "2026-08-24", "incentive_type": "weekly_target"}),
        ("Bobby - Zomato weekly target, week of 7 Sep 2026 (rule version changed mid-week)",
         {"partner_id": "bobby", "source": "history", "platform": "Zomato",
          "period_date": "2026-09-07", "incentive_type": "weekly_target"}),
        ("Bobby - a date outside his records (1 Oct 2026)",
         {"partner_id": "bobby", "source": "history", "period_date": "2026-10-01"}),
    ):
        response = client.post("/audit/incentive", json=body)
        assert response.status_code == 200, response.text
        show(title, response.json(), with_fit=title.endswith("attention)"))


# ---------------------------------------------------------------------------
# Part 2: demos against the real researched rules
# ---------------------------------------------------------------------------

def _orders(n: int, prefix: str, day: date = NYE, start_hour: int = 18, amount: str = "115") -> list:
    """n delivered orders inside 18:00-22:59, each with a printed amount."""
    rows = []
    for i in range(n):
        hour, minute = start_hour + (i % 5), (i * 11) % 60
        rows.append({"order_id": f"{prefix}{i:03d}", "date": day.isoformat(),
                     "accepted_time": f"{hour:02d}:{minute:02d}",
                     "completed_time": f"{hour:02d}:{min(minute + 20, 59):02d}",
                     "status_label": "Delivered", "amount": amount})
    return rows


def _batch(ids: list, trip_id: str, clock: str = "20:05", day: date = NYE) -> list:
    hour, minute = map(int, clock.split(":"))
    done = f"{hour + (minute + 30) // 60:02d}:{(minute + 30) % 60:02d}"
    return [{"order_id": oid, "date": day.isoformat(), "accepted_time": clock, "completed_time": done,
             "status_label": "Delivered", "orders_in_trip": len(ids), "batch_id": trip_id, "amount": "115"}
            for oid in ids]


def _rejected(n: int, day: date = NYE) -> list:
    return [{"order_id": f"REJ{i}", "date": day.isoformat(), "accepted_time": f"2{i}:15",
             "status_label": "Rejected"} for i in range(n)]


def demos() -> None:
    print("\n" + "=" * 78)
    print("PART 2 - DEMOS: synthetic one-day trip histories against the REAL researched rules in Qdrant")
    print("         (the Swiggy and Zomato Bengaluru offers of 31 Dec 2025, reported by Deccan Herald)")
    print("=" * 78)
    store = default_store()

    def run(title, platform, reading, region=BENGALURU, day=NYE, with_fit=False, kind="period_target"):
        # The rider is asking about one incentive, so the check is narrowed to
        # that kind; several incentives can run on the same day.
        reading.setdefault("work_time", [])
        ev = evidence_from_extraction("demo-rider", platform, reading, region=region)
        report = build_incentive_report(ev, store, platform=platform, period_date=day, incentive_type=kind)
        show(title, report, with_fit=with_fit)

    # 1. Achieved: 19 orders in the window and the Rs 2,175 payment is visible.
    run("DEMO 1 - incentive achieved: 19 orders in 18:00-00:00 and the payment is in the records",
        "Swiggy", {
            "trips": _orders(19, "A"),
            "incentive_payments": [{"label": "Double Earnings offer", "amount": "2175", "date": NYE.isoformat(),
                                    "period_start": NYE.isoformat(), "period_end": NYE.isoformat()}],
            "statement_periods": [{"period_start": NYE.isoformat(), "period_end": NYE.isoformat(),
                                   "itemizes_incentives": True}],
        })

    # 2. Target missed: 17 orders. Nothing is wrong with the platform.
    run("DEMO 2 - target missed: 17 orders in the window against the 19 the offer required",
        "Swiggy", {"trips": _orders(17, "B")})

    # 3. Enough orders, but a documented eligibility condition failed.
    run("DEMO 3 - enough orders, eligibility failed: 20 orders but 2 rejected, and the offer allowed one",
        "Swiggy", {"trips": _orders(20, "C") + _rejected(2)})

    # 4. Counting mismatch: 19 single orders all qualify, but the app counted 17.
    run("DEMO 4 - counting mismatch: 19 single orders in the window, app progress shows only 17 of 19",
        "Swiggy", {
            "trips": _orders(19, "D"),
            "incentive_progress": [{"period_start": NYE.isoformat(), "period_end": NYE.isoformat(),
                                    "completed_orders_shown": 17, "target_orders": 19, "reward_amount": "2175"}],
        })

    # 4b. The same shortfall, but caused by a counting rule nobody publishes.
    run("DEMO 4b - undocumented counting: 17 single orders plus a batched trip of 2, app shows 17 of 19",
        "Swiggy", {
            "trips": _orders(17, "E") + _batch(["BATCH1", "BATCH2"], "TRIP-77"),
            "incentive_progress": [{"period_start": NYE.isoformat(), "period_end": NYE.isoformat(),
                                    "completed_orders_shown": 17, "target_orders": 19, "reward_amount": "2175"}],
        })

    # 5. Zomato's reported login condition, and its contradicted national rate.
    run("DEMO 5 - Zomato Bengaluru offer: login inside 18:00-00:00 below the 3.5 hours reported",
        "Zomato", {
            "trips": _orders(6, "F"),
            "work_time": [{"date": NYE.isoformat(), "start": "20:30", "end": "22:00", "minutes": 90}],
        }, with_fit=True, kind="per_order_bonus")


if __name__ == "__main__":
    if "--demos-only" not in sys.argv:
        real_data()
    demos()
    print("\nDone.")
