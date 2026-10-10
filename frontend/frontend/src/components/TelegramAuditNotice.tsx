import React, { useEffect, useRef, useState } from 'react';
import {
  get_latest_telegram_audit,
  mark_telegram_audit_seen,
  type TelegramSubmissionSummary,
} from '../services/api';

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

  useEffect(() => {
    if (!partnerId) return;
    let cancelled = false;

    const poll = async () => {
      try {
        const latest = await get_latest_telegram_audit(partnerId);
        if (cancelled || !latest.submission || !latest.ai_analysis) return;
        // Only announce a submission we have not already shown.
        if (latest.submission.seen_on_website) return;
        if (lastSeenId.current === latest.submission.submission_id) return;
        lastSeenId.current = latest.submission.submission_id;
        setSubmission(latest.submission);
        setAnalysis(latest.ai_analysis);
        setDismissed(false);
      } catch {
        // A failed poll is not worth interrupting the page for.
      }
    };

    poll();
    const timer = setInterval(poll, pollIntervalMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [partnerId, pollIntervalMs]);

  const acknowledge = async () => {
    if (!submission) return;
    try {
      await mark_telegram_audit_seen(partnerId, submission.submission_id);
    } catch {
      // Acknowledging is best-effort; the notice still closes.
    }
  };

  const handleView = async () => {
    onView({ status: 'Audit Complete', ai_analysis: analysis });
    await acknowledge();
    setDismissed(true);
    const section = document.getElementById('audit-results');
    if (section) section.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const handleDismiss = async () => {
    await acknowledge();
    setDismissed(true);
  };

  if (!submission || dismissed) return null;

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
