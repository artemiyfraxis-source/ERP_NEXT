// @vitest-environment node
import { getE4ConnectorLayout } from "./CanvasViewport";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { newTemplateContentV2 } from "../component-library/template-commands-v2";
import { upgradeTemplateContentV2ToV3 } from "../component-library/template-upgrade-v3";
import { createConnector, createWire, applyEditorCommand } from "./commands";
import {
  acceptProjectComponentSnapshotLookup,
  buildComponentTemplateViewInstances,
  buildProjectComponentSnapshotLookup,
  cableMaterialUpdateFromCatalogItem,
  ComponentGraphErrorAlert,
  designToScene,
  editorWireUpdateCommand,
  HarnessEditorErrorBoundary,
  loadComponentTemplateForPlacement,
  normalizeEditorSelection,
  selectedEditorDeletionCommands,
  snapRoutePoint,
  wireMaterialUpdateFromCatalogItem,
  wireStripProfileUpdateFromCatalogItem,
} from "./HarnessDesignEditor";
import type {
  ProjectComponentPlacementGraph,
  ProjectComponentSnapshotResource,
} from "./component-placement-api";
import { createConnectorInstanceFromComponentTemplateV3 } from "./component-template-placement";
import type { EditorCatalogItem } from "./editor-types";
import { connectorE4TableGeometry, createEmptyHarnessDesign } from "./model";

// ERP: the component library is read-only here, so placement pins exactly the catalog version.
it("places exactly the published catalog version of a template", async () => {
  const published = { templateId: "template", version: 2 };
  const api = { getVersion: vi.fn().mockResolvedValue(published) };
  await expect(loadComponentTemplateForPlacement(api as never, "template", 2)).resolves.toBe(published);
  expect(api.getVersion).toHaveBeenCalledWith("template", 2);
});

function wireMaterialCatalogItem(): EditorCatalogItem {
  return {
    id: "technology-database:wire:UL1061-24AWG",
    title: "UL1061-24AWG",
    subtitle: "UL1061 24AWG",
    category: "Провода",
    accent: "#356c88",
    placement: "reference-only",
    sourceId: "technology-database",
    snapshotId: "00000000-0000-4000-8000-000000000099",
    snapshotSha256: "a".repeat(64),
    recordId: "b".repeat(64),
    entityType: "wire",
    sourceKey: "UL1061-24AWG",
    referenceDisplayName: "UL1061 24AWG",
  };
}

function wireStripCatalogItem(): EditorCatalogItem {
  return {
    id: "technology-database:coax-termination:BNC-RG58",
    title: "BNC / RG58",
    subtitle: "D1 0.9 / L1 6",
    category: "Коаксиальная разделка",
    accent: "#356c88",
    placement: "reference-only",
    sourceId: "technology-database",
    snapshotId: "00000000-0000-4000-8000-000000000099",
    snapshotSha256: "a".repeat(64),
    recordId: "b".repeat(64),
    entityType: "coax-termination",
    sourceKey: "BNC|RG58|6|9|13",
    coaxTerminationCandidate: {
      state: "ready",
      diagnostics: [],
      layers: [
        { index: 1, diameterMm: 0.9, stripLengthMm: 6 },
        { index: 2, diameterMm: 3.1, stripLengthMm: 9 },
      ],
      binding: {
        sourceId: "technology-database",
        snapshotId: "00000000-0000-4000-8000-000000000099",
        snapshotSha256: "a".repeat(64),
        recordId: "b".repeat(64),
        entityType: "coax-termination",
        sourceKey: "BNC|RG58|6|9|13",
        layers: [
          { index: 1, diameterMm: 0.9, stripLengthMm: 6 },
          { index: 2, diameterMm: 3.1, stripLengthMm: 9 },
        ],
      },
    },
  };
}

