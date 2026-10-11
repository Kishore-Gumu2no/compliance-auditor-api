import React, { useState, useEffect, useCallback } from 'react';
import {
  simulate_counterfactual,
  get_earnings_baseline,
  simulate_earnings_opportunity,
  save_simulation,
  get_simulations,
  type ScenarioType,
  type EarningsBaseline,
  type OpportunityResult,
  type SavedSimulation,
} from '../services/api';
import { formatCurrency, auditNumber } from '../services/demoData';
import { PLATFORMS, platformDisplayName } from '../services/platforms';
import ReactMarkdown from 'react-markdown';

interface Synthesis {
  synthesis: string;
  realist: string;
  optimist: string;
  pessimist: string;
  direction?: string | null;
  calculation?: string | null;
}

interface CounterfactualSimulatorProps {
  initialAuditContext?: Record<string, any>;
}

// Labels for the three earnings opportunities, kept in one place so the UI
// wording and the history list stay consistent.
const SCENARIO_LABELS: Record<ScenarioType, string> = {
  hours: 'Working hours',
  days: 'Working days',
  expenses: 'Expenses',
};

// Example prompts for "Describe a Change", grounded in numeric pay/cost
// changes so the AI debate has a calculable figure to work from. The first
// three describe changes that put more money in the partner's pocket; the
// last two describe changes that take money out.
const SCENARIO_SUGGESTIONS: { text: string; direction: 'up' | 'down' }[] = [
  { text: 'Increase peak hour incentive by 20%', direction: 'up' },
  { text: 'Add a ₹50 bonus for every order above 20 in a day', direction: 'up' },
  { text: 'Reduce fuel cost per order by 15%', direction: 'up' },
  { text: 'Cut per-order base pay by 10%', direction: 'down' },
  { text: 'Remove the weekend incentive bonus', direction: 'down' },
];

