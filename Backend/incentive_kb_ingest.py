"""Populate the Qdrant incentive rule knowledge base.

    python incentive_kb_ingest.py              # write to the FairPay Qdrant cluster
    python incentive_kb_ingest.py --dry-run    # show what would be written

Sources
-------
1. ``incentive_kb/research_*.json`` - incentive rules researched from public
   official sources (app listings published by the platforms, platform help
   pages), each with source quotes. Unofficial claims are listed there under
   ``not_ingested`` and are NOT written.
2. The contract terms on file (``platform_rules`` BONUS_DAILY_TARGET /
   BONUS_WEEKLY_TARGET with their ``contracts``) - FairPay's demonstration
   contracts, written as their own documents and labelled as such.

Writing never overwrites blindly (see ``IncentiveRuleStore.ingest``):
identical documents are skipped, changed ones become a new revision and the
old revision is kept as ``replaced_revision``. Documents from the earlier
schema are kept and marked ``superseded_schema``.

This script is the only reader of the inputs above. At audit time FairPay
reads incentive rules from Qdrant only.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from incentive_rules import IncentiveRule, IncentiveRuleStore, RuleEvidence, platform_key

RESEARCH_DIR = Path(__file__).resolve().parent / "incentive_kb"

_DB_TYPES = {
    "BONUS_DAILY_TARGET": ("daily_target", "day", "Daily order target bonus"),
    "BONUS_WEEKLY_TARGET": ("weekly_target", "week_mon_sun", "Weekly order target bonus (Mon-Sun)"),
}


def load_research_rules(directory: Path = RESEARCH_DIR) -> List[IncentiveRule]:
    rules: List[IncentiveRule] = []
    for path in sorted(directory.glob("research_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        rules.extend(IncentiveRule.model_validate(item) for item in data["rules"])
    return rules


def contract_rule(row: Dict[str, Any], contract: Dict[str, Any]) -> Optional[IncentiveRule]:
    """One ``platform_rules`` target-bonus row as a knowledge-base document.

    Only what the row documents is carried over: target, reward, period and
    when the bonus is paid (on the order that reaches the target). It says
    nothing about acceptance, login, or how batched/cancelled orders or the
    day boundary count, so those stay NOT_DOCUMENTED.
    """
    mapped = _DB_TYPES.get(row.get("rule_type"))
    if not mapped or row.get("status") == "DRAFT" or contract.get("status") == "DRAFT":
        return None
    incentive_type, scope, name = mapped
    params = row.get("parameters") or {}
    try:
        target, reward = int(params["threshold_orders"]), Decimal(str(params["amount"]))
    except (KeyError, TypeError, ValueError):
        return None
    on_crossing = params.get("awarded_on") == "threshold_crossing_order"
    url = f"fairpay-db://contracts/{row['contract_version']}#{row['clause_id']}"
    return IncentiveRule(
        rule_id=row["rule_id"],
        program_id=f"contract-{platform_key(row['platform'])}-{incentive_type}",
        version=row.get("rule_version") or row["contract_version"],
        platform=row["platform"],
        region="ALL",
        region_level="all",
        incentive_name=name,
        incentive_type=incentive_type,
        period_scope=scope,
        effective_from=row["effective_from"],
        effective_to=row.get("effective_to"),
        effective_to_basis="documented" if row.get("effective_to") else "open",
        target_orders=target,
        reward_amount=reward,
        award_basis=params.get("awarded_on"),
        payout_timing="Paid once, on the order that reaches the target" if on_crossing else None,
        payout_window_days=0 if on_crossing else None,
        source_type="contract_on_file",
        source_tier=1,
        evidence_class="demonstration_dataset",
        earning_basis="orders",
        confidence_label="HIGH",
        evidence_status="VERIFIED",
        verification_count=1,
        source_title=contract.get("document_title") or row["contract_version"],
        source_url=url,
        source_publisher="FairPay contracts on file (demonstration dataset)",
        source_date=contract.get("effective_from"),
        retrieved_on=date.today(),
        source_reference=f"{row['contract_version']} clause {row['clause_id']} (rule {row['rule_id']})",
        confidence=Decimal(str(contract.get("confidence") or "1")),
        conditions_complete=True,
        description=row.get("description") or name,
        evidence=[RuleEvidence(field="target_orders,reward_amount,payout_timing",
                               quote=f"{row.get('description')} {json.dumps(params, default=str)}", source_url=url)],
        notes=["FairPay demonstration dataset, not a real-world platform rule: it is the contract on file for the "
               "sample partner. It names no city, so it applies to any region. VERIFIED means it matches that "
               "contract exactly, not that any platform publishes these terms."],
    )


def load_contract_rules() -> List[IncentiveRule]:
    from psycopg.rows import dict_row

    from real_data import conn

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM contracts")
        contracts = {(c["platform"], c["version_id"]): c for c in cur.fetchall()}
        cur.execute("SELECT * FROM platform_rules WHERE rule_type IN ('BONUS_DAILY_TARGET', 'BONUS_WEEKLY_TARGET')"
                    " ORDER BY platform, effective_from")
        rows = cur.fetchall()
    rules = [contract_rule(r, contracts.get((r["platform"], r["contract_version"])) or {}) for r in rows]
    return [r for r in rules if r is not None]


def collection_summary(store: IncentiveRuleStore) -> Dict[str, Any]:
    client = store.client()
    total = client.get_collection(store.collection).points_count
    by_status: Dict[str, int] = {}
    by_platform: Dict[str, int] = {}
    offset = None
    while True:
        points, offset = client.scroll(store.collection, limit=256, offset=offset,
                                       with_payload=["record_status", "platform"])
        for p in points:
            status = p.payload.get("record_status") or "legacy (no status)"
            by_status[status] = by_status.get(status, 0) + 1
            if status == "current":
                by_platform[p.payload["platform"]] = by_platform.get(p.payload["platform"], 0) + 1
        if offset is None:
            break
    return {"collection": store.collection, "points": total, "by_status": by_status, "current_by_platform": by_platform}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-contracts", action="store_true", help="Do not write the contract terms on file")
    args = parser.parse_args()

    rules = load_research_rules()
    if not args.skip_contracts:
        rules += load_contract_rules()
    print(f"{len(rules)} rule documents prepared")
    for r in rules:
        print(f"  {r.platform:9} {r.program_id:38} {r.version:32} {r.incentive_type:15} {r.region}")
    if args.dry_run:
        return
    store = IncentiveRuleStore()
    store.ensure_collection()
    legacy = store.retire_legacy()
    report = store.ingest(rules)
    # Anything current that this record no longer carries (e.g. two documents
    # merged into one) stops being served, without being deleted.
    withdrawn = store.retire_versions_not_in({(r.program_id, r.version) for r in rules})
    print(json.dumps({"legacy_marked_superseded": legacy, "withdrawn_from_kb": withdrawn,
                      **{k: len(v) for k, v in report.items()}}, indent=1))
    print(json.dumps(collection_summary(store), indent=1))


if __name__ == "__main__":
    main()
