// Typed client for the backend REST API. Every request is same-origin under /api and carries the
// CSRF header the backend requires on state-changing requests.

export type Scalar = number | string | boolean | null;

export interface ApiErrorBody {
  code: string;
  message: string;
  diagnostic_id?: string;
  details?: Record<string, unknown>;
}

export class ApiError extends Error {
  readonly status: number;
  readonly body: ApiErrorBody;
  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.status = status;
    this.body = body;
  }
}

export interface Me {
  email: string;
  is_owner: boolean;
  ai_mode: "demo" | "anthropic";
  ai_model: string | null;
  suggested_prompts: string[];
  privacy_notice: string;
}

export interface WorkbookListItem {
  id: string;
  name: string;
  is_demo: boolean;
  compatibility_status: string;
  created_at: string;
}

export interface VersionInfo {
  id: string;
  seq: number;
  label: string;
  kind: string;
  parent_version_id: string | null;
  sha256: string;
  mapping_version_id: string | null;
  scenario_id: string | null;
  values_source: string;
  created_at: string;
  active?: boolean;
}

export type ValueType = "number" | "currency" | "percent" | "integer" | "text" | "boolean" | "date";

export interface FieldDef {
  id: string;
  label: string;
  description: string;
  sheet: string;
  address: string;
  role: "input" | "output" | "reference";
  value_type: ValueType;
  unit: string;
  minimum: number | null;
  maximum: number | null;
  aliases: string[];
  editable: boolean;
  confirmed: boolean;
  source: string;
}

export interface TableColumn {
  id: string;
  label: string;
  column: string;
  unit: string;
  value_type: ValueType;
}

export interface OutputTable {
  id: string;
  label: string;
  description: string;
  sheet: string;
  row_label_column: string;
  first_row: number;
  last_row: number;
  total_row: number | null;
  columns: TableColumn[];
  confirmed: boolean;
}

export interface BusinessRule {
  id: string;
  text: string;
  confirmed: boolean;
}

export interface WorkbookMap {
  schema_version: number;
  source: string;
  fields: FieldDef[];
  output_tables: OutputTable[];
  output_field_ids: string[];
  business_rules: BusinessRule[];
  workbook_id?: string;
  mapping_version_id?: string;
  source_version_id?: string;
}

export interface CompatIssue {
  code: string;
  message: string;
}

export interface RoundTripSummary {
  passed: boolean | null;
  fidelity_verified: boolean | null;
  engine_error: { code: string; message: string } | null;
  failed_checks: string[];
}

export interface Check {
  code: string;
  passed: boolean;
  message: string;
  details?: Record<string, unknown>;
}

export interface ActiveResults {
  fields: Record<string, Scalar>;
  tables: Record<string, { rows: { row: number; label: string; values: Record<string, Scalar> }[]; total: Record<string, Scalar> | null }>;
}

export interface WorkbookSummary {
  id: string;
  name: string;
  is_demo: boolean;
  compatibility_status: string;
  editing_enabled: boolean;
  active_version: VersionInfo | null;
  mapping_version_id: string;
  mapping_seq: number;
  map: WorkbookMap;
  unconfirmed_fields: number;
  sheets: { name: string; state: string }[];
  compatibility: {
    blocking: CompatIssue[];
    warnings: CompatIssue[];
    round_trip: RoundTripSummary | null;
    reference_check: Check | null;
  };
  active_results?: ActiveResults | null;
  admin?: {
    inspection: Record<string, unknown>;
    round_trip: { passed: boolean; fidelity_verified?: boolean; checks?: Check[]; engine_error?: unknown } | null;
    original_sha256: string;
    reference_outputs_source: string | null;
    baseline_confirmed: boolean;
  };
}

export interface Delta {
  before: Scalar;
  after: Scalar;
  delta?: number;
  changed?: boolean;
  percent_change?: number | null;
  percentage_point_change?: number;
  zero_baseline?: boolean;
  sign_change?: "became_negative" | "became_non_negative" | null;
}

