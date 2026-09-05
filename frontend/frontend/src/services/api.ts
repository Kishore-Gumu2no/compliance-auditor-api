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
    final_status: string;
    receipt_id: string;
    partner_id: string;
    audit_date: string;
    contract_compliance_findings: ContractComplianceFinding[];
    legal_risk_findings: LegalRiskFinding[];
    economic_concern_findings: EconomicConcernFinding[];
    evidence_gaps: string[];
    overall_grade: string;
    summary: string;
  };
}

export interface ContractComplianceFinding {
  finding_id: string;
  grade: string;
  transaction_id: string;
  contract_clause: string;
  expected_amount: number;
  actual_amount: number;
  variance: number;
  evidence_strength: number;
  notes: string;
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
  classification_probability: number;
  applicable_law_version: string;
  risk_if_employee: string;
  risk_if_contractor: string;
  evidence_strength: number;
  notes: string;
}

export interface EconomicConcernFinding {
  finding_id: string;
  grade: string;
  transaction_id: string;
  business_hourly_rate: number;
  contracted_hourly_benchmark: number;
  market_hourly_benchmark: number;
  min_wage_hourly_benchmark: number;
  gap_vs_contracted_pct: number;
  gap_vs_market_pct: number;
  gap_vs_min_wage_pct: number;
  severity: string;
  notes: string;
}

export interface UploadFilesParams {
  images?: File[];
  pdfs?: File[];
  partner_profile: string;
  platform: string;
  contract_versions: string;
}

// ---------------------------------------------------------------------
// Upload evidence (PDFs / images) – POST /audit/
// ---------------------------------------------------------------------
export async function uploadEvidence(
  files: UploadFilesParams
): Promise<AuditResponse> {
  const formData = new FormData();
  formData.append("partner_profile", files.partner_profile);
  formData.append("platform", files.platform);
  formData.append("contract_versions", files.contract_versions);
  if (files.images) {
    files.images.forEach((image) => {
      formData.append("images", image);
    });
  }

  if (files.pdfs) {
    files.pdfs.forEach((pdf) => {
      formData.append("pdfs", pdf);
    });
  }

  const response = await fetch(`${API_BASE_URL}/audit/`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const errorData = await response
      .json()
      .catch(() => ({ detail: "Upload failed" }));
    throw new Error(errorData.detail || "Failed to upload evidence");
  }

  return response.json();
}

// ---------------------------------------------------------------------
// WebSocket earnings stream – GET /stream
// ---------------------------------------------------------------------
export interface StreamData {
  platform: string;
  earnings: number;
}

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

// Slider request (numeric adjustment)
export interface CounterfactualSliderRequest {
  platform: string;
  adjusted_value: number;
}

// POST /counterfactual/slider
export async function slider_counterfactual(
  req: CounterfactualSliderRequest
): Promise<CounterfactualResult> {
  const response = await fetch(`${API_BASE_URL}/counterfactual/slider`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });

  if (!response.ok) {
    const err = await response
      .json()
      .catch(() => ({ detail: "Slider failed" }));
    throw new Error(err.detail || "Failed to run slider simulation");
  }
  return response.json();
}

// History item returned by GET /counterfactual/history/{partner_id}
export interface CounterfactualHistoryItem {
  partner_id: string;
  timestamp: string;
  synthesis: string;
}

// GET history
export async function get_counterfactual_history(
  partner_id: string
): Promise<CounterfactualHistoryItem[]> {
  const response = await fetch(
    `${API_BASE_URL}/counterfactual/history/${partner_id}`
  );
  if (!response.ok) {
    const err = await response
      .json()
      .catch(() => ({ detail: "History fetch failed" }));
    throw new Error(err.detail || "Failed to fetch counterfactual history");
  }
  return response.json();
}
