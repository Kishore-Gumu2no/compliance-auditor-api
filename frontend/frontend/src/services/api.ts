// src/services/api.ts
// -------------------------------------------------
// Core FastAPI service layer (audit & streaming)
// -------------------------------------------------

// Base URL – picks up VITE_API_BASE_URL from .env, defaults to local dev server
const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

// ---------------------------------------------------------------------
// Types for the main audit endpoint
// ---------------------------------------------------------------------
export interface AuditResponse {
  status: string;
  ai_analysis: {
    status: string;
    error: string | null;
    partner_id: string;
    platform: string;
    audit_date: string;
    evidence_gaps: string[];
    reconciliation_predictions: ReconciliationPrediction[];
    transactions: TransactionRecord[];
    payment_calculations: PaymentCalculation[];
    business_cost_calculations: BusinessCostCalculation[];
    controller_decisions: ControllerDecision[];
    batch_summary: BatchSummary | null;
    overall_grade: string;
    summary: string;
    audit_findings?: {
      contract_compliance_findings: ContractComplianceFinding[];
      legal_risk_findings: LegalRiskFinding[];
      economic_concern_findings: EconomicConcernFinding[];
      overall_grade: string | null;
      summary: string;
      evidence_gaps: string[];
    };
    counterfactual_context?: Record<string, any>;
  };
}

export interface ContractComplianceFinding {
  finding_id: string;
  grade: string;
  transaction_id: string;
  contract_clause: string;
  expected_amount: number | string;
  actual_amount: number | string;
  variance: number | string;
  evidence_strength: number | string;
  notes: string;
  severity?: string;
  human_review_required?: boolean;
}

export interface LegalRiskFinding {
  finding_id: string;
  grade: string;
  transaction_id: string;
  legal_basis: {
    statute: string;
    section: string;
    jurisdiction: string;
    effective_date: string;
    applicability_note?: string;
  };
  classification_probability: number | string;
  applicable_law_version: string;
  risk_if_employee: string;
  risk_if_contractor: string;
  evidence_strength: number | string;
  notes: string;
  severity?: string;
  human_review_required?: boolean;
}

export interface EconomicConcernFinding {
  finding_id: string;
  grade: string;
  transaction_id: string;
  business_hourly_rate: number | string;
  contracted_hourly_benchmark: number | string;
  market_hourly_benchmark: number | string;
  min_wage_hourly_benchmark: number | string;
  gap_vs_contracted_pct: number | string;
  gap_vs_market_pct: number | string;
  gap_vs_min_wage_pct: number | string;
  severity: string;
  notes: string;
  human_review_required?: boolean;
}

export interface ReconciliationPrediction {
  transaction_id: string;
  earning_id?: string;
  order_id?: string;
  anomaly_type: string;
  expected_amount: number | string;
  settlement_amount: number | string;
  variance: number | string;
}

export interface TransactionRecord {
  transaction_id: string;
  platform: string;
  transaction_date: string;
  gross_amount: number | string;
  deductions: { name: string; amount: number | string; category: string }[];
  incentives: { name: string; amount: number | string }[];
  net_amount: number | string;
}

export interface PaymentCalculation {
  platform_net: number | string;
  contractual_net: number | string;
  variance: number | string;
  variance_pct: number | string;
  breakdown: Record<string, number | string>;
}

export interface ControllerDecision {
  transaction_id: string;
  resolution_status: string;
  priority: string;
  recommended_action: string;
  human_review_required: boolean;
  reason: string;
}

export interface BusinessCostCalculation {
  petrol_cost: number | string;
  business_hourly_rate: number | string;
  active_hourly_rate?: number | string;
  online_hourly_rate?: number | string;
}

export interface BatchSummary {
  records_processed: number;
  automatically_resolved: number;
  action_required: number;
  human_review: number;
  insufficient_evidence: number;
  exception_rate: number;
  resolution_rate: number;
  by_priority: Record<string, number>;
  by_action: Record<string, number>;
}