describe("harness design scene adapter", () => {
  it("shows an explicit retry action when component graph loading fails", () => {
    const markup = renderToStaticMarkup(createElement(ComponentGraphErrorAlert, {
      message: "Не удалось загрузить закреплённые виды компонентов.",
      onRetry: vi.fn(),
    }));

    expect(markup).toContain('role="alert"');
    expect(markup).toContain("Не удалось загрузить закреплённые виды компонентов.");
    expect(markup).toContain("Повторить загрузку видов");
  });

  it("maps a placement to the exact project-owned component snapshot", () => {
    const graph = projectComponentGraph();
    const lookup = buildProjectComponentSnapshotLookup(graph);

    expect(lookup.get(graph.placements[0]!.placementId)).toBe(graph.snapshots[0]);
    expect(lookup.get(graph.placements[0]!.placementId)).toMatchObject({
      sourceVersion: 7,
      sourceVersionSha256: "a".repeat(64),
    });
  });

  it("does not replace the current component snapshot lookup with a stale harness response", () => {
    const currentSnapshot = projectComponentGraph().snapshots[0]!;
    const current = new Map([["current-placement", currentSnapshot]]);

    expect(acceptProjectComponentSnapshotLookup(current, projectComponentGraph(), 4, 5)).toBe(current);
    expect(acceptProjectComponentSnapshotLookup(current, projectComponentGraph(), 5, 5))
      .not.toBe(current);
  });

  it("rejects a placement graph whose pinned snapshot is missing", () => {
    const graph = projectComponentGraph();
    expect(() => buildProjectComponentSnapshotLookup({ ...graph, snapshots: [] }))
      .toThrow(/отсутствует закреплённый снимок/);
  });

  it.each(["technology-database", "БД.СОЕД"])("renders only a component matching the project snapshot with source %s", sourceId => {
    const graph = projectComponentGraph();
    const snapshot = graph.snapshots[0]!;
    if (snapshot.content.schemaVersion !== 3) throw new Error("test fixture must be v3");
    const variant = {
      id: crypto.randomUUID(),
      sourceId,
      entityType: "connector",
      articleKey: "B2B-XH-A",
      parameterValues: [],
      contactGroups: null,
    };
    snapshot.content.logicalContacts.push({
      id: crypto.randomUUID(),
      number: "1",
      name: "Contact",
      circuitText: null,
      contactTypeGroupId: null,
    });
    snapshot.content.articleVariants.push(variant);
    const connector = createConnectorInstanceFromComponentTemplateV3({
      templateId: snapshot.sourceTemplateId,
      version: snapshot.sourceVersion,
      versionSha256: snapshot.sourceVersionSha256,
      code: snapshot.code,
      name: snapshot.name,
      articleBindings: snapshot.articleBindings,
      assets: snapshot.assets,
      content: snapshot.content,
    }, {
      id: graph.placements[0]!.placementId,
      designation: "XS1",
      articleVariantId: variant.id,
      e4Position: { x: 10, y: 20 },
    });
    const document = { ...createEmptyHarnessDesign(), connectors: [connector] };
    const lookup = buildProjectComponentSnapshotLookup(graph);

    expect(buildComponentTemplateViewInstances(document, lookup)).toEqual([{
      objectId: connector.id,
      snapshotId: snapshot.snapshotId,
      articleVariantId: variant.id,
      content: snapshot.content,
    }]);
    expect(buildComponentTemplateViewInstances(document, new Map([[connector.id, {
      ...snapshot,
      sourceVersion: snapshot.sourceVersion + 1,
    }]]))).toEqual([]);

    if (connector.libraryBinding?.mode !== "template") throw new Error("test fixture must be template-bound");
    const tamperedConnector = {
      ...connector,
      libraryBinding: {
        ...connector.libraryBinding,
        snapshot: {
          ...connector.libraryBinding.snapshot,
          contacts: connector.libraryBinding.snapshot.contacts.map((contact, index) => index === 0
            ? { ...contact, name: "Tampered contact" }
            : contact),
        },
      },
    };
    expect(buildComponentTemplateViewInstances(
      { ...document, connectors: [tamperedConnector] },
      lookup,
    )).toEqual([]);
  });

  it("shows a recoverable error instead of an empty editor surface", () => {
    const boundary = new HarnessEditorErrorBoundary({
      children: createElement("div", null, "editor"),
      onError: vi.fn(),
      onRecover: vi.fn(),
    });
    boundary.state = HarnessEditorErrorBoundary.getDerivedStateFromError(new Error("render failed"));
    const markup = renderToStaticMarkup(boundary.render());
    expect(markup).toContain("Редактор не смог отобразить последнее изменение");
    expect(markup).toContain("render failed");
    expect(markup).toContain("Вернуться к редактору");
  });

  it("removes deleted and duplicate ids from multi-selection and promotes a surviving primary object", () => {
    expect(normalizeEditorSelection(
      ["wire-1", "deleted", "wire-1", "wire-2"],
      "deleted",
      new Set(["wire-1", "wire-2"]),
    )).toEqual({ objectIds: ["wire-1", "wire-2"], primaryObjectId: "wire-2" });
    expect(normalizeEditorSelection(["wire-1"], "connector-1", new Set(["wire-1", "connector-1"]))).toEqual({
      objectIds: ["wire-1", "connector-1"], primaryObjectId: "connector-1",
    });
    expect(normalizeEditorSelection(["deleted"], "deleted", new Set())).toEqual({
      objectIds: [], primaryObjectId: null,
    });
  });

  it("deletes selected wires before selected connectors and ignores stale selection", () => {
    const x1 = createConnector("x1", "X1", 1, { x: 0, y: 0 });
    const x2 = createConnector("x2", "X2", 1, { x: 800, y: 0 });
    let document = applyEditorCommand(createEmptyHarnessDesign(), { type: "add-connector", connector: x1 });
    document = applyEditorCommand(document, { type: "add-connector", connector: x2 });
    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire("w1", { connectorId: "x1", contactId: "x1:contact:1" }, { connectorId: "x2", contactId: "x2:contact:1" }),
    });
    expect(selectedEditorDeletionCommands(document, ["x1", "w1", "removed"])).toEqual([
      { type: "remove-wire", wireId: "w1" },
      { type: "remove-connector", connectorId: "x1" },
    ]);
  });

  it("removes all selected authored graphics with one persisted drawing-document command", () => {
    const base=createEmptyHarnessDesign();
    const document={...base,drawingDocuments:{tables:[],leaders:[],bomOrder:[],graphics:[
      {id:"line",view:"e4" as const,kind:"line" as const,points:[{x:0,y:0},{x:10,y:0}]},
      {id:"label",view:"e4" as const,kind:"text" as const,points:[{x:20,y:0}],text:"X"},
    ]}};
    expect(selectedEditorDeletionCommands(document,["line","label"])).toEqual([{type:"set-drawing-documents",documents:{...document.drawingDocuments,graphics:[]}}]);
  });

  it("renders the same domain instances in E4 and drawing with separate positions", () => {
    const x1 = createConnector("x1", "X1", 2, { x: 10, y: 20 }, { x: 100, y: 120 });
    const x2 = createConnector("x2", "X2", 2, { x: 800, y: 20 }, { x: 500, y: 120 });
    let document = applyEditorCommand(createEmptyHarnessDesign(), { type: "add-connector", connector: x1 });
    document = applyEditorCommand(document, { type: "add-connector", connector: x2 });
    document = applyEditorCommand(document, {
      type: "add-wire",
      wire: createWire("w1", { connectorId: "x1", contactId: "x1:contact:1" }, { connectorId: "x2", contactId: "x2:contact:2" }, 350),
    });

    const e4 = designToScene(document, "e4");
    const drawing = designToScene(document, "drawing");

    expect(e4.find((item) => item.id === "x1")).toMatchObject({ x: 10, y: 20 });
    expect(drawing.find((item) => item.id === "x1")).toMatchObject({ x: 100, y: 120 });
    expect(e4.find((item) => item.id === "w1")?.metadata?.lengthMm).toBe("350");
    expect(drawing.find((item) => item.id === "dimension:w1")?.label).toBe("350 мм");
    expect(drawing.find((item) => item.id === "w1")?.points?.[0]?.x).toBe(218);
    expect(drawing.find((item) => item.id === "dimension:w1")?.points?.[0]?.x).toBe(218);
  });

  it("projects wire cut corrections, rounding and material status into the inspector scene", () => {
    const x1 = createConnector("cut-x1", "XS1", 1, { x: 10, y: 20 });
    const x2 = createConnector("cut-x2", "XS2", 1, { x: 500, y: 20 });
    const wire = createWire(
      "cut-wire",
      { connectorId: x1.id, contactId: x1.contacts[0]!.id },
      { connectorId: x2.id, contactId: x2.contacts[0]!.id },
      1_000.1,
      "",
      "#334155",
      10,
      20,
      1,
    );
    const document = { ...createEmptyHarnessDesign(), connectors: [x1, x2], wires: [wire] };

    expect(designToScene(document, "e4").find((item) => item.id === wire.id)?.metadata).toMatchObject({
      lengthKnown: "true",
      lengthMm: "1000.1",
      endCorrectionFromMm: "10",
      endCorrectionToMm: "20",
      cutRoundingStepMm: "1",
      cutLengthMm: "1031",
      materialStatus: "included",
    });
  });

  it("projects end strip profiles only into the drawing scene", () => {
    const x1 = createConnector("strip-x1", "X1", 1, { x: 10, y: 20 }, { x: 100, y: 120 });
    const x2 = createConnector("strip-x2", "X2", 1, { x: 500, y: 20 }, { x: 500, y: 120 });
    const profile = {
      sourceId: "technology-coax-terminations",
      snapshotId: "38d9aa91-b8d4-45d8-8e39-da14ee4effad",
      snapshotSha256: "a".repeat(64), recordId: "b".repeat(64),
      entityType: "coax-termination" as const, sourceKey: "BNC|RG58", displayName: "BNC / RG58",
      layers: [{ index: 1, diameterMm: 1, stripLengthMm: 4 }, { index: 2, diameterMm: 4, stripLengthMm: 8 }],
    };
    const wire = {
      ...createWire("strip-wire", { connectorId: x1.id, contactId: x1.contacts[0]!.id },
        { connectorId: x2.id, contactId: x2.contacts[0]!.id }),
      stripProfiles: { from: profile },
    };
    const document = { ...createEmptyHarnessDesign(), connectors: [x1, x2], wires: [wire] };

    expect(designToScene(document, "drawing").find(item => item.id === wire.id)?.stripProfiles)
      .toEqual({ from: profile });
    expect(designToScene(document, "e4").find(item => item.id === wire.id)?.stripProfiles).toBeUndefined();
  });

  it("explains when a saved strip profile cannot fit its drawing endpoint", () => {
    const x1 = createConnector("short-strip-x1", "X1", 1, { x: 10, y: 20 }, { x: 100, y: 120 });
    const x2 = createConnector("short-strip-x2", "X2", 1, { x: 500, y: 20 }, { x: 222, y: 120 });
    const profile = {
      sourceId: "technology-coax-terminations", snapshotId: "38d9aa91-b8d4-45d8-8e39-da14ee4effad",
      snapshotSha256: "a".repeat(64), recordId: "b".repeat(64), entityType: "coax-termination" as const,
      sourceKey: "BNC|RG58", displayName: "BNC / RG58",
      layers: [{ index: 1, diameterMm: 1, stripLengthMm: 4 }],
    };
    const wire = {
      ...createWire("short-strip-wire", { connectorId: x1.id, contactId: x1.contacts[0]!.id },
        { connectorId: x2.id, contactId: x2.contacts[0]!.id }),
      stripProfiles: { from: profile, to: profile },
    };
    const document = { ...createEmptyHarnessDesign(), connectors: [x1, x2], wires: [wire] };

    const drawingWire = designToScene(document, "drawing").find(item => item.id === wire.id);
    expect(drawingWire?.stripProfiles).toEqual({ from: profile, to: profile });
    expect(drawingWire?.metadata?.stripProfileDisplayWarning).toBe("from,to");
    expect(designToScene(document, "e4").find(item => item.id === wire.id)?.metadata?.stripProfileDisplayWarning)
      .toBeUndefined();
  });

  it("projects the pinned wire material into the inspector scene", () => {
    const x1 = createConnector("material-x1", "XS1", 1, { x: 10, y: 20 });
    const x2 = createConnector("material-x2", "XS2", 1, { x: 500, y: 20 });
    const wire = {
      ...createWire(
        "material-wire",
        { connectorId: x1.id, contactId: x1.contacts[0]!.id },
        { connectorId: x2.id, contactId: x2.contacts[0]!.id },
      ),
      materialBinding: {
        sourceId: "technology-database",
        snapshotId: "00000000-0000-4000-8000-000000000099",
        snapshotSha256: "a".repeat(64),
        recordId: "b".repeat(64),
        entityType: "wire" as const,
        sourceKey: "UL1061-24AWG",
        displayName: "UL1061 24AWG",
      },
    };
    const document = { ...createEmptyHarnessDesign(), connectors: [x1, x2], wires: [wire] };

    expect(designToScene(document, "e4").find((item) => item.id === wire.id)?.metadata).toMatchObject({
      materialSourceKey: "UL1061-24AWG",
      materialDisplayName: "UL1061 24AWG",
      materialEntityType: "wire",
    });
  });

  it("builds a wire material update from an exact catalog snapshot", () => {
    const item = wireMaterialCatalogItem();

    expect(wireMaterialUpdateFromCatalogItem(item, "wire-1")).toEqual({
      ok: true,
      command: {
        type: "update-wire",
        wireId: "wire-1",
        materialBinding: {
          sourceId: "technology-database",
          snapshotId: "00000000-0000-4000-8000-000000000099",
          snapshotSha256: "a".repeat(64),
          recordId: "b".repeat(64),
          entityType: "wire",
          sourceKey: "UL1061-24AWG",
          displayName: "UL1061 24AWG",
        },
      },
    });
  });

  it("assigns only a published cable material to a cable container", () => {
    const item: EditorCatalogItem = {
      ...wireMaterialCatalogItem(),
      id: "technology-database:cable:CABLE-2X",
      entityType: "cable",
      sourceKey: "CABLE-2X",
      referenceDisplayName: "Кабель 2×0,35",
    };
    const result = cableMaterialUpdateFromCatalogItem(item, "CABLE-1");
    expect(result).toMatchObject({ ok: true, command: {
      type: "update-cable", cableId: "CABLE-1",
      materialBinding: { entityType: "cable", sourceKey: "CABLE-2X", displayName: "Кабель 2×0,35" },
    } });
    expect(cableMaterialUpdateFromCatalogItem({ ...item, entityType: "wire" }, "CABLE-1"))
      .toEqual({ ok: false, error: "Для общего материала выберите кабель." });
    expect(cableMaterialUpdateFromCatalogItem(item, null))
      .toEqual({ ok: false, error: "Сначала создайте кабель из выбранных проводов." });
  });

  it("rejects a material assignment without one selected wire", () => {
    expect(wireMaterialUpdateFromCatalogItem(wireMaterialCatalogItem(), null)).toEqual({
      ok: false,
      error: "Сначала выберите один провод, затем дважды щёлкните материал в справочнике.",
    });
  });

  it("rejects a catalog entity that is not wire material", () => {
    expect(wireMaterialUpdateFromCatalogItem({
      ...wireMaterialCatalogItem(), entityType: "terminal",
    }, "wire-1")).toEqual({
      ok: false,
      error: "Выбранная справочная позиция не является проводом или кабелем.",
    });
  });

  it("rejects an incomplete catalog snapshot identity", () => {
    expect(wireMaterialUpdateFromCatalogItem({
      ...wireMaterialCatalogItem(), snapshotSha256: undefined,
    }, "wire-1")).toEqual({
      ok: false,
      error: "Справочная позиция не содержит данных опубликованной версии.",
    });
  });

  it("builds an exact strip profile command for the active wire end", () => {
    expect(wireStripProfileUpdateFromCatalogItem(wireStripCatalogItem(), "wire-1", "to")).toEqual({
      ok: true,
      command: {
        type: "set-wire-strip-profile",
        wireId: "wire-1",
        end: "to",
        profile: {
          ...wireStripCatalogItem().coaxTerminationCandidate!.binding,
          displayName: "BNC / RG58",
        },
      },
    });
  });

  it("rejects strip assignment without one editable drawing wire", () => {
    expect(wireStripProfileUpdateFromCatalogItem(wireStripCatalogItem(), null, "from")).toMatchObject({
      ok: false, error: expect.stringContaining("выберите один провод"),
    });
    expect(wireStripProfileUpdateFromCatalogItem(wireStripCatalogItem(), "wire-1", "from", true)).toEqual({
      ok: false, error: "Слой выбранного провода заблокирован.",
    });
  });

  it("reports the first catalog diagnostic for an incomplete strip profile", () => {
    const item = wireStripCatalogItem();
    expect(wireStripProfileUpdateFromCatalogItem({
      ...item,
      coaxTerminationCandidate: {
        state: "incomplete",
        layers: [{ index: 1, diameterMm: 0.9, stripLengthMm: null }],
        diagnostics: [{ code: "layer-strip-length-missing", layerIndex: 1, message: "Для слоя L1 не указана длина разделки." }],
        binding: null,
      },
    }, "wire-1", "from")).toEqual({
      ok: false, error: "Для слоя L1 не указана длина разделки.",
    });
  });

  it("projects an unknown wire length as incomplete and excluded from materials", () => {
    const x1 = createConnector("unknown-x1", "XS1", 1, { x: 10, y: 20 });
    const x2 = createConnector("unknown-x2", "XS2", 1, { x: 500, y: 20 });
    const wire = createWire(
      "unknown-wire",
      { connectorId: x1.id, contactId: x1.contacts[0]!.id },
      { connectorId: x2.id, contactId: x2.contacts[0]!.id },
      null,
    );
    const document = { ...createEmptyHarnessDesign(), connectors: [x1, x2], wires: [wire] };
    const scene = designToScene(document, "drawing");

    expect(scene.find((item) => item.id === wire.id)?.metadata).toMatchObject({
      lengthKnown: "false",
      lengthMm: "",
      cutLengthMm: "",
      materialStatus: "excluded",
    });
    expect(scene.filter(item=>item.kind==="dimension")).toEqual([]);
  });

  it("translates known, unknown and corrected inspector values without losing null semantics", () => {
    const original = {
      id: "wire-command",
      layerId: "wires",
      kind: "wire" as const,
      label: "CAN-H",
      x: 0,
      y: 0,
      width: 0,
      height: 0,
      color: "#334155",
      metadata: {
        lengthKnown: "true", lengthMm: "100", endCorrectionFromMm: "0",
        endCorrectionToMm: "0", cutRoundingStepMm: "1",
      },
    };

    expect(editorWireUpdateCommand({
      ...original,
      metadata: { ...original.metadata, lengthKnown: "false", lengthMm: "" },
    }, original)).toMatchObject({ lengthMm: null });
    expect(editorWireUpdateCommand({
      ...original,
      metadata: {
        ...original.metadata,
        lengthMm: "350.25",
        endCorrectionFromMm: "-2",
        endCorrectionToMm: "3",
        cutRoundingStepMm: "0.5",
      },
    }, original)).toMatchObject({
      lengthMm: 350.25,
      endCorrectionFromMm: -2,
      endCorrectionToMm: 3,
      cutRoundingStepMm: 0.5,
    });
  });

  it("snaps an added route point to a 15 degree direction", () => {
    const snapped = snapRoutePoint({ x: 0, y: 0 }, { x: 100, y: 23 }, true);
    const angleDegrees = Math.atan2(snapped.y, snapped.x) * 180 / Math.PI;
    expect(angleDegrees).toBeCloseTo(15, 8);
    expect(snapRoutePoint({ x: 0, y: 0 }, { x: 100, y: 23 }, false)).toEqual({ x: 100, y: 23 });
    const ctrlSnapped = snapRoutePoint({ x: 0, y: 0 }, { x: 100, y: 50 }, true, Math.PI / 6);
    expect(Math.atan2(ctrlSnapped.y, ctrlSnapped.x) / (Math.PI / 6)).toBeCloseTo(1, 6);
  });

  it("passes the persisted E4 table fields and mirrored geometry to the canvas scene", () => {
    let document = applyEditorCommand(createEmptyHarnessDesign(), {
      type: "add-connector",
      connector: createConnector("x1", "XS1", 2, { x: 10, y: 20 }, undefined, "PHR-7"),
    });
    document = applyEditorCommand(document, {
      type: "update-contact", connectorId: "x1", contactId: "x1:contact:1",
      contactType: "сигнальный", circuit: "CAN-H", terminalArticle: "SHP-002P-0.5T",
      wire: "UL1061 28AWG", color: "красный", secondaryColor: "черный", connectionStatus: "not-connected",
    });
    document = applyEditorCommand(document, { type: "flip-connector-orientation", connectorId: "x1" });
    const connector = document.connectors[0]!;
    const geometry = connectorE4TableGeometry(connector);
    const scene = designToScene(document, "e4");
    const object = scene.find((item) => item.id === "x1")!;
    const canvasLayout = getE4ConnectorLayout(object)!;
    expect(canvasLayout.width).toBe(geometry.width);
    expect(canvasLayout.columns.map(column => column.width)).toEqual(geometry.columns.map(column => column.width));
    expect(canvasLayout.contactPoints).toEqual(connector.contacts.map(contact => ({x: connector.positions.e4.x + geometry.contactPoints[contact.id]!.x,y: connector.positions.e4.y + geometry.contactPoints[contact.id]!.y})));
    const rows = JSON.parse(object.metadata!.rows!) as Array<Record<string, unknown>>;

    expect(object).toMatchObject({ x: 10, y: 20, width: geometry.width, height: geometry.height });
    expect(object.metadata).toMatchObject({
      view: "e4", orientation: "left", designation: "XS1", libraryCode: "FREE", partNumber: "PHR-7",
    });
    expect(JSON.parse(object.metadata!.columns!)).toEqual([
      "number", "contactType", "circuit", "terminal", "wire", "wireSection", "color",
    ]);
    expect(rows[0]).toMatchObject({
      number: 1, contactType: "сигнальный", circuit: "CAN-H", terminal: "SHP-002P-0.5T",
      wire: "UL1061 28AWG", color: "красный", secondaryColor: "черный", status: "not-connected",
    });
  });

  it("marks every conflicting connector in the E4 scene and clears the marks after correction", () => {
    let document = createEmptyHarnessDesign();
    document = applyEditorCommand(document, {
      type: "add-connector", connector: createConnector("x1", "X1", 1, { x: 0, y: 0 }),
    });
    document = applyEditorCommand(document, {
      type: "add-connector", connector: createConnector("x2", "X2", 1, { x: 600, y: 0 }),
    });
    document = applyEditorCommand(document, {
      type: "update-connector", connectorId: "x2", designation: "x1",
    });
    const duplicateIds = new Set(["x1", "x2"]);
    expect(designToScene(document, "e4", duplicateIds)
      .filter((object) => object.kind === "connector")
      .map((object) => object.metadata?.diagnostic)).toEqual(["error", "error"]);

    document = applyEditorCommand(document, {
      type: "update-connector", connectorId: "x2", designation: "X2",
    });
    expect(designToScene(document, "e4")
      .filter((object) => object.kind === "connector")
      .map((object) => object.metadata?.diagnostic)).toEqual([undefined, undefined]);
  });

  it("passes nullable local library contact anchors without shifting contact indexes", () => {
    const graph = projectComponentGraph();
    const snapshot = graph.snapshots[0]!;
    if (snapshot.content.schemaVersion !== 3) throw new Error("test fixture must be v3");
    const logicalId = crypto.randomUUID();
    const connector = {
      ...createConnector("library-1", "XS1", 2, { x: 10, y: 20 }),
      libraryBinding: {
        mode: "template" as const,
        templateId: snapshot.sourceTemplateId,
        templateVersion: snapshot.sourceVersion,
        versionSha256: snapshot.sourceVersionSha256,
        articleVariantId: crypto.randomUUID(),
        article: { sourceId: "source", entityType: "connector", articleKey: "part" },
        snapshot: {
          templateId: snapshot.sourceTemplateId,
          templateVersion: snapshot.sourceVersion,
          versionSha256: snapshot.sourceVersionSha256,
          code: "LIB",
          name: "Library",
          articleVariantId: crypto.randomUUID(),
          article: { sourceId: "source", entityType: "connector", articleKey: "part" },
          articleBindings: [],
          assets: [],
          contacts: [{
            logicalContactId: logicalId,
            prototypeLogicalContactId: logicalId,
            sourceNumber: "2",
            name: "Second",
            circuitText: null,
            contactTypeGroupId: null,
            contactType: "",
            allowedTerminalArticleKeys: [],
            representations: [{
              viewId: crypto.randomUUID(), viewName: "E4", viewKind: "e4" as const,
              pointId: crypto.randomUUID(), x: 70, y: 35, direction: "left" as const,
            }, {
              viewId: crypto.randomUUID(), viewName: "Drawing", viewKind: "drawing" as const,
              pointId: crypto.randomUUID(), x: 80, y: 90, direction: "down" as const,
            }],
          }],
        },
      },
      contacts: [
        { ...createConnector("unused", "X", 1, { x: 0, y: 0 }).contacts[0]!, logicalContactId: crypto.randomUUID() },
        { ...createConnector("unused2", "X", 1, { x: 0, y: 0 }).contacts[0]!, id: "library-1:contact:2", logicalContactId: logicalId },
      ],
    };
    const other = createConnector("other", "XS2", 1, { x: 400, y: 20 });
    const wire = createWire(
      "wire",
      { connectorId: connector.id, contactId: connector.contacts[1]!.id },
      { connectorId: other.id, contactId: other.contacts[0]!.id },
    );
    const document = { ...createEmptyHarnessDesign(), connectors: [connector, other], wires: [wire] };
    const object = designToScene(document, "e4")[0]!;

    expect(JSON.parse(object.metadata!.materializedContactPoints!)).toEqual([
      null,
      { x: 70, y: 35, direction: "left", status: "available" },
    ]);
    expect(designToScene(document, "drawing").find((candidate) => candidate.id === wire.id)?.points?.[0])
      .toEqual({ x: 90, y: 110 });
  });

  it("uses ordinary contact geometry when the project snapshot graph is unavailable", () => {
    const graph = projectComponentGraph();
    const snapshot = graph.snapshots[0]!;
    const logicalId = crypto.randomUUID();
    const connector = {
      ...createConnector("library-fallback", "XS1", 1, { x: 10, y: 20 }),
      libraryBinding: {
        mode: "template" as const,
        templateId: snapshot.sourceTemplateId,
        templateVersion: snapshot.sourceVersion,
        versionSha256: snapshot.sourceVersionSha256,
        articleVariantId: crypto.randomUUID(),
        article: { sourceId: "source", entityType: "connector", articleKey: "part" },
        snapshot: {
          templateId: snapshot.sourceTemplateId,
          templateVersion: snapshot.sourceVersion,
          versionSha256: snapshot.sourceVersionSha256,
          code: "LIB",
          name: "Library",
          articleVariantId: crypto.randomUUID(),
          article: { sourceId: "source", entityType: "connector", articleKey: "part" },
          articleBindings: [],
          assets: [],
          contacts: [{
            logicalContactId: logicalId,
            prototypeLogicalContactId: logicalId,
            sourceNumber: "1",
            name: "Contact",
            circuitText: null,
            contactTypeGroupId: null,
            contactType: "",
            allowedTerminalArticleKeys: [],
            representations: [{
              viewId: crypto.randomUUID(), viewName: "Drawing", viewKind: "drawing" as const,
              pointId: crypto.randomUUID(), x: 500, y: 600, direction: "left" as const,
            }],
          }],
        },
      },
      contacts: [{
        ...createConnector("unused", "X", 1, { x: 0, y: 0 }).contacts[0]!,
        id: "library-fallback:contact:1",
        logicalContactId: logicalId,
      }],
    };
    const other = createConnector("other-fallback", "XS2", 1, { x: 400, y: 20 });
    const wire = createWire(
      "fallback-wire",
      { connectorId: connector.id, contactId: connector.contacts[0]!.id },
      { connectorId: other.id, contactId: other.contacts[0]!.id },
    );
    const document = { ...createEmptyHarnessDesign(), connectors: [connector, other], wires: [wire] };
    const scene = designToScene(document, "drawing", new Set(), new Set());

    expect(scene[0]!.metadata?.materializedContactPoints).toBeUndefined();
    expect(scene.find((candidate) => candidate.id === wire.id)?.points?.[0]).toEqual({ x: 128, y: 48 });
  });
});

