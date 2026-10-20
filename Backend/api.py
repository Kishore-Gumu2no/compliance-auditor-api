"""In this file, the main topic is not the asynchronous programming part, it is about '@', this is a decorator.

WHAT IT DOES: It signifies that the following function after using '@' is a special kind of function called DECORATOR function.

WHY USE THIS: Usually when you write a function, let's say Audit, if we gotta use that function you need to call it manually by typing
               result = Audit("Hi Hello I am Kishore"), this is okay but not productive when we don't know when we will have to call it,
               so we need a SPECIAL INSTRUCTOR MARKER (@) to mark the function SPECIAL and tell the compiler that this function is now called
               by ANOTHER PIECE OF CODE (functions) and not the programmer.

HOW DOES IT WORK: Now that th programmer has no right to call the function independently, then how will this function be called? It will
                 called by another PIECE OF CODE (functions). Example: In this file we have got ' @server.post("/audit/synthetic") ', what this
                 means is:
                    1. The function below this line, synthetic_audit_endpoint, is marked as a special function, saying that it cannot be called by the
                       programmer anymore in this file.
                    2. synthetic_audit_endpoint's control of calling is handed over to server, i.e an instant of the FastAPI program, which means,
                       THE CONTROL IS BEING HANDED OVER TO ANOTHER PIECE OF CODE.
                    3. The ' .post("/audit/synthetic") ' here is the trigger, if any user visits a website such as https://www.linkedin.com and
                       clicks the "AUDIT" button, it redirects to https://www.linkedin.com/audit/synthetic and this is when our async function
                       gets triggered or called.
"""

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError, Field
from orchestrator import run_audit
from audit_context import serialize_audit_state, debate_state_from_audit_context
from real_data import load_partner_history_from_db
from api_errors import operation, register_exception_handlers
import db
import json
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import List, Dict, Any, Optional

from debate import run_debate, summarize_scenario

class CounterfactualScenario(BaseModel):
    """Payload for a what-if scenario."""
    platform: str = Field(..., description="Partner/platform name")
    description: str = Field(..., description="Natural-language description of the scenario")
    audit_context: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Sanitized audit context (counterfactual_context) from a completed audit",
    )


class CounterfactualResult(BaseModel):
    """A one-sentence summary first; the three personas are the detail behind it."""
    synthesis: str = Field(..., description="Ultra-simple one-sentence summary of the scenario's effect")
    realist: str = Field(..., description="Realist analysis")
    optimist: str = Field(..., description="Optimist analysis")
    pessimist: str = Field(..., description="Pessimist analysis")
    direction: Optional[str] = Field(default=None, description="increase, decrease, no_change or unclear")
    estimated_change: Optional[Decimal] = Field(default=None, description="Rupee change, computed from the partner's figures")
    calculation: Optional[str] = Field(default=None, description="How the rupee change was computed")


from platforms import STREAM_PROFILES, Platform, StreamProfile


async def run_platform_stream(profile: StreamProfile) -> None:
    """Publish one simulated platform's rising earnings, forever.

    The simulator calls :meth:`ConnectionManager.publish` directly, because it
    runs inside this very process. It used to open a WebSocket client back to
    this server's own ``/stream`` endpoint over loopback TCP, and that was the
    cause of the repeating "keepalive ping timeout" drops:

    * ``manager.broadcast`` sent every tick to *every* connection, so each
      simulator was also sent its own ticks and the other two platforms' ticks.
    * Each simulator read exactly one frame per tick it sent, so the unread
      frames accumulated - and the WebSocket keepalive **pong** queued up
      behind them.
    * The library saw no pong inside its timeout and killed the connection with
      code 1011. All three reconnected, refilled their queues, and were killed
      again, so the dashboard froze for ~5 seconds on a cycle, permanently.

    Publishing in-process removes the whole class of failure: there is no
    socket to drop, no keepalive to miss, no reconnect loop and no queue to
    overflow. ``/stream/ingest`` still exists for a *real* external platform
    feed, which is the only case that needs a network hop.
    """
    earnings = profile.starting_earnings
    while True:
        await manager.publish(profile.platform, earnings)
        earnings += profile.increment
        await asyncio.sleep(profile.tick_seconds)


