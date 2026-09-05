"""Batch audit runner for concurrent partner audits."""

from __future__ import annotations
import asyncio
from typing import List, Dict, Any
from decimal import Decimal
from datetime import date

from models import (
    PartnerProfile, ContractVersion, FairPayState, TransactionRecord,
    Deduction, Incentive, PaymentCalculation, BusinessCostCalculation
)
from orchestrator import run_audit


async def run_batch_audit(partner_specs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Run audits for multiple partners concurrently."""

    async def run_single(partner_spec: Dict[str, Any]) -> Dict[str, Any]:
        try:
            profile = PartnerProfile.model_validate(partner_spec["partner_profile"])
            contract_versions = [
                ContractVersion.model_validate(cv) for cv in partner_spec.get("contract_versions", [])
            ]

            result = run_audit(
                partner_id=partner_spec["partner_id"],
                partner_profile=profile,
                pdf_paths=partner_spec.get("pdf_paths", []),
                image_paths=partner_spec.get("image_paths", []),
                platform=partner_spec.get("platform", "unknown"),
                contract_versions=contract_versions,
            )

            from audit_context import serialize_audit_state
            serialized = serialize_audit_state(result)
            return {
                "partner_id": partner_spec["partner_id"],
                "status": serialized.get("status", "unknown"),
                "ai_analysis": serialized,
            }
        except Exception as e:
            return {
                "partner_id": partner_spec.get("partner_id", "unknown"),
                "status": "error",
                "error": str(e),
            }

    tasks = [run_single(spec) for spec in partner_specs]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    final_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            final_results.append({
                "partner_id": partner_specs[i].get("partner_id", "unknown"),
                "status": "error",
                "error": str(result),
            })
        else:
            final_results.append(result)

    return final_results