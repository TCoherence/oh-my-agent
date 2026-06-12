/**
 * Thin fetch wrapper around the FastAPI `/api/v1/*` JSON endpoints.
 *
 * - Reads optional bearer token from `localStorage["oma-dashboard-token"]`
 *   so the operator can paste a token without it leaking into URLs /
 *   browser history. Set it via DevTools or a future settings page.
 *   When unset, requests go without an Authorization header — fine for
 *   loopback-only deployments where the FastAPI app is started without
 *   --auth-token.
 *
 * - Raises `ApiError` on non-2xx so TanStack Query can show error states.
 */

const TOKEN_KEY = "oma-dashboard-token";

export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, body: unknown, msg: string) {
    super(msg);
    this.status = status;
    this.body = body;
    this.name = "ApiError";
  }
}

function authHeader(): Record<string, string> {
  if (typeof window === "undefined") return {};
  const token = window.localStorage.getItem(TOKEN_KEY);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// ── Token management (localStorage-backed, persists across refreshes) ── //

export function getToken(): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(TOKEN_KEY) ?? "";
}

export function setToken(value: string): void {
  if (typeof window === "undefined") return;
  const v = value.trim();
  if (v) window.localStorage.setItem(TOKEN_KEY, v);
  else window.localStorage.removeItem(TOKEN_KEY);
  // Let any listeners (the nav status dot) refresh.
  window.dispatchEvent(new CustomEvent("oma-token-changed"));
}

export function hasToken(): boolean {
  return getToken().length > 0;
}

// Fired when an API call gets 401 (auth required / wrong token) — the
// TokenModal listens and prompts the operator to paste a token.
const AUTH_401_EVENT = "oma-auth-401";

function emitAuthRequired(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(AUTH_401_EVENT));
  }
}

export { AUTH_401_EVENT };

async function apiGet<T>(path: string): Promise<T> {
  const r = await fetch(path, {
    headers: { Accept: "application/json", ...authHeader() },
    credentials: "same-origin",
  });
  if (!r.ok) {
    let body: unknown = null;
    try {
      body = await r.json();
    } catch {
      body = await r.text();
    }
    if (r.status === 401) emitAuthRequired();
    throw new ApiError(r.status, body, `${path} → ${r.status}`);
  }
  return (await r.json()) as T;
}

// Write helper (POST / PATCH). Sends the bearer token in the Authorization
// HEADER only — the dashboard write routes reject ?token= query params
// (M2 PR1: query tokens leak via logs/history).
async function apiWrite<T>(
  method: "POST" | "PATCH",
  path: string,
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...authHeader(),
  };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    let errBody: unknown = null;
    try {
      errBody = await r.json();
    } catch {
      errBody = await r.text();
    }
    if (r.status === 401) emitAuthRequired();
    throw new ApiError(r.status, errBody, `${path} → ${r.status}`);
  }
  // 204 / empty body tolerated
  const text = await r.text();
  return (text ? JSON.parse(text) : {}) as T;
}

// ── Types mirror the Python API shapes ─────────────────────────── //

export interface SessionRow {
  platform: string;
  channel_id: string;
  thread_id: string;
  turn_count: number;
  last_turn_at: string;
  last_role: string | null;
}

export interface SessionListResponse {
  items: SessionRow[];
  next_cursor: string | null;
}

export interface TurnRow {
  _id: number;
  role: "user" | "assistant" | "system";
  content: string;
  author: string | null;
  agent: string | null;
  created_at: string;
}

export type TraceEventKind =
  | "tool_use"
  | "tool_result"
  | "thinking"
  | "system_init"
  | "usage"
  | "error"
  | "complete"
  | "text";

export interface TraceEvent {
  ts: string;
  type: TraceEventKind;
  thread_id?: string;
  agent?: string | null;
  // tool_use
  tool_id?: string;
  name?: string;
  input?: Record<string, unknown>;
  // tool_result
  output?: string;
  is_error?: boolean;
  // thinking / text
  text?: string;
  // system_init
  session_id?: string;
  model?: string;
  tools?: string[];
  // usage
  input_tokens?: number | null;
  output_tokens?: number | null;
  cost_usd?: number | null;
  // error
  message?: string;
  error_kind?: string | null;
}

export interface TraceResponse {
  items: TraceEvent[];
  date: string;
  thread_id: string;
  enabled: boolean;
}

export interface SessionSearchHit {
  platform: string;
  channel_id: string;
  thread_id: string;
  _id: number;
  role: "user" | "assistant" | "system";
  snippet: string;
  author: string | null;
  agent: string | null;
  created_at: string;
}

export interface SessionSearchResponse {
  items: SessionSearchHit[];
  query: string;
}

export interface TrendBucket {
  day: string; // YYYY-MM-DD (UTC)
  cost: number;
  in_tok: number;
  out_tok: number;
  task_total: number;
  task_success: number;
  task_failed: number;
  turns: number;
}

export interface TrendsResponse {
  weeks: number;
  days: number;
  buckets: TrendBucket[];
  totals: {
    cost: number;
    in_tok: number;
    out_tok: number;
    task_total: number;
    task_success: number;
    task_failed: number;
    turns: number;
  };
  // Per-signal degradation notes (e.g. an old memory.db missing
  // runtime_tasks). Empty when every signal queried cleanly.
  warnings?: string[];
}

// ── Endpoint helpers ──────────────────────────────────────────────── //

