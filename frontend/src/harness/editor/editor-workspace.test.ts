// @vitest-environment node
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  e4ContactMarker,
  e4WireSegments,
  findE4CommonParallelSpan,
  getEditorSceneBounds,
  getDrawingWireStripProfileGeometries,
  getE4BridgeGeometry,
  getE4DifferentialPairLayout,
  getE4ScreenLayout,
  getVisibleE4SceneOverlays,
  getE4ConnectorLayout,
  getMaterializedConnectorContactPoints,
  getE4WireCrossings,
  getE4WireRoute,
  hitTestE4DifferentialPair,
  hitTestE4Screen,
  hitTestE4ScreenConnection,
  hitTestE4WireSegment,
  hitTestE4WireLabel,
  hitTestConnectorContact,
  hitTestEditorScene,
  hitTestWireSegment,
  hitTestWireEnd,
  hitTestWireRoutePoint,
  handleEditorViewportWheel,
  containInlineEditorPointerEvent,
  drawE4DifferentialPairs,
  drawCableSheaths,
  drawEditorSceneObject,
  drawSelectedConnectorContacts,
  getVisibleCableSheathScene,
  hitTestCableSheath,
  inlineObjectDragDestination,
  inlineObjectDragMoved,
  isInlineEditorControlTarget,
  isInlineEditorReadonlyTarget,
  objectsInPaintOrder,
  parseE4SceneOverlays,
  projectPointToOrthogonalSegment,
} from "./CanvasViewport";
import {
  clampEditorZoom,
  fitEditorCameraToBounds,
  maximumEditorZoom,
  minimumEditorZoom,
  panEditorCamera,
  screenToWorld,
  worldToScreen,
  zoomEditorCameraAt,
  zoomEditorCameraFromWheel,
} from "./editor-camera";
import { moveLayer, toggleLayerLock, toggleLayerVisibility, updateEditorObject } from "./editor-state";
import type { EditorLayer, EditorPoint, EditorSceneObject } from "./editor-types";
import type { CableInstance } from "./model";
import { HarnessEditorWorkspace, hiddenIdsForIsolatedObjects, layersWithIsolatedLayer, reconcileWorkspaceGroupSelection, reconcileWorkspaceSelection } from "./HarnessEditorWorkspace";

const layers: readonly EditorLayer[] = [
  { id: "top", label: "Верхний", visible: true, locked: false },
  { id: "bottom", label: "Нижний", visible: true, locked: false },
];

const objects: readonly EditorSceneObject[] = [
  { id: "lower", layerId: "bottom", kind: "connector", label: "XS2", x: 10, y: 10, width: 50, height: 40, color: "#222222" },
  { id: "upper", layerId: "top", kind: "connector", label: "XS1", x: 10, y: 10, width: 50, height: 40, color: "#111111" },
  { id: "wire", layerId: "bottom", kind: "wire", label: "W1", x: 0, y: 0, width: 0, height: 0, color: "#cc0000", points: [{ x: 100, y: 100 }, { x: 180, y: 100 }] },
];

const stripProfile = {
  sourceId: "technology-coax-terminations",
  snapshotId: "38d9aa91-b8d4-45d8-8e39-da14ee4effad",
  snapshotSha256: "a".repeat(64), recordId: "b".repeat(64),
  entityType: "coax-termination" as const, sourceKey: "BNC|RG58", displayName: "BNC / RG58",
  layers: [{ index: 1, diameterMm: 1, stripLengthMm: 4 }, { index: 2, diameterMm: 4, stripLengthMm: 8 }],
};

