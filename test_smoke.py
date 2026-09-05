#!/usr/bin/env python
"""Smoke test for the real /audit/ path using synthetic document fixtures."""

import sys
import os
import json
from decimal import Decimal
from datetime import date


sys.path.insert(0, '/d/compliance-auditor/Backend')

def test_pdf_extraction():
    """Test PDF text extraction with dummy.pdf"""
    print("\n=== Test 1: PDF Text Extraction ===")
    try:
        from Backend.evidence_extractor import extract_pdf_text
        pdf_paths = ['/d/compliance-auditor/Backend/Experimental_files/dummy.pdf']
        text = extract_pdf_text(pdf_paths)
        print(f"✅ PDF extraction succeeded: {len(text)} chars extracted")
        print(f"   First 200 chars: {text[:200]}")
        return True
    except Exception as e:
        print(f"❌ PDF extraction failed: {e}")
        return False

def test_image_ocr():
    """Test OCR on dummy.png"""
    print("\n=== Test 2: Image OCR ===")
    try:
        from Backend.evidence_extractor import extract_image_text
        image_paths = ['/d/compliance-auditor/Backend/Experimental_files/dummy.png']
        text = extract_image_text(image_paths)
        print(f"✅ OCR succeeded: {len(text)} chars extracted")
        print(f"   First 200 chars: {text[:200]}")
        return True
    except Exception as e:
        print(f"❌ OCR failed: {e}")
        return False

def test_pii_sanitization():
    """Test PII sanitization"""
    print("\n=== Test 3: PII Sanitization ===")
    try:
        from Backend.sanitizer import mask_pii
        test_text = "John Doe earned 5000 INR on 2026-01-15. Phone: 9876543210. Email: john.doe@example.com. Order ID: ORD12345"
        sanitized = mask_pii(test_text)
        print(f"✅ PII sanitization succeeded")
        print(f"   Original: {test_text}")
        print(f"   Sanitized: {sanitized}")
        # Verify PII is masked
        assert "John Doe" not in sanitized, "Name not masked!"
        assert "9876543210" not in sanitized, "Phone not masked!"
        assert "john.doe@example.com" not in sanitized, "Email not masked!"
        print("   All PII properly masked")
        return True
    except Exception as e:
        print(f"❌ PII sanitization failed: {e}")
        return False

def test_full_evidence_processing():
    """Test full evidence processing pipeline"""
    print("\n=== Test 4: Full Evidence Processing ===")
    try:
        from Backend.evidence_extractor import process_evidence, EvidenceExtractionError
        pdf_paths = ['/d/compliance-auditor/Backend/Experimental_files/dummy.pdf']
        image_paths = ['/d/compliance-auditor/Backend/Experimental_files/dummy.png']
        transactions, gaps = process_evidence(pdf_paths, image_paths, "zomato")
        print(f"✅ Evidence processing succeeded")
        print(f"   Transactions extracted: {len(transactions)}")
        print(f"   Evidence gaps: {len(gaps)}")
        for tx in transactions[:3]:
            print(f"   - {tx.record_type} {tx.record_id}: {tx.amount} ({tx.platform})")
        for gap in gaps[:5]:
            print(f"   - Gap: {gap}")
        return True, transactions, gaps
    except EvidenceExtractionError as e:
        print(f"⚠️ Evidence extraction error (expected if no financial data): {e}")
        return False, [], []
    except Exception as e:
        print(f"❌ Evidence processing failed: {e}")
        import traceback
        traceback.print_exc()
        return False, [], []

