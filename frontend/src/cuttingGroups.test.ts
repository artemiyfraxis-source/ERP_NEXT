import { describe, expect, it } from "vitest";
import { cuttingProjects, cuttingRows, labelTotals, sharesText, tubeProjectHint, tubeTotals, type CuttingGroup, type CuttingMember, type MarkingProgress } from "./cuttingGroups";

const member = (index: number, extra: Partial<CuttingMember> = {}): CuttingMember => ({
  id: `m${index}`, task_id: `t${index}`, operation_id: `o${index}`, task_version: 1, task_number: `ПЗ-${index}`, project: "708К.",
  item: `КБ${index + 1}`, name: `Кабель ${index + 1}`, position: index, assembly_quantity: 15, blank_quantity: "15", operation_quantity: "15",
  cables_per_assembly: 1, cut_length_mm: null, labels: [], good: "0", defects: "0", membership_state: "active", state: "assigned",
  operator_ids: [], planned_start: null, planned_finish: null, norm_seconds: 0, boxes: [], etk_url: "", ...extra,
});
const tube = (material: string, length_mm: string | number, quantity: string | number) => ({ material, length_mm, quantity });
const text = (value: string) => value.replaceAll(" ", " ");

describe("tubeTotals", () => {
  it("sums the same material and length over КБ: 20 + 30 pieces of 15 mm are one row of 50", () => {
    const { rows, missing } = tubeTotals([member(0, { tubes: [tube("ТУТ 2:1 Ø6", "15", 20)] }), member(1, { tubes: [tube("ТУТ 2:1 Ø6", 15, "30")] })]);
    expect(rows.map(({ material, length, quantity }) => [material, length, quantity])).toEqual([["ТУТ 2:1 Ø6", 15, 50]]);
    expect(missing).toEqual([]);
  });

  it("keeps another length or another article separate and orders by material, then length", () => {
    const { rows } = tubeTotals([
      member(0, { tubes: [tube("ТУТ 2:1 Ø6", "30", 10), tube("ТУТ 2:1 Ø6", "15,0", 20)] }),
      member(1, { tubes: [tube("ТТК 12/4", "15", 5), tube("ТУТ 2:1 Ø6", "15", 30)] }),
    ]);
    expect(rows.map(({ material, length, quantity }) => [material, length, quantity])).toEqual([
      ["ТТК 12/4", 15, 5], ["ТУТ 2:1 Ø6", 15, 50], ["ТУТ 2:1 Ø6", 30, 10]]);
  });

  it("8 КБ: the face has one row per material × length, never one per КБ", () => {
    const rows = Array.from({ length: 8 }, (_, index) => member(index, { tubes: [tube("ТУТ 2:1 Ø3", 35, 30), tube("ТУТ 2:1 Ø3", 55, 15)] }));
    expect(tubeTotals(rows).rows.map((row) => row.quantity)).toEqual([240, 120]);
  });

  it("names КБ without tube data or with a broken quantity instead of counting them as zero", () => {
    const { rows, missing } = tubeTotals([member(0, { tubes: [tube("ТУТ", 15, 10)] }), member(1), member(2, { tubes: [] }),
      member(3, { tubes: [tube("ТУТ", 15, "много")] })]);
    expect(rows.map((row) => row.quantity)).toEqual([10]);
    expect(missing).toEqual(["КБ2", "КБ3", "КБ4"]);
  });
});

describe("project split hint", () => {
  it("stays empty for one project", () => {
    const rows = [member(0, { tubes: [tube("ТУТ", 15, 20)] }), member(1, { tubes: [tube("ТУТ", 15, 30)] })];
    expect(tubeProjectHint(rows, tubeTotals(rows).rows)).toEqual([]);
  });

  it("one tube size: pieces per project in the summary unit", () => {
    const rows = [member(0, { tubes: [tube("ТУТ", 15, 20)] }), member(1, { project: "709К.", tubes: [tube("ТУТ", 15, 30)] }),
      member(2, { tubes: [tube("ТУТ", 15, 30)] })];
    expect(cuttingProjects(rows)).toEqual(["708К.", "709К."]);
    expect(tubeProjectHint(rows, tubeTotals(rows).rows).map(text)).toEqual(["708К.: 50 шт; 709К.: 30 шт"]);
  });

  it("several sizes: the hint names the material and length of each cut", () => {
    const rows = [member(0, { tubes: [tube("ТУТ", 15, 20), tube("ТУТ", 30, 4)] }), member(1, { project: "709К.", tubes: [tube("ТУТ", 15, 30)] })];
    expect(tubeProjectHint(rows, tubeTotals(rows).rows).map(text)).toEqual([
      "ТУТ · 15 мм — 708К.: 20 шт; 709К.: 30 шт", "ТУТ · 30 мм — 708К.: 4 шт"]);
  });
});

describe("labelTotals", () => {
  const row = (task_id: string, total: number, printed: number) => ({ task_id, task_number: "", item: "", quantity: 15,
    printed: 0, remaining: 15, remaining_labels: total - printed, total_labels: total, printed_labels: printed });
  const progress = (rows: MarkingProgress["rows"]): MarkingProgress => ({ rows, printed: 0, remaining: 0, remaining_labels: 0 });

  it("28 КБ: label pieces in all, printed and left over the shown КБ", () => {
    const members = Array.from({ length: 28 }, (_, index) => member(index));
    const answer = progress(members.map((item, index) => row(item.task_id, 45, index < 10 ? 45 : 3)));
    expect(labelTotals(members, answer)).toMatchObject({ total: 1260, printed: 450 + 18 * 3, remaining: 18 * 42 });
  });

  it("counts only shown КБ (partial view, split or cancelled members) and each task once", () => {
    const group = { rows: [member(0), member(1, { membership_state: "split" }), member(2, { membership_state: "cancelled" }), member(3)] } as CuttingGroup;
    const shown = cuttingRows(group);
    const answer = progress([row("t0", 30, 10), row("t1", 300, 0), row("t2", 300, 0), row("t3", 30, 0), row("t9", 999, 0)]);
    expect(labelTotals([...shown, shown[0]], answer)).toMatchObject({ total: 60, printed: 10, remaining: 50 });
  });

  it("unknown, never zero: no progress, a missing КБ row, or an older server without label counts", () => {
    expect(labelTotals([member(0)], null)).toBeNull();
    expect(labelTotals([member(0), member(1)], progress([row("t0", 30, 0)]))).toBeNull();
    const old = { task_id: "t0", task_number: "", item: "", quantity: 15, printed: 0, remaining: 15, remaining_labels: 45 };
    expect(labelTotals([member(0)], progress([old]))).toBeNull();
  });

  it("splits what is left by project", () => {
    const members = [member(0), member(1, { project: "709К." }), member(2)];
    const totals = labelTotals(members, progress([row("t0", 45, 15), row("t1", 45, 15), row("t2", 30, 10)]))!;
    expect(text(sharesText(totals.projects))).toBe("708К.: 50 шт; 709К.: 30 шт");
  });
});
