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
    // Which kind of evidence this result rests on. Results from different
    // modes are different claims and are never combined.
    evidence_mode?: EvidenceMode;
    // How FairPay chose to check each app's files (Telegram decides this itself).
    evidence_modes?: Record<string, EvidenceMode>;
    evidence_mode_label?: string;
    evidence_source?: string;
    settlement_reconciliation?: SettlementReconciliation[];
    spot_check?: SpotCheck[];
    contract_questions?: ContractQuestion[];
    could_not_check?: EvidenceLimit[];
    mode_notes?: string[];
    hours_worked?: HoursWorked;
  };
}

export type EvidenceMode = "settlement_reconciliation" | "contract_spot_check" | "full_audit";

/** Something FairPay could not check for this audit, and why. */
export interface EvidenceLimit {
  item: string;
  reason: string;
  platform: string | null;
}

/** A question built from a contract term, what was observed and what is missing. */
export interface ContractQuestion {
  topic: string;
  platform: string;
  contract_term: string;
  observed: string;
  not_shown: string;
  question: string;
  order_ids: string[];
  status: string;
}

export interface SettlementCredit {
  date: string | null;
  amount: string | number | null;
  payer_label: string | null;
  reference: string | null;
  source: string | null;
  reason?: string;
}

/** Mode 1: platform-declared total vs money received. Not a contract claim. */
export interface SettlementReconciliation {
  platform: string;
  status: "MATCH" | "SHORTFALL" | "AMBIGUOUS" | "INSUFFICIENT_EVIDENCE";
  statement: string;
  declared_total: string | null;
  declared_label: string | null;
  declared_basis: "payout" | "not_confirmed_as_payout" | null;
  received_total: string | null;
  difference: string | null;
  comparability: "comparable" | "not_established" | "not_assessed";
  period_start: string | null;
  period_end: string | null;
  credit_window_end: string | null;
  order_count: number | null;
  credits_counted: SettlementCredit[];
  credits_excluded: SettlementCredit[];
  adjustments_shown: { label: string; amount: string | null; kind: string; period?: string }[];
  missing_evidence: string[];
  assumptions: string[];
  notes: string[];
  evidence_sources: string[];
}

export interface SpotCheckOrder {
  order_id: string;
  platform: string;
  date: string | null;
  time: string | null;
  printed_amount: string | null;
  status: "possible_issue" | "no_issue_found" | "not_checked";
  findings: string[];
  not_checked: string[];
  payment_check: { result: string; label: string; issue: boolean } | null;
  contract_check: { explanation: string; issue: boolean; possible_shortfall: string | null } | null;
}

/** Mode 2: bounded to the orders supplied - never extrapolated. */
export interface SpotCheck {
  platform: string;
  orders_received: number;
  sample_orders_checked: number;
  orders_with_findings: number;
  orders_without_findings: number;
  orders_not_checked: number;
  statement: string;
  sample_boundary: string;
  contracts_used: { version: string; reference: string }[];
  orders: SpotCheckOrder[];
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
  min_wage_hourly_benchmark: number | string | null;
  gap_vs_contracted_pct: number | string;
  gap_vs_market_pct: number | string;
  gap_vs_min_wage_pct: number | string | null;
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
  // One simple sentence: what the scenario does to earnings, and why.
  synthesis: string;
  realist: string;
  optimist: string;
  pessimist: string;
  direction?: "increase" | "decrease" | "no_change" | "unclear" | null;
  estimated_change?: number | string | null;
  calculation?: string | null;
}

/** Hours worked per app, and what is not available. */
export interface HoursWorkedPlatform {
  platform: string;
  online_hours: string | null;
  online_hours_note: string;
  order_hours: string | null;
  order_hours_note: string;
  days: number;
}

