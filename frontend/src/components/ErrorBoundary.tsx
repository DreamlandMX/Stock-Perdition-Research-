import { Component } from "react";
import type { ReactNode } from "react";
import { useI18n } from "../i18n/context";
import { translate } from "../i18n/core";
import { RotateCcw, TriangleAlert } from "lucide-react";

export interface ErrorBoundaryProps {
  children: ReactNode;
  onReset?: () => void;
}

interface ErrorBoundaryState {
  failed: boolean;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { failed: true };
  }

  componentDidCatch() {
    // The UI intentionally does not expose upstream exception details.
  }

  private readonly reset = () => {
    if (this.props.onReset !== undefined) {
      this.setState({ failed: false });
      this.props.onReset();
      return;
    }
    window.location.reload();
  };

  render() {
    if (!this.state.failed) {
      return this.props.children;
    }

    return <ErrorFallback onReset={this.reset} />;
  }
}

function ErrorFallback({ onReset }: { onReset: () => void }) {
    const { locale } = useI18n();
    return (
      <section className="error-state" role="alert">
        <TriangleAlert aria-hidden="true" size={20} strokeWidth={1.5} />
        <div>
          <h1>{translate(locale, "common.shared.researchWorkbenchTemporarilyUnavailable")}</h1>
          <p>{translate(locale, "common.shared.anApplicationErrorOccurredReloadThePageUnverified")}</p>
          <button onClick={onReset} type="button">
            <RotateCcw aria-hidden="true" size={18} strokeWidth={1.5} />
            {translate(locale, "common.shared.reloadResearchWorkbench")}
          </button>
        </div>
      </section>
    );
}