// ---------------------------------------------------------------------
// WebSocket earnings stream – GET /stream
// ---------------------------------------------------------------------
export interface StreamMessage {
  type: "earnings" | "error" | "connected";
  platform?: string;
  earnings?: number;
  message?: string;
}

export type StreamCallback = (message: StreamMessage) => void;

export function createEarningsStream(callback: StreamCallback, partnerId?: string): WebSocket {
  const wsUrl = `ws://127.0.0.1:8000/stream${partnerId ? `?partner_id=${partnerId}` : ''}`;
  const ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    callback({ type: "connected" });
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.platform && typeof data.earnings === "number") {
        callback({
          type: "earnings",
          platform: data.platform,
          earnings: data.earnings,
        });
      } else {
        // Server may send plain‑text confirmations
        callback({ type: "earnings", message: event.data });
      }
    } catch {
      callback({ type: "earnings", message: event.data });
    }
  };

  ws.onerror = () => {
    callback({ type: "error", message: "WebSocket connection error" });
  };

  ws.onclose = () => {
    callback({ type: "error", message: "Connection closed. Reconnecting..." });
    // Auto‑reconnect after a short delay
    setTimeout(() => {
      createEarningsStream(callback, partnerId);
    }, 3000);
  };

  return ws;
}

// -------------------------------------------------
// Counter‑factual API helpers (added later)
// -------------------------------------------------

// 1️⃣ Scenario request
export interface CounterfactualScenario {
  platform: string;
  description: string;
  audit_context: Record<string, any>;
}

// 2️⃣ Result shape returned by the three personas
export interface CounterfactualResult {
  synthesis: string;
  realist: string;
  optimist: string;
  pessimist: string;
}

