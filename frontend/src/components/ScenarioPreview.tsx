import { useRef, useState } from "react";
import type { Delta, Scenario, TableComparison } from "../api";
import { scenarioDownloadUrl } from "../api";
import { formatDelta, formatPercentChange, formatValue, signChangeLabel, statusLabel } from "../format";
import { SourceToggle, Spinner, StatusBadge } from "./common";

interface Props {
  scenario: Scenario;
  busy: boolean;
  onApply: (scenario: Scenario, idempotencyKey: string) => void;
  onRevise: () => void;
  onDiscard: (scenario: Scenario) => void;
  newKey: () => string;
}

function DeltaCells({ d, valueType, unit }: { d: Delta; valueType: string; unit: string }) {
  const sign = signChangeLabel(d.sign_change);
  return (
    <>
      <td className="num">{formatValue(d.before, valueType, unit)}</td>
      <td className="num">{formatValue(d.after, valueType, unit)}</td>
      <td className={`num${typeof d.delta === "number" && d.delta < 0 ? " neg" : ""}`}>
        {formatDelta(d, valueType, unit)}
        {valueType !== "percent" && formatPercentChange(d) && <span className="small muted"> ({formatPercentChange(d)})</span>}
        {sign && <span className="flag"> ⚠ {sign}</span>}
      </td>
    </>
  );
}

