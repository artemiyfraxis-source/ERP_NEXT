// @vitest-environment node
import { describe, expect, it } from "vitest";
import { drawingTableColumnWidths } from "./drawing-table-column-widths";

describe("drawingTableColumnWidths", () => {
  it("sizes each column to its longest header or value", () => {
    expect(drawingTableColumnWidths(["Поз.", "Наименование"], [["12", "Кронштейн"]]))
      .toEqual([42, 91]);
  });

  it("keeps empty tables readable and caps exceptionally long content", () => {
    expect(drawingTableColumnWidths(["Поз.", "Примечание"], [], { maximum: 180 }))
      .toEqual([42, 78]);
    expect(drawingTableColumnWidths(["Код"], [["X".repeat(100)]], { maximum: 180 }))
      .toEqual([180]);
  });
});
