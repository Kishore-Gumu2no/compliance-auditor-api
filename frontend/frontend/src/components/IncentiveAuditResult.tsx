import React from 'react';
import type { IncentiveCondition, IncentiveFitPlatform, IncentiveFitResult, IncentiveReport } from '../services/api';

/**
 * Two short answers, in plain words:
 *   1. Why didn't I get my incentive?  (what it needed / what FairPay found)
 *   2. Which incentive structure fits you better?  (a few lines and a small table)
 *
 * Display only. Every number comes from the backend response; nothing is
 * recalculated here and nothing is invented. A line is shown only when the
 * response actually contains the value for it.
 */

const WHY_HEADING = "Why didn't I get my incentive?";
const FIT_HEADING = 'Which incentive structure fits you better?';

// Conditions worth showing as a simple requirement. Everything else
// (rule notes, unpublished terms) is left out on purpose.
const SIMPLE_CONDITIONS = new Set([
  'acceptance_rate',
  'max_rejections',
  'login_minutes',
  'login_in_window',
  'login_windows',
  'orders_in_window',
  'rating',
  'payout_target',
]);

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);
const rupees = (value: string | number) => `₹${Number(value).toLocaleString('en-IN')}`;

function requiredLines(report: IncentiveReport): string[] {
  const primary = report.incentive_audit?.primary;
  if (!primary) return [];
  const lines: string[] = [];
  const target = primary.incentive?.target_orders;
  const reward = primary.incentive?.reward_amount;
  if (target) lines.push(`Complete ${target} ${plural(target, 'order', 'orders')}`);
  if (reward !== null && reward !== undefined && Number(reward) > 0) lines.push(`Earn ${rupees(reward)}`);

  const all: IncentiveCondition[] = [
    ...(primary.conditions?.passed ?? []),
    ...(primary.conditions?.failed ?? []),
    ...(primary.conditions?.unknown ?? []),
  ];
  for (const c of all) {
    if (SIMPLE_CONDITIONS.has(c.condition) && c.required) lines.push(`${c.label}: ${c.required}`);
  }
  return lines;
}

function foundLines(report: IncentiveReport): string[] {
  const primary = report.incentive_audit?.primary;
  if (!primary) return [];
  const lines: string[] = [];
  const counting = primary.counting;
  const target = primary.incentive?.target_orders ?? null;

  let done: number | null = null;
  if (counting) {
    done = counting.platform_count ?? counting.min_count;
    if (counting.platform_count === null && counting.min_count !== counting.max_count) {
      lines.push(`You completed ${counting.min_count} to ${counting.max_count} orders`);
    } else {
      lines.push(`You completed ${done} ${plural(done, 'order', 'orders')}`);
    }
  }

  if (target && done !== null && counting && counting.min_count === counting.max_count) {
    if (done >= target) lines.push('You did enough orders');
    else lines.push(`You were ${target - done} ${plural(target - done, 'order', 'orders')} short`);
  } else if (primary.shortfall) {
    lines.push(`You were ${primary.shortfall} ${plural(primary.shortfall, 'order', 'orders')} short`);
  }

  for (const c of primary.conditions?.failed ?? []) {
    if (SIMPLE_CONDITIONS.has(c.condition)) lines.push(`${c.label}: you had ${c.observed}`);
  }

  if (primary.status === 'INCENTIVE_RECEIVED') {
    const paid = primary.payment?.amount;
    lines.push(paid ? `You got ${rupees(paid)}` : 'You got this bonus');
  } else if (primary.status === 'POSSIBLE_PAYMENT_FAILURE' || primary.status === 'PAYMENT_NOT_OBSERVED') {
    // Every condition was met; the backend's message for this case.
    lines.push(primary.headline);
  } else if (primary.status === 'AMBIGUOUS') {
    lines.push('We could not give one clear answer');
  }

  if (lines.length === 0) lines.push('We could not tell for this day');
  return lines;
}

const bonusOf = (p: IncentiveFitPlatform): string | null =>
  p.reward_amounts?.[0] && Number(p.reward_amounts[0]) > 0 ? rupees(p.reward_amounts[0]) : null;

function reachedState(p: IncentiveFitPlatform): 'yes' | 'maybe' | 'no' {
  if (p.reached) return 'yes';
  if (p.target_met_max > 0) return 'maybe';
  return 'no';
}

const REACHED_TEXT = { yes: 'Yes', maybe: 'Not sure', no: 'No' } as const;

/** The 30-day audit compares several days; the single-day check compares one. */
const isMultiDay = (fit: IncentiveFitResult) => fit.comparison_unit === 'days';

const daysText = (p: IncentiveFitPlatform) => `${p.target_met_min} of ${p.periods_compared} days`;

