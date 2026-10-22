import { useEffect, useState } from "react";
import type { FieldDef, Me, WorkbookMap, WorkbookSummary } from "../api";
import { api } from "../api";
import { formatValue, statusLabel } from "../format";
import { ErrorNotice, Spinner, StatusBadge, toUiError, type UiError } from "./common";

interface Props {
  me: Me;
  workbook: WorkbookSummary;
  onChanged: () => void;
  onClose: () => void;
}

function cleanMap(map: WorkbookMap): WorkbookMap {
  // The backend forbids extra keys: send only the map's own fields.
  return {
    schema_version: map.schema_version,
    source: map.source,
    fields: map.fields,
    output_tables: map.output_tables,
    output_field_ids: map.output_field_ids,
    business_rules: map.business_rules,
  };
}

const numOrNull = (s: string): number | null => {
  if (s.trim() === "") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
};

export function CompatibilityReview({ workbook }: { workbook: WorkbookSummary }) {
  const c = workbook.compatibility;
  return (
    <section className="card" aria-labelledby="compat-heading">
      <h3 id="compat-heading">
        Compatibility review <StatusBadge status={workbook.compatibility_status} label={statusLabel(workbook.compatibility_status)} />
      </h3>
      {workbook.compatibility_status === "pending" && <Spinner label="Running the no-change round-trip check…" />}
      {workbook.compatibility_status === "editable" && (
        <p>The workbook passed the round-trip check{c.round_trip?.fidelity_verified ? " and its recalculated results match the trusted values" : ""}. Scenarios can be calculated.</p>
      )}
      {workbook.compatibility_status === "needs_baseline_confirmation" && (
        <p>
          The workbook recalculates consistently, but some formulas had no stored Excel results to compare with, so calculation fidelity is
          unverified. An administrator must confirm the baseline results (or provide trusted reference outputs) before scenarios can be
          calculated.
        </p>
      )}
      {c.blocking.length > 0 && (
        <>
          <h4>Why editing is blocked</h4>
          <ul>
            {c.blocking.map((b) => (
              <li key={b.code}>{b.message}</li>
            ))}
          </ul>
        </>
      )}
      {c.round_trip && !c.round_trip.passed && (
        <>
          <h4>Round-trip check failed</h4>
          {c.round_trip.engine_error && <p>{c.round_trip.engine_error.message}</p>}
          <ul>
            {c.round_trip.failed_checks.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        </>
      )}
      {c.warnings.length > 0 && (
        <>
          <h4>Things to know</h4>
          <ul>
            {c.warnings.map((w, i) => (
              <li key={`${w.code}-${i}`}>{w.message}</li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

export function SetupScreen({ me, workbook, onChanged, onClose }: Props) {
  const [admin, setAdmin] = useState(false);
  const [adminData, setAdminData] = useState<WorkbookSummary | null>(null);
  const [fields, setFields] = useState<FieldDef[]>(workbook.map.fields);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<UiError | null>(null);
  const [status, setStatus] = useState("");

  useEffect(() => {
    setFields(workbook.map.fields);
    setDirty(false);
  }, [workbook.map]);

  useEffect(() => {
    if (!admin) return;
    api.getWorkbook(workbook.id, true).then(setAdminData, (e) => setError(toUiError(e)));
  }, [admin, workbook.id, workbook.mapping_version_id]);

  const update = (id: string, patch: Partial<FieldDef>) => {
    setFields((fs) =>
      fs.map((f) => {
        if (f.id !== id) return f;
        const next = { ...f, ...patch };
        if (!next.confirmed) next.editable = false;
        if (next.role !== "input") next.editable = false;
        return next;
      }),
    );
    setDirty(true);
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.updateMap(workbook.id, workbook.mapping_version_id, cleanMap({ ...workbook.map, fields }));
      setStatus("Workbook map saved. Pending previews based on the old map were invalidated.");
      setDirty(false);
      onChanged();
    } catch (e) {
      setError(toUiError(e));
    } finally {
      setSaving(false);
    }
  };

  const confirmBaseline = async () => {
    if (!window.confirm("Confirm that the recalculated baseline results are correct? This enables scenario editing.")) return;
    try {
      await api.confirmBaseline(workbook.id);
      setStatus("Baseline confirmed.");
      onChanged();
    } catch (e) {
      setError(toUiError(e));
    }
  };

  const values = workbook.active_results?.fields ?? {};
  const inspection = adminData?.admin?.inspection;

  return (
    <section className="setup" aria-labelledby="setup-title">
      <div className="setup-head">
        <h2 id="setup-title">Understand this workbook</h2>
        <button type="button" onClick={onClose}>
          Back to conversation
        </button>
      </div>
      <p className="visually-hidden" aria-live="polite">
        {status}
      </p>
      {status && <p className="notice">{status}</p>}
      {error && <ErrorNotice error={error} onDismiss={() => setError(null)} />}

      <CompatibilityReview workbook={workbook} />
      {workbook.compatibility_status === "needs_baseline_confirmation" && me.is_owner && (
        <button type="button" className="primary" onClick={confirmBaseline}>
          Confirm baseline results
        </button>
      )}

      <section className="card" aria-labelledby="map-heading">
        <div className="setup-head">
          <h3 id="map-heading">Workbook map (map version {workbook.mapping_seq})</h3>
          <label className="toggle">
            <input type="checkbox" checked={admin} onChange={(e) => setAdmin(e.target.checked)} /> Administrator detail
          </label>
        </div>
        <p className="small muted">
          Suggested meanings stay unconfirmed until you review them. Only confirmed inputs marked “can change” are used in scenarios.
          {workbook.unconfirmed_fields > 0 && ` ${workbook.unconfirmed_fields} item(s) still need review.`}
        </p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Business label</th>
                <th scope="col">Role</th>
                <th scope="col">Unit</th>
                <th scope="col">Current value</th>
                <th scope="col">Min</th>
                <th scope="col">Max</th>
                <th scope="col">Confirmed</th>
                <th scope="col">Can change</th>
                {admin && <th scope="col">Source</th>}
              </tr>
            </thead>
            <tbody>
              {fields.map((f) => (
                <tr key={f.id}>
                  <td>
                    <label className="visually-hidden" htmlFor={`label-${f.id}`}>
                      Label for {f.id}
                    </label>
                    <input id={`label-${f.id}`} value={f.label} onChange={(e) => update(f.id, { label: e.target.value })} />
                    {f.description && <div className="small muted">{f.description}</div>}
                  </td>
                  <td>{f.role}</td>
                  <td>
                    <label className="visually-hidden" htmlFor={`unit-${f.id}`}>
                      Unit for {f.label}
                    </label>
                    <input id={`unit-${f.id}`} className="short" value={f.unit} onChange={(e) => update(f.id, { unit: e.target.value })} />
                  </td>
                  <td className="num">{formatValue(values[f.id], f.value_type, f.unit)}</td>
                  <td>
                    <label className="visually-hidden" htmlFor={`min-${f.id}`}>
                      Minimum for {f.label}
                    </label>
                    <input
                      id={`min-${f.id}`}
                      className="short"
                      inputMode="decimal"
                      defaultValue={f.minimum ?? ""}
                      disabled={f.role !== "input"}
                      onBlur={(e) => update(f.id, { minimum: numOrNull(e.target.value) })}
                    />
                  </td>
                  <td>
                    <label className="visually-hidden" htmlFor={`max-${f.id}`}>
                      Maximum for {f.label}
                    </label>
                    <input
                      id={`max-${f.id}`}
                      className="short"
                      inputMode="decimal"
                      defaultValue={f.maximum ?? ""}
                      disabled={f.role !== "input"}
                      onBlur={(e) => update(f.id, { maximum: numOrNull(e.target.value) })}
                    />
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`${f.label} confirmed`}
                      checked={f.confirmed}
                      onChange={(e) => update(f.id, { confirmed: e.target.checked })}
                    />
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`${f.label} can change in scenarios`}
                      checked={f.editable}
                      disabled={f.role !== "input" || !f.confirmed}
                      onChange={(e) => update(f.id, { editable: e.target.checked })}
                    />
                  </td>
                  {admin && (
                    <td className="small">
                      <code>
                        {f.sheet}!{f.address}
                      </code>
                      <div className="muted">
                        {f.value_type} · {f.source}
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="row-actions">
          <button type="button" className="primary" disabled={!dirty || saving} onClick={save}>
            {saving ? "Saving…" : "Save map"}
          </button>
          {dirty && <span className="small muted">Unsaved changes</span>}
        </div>

        {workbook.map.output_tables.length > 0 && (
          <>
            <h4>Output tables</h4>
            <ul>
              {workbook.map.output_tables.map((t) => (
                <li key={t.id}>
                  <strong>{t.label}</strong> — {t.columns.map((c) => c.label).join(", ")}
                  {admin && (
                    <span className="small muted">
                      {" "}
                      ({t.sheet}, rows {t.first_row}–{t.last_row})
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </>
        )}
        {workbook.map.business_rules.length > 0 && (
          <>
            <h4>Business rules</h4>
            <ul>
              {workbook.map.business_rules.map((r) => (
                <li key={r.id}>{r.text}</li>
              ))}
            </ul>
          </>
        )}
      </section>

      {admin && (
        <section className="card" aria-labelledby="admin-heading">
          <h3 id="admin-heading">Administrator detail</h3>
          {!adminData ? (
            <Spinner label="Loading inspection details…" />
          ) : (
            <>
              <p className="small">
                Map source: {workbook.map.source} · Original file SHA-256: <code>{adminData.admin?.original_sha256}</code>
              </p>
              {adminData.admin?.reference_outputs_source && (
                <p className="small">Trusted reference outputs: {adminData.admin.reference_outputs_source}</p>
              )}
              <h4>Sheets</h4>
              <ul>
                {workbook.sheets.map((s) => (
                  <li key={s.name}>
                    {s.name} ({s.state})
                  </li>
                ))}
              </ul>
              {inspection && (
                <>
                  <h4>Functions used</h4>
                  <p>{(inspection.functions_used as string[] | undefined)?.join(", ") || "none"}</p>
                  <h4>Detected features</h4>
                  <pre className="diag-pre">{JSON.stringify(inspection.features, null, 2)}</pre>
                </>
              )}
              {adminData.admin?.round_trip?.checks && (
                <>
                  <h4>Round-trip checks</h4>
                  <ul className="plain-list">
                    {adminData.admin.round_trip.checks.map((ch) => (
                      <li key={ch.code}>
                        {ch.passed ? "✓ Passed" : "✗ Failed"}: {ch.message}
                        {!ch.passed && ch.details && <pre className="diag-pre">{JSON.stringify(ch.details, null, 2)}</pre>}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </>
          )}
        </section>
      )}
    </section>
  );
}
