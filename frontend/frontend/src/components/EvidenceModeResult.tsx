import React from 'react';
import type {
  ContractQuestion,
  EvidenceLimit,
  HoursWorked,
  SettlementReconciliation,
  SpotCheck,
} from '../services/api';
import { auditNumber } from '../services/demoData';

/*
 * Views for the evidence-mode parts of an audit result. Every figure and
 * sentence comes from the backend; nothing is summed across modes here.
 */

type Tone = 'good' | 'bad' | 'warn' | 'neutral';

// Two decimals where they matter (per-order amounts), none for whole rupees.
const money = (value: string | number | null | undefined) => {
  if (value === null || value === undefined || value === '') return '—';
  const n = auditNumber(value);
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    minimumFractionDigits: Number.isInteger(n) ? 0 : 2,
    maximumFractionDigits: 2,
  }).format(n);
};

const formatDay = (value?: string | null) => {
  if (!value) return '—';
  const d = new Date(value);
  return isNaN(d.getTime())
    ? value
    : d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
};

const SETTLEMENT_STATUS: Record<SettlementReconciliation['status'], { label: string; tone: Tone }> = {
  MATCH: { label: 'Match', tone: 'good' },
  SHORTFALL: { label: 'Less received', tone: 'bad' },
  AMBIGUOUS: { label: 'Cannot compare', tone: 'warn' },
  INSUFFICIENT_EVIDENCE: { label: 'Not enough evidence', tone: 'neutral' },
};

const COMPARABILITY: Record<SettlementReconciliation['comparability'], string> = {
  comparable: 'Comparable — the platform figure is printed as a payout',
  not_established: 'Not established — the two amounts may not be the same settlement concept',
  not_assessed: 'Not assessed — evidence missing',
};

export const SettlementReconciliationView: React.FC<{ results: SettlementReconciliation[] }> = ({ results }) => {
  if (results.length === 0) {
    return (
      <div className="audit-verdict tone-neutral" role="status">
        <span className="audit-badge tone-neutral">No data</span>
        <p className="audit-verdict-title">No settlement result for this platform.</p>
      </div>
    );
  }
  return (
    <>
      {results.map((r) => {
        const status = SETTLEMENT_STATUS[r.status];
        return (
          <div key={r.platform} className="evidence-mode-block">
            <div className={`audit-verdict tone-${status.tone}`} role="status">
              <span className={`audit-badge tone-${status.tone}`}>{status.label}</span>
              <p className="audit-verdict-sub evidence-mode-platform">
                {r.platform}
                {r.period_start && ` · ${formatDay(r.period_start)} – ${formatDay(r.period_end)}`}
              </p>
              <p className="audit-verdict-sub evidence-mode-statement">{r.statement}</p>
            </div>

            <div className="audit-result-summary">
              <div className="result-grid">
                <div className="result-item">
                  <span className="result-label">Declared by the platform</span>
                  <span className="result-value">{money(r.declared_total)}</span>
                  {r.declared_label && <span className="evidence-mode-caption">Labelled “{r.declared_label}”</span>}
                </div>
                <div className="result-item">
                  <span className="result-label">Received in your bank</span>
                  <span className="result-value">{money(r.received_total)}</span>
                  {r.credits_counted.length > 0 && (
                    <span className="evidence-mode-caption">
                      {r.credits_counted.length} credit{r.credits_counted.length === 1 ? '' : 's'} counted
                    </span>
                  )}
                </div>
                <div className={`result-item${r.status === 'SHORTFALL' ? ' highlight' : ''}`}>
                  <span className="result-label">Difference</span>
                  <span className={`result-value${r.status === 'SHORTFALL' ? ' tone-text-bad' : ''}`}>
                    {r.difference !== null ? money(r.difference) : 'Not established'}
                  </span>
                </div>
              </div>
              <p className="evidence-mode-caption evidence-mode-comparability">
                <strong>Comparability:</strong> {COMPARABILITY[r.comparability]}
              </p>
            </div>

            {r.missing_evidence.length > 0 && (
              <div className="audit-section">
                <h4>Evidence that would settle this</h4>
                <ul className="evidence-mode-list">
                  {r.missing_evidence.map((m, i) => <li key={i}>{m}</li>)}
                </ul>
              </div>
            )}

            {(r.credits_counted.length > 0 || r.credits_excluded.length > 0) && (
              <details className="audit-details">
                <summary>Bank credits ({r.credits_counted.length} counted, {r.credits_excluded.length} not counted)</summary>
                <ul>
                  {r.credits_counted.map((c, i) => (
                    <li key={`c${i}`}>
                      {formatDay(c.date)}: {money(c.amount)}
                      {c.payer_label && ` from “${c.payer_label}”`} — counted
                    </li>
                  ))}
                  {r.credits_excluded.map((c, i) => (
                    <li key={`x${i}`}>
                      {formatDay(c.date)}: {money(c.amount)} — not counted. {c.reason}
                    </li>
                  ))}
                </ul>
              </details>
            )}

            {(r.assumptions.length > 0 || r.notes.length > 0 || r.adjustments_shown.length > 0) && (
              <details className="audit-details">
                <summary>Assumptions and notes</summary>
                <ul>
                  {r.adjustments_shown.map((a, i) => (
                    <li key={`a${i}`}>Shown on the summary: {a.label} {money(a.amount)}</li>
                  ))}
                  {r.assumptions.map((a, i) => <li key={`s${i}`}>Assumption: {a}</li>)}
                  {r.notes.map((n, i) => <li key={`n${i}`}>{n}</li>)}
                </ul>
              </details>
            )}
          </div>
        );
      })}
      <p className="audit-disclaimer-simple">
        <strong>Note:</strong> This compares totals only. It is not a contract check and says nothing about
        how any single order was paid.
      </p>
    </>
  );
};

