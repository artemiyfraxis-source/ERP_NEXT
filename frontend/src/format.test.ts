import { describe, expect, it } from "vitest";
import {
  countLabel, displayCell, formatDate, formatDateTime, formatDay, formatDayMonth, formatMoment, formatNumber, formatQty,
  kbLabel, plural, toNumber, unitLabel,
} from "./format";

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

describe("formatDay", () => {
  it("shows the day of an ISO day or moment without the time", () => {
    expect(formatDay("2026-10-01")).toBe("01.10.2026");
    expect(formatDay("2026-10-01T14:30:00+03:00")).toBe("01.10.2026");
  });
  it("leaves other text alone", () => {
    expect(formatDay("до востребования")).toBe("до востребования");
    expect(formatDay(null)).toBe("");
  });
});

describe("formatDayMonth and formatMoment", () => {
  it("shows a day without the year and a plan moment short", () => {
    expect(formatDayMonth("2026-10-01")).toBe("01.10");
    expect(formatDayMonth("2026-10-01T14:30:00+03:00")).toBe("01.10");
    expect(formatDayMonth("до востребования")).toBe("до востребования");
    expect(formatMoment(new Date(2026, 9, 1, 9, 5).toISOString())).toBe("01.10 09:05");
    expect(formatMoment("")).toBe("");
  });
  it("keeps a day without a time and a moment with it", () => {
    expect(formatDateTime("2026-08-26")).toBe("26.08.2026");
    expect(formatDateTime(new Date(2026, 9, 10, 21, 53).toISOString())).toBe("10.10.2026 21:53");
  });
});

describe("formatNumber", () => {
  it("groups thousands with a thin space and writes the fraction with a comma", () => {
    expect(formatNumber(12345.5)).toBe("12\u202f345,5");
    expect(formatNumber("1234567.125")).toBe("1\u202f234\u202f567,125");
    expect(formatNumber(0.5)).toBe("0,5");
    expect(formatNumber(-42)).toBe("-42");
  });
  it("reads the server's decimal strings and drops trailing zeros", () => {
    expect(formatNumber("400.000")).toBe("400");
    expect(formatNumber("184.80")).toBe("184,8");
    expect(formatNumber("1,5")).toBe("1,5");
  });
  it("rounds to the asked digits and keeps money cents", () => {
    expect(formatNumber(1 / 3)).toBe("0,333");
    expect(formatNumber(2.5, { digits: 0 })).toBe("3");
    expect(formatNumber("1500", { digits: 2, minDigits: 2 })).toBe("1\u202f500,00");
  });
  it("leaves text and empties alone", () => {
    expect(formatNumber("—")).toBe("—");
    expect(formatNumber("12 шт")).toBe("12 шт");
    expect(formatNumber("")).toBe("");
    expect(formatNumber(null)).toBe("");
    expect(formatNumber(Number.NaN)).toBe("NaN");
  });
  it("parses numbers from text", () => {
    expect(toNumber("12\u202f345,5")).toBe(12345.5);
    expect(toNumber(" 7 ")).toBe(7);
    expect(toNumber("7a")).toBeNull();
    expect(toNumber(Number.POSITIVE_INFINITY)).toBeNull();
    expect(toNumber(undefined)).toBeNull();
  });
});

describe("formatQty and unitLabel", () => {
  it("writes the unit in lower case after a non-breaking space", () => {
    expect(formatQty("184.8", "М.")).toBe("184,8\u00a0м");
    expect(formatQty(1400, "Шт.")).toBe("1\u202f400\u00a0шт");
    expect(formatQty(3, "")).toBe("3");
    expect(formatQty(null, "м")).toBe("");
    expect(unitLabel("кВт")).toBe("кВт");
    expect(unitLabel("м2")).toBe("м²");
  });
});

describe("plural and countLabel", () => {
  it("picks the Russian form by the number", () => {
    const forms = ["партия", "партии", "партий"] as const;
    expect([1, 2, 5, 11, 12, 21, 22, 25, 101, 111, 0].map((n) => plural(n, forms))).toEqual([
      "партия", "партии", "партий", "партий", "партий", "партия", "партии", "партий", "партия", "партий", "партий",
    ]);
    expect(plural(1.5, forms)).toBe("партии");
    expect(countLabel(1, forms)).toBe("1\u00a0партия");
    expect(countLabel(12345, ["позиция", "позиции", "позиций"])).toBe("12\u202f345\u00a0позиций");
  });
});

describe("kbLabel", () => {
  it("separates the project and the assembly", () => {
    expect(kbLabel("614К.1", "КБ1")).toBe("614К.1 · КБ1");
    expect(kbLabel("614К.1КБ2")).toBe("614К.1 · КБ2");
    expect(kbLabel("614К.1")).toBe("614К.1");
    expect(kbLabel("", "КБ1")).toBe("КБ1");
    expect(kbLabel(null)).toBe("");
  });
});
