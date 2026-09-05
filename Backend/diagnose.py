"""Diagnostic script to trace discrepancies."""
import sys
sys.path.insert(0, '.')

from synthetic_verification import load_synthetic_data, run_engine_on_partner, get_default_contract_versions, derive_reconciliation_predictions

dataset = load_synthetic_data('../synthetic_data_lite')
contract_versions = get_default_contract_versions()

for p in dataset.partners:
    pid = p['partner_id']
    state = run_engine_on_partner(pid, dataset, contract_versions)
    print(f'=== {pid} ===')
    print(f'  status: {state.get("status")}')
    print(f'  error: {state.get("error")}')
    preds = derive_reconciliation_predictions(pid, dataset, state)
    for pred in preds:
        gt = next((g for g in dataset.ground_truth_by_partner.get(pid, []) if g["earning_id"] == pred.earning_id), None)
        gt_type = gt.get('anomaly_type') if gt else 'N/A'
        print(f'  {pred.earning_id}: GT={gt_type} PRED={pred.predicted_anomaly.value} settlement={pred.transaction_id} order={pred.order_id} var={pred.variance} conf={pred.confidence}')
        print(f'    notes: {pred.notes}')