async def run_delivery_streams() -> None:
    """Background delivery platform simulators for the live earnings stream.

    Each platform ticks on its own schedule, so they run as independent tasks.
    ``asyncio.gather`` propagates the first exception, which the lifespan logs;
    a bug in one simulator must not pass silently.
    """
    await asyncio.gather(*(run_platform_stream(p) for p in STREAM_PROFILES))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start and stop everything the server owns for the length of its run.

    ``verify=True`` is what makes an unreachable database fail *startup* rather
    than every later request. Without it the pool opens successfully against an
    address with no server behind it (see :func:`db.open_pool`), the server
    reports itself healthy, and the fault only appears as a timeout on each
    request - which is exactly how a missing database stayed invisible.

    Startup is deliberately aborted rather than degraded: every feature on the
    dashboard reads from Postgres, so a server without it can serve nothing but
    error pages, and an orchestrator restarting a container that exits is more
    useful than one that keeps a useless container alive.

    The simulator task is cancelled and awaited on the way out; before, it was
    created and never stopped, so its exceptions were never retrieved and
    shutdown left it running.
    """
    try:
        db.open_pool(verify=True)
    except db.DatabaseUnavailable as exc:
        logger.critical("Refusing to start: %s", exc)
        raise
    logger.info("Database ready at %s", db.describe_target())

    stream_task = asyncio.create_task(run_delivery_streams(), name="delivery-streams")

    # Without this, a simulator that raised would die quietly and the
    # dashboard would just stop updating with nothing in the log to say why.
    def _log_stream_failure(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        if (exc := task.exception()) is not None:
            logger.error("Earnings simulators stopped", exc_info=exc)

    stream_task.add_done_callback(_log_stream_failure)

    try:
        yield
    finally:
        stream_task.cancel()
        try:
            await stream_task
        except asyncio.CancelledError:
            pass
        db.close_pool()
        logger.info("Shutdown complete")


# Logging is configured once, here, so every module's ``logger`` call has a
# destination. `print` wrote to stdout with no level, no timestamp and no way
# to turn it down; uvicorn's own logs are already on `logging`, so this puts
# the application's output in the same stream and format as the server's.
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("fairpay.api")

server = FastAPI(lifespan=lifespan)
# Database failures that reached the app without an `operation(...)` wrapper
# still answer 503 rather than a misleading 500.
register_exception_handlers(server)
server.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_methods=["*"],
    allow_headers=["*"],
)
""" #This line is important because it tells the FastAPI framework to use the lifespan function when the server starts and stops, and it is used to perform any setup or teardown tasks that
need to be done when the server starts or stops, so that we can start the async functions when the server starts and stop them when the server stops."""

class ConnectionManager:
    """The dashboards currently watching the live earnings stream.

    It holds **subscribers only** - connections that listen and never send.
    That distinction is the fix for the stream's repeating self-inflicted
    disconnects: the old manager held one undifferentiated set of "active
    connections" and sent every tick to all of them, so the three simulators,
    which were themselves connected as senders, were each sent the other two's
    ticks plus their own. They read one frame per tick, the rest piled up, the
    keepalive pong queued behind the backlog, and the library closed the
    connection as unresponsive (code 1011) every few seconds.

    A producer is now never a subscriber. ``/stream/ingest`` accepts a sender
    and :meth:`publish` fans out only to the dashboards in :attr:`_subscribers`,
    so a producer is never sent its own data and can never be starved by it.
    """

    def __init__(self) -> None:
        # Maps each watching socket to the partner it says it is watching.
        self._subscribers: Dict[WebSocket, Optional[str]] = {}
        # `publish` iterates and awaits; a lock keeps a connect or disconnect
        # arriving mid-fanout from mutating the mapping underneath it. The
        # event loop is single-threaded but `await` is a yield point, so
        # "single-threaded" does not mean "uninterrupted".
        self._lock = asyncio.Lock()

    async def subscribe(self, websocket: WebSocket, partner_id: Optional[str] = None) -> None:
        """Accept a dashboard connection and start sending it ticks."""
        await websocket.accept()
        async with self._lock:
            self._subscribers[websocket] = partner_id
        logger.info(
            "Dashboard subscribed (partner=%s, watching=%d)",
            partner_id or "unspecified",
            len(self._subscribers),
        )

    async def unsubscribe(self, websocket: WebSocket) -> None:
        async with self._lock:
            partner_id = self._subscribers.pop(websocket, None)
        logger.info(
            "Dashboard unsubscribed (partner=%s, watching=%d)",
            partner_id or "unspecified",
            len(self._subscribers),
        )

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def publish(self, platform: Platform, earnings: int) -> int:
        """Send one platform's running total to every watching dashboard.

        Returns the number of dashboards it reached, so a caller can log that
        a tick went nowhere rather than assuming it was delivered.

        ``platform.value`` throughout: formatting the enum itself would send
        ``"Platform.ZOMATO"`` where the dashboard keys its cards by the wire
        name (``"UberEats"``). That mismatch is what once froze the Uber Eats
        card, and ``Backend/platforms.py`` is the single source of both names.
        """
        message = json.dumps({"platform": platform.value, "earnings": earnings})
        logger.debug("Tick: %s -> %s", platform.value, earnings)

        async with self._lock:
            targets = list(self._subscribers)

        dead: list[WebSocket] = []
        delivered = 0
        for connection in targets:
            try:
                await connection.send_text(message)
                delivered += 1
            except Exception:
                # A dashboard that closed between the snapshot above and this
                # send. Collected and removed after the loop so the iteration
                # is never mutated while it runs.
                dead.append(connection)

        if dead:
            async with self._lock:
                for connection in dead:
                    self._subscribers.pop(connection, None)
            logger.debug("Dropped %d closed dashboard connection(s)", len(dead))
        return delivered


# Global connection manager for the earnings stream
manager = ConnectionManager()


class StreamData(BaseModel):
    # Typed as the enum, so a platform name this server does not know is
    # rejected at the boundary instead of quietly becoming a new key on the
    # dashboard - which is exactly how the Uber Eats card went stale.
    platform: Platform
    earnings: int

# Documents are submitted only through the Telegram bot (telegram_bot.py ->
# telegram_audit.run_submission_audit). There is no document upload endpoint.

@server.websocket("/stream")
async def subscribe_stream(websocket: WebSocket, partner_id: Optional[str] = None):
    """The dashboard's live earnings feed. Server-to-client only.

    Reading and writing are separate endpoints because they are separate roles,
    and conflating them is what made the stream disconnect itself every few
    seconds (see :class:`ConnectionManager`). A dashboard subscribes here; a
    platform feed sends to ``/stream/ingest``.

    ``partner_id`` is recorded for the connection rather than ignored, as it
    was before. The ticks themselves stay global: they are a demonstration of
    the live dashboard driven by ``STREAM_PROFILES``, not one partner's
    earnings, so there is nothing per-partner to filter on yet. When a real
    per-partner feed arrives, this is the value to filter by - and until then
    the log says which partner each dashboard claimed to be.

    These ticks are cosmetic and are never written to the ``earnings`` table:
    that table is the audit-grade schema (order_id, contract_version, the
    base/distance/wait/incentive/bonus breakdown, ...) and a tick has none of
    that. Demo mode in the frontend mirrors this by never persisting either.
    """
    await manager.subscribe(websocket, partner_id)
    try:
        while True:
            # A subscriber sends nothing. This await exists only to notice the
            # close: without reading, a disconnect is detected no earlier than
            # the next failed send, and the connection leaks until then.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.unsubscribe(websocket)


@server.websocket("/stream/ingest")
async def ingest_stream(websocket: WebSocket):
    """Where a platform earnings feed sends its ticks. Client-to-server only.

    The in-process simulators do not use this - they call
    :meth:`ConnectionManager.publish` directly, since a loopback TCP connection
    between two objects in the same process buys nothing and cost this project
    a permanent reconnect loop. This endpoint is for a genuinely external feed,
    which is the only case that needs the network hop.

    A sender is never added to the subscriber set, so it is never sent the data
    it just published, nor any other platform's.
    """
    await websocket.accept()
    logger.info("Platform feed connected to /stream/ingest")
    try:
        while True:
            try:
                tick = StreamData(**await websocket.receive_json())
            except WebSocketDisconnect:
                break
            except (ValidationError, ValueError, TypeError) as exc:
                # Rejected at the boundary: an unrecognised platform name must
                # not become a new key on the dashboard. The sender is told
                # which message was refused and the connection stays open.
                detail = (
                    f"{exc.error_count()} problem(s)"
                    if isinstance(exc, ValidationError)
                    else str(exc)
                )
                logger.warning("Rejected a malformed tick: %s", detail)
                await websocket.send_text(json.dumps({
                    "status": "error",
                    "error": "invalid_payload",
                    "detail": f"Invalid data format received: {detail}.",
                }))
                continue

            delivered = await manager.publish(tick.platform, tick.earnings)

            # No "earnings" key: a sender-only acknowledgement must not be
            # shaped like a tick frame, or a client that both sends and listens
            # would read its own ack as a live update. `delivered` lets the
            # sender see that a tick reached nobody.
            await websocket.send_text(json.dumps({
                "status": "published",
                "platform": tick.platform.value,
                "delivered_to": delivered,
            }))
    except WebSocketDisconnect:
        pass
    finally:
        logger.info("Platform feed disconnected from /stream/ingest")

# ---------- Counter‑factual endpoints ----------

@server.post("/counterfactual/simulate", response_model=CounterfactualResult)
async def simulate_counterfactual(scenario: CounterfactualScenario) -> CounterfactualResult:
    """Run a single counter‑factual debate for the supplied scenario using structured audit context."""
    if scenario.audit_context is None:
        raise HTTPException(status_code=422, detail="audit_context is required. Provide the counterfactual_context from a completed audit.")

    figures = await _scenario_figures(scenario.audit_context, scenario.platform)
    figures_text = describe_figures(figures["figures"])
    initial_state = debate_state_from_audit_context(
        audit_context=scenario.audit_context,
        platform=scenario.platform,
        description=scenario.description,
        figures_text=figures_text,
    )
    final_state = await run_debate(initial_state)
    impact = await summarize_scenario(
        f"On {scenario.platform}: {scenario.description}",
        figures["figures"],
        figures_text,
        figures.get("days"),
        {k: final_state.get(k, "") for k in ("realist", "optimist", "pessimist")},
    )
    return CounterfactualResult(
        synthesis=impact["summary"],
        realist=final_state.get("realist", ""),
        optimist=final_state.get("optimist", ""),
        pessimist=final_state.get("pessimist", ""),
        direction=impact.get("direction"),
        estimated_change=impact.get("amount"),
        calculation=impact.get("calculation"),
    )


async def _scenario_figures(audit_context: Dict[str, Any], platform: str) -> Dict[str, Any]:
    """The partner's real numbers for a typed scenario; empty when unavailable."""
    partner_id = audit_context.get("partner_id")
    if not partner_id:
        return {"figures": {}, "days": None}
    merged = None
    if audit_context.get("source") == "telegram" and audit_context.get("submission_id"):
        # A lookup that fails is not the same as a submission that is absent:
        # the first is a server fault and must be reported, the second simply
        # leaves the scenario without figures.
        with operation("load the Telegram audit for this scenario"):
            merged = await asyncio.to_thread(
                telegram_store.get_submission_result, partner_id, audit_context["submission_id"]
            )
        if merged is None:
            return {"figures": {}, "days": None}
    with operation("load the scenario figures"):
        result = await asyncio.to_thread(
            load_scenario_figures, partner_id, platform, audit_context.get("submission_id"), merged
        )
    days = result["figures"].get("days_worked")
    result["days"] = int(days["value"]) if days else None
    return result

