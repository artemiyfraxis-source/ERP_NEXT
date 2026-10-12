// @vitest-environment node
import { describe, expect, it } from "vitest";
import { applyEditorCommand, createConnector } from "./commands";
import {
  collectE4Diagnostics,
  diagnoseDuplicateConnectorDesignations,
  normalizeConnectorDesignation,
} from "./e4-diagnostics";
import { createEmptyHarnessDesign } from "./model";

describe("E4 diagnostics", () => {
  it("accepts duplicate designations in the document and reports every affected connector", () => {
    let document = createEmptyHarnessDesign();
    document = applyEditorCommand(document, {
      type: "add-connector",
      connector: createConnector("connector-a", "X2", 1, { x: 20, y: 40 }),
    });
    document = applyEditorCommand(document, {
      type: "add-connector",
      connector: createConnector("connector-b", "X4", 1, { x: 300, y: 40 }),
    });
    document = applyEditorCommand(document, {
      type: "add-connector",
      connector: createConnector("connector-c", "X3", 1, { x: 580, y: 40 }),
    });
    document = applyEditorCommand(document, {
      type: "update-connector", connectorId: "connector-b", designation: "x2",
    });

    expect(document.connectors.map((connector) => connector.designation)).toEqual(["X2", "x2", "X3"]);
    expect(collectE4Diagnostics(document)).toEqual([
      {
        id: "duplicate-connector-designation:connector-a",
        code: "duplicate-connector-designation",
        severity: "error",
        message: "Обозначение «X2» используется несколькими соединителями.",
        designation: "X2",
        target: { view: "e4", kind: "connector", objectId: "connector-a" },
        conflictingObjectIds: ["connector-b"],
      },
      {
        id: "duplicate-connector-designation:connector-b",
        code: "duplicate-connector-designation",
        severity: "error",
        message: "Обозначение «x2» используется несколькими соединителями.",
        designation: "x2",
        target: { view: "e4", kind: "connector", objectId: "connector-b" },
        conflictingObjectIds: ["connector-a"],
      },
    ]);
  });

  it("keeps diagnostics in document order and links every member of a larger duplicate group", () => {
    const connectors = [
      createConnector("first", "XS7", 1, { x: 0, y: 0 }),
      createConnector("unique", "XS8", 1, { x: 0, y: 0 }),
      createConnector("second", "XS7", 1, { x: 0, y: 0 }),
      createConnector("third", "XS7", 1, { x: 0, y: 0 }),
    ];

    const diagnostics = diagnoseDuplicateConnectorDesignations(connectors);

    expect(diagnostics.map((diagnostic) => diagnostic.target.objectId)).toEqual(["first", "second", "third"]);
    expect(diagnostics.map((diagnostic) => diagnostic.conflictingObjectIds)).toEqual([
      ["second", "third"],
      ["first", "third"],
      ["first", "second"],
    ]);
  });

  it("normalizes casing and surrounding whitespace but does not report unique designations", () => {
    expect(normalizeConnectorDesignation("  xsа-12  ")).toBe("XSА-12");
    expect(normalizeConnectorDesignation("XИ\u0306-1")).toBe(normalizeConnectorDesignation("XЙ-1"));
    const connectors = [
      createConnector("first", "X1", 1, { x: 0, y: 0 }),
      createConnector("second", "X2", 1, { x: 0, y: 0 }),
    ];
    expect(diagnoseDuplicateConnectorDesignations(connectors)).toEqual([]);
  });
});
