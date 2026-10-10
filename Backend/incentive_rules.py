"""Incentive rule knowledge base, stored and retrieved in Qdrant only.

This is the rule boundary of the incentive audit ("Why didn't I get my
incentive?"). It answers one question: *which documented incentive rules
apply to this platform, region and period?* It never decides eligibility;
``incentive_engine`` does that deterministically from the rules returned.

Where rules live
----------------
Only in the Qdrant collection ``incentive_rules`` (the existing FairPay Qdrant
cluster, ``versioning._get_client``). There is no local copy and no fallback:
if Qdrant cannot be reached, no rule is established and the audit says so.
Documents are written by ``incentive_kb_ingest.py`` from researched public
sources and from the contract terms on file; the audit never reads those
inputs directly.

One document per rule version
-----------------------------
Each :class:`IncentiveRule` is one version of one incentive programme
(``program_id``). Every field a source does not document stays ``None`` and is
listed as ``NOT_DOCUMENTED`` in ``field_status``; the source quotes behind the
documented fields are kept in ``evidence``.

Ingestion never overwrites blindly: a document identical to a current one is
skipped; a changed document for the same programme and version is added as a
new revision and the old one is kept, marked ``replaced_revision``.

Versioning at retrieval
-----------------------
:func:`resolve_rule_for_period` works per programme:

* one version covering the whole period            -> ESTABLISHED
* several versions with identical terms covering it -> ESTABLISHED (noted)
* versions with different terms inside the period   -> AMBIGUOUS
* nothing covering the whole period                 -> NOT_ESTABLISHED

The current rule is never assumed to apply historically.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

COLLECTION = "incentive_rules"
SCHEMA_VERSION = 3
_NO_EXPIRY_TS = int(datetime(2100, 1, 1).timestamp())

ESTABLISHED = "ESTABLISHED"
NOT_ESTABLISHED = "NOT_ESTABLISHED"
RULE_AMBIGUOUS = "AMBIGUOUS"

DOCUMENTED = "DOCUMENTED"
NOT_DOCUMENTED = "NOT_DOCUMENTED"

RULE_NOT_ESTABLISHED_TEXT = "FairPay could not establish the exact incentive rules for this period."
KB_UNAVAILABLE_TEXT = "FairPay could not reach its incentive rule knowledge base, so no rule could be established."

IncentiveType = Literal[
    "daily_target", "weekly_target", "period_target", "earnings_target",
    "per_order_bonus", "minimum_guarantee", "joining_bonus", "referral_bonus", "unspecified",
]

# How the target is measured, when there is one.
EarningBasis = Literal["orders", "payout_amount", "login_time", "per_order", "not_documented"]

# What kind of evidence a rule rests on. Always reported with the rule: a
# demonstration-dataset rule must never be read as a real-world platform rule.
EvidenceClass = Literal["real_world_evidence", "demonstration_dataset"]

# The user-facing confidence bands.
CONFIDENCE_LABELS = ("HIGH", "MEDIUM", "LOW")
ConfidenceLabel = Literal["HIGH", "MEDIUM", "LOW"]

# What the evidence says about the rule itself.
EvidenceStatus = Literal["VERIFIED", "OBSERVED", "SUPERSEDED", "CONTRADICTED", "UNKNOWN"]

# 1 official, 2 reputable journalism / published research, 3 real-world rider
# evidence, 4 third-party blogs and aggregators. Tier 4 is never equivalent to
# an official rule and is only used where nothing stronger was found.
SourceTier = Literal[1, 2, 3, 4]
PeriodScope = Literal["day", "week_mon_sun", "custom", "not_documented"]

# Incentives whose target is a number of orders in a day / week / stated period.
ORDER_TARGET_TYPES = ("daily_target", "weekly_target", "period_target")

# Fields reported as DOCUMENTED / NOT_DOCUMENTED for every rule.
TRACKED_FIELDS = (
    "target_orders", "reward_amount", "min_acceptance_rate", "max_rejection_rate", "min_login_minutes",
    "required_login_windows", "period_scope", "time_windows", "counting.multi_order", "counting.multi_pickup",
    "counting.cancelled", "counting.incomplete", "counting.day_boundary", "min_distance_km", "min_rating",
    "opt_in_required", "other_conditions", "region", "effective_from", "effective_to", "payout_timing",
    "payout_window_days", "city", "zone", "earning_basis", "login_window", "min_login_minutes_in_window",
    "min_orders_in_window", "max_rejections_in_period", "min_weight_kg", "per_order_amount",
    "target_payout_amount",
)


class RuleStoreUnavailable(RuntimeError):
    """Qdrant could not be reached or queried."""


class CountingRules(BaseModel):
    """How the platform counts orders towards the target. ``None`` = not documented."""

    multi_order: Optional[Literal["each_order", "once_per_trip"]] = None
    multi_pickup: Optional[Literal["each_order", "once_per_trip"]] = None
    incomplete: Optional[Literal["counted", "excluded"]] = None
    cancelled: Optional[Literal["counted", "excluded"]] = None
    day_boundary: Optional[Literal["accepted_at", "completed_at"]] = None


class RuleEvidence(BaseModel):
    """A quote from the source that supports one field."""

    field: str
    quote: str
    source_url: str
    note: str = ""


class FieldInference(BaseModel):
    """A likely behaviour for a field the sources do not document.

    Never used by the deterministic engine: an inference stays UNKNOWN for
    evaluation and is shown only as context.
    """

    field: str
    inferred_behaviour: str
    confidence_label: ConfidenceLabel = "LOW"
    basis: str = ""


class IncentiveRule(BaseModel):
    """One documented version of one incentive programme."""

    rule_id: str = Field(description="Unique per version")
    program_id: str = Field(description="The incentive programme this version belongs to")
    version: str
    platform: str
    region: str = Field(default="ALL", description="City, state, country, or ALL when the source sets no region")
    region_level: Literal["city", "state", "country", "all"] = "all"
    city: Optional[str] = Field(default=None, description="City the evidence names, when it names one")
    zone: Optional[str] = Field(default=None, description="Zone or dark store the evidence names, when it names one")
    incentive_name: str
    incentive_type: IncentiveType
    period_scope: PeriodScope = "not_documented"
    effective_from: date
    effective_from_basis: Literal["documented", "source_as_of_date", "source_year_only"] = "documented"
    effective_to: Optional[date] = None
    effective_to_basis: Literal["documented", "superseded_by_newer_source", "open"] = "open"
    # Target and reward. None = not documented (often personalised and shown only in the app).
    earning_basis: EarningBasis = Field(default="not_documented", description="What the target counts")
    target_orders: Optional[int] = Field(default=None, gt=0)
    target_payout_amount: Optional[Decimal] = Field(
        default=None, ge=0, description="Target expressed as a day's earnings rather than an order count")
    reward_amount: Optional[Decimal] = Field(default=None, ge=0)
    per_order_amount: Optional[Decimal] = Field(
        default=None, ge=0, description="Amount paid per qualifying order, for a per-order bonus")
    reward_text: Optional[str] = Field(default=None, description="Reward as printed when it is not one amount, e.g. 'up to ₹10,000'")
    # Eligibility conditions. None = not documented.
    min_acceptance_rate: Optional[Decimal] = Field(default=None, ge=0, le=1)
    max_rejection_rate: Optional[Decimal] = Field(default=None, ge=0, le=1)
    min_login_minutes: Optional[int] = Field(default=None, ge=0)
    required_login_windows: Optional[List[str]] = Field(
        default=None, description="Windows the rider must be logged in for in full")
    login_window: Optional[str] = Field(
        default=None, description="A window inside which login time or orders are measured, e.g. '18:00-00:00'")
    min_login_minutes_in_window: Optional[int] = Field(
        default=None, ge=0, description="Minutes logged in required inside login_window (not the whole window)")
    min_orders_in_window: Optional[int] = Field(
        default=None, gt=0, description="Orders required inside login_window")
    max_rejections_in_period: Optional[int] = Field(
        default=None, ge=0, description="How many offered orders may be rejected in the period")
    time_windows: List[str] = Field(default_factory=list, description="Only orders inside these local windows count")
    min_distance_km: Optional[Decimal] = Field(default=None, ge=0)
    min_weight_kg: Optional[Decimal] = Field(default=None, ge=0, description="Order weight an order must reach to qualify")
    min_rating: Optional[Decimal] = None
    opt_in_required: Optional[bool] = None
    other_conditions: List[str] = Field(default_factory=list, description="Documented conditions FairPay cannot check automatically")
    counting: CountingRules = Field(default_factory=CountingRules)
    award_basis: Optional[str] = Field(default=None, description="e.g. threshold_crossing_order")
    # Payment wait: when the incentive is paid, as documented.
    payout_timing: Optional[str] = None
    payout_window_days: Optional[int] = Field(default=None, ge=0, description="Days after the period by which it is paid")
    # Provenance
    source_type: Literal["official_platform_page", "official_filing", "reported_offer", "published_research",
                         "rider_report", "third_party_summary", "contract_on_file", "offer_shown_in_evidence",
                         "synthetic"]
    source_tier: SourceTier = Field(default=1, description="1 official, 2 journalism/research, 3 rider evidence, 4 third-party")
    evidence_class: EvidenceClass = Field(default="real_world_evidence")
    source_title: str = ""
    source_url: str = ""
    source_publisher: str = ""
    source_date: Optional[date] = None
    retrieved_on: Optional[date] = None
    source_reference: str = Field(description="One line naming the source, shown to partners")
    confidence: Decimal = Field(default=Decimal("1"), ge=0, le=1)
    confidence_label: ConfidenceLabel = Field(default="LOW", description="HIGH, MEDIUM or LOW")
    evidence_status: EvidenceStatus = Field(default="UNKNOWN")
    verification_count: int = Field(default=1, ge=0, description="Independent sources that agree on this rule")
    contradicted_by: List[str] = Field(default_factory=list, description="rule_ids of documents that disagree")
    conditions_complete: bool = Field(default=True, description="False when the source does not publish the full terms")
    evidence: List[RuleEvidence] = Field(default_factory=list)
    inferences: List[FieldInference] = Field(default_factory=list)
    field_status: Dict[str, str] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)
    description: str = ""

    def covers(self, day: date) -> bool:
        return self.effective_from <= day and (self.effective_to is None or self.effective_to >= day)

    def order_target_rule(self) -> bool:
        return self.incentive_type in ORDER_TARGET_TYPES

    def measurable_without_order_target(self) -> bool:
        """True when the rule sets a requirement FairPay can test without an order target."""
        return any(x is not None for x in (
            self.target_payout_amount, self.min_login_minutes, self.min_login_minutes_in_window,
            self.min_orders_in_window, self.min_acceptance_rate, self.max_rejection_rate,
            self.max_rejections_in_period, self.min_rating,
        ))

    def terms_key(self) -> tuple:
        """Everything that changes eligibility, counting or payment; ids and dates excluded."""
        return (
            self.program_id, self.incentive_type, self.period_scope, self.target_orders, self.reward_amount,
            self.min_acceptance_rate, self.max_rejection_rate, self.min_login_minutes,
            tuple(self.required_login_windows or ()), tuple(self.time_windows), self.min_distance_km,
            self.min_rating, self.opt_in_required, tuple(self.other_conditions), self.earning_basis,
            self.target_payout_amount, self.per_order_amount, self.login_window, self.min_login_minutes_in_window,
            self.min_orders_in_window, self.max_rejections_in_period, self.min_weight_kg,
            tuple(sorted(self.counting.model_dump().items())), self.payout_window_days, self.conditions_complete,
        )

    def embedding_text(self) -> str:
        parts = [f"{self.platform} {self.incentive_name}", self.incentive_type.replace("_", " "),
                 " ".join(filter(None, [self.region, self.city, self.zone])), self.description]
        if self.target_orders:
            parts.append(f"target {self.target_orders} orders")
        if self.reward_amount is not None:
            parts.append(f"reward {self.reward_amount}")
        parts.extend(self.other_conditions)
        return " | ".join(p for p in parts if p)

    def with_field_status(self) -> "IncentiveRule":
        """Fill ``field_status`` for every tracked field (explicit entries win)."""
        status: Dict[str, str] = {}
        for name in TRACKED_FIELDS:
            if name in self.field_status:
                status[name] = self.field_status[name]
                continue
            if name.startswith("counting."):
                value = getattr(self.counting, name.split(".", 1)[1])
            else:
                value = getattr(self, name)
            if name in ("period_scope", "earning_basis"):
                documented = value != "not_documented"
            elif name == "effective_from":
                documented = self.effective_from_basis == "documented"
            elif name == "effective_to":
                documented = self.effective_to_basis == "documented"
            elif name == "region":
                documented = self.region_level != "all"
            elif name in ("time_windows", "other_conditions"):
                documented = bool(value)
            else:
                documented = value is not None
            status[name] = DOCUMENTED if documented else NOT_DOCUMENTED
        return self.model_copy(update={"field_status": status})

    def content_hash(self) -> str:
        data = self.model_dump(mode="json", exclude={"retrieved_on"})
        return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


class RuleQuery(BaseModel):
    """Enough context to select the right rule, not just a similar one."""

    platform: str
    period_start: date
    period_end: date
    region: Optional[str] = None
    incentive_type: Optional[str] = None
    program_id: Optional[str] = None
    evidence_text: str = Field(default="", description="Plain words from the evidence, used only to order candidates")


class RuleResolution(BaseModel):
    status: Literal["ESTABLISHED", "NOT_ESTABLISHED", "AMBIGUOUS"]
    rule: Optional[IncentiveRule] = None
    versions: List[IncentiveRule] = Field(default_factory=list)
    retrieval_backend: Literal["qdrant", "unavailable", "evidence"] = "qdrant"
    note: str = ""


def platform_key(name: str) -> str:
    """'Uber Eats', 'ubereats' and 'UberEats' name the same platform."""
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def _ts(day: date) -> int:
    return int(datetime(day.year, day.month, day.day).timestamp())


# ---------------------------------------------------------------------------
# Regions: a rider's area matches rules for that area, its city, state and country
# ---------------------------------------------------------------------------

_CITY_STATE = {
    "mumbai": "Maharashtra", "pune": "Maharashtra", "nagpur": "Maharashtra", "thane": "Maharashtra",
    "bengaluru": "Karnataka", "bangalore": "Karnataka", "mysuru": "Karnataka", "mysore": "Karnataka",
    "mangaluru": "Karnataka", "hubballi": "Karnataka", "delhi": "Delhi", "new delhi": "Delhi",
    "gurugram": "Haryana", "gurgaon": "Haryana", "noida": "Uttar Pradesh", "lucknow": "Uttar Pradesh",
    "hyderabad": "Telangana", "chennai": "Tamil Nadu", "coimbatore": "Tamil Nadu", "kolkata": "West Bengal",
    "ahmedabad": "Gujarat", "surat": "Gujarat", "jaipur": "Rajasthan", "kochi": "Kerala",
    "chandigarh": "Chandigarh", "indore": "Madhya Pradesh", "bhopal": "Madhya Pradesh",
}


def region_scopes(region: Optional[str]) -> List[str]:
    """Lower-case region keys a rule may be scoped to for this rider's area."""
    scopes = ["all"]
    if not region:
        return scopes
    text = region.strip().lower()
    parts = [p.strip() for p in text.split(",") if p.strip()]
    scopes += [text] + parts
    for city, state in _CITY_STATE.items():
        if city in parts or city in text:
            scopes += [city, state.lower(), "india"]
    return list(dict.fromkeys(scopes))


