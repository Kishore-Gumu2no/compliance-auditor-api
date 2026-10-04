"""In this file, the main topic is not the asynchronous programming part, it is about '@', this is a decorator.

WHAT IT DOES: It signifies that the following function after using '@' is a special kind of function called DECORATOR function.

WHY USE THIS: Usually when you write a function, let's say Audit, if we gotta use that function you need to call it manually by typing
               result = Audit("Hi Hello I am Kishore"), this is okay but not productive when we don't know when we will have to call it,
               so we need a SPECIAL INSTRUCTOR MARKER (@) to mark the function SPECIAL and tell the compiler that this function is now called
               by ANOTHER PIECE OF CODE (functions) and not the programmer.

HOW DOES IT WORK: Now that th programmer has no right to call the function independently, then how will this function be called? It will
                 called by another PIECE OF CODE (functions). Example: In this file we have got ' @server.post("/audit/") ', what this
                 means is:
                    1. The function below this line, Audit, is marked as a special function, saying that it cannot be called by the
                       programmer anymore in this file.
                    2. Audit function's control of calling is handed over to server, i.e an instant of the FastAPI program, which means,
                       THE CONTROL IS BEING HANDED OVER TO ANOTHER PIECE OF CODE.
                    3. The ' .post("/audit/") ' here is the trigger, if any user visits a website such as https://www.linkedin.com and
                       clicks the "AUDIT" button, it redirects to https://www.linkedin.com/audit/ and this is when our async Audit function
                       gets triggered or called.
"""

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, WebSocket, UploadFile, File, WebSocketDisconnect, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError, Field
from orchestrator import run_audit
from database import supabase_db
from models import PartnerProfile, ContractVersion
from audit_context import serialize_audit_state, debate_state_from_audit_context
from real_data import load_partner_history_from_db
import json
import asyncio
import hashlib
import os
from typing import List, Annotated, Set, Dict, Any, Optional

from debate import run_debate

class CounterfactualScenario(BaseModel):
    """Payload for a what-if scenario."""
    platform: str = Field(..., description="Partner/platform name")
    description: str = Field(..., description="Natural-language description of the scenario")
    audit_context: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Sanitized audit context from a completed /audit/ response",
    )


class CounterfactualResult(BaseModel):
    """Combined output of the three personas."""
    synthesis: str = Field(..., description="Combined narrative")
    realist: str = Field(..., description="Realist analysis")
    optimist: str = Field(..., description="Optimist analysis")
    pessimist: str = Field(..., description="Pessimist analysis")


def _parse_partner_profile(raw: str) -> PartnerProfile:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="partner_profile must be valid JSON.") from exc
    try:
        return PartnerProfile.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


def _parse_contract_versions(raw: str) -> List[ContractVersion]:
    if not raw or raw.strip() in ("", "[]"):
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="contract_versions must be valid JSON.") from exc
    if not isinstance(payload, list):
        raise HTTPException(status_code=422, detail="contract_versions must be a JSON array.")
    try:
        return [ContractVersion.model_validate(item) for item in payload]
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


async def run_delivery_streams():
    """Background delivery platform simulators for the live earnings stream."""
    await asyncio.sleep(2)
    await asyncio.gather(
        Zomato(),
        Swiggy(),
        UberEats(),
    )


import websockets


async def Zomato():
        Zomato_earnings = 4000
        while True:
            try:
                async with websockets.connect("ws://127.0.0.1:8000/stream") as ws:
                    payload = {"platform": "Zomato", "earnings": Zomato_earnings}
                    await ws.send(json.dumps(payload))
                    response_Zomato = await ws.recv() #This line is important because it tells the client to receive the data in JSON format from the server
                    print(f"Zomato received: {response_Zomato}")
                    Zomato_earnings += 100
                    await asyncio.sleep(5)
            except Exception as e:
                print(f"Zomato connection failed, retrying in 5s... Error: {e}")
                await asyncio.sleep(5)

async def Swiggy():
    async with websockets.connect("ws://127.0.0.1:8000/stream") as ws:
        Swiggy_earnings = 3500
        while True:
            try:
                payload = {"platform": "Swiggy", "earnings": Swiggy_earnings}
                await ws.send(json.dumps(payload))
                response_Swiggy = await ws.recv() #This line is important because it tells the client to receive the data in JSON format from the server
                print(f"Swiggy received: {response_Swiggy}")
                Swiggy_earnings += 100
                await asyncio.sleep(13)
            except Exception as e:
                print(f"Swiggy connection failed, retrying in 5s... Error: {e}")
                await asyncio.sleep(5)

