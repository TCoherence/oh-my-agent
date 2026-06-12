import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * shadcn-style `cn()` — concat classNames with deduplication-aware
 * tailwind-merge so later classes override earlier ones reliably
 * (e.g. `cn("p-4", "p-2")` → `"p-2"`).
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const ts = new Date(iso);
  if (Number.isNaN(ts.getTime())) return iso;
  const delta = Date.now() - ts.getTime();
  const secs = Math.max(0, Math.round(delta / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  return `${Math.round(secs / 86400)}d ago`;
}

export function formatLocal(iso: string | null | undefined): string {
  if (!iso) return "";
  const ts = new Date(iso);
  if (Number.isNaN(ts.getTime())) return iso;
  return ts.toLocaleString();
}

export function todayYmd(): string {
  const now = new Date();
  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

/**
 * Date portion (YYYY-MM-DD) of an ISO-ish timestamp. Handles both
 * `2026-05-12T10:00:00` and SQLite's space-separated `2026-05-12 10:00:00`
 * **without** going through `Date` — trace files are keyed by the
 * server-side calendar day, so converting to browser-local time here
 * would reintroduce the TZ-mismatch bug this helper exists to fix.
 */
export function ymdFromIso(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const m = /^(\d{4}-\d{2}-\d{2})/.exec(iso);
  return m ? m[1] : null;
}

/** Shift a YYYY-MM-DD string by whole days (UTC arithmetic — no DST drift). */
export function shiftYmd(ymd: string, deltaDays: number): string {
  const [y, m, d] = ymd.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + deltaDays));
  const mm = String(dt.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(dt.getUTCDate()).padStart(2, "0");
  return `${dt.getUTCFullYear()}-${mm}-${dd}`;
}
