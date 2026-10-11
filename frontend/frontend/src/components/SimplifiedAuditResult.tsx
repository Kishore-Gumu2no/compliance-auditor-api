import React, { useState, useMemo } from 'react';
import { formatCurrency, auditNumber } from '../services/demoData';
import { platformDisplayName } from '../services/platforms';
import type { EvidenceLimit, SettlementReconciliation, SpotCheck } from '../services/api';
import { ContractQuestions, CouldNotCheck, HoursWorkedView, SettlementReconciliationView, SpotCheckView } from './EvidenceModeResult';

interface ReconciliationPrediction {
  transaction_id: string;
  order_id?: string;
  platform?: string;
  transaction_date?: string;
  anomaly_type: string;
  expected_amount: number | string;
  settlement_amount: number | string;
  variance: number | string;
}

interface SimplifiedAuditResultProps {
  predictions: ReconciliationPrediction[];
  evidenceGaps: string[];
  uploadResult?: any;
  demoAuditResult?: any;
}

type Tone = 'good' | 'bad' | 'warn' | 'neutral';

interface OrderRow {
  key: string;
  date?: string;
  orderId: string;
  platform?: string;
  earned: number;
  paid: number;
  difference: number; // paid - earned: negative means paid less
  label: string;
  tone: Tone;
}

// Plain-language names for the engine's reconciliation results.
const RESULT_LABELS: Record<string, string> = {
  perfect_match: 'Paid correctly',
  partial_payment: 'Paid less',
  missing_payment: 'Not paid',
  duplicate_settlement: 'Paid twice',
  missing_incentive: 'Incentive missing',
  incorrect_deduction: 'Wrong deduction',
  date_out_of_tolerance: 'Paid late',
  ambiguous_match: 'Needs review',
};
const UNMATCHED_PAYMENT = 'extra_unexplained_settlement';

// Gap wording produced by the backend extractor and the Telegram bridge.
const NOT_CHECKED_PREFIX = 'Not checked (no date on the document):';
const MISSING_FIELD_RE = /^(\w+)(?: - MISSING ---- FOR BETTER CALCULATION| missing for record)/;
const PLATFORM_PREFIX_RE = /^\[([^\]]+)\]\s*/;
const FIELD_LABELS: Record<string, string> = {
  distance_km: 'Distance',
  duration_minutes: 'Trip time',
  wait_time_minutes: 'Wait time',
};

const ROW_LIMIT = 10;
const TONE_ORDER: Record<Tone, number> = { bad: 0, warn: 1, neutral: 2, good: 3 };

const formatDate = (value?: string) => {
  if (!value) return '—';
  const d = new Date(value);
  return isNaN(d.getTime())
    ? value
    : d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
};

const formatSigned = (value: number) => {
  if (value === 0) return formatCurrency(0);
  return `${value > 0 ? '+' : '−'}${formatCurrency(Math.abs(value))}`;
};

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