# ---------- Earnings Opportunity Simulator ----------
from earnings_opportunity import (
    SCENARIO_TYPES,
    compute_opportunity,
    debate_state_from_opportunity,
    describe_figures,
    fetch_simulations,
    history_hours_by_platform,
    load_scenario_figures,
    work_time_hours_by_platform,
    insert_simulation,
    load_earnings_baseline,
    load_telegram_baseline,
    partner_exists,
)


class EarningsBaseline(BaseModel):
    """The partner's actual numbers, used to pre-fill the simulator.

    Comes from the 30-day history or from a Telegram audit. When the documents
    do not show hours or days worked, simulation_possible is false and
    unavailable_message says why; the figures are then absent.
    """
    partner_id: str
    source: str = "history"
    submission_id: Optional[str] = None
    simulation_possible: bool = True
    unavailable_message: Optional[str] = None
    days_with_earnings: Optional[int] = None
    max_working_days: Optional[int] = None
    expenses_available: bool = True
    expenses_unavailable_reason: Optional[str] = None
    baseline_notes: List[str] = []
    # What would make a refused simulation possible.
    evidence_needed: List[str] = []
    # Each baseline value and whether it was observed, calculated, assumed or unavailable.
    value_sources: List[Dict[str, str]] = []
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    days_worked: Optional[int] = None
    total_orders: Optional[int] = None
    total_income: Optional[Decimal] = None
    total_operating_cost: Optional[Decimal] = None
    net_after_expenses: Optional[Decimal] = None
    total_online_hours: Optional[Decimal] = None
    avg_income_per_day: Optional[Decimal] = None
    avg_hours_per_day: Optional[Decimal] = None
    income_per_hour: Optional[Decimal] = None
    lowest_daily_hourly_rate: Optional[Decimal] = None
    highest_daily_hourly_rate: Optional[Decimal] = None


