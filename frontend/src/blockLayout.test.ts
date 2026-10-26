import { describe, expect, it } from "vitest";
import { CAP_FLOOR, CAP_READABLE, CELL_PAD, captionFloors, chunksOf, headWidth, levelWidths, longestTexts, naturalWidths, typicalTexts, type Measure } from "./blockLayout";

/* «База данных» blocks (26.10.2026): widths from the data, one line per row, the widest texts give way first. */

const measure: Measure = (text) => text.length * 10;

describe("column widths of a list in blocks", () => {
  it("measures only the longest texts of a column", () => {
    expect(longestTexts(["a", "ccc", "", "bb", "dddd", "ccc"], 2)).toEqual(["dddd", "ccc"]);
  });

  it("sizes a column by its typical texts: a rare long name does not widen it for every row", () => {
    const values = [...Array.from({ length: 39 }, () => "SMA-female"), "SMA-female на корпус для RG-178/RG-316/RG-174"];
    expect(typicalTexts(values)).toEqual(["SMA-female"]);
    const natural = naturalWidths([
      { key: "type", title: "Тип", numeric: false, first: false, texts: values },
      { key: "article", title: "Артикул", numeric: false, first: true, texts: values },
    ], measure);
    expect(natural.get("type")).toBe(100 + CELL_PAD);
    expect(natural.get("article")).toBe(100 + CELL_PAD); // the rare long one is cut with «…», the whole on hover
  });

  it("lets a caption take two lines: the best split of its words", () => {
    expect(headWidth("Сечение", measure)).toBe(70);
    // «Наружный диаметр, мм» → «Наружный» / «диаметр, мм»
    expect(headWidth("Наружный диаметр, мм", measure)).toBe(110);
  });

  it("gives a column its widest text, never less than its caption, up to the cap", () => {
    const natural = naturalWidths([
      { key: "mark", title: "Марка", numeric: false, first: true, texts: ["МГТФ 0,12", "МГТФ 0,035"] },
      { key: "note", title: "Примечание", numeric: false, first: false, texts: ["x".repeat(80)] },
      { key: "awg", title: "AWG", numeric: true, first: false, texts: ["26", "32"] },
    ], measure);
    expect(natural.get("mark")).toBe(100 + CELL_PAD);
    expect(natural.get("note")).toBe(360 + CELL_PAD); // LEVEL_MAX: the rest is cut with «…»
    expect(natural.get("awg")).toBe(30 + 14 + CELL_PAD); // the caption (and its sort mark) is wider than «26»
  });

  it("keeps the natural widths when the list fits", () => {
    const natural = new Map([["a", 100], ["b", 200]]);
    const { widths, wide } = levelWidths(natural, new Set(), [{ keys: ["a", "b"], taken: 0 }], 1000);
    expect([...widths.values()]).toEqual([100, 200]);
    expect(wide).toEqual([null]);
  });

  it("cuts the widest text columns first, keeps short ones and numbers whole", () => {
    const natural = new Map([["short", 100], ["long", 400], ["longer", 600], ["num", 80]]);
    const { widths, wide } = levelWidths(natural, new Set(["num"]), [{ keys: ["short", "long", "longer", "num"], taken: 36 }], 1000);
    expect(widths.get("short")).toBe(100);
    expect(widths.get("num")).toBe(80);
    expect(widths.get("long")).toBe(widths.get("longer"));
    const total = 36 + [...widths.values()].reduce((sum, width) => sum + width, 0);
    expect(total).toBeLessThanOrEqual(1000);
    expect(total).toBeGreaterThan(990); // the room is used, not left empty
    expect(wide).toEqual([null]);
  });

  it("scrolls sideways only a list that cannot fit even with every text at the floor", () => {
    const natural = new Map(Array.from({ length: 20 }, (_, index) => [`c${index}`, 300] as [string, number]));
    const { widths, wide } = levelWidths(natural, new Set(), [{ keys: [...natural.keys()], taken: 0 }], 20 * CAP_FLOOR - 1);
    expect(widths.get("c0")).toBe(CAP_READABLE);
    expect(wide[0]).toBe(20 * CAP_READABLE);
  });

  it("never cuts a column below its caption's longest word", () => {
    const natural = new Map([["maker", 300], ["note", 300]]);
    const floors = captionFloors([
      { key: "maker", title: "Производители", numeric: false, first: false, texts: [] },
      { key: "note", title: "Примечание к строке", numeric: false, first: false, texts: [] },
    ], measure);
    expect(floors.get("maker")).toBe(130 + 14 + CELL_PAD);
    const { widths } = levelWidths(natural, new Set(), [{ keys: ["maker", "note"], taken: 0 }], 300, floors);
    expect(widths.get("maker")).toBe(168);
    expect(widths.get("note")).toBeLessThan(168);
  });

  it("levels blocks with their own columns to the narrowest fit", () => {
    const natural = new Map([["a", 300], ["b", 300], ["c", 300]]);
    const { widths, wide } = levelWidths(natural, new Set(), [{ keys: ["a", "b", "c"], taken: 0 }, { keys: ["a"], taken: 0 }], 600);
    expect(widths.get("a")).toBe(200);
    expect(wide).toEqual([null, null]);
  });

  it("splits a block into pieces of rows for the window", () => {
    expect(chunksOf([1, 2, 3, 4, 5], 2)).toEqual([[1, 2], [3, 4], [5]]);
    expect(chunksOf([], 2)).toEqual([]);
  });
});
