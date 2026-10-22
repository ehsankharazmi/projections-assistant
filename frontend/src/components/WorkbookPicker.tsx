import { useRef, useState } from "react";
import type { WorkbookListItem } from "../api";
import { formatDate, statusLabel } from "../format";
import { Spinner } from "./common";

interface Props {
  workbooks: WorkbookListItem[];
  busy: string | null;
  onOpen: (id: string) => void;
  onLoadDemo: () => void;
  onUpload: (file: File) => void;
  onDelete: (wb: WorkbookListItem) => void;
}

export function WorkbookPicker({ workbooks, busy, onOpen, onLoadDemo, onUpload, onDelete }: Props) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);

  return (
    <section className="picker" aria-labelledby="picker-title">
      <h1 id="picker-title">Workbooks</h1>
      <p className="muted">
        Upload an Excel workbook (.xlsx) or start with the synthetic demo. The original file is kept unchanged; every
        applied change becomes a new version you can download or undo.
      </p>

      <div className="picker-actions">
        <div className="card">
          <h2>Try the demo</h2>
          <p className="muted">A small product-margin model with invented products and figures.</p>
          <button type="button" className="primary" onClick={onLoadDemo} disabled={busy !== null}>
            Load synthetic demo workbook
          </button>
        </div>
        <form
          className="card"
          onSubmit={(e) => {
            e.preventDefault();
            if (file) onUpload(file);
          }}
        >
          <h2>Upload a workbook</h2>
          <label htmlFor="upload-file">Excel workbook (.xlsx, up to 10 MiB)</label>
          <input
            id="upload-file"
            ref={fileInput}
            type="file"
            accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <button type="submit" className="primary" disabled={!file || busy !== null}>
            Upload and inspect
          </button>
        </form>
      </div>

      {busy && (
        <p className="notice">
          <Spinner label={busy} />
        </p>
      )}

      <h2>Your workbooks</h2>
      {workbooks.length === 0 ? (
        <p className="muted">No workbooks yet.</p>
      ) : (
        <ul className="workbook-list">
          {workbooks.map((wb) => (
            <li key={wb.id} className="workbook-item">
              <div>
                <button type="button" className="link-button strong" onClick={() => onOpen(wb.id)}>
                  {wb.name}
                </button>
                {wb.is_demo && <span className="badge badge-demo">Demo data</span>}
                <div className="muted small">
                  {statusLabel(wb.compatibility_status)} · added {formatDate(wb.created_at)}
                </div>
              </div>
              <button type="button" className="danger small" onClick={() => onDelete(wb)} aria-label={`Delete ${wb.name}`}>
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
