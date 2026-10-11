import { useState, useCallback } from 'react';
import { apiPost } from '../services/http';

interface SyntheticFileData {
  partners: File | null;
  settlements: File | null;
  earnings: File | null;
  groundTruth: File | null;
  groundTruthLayer2: File | null;
}

interface SyntheticAuditResponse {
  status: string;
  aggregate_report: any;
  per_partner_evaluations: any[];
}

export function SyntheticDataUpload() {
  const [selectedFiles, setSelectedFiles] = useState<SyntheticFileData>({
    partners: null,
    settlements: null,
    earnings: null,
    groundTruth: null,
    groundTruthLayer2: null,
  });
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadResult, setUploadResult] = useState<SyntheticAuditResponse | null>(null);
  const [demoMode] = useState(typeof window !== 'undefined' && window.location.hostname !== 'localhost');

  const handleFileSelect = useCallback((field: keyof SyntheticFileData) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      setSelectedFiles(prev => ({ ...prev, [field]: file }));
      setUploadError(null);
    }
  }, []);

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault();

    // Validate required files
    const requiredFields: (keyof SyntheticFileData)[] = ['partners', 'settlements', 'earnings', 'groundTruth'];
    const missing = requiredFields.filter(f => !selectedFiles[f]);
    if (missing.length > 0) {
      setUploadError(`Missing required files: ${missing.join(', ')}`);
      return;
    }

    setIsUploading(true);
    setUploadError(null);
    setUploadResult(null);

    if (demoMode) {
      // Simulate processing delay in demo mode
      await new Promise(resolve => setTimeout(resolve, 2000));
      setUploadResult({
        status: "Synthetic Audit Complete (Demo)",
        aggregate_report: {
          total_partners: 20,
          total_transactions_processed: 208,
          overall_reconciliation_f1: 0.7773,
          overall_reconciliation_precision: 0.7773,
          overall_reconciliation_recall: 0.7773,
          total_expected_findings: 8,
          total_detected_findings: 8,
          total_true_positives: 8,
          total_false_positives: 0,
          total_false_negatives: 0,
          findings_precision: 1.0,
          findings_recall: 1.0,
          findings_f1: 1.0,
        },
        per_partner_evaluations: []
      });
      setIsUploading(false);
      return;
    }

    try {
      // Parse JSON files
      const parseFile = (file: File): Promise<any> => {
        return new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = (event) => {
            try {
              resolve(JSON.parse(event.target?.result as string));
            } catch (cause) {
              // The parser's own message says *where* the JSON is malformed,
              // which is the only actionable part. Discarding it left the
              // partner with a file name and nothing to fix.
              reject(new Error(
                `Failed to parse ${file.name} as JSON: ${cause instanceof Error ? cause.message : String(cause)}`,
                { cause },
              ));
            }
          };
          reader.onerror = () => reject(new Error(`Failed to read ${file.name}`));
          reader.readAsText(file);
        });
      };

      const [partners, settlements, earnings, groundTruth, groundTruthLayer2] = await Promise.all([
        parseFile(selectedFiles.partners!),
        parseFile(selectedFiles.settlements!),
        parseFile(selectedFiles.earnings!),
        parseFile(selectedFiles.groundTruth!),
        selectedFiles.groundTruthLayer2 ? parseFile(selectedFiles.groundTruthLayer2) : Promise.resolve(null),
      ]);

      // Call the synthetic audit endpoint. The base URL and the error handling
      // come from services/http.ts, so this no longer carries its own copy of
      // either.
      const result = await apiPost<SyntheticAuditResponse>(
        '/audit/synthetic',
        {
          partners_json: partners,
          settlements_json: settlements,
          earnings_json: earnings,
          ground_truth_json: groundTruth,
          ground_truth_layer2_json: groundTruthLayer2,
        },
        'Failed to run synthetic audit',
      );
      setUploadResult(result);
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : 'Upload failed');
    } finally {
      setIsUploading(false);
    }
  };

  const clearResults = () => {
    setUploadResult(null);
    setSelectedFiles({ partners: null, settlements: null, earnings: null, groundTruth: null, groundTruthLayer2: null });
  };

  const fileInputs = [
    { key: 'partners', label: 'partners.json', required: true },
    { key: 'settlements', label: 'settlements.json', required: true },
    { key: 'earnings', label: 'earnings.json', required: true },
    { key: 'groundTruth', label: 'ground_truth.json', required: true },
    { key: 'groundTruthLayer2', label: 'ground_truth_layer2.json', required: false },
  ] as const;

  return (
    <div className="synthetic-upload-card">
      <div className="upload-info">
        <span className="section-badge">SYNTHETIC</span>
        <h2 className="section-title-large">Submit your Synthetic Data</h2>
      </div>

      <div className="upload-area">
        <form onSubmit={handleUpload} className="upload-form">
          <div className="file-inputs synthetic-file-inputs">
            {fileInputs.map(({ key, label, required }) => (
              <div key={key} className="file-input-group">
                <label className="file-label">
                  <span className="file-icon">📄</span>
                  <span>{label} {required && <span className="required-star">*</span>}</span>
                  <input
                    type="file"
                    accept=".json"
                    onChange={handleFileSelect(key)}
                    disabled={isUploading}
                  />
                </label>
                {selectedFiles[key] && (
                  <div className="selected-files">
                    Selected: {selectedFiles[key]!.name}
                  </div>
                )}
              </div>
            ))}
          </div>

          {uploadError && (
            <div className="upload-error" role="alert">
              {uploadError}
            </div>
          )}

          <button
            type="submit"
            className="upload-button"
            disabled={isUploading || fileInputs.some(f => f.required && !selectedFiles[f.key])}
          >
            {isUploading && <span className="loading-spinner" />}
            <span className="button-icon" aria-hidden="true">⚙️</span>
            <span>{isUploading ? 'Running Verification...' : 'Run Synthetic Verification'}</span>
          </button>

          <p className="upload-hint">
            All files must be valid JSON. Ground truth is used for evaluation only — never for predictions.
          </p>
        </form>

        {/* Results Display */}
        {uploadResult && (
          <div className="audit-results-display synthetic-results" style={{ marginTop: 'var(--spacing-32)' }}>
            <div className="result-header">
              <div className="result-status status-correct">
                {uploadResult.status}
              </div>
            </div>

            <div className="result-details">
              <div className="result-card">
                <h4>Aggregate Metrics</h4>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 'var(--spacing-16)' }}>
                  <div className="metric-box">
                    <div className="metric-value">{uploadResult.aggregate_report.overall_reconciliation_f1?.toFixed(2) || 'N/A'}</div>
                    <div className="metric-label">Layer 1 F1</div>
                  </div>
                  <div className="metric-box">
                    <div className="metric-value">{((uploadResult.aggregate_report.findings_f1 || 0) * 100).toFixed(1)}%</div>
                    <div className="metric-label">Layer 2 F1</div>
                  </div>
                  <div className="metric-box">
                    <div className="metric-value">{uploadResult.aggregate_report.total_partners}</div>
                    <div className="metric-label">Partners</div>
                  </div>
                  <div className="metric-box">
                    <div className="metric-value">{uploadResult.aggregate_report.total_transactions_processed}</div>
                    <div className="metric-label">Transactions</div>
                  </div>
                </div>
              </div>

              <div className="result-card">
                <h4>Layer 1: Reconciliation by Type</h4>
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--text-sm)' }}>
                    <thead>
                      <tr style={{ borderBottom: '1px solid var(--color-slate)', textAlign: 'left' }}>
                        <th style={{ padding: 'var(--spacing-8) 0' }}>Anomaly Type</th>
                        <th style={{ padding: 'var(--spacing-8) 0' }}>Expected</th>
                        <th style={{ padding: 'var(--spacing-8) 0' }}>Detected</th>
                        <th style={{ padding: 'var(--spacing-8) 0' }}>TP</th>
                        <th style={{ padding: 'var(--spacing-8) 0' }}>FP</th>
                        <th style={{ padding: 'var(--spacing-8) 0' }}>FN</th>
                        <th style={{ padding: 'var(--spacing-8) 0' }}>Precision</th>
                        <th style={{ padding: 'var(--spacing-8) 0' }}>Recall</th>
                        <th style={{ padding: 'var(--spacing-8) 0' }}>F1</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(uploadResult.aggregate_report.reconciliation_by_type || {}).map(([type, m]: [string, any]) => (
                        m.expected > 0 || m.detected > 0 ? (
                          <tr key={type} style={{ borderBottom: '1px solid var(--color-slate)' }}>
                            <td style={{ padding: 'var(--spacing-8) 0', fontFamily: 'monospace' }}>{type}</td>
                            <td style={{ padding: 'var(--spacing-8) 0' }}>{m.expected}</td>
                            <td style={{ padding: 'var(--spacing-8) 0' }}>{m.detected}</td>
                            <td style={{ padding: 'var(--spacing-8) 0', color: m.true_positives > 0 ? 'var(--color-emerald)' : 'inherit' }}>{m.true_positives}</td>
                            <td style={{ padding: 'var(--spacing-8) 0', color: m.false_positives > 0 ? 'var(--color-coral)' : 'inherit' }}>{m.false_positives}</td>
                            <td style={{ padding: 'var(--spacing-8) 0', color: m.false_negatives > 0 ? 'var(--color-coral)' : 'inherit' }}>{m.false_negatives}</td>
                            <td style={{ padding: 'var(--spacing-8) 0' }}>{(m.precision * 100).toFixed(1)}%</td>
                            <td style={{ padding: 'var(--spacing-8) 0' }}>{(m.recall * 100).toFixed(1)}%</td>
                            <td style={{ padding: 'var(--spacing-8) 0', fontWeight: '600' }}>{(m.f1 * 100).toFixed(1)}%</td>
                          </tr>
                        ) : null
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="result-card">
                <h4>Layer 2: Findings by Category</h4>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 'var(--spacing-16)' }}>
                  {[
                    { key: 'contract_metrics', label: 'Contract Compliance' },
                    { key: 'legal_metrics', label: 'Legal Risks' },
                    { key: 'economic_metrics', label: 'Economic Concerns' },
                  ].map(({ key, label }) => {
                    const m = uploadResult.aggregate_report[key];
                    return (
                      <div key={key} style={{ border: '1px solid var(--color-slate)', borderRadius: 'var(--radius-lg)', padding: 'var(--spacing-16)' }}>
                        <div style={{ fontWeight: '600', marginBottom: 'var(--spacing-8)' }}>{label}</div>
                        <div style={{ display: 'grid', gap: 'var(--spacing-4)' }}>
                          <div><strong>Expected:</strong> {m.expected}</div>
                          <div><strong>Detected:</strong> {m.detected}</div>
                          <div><strong>TP:</strong> {m.true_positives} <strong>FP:</strong> {m.false_positives} <strong>FN:</strong> {m.false_negatives}</div>
                          <div style={{ borderTop: '1px solid var(--color-slate)', paddingTop: 'var(--spacing-8)' }}>
                            <div>Precision: {(m.precision * 100).toFixed(1)}%</div>
                            <div>Recall: {(m.recall * 100).toFixed(1)}%</div>
                            <div style={{ fontWeight: '600' }}>F1: {(m.f1 * 100).toFixed(1)}%</div>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="result-card">
                <h4>Layer 3: Controller Decisions</h4>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 'var(--spacing-16)' }}>
                  <div className="metric-box">
                    <div className="metric-value">{uploadResult.aggregate_report.layer3_total_records}</div>
                    <div className="metric-label">Total Records</div>
                  </div>
                  <div className="metric-box">
                    <div className="metric-value">{uploadResult.aggregate_report.layer3_auto_resolved}</div>
                    <div className="metric-label">Auto Resolved</div>
                  </div>
                  <div className="metric-box">
                    <div className="metric-value">{uploadResult.aggregate_report.layer3_action_required}</div>
                    <div className="metric-label">Action Required</div>
                  </div>
                  <div className="metric-box">
                    <div className="metric-value">{uploadResult.aggregate_report.layer3_human_review}</div>
                    <div className="metric-label">Human Review</div>
                  </div>
                  <div className="metric-box">
                    <div className="metric-value">{uploadResult.aggregate_report.layer3_insufficient_evidence}</div>
                    <div className="metric-label">Insufficient Evidence</div>
                  </div>
                  <div className="metric-box">
                    <div className="metric-value">{(uploadResult.aggregate_report.layer3_resolution_rate * 100).toFixed(1)}%</div>
                    <div className="metric-label">Resolution Rate</div>
                  </div>
                </div>
              </div>

              {uploadResult.aggregate_report.layer3_human_review_queue && uploadResult.aggregate_report.layer3_human_review_queue.length > 0 && (
                <div className="result-card">
                  <h4>Human Review Queue</h4>
                  <div style={{ maxHeight: '300px', overflowY: 'auto' }}>
                    {uploadResult.aggregate_report.layer3_human_review_queue.slice(0, 10).map((entry: any, idx: number) => (
                      <div key={idx} style={{ padding: 'var(--spacing-12)', borderBottom: '1px solid var(--color-slate)', fontSize: 'var(--text-sm)' }}>
                        <div><strong>Partner:</strong> {entry.partner_id} ({entry.partner_name})</div>
                        <div><strong>Transaction:</strong> {entry.transaction_id}</div>
                        <div><strong>Priority:</strong> {entry.priority} | <strong>Action:</strong> {entry.recommended_action}</div>
                        <div style={{ color: 'var(--color-ash)' }}>{entry.reason}</div>
                      </div>
                    ))}
                    {uploadResult.aggregate_report.layer3_human_review_queue.length > 10 && (
                      <div style={{ padding: 'var(--spacing-12)', color: 'var(--color-ash)', fontSize: 'var(--text-sm)' }}>
                        ... and {uploadResult.aggregate_report.layer3_human_review_queue.length - 10} more
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>

            <div className="result-actions">
              <button className="secondary-button" onClick={clearResults}>
                Run New Verification
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}