/** The one-line message under the platform name, from the platform's own numbers. */
function recommendText(winner: IncentiveFitPlatform, done: number | null, multi: boolean): string {
  if (multi) {
    return winner.reached
      ? `You reached the target on ${daysText(winner)}.`
      : `You did not reach any target on these ${winner.periods_compared} days. This was the closest.`;
  }
  const target = winner.target_orders?.[0];
  const state = reachedState(winner);
  if (state === 'yes') {
    return done !== null ? `You reached the target with ${done} ${plural(done, 'order', 'orders')}.` : 'You reached the target.';
  }
  if (state === 'maybe') return 'You may have reached the target.';
  const short = winner.shortfall ?? (target && done !== null ? target - done : null);
  return short ? `You were ${short} ${plural(short, 'order', 'orders')} short, the closest of the three.` : 'This was the closest.';
}

const FitSection: React.FC<{ fit: IncentiveFitResult }> = ({ fit }) => {
  const rows = fit.status === 'INSUFFICIENT_DATA' ? [] : fit.platforms.filter((p) => p.target_orders?.[0]);
  const winner = rows.find((p) => p.platform === fit.recommended_platform);
  const done = fit.rider_pattern?.median_orders ?? null;
  const hasBonus = rows.some((p) => bonusOf(p) !== null);
  const multi = isMultiDay(fit);
  const when = multi ? 'these days' : 'this day';

  const label = winner ? (winner.reached ? 'Best fit' : 'Closest fit') : 'No clear winner';
  const title = winner ? winner.platform : fit.status === 'INSUFFICIENT_DATA' ? 'Not enough to compare' : 'It is a tie';
  const text = winner
    ? recommendText(winner, done, multi)
    : fit.status === 'INSUFFICIENT_DATA'
      ? `We could not compare for ${when}.`
      : `No platform looks clearly better for ${when}.`;

  const stats: { name: string; value: string }[] = [];
  if (done !== null) stats.push({ name: multi ? 'Orders a day' : 'Your orders', value: String(done) });
  if (winner?.target_orders?.[0]) stats.push({ name: 'Target', value: String(winner.target_orders[0]) });
  if (winner && bonusOf(winner)) stats.push({ name: 'Bonus', value: bonusOf(winner) as string });
  if (!winner && done === null) stats.length = 0;

  return (
    <div className="incentive-block">
      <h3 className="incentive-question">{FIT_HEADING}</h3>

      <div className="incentive-recommend">
        <div>
          <div className="incentive-recommend-label">{label}</div>
          <div className="incentive-recommend-name">{title}</div>
          <p className="incentive-recommend-text">{text}</p>
        </div>
        {stats.length > 0 && (
          <div className="incentive-recommend-stats">
            {stats.map((st) => (
              <div className="incentive-recommend-stat" key={st.name}>
                <span>{st.name}</span>
                <span>{st.value}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {rows.length > 0 && (
        <div className="incentive-table-wrap">
          <table className="incentive-table">
            <thead>
              <tr>
                <th>Platform</th>
                <th>Orders needed</th>
                {hasBonus && <th>Bonus</th>}
                <th>Did you reach it?</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => {
                const state = reachedState(p);
                return (
                  <tr key={p.platform} className={p === winner ? 'is-highlight' : ''}>
                    <td>{p.platform}</td>
                    <td className="is-number">{p.target_orders[0]}</td>
                    {hasBonus && <td className="is-number">{bonusOf(p) ?? '—'}</td>}
                    <td>
                      <span className={`incentive-yesno is-${state}`}>{multi ? daysText(p) : REACHED_TEXT[state]}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

interface Props {
  report: IncentiveReport;
  busy?: boolean;
}

const IncentiveAuditResult: React.FC<Props> = ({ report, busy }) => {
  const audit = report.incentive_audit;
  const fit = report.incentive_fit;
  const required = requiredLines(report);
  const found = foundLines(report);

  return (
    <div className="incentive-result" aria-busy={busy}>
      {audit && (
        <div className="incentive-block">
          <h3 className="incentive-question">{WHY_HEADING}</h3>
          <div className="incentive-grid">
            <div className="incentive-card">
              <h4 className="incentive-subtitle">What the incentive required</h4>
              {required.length > 0 ? (
                <ul className="incentive-list">
                  {required.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              ) : (
                <p>We could not find what this incentive needs.</p>
              )}
            </div>
            <div className="incentive-card">
              <h4 className="incentive-subtitle">What FairPay found</h4>
              <ul className="incentive-list">
                {found.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      )}

      {fit && <FitSection fit={fit} />}
    </div>
  );
};

export default IncentiveAuditResult;
