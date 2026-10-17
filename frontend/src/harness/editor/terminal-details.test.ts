// @vitest-environment node
import { describe, expect, it } from "vitest";
import { createBuiltInConnectorInstance, connectorSeriesCatalogId } from "./connector-series-demo";
import { applyEditorCommand, createWire } from "./commands";
import { createEmptyHarnessDesign, parseHarnessDesignDocument } from "./model";
import { hydrateTerminalDetails, resolveTerminalDetails, terminalDetailsFromRecord, wireEndTerminalCorrections } from "./terminal-details";

const record = {
  recordId: "record", entityType: "terminal", sourceKey: "T-001", sourceLocation: null,
  payload: { manufacturer: "Acme", series: "Series A", description: "Crimp terminal", reelArticle: "T-001" },
};

describe("terminal details", () => {
  it("reads the bundled terminal catalog Product Name and decodes its article identity", () => {
    expect(terminalDetailsFromRecord({...record,sourceKey:"3:JST|6:SPH-02|0:|2:PH",payload:{manufacturer:"JST",series:"PH",productName:"Crimp contact"}}))
      .toEqual({manufacturer:"JST",series:"PH",description:"Crimp contact",article:"SPH-02"});
  });
  it("captures catalog fields and persists them with the selected contact", () => {
    const details = terminalDetailsFromRecord(record);
    expect(details).toEqual({ manufacturer: "Acme", series: "Series A", description: "Crimp terminal", article: "T-001" });
    const connector = createBuiltInConnectorInstance(connectorSeriesCatalogId("jst-xh"), { id: "X1", designation: "X1", partNumber: "XHP-2", e4Position: { x: 0, y: 0 } });
    const document = parseHarnessDesignDocument(JSON.parse(JSON.stringify({
      ...createEmptyHarnessDesign(),
      connectors: [{ ...connector, contacts: [{ ...connector.contacts[0], terminalArticle: "T-001", terminalDetails: details }] }],
    })));
    expect(document.connectors[0]!.contacts[0]!.terminalDetails).toEqual(details);
  });

  it("hydrates only exact matching legacy keys and preserves authored details", () => {
    const connector = createBuiltInConnectorInstance(connectorSeriesCatalogId("jst-xh"), { id: "X1", designation: "X1", partNumber: "XHP-2", e4Position: { x: 0, y: 0 } });
    const first = connector.contacts[0]!;
    const document = { ...createEmptyHarnessDesign(), connectors: [{ ...connector, contacts: [
      { ...first, terminalArticle: "T-001" },
      { ...first, id: `${first.id}:saved`, number: 2, terminalArticle: "T-001", terminalDetails: { manufacturer: "Saved", series: "", description: "", article: "T-001" } },
      { ...first, id: `${first.id}:unknown`, number: 3, terminalArticle: "OTHER" },
    ] }] };
    const next = hydrateTerminalDetails(document, [record]);
    expect(next.connectors[0]!.contacts.map(contact => contact.terminalDetails?.manufacturer)).toEqual(["Acme", "Saved", undefined]);
    expect(hydrateTerminalDetails(next, [record])).toBe(next);
  });

  it("resolves a bare article only when its catalog identity is unambiguous", () => {
    const composite = { ...record, sourceKey: "3:Acme|5:T-001|0:|3:SER" };
    expect(resolveTerminalDetails("T-001", [composite])).toMatchObject({ manufacturer: "Acme", article: "T-001" });
    expect(resolveTerminalDetails("3:Acme|5:T-001|0:|3:SER", [composite])).toMatchObject({ series: "Series A" });
    expect(resolveTerminalDetails("T-001", [composite, { ...composite, recordId: "other", sourceKey: "3:XYZ|5:T-001|0:|3:SER", payload: { ...composite.payload, manufacturer: "XYZ" } }]))
      .toBeUndefined();
  });

  it("preserves details when the same article is reselected without cache data and clears them for a changed article", () => {
    const connector = createBuiltInConnectorInstance(connectorSeriesCatalogId("jst-xh"), { id: "X1", designation: "X1", partNumber: "XHP-2", e4Position: { x: 0, y: 0 } });
    const contact = { ...connector.contacts[0]!, terminalArticle: "T-001", terminalDetails: terminalDetailsFromRecord(record) };
    const document = { ...createEmptyHarnessDesign(), connectors: [{ ...connector, contacts: [contact] }] };
    const same = applyEditorCommand(document, { type: "update-contact", connectorId: connector.id, contactId: contact.id, terminalArticle: "T-001" });
    expect(same.connectors[0]!.contacts[0]!.terminalDetails).toEqual(contact.terminalDetails);
    const changed = applyEditorCommand(document, { type: "update-contact", connectorId: connector.id, contactId: contact.id, terminalArticle: "T-002" });
    expect(changed.connectors[0]!.contacts[0]!.terminalDetails).toBeUndefined();
  });

  it("uses the terminal L+ by default and exposes L- as its signed counterpart for each connected end", () => {
    const from = createBuiltInConnectorInstance(connectorSeriesCatalogId("jst-xh"), { id: "X1", designation: "X1", partNumber: "XHP-2", e4Position: { x: 0, y: 0 } });
    const to = createBuiltInConnectorInstance(connectorSeriesCatalogId("jst-xh"), { id: "X2", designation: "X2", partNumber: "XHP-2", e4Position: { x: 200, y: 0 } });
    const records = [
      { ...record, sourceKey: "T-FROM", payload: { ...record.payload, reelArticle: "T-FROM", lengthPlusMm: "1,25", lengthMinusMm: "0.5" } },
      { ...record, sourceKey: "T-TO", payload: { ...record.payload, reelArticle: "T-TO", lengthPlusMm: -2, lengthMinusMm: -0.75 } },
    ];
    const document = {
      ...createEmptyHarnessDesign(),
      connectors: [
        { ...from, contacts: [{ ...from.contacts[0]!, terminalArticle: "T-FROM", terminalDetails: terminalDetailsFromRecord(records[0]!) }] },
        { ...to, contacts: [{ ...to.contacts[0]!, terminalArticle: "T-TO", terminalDetails: terminalDetailsFromRecord(records[1]!) }] },
      ],
    };
    const wire = createWire("W1", { connectorId: from.id, contactId: from.contacts[0]!.id }, { connectorId: to.id, contactId: to.contacts[0]!.id }, 100);
    const corrections = wireEndTerminalCorrections(document, wire, records);

    expect(corrections).toEqual({
      from: { terminalArticle: "T-FROM", plusMm: 1.25, minusMm: -0.5 },
      to: { terminalArticle: "T-TO", plusMm: 2, minusMm: -0.75 },
    });
    expect({ ...wire, endCorrectionFromMm: corrections.from!.plusMm!, endCorrectionToMm: corrections.to!.plusMm! })
      .toMatchObject({ endCorrectionFromMm: 1.25, endCorrectionToMm: 2 });
  });
});