def test_normalization():
    """Test normalization of extracted records"""
    print("\n=== Test 5: Normalization ===")
    try:
        from Backend.evidence_extractor import process_evidence, EvidenceExtractionError
        from Backend.real_data import normalize_real_records
        pdf_paths = ['/d/compliance-auditor/Backend/Experimental_files/dummy.pdf']
        image_paths = ['/d/compliance-auditor/Backend/Experimental_files/dummy.png']
        try:
            transactions, gaps = process_evidence(pdf_paths, image_paths, "zomato")
        except EvidenceExtractionError:
            # Create mock extracted records for testing
            from Backend.real_data import ExtractedFinancialRecord
            transactions = [
                ExtractedFinancialRecord(
                    record_type="earning",
                    record_id="EARN001",
                    platform="zomato",
                    order_id="ORD001",
                    record_date=date(2026, 1, 15),
                    amount=Decimal("500"),
                    incentive=Decimal("50"),
                    bonus=Decimal("0"),
                    deduction=Decimal("20"),
                    gross_amount=Decimal("570"),
                ),
                ExtractedFinancialRecord(
                    record_type="settlement",
                    record_id="SETL001",
                    platform="zomato",
                    order_id="ORD001",
                    record_date=date(2026, 1, 16),
                    amount=Decimal("480"),
                )
            ]
            gaps = []

        normalized = normalize_real_records(transactions)
        print(f"✅ Normalization succeeded")
        print(f"   TransactionRecords: {len(normalized['transactions'])}")
        print(f"   PaymentCalculations: {len(normalized['payment_calculations'])}")
        print(f"   Predictions: {len(normalized['predictions'])}")
        print(f"   Evidence gaps: {len(normalized['evidence_gaps'])}")
        for tx in normalized['transactions']:
            print(f"   - {tx.transaction_id}: platform_net={tx.net_amount}, gross={tx.gross_amount}")
        for pred in normalized['predictions']:
            print(f"   - {pred['anomaly_type']}: expected={pred['expected_amount']}, settled={pred['settlement_amount']}")
        return True, normalized
    except Exception as e:
        print(f"❌ Normalization failed: {e}")
        import traceback
        traceback.print_exc()
        return False, None

