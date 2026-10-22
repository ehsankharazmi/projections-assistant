// Number formatting for business users. Percent values are stored as fractions (0.08 = 8%).
import type { Delta, Scalar, ValueType } from "./api";

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

function isCurrency(valueType: ValueType | string | undefined, unit: string | undefined): boolean {
  return valueType === "currency" || (unit ?? "").toUpperCase().startsWith("USD");
}

function unitSuffix(unit: string | undefined): string {
  const u = unit ?? "";
  const slash = u.indexOf("/");
  if (slash >= 0) {
    const per = u.slice(slash + 1);
    return per && per !== "year" ? `/${per}` : "";
  }
  return "";
}

export function formatValue(value: Scalar | undefined, valueType?: ValueType | string, unit?: string): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (!isNum(value)) return String(value);
  if (valueType === "percent") return `${(value * 100).toFixed(1)}%`;
  if (isCurrency(valueType, unit)) {
    const abs = Math.abs(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return `${value < 0 ? "-" : ""}$${abs}${unitSuffix(unit)}`;
  }
  const digits = Number.isInteger(value) ? 0 : 2;
  const text = value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return unit && !["fraction", "flag", "count"].includes(unit) ? `${text} ${unit}` : text;
}

export function formatDelta(d: Delta, valueType?: ValueType | string, unit?: string): string {
  if (!isNum(d.delta)) return d.changed ? "changed" : "no change";
  if (d.delta === 0) return "no change";
  const sign = d.delta > 0 ? "+" : "-";
  if (valueType === "percent") {
    const pts = d.percentage_point_change ?? d.delta * 100;
    return `${pts > 0 ? "+" : "-"}${Math.abs(pts).toFixed(1)} pts`;
  }
  let text: string;
  if (isCurrency(valueType, unit)) {
    text = `${sign}$${Math.abs(d.delta).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  } else {
    const digits = Number.isInteger(d.delta) ? 0 : 2;
    text = `${sign}${Math.abs(d.delta).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
  }
  return text;
}

export function formatPercentChange(d: Delta): string {
  if (d.zero_baseline) return "no % change from zero";
  if (!isNum(d.percent_change)) return "";
  if (d.percent_change === 0) return "0.0%";
  return `${d.percent_change > 0 ? "+" : "-"}${Math.abs(d.percent_change * 100).toFixed(1)}%`;
}

export function signChangeLabel(change: string | null | undefined): string | null {
  if (change === "became_negative") return "Turned negative";
  if (change === "became_non_negative") return "No longer negative";
  return null;
}

export function formatUsd(value: number | undefined): string {
  if (!isNum(value)) return "—";
  return `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
}

export function formatDate(iso: string): string {
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export const STATUS_LABELS: Record<string, string> = {
  pending: "Checking compatibility",
  editable: "Ready for scenarios",
  inspect_only: "Inspection only (editing blocked)",
  needs_baseline_confirmation: "Waiting for baseline confirmation",
  rejected: "Rejected",
  draft: "Draft",
  needs_clarification: "Needs clarification",
  validating: "Validating",
  calculating: "Calculating",
  ready: "Ready to apply",
  failed: "Failed",
  applied: "Applied",
  discarded: "Discarded",
  superseded: "Replaced",
};

export const statusLabel = (s: string) => STATUS_LABELS[s] ?? s;
