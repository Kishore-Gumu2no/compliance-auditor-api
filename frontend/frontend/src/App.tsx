import { useState, useEffect, useCallback } from 'react';
import './App.css';
import {
  createEarningsStream,
  auditPartnerHistory,
  runIncentiveAudit,
  type StreamMessage,
  type AuditResponse,
  type IncentiveAuditRequest,
  type IncentiveReport,
} from './services/api';
import { DEMO_INCREMENT, PLATFORMS, startingEarnings } from './services/platforms';
import { CounterfactualSimulator } from './components/CounterfactualSimulator';
import IncentiveAuditResult from './components/IncentiveAuditResult';
import { SyntheticDataUpload } from './components/SyntheticDataUpload';
import SimplifiedAuditResult from './components/SimplifiedAuditResult';
import TelegramAuditNotice from './components/TelegramAuditNotice';
import { ErrorBoundary } from './components/ErrorBoundary';
import { SAMPLE_AUDIT_RESULTS, SAMPLE_PLATFORM_EARNINGS, formatCurrency, getStatusColor, type SampleAuditResult } from './services/demoData';

// Partner ID for Bobby - this should come from auth in production
const PARTNER_ID = 'bobby';

// Link to the FairPay Telegram bot, configured per environment (never hardcoded).
const TELEGRAM_BOT_URL: string = (import.meta.env.VITE_TELEGRAM_BOT_URL || '').trim();

// Platform identity, names and demo-mode figures all come from
// services/platforms.ts, which is the counterpart of Backend/platforms.py.

// Determine if we're in demo mode (Vercel deployment)
const isDemoMode = import.meta.env.VITE_DEMO_MODE === 'true' || typeof window !== 'undefined' && window.location.hostname !== 'localhost';

