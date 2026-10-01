import { describe, expect, it } from "vitest";
import { displayCell, formatDate } from "./format";

describe("formatDate", () => {
  it("shows an ISO day the Russian way", () => {
    expect(formatDate("2026-10-01")).toBe("01.10.2026");
  });
  it("shows an ISO moment with local time", () => {
    const local = new Date(2026, 9, 1, 14, 30).toISOString();
    expect(formatDate(local)).toBe("01.10.2026 14:30");
  });
  it("leaves other text alone", () => {
    expect(formatDate("SM-20261001-01")).toBe("SM-20261001-01");
    expect(formatDate("2026-10")).toBe("2026-10");
    expect(formatDate("")).toBe("");
    expect(formatDate(null)).toBe("");
  });
  it("formats only string cells", () => {
    expect(displayCell("2026-10-22")).toBe("22.10.2026");
    expect(displayCell(42)).toBe(42);
    expect(displayCell(null)).toBe(null);
  });
});
