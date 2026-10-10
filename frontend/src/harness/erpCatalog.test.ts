// @vitest-environment node
// Fixture: a slice of GET /api/techcards/harness-catalog built by backend/app/modules/techcards/harness_catalog.py
// over backend/data/reference-mirror/ (our references, not the TECHMAP-GRAPHER sample catalog).
import { describe, expect, it, vi } from "vitest";
import fixture from "./erpCatalog.fixture.json";
import {
  connectorKnown,
  createErpHarnessEditorCatalog,
  createErpReferenceCatalog,
  findMissingReferences,
  normalizeConnectorArticle,
  parseErpHarnessCatalog,
  type ErpHarnessCatalog,
} from "./erpCatalog";
import {
  normalizeCoaxTerminationCatalogCandidate,
  referenceRecordToEditorCatalogItem,
  remoteEditorCatalogSources,
} from "./editor/editor-reference-catalog";
import { createEmptyHarnessDesign, type ConnectorContact, type ConnectorInstance, type HarnessDesignDocument } from "./editor/model";
import { terminalLengthCorrectionOptions } from "./editor/terminal-details";
import { isSelectableWirePayload, wireDatabaseOption } from "./editor/wire-database";
import type { ReferenceCatalogSearchRequest, ReferenceCatalogSnapshot } from "./reference-catalog";

const catalog = parseErpHarnessCatalog(fixture);
const snapshot = (sourceId: string) => catalog.snapshots.find((item) => item.sourceId === sourceId)!;
const record = (sourceId: string, key: string) => snapshot(sourceId).records.find((item) => item.sourceKey === key)!;
const request = (entityTypes: readonly string[], text: string | null = null): ReferenceCatalogSearchRequest => ({
  text, exactSourceKey: null, entityTypes, filters: [], filterLogic: "all", sort: "source-key-asc", pageSize: 30, cursor: null,
});

