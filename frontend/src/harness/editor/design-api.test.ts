// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  createInMemoryHarnessDesignApi,
  HarnessDesignApiError,
  parseHarnessDesignSnapshot,
  parseRecoverableHarnessDesignContent,
} from "./design-api";
import { createBuiltInConnectorInstance } from "./connector-series-demo";
import { applyEditorCommand, createConnector, createWire } from "./commands";
import { createEmptyHarnessDesign, parseHarnessDesignDocument } from "./model";

// ERP: the HTTP client of TECHMAP-GRAPHER is replaced by an adapter; these tests run the same
// contracts against the in-memory adapter that stores snapshot v1 JSON.
const projectId = "22345678-1234-4123-8123-123456789abc";
const harnessId = "32345678-1234-4123-8123-123456789abc";
const content = createEmptyHarnessDesign();
const snapshot = (body: unknown, revision = 0) => JSON.stringify({ harnessId, schemaVersion: 1, revision, content: body, updatedUtc: "2026-09-13T00:00:00Z" });

describe("harness design API", () => {
  it("preserves the writer contract and rejects unsupported future documents", () => {
    expect(parseHarnessDesignDocument({ ...content, requiredWriterContractVersion: 1 }).requiredWriterContractVersion).toBe(1);
    expect(parseHarnessDesignDocument({ ...content, requiredWriterContractVersion: 2 }).requiredWriterContractVersion).toBe(2);
    expect(() => parseHarnessDesignDocument({ ...content, requiredWriterContractVersion: 3 })).toThrow(/новая версия/);
  });
  it("loads the shared E4 and drawing document from a stored snapshot v1", async () => {
    const api = createInMemoryHarnessDesignApi([{ projectId, snapshot: snapshot(content) }]);
    await expect(api.get(projectId, harnessId)).resolves.toMatchObject({ harnessId, revision: 0, content });
  });

  it("opens an empty document at revision 0 when nothing was stored yet", async () => {
    const api = createInMemoryHarnessDesignApi();
    await expect(api.get(projectId, harnessId)).resolves.toMatchObject({ harnessId, revision: 0, content });
    expect(api.exportSnapshot(projectId, harnessId)).toBeNull();
  });

  it("saves with optimistic document revision and exports snapshot v1 JSON", async () => {
    const api = createInMemoryHarnessDesignApi([{ projectId, snapshot: snapshot(content, 3) }]);
    const saved = await api.save(projectId, harnessId, 3, content);
    expect(saved.revision).toBe(4);
    const exported = JSON.parse(api.exportSnapshot(projectId, harnessId)!);
    expect(exported).toMatchObject({ harnessId, schemaVersion: 1, revision: 4, content });
    expect(parseHarnessDesignSnapshot(exported).content).toEqual(content);
  });

  it("round-trips connector series metadata through the save API", async () => {
    const connector = createBuiltInConnectorInstance("catalog-xs-10", {
      id: "xs1", designation: "XS1", e4Position: { x: 20, y: 30 },
    });
    const seriesContent = { ...content, connectors: [connector] };
    const api = createInMemoryHarnessDesignApi();

    const saved = await api.save(projectId, harnessId, 0, seriesContent);

    const sentContent = JSON.parse(api.exportSnapshot(projectId, harnessId)!).content as typeof seriesContent;
    expect(sentContent.connectors[0]).toMatchObject({
      libraryBinding: { mode: "series", seriesId: "xs-demo-series", partNumber: "XS-10" },
    });
    expect(sentContent.connectors[0]?.contacts[0]?.libraryContact).toEqual({ kind: "signal", ordinal: 1 });
    expect(saved.content.connectors[0]).toMatchObject({
      libraryBinding: { mode: "series", seriesId: "xs-demo-series", partNumber: "XS-10" },
    });
    expect(saved.content.connectors[0]?.contacts.at(-1)?.libraryContact).toEqual({ kind: "third", ordinal: 2 });
  });

  it("normalizes old schemaVersion 1 connectors before saving and rejects inconsistent metadata locally", async () => {
    const current = createBuiltInConnectorInstance("catalog-xs-04", {
      id: "xs1", designation: "XS1", e4Position: { x: 0, y: 0 },
    });
    const { libraryBinding: _binding, ...legacyConnector } = current;
    const legacyContacts = legacyConnector.contacts.map(({ libraryContact: _position, ...contact }) => contact);
    const api = createInMemoryHarnessDesignApi();
    const saved = await api.save(projectId, harnessId, 0, {
      ...content,
      connectors: [{ ...legacyConnector, contacts: legacyContacts }],
    });
    expect(saved.content.connectors[0]?.libraryBinding).toEqual({ mode: "free" });
    expect(saved.content.connectors[0]?.contacts.every((contact) => contact.libraryContact === null)).toBe(true);

    const inconsistent = {
      ...content,
      connectors: [{ ...current, partNumber: "BROKEN" }],
    };
    await expect(api.save(projectId, harnessId, 1, inconsistent)).rejects.toThrow(/не совпадает/);
    expect((await api.get(projectId, harnessId)).revision).toBe(1);
  });

  it("repairs stale automatic E4 geometry at the API boundary", async () => {
    let staleContent = createEmptyHarnessDesign();
    staleContent = applyEditorCommand(staleContent, {
      type: "add-connector", connector: createConnector("x1", "X1", 1, { x: 0, y: 0 }),
    });
    staleContent = applyEditorCommand(staleContent, {
      type: "add-connector", connector: createConnector("x2", "X2", 1, { x: 1600, y: 0 }),
    });
    staleContent = applyEditorCommand(staleContent, { type: "flip-connector-orientation", connectorId: "x2" });
    staleContent = applyEditorCommand(staleContent, {
      type: "add-wire",
      wire: createWire("w1", { connectorId: "x1", contactId: "x1:contact:1" }, { connectorId: "x2", contactId: "x2:contact:1" }),
    });
    staleContent = {
      ...staleContent,
      connectors: [
        ...staleContent.connectors,
        createConnector("obstacle", "X3", 1, { x: 800, y: 40 }),
      ],
    };
    const staleRoute = staleContent.wires[0]!.e4Route;
    const api = createInMemoryHarnessDesignApi();

    const saved = await api.save(projectId, harnessId, 0, staleContent);

    const storedContent = JSON.parse(api.exportSnapshot(projectId, harnessId)!).content as typeof staleContent;
    expect(storedContent.wires[0]?.e4Route).not.toEqual(staleRoute);
    expect(saved.content.wires[0]?.e4Route).toEqual(storedContent.wires[0]?.e4Route);
  });

  it("surfaces the current revision on a conflict", async () => {
    const api = createInMemoryHarnessDesignApi([{ projectId, snapshot: snapshot(content, 7) }]);
    const error = await api.save(projectId, harnessId, 2, content).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(HarnessDesignApiError);
    expect(error).toMatchObject({ code: "design_revision_conflict", currentRevision: 7 });
  });

  it("opens a structurally valid document when only persisted E4 route geometry is invalid", async () => {
    let routed = createEmptyHarnessDesign();
    routed = applyEditorCommand(routed, {
      type: "add-connector", connector: createConnector("x1", "X1", 1, { x: 0, y: 0 }),
    });
    routed = applyEditorCommand(routed, {
      type: "add-connector", connector: createConnector("x2", "X2", 1, { x: 800, y: 0 }),
    });
    routed = applyEditorCommand(routed, {
      type: "add-wire",
      wire: createWire("w1", { connectorId: "x1", contactId: "x1:contact:1" }, { connectorId: "x2", contactId: "x2:contact:1" }),
    });
    const invalid = {
      ...routed,
      wires: routed.wires.map((wire) => ({
        ...wire,
        e4RouteMode: "manual",
        e4Route: [wire.e4Route[0]!, wire.e4Route[0]!],
      })),
    };

    const loaded = await createInMemoryHarnessDesignApi([{ projectId, snapshot: snapshot(invalid, 9) }]).get(projectId, harnessId);

    expect(loaded.content.connectors).toHaveLength(2);
    expect(loaded.content.wires).toHaveLength(1);
    expect(loaded.content.wires[0]?.e4RouteMode).toBe("auto");
    expect(loaded.recoveryWarning).toMatch(/безопасном режиме/);
  });

  it("preserves valid manual routes while repairing only a malformed wire", () => {
    const x1 = createConnector("x1", "X1", 2, { x: 0, y: 0 });
    const x2 = createConnector("x2", "X2", 2, { x: 800, y: 0 });
    const routed = { ...createEmptyHarnessDesign(), connectors: [x1, x2], wires: [
      createWire("w1", { connectorId: "x1", contactId: "x1:contact:1" }, { connectorId: "x2", contactId: "x2:contact:1" }),
      createWire("w2", { connectorId: "x1", contactId: "x1:contact:2" }, { connectorId: "x2", contactId: "x2:contact:2" }),
    ] };
    const baselineSource = {
      ...routed,
      wires: routed.wires.map(({ e4Route: _route, e4RouteMode: _mode, ...wire }) => wire),
    };
    const baseline = parseHarnessDesignDocument(baselineSource);
    const validManualRoute = baseline.wires[0]!.e4Route;
    const duplicatedPoint = baseline.wires[1]!.e4Route[0]!;
    const first = {
      ...createWire("w1", { connectorId: "x1", contactId: "x1:contact:1" }, { connectorId: "x2", contactId: "x2:contact:1" }),
      e4RouteMode: "manual" as const,
      e4Route: validManualRoute,
    };
    const second = createWire("w2", { connectorId: "x1", contactId: "x1:contact:2" }, { connectorId: "x2", contactId: "x2:contact:2" });
    const invalid = {
      ...routed,
      wires: [
        first,
        {
          ...second,
          e4RouteMode: "manual",
          e4Route: [duplicatedPoint, duplicatedPoint],
        },
      ],
    };

    const recovered = parseRecoverableHarnessDesignContent(invalid);

    expect(recovered.content.wires[0]).toMatchObject({
      id: "w1",
      e4RouteMode: "manual",
      e4Route: validManualRoute,
    });
    expect(recovered.content.wires[1]?.e4RouteMode).toBe("auto");
    expect(recovered.content.wires[1]?.e4Route).not.toEqual(invalid.wires[1]?.e4Route);
    expect(recovered.warning).toMatch(/восстановлены автоматически/);
  });

  it("does not disguise structural document corruption as a routing recovery", () => {
    expect(() => parseRecoverableHarnessDesignContent({ ...content, connectors: "lost" }))
      .toThrow();
  });
});
