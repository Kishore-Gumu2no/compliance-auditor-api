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

from fastapi import FastAPI, HTTPException, WebSocket, UploadFile, File, WebSocketDisconnect, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError, Field
from orchestrator import app, run_audit
from database import supabase_db
from models import PartnerProfile, ContractVersion
from audit_context import serialize_audit_state, debate_state_from_audit_context
import json
import asyncio
import hashlib
import os
from typing import List, Annotated, Set, Dict, Any, Optional

from debate import run_debate, DebateState

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
    allow_origins=["http://localhost:5174"],
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

class CounterfactualSliderRequest(BaseModel):
    platform: str
    adjusted_value: float = Field(..., description="New numeric value the user set (e.g. incentive amount)")
    audit_context: Dict[str, Any] = Field(..., description="Sanitized audit context from a completed /audit/ response")

@server.post("/counterfactual/slider", response_model=CounterfactualResult)
async def slider_counterfactual(req: CounterfactualSliderRequest) -> CounterfactualResult:
    """Re‑run the debate with a modified numeric input using structured audit context."""
    initial_state = debate_state_from_audit_context(
        audit_context=req.audit_context,
        platform=req.platform,
        description=f"Adjusted value: {req.adjusted_value}",
    )
    final_state = await run_debate(initial_state)
    return CounterfactualResult(
        synthesis=final_state.get("synthesis", ""),
        realist=final_state.get("realist", ""),
        optimist=final_state.get("optimist", ""),
        pessimist=final_state.get("pessimist", ""),
    )

class CounterfactualHistoryItem(BaseModel):
    partner_id: str
    timestamp: str
    synthesis: str

@server.get("/counterfactual/history/{partner_id}", response_model=List[CounterfactualHistoryItem])
async def get_counterfactual_history(partner_id: str) -> List[CounterfactualHistoryItem]:
    """Return placeholder history items for a partner."""
    dummy = [
        CounterfactualHistoryItem(
            partner_id=partner_id,
            timestamp="2026-08-28T14:22:00Z",
            synthesis="Previous synthesis for demo purposes."
        ),
        CounterfactualHistoryItem(
            partner_id=partner_id,
            timestamp="2026-08-30T09:10:00Z",
            synthesis="Another past synthesis."
        ),
    ]
    return dummy

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
from synthetic_verification import run_full_verification, SyntheticDataset, load_synthetic_data

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