function projectComponentGraph(): ProjectComponentPlacementGraph {
  const placementId = "33333333-3333-4333-8333-333333333333";
  const snapshotId = "22222222-2222-4222-8222-222222222222";
  const content = upgradeTemplateContentV2ToV3(newTemplateContentV2()).content;
  const snapshot: ProjectComponentSnapshotResource = {
    snapshotId,
    projectId: "project",
    sourceTemplateId: "44444444-4444-4444-8444-444444444444",
    sourceVersion: 7,
    sourceVersionSha256: "a".repeat(64),
    code: "XH",
    name: "JST XH",
    articleBindings: [{ sourceId: "technology-database", entityType: "connector", articleKey: "B2B-XH-A" }],
    assets: [],
    schemaVersion: 3,
    content,
    createdUtc: "2026-09-14T00:00:00Z",
    updatedUtc: "2026-09-14T00:00:00Z",
  };
  return {
    placements: [{
      placementId,
      harnessId: "harness",
      snapshotId,
      sourceId: "technology-database",
      entityType: "connector",
      articleKey: "B2B-XH-A",
      instance: { id: placementId },
      createdUtc: "2026-09-14T00:00:00Z",
      updatedUtc: "2026-09-14T00:00:00Z",
    }],
    snapshots: [snapshot],
  };
}
