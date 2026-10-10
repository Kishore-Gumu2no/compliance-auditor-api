"""The facts an incentive audit may use, each with where it came from.

Everything the incentive audit reasons about passes through
:class:`IncentiveEvidence`. Two adapters fill it:

* :func:`load_history_evidence` - the partner's records on file (orders,
  incentive line items, the dataset window).
* :func:`evidence_from_extraction` - what was read off the documents sent
  through Telegram (see ``incentive_extraction``). The trips a partner sends
  for a day are taken as every trip of that day: they are asked to send the
  day's entire trip history, and FairPay does not depend on a printed total.

Nothing is estimated here. A field the evidence does not show stays ``None``;
a fact that had to be inferred carries ``provenance="INFERRED"`` and a note
saying why. Contradictions between documents are recorded in ``conflicts``
and never resolved by picking the more convenient value.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from incentive_rules import platform_key

Provenance = Literal["OBSERVED", "CALCULATED", "RETRIEVED_RULE", "INFERRED", "UNKNOWN"]
TripStatus = Literal["delivered", "cancelled", "incomplete", "rejected", "unknown"]

LOCAL_TZ = "Asia/Kolkata"


class TripObservation(BaseModel):
    """One order the rider was offered or did, as the evidence shows it."""

    order_id: str
    platform: str
    accepted_at: Optional[datetime] = Field(default=None, description="Local time the order was accepted")
    completed_at: Optional[datetime] = Field(default=None, description="Local time the order was delivered/closed")
    service_date: Optional[date] = Field(default=None, description="Date printed for the order when no time is shown")
    status: TripStatus = "unknown"
    status_label: Optional[str] = Field(default=None, description="Status exactly as printed")
    status_provenance: Provenance = "OBSERVED"
    batch_id: Optional[str] = Field(default=None, description="Orders sharing a batch_id were done in one trip")
    batch_kind: Optional[Literal["multi_order", "multi_pickup"]] = None
    distance_km: Optional[Decimal] = None
    earning_amount: Optional[Decimal] = Field(
        default=None, description="What this order earned, when the evidence shows it")
    source: str = ""
    notes: List[str] = Field(default_factory=list)

    def dates(self) -> Dict[str, Optional[date]]:
        return {
            "accepted_at": self.accepted_at.date() if self.accepted_at else None,
            "completed_at": self.completed_at.date() if self.completed_at else None,
            "printed": self.service_date,
        }

    def order_time(self) -> Optional[datetime]:
        return self.accepted_at or self.completed_at


class PlatformCountObservation(BaseModel):
    """The platform's own progress figure for an incentive (e.g. '16/20 orders')."""

    platform: str
    period_start: date
    period_end: date
    counted_orders: int = Field(ge=0)
    target_orders: Optional[int] = None
    reward_amount: Optional[Decimal] = None
    incentive_name: Optional[str] = None
    status_label: Optional[str] = None
    conditions_text: List[str] = Field(default_factory=list)
    source: str = ""


class IncentivePaymentObservation(BaseModel):
    """An incentive/bonus amount the evidence shows as paid or credited."""

    platform: str
    amount: Decimal
    paid_on: Optional[date] = None
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    label: str = ""
    rule_id: Optional[str] = Field(default=None, description="Known only for records on file")
    source: str = ""


ALL_APPS = "*"  # platform value for facts that cover every app at once (e.g. login sessions on file)


class MetricObservation(BaseModel):
    """A performance figure for a stated period.

    ``login_minutes`` is time logged in on this platform. ``login_minutes_all_apps``
    is time logged in across all apps together (the sessions on file): time on
    one app can never be more than it, but it cannot be split by app.
    """

    platform: str
    metric: Literal["acceptance_rate", "rejection_rate", "login_minutes", "login_minutes_all_apps", "rating"]
    value: Decimal
    period_start: date
    period_end: date
    start_time: Optional[str] = Field(default=None, description="Local HH:MM, when a login range is shown")
    end_time: Optional[str] = Field(default=None, description="Local HH:MM, when a login range is shown")
    source: str = ""


class CoverageRange(BaseModel):
    """Dates for which a kind of evidence is complete."""

    platform: str
    start: date
    end: date
    basis: str = Field(description="Why this range is known to be complete")


