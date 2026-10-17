import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  TemplateCanvasV2,
  applyTemplatePointAngleModeV2,
  clientPointToTemplateCoordinatesV2,
  completedTemplateNodeDragV2,
  completedTemplateNodePointDragV2,
  createTemplateNumericEvaluatorV2,
  hitTestTemplateSegmentsV2,
  nodePointToTemplatePointV2,
  projectPointToTemplateSegmentV2,
  roundedPolylinePathV2,
  snapTemplatePointAngleV2,
  templatePointToNodePointV2,
  templateRootDragPreviewV2,
  type TemplateCanvasV2Props,
} from "./TemplateCanvasV2";
import type {
  NumericExpressionV2,
  TemplateContentV2,
  TemplateNodeV2,
  TransformV2,
} from "./template-model-v2";

const ids = {
  view: "00000000-0000-4000-8000-000000000001",
  drawing: "00000000-0000-4000-8000-000000000002",
  layer: "00000000-0000-4000-8000-000000000003",
  hiddenLayer: "00000000-0000-4000-8000-000000000004",
  drawingLayer: "00000000-0000-4000-8000-000000000005",
  line: "00000000-0000-4000-8000-000000000006",
  hidden: "00000000-0000-4000-8000-000000000007",
  rectangle: "00000000-0000-4000-8000-000000000008",
  ellipse: "00000000-0000-4000-8000-000000000009",
  text: "00000000-0000-4000-8000-00000000000a",
  image: "00000000-0000-4000-8000-00000000000b",
  asset: "00000000-0000-4000-8000-00000000000c",
  parameter: "00000000-0000-4000-8000-00000000000d",
  formula: "00000000-0000-4000-8000-00000000000e",
  missingParameter: "00000000-0000-4000-8000-00000000000f",
  group: "00000000-0000-4000-8000-000000000010",
  child: "00000000-0000-4000-8000-000000000011",
  sibling: "00000000-0000-4000-8000-000000000012",
  contact: "00000000-0000-4000-8000-000000000013",
  point: "00000000-0000-4000-8000-000000000014",
} as const;

const c = (value: number): NumericExpressionV2 => ({ kind: "constant", value });
const p = (parameterId: string): NumericExpressionV2 => ({ kind: "parameter", parameterId });
const identity: TransformV2 = {
  translateX: c(0), translateY: c(0), rotationDegrees: c(0), scaleX: c(1), scaleY: c(1),
};

function node(partial: Partial<TemplateNodeV2> & Pick<TemplateNodeV2, "id" | "kind" | "geometry">): TemplateNodeV2 {
  return {
    layerId: ids.layer,
    visible: true,
    locked: false,
    opacity: 1,
    transform: identity,
    stroke: { color: "#123456", width: c(2) },
    fill: { color: null },
    ...partial,
  } as TemplateNodeV2;
}

function line(id: string, x = 0, visible = true): TemplateNodeV2 {
  return node({
    id,
    kind: "line",
    visible,
    geometry: { points: [{ x: c(x), y: c(10) }, { x: c(x + 20), y: c(10) }], bendRadius: c(0) },
  });
}

function content(nodes: TemplateNodeV2[]): TemplateContentV2 {
  return {
    schemaVersion: 2,
    views: [
      {
        id: ids.view,
        name: "Схема Э4",
        kind: "e4",
        layers: [
          { id: ids.layer, name: "Основной", visible: true, locked: false, nodes },
          { id: ids.hiddenLayer, name: "Скрытый", visible: false, locked: false, nodes: [{ ...line(ids.text), layerId: ids.hiddenLayer }] },
        ],
        contactPoints: [],
        bundlePorts: [],
        repeatPlacements: [],
      },
      {
        id: ids.drawing,
        name: "Чертёж",
        kind: "drawing",
        layers: [{ id: ids.drawingLayer, name: "Основной", visible: true, locked: false, nodes: [] }],
        contactPoints: [],
        bundlePorts: [],
        repeatPlacements: [],
      },
    ],
    logicalContacts: [],
    parameters: [
      { id: ids.parameter, name: "Размер", type: "number", unit: null, defaultValue: 25, minimum: null, maximum: null, formula: null },
      { id: ids.formula, name: "Удвоенный", type: "number", unit: null, defaultValue: 0, minimum: null, maximum: null, formula: { kind: "binary", operator: "multiply", left: p(ids.parameter), right: c(2) } },
    ],
    repeaters: [],
    assets: [{ assetId: ids.asset, fileName: "part.png", mediaType: "image/png", sha256: "a".repeat(64), sizeBytes: 100 }],
    articleParameterPresets: [],
  };
}