// POST /counterfactual/simulate
export async function simulate_counterfactual(
  scenario: CounterfactualScenario
): Promise<CounterfactualResult> {
  const response = await fetch(`${API_BASE_URL}/counterfactual/simulate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(scenario),
  });

  if (!response.ok) {
    const err = await response
      .json()
      .catch(() => ({ detail: "Simulation failed" }));
    throw new Error(err.detail || "Failed to run counterfactual simulation");
  }
  return response.json();
}

// -------------------------------------------------
// Earnings Opportunity Simulator
// -------------------------------------------------

export type ScenarioType = "hours" | "days" | "expenses";

// The partner's own 30-day numbers, used to pre-fill the simulator inputs.
// The partner's own numbers, from the 30-day history or a Telegram audit.
// When simulation_possible is false, the figures are null and
// unavailable_message explains why.
export interface EarningsBaseline {
  partner_id: string;
  source: "history" | "telegram";
  submission_id: string | null;
  simulation_possible: boolean;
  unavailable_message: string | null;
  days_with_earnings: number | null;
  max_working_days: number | null;
  expenses_available: boolean;
  expenses_unavailable_reason: string | null;
  baseline_notes: string[];
  period_start: string | null;
  period_end: string | null;
  days_worked: number | null;
  total_orders: number | null;
  total_income: number | string | null;
  total_operating_cost: number | string | null;
  net_after_expenses: number | string | null;
  total_online_hours: number | string | null;
  avg_income_per_day: number | string | null;
  avg_hours_per_day: number | string | null;
  income_per_hour: number | string | null;
  lowest_daily_hourly_rate: number | string | null;
  highest_daily_hourly_rate: number | string | null;
}

export interface ScenarioAssumptions {
  basis: string;
  notes: string[];
}

export interface OpportunityResult {
  partner_id: string;
  scenario_type: ScenarioType;
  scenario_inputs: Record<string, number>;
  baseline_earnings: number | string;
  projected_earnings: number | string;
  estimated_difference: number | string;
  conservative_estimate?: number | string | null;
  assumptions: ScenarioAssumptions;
  synthesis: string;
  realist: string;
  optimist: string;
  pessimist: string;
}

export interface SavedSimulation {
  simulation_id: string;
  partner_id: string;
  scenario_type: ScenarioType;
  scenario_inputs: Record<string, number>;
  baseline_earnings: number | string;
  projected_earnings: number | string;
  estimated_difference: number | string;
  assumptions: ScenarioAssumptions;
  created_at: string;
}

async function parseOrThrow(response: Response, fallback: string) {
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: fallback }));
    const detail = err.detail;
    throw new Error(
      typeof detail === "string" ? detail : fallback
    );
  }
  return response.json();
}

// GET /earnings-opportunity/baseline/{partner_id}
export async function get_earnings_baseline(
  partner_id: string,
  submission_id?: string | null
): Promise<EarningsBaseline> {
  const query = submission_id ? `?submission_id=${encodeURIComponent(submission_id)}` : "";
  const response = await fetch(
    `${API_BASE_URL}/earnings-opportunity/baseline/${partner_id}${query}`
  );
  return parseOrThrow(response, "Failed to load your earnings baseline");
}

// POST /earnings-opportunity/simulate
export async function simulate_earnings_opportunity(req: {
  partner_id: string;
  scenario_type: ScenarioType;
  value: number;
  submission_id?: string | null;
}): Promise<OpportunityResult> {
  const response = await fetch(`${API_BASE_URL}/earnings-opportunity/simulate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  return parseOrThrow(response, "Failed to run the simulation");
}

// POST /simulations - persist a completed simulation
export async function save_simulation(req: {
  partner_id: string;
  scenario_type: ScenarioType;
  value: number;
  submission_id?: string | null;
}): Promise<SavedSimulation> {
  const response = await fetch(`${API_BASE_URL}/simulations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  return parseOrThrow(response, "Failed to save the simulation");
}

// GET /simulations/{partner_id} - saved history for this partner only
export async function get_simulations(
  partner_id: string
): Promise<SavedSimulation[]> {
  const response = await fetch(`${API_BASE_URL}/simulations/${partner_id}`);
  return parseOrThrow(response, "Failed to load your saved simulations");
}

// -------------------------------------------------
// Telegram evidence submissions (read-only from the website)
// -------------------------------------------------

export interface TelegramSubmissionSummary {
  submission_id: string;
  partner_id: string;
  chat_id: number;
  status: "processing" | "complete" | "failed";
  platforms: string[];
  document_counts: Record<string, { images: number; pdfs: number }>;
  missing_fields: { field: string; records_affected: number; status: string }[];
  error: string | null;
  created_at: string;
  completed_at: string | null;
  seen_on_website: boolean;
}

export interface TelegramLatestResponse {
  status: string;
  submission: TelegramSubmissionSummary | null;
  ai_analysis: Record<string, any> | null;
  results_by_platform?: Record<string, any> | null;
}

// GET /telegram/submissions/{partner_id}/latest
export async function get_latest_telegram_audit(
  partner_id: string
): Promise<TelegramLatestResponse> {
  const response = await fetch(
    `${API_BASE_URL}/telegram/submissions/${partner_id}/latest`
  );
  return parseOrThrow(response, "Failed to load the Telegram audit");
}

// POST /telegram/submissions/seen
export async function mark_telegram_audit_seen(
  partner_id: string,
  submission_id: string
): Promise<{ status: string }> {
  const response = await fetch(`${API_BASE_URL}/telegram/submissions/seen`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ partner_id, submission_id }),
  });
  return parseOrThrow(response, "Failed to update the submission");
}

// POST /audit/history/{partner_id} - Milestone 2 CTA
export async function auditPartnerHistory(
  partner_id: string
): Promise<AuditResponse> {
  const response = await fetch(`${API_BASE_URL}/audit/history/${partner_id}`, {
    method: "GET",
    headers: { "Content-Type": "application/json" },
  });

  if (!response.ok) {
    const err = await response
      .json()
      .catch(() => ({ detail: "History audit failed" }));
    throw new Error(err.detail || "Failed to run history audit");
  }
  return response.json();
}
