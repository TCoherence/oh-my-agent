import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { AUTH_401_EVENT, getToken, hasToken, setToken } from "@/lib/api";
import { cn } from "@/lib/utils";

/**
 * Token UX for the dashboard write surface.
 *
 * - `TokenBar`: a 🔑 button in the nav showing set/unset; click to paste /
 *   update / clear the bearer token. Persists to localStorage (survives
 *   refresh).
 * - `TokenModal`: auto-opens when any API call returns 401 (listens for
 *   the `oma-auth-401` window event), prompts for the token, then refetches
 *   so the failed action recovers without a manual reload.
 */

function useTokenState(): boolean {
  const [present, setPresent] = useState<boolean>(hasToken());
  useEffect(() => {
    const refresh = () => setPresent(hasToken());
    window.addEventListener("oma-token-changed", refresh);
    window.addEventListener("storage", refresh); // other tabs
    return () => {
      window.removeEventListener("oma-token-changed", refresh);
      window.removeEventListener("storage", refresh);
    };
  }, []);
  return present;
}

export function TokenBar() {
  const present = useTokenState();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setDraft(getToken());
      // focus after render
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 text-xs hover:text-foreground transition-colors"
        title={present ? "Auth token set — click to edit" : "No auth token — click to set"}
      >
        <span
          className={cn(
            "inline-block h-2 w-2 rounded-full",
            present ? "bg-green-500" : "bg-muted-foreground/40",
          )}
        />
        🔑 token
      </button>
      {open ? (
        <div className="absolute right-0 mt-2 w-72 rounded-md border border-border bg-card p-3 shadow-lg z-50">
          <p className="text-xs text-muted-foreground mb-2">
            Bearer token for write actions. Stored in this browser
            (localStorage); persists across refreshes.
          </p>
          <input
            ref={inputRef}
            type="password"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                setToken(draft);
                setOpen(false);
              }
            }}
            placeholder="paste token…"
            className="w-full rounded border border-border bg-background px-2 py-1 text-xs"
          />
          <div className="mt-2 flex gap-2 justify-end">
            <button
              type="button"
              onClick={() => {
                setToken("");
                setDraft("");
                setOpen(false);
              }}
              className="px-2 py-1 rounded text-xs text-destructive hover:bg-destructive/10"
            >
              clear
            </button>
            <button
              type="button"
              onClick={() => {
                setToken(draft);
                setOpen(false);
              }}
              className="px-2 py-1 rounded text-xs bg-primary/10 text-primary hover:bg-primary/20"
            >
              save
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function TokenModal() {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const qc = useQueryClient();

  useEffect(() => {
    const onAuth = () => {
      // Only seed the draft on a closed→open transition. Background polls
      // can fire 401 repeatedly while the modal is open; without this guard
      // each one would reset the input to stale localStorage, erasing what
      // the user is typing (Codex catch).
      setOpen((alreadyOpen) => {
        if (!alreadyOpen) setDraft(getToken());
        return true;
      });
    };
    window.addEventListener(AUTH_401_EVENT, onAuth);
    return () => window.removeEventListener(AUTH_401_EVENT, onAuth);
  }, []);

  if (!open) return null;

  const submit = () => {
    setToken(draft);
    setOpen(false);
    // Refetch reads now that the token is set. Note: this recovers READ
    // queries; a failed write (fire/disable/patch) must be re-triggered by
    // the user — invalidateQueries does not replay mutations.
    qc.invalidateQueries();
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40">
      <div className="w-80 rounded-md border border-border bg-card p-4 shadow-xl">
        <h2 className="text-sm font-semibold mb-1">Auth required</h2>
        <p className="text-xs text-muted-foreground mb-3">
          The dashboard returned 401. Paste the bearer token to continue —
          colocated: the env var named by <code>dashboard.auth_token_env</code>{" "}
          in config.yaml; standalone: <code>--auth-token</code> /{" "}
          <code>OMA_DASHBOARD_AUTH_TOKEN</code>.
        </p>
        <input
          type="password"
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
            if (e.key === "Escape") setOpen(false);
          }}
          placeholder="paste token…"
          className="w-full rounded border border-border bg-background px-2 py-1 text-sm"
        />
        <div className="mt-3 flex gap-2 justify-end">
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="px-3 py-1 rounded text-xs text-muted-foreground hover:bg-accent/40"
          >
            cancel
          </button>
          <button
            type="button"
            onClick={submit}
            className="px-3 py-1 rounded text-xs bg-primary text-primary-foreground hover:opacity-90"
          >
            save & retry
          </button>
        </div>
      </div>
    </div>
  );
}