export const CounterfactualSimulator: React.FC<CounterfactualSimulatorProps> = ({ initialAuditContext }) => {
  const [platform, setPlatform] = useState('Zomato');
  const [description, setDescription] = useState('');
  const [result, setResult] = useState<Synthesis | null>(null);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<'scenario' | 'opportunity' | 'history'>('opportunity');
  const [error, setError] = useState<string | null>(null);
  const [auditContext, setAuditContext] = useState<Record<string, any> | null>(null);
  const [showPersonas, setShowPersonas] = useState(false);

  // Earnings Opportunity Simulator state
  const [partnerId, setPartnerId] = useState<string | null>(null);
  const [submissionId, setSubmissionId] = useState<string | null>(null);
  const [baseline, setBaseline] = useState<EarningsBaseline | null>(null);
  const [baselineError, setBaselineError] = useState<string | null>(null);
  const [scenarioType, setScenarioType] = useState<ScenarioType>('hours');
  const [scenarioValue, setScenarioValue] = useState<string>('');
  const [opportunity, setOpportunity] = useState<OpportunityResult | null>(null);
  const [showAssumptions, setShowAssumptions] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveNotice, setSaveNotice] = useState<string | null>(null);

  // Saved simulation history (from the database, never fabricated)
  const [history, setHistory] = useState<SavedSimulation[] | null>(null);
  const [expandedHistoryId, setExpandedHistoryId] = useState<string | null>(null);

  // Initialize from audit context if provided
  useEffect(() => {
    if (initialAuditContext) {
      setAuditContext(initialAuditContext);

      // Set platform from audit context
      if (initialAuditContext.platform) {
        setPlatform(initialAuditContext.platform);
      }
      // Pre-fill description with a summary
      if (initialAuditContext.findings_summary) {
        setDescription(`Based on audit: ${initialAuditContext.findings_summary.summary || 'Review findings for optimization opportunities'}`);
      }
      // The partner is identified by the audit context, never hardcoded here.
      if (initialAuditContext.partner_id) {
        setPartnerId(initialAuditContext.partner_id);
      }
      // A Telegram audit simulates from the documents the partner sent.
      setSubmissionId(
        initialAuditContext.source === 'telegram' ? initialAuditContext.submission_id ?? null : null
      );
    }
  }, [initialAuditContext]);

  // Load the partner's real baseline (30-day history, or the Telegram audit's
  // documents) so the inputs start from their actual working pattern.
  useEffect(() => {
    if (!partnerId) return;
    let cancelled = false;
    setBaseline(null);
    (async () => {
      try {
        const data = await get_earnings_baseline(partnerId, submissionId);
        if (cancelled) return;
        setBaseline(data);
        setBaselineError(null);
      } catch (err) {
        if (cancelled) return;
        setBaselineError(err instanceof Error ? err.message : 'Failed to load your earnings baseline');
      }
    })();
    return () => { cancelled = true; };
  }, [partnerId, submissionId]);

  const canSimulate = baseline?.simulation_possible === true;
  const expensesAvailable = canSimulate && baseline?.expenses_available !== false;

  // Default the input to the partner's current pattern whenever the scenario changes.
  // Clearing result/opportunity here, together and unconditionally, stops a
  // stale result from the other flow (e.g. "Describe a Change") rendering in
  // its own, different style while the new scenario's calculation is loading.
  useEffect(() => {
    setResult(null);
    setOpportunity(null);
    setSaveNotice(null);
    if (!baseline || !baseline.simulation_possible) return;
    if (scenarioType === 'expenses' && baseline.expenses_available === false) {
      setScenarioType('hours');
      return;
    }
    if (scenarioType === 'hours') {
      setScenarioValue(auditNumber(baseline.avg_hours_per_day ?? 0).toFixed(1));
    } else if (scenarioType === 'days') {
      setScenarioValue(String(baseline.days_worked));
    } else {
      setScenarioValue('0');
    }
  }, [scenarioType, baseline]);

  const loadHistory = useCallback(async (partner: string) => {
    const saved = await get_simulations(partner);
    setHistory(saved);
  }, []);

  const handleOpenHistory = async () => {
    setActiveTab('history');
    if (!partnerId) return;
    setLoading(true);
    setError(null);
    try {
      await loadHistory(partnerId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'History load failed');
    } finally {
      setLoading(false);
    }
  };

  const handleCalculate = async () => {
    if (!partnerId) {
      setError('No audit context available. Please complete an audit first to enable simulation.');
      return;
    }
    const value = parseFloat(scenarioValue);
    if (isNaN(value)) {
      setError('Enter a number first.');
      return;
    }
    setLoading(true);
    setError(null);
    setSaveNotice(null);
    try {
      const res = await simulate_earnings_opportunity({
        partner_id: partnerId,
        scenario_type: scenarioType,
        value,
        submission_id: submissionId,
      });
      setOpportunity(res);
      setResult({
        synthesis: res.synthesis,
        realist: res.realist,
        optimist: res.optimist,
        pessimist: res.pessimist,
      });
      setActiveTab('opportunity');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Simulation failed');
    } finally {
      setLoading(false);
    }
  };

  // Saving happens only when the partner asks for it, never on every input change.
  const handleSave = async () => {
    if (!partnerId || !opportunity) return;
    const value = parseFloat(scenarioValue);
    if (isNaN(value)) return;
    setSaving(true);
    setError(null);
    try {
      await save_simulation({
        partner_id: partnerId,
        scenario_type: opportunity.scenario_type,
        value,
        submission_id: submissionId,
      });
      setSaveNotice('Saved. You can find this in History.');
      await loadHistory(partnerId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save the simulation');
    } finally {
      setSaving(false);
    }
  };

  const handleSimulate = async () => {
    if (!description.trim()) return;
    if (!auditContext) {
      setError('No audit context available. Please complete an audit first to enable simulation.');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await simulate_counterfactual({ platform, description, audit_context: auditContext });
      setResult(res);
      setShowPersonas(false); // details stay behind "Show Detailed Analysis"
      setOpportunity(null);
      setActiveTab('scenario');
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Simulation failed';
      setError(message);
      console.error('Simulation failed:', error);
    } finally {
      setLoading(false);
    }
  };

  // Input configuration per scenario, derived from the partner's own history.
  const inputConfig = (() => {
    if (scenarioType === 'hours') {
      return {
        label: 'Hours you work each day',
        min: 0.5,
        max: 16,
        step: 0.5,
        suffix: 'hours a day',
        hint: canSimulate && baseline
          ? `Right now you work about ${auditNumber(baseline.avg_hours_per_day ?? 0).toFixed(1)} hours a day.`
          : '',
      };
    }
    if (scenarioType === 'days') {
      return {
        label: 'Days you work in a month',
        min: 1,
        max: baseline?.max_working_days ?? 45,
        step: 1,
        suffix: 'days',
        hint: !canSimulate
          ? ''
          : baseline?.source === 'telegram'
            ? `Your documents show ${baseline.days_worked} day(s) of work.`
            : `In the last 30 days you worked ${baseline?.days_worked} days.`,
      };
    }
    return {
      label: 'Change in your fuel or energy cost',
      min: -100,
      max: 100,
      step: 5,
      suffix: '%',
      hint: expensesAvailable && baseline
        ? `You spent about ${formatCurrency(auditNumber(baseline.total_operating_cost ?? 0))} on fuel or energy. Use a minus number to spend less, for example -10 to spend 10% less.`
        : '',
    };
  })();

  const difference = opportunity ? auditNumber(opportunity.estimated_difference) : 0;

  return (
    <section id="what-if-i-earn-more" className="section counterfactual-section">
      <div className="container">
        <div className="section-header">
          <span className="section-badge">WHAT IF</span>
          <h2 className="section-title-large">How to Earn More</h2>
          <p className="section-subtitle">
            See how working different hours, working more or fewer days, or
            spending less on fuel could change your earnings.
          </p>
        </div>

        {/* The documents could not support a projection - say so up front. */}
        {baseline && !baseline.simulation_possible && baseline.unavailable_message && (
          <div className="simulation-unavailable" role="alert">
            <span className="simulation-unavailable-icon" aria-hidden="true">⚠️</span>
            <div>
              <p className="simulation-unavailable-title">{baseline.unavailable_message}</p>
              {baseline.evidence_needed && baseline.evidence_needed.length > 0 ? (
                <>
                  <p className="simulation-unavailable-hint">
                    FairPay does not estimate working hours. To simulate, send the FairPay bot:
                  </p>
                  <ul className="simulation-unavailable-hint evidence-needed-list">
                    {baseline.evidence_needed.map((item, idx) => <li key={idx}>{item}</li>)}
                  </ul>
                </>
              ) : (
                <p className="simulation-unavailable-hint">
                  Send screenshots that show your login or online hours, with their dates, to the
                  FairPay bot and run the audit again.
                </p>
              )}
            </div>
          </div>
        )}

        <div className="counterfactual-layout">
          {/* Left Panel - Controls */}
          <div className="counterfactual-controls">
            <div className="control-card">
              <h3 className="control-title">Choose How to Explore</h3>
              <div className="tab-group" role="tablist">
                <button
                  role="tab"
                  aria-selected={activeTab === 'opportunity'}
                  onClick={() => setActiveTab('opportunity')}
                  className={`tab-button ${activeTab === 'opportunity' ? 'active' : ''}`}
                >
                  Earn More
                </button>
                <button
                  role="tab"
                  aria-selected={activeTab === 'scenario'}
                  onClick={() => setActiveTab('scenario')}
                  className={`tab-button ${activeTab === 'scenario' ? 'active' : ''}`}
                >
                  Describe a Change
                </button>
                <button
                  role="tab"
                  aria-selected={activeTab === 'history'}
                  onClick={handleOpenHistory}
                  className={`tab-button ${activeTab === 'history' ? 'active' : ''}`}
                  disabled={loading}
                >
                  History
                </button>
              </div>
            </div>

            {activeTab === 'opportunity' && (
              <div className="control-card">
                <h3 className="control-title">What would you like to change?</h3>
                {canSimulate && baseline && (
                  <p className="slider-hint opportunity-source">
                    {baseline.source === 'telegram'
                      ? `Based on the documents you sent through Telegram (${baseline.days_worked} day(s) with working hours).`
                      : 'Based on your last 30 days of work history.'}
                  </p>
                )}
                {canSimulate && baseline?.value_sources && baseline.value_sources.length > 0 && (
                  <details className="audit-details value-sources">
                    <summary>Where these numbers come from</summary>
                    <ul>
                      {baseline.value_sources.map((source) => (
                        <li key={source.label}>
                          <span className={`value-kind value-kind-${source.kind}`}>{source.kind}</span>{' '}
                          <strong>{source.label}</strong> — {source.detail}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
                <div className="tab-group scenario-group" role="tablist">
                  {(Object.keys(SCENARIO_LABELS) as ScenarioType[]).map((type) => (
                    <button
                      key={type}
                      role="tab"
                      aria-selected={scenarioType === type}
                      onClick={() => setScenarioType(type)}
                      className={`tab-button ${scenarioType === type ? 'active' : ''}`}
                      disabled={
                        loading || !canSimulate || (type === 'expenses' && !expensesAvailable)
                      }
                      title={
                        type === 'expenses' && canSimulate && !expensesAvailable
                          ? baseline?.expenses_unavailable_reason ?? undefined
                          : undefined
                      }
                    >
                      {SCENARIO_LABELS[type]}
                    </button>
                  ))}
                </div>
                {canSimulate && !expensesAvailable && baseline?.expenses_unavailable_reason && (
                  <p className="control-hint">Expenses: {baseline.expenses_unavailable_reason}</p>
                )}

                <label className="opportunity-label" htmlFor="opportunity-value">
                  {inputConfig.label}
                </label>
                <div className="opportunity-input-row">
                  <input
                    id="opportunity-value"
                    type="number"
                    className="opportunity-input"
                    value={scenarioValue}
                    min={inputConfig.min}
                    max={inputConfig.max}
                    step={inputConfig.step}
                    onChange={(e) => setScenarioValue(e.target.value)}
                    disabled={loading || !canSimulate}
                  />
                  <span className="opportunity-suffix">{inputConfig.suffix}</span>
                </div>
                {inputConfig.hint && <p className="slider-hint">{inputConfig.hint}</p>}

                <button
                  onClick={handleCalculate}
                  disabled={loading || !canSimulate}
                  className="primary-button full-width"
                >
                  {loading ? (
                    <>
                      <span className="loading-spinner" />
                      Calculating...
                    </>
                  ) : (
                    'See What Changes'
                  )}
                </button>
                {!partnerId && (
                  <p className="control-hint">Complete an audit first to enable simulation.</p>
                )}
                {partnerId && baselineError && (
                  <p className="control-hint">{baselineError}</p>
                )}
                {baseline && !baseline.simulation_possible && (
                  <p className="control-hint">Simulation is not possible with the documents sent so far.</p>
                )}
              </div>
            )}

            {activeTab === 'scenario' && (
              <div className="control-card">
                <h3 className="control-title">Platform</h3>
                <select
                  value={platform}
                  onChange={(e) => setPlatform(e.target.value)}
                  className="platform-select"
                  disabled={loading}
                >
                  {/* Values are wire names, labels are display names - both
                      from services/platforms.ts, so adding a platform is one
                      edit there rather than another hardcoded <option>. */}
                  {PLATFORMS.map((p) => (
                    <option key={p.id} value={p.id}>{p.displayName}</option>
                  ))}
                </select>
              </div>
            )}

            {activeTab === 'scenario' && (
              <div className="control-card">
                <h3 className="control-title">Describe a Change</h3>
                <p className="slider-hint">Type a change in your own words, like "increase peak hour bonus by 20%".</p>
                <p className="control-hint scenario-guidance">
                  Ask about a specific, numeric change to pay, incentives, bonuses, fuel or other
                  costs — FairPay calculates the real impact from your own figures, it never
                  invents them. It cannot answer questions with no number to calculate, such as
                  legal advice or anything unrelated to your earnings.
                </p>
                <div className="suggestion-chips">
                  {SCENARIO_SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion.text}
                      type="button"
                      className={`suggestion-chip suggestion-chip-${suggestion.direction}`}
                      onClick={() => setDescription(suggestion.text)}
                      disabled={loading || !auditContext}
                    >
                      <span className="suggestion-chip-icon" aria-hidden="true">
                        {suggestion.direction === 'up' ? '▲' : '▼'}
                      </span>
                      {suggestion.text}
                    </button>
                  ))}
                </div>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="e.g., Increase peak hour incentive by 15% for weekend shifts"
                  className="scenario-textarea"
                  rows={3}
                  disabled={loading || !auditContext}
                />
                <button
                  onClick={handleSimulate}
                  disabled={loading || !description.trim() || !auditContext}
                  className="primary-button full-width"
                >
                  {loading ? (
                    <>
                      <span className="loading-spinner" />
                      Calculating...
                    </>
                  ) : (
                    'See What Changes'
                  )}
                </button>
                {!auditContext && (
                  <p className="control-hint">Complete an audit first to enable simulation.</p>
                )}
              </div>
            )}

            {activeTab === 'history' && (
              <div className="control-card">
                <h3 className="control-title">Saved Simulations</h3>
                {!partnerId ? (
                  <div className="history-empty">
                    <p>Complete an audit first to see your saved simulations.</p>
                  </div>
                ) : history && history.length > 0 ? (
                  <div className="history-list">
                    {history.map((item) => {
                      const diff = auditNumber(item.estimated_difference);
                      const isOpen = expandedHistoryId === item.simulation_id;
                      return (
                        <div key={item.simulation_id} className="history-item">
                          <span className="history-timestamp">
                            {SCENARIO_LABELS[item.scenario_type] ?? item.scenario_type}
                            {' · '}
                            {new Date(item.created_at).toLocaleString()}
                          </span>
                          <span className="history-synthesis">
                            {formatCurrency(auditNumber(item.baseline_earnings))}
                            {' → '}
                            {formatCurrency(auditNumber(item.projected_earnings))}
                            {' ('}
                            <span style={{ color: diff >= 0 ? '#10b981' : '#ef4444' }}>
                              {diff >= 0 ? '+' : '−'}{formatCurrency(Math.abs(diff))}
                            </span>
                            {')'}
                          </span>
                          <button
                            type="button"
                            className="expand-button history-expand"
                            onClick={() => setExpandedHistoryId(isOpen ? null : item.simulation_id)}
                            aria-expanded={isOpen}
                          >
                            <span>{isOpen ? 'Hide' : 'Show'} assumptions</span>
                            <span className="expand-icon">{isOpen ? '▲' : '▼'}</span>
                          </button>
                          {isOpen && (
                            <div className="assumptions-block">
                              <p className="assumptions-basis">{item.assumptions?.basis}</p>
                              <ul className="assumptions-list">
                                {(item.assumptions?.notes ?? []).map((note, i) => (
                                  <li key={i}>{note}</li>
                                ))}
                              </ul>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="history-empty">
                    <p>No saved simulations yet.</p>
                    <p className="history-hint">
                      Try a change under "Earn More", then choose Save to keep it here.
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Right Panel - Results */}
          <div className="counterfactual-results">
            {error && (
              <div className="result-error" role="alert">
                <span className="error-icon">⚠️</span>
                <span>{error}</span>
              </div>
            )}
            {!auditContext && !error && (
              <div className="results-empty no-audit-context">
                <div className="empty-icon">📋</div>
                <h4>Audit Required</h4>
                <p>Complete an audit first to generate the context needed for simulation.</p>
                <p className="empty-hint">Send your evidence through the FairPay Telegram bot and run an audit to unlock this simulator.</p>
              </div>
            )}
            {result && !error && (
              <div className="result-card-wrapper">
                <div className="result-header">
                  <span className="result-badge">RESULT</span>
                  <span className="result-platform">
                    {opportunity
                      ? SCENARIO_LABELS[opportunity.scenario_type]
                      : platformDisplayName(platform)}
                  </span>
                </div>

                {/* Financial Summary - Primary Focus */}
                {opportunity && (
                  <div className="financial-summary">
                    <h4 className="result-subtitle">Your Earnings Summary</h4>
                    <div className="earnings-grid">
                      <div className="earnings-item">
                        <span className="earnings-label">What you actually earned</span>
                        <span className="earnings-value">
                          {formatCurrency(auditNumber(opportunity.baseline_earnings))}
                        </span>
                      </div>
                      <div className="earnings-item">
                        <span className="earnings-label">Estimated with this change</span>
                        <span className="earnings-value adjusted">
                          {formatCurrency(auditNumber(opportunity.projected_earnings))}
                        </span>
                      </div>
                      <div className="earnings-item difference">
                        <span className="earnings-label">Estimated change</span>
                        <span
                          className="earnings-value"
                          style={{ color: difference >= 0 ? '#10b981' : '#ef4444' }}
                        >
                          {difference >= 0 ? '+' : '−'}{formatCurrency(Math.abs(difference))}
                        </span>
                      </div>
                    </div>
                    <p className="explanation">{opportunity.assumptions.basis}.{' '}
                      This is an estimate based on your own past 30 days, not guaranteed income.
                    </p>

                    <div className="assumptions-section">
                      <button
                        type="button"
                        className="expand-button"
                        onClick={() => setShowAssumptions(!showAssumptions)}
                        aria-expanded={showAssumptions}
                      >
                        <span>{showAssumptions ? 'Hide' : 'Show'} what this estimate assumes</span>
                        <span className="expand-icon">{showAssumptions ? '▲' : '▼'}</span>
                      </button>
                      {showAssumptions && (
                        <div className="assumptions-block">
                          <ul className="assumptions-list">
                            {opportunity.assumptions.notes.map((note, i) => (
                              <li key={i}>{note}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>

                    <div className="save-simulation-row">
                      <button
                        type="button"
                        className="primary-button full-width"
                        onClick={handleSave}
                        disabled={saving}
                      >
                        {saving ? (
                          <>
                            <span className="loading-spinner" />
                            Saving...
                          </>
                        ) : (
                          'Save this simulation'
                        )}
                      </button>
                      {saveNotice && <p className="control-hint">{saveNotice}</p>}
                    </div>
                  </div>
                )}

                {!opportunity && (
                  <div className="financial-summary">
                    <h4 className="result-subtitle">Summary</h4>
                    {/* One simple sentence; the three views stay under "Show Detailed Analysis". */}
                    <p
                      className={`scenario-summary${
                        result.direction === 'increase' ? ' is-up' : result.direction === 'decrease' ? ' is-down' : ''
                      }`}
                    >
                      {result.synthesis || 'No summary available.'}
                    </p>
                    {result.calculation && (
                      <p className="explanation scenario-calculation">
                        How we got this: {result.calculation}. This is an estimate from your own past numbers,
                        not guaranteed income.
                      </p>
                    )}
                  </div>
                )}

                {/* Expandable AI Assessments */}
                <div className="personas-section">
                  <button
                    type="button"
                    className="expand-button"
                    onClick={() => setShowPersonas(!showPersonas)}
                    aria-expanded={showPersonas}
                  >
                    <span>{showPersonas ? 'Hide' : 'Show'} Detailed Analysis</span>
                    <span className="expand-icon">{showPersonas ? '▲' : '▼'}</span>
                  </button>

                  {showPersonas && (
                    <div className="personas-grid">
                      <div className="persona-card realist">
                        <div className="persona-header">
                          <span className="persona-icon">⚖️</span>
                          <span className="persona-name">Balanced View</span>
                        </div>
                        <div className="persona-content">
                          <ReactMarkdown>{result.realist}</ReactMarkdown>
                        </div>
                      </div>
                      <div className="persona-card optimist">
                        <div className="persona-header">
                          <span className="persona-icon">📈</span>
                          <span className="persona-name">Optimistic View</span>
                        </div>
                        <div className="persona-content">
                          <ReactMarkdown>{result.optimist}</ReactMarkdown>
                        </div>
                      </div>
                      <div className="persona-card pessimist">
                        <div className="persona-header">
                          <span className="persona-icon">📉</span>
                          <span className="persona-name">Cautious View</span>
                        </div>
                        <div className="persona-content">
                          <ReactMarkdown>{result.pessimist}</ReactMarkdown>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}
            {!result && !error && auditContext && (
              <div className="results-empty">
                <div className="empty-icon">🔮</div>
                <h4>No Simulation Yet</h4>
                <p>Choose what you would like to change, then click "See What Changes" to see results.</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
};