function App() {
  // Live Earnings State - initialize with demo data if in demo mode to avoid sync setState in effect
  const [platformData, setPlatformData] = useState<Record<string, number>>(
    isDemoMode ? SAMPLE_PLATFORM_EARNINGS : startingEarnings()
  );
  // The socket's own readyState - never a guess, and never set by a timer, so
  // "Live stream active" means the socket really is open.
  const [streamState, setStreamState] = useState<number>(WebSocket.CLOSED);
  // Demo mode has no socket; its simulated stream is always "live".
  const isConnected = isDemoMode || streamState === WebSocket.OPEN;

  // Audit result state (evidence arrives through the Telegram bot)
  const [uploadResult, setUploadResult] = useState<AuditResponse | null>(null);
  const [demoAuditResult, setDemoAuditResult] = useState<SampleAuditResult | null>(null);

  // Discover Audit State (Milestone 2 CTA)
  const [isDiscoverLoading, setIsDiscoverLoading] = useState(false);
  const [discoverError, setDiscoverError] = useState<string | null>(null);

  // Incentive check ("Why didn't I get my incentive?") - kept apart from the
  // full audit result above; it never feeds the Audit Results views.
  const [incentiveReport, setIncentiveReport] = useState<IncentiveReport | null>(null);
  const [isIncentiveLoading, setIsIncentiveLoading] = useState(false);
  const [incentiveError, setIncentiveError] = useState<string | null>(null);

  // Derive total earnings during render (avoids sync setState in effect).
  // Only the platforms the dashboard knows are summed, so a name the app does
  // not recognise cannot quietly inflate the total.
  const totalEarnings = PLATFORMS.reduce(
    (sum, p) => sum + (platformData[p.id] ?? p.startingEarnings),
    0,
  );

  // Handle stream message - defined before useEffect to avoid initialization warning
  const handleStreamMessage = useCallback((message: StreamMessage) => {
    switch (message.type) {
      case 'status':
        setStreamState(message.readyState);
        break;
      case 'earnings':
        // Keyed by the wire name the stream sends, which is the same key
        // services/platforms.ts renders from.
        setPlatformData(prev => ({ ...prev, [message.platform]: message.earnings }));
        break;
      case 'notice':
        // The server confirms each stored message; nothing to show for it.
        break;
    }
  }, []);

  // Initialize WebSocket connection (only in non-demo mode)
  useEffect(() => {
    if (isDemoMode) {
      // Platform data already initialized with SAMPLE_PLATFORM_EARNINGS in useState.
      // Simulate live earnings increments on the same cadence the backend uses
      // (both sides read these figures from their platforms module).
      const intervals = PLATFORMS.map(p =>
        setInterval(() => {
          setPlatformData(prev => ({
            ...prev,
            [p.id]: (prev[p.id] ?? p.startingEarnings) + DEMO_INCREMENT,
          }));
        }, p.incrementIntervalMs)
      );

      return () => intervals.forEach(clearInterval);
    }

    const stream = createEarningsStream(handleStreamMessage, PARTNER_ID);

    // Closes the socket whatever state it is in and stops the stream from
    // reconnecting, so StrictMode's second run cannot leave one behind.
    return () => stream.close();
  }, [handleStreamMessage]);

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

  const scrollToResults = () =>
    setTimeout(() => document.getElementById('audit-results')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 100);

  const handleDiscoverAudit = async () => {
    setIsDiscoverLoading(true);
    setDiscoverError(null);
    setUploadResult(null);
    setDemoAuditResult(null);

    try {
      const result = await auditPartnerHistory('bobby');
      setUploadResult(result);
      scrollToResults();
    } catch (error) {
      setDiscoverError(error instanceof Error ? error.message : 'Failed to run history audit');
    } finally {
      setIsDiscoverLoading(false);
    }
  };

  const runIncentiveCheck = async (req: IncentiveAuditRequest) => {
    setIsIncentiveLoading(true);
    setIncentiveError(null);
    try {
      const report = await runIncentiveAudit(req);
      setIncentiveReport(report);
      scrollToResults();
    } catch (error) {
      setIncentiveError(error instanceof Error ? error.message : 'Failed to check your incentive');
    } finally {
      setIsIncentiveLoading(false);
    }
  };

  // One action behind the single button: the full 30-day pay audit, then why an
  // incentive was missed and which platform's incentive structure fits.
  const handleCheck30DayAudit = async () => {
    await handleDiscoverAudit();
    await runIncentiveCheck({ partner_id: PARTNER_ID, source: 'history' });
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
            {/* Header links only; every other section stays on the page. */}
            <a href="#live-earnings" className="nav-link">Earnings</a>
            <a href="#why-fairpay" className="nav-link">The Problem</a>
            <a href="#evidence-upload" className="nav-link">Send Evidence</a>
            <a href="#audit-results" className="nav-link">Audit Results</a>
            <a href="#what-if-i-earn-more" className="nav-link">How to Earn More</a>
            <a href="#privacy" className="nav-link">Privacy</a>
            {isDemoMode && (
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
                  key={platform.id}
                  className="earnings-card"
                  data-platform={platform.id}
                  style={{ '--platform-color': platform.color } as React.CSSProperties}
                >
                  <div className="platform-header">
                    <span className="platform-icon" aria-hidden="true">{platform.icon}</span>
                    <span className="platform-name">{platform.displayName}</span>
                    <div
                      className={`connection-indicator ${isConnected ? 'connected' : 'connecting'}`}
                      aria-label={
                        isDemoMode
                          ? 'Demo mode - simulated data'
                          : isConnected
                            ? 'Connected to live stream'
                            : 'Connecting...'
                      }
                    />
                  </div>
                  <div className="earnings-amount">
                    {/* ?? not ||: an earned total of 0 is a real value. */}
                    {formatCurrency(platformData[platform.id] ?? platform.startingEarnings)}
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
              <span>{isDemoMode ? 'Demo mode - simulated live data' : isConnected ? 'Live stream active' : 'Connecting...'}</span>
            </div>

            {/* Milestone 2 CTA: Discover how you can earn more */}
            {/* One button: the full 30-day pay audit, why an incentive was
                missed, and which platform's incentive structure fits. */}
            <div className="cta-discover-section">
              <button
                className="cta-discover-button"
                onClick={handleCheck30DayAudit}
                disabled={isDiscoverLoading || isIncentiveLoading}
                aria-label="Check your 30 day audit"
              >
                {isDiscoverLoading || isIncentiveLoading ? (
                  <>
                    <span className="loading-spinner" style={{ marginRight: 'var(--spacing-8)' }} />
                    Checking your 30 day audit...
                  </>
                ) : (
                  'Check your 30 day audit'
                )}
              </button>
              {(discoverError || incentiveError) && (
                <p className="cta-discover-error" role="alert">{discoverError || incentiveError}</p>
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
                  <p>Send evidence securely through Telegram — your personal data is redacted before any analysis.</p>
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
                <h2 className="section-title-large">Send Your Evidence on Telegram</h2>
                <p className="upload-description">
                  Uploading docs on website? Nah that's the old way, let's do it on Telegram so that you can know which
                  documents or photos to send by particularly selecting and sending them to our bot.{' '}
                  <span className="emphasis">EASY SIMPLE CONVENIENT</span>
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

            {/* What the two choices in the Telegram bot (step 3) mean. */}
            {/* The two options the Telegram bot offers, each with what to send
                and its own link. Nothing is submitted on this website. */}
            <div className="telegram-options">
              <h3 className="evidence-card-title">Choose what to check, then send it on Telegram</h3>
              <div className="telegram-options-grid">
                <div className="telegram-option">
                  <strong>Option 1 — Didn't get your incentive?</strong>
                  <span>
                    Send one full day of trips, every single one, plus the incentive screen if you have it.
                    FairPay then tells you what the incentive needed and why it was not paid.
                  </span>
                  {TELEGRAM_BOT_URL ? (
                    <a
                      className="incentive-button"
                      href={TELEGRAM_BOT_URL}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Open Telegram and send a day's trips
                    </a>
                  ) : (
                    <span className="incentive-button is-disabled" aria-disabled="true">
                      Telegram link not configured
                    </span>
                  )}
                </div>
                <div className="telegram-option">
                  <strong>Option 2 — Full audit</strong>
                  <span>
                    Send all your orders and payments for a period. PDF statements work best. FairPay matches
                    every order with its payment and shows any money that may be missing.
                  </span>
                  {TELEGRAM_BOT_URL ? (
                    <a
                      className="incentive-button is-secondary"
                      href={TELEGRAM_BOT_URL}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Open Telegram and send statements
                    </a>
                  ) : (
                    <span className="incentive-button is-disabled" aria-disabled="true">
                      Telegram link not configured
                    </span>
                  )}
                </div>
              </div>
              <p className="telegram-options-note">
                The bot asks you to choose Option 1 or Option 2 after you send <code>/start</code>, then link your
                data with <code>/link</code> and your ID. Your results appear on this page.
                {!TELEGRAM_BOT_URL && ' Set VITE_TELEGRAM_BOT_URL to enable the links.'}
              </p>
            </div>

            {isDemoMode && (
              <div className="upload-area">
                {/* Demo audit samples in demo mode */}
                {isDemoMode && (
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
              <ErrorBoundary label="the Telegram audit notice">
              <TelegramAuditNotice
                partnerId={PARTNER_ID}
                onView={(result) => {
                  const analysis = result?.ai_analysis;
                  if (analysis?.evidence_mode === 'incentive_audit') {
                    // Shown in its own section, never as a full-audit result.
                    setIncentiveReport({
                      incentive_audit: analysis.incentive_audit ?? null,
                      incentive_fit: analysis.incentive_fit ?? null,
                      submission_id: analysis.submission_id,
                    });
                    scrollToResults();
                    return;
                  }
                  setDemoAuditResult(null);
                  setUploadResult(result);
                }}
              />
              </ErrorBoundary>

              {uploadResult || demoAuditResult || incentiveReport ? (
                <>
                  {(uploadResult || demoAuditResult) && (
                    <ErrorBoundary label="your audit result">
                      <SimplifiedAuditResult
                        predictions={uploadResult?.ai_analysis?.reconciliation_predictions || []}
                        evidenceGaps={uploadResult?.ai_analysis?.evidence_gaps || []}
                        uploadResult={uploadResult}
                        demoAuditResult={demoAuditResult}
                      />
                    </ErrorBoundary>
                  )}
                  {/* Why an incentive was missed, then which platform's
                      incentive structure fits, in the same results section. */}
                  {incentiveReport ? (
                    <ErrorBoundary label="your incentive check">
                      <IncentiveAuditResult
                        report={incentiveReport}
                        busy={isIncentiveLoading}
                      />
                    </ErrorBoundary>
                  ) : isIncentiveLoading ? (
                    <div className="incentive-loading-state" role="status" aria-live="polite">
                      <span className="loading-spinner" />
                      <p>Checking why your incentive wasn't paid...</p>
                    </div>
                  ) : null}
                </>
              ) : (
                <div className="audit-empty-state">
                  <div className="empty-icon">📋</div>
                  <h3>No Audit Results Yet</h3>
                  <p>Send your earnings evidence to the FairPay Telegram bot to run your first compliance audit.</p>
                  <p className="empty-hint">The audit will check your pay against minimum wage laws, calculate discrepancies, and provide a verifiable receipt.</p>
                  {isDemoMode && (
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
        <ErrorBoundary label="How to Earn More">
          <CounterfactualSimulator
            initialAuditContext={uploadResult?.ai_analysis?.counterfactual_context}
          />
        </ErrorBoundary>

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
                  <p>Documents you send through Telegram are deleted as soon as the audit finishes. The trips read from them — their times and amounts — are deleted too, as soon as you have been shown your result.</p>
                </div>
                <div className="privacy-card">
                  <div className="privacy-icon">🔐</div>
                  <h3>Zero-Trust Architecture</h3>
                  <p>Names, phone numbers and other personal details are blacked out before the AI ever sees a document. FairPay keeps your partner ID with your result, so it knows whose result to show you.</p>
                </div>
                <div className="privacy-card">
                  <div className="privacy-icon">📜</div>
                  <h3>Audit Transparency</h3>
                  <p>Every audit generates an immutable SHA-256 receipt hash. You can verify the integrity of your audit at any time.</p>
                </div>
              </div>

              <div className="privacy-notice">
                <p><strong>FairPay Engine does not store your personal details.</strong> What is kept is your result — the amounts worked out, whether the conditions were met, and the receipt — stored under your partner ID so it can be shown back to you. FairPay also remembers which Telegram chat that ID belongs to; send <strong>/unlink</strong> to the bot and it forgets the chat and your past results.</p>
              </div>
            </div>
          </div>
        </section>

        {/* Section 7: Submit Your Synthetic Data */}
        <section id="synthetic-data" className="section synthetic-data-section">
          <div className="container">
            <ErrorBoundary label="the synthetic data check">
              <SyntheticDataUpload />
            </ErrorBoundary>
          </div>
        </section>

      </main>

      <footer className="footer">
        <div className="container">
          <p>FairPay Engine — Empowering delivery partners with transparent earnings audit</p>
          <p className="footer-note">Built with zero-trust architecture • Personal data never stored</p>
          {isDemoMode && (
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