describe("harness editor workspace", () => {
  it("keeps material objects separate from service geometry in an assembly drawing copy", () => {
    expect(hiddenIdsForIsolatedObjects(objects, ["upper"])).toEqual(["lower", "wire"]);
    expect(hiddenIdsForIsolatedObjects(objects, ["upper", "wire"])).toEqual(["lower"]);
    const routeSupport: EditorSceneObject = { id: "P1", layerId: "bottom", kind: "physical-segment", label: "П", x: 0, y: 0, width: 1, height: 1, color: "#333", points: [{ x: 0, y: 0 }, { x: 20, y: 0 }], pipe: { controls: [], handles: [], wireIds: ["wire"], fromNodeId: "N1" } };
    const unrelatedSupport: EditorSceneObject = { ...routeSupport, id: "P2", pipe: { controls: [], handles: [], wireIds: ["other"] } };
    const routeNode: EditorSceneObject = { id: "N1", layerId: "bottom", kind: "physical-node", label: "Выход", x: 0, y: 0, width: 1, height: 1, color: "#333", port: { connectorId: "upper", direction: null } };
    expect(hiddenIdsForIsolatedObjects([...objects, routeSupport, unrelatedSupport, routeNode], ["wire"])).toEqual(["lower", "upper", "P2"]);
    const covering: EditorSceneObject = { id: "C1", layerId: "bottom", kind: "physical-covering", label: "Оболочка", x: 0, y: 0, width: 1, height: 1, color: "#333", metadata: { supportSegmentIds: '["P1"]' } };
    expect(hiddenIdsForIsolatedObjects([...objects, routeSupport, unrelatedSupport, routeNode, covering], ["C1"])).toEqual(["lower", "upper", "wire", "P2"]);
    expect(layersWithIsolatedLayer(layers, "bottom").map(layer => layer.visible)).toEqual([false, true]);
    const onHiddenObjectIdsChange = vi.fn();
    const markup = renderToStaticMarkup(createElement(HarnessEditorWorkspace, {
      harnessId: "assembly-copy",
      harnessDesignation: "СБ-01",
      view: "drawing",
      objects,
      layers,
      localCopyControls: {
        hiddenObjectIds: [],
        backgroundOpacity: 0.25,
        onHiddenObjectIdsChange,
        onBackgroundOpacityChange: vi.fn(),
        onCancel: vi.fn(),
      },
    }));
    expect(markup).toContain('aria-label="Видимость фрагмента"');
    expect(markup).toContain('aria-label="Список материальных объектов"');
    expect(markup).toContain('aria-label="Соединители"');
    expect(markup).toContain('aria-label="Провода"');
    expect(markup).not.toContain("Изолировать выбранные");
    expect(markup).not.toContain("Изолировать слой Верхний");
    expect(markup).not.toContain("Изолировать XS1");
    expect(markup).toContain("Показать все");
  });

  it("draws, selects and fits a multicore cable sheath in the drawing view", () => {
    const cable: CableInstance = {
      id: "CABLE-1", memberWireIds: ["core-a", "core-b"], lengthMm: 250,
      endCorrectionFromMm: 0, endCorrectionToMm: 0, cutRoundingStepMm: 1,
    };
    const cableObjects: readonly EditorSceneObject[] = [
      { id: "core-a", layerId: "bottom", kind: "wire", label: "1",
        x: 0, y: 0, width: 0, height: 0, color: "#c00", points: [{ x: 20, y: 20 }, { x: 120, y: 20 }] },
      { id: "core-b", layerId: "bottom", kind: "wire", label: "2",
        x: 0, y: 0, width: 0, height: 0, color: "#00c", points: [{ x: 30, y: 30 }, { x: 110, y: 30 }] },
    ];
    const scene = getVisibleCableSheathScene([cable], cableObjects, layers);
    expect(scene.incompatibleCableIds).toEqual([]);
    expect(scene.geometries).toHaveLength(1);
    expect(getVisibleCableSheathScene([{ ...cable, sheathStrip: { fromMm: 100, toMm: 150 } }], cableObjects, layers))
      .toEqual({ geometries: [], incompatibleCableIds: [] });
    expect(getVisibleCableSheathScene([{ ...cable, sheathStrip: { fromMm: null, toMm: 20 } }], cableObjects, layers))
      .toEqual({ geometries: [], incompatibleCableIds: [] });
    expect(hitTestCableSheath(scene.geometries, { x: 60, y: 14 }, 1)?.cableId).toBe("CABLE-1");
    expect(hitTestCableSheath(scene.geometries, { x: 60, y: 25 }, 1)).toBeNull();
    expect(getEditorSceneBounds(cableObjects, layers, "drawing", undefined, [], undefined, [cable]))
      .toEqual({ minX: 20, minY: 14, maxX: 120, maxY: 36 });

    const strokes: string[] = [];
    const contextState = {
      strokeStyle: "", fillStyle: "", lineWidth: 1,
      save: () => undefined, restore: () => undefined, beginPath: () => undefined,
      moveTo: () => undefined, lineTo: () => undefined, closePath: () => undefined,
      setLineDash: () => undefined, fill: () => undefined,
      stroke: () => strokes.push(String(contextState.strokeStyle)),
    };
    drawCableSheaths(contextState as unknown as CanvasRenderingContext2D, scene.geometries, new Set(["core-a", "core-b"]));
    expect(strokes).toEqual(["#1179ac"]);
  });

  it("does not guess incompatible cable routes and explains the omitted sheath", () => {
    const cable: CableInstance = {
      id: "CABLE-X", memberWireIds: ["core-a", "core-b"], lengthMm: null,
      endCorrectionFromMm: 0, endCorrectionToMm: 0, cutRoundingStepMm: 1,
    };
    const cableObjects: readonly EditorSceneObject[] = [
      { id: "core-a", layerId: "bottom", kind: "wire", label: "1",
        x: 0, y: 0, width: 0, height: 0, color: "#c00", points: [{ x: 0, y: 0 }, { x: 50, y: 0 }] },
      { id: "core-b", layerId: "bottom", kind: "wire", label: "2",
        x: 0, y: 0, width: 0, height: 0, color: "#00c", points: [{ x: 60, y: 10 }, { x: 100, y: 10 }] },
    ];
    expect(getVisibleCableSheathScene([cable], cableObjects, layers)).toEqual({
      geometries: [], incompatibleCableIds: ["CABLE-X"],
    });
    const markup = renderToStaticMarkup(createElement(HarnessEditorWorkspace, {
      harnessId: "harness-cable", harnessDesignation: "ЖГ-К",
      view: "drawing", objects: cableObjects, layers, cables: [cable],
    }));
    expect(markup).toContain("Общая оболочка не показана для кабеля CABLE-X");
    expect(markup).toContain("нет однозначного общего участка");
  });

  it("draws, hits and bounds end strip polygons only in the drawing view", () => {
    const wire: EditorSceneObject = {
      id: "stripped", layerId: "bottom", kind: "wire", label: "W1",
      x: 0, y: 0, width: 0, height: 0, color: "#c00",
      points: [{ x: 0, y: 20 }, { x: 100, y: 20 }], stripProfiles: { from: stripProfile },
    };

    const geometry = getDrawingWireStripProfileGeometries(wire, "drawing");
    expect(geometry).toHaveLength(1);
    expect(geometry[0]?.primitives).toHaveLength(2);
    expect(getDrawingWireStripProfileGeometries(wire, "e4")).toEqual([]);
    const stripOnlyPoint = geometry[0]!.primitives[1]!.polygon[0];
    expect(hitTestEditorScene([wire], layers, stripOnlyPoint, 2, "drawing")).toBe("stripped");
    expect(hitTestEditorScene([wire], layers, stripOnlyPoint, 2, "e4")).toBeNull();
    expect(getEditorSceneBounds([wire], layers, "drawing")).toMatchObject({
      minX: -0.75, minY: 12.25, maxX: 100, maxY: 27.75,
    });
    expect(getEditorSceneBounds([wire], layers, "e4"))
      .toEqual(getEditorSceneBounds([{ ...wire, stripProfiles: undefined }], layers, "e4"));
    expect(getEditorSceneBounds([wire], [{ ...layers[1]!, visible: false }, layers[0]!], "drawing")).toBeNull();
  });

  it("uses the wire colour for the outer strip layer and highlights selected outlines", () => {
    const fills: string[] = [];
    const strokes: string[] = [];
    const contextState = {
      strokeStyle: "", fillStyle: "", lineWidth: 1, lineJoin: "miter", font: "",
      textAlign: "start", textBaseline: "alphabetic",
      save: () => undefined, restore: () => undefined, beginPath: () => undefined,
      moveTo: () => undefined, lineTo: () => undefined, closePath: () => undefined,
      arc: () => undefined, fillText: () => undefined, setLineDash: () => undefined,
      fill: () => fills.push(String(contextState.fillStyle)),
      stroke: () => strokes.push(String(contextState.strokeStyle)),
    };
    const wire: EditorSceneObject = {
      id: "stripped", layerId: "bottom", kind: "wire", label: "W1",
      x: 0, y: 0, width: 0, height: 0, color: "#c00",
      points: [{ x: 0, y: 20 }, { x: 100, y: 20 }], stripProfiles: { from: stripProfile },
    };

    drawEditorSceneObject(contextState as unknown as CanvasRenderingContext2D, wire, false, "drawing");
    expect(fills.slice(0, 2)).toEqual(["#d6ad65", "#c00"]);
    expect(strokes.slice(1, 3)).toEqual(["#344b59", "#344b59"]);

    strokes.length = 0;
    drawEditorSceneObject(contextState as unknown as CanvasRenderingContext2D, wire, true, "drawing");
    expect(strokes.slice(0, 3)).toEqual(["#1179ac", "#1179ac", "#1179ac"]);
  });

  it("reconciles controlled and local multi-selection after scene objects disappear", () => {
    expect(reconcileWorkspaceSelection(
      ["wire", "removed", "wire"],
      "removed",
      objects,
    )).toEqual({ objectIds: ["wire"], primaryObjectId: "wire" });
    expect(reconcileWorkspaceSelection([], "upper", objects)).toEqual({
      objectIds: ["upper"], primaryObjectId: "upper",
    });
    expect(reconcileWorkspaceSelection(["dimension:wire"], "dimension:wire", [{
      id: "dimension:wire", layerId: "top", kind: "dimension", label: "100 мм",
      x: 0, y: 0, width: 0, height: 0, color: "#000", points: [{ x: 0, y: 0 }, { x: 10, y: 0 }],
    }])).toEqual({ objectIds: [], primaryObjectId: null });
  });

  it("keeps an authored dimension selected when the canvas reports its hit", () => {
    const dimension: EditorSceneObject = {
      id: "D1", layerId: "top", kind: "dimension", label: "100 мм",
      x: 0, y: 0, width: 0, height: 0, color: "#000", points: [{ x: 0, y: 0 }, { x: 10, y: 0 }],
    };
    expect(reconcileWorkspaceGroupSelection([dimension.id], [dimension])).toEqual({ objectIds: [dimension.id], primaryObjectId: dimension.id });
  });

  it("renders a complete two-view editor shell with real controls", () => {
    const markup = renderToStaticMarkup(createElement(HarnessEditorWorkspace, {
      harnessId: "harness-a",
      harnessDesignation: "ЖГ-01",
      objects,
      layers,
      selectedObjectId: "upper",
      onObjectsChange: vi.fn(),
      onLayersChange: vi.fn(),
      onClose: vi.fn(),
    }));

    expect(markup).toContain("Редактор жгута ЖГ-01");
    expect(markup).toContain("Схема Э4");
    expect(markup).toContain("Чертёж");
    // ERP: route, route v2 and UML sections are not part of the port.
    expect(markup).not.toContain("Маршрут");
    expect(markup).not.toContain("UML");
    const header = markup.match(/<header class="he-header">[\s\S]*?<\/header>/)?.[0] ?? "";
    expect(header).not.toContain("Объекты");
    expect(header).not.toContain("Свойства и слои");
    expect(header).toContain("К проектам");
    expect(markup).toContain("Инструменты редактора");
    expect(markup).toContain("Поле схемы Э4");
    expect(markup).toContain("Свойства");
    expect(markup).toContain("Слои");
    expect(markup).toContain("Объекты и материалы");
    expect(markup).toContain("Артикул, название или характеристика");
    expect(markup).toContain("Соединители");
    expect(markup).toContain("XS-04");
    expect(markup).toContain("XS-10");
    expect(markup).toContain("Масштаб редактора");
    expect(markup).toContain("25%");
    expect(markup).toContain("50%");
    expect(markup).toContain("100%");
    expect(markup).toContain("150%");
    expect(markup).toContain("200%");
    expect(markup).toContain("Вписать в экран");
    expect(markup).toContain("Ctrl + колесо — масштаб");
    expect(markup).not.toContain("Размер, клавиша D");
  });

  it("renders server-backed catalog navigation, loading state and pagination action", () => {
    const markup = renderToStaticMarkup(createElement(HarnessEditorWorkspace, {
      harnessId: "harness-a",
      harnessDesignation: "ЖГ-01",
      objects,
      layers,
      catalogSources: [
        { id: "terminals", label: "Терминалы", description: "БД.ТЕР" },
        { id: "cables", label: "Кабели", description: "СПР.КАБ" },
      ],
      selectedCatalogSourceId: "terminals",
      catalogQuery: "M39029",
      catalogLoadState: "ready",
      catalogHasMore: true,
      catalogItems: [{
        id: "reference:terminals:1",
        title: "M39029/57-354",
        subtitle: "Сигнальный контакт · 0,35–0,5 мм²",
        category: "Терминалы",
        accent: "#8a6635",
        placement: "reference-only",
      }],
    }));

    expect(markup).toContain("Терминалы");
    expect(markup).toContain("Кабели");
    expect(markup).toContain('value="M39029"');
    expect(markup).toContain("M39029/57-354");
    expect(markup).toContain("0,35–0,5 мм²");
    expect(markup).toContain("Показать ещё");
    expect(markup).toContain('draggable="false"');
  });

  it("keeps the world point under the cursor while zooming and reverses transforms", () => {
    const camera = { offsetX: 40, offsetY: -20, zoom: 1.25 };
    const anchor = { x: 315, y: 220 };
    const worldBefore = screenToWorld(camera, anchor);
    const zoomed = zoomEditorCameraAt(camera, anchor, 2.5);
    const worldAfter = screenToWorld(zoomed, anchor);

    expect(worldAfter.x).toBeCloseTo(worldBefore.x, 10);
    expect(worldAfter.y).toBeCloseTo(worldBefore.y, 10);
    expect(worldToScreen(zoomed, worldAfter)).toEqual(anchor);
    expect(panEditorCamera(camera, 12, -7)).toEqual({ offsetX: 52, offsetY: -27, zoom: 1.25 });
  });

  it("selects an E4 wire by its movable label box", () => {
    const labelWire: EditorSceneObject = {
      id: "label-wire",
      layerId: "bottom",
      kind: "wire",
      label: "CAN-H",
      x: 0,
      y: 0,
      width: 0,
      height: 0,
      color: "#34566a",
      points: [{ x: 100, y: 100 }, { x: 220, y: 100 }],
      metadata: { e4LabelPosition: "0.5" },
    };
    expect(hitTestE4WireLabel([labelWire], layers, { x: 160, y: 80 }, 1)).toEqual({ wireId: "label-wire" });
    expect(hitTestE4WireLabel([labelWire], layers, { x: 160, y: 130 }, 1)).toBeNull();
  });

  it("changes zoom from the wheel only while Ctrl is pressed", () => {
    const camera = { offsetX: 40, offsetY: -20, zoom: 1.25 };
    const anchor = { x: 315, y: 220 };
    expect(zoomEditorCameraFromWheel(camera, anchor, -100, false)).toBe(camera);
    expect(zoomEditorCameraFromWheel(camera, anchor, 0, true)).toBe(camera);
    const zoomed = zoomEditorCameraFromWheel(camera, anchor, -100, true);
    expect(zoomed.zoom).toBeCloseTo(1.4);
    expect(screenToWorld(zoomed, anchor).x).toBeCloseTo(screenToWorld(camera, anchor).x);
    expect(screenToWorld(zoomed, anchor).y).toBeCloseTo(screenToWorld(camera, anchor).y);
  });

  it("clamps wheel zoom at both limits without losing its screen anchor", () => {
    const anchor = { x: 113.25, y: 87.5 };
    for (const [camera, deltaY, expectedZoom] of [
      [{ offsetX: -8, offsetY: 15, zoom: maximumEditorZoom }, -1, maximumEditorZoom],
      [{ offsetX: -8, offsetY: 15, zoom: minimumEditorZoom }, 1, minimumEditorZoom],
    ] as const) {
      const worldBefore = screenToWorld(camera, anchor);
      const next = zoomEditorCameraFromWheel(camera, anchor, deltaY, true);
      expect(next.zoom).toBe(expectedZoom);
      expect(screenToWorld(next, anchor).x).toBeCloseTo(worldBefore.x, 10);
      expect(screenToWorld(next, anchor).y).toBeCloseTo(worldBefore.y, 10);
    }
    expect(clampEditorZoom(Number.NaN)).toBe(minimumEditorZoom);
    expect(clampEditorZoom(Number.NEGATIVE_INFINITY)).toBe(minimumEditorZoom);
    expect(clampEditorZoom(Number.POSITIVE_INFINITY)).toBe(maximumEditorZoom);
    const camera = { offsetX: 1, offsetY: 2, zoom: 1 };
    expect(zoomEditorCameraFromWheel(camera, anchor, Number.NaN, true)).toBe(camera);
  });

  it("handles Ctrl+wheel over inline inputs without letting pointer presses reach the canvas", () => {
    const camera = { offsetX: 40, offsetY: -20, zoom: 1.25 };
    const preventDefault = vi.fn();
    const changeCamera = vi.fn();
    const ordinaryWheel = {
      ctrlKey: false, deltaY: -100, clientX: 315, clientY: 220, preventDefault,
      target: { tagName: "INPUT" },
    };
    expect(handleEditorViewportWheel(ordinaryWheel, camera, { left: 15, top: 20 }, changeCamera)).toBe(false);
    expect(preventDefault).not.toHaveBeenCalled();
    expect(changeCamera).not.toHaveBeenCalled();

    const ctrlWheel = { ...ordinaryWheel, ctrlKey: true };
    expect(handleEditorViewportWheel(ctrlWheel, camera, { left: 15, top: 20 }, changeCamera)).toBe(true);
    expect(preventDefault).toHaveBeenCalledOnce();
    const nextCamera = changeCamera.mock.calls[0]![0];
    expect(nextCamera.zoom).toBeCloseTo(1.4);
    expect(screenToWorld(nextCamera, { x: 300, y: 200 }).x).toBeCloseTo(screenToWorld(camera, { x: 300, y: 200 }).x);
    expect(screenToWorld(nextCamera, { x: 300, y: 200 }).y).toBeCloseTo(screenToWorld(camera, { x: 300, y: 200 }).y);

    const stopPropagation = vi.fn();
    containInlineEditorPointerEvent({ stopPropagation });
    expect(stopPropagation).toHaveBeenCalledOnce();
  });

  it("starts inline movement only after the drag threshold and respects zoom", () => {
    expect(inlineObjectDragMoved(2, 2)).toBe(false);
    expect(inlineObjectDragMoved(3, 0)).toBe(true);
    expect(inlineObjectDragMoved(0, 0)).toBe(false);
    expect(inlineObjectDragMoved(Number.NaN, 5)).toBe(false);
    expect(inlineObjectDragDestination({ x: 100, y: 80 }, 20, -10, 2)).toEqual({ x: 110, y: 75 });
    expect(inlineObjectDragDestination({ x: 100, y: 80 }, 20, -10, 0.25)).toEqual({ x: 180, y: 40 });
  });

  it("treats the complete readonly table surface as a drag target", () => {
    const readonlyRoot = {};
    const closest = vi.fn((selector: string) => selector === ".e4-connector-canvas-editor.is-readonly" ? readonlyRoot : null);
    expect(isInlineEditorReadonlyTarget({ closest } as unknown as EventTarget)).toBe(true);
    expect(closest).toHaveBeenCalledWith(".e4-connector-canvas-editor.is-readonly");
    expect(isInlineEditorReadonlyTarget({ closest: () => null } as unknown as EventTarget)).toBe(false);
    expect(isInlineEditorReadonlyTarget(null)).toBe(false);
  });

  it("leaves input selection, copy and native popup controls interactive", () => {
    const input = {};
    const closest = vi.fn((selector: string) =>
      selector === "input, textarea, select, button, [contenteditable='true']" ? input : null);
    expect(isInlineEditorControlTarget({ closest } as unknown as EventTarget)).toBe(true);
    expect(closest).toHaveBeenCalledWith("input, textarea, select, button, [contenteditable='true']");
    expect(isInlineEditorControlTarget({ closest: () => null } as unknown as EventTarget)).toBe(false);
    expect(isInlineEditorControlTarget(null)).toBe(false);
  });

  it("fits actual E4 table and wire bounds into the available viewport", () => {
    const connector: EditorSceneObject = {
      id: "X-fit", layerId: "top", kind: "connector", label: "X-fit",
      x: 100, y: 80, width: 118, height: 72, color: "#123456",
      metadata: {
        view: "e4", orientation: "right",
        rows: JSON.stringify([
          { number: 1, contactType: "S", circuit: "A", terminal: "T", wire: "W", color: "R", status: "available", customValues: {} },
          { number: 2, contactType: "S", circuit: "B", terminal: "T", wire: "W", color: "B", status: "available", customValues: {} },
          { number: 3, contactType: "P", circuit: "C", terminal: "T", wire: "W", color: "G", status: "not-connected", customValues: {} },
        ]),
      },
    };
    const wire: EditorSceneObject = {
      id: "W-fit", layerId: "bottom", kind: "wire", label: "W-fit",
      x: 0, y: 0, width: 0, height: 0, color: "#c00",
      points: [{ x: -50, y: 30 }, { x: 250, y: 30 }, { x: 250, y: 420 }],
      metadata: { view: "e4" },
    };
    const bounds = getEditorSceneBounds([connector, wire], layers, "e4")!;
    const layout = getE4ConnectorLayout(connector)!;
    expect(bounds).toEqual({
      minX: -50,
      minY: 30,
      maxX: connector.x + layout.width + 21,
      maxY: 420,
    });
    const fitted = fitEditorCameraToBounds(
      { offsetX: 0, offsetY: 0, zoom: 1 },
      bounds,
      { width: 900, height: 600 },
      50,
    );
    expect(fitted.zoom).toBeCloseTo(Math.min(
      800 / (bounds.maxX - bounds.minX),
      500 / (bounds.maxY - bounds.minY),
    ));
    expect(worldToScreen(fitted, { x: bounds.minX, y: bounds.minY }).x).toBeGreaterThanOrEqual(50);
    expect(worldToScreen(fitted, { x: bounds.maxX, y: bounds.maxY }).x).toBeLessThanOrEqual(850);

    const hiddenWireLayers = layers.map((layer) => layer.id === "bottom" ? { ...layer, visible: false } : layer);
    expect(getEditorSceneBounds([connector, wire], hiddenWireLayers, "e4")).toMatchObject({
      minX: connector.x,
      minY: connector.y,
      maxX: connector.x + layout.width + 21,
      maxY: connector.y + layout.height,
    });
  });

  it("keeps an empty scene camera unchanged and fits a 300-contact table", () => {
    const camera = { offsetX: 17, offsetY: -31, zoom: 1.5 };
    expect(getEditorSceneBounds([], layers, "e4")).toBeNull();
    expect(fitEditorCameraToBounds(camera, null, { width: 900, height: 600 })).toBe(camera);

    const rows = Array.from({ length: 300 }, (_, index) => ({
      number: index + 1,
      contactType: "S",
      circuit: `C${index + 1}`,
      terminal: "T",
      wire: "W",
      color: "R",
      status: index === 299 ? "not-connected" : "available",
      customValues: {},
    }));
    const connector: EditorSceneObject = {
      id: "X-300", layerId: "top", kind: "connector", label: "X-300",
      x: -700, y: -250, width: 118, height: 72, color: "#123456",
      metadata: { view: "e4", orientation: "left", rows: JSON.stringify(rows) },
    };
    const layout = getE4ConnectorLayout(connector)!;
    const bounds = getEditorSceneBounds([connector], layers, "e4")!;
    expect(layout.height).toBe(7_276);
    expect(bounds).toEqual({
      minX: connector.x - 21,
      minY: connector.y,
      maxX: connector.x + layout.width,
      maxY: connector.y + layout.height,
    });
    const fitted = fitEditorCameraToBounds(camera, bounds, { width: 900, height: 600 }, 48);
    expect(fitted.zoom).toBeCloseTo(504 / layout.height, 10);
    expect(fitted.zoom).toBeLessThan(0.25);
    expect(worldToScreen(fitted, { x: bounds.minX, y: bounds.minY }).y).toBeCloseTo(48, 8);
    expect(worldToScreen(fitted, { x: bounds.maxX, y: bounds.maxY }).y).toBeCloseTo(552, 8);
  });

  it("normalizes inverted fit bounds and remains finite in a viewport smaller than its margins", () => {
    const fitted = fitEditorCameraToBounds(
      { offsetX: 0, offsetY: 0, zoom: 1 },
      { minX: 200, minY: 100, maxX: -200, maxY: -100 },
      { width: 20, height: 10 },
      48,
    );
    expect(fitted.zoom).toBe(minimumEditorZoom);
    expect(fitted).toEqual({ zoom: minimumEditorZoom, offsetX: 10, offsetY: 5 });
  });

  it("honors visual layer order, visibility and polyline selection", () => {
    expect(objectsInPaintOrder(objects, layers).map((object) => object.id)).toEqual(["lower", "wire", "upper"]);
    expect(hitTestEditorScene(objects, layers, { x: 20, y: 20 }, 1)).toBe("upper");

    const hiddenTop = toggleLayerVisibility(layers, "top");
    expect(hitTestEditorScene(objects, hiddenTop, { x: 20, y: 20 }, 1)).toBe("lower");
    expect(hitTestEditorScene(objects, hiddenTop, { x: 140, y: 104 }, 1)).toBe("wire");

    const reordered = moveLayer(layers, "bottom", 0);
    expect(hitTestEditorScene(objects, reordered, { x: 20, y: 20 }, 1)).toBe("lower");
  });

  it("finds a numbered connector contact for the wire tool", () => {
    const connector: EditorSceneObject = {
      id: "X1", layerId: "top", kind: "connector", label: "X1",
      x: 100, y: 100, width: 118, height: 132, color: "#123456",
      metadata: { contactCount: "4" },
    };
    expect(hitTestConnectorContact([connector], layers, { x: 218, y: 150 }, 1)).toEqual({
      connectorId: "X1",
      contactIndex: 1,
    });
  });

  it("builds mirrored E4 connector tables and exposes only the number-column outer contact edge", () => {
    const rows = JSON.stringify([
      { number: 1, contactType: "S", circuit: "PWR", terminal: "M39029", wire: "МС 0,5", color: "красный", status: "available", customValues: { note: "A" } },
      { number: 2, contactType: "S", circuit: "GND", terminal: "M39029", wire: "МС 0,5", color: "чёрный", status: "not-connected", customValues: { note: "B" } },
    ]);
    const right: EditorSceneObject = {
      id: "X-right", layerId: "top", kind: "connector", label: "X1",
      x: 100, y: 80, width: 118, height: 72, color: "#123456",
      metadata: {
        view: "e4", orientation: "right", designation: "X1", partNumber: "D-SUB-9",
        columns: JSON.stringify(["color", "wire", "terminal", "circuit", "contactType", "number"]),
        rows,
      },
    };
    const left: EditorSceneObject = {
      ...right,
      id: "X-left",
      x: 800,
      metadata: {
        ...right.metadata,
        orientation: "left",
        columns: JSON.stringify(["number", "contactType", "circuit", "terminal", "wire", "color"]),
      },
    };
    const rightLayout = getE4ConnectorLayout(right)!;
    const leftLayout = getE4ConnectorLayout(left)!;

    expect(rightLayout.columns.map((column) => column.id)).toEqual(["color", "wire", "terminal", "circuit", "contactType", "number"]);
    expect(leftLayout.columns.map((column) => column.id)).toEqual(["number", "contactType", "circuit", "terminal", "wire", "color"]);
    expect(rightLayout.width).toBeGreaterThan(right.width);
    expect(rightLayout.height).toBe(124);
    expect(rightLayout.contactPoints[0]).toEqual({ x: right.x + rightLayout.width, y: 144 });
    expect(leftLayout.contactPoints[0]).toEqual({ x: left.x, y: 144 });

    expect(hitTestConnectorContact([right], layers, rightLayout.contactPoints[0]!, 1, "e4")).toEqual({ connectorId: "X-right", contactIndex: 0 });
    expect(hitTestConnectorContact([right], layers, rightLayout.contactPoints[1]!, 1, "e4")).toBeNull();
    expect(hitTestConnectorContact([left], layers, leftLayout.contactPoints[0]!, 1, "e4")).toEqual({ connectorId: "X-left", contactIndex: 0 });
    const rightNumber = rightLayout.columns.at(-1)!;
    expect(hitTestConnectorContact([right], layers, { x: rightNumber.x, y: rightLayout.contactPoints[0]!.y }, 1, "e4")).toBeNull();
    expect(hitTestConnectorContact([left], layers, { x: left.x + leftLayout.width, y: leftLayout.contactPoints[0]!.y }, 1, "e4")).toBeNull();
    expect(e4ContactMarker("available", "right")).toBeNull();
    expect(e4ContactMarker("not-connected", "right")).toEqual({
      lineStart: { x: 0, y: 0 },
      lineEnd: { x: 12, y: 0 },
      crossCenter: { x: 16, y: 0 },
      crossSize: 5,
    });
    expect(e4ContactMarker("not-connected", "left")).toEqual({
      lineStart: { x: 0, y: 0 },
      lineEnd: { x: -12, y: 0 },
      crossCenter: { x: -16, y: 0 },
      crossSize: 5,
    });
    expect(hitTestEditorScene([right], layers, {
      x: rightLayout.contactPoints[1]!.x + 16,
      y: rightLayout.contactPoints[1]!.y,
    }, 1, "e4")).toBe("X-right");
    expect(hitTestEditorScene([left], layers, {
      x: leftLayout.contactPoints[1]!.x - 16,
      y: leftLayout.contactPoints[1]!.y,
    }, 1, "e4")).toBe("X-left");
    expect(hitTestEditorScene([right], layers, {
      x: rightLayout.contactPoints[1]!.x + 16,
      y: rightLayout.contactPoints[1]!.y + 8,
    }, 1, "e4")).toBe("X-right");
    expect(hitTestEditorScene([right], layers, {
      x: rightLayout.contactPoints[1]!.x + 8,
      y: rightLayout.contactPoints[1]!.y + 11,
    }, 1, "e4")).toBeNull();
  });

  it("supports hidden and custom E4 columns while preserving Drawing connector fallback", () => {
    const connector: EditorSceneObject = {
      id: "X-custom", layerId: "top", kind: "connector", label: "X3",
      x: 50, y: 60, width: 118, height: 100, color: "#123456",
      metadata: {
        view: "e4", orientation: "right", contactCount: "2",
        columns: JSON.stringify(["circuit", "custom:note"]),
        columnLabels: JSON.stringify({ "custom:note": "Примечание" }),
        rows: JSON.stringify([
          { number: 1, contactType: "", circuit: "A1", terminal: "", wire: "", color: "", status: "available", customValues: { note: "Экран" } },
          { number: 2, contactType: "", circuit: "A2", terminal: "", wire: "", color: "", status: "available", customValues: { note: "Резерв" } },
        ]),
      },
    };
    const layout = getE4ConnectorLayout(connector)!;
    expect(layout.columns.map((column) => [column.id, column.label])).toEqual([
      ["circuit", "Цепь"],
      ["custom:note", "Примечание"],
    ]);
    expect(layout.contactPoints[0]!.x).toBe(connector.x + layout.width);
    const drawingContact = { x: connector.x, y: connector.y + 28 };
    expect(hitTestConnectorContact([connector], layers, drawingContact, 1, "drawing")).toEqual({ connectorId: "X-custom", contactIndex: 0 });
    expect(hitTestConnectorContact([connector], layers, { ...drawingContact, x: connector.x + connector.width }, 1, "drawing")).toEqual({ connectorId: "X-custom", contactIndex: 0 });
  });

  it("does not widen the canvas number column for a long footer article", () => {
    const makeConnector = (partNumber: string): EditorSceneObject => ({
      id: partNumber, layerId: "top", kind: "connector", label: "X1",
      x: 0, y: 0, width: 118, height: 72, color: "#123456",
      metadata: {
        view: "e4", orientation: "right", designation: "X1", partNumber,
        rows: JSON.stringify([{ number: 1, contactType: "", circuit: "", terminal: "", wire: "", color: "", status: "available", customValues: {} }]),
      },
    });
    const numberWidth = (partNumber: string) => getE4ConnectorLayout(makeConnector(partNumber))!.columns
      .find((column) => column.id === "number")!.width;
    expect(numberWidth("XHP-2(10.0)-U-WITH-A-LONG-SUFFIX")).toBe(numberWidth("XHP-2"));
  });

  it("finds editable route points and wire ends without treating ends as route points", () => {
    const routedWire: EditorSceneObject = {
      id: "W1", layerId: "bottom", kind: "wire", label: "W1",
      x: 0, y: 0, width: 0, height: 0, color: "#222222",
      points: [{ x: 10, y: 20 }, { x: 100, y: 80 }, { x: 180, y: 20 }],
    };
    expect(hitTestWireRoutePoint(routedWire, { x: 104, y: 84 }, 1)).toBe(0);
    expect(hitTestWireRoutePoint(routedWire, { x: 10, y: 20 }, 1)).toBeNull();
    expect(hitTestWireEnd(routedWire, { x: 12, y: 22 }, 1)).toBe("from");
    expect(hitTestWireEnd(routedWire, { x: 181, y: 21 }, 1)).toBe("to");
  });

  it("outlines a white E4 wire while keeping its conductor white", () => {
    const strokes: { readonly color: string; readonly width: number }[] = [];
    const contextState = {
      strokeStyle: "",
      fillStyle: "",
      lineWidth: 1,
      font: "",
      textAlign: "start",
      textBaseline: "alphabetic",
      save: () => undefined,
      restore: () => undefined,
      beginPath: () => undefined,
      moveTo: () => undefined,
      lineTo: () => undefined,
      quadraticCurveTo: () => undefined,
      closePath: () => undefined,
      arc: () => undefined,
      fill: () => undefined,
      fillText: () => undefined,
      setLineDash: () => undefined,
      stroke: () => strokes.push({ color: String(contextState.strokeStyle), width: contextState.lineWidth }),
    };
    const wire: EditorSceneObject = {
      id: "white", layerId: "bottom", kind: "wire", label: "W1",
      x: 0, y: 0, width: 0, height: 0, color: "#FFFFFF",
      points: [{ x: 0, y: 20 }, { x: 120, y: 20 }], metadata: { view: "e4" },
    };

    drawEditorSceneObject(contextState as unknown as CanvasRenderingContext2D, wire, false, "e4");

    expect(strokes.slice(0, 2)).toEqual([
      { color: "#53636c", width: 5 },
      { color: "#FFFFFF", width: 3 },
    ]);
  });

  it("uses the model route without inferring leads or corners from scene metadata", () => {
    const complete: EditorSceneObject = {
      id: "complete", layerId: "top", kind: "wire", label: "W1",
      x: 0, y: 0, width: 0, height: 0, color: "#222222",
      points: [{ x: 0, y: 0 }, { x: 24, y: 0 }, { x: 24, y: 60 }, { x: 100, y: 60 }],
      metadata: { view: "e4", fromSide: "right", toSide: "left", leadLength: "24" },
    };
    for (const metadata of [undefined, {}, { fromSide: "left", toSide: "right", leadLength: "100" }] as (Readonly<Record<string,string>> | undefined)[]) {
      expect(getE4WireRoute({ ...complete, metadata })).toBe(complete.points);
    }
    const direct = { ...complete, metadata: {}, points: [{ x: 0, y: 10 }, { x: 100, y: 60 }] };
    expect(getE4WireRoute(direct)).toBe(direct.points);
    expect(getE4WireRoute(complete)).toBe(complete.points);
    expect(e4WireSegments(complete.points!).map((segment) => segment.orientation)).toEqual([
      "horizontal", "vertical", "horizontal",
    ]);
  });

  it("projects and finds an internal E4 segment without moving endpoints", () => {
    const points = [
      { x: 0, y: 0 }, { x: 24, y: 0 }, { x: 24, y: 50 },
      { x: 76, y: 50 }, { x: 76, y: 0 }, { x: 100, y: 0 },
    ];
    const wire: EditorSceneObject = {
      id: "route", layerId: "top", kind: "wire", label: "W1",
      x: 0, y: 0, width: 0, height: 0, color: "#222222", points,
      metadata: { view: "e4" },
    };
    expect(projectPointToOrthogonalSegment({ x: 45, y: 54 }, points[2]!, points[3]!)).toEqual({
      point: { x: 45, y: 50 }, distance: 4, position: 21 / 52, orientation: "horizontal",
    });
    expect(hitTestE4WireSegment([wire], layers, { x: 50, y: 54 }, 1)).toMatchObject({
      wireId: "route", index: 2, orientation: "horizontal", point: { x: 50, y: 50 },
    });
    expect(hitTestWireSegment([wire], layers, { x: 10, y: 3 }, 1)).toMatchObject({ wireId: "route", index: 0 });
  });

  it("finds crossings, omits explicit junctions and derives a movable screen on a common span", () => {
    const wires: readonly EditorSceneObject[] = [
      { id: "h1", layerId: "bottom", kind: "wire", label: "W1", x: 0, y: 0, width: 0, height: 0, color: "#c00", points: [{ x: 0, y: 40 }, { x: 120, y: 40 }], metadata: { view: "e4" } },
      { id: "h2", layerId: "bottom", kind: "wire", label: "W2", x: 0, y: 0, width: 0, height: 0, color: "#00c", points: [{ x: 20, y: 60 }, { x: 100, y: 60 }], metadata: { view: "e4" } },
      { id: "v", layerId: "top", kind: "wire", label: "W3", x: 0, y: 0, width: 0, height: 0, color: "#090", points: [{ x: 70, y: 0 }, { x: 70, y: 100 }], metadata: { view: "e4" } },
    ];
    expect(getE4WireCrossings(wires, layers).map((item) => item.point)).toEqual([
      { x: 70, y: 40 }, { x: 70, y: 60 },
    ]);
    expect(getE4WireCrossings(wires, layers, [{ id: "j1", position: { x: 70, y: 40 }, wireIds: ["h1", "v"] }])).toHaveLength(1);
    const span = findE4CommonParallelSpan(wires, ["h1", "h2"]);
    expect(span).toMatchObject({ orientation: "horizontal", start: 20, end: 100, crossMinimum: 40, crossMaximum: 60 });
    const screen = { id: "s1", wireIds: ["h1", "h2"], position: 0.25, label: "Экран", width: 18 };
    const screenLayout = getE4ScreenLayout(screen, wires)!;
    expect(screenLayout).toMatchObject({
      center: { x: 40, y: 50 }, orientation: "horizontal", crossSize: 38,
      bodyConnectionPoint: { x: 40, y: 31 }, connectionPoint: { x: 40, y: 31 },
    });
    expect(screenLayout.alongSize).toBe(18);
    expect(screenLayout.crossSize / screenLayout.alongSize).toBeGreaterThan(2);
    expect(hitTestE4Screen([screen], wires, { x: 40, y: 50 }, 1)?.id).toBe("s1");
    expect(hitTestE4Screen([screen], wires, { x: 40, y: 70 }, 1_000)).toBeNull();
    expect(hitTestE4ScreenConnection([screen], wires, { x: 40, y: 31 }, 1)).toEqual({
      screenId: "s1", screenTerminalSide: "above",
    });
    expect(hitTestE4ScreenConnection([screen], wires, { x: 40, y: 15 }, 1)).toBeNull();
    const bothSides = getE4ScreenLayout({ ...screen, terminalSide: "both" }, wires)!;
    expect(bothSides.terminals).toEqual([
      { side: "above", bodyConnectionPoint: { x: 40, y: 31 }, connectionPoint: { x: 40, y: 31 } },
      { side: "below", bodyConnectionPoint: { x: 40, y: 69 }, connectionPoint: { x: 40, y: 69 } },
    ]);
    expect(hitTestE4ScreenConnection([{ ...screen, terminalSide: "both" }], wires, { x: 40, y: 69 }, 1)).toEqual({
      screenId: "s1", screenTerminalSide: "below",
    });
    const pair = { id: "dp", wireIds: ["h1", "h2"] as const, step: 25, amplitude: 6, variant: 2 as const };
    const pairLayout = getE4DifferentialPairLayout(pair, wires)!;
    expect(pairLayout).toMatchObject({
      variant: 2, wireIds: ["h1", "h2"], crossMinimum: 40, crossMaximum: 60,
    });
    expect(pairLayout.motifs.length).toBeGreaterThan(1);
    expect(pairLayout.motifs.every(motif => motif.to > motif.from && motif.to - motif.from <= 16)).toBe(true);
    expect(pairLayout.motifs[0]!.coloredFrom).toBeLessThan(pairLayout.motifs[0]!.from);
    expect(pairLayout.motifs.at(-1)!.coloredTo).toBeGreaterThan(pairLayout.motifs.at(-1)!.to);
    expect(pairLayout.motifs.slice(1).every((motif, index) =>
      motif.center > pairLayout.motifs[index]!.center)).toBe(true);
    expect(hitTestE4DifferentialPair([pair], wires, { x: 60, y: 50 }, 1)?.wireIds).toEqual(["h1", "h2"]);
    expect(hitTestE4DifferentialPair([pair], wires, { x: 20, y: 50 }, 1_000)).toBeNull();
    expect(getE4DifferentialPairLayout({
      id: "dp", wireIds: ["h1", "h2"], step: 25, amplitude: 6, variant: 2,
    }, wires)).toMatchObject({ variant: 2, crossMinimum: 40, crossMaximum: 60 });
    expect(getE4DifferentialPairLayout({
      id: "dp-overlap", wireIds: ["h1", "h1"], step: 25, amplitude: 6, variant: 1,
    }, wires)).toMatchObject({ crossMinimum: 34, crossMaximum: 46 });
  });

  it("keeps screen placement continuous when selected routes have different bend counts", () => {
    const wires: readonly EditorSceneObject[] = [
      { id: "a", layerId: "bottom", kind: "wire", label: "A", x: 0, y: 0, width: 0, height: 0, color: "#c00", points: [
        { x: 0, y: 20 }, { x: 80, y: 20 }, { x: 80, y: 80 }, { x: 140, y: 80 },
      ], metadata: { view: "e4" } },
      { id: "b", layerId: "bottom", kind: "wire", label: "B", x: 0, y: 0, width: 0, height: 0, color: "#00c", points: [
        { x: 0, y: 40 }, { x: 50, y: 40 }, { x: 50, y: 60 }, { x: 100, y: 60 },
        { x: 100, y: 100 }, { x: 140, y: 100 },
      ], metadata: { view: "e4" } },
    ];
    const start = getE4ScreenLayout({ id: "s", wireIds: ["a", "b"], position: 0, label: "SH", width: 20 }, wires);
    const end = getE4ScreenLayout({ id: "s", wireIds: ["a", "b"], position: 1, label: "SH", width: 20 }, wires);
    expect(start).not.toBeNull();
    expect(end).not.toBeNull();
    expect(start!.pathLength).toBeGreaterThan(0);
    expect(end!.pathLength).toBe(start!.pathLength);
    expect(end!.center).not.toEqual(start!.center);
  });

  it("isolates both differential-pair traces from a wire between contacts 2 and 4", () => {
    const wires: readonly EditorSceneObject[] = [
      { id: "xs1:2", layerId: "bottom", kind: "wire", label: "PAIR-P", x: 0, y: 0, width: 0, height: 0, color: "#c000c0", points: [{ x: 0, y: 40 }, { x: 120, y: 40 }], metadata: { view: "e4" } },
      { id: "xs1:3-xs3:3", layerId: "bottom", kind: "wire", label: "SINGLE", x: 0, y: 0, width: 0, height: 0, color: "#0077bb", points: [{ x: 0, y: 60 }, { x: 120, y: 60 }], metadata: { view: "e4" } },
      { id: "xs1:4", layerId: "bottom", kind: "wire", label: "PAIR-N", x: 0, y: 0, width: 0, height: 0, color: "#0044cc", points: [{ x: 0, y: 80 }, { x: 120, y: 80 }], metadata: { view: "e4" } },
    ];
    const strokes: { readonly color: string; readonly width: number; readonly points: readonly EditorPoint[] }[] = [];
    let points: EditorPoint[] = [];
    const contextState = {
      strokeStyle: "",
      lineWidth: 1,
      lineCap: "butt",
      lineJoin: "miter",
      save: () => undefined,
      restore: () => undefined,
      beginPath: () => { points = []; },
      moveTo: (x: number, y: number) => { points.push({ x, y }); },
      lineTo: (x: number, y: number) => { points.push({ x, y }); },
      bezierCurveTo: (_x1: number, _y1: number, _x2: number, _y2: number, x: number, y: number) => { points.push({ x, y }); },
      stroke: () => {
        strokes.push({ color: String(contextState.strokeStyle), width: contextState.lineWidth, points: [...points] });
      },
    };
    const context = contextState as unknown as CanvasRenderingContext2D;

    drawE4DifferentialPairs(context, [{
      id: "pair", wireIds: ["xs1:2", "xs1:4"], step: 25, amplitude: 6, variant: 2,
    }], wires);

    const crossingStrokes = strokes.filter((stroke) => stroke.points.some((point, index) => {
      const previous = stroke.points[index - 1];
      return previous !== undefined && previous.x !== point.x && previous.y !== point.y;
    }));
    expect(crossingStrokes).toHaveLength(16);
    const coloredCrossings = crossingStrokes.filter(stroke => stroke.width === 3);
    expect(coloredCrossings).toHaveLength(8);
    expect(coloredCrossings.every(stroke => stroke.color === "#c000c0" || stroke.color === "#0044cc")).toBe(true);
    expect(strokes.some(stroke => stroke.color === "#0077bb")).toBe(false);
  });

  it("does not invent fallback contacts beside a drawing and hits its external X", () => {
    const connector: EditorSceneObject = {
      id: "library", layerId: "top", kind: "connector", label: "XS1",
      x: 100, y: 200, width: 60, height: 90, color: "#334455",
      metadata: {
        contactCount: "3",
        materializedContactPoints: JSON.stringify([
          null,
          { x: -30, y: 20, direction: "left", status: "not-connected" },
          { x: 30, y: 40, direction: "right", status: "available" },
        ]),
      },
    };
    expect(getMaterializedConnectorContactPoints(connector)).toEqual([
      null,
      { x: 70, y: 220, direction: "left", status: "not-connected" },
      { x: 130, y: 240, direction: "right", status: "available" },
    ]);
    expect(hitTestConnectorContact([connector], layers, { x: 160, y: 228 }, 1, "drawing")).toBeNull();
    expect(hitTestConnectorContact([connector], layers, { x: 70, y: 220 }, 1, "drawing")).toBeNull();
    expect(hitTestConnectorContact([connector], layers, { x: 130, y: 240 }, 1, "drawing")).toEqual({
      connectorId: "library", contactIndex: 2,
    });

    expect(hitTestEditorScene([connector], layers, { x: 58, y: 220 }, 1, "drawing")).toBe("library");
    expect(getEditorSceneBounds([connector], layers, "drawing")!.minX).toBe(55);

    const arcs: EditorPoint[] = [];
    const path: EditorPoint[] = [];
    const context = {
      save: () => undefined, restore: () => undefined, beginPath: () => undefined,
      moveTo: (x: number, y: number) => path.push({ x, y }),
      lineTo: (x: number, y: number) => path.push({ x, y }), quadraticCurveTo: () => undefined,
      closePath: () => undefined, fill: () => undefined, stroke: () => undefined,
      fillText: () => undefined, arc: (x: number, y: number) => arcs.push({ x, y }),
      fillStyle: "", strokeStyle: "", lineWidth: 1, lineCap: "butt", font: "",
    } as unknown as CanvasRenderingContext2D;
    drawEditorSceneObject(context, connector, false, "drawing");
    expect(arcs).toEqual([
      { x: 130, y: 240 },
    ]);
    expect(path).toContainEqual({ x: 70, y: 220 });
    expect(path).toContainEqual({ x: 58, y: 220 });
  });

  it.each([false, true])("keeps an E4 table outline crisp regardless of selection (%s)", (selected) => {
    const connector: EditorSceneObject = {
      id: "e4-table", layerId: "top", kind: "connector", label: "XS3",
      x: 40, y: 30, width: 180, height: 100, color: "#55717f",
      metadata: {
        view: "e4", orientation: "left", designation: "XS3", libraryCode: "JBT",
        partNumber: "ННР-2", columns: JSON.stringify(["number"]),
        rows: JSON.stringify([{
          number: 1, contactType: "Сигнальные", circuit: "", terminal: "",
          wire: "", wireSection: "", color: "", secondaryColor: "",
          status: "available", customValues: {},
        }]),
      },
    };
    const outlines: { style: string; width: number }[] = [];
    const state: Record<string, unknown> = { strokeStyle: "", lineWidth: 0 };
    const context = new Proxy(state, {
      get(target, key: string) {
        if (key === "strokeRect") return () => outlines.push({ style: String(target.strokeStyle), width: Number(target.lineWidth) });
        if (key in target) return target[key];
        if (key === "measureText") return () => ({ width: 0 });
        return () => undefined;
      },
      set(target, key: string, value: unknown) {
        target[key] = value;
        return true;
      },
    }) as unknown as CanvasRenderingContext2D;

    drawEditorSceneObject(context, connector, selected, "e4");

    expect(outlines[0]).toEqual({ style: "#087bb4", width: 3 });
  });

  it("reports one bridge at a polyline vertex and only suppresses a junction for its own wires", () => {
    const crossingWires: readonly EditorSceneObject[] = [
      {
        id: "under", layerId: "bottom", kind: "wire", label: "W1",
        x: 0, y: 0, width: 0, height: 0, color: "#c00",
        points: [{ x: 0, y: 40 }, { x: 70, y: 40 }, { x: 70, y: 90 }], metadata: { view: "e4" },
      },
      {
        id: "over", layerId: "top", kind: "wire", label: "W2",
        x: 0, y: 0, width: 0, height: 0, color: "#00c",
        points: [{ x: 70, y: 0 }, { x: 70, y: 40 }, { x: 120, y: 40 }], metadata: { view: "e4" },
      },
    ];
    expect(getE4WireCrossings(crossingWires, layers)).toEqual([{
      point: { x: 70, y: 40 }, overWireId: "over", underWireId: "under", overOrientation: "vertical",
    }]);
    expect(getE4WireCrossings(crossingWires, layers, [{
      id: "own", position: { x: 70, y: 40 }, wireIds: ["under", "over"],
    }])).toEqual([]);
    expect(getE4WireCrossings(crossingWires, layers, [{
      id: "other", position: { x: 70, y: 40 }, wireIds: ["third", "fourth"],
    }])).toEqual([]);
    const horizontalBridge = getE4BridgeGeometry({ point: { x: 70, y: 40 }, overOrientation: "horizontal" });
    expect(horizontalBridge).toEqual({
      clearStart: { x: 62, y: 40 }, clearEnd: { x: 78, y: 40 },
      coloredStart: { x: 61, y: 40 }, arcStart: { x: 63, y: 40 },
      arcEnd: { x: 77, y: 40 }, coloredEnd: { x: 79, y: 40 },
    });
    expect(horizontalBridge.coloredStart.x).toBeLessThan(horizontalBridge.clearStart.x);
    expect(horizontalBridge.coloredEnd.x).toBeGreaterThan(horizontalBridge.clearEnd.x);
    const verticalBridge = getE4BridgeGeometry({ point: { x: 70, y: 40 }, overOrientation: "vertical" });
    expect(verticalBridge.coloredStart.y).toBeLessThan(verticalBridge.clearStart.y);
    expect(verticalBridge.coloredEnd.y).toBeGreaterThan(verticalBridge.clearEnd.y);
  });

  it("finds a common span in polynomial time for a large routed bundle", () => {
    const bundle = Array.from({ length: 20 }, (_, wireIndex): EditorSceneObject => ({
      id: `bundle-${wireIndex}`,
      layerId: "top",
      kind: "wire",
      label: `W${wireIndex + 1}`,
      x: 0,
      y: 0,
      width: 0,
      height: 0,
      color: "#222222",
      points: [
        { x: 0, y: wireIndex * 8 }, { x: 100, y: wireIndex * 8 },
        { x: 100, y: 300 + wireIndex * 8 }, { x: 220, y: 300 + wireIndex * 8 },
        { x: 220, y: 600 + wireIndex * 8 }, { x: 360, y: 600 + wireIndex * 8 },
        { x: 360, y: 900 + wireIndex * 8 }, { x: 460, y: 900 + wireIndex * 8 },
      ],
      metadata: { view: "e4" },
    }));
    expect(findE4CommonParallelSpan(bundle, bundle.map((wire) => wire.id))).toMatchObject({
      orientation: "vertical", start: 152, end: 300,
    });
  });

  it("does not hit-test a screen when any of its wires is on a hidden layer", () => {
    const screenWires: readonly EditorSceneObject[] = [
      { id: "visible", layerId: "top", kind: "wire", label: "W1", x: 0, y: 0, width: 0, height: 0, color: "#c00", points: [{ x: 0, y: 40 }, { x: 100, y: 40 }], metadata: { view: "e4" } },
      { id: "hidden", layerId: "bottom", kind: "wire", label: "W2", x: 0, y: 0, width: 0, height: 0, color: "#00c", points: [{ x: 0, y: 60 }, { x: 100, y: 60 }], metadata: { view: "e4" } },
    ];
    const screen = { id: "screen", wireIds: ["visible", "hidden"], position: 0.5, label: "SH", width: 18 };
    const layersWithHiddenBottom = layers.map((layer) => layer.id === "bottom" ? { ...layer, visible: false } : layer);
    expect(hitTestE4Screen([screen], screenWires, { x: 50, y: 50 }, 1)).toMatchObject({ id: "screen" });
    expect(hitTestE4Screen([screen], screenWires, { x: 50, y: 50 }, 1, layersWithHiddenBottom)).toBeNull();
    const overlays = getVisibleE4SceneOverlays({
      crossingStyle: "bridge",
      junctions: [{ id: "j", position: { x: 50, y: 50 }, wireIds: ["visible", "hidden"] }],
      diffPairs: [{ id: "d", wireIds: ["visible", "hidden"], step: 20, amplitude: 4 }],
      screens: [screen],
    }, screenWires, layersWithHiddenBottom);
    expect(overlays.junctions).toEqual([]);
    expect(overlays.diffPairs).toEqual([]);
    expect(overlays.screens).toEqual([]);
  });

  it("keeps the screen upright and covers all horizontal sections through staggered bends", () => {
    const routed: readonly EditorSceneObject[] = [
      {
        id: "route-a", layerId: "top", kind: "wire", label: "A",
        x: 0, y: 0, width: 0, height: 0, color: "#c00", metadata: { view: "e4" },
        points: [{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 100 }, { x: 200, y: 100 }],
      },
      {
        id: "route-b", layerId: "top", kind: "wire", label: "B",
        x: 0, y: 0, width: 0, height: 0, color: "#00c", metadata: { view: "e4" },
        points: [{ x: 0, y: 20 }, { x: 80, y: 20 }, { x: 80, y: 120 }, { x: 200, y: 120 }],
      },
    ];
    const base = { id: "screen", wireIds: ["route-a", "route-b"], label: "SH", width: 18 };
    expect(getE4ScreenLayout({ ...base, position: 0.1 }, routed)).toMatchObject({
      center: { x: 20, y: 10 }, orientation: "horizontal", pathLength: 200,
    });
    expect(getE4ScreenLayout({ ...base, position: 0.5 }, routed)).toMatchObject({
      center: { x: 100, y: 60 }, orientation: "horizontal", pathLength: 200,
    });
    expect(getE4ScreenLayout({ ...base, position: 0.9 }, routed)).toMatchObject({
      center: { x: 180, y: 110 }, orientation: "horizontal", pathLength: 200,
    });
  });

  it("follows the first wire traversal direction across a reversed multi-bend screen path", () => {
    const routed: readonly EditorSceneObject[] = [
      {
        id: "route-a", layerId: "top", kind: "wire", label: "A",
        x: 0, y: 0, width: 0, height: 0, color: "#c00", metadata: { view: "e4" },
        points: [{ x: 200, y: 100 }, { x: 100, y: 100 }, { x: 100, y: 0 }, { x: 0, y: 0 }],
      },
      {
        id: "route-b", layerId: "top", kind: "wire", label: "B",
        x: 0, y: 0, width: 0, height: 0, color: "#00c", metadata: { view: "e4" },
        points: [{ x: 200, y: 120 }, { x: 80, y: 120 }, { x: 80, y: 20 }, { x: 0, y: 20 }],
      },
    ];
    const base = { id: "screen", wireIds: ["route-a", "route-b"], label: "SH", width: 18 };
    expect(getE4ScreenLayout({ ...base, position: 0.1 }, routed)).toMatchObject({
      center: { x: 180, y: 110 }, orientation: "horizontal", pathLength: 200,
    });
    expect(getE4ScreenLayout({ ...base, position: 0.5 }, routed)).toMatchObject({
      center: { x: 100, y: 110 }, orientation: "horizontal", pathLength: 200,
    });
    expect(getE4ScreenLayout({ ...base, position: 0.9 }, routed)).toMatchObject({
      center: { x: 20, y: 10 }, orientation: "horizontal", pathLength: 200,
    });
  });

  it("accepts model overlay metadata and drops malformed groups", () => {
    const carrier: EditorSceneObject = {
      id: "W1", layerId: "top", kind: "wire", label: "W1", x: 0, y: 0, width: 0, height: 0,
      color: "#222222", points: [{ x: 0, y: 0 }, { x: 100, y: 0 }],
      metadata: {
        view: "e4",
        crossingStyle: "bridge",
        junctions: JSON.stringify([{ id: "j1", position: { x: 50, y: 0 }, wireIds: ["W1", "W2"] }]),
        diffPairs: JSON.stringify([{ id: "d1", wireIds: ["W1", "W2"], step: 25, amplitude: 5 }]),
        screens: JSON.stringify([{ id: "s1", wireIds: ["W1"], position: 0.5, label: "SH", width: 18 }, { id: "bad", wireIds: [], position: 5, width: 0 }]),
      },
    };
    const parsed = parseE4SceneOverlays([carrier]);
    expect(parsed.crossingStyle).toBe("bridge");
    expect(parsed.junctions).toHaveLength(1);
    expect(parsed.diffPairs).toHaveLength(1);
    expect(parsed.screens.map((screen) => screen.id)).toEqual(["s1"]);
  });

  it("updates immutable scene and layer state without changing unrelated entries", () => {
    const edited = updateEditorObject(objects, "upper", { label: "XP1", x: 45 });
    expect(edited).not.toBe(objects);
    expect(edited[0]).toBe(objects[0]);
    expect(edited[1]).toMatchObject({ id: "upper", label: "XP1", x: 45 });

    const locked = toggleLayerLock(layers, "bottom");
    expect(locked[0]).toBe(layers[0]);
    expect(locked[1]).toMatchObject({ id: "bottom", locked: true });
    expect(moveLayer(layers, "missing", 0)).toBe(layers);
  });
});

