// Ported from TECHMAP-GRAPHER © Василий (SaveOurSouls), used with the author's permission (THIRD_PARTY_NOTICES.md).
// Original: src/Techmap.Client/src/uml/uml-model.test.ts @ 0a456d2f7bd23288111e871058ebf55bf14426cd (branch codex/integration-m5-22).
import { describe, expect, it } from "vitest";
import { createUmlFromRouteV2, documentMinutes, nodeMinutes, operationMinutes, type UmlDocument } from "./uml-model";

describe("UML prototype model", () => {
  it("calculates running time by length, speed and employees", () => {
    expect(operationMinutes({ id: "r", title: "Протяжка", kind: "running", lengthMm: 1200, speedMmPerMinute: 600, employees: 2, quantity: 1, minutesEach: 1 })).toBe(1);
  });

  it("calculates static time by required semifinished quantity", () => {
    expect(operationMinutes({ id: "s", title: "Сборка", kind: "static", lengthMm: 0, speedMmPerMinute: 1, employees: 1, quantity: 3, minutesEach: 4 })).toBe(12);
  });

  it("imports route v2 dependencies without copying drawings", () => {
    const route = { version: 1 as const, nodes: [{ id: "a", kind: "semiFinished" as const, title: "ПФ-1", refs: [], quantity: 1, x: 1, y: 2, operatorConfirmed: false }, { id: "b", kind: "assembly" as const, title: "Сборка", refs: [], quantity: 1, x: 3, y: 4, operatorConfirmed: false }], edges: [{ id: "e", from: "a", to: "b" }], finalNodeId: null };
    const uml = createUmlFromRouteV2(route);
    expect(uml.nodes.map(node => node.sourceNodeId)).toEqual(["a", "b"]);
    expect(uml.edges).toEqual([{ id: "uml-e", from: "uml-a", to: "uml-b" }]);
    expect(uml.nodes.every(node => node.operations.length === 0)).toBe(true);
  });

  it("sums operation and document time", () => {
    const document: UmlDocument = { version: 1, nodes: [{ id: "a", title: "A", sourceNodeId: null, x: 0, y: 0, operations: [{ id: "op", title: "Статика", kind: "static", lengthMm: 0, speedMmPerMinute: 1, employees: 1, quantity: 2, minutesEach: 2 }] }], edges: [] };
    expect(nodeMinutes(document.nodes[0]!)).toBe(4);
    expect(documentMinutes(document)).toBe(4);
  });
});
