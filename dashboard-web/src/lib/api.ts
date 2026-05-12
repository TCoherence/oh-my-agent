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
    throw new ApiError(r.status, body, `${path} → ${r.status}`);
  }
  return (await r.json()) as T;
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
