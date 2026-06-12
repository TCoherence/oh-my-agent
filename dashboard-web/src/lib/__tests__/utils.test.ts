import { describe, expect, it } from "vitest";

import { shiftYmd, ymdFromIso } from "@/lib/utils";

describe("ymdFromIso", () => {
  it("extracts the date portion from T-separated ISO", () => {
    expect(ymdFromIso("2026-05-12T10:00:00")).toBe("2026-05-12");
  });

  it("extracts the date portion from SQLite space-separated timestamps", () => {
    expect(ymdFromIso("2026-05-12 10:00:00")).toBe("2026-05-12");
  });

  it("does NOT shift the day through browser-local time", () => {
    // 23:59 UTC on the 12th is already the 13th in UTC+ TZs — the trace
    // file is keyed by the server-side day, so the raw date must win.
    expect(ymdFromIso("2026-05-12T23:59:59+00:00")).toBe("2026-05-12");
  });

  it("returns null for empty / non-date input", () => {
    expect(ymdFromIso(null)).toBeNull();
    expect(ymdFromIso(undefined)).toBeNull();
    expect(ymdFromIso("")).toBeNull();
    expect(ymdFromIso("not a date")).toBeNull();
  });
});

describe("shiftYmd", () => {
  it("shifts forward and backward by whole days", () => {
    expect(shiftYmd("2026-05-12", 1)).toBe("2026-05-13");
    expect(shiftYmd("2026-05-12", -1)).toBe("2026-05-11");
  });

  it("crosses month and year boundaries", () => {
    expect(shiftYmd("2026-05-31", 1)).toBe("2026-06-01");
    expect(shiftYmd("2026-01-01", -1)).toBe("2025-12-31");
    expect(shiftYmd("2024-02-28", 1)).toBe("2024-02-29"); // leap year
  });
});