# ---------------------------------------------------------------------------
# The Qdrant-backed store
# ---------------------------------------------------------------------------

def _payload(rule: IncentiveRule, revision: int) -> Dict[str, Any]:
    return {
        **rule.model_dump(mode="json"),
        "platform_key": platform_key(rule.platform),
        "region_key": rule.region.strip().lower(),
        "effective_from_ts": _ts(rule.effective_from),
        "effective_to_ts": _ts(rule.effective_to) if rule.effective_to else _NO_EXPIRY_TS,
        "record_status": "current",
        "revision": revision,
        "content_hash": rule.content_hash(),
        "schema_version": SCHEMA_VERSION,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "embedding_text": rule.embedding_text(),
    }


def _rule_from_payload(payload: Dict[str, Any]) -> IncentiveRule:
    return IncentiveRule.model_validate({k: v for k, v in payload.items() if k in IncentiveRule.model_fields})


class IncentiveRuleStore:
    """Incentive rules in Qdrant. ``client`` / ``embedder`` default to FairPay's own."""

    def __init__(self, client: Any = None, embedder: Any = None, collection: str = COLLECTION):
        self._client = client
        self._embedder = embedder
        self.collection = collection

    # -- plumbing -----------------------------------------------------------
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

    def ensure_collection(self) -> None:
        from qdrant_client.models import Distance, PayloadSchemaType, VectorParams

        client = self.client()
        if not client.collection_exists(self.collection):
            client.create_collection(self.collection, vectors_config=VectorParams(size=384, distance=Distance.COSINE))
        existing = set((client.get_collection(self.collection).payload_schema or {}).keys())
        for field_name, schema in (
            ("platform_key", PayloadSchemaType.KEYWORD), ("incentive_type", PayloadSchemaType.KEYWORD),
            ("region_key", PayloadSchemaType.KEYWORD), ("program_id", PayloadSchemaType.KEYWORD),
            ("version", PayloadSchemaType.KEYWORD), ("record_status", PayloadSchemaType.KEYWORD),
            ("effective_from_ts", PayloadSchemaType.INTEGER), ("effective_to_ts", PayloadSchemaType.INTEGER),
            ("schema_version", PayloadSchemaType.INTEGER), ("evidence_status", PayloadSchemaType.KEYWORD),
            ("evidence_class", PayloadSchemaType.KEYWORD), ("confidence_label", PayloadSchemaType.KEYWORD),
            ("source_tier", PayloadSchemaType.INTEGER),
        ):
            if field_name not in existing:
                client.create_payload_index(self.collection, field_name=field_name, field_schema=schema)

    def _filter(self, query: RuleQuery):
        from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue, Range

        must = [
            FieldCondition(key="record_status", match=MatchValue(value="current")),
            FieldCondition(key="platform_key", match=MatchValue(value=platform_key(query.platform))),
            FieldCondition(key="effective_from_ts", range=Range(lte=_ts(query.period_end))),
            FieldCondition(key="effective_to_ts", range=Range(gte=_ts(query.period_start))),
            FieldCondition(key="region_key", match=MatchAny(any=region_scopes(query.region))),
        ]
        if query.incentive_type:
            must.append(FieldCondition(key="incentive_type", match=MatchValue(value=query.incentive_type)))
        if query.program_id:
            must.append(FieldCondition(key="program_id", match=MatchValue(value=query.program_id)))
        return Filter(must=must)

    # -- reading ------------------------------------------------------------
    def retrieve(self, query: RuleQuery, limit: int = 200) -> List[IncentiveRule]:
        """Every current rule version matching platform, region, type and period.

        The metadata filter decides what matches; the embedding only orders
        the results. Raises :class:`RuleStoreUnavailable` if Qdrant fails.
        """
        try:
            text = " ".join(filter(None, [query.platform, (query.incentive_type or "").replace("_", " "),
                                          "incentive", query.region or "", query.evidence_text]))
            points = self.client().query_points(
                collection_name=self.collection, query=self._embed([text])[0],
                query_filter=self._filter(query), limit=limit, with_payload=True,
            ).points
        except Exception as exc:
            raise RuleStoreUnavailable(f"{type(exc).__name__}: {exc}") from exc
        rules = [_rule_from_payload(p.payload) for p in points]
        return sorted(rules, key=lambda r: (r.program_id, r.effective_from, r.rule_id))

    def platforms(self) -> List[str]:
        """Platforms with at least one current rule."""
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        seen: Dict[str, str] = {}
        offset = None
        try:
            while True:
                points, offset = self.client().scroll(
                    self.collection, limit=256, offset=offset, with_payload=["platform", "platform_key"],
                    scroll_filter=Filter(must=[FieldCondition(key="record_status", match=MatchValue(value="current"))]),
                )
                for p in points:
                    seen.setdefault(p.payload["platform_key"], p.payload["platform"])
                if offset is None:
                    break
        except Exception as exc:
            raise RuleStoreUnavailable(f"{type(exc).__name__}: {exc}") from exc
        return sorted(seen.values())

    # -- writing (used by incentive_kb_ingest only) ---------------------------
    def ingest(self, rules: List[IncentiveRule]) -> Dict[str, List[str]]:
        """Add rules without overwriting: dedupe identical, version changed ones."""
        from qdrant_client.models import FieldCondition, Filter, MatchValue, PointStruct

        self.ensure_collection()
        client = self.client()
        report: Dict[str, List[str]] = {"added": [], "unchanged": [], "revised": []}
        new_points: List[Any] = []
        new_rules: List[IncentiveRule] = []
        for rule in rules:
            rule = rule.with_field_status()
            digest = rule.content_hash()
            same_version, _ = client.scroll(
                self.collection, limit=50, with_payload=True,
                scroll_filter=Filter(must=[
                    FieldCondition(key="program_id", match=MatchValue(value=rule.program_id)),
                    FieldCondition(key="version", match=MatchValue(value=rule.version)),
                ]),
            )
            current = [p for p in same_version if p.payload.get("record_status") == "current"]
            if any(p.payload.get("content_hash") == digest for p in current):
                report["unchanged"].append(rule.rule_id)
                continue
            revision = 1 + max((int(p.payload.get("revision") or 1) for p in same_version), default=0)
            point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"fairpay-incentive/{rule.program_id}/{rule.version}/{digest}"))
            for old in current:
                client.set_payload(self.collection, payload={
                    "record_status": "replaced_revision", "replaced_by": point_id,
                    "replaced_at": datetime.now(timezone.utc).isoformat(),
                }, points=[old.id])
            (report["revised"] if current else report["added"]).append(rule.rule_id)
            new_rules.append(rule)
            new_points.append((point_id, _payload(rule, revision)))
        if new_points:
            vectors = self._embed([r.embedding_text() for r in new_rules])
            client.upsert(self.collection, points=[
                PointStruct(id=pid, vector=vec, payload=payload)
                for (pid, payload), vec in zip(new_points, vectors)
            ])
        return report

    def retire_versions_not_in(self, keep: set) -> List[str]:
        """Mark current documents the research record no longer carries.

        Takes ``{(program_id, version)}``. Nothing is deleted: a withdrawn
        document keeps its payload and stops being retrievable, so a rule that
        was merged into another one cannot still be served as current.
        """
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        client = self.client()
        withdrawn: List[str] = []
        offset = None
        while True:
            points, offset = client.scroll(
                self.collection, limit=256, offset=offset, with_payload=["program_id", "version", "rule_id"],
                scroll_filter=Filter(must=[FieldCondition(key="record_status", match=MatchValue(value="current"))]),
            )
            stale = [p for p in points
                     if (p.payload.get("program_id"), p.payload.get("version")) not in keep]
            if stale:
                client.set_payload(self.collection, payload={
                    "record_status": "withdrawn_from_kb",
                    "replaced_at": datetime.now(timezone.utc).isoformat(),
                }, points=[p.id for p in stale])
                withdrawn += [str(p.payload.get("rule_id")) for p in stale]
            if offset is None:
                break
        return withdrawn

    def retire_legacy(self) -> List[str]:
        """Mark documents from before this schema as not current (kept, not deleted)."""
        from qdrant_client.models import Filter, IsEmptyCondition, PayloadField

        client = self.client()
        points, _ = client.scroll(
            self.collection, limit=500, with_payload=["rule_id"],
            scroll_filter=Filter(must=[IsEmptyCondition(is_empty=PayloadField(key="schema_version"))]),
        )
        if points:
            client.set_payload(self.collection, payload={
                "record_status": "superseded_schema", "replaced_at": datetime.now(timezone.utc).isoformat(),
            }, points=[p.id for p in points])
        return [str(p.payload.get("rule_id")) for p in points]


# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------

def resolve_program(versions: List[IncentiveRule], start: date, end: date) -> RuleResolution:
    """The version of ONE programme that governs [start, end], or why there is none."""
    candidates = sorted([v for v in versions if v.effective_from <= end and (v.effective_to is None or v.effective_to >= start)],
                        key=lambda r: (r.effective_from, r.rule_id))
    if not candidates:
        return RuleResolution(status=NOT_ESTABLISHED, note=RULE_NOT_ESTABLISHED_TEXT)
    day, uncovered = start, []
    while day <= end:
        if not any(r.covers(day) for r in candidates):
            uncovered.append(day)
        day = date.fromordinal(day.toordinal() + 1)
    if uncovered:
        return RuleResolution(
            status=NOT_ESTABLISHED, versions=candidates,
            note=(f"{RULE_NOT_ESTABLISHED_TEXT} The rules on record do not cover {uncovered[0].isoformat()}"
                  + (f" to {uncovered[-1].isoformat()}" if len(uncovered) > 1 else "") + " of this period."))
    if len({r.terms_key() for r in candidates}) > 1:
        return RuleResolution(
            status=RULE_AMBIGUOUS, versions=candidates,
            note=("The incentive rules changed during this period ("
                  + "; ".join(f"{r.version} from {r.effective_from}" for r in candidates)
                  + "), so FairPay cannot tell which version applies to the whole period."))
    note = ""
    if len(candidates) > 1:
        note = ("Two or more rule versions apply within this period ("
                + ", ".join(r.version for r in candidates) + ") with identical terms, so they were treated as one rule.")
    return RuleResolution(status=ESTABLISHED, rule=candidates[-1], versions=candidates, note=note)


def resolve_rule_for_period(store: IncentiveRuleStore, query: RuleQuery) -> RuleResolution:
    """Retrieve from Qdrant, then resolve. Several programmes matching is AMBIGUOUS
    unless the query names one (``program_id``)."""
    try:
        rules = store.retrieve(query)
    except RuleStoreUnavailable as exc:
        return RuleResolution(status=NOT_ESTABLISHED, retrieval_backend="unavailable",
                              note=f"{KB_UNAVAILABLE_TEXT} ({exc})")
    programs = sorted({r.program_id for r in rules})
    if len(programs) > 1:
        return RuleResolution(status=RULE_AMBIGUOUS, versions=rules,
                              note="More than one incentive programme matches: " + ", ".join(programs) + ".")
    return resolve_program(rules, query.period_start, query.period_end)


_default_store: Optional[IncentiveRuleStore] = None


def default_store() -> IncentiveRuleStore:
    """The FairPay Qdrant knowledge base (connection reused per process)."""
    global _default_store
    if _default_store is None:
        _default_store = IncentiveRuleStore()
    return _default_store