async def UberEats():
    async with websockets.connect("ws://127.0.0.1:8000/stream") as ws:
        Uber_earnings = 1700
        while True:
            try:
                payload = {"platform": "UberEats", "earnings": Uber_earnings}
                await ws.send(json.dumps(payload))
                response_Uber = await ws.recv() #This line is important because it tells the server to receive the data in JSON format from the client
                print(f"Uber received: {response_Uber}")
                Uber_earnings += 100
                await asyncio.sleep(18)
            except Exception as e:
                print(f"UberEats connection failed, retrying in 5s... Error: {e}")
                await asyncio.sleep(5)

async def main():
            #region asyncio.gather explanation
            """This 'asyncio.gather' is real important because this function is the reason why all the three async functions are sent to the event loop at the same time, and
            the why they are running in parallel, if we didn't use this function these three async functions would have been executed one after the other, which is not what we want.
            When we put 'await' in front of 'asyncio.gather' this happens:
            1. Send three async functions to the event loop at the same time.
            2. Wait for all three async functions to complete and stops reading the code below this line until all three async functions are completed.
            3. Once all three async functions are completed, it continues reading the code below this line."""
            #endregion
            await asyncio.sleep(2)
            await asyncio.gather(
                Zomato(),
                Swiggy(),
                UberEats()
            )

#region Important note about asyncio.run(main())
"""This line is important because it tells the client to run the main function, which is the entry point of the program, and it is also important because it tells
 the client to run the main function in an event loop, which is a loop that runs forever and keeps the program running until it is stopped by the user. This is like the
 master ignition switch of the program, it provisions the event loop in the operating system and runs until the program is stopped by the user."""
#endregion

async def lifespan(app: FastAPI):
    """Special function called by FastAPI on startup/shutdown to start/stop background tasks."""
    task = asyncio.create_task(run_delivery_streams())
    yield


server = FastAPI(lifespan=lifespan)
server.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_methods=["*"],
    allow_headers=["*"],
)
""" #This line is important because it tells the FastAPI framework to use the lifespan function when the server starts and stops, and it is used to perform any setup or teardown tasks that
need to be done when the server starts or stops, so that we can start the async functions when the server starts and stop them when the server stops."""

# Connection manager for broadcasting to all connected frontend clients
class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: str):
        """Broadcast message to all connected clients"""
        for connection in self.active_connections.copy():
            try:
                await connection.send_text(message)
            except Exception:
                # Connection might be closed, remove it
                self.active_connections.discard(connection)


# Global connection manager for the earnings stream
manager = ConnectionManager()


class StreamData(BaseModel):
    platform: str
    earnings: int

@server.post("/audit/")
async def Audit(
    partner_profile: Annotated[str, Form(description="JSON PartnerProfile payload")],
    platform: Annotated[str, Form(description="Primary platform for document extraction hints")],
    contract_versions: Annotated[str, Form(description="JSON array of ContractVersion objects")] = "[]",
    images: Annotated[list[UploadFile] | None, File()] = None,
    pdfs: Annotated[list[UploadFile] | None, File()] = None,
):
    """Upload images/PDFs with explicit partner context and run the real audit pipeline."""
    if images is None:
        images = []
    if pdfs is None:
        pdfs = []
    if not images and not pdfs:
        raise HTTPException(status_code=400, detail="At least one PDF or image is required.")
    if len(images) > 20:
        raise HTTPException(status_code=400, detail="Too many images !!")
    if len(pdfs) > 5:
        raise HTTPException(status_code=400, detail="Too many PDFs !!")

    profile = _parse_partner_profile(partner_profile)
    versions = _parse_contract_versions(contract_versions)

    saved_paths: list[str] = []
    saved_pdf_paths: list[str] = []
    saved_image_paths: list[str] = []
    try:
        for pdf in pdfs:
            file_bytes = await pdf.read()
            hasher_pdf = hashlib.sha256()
            hasher_pdf.update(file_bytes)
            file_hash = hasher_pdf.hexdigest()
            path = f"temp_{file_hash}.pdf"
            with open(path, "wb") as pdf_obj:
                pdf_obj.write(file_bytes)
            saved_paths.append(path)
            saved_pdf_paths.append(path)

        for image in images:
            image_bytes = await image.read()
            hasher_img = hashlib.sha256()
            hasher_img.update(image_bytes)
            img_hash = hasher_img.hexdigest()
            path = f"temp_{img_hash}.png"
            with open(path, "wb") as image_obj:
                image_obj.write(image_bytes)
            saved_paths.append(path)
            saved_image_paths.append(path)

        ai_result = run_audit(
            partner_id=profile.partner_id,
            partner_profile=profile,
            pdf_paths=saved_pdf_paths,
            image_paths=saved_image_paths,
            platform=platform,
            contract_versions=versions,
        )
    finally:
        for path in saved_paths:
            if os.path.exists(path):
                os.remove(path)

    serialized = serialize_audit_state(ai_result)
    if serialized.get("status") == "error":
        raise HTTPException(status_code=422, detail=serialized.get("error") or "Audit failed.")

    return {
        "status": "Audit Complete",
        "ai_analysis": serialized,
    }

