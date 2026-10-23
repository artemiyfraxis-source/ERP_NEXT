import { expect, it } from "vitest";
import { applyGridPaste, dateToIso, hasHeaderRow, normalizeHeader, parseTsv, splitLines, type PasteField } from "./gridPaste";

type Key = "a" | "b" | "d";
const FIELDS: Array<PasteField<Key>> = [
  { key: "a", label: "Артикул", aliases: ["арт ПОСТАВЩИКА"] },
  { key: "b", label: "Кол-во", aliases: ["приход"] },
  { key: "d", label: "Дата", aliases: [] },
];
const DATES = new Set<Key>(["d"]);
const MESSAGES = { short: "мало", noHeaders: "нет заголовков" };
type Row = { a: string; b: string; d: string };
const seed: Row = { a: "", b: "", d: "" };
const setup = (limit = 100) => ({
  fields: FIELDS, dateKeys: DATES, columns: ["a", "b", "d"] as Key[], limit, seed,
  isBlank: (row: Row) => !row.a && !row.b, cellValue: (_key: Key, raw: string) => raw.trim(), messages: MESSAGES,
});

it("splits lines without the trailing empty ones and normalizes headers", () => {
  expect(splitLines("x\r\ny\n\n")).toEqual(["x", "y"]);
  expect(normalizeHeader("  Кол-во  * ")).toBe("кол-во");
  expect(dateToIso("5.1.2026")).toBe("2026-01-05");
  expect(dateToIso("вчера")).toBe("");
});

it("recognizes a header row by a name or an alias, case-insensitively", () => {
  expect(hasHeaderRow("АРТ поставщика\tПриход\nX\t1", FIELDS)).toBe(true);
  expect(hasHeaderRow("X\t1", FIELDS)).toBe(false);
});

it("parses a block by headers and reports unknown columns", () => {
  const parsed = parseTsv("Артикул\tЛишнее\tДата\nX\tz\t01.02.2026", FIELDS, DATES, MESSAGES);
  expect(parsed).toEqual({ rows: [{ a: "X", d: "2026-02-01" }], recognized: ["Артикул", "Дата"], unknown: ["Лишнее"] });
  expect(parseTsv("Артикул", FIELDS, DATES, MESSAGES)).toEqual({ error: "мало" });
  expect(parseTsv("Что-то\nX", FIELDS, DATES, MESSAGES)).toEqual({ error: "нет заголовков" });
});

it("a header block goes after the filled rows and reuses the blank tail", () => {
  const result = applyGridPaste([{ a: "A", b: "1", d: "" }, { ...seed }], 1, "a", "Артикул\tприход\nX\t2\nY\t3", setup());
  if ("error" in result) throw new Error(result.error);
  expect(result.mode).toBe("headers");
  expect(result.positions.map((row) => row.a)).toEqual(["A", "X", "Y"]);
});

it("a plain block lands from the focused cell down and right; empty pasted cells keep the value", () => {
  const result = applyGridPaste([{ a: "A", b: "1", d: "" }], 0, "b", "5\t2026-01-01\n7", setup());
  if ("error" in result) throw new Error(result.error);
  expect(result.positions).toEqual([{ a: "A", b: "5", d: "2026-01-01" }, { a: "", b: "7", d: "" }]);
  expect(result.filled).toBe(2);
});

it("drops rows past the limit", () => {
  const result = applyGridPaste([], 0, "a", "1\n2\n3", setup(2));
  if ("error" in result) throw new Error(result.error);
  expect(result.positions).toHaveLength(2);
  expect(result.dropped).toBe(1);
});
