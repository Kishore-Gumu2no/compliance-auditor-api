import { useState, useEffect, useRef, useCallback } from 'react';
import './App.css';
import { createEarningsStream, auditPartnerHistory, type StreamMessage, type AuditResponse } from './services/api';
import { CounterfactualSimulator } from './components/CounterfactualSimulator';
import { SyntheticDataUpload } from './components/SyntheticDataUpload';
import SimplifiedAuditResult from './components/SimplifiedAuditResult';
import TelegramAuditNotice from './components/TelegramAuditNotice';
import { SAMPLE_AUDIT_RESULTS, SAMPLE_PLATFORM_EARNINGS, formatCurrency, getStatusColor, type SampleAuditResult } from './services/demoData';

// Partner ID for Bobby - this should come from auth in production
const PARTNER_ID = 'bobby';

// Link to the FairPay Telegram bot, configured per environment (never hardcoded).
const TELEGRAM_BOT_URL: string = (import.meta.env.VITE_TELEGRAM_BOT_URL || '').trim();

// Platform types for live earnings
interface PlatformEarnings {
  platform: string;
  earnings: number;
  color: string;
  icon: string;
  incrementInterval: number; // ms - matches backend run_delivery_streams()
}

const PLATFORMS: PlatformEarnings[] = [
  { platform: 'Zomato', earnings: 4000, color: '#E23744', icon: '🛵', incrementInterval: 5000 },
  { platform: 'Swiggy', earnings: 3500, color: '#FC8019', icon: '📦', incrementInterval: 13000 },
  { platform: 'Uber Eats', earnings: 1700, color: '#000000', icon: '🚗', incrementInterval: 18000 },
];

// Determine if we're in demo mode (Vercel deployment)
const isDemoMode = import.meta.env.VITE_DEMO_MODE === 'true' || typeof window !== 'undefined' && window.location.hostname !== 'localhost';

