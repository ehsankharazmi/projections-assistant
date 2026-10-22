import { useState } from "react";
import { ApiError } from "../api";

export interface UiError {
  message: string;
  code?: string;
  diagnosticId?: string;
  details?: Record<string, unknown>;
}

export function toUiError(err: unknown): UiError {
  if (err instanceof ApiError) {
    return { message: err.body.message, code: err.body.code, diagnosticId: err.body.diagnostic_id, details: err.body.details };
  }
  return { message: "Something unexpected happened in the page. Please reload and try again." };
}

export function ErrorNotice({ error, onDismiss }: { error: UiError; onDismiss?: () => void }) {
  const hasDiagnostics = Boolean(error.code || error.diagnosticId || error.details);
  return (
    <div className="notice notice-error" role="alert">
      <p>
        <strong>Problem: </strong>
        {error.message}
      </p>
      {hasDiagnostics && (
        <details>
          <summary>Diagnostic details</summary>
          <dl className="diag">
            {error.code && (
              <>
                <dt>Code</dt>
                <dd>{error.code}</dd>
              </>
            )}
            {error.diagnosticId && (
              <>
                <dt>Diagnostic ID</dt>
                <dd>{error.diagnosticId}</dd>
              </>
            )}
          </dl>
          {error.details && <pre className="diag-pre">{JSON.stringify(error.details, null, 2)}</pre>}
        </details>
      )}
      {onDismiss && (
        <button type="button" className="link-button" onClick={onDismiss}>
          Dismiss
        </button>
      )}
    </div>
  );
}

export function SourceToggle({ sheet, address }: { sheet: string; address: string }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="source">
      <button type="button" className="link-button small" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? "Hide source" : "Show source"}
      </button>
      {open && (
        <code className="source-ref">
          {sheet}!{address}
        </code>
      )}
    </span>
  );
}

export function StatusBadge({ status, label }: { status: string; label: string }) {
  return <span className={`badge badge-${status}`}>{label}</span>;
}

export function Spinner({ label }: { label: string }) {
  return (
    <span className="spinner-wrap" role="status">
      <span className="spinner" aria-hidden="true" />
      {label}
    </span>
  );
}