class IncentiveEvidence(BaseModel):
    partner_id: str
    source: Literal["history", "telegram", "synthetic"]
    region: Optional[str] = None
    platforms: List[str] = Field(default_factory=list)
    trips: List[TripObservation] = Field(default_factory=list)
    platform_counts: List[PlatformCountObservation] = Field(default_factory=list)
    payments: List[IncentivePaymentObservation] = Field(default_factory=list)
    metrics: List[MetricObservation] = Field(default_factory=list)
    # Dates for which every trip is in the evidence (so a low count is a real count).
    trip_coverage: List[CoverageRange] = Field(default_factory=list)
    # Dates for which the earnings records itemize incentives (so a missing payment is visible).
    payment_coverage: List[CoverageRange] = Field(default_factory=list)
    # Dates for which all login time across apps is on file (so a low total is a real total).
    login_coverage: List[CoverageRange] = Field(default_factory=list)
    # Incentive terms printed in the evidence (offer screens); used as observations.
    printed_offers: List[PlatformCountObservation] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    read_gaps: List[str] = Field(default_factory=list)

    def for_platform(self, platform: str) -> "IncentiveEvidence":
        key = platform_key(platform)
        same = lambda items: [i for i in items if platform_key(i.platform) == key or i.platform == ALL_APPS]  # noqa: E731
        return self.model_copy(update={
            "platforms": [p for p in self.platforms if platform_key(p) == key],
            "trips": same(self.trips),
            "platform_counts": same(self.platform_counts),
            "payments": same(self.payments),
            "metrics": same(self.metrics),
            "trip_coverage": same(self.trip_coverage),
            "payment_coverage": same(self.payment_coverage),
            "printed_offers": same(self.printed_offers),
            "login_coverage": same(self.login_coverage),
        })


def covered(ranges: List[CoverageRange], start: date, end: date) -> bool:
    """True when every day from start to end lies in one of the ranges."""
    day = start
    while day <= end:
        if not any(r.start <= day <= r.end for r in ranges):
            return False
        day += timedelta(days=1)
    return True


# ---------------------------------------------------------------------------
# Records on file (the 30-day history)
# ---------------------------------------------------------------------------

def _local(ts: Optional[datetime]) -> Optional[datetime]:
    if ts is None:
        return None
    if ts.tzinfo is not None:
        from zoneinfo import ZoneInfo
        ts = ts.astimezone(ZoneInfo(LOCAL_TZ))
    return ts.replace(tzinfo=None)