function App() {
  // Live Earnings State - initialize with demo data if in demo mode to avoid sync setState in effect
  const [platformData, setPlatformData] = useState<Record<string, number>>(
    isDemoMode ? SAMPLE_PLATFORM_EARNINGS : { Zomato: 4000, Swiggy: 3500, 'Uber Eats': 1700 }
  );
  const [isConnected, setIsConnected] = useState(isDemoMode); // true in demo mode
  const wsRef = useRef<WebSocket | null>(null);

  // Audit result state (evidence arrives through the Telegram bot)
  const [uploadResult, setUploadResult] = useState<AuditResponse | null>(null);
  const [demoAuditResult, setDemoAuditResult] = useState<SampleAuditResult | null>(null);

  // Discover Audit State (Milestone 2 CTA)
  const [isDiscoverLoading, setIsDiscoverLoading] = useState(false);
  const [discoverError, setDiscoverError] = useState<string | null>(null);

  // Demo state
  const [demoMode] = useState(isDemoMode);

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
      // Platform data already initialized with SAMPLE_PLATFORM_EARNINGS in useState
      // isConnected already initialized to true for demo mode

      // Simulate live earnings increment matching backend run_delivery_streams() intervals
      const intervals = PLATFORMS.map(p =>
        setInterval(() => {
          setPlatformData(prev => ({
            ...prev,
            [p.platform]: (prev[p.platform] || p.earnings) + 100
          }));
        }, p.incrementInterval)
      );

      return () => intervals.forEach(clearInterval);
    }

    const websocket = createEarningsStream(handleStreamMessage, PARTNER_ID);
    wsRef.current = websocket;

    return () => {
      if (websocket.readyState === WebSocket.OPEN) {
        websocket.close();
      }
    };
  }, [demoMode, handleStreamMessage]);

  const handleDemoAudit = (index: number) => {
    setDemoAuditResult(SAMPLE_AUDIT_RESULTS[index]);
    setUploadResult(null);
    // Scroll to audit results section
    setTimeout(() => {
      const resultsSection = document.getElementById('audit-results');
      if (resultsSection) {
        resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }, 100);
  };

  const handleDiscoverAudit = async () => {
    setIsDiscoverLoading(true);
    setDiscoverError(null);
    setUploadResult(null);
    setDemoAuditResult(null);

    try {
      const result = await auditPartnerHistory('bobby');
      setUploadResult(result);

      // Scroll to audit results section
      setTimeout(() => {
        const resultsSection = document.getElementById('audit-results');
        if (resultsSection) {
          resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      }, 100);
    } catch (error) {
      setDiscoverError(error instanceof Error ? error.message : 'Failed to run history audit');
    } finally {
      setIsDiscoverLoading(false);
    }
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
            <a href="#why-fairpay" className="nav-link">The Problem</a>
            <a href="#evidence-upload" className="nav-link">Upload Evidence</a>
            <a href="#audit-results" className="nav-link">Audit Results</a>
            <a href="#what-if-i-earn-more" className="nav-link">How to Earn More</a>
            <a href="#privacy" className="nav-link">Privacy</a>
            <a href="#synthetic-data" className="nav-link">Synthetic Data</a>
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

            {/* Milestone 2 CTA: Discover how you can earn more */}
            <div className="cta-discover-section">
              <button
                className="cta-discover-button"
                onClick={handleDiscoverAudit}
                disabled={isDiscoverLoading}
                aria-label="Run 30-day audit to discover earning optimization"
              >
                {isDiscoverLoading ? (
                  <>
                    <span className="loading-spinner" style={{ marginRight: 'var(--spacing-8)' }} />
                    Analyzing 30-day history...
                  </>
                ) : (
                  'Discover how you can earn more'
                )}
              </button>
              {discoverError && (
                <p className="cta-discover-error" role="alert">{discoverError}</p>
              )}
            </div>
          </div>
        </section>

        {/* Section 2: The Problem */}
        <section id="why-fairpay" className="section why-fairpay-section">
          <div className="container">
            <div className="why-fairpay-content">
              <div className="why-fairpay-text">
                <span className="section-badge">THE PROBLEM</span>
                <h2 className="section-title-large">
                  Don't you think it is <span className="emphasis">difficult</span> for our delivery partners to track their earnings from several platforms such as
                  Zomato, Swiggy, Uber Eats and many more?
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
            <div className="upload-content upload-content-split">
              <div className="upload-info">
                <span className="section-badge">EVIDENCE</span>
                <h2 className="section-title-large">Upload Your Evidence</h2>
                <p className="upload-description">
                  Provide your earnings evidence to trigger a comprehensive audit. FairPay's zero-trust ingestion engine
                  processes your documents locally, redacts all personal information, and determines whether you received
                  your rightful pay.
                </p>
              </div>

              {/* One compact card for every supported evidence type */}
              <div className="evidence-card evidence-card-side">
                  <h3 className="evidence-card-title">Supported documents</h3>
                  <ul className="evidence-card-list">
                    <li>
                      <span className="evidence-icon" aria-hidden="true">📸</span>
                      <div>
                        <strong>Earnings Screenshots</strong>
                        <span>Platform earnings screenshots and payment confirmations</span>
                      </div>
                    </li>
                    <li>
                      <span className="evidence-icon" aria-hidden="true">📄</span>
                      <div>
                        <strong>Shift Records</strong>
                        <span>Work schedules, shift timings, and hours logged</span>
                      </div>
                    </li>
                    <li>
                      <span className="evidence-icon" aria-hidden="true">💳</span>
                      <div>
                        <strong>Payment Records</strong>
                        <span>Bank statements, UPI transactions, and payout receipts</span>
                      </div>
                    </li>
                    <li>
                      <span className="evidence-icon" aria-hidden="true">📋</span>
                      <div>
                        <strong>Other Documents</strong>
                        <span>Contracts, offer letters, and platform policies</span>
                      </div>
                    </li>
                  </ul>
              </div>
            </div>

            {/* Documents are submitted through the Telegram bot; the website shows results. */}
            {TELEGRAM_BOT_URL ? (
              <a
                className="telegram-cta"
                href={TELEGRAM_BOT_URL}
                target="_blank"
                rel="noopener noreferrer"
              >
                <span className="telegram-cta-icon" aria-hidden="true">📨</span>
                <span className="telegram-cta-text">Upload your documents through Telegram</span>
                <span className="telegram-cta-arrow" aria-hidden="true">→</span>
              </a>
            ) : (
              <div className="telegram-cta telegram-cta-disabled" aria-disabled="true">
                <span className="telegram-cta-icon" aria-hidden="true">📨</span>
                <span className="telegram-cta-text">Upload your documents through Telegram</span>
                <span className="telegram-cta-note">Telegram link not configured (set VITE_TELEGRAM_BOT_URL)</span>
              </div>
            )}

            {demoMode && (
              <div className="upload-area">
                {/* Demo audit samples in demo mode */}
                {demoMode && (
                  <div className="demo-samples">
                    <h4>Try Sample Audits</h4>
                    <p className="demo-samples-hint">Click a delivery partner to see their audit results</p>
                    <div className="demo-sample-buttons">
                      {SAMPLE_AUDIT_RESULTS.map((sample, index) => (
                        <button
                          key={sample.id}
                          className="demo-sample-button"
                          onClick={() => handleDemoAudit(index)}
                          style={{ borderColor: getStatusColor(sample.status) } as React.CSSProperties}
                        >
                          <span className="demo-sample-partner">{sample.partnerName}</span>
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
        </section>

        {/* Section 4: Audit Results */}
        <section id="audit-results" className="section audit-results-section">
          <div className="container">
            <div className="audit-results-content">
              <span className="section-badge">RESULTS</span>
              <h2 className="section-title-large">Audit Results</h2>

              {/* Announces audits submitted through the Telegram bot. */}
              <TelegramAuditNotice
                partnerId={PARTNER_ID}
                onView={(result) => {
                  setDemoAuditResult(null);
                  setUploadResult(result);
                }}
              />

              {uploadResult || demoAuditResult ? (
                <SimplifiedAuditResult
                  predictions={uploadResult?.ai_analysis?.reconciliation_predictions || []}
                  evidenceGaps={uploadResult?.ai_analysis?.evidence_gaps || []}
                  uploadResult={uploadResult}
                  demoAuditResult={demoAuditResult}
                />
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
                            <span className="demo-sample-partner">{sample.partnerName}</span>
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

        {/* Section 5: How to Earn More (Counterfactual Simulator) */}
        <CounterfactualSimulator
          initialAuditContext={uploadResult?.ai_analysis?.counterfactual_context}
        />

        {/* Section 6: Privacy / Trust */}
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
                  <p>Your uploaded documents are processed in memory and never saved to disk. When it comes to personal data, what happens in engine stays in engine, and vanishes</p>
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

        {/* Section 7: Submit Your Synthetic Data */}
        <section id="synthetic-data" className="section synthetic-data-section">
          <div className="container">
            <SyntheticDataUpload />
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