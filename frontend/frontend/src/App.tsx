import { useState, useEffect, useRef, useCallback } from 'react';
import './App.css';
import { createEarningsStream, uploadEvidence, type StreamMessage, type AuditResponse } from './services/api';
import { SAMPLE_AUDIT_RESULTS, SAMPLE_PLATFORM_EARNINGS, formatCurrency, getStatusColor, type SampleAuditResult } from './services/demoData';

// Platform types for live earnings
interface PlatformEarnings {
  platform: string;
  earnings: number;
  color: string;
  icon: string;
}

const PLATFORMS: PlatformEarnings[] = [
  { platform: 'Zomato', earnings: 4000, color: '#E23744', icon: '🛵' },
  { platform: 'Swiggy', earnings: 3500, color: '#FC8019', icon: '📦' },
  { platform: 'Uber Eats', earnings: 1700, color: '#000000', icon: '🚗' },
];

// Determine if we're in demo mode (Vercel deployment)
const isDemoMode = import.meta.env.VITE_DEMO_MODE === 'true' || typeof window !== 'undefined' && window.location.hostname !== 'localhost';

function App() {
  // Live Earnings State
  const [platformData, setPlatformData] = useState<Record<string, number>>({
    Zomato: 4000,
    Swiggy: 3500,
    'Uber Eats': 1700,
  });
  const [isConnected, setIsConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  // Evidence Upload State
  const [selectedImages, setSelectedImages] = useState<File[]>([]);
  const [selectedPdfs, setSelectedPdfs] = useState<File[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<AuditResponse | null>(null);
  const [demoAuditResult, setDemoAuditResult] = useState<SampleAuditResult | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  // Demo state
  const [demoMode] = useState(isDemoMode);
  const [currentDemoIndex, setCurrentDemoIndex] = useState(0);

  // Derive total earnings during render (avoids sync setState in effect)
  const totalEarnings = Object.values(platformData).reduce((sum, val) => sum + val, 0);

  // Handle stream message - defined before useEffect to avoid initialization warning
  const handleStreamMessage = useCallback((message: StreamMessage) => {
    if (message.type === 'connected') {
      setIsConnected(true);
    } else if (message.type === 'earnings' && message.platform && message.earnings) {
      setPlatformData(prev => ({
        ...prev,
        [message.platform!]: message.earnings!,
      }));
    } else if (message.type === 'error') {
      setIsConnected(false);
      // Reconnection is handled in the api service
      setTimeout(() => setIsConnected(true), 3000);
    }
  }, []);

  // Initialize WebSocket connection (only in non-demo mode)
  useEffect(() => {
    if (demoMode) {
      // Use sample data in demo mode
      setPlatformData(SAMPLE_PLATFORM_EARNINGS);
      setIsConnected(true);
      return;
    }

    const websocket = createEarningsStream(handleStreamMessage);
    wsRef.current = websocket;

    return () => {
      if (websocket.readyState === WebSocket.OPEN) {
        websocket.close();
      }
    };
  }, [demoMode, handleStreamMessage]);

  // Handle file selection
  const handleImageSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length > 20) {
      setUploadError('Too many images! Maximum 20 allowed.');
      return;
    }
    setSelectedImages(files);
    setUploadError(null);
  };

  const handlePdfSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length > 5) {
      setUploadError('Too many PDFs! Maximum 5 allowed.');
      return;
    }
    setSelectedPdfs(files);
    setUploadError(null);
  };

  // Handle evidence upload
  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (selectedImages.length === 0 && selectedPdfs.length === 0) {
      setUploadError('Please select at least one file to upload.');
      return;
    }

    setIsUploading(true);
    setUploadError(null);
    setUploadResult(null);
    setDemoAuditResult(null);

    if (demoMode) {
      // Simulate processing delay
      await new Promise(resolve => setTimeout(resolve, 2000));
      const result = SAMPLE_AUDIT_RESULTS[currentDemoIndex % SAMPLE_AUDIT_RESULTS.length];
      setDemoAuditResult(result);
      setCurrentDemoIndex(prev => prev + 1);
      setIsUploading(false);
      return;
    }

    try {
      const result = await uploadEvidence({
        images: selectedImages.length > 0 ? selectedImages : undefined,
        pdfs: selectedPdfs.length > 0 ? selectedPdfs : undefined,
      });
      setUploadResult(result);
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : 'Upload failed');
    } finally {
      setIsUploading(false);
    }
  };

  const handleDemoAudit = (index: number) => {
    setDemoAuditResult(SAMPLE_AUDIT_RESULTS[index]);
    setUploadResult(null);
  };

  const clearResults = () => {
    setUploadResult(null);
    setDemoAuditResult(null);
  };

  return (
    <div className="app">
      {/* Navigation */}
      <nav className="nav">
        <div className="nav-container">
          <div className="nav-brand">
            <span className="brand-icon">⚖️</span>
            <span className="brand-text">FairPay Engine</span>
          </div>
          <div className="nav-links">
            <a href="#live-earnings" className="nav-link">Live Earnings</a>
            <a href="#why-fairpay" className="nav-link">Why FairPay?</a>
            <a href="#evidence-upload" className="nav-link">Upload Evidence</a>
            <a href="#privacy" className="nav-link">Privacy</a>
            <a href="#audit-results" className="nav-link">Audit Results</a>
            {demoMode && (
              <span className="demo-badge">DEMO MODE</span>
            )}
          </div>
        </div>
      </nav>

      <main className="main">
        {/* Section 1: Live Earnings */}
        <section id="live-earnings" className="section live-earnings-section">
          <div className="container">
            <div className="section-header">
              <span className="section-badge">LIVE</span>
              <h1 className="section-title">Live Earnings for <span className="highlight">Bobby</span></h1>
              <p className="section-subtitle">Real-time earnings tracking across all delivery platforms</p>
            </div>

            <div className="earnings-grid">
              {PLATFORMS.map((platform) => (
                <div
                  key={platform.platform}
                  className="earnings-card"
                  data-platform={platform.platform}
                  style={{ '--platform-color': platform.color } as React.CSSProperties}
                >
                  <div className="platform-header">
                    <span className="platform-icon" aria-hidden="true">{platform.icon}</span>
                    <span className="platform-name">{platform.platform}</span>
                    {!demoMode && (
                      <div
                        className={`connection-indicator ${isConnected ? 'connected' : 'connecting'}`}
                        aria-label={isConnected ? 'Connected to live stream' : 'Connecting...'}
                      />
                    )}
                    {demoMode && (
                      <div
                        className="connection-indicator connected"
                        aria-label="Demo mode - simulated data"
                      />
                    )}
                  </div>
                  <div className="earnings-amount">
                    {formatCurrency(platformData[platform.platform] || platform.earnings)}
                  </div>
                  <div className="earnings-label">Current Session</div>
                </div>
              ))}

              {/* Total Earnings Card */}
              <div className="earnings-card total-card">
                <div className="platform-header">
                  <span className="platform-icon" aria-hidden="true">💰</span>
                  <span className="platform-name">Total Earnings</span>
                </div>
                <div className="earnings-amount total-amount">{formatCurrency(totalEarnings)}</div>
                <div className="earnings-label">Across All Platforms</div>
              </div>
            </div>

            <div className="stream-status">
              <span className={`status-dot ${isConnected ? 'active' : 'inactive'}`} />
              <span>{demoMode ? 'Demo mode - simulated live data' : isConnected ? 'Live stream active' : 'Connecting...'}</span>
            </div>
          </div>
        </section>

        {/* Section 2: Why FairPay? */}
        <section id="why-fairpay" className="section why-fairpay-section">
          <div className="container">
            <div className="why-fairpay-content">
              <div className="why-fairpay-text">
                <span className="section-badge">THE PROBLEM</span>
                <h2 className="section-title-large">
                  Don't you think it is tedious for our delivery partners to track their earnings from several platforms such as
                  <span className="platform-list">Zomato, Swiggy, Uber Eats</span> and many more?
                </h2>
                <p className="why-fairpay-description">
                  Every day, delivery partners like Bobby juggle multiple apps, each with different payout structures,
                  incentive schemes, and payment cycles. Manually tracking earnings across platforms is time-consuming,
                  error-prone, and leaves partners vulnerable to discrepancies.
                </p>
              </div>

              <div className="why-fairpay-solution">
                <div className="solution-card">
                  <div className="solution-icon">🎯</div>
                  <h3>One Place to Track</h3>
                  <p>Consolidated view of earnings from all delivery platforms in real-time.</p>
                </div>
                <div className="solution-card">
                  <div className="solution-icon">🔍</div>
                  <h3>Audit & Verify</h3>
                  <p>Automated audit engine checks your pay against local labor laws and minimum wages.</p>
                </div>
                <div className="solution-card">
                  <div className="solution-icon">🛡️</div>
                  <h3>Zero-Trust Evidence</h3>
                  <p>Upload evidence securely — your personal data is redacted before any analysis.</p>
                </div>
                <div className="solution-card">
                  <div className="solution-icon">📊</div>
                  <h3>Transparent Results</h3>
                  <p>Get detailed audit reports with receipt IDs, discrepancy calculations, and explanations.</p>
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Section 3: Evidence Upload */}
        <section id="evidence-upload" className="section evidence-upload-section">
          <div className="container">
            <div className="upload-content">
              <div className="upload-info">
                <span className="section-badge">EVIDENCE</span>
                <h2 className="section-title-large">Upload Your Evidence</h2>
                <p className="upload-description">
                  Provide your earnings evidence to trigger a comprehensive audit. FairPay's zero-trust ingestion engine
                  processes your documents locally, redacts all personal information, and determines whether you received
                  your rightful pay.
                </p>

                <div className="evidence-types">
                  <h3>Supported Evidence Types</h3>
                  <ul>
                    <li>
                      <span className="evidence-icon">📸</span>
                      <div>
                        <strong>Earnings Screenshots</strong>
                        <span>Platform earnings screenshots, payment confirmations</span>
                      </div>
                    </li>
                    <li>
                      <span className="evidence-icon">📄</span>
                      <div>
                        <strong>Shift Records</strong>
                        <span>Work schedules, shift timings, hours logged</span>
                      </div>
                    </li>
                    <li>
                      <span className="evidence-icon">💳</span>
                      <div>
                        <strong>Payment Records</strong>
                        <span>Bank statements, UPI transactions, payout receipts</span>
                      </div>
                    </li>
                    <li>
                      <span className="evidence-icon">📋</span>
                      <div>
                        <strong>Other Documents</strong>
                        <span>Contracts, offer letters, platform policies</span>
                      </div>
                    </li>
                  </ul>
                </div>

                <div className="zero-trust-explanation">
                  <h3>How Zero-Trust Ingestion Works</h3>
                  <ol>
                    <li><strong>Local Extraction:</strong> Text is extracted from your images/PDFs using OCR.</li>
                    <li><strong>PII Redaction:</strong> Names, phone numbers, emails, and addresses are automatically masked.</li>
                    <li><strong>AI Analysis:</strong> Only redacted data is sent to the audit engine for earnings extraction.</li>
                    <li><strong>Compliance Check:</strong> Extracted data is verified against temporal labor law database.</li>
                    <li><strong>Audit Receipt:</strong> Immutable SHA-256 receipt generated for transparency.</li>
                  </ol>
                </div>
              </div>

              <div className="upload-area">
                <form onSubmit={handleUpload} className="upload-form">
                  <div className="file-inputs">
                    <div className="file-input-group">
                      <label className="file-label">
                        <span className="file-icon">📷</span>
                        <span>Images (Max 20)</span>
                        <input
                          type="file"
                          accept="image/*"
                          multiple
                          onChange={handleImageSelect}
                          disabled={isUploading}
                        />
                      </label>
                      {selectedImages.length > 0 && (
                        <div className="selected-files">
                          {selectedImages.length} image(s) selected
                        </div>
                      )}
                    </div>

                    <div className="file-input-group">
                      <label className="file-label">
                        <span className="file-icon">📄</span>
                        <span>PDFs (Max 5)</span>
                        <input
                          type="file"
                          accept="application/pdf"
                          multiple
                          onChange={handlePdfSelect}
                          disabled={isUploading}
                        />
                      </label>
                      {selectedPdfs.length > 0 && (
                        <div className="selected-files">
                          {selectedPdfs.length} PDF(s) selected
                        </div>
                      )}
                    </div>
                  </div>

                  {uploadError && (
                    <div className="upload-error" role="alert">
                      {uploadError}
                    </div>
                  )}

                  <button
                    type="submit"
                    className="upload-button"
                    disabled={isUploading || (selectedImages.length === 0 && selectedPdfs.length === 0)}
                  >
                    {isUploading && <span className="loading-spinner" />}
                    <span className="button-icon" aria-hidden="true">⬆️</span>
                    <span>{isUploading ? 'Processing...' : 'Touch here to Upload'}</span>
                  </button>

                  <p className="upload-hint">
                    Maximum 20 images and 5 PDFs. Personal details will be redacted automatically.
                  </p>
                </form>

                {/* Demo audit samples in demo mode */}
                {demoMode && (
                  <div className="demo-samples">
                    <h4>Try Sample Audits</h4>
                    <p className="demo-samples-hint">Click to preview audit results without uploading files</p>
                    <div className="demo-sample-buttons">
                      {SAMPLE_AUDIT_RESULTS.map((sample, index) => (
                        <button
                          key={sample.id}
                          className="demo-sample-button"
                          onClick={() => handleDemoAudit(index)}
                          style={{ borderColor: getStatusColor(sample.status) } as React.CSSProperties}
                        >
                          <span className="demo-sample-platform">{sample.platform}</span>
                          <span className="demo-sample-status" style={{ color: getStatusColor(sample.status) }}>
                            {sample.status}
                          </span>
                          <span className="demo-sample-discrepancy">
                            {formatCurrency(sample.discrepancy)}
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </section>

        {/* Section 4: Privacy / Trust */}
        <section id="privacy" className="section privacy-section">
          <div className="container">
            <div className="privacy-content">
              <span className="section-badge">PRIVACY</span>
              <h2 className="section-title-large">Your Privacy is Non-Negotiable</h2>

              <div className="privacy-principles">
                <div className="privacy-card">
                  <div className="privacy-icon">🔒</div>
                  <h3>PII Redaction</h3>
                  <p>All personal identifiers — names, phone numbers, email addresses, Aadhaar, PAN — are automatically detected and masked before any AI processing.</p>
                </div>
                <div className="privacy-card">
                  <div className="privacy-icon">🚫</div>
                  <h3>No Data Storage</h3>
                  <p>Your uploaded documents are processed in memory and never persisted to disk. Temporary files are deleted immediately after audit completion.</p>
                </div>
                <div className="privacy-card">
                  <div className="privacy-icon">🔐</div>
                  <h3>Zero-Trust Architecture</h3>
                  <p>The audit engine only receives redacted, anonymized data. Your identity is never linked to the analysis results.</p>
                </div>
                <div className="privacy-card">
                  <div className="privacy-icon">📜</div>
                  <h3>Audit Transparency</h3>
                  <p>Every audit generates an immutable SHA-256 receipt hash. You can verify the integrity of your audit at any time.</p>
                </div>
              </div>

              <div className="privacy-notice">
                <p><strong>FairPay Engine does not store your personal details.</strong> The only data retained is the audit result: discrepancy calculations, compliance status, and the cryptographic receipt — all fully anonymized.</p>
              </div>
            </div>
          </div>
        </section>

        {/* Section 5: Audit Results */}
        <section id="audit-results" className="section audit-results-section">
          <div className="container">
            <div className="audit-results-content">
              <span className="section-badge">RESULTS</span>
              <h2 className="section-title-large">Audit Results</h2>

              {uploadResult || demoAuditResult ? (
                <div className="audit-results-display">
                  <div className="result-header">
                    <div
                      className={`result-status ${(uploadResult?.ai_analysis.final_status || demoAuditResult?.status || '').toLowerCase().includes('underpaid') ? 'status-underpaid' : (uploadResult?.ai_analysis.final_status || demoAuditResult?.status || '').toLowerCase().includes('overpaid') ? 'status-overpaid' : 'status-correct'}`}
                    >
                      {uploadResult?.ai_analysis.final_status || demoAuditResult?.status}
                    </div>
                    <div className="receipt-id">
                      Receipt: <code>{(uploadResult?.ai_analysis.receipt_id || demoAuditResult?.receiptId || '').substring(0, 16)}...</code>
                    </div>
                  </div>

                  <div className="result-details">
                    <div className="result-card">
                      <h4>Audit Summary</h4>
                      <p>
                        {demoAuditResult?.auditDetails.explanation ||
                         'The compliance audit has been completed. Your earnings have been verified against applicable labor laws for the relevant period.'}
                      </p>
                    </div>

                    <div className="result-card">
                      <h4>Receipt ID</h4>
                      <code className="full-receipt">{uploadResult?.ai_analysis.receipt_id || demoAuditResult?.receiptId}</code>
                      <p className="receipt-note">Save this receipt for your records. It serves as an immutable proof of the audit.</p>
                    </div>
                  </div>

                  {demoAuditResult && (
                    <div className="result-card" style={{ marginTop: 'var(--spacing-20)' }}>
                      <h4>Detailed Breakdown</h4>
                      <div style={{ display: 'grid', gap: 'var(--spacing-12)', fontFamily: 'var(--font-suisse-intl)', fontSize: 'var(--text-body)', color: 'var(--color-ash)' }}>
                        <div><strong>Platform:</strong> {demoAuditResult.platform}</div>
                        <div><strong>Shift Date:</strong> {demoAuditResult.shiftDate}</div>
                        <div><strong>Hours Worked:</strong> {demoAuditResult.hoursWorked} hrs</div>
                        <div><strong>Hourly Rate:</strong> {formatCurrency(demoAuditResult.rate)}</div>
                        <div><strong>Expected Payout:</strong> {formatCurrency(demoAuditResult.expectedPayout)}</div>
                        <div><strong>Actual Payout:</strong> {formatCurrency(demoAuditResult.actualPayout)}</div>
                        <div><strong>Discrepancy:</strong> <span style={{ color: getStatusColor(demoAuditResult.status) }}>{formatCurrency(demoAuditResult.discrepancy)}</span></div>
                        <div><strong>Minimum Wage (Karnataka 2024):</strong> {formatCurrency(demoAuditResult.auditDetails.minimumWage)}/hr</div>
                        <div><strong>Compliance:</strong> {demoAuditResult.auditDetails.complianceStatus}</div>
                      </div>
                    </div>
                  )}

                  <div className="result-actions">
                    <button className="secondary-button" onClick={clearResults}>
                      {demoMode ? 'Try Another Sample' : 'Run New Audit'}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="audit-empty-state">
                  <div className="empty-icon">📋</div>
                  <h3>No Audit Results Yet</h3>
                  <p>Upload your earnings evidence in the section above to run your first compliance audit.</p>
                  <p className="empty-hint">The audit will check your pay against minimum wage laws, calculate discrepancies, and provide a verifiable receipt.</p>
                  {demoMode && (
                    <div className="demo-samples" style={{ marginTop: 'var(--spacing-32)' }}>
                      <p className="demo-samples-hint">Or try a sample audit:</p>
                      <div className="demo-sample-buttons">
                        {SAMPLE_AUDIT_RESULTS.map((sample, index) => (
                          <button
                            key={sample.id}
                            className="demo-sample-button"
                            onClick={() => handleDemoAudit(index)}
                            style={{ borderColor: getStatusColor(sample.status) } as React.CSSProperties}
                          >
                            <span className="demo-sample-platform">{sample.platform}</span>
                            <span className="demo-sample-status" style={{ color: getStatusColor(sample.status) }}>
                              {sample.status}
                            </span>
                            <span className="demo-sample-discrepancy">
                              {formatCurrency(sample.discrepancy)}
                            </span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </section>
      </main>

      <footer className="footer">
        <div className="container">
          <p>FairPay Engine — Empowering delivery partners with transparent earnings audit</p>
          <p className="footer-note">Built with zero-trust architecture • Personal data never stored</p>
          {demoMode && (
            <p className="footer-note" style={{ marginTop: 'var(--spacing-8)' }}>
              Demo Mode — Connect to local backend for full functionality
            </p>
          )}
        </div>
      </footer>
    </div>
  );
}

export default App;