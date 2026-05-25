import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { type ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { TokenBar, TokenModal } from "@/components/token-control";
import { AUTH_401_EVENT, getToken } from "@/lib/api";

function wrap(ui: ReactElement) {
  const qc = new QueryClient();
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  cleanup(); // not auto-registered without vitest globals — unmount between tests
});

describe("TokenBar", () => {
  it("reflects no-token state and persists a saved token", () => {
    wrap(<TokenBar />);
    const trigger = screen.getByRole("button", { name: /token/i });
    expect(trigger.getAttribute("title")).toContain("No auth token");

    fireEvent.click(trigger); // open the popover
    fireEvent.change(screen.getByPlaceholderText("paste token…"), {
      target: { value: "tk1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "save" }));

    expect(getToken()).toBe("tk1");
    // The reactive dot/title updates via the oma-token-changed event.
    expect(screen.getByRole("button", { name: /token/i }).getAttribute("title")).toContain(
      "Auth token set",
    );
  });
});

describe("TokenModal", () => {
  it("stays hidden until a 401, then saves + persists and closes", () => {
    wrap(<TokenModal />);
    expect(screen.queryByText("Auth required")).toBeNull();

    // Raw window event isn't auto-wrapped in act() like fireEvent — flush the
    // resulting state update so the modal renders before we assert.
    act(() => {
      window.dispatchEvent(new CustomEvent(AUTH_401_EVENT));
    });
    expect(screen.getByText("Auth required")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("paste token…"), {
      target: { value: "modal-tk" },
    });
    fireEvent.click(screen.getByRole("button", { name: /save & retry/i }));

    expect(getToken()).toBe("modal-tk");
    expect(screen.queryByText("Auth required")).toBeNull();
  });
});
