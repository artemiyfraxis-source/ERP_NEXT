// @vitest-environment node
import { describe, expect, it } from "vitest";
import { addTwistMember, planTwist, setTwistMember, twistGroupProblems, twistGroupsOf, type TwistRow } from "./twistGroups";

const NAMES = ["МГТФ 0,12 красный", "МГТФ 0,12 черный", "МГТФ 0,12 белый", "МГТФ 0,12 зеленый"];

describe("twist groups of rows", () => {
  it("reads pairs as before and a third wire as a group of three", () => {
    expect(twistGroupsOf([{ a: "0", b: "1" }])).toEqual([[0, 1]]);
    expect(twistGroupsOf([{ a: "0", b: "1", more: ["2"] }])).toEqual([[0, 1, 2]]);
    // Невыбранные места и повторы не считаются; меньше двух проводов — не группа; позиции вне списка отброшены.
    expect(twistGroupsOf([{ a: "0", b: "", more: ["", "2"] }, { a: "1", b: "1" }, { a: "3", b: "" }])).toEqual([[0, 2]]);
    expect(twistGroupsOf([{ a: "0", b: "1", more: ["5"] }], 4)).toEqual([[0, 1]]);
  });

  it("edits a row: «+ провод» adds a place, an emptied third place goes away", () => {
    let rows: TwistRow[] = [{ a: "0", b: "1" }];
    rows = addTwistMember(rows, 0);
    expect(rows).toEqual([{ a: "0", b: "1", more: [""] }]);
    rows = setTwistMember(rows, 0, 2, "2");
    expect(rows).toEqual([{ a: "0", b: "1", more: ["2"] }]);
    rows = setTwistMember(rows, 0, 1, "3");
    expect(rows).toEqual([{ a: "0", b: "3", more: ["2"] }]);
    rows = setTwistMember(rows, 0, 2, "");
    expect(rows).toEqual([{ a: "0", b: "3" }]);
  });
});

describe("twist checks", () => {
  it("stops on a wire twisted into two groups", () => {
    const { error } = twistGroupProblems([{ a: "0", b: "1", more: ["2"] }, { a: "2", b: "3" }], NAMES, [1, 1, 1, 1]);
    expect(error).toBe("Свивка: «МГТФ 0,12 белый» — в свивках 1 и 2. Провод свивается только в одной группе.");
  });

  it("asks about a lone wire, a wire chosen twice and different wire counts", () => {
    const { error, warnings } = twistGroupProblems(
      [{ a: "0", b: "" }, { a: "1", b: "2", more: ["2"] }, { a: "3", b: "0", more: ["1"] }], NAMES, [1, 1, 1, 2]);
    // Строка с одним проводом — не группа: красный из первой строки в двух группах не числится.
    expect(error).toBe("Свивка: «МГТФ 0,12 черный» — в свивках 2 и 3. Провод свивается только в одной группе.");
    expect(warnings).toEqual([
      "Свивка 1: выбран один провод — в свивку не войдёт.",
      "Свивка 2: «МГТФ 0,12 белый» выбран дважды.",
      "Свивка 3: у кабелей разное число проводов (2, 1, 1) — свиваются 1.",
    ]);
  });

  it("has nothing to say about plain pairs", () => {
    expect(twistGroupProblems([{ a: "0", b: "1" }, { a: "2", b: "3" }], NAMES, [1, 1, 1, 1])).toEqual({ error: "", warnings: [] });
  });
});

describe("twist on the drawing", () => {
  const units: Record<number, string[]> = { 0: ["r"], 1: ["k"], 2: ["w"], 3: ["g1", "g2"] };
  const unitsOf = (pos: number) => units[pos] ?? [];

  it("keeps two wires a twisted pair and three or more a group", () => {
    expect(planTwist("pairs", [[0, 1], [2, 3]], unitsOf)).toEqual({ pairs: [["r", "k"], ["w", "g1"]], groups: [] });
    expect(planTwist("pairs", [[0, 1, 2]], unitsOf)).toEqual({ pairs: [], groups: [["r", "k", "w"]] });
  });

  it("twists every wire of the chosen cables together in «Все вместе»", () => {
    expect(planTwist("all", [[0, 3]], unitsOf)).toEqual({ pairs: [], groups: [["r", "g1", "g2"]] });
    expect(planTwist("all", [[0, 1]], unitsOf)).toEqual({ pairs: [["r", "k"]], groups: [] });
    expect(planTwist("all", [[0]], unitsOf)).toEqual({ pairs: [], groups: [] });
  });
});