function TableDiff({ table }: { table: TableComparison }) {
  const changedCols = table.columns.filter((c) => table.rows.some((r) => r.cells[c.id]?.changed));
  const cols = changedCols.length > 0 ? changedCols : table.columns.slice(0, 3);
  return (
    <div className="table-wrap">
      <h4>{table.label}</h4>
      {table.sign_changes.length > 0 && (
        <ul className="sign-changes">
          {table.sign_changes.map((s) => {
            const col = table.columns.find((c) => c.id === s.column_id);
            return (
              <li key={`${s.row_label}-${s.column_id}`}>
                <strong>{s.row_label}</strong>: {s.column_label} {signChangeLabel(s.change)?.toLowerCase()} (
                {formatValue(s.before, col?.value_type, col?.unit)} → {formatValue(s.after, col?.value_type, col?.unit)})
              </li>
            );
          })}
        </ul>
      )}
      <table>
        <caption className="visually-hidden">{table.label}: before and after</caption>
        <thead>
          <tr>
            <th scope="col">Row</th>
            {cols.map((c) => (
              <th key={c.id} scope="col" colSpan={3}>
                {c.label}
              </th>
            ))}
          </tr>
          <tr className="subhead">
            <th scope="col" />
            {cols.map((c) => (
              <FragmentHeads key={c.id} />
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((r) => (
            <tr key={r.row}>
              <th scope="row">{r.label}</th>
              {cols.map((c) => {
                const d = r.cells[c.id];
                return d ? <DeltaCells key={c.id} d={d} valueType={c.value_type} unit={c.unit} /> : <td key={c.id} colSpan={3} />;
              })}
            </tr>
          ))}
          {table.total && (
            <tr className="total-row">
              <th scope="row">Total</th>
              {cols.map((c) => {
                const d = table.total?.[c.id];
                return d ? <DeltaCells key={c.id} d={d} valueType={c.value_type} unit={c.unit} /> : <td key={c.id} colSpan={3} />;
              })}
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function FragmentHeads() {
  return (
    <>
      <th scope="col" className="small">Before</th>
      <th scope="col" className="small">After</th>
      <th scope="col" className="small">Change</th>
    </>
  );
}

export function ScenarioPreview({ scenario, busy, onApply, onRevise, onDiscard, newKey }: Props) {
  const applyKey = useRef<string | null>(null);
  const [showChecks, setShowChecks] = useState(false);
  const c = scenario.comparison;
  const failedChecks = (scenario.validations ?? []).filter((v) => !v.passed);
  const terminal = ["applied", "discarded", "superseded"].includes(scenario.status);

  let blockedReason: string | null = null;
  if (!scenario.apply_allowed && !terminal) {
    if (scenario.status === "calculating" || scenario.status === "validating" || scenario.status === "draft") blockedReason = "Still calculating.";
    else if (scenario.stale) blockedReason = "The workbook or its map changed since this preview was calculated. Ask for it again to recalculate from the current version.";
    else if (scenario.status === "failed") blockedReason = "This scenario failed its checks, so it cannot be applied. The workbook is unchanged.";
    else blockedReason = `This scenario is ${statusLabel(scenario.status).toLowerCase()}.`;
  }

  return (
    <section className="card scenario" aria-labelledby="scenario-title">
      <div className="scenario-head">
        <h3 id="scenario-title">{scenario.name}</h3>
        <StatusBadge status={scenario.status} label={statusLabel(scenario.status)} />
      </div>
      <p className="small">
        Starting point: <strong>Version {scenario.base_version?.seq}</strong> — {scenario.base_version?.label}
      </p>
      {scenario.interpreted_request && (
        <p>
          <span className="muted">Interpreted request: </span>
          {scenario.interpreted_request}
        </p>
      )}
      {scenario.assumptions.length > 0 && (
        <div>
          <h4>Assumptions</h4>
          <ul>
            {scenario.assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </div>
      )}

      {(scenario.status === "calculating" || scenario.status === "validating") && <Spinner label="Recalculating with the workbook's formulas…" />}

      {scenario.error && (
        <div className="notice notice-error" role="alert">
          <p>{scenario.error.message}</p>
          {scenario.error.failed_checks && (
            <ul>
              {scenario.error.failed_checks.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {c && (
        <>
          <h4>Inputs changed</h4>
          <table>
            <thead>
              <tr>
                <th scope="col">Input</th>
                <th scope="col">Before</th>
                <th scope="col">After</th>
                <th scope="col">Change</th>
              </tr>
            </thead>
            <tbody>
              {c.inputs.map((i) => (
                <tr key={i.field_id}>
                  <th scope="row">
                    {i.label} <SourceToggle sheet={i.source.sheet} address={i.source.address} />
                  </th>
                  <DeltaCells d={i} valueType={i.value_type} unit={i.unit} />
                </tr>
              ))}
            </tbody>
          </table>

          {c.outputs.length > 0 && (
            <>
              <h4>Results</h4>
              <table>
                <thead>
                  <tr>
                    <th scope="col">Result</th>
                    <th scope="col">Before</th>
                    <th scope="col">After</th>
                    <th scope="col">Change</th>
                  </tr>
                </thead>
                <tbody>
                  {c.outputs.map((o) => (
                    <tr key={o.field_id}>
                      <th scope="row">
                        {o.label} <SourceToggle sheet={o.source.sheet} address={o.source.address} />
                      </th>
                      <DeltaCells d={o} valueType={o.value_type} unit={o.unit} />
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
          {c.tables.map((t) => (
            <TableDiff key={t.table_id} table={t} />
          ))}
        </>
      )}

      {scenario.validations && (
        <div className="checks">
          <p>
            Checks:{" "}
            {failedChecks.length === 0 ? (
              <strong className="ok">✓ all {scenario.validations.length} passed</strong>
            ) : (
              <strong className="bad">✗ {failedChecks.length} failed</strong>
            )}{" "}
            <button type="button" className="link-button small" aria-expanded={showChecks} onClick={() => setShowChecks(!showChecks)}>
              {showChecks ? "Hide checks" : "Show checks"}
            </button>
          </p>
          {showChecks && (
            <ul className="plain-list">
              {scenario.validations.map((v) => (
                <li key={v.code}>
                  {v.passed ? "✓ Passed" : "✗ Failed"}: {v.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {blockedReason && <p className="notice small">{blockedReason}</p>}

      {!terminal && (
        <div className="row-actions">
          <button
            type="button"
            className="primary"
            disabled={!scenario.apply_allowed || busy}
            onClick={() => {
              if (!applyKey.current) applyKey.current = newKey();
              onApply(scenario, applyKey.current);
            }}
          >
            Apply as new version
          </button>
          <button type="button" onClick={onRevise} disabled={busy}>
            Revise
          </button>
          <button type="button" onClick={() => onDiscard(scenario)} disabled={busy}>
            Discard
          </button>
        </div>
      )}
      {scenario.status === "applied" && <p className="notice small">Applied — it is now a saved version in the history.</p>}
      {(scenario.status === "ready" || scenario.status === "applied") && (
        <p className="small">
          <a href={scenarioDownloadUrl(scenario.id)} download>
            Download this scenario preview (.xlsx) — labelled as a preview, not an applied version
          </a>
        </p>
      )}
    </section>
  );
}
