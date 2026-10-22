import type { Scenario, WorkbookSummary } from "../api";
import { formatValue } from "../format";
import { SourceToggle } from "./common";
import { ScenarioPreview } from "./ScenarioPreview";

interface Props {
  workbook: WorkbookSummary;
  scenario: Scenario | null;
  busy: boolean;
  onApply: (scenario: Scenario, key: string) => void;
  onRevise: () => void;
  onDiscard: (scenario: Scenario) => void;
  newKey: () => string;
}

export function ResultsPanel({ workbook, scenario, busy, onApply, onRevise, onDiscard, newKey }: Props) {
  const results = workbook.active_results;
  const outputs = workbook.map.fields.filter((f) => f.role === "output" && f.confirmed);
  const active = workbook.active_version;

  return (
    <aside className="panel panel-right" aria-label="Results and scenario preview">
      {scenario && (
        <ScenarioPreview key={scenario.id} scenario={scenario} busy={busy} onApply={onApply} onRevise={onRevise} onDiscard={onDiscard} newKey={newKey} />
      )}

      <section aria-labelledby="results-heading">
        <h2 id="results-heading">Current results</h2>
        <p className="small muted">
          {active ? `Version ${active.seq}` : ""}
          {active?.values_source === "recalculated"
            ? " · recalculated with the workbook's formulas"
            : " · values as last saved in Excel (not recalculated here)"}
        </p>
        {!results ? (
          <p className="muted">Results appear here once the compatibility check has finished.</p>
        ) : (
          <>
            <div className="cards">
              {outputs.map((f) => (
                <div key={f.id} className="result-card">
                  <div className="small muted">{f.label}</div>
                  <div className="result-value">{formatValue(results.fields[f.id], f.value_type, f.unit)}</div>
                  <SourceToggle sheet={f.sheet} address={f.address} />
                </div>
              ))}
            </div>
            {workbook.map.output_tables
              .filter((t) => t.confirmed)
              .map((t) => {
                const data = results.tables[t.id];
                if (!data) return null;
                return (
                  <div key={t.id} className="table-wrap">
                    <h3>{t.label}</h3>
                    <table>
                      <thead>
                        <tr>
                          <th scope="col">Row</th>
                          {t.columns.map((c) => (
                            <th key={c.id} scope="col">
                              {c.label}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {data.rows.map((r) => (
                          <tr key={r.row}>
                            <th scope="row">{r.label}</th>
                            {t.columns.map((c) => {
                              const v = r.values[c.id];
                              const negative = typeof v === "number" && v < 0;
                              return (
                                <td key={c.id} className={`num${negative ? " neg" : ""}`}>
                                  {formatValue(v, c.value_type, c.unit)}
                                  {negative && <span className="visually-hidden"> (negative)</span>}
                                </td>
                              );
                            })}
                          </tr>
                        ))}
                        {data.total && (
                          <tr className="total-row">
                            <th scope="row">Total</th>
                            {t.columns.map((c) => (
                              <td key={c.id} className="num">
                                {formatValue(data.total?.[c.id] ?? null, c.value_type, c.unit)}
                              </td>
                            ))}
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                );
              })}
          </>
        )}
      </section>
    </aside>
  );
}
