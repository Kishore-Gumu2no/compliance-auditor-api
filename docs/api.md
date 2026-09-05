---
title: API Documentation
---

# FairPay Engine API (FastAPI)

All endpoints are served under the base URL `http://<host>:8000`.

## 1. Audit endpoints
| Method | Path | Description | Request Body | Response |
|--------|------|-------------|--------------|----------|
| `POST` | `/audit/` | Upload PDFs / images and trigger a full compliance audit. | `multipart/form-data` with optional `images[]` and `pdfs[]`. | `{ "status": "Audit Complete", "ai_analysis": { "final_status": "...", "receipt_id": "..." } }` |
| `POST` | `/audit/batch` | Run audits for many partners concurrently. | JSON array of `BatchPartnerSpec` objects (see **Batch Partner Spec** below). | Array of `FairPayState` objects – the final orchestrator state for each partner. |

## 2. Counterfactual Debate endpoints
| Method | Path | Description | Request Body | Response |
|--------|------|-------------|--------------|----------|
| `POST` | `/counterfactual/simulate` | Run a single what‑if scenario through the three‑persona debate. | `{ "platform": "Zomato", "description": "increase incentive by 10%" }` | `{ "synthesis": "...", "realist": "...", "optimist": "...", "pessimist": "..." }` |
| `POST` | `/counterfactual/slider` | Re‑run the debate with an adjusted numeric value (e.g., incentive amount). | `{ "platform": "Zomato", "adjusted_value": 1200 }` | Same shape as **simulate**. |
| `GET` | `/counterfactual/history/{partner_id}` | Retrieve historic debate syntheses for a partner. | – | `[ { "partner_id": "demo", "timestamp": "2026-08-28T14:22:00Z", "synthesis": "..." }, ... ]` |

## 3. WebSocket
| Path | Description |
|------|-------------|
| `/stream` | Live earnings stream used by the frontend demo. Sends JSON `{ "platform": "Zomato", "earnings": 4200 }`. |

## 4. Data models (Pydantic)
```ts
// BatchPartnerSpec (frontend) – mirrors the expected backend shape
interface BatchPartnerSpec {
  partner_id: string;
  partner_profile: Record<string, any>; // see Backend/models.PartnerProfile
  pdf_paths?: string[];
  image_paths?: string[];
  platform?: string;
  contract_versions?: any[];
}
```

All responses are JSON‐serialisable. Errors are returned as FastAPI `HTTPException` objects with a `detail` field.

---
*Generated on 2026‑09‑01 as part of Phase 5.*