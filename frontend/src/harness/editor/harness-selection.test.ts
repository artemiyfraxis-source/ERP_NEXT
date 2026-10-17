// @vitest-environment node
import { describe, expect, it } from "vitest";
import { createConnector, createWire } from "./commands";
import { createEmptyHarnessDesign, type HarnessDesignDocument } from "./model";
import { buildHarnessSelectionIndex, resolveHarnessSelection } from "./harness-selection";
import { selectedEditorDeletionCommands } from "./HarnessDesignEditor";

function fixture(): HarnessDesignDocument {
  const connectors = ["A", "B", "C"].map(id => createConnector(id, id, 2, {x: 0, y: 0}));
  const end = (connectorId: string, contactId: string) => ({connectorId, contactId: `${connectorId}:contact:${contactId}`});
  const wires = [createWire("W1", end("A", "1"), end("B", "1"), 100),
    createWire("W2", end("A", "2"), end("C", "1"), 100),
    createWire("W3", end("B", "2"), end("C", "2"), 101)];
  return {...createEmptyHarnessDesign(), connectors, wires};
}

describe("cross-view selection", () => {
  it("uses IDs, preserves independent contacts and never extends editable selection", () => {
    const doc = fixture();
    const selected = ["A"];
    const before = JSON.stringify(doc);
    const result = resolveHarnessSelection(buildHarnessSelectionIndex(doc), selected, true);
    expect(result.wireIds).toEqual(["W1", "W2"]);
    expect(selected).toEqual(["A"]);
    expect(selectedEditorDeletionCommands(doc, selected).every(command => command.type !== "remove-wire")).toBe(true);
    expect(JSON.stringify(doc)).toBe(before);
    expect(resolveHarnessSelection(buildHarnessSelectionIndex(doc), ["missing"]).unresolvedIds).toEqual(["missing"]);
  });
  it("traverses junctions with cycle protection but not geometric or label coincidences", () => {
    const doc = fixture();
    const wires = [
      {...doc.wires[0]!, to: {connectorId: "" as const, contactId: "" as const, junctionId: "J"}},
      {...doc.wires[1]!, from: {connectorId: "" as const, contactId: "" as const, junctionId: "J"}},
      doc.wires[2]!,
    ];
    const index = buildHarnessSelectionIndex({...doc, wires});
    expect(resolveHarnessSelection(index, ["W1"]).wireIds).toEqual(["W1"]);
    expect(resolveHarnessSelection(index, ["W1"], true).wireIds).toEqual(["W1", "W2"]);
  });
  it("maps cable cut rows to conductors without double material consumption", () => {
    const doc = fixture();
    const cable = {id:"K",memberWireIds:["W1","W2"],lengthMm:100.1,endCorrectionFromMm:2,endCorrectionToMm:3,cutRoundingStepMm:1};
    const model = {...doc, cables: [cable]};
    const index = buildHarnessSelectionIndex(model);
    expect(resolveHarnessSelection(index, ["K"]).wireIds).toEqual(["W1", "W2"]);
    expect(resolveHarnessSelection(index, ["W1"]).rowIds).toContain("K");
    // The cut rows themselves are the server's (backend tests/techcards/test_harness_documents.py).
  });
  it("indexes 300 conductors without confusing housing contacts or input order", () => {
    const base=fixture();
    const doc={...base,wires:Array.from({length:300},(_,i)=>({...base.wires[0]!,id:`wire-${i}`,from:{connectorId:"A",contactId:String(i)},to:{connectorId:"B",contactId:String(i)}}))};
    const index=buildHarnessSelectionIndex(doc);
    expect(resolveHarnessSelection(index,["A"]).wireIds).toHaveLength(300);
    expect(resolveHarnessSelection(index,["wire-42"],true).wireIds).toEqual(["wire-42"]);
    expect(resolveHarnessSelection(buildHarnessSelectionIndex({...doc,wires:[]}),["wire-42"]).unresolvedIds).toEqual(["wire-42"]);
  });
  it("does not conduct through shielded wires or across different contacts with similar labels", () => {
    const doc=fixture();
    const screen={id:"shield",wireIds:["W1"],position:.5,label:"SH",width:40};
    const drain={...doc.wires[2]!,id:"drain",from:{screenId:"shield",connectorId:"" as const,contactId:"" as const}};
    const index=buildHarnessSelectionIndex({...doc,screens:[screen],wires:[...doc.wires,drain]});
    expect(resolveHarnessSelection(index,["shield"],true).wireIds).not.toContain("W1");
    expect(resolveHarnessSelection(index,["W1"],true).wireIds).toEqual(["W1"]);
  });
});