it("releases drawing catalogue space while retaining document actions and E4 catalogue",()=>{
 const props={harnessId:"test",harnessDesignation:"TEST",documentActions:createElement("button",null,"Соединения")};
 const drawing=renderToStaticMarkup(createElement(HarnessEditorWorkspace,{...props,view:"drawing"}));
 expect(drawing).toContain("Соединения");expect(drawing).not.toContain('class="he-catalog');
 const e4=renderToStaticMarkup(createElement(HarnessEditorWorkspace,{...props,view:"e4"}));
 expect(e4).toContain('class="he-catalog');
});
it("keeps detached wire end controls reachable at the top of local copy properties",()=>{
 const freeWire:EditorSceneObject={id:"free-wire",layerId:"bottom",kind:"wire",label:"W-FREE",x:0,y:0,width:0,height:0,color:"#c00",points:[{x:0,y:0},{x:100,y:0}],metadata:{freeFrom:"true",freeTo:"false"},drawingEndStyles:{from:"copper",to:"sealed"}};
 const relation=createElement("section",{"aria-label":"Large relation panel"},"Relation content");
 const html=renderToStaticMarkup(createElement(HarnessEditorWorkspace,{
  harnessId:"assembly-copy",harnessDesignation:"COPY",view:"drawing",objects:[freeWire],layers,
  selectedObjectId:"free-wire",selectedObjectIds:["free-wire"],relationPanel:relation,
  localCopyControls:{hiddenObjectIds:[],backgroundOpacity:0,onHiddenObjectIdsChange:vi.fn(),onBackgroundOpacityChange:vi.fn(),onCancel:vi.fn()},
  onDrawingEndStyleChange:vi.fn(),onDrawingEndStylesChange:vi.fn(),onDrawingEndEndpointChange:vi.fn(),
 }));
 expect(html).toContain('aria-selected="true" class="active">Свойства</button>');
 expect(html).toContain('aria-label="Оконцовки свободных проводов"');
 expect(html.indexOf('aria-label="Оконцовки свободных проводов"')).toBeLessThan(html.indexOf('aria-label="Large relation panel"'));
 expect(html).toContain('aria-label="W-FREE: режим from"');
});
it("marks the actual drawing contact coordinates at a constant screen size and skips absent contacts",()=>{
 const arc=vi.fn(),context={save:vi.fn(),restore:vi.fn(),beginPath:vi.fn(),arc,fill:vi.fn(),stroke:vi.fn()} as unknown as CanvasRenderingContext2D;
 const connector:EditorSceneObject={id:"A",kind:"connector",layerId:"top",label:"A",x:100,y:200,width:60,height:90,color:"#123456",metadata:{contactCount:"3",materializedContactPoints:JSON.stringify([null,{x:-20,y:30,direction:"left",status:"available"},{x:40,y:50,direction:"right",status:"available"}])}};
 drawSelectedConnectorContacts(context,connector,"drawing",2);
 expect(arc.mock.calls.map(a=>a.slice(0,3))).toEqual([[80,230,3],[80,230,1],[140,250,3],[140,250,1]]);
});
it("renders an exit as a neutral point without a spurious directional sleeve",()=>{
 const lineTo=vi.fn(),arc=vi.fn(),context={save:vi.fn(),restore:vi.fn(),beginPath:vi.fn(),arc,lineTo,fill:vi.fn(),stroke:vi.fn()} as unknown as CanvasRenderingContext2D;
 drawEditorSceneObject(context,{id:"exit",kind:"physical-node",layerId:"top",label:"Выход",x:95,y:195,width:10,height:10,color:"#123456",metadata:{arms:'[{"x":0,"y":10}]'}},true,"drawing");
 expect(lineTo).not.toHaveBeenCalled();expect(arc.mock.calls[0]!.slice(0,3)).toEqual([100,200,5]);
});