function render(document: TemplateContentV2, overrides: Partial<TemplateCanvasV2Props> = {}): string {
  return renderToStaticMarkup(createElement(TemplateCanvasV2, {
    content: document,
    viewId: ids.view,
    selectedId: null,
    onSelect: () => undefined,
    resolveAssetUrl: assetId => `/templates/7/assets/${assetId}/content`,
    ...overrides,
  }));
}

describe("TemplateCanvasV2", () => {
  it("converts client coordinates through the fitted SVG viewBox including letterboxing", () => {
    expect(clientPointToTemplateCoordinatesV2(
      { left: 10, top: 20, width: 360, height: 440 },
      190,
      240,
      720,
      440,
    )).toEqual({ x: 360, y: 220 });
    expect(clientPointToTemplateCoordinatesV2(
      { left: 10, top: 20, width: 360, height: 440 },
      60,
      140,
      720,
      440,
    )).toEqual({ x: 100, y: 20 });
    expect(clientPointToTemplateCoordinatesV2(
      { left: 10, top: 20, width: 1440, height: 440 },
      370,
      240,
      720,
      440,
    )).toEqual({ x: 0, y: 220 });
    expect(clientPointToTemplateCoordinatesV2(
      { left: 0, top: 0, width: 0, height: 100 },
      10,
      10,
      720,
      440,
    )).toBeNull();
  });

  it("commits only a completed drag beyond the client-pixel threshold", () => {
    const start = { clientX: 10, clientY: 20, templateX: 100, templateY: 200 };
    expect(completedTemplateNodeDragV2(start, {
      clientX: 12,
      clientY: 22,
      templateX: 104,
      templateY: 204,
    })).toBeNull();
    expect(completedTemplateNodeDragV2(start, {
      clientX: 13,
      clientY: 24,
      templateX: 106,
      templateY: 208,
    })).toEqual({ deltaX: 6, deltaY: 8 });
  });

  it("renders visible layers and nodes in array order and preserves node opacity", () => {
    const first = line(ids.line, 10);
    first.opacity = 0.4;
    const hidden = line(ids.hidden, 20, false);
    const last = node({
      id: ids.ellipse,
      kind: "ellipse",
      geometry: { centerX: c(80), centerY: c(50), radiusX: c(12), radiusY: c(8) },
    });
    const markup = render(content([first, hidden, last]));

    expect(markup).toContain(`data-template-layer-id="${ids.layer}"`);
    expect(markup).not.toContain(ids.hiddenLayer);
    expect(markup).not.toContain(ids.hidden);
    expect(markup.indexOf(ids.line)).toBeLessThan(markup.indexOf(ids.ellipse));
    expect(markup).toContain(`data-template-node-id="${ids.line}"`);
    expect(markup).toContain('opacity="0.4"');
  });

  it("projects a click onto a segment and returns the nearest segment hit", () => {
    expect(projectPointToTemplateSegmentV2(
      { x: 7, y: 4 }, { x: 0, y: 0 }, { x: 10, y: 0 },
    )).toEqual({ x: 7, y: 0, t: 0.7, distance: 4 });
    expect(projectPointToTemplateSegmentV2(
      { x: 20, y: 4 }, { x: 0, y: 0 }, { x: 10, y: 0 },
    )).toEqual({ x: 10, y: 0, t: 1, distance: Math.hypot(10, 4) });
    expect(hitTestTemplateSegmentsV2(
      [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 20 }],
      { x: 12, y: 8 },
      3,
    )).toEqual({ x: 10, y: 8, t: 0.4, distance: 2, segmentIndex: 1 });
    expect(hitTestTemplateSegmentsV2(
      [{ x: 0, y: 0 }, { x: 10, y: 0 }], { x: 5, y: 8 }, 4,
    )).toBeNull();
  });

  it("converts a completed point gesture into local node coordinates", () => {
    const start = { clientX: 0, clientY: 0, templateX: 100, templateY: 50 };
    const end = { clientX: 0, clientY: 10, templateX: 100, templateY: 70 };
    expect(completedTemplateNodePointDragV2(start, end, 90, 2, 4)).toEqual({ deltaX: 10, deltaY: 0 });
    expect(completedTemplateNodePointDragV2(start, { ...end, clientY: 2 }, 90, 2, 4)).toBeNull();
    expect(templatePointToNodePointV2(100, 80, 100, 40, 90, 2, 4)).toEqual({ x: 20, y: 0 });
  });

  it("switches point dragging between free movement and 15-degree angular snapping", () => {
    const origin = { x: 10, y: 0 };
    const delta = { deltaX: 0, deltaY: 2 };
    const anchor = { x: 0, y: 0 };

    expect(applyTemplatePointAngleModeV2(origin, delta, anchor, "free")).toEqual(delta);
    const snapped = applyTemplatePointAngleModeV2(origin, delta, anchor, "snap-15");
    const radius = Math.hypot(10, 2);
    expect(snapped?.deltaX).toBeCloseTo(Math.cos(Math.PI / 12) * radius - 10);
    expect(snapped?.deltaY).toBeCloseTo(Math.sin(Math.PI / 12) * radius);
  });

  it("keeps angular snapping stable for axis-aligned, zero-length, and invalid points", () => {
    expect(snapTemplatePointAngleV2({ x: 4, y: 8 }, { x: 14, y: 8 }, "snap-15"))
      .toEqual({ x: 14, y: 8 });
    expect(snapTemplatePointAngleV2({ x: 4, y: 8 }, { x: 4, y: 8 }, "snap-15"))
      .toEqual({ x: 4, y: 8 });
    expect(snapTemplatePointAngleV2({ x: 0, y: 0 }, { x: Number.NaN, y: 1 }, "snap-15"))
      .toBeNull();
    expect(applyTemplatePointAngleModeV2(
      { x: 1, y: 2 }, { deltaX: 3, deltaY: 4 }, null, "snap-15",
    )).toEqual({ deltaX: 3, deltaY: 4 });
  });

  it("snaps in visible view coordinates after rotation and non-uniform scale", () => {
    const transform = { translateX: 7, translateY: 9, rotationDegrees: 30, scaleX: 2, scaleY: 0.5 };
    const origin = nodePointToTemplatePointV2([10, 0], transform);
    const anchor = nodePointToTemplatePointV2([0, 0], transform);
    const snapped = applyTemplatePointAngleModeV2(origin, { deltaX: 3, deltaY: 8 }, anchor, "snap-15")!;
    const angle = Math.atan2(origin.y + snapped.deltaY - anchor.y, origin.x + snapped.deltaX - anchor.x) * 180 / Math.PI;
    expect(angle / 15).toBeCloseTo(Math.round(angle / 15));
  });

  it("adds forgiving hit targets and Visio-style handles for selected simple figures", () => {
    const rectangle = node({
      id: ids.rectangle,
      kind: "rectangle",
      geometry: { x: c(10), y: c(20), width: c(80), height: c(40), cornerRadii: [c(0), c(0), c(0), c(0)] },
    });
    const markup = render(content([rectangle]), { selectedId: ids.rectangle, onNodeResize: () => undefined });

    expect(markup).toContain('data-shape-hit-region="true"');
    expect(markup).toContain('fill="transparent" stroke="transparent" stroke-width="2"');
    expect(markup).toContain('data-selection-kind="box"');
    expect(markup.match(/data-resize-handle=/g)).toHaveLength(4);
    expect(markup).toContain('data-resize-handle="nw"');
    expect(markup).toContain('data-resize-handle="se"');
  });

  it("shows two endpoint handles and the selected dash style for a line", () => {
    const dashed = line(ids.line);
    dashed.stroke = { ...dashed.stroke, dash: "dash-dot" };
    const markup = render(content([dashed]), { selectedId: ids.line, onNodeResize: () => undefined });

    expect(markup).toContain('stroke-width="12"');
    expect(markup).toContain('stroke-dasharray="12 6 2 6"');
    expect(markup).toContain('data-selection-kind="line"');
    expect(markup.match(/data-resize-handle=/g)).toHaveLength(2);
  });

  it("hides single-object editing handles while several roots are selected", () => {
    const rectangle = node({
      id: ids.rectangle,
      kind: "rectangle",
      geometry: { x: c(10), y: c(20), width: c(80), height: c(40), cornerRadii: [c(0), c(0), c(0), c(0)] },
    });
    const sibling = line(ids.line);
    const markup = render(content([rectangle, sibling]), {
      selectedId: ids.rectangle,
      selectedIds: [ids.rectangle, ids.line],
      onNodeResize: () => undefined,
    });

    expect(markup).not.toContain("data-resize-handle");
    expect(markup.match(/data-selected="true"/g)).toHaveLength(2);
    expect(markup).toContain('data-selection-kind="multi"');
    expect(markup).toContain('data-multi-selection-bounds="true"');
  });

  it("bounds evaluated root geometry and recursively transformed group children in view coordinates", () => {
    const child = node({
      id: ids.child,
      kind: "rectangle",
      transform: { ...identity, translateX: c(5), translateY: c(6) },
      geometry: { x: c(0), y: c(0), width: c(10), height: c(20), cornerRadii: [c(0), c(0), c(0), c(0)] },
    });
    const group = node({
      id: ids.group,
      kind: "group",
      transform: { translateX: c(100), translateY: c(50), rotationDegrees: c(90), scaleX: c(2), scaleY: c(1) },
      geometry: { childIds: [ids.child] },
    });
    const evaluated = node({
      id: ids.rectangle,
      kind: "rectangle",
      geometry: { x: c(130), y: c(10), width: p(ids.parameter), height: c(10), cornerRadii: [c(0), c(0), c(0), c(0)] },
    });
    const markup = render(content([group, child, evaluated]), {
      selectedId: ids.group,
      selectedIds: [ids.group, ids.rectangle],
    });

    expect(markup).toContain('data-selection-kind="multi"');
    expect(markup).toContain('data-multi-selection-bounds="true" x="74" y="10" width="81" height="70"');
    expect(markup).not.toContain("data-resize-handle");
  });

  it("previews one drag delta on every selected root without moving nested children twice", () => {
    const selected = new Set([ids.group, ids.rectangle]);
    const drag = { id: ids.group, deltaX: 12, deltaY: -8 };

    expect(templateRootDragPreviewV2(ids.group, true, drag, selected)).toEqual({ deltaX: 12, deltaY: -8 });
    expect(templateRootDragPreviewV2(ids.rectangle, true, drag, selected)).toEqual({ deltaX: 12, deltaY: -8 });
    expect(templateRootDragPreviewV2(ids.child, false, drag, selected)).toBeNull();
    expect(templateRootDragPreviewV2(ids.line, true, drag, selected)).toBeNull();
  });

  it("shows editable handles for every constant line point and segment insertion hit targets", () => {
    const routed = node({
      id: ids.line,
      kind: "line",
      geometry: {
        points: [
          { x: c(10), y: c(10) },
          { x: c(40), y: c(10) },
          { x: c(40), y: c(50) },
        ],
        bendRadius: c(0),
      },
    });
    const markup = render(content([routed]), {
      selectedId: ids.line,
      onNodePointMove: () => undefined,
      onNodePointDelete: () => undefined,
      onNodePointInsert: () => undefined,
    });

    expect(markup).toContain('data-selection-kind="line"');
    expect(markup.match(/data-point-handle=/g)).toHaveLength(3);
    expect(markup.match(/data-point-segment=/g)).toHaveLength(2);
    expect(markup).toContain('data-point-handle="1"');
  });

  it("shows bezier anchors and control handles but hides handles for locked or parameterized nodes", () => {
    const bezier = node({
      id: ids.line,
      kind: "bezier",
      geometry: {
        points: [
          { x: c(10), y: c(10) }, { x: c(20), y: c(0) },
          { x: c(30), y: c(20) }, { x: c(40), y: c(10) },
        ],
        closed: false,
      },
    });
    const callbacks = { selectedId: ids.line, onNodePointMove: () => undefined };
    const editableMarkup = render(content([bezier]), callbacks);
    expect(editableMarkup.match(/data-point-handle=/g)).toHaveLength(4);
    expect(editableMarkup.match(/data-point-role="control"/g)).toHaveLength(2);
    expect(editableMarkup.match(/data-point-guide=/g)).toHaveLength(2);

    if (bezier.kind !== "bezier") throw new Error("Expected bezier node.");
    bezier.locked = true;
    expect(render(content([bezier]), callbacks)).not.toContain("data-point-handle");
    bezier.locked = false;
    bezier.geometry.points[1]!.x = p(ids.parameter);
    expect(render(content([bezier]), callbacks)).not.toContain("data-point-handle");
  });

  it("exposes every closed-contour edge, including the closing edge, for point editing", () => {
    const contour = node({
      id: ids.line,
      kind: "closedContour",
      geometry: {
        points: [
          { x: c(10), y: c(10) }, { x: c(50), y: c(10) },
          { x: c(50), y: c(40) }, { x: c(10), y: c(40) },
        ],
      },
    });
    const markup = render(content([contour]), {
      selectedId: ids.line,
      onNodePointMove: () => undefined,
      onNodePointDelete: () => undefined,
      onNodePointInsert: () => undefined,
    });

    expect(markup).toContain('data-selection-kind="closedContour"');
    expect(markup.match(/data-point-handle=/g)).toHaveLength(4);
    expect(markup.match(/data-point-segment=/g)).toHaveLength(4);
    expect(markup).toContain('data-point-segment="3"');
  });

  it("hides resize handles for parameters and exposes them after rotation", () => {
    const parameterized = node({
      id: ids.rectangle,
      kind: "rectangle",
      geometry: { x: c(10), y: c(20), width: p(ids.parameter), height: c(40), cornerRadii: [c(0), c(0), c(0), c(0)] },
    });
    expect(render(content([parameterized]), { selectedId: ids.rectangle, onNodeResize: () => undefined })).not.toContain("data-resize-handle");
    const rotated = node({
      id: ids.rectangle,
      kind: "rectangle",
      transform: { ...identity, rotationDegrees: c(15) },
      geometry: { x: c(10), y: c(20), width: c(80), height: c(40), cornerRadii: [c(0), c(0), c(0), c(0)] },
    });
    expect(render(content([rotated]), { selectedId: ids.rectangle, onNodeResize: () => undefined })).toContain("data-resize-handle");
  });

  it("evaluates parameter defaults, formulas and explicit overrides without eval", () => {
    const document = content([]);
    const evaluateDefaults = createTemplateNumericEvaluatorV2(document);
    const evaluateOverrides = createTemplateNumericEvaluatorV2(document, { [ids.parameter]: 30 });

    expect(evaluateDefaults(p(ids.formula))).toBe(50);
    expect(evaluateOverrides(p(ids.formula))).toBe(60);
    expect(evaluateDefaults({ kind: "binary", operator: "divide", left: c(1), right: c(0) })).toBeNull();
  });

  it("renders parameterized base geometry when defaults resolve and uses a stable non-interactive placeholder otherwise", () => {
    const rectangle = node({
      id: ids.rectangle,
      kind: "rectangle",
      geometry: { x: c(10), y: c(20), width: p(ids.parameter), height: c(30), cornerRadii: [c(1), c(2), c(3), c(4)] },
    });
    const unresolved = node({
      id: ids.ellipse,
      kind: "ellipse",
      geometry: { centerX: c(30), centerY: c(30), radiusX: p(ids.missingParameter), radiusY: c(5) },
    });
    const first = render(content([rectangle, unresolved]), { parameterDefaults: { [ids.parameter]: 40 } });
    const second = render(content([rectangle, unresolved]), { parameterDefaults: { [ids.parameter]: 40 } });

    expect(first).toContain(`data-template-node-id="${ids.rectangle}"`);
    expect(first).toContain("H 48");
    expect(first).toContain(`data-template-node-id="${ids.ellipse}"`);
    expect(first).toContain('data-render="placeholder"');
    expect(first).toContain('pointer-events="none"');
    expect(first).toBe(second);
  });

  it("renders a rounded polyline as tangent circular arcs", () => {
    const rounded = node({
      id: ids.rectangle,
      kind: "polyline",
      geometry: {
        points: [{ x: c(10), y: c(10) }, { x: c(30), y: c(10) }, { x: c(30), y: c(30) }],
        bendRadius: c(5),
      },
    });
    const markup = render(content([rounded]));

    expect(markup).toContain(`data-template-node-id="${ids.rectangle}"`);
    expect(markup).not.toContain('data-render="placeholder"');
    expect(markup).toContain('d="M 10 10 L 25 10 A 5 5 0 0 1 30 15 L 30 30"');
    expect(markup).not.toContain("<polyline");
  });

  it("caps a rounded corner by both adjacent segment lengths", () => {
    expect(roundedPolylinePathV2([[0, 0], [20, 0], [20, 20]], 100)).toBe(
      "M 0 0 L 10 0 A 10 10 0 0 1 20 10 L 20 20",
    );
  });

  it("keeps radius-zero polylines sharp and does not alter collinear vertices", () => {
    const sharp = node({
      id: ids.rectangle,
      kind: "polyline",
      geometry: {
        points: [{ x: c(0), y: c(0) }, { x: c(20), y: c(0) }, { x: c(20), y: c(20) }],
        bendRadius: c(0),
      },
    });
    expect(render(content([sharp]))).toContain('<polyline');
    expect(roundedPolylinePathV2([[0, 0], [20, 0], [40, 0]], 5)).toBe("M 0 0 L 20 0 L 40 0");
  });

  it("renders an owned child exactly once inside its group at the group position", () => {
    const child = line(ids.child, 10);
    const sibling = line(ids.sibling, 40);
    const group = node({ id: ids.group, kind: "group", geometry: { childIds: [ids.child] } });
    const markup = render(content([child, sibling, group]));

    expect(markup.match(new RegExp(ids.child, "g"))).toHaveLength(1);
    expect(markup.indexOf(ids.sibling)).toBeLessThan(markup.indexOf(ids.group));
    expect(markup.indexOf(ids.group)).toBeLessThan(markup.indexOf(ids.child));
    expect(markup).toContain('data-template-group="true"');
  });

  it("exposes drag only for movable top-level nodes and groups", () => {
    const movable = line(ids.line, 0);
    const locked = line(ids.sibling, 30);
    locked.locked = true;
    const child = line(ids.child, 60);
    const group = node({ id: ids.group, kind: "group", geometry: { childIds: [ids.child] } });
    const markup = render(content([movable, locked, child, group]), { onNodeMove: () => undefined });

    expect(markup.match(/data-draggable="true"/g)).toHaveLength(2);
    expect(markup).toContain(`data-template-node-id="${ids.sibling}"`);
    expect(markup).toContain('data-locked="true"');
    expect(markup).not.toMatch(new RegExp(`data-template-node-id="${ids.sibling}"[^>]*pointer-events="none"`));
    expect(markup).toMatch(new RegExp(`data-template-node-id="${ids.group}"[^>]*data-draggable="true"[^>]*data-template-group="true"`));
    expect(markup.indexOf(`data-template-node-id="${ids.child}" data-template-node-kind="line" data-draggable`)).toBe(-1);
  });

  it("makes a nested group the single draggable root and preserves explicit canvas fitting", () => {
    const child = line(ids.child, 60);
    const nestedId = crypto.randomUUID();
    const nested = node({ id: nestedId, kind: "group", geometry: { childIds: [ids.child] } });
    const root = node({ id: ids.group, kind: "group", geometry: { childIds: [nestedId] } });
    const markup = render(content([child, nested, root]), { onNodeMove: () => undefined });

    expect(markup.match(/data-draggable="true"/g)).toHaveLength(1);
    expect(markup).toContain('preserveAspectRatio="xMidYMid meet"');
    expect(markup).toMatch(new RegExp(`data-template-node-id="${ids.group}"[^>]*data-draggable="true"`));
  });

  it("renders versioned image URLs, normalized crop, transform and underlay metadata without reordering nodes", () => {
    const before = line(ids.line, 0);
    const image = node({
      id: ids.image,
      kind: "image",
      opacity: 0.65,
      transform: { translateX: c(5), translateY: c(6), rotationDegrees: c(15), scaleX: c(2), scaleY: c(3) },
      geometry: { assetId: ids.asset, x: c(10), y: c(20), width: c(100), height: c(80), cropX: 0.1, cropY: 0.2, cropWidth: 0.7, cropHeight: 0.6, underlay: true },
    });
    const resolver = vi.fn((assetId: string) => `/component-templates/template/versions/7/assets/${assetId}/content`);
    const markup = render(content([before, image]), { resolveAssetUrl: resolver });

    expect(resolver).toHaveBeenCalledOnce();
    expect(resolver).toHaveBeenCalledWith(ids.asset);
    expect(markup.indexOf(ids.line)).toBeLessThan(markup.indexOf(ids.image));
    expect(markup).toContain('data-underlay="true"');
    expect(markup).toContain('transform="translate(5 6) rotate(15) scale(2 3)"');
    expect(markup).toContain('viewBox="0.1 0.2 0.7 0.6"');
    expect(markup).toContain(`href="/component-templates/template/versions/7/assets/${ids.asset}/content"`);
    expect(markup).toContain('preserveAspectRatio="none"');
  });

  it("renders linked contact points after layers", () => {
    const document = content([line(ids.line)]);
    document.logicalContacts.push({ id: ids.contact, number: "X2", name: "Питание", contactType: "power" });
    document.views[0]!.contactPoints.push({ id: ids.point, logicalContactId: ids.contact, x: c(90), y: c(40), direction: "right" });
    const markup = render(document, { selectedId: ids.point });

    expect(markup.indexOf(ids.layer)).toBeLessThan(markup.indexOf(ids.point));
    expect(markup).toContain(`data-template-point-id="${ids.point}"`);
    expect(markup).toContain('data-selected="true"');
    expect(markup).toContain("X2");
    expect(markup).toContain("Контакт X2: Питание; направление right");
    expect(markup).toContain('role="button"');
    expect(markup).toContain('tabindex="0"');
  });

  it("renders a bundle port as a distinct non-electrical diamond", () => {
    const document = content([]);
    const portId = crypto.randomUUID();
    document.views[0]!.bundlePorts.push({ id: portId, name: "Кабель", x: c(120), y: c(80), direction: "up" });

    const markup = render(document, { selectedId: portId });

    expect(markup).toContain(`data-template-point-id="${portId}"`);
    expect(markup).toContain('data-template-point-kind="bundle"');
    expect(markup).toContain('d="M 0 -8 L 8 0 L 0 8 L -8 0 Z"');
    expect(markup).toContain("Общий выход пучка: Кабель; направление up");
    expect(markup).not.toContain("Контакт без номера: Кабель");
  });

  it("renders repeat occurrences instead of an extra prototype group and contact point", () => {
    const document = content([]);
    const countId = crypto.randomUUID();
    const domainId = crypto.randomUUID();
    const child = line(ids.child, 10);
    const group = node({ id: ids.group, kind: "group", geometry: { childIds: [ids.child] } });
    document.views[0]!.layers[0]!.nodes.push(child, group);
    document.logicalContacts.push({ id: ids.contact, number: "1", name: "Контакт", contactType: "signal" });
    document.views[0]!.contactPoints.push({ id: ids.point, logicalContactId: ids.contact, x: c(20), y: c(30), direction: "right" });
    document.parameters.push({ id: countId, name: "Контакты", type: "integer", unit: "шт", defaultValue: 2, minimum: 1, maximum: 10, formula: null });
    document.repeaters.push({ id: domainId, countParameterId: countId, logicalContactIds: [ids.contact] });
    document.views[0]!.repeatPlacements.push({ repeatDomainId: domainId, prototypeGroupId: ids.group, step: { x: c(0), y: c(25) }, contactPointIds: [ids.point] });

    const markup = render(document, { parameterDefaults: { [countId]: 2 } });

    expect(markup.match(/data-template-repeat-index=/g)).toHaveLength(4);
    expect(markup.match(new RegExp(`data-template-node-id="${ids.child}"`, "g"))).toHaveLength(2);
    expect(markup.match(new RegExp(`data-template-point-id="${ids.point}"`, "g"))).toHaveLength(2);
    expect(markup).toContain(">1</text>");
    expect(markup).toContain(">2</text>");
    expect(markup.match(new RegExp(`data-template-node-id="${ids.group}"`, "g"))).toHaveLength(2);
  });

  it("shows an explicit diagnostic instead of silently falling back to repeat prototypes", () => {
    const document = content([]);
    const countId = crypto.randomUUID();
    const domainId = crypto.randomUUID();
    const child = line(ids.child, 10);
    const group = node({ id: ids.group, kind: "group", geometry: { childIds: [ids.child] } });
    document.views[0]!.layers[0]!.nodes.push(child, group);
    document.logicalContacts.push({ id: ids.contact, number: "1", name: "Контакт", contactType: "signal" });
    document.views[0]!.contactPoints.push({ id: ids.point, logicalContactId: ids.contact, x: c(20), y: c(30), direction: "right" });
    document.parameters.push({ id: countId, name: "Контакты", type: "integer", unit: "шт", defaultValue: 0, minimum: 0, maximum: 10, formula: null });
    document.repeaters.push({ id: domainId, countParameterId: countId, logicalContactIds: [ids.contact] });
    document.views[0]!.repeatPlacements.push({ repeatDomainId: domainId, prototypeGroupId: ids.group, step: { x: c(0), y: c(25) }, contactPointIds: [ids.point] });

    const markup = render(document);

    expect(markup).toContain('data-template-repeat-error="true"');
    expect(markup).toContain("Повторы показаны как прототипы");
    expect(markup.match(new RegExp(`data-template-node-id="${ids.child}"`, "g"))).toHaveLength(1);
  });
});