@server.websocket("/stream")
async def ingest_stream(websocket: WebSocket):
    """
    WebSocket endpoint that:
    1. Accepts earnings data from platform simulators (Zomato, Swiggy, Uber Eats)
    2. Stores to database
    3. Broadcasts to all connected frontend clients (Bobby's dashboard)
    """
    await manager.connect(websocket)
    try:
        while True:
            try:
                data = await websocket.receive_json()
                secure_data = StreamData(**data)
                print(f"STREAM RECEIVE: Logged {secure_data.earnings} from {secure_data.platform}")

                # Store to database
                try:
                    supabase_db.table("earnings").insert({
                        "platform": secure_data.platform,
                        "amount": secure_data.earnings
                    }).execute()
                except Exception as e:
                    print(f"Database error: {e}")

                # Broadcast to all connected frontend clients (Bobby's dashboard)
                broadcast_message = json.dumps({
                    "platform": secure_data.platform,
                    "earnings": secure_data.earnings
                })
                await manager.broadcast(broadcast_message)

                # Send confirmation back to the sender
                await websocket.send_text(f"Logged earnings of {secure_data.earnings} from {secure_data.platform}")
            except WebSocketDisconnect:
                break
            except ValidationError as e:
                await websocket.send_text("Invalid Data Format received")
                continue
    finally:
        manager.disconnect(websocket)

# ---------- Counter‑factual endpoints ----------

@server.post("/counterfactual/simulate", response_model=CounterfactualResult)
async def simulate_counterfactual(scenario: CounterfactualScenario) -> CounterfactualResult:
    """Run a single counter‑factual debate for the supplied scenario using structured audit context."""
    if scenario.audit_context is None:
        raise HTTPException(status_code=422, detail="audit_context is required. Provide the counterfactual_context from a completed /audit/ response.")

    initial_state = debate_state_from_audit_context(
        audit_context=scenario.audit_context,
        platform=scenario.platform,
        description=scenario.description,
    )
    final_state = await run_debate(initial_state)
    return CounterfactualResult(
        synthesis=final_state.get("synthesis", ""),
        realist=final_state.get("realist", ""),
        optimist=final_state.get("optimist", ""),
        pessimist=final_state.get("pessimist", ""),
    )

# ---------- Earnings Opportunity Simulator ----------
from decimal import Decimal