class OpportunityRequest(BaseModel):
    partner_id: str = Field(..., description="Delivery partner identifier")
    scenario_type: str = Field(..., description="One of: hours, days, expenses")
    value: Decimal = Field(
        ...,
        description="Hours per day, number of working days, or expense change percent",
    )
    submission_id: Optional[str] = Field(
        default=None,
        description="Simulate from this Telegram audit instead of the 30-day history",
    )


class OpportunityResult(BaseModel):
    """Deterministic projection plus the three personas' assessment of it."""
    partner_id: str
    scenario_type: str
    scenario_inputs: Dict[str, Any]
    baseline_earnings: Decimal
    projected_earnings: Decimal
    estimated_difference: Decimal
    conservative_estimate: Optional[Decimal] = None
    assumptions: Dict[str, Any]
    synthesis: str
    realist: str
    optimist: str
    pessimist: str


class SavedSimulation(BaseModel):
    simulation_id: str
    partner_id: str
    scenario_type: str
    scenario_inputs: Dict[str, Any]
    baseline_earnings: Decimal
    projected_earnings: Decimal
    estimated_difference: Decimal
    assumptions: Dict[str, Any]
    created_at: str


async def _baseline_or_error(partner_id: str, submission_id: Optional[str]) -> Dict[str, Any]:
    """The 30-day history baseline, or one built from a Telegram audit.

    Async, and the database reads go through ``asyncio.to_thread``, because
    every one of them is blocking: run directly inside an ``async def`` they
    stop the event loop, and so stop the live earnings stream and every other
    request, for as long as the query takes.
    """
    import telegram_store

    with operation("load the earnings baseline", not_found=(ValueError,)):
        if submission_id:
            merged = await asyncio.to_thread(
                telegram_store.get_submission_result, partner_id, submission_id
            )
            if merged is None:
                raise HTTPException(
                    status_code=404, detail="Telegram audit not found for this partner."
                )
            return await asyncio.to_thread(
                load_telegram_baseline, partner_id, submission_id, merged
            )
        return await asyncio.to_thread(load_earnings_baseline, partner_id)