export function fetchSessionList(opts: {
  limit?: number;
  cursor?: string | null;
}): Promise<SessionListResponse> {
  const params = new URLSearchParams();
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.cursor) params.set("cursor", opts.cursor);
  const qs = params.toString();
  return apiGet<SessionListResponse>(`/api/v1/sessions${qs ? `?${qs}` : ""}`);
}

export function fetchTrends(opts: { weeks: number }): Promise<TrendsResponse> {
  const params = new URLSearchParams({ weeks: String(opts.weeks) });
  return apiGet<TrendsResponse>(`/api/v1/trends?${params.toString()}`);
}

export function fetchSessionSearch(opts: {
  q: string;
  limit?: number;
}): Promise<SessionSearchResponse> {
  const params = new URLSearchParams({ q: opts.q });
  if (opts.limit) params.set("limit", String(opts.limit));
  return apiGet<SessionSearchResponse>(
    `/api/v1/sessions/search?${params.toString()}`,
  );
}

export function fetchSessionHistory(opts: {
  platform: string;
  channelId: string;
  threadId: string;
  limit?: number;
  beforeId?: number | null;
}): Promise<TurnRow[]> {
  const params = new URLSearchParams();
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.beforeId) params.set("before_id", String(opts.beforeId));
  const qs = params.toString();
  const p = `/api/v1/sessions/${encodeURIComponent(opts.platform)}/${encodeURIComponent(opts.channelId)}/${encodeURIComponent(opts.threadId)}/history`;
  return apiGet<TurnRow[]>(`${p}${qs ? `?${qs}` : ""}`);
}

export function fetchSessionTrace(opts: {
  platform: string;
  channelId: string;
  threadId: string;
  date: string;
  limit?: number;
}): Promise<TraceResponse> {
  const params = new URLSearchParams({ date: opts.date });
  if (opts.limit) params.set("limit", String(opts.limit));
  const p = `/api/v1/sessions/${encodeURIComponent(opts.platform)}/${encodeURIComponent(opts.channelId)}/${encodeURIComponent(opts.threadId)}/trace`;
  return apiGet<TraceResponse>(`${p}?${params.toString()}`);
}

// ── M2 PR5 — Skill health + Automation control ────────────────────── //

// (The legacy /api/v1/skills/health client lived here; the SPA now uses
// /api/v1/skills exclusively, so the dead fetchSkillHealth/SkillHealthRow
// pair was removed. The backend endpoint itself stays for external users.)

/** /api/v1/skills row — installed catalog merged with runtime stats. */
export interface SkillOverviewRow {
  skill: string;
  installed: boolean;
  description: string;
  allowed_tool_count: number;
  timeout_seconds: number | null;
  max_turns: number | null;
  runs_7d: number;
  runs_30d: number;
  success_rate: number | null;
  last_run_at: string | null;
  last_failure_reason: string | null;
  negative_feedback_rate: number | null;
  /** null = active; "manual" = operator-disabled; "auto" = auto-disabled
   *  by repeated failures. The split lets the UI explain *why*. */
  disabled_kind: "manual" | "auto" | null;
}

export interface SkillOverviewResponse {
  items: SkillOverviewRow[];
  warnings: string[];
}

export interface SkillRecentTaskRow {
  id: string;
  status: string;
  goal: string;
  error: string | null;
  at: string;
}

export interface AutomationRow {
  name: string;
  enabled: boolean;
  schedule_kind: string;
  cron: string | null;
  interval_seconds: number | null;
  agent: string | null;
  skill_name: string | null;
  platform: string;
  channel_id: string;
  next_run_at: string | null;
}

export function fetchSkillsOverview(): Promise<SkillOverviewResponse> {
  return apiGet<SkillOverviewResponse>("/api/v1/skills");
}

export function fetchSkillRecentTasks(
  name: string,
  opts?: { limit?: number },
): Promise<{ skill: string; items: SkillRecentTaskRow[] }> {
  const params = new URLSearchParams();
  if (opts?.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  return apiGet<{ skill: string; items: SkillRecentTaskRow[] }>(
    `/api/v1/skills/${encodeURIComponent(name)}/recent_tasks${qs ? `?${qs}` : ""}`,
  );
}

export function setSkillEnabled(
  name: string,
  enabled: boolean,
): Promise<{ skill: string; enabled: boolean }> {
  const verb = enabled ? "enable" : "disable";
  return apiWrite("POST", `/api/v1/skills/${encodeURIComponent(name)}/${verb}`);
}

export interface AutomationsResponse {
  items: AutomationRow[];
  /** Soft-fail signals (per-file parse errors etc.). Empty in happy path.
   *  Older backends may omit — treat absent as []. */
  warnings?: string[];
  /** "live" = colocated scheduler answered (fire/pause usable).
   *  "static" = standalone dashboard fallback (read-only YAML). Older
   *  backends omit — treat absent as "live" for back-compat. */
  mode?: "live" | "static";
}

export function fetchAutomations(): Promise<AutomationsResponse> {
  return apiGet<AutomationsResponse>("/api/v1/automations");
}

export function fireAutomation(
  name: string,
): Promise<{ name: string; result: string }> {
  return apiWrite("POST", `/api/v1/automations/${encodeURIComponent(name)}/fire`);
}

export function patchAutomation(
  name: string,
  updates: { enabled?: boolean; cron?: string; interval_seconds?: number },
): Promise<AutomationRow> {
  return apiWrite("PATCH", `/api/v1/automations/${encodeURIComponent(name)}`, updates);
}