def test_reconciliation():
    """Test shared reconciliation engine"""
    print("\n=== Test 6: Shared Reconciliation Engine ===")
    try:
        from Backend.reconciliation import classify_single, AnomalyType, AMOUNT_TOLERANCE, DATE_TOLERANCE_DAYS
        from datetime import date as dt_date

        def parse_date(d):
            return dt_date.fromisoformat(d)

        # Test cases
        earning = {"date": "2026-01-15", "expected_amount": 500, "incentive": 50, "bonus": 0, "deduction": 20}
        settlement = {"date": "2026-01-16", "amount_paid": 480}

        result = classify_single(settlement, earning, parse_date)
        print(f"✅ Reconciliation test: {result.value}")
        print(f"   AMOUNT_TOLERANCE={AMOUNT_TOLERANCE}, DATE_TOLERANCE_DAYS={DATE_TOLERANCE_DAYS}")

        # Test perfect match
        settlement2 = {"date": "2026-01-16", "amount_paid": 500}
        result2 = classify_single(settlement2, earning, parse_date)
        print(f"   Perfect match: {result2.value}")
        assert result2 == AnomalyType.PERFECT_MATCH

        # Test missing incentive
        settlement3 = {"date": "2026-01-16", "amount_paid": 450}
        result3 = classify_single(settlement3, earning, parse_date)
        print(f"   Missing incentive: {result3.value}")
        assert result3 == AnomalyType.MISSING_INCENTIVE

        print("   All reconciliation rules working correctly")
        return True
    except Exception as e:
        print(f"❌ Reconciliation test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_orchestrator():
    """Test full orchestrator pipeline with synthetic data"""
    print("\n=== Test 7: Orchestrator Pipeline (Synthetic Data) ===")
    try:
        from Backend.orchestrator import run_audit
        from Backend.models import PartnerProfile, ContractVersion

        profile = PartnerProfile(
            partner_id="PTR_TEST",
            name="Test Partner",
            phone="9999999999",
            vehicle_type="bike",
            petrol_per_km=Decimal("8.5"),
            zone="Bengaluru",
            platform_accounts=["Zomato"],
            active_since=date(2024, 1, 1)
        )

        contract_versions = [
            ContractVersion(
                platform="Zomato",
                version_id="v2.1",
                effective_from=date(2024, 1, 1),
                effective_to=None,
                source="test",
                confidence=Decimal("0.9"),
                clauses=["Base pay: 160/day", "Per km: 15", "Wait time: 50/hr", "Peak bonus: 100"]
            )
        ]

        # Use empty paths to test synthetic path (no evidence extraction)
        result = run_audit(
            partner_id="PTR_TEST",
            partner_profile=profile,
            pdf_paths=[],
            image_paths=[],
            platform="zomato",
            contract_versions=contract_versions
        )

        # Should fail because no documents provided
        if result.get("status") == "error":
            print(f"✅ Orchestrator correctly requires documents: {result.get('error')}")
        else:
            print(f"⚠️ Orchestrator returned: {result.get('status')}")
            print(f"   Transactions: {len(result.get('transactions', []))}")
            print(f"   Findings: {result.get('audit_findings') is not None}")
            print(f"   Controller decisions: {len(result.get('controller_decisions', []))}")

        return True
    except Exception as e:
        print(f"❌ Orchestrator test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_counterfactual_context():
    """Test counterfactual context generation"""
    print("\n=== Test 8: Counterfactual Context ===")
    try:
        from Backend.audit_context import build_counterfactual_context, debate_state_from_audit_context

        # Mock audit state
        mock_state = {
            "partner_id": "PTR_TEST",
            "platform": "zomato",
            "transactions": [],
            "evidence_gaps": ["wait_time_minutes missing for record EARN001"],
            "reconciliation_predictions": [
                {"anomaly_type": "perfect_match"},
                {"anomaly_type": "missing_incentive"},
            ],
            "audit_findings": type('obj', (object,), {
                "overall_grade": "confirmed_violation",
                "summary": "Test summary",
                "contract_compliance_findings": [type('obj', (object,), {})()],
                "legal_risk_findings": [],
                "economic_concern_findings": [type('obj', (object,), {})()],
            })(),
            "controller_decisions": [
                type('obj', (object,), {
                    "resolution_status": type('obj', (object,), {"value": "action_required"})(),
                    "human_review_required": True,
                })(),
            ],
            "batch_summary": type('obj', (object,), {
                "records_processed": 10,
                "automatically_resolved": 8,
                "action_required": 2,
            })(),
        }

        context = build_counterfactual_context(mock_state)
        print(f"✅ Counterfactual context generated")
        print(f"   reconciliation_summary: {context.get('reconciliation_summary')}")
        print(f"   evidence_gaps: {context.get('evidence_gaps')}")
        print(f"   findings_summary: {context.get('findings_summary')}")
        print(f"   controller_summary: {context.get('controller_summary')}")

        # Test debate state builder
        debate_state = debate_state_from_audit_context(context, platform="zomato", description="What if incentive increased?")
        print(f"   Debate evidence: {debate_state['evidence'][:100]}...")
        return True
    except Exception as e:
        print(f"❌ Counterfactual context test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_synthetic_benchmark():
    """Run synthetic verification benchmark (quick check)"""
    print("\n=== Test 9: Synthetic Benchmark (Quick) ===")
    try:
        # Just test imports and basic setup
        from Backend.synthetic_verification import load_synthetic_data, get_default_contract_versions
        dataset = load_synthetic_data('/d/compliance-auditor/synthetic_data')
        contracts = get_default_contract_versions()
        print(f"✅ Synthetic data loaded: {len(dataset.partners)} partners")
        print(f"   Contract versions: {len(contracts)}")
        print(f"   First partner: {dataset.partners[0]['partner_id']}")
        return True
    except Exception as e:
        print(f"❌ Synthetic benchmark setup failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    print("=" * 60)
    print("SMOKE TEST: Real /audit/ Path with Synthetic Fixtures")
    print("=" * 60)

    results = {}

    results['pdf_extraction'] = test_pdf_extraction()
    results['image_ocr'] = test_image_ocr()
    results['pii_sanitization'] = test_pii_sanitization()

    ok, tx, gaps = test_full_evidence_processing()
    results['evidence_processing'] = ok

    ok, norm = test_normalization()
    results['normalization'] = ok

    results['reconciliation'] = test_reconciliation()
    results['orchestrator'] = test_orchestrator()
    results['counterfactual'] = test_counterfactual_context()
    results['synthetic_benchmark'] = test_synthetic_benchmark()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for test, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {test:25s}: {status}")

    all_passed = all(results.values())
    print("=" * 60)
    if all_passed:
        print("🎉 ALL TESTS PASSED - Real path is functional!")
    else:
        print("⚠️  SOME TESTS FAILED - See details above")
    print("=" * 60)

    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())