async def _projection_or_422(
    partner_id: str, scenario_type: str, value: Decimal, submission_id: Optional[str] = None
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Load the baseline and run the deterministic calculation, or fail loudly."""
    if scenario_type not in SCENARIO_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"scenario_type must be one of {', '.join(SCENARIO_TYPES)}.",
        )
    baseline = await _baseline_or_error(partner_id, submission_id)
    try:
        projection = compute_opportunity(baseline, scenario_type, value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return projection, baseline


@server.get("/earnings-opportunity/baseline/{partner_id}", response_model=EarningsBaseline)
async def get_earnings_baseline(partner_id: str, submission_id: Optional[str] = None) -> EarningsBaseline:
    """The partner's own averages, so the UI never hardcodes defaults.

    With ``submission_id`` the baseline comes from that Telegram audit.
    """
    baseline = await _baseline_or_error(partner_id, submission_id)
    return EarningsBaseline(**{k: v for k, v in baseline.items() if k in EarningsBaseline.model_fields})


@server.post("/earnings-opportunity/simulate", response_model=OpportunityResult)
async def simulate_earnings_opportunity(req: OpportunityRequest) -> OpportunityResult:
    """Project one scenario deterministically, then have the personas assess it."""
    projection, baseline = await _projection_or_422(
        req.partner_id, req.scenario_type, req.value, req.submission_id
    )

    debate_input = debate_state_from_opportunity(projection, baseline)
    final_state = await run_debate(debate_input)

    return OpportunityResult(
        **projection,
        synthesis=final_state.get("synthesis", ""),
        realist=final_state.get("realist", ""),
        optimist=final_state.get("optimist", ""),
        pessimist=final_state.get("pessimist", ""),
    )


@server.post("/simulations", response_model=SavedSimulation, status_code=201)
async def save_simulation(req: OpportunityRequest) -> SavedSimulation:
    """Persist a completed simulation.

    The financial figures are recomputed here from the partner's own history
    using the same deterministic function that produced them, so nothing the
    frontend sends is trusted as a financial result.
    """
    with operation("check that the partner exists"):
        exists = await asyncio.to_thread(partner_exists, req.partner_id)
    if not exists:
        raise HTTPException(status_code=404, detail=f"Partner {req.partner_id} not found.")

    projection, baseline = await _projection_or_422(
        req.partner_id, req.scenario_type, req.value, req.submission_id
    )
    # Record which evidence the figures came from, for the History view.
    projection["scenario_inputs"]["source"] = baseline.get("source", "history")
    if baseline.get("submission_id"):
        projection["scenario_inputs"]["submission_id"] = baseline["submission_id"]

    with operation("save the simulation"):
        saved = await asyncio.to_thread(insert_simulation, projection)
    return SavedSimulation(**saved)


@server.get("/simulations/{partner_id}", response_model=List[SavedSimulation])
async def list_simulations(partner_id: str) -> List[SavedSimulation]:
    """Return only this partner's saved simulations, newest first."""
    with operation("load the saved simulations"):
        rows = await asyncio.to_thread(fetch_simulations, partner_id)
    return [SavedSimulation(**row) for row in rows]


# ---------- Telegram submissions (read-only for the website) ----------
# The bot writes these rows; the website polls them so results and completion
# notices appear here. The frozen WebSocket stream is untouched.
import telegram_store


@server.get("/telegram/submissions/{partner_id}/latest")
async def latest_telegram_submission(partner_id: str):
    """The newest completed Telegram audit for this partner, with its results.

    ``ai_analysis`` is the merged output of the existing pipeline, in the same
    shape the website already renders for uploads.
    """
    # This is the first call the dashboard makes on load, and it polls it, so
    # it is also the first thing to break when the database is down - the
    # endpoint whose bare 500 said nothing. It now answers 503 with the reason.
    with operation("load the Telegram audit"):
        latest = await asyncio.to_thread(telegram_store.get_latest_complete, partner_id)
    if latest is None:
        return {"status": "none", "submission": None, "ai_analysis": None}

    # Audits saved before the simulator wiring lack this; derive it from the
    # stored per-platform results so they unlock "How to Earn More" too.
    merged = latest.get("merged_result")
    if merged and merged.get("evidence_mode") == "incentive_audit":
        # The incentive check is shown on its own; it does not feed the
        # earnings simulator or the full-audit result views.
        merged["submission_id"] = latest["submission_id"]
        return {
            "status": "Audit Complete",
            "submission": {k: v for k, v in latest.items() if k not in ("merged_result", "results_by_platform")},
            # The stored facts stay server-side; the page gets the results only.
            "ai_analysis": {k: v for k, v in merged.items() if k != "incentive_evidence"},
            "results_by_platform": None,
        }
    if merged and not merged.get("counterfactual_context"):
        from telegram_audit import build_merged_counterfactual_context
        merged["counterfactual_context"] = build_merged_counterfactual_context(
            latest.get("results_by_platform") or {}, merged
        )
    if merged and merged.get("counterfactual_context") is not None:
        # Lets "Earn More" simulate from this Telegram audit's own documents.
        merged["counterfactual_context"]["submission_id"] = latest["submission_id"]
    if merged:
        # Derived on read so audits saved earlier show hours worked too.
        merged["hours_worked"] = work_time_hours_by_platform(
            merged.get("work_time") or [],
            (merged.get("partner_metadata") or {}).get("platforms") or [],
        )
    return {
        "status": "Audit Complete",
        "submission": {k: v for k, v in latest.items() if k not in ("merged_result", "results_by_platform")},
        "ai_analysis": latest.get("merged_result"),
        "results_by_platform": latest.get("results_by_platform"),
    }


class TelegramSeenRequest(BaseModel):
    partner_id: str
    submission_id: str


@server.post("/telegram/submissions/seen")
async def mark_telegram_submission_seen(req: TelegramSeenRequest):
    """Acknowledge a completion notice so it is not shown again."""
    with operation("update the submission"):
        updated = await asyncio.to_thread(
            telegram_store.mark_seen, req.submission_id, req.partner_id
        )
    if not updated:
        raise HTTPException(status_code=404, detail="Submission not found for this partner.")
    return {"status": "ok"}

# ---------- Partner History Audit endpoint (Milestone 2 CTA) ----------

@server.get("/audit/history/{partner_id}")
async def audit_partner_history(partner_id: str):
    """
    Query the partner's history from Postgres and run the full audit pipeline.
    Used by the 'Discover how you can earn more' CTA.

    Both steps run in a worker thread. `run_audit` is the longest-running call
    in the project - it reads the database, calls an LLM and walks every
    transaction - and it is ordinary blocking code. Called directly inside this
    ``async def`` it held the event loop for its whole duration, which froze
    the live earnings stream and every other request until it finished. The
    incentive endpoint below already did this correctly; this one was the
    exception, and it was the slowest path in the app.
    """
    with operation("load the partner history", not_found=(ValueError,)):
        db_data = await asyncio.to_thread(load_partner_history_from_db, partner_id)

    with operation("run the full audit"):
        ai_result = await asyncio.to_thread(
            run_audit,
            partner_id=db_data["partner_id"],
            partner_profile=db_data["partner_profile"],
            pdf_paths=[],
            image_paths=[],
            platform=db_data["platform"],
            contract_versions=db_data["contract_versions"],
            transactions=db_data["transactions"],
            reconciliation_predictions=db_data.get("reconciliation_predictions", []),
        )

    serialized = serialize_audit_state(ai_result)
    if serialized.get("status") == "error":
        raise HTTPException(status_code=422, detail=serialized.get("error") or "Audit failed.")

    # Partner metadata from the same 30-day load (no extra queries)
    serialized["partner_metadata"] = {
        "partner_id": db_data["partner_id"],
        "name": db_data["partner_profile"].name,
        "platforms": db_data.get("platforms", []),
    }
    # Rich per-order history is the full-audit path (here, the synthetic
    # demonstration dataset) - not what a partner can normally supply.
    serialized["evidence_mode"] = "full_audit"
    serialized["evidence_mode_label"] = "Full Audit"
    serialized["evidence_source"] = "demonstration_history"
    # Deliberately non-fatal and deliberately not an `operation(...)`: hours
    # worked is one display field, and the audit result behind it is worth far
    # more than this. The failure travels in the payload so the page can say
    # the field is missing, and it is logged so it is not lost.
    try:
        serialized["hours_worked"] = await asyncio.to_thread(
            history_hours_by_platform, db_data["partner_id"], db_data.get("platforms", [])
        )
    except Exception as exc:
        logger.warning("Hours worked could not be loaded for %s: %s", partner_id, exc)
        serialized["hours_worked"] = {"error": f"Hours worked could not be loaded: {exc}"}

    return {
        "status": "Audit Complete",
        "ai_analysis": serialized,
    }


# ---------- Incentive audit: "Why didn't I get my incentive?" ----------
# Separate from the full audit above: it never runs run_audit and its result is
# never merged into contract, legal or economic findings. Every count and
# condition is recomputed here from the evidence on the server.
from datetime import date as _date
from typing import Literal

from audit_context import _json_safe
from incentive_audit import build_incentive_report
from incentive_evidence import IncentiveEvidence, load_history_evidence
from incentive_fit import FULL_AUDIT_FIT_DAYS, INCENTIVE_CHECK_FIT_DAYS
from incentive_rules import default_store


class IncentiveAuditRequest(BaseModel):
    partner_id: str = Field(..., description="Delivery partner identifier")
    source: Literal["history", "telegram"] = Field(
        default="history",
        description="history: the partner's records on file; telegram: a completed Telegram incentive check",
    )
    submission_id: Optional[str] = Field(default=None, description="Required when source is telegram")
    platform: Optional[str] = Field(default=None, description="Only this platform")
    period_date: Optional[_date] = Field(default=None, description="A date inside the incentive period the partner means")
    incentive_type: Optional[Literal["daily_target", "weekly_target", "period_target"]] = None


async def _incentive_evidence_for(req: IncentiveAuditRequest) -> IncentiveEvidence:
    if req.source == "history":
        with operation("load the incentive evidence", not_found=(ValueError,)):
            return await asyncio.to_thread(load_history_evidence, req.partner_id)
    if not req.submission_id:
        raise HTTPException(status_code=422, detail="submission_id is required when source is telegram.")
    # This read used to turn *every* exception into "Telegram submission not
    # found", so a database outage was reported to the partner as missing
    # evidence - a wrong answer, not just a vague one. Only a genuinely absent
    # row is a 404 now, and that case is the `if not stored` below.
    with operation("load the Telegram submission"):
        merged = await asyncio.to_thread(
            telegram_store.get_submission_result, req.partner_id, req.submission_id
        )
    stored = (merged or {}).get("incentive_evidence")
    if not stored:
        if (merged or {}).get("details_purged"):
            # Deleted once the partner was shown the result; only the outcome is kept.
            raise HTTPException(
                status_code=410,
                detail=("The trip details of this submission were deleted after you were shown the result. "
                        "Send the day's trips again on Telegram to check another incentive for it."),
            )
        raise HTTPException(status_code=404, detail="This Telegram submission has no incentive evidence.")
    # Stored are the normalised facts read off the documents (no raw files);
    # every result is recomputed from them.
    return IncentiveEvidence.model_validate(stored)


@server.post("/audit/incentive")
async def audit_incentive(req: IncentiveAuditRequest):
    """Why didn't I get my incentive? Then, separately, which structure fits better."""
    evidence = await _incentive_evidence_for(req)
    # The 30-day audit (records on file) compares five days; a Telegram
    # "Didn't get your incentive?" check compares only the one day sent.
    fit_days = FULL_AUDIT_FIT_DAYS if req.source == "history" else INCENTIVE_CHECK_FIT_DAYS
    with operation("run the incentive audit"):
        report = await asyncio.to_thread(
            build_incentive_report, evidence, default_store(), req.platform, req.period_date, req.incentive_type,
            fit_days,
        )
    return {"status": "Incentive Audit Complete", **_json_safe(report)}


# ---------- Synthetic Data Batch endpoint ----------
from synthetic_verification import run_full_verification, load_synthetic_data

class SyntheticDataUpload(BaseModel):
    """Raw synthetic dataset JSON files uploaded from frontend."""
    partners_json: List[Dict[str, Any]]
    settlements_json: List[Dict[str, Any]]
    earnings_json: List[Dict[str, Any]]
    ground_truth_json: List[Dict[str, Any]]
    ground_truth_layer2_json: Optional[Dict[str, Any]] = None


@server.post("/audit/synthetic")
async def synthetic_audit_endpoint(data: SyntheticDataUpload):
    """
    Run the synthetic verification on uploaded dataset files.
    This reuses the existing synthetic_verification engine directly.
    """
    import tempfile
    import os
    import json

    from synthetic_verification import build_aggregate_report

    def _verify():
        """Write the uploaded files and verify them, off the event loop.

        The whole body runs in one worker thread rather than being awaited
        piece by piece: it writes files, walks every transaction and builds the
        report, all of it blocking. Left on the event loop it would freeze the
        live earnings stream and every other request for the duration, which is
        the same fault the 30-day audit had.

        The temporary directory is created and removed inside this function so
        it cannot outlive the thread that uses it.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            files = {
                "partners.json": data.partners_json,
                "settlements.json": data.settlements_json,
                "earnings.json": data.earnings_json,
                "ground_truth.json": data.ground_truth_json,
            }
            if data.ground_truth_layer2_json:
                files["ground_truth_layer2.json"] = data.ground_truth_layer2_json
            for name, payload in files.items():
                with open(os.path.join(tmpdir, name), "w", encoding="utf-8") as f:
                    json.dump(payload, f)

            evaluations = run_full_verification(tmpdir)
            dataset = load_synthetic_data(tmpdir)
            return build_aggregate_report(evaluations, dataset)

    with operation("run the synthetic verification"):
        aggregate = await asyncio.to_thread(_verify)

    # Build aggregate response compatible with frontend

    return {
        "status": "Synthetic Audit Complete",
        "aggregate_report": {
            "total_partners": aggregate.total_partners,
            "total_transactions_processed": aggregate.total_transactions_processed,
            "overall_reconciliation_f1": aggregate.overall_reconciliation_f1,
            "overall_reconciliation_precision": aggregate.overall_reconciliation_precision,
            "overall_reconciliation_recall": aggregate.overall_reconciliation_recall,
            "total_expected_findings": aggregate.total_expected_findings,
            "total_detected_findings": aggregate.total_detected_findings,
            "total_true_positives": aggregate.total_true_positives,
            "total_false_positives": aggregate.total_false_positives,
            "total_false_negatives": aggregate.total_false_negatives,
            "findings_precision": aggregate.findings_precision,
            "findings_recall": aggregate.findings_recall,
            "findings_f1": aggregate.findings_f1,
            "contract_metrics": {
                "expected": aggregate.contract_metrics.expected,
                "detected": aggregate.contract_metrics.detected,
                "true_positives": aggregate.contract_metrics.true_positives,
                "false_positives": aggregate.contract_metrics.false_positives,
                "false_negatives": aggregate.contract_metrics.false_negatives,
                "precision": aggregate.contract_metrics.precision,
                "recall": aggregate.contract_metrics.recall,
                "f1": aggregate.contract_metrics.f1,
            },
            "legal_metrics": {
                "expected": aggregate.legal_metrics.expected,
                "detected": aggregate.legal_metrics.detected,
                "true_positives": aggregate.legal_metrics.true_positives,
                "false_positives": aggregate.legal_metrics.false_positives,
                "false_negatives": aggregate.legal_metrics.false_negatives,
                "precision": aggregate.legal_metrics.precision,
                "recall": aggregate.legal_metrics.recall,
                "f1": aggregate.legal_metrics.f1,
            },
            "economic_metrics": {
                "expected": aggregate.economic_metrics.expected,
                "detected": aggregate.economic_metrics.detected,
                "true_positives": aggregate.economic_metrics.true_positives,
                "false_positives": aggregate.economic_metrics.false_positives,
                "false_negatives": aggregate.economic_metrics.false_negatives,
                "precision": aggregate.economic_metrics.precision,
                "recall": aggregate.economic_metrics.recall,
                "f1": aggregate.economic_metrics.f1,
            },
            "reconciliation_by_type": {
                k: {
                    "anomaly_type": v.anomaly_type,
                    "expected": v.expected,
                    "detected": v.detected,
                    "true_positives": v.true_positives,
                    "false_positives": v.false_positives,
                    "false_negatives": v.false_negatives,
                    "precision": v.precision,
                    "recall": v.recall,
                    "f1": v.f1,
                }
                for k, v in aggregate.reconciliation_by_type.items()
            },
            "successful_runs": aggregate.successful_runs,
            "failed_runs": aggregate.failed_runs,
            "partners_with_errors": aggregate.partners_with_errors,
            "human_review_cases": aggregate.human_review_cases,
            "partners_needing_review": aggregate.partners_needing_review,
            "layer3_total_records": aggregate.layer3_total_records,
            "layer3_auto_resolved": aggregate.layer3_auto_resolved,
            "layer3_action_required": aggregate.layer3_action_required,
            "layer3_human_review": aggregate.layer3_human_review,
            "layer3_insufficient_evidence": aggregate.layer3_insufficient_evidence,
            "layer3_exception_rate": aggregate.layer3_exception_rate,
            "layer3_resolution_rate": aggregate.layer3_resolution_rate,
            "layer3_by_priority": aggregate.layer3_by_priority,
            "layer3_by_action": aggregate.layer3_by_action,
            "layer3_human_review_queue": aggregate.layer3_human_review_queue,
        },
        "per_partner_evaluations": [
            {
                "partner_id": e.partner_id,
                "partner_name": e.partner_name,
                "overall_reconciliation_f1": e.overall_reconciliation_f1,
                "reconciliation_metrics": {
                    k: {
                        "anomaly_type": v.anomaly_type,
                        "expected": v.expected,
                        "detected": v.detected,
                        "true_positives": v.true_positives,
                        "false_positives": v.false_positives,
                        "false_negatives": v.false_negatives,
                        "precision": v.precision,
                        "recall": v.recall,
                        "f1": v.f1,
                    }
                    for k, v in e.reconciliation_metrics.items()
                },
                "contract_compliance": {
                    "expected": e.contract_compliance.expected,
                    "detected": e.contract_compliance.detected,
                    "true_positives": e.contract_compliance.true_positives,
                    "false_positives": e.contract_compliance.false_positives,
                    "false_negatives": e.contract_compliance.false_negatives,
                    "precision": e.contract_compliance.precision,
                    "recall": e.contract_compliance.recall,
                    "f1": e.contract_compliance.f1,
                },
                "legal_risks": {
                    "expected": e.legal_risks.expected,
                    "detected": e.legal_risks.detected,
                    "true_positives": e.legal_risks.true_positives,
                    "false_positives": e.legal_risks.false_positives,
                    "false_negatives": e.legal_risks.false_negatives,
                    "precision": e.legal_risks.precision,
                    "recall": e.legal_risks.recall,
                    "f1": e.legal_risks.f1,
                },
                "economic_concerns": {
                    "expected": e.economic_concerns.expected,
                    "detected": e.economic_concerns.detected,
                    "true_positives": e.economic_concerns.true_positives,
                    "false_positives": e.economic_concerns.false_positives,
                    "false_negatives": e.economic_concerns.false_negatives,
                    "precision": e.economic_concerns.precision,
                    "recall": e.economic_concerns.recall,
                    "f1": e.economic_concerns.f1,
                },
                "engine_status": e.engine_status,
                "engine_error": e.engine_error,
                "controller_decisions_count": len(e.controller_decisions),
            }
            for e in evaluations
        ],
    }
