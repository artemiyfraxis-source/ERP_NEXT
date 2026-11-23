import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { ProjectTechcard } from "./api";
import { EtkPfList } from "./EtkPfList";
import { previewSummary } from "./GenPfScheme";
import { etkPfGroups, etkRouteChips, etkRouteOf, etkRouteSummary, formatTimeTotal, operationMinutes, operationTimeSummary } from "./etkRoute";
import { routeTotals } from "./routeLogic";

afterEach(cleanup);

const operations = [
  { position: 1, code: "KNOWN", title: "Резка", time: [{ norm: "2" }] },
  { position: 2, code: "UNKNOWN", title: "Маркировка", time: [{ norm: "1" }, { norm: "" }], times: { operation_seconds: 999 } },
  { position: 3, code: "ZERO", title: "Контроль", time: [{ norm: "0" }] },
];
const document = {
  kit: { part_quantity: "10" }, operations,
  route: { source: "manual", final: "f", blocks: [
    { key: "f", name: "Изделие", ops: [1, 2, 3], final: true, minutes: 3, unit_minutes: .3,
      rolled_minutes: .3, rolled_ops: 3, unknown_operations: 1, rolled_unknown_operations: 1 },
  ], edges: [] },
};
const card = { id: "card", version: 1, status: "Черновик", document } as unknown as ProjectTechcard;

describe("unknown time norms", () => {
  it.each([undefined, null, "", "  ", "не задано", "NaN", "Infinity", "-1"])("keeps %s unknown while retaining a numeric subtotal", (missing) => {
    const operation = { time: [{ norm: "2,5" }, { norm: missing }], times: { operation_seconds: 999 } };
    expect(operationTimeSummary(operation, 10)).toEqual({ minutes: 2.5, unknown: true });
    expect(operationMinutes(operation, 10)).toBe(2.5);
  });

  it("distinguishes an explicit zero from unknown and retains legacy seconds", () => {
    expect(operationTimeSummary(operations[2], 10)).toEqual({ minutes: 0, unknown: false });
    expect(operationTimeSummary({ times: { operation_seconds: 12 } }, 10)).toEqual({ minutes: 2, unknown: false });
    expect(formatTimeTotal(0)).toBe("0 мин");
    expect(formatTimeTotal(0, 1)).toBe("≥ 0 мин, не задано у 1 операции");
  });

  it("keeps unknown counts in PFs, the whole route and generator preview", () => {
    const route = etkRouteOf(document)!;
    expect(etkPfGroups(route, operations, 10)[0]).toMatchObject({ minutes: 3, unknownOperations: 1 });
    expect(etkRouteChips(route)[0]).toMatchObject({ perUnit: "≥ 0,3 мин/шт, не задано у 1 операции", rolledUnknownOperations: 1 });
    expect(etkRouteSummary(etkRouteChips(route))).toContain("на изделие ≥ 0,3 мин, не задано у 1 операции");
    render(<>{previewSummary(card)}</>);
    expect(screen.getByText("≥ 0,3 мин, не задано у 1 операции")).toBeInTheDocument();
  });

  it("shows the incomplete PF subtotal instead of an exact total", () => {
    render(<EtkPfList card={card} list={operations} selected={0} onPick={() => {}} onSaved={() => {}} onClose={() => {}}
      title={(op) => op.title} sub={() => ""} />);
    expect(screen.getByText("≥ 3 мин, не задано у 1 операции")).toBeInTheDocument();
  });

  it("keeps unknown counts through editor routes without counting shared upstream operations twice", () => {
    const route = { blocks: [
      { key: "a", name: "", ops: [1], final: false, components: [] },
      { key: "b", name: "", ops: [2], final: false, components: [] },
      { key: "f", name: "", ops: [3], final: true, components: [] },
    ], edges: [{ from: "a", to: "b", per_unit: 2 }, { from: "a", to: "f", per_unit: 1 }, { from: "b", to: "f", per_unit: 1 }] };
    const ops = [
      { position: 1, key: "1", type: "", code: "", label: "", minutes: 2, unknownTime: true },
      { position: 2, key: "2", type: "", code: "", label: "", minutes: null },
      { position: 3, key: "3", type: "", code: "", label: "", minutes: 0 },
    ];
    expect(routeTotals(route, ops, 1).get("f")).toMatchObject({ rolled: 6, rolledUnknownOperations: 2 });
  });
});