def load_history_evidence(partner_id: str, platform: Optional[str] = None) -> IncentiveEvidence:
    """The partner's records on file as incentive evidence.

    * Trips: every order row (``orders``), with accepted and delivered times.
    * Payments: the target-bonus line items (``incentives``), with their rule.
    * Coverage: the dataset window - every order and earning in it is on file.
    * Earnings: each order's ``net_amount`` from the earnings rows on file,
      used only for incentives whose target is an amount earned.
    * Login: ``work_sessions`` records when the partner was online, across
      all apps together (it has no platform column). Each session becomes a
      ``login_minutes_all_apps`` figure for its day.
    * No acceptance, rejection or rating figures are on file.
    """
    from psycopg.rows import dict_row

    from real_data import conn

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT zone FROM partners WHERE partner_id = %s", (partner_id,))
        partner = cur.fetchone()
        if not partner:
            raise ValueError(f"Partner {partner_id} not found")
        cur.execute(
            """
            SELECT o.order_id, o.platform, o.order_timestamp, o.completed_timestamp, o.order_status,
                   o.distance_km, e.net_amount
            FROM orders o
            LEFT JOIN earnings e ON e.order_id = o.order_id
            WHERE o.partner_id = %s ORDER BY o.order_timestamp
            """,
            (partner_id,),
        )
        orders = cur.fetchall()
        cur.execute(
            """
            SELECT i.rule_id, i.name, i.amount, i.platform, i.incentive_type, e.transaction_date, e.earning_id
            FROM incentives i JOIN earnings e ON e.earning_id = i.earning_id
            WHERE i.partner_id = %s AND i.incentive_type IN ('daily_target', 'weekly_target')
            """,
            (partner_id,),
        )
        bonuses = cur.fetchall()
        cur.execute("SELECT key, value FROM dataset_meta WHERE key IN ('window_start', 'window_end')")
        meta = {r["key"]: r["value"] for r in cur.fetchall()}
        cur.execute(
            "SELECT session_id, session_date, online_start, online_end FROM work_sessions "
            "WHERE partner_id = %s ORDER BY online_start",
            (partner_id,),
        )
        sessions = cur.fetchall()

    if not orders:
        raise ValueError(f"No orders on file for partner {partner_id}")

    trips: List[TripObservation] = []
    for row in orders:
        status_raw = (row["order_status"] or "").upper()
        status: TripStatus = {"DELIVERED": "delivered", "CANCELLED": "cancelled"}.get(status_raw, "unknown")
        trips.append(TripObservation(
            order_id=row["order_id"],
            platform=row["platform"],
            accepted_at=_local(row["order_timestamp"]),
            completed_at=_local(row["completed_timestamp"]),
            status=status,
            status_label=row["order_status"],
            distance_km=Decimal(str(row["distance_km"])) if row.get("distance_km") is not None else None,
            earning_amount=Decimal(str(row["net_amount"])) if row.get("net_amount") is not None else None,
            source="orders on file",
        ))

    def _meta_day(key: str, fallback: date) -> date:
        value = meta.get(key)
        try:
            return date.fromisoformat(str(value)[:10]) if value else fallback
        except ValueError:
            return fallback

    first = min(t.accepted_at.date() for t in trips if t.accepted_at)
    last = max(t.accepted_at.date() for t in trips if t.accepted_at)
    window_start, window_end = _meta_day("window_start", first), _meta_day("window_end", last)

    platforms = sorted({t.platform for t in trips})
    coverage = [CoverageRange(platform=p, start=window_start, end=window_end,
                              basis="every order on file for this window") for p in platforms]
    payment_coverage = [CoverageRange(platform=p, start=window_start, end=window_end,
                                      basis="itemised earnings on file for this window") for p in platforms]
    payments = [
        IncentivePaymentObservation(
            platform=b["platform"], amount=Decimal(str(b["amount"])), paid_on=b["transaction_date"],
            label=b["name"], rule_id=b["rule_id"], source=f"earnings record {b['earning_id']}",
        )
        for b in bonuses
    ]
    metrics: List[MetricObservation] = []
    for s in sessions:
        start, end = _local(s["online_start"]), _local(s["online_end"])
        if not start or not end or end <= start:
            continue
        metrics.append(MetricObservation(
            platform=ALL_APPS, metric="login_minutes_all_apps",
            value=Decimal(int((end - start).total_seconds() // 60)),
            period_start=start.date(), period_end=start.date(),
            start_time=start.strftime("%H:%M"), end_time=end.strftime("%H:%M"),
            source=f"login session {s['session_id']} on file (all apps)",
        ))
    login_coverage = ([CoverageRange(platform=ALL_APPS, start=window_start, end=window_end,
                                     basis="every login session on file for this window")] if sessions else [])
    evidence = IncentiveEvidence(
        partner_id=partner_id, source="history", region=partner["zone"], platforms=platforms,
        trips=trips, payments=payments, metrics=metrics, trip_coverage=coverage,
        payment_coverage=payment_coverage, login_coverage=login_coverage,
    )
    return evidence.for_platform(platform) if platform else evidence


# ---------------------------------------------------------------------------
# Documents read through Telegram
# ---------------------------------------------------------------------------

_STATUS_WORDS: List[Tuple[TripStatus, Tuple[str, ...]]] = [
    ("rejected", ("reject", "declin", "missed offer", "ignored")),
    ("cancelled", ("cancel",)),
    ("incomplete", ("incomplete", "undeliver", "not delivered", "returned", "rto", "failed")),
    ("delivered", ("deliver", "complete", "done", "success")),
]


def classify_status(label: Optional[str]) -> Optional[TripStatus]:
    """Map a printed status label to a status; None when no label is printed."""
    text = (label or "").strip().lower()
    if not text:
        return None
    for status, words in _STATUS_WORDS:
        if any(w in text for w in words):
            return status
    return "unknown"


# The year used when the documents print none for a day.
DEFAULT_YEAR = 2026
# Customer tips are never counted as earnings.
TIP_NOT_COUNTED = "is not counted as earnings"

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}


def _printed_day(text: Any) -> Optional[Tuple[int, int, Optional[int]]]:
    """(day, month, year or None) from a printed date such as '8 Oct', '08 Oct, 2026', 'Oct 8' or '2026-10-08'."""
    if not text:
        return None
    s = str(text).strip().lower()
    iso = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", s)
    if iso:
        return int(iso[3]), int(iso[2]), int(iso[1])
    found = re.search(r"\b(20\d{2})\b", s)
    year = int(found[1]) if found else None
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s*([a-z]{3})[a-z]*\b", s)
    if m and m[2] in _MONTHS:
        return int(m[1]), _MONTHS[m[2]], year
    m = re.search(r"\b([a-z]{3})[a-z]*\s+(\d{1,2})\b", s)
    if m and m[1] in _MONTHS:
        return int(m[2]), _MONTHS[m[1]], year
    m = re.search(r"\b(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\b", s)
    if m:
        y = (int(m[3]) + 2000 if len(m[3]) == 2 else int(m[3])) if m[3] else year
        return int(m[1]), int(m[2]), y
    return None


def _parse_clock(value: Optional[str]) -> Optional[Tuple[int, int]]:
    if not value:
        return None
    cleaned = value.strip().upper().replace(".", "")
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
        try:
            parsed = datetime.strptime(cleaned, fmt)
            return parsed.hour, parsed.minute
        except ValueError:
            continue
    return None


def _to_dec(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _rate(value: Any) -> Optional[Decimal]:
    """82 or 82% or 0.82 -> 0.82"""
    number = _to_dec(str(value).replace("%", "").strip()) if value is not None else None
    if number is None:
        return None
    return (number / 100) if number > 1 else number


def evidence_from_extraction(partner_id: str, platform: str, extraction: Dict[str, Any],
                             region: Optional[str] = None) -> IncentiveEvidence:
    """Normalise what was read off the documents into incentive evidence.

    ``extraction`` is ``IncentiveDocumentExtraction.model_dump()`` (plain data,
    so this function is testable without the reader). Rules applied:

    * A trip without a printed status but listed as a paid order is taken as
      delivered, marked INFERRED with the reason.
    * A completion time earlier than the acceptance time on the same printed
      date is taken to be the next day, marked INFERRED.
    * The trip list for a period is complete only when the documents print an
      order count for that period equal to the trips read for it.
    * The same order shown with different statuses or dates is a conflict.
    * The day comes from the printed date. With no year printed, the year
      printed for that same day elsewhere in the documents is used, else
      DEFAULT_YEAR.
    * Without a printed order ID, trips are told apart by day and time. A
      "order ID" printed on trips at different times is a label (e.g. a store
      code), not an order ID. Overlapping screenshots show the same trip twice:
      it is counted once, keeping what each copy shows.
    * Customer tips are not counted as earnings.
    """
    gaps: List[str] = []
    conflicts: List[str] = []

    # -- The day of each row ---------------------------------------------------
    raws = [dict(r) for r in extraction.get("trips") or []]
    printed = [_printed_day(r.get("date_text")) for r in raws]
    years = {(d, m): y for d, m, y in (p for p in printed if p) if y}
    no_year: Dict[date, None] = {}
    for raw, p in zip(raws, printed):
        day = None
        if p:
            d, m, y = p
            if y is None:
                y = years.get((d, m))
            try:
                day = date(y or DEFAULT_YEAR, m, d)
            except ValueError:
                day = None
            if day and y is None:
                no_year[day] = None
        if day is None:
            given = raw.get("date")
            day = date.fromisoformat(given) if isinstance(given, str) and given else (
                given if isinstance(given, date) else None)
        raw["_day"] = day
        # A trip card shows one time; use it whichever field the reader put it in.
        raw["_clock"] = _parse_clock(raw.get("accepted_time")) or _parse_clock(raw.get("completed_time"))
        raw["order_id"] = (raw.get("order_id") or "").strip() or None
    for day in no_year:
        gaps.append(f"No year is printed for {day.day} {day:%b}, so FairPay took it as {day:%d %b %Y}.")

    # -- An "order ID" printed on trips at different times is not an order ID -----
    times: Dict[str, set] = defaultdict(set)
    for raw in raws:
        if raw["order_id"] and raw["_clock"]:
            times[raw["order_id"]].add((raw["_day"], raw["_clock"]))
    labels = sorted(i for i, t in times.items() if len(t) > 1)
    for label in labels:
        gaps.append(f"'{label}' is printed on trips at different times, so it is not an order ID; FairPay told "
                    "those trips apart by their time.")
    for raw in raws:
        if raw["order_id"] in labels:
            raw["order_id"] = None

    # -- One entry per trip; overlapping screenshots counted once ----------------
    def _status_of(raw: Dict[str, Any]) -> Optional[str]:
        label = classify_status(raw.get("status_label"))
        return label or ("delivered" if _to_dec(raw.get("amount")) is not None else None)

    groups: Dict[Any, List[Dict[str, Any]]] = {}
    for index, raw in enumerate(raws, start=1):
        if raw["order_id"]:
            groups.setdefault(("id", raw["order_id"]), []).append(raw)
            continue
        if raw["_day"] is None or raw["_clock"] is None:
            gaps.append(f"Trip {index} on the documents has no order ID or time, so it was not counted.")
            continue
        amount = _to_dec(raw.get("amount"))
        n = 0
        while True:  # same day and time, same (or unreadable) amount = the same trip
            key = ("time", raw["_day"], raw["_clock"], n)
            group = groups.get(key)
            known = {_to_dec(g.get("amount")) for g in group or []} - {None}
            if group is None or amount is None or not known or amount in known:
                break
            n += 1
        hh, mm = raw["_clock"]
        raw["order_id"] = f"{raw['_day']:%d %b} {hh:02d}:{mm:02d}" + (f" ({n + 1})" if n else "")
        groups.setdefault(key, []).append(raw)

    merged_rows: List[Dict[str, Any]] = []
    for (kind, *_), group in groups.items():
        first = group[0]
        if kind == "id":
            known = [(g, _status_of(g)) for g in group if _status_of(g)]
            for g, status in known[1:]:
                if status != known[0][1] or g["_day"] != known[0][0]["_day"]:
                    conflicts.append(
                        f"Order {first['order_id']} appears twice with different details "
                        f"({known[0][1]} on {known[0][0]['_day']}, {status} on {g['_day']}).")
        row = dict(first)
        for other in group[1:]:
            for k, v in other.items():
                if row.get(k) in (None, "") and v not in (None, ""):
                    row[k] = v
        merged_rows.append(row)

    trips: List[TripObservation] = []
    for index, raw in enumerate(merged_rows, start=1):
        order_id, day = raw["order_id"], raw["_day"]
        accepted_clock, completed_clock = raw["_clock"], _parse_clock(raw.get("completed_time"))
        notes: List[str] = []
        accepted = completed = None
        if day and accepted_clock:
            accepted = datetime(day.year, day.month, day.day, *accepted_clock)
        if day and completed_clock:
            completed = datetime(day.year, day.month, day.day, *completed_clock)
            if accepted and completed < accepted:
                completed += timedelta(days=1)
                notes.append("Delivered after midnight: the delivery time is earlier than the acceptance "
                             "time on the same printed date, so it was taken as the next day (inferred).")
        if day is None:
            gaps.append(f"Order {order_id} has no date on the documents, so it could not be placed in an incentive period.")
            continue

        printed = classify_status(raw.get("status_label"))
        if printed is None:
            if _to_dec(raw.get("amount")) is not None:
                status, provenance = "delivered", "INFERRED"
                notes.append("No status is printed; it is listed with an order earning, so it was taken as delivered (inferred).")
            else:
                status, provenance = "unknown", "UNKNOWN"
        else:
            status, provenance = printed, "OBSERVED"

        orders_in_trip = raw.get("orders_in_trip")
        pickups = raw.get("pickups_in_trip")
        batch_id = raw.get("batch_id") or None
        batch_kind = None
        if (isinstance(orders_in_trip, int) and orders_in_trip > 1) or batch_id:
            batch_kind = "multi_order"
        if isinstance(pickups, int) and pickups > 1:
            batch_kind = "multi_pickup"
        if batch_kind and not batch_id:
            batch_id = f"trip-{index}"

        earning, tip = _to_dec(raw.get("amount")), _to_dec(raw.get("tip_amount"))
        if earning is not None and tip:
            earning -= tip
            notes.append(f"Customer tip of ₹{tip} {TIP_NOT_COUNTED}.")
        trips.append(TripObservation(
            order_id=order_id, platform=platform, accepted_at=accepted, completed_at=completed,
            service_date=day, status=status, status_label=raw.get("status_label"),
            status_provenance=provenance, batch_id=batch_id, batch_kind=batch_kind,
            distance_km=_to_dec(raw.get("distance_km")), earning_amount=earning,
            source=raw.get("source") or "documents", notes=notes,
        ))

    def _day(value: Any) -> Optional[date]:
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value)[:10]) if value else None
        except ValueError:
            return None

    progress: List[PlatformCountObservation] = []
    for raw in extraction.get("incentive_progress") or []:
        start, end = _day(raw.get("period_start")), _day(raw.get("period_end"))
        start, end = start or end, end or start
        if start is None or raw.get("completed_orders_shown") is None:
            if raw.get("completed_orders_shown") is not None:
                gaps.append("An incentive progress figure has no dates, so it could not be matched to a period.")
            continue
        progress.append(PlatformCountObservation(
            platform=platform, period_start=start, period_end=end,
            counted_orders=int(raw["completed_orders_shown"]),
            target_orders=raw.get("target_orders"), reward_amount=_to_dec(raw.get("reward_amount")),
            incentive_name=raw.get("incentive_name"), status_label=raw.get("status_label"),
            conditions_text=list(raw.get("conditions_text") or []), source=raw.get("source") or "documents",
        ))

    payments = [
        IncentivePaymentObservation(
            platform=platform, amount=_to_dec(raw.get("amount")), paid_on=_day(raw.get("date")),
            period_start=_day(raw.get("period_start")), period_end=_day(raw.get("period_end")),
            label=raw.get("label") or "", source=raw.get("source") or "documents",
        )
        for raw in extraction.get("incentive_payments") or []
        if _to_dec(raw.get("amount")) is not None
    ]

    metrics: List[MetricObservation] = []
    for raw in extraction.get("performance") or []:
        start, end = _day(raw.get("period_start")) or _day(raw.get("date")), _day(raw.get("period_end")) or _day(raw.get("date"))
        metric = raw.get("metric")
        if metric in ("acceptance_rate", "rejection_rate"):
            value = _rate(raw.get("value"))
        elif metric in ("login_minutes", "rating"):
            value = _to_dec(raw.get("value"))
        else:
            continue
        if value is None or start is None or end is None:
            gaps.append(f"A {str(metric).replace('_', ' ')} figure has no clear period, so it was not used.")
            continue
        metrics.append(MetricObservation(platform=platform, metric=metric, value=value,
                                         period_start=start, period_end=end, source=raw.get("source") or "documents"))
    for entry in extraction.get("work_time") or []:
        if entry.get("date") and entry.get("minutes"):
            day = _day(entry["date"])
            metrics.append(MetricObservation(platform=platform, metric="login_minutes",
                                             value=Decimal(entry["minutes"]), period_start=day, period_end=day,
                                             start_time=entry.get("start"), end_time=entry.get("end"),
                                             source="login time on documents"))

    # The trips sent for a day are every trip of that day. Partners are asked
    # for the day's entire trip history; a printed total is not required.
    trip_days = sorted({t.service_date for t in trips if t.service_date})
    trip_coverage = [CoverageRange(platform=platform, start=d, end=d,
                                   basis="the trips you sent are taken as every trip of this day")
                     for d in trip_days]

    payment_coverage: List[CoverageRange] = []
    for raw in extraction.get("statement_periods") or []:
        start, end = _day(raw.get("period_start")), _day(raw.get("period_end"))
        if start and end and raw.get("itemizes_incentives"):
            payment_coverage.append(CoverageRange(platform=platform, start=start, end=end,
                                                  basis="an earnings statement that lists incentives"))

    # Contradictory printed figures for the same period are kept as conflicts.
    by_period: Dict[Tuple, set] = defaultdict(set)
    for obs in progress:
        by_period[("count", obs.period_start, obs.period_end, (obs.incentive_name or "").lower())].add(obs.counted_orders)
    for metric in metrics:
        if metric.metric != "login_minutes":
            by_period[(metric.metric, metric.period_start, metric.period_end, "")].add(metric.value)
    for (kind, start, end, _), values in by_period.items():
        if len(values) > 1:
            label = "incentive progress count" if kind == "count" else kind.replace("_", " ")
            conflicts.append(f"The documents show different {label} figures for {start} to {end}: "
                             + ", ".join(str(v) for v in sorted(values)) + ".")

    return IncentiveEvidence(
        partner_id=partner_id, source="telegram", region=region, platforms=[platform], trips=trips,
        platform_counts=progress, payments=payments, metrics=metrics, trip_coverage=trip_coverage,
        payment_coverage=payment_coverage, printed_offers=[p for p in progress if p.target_orders],
        conflicts=list(dict.fromkeys(conflicts)), read_gaps=list(extraction.get("read_gaps") or []) + gaps,
    )