from earnings_opportunity import (
    SCENARIO_TYPES,
    compute_opportunity,
    debate_state_from_opportunity,
    fetch_simulations,
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


def _baseline_or_error(partner_id: str, submission_id: Optional[str]) -> Dict[str, Any]:
    """The 30-day history baseline, or one built from a Telegram audit."""
    import telegram_store

    try:
        if submission_id:
            merged = telegram_store.get_submission_result(partner_id, submission_id)
            if merged is None:
                raise HTTPException(
                    status_code=404, detail="Telegram audit not found for this partner."
                )
            return load_telegram_baseline(partner_id, submission_id, merged)
        return load_earnings_baseline(partner_id)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load earnings baseline: {exc}") from exc


def _projection_or_422(
    partner_id: str, scenario_type: str, value: Decimal, submission_id: Optional[str] = None
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Load the baseline and run the deterministic calculation, or fail loudly."""
    if scenario_type not in SCENARIO_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"scenario_type must be one of {', '.join(SCENARIO_TYPES)}.",
        )
    baseline = _baseline_or_error(partner_id, submission_id)
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
    baseline = _baseline_or_error(partner_id, submission_id)
    return EarningsBaseline(**{k: v for k, v in baseline.items() if k in EarningsBaseline.model_fields})


@server.post("/earnings-opportunity/simulate", response_model=OpportunityResult)
async def simulate_earnings_opportunity(req: OpportunityRequest) -> OpportunityResult:
    """Project one scenario deterministically, then have the personas assess it."""
    projection, baseline = _projection_or_422(
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
    try:
        if not partner_exists(req.partner_id):
            raise HTTPException(status_code=404, detail=f"Partner {req.partner_id} not found.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to validate partner: {exc}") from exc

    projection, baseline = _projection_or_422(
        req.partner_id, req.scenario_type, req.value, req.submission_id
    )
    # Record which evidence the figures came from, for the History view.
    projection["scenario_inputs"]["source"] = baseline.get("source", "history")
    if baseline.get("submission_id"):
        projection["scenario_inputs"]["submission_id"] = baseline["submission_id"]

    try:
        saved = insert_simulation(projection)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save simulation: {exc}") from exc
    return SavedSimulation(**saved)


@server.get("/simulations/{partner_id}", response_model=List[SavedSimulation])
async def list_simulations(partner_id: str) -> List[SavedSimulation]:
    """Return only this partner's saved simulations, newest first."""
    try:
        rows = fetch_simulations(partner_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to load simulations: {exc}") from exc
    return [SavedSimulation(**row) for row in rows]


# ---------- Telegram submissions (read-only for the website) ----------
# The bot writes these rows; the website polls them so results and completion
# notices appear here. The frozen WebSocket stream is untouched.
import telegram_store


@server.get("/telegram/submissions/{partner_id}")
async def list_telegram_submissions(partner_id: str):
    """Summaries of this partner's Telegram submissions, newest first."""
    try:
        return telegram_store.list_submissions(partner_id)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to load Telegram submissions: {exc}"
        ) from exc


@server.get("/telegram/submissions/{partner_id}/latest")
async def latest_telegram_submission(partner_id: str):
    """The newest completed Telegram audit for this partner, with its results.

    ``ai_analysis`` is the merged output of the existing pipeline, in the same
    shape the website already renders for uploads.
    """
    try:
        latest = telegram_store.get_latest_complete(partner_id)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to load the Telegram audit: {exc}"
        ) from exc
    if latest is None:
        return {"status": "none", "submission": None, "ai_analysis": None}

    # Audits saved before the simulator wiring lack this; derive it from the
    # stored per-platform results so they unlock "How to Earn More" too.
    merged = latest.get("merged_result")
    if merged and not merged.get("counterfactual_context"):
        from telegram_audit import build_merged_counterfactual_context
        merged["counterfactual_context"] = build_merged_counterfactual_context(
            latest.get("results_by_platform") or {}, merged
        )
    if merged and merged.get("counterfactual_context") is not None:
        # Lets "Earn More" simulate from this Telegram audit's own documents.
        merged["counterfactual_context"]["submission_id"] = latest["submission_id"]
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
    try:
        updated = telegram_store.mark_seen(req.submission_id, req.partner_id)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to update the submission: {exc}"
        ) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Submission not found for this partner.")
    return {"status": "ok"}

# ---------- Partner History Audit endpoint (Milestone 2 CTA) ----------

@server.get("/audit/history/{partner_id}")
async def audit_partner_history(partner_id: str):
    """
    Query partner's 30-day history from Supabase and run full audit pipeline.
    Used by the 'Discover how you can earn more' CTA.
    """
    try:
        # Load data from Supabase
        db_data = load_partner_history_from_db(partner_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load partner history: {e}")

    # Run audit pipeline with DB-sourced data (no PDF/images needed)
    ai_result = run_audit(
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

    return {
        "status": "Audit Complete",
        "ai_analysis": serialized,
    }


# ---------- Batch audit endpoint ----------
from batch_audit import run_batch_audit

class BatchPartnerSpec(BaseModel):
    partner_id: str
    partner_profile: Dict[str, Any]
    pdf_paths: List[str] = []
    image_paths: List[str] = []
    platform: str = "unknown"
    contract_versions: List[Any] = []

@server.post("/audit/batch")
async def batch_audit_endpoint(specs: List[BatchPartnerSpec]):
    """Run audits for many partners concurrently and return their final states."""
    # Convert Pydantic models to plain dicts for the batch runner.
    partner_specs = [spec.dict() for spec in specs]
    results = await run_batch_audit(partner_specs)
    return results


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

    # Write uploaded JSON to temp directory to reuse existing load_synthetic_data
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write the uploaded JSON files
        with open(os.path.join(tmpdir, "partners.json"), "w") as f:
            json.dump(data.partners_json, f)
        with open(os.path.join(tmpdir, "settlements.json"), "w") as f:
            json.dump(data.settlements_json, f)
        with open(os.path.join(tmpdir, "earnings.json"), "w") as f:
            json.dump(data.earnings_json, f)
        with open(os.path.join(tmpdir, "ground_truth.json"), "w") as f:
            json.dump(data.ground_truth_json, f)
        if data.ground_truth_layer2_json:
            with open(os.path.join(tmpdir, "ground_truth_layer2.json"), "w") as f:
                json.dump(data.ground_truth_layer2_json, f)

        # Run the existing verification engine
        evaluations = run_full_verification(tmpdir)
        from synthetic_verification import build_aggregate_report
        dataset = load_synthetic_data(tmpdir)
        aggregate = build_aggregate_report(evaluations, dataset)

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