export interface FieldDelta extends Delta {
  field_id: string;
  label: string;
  unit: string;
  value_type: ValueType;
  source: { sheet: string; address: string };
  operation?: string;
  operand?: Scalar;
  rationale?: string;
}

export interface TableComparison {
  table_id: string;
  label: string;
  sheet: string;
  columns: TableColumn[];
  rows: { label: string; row: number; cells: Record<string, Delta> }[];
  total: Record<string, Delta> | null;
  sign_changes: { row_label: string; column_id: string; column_label: string; change: string; before: Scalar; after: Scalar }[];
}

export interface Comparison {
  inputs: FieldDelta[];
  outputs: FieldDelta[];
  tables: TableComparison[];
}

export interface Scenario {
  id: string;
  name: string;
  status: string;
  status_reason: string | null;
  base_version: VersionInfo | null;
  mapping_version_id: string;
  revision_of_id: string | null;
  interpreted_request: string;
  assumptions: string[];
  changes: {
    field_id: string;
    operation: string;
    operand: Scalar;
    rationale: string;
    old_value: Scalar;
    new_value: Scalar;
    source: { sheet: string; address: string };
  }[];
  comparison: Comparison | null;
  validations: Check[] | null;
  error: { code: string; message: string; failed_checks?: string[] } | null;
  job_id: string | null;
  applied_version_id: string | null;
  stale: boolean;
  apply_allowed: boolean;
  created_at: string;
  updated_at: string;
}

export interface ScenarioListItem {
  id: string;
  name: string;
  status: string;
  base_version_id: string;
  created_at: string;
  applied_version_id: string | null;
}

export interface Payload {
  kind?: "clarification" | "answer" | "scenario_pending" | "scenario_ready" | "error";
  scenario_id?: string | null;
  clarification?: { question: string; options: string[] } | null;
  referenced_field_ids?: string[];
  proposed_rule?: { text: string } | null;
  mode?: string;
  demo_note?: string;
  interim?: boolean;
  error?: { code: string; request_id?: string };
  scenario?: Scenario;
}

export interface ChatMessage {
  id: string;
  seq: number;
  role: "user" | "assistant";
  text: string;
  payload: Payload;
  turn_id: string | null;
  created_at: string;
}

export interface MessagesResponse {
  mode: string;
  messages: ChatMessage[];
  pending_turns: { id: string; status: string }[];
}

export interface TurnResponse {
  turn: { id: string; status: string; mode: string; error: string | null };
  messages: ChatMessage[];
}

export interface Job {
  id: string;
  purpose: string;
  status: "queued" | "running" | "succeeded" | "failed";
  workbook_id: string | null;
  scenario_id: string | null;
  error: { code: string; message: string } | null;
  created_at: string;
  finished_at: string | null;
}

export interface UsageSummary {
  ai_mode: string;
  model: string;
  effort: string;
  refusal_fallback: string;
  note?: string;
  label?: string;
  price_version?: string;
  prices_verified?: boolean;
  price_source?: string;
  today?: { estimated_usd: number; open_reservations_usd: number; budget_usd: number };
  month?: { estimated_usd: number; open_reservations_usd: number; budget_usd: number };
  uncertain_requests?: number;
  recent?: {
    id: string;
    created_at: string;
    status: string;
    requested_model: string;
    served_model: string | null;
    request_id: string | null;
    input_tokens: number;
    cache_write_tokens: number;
    cache_read_tokens: number;
    output_tokens: number;
    cost_usd: number;
    reserved_usd: number;
    fallback_used: boolean;
    error_code: string | null;
  }[];
}

const CSRF = { "X-Requested-With": "projections-assistant" };