const SimplifiedAuditResult: React.FC<SimplifiedAuditResultProps> = ({
  predictions,
  evidenceGaps,
  uploadResult,
  demoAuditResult,
}) => {
  const [selectedPlatform, setSelectedPlatform] = useState<string>('All');
  const [showAllRows, setShowAllRows] = useState(false);

  // Create mock predictions from demoAuditResult for demo mode
  const demoPredictions = useMemo((): ReconciliationPrediction[] => {
    if (!demoAuditResult || predictions.length > 0) return [];
    const platforms = demoAuditResult.platform.split(', ').map((p: string) => p.trim());
    const expectedPerPlatform = Math.floor(demoAuditResult.expectedPayout / platforms.length);
    const receivedPerPlatform = Math.floor(demoAuditResult.actualPayout / platforms.length);
    const variancePerPlatform = expectedPerPlatform - receivedPerPlatform;

    return platforms.map((platform: string, index: number): ReconciliationPrediction => ({
      transaction_id: `demo-${demoAuditResult.id}-${index}`,
      order_id: `order-${index}`,
      platform,
      transaction_date: demoAuditResult.shiftDate,
      anomaly_type: variancePerPlatform > 0 ? 'underpaid' : 'correct',
      expected_amount: expectedPerPlatform,
      settlement_amount: receivedPerPlatform,
      variance: variancePerPlatform,
    }));
  }, [demoAuditResult, predictions.length]);

  // Combine real predictions with demo predictions
  const allPredictions = useMemo((): ReconciliationPrediction[] => {
    return predictions.length > 0 ? predictions : demoPredictions;
  }, [predictions, demoPredictions]);

  // Get unique platforms from predictions
  const availablePlatforms = useMemo(() => {
    const platforms = new Set<string>();
    allPredictions.forEach(p => {
      if (p.platform) platforms.add(p.platform);
    });
    return Array.from(platforms).sort();
  }, [allPredictions]);

  // Platform options: worked platforms from the 30-day partner metadata,
  // falling back to platforms present in the predictions when metadata is absent.
  const metadataPlatforms: string[] | undefined = uploadResult?.ai_analysis?.partner_metadata?.platforms;
  const platformOptions = [
    'All',
    ...(metadataPlatforms && metadataPlatforms.length > 0 ? metadataPlatforms : availablePlatforms),
  ];
  const activePlatform = platformOptions.includes(selectedPlatform) ? selectedPlatform : 'All';
  // Display names come from services/platforms.ts, not from a regex that
  // happened to turn "UberEats" into "Uber Eats". "All" and any platform the
  // app does not know are shown unchanged.
  const formatPlatformLabel = platformDisplayName;
  const showPlatformColumn = activePlatform === 'All' && platformOptions.length > 2;

  // Filter predictions by selected platform
  const filteredPredictions = useMemo(() => {
    if (activePlatform === 'All') return allPredictions;
    return allPredictions.filter(p => p.platform === activePlatform);
  }, [allPredictions, activePlatform]);

  // Dates live on the transactions; predictions reference them by id.
  const transactionDates = useMemo(() => {
    const map = new Map<string, string>();
    for (const t of uploadResult?.ai_analysis?.transactions ?? []) {
      if (t?.transaction_id && t?.transaction_date) map.set(t.transaction_id, t.transaction_date);
    }
    return map;
  }, [uploadResult]);

  // One row per checked order. Every number comes from the engine's output;
  // shortfalls are read from the amounts, not from the result name.
  const rows = useMemo((): OrderRow[] => {
    return filteredPredictions
      .filter(p => p.anomaly_type !== UNMATCHED_PAYMENT)
      .map((p, index) => {
        const earned = auditNumber(p.expected_amount);
        const paid = auditNumber(p.settlement_amount);
        const difference = paid - earned;
        const known = RESULT_LABELS[p.anomaly_type];
        const label = known ?? (difference < 0 ? 'Paid less' : difference > 0 ? 'Paid more' : 'Paid correctly');
        let tone: Tone = 'good';
        if (difference < 0) tone = 'bad';
        else if (difference > 0) tone = 'warn';
        else if (p.anomaly_type !== 'perfect_match' && known) tone = 'warn';
        return {
          key: `${p.transaction_id}-${index}`,
          date: p.transaction_date ?? transactionDates.get(p.transaction_id),
          orderId: p.order_id || p.transaction_id,
          platform: p.platform,
          earned,
          paid,
          difference,
          label,
          tone,
        };
      })
      .sort((a, b) =>
        TONE_ORDER[a.tone] - TONE_ORDER[b.tone] ||
        a.difference - b.difference ||
        (a.date ?? '').localeCompare(b.date ?? '')
      );
  }, [filteredPredictions, transactionDates]);

  const unmatchedPayments = filteredPredictions.filter(p => p.anomaly_type === UNMATCHED_PAYMENT);

  // Sort the engine's gaps into what the partner needs to act on.
  const { notChecked, otherNotes, missingFields } = useMemo(() => {
    const notCheckedItems: string[] = [];
    const notes: string[] = [];
    const fieldCounts = new Map<string, number>();
    for (const raw of evidenceGaps) {
      const prefix = raw.match(PLATFORM_PREFIX_RE);
      if (prefix && activePlatform !== 'All' && prefix[1] !== activePlatform) continue;
      const gap = raw.replace(PLATFORM_PREFIX_RE, '').trim();

      const field = gap.match(MISSING_FIELD_RE);
      if (field) {
        fieldCounts.set(field[1], (fieldCounts.get(field[1]) ?? 0) + 1);
        continue;
      }
      if (gap.includes(NOT_CHECKED_PREFIX)) {
        const detail = gap.split(NOT_CHECKED_PREFIX)[1].trim();
        const m = detail.match(/(earning|settlement) for order (\S+) \((.+?)\)/);
        notCheckedItems.push(
          m ? `${m[2]}: ${m[1]} of ${m[3]} has no date on the document` : detail
        );
        continue;
      }
      // Already shown as an unmatched payment below.
      if (/^Settlement .+ has no matching earning record\.?$/.test(gap)) continue;
      notes.push(gap);
    }
    return {
      notChecked: notCheckedItems,
      otherNotes: notes,
      missingFields: Array.from(fieldCounts.entries()).map(([name, count]) => ({
        label: FIELD_LABELS[name] ?? name,
        count,
      })),
    };
  }, [evidenceGaps, activePlatform]);

  // Totals over the orders that could actually be checked.
  const totalEarned = rows.reduce((sum, r) => sum + r.earned, 0);
  const totalPaid = rows.reduce((sum, r) => sum + r.paid, 0);
  const shortBy = rows.reduce((sum, r) => sum + Math.max(0, -r.difference), 0);
  const overBy = rows.reduce((sum, r) => sum + Math.max(0, r.difference), 0);
  const underpaidCount = rows.filter(r => r.difference < 0).length;
  const overpaidCount = rows.filter(r => r.difference > 0).length;
  const uncheckedCount = notChecked.length + unmatchedPayments.length + otherNotes.length;

  // Status and the one-sentence verdict.
  let status = 'Paid correctly';
  let tone: Tone = 'good';
  let headline = `You were paid correctly for all ${rows.length} ${plural(rows.length, 'order', 'orders')}.`;
  const details: string[] = [];

  if (rows.length === 0) {
    status = uncheckedCount > 0 ? 'Needs review' : 'No data';
    tone = uncheckedCount > 0 ? 'warn' : 'neutral';
    headline = uncheckedCount > 0
      ? 'We could not check any order yet.'
      : 'No audit data for this platform.';
    if (uncheckedCount > 0) details.push('See what is missing below.');
  } else if (shortBy > 0) {
    status = 'Underpaid';
    tone = 'bad';
    headline = `You were paid ${formatCurrency(shortBy)} less than you earned.`;
    details.push(`${underpaidCount} of ${rows.length} checked ${plural(rows.length, 'order', 'orders')} ${plural(underpaidCount, 'was', 'were')} paid less.`);
    if (overpaidCount > 0) {
      details.push(`${overpaidCount} ${plural(overpaidCount, 'order was', 'orders were')} paid ${formatCurrency(overBy)} more.`);
    }
  } else if (overBy > 0) {
    status = 'Overpaid';
    tone = 'warn';
    headline = `You were paid ${formatCurrency(overBy)} more than you earned.`;
    details.push(`${overpaidCount} of ${rows.length} checked ${plural(rows.length, 'order', 'orders')} ${plural(overpaidCount, 'was', 'were')} paid more. This may be a platform mistake.`);
  } else if (uncheckedCount > 0) {
    status = 'Partly checked';
    tone = 'warn';
    headline = `The ${rows.length} ${plural(rows.length, 'order', 'orders')} we could check ${plural(rows.length, 'was', 'were')} paid correctly.`;
  }
  if (rows.length > 0 && uncheckedCount > 0) {
    details.push(`${uncheckedCount} ${plural(uncheckedCount, 'item', 'items')} could not be checked. See below.`);
  }

  // What to do next
  const platformName = activePlatform !== 'All' ? formatPlatformLabel(activePlatform) : 'your platform';
  const actions: string[] = [];
  if (shortBy > 0) {
    actions.push(`Contact ${platformName} support and ask for the missing ${formatCurrency(shortBy)}.`);
    actions.push('Show them the orders marked "Paid less" in the table above.');
  }
  if (overBy > 0) {
    actions.push(`Check with ${platformName} before spending the extra ${formatCurrency(overBy)}. It may be taken back later.`);
  }
  if (uncheckedCount > 0) {
    actions.push('Send the missing earning screens, with their dates, to the FairPay bot and run the audit again.');
  }
  if (shortBy > 0 || overBy > 0) {
    actions.push('Keep your screenshots and payment records as proof.');
  }

  // Which kind of evidence this result rests on (see backend evidence_modes.py).
  const analysis = uploadResult?.ai_analysis;
  const evidenceMode: string | undefined =
    analysis?.evidence_mode ?? (analysis?.source === 'telegram' ? 'full_audit' : undefined);
  const inPlatform = (p?: string | null) => activePlatform === 'All' || !p || p === activePlatform;
  const settlementResults: SettlementReconciliation[] = (analysis?.settlement_reconciliation ?? [])
    .filter((r: SettlementReconciliation) => inPlatform(r.platform));
  const spotChecks: SpotCheck[] = (analysis?.spot_check ?? []).filter((c: SpotCheck) => inPlatform(c.platform));
  // FairPay picks how each app's files are checked; one result can hold more
  // than one kind (e.g. totals for one app, single orders for another).
  const modesByPlatform: Record<string, string> = analysis?.evidence_modes ?? {};
  const platformMode = activePlatform !== 'All' ? modesByPlatform[activePlatform] : undefined;
  const showTotals = evidenceMode === 'settlement_reconciliation' || settlementResults.length > 0;
  const showSpotCheck = evidenceMode === 'contract_spot_check'
    && (platformMode === undefined || platformMode === 'contract_spot_check');
  const showOrders = evidenceMode !== 'settlement_reconciliation' && evidenceMode !== 'contract_spot_check'
    && platformMode !== 'settlement_reconciliation';
  const questions = (analysis?.contract_questions ?? []).filter((q: { platform: string }) => inPlatform(q.platform));
  const limits: EvidenceLimit[] = analysis?.could_not_check ?? [];
  const modeNotes: string[] = analysis?.mode_notes ?? [];

  let modeTitle = 'Earnings Audit';
  if (evidenceMode === 'settlement_reconciliation') modeTitle = 'Settlement Reconciliation';
  else if (evidenceMode === 'contract_spot_check') {
    const checked = spotChecks.reduce((sum, c) => sum + c.sample_orders_checked, 0);
    modeTitle = `Contract Spot Check — ${checked} ${plural(checked, 'order', 'orders')} checked`;
  } else if (evidenceMode === 'full_audit') modeTitle = 'Full Audit';

  // Get partner info
  let partnerName = 'Delivery Partner';
  let auditPeriod = 'N/A';

  if (demoAuditResult && !uploadResult) {
    partnerName = demoAuditResult.partnerName;
    auditPeriod = demoAuditResult.shiftDate;
  } else if (uploadResult?.ai_analysis) {
    const result = uploadResult.ai_analysis;
    if (result.partner_metadata?.name) partnerName = result.partner_metadata.name;
    else if (result.partner_profile?.name) partnerName = result.partner_profile.name;
    if (result.transactions && result.transactions.length > 0) {
      const dates = result.transactions
        .map((t: any) => t.transaction_date)
        .filter(Boolean)
        .map((d: string) => new Date(d))
        .filter((d: Date) => !isNaN(d.getTime()));
      if (dates.length > 0) {
        const minDate = new Date(Math.min(...dates.map((d: Date) => d.getTime())));
        const maxDate = new Date(Math.max(...dates.map((d: Date) => d.getTime())));
        const fmt = (d: Date) => d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
        auditPeriod = minDate.getTime() === maxDate.getTime() ? fmt(minDate) : `${fmt(minDate)} – ${fmt(maxDate)}`;
      }
    }
    const periods = settlementResults.filter(r => r.period_start);
    if (periods.length > 0) {
      const start = periods.map(r => r.period_start as string).sort()[0];
      const end = periods.map(r => r.period_end as string).sort().slice(-1)[0];
      auditPeriod = start === end ? formatDate(start) : `${formatDate(start)} – ${formatDate(end)}`;
    }
  }

  // Only orders that were not paid correctly are listed; correct ones are counted.
  const problemRows = rows.filter(r => r.tone !== 'good');
  const visibleRows = showAllRows ? problemRows : problemRows.slice(0, ROW_LIMIT);

  return (
    <div className="simplified-audit-result">
      <div className="audit-result-header">
        <h3>FairPay — {modeTitle}</h3>
        <div className="audit-meta-simple">
          <div><strong>Partner:</strong> {partnerName}</div>
          <div>
            <label htmlFor="audit-platform-filter"><strong>Platform:</strong></label>
            <select
              id="audit-platform-filter"
              value={activePlatform}
              onChange={(e) => {
                setSelectedPlatform(e.target.value);
                setShowAllRows(false);
              }}
              className="platform-dropdown"
              aria-label="Select platform to filter audit results"
            >
              {platformOptions.map(opt => (
                <option key={opt} value={opt}>{formatPlatformLabel(opt)}</option>
              ))}
            </select>
          </div>
          <div><strong>Period:</strong> {auditPeriod}</div>
        </div>
      </div>

      {/* Hours worked: all apps, or the app picked in the filter above */}
      {analysis && <HoursWorkedView hours={analysis.hours_worked} platform={activePlatform} />}

      {showTotals && <SettlementReconciliationView results={settlementResults} />}
      {showSpotCheck && <SpotCheckView checks={spotChecks} />}

      {showOrders && (<>
      {/* 1. The answer, in one sentence */}
      <div className={`audit-verdict tone-${tone}`} role="status">
        <span className={`audit-badge tone-${tone}`}>{status}</span>
        <p className="audit-verdict-title">{headline}</p>
        {details.map((line, idx) => (
          <p key={idx} className="audit-verdict-sub">{line}</p>
        ))}
      </div>

      {/* 2. The money */}
      {rows.length > 0 && (
        <div className="audit-result-summary">
          <div className="result-grid">
            <div className="result-item">
              <span className="result-label">You earned</span>
              <span className="result-value">{formatCurrency(totalEarned)}</span>
            </div>
            <div className="result-item">
              <span className="result-label">You were paid</span>
              <span className="result-value">{formatCurrency(totalPaid)}</span>
            </div>
            {shortBy > 0 && (
              <div className="result-item highlight">
                <span className="result-label">Paid less by</span>
                <span className="result-value tone-text-bad">{formatCurrency(shortBy)}</span>
              </div>
            )}
            {overBy > 0 && (
              <div className="result-item highlight-warn">
                <span className="result-label">Paid more by</span>
                <span className="result-value tone-text-warn">{formatCurrency(overBy)}</span>
              </div>
            )}
            <div className="result-item">
              <span className="result-label">Orders checked</span>
              <span className="result-value">
                {rows.length}
                {uncheckedCount > 0 && <span className="result-value-note"> · {uncheckedCount} not checked</span>}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* 3. Order by order - only the orders with a problem */}
      {rows.length > 0 && problemRows.length === 0 && (
        <div className="audit-section">
          <h4>Order by order</h4>
          <p className="evidence-mode-caption">
            All {rows.length} {plural(rows.length, 'order was', 'orders were')} paid correctly, so none are listed here.
          </p>
        </div>
      )}
      {problemRows.length > 0 && (
        <div className="audit-section">
          <h4>Order by order</h4>
          <p className="evidence-mode-caption">
            {problemRows.length} of {rows.length} {plural(rows.length, 'order', 'orders')} had a problem.
            The other {rows.length - problemRows.length} {plural(rows.length - problemRows.length, 'was', 'were')} paid
            correctly and {plural(rows.length - problemRows.length, 'is', 'are')} not listed.
          </p>
          <div className="audit-table-wrap">
            <table className="audit-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Order</th>
                  {showPlatformColumn && <th>Platform</th>}
                  <th className="num">Earned</th>
                  <th className="num">Paid</th>
                  <th className="num">Difference</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                {visibleRows.map(row => (
                  <tr key={row.key} className={row.tone === 'bad' ? 'row-bad' : undefined}>
                    <td>{formatDate(row.date)}</td>
                    <td className="mono">{row.orderId}</td>
                    {showPlatformColumn && <td>{row.platform ? formatPlatformLabel(row.platform) : '—'}</td>}
                    <td className="num">{formatCurrency(row.earned)}</td>
                    <td className="num">{formatCurrency(row.paid)}</td>
                    <td className={`num tone-text-${row.difference < 0 ? 'bad' : row.difference > 0 ? 'warn' : 'muted'}`}>
                      {formatSigned(row.difference)}
                    </td>
                    <td><span className={`audit-badge tone-${row.tone}`}>{row.label}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {problemRows.length > ROW_LIMIT && (
            <button type="button" className="audit-show-all" onClick={() => setShowAllRows(!showAllRows)}>
              {showAllRows ? 'Show fewer orders' : `Show all ${problemRows.length} orders with a problem`}
            </button>
          )}
        </div>
      )}

      {/* 4. What could not be checked - always visible when present */}
      {uncheckedCount > 0 && (
        <div className="audit-section">
          <h4>Could not be checked</h4>
          <div className="audit-unchecked">
            {notChecked.length > 0 && (
              <div className="audit-unchecked-group">
                <p className="audit-unchecked-title">Orders with no date</p>
                <p className="audit-unchecked-hint">We need a date to match an order with its payment.</p>
                <ul>
                  {notChecked.map((item, idx) => <li key={idx}>{item}</li>)}
                </ul>
              </div>
            )}
            {unmatchedPayments.length > 0 && (
              <div className="audit-unchecked-group">
                <p className="audit-unchecked-title">Payments with no earning record</p>
                <p className="audit-unchecked-hint">You were paid for these, but we did not get what you earned for them.</p>
                <ul>
                  {unmatchedPayments.map((p, idx) => (
                    <li key={idx}>
                      <span className="mono">{p.order_id || p.transaction_id}</span>
                      {': '}
                      {formatCurrency(auditNumber(p.settlement_amount))} paid
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {otherNotes.length > 0 && (
              <div className="audit-unchecked-group">
                <p className="audit-unchecked-title">Other notes</p>
                <ul>
                  {otherNotes.map((note, idx) => <li key={idx}>{note}</li>)}
                </ul>
              </div>
            )}
          </div>
        </div>
      )}

      {/* 5. Optional details that would sharpen the audit */}
      {missingFields.length > 0 && (
        <details className="audit-details">
          <summary>Details that would make this audit more accurate ({missingFields.length})</summary>
          <ul>
            {missingFields.map(f => (
              <li key={f.label}>
                <strong>{f.label}</strong> was missing for {f.count} {plural(f.count, 'order', 'orders')}.
              </li>
            ))}
          </ul>
          <p>The audit still ran without these. Adding them lets us check distance and waiting pay too.</p>
        </details>
      )}

      {/* 6. Next steps */}
      {actions.length > 0 && (
        <div className="audit-section">
          <h4>What to do next</h4>
          <ol className="audit-steps">
            {actions.map((action, idx) => <li key={idx}>{action}</li>)}
          </ol>
        </div>
      )}

      {(shortBy > 0 || overBy > 0) && (
        <p className="audit-disclaimer-simple">
          <strong>Note:</strong> These amounts are based only on the documents you provided.
          Check with your platform before taking action.
        </p>
      )}
      </>)}

      {/* Shared by every evidence mode */}
      <ContractQuestions questions={questions} />
      {modeNotes.length > 0 && (
        <div className="audit-section">
          {modeNotes.map((note, idx) => <p key={idx} className="evidence-mode-caption">{note}</p>)}
        </div>
      )}
      <CouldNotCheck limits={limits} platform={activePlatform} />
    </div>
  );
};

export default SimplifiedAuditResult;
