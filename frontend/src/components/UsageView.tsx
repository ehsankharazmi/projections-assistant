import { useCallback, useEffect, useState } from "react";
import type { UsageSummary } from "../api";
import { api } from "../api";
import { formatDate, formatUsd } from "../format";
import { ErrorNotice, Spinner, toUiError, type UiError } from "./common";

export function UsageView({ onClose }: { onClose: () => void }) {
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [error, setError] = useState<UiError | null>(null);
  const [check, setCheck] = useState<Record<string, unknown> | null>(null);
  const [checking, setChecking] = useState(false);

  const load = useCallback(() => {
    api.usage().then(setUsage, (e) => setError(toUiError(e)));
  }, []);
  useEffect(load, [load]);

  const runCheck = async (live: boolean) => {
    if (live && !window.confirm("The live check sends one very small message to the Claude API and costs a small amount. Continue?")) return;
    setChecking(true);
    setError(null);
    try {
      setCheck(await api.aiCheck(live));
      load();
    } catch (e) {
      setError(toUiError(e));
    } finally {
      setChecking(false);
    }
  };

  return (
    <section className="setup" aria-labelledby="usage-title">
      <div className="setup-head">
        <h2 id="usage-title">AI usage and settings (owner only)</h2>
        <button type="button" onClick={onClose}>
          Back
        </button>
      </div>
      {error && <ErrorNotice error={error} onDismiss={() => setError(null)} />}
      {!usage ? (
        <Spinner label="Loading usage…" />
      ) : (
        <>
          <section className="card">
            <p>
              Mode: <strong>{usage.ai_mode}</strong> · Model: {usage.model} · Effort: {usage.effort} · Refusal fallback: {usage.refusal_fallback}
            </p>
            {usage.note && <p className="muted">{usage.note}</p>}
            {usage.label && <p className="notice small">{usage.label}</p>}
            {usage.prices_verified === false && (
              <p className="notice small">The price table has not been marked as verified against the official pricing page.</p>
            )}
            {usage.today && usage.month && (
              <div className="cards">
                <div className="result-card">
                  <div className="small muted">Today (estimated)</div>
                  <div className="result-value">{formatUsd(usage.today.estimated_usd)}</div>
                  <div className="small">
                    of {formatUsd(usage.today.budget_usd)} budget · {formatUsd(usage.today.open_reservations_usd)} reserved
                  </div>
                </div>
                <div className="result-card">
                  <div className="small muted">This month (estimated)</div>
                  <div className="result-value">{formatUsd(usage.month.estimated_usd)}</div>
                  <div className="small">
                    of {formatUsd(usage.month.budget_usd)} budget · {formatUsd(usage.month.open_reservations_usd)} reserved
                  </div>
                </div>
                <div className="result-card">
                  <div className="small muted">Requests with uncertain usage</div>
                  <div className="result-value">{usage.uncertain_requests ?? 0}</div>
                </div>
              </div>
            )}
          </section>

          <section className="card">
            <h3>Connection check</h3>
            <div className="row-actions">
              <button type="button" disabled={checking || usage.ai_mode !== "anthropic"} onClick={() => runCheck(false)}>
                Check key and model (no cost)
              </button>
              <button type="button" disabled={checking || usage.ai_mode !== "anthropic"} onClick={() => runCheck(true)}>
                Live check (small cost)
              </button>
            </div>
            {checking && <Spinner label="Checking…" />}
            {check && <pre className="diag-pre">{JSON.stringify(check, null, 2)}</pre>}
          </section>

          {usage.recent && usage.recent.length > 0 && (
            <section className="card table-wrap">
              <h3>Recent requests</h3>
              <table>
                <thead>
                  <tr>
                    <th scope="col">Time</th>
                    <th scope="col">Status</th>
                    <th scope="col">Model</th>
                    <th scope="col">Input</th>
                    <th scope="col">Cache write / read</th>
                    <th scope="col">Output</th>
                    <th scope="col">Est. cost</th>
                    <th scope="col">Request ID</th>
                  </tr>
                </thead>
                <tbody>
                  {usage.recent.map((r) => (
                    <tr key={r.id}>
                      <td className="small">{formatDate(r.created_at)}</td>
                      <td>
                        {r.status}
                        {r.error_code ? ` (${r.error_code})` : ""}
                        {r.fallback_used ? " · fallback" : ""}
                      </td>
                      <td className="small">{r.served_model ?? r.requested_model}</td>
                      <td className="num">{r.input_tokens.toLocaleString()}</td>
                      <td className="num">
                        {r.cache_write_tokens.toLocaleString()} / {r.cache_read_tokens.toLocaleString()}
                      </td>
                      <td className="num">{r.output_tokens.toLocaleString()}</td>
                      <td className="num">{formatUsd(r.status === "reconciled" ? r.cost_usd : r.reserved_usd)}</td>
                      <td className="small">
                        <code>{r.request_id ?? "—"}</code>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </>
      )}
    </section>
  );
}