async function request<T>(method: string, path: string, options: { body?: unknown; form?: FormData; headers?: Record<string, string> } = {}): Promise<T> {
  const headers: Record<string, string> = { ...CSRF, Accept: "application/json", ...(options.headers ?? {}) };
  let body: BodyInit | undefined;
  if (options.form) {
    body = options.form;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { method, headers, body, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, { code: "network_error", message: "The application could not be reached. Check your connection and try again." });
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!response.ok) {
    const err = (data as { error?: ApiErrorBody } | null)?.error;
    throw new ApiError(response.status, err ?? { code: `http_${response.status}`, message: "The request failed. Please try again." });
  }
  return data as T;
}

const enc = encodeURIComponent;

export const api = {
  me: () => request<Me>("GET", "/me"),
  listWorkbooks: () => request<WorkbookListItem[]>("GET", "/workbooks"),
  loadDemo: () => request<{ workbook: WorkbookSummary; job_id: string | null }>("POST", "/workbooks/demo"),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ workbook: WorkbookSummary; job_id: string | null }>("POST", "/workbooks", { form });
  },
  getWorkbook: (id: string, admin = false) => request<WorkbookSummary>("GET", `/workbooks/${enc(id)}${admin ? "?admin=true" : ""}`),
  deleteWorkbook: (id: string) => request<void>("DELETE", `/workbooks/${enc(id)}`, { headers: { "X-Confirm-Delete": id } }),
  updateMap: (id: string, expectedMappingVersionId: string, map: WorkbookMap) =>
    request<{ mapping_version_id: string; seq: number }>("PUT", `/workbooks/${enc(id)}/map`, {
      body: { expected_mapping_version_id: expectedMappingVersionId, map },
    }),
  confirmBaseline: (id: string) => request<{ compatibility_status: string }>("POST", `/workbooks/${enc(id)}/confirm-baseline`),
  messages: (id: string, after = 0) => request<MessagesResponse>("GET", `/workbooks/${enc(id)}/messages?after=${after}`),
  sendMessage: (id: string, text: string, requestKey: string) =>
    request<TurnResponse>("POST", `/workbooks/${enc(id)}/messages`, { body: { text, request_key: requestKey } }),
  listScenarios: (id: string) => request<ScenarioListItem[]>("GET", `/workbooks/${enc(id)}/scenarios`),
  getScenario: (id: string) => request<Scenario>("GET", `/scenarios/${enc(id)}`),
  applyScenario: (id: string, expectedActiveVersionId: string, idempotencyKey: string) =>
    request<{ scenario_id: string; version: VersionInfo }>("POST", `/scenarios/${enc(id)}/apply`, {
      body: { expected_active_version_id: expectedActiveVersionId },
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  discardScenario: (id: string) => request<{ id: string; status: string }>("POST", `/scenarios/${enc(id)}/discard`),
  versions: (id: string) => request<VersionInfo[]>("GET", `/workbooks/${enc(id)}/versions`),
  restore: (id: string, targetVersionId: string, expectedActiveVersionId: string) =>
    request<{ active_version: VersionInfo; mapping_version_id: string }>("POST", `/workbooks/${enc(id)}/restore`, {
      body: { target_version_id: targetVersionId, expected_active_version_id: expectedActiveVersionId },
    }),
  job: (id: string) => request<Job>("GET", `/jobs/${enc(id)}`),
  usage: () => request<UsageSummary>("GET", "/admin/usage"),
  aiCheck: (live: boolean) => request<Record<string, unknown>>("POST", "/admin/ai-check", { body: { live } }),
};

export const exportUrl = (workbookId: string, versionId: string) => `/api/workbooks/${enc(workbookId)}/export?version_id=${enc(versionId)}`;
export const scenarioDownloadUrl = (scenarioId: string) => `/api/scenarios/${enc(scenarioId)}/download`;

export function newKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export async function waitForJob(jobId: string, onUpdate?: (job: Job) => void): Promise<Job> {
  for (let i = 0; i < 600; i++) {
    const job = await api.job(jobId);
    onUpdate?.(job);
    if (job.status === "succeeded" || job.status === "failed") return job;
    await sleep(1000);
  }
  throw new ApiError(0, { code: "job_timeout", message: "The calculation is taking unusually long. Reload the page to check again." });
}
