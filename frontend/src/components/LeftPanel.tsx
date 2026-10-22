import type { ScenarioListItem, VersionInfo, WorkbookSummary } from "../api";
import { exportUrl } from "../api";
import { formatDate, statusLabel } from "../format";
import { StatusBadge } from "./common";

interface Props {
  workbook: WorkbookSummary;
  versions: VersionInfo[];
  scenarios: ScenarioListItem[];
  selectedScenarioId: string | null;
  busy: boolean;
  onSelectScenario: (id: string) => void;
  onRestore: (version: VersionInfo) => void;
}

export function LeftPanel({ workbook, versions, scenarios, selectedScenarioId, busy, onSelectScenario, onRestore }: Props) {
  const active = workbook.active_version;
  const visibleScenarios = scenarios.filter((s) => s.status !== "superseded");
  return (
    <aside className="panel panel-left" aria-label="Workbook, scenarios and history">
      <section aria-labelledby="wb-heading">
        <h2 id="wb-heading" className="wb-name">
          {workbook.name}
        </h2>
        {workbook.is_demo && <span className="badge badge-demo">Synthetic demo data</span>}
        <p className="small">
          Status: <StatusBadge status={workbook.compatibility_status} label={statusLabel(workbook.compatibility_status)} />
        </p>
        {active && (
          <div className="active-version">
            <div className="small muted">Active version</div>
            <div>
              <strong>Version {active.seq}</strong> — {active.label}
            </div>
            <a className="button-link small" href={exportUrl(workbook.id, active.id)} download>
              Download version {active.seq} (.xlsx)
            </a>
          </div>
        )}
      </section>

      <section aria-labelledby="scenarios-heading">
        <h3 id="scenarios-heading">Scenarios</h3>
        {visibleScenarios.length === 0 ? (
          <p className="muted small">No scenarios yet. Ask a what-if question in the chat.</p>
        ) : (
          <ul className="plain-list">
            {visibleScenarios.map((s) => (
              <li key={s.id}>
                <button
                  type="button"
                  className={`list-button${s.id === selectedScenarioId ? " selected" : ""}`}
                  aria-current={s.id === selectedScenarioId ? "true" : undefined}
                  onClick={() => onSelectScenario(s.id)}
                >
                  <span>{s.name}</span>
                  <span className="small muted">{statusLabel(s.status)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="history-heading">
        <h3 id="history-heading">Version history</h3>
        <ol className="plain-list history" reversed>
          {[...versions].reverse().map((v) => (
            <li key={v.id} className={v.active ? "history-item active" : "history-item"}>
              <div>
                <strong>Version {v.seq}</strong>
                {v.active && <span className="badge badge-ready">Active</span>}
              </div>
              <div className="small">{v.label}</div>
              <div className="small muted">{formatDate(v.created_at)}</div>
              <div className="row-actions">
                <a className="button-link small" href={exportUrl(workbook.id, v.id)} download aria-label={`Download version ${v.seq}`}>
                  Download
                </a>
                {!v.active && active && (
                  <button type="button" className="small" disabled={busy} onClick={() => onRestore(v)}>
                    Restore
                  </button>
                )}
              </div>
            </li>
          ))}
        </ol>
      </section>
    </aside>
  );
}
