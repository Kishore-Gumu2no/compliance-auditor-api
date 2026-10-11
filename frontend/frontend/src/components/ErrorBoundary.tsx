import { Component, type ErrorInfo, type ReactNode } from 'react';

/*
 * Catches a render failure anywhere below it and shows a distinct error state.
 *
 * Without this, one bad value in an audit result - a null where an object was
 * expected, a number where a string was - unmounts the whole tree and leaves a
 * blank white page. A partner then cannot tell a broken app from an app with
 * nothing to show, and neither can anyone debugging it.
 *
 * This has to be a class: `componentDidCatch` has no hook equivalent.
 */

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Shown in the heading, so the partner knows which part failed. */
  label?: string;
  /** Rendered instead of the default panel, when a caller wants its own. */
  fallback?: (error: Error, reset: () => void) => ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // The component stack is what makes one of these diagnosable; React only
    // provides it here.
    console.error(
      `[FairPay] render failed in ${this.props.label ?? 'the app'}:`,
      error,
      info.componentStack,
    );
  }

  private reset = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback) return this.props.fallback(error, this.reset);

    return (
      <div className="error-boundary" role="alert">
        <div className="error-boundary-icon" aria-hidden="true">⚠️</div>
        <h3 className="error-boundary-title">
          Something went wrong{this.props.label ? ` in ${this.props.label}` : ''}
        </h3>
        <p className="error-boundary-text">
          FairPay could not display this. Nothing you sent has been lost, and no
          audit figure shown elsewhere on this page is affected.
        </p>
        {/* The message is kept visible on purpose: a partner reporting this is
            the fastest route to a fix, and it is not sensitive. */}
        <p className="error-boundary-detail">{error.message || String(error)}</p>
        <div className="error-boundary-actions">
          <button type="button" className="primary-button" onClick={this.reset}>
            Try again
          </button>
          <button
            type="button"
            className="telegram-notice-dismiss"
            onClick={() => window.location.reload()}
          >
            Reload the page
          </button>
        </div>
      </div>
    );
  }
}

export default ErrorBoundary;
