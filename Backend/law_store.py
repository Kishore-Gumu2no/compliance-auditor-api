"""Labour laws in Qdrant: the one place the audit reads laws from.

Qdrant is the source of truth. ``real_laws.json`` and
``law_jurisdictions.json`` are only seed files: ``law_kb_ingest.py`` adds what
Qdrant does not have yet, and never overwrites what is there unless asked
(``--update-from-seed``), so a change made directly in Qdrant stays.

Temporal by design
------------------
Every Qdrant document is one *version* of one provision, with the dates it was
in force (``effective_from`` .. ``effective_to``, both inclusive; no
``effective_to`` means still in force). An amendment is a new document for the
same ``id`` starting on its date; ingesting it closes the previous version the
day before. For every trip, only the versions in force on that trip's date
are used, so a February trip gets the February text and a March trip the
March text.

What a law decides, as data
---------------------------
* ``concepts``: which kind of problem the provision can flag (minimum wage,
  wage protection, ...) and the words in its text that show it. The audit code
  only knows what *evidence* each concept needs.
* ``parameters``: figures the provision sets, e.g. the minimum wage used by
  the economic check.

Changing a provision's text, dates, status, figures or concepts - or adding a
provision for an existing concept - therefore needs no code change.

Selection is by filter, never by similarity: every provision whose
jurisdiction, applicability and dates match is returned.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

COLLECTION = "labor_laws"
ZONES_COLLECTION = "law_jurisdictions"
SCHEMA_VERSION = 2
SEED_LAWS = Path(__file__).with_name("real_laws.json")
SEED_ZONES = Path(__file__).with_name("law_jurisdictions.json")
NATIONWIDE = "India"

# Fields the store manages itself; they are not part of a law's content.
_STORE_FIELDS = {"record_status", "schema_version", "content_hash", "revision", "replaced_by", "replaced_at",
                 "ingested_at", "listing_order", "closed_by_amendment"}


class LawStoreUnavailable(RuntimeError):
    """Qdrant could not be read, so no law can be used."""


def _day(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def content_hash(law: Dict[str, Any]) -> str:
    content = {k: v for k, v in law.items() if k not in _STORE_FIELDS}
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()


def matches_seed(payload: Dict[str, Any], law: Dict[str, Any]) -> bool:
    """Is the document in Qdrant, as it is now (including direct edits), the same as this seed entry?

    An end date the importer set because a later version was added does not
    count as a difference when the seed leaves the end date open.
    """
    current = {k: v for k, v in payload.items() if k not in _STORE_FIELDS}
    if payload.get("closed_by_amendment") and law.get("effective_to") is None:
        current["effective_to"] = None
    return content_hash(current) == content_hash(law)


def normalise_zone(zone: str) -> str:
    return " ".join((zone or "").strip().lower().split())


# ---------------------------------------------------------------------------
# Choosing the provisions for one trip (pure; used by every caller)
# ---------------------------------------------------------------------------

def state_for_zone(zone: str, zones: Dict[str, str]) -> str:
    """The state a partner's zone is in, from the jurisdiction map; nationwide if unknown.

    The whole zone is looked up first, then each comma-separated part
    ("Andheri West, Mumbai" -> "mumbai").
    """
    key = normalise_zone(zone)
    if key in zones:
        return zones[key]
    for part in key.split(","):
        part = part.strip()
        if part in zones:
            return zones[part]
    return NATIONWIDE


def in_force(law: Dict[str, Any], on: date) -> bool:
    start = _day(law.get("effective_from")) or date(1900, 1, 1)
    end = _day(law.get("effective_to"))
    return start <= on and (end is None or on <= end)


def laws_in_force(laws: Iterable[Dict[str, Any]], state: str, on: date, applicability: List[str]) -> List[Dict[str, Any]]:
    """Every provision for this state (or nationwide), in force on this date, of these applicability kinds."""
    return [
        law for law in laws
        if law.get("jurisdiction") in (state, NATIONWIDE)
        and law.get("status") == "in_force"
        and in_force(law, on)
        and law.get("applicability") in applicability
    ]


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

class LawStore:
    """Labour laws and the zone-to-state map in Qdrant."""

    def __init__(self, client: Any = None, embedder: Any = None, collection: str = COLLECTION,
                 zones_collection: str = ZONES_COLLECTION):
        self._client = client
        self._embedder = embedder
        self.collection = collection
        self.zones_collection = zones_collection

    def client(self):
        if self._client is None:
            from versioning import _get_client
            self._client = _get_client()
        return self._client

    def _embed(self, texts: List[str]) -> List[List[float]]:
        if self._embedder is None:
            from versioning import _get_embedding_model
            self._embedder = _get_embedding_model()
        return [list(map(float, v)) for v in self._embedder.embed(texts)]

    def ensure_collections(self) -> None:
        from qdrant_client.models import Distance, PayloadSchemaType, VectorParams

        client = self.client()
        for name, fields in (
            (self.collection, (("record_status", PayloadSchemaType.KEYWORD), ("schema_version", PayloadSchemaType.INTEGER),
                               ("id", PayloadSchemaType.KEYWORD), ("version", PayloadSchemaType.KEYWORD),
                               ("jurisdiction", PayloadSchemaType.KEYWORD), ("status", PayloadSchemaType.KEYWORD),
                               ("applicability", PayloadSchemaType.KEYWORD))),
            (self.zones_collection, (("record_status", PayloadSchemaType.KEYWORD), ("zone_key", PayloadSchemaType.KEYWORD))),
        ):
            if not client.collection_exists(name):
                client.create_collection(name, vectors_config=VectorParams(size=384, distance=Distance.COSINE))
            existing = set((client.get_collection(name).payload_schema or {}).keys())
            for field_name, schema in fields:
                if field_name not in existing:
                    client.create_payload_index(name, field_name=field_name, field_schema=schema)

    def _scroll(self, collection: str, scroll_filter) -> List[Any]:
        points: List[Any] = []
        offset = None
        while True:
            batch, offset = self.client().scroll(collection, limit=256, offset=offset, with_payload=True,
                                                 scroll_filter=scroll_filter)
            points.extend(batch)
            if offset is None:
                return points

    # -- reading ------------------------------------------------------------
    def current_laws(self) -> List[Dict[str, Any]]:
        """Every current provision version, in listing order. Raises LawStoreUnavailable."""
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        try:
            points = self._scroll(self.collection, Filter(must=[
                FieldCondition(key="record_status", match=MatchValue(value="current")),
                FieldCondition(key="schema_version", match=MatchValue(value=SCHEMA_VERSION)),
            ]))
        except Exception as exc:
            raise LawStoreUnavailable(f"{type(exc).__name__}: {exc}") from exc
        laws = [dict(p.payload) for p in points]
        return sorted(laws, key=lambda law: (law.get("listing_order", 10**6), str(law.get("id")),
                                            str(law.get("effective_from"))))

    def zone_map(self) -> Dict[str, str]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        try:
            points = self._scroll(self.zones_collection, Filter(must=[
                FieldCondition(key="record_status", match=MatchValue(value="current"))]))
        except Exception as exc:
            raise LawStoreUnavailable(f"{type(exc).__name__}: {exc}") from exc
        return {p.payload["zone_key"]: p.payload["state"] for p in points}

    # -- writing (law_kb_ingest.py and tests only) ---------------------------
    def _versions(self, law_id: str) -> List[Any]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        return self._scroll(self.collection, Filter(must=[
            FieldCondition(key="id", match=MatchValue(value=law_id)),
            FieldCondition(key="schema_version", match=MatchValue(value=SCHEMA_VERSION)),
        ]))

    def ingest_laws(self, laws: List[Dict[str, Any]], update_existing: bool = False) -> Dict[str, List[str]]:
        """Add provision versions Qdrant does not have.

        A version already in Qdrant is left as it is (Qdrant is the master),
        unless ``update_existing``: then a seed that differs becomes a new
        revision and the old one is kept as ``replaced_revision``. Afterwards,
        an older version with no end date is closed the day before the next
        version of the same provision starts.
        """
        from qdrant_client.models import PointStruct

        self.ensure_collections()
        client = self.client()
        report: Dict[str, List[str]] = {"added": [], "unchanged": [], "kept_qdrant_version": [], "revised": [],
                                        "closed_by_amendment": []}
        new: List[tuple] = []
        touched: set = set()
        for order, law in enumerate(laws):
            law = {k: v for k, v in law.items() if k not in _STORE_FIELDS}
            law.setdefault("version", "original")
            label = f"{law['id']}@{law['version']}"
            digest = content_hash(law)
            same = [p for p in self._versions(law["id"]) if p.payload.get("version") == law["version"]]
            current = [p for p in same if p.payload.get("record_status") == "current"]
            if any(matches_seed(p.payload, law) for p in current):
                report["unchanged"].append(label)
                continue
            if current and not update_existing:
                report["kept_qdrant_version"].append(label)
                continue
            # Each revision is its own document, so an earlier one is never overwritten.
            revision = 1 + max((int(p.payload.get("revision") or 1) for p in same), default=0)
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"fairpay-law/{law['id']}/{law['version']}/r{revision}/{digest}"))
            now = datetime.now(timezone.utc).isoformat()
            for old in current:
                client.set_payload(self.collection, payload={"record_status": "replaced_revision",
                                                             "replaced_by": point_id, "replaced_at": now},
                                   points=[old.id])
            listing = next((p.payload.get("listing_order") for p in same if p.payload.get("listing_order") is not None),
                           order)
            payload = {**law, "record_status": "current", "schema_version": SCHEMA_VERSION, "content_hash": digest,
                       "revision": revision, "listing_order": listing, "ingested_at": now}
            new.append((point_id, payload))
            (report["revised"] if current else report["added"]).append(label)
            touched.add(law["id"])
        if new:
            vectors = self._embed([f"{p['statute']} {p.get('section', '')}. {p.get('text', '')}" for _, p in new])
            client.upsert(self.collection, points=[PointStruct(id=pid, vector=vec, payload=payload)
                                                   for (pid, payload), vec in zip(new, vectors)])
        for law_id in sorted(touched):
            report["closed_by_amendment"].extend(self._close_open_versions(law_id))
        return report

    def _close_open_versions(self, law_id: str) -> List[str]:
        """An open-ended version ends the day before the next version of the same provision starts."""
        current = [p for p in self._versions(law_id) if p.payload.get("record_status") == "current"]
        current.sort(key=lambda p: _day(p.payload.get("effective_from")) or date(1900, 1, 1))
        closed: List[str] = []
        for earlier, later in zip(current, current[1:]):
            start = _day(later.payload.get("effective_from"))
            if earlier.payload.get("effective_to") is None and start is not None:
                end = (start - timedelta(days=1)).isoformat()
                self.client().set_payload(self.collection, payload={
                    "effective_to": end, "closed_by_amendment": later.payload.get("version")}, points=[earlier.id])
                closed.append(f"{law_id}@{earlier.payload.get('version')} -> {end}")
        return closed

    def ingest_zones(self, zones: List[Dict[str, str]], update_existing: bool = False) -> Dict[str, List[str]]:
        from qdrant_client.models import PointStruct

        self.ensure_collections()
        client = self.client()
        current = self.zone_map()
        report: Dict[str, List[str]] = {"added": [], "unchanged": [], "kept_qdrant_version": [], "revised": []}
        points = []
        for entry in zones:
            key = normalise_zone(entry["zone"])
            if current.get(key) == entry["state"]:
                report["unchanged"].append(key)
                continue
            if key in current and not update_existing:
                report["kept_qdrant_version"].append(key)
                continue
            (report["revised"] if key in current else report["added"]).append(key)
            points.append(PointStruct(id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"fairpay-zone/{key}")),
                                      vector=self._embed([key])[0],
                                      payload={"zone_key": key, "state": entry["state"], "record_status": "current"}))
        if points:
            client.upsert(self.zones_collection, points=points)
        return report

    def retire_legacy(self) -> List[str]:
        """Documents from before this schema stop being served (kept, not deleted)."""
        from qdrant_client.models import FieldCondition, Filter, IsEmptyCondition, MatchValue, PayloadField

        self.ensure_collections()
        points = self._scroll(self.collection, Filter(
            must=[IsEmptyCondition(is_empty=PayloadField(key="schema_version"))],
            must_not=[FieldCondition(key="record_status", match=MatchValue(value="superseded_schema"))]))
        if points:
            self.client().set_payload(self.collection, payload={
                "record_status": "superseded_schema", "replaced_at": datetime.now(timezone.utc).isoformat(),
            }, points=[p.id for p in points])
        return [str(p.payload.get("id")) for p in points]


def load_seed_laws(path: Path = SEED_LAWS) -> List[Dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_seed_zones(path: Path = SEED_ZONES) -> List[Dict[str, str]]:
    return json.loads(path.read_text(encoding="utf-8"))


_LOCAL_STORE: Optional[LawStore] = None


def seeded_local_store() -> LawStore:
    """An in-memory Qdrant holding the seed files: same code path as production, no network.

    Used by the synthetic verification and tests.
    """
    global _LOCAL_STORE
    if _LOCAL_STORE is None:
        from qdrant_client import QdrantClient

        store = LawStore(client=QdrantClient(":memory:"))
        store.ingest_laws(load_seed_laws())
        store.ingest_zones(load_seed_zones())
        _LOCAL_STORE = store
    return _LOCAL_STORE
