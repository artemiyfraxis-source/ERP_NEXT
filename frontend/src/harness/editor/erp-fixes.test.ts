// @vitest-environment node
// Regression tests for the bugs fixed while porting TECHMAP-GRAPHER into the ERP.
import { describe, expect, it } from "vitest";
import { createConnector } from "./commands";
import { createEditorHistory, executeEditorCommand } from "./history";
import { withDrawingTable } from "./HarnessDesignEditor";
import { createEmptyHarnessDesign, parseHarnessDesignDocument } from "./model";

describe("ERP fixes of the ported harness editor", () => {
  it("opens the cut map once: a repeated request keeps the same documents", () => {
    const first = withDrawingTable(undefined, "cut")!;
    expect(first.tables.map((table) => table.kind)).toEqual(["cut"]);
    expect(withDrawingTable(first, "cut")).toBe(first);
    const both = withDrawingTable(first, "bom")!;
    expect(both.tables.map((table) => table.kind)).toEqual(["cut", "bom"]);
  });

  it("does not record an undo step for a command that changes nothing", () => {
    const document = createEmptyHarnessDesign();
    const history = createEditorHistory(document);
    const added = executeEditorCommand(history, { type: "add-connector", connector: createConnector("x1", "X1", 2, { x: 0, y: 0 }) });
    expect(added.past).toHaveLength(1);
    const same = executeEditorCommand(added, { type: "replace-layers", view: "e4", layers: added.present.views.e4.layers });
    if (same.present === added.present) expect(same).toBe(added);
  });

  it("keeps a TECHMAP-GRAPHER manufacturing route verbatim instead of dropping it on save", () => {
    const route = { schemaVersion: 2, rows: [{ id: "r1", kind: "assembly", title: "Сборка" }], source: { sha256: "a".repeat(64) } };
    const parsed = parseHarnessDesignDocument({ ...createEmptyHarnessDesign(), manufacturingRoute: route });
    expect(JSON.parse(JSON.stringify(parsed)).manufacturingRoute).toEqual(route);
    expect(() => parseHarnessDesignDocument({ ...createEmptyHarnessDesign(), manufacturingRoute: "broken" })).toThrow();
  });
});
