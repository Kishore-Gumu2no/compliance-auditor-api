"""Put the labour laws into Qdrant, the only place the audit reads them from.

    python law_kb_ingest.py                       # add what Qdrant does not have yet
    python law_kb_ingest.py --dry-run             # show what the seed files hold
    python law_kb_ingest.py --update-from-seed    # also push seed changes to laws already in Qdrant
    python law_kb_ingest.py --laws amendments.json  # add new law versions from another file

Inputs: ``real_laws.json`` (one entry per provision version) and
``law_jurisdictions.json`` (which state a city or zone is in).

Qdrant is the master. By default nothing already in Qdrant is changed, so an
edit made directly in Qdrant (text, dates, status, figures, concepts) stays.
``--update-from-seed`` turns a seed that differs into a new revision; the
old revision is kept as ``replaced_revision``. Nothing is ever deleted.

To amend a law: add an entry with the same ``id``, a new ``version`` and the
date it starts (``effective_from``). The previous version is closed the day
before. Documents from before this schema are kept and marked
``superseded_schema``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from law_store import LawStore, load_seed_laws, load_seed_zones  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--update-from-seed", action="store_true",
                        help="Replace laws already in Qdrant when the seed differs (old revision kept)")
    parser.add_argument("--laws", type=Path, help="A JSON list of law versions to add instead of real_laws.json")
    args = parser.parse_args()

    laws = json.loads(args.laws.read_text(encoding="utf-8")) if args.laws else load_seed_laws()
    zones = load_seed_zones()
    print(f"{len(laws)} law versions, {len(zones)} zones prepared")
    for law in laws:
        print(f"  {law['jurisdiction']:10} {law['id']:38} {law.get('version', 'original'):12} "
              f"{law.get('effective_from')} -> {law.get('effective_to') or 'open'}")
    if args.dry_run:
        return
    store = LawStore()
    store.ensure_collections()
    legacy = store.retire_legacy()
    laws_report = store.ingest_laws(laws, update_existing=args.update_from_seed)
    zones_report = store.ingest_zones(zones, update_existing=args.update_from_seed)
    print(json.dumps({"legacy_marked_superseded": len(legacy),
                      "laws": {k: len(v) for k, v in laws_report.items()},
                      "laws_kept_as_in_qdrant": laws_report["kept_qdrant_version"],
                      "versions_closed_by_amendment": laws_report["closed_by_amendment"],
                      "zones": {k: len(v) for k, v in zones_report.items()}}, indent=1))
    print(f"Current law versions in Qdrant: {len(store.current_laws())}; zones: {len(store.zone_map())}")


if __name__ == "__main__":
    main()