export interface HoursWorked {
  source?: "history" | "telegram";
  platforms?: HoursWorkedPlatform[];
  all_apps_online_hours?: string | null;
  all_apps_online_days?: number;
  all_apps_note?: string;
  error?: string;
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
  // What would make a refused simulation possible.
  evidence_needed?: string[];
  // Where each baseline value came from.
  value_sources?: { label: string; kind: "observed" | "calculated" | "assumption" | "unavailable"; detail: string }[];
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

// ---------------------------------------------------------------------
// Incentive check: "Why didn't I get my incentive?" (POST /audit/incentive)
// Kept separate from the full audit result above. Every count and condition
// is computed by the backend; nothing here recalculates it.
// ---------------------------------------------------------------------
export type IncentiveStatus =
  | "INCENTIVE_RECEIVED"
  | "COUNTING_MISMATCH"
  | "ELIGIBILITY_FAILURE"
  | "PAYMENT_NOT_OBSERVED"
  | "POSSIBLE_PAYMENT_FAILURE"
  | "INSUFFICIENT_EVIDENCE"
  | "AMBIGUOUS";

export type Provenance = "OBSERVED" | "CALCULATED" | "RETRIEVED_RULE" | "INFERRED" | "UNKNOWN";

export interface IncentiveCondition {
  condition: string;
  label: string;
  required: string;
  observed: string;
  status: "PASS" | "FAIL" | "UNKNOWN";
  provenance: Provenance;
  detail: string;
}

export interface IncentiveTransaction {
  order_id: string;
  when: string | null;
  status: string;
  counts: "yes" | "no" | "uncertain";
  reason: string;
  provenance: Provenance;
  flagged: boolean;
}

export interface IncentivePrimary {
  question: string;
  status: IncentiveStatus;
  status_title: string;
  headline: string;
  platform?: string;
  period_start?: string;
  period_end?: string;
  period_label?: string;
  near_miss?: boolean;
  shortfall?: number | null;
  incentive?: {
    name: string;
    type: string;
    target_orders: number;
    reward_amount: string;
    source_reference: string;
    source_type: string;
    version: string;
  } | null;
  what_it_required?: string[];
  what_fairpay_found?: string[];
  conditions?: { passed: IncentiveCondition[]; failed: IncentiveCondition[]; unknown: IncentiveCondition[] };
  counting?: {
    min_count: number;
    max_count: number;
    platform_count: number | null;
    open_questions: string[];
    trips_complete: boolean;
  } | null;
  counting_mismatch?: {
    platform_count: number;
    fairpay_min: number;
    fairpay_max: number;
    difference: number;
    basis: string;
    orders: string[];
    explanation: string;
    decisive: boolean;
  } | null;
  relevant_transactions?: IncentiveTransaction[];
  payment?: { status: string; amount?: string | number | null; detail?: string } | null;
  next_action: string;
  evidence_requests?: string[];
  evidence_trail?: { fact: string; value: string; provenance: Provenance; source: string }[];
  conflicts?: string[];
  notes?: string[];
  selection_note?: string;
}

export interface IncentivePeriodRow {
  platform: string;
  period_start: string;
  period_end: string;
  period_label: string;
  incentive_type: string;
  incentive_name: string;
  status: IncentiveStatus;
  status_title: string;
  target_orders: number | null;
  orders_counted: string | null;
  near_miss: boolean;
}

export interface IncentiveAuditResult {
  kind: "incentive_audit";
  partner_id: string;
  evidence_source: string;
  primary: IncentivePrimary;
  periods_checked: number;
  outcome_counts: Record<string, number>;
  other_periods: IncentivePeriodRow[];
  limitations: string[];
}

export interface IncentiveFitPlatform {
  platform: string;
  periods_compared: number;
  target_met_min: number;
  target_met_max: number;
  share_min: string;
  share_max: string;
  target_orders: number[];
  reward_amounts?: string[];
  reached?: boolean;
  shortfall?: number;
  reward_text: string;
  time_windows: string[];
  conditions_not_checked: string[];
  rule_versions: string[];
  is_audited_platform: boolean;
}

export interface IncentiveFitResult {
  kind: "incentive_fit";
  question: string;
  status: "FIT_FOUND" | "NO_CLEAR_DIFFERENCE" | "INSUFFICIENT_DATA";
  headline: string;
  recommended_platform?: string | null;
  audited_platform: string;
  comparison_unit: string;
  comparison_days?: string[];
  why: string[];
  assumptions: string[];
  platforms: IncentiveFitPlatform[];
  rider_pattern?: {
    platform: string;
    unit: string;
    periods_observed: number;
    period_start: string | null;
    period_end: string | null;
    typical_orders_low: number;
    typical_orders_high: number;
    median_orders: number | null;
    busiest_hours: string[];
    acceptance_rate: string;
    login_time: string;
  };
  disclaimer: string;
}

export interface IncentiveReport {
  incentive_audit: IncentiveAuditResult | null;
  incentive_fit: IncentiveFitResult | null;
  /** Present when the check came from a Telegram submission. */
  submission_id?: string;
}

export interface IncentiveAuditRequest {
  partner_id: string;
  source?: "history" | "telegram";
  submission_id?: string;
  platform?: string;
  period_date?: string;
  incentive_type?: "daily_target" | "weekly_target" | "period_target";
}

export async function runIncentiveAudit(req: IncentiveAuditRequest): Promise<IncentiveReport> {
  const response = await fetch(`${API_BASE_URL}/audit/incentive`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: "Incentive check failed" }));
    throw new Error(err.detail || "Incentive check failed");
  }
  return response.json();
}