const ORDER_STATUS: Record<string, { label: string; tone: Tone }> = {
  possible_issue: { label: 'Possible issue', tone: 'bad' },
  no_issue_found: { label: 'No issue found', tone: 'good' },
  not_checked: { label: 'Not checked', tone: 'neutral' },
};

export const SpotCheckView: React.FC<{ checks: SpotCheck[] }> = ({ checks }) => {
  if (checks.length === 0) {
    return (
      <div className="audit-verdict tone-neutral" role="status">
        <span className="audit-badge tone-neutral">No data</span>
        <p className="audit-verdict-title">No spot-check result for this platform.</p>
      </div>
    );
  }
  return (
    <>
      {checks.map((c) => {
        const tone: Tone = c.orders_with_findings > 0 ? 'bad' : c.sample_orders_checked > 0 ? 'good' : 'neutral';
        // Only orders with a possible problem are listed; the rest are counted above.
        const orders = c.orders.filter((o) => o.status === 'possible_issue');
        return (
          <div key={c.platform} className="evidence-mode-block">
            <div className={`audit-verdict tone-${tone}`} role="status">
              <span className={`audit-badge tone-${tone}`}>{c.platform}</span>
              <p className="audit-verdict-title">{c.statement}</p>
              <p className="audit-verdict-sub">{c.sample_boundary}</p>
            </div>

            <div className="audit-result-summary">
              <div className="result-grid">
                <div className="result-item">
                  <span className="result-label">Orders checked</span>
                  <span className="result-value">{c.sample_orders_checked}</span>
                </div>
                <div className={`result-item${c.orders_with_findings > 0 ? ' highlight' : ''}`}>
                  <span className="result-label">Orders with a possible issue</span>
                  <span className={`result-value${c.orders_with_findings > 0 ? ' tone-text-bad' : ''}`}>
                    {c.orders_with_findings}
                  </span>
                </div>
                <div className="result-item">
                  <span className="result-label">Orders without findings</span>
                  <span className="result-value">{c.orders_without_findings}</span>
                </div>
                {c.orders_not_checked > 0 && (
                  <div className="result-item highlight-warn">
                    <span className="result-label">Could not be checked</span>
                    <span className="result-value tone-text-warn">{c.orders_not_checked}</span>
                  </div>
                )}
              </div>
            </div>

            <div className="audit-section">
              <h4>Order by order</h4>
              {orders.length === 0 ? (
                <p className="evidence-mode-caption">
                  No order showed a problem, so none are listed here.
                </p>
              ) : (
              <div className="audit-table-wrap">
                <table className="audit-table">
                  <thead>
                    <tr>
                      <th>Date</th>
                      <th>Order</th>
                      <th className="num">Shown</th>
                      <th>Result</th>
                      <th>Detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orders.map((o) => {
                      const s = ORDER_STATUS[o.status];
                      const detail = o.findings.length > 0
                        ? o.findings.join(' ')
                        : o.contract_check?.explanation ?? o.payment_check?.label ?? `Not checked: ${o.not_checked.join('; ')}`;
                      return (
                        <tr key={o.order_id} className={o.status === 'possible_issue' ? 'row-bad' : undefined}>
                          <td>{formatDay(o.date)}{o.time ? ` ${o.time}` : ''}</td>
                          <td className="mono">{o.order_id}</td>
                          <td className="num">{money(o.printed_amount)}</td>
                          <td><span className={`audit-badge tone-${s.tone}`}>{s.label}</span></td>
                          <td className="evidence-mode-detail">{detail}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              )}
              {c.contracts_used.length > 0 && (
                <p className="evidence-mode-caption">
                  Compared with: {c.contracts_used.map((u) => u.reference).join('; ')}
                </p>
              )}
            </div>
          </div>
        );
      })}
      <p className="audit-disclaimer-simple">
        <strong>Note:</strong> A possible issue is not a confirmed violation. These results cover only the
        orders you sent and are not an estimate for your whole month.
      </p>
    </>
  );
};

export const ContractQuestions: React.FC<{ questions: ContractQuestion[] }> = ({ questions }) => {
  if (questions.length === 0) return null;
  return (
    <div className="audit-section">
      <h4>Questions to ask the platform</h4>
      <p className="evidence-mode-caption">
        FairPay could not prove these were underpaid. Each question comes from a contract term, what your
        documents show, and what they do not show.
      </p>
      <div className="audit-unchecked">
        {questions.map((q, i) => (
          <div key={i} className="audit-unchecked-group evidence-question">
            <p className="audit-unchecked-title">{q.topic} · {q.platform}</p>
            <ul>
              <li><strong>Contract says:</strong> {q.contract_term}</li>
              <li><strong>Your documents show:</strong> {q.observed}</li>
              <li><strong>Not shown:</strong> {q.not_shown}</li>
            </ul>
            <p className="evidence-question-text">{q.question}</p>
          </div>
        ))}
      </div>
    </div>
  );
};

/**
 * Hours worked, as one figure that follows the platform filter:
 * "All" shows the total across every app, an app shows that app's hours.
 * When a figure is missing it says "Not available" and why.
 */
export const HoursWorkedView: React.FC<{ hours?: HoursWorked; platform?: string }> = ({ hours, platform }) => {
  const key = (name?: string | null) => (name ?? '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const allApps = !platform || platform === 'All';
  const appLabel = platform ? platform.replace(/([a-z])([A-Z])/g, '$1 $2') : '';

  let label = allApps ? 'Hours worked (all apps)' : `Hours worked on ${appLabel}`;
  let value: string | null = null;
  let note: string;

  if (!hours || hours.error) {
    note = hours?.error ?? 'Not available: this audit result does not include working hours.';
  } else if (allApps) {
    value = hours.all_apps_online_hours ?? null;
    // The figure speaks for itself; the explanatory note is not shown.
    note = value ? '' : (hours.all_apps_note ?? '');
  } else {
    const row = (hours.platforms ?? []).find((r) => key(r.platform) === key(platform));
    if (!row) {
      note = `Not available: no hours were found for ${appLabel}.`;
    } else if (row.online_hours !== null) {
      value = row.online_hours;
      note = row.online_hours_note;
    } else if (row.order_hours !== null) {
      // History data: login time is shared by all apps, so the app's own
      // figure is the time spent on its orders.
      value = row.order_hours;
      label = `Hours worked on ${appLabel} orders`;
      note = `${row.order_hours_note} ${row.online_hours_note}`;
    } else {
      note = row.online_hours_note;
    }
    if (value) note = '';
  }

  return (
    <div className="audit-result-summary hours-worked">
      <div className="result-grid">
        <div className="result-item">
          <span className="result-label">{label}</span>
          <span className={`result-value${value ? '' : ' tone-text-muted'}`}>{value ? `${value} h` : 'Not available'}</span>
          {note.trim() && <span className="evidence-mode-caption">{note.trim()}</span>}
        </div>
      </div>
    </div>
  );
};

export const CouldNotCheck: React.FC<{ limits: EvidenceLimit[]; platform?: string }> = ({ limits, platform }) => {
  const visible = limits.filter((l) => !platform || platform === 'All' || !l.platform || l.platform === platform);
  if (visible.length === 0) return null;
  const showPlatform = new Set(visible.map((l) => l.platform).filter(Boolean)).size > 1;
  return (
    <div className="audit-section">
      <h4>What FairPay could not check</h4>
      <ul className="evidence-limits">
        {visible.map((l, i) => (
          <li key={i}>
            <strong>{l.item}</strong>
            {showPlatform && l.platform ? ` (${l.platform})` : ''} — {l.reason}
          </li>
        ))}
      </ul>
    </div>
  );
};