describe("ERP harness catalog adapter", () => {
  it("fetches the catalog once and serves every picker from it", async () => {
    const load = vi.fn(async () => catalog);
    const references = createErpReferenceCatalog(load);
    const terminals = await references.searchCatalog("technology-terminals", request(["terminal"]));
    const wires = await references.searchCatalog("technology-wires", request(["wire", "cable"], "ul1061"));
    const coverings = await references.searchCatalog("technology-database", request(["protective-covering"]));
    expect(terminals.items.map((item) => item.sourceKey)).toEqual(["08701039", "SSHL-002T-P0.2"]);
    expect(wires.items.map((item) => item.sourceKey)).toEqual(["UL1061 28AWG"]);
    expect(coverings.items.map((item) => item.sourceKey)).toEqual(["ТУТнг 4/2 черная"]);
    expect(terminals.snapshotSha256).toBe(snapshot("technology-terminals").sha256);
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("retries after a failed load and reports an unloaded source as not published", async () => {
    const empty: ErpHarnessCatalog = {
      ...catalog,
      snapshots: catalog.snapshots.map((item) => item.sourceId === "technology-terminals" ? { ...item, records: [] } : item),
    };
    const load = vi.fn<() => Promise<ErpHarnessCatalog>>()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue(empty);
    const references = createErpReferenceCatalog(load);
    await expect(references.getActive("technology-wires")).rejects.toThrow("Failed to fetch");
    expect(await references.getActive("technology-terminals")).toBeNull();
    await expect(references.searchCatalog("technology-terminals", request(["terminal"])))
      .rejects.toMatchObject({ code: "catalog_active_snapshot_not_found" });
    expect((await references.getActive("technology-wires"))?.records).toHaveLength(3);
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("refuses a malformed answer or another contract", () => {
    expect(() => parseErpHarnessCatalog({ snapshots: [{ sourceId: "x" }] })).toThrow("неверном формате");
    expect(() => parseErpHarnessCatalog({ ...fixture, contractVersion: 2 })).toThrow("Обновите страницу");
  });

  it("is the editor's full catalog: references and the component library from the ERP, empty textures", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify([{ templateId: "t", version: 2, code: "SHR-04V-S",
      name: "JST SH", connectorRef: "SHR-04V-S", articleBindings: [], createdUtc: "c", updatedUtc: "u", hasImage: true }])));
    vi.stubGlobal("fetch", fetch);
    const editorCatalog = createErpHarnessEditorCatalog(async () => catalog);
    expect(await editorCatalog.templates.list()).toEqual([{ templateId: "t", version: 2, code: "SHR-04V-S", name: "JST SH",
      articleBindings: [], createdUtc: "c", updatedUtc: "u" }]);
    expect(fetch).toHaveBeenCalledWith("/api/techcards/harness-components", expect.anything());
    expect(editorCatalog.templates.assetContentUrl("t", 2, "a")).toBe("/api/techcards/harness-components/t/versions/2/assets/a");
    vi.unstubAllGlobals();
    expect(await editorCatalog.materials.listMaterials()).toEqual([]);
    expect((await editorCatalog.references.getActive("technology-coax-terminations"))?.records).toHaveLength(3);
  });
});

describe("our records in the editor's own readers", () => {
  it("terminal L+ / L− become the wire-end corrections of the cut length", () => {
    const terminals = snapshot("technology-terminals").records;
    expect(terminalLengthCorrectionOptions("SSHL-002T-P0.2", terminals)).toEqual({
      terminalArticle: "SSHL-002T-P0.2", plusMm: 5, minusMm: -10,
    });
    expect(terminalLengthCorrectionOptions("08701039", terminals)).toEqual({
      terminalArticle: "08701039", plusMm: 5, minusMm: null,
    });
    const source = remoteEditorCatalogSources.find((item) => item.id === "technology-terminals")!;
    const item = referenceRecordToEditorCatalogItem(source, record("technology-terminals", "08701039"));
    expect(item.subtitle).toContain("Mini-SPOX");
    expect(item.subtitle).toContain("0,08–0,35 мм²");
  });

  it("wires and multicore cables get marks, sections and outer diameters", () => {
    const wire = wireDatabaseOption(record("technology-wires", "UL1061 28AWG"));
    expect([wire.label, wire.diameterMm]).toEqual(["UL1061 28AWG · 0,08", 0.89]);
    const cable = record("technology-wires", "ПВВС 4×0,35");
    expect(cable.entityType).toBe("cable");
    expect(wireDatabaseOption(cable).section).toBe("4x0,35");
    // A coax without a conductor section is listed but is not a wire for the contact table.
    expect(isSelectableWirePayload(record("technology-wires", "RG316").payload)).toBe(false);
  });

  it("coax termination: a full БД.КОАКС row is bindable, partial ones are flagged by the editor", () => {
    const pin = { snapshotId: snapshot("technology-coax-terminations").snapshotId, snapshotSha256: snapshot("technology-coax-terminations").sha256 };
    const ready = normalizeCoaxTerminationCatalogCandidate("technology-coax-terminations", record("technology-coax-terminations", "MCX-male RG-174"), pin);
    expect(ready.state).toBe("ready");
    expect(ready.binding?.layers).toEqual([
      { index: 1, diameterMm: 0.48, stripLengthMm: 2 },
      { index: 2, diameterMm: 1.52, stripLengthMm: 4 },
      { index: 3, diameterMm: 1.9, stripLengthMm: 10.5 },
    ]);
    const noLengths = normalizeCoaxTerminationCatalogCandidate("technology-coax-terminations", record("technology-coax-terminations", "MCX-female RG-174"), pin);
    expect(noLengths.state).toBe("incomplete");
    expect(noLengths.diagnostics.map((item) => item.code)).toContain("layer-strip-length-missing");
    // L1..L3 of BNC-C58P are not cumulative (5 / 2,5 / 12): kept as written, the editor refuses to bind it.
    const notCumulative = normalizeCoaxTerminationCatalogCandidate("technology-coax-terminations", record("technology-coax-terminations", "BNC-C58P"), pin);
    expect(notCumulative.state).toBe("incomplete");
  });
});

function connector(id: string, partNumber: string, contacts: Partial<ConnectorContact>[]): ConnectorInstance {
  return {
    id, designation: id, partNumber,
    contacts: contacts.map((contact, index) => ({
      id: `${id}:${index + 1}`, number: index + 1, contactType: "", circuit: "", terminalArticle: "", wire: "",
      color: "", connectionStatus: "available", customValues: {}, ...contact,
    })),
  } as unknown as ConnectorInstance;
}

function document(parts: Partial<HarnessDesignDocument>): HarnessDesignDocument {
  return { ...createEmptyHarnessDesign(), ...parts };
}

describe("«нет в справочнике»", () => {
  const snapshots: readonly ReferenceCatalogSnapshot[] = catalog.snapshots;

  it("connector part numbers are checked against the ERP base of connectors, analogues included", () => {
    const records = snapshot("technology-connectors").records;
    expect(normalizeConnectorArticle(" JST xhp-4 ")).toBe("XHP-4");
    expect(connectorKnown("XHP-4", records)).toBe(true);
    expect(connectorKnown("BL2018P-04", records)).toBe(true);
    expect(connectorKnown("LP-12", records)).toBe(true);
    expect(connectorKnown("ABC-123", records)).toBe(false);
  });

  it("lists what the document uses and our references do not hold", () => {
    const wireBinding = {
      sourceId: "technology-wires", snapshotId: snapshot("technology-wires").snapshotId,
      snapshotSha256: snapshot("technology-wires").sha256, entityType: "wire" as const,
    };
    const doc = document({
      connectors: [
        connector("X1", "XHP-4", [
          { terminalArticle: "08701039", wire: "UL1061 28AWG", wireSection: "0,08" },
          { terminalArticle: "SXH-001T-P0.6", wire: "МГТФ", wireSection: "0,12" },
          { terminalArticle: "SXH-001T-P0.6", materialBinding: { ...wireBinding, recordId: "a".repeat(64), sourceKey: "UL1061 28AWG", displayName: "UL1061" } },
        ]),
        connector("X2", "ABC-123", []),
      ],
      wires: [{
        id: "w1", circuit: "GND",
        materialBinding: { ...wireBinding, recordId: "b".repeat(64), sourceKey: "МГШВ 0,35", displayName: "МГШВ 0,35" },
        stripProfiles: { to: {
          sourceId: "technology-coax-terminations", snapshotId: "s", snapshotSha256: "s", recordId: "c".repeat(64),
          entityType: "coax-termination", sourceKey: "SMA-неизвестный", displayName: "SMA", layers: [],
        } },
      } as unknown as HarnessDesignDocument["wires"][number]],
    });
    expect(findMissingReferences(doc, snapshots)).toEqual([
      { kind: "terminal", key: "SXH-001T-P0.6", places: ["X1, конт. 2", "X1, конт. 3"], status: "нет в справочнике" },
      { kind: "wire", key: "МГТФ · 0,12", places: ["X1, конт. 2"], status: "нет в справочнике" },
      { kind: "connector", key: "ABC-123", places: ["X2"], status: "нет в справочнике" },
      { kind: "wire", key: "МГШВ 0,35", places: ["провод GND"], status: "нет в справочнике" },
      { kind: "coax-termination", key: "SMA-неизвестный", places: ["провод GND, конец"], status: "нет в справочнике" },
    ]);
  });

  it("a document built only from our references has nothing to flag", () => {
    const doc = document({ connectors: [connector("X1", "XHP-4", [{ terminalArticle: "08701039", wire: "UL1061 28AWG" }])] });
    expect(findMissingReferences(doc, snapshots)).toEqual([]);
  });
});
