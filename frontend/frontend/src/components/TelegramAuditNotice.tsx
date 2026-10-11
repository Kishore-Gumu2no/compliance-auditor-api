import React, { useEffect, useRef, useState } from 'react';
import {
  get_latest_telegram_audit,
  mark_telegram_audit_seen,
  type TelegramSubmissionSummary,
} from '../services/api';

// Consecutive failed polls before the partner is told. At the default 15s
// interval that is about 45 seconds of silence.
const POLL_FAILURES_BEFORE_REPORTING = 3;

interface TelegramAuditNoticeProps {
  partnerId: string;
  /** Hands the completed audit to the existing Audit Results display. */
  onView: (result: any) => void;
  pollIntervalMs?: number;
}

/**
 * Polls for audits submitted through the Telegram bot and announces them here.
 *
 * Polling is used deliberately: the WebSocket stream is frozen, so this does
 * not touch it. Results themselves are rendered by the existing Audit Results
 * components - this only notifies and hands the payload over.
 */
export const TelegramAuditNotice: React.FC<TelegramAuditNoticeProps> = ({
  partnerId,
  onView,
  pollIntervalMs = 15000,
}) => {
  const [submission, setSubmission] = useState<TelegramSubmissionSummary | null>(null);
  const [analysis, setAnalysis] = useState<any>(null);
  const [dismissed, setDismissed] = useState(false);
  const lastSeenId = useRef<string | null>(null);
  // Explicit fallback states, in place of two swallowed promises.
  const [pollError, setPollError] = useState<string | null>(null);
  const [ackError, setAckError] = useState<string | null>(null);
  const consecutiveFailures = useRef(0);

  useEffect(() => {
    if (!partnerId) return;
    let cancelled = false;

    const poll = async () => {
      try {
        const latest = await get_latest_telegram_audit(partnerId);
        if (cancelled) return;
        consecutiveFailures.current = 0;
        setPollError(null);
        if (!latest.submission || !latest.ai_analysis) return;
        // Only announce a submission we have not already shown.
        if (latest.submission.seen_on_website) return;
        if (lastSeenId.current === latest.submission.submission_id) return;
        lastSeenId.current = latest.submission.submission_id;
        setSubmission(latest.submission);
        setAnalysis(latest.ai_analysis);
        setDismissed(false);
      } catch (error: unknown) {
        if (cancelled) return;
        // One blip between polls is not worth a banner, but a run of them
        // means the partner is waiting for a notice that will never arrive -
        // so it is reported rather than swallowed.
        consecutiveFailures.current += 1;
        if (consecutiveFailures.current >= POLL_FAILURES_BEFORE_REPORTING) {
          setPollError(
            error instanceof Error
              ? `FairPay cannot check whether your Telegram audit has finished: ${error.message}`
              : 'FairPay cannot check whether your Telegram audit has finished.',
          );
        }
      }
    };

    poll();
    const timer = setInterval(poll, pollIntervalMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [partnerId, pollIntervalMs]);

  /** Marks the submission seen. Returns false when the server refused. */
  const acknowledge = async (): Promise<boolean> => {
    if (!submission) return true;
    try {
      await mark_telegram_audit_seen(partnerId, submission.submission_id);
      setAckError(null);
      return true;
    } catch (error: unknown) {
      // The server still has this submission marked unseen, so it will be
      // announced again. Saying so beats closing as if nothing happened.
      setAckError(
        error instanceof Error
          ? `This notice could not be marked as seen (${error.message}), so it may appear again.`
          : 'This notice could not be marked as seen, so it may appear again.',
      );
      return false;
    }
  };

  const handleView = async () => {
    onView({ status: 'Audit Complete', ai_analysis: analysis });
    const acknowledged = await acknowledge();
    // Stay open when the acknowledgement failed, so its explanation can
    // actually be read. The results are shown below either way.
    setDismissed(acknowledged);
    const section = document.getElementById('audit-results');
    if (section) section.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const handleDismiss = async () => {
    setDismissed(await acknowledge());
  };

  if (!submission || dismissed) {
    // No audit to announce, but polling is broken - which is itself worth
    // showing, because silence here is indistinguishable from "not ready yet".
    return pollError ? (
      <div className="telegram-notice" role="alert">
        <div className="telegram-notice-body">
          <span className="telegram-notice-icon">⚠️</span>
          <div className="telegram-notice-text">
            <strong>Cannot reach FairPay</strong>
            <p>{pollError}</p>
          </div>
        </div>
      </div>
    ) : null;
  }

  const platforms = submission.platforms ?? [];
  const missing = submission.missing_fields ?? [];

  return (
    <div className="telegram-notice" role="status">
      <div className="telegram-notice-body">
        <span className="telegram-notice-icon">📨</span>
        <div className="telegram-notice-text">
          <strong>
            Your Telegram audit is ready
            {analysis?.evidence_mode_label ? ` — ${analysis.evidence_mode_label}` : ''}
          </strong>
          <p>
            {platforms.length > 0
              ? `Documents you sent for ${platforms.join(', ')} have been audited.`
              : 'The documents you sent have been audited.'}
            {submission.completed_at &&
              ` Finished ${new Date(submission.completed_at).toLocaleString()}.`}
          </p>
          {missing.length > 0 && (
            <p className="telegram-notice-missing">
              Some details were missing: {missing.map((m) => m.field).join(', ')}. The audit
              ran with what was provided.
            </p>
          )}
        </div>
      </div>
      {ackError && (
        <p className="telegram-notice-missing" role="alert">{ackError}</p>
      )}
      <div className="telegram-notice-actions">
        <button type="button" className="primary-button" onClick={handleView}>
          View results
        </button>
        <button type="button" className="telegram-notice-dismiss" onClick={handleDismiss}>
          Dismiss
        </button>
      </div>
    </div>
  );
};

export default TelegramAuditNotice;
