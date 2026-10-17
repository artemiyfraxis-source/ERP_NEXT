import { contactShapeLabel, contactLabelColor } from "./contact-shape";
import { boxHandleRadius, drawingHandleRadii, spacedHandleBounds, zoomDrawingCamera, fitDrawingCamera, panDrawingCamera, type DrawingCamera } from "./drawing-viewport";
import { nodesInsideSelectionBox, selectionBounds, type SelectionBox } from "./drawing-selection";
import { useId, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { rootNodeRotationCenterV3 } from "./template-commands-v3";
import { hatchTile } from "./drawing-hatch";
import type {
  NumericExpressionV2,
  ParameterValueV2,
  PointExpressionV2,
  TemplateContentV2,
  TemplateNodeV2,
  TemplateViewV2,
  TransformV2,
} from "./template-model-v2";
import { expandTemplateViewRepeatsV2, type RepeatOccurrenceDescriptorV2 } from "./template-repeat-v2";
import type { NodeResizeHandleV2 } from "./template-commands-v2";
import { roundedPolylinePathV2 } from "./rounded-polyline-v2";
import { drawingOutline, drawingLayerOutlines, snapDrawingPoint, snapDrawingResizeDelta, snapDrawingTranslation, type DrawingSnaps } from "./drawing-geometry";
export { roundedPolylinePathV2 } from "./rounded-polyline-v2";

export const TEMPLATE_CANVAS_V2_WIDTH = 720;
export const TEMPLATE_CANVAS_V2_HEIGHT = 440;

export type TemplateParameterDefaultsV2 =
  | Readonly<Record<string, ParameterValueV2>>
  | ReadonlyMap<string, ParameterValueV2>;

export interface TemplateCanvasV2Props {
  /** Read-only navigation: fit the whole drawing, Ctrl+wheel zoom and left-button pan. */
  preview?: boolean;
  previewKey?: string | null;
  content: TemplateContentV2;
  viewId: string;
  selectedId: string | null;
  selectedIds?: readonly string[];
  onSelect: (id: string | null) => void;
  onSelectionChange?: (id: string | null, extend: boolean) => void;
  onBoxSelection?: (ids: readonly string[]) => void;
  onSelectionStretch?: (factor:number,anchor:{x:number;y:number})=>void;
  onSelectionRotate?: (angle: number, center: {x:number;y:number}) => void;
  onEditText?: (id: string) => void;
  onNodeMove?: (id: string, deltaX: number, deltaY: number) => void;
  onNodeResize?: (id: string, handle: NodeResizeHandleV2, deltaX: number, deltaY: number) => void;
  onNodeRotate?: (id: string, rotationDegrees: number) => void;
  snaps?: DrawingSnaps;
  onNodePointMove?: (id: string, pointIndex: number, deltaX: number, deltaY: number) => void;
  onNodePointDelete?: (id: string, pointIndex: number) => void;
  onNodePointInsert?: (id: string, segmentIndex: number, x: number, y: number) => void;
  pointAngleMode?: TemplatePointAngleModeV2;
  resolveAssetUrl: (assetId: string) => string;
  parameterDefaults?: TemplateParameterDefaultsV2;
  repeatedContactLabels?: Readonly<Record<string,{number:string;name:string}|null>>;
  width?: number;
  height?: number;
}

type NumericEvaluator = (expression: NumericExpressionV2) => number | null;
type SvgPoint = readonly [number, number];

interface TemplateMatrixV2 {
  readonly a: number;
  readonly b: number;
  readonly c: number;
  readonly d: number;
  readonly e: number;
  readonly f: number;
}

interface TemplateBoundsV2 {
  readonly left: number;
  readonly top: number;
  readonly right: number;
  readonly bottom: number;
}

const IDENTITY_MATRIX_V2: TemplateMatrixV2 = { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 };

interface DragStateV2 {
  readonly id: string;
  readonly pointerId: number;
  readonly startClientX: number;
  readonly startClientY: number;
  readonly clearOnClick?: boolean;
  readonly start: SvgPoint;
  readonly latest: SvgPoint;
}

interface DragPreviewV2 {
  readonly id: string;
  readonly deltaX: number;
  readonly deltaY: number;
}

export interface TemplateCanvasClientRectV2 {
  readonly left: number;
  readonly top: number;
  readonly width: number;
  readonly height: number;
}

export interface TemplateNodeDragSampleV2 {
  readonly clientX: number;
  readonly clientY: number;
  readonly templateX: number;
  readonly templateY: number;
}

export function clientPointToTemplateCoordinatesV2(
  rect: TemplateCanvasClientRectV2,
  clientX: number,
  clientY: number,
  viewBoxWidth: number,
  viewBoxHeight: number,
): { x: number; y: number } | null {
  if (![rect.left, rect.top, rect.width, rect.height, clientX, clientY, viewBoxWidth, viewBoxHeight].every(Number.isFinite) ||
      rect.width <= 0 || rect.height <= 0 || viewBoxWidth <= 0 || viewBoxHeight <= 0) return null;
  const scale = Math.min(rect.width / viewBoxWidth, rect.height / viewBoxHeight);
  if (!Number.isFinite(scale) || scale <= 0) return null;
  const offsetX = rect.left + (rect.width - viewBoxWidth * scale) / 2;
  const offsetY = rect.top + (rect.height - viewBoxHeight * scale) / 2;
  return { x: (clientX - offsetX) / scale, y: (clientY - offsetY) / scale };
}

export function completedTemplateNodeDragV2(
  start: TemplateNodeDragSampleV2,
  end: TemplateNodeDragSampleV2,
  thresholdInClientPixels = 3,
): { deltaX: number; deltaY: number } | null {
  const values = [
    start.clientX, start.clientY, start.templateX, start.templateY,
    end.clientX, end.clientY, end.templateX, end.templateY, thresholdInClientPixels,
  ];
  if (!values.every(Number.isFinite) || thresholdInClientPixels < 0) return null;
  if (Math.hypot(end.clientX - start.clientX, end.clientY - start.clientY) < thresholdInClientPixels) return null;
  const deltaX = end.templateX - start.templateX;
  const deltaY = end.templateY - start.templateY;
  return deltaX === 0 && deltaY === 0 ? null : { deltaX, deltaY };
}

export function templateRootDragPreviewV2(
  nodeId: string,
  topLevel: boolean,
  drag: { readonly id: string; readonly deltaX: number; readonly deltaY: number } | null,
  selectedIds: ReadonlySet<string>,
): { deltaX: number; deltaY: number } | null {
  if (!topLevel || !drag) return null;
  if (drag.id === nodeId) return { deltaX: drag.deltaX, deltaY: drag.deltaY };
  return selectedIds.size > 1 && selectedIds.has(drag.id) && selectedIds.has(nodeId)
    ? { deltaX: drag.deltaX, deltaY: drag.deltaY }
    : null;
}

export type TemplatePointAngleModeV2 = "free" | "snap-15";

export interface TemplateSegmentProjectionV2 {
  readonly x: number;
  readonly y: number;
  readonly t: number;
  readonly distance: number;
}

export interface TemplateSegmentHitV2 extends TemplateSegmentProjectionV2 {
  readonly segmentIndex: number;
}

export function projectPointToTemplateSegmentV2(
  point: { readonly x: number; readonly y: number },
  start: { readonly x: number; readonly y: number },
  end: { readonly x: number; readonly y: number },
): TemplateSegmentProjectionV2 | null {
  if (![point.x, point.y, start.x, start.y, end.x, end.y].every(Number.isFinite)) return null;
  const segmentX = end.x - start.x;
  const segmentY = end.y - start.y;
  const lengthSquared = segmentX * segmentX + segmentY * segmentY;
  const t = lengthSquared === 0
    ? 0
    : Math.max(0, Math.min(1, ((point.x - start.x) * segmentX + (point.y - start.y) * segmentY) / lengthSquared));
  const x = start.x + segmentX * t;
  const y = start.y + segmentY * t;
  return { x, y, t, distance: Math.hypot(point.x - x, point.y - y) };
}

export function hitTestTemplateSegmentsV2(
  points: readonly { readonly x: number; readonly y: number }[],
  point: { readonly x: number; readonly y: number },
  tolerance: number,
): TemplateSegmentHitV2 | null {
  if (!Number.isFinite(tolerance) || tolerance < 0) return null;
  let nearest: TemplateSegmentHitV2 | null = null;
  for (let segmentIndex = 0; segmentIndex + 1 < points.length; segmentIndex++) {
    const projection = projectPointToTemplateSegmentV2(point, points[segmentIndex]!, points[segmentIndex + 1]!);
    if (projection && projection.distance <= tolerance && (!nearest || projection.distance < nearest.distance))
      nearest = { ...projection, segmentIndex };
  }
  return nearest;
}

export function templateDeltaToNodeDeltaV2(
  deltaX: number,
  deltaY: number,
  rotationDegrees: number,
  scaleX: number,
  scaleY: number,
): { deltaX: number; deltaY: number } | null {
  if (![deltaX, deltaY, rotationDegrees, scaleX, scaleY].every(Number.isFinite) || scaleX === 0 || scaleY === 0) return null;
  const radians = rotationDegrees * Math.PI / 180;
  const cosine = Math.cos(radians);
  const sine = Math.sin(radians);
  const normalize = (value: number) => Math.abs(value) < 1e-12 ? 0 : value;
  return {
    deltaX: normalize((deltaX * cosine + deltaY * sine) / scaleX),
    deltaY: normalize((-deltaX * sine + deltaY * cosine) / scaleY),
  };
}

export function templatePointToNodePointV2(
  x: number,
  y: number,
  translateX: number,
  translateY: number,
  rotationDegrees: number,
  scaleX: number,
  scaleY: number,
): { x: number; y: number } | null {
  const delta = templateDeltaToNodeDeltaV2(x - translateX, y - translateY, rotationDegrees, scaleX, scaleY);
  return delta ? { x: delta.deltaX, y: delta.deltaY } : null;
}

export function nodePointToTemplatePointV2(
  point: readonly [number, number],
  transform: {
    readonly translateX: number; readonly translateY: number;
    readonly rotationDegrees: number; readonly scaleX: number; readonly scaleY: number;
  },
): { x: number; y: number } {
  const radians = transform.rotationDegrees * Math.PI / 180;
  const scaledX = point[0] * transform.scaleX, scaledY = point[1] * transform.scaleY;
  return {
    x: transform.translateX + scaledX * Math.cos(radians) - scaledY * Math.sin(radians),
    y: transform.translateY + scaledX * Math.sin(radians) + scaledY * Math.cos(radians),
  };
}

export function completedTemplateNodePointDragV2(
  start: TemplateNodeDragSampleV2,
  end: TemplateNodeDragSampleV2,
  rotationDegrees: number,
  scaleX: number,
  scaleY: number,
  thresholdInClientPixels = 3,
): { deltaX: number; deltaY: number } | null {
  const completed = completedTemplateNodeDragV2(start, end, thresholdInClientPixels);
  return completed
    ? templateDeltaToNodeDeltaV2(completed.deltaX, completed.deltaY, rotationDegrees, scaleX, scaleY)
    : null;
}

export function snapTemplatePointAngleV2(
  anchor: { readonly x: number; readonly y: number },
  point: { readonly x: number; readonly y: number },
  mode: TemplatePointAngleModeV2,
): { x: number; y: number } | null {
  if (![anchor.x, anchor.y, point.x, point.y].every(Number.isFinite)) return null;
  if (mode === "free") return { x: point.x, y: point.y };
  const deltaX = point.x - anchor.x;
  const deltaY = point.y - anchor.y;
  const radius = Math.hypot(deltaX, deltaY);
  if (radius === 0) return { x: anchor.x, y: anchor.y };
  const step = Math.PI / 12;
  const angle = Math.round(Math.atan2(deltaY, deltaX) / step) * step;
  const normalize = (value: number) => Math.abs(value) < 1e-12 ? 0 : value;
  return {
    x: normalize(anchor.x + Math.cos(angle) * radius),
    y: normalize(anchor.y + Math.sin(angle) * radius),
  };
}

export function applyTemplatePointAngleModeV2(
  origin: { readonly x: number; readonly y: number },
  delta: { readonly deltaX: number; readonly deltaY: number },
  anchor: { readonly x: number; readonly y: number } | null,
  mode: TemplatePointAngleModeV2,
): { deltaX: number; deltaY: number } | null {
  if (![origin.x, origin.y, delta.deltaX, delta.deltaY].every(Number.isFinite)) return null;
  if (mode === "free" || !anchor) return { deltaX: delta.deltaX, deltaY: delta.deltaY };
  const snapped = snapTemplatePointAngleV2(anchor, {
    x: origin.x + delta.deltaX,
    y: origin.y + delta.deltaY,
  }, mode);
  return snapped ? { deltaX: snapped.x - origin.x, deltaY: snapped.y - origin.y } : null;
}

function suppliedDefault(
  defaults: TemplateParameterDefaultsV2 | undefined,
  parameterId: string,
): { found: boolean; value: ParameterValueV2 | undefined } {
  if (!defaults) return { found: false, value: undefined };
  if ("get" in defaults && typeof defaults.get === "function") {
    const map = defaults as ReadonlyMap<string, ParameterValueV2>;
    return { found: map.has(parameterId), value: map.get(parameterId) };
  }
  const record = defaults as Readonly<Record<string, ParameterValueV2>>;
  return { found: Object.prototype.hasOwnProperty.call(record, parameterId), value: record[parameterId] };
}

export function createTemplateNumericEvaluatorV2(
  content: TemplateContentV2,
  defaults?: TemplateParameterDefaultsV2,
): NumericEvaluator {
  const parameters = new Map(content.parameters.map(parameter => [parameter.id, parameter]));
  const cached = new Map<string, number | null>();
  const resolving = new Set<string>();

  function finite(value: unknown): number | null {
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  }

  function parameterValue(parameterId: string): number | null {
    const supplied = suppliedDefault(defaults, parameterId);
    if (supplied.found) return finite(supplied.value);
    if (cached.has(parameterId)) return cached.get(parameterId) ?? null;
    const parameter = parameters.get(parameterId);
    if (!parameter || resolving.has(parameterId)) return null;
    resolving.add(parameterId);
    const result = parameter.formula === null
      ? finite(parameter.defaultValue)
      : evaluate(parameter.formula);
    resolving.delete(parameterId);
    cached.set(parameterId, result);
    return result;
  }

  function evaluate(expression: NumericExpressionV2): number | null {
    if (expression.kind === "constant") return finite(expression.value);
    if (expression.kind === "parameter") return parameterValue(expression.parameterId);
    if (expression.kind === "negate") {
      const operand = evaluate(expression.operand);
      return operand === null ? null : finite(-operand);
    }
    const left = evaluate(expression.left);
    const right = evaluate(expression.right);
    if (left === null || right === null) return null;
    let result: number;
    if (expression.operator === "add") result = left + right;
    else if (expression.operator === "subtract") result = left - right;
    else if (expression.operator === "multiply") result = left * right;
    else {
      if (right === 0) return null;
      result = left / right;
    }
    return finite(result);
  }

  return evaluate;
}

function evaluatePoint(point: PointExpressionV2, evaluate: NumericEvaluator): SvgPoint | null {
  const x = evaluate(point.x);
  const y = evaluate(point.y);
  return x === null || y === null ? null : [x, y];
}

function evaluatePoints(points: readonly PointExpressionV2[], evaluate: NumericEvaluator): SvgPoint[] | null {
  const result: SvgPoint[] = [];
  for (const point of points) {
    const evaluated = evaluatePoint(point, evaluate);
    if (!evaluated) return null;
    result.push(evaluated);
  }
  return result;
}

function evaluateTransform(transform: TransformV2, evaluate: NumericEvaluator): string | null {
  const translateX = evaluate(transform.translateX);
  const translateY = evaluate(transform.translateY);
  const rotation = evaluate(transform.rotationDegrees);
  const scaleX = evaluate(transform.scaleX);
  const scaleY = evaluate(transform.scaleY);
  if (translateX === null || translateY === null || rotation === null || scaleX === null || scaleY === null)
    return null;
  return `translate(${formatNumber(translateX)} ${formatNumber(translateY)}) rotate(${formatNumber(rotation)}) scale(${formatNumber(scaleX)} ${formatNumber(scaleY)})`;
}

function evaluateTransformMatrixV2(transform: TransformV2, evaluate: NumericEvaluator): TemplateMatrixV2 | null {
  const translateX = evaluate(transform.translateX);
  const translateY = evaluate(transform.translateY);
  const rotation = evaluate(transform.rotationDegrees);
  const scaleX = evaluate(transform.scaleX);
  const scaleY = evaluate(transform.scaleY);
  if (translateX === null || translateY === null || rotation === null || scaleX === null || scaleY === null) return null;
  const radians = rotation * Math.PI / 180;
  const cosine = Math.cos(radians);
  const sine = Math.sin(radians);
  return {
    a: cosine * scaleX,
    b: sine * scaleX,
    c: -sine * scaleY,
    d: cosine * scaleY,
    e: translateX,
    f: translateY,
  };
}

function multiplyTemplateMatricesV2(parent: TemplateMatrixV2, child: TemplateMatrixV2): TemplateMatrixV2 {
  return {
    a: parent.a * child.a + parent.c * child.b,
    b: parent.b * child.a + parent.d * child.b,
    c: parent.a * child.c + parent.c * child.d,
    d: parent.b * child.c + parent.d * child.d,
    e: parent.a * child.e + parent.c * child.f + parent.e,
    f: parent.b * child.e + parent.d * child.f + parent.f,
  };
}

function transformTemplatePointV2(matrix: TemplateMatrixV2, x: number, y: number): { x: number; y: number } {
  return { x: matrix.a * x + matrix.c * y + matrix.e, y: matrix.b * x + matrix.d * y + matrix.f };
}

function boundsFromTemplatePointsV2(points: readonly { readonly x: number; readonly y: number }[]): TemplateBoundsV2 | null {
  if (points.length === 0 || points.some(point => !Number.isFinite(point.x) || !Number.isFinite(point.y))) return null;
  return {
    left: Math.min(...points.map(point => point.x)),
    top: Math.min(...points.map(point => point.y)),
    right: Math.max(...points.map(point => point.x)),
    bottom: Math.max(...points.map(point => point.y)),
  };
}

function unionTemplateBoundsV2(bounds: readonly (TemplateBoundsV2 | null)[]): TemplateBoundsV2 | null {
  const present = bounds.filter((item): item is TemplateBoundsV2 => item !== null);
  if (present.length === 0) return null;
  return {
    left: Math.min(...present.map(item => item.left)),
    top: Math.min(...present.map(item => item.top)),
    right: Math.max(...present.map(item => item.right)),
    bottom: Math.max(...present.map(item => item.bottom)),
  };
}

function formatNumber(value: number): string {
  return Object.is(value, -0) ? "0" : String(value);
}

function pointsAttribute(points: readonly SvgPoint[]): string {
  return points.map(([x, y]) => `${formatNumber(x)},${formatNumber(y)}`).join(" ");
}

interface PointDragStateV2 extends DragStateV2 {
  readonly pointIndex: number;
  readonly rotationDegrees: number;
  readonly scaleX: number;
  readonly scaleY: number;
  readonly translateX: number;
  readonly translateY: number;
  readonly origin: SvgPoint;
  readonly angleAnchor: SvgPoint | null;
  readonly angleMode: TemplatePointAngleModeV2;
}

interface PointDragPreviewV2 extends DragPreviewV2 {
  readonly pointIndex: number;
}

interface ResizeStateV2 extends DragStateV2 {
  readonly handle: NodeResizeHandleV2;
}

interface ResizePreviewV2 extends DragPreviewV2 {
  readonly handle: NodeResizeHandleV2;
}

function strokeDasharray(dash: TemplateNodeV2["stroke"]["dash"], width: number): string | undefined {
  const unit = Math.max(width, 1);
  if (dash === "dash") return `${6 * unit} ${4 * unit}`;
  if (dash === "dot") return `${unit} ${3 * unit}`;
  if (dash === "dash-dot") return `${6 * unit} ${3 * unit} ${unit} ${3 * unit}`;
  return undefined;
}

function bezierPath(points: readonly SvgPoint[], closed: boolean): string | null {
  if (points.length < 4 || (points.length - 1) % 3 !== 0) return null;
  let path = `M ${formatNumber(points[0]![0])} ${formatNumber(points[0]![1])}`;
  for (let index = 1; index < points.length; index += 3) {
    const first = points[index]!;
    const second = points[index + 1]!;
    const end = points[index + 2]!;
    path += ` C ${formatNumber(first[0])} ${formatNumber(first[1])} ${formatNumber(second[0])} ${formatNumber(second[1])} ${formatNumber(end[0])} ${formatNumber(end[1])}`;
  }
  return closed ? `${path} Z` : path;
}

function rectanglePath(x: number, y: number, width: number, height: number, radii: readonly number[]): string | null {
  if (width <= 0 || height <= 0 || radii.length !== 4 || radii.some(radius => !Number.isFinite(radius))) return null;
  const maximum = Math.min(width, height) / 2;
  const [topLeft, topRight, bottomRight, bottomLeft] = radii.map(radius => Math.min(Math.max(0, radius), maximum));
  return [
    `M ${formatNumber(x + topLeft!)} ${formatNumber(y)}`,
    `H ${formatNumber(x + width - topRight!)}`,
    `Q ${formatNumber(x + width)} ${formatNumber(y)} ${formatNumber(x + width)} ${formatNumber(y + topRight!)}`,
    `V ${formatNumber(y + height - bottomRight!)}`,
    `Q ${formatNumber(x + width)} ${formatNumber(y + height)} ${formatNumber(x + width - bottomRight!)} ${formatNumber(y + height)}`,
    `H ${formatNumber(x + bottomLeft!)}`,
    `Q ${formatNumber(x)} ${formatNumber(y + height)} ${formatNumber(x)} ${formatNumber(y + height - bottomLeft!)}`,
    `V ${formatNumber(y + topLeft!)}`,
    `Q ${formatNumber(x)} ${formatNumber(y)} ${formatNumber(x + topLeft!)} ${formatNumber(y)}`,
    "Z",
  ].join(" ");
}

function stablePlaceholderPosition(id: string, width: number, height: number): SvgPoint {
  let first = 0x811c9dc5;
  let second = 0x9e3779b9;
  for (let index = 0; index < id.length; index++) {
    const code = id.charCodeAt(index);
    first = Math.imul(first ^ code, 0x01000193);
    second = Math.imul(second ^ code, 0x27d4eb2d);
  }
  const availableWidth = Math.max(1, Math.floor(width - 88));
  const availableHeight = Math.max(1, Math.floor(height - 56));
  return [24 + (first >>> 0) % availableWidth, 24 + (second >>> 0) % availableHeight];
}

function placeholder(
  node: TemplateNodeV2,
  width: number,
  height: number,
  reason: string,
): ReactNode {
  const [x, y] = stablePlaceholderPosition(node.id, width, height);
  return (
    <g
      key={node.id}
      data-template-node-id={node.id}
      data-template-node-kind={node.kind}
      data-render="placeholder"
      pointerEvents="none"
      opacity={node.opacity}
      transform={`translate(${x} ${y})`}
      aria-label={`Не удалось отобразить ${node.kind}`}
    >
      <rect x="-10" y="-10" width="68" height="32" rx="4" fill="#fff7e6" stroke="#a86519" strokeDasharray="4 3" />
      <path d="M 0 10 L 8 -4 L 16 10 Z" fill="none" stroke="#a86519" />
      <text x="22" y="10" fill="#7b4c16" fontSize="10">{node.kind}</text>
      <title>{reason}</title>
    </g>
  );
}

export function TemplateCanvasV2({
  preview = false,
  previewKey,
  content,
  viewId,
  selectedId,
  selectedIds,
  onSelect,
  onSelectionChange,
  onBoxSelection,
  onSelectionStretch,
  onSelectionRotate,
  onEditText,
  onNodeMove,
  onNodeResize,
  onNodeRotate,
  snaps = { corners: false, contours: false, tangents: false },
  onNodePointMove,
  onNodePointDelete,
  onNodePointInsert,
  pointAngleMode = "free",
  resolveAssetUrl,
  parameterDefaults,
  repeatedContactLabels,
  width: defaultWidth = TEMPLATE_CANVAS_V2_WIDTH,
  height: defaultHeight = TEMPLATE_CANVAS_V2_HEIGHT,
}: TemplateCanvasV2Props) {
  const stretchRef=useRef<{pointerId:number;anchor:SvgPoint;start:SvgPoint;factor:number}|null>(null);
  const [stretchPreview,setStretchPreview]=useState<{factor:number;anchor:SvgPoint}|null>(null);
  const marqueeRef = useRef<{pointerId:number;start:SvgPoint;client:SvgPoint;latest:SvgPoint} | null>(null);
  const [marquee,setMarquee] = useState<SelectionBox | null>(null);
  const dragRef = useRef<DragStateV2 | null>(null);
  const hatchPrefix = useId().replaceAll(":", "");
  const resizeRef = useRef<ResizeStateV2 | null>(null);
  const rotationRef = useRef<{ id: string; pointerId: number; center: SvgPoint; startAngle: number; rotation: number; latest: number } | null>(null);
  const [rotationPreview, setRotationPreview] = useState<{ id: string; center: SvgPoint; angle: number } | null>(null);
  const pointDragRef = useRef<PointDragStateV2 | null>(null);
  const [dragPreview, setDragPreview] = useState<DragPreviewV2 | null>(null);
  const [resizePreview, setResizePreview] = useState<ResizePreviewV2 | null>(null);
  const [pointDragPreview, setPointDragPreview] = useState<PointDragPreviewV2 | null>(null);
  const view = content.views.find(candidate => candidate.id === viewId);
  const selectedIdSet = new Set(selectedIds ?? (selectedId ? [selectedId] : []));
  const evaluate = createTemplateNumericEvaluatorV2(content, parameterDefaults);
  const assetIds = new Set(content.assets.map(asset => asset.assetId));
  const svgRef = useRef<SVGSVGElement | null>(null);
  const artworkRef = useRef<SVGGElement | null>(null);
  const fittedCameraRef = useRef<DrawingCamera>({x:0,y:0,zoom:1});
  const fittedGeometryRef = useRef("");
  const panRef = useRef<{pointerId:number;clientX:number;clientY:number;camera:DrawingCamera;screenScale:number}|null>(null);
  const [panning,setPanning] = useState(false);
  const [camera,setCamera]=useState({x:0,y:0,zoom:1});
  const [viewportSize,setViewportSize]=useState({width:defaultWidth,height:defaultHeight});
  const width=preview?viewportSize.width:defaultWidth, height=preview?viewportSize.height:defaultHeight;
  const screenScale=Math.max(.0001,Math.min(viewportSize.width/width,viewportSize.height/height)*camera.zoom);
  useEffect(()=>{if(!preview)setCamera({x:0,y:0,zoom:1});},[viewId,preview]);
  useLayoutEffect(()=>{
    if(!preview || !artworkRef.current)return;
    const bounds=artworkRef.current.getBBox();
    const geometryKey=JSON.stringify([previewKey,viewId,bounds.x,bounds.y,bounds.width,bounds.height,width,height]);
    if(fittedGeometryRef.current===geometryKey)return;
    fittedGeometryRef.current=geometryKey;
    const fitted=fitDrawingCamera(bounds,{width,height});
    fittedCameraRef.current=fitted;
    panRef.current=null;setPanning(false);setCamera(fitted);
  },[preview,previewKey,viewId,content,parameterDefaults,repeatedContactLabels,width,height]);
  useEffect(()=>{
    const svg=svgRef.current;if(!svg)return;
    const observer=new ResizeObserver(()=>{const r=svg.getBoundingClientRect();if(r.width>0&&r.height>0)setViewportSize(previous=>previous.width===r.width&&previous.height===r.height?previous:{width:r.width,height:r.height});});
    observer.observe(svg);
    const wheel=(event:WheelEvent)=>{
      if(!event.ctrlKey || (!onNodeMove && !preview))return;
      event.preventDefault();event.stopPropagation();
      setCamera(previous=>{
        const point=clientPointToTemplateCoordinatesV2(svg.getBoundingClientRect(),event.clientX,event.clientY,width/previous.zoom,height/previous.zoom);
        return point?zoomDrawingCamera(previous,{x:point.x+previous.x,y:point.y+previous.y},event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?height:1),preview?{min:fittedCameraRef.current.zoom/20,max:fittedCameraRef.current.zoom*40}:undefined):previous;
      });
    };
    svg.addEventListener("wheel",wheel,{passive:false});
    return()=>{observer.disconnect();svg.removeEventListener("wheel",wheel);};
  },[width,height,onNodeMove,preview]);

  const snapTolerance = () => { const rect = svgRef.current?.getBoundingClientRect(); return rect && rect.width > 0 && rect.height > 0 ? 7 / screenScale : 7; };
  const outlines = view?.layers.filter(layer => layer.visible).flatMap(layer => drawingLayerOutlines(layer.nodes,evaluate)) ?? [];
  const pointOutlines = [...(view?.contactPoints ?? []), ...(view?.bundlePorts ?? [])].flatMap(point => {
    const x=evaluate(point.x), y=evaluate(point.y);
    return x===null || y===null ? [] : [{id:point.id,points:[{x,y}],closed:false}];
  });
  const targets = (id: string) => [...outlines, ...pointOutlines].filter(outline => outline.id !== id && !selectedIdSet.has(outline.id));
  const snapDelta = (id: string, start: SvgPoint, point: SvgPoint): SvgPoint => {
    const delta = { x: point[0] - start[0], y: point[1] - start[1] };
    const moving = [...outlines,...pointOutlines].filter(item => item.id === id || selectedIdSet.has(id) && selectedIdSet.has(item.id));
    if (moving.length) {
      const candidates = moving.map(outline => snapDrawingTranslation(outline,delta,targets(id),snaps,snapTolerance()))
        .filter(result=>result.x!==delta.x || result.y!==delta.y)
        .sort((a,b)=>Math.hypot(a.x-delta.x,a.y-delta.y)-Math.hypot(b.x-delta.x,b.y-delta.y));
      const result=candidates[0] ?? delta; return [result.x,result.y];
    }
    const contact = view?.contactPoints.find(p => p.id === id) ?? view?.bundlePorts.find(p => p.id === id);
    const x = contact && evaluate(contact.x), y = contact && evaluate(contact.y);
    if (x != null && y != null) { const result = snapDrawingPoint({ x: x + delta.x, y: y + delta.y }, targets(id), snaps, snapTolerance()); return [result.x - x, result.y - y]; }
    return [delta.x, delta.y];
  };
  const logicalContacts = new Map(content.logicalContacts.map(contact => [contact.id, contact]));
  const repeatPreview = new Map<string, readonly RepeatOccurrenceDescriptorV2[]>();
  const repeatedGroupIds = new Set<string>();
  const repeatedPointIds = new Set<string>();
  let repeatPreviewError: string | null = null;
  if (view) try {
    for (const expansion of expandTemplateViewRepeatsV2(content, view.id, { overrides: parameterDefaults })) {
      repeatPreview.set(expansion.prototypeGroupId, expansion.occurrences);
      repeatedGroupIds.add(expansion.prototypeGroupId);
      const placement = view.repeatPlacements.find(item => item.prototypeGroupId === expansion.prototypeGroupId);
      placement?.contactPointIds.forEach(id => repeatedPointIds.add(id));
    }
  } catch (caught) {
    repeatPreviewError = caught instanceof Error ? caught.message : "Повторяемые сегменты не удалось развернуть.";
  }

  useEffect(() => {
    marqueeRef.current=null; setMarquee(null);
    rotationRef.current = null;
    setRotationPreview(null);
    dragRef.current = null;
    resizeRef.current = null;
    pointDragRef.current = null;
    setDragPreview(null);
    setResizePreview(null);
    setPointDragPreview(null);
  }, [viewId]);

  const select = (event: ReactPointerEvent<SVGElement>, id: string, interactive = true) => {
    if (!interactive) return;
    event.stopPropagation();
    const extend = event.ctrlKey || event.metaKey || event.shiftKey;
    if (onSelectionChange) {
      if (!(selectedIdSet.has(id) && !extend)) onSelectionChange(id, extend);
    } else onSelect(id);
  };

  const selectFromKeyboard = (event: ReactKeyboardEvent<SVGElement>, id: string) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    event.stopPropagation();
    if (onSelectionChange) onSelectionChange(id, event.ctrlKey || event.metaKey || event.shiftKey);
    else onSelect(id);
  };

  const pointFromEvent = (event: ReactPointerEvent<SVGElement> | ReactMouseEvent<SVGElement>): SvgPoint | null => {
    const svg = event.currentTarget.ownerSVGElement ??
      (event.currentTarget.tagName.toLowerCase() === "svg" ? event.currentTarget as SVGSVGElement : null);
    if (!svg) return null;
    const point = clientPointToTemplateCoordinatesV2(svg.getBoundingClientRect(), event.clientX, event.clientY, width/camera.zoom, height/camera.zoom);
    return point ? [point.x+camera.x, point.y+camera.y] : null;
  };

  const beginNodeGesture = (event: ReactPointerEvent<SVGElement>, id: string, selectable: boolean, movable: boolean) => {
    if(event.button !== 0) return;
    const clearOnClick = selectedIdSet.has(id) && !(event.ctrlKey || event.metaKey || event.shiftKey);
    select(event, id, selectable);
    if (event.ctrlKey || event.metaKey || event.shiftKey) return;
    if (!movable || !onNodeMove) return;
    const start = pointFromEvent(event);
    const svg = event.currentTarget.ownerSVGElement;
    if (!start || !svg) return;
    try { svg.setPointerCapture(event.pointerId); } catch { /* Capture can fail for a pointer that already ended. */ }
    dragRef.current = {
      id, clearOnClick,
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      start,
      latest: start,
    };
    setDragPreview({ id, deltaX: 0, deltaY: 0 });
  };

  const resizeDelta = (resize: ResizeStateV2, point: SvgPoint) => {
    const node = view?.layers.flatMap(layer => layer.nodes).find(node => node.id === resize.id);
    if (!node) return null;
    let dx = point[0] - resize.start[0], dy = point[1] - resize.start[1];
    const outline = drawingOutline(node,evaluate);
    if (outline && !outline.curved) {
      const snapped = snapDrawingResizeDelta(outline, { x: dx, y: dy }, resize.handle, targets(resize.id), snaps, snapTolerance(), evaluate(node.transform.rotationDegrees) ?? 0);
      dx = snapped.x; dy = snapped.y;
    }
    return templateDeltaToNodeDeltaV2(dx,dy,evaluate(node.transform.rotationDegrees) ?? 0,evaluate(node.transform.scaleX) ?? 1,evaluate(node.transform.scaleY) ?? 1);
  };

  const moveNodeGesture = (event: ReactPointerEvent<SVGSVGElement>) => {
    const stretch=stretchRef.current;
    if(stretch && stretch.pointerId===event.pointerId) {
      const point=pointFromEvent(event);if(!point)return;
      const dx=stretch.start[0]-stretch.anchor[0],dy=stretch.start[1]-stretch.anchor[1];
      const factor=Math.max(.01,((point[0]-stretch.anchor[0])*dx+(point[1]-stretch.anchor[1])*dy)/Math.max(1,dx*dx+dy*dy));
      stretchRef.current={...stretch,factor};setStretchPreview({factor,anchor:stretch.anchor});return;
    }
    const box = marqueeRef.current;
    if(box && box.pointerId === event.pointerId) { const point=pointFromEvent(event); if(!point) return; marqueeRef.current={...box,latest:point}; setMarquee({left:Math.min(box.start[0],point[0]),top:Math.min(box.start[1],point[1]),right:Math.max(box.start[0],point[0]),bottom:Math.max(box.start[1],point[1])}); return; }
    const rotation = rotationRef.current;
    if (rotation && rotation.pointerId === event.pointerId) {
      const point = pointFromEvent(event);
      if (!point) return;
      const angle = (Math.atan2(point[1] - rotation.center[1], point[0] - rotation.center[0]) - rotation.startAngle) * 180 / Math.PI;
      rotationRef.current = { ...rotation, latest: rotation.rotation + angle };
      setRotationPreview({ id: rotation.id, center: rotation.center, angle });
      return;
    }
    const pointDrag = pointDragRef.current;
    if (pointDrag && pointDrag.pointerId === event.pointerId) {
      const point = pointFromEvent(event);
      if (!point) return;
      const origin = nodePointToTemplatePointV2(pointDrag.origin, pointDrag);
      const anchor = pointDrag.angleAnchor && nodePointToTemplatePointV2(pointDrag.angleAnchor, pointDrag);
      const viewDelta = { deltaX: point[0] - pointDrag.start[0], deltaY: point[1] - pointDrag.start[1] };
      const snappedViewDelta = applyTemplatePointAngleModeV2(
        origin, viewDelta, anchor,
        pointDrag.angleMode,
      );
      const snappedPoint = snappedViewDelta && snapDrawingPoint({ x: origin.x + snappedViewDelta.deltaX, y: origin.y + snappedViewDelta.deltaY }, targets(pointDrag.id), snaps, snapTolerance(), anchor);
      const adjusted = snappedPoint && templateDeltaToNodeDeltaV2(
        snappedPoint.x - origin.x, snappedPoint.y - origin.y,
        pointDrag.rotationDegrees, pointDrag.scaleX, pointDrag.scaleY,
      );
      if (!adjusted) return;
      pointDragRef.current = { ...pointDrag, latest: point };
      setPointDragPreview({ id: pointDrag.id, pointIndex: pointDrag.pointIndex, ...adjusted });
      return;
    }
    const resize = resizeRef.current;
    if (resize && resize.pointerId === event.pointerId) {
      const point = pointFromEvent(event);
      if (!point) return;
      resizeRef.current = { ...resize, latest: point };
      const node = view?.layers.flatMap(layer => layer.nodes).find(node => node.id === resize.id);
      const delta = resizeDelta(resize,point);
      if (delta) setResizePreview({ id: resize.id, handle: resize.handle, ...delta });
      return;
    }
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const point = pointFromEvent(event);
    if (!point) return;
    dragRef.current = { ...drag, latest: point };
    const delta = snapDelta(drag.id, drag.start, point);
    setDragPreview({ id: drag.id, deltaX: delta[0], deltaY: delta[1] });
  };

  const clearNodeGesture = (event: ReactPointerEvent<SVGSVGElement>) => {
    stretchRef.current=null;setStretchPreview(null);
    marqueeRef.current=null; setMarquee(null);
    rotationRef.current = null;
    setRotationPreview(null);
    const drag = dragRef.current;
    const pointDrag = pointDragRef.current;
    const resize = resizeRef.current;
    if (drag && drag.pointerId !== event.pointerId || pointDrag && pointDrag.pointerId !== event.pointerId || resize && resize.pointerId !== event.pointerId) return;
    try {
      if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    } catch { /* The browser can release capture before React handles the terminal event. */ }
    dragRef.current = null;
    resizeRef.current = null;
    pointDragRef.current = null;
    setDragPreview(null);
    setResizePreview(null);
    setPointDragPreview(null);
  };

  const endNodeGesture = (event: ReactPointerEvent<SVGSVGElement>) => {
    const stretch=stretchRef.current;
    if(stretch && stretch.pointerId===event.pointerId){onSelectionStretch?.(stretch.factor,{x:stretch.anchor[0],y:stretch.anchor[1]});stretchRef.current=null;setStretchPreview(null);return;}
    const box=marqueeRef.current;
    if(box && box.pointerId === event.pointerId) {
      const point=pointFromEvent(event) ?? box.latest;
      const moved=Math.hypot(event.clientX-box.client[0],event.clientY-box.client[1])>=3;
      const ids=moved && view ? nodesInsideSelectionBox(view,{left:Math.min(box.start[0],point[0]),top:Math.min(box.start[1],point[1]),right:Math.max(box.start[0],point[0]),bottom:Math.max(box.start[1],point[1])},evaluate,repeatPreview) : [];
      clearNodeGesture(event); onBoxSelection?.(ids); return;
    }
    const rotation = rotationRef.current;
    if (rotation && rotation.pointerId === event.pointerId) {
      const point = pointFromEvent(event);
      const angle = point ? rotation.rotation + (Math.atan2(point[1] - rotation.center[1], point[0] - rotation.center[0]) - rotation.startAngle) * 180 / Math.PI : rotation.latest;
      clearNodeGesture(event);
      if(rotation.id === "selection") onSelectionRotate?.(angle,{x:rotation.center[0],y:rotation.center[1]}); else onNodeRotate?.(rotation.id, angle);
      return;
    }
    const pointDrag = pointDragRef.current;
    if (pointDrag && pointDrag.pointerId === event.pointerId) {
      const finalPoint = pointFromEvent(event) ?? pointDrag.latest;
      const completed = completedTemplateNodeDragV2(
        { clientX: pointDrag.startClientX, clientY: pointDrag.startClientY, templateX: pointDrag.start[0], templateY: pointDrag.start[1] },
        { clientX: event.clientX, clientY: event.clientY, templateX: finalPoint[0], templateY: finalPoint[1] },
      );
      const origin = nodePointToTemplatePointV2(pointDrag.origin, pointDrag);
      const anchor = pointDrag.angleAnchor && nodePointToTemplatePointV2(pointDrag.angleAnchor, pointDrag);
      const snappedViewDelta = completed && applyTemplatePointAngleModeV2(
        origin, completed, anchor,
        pointDrag.angleMode,
      );
      const snappedPoint = snappedViewDelta && snapDrawingPoint({ x: origin.x + snappedViewDelta.deltaX, y: origin.y + snappedViewDelta.deltaY }, targets(pointDrag.id), snaps, snapTolerance(), anchor);
      const adjusted = snappedPoint && templateDeltaToNodeDeltaV2(
        snappedPoint.x - origin.x, snappedPoint.y - origin.y,
        pointDrag.rotationDegrees, pointDrag.scaleX, pointDrag.scaleY,
      );
      clearNodeGesture(event);
      if (adjusted) onNodePointMove?.(pointDrag.id, pointDrag.pointIndex, adjusted.deltaX, adjusted.deltaY);
      return;
    }
    const resize = resizeRef.current;
    if (resize && resize.pointerId === event.pointerId) {
      const finalPoint = pointFromEvent(event) ?? resize.latest;
      const completed = completedTemplateNodeDragV2(
        { clientX: resize.startClientX, clientY: resize.startClientY, templateX: resize.start[0], templateY: resize.start[1] },
        { clientX: event.clientX, clientY: event.clientY, templateX: finalPoint[0], templateY: finalPoint[1] },
      );
      clearNodeGesture(event);
      const node = view?.layers.flatMap(layer => layer.nodes).find(node => node.id === resize.id);
      const local = completed && resizeDelta(resize,finalPoint);
      if (local) onNodeResize?.(resize.id, resize.handle, local.deltaX, local.deltaY);
      return;
    }
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const finalPoint = pointFromEvent(event) ?? drag.latest;
    const completed = completedTemplateNodeDragV2(
      { clientX: drag.startClientX, clientY: drag.startClientY, templateX: drag.start[0], templateY: drag.start[1] },
      { clientX: event.clientX, clientY: event.clientY, templateX: finalPoint[0], templateY: finalPoint[1] },
    );
    clearNodeGesture(event);
    if (completed) { const delta = snapDelta(drag.id, drag.start, finalPoint); onNodeMove?.(drag.id, delta[0], delta[1]); }
    else if(drag.clearOnClick) { if(onBoxSelection) onBoxSelection([]); else onSelect(null); }
  };

  const beginResizeGesture = (event: ReactPointerEvent<SVGElement>, id: string, handle: NodeResizeHandleV2) => {
    event.preventDefault();
    event.stopPropagation();
    if (!onNodeResize) return;
    const start = pointFromEvent(event);
    const svg = event.currentTarget.ownerSVGElement;
    if (!start || !svg) return;
    try { svg.setPointerCapture(event.pointerId); } catch { /* Pointer capture is best effort. */ }
    resizeRef.current = { id, handle, pointerId: event.pointerId, startClientX: event.clientX, startClientY: event.clientY, start, latest: start };
    setResizePreview({ id, handle, deltaX: 0, deltaY: 0 });
  };

  const beginPointGesture = (
    event: ReactPointerEvent<SVGElement>,
    id: string,
    pointIndex: number,
    rotationDegrees: number,
    scaleX: number,
    scaleY: number,
    translateX: number,
    translateY: number,
    origin: SvgPoint,
    angleAnchor: SvgPoint | null,
  ) => {
    event.preventDefault();
    event.stopPropagation();
    if (!onNodePointMove) return;
    const start = pointFromEvent(event);
    const svg = event.currentTarget.ownerSVGElement;
    if (!start || !svg) return;
    try { svg.setPointerCapture(event.pointerId); } catch { /* Pointer capture is best effort. */ }
    pointDragRef.current = {
      id, pointIndex, pointerId: event.pointerId,
      startClientX: event.clientX, startClientY: event.clientY,
      start, latest: start, rotationDegrees, scaleX, scaleY, translateX, translateY,
      origin, angleAnchor, angleMode: pointAngleMode,
    };
    setPointDragPreview({ id, pointIndex, deltaX: 0, deltaY: 0 });
  };

  const previewTransform = (nodeId: string, transform: string, topLevel: boolean): string => {
    if(stretchPreview && topLevel && selectedIdSet.has(nodeId)) return `translate(${stretchPreview.anchor.join(" ")}) scale(${stretchPreview.factor}) translate(${-stretchPreview.anchor[0]} ${-stretchPreview.anchor[1]}) ${transform}`;
    const preview = templateRootDragPreviewV2(nodeId, topLevel, dragPreview, selectedIdSet);
    if (!preview) return transform;
    return `translate(${formatNumber(preview.deltaX)} ${formatNumber(preview.deltaY)}) ${transform}`;
  };

  const previewPoints = (nodeId: string, points: readonly SvgPoint[]): SvgPoint[] => {
    const preview = pointDragPreview?.id === nodeId ? pointDragPreview : null;
    return points.map((point, pointIndex) => preview?.pointIndex === pointIndex
      ? [point[0] + preview.deltaX, point[1] + preview.deltaY]
      : point);
  };

  const hasMovableTransform = (node: TemplateNodeV2) =>
    node.transform.translateX.kind === "constant" && node.transform.translateY.kind === "constant";

  function renderLayer(layer: TemplateViewV2["layers"][number]): ReactNode {
    if (!layer.visible) return null;
    const nodesById = new Map(layer.nodes.map(node => [node.id, node]));
    const ownedIds = new Set<string>();
    for (const node of layer.nodes) {
      if (node.kind === "group") for (const childId of node.geometry.childIds) ownedIds.add(childId);
    }

    function renderNode(
      node: TemplateNodeV2,
      ancestorLocked = false,
      ancestors = new Set<string>(),
      rootNodeId = node.id,
      topLevel = true,
      rootMovable = hasMovableTransform(node),
      occurrenceNumber?: number,
    ): ReactNode {
      if (!node.visible) return null;
      const locked = ancestorLocked || layer.locked || node.locked;
      const transform = evaluateTransform(node.transform, evaluate);
      const strokeWidth = evaluate(node.stroke.width);
      if (transform === null || strokeWidth === null || strokeWidth < 0)
        return placeholder(node, width, height, "Преобразование или толщина линии не вычисляется из параметров.");

      const common = {
        "data-template-node-id": node.id,
        "data-template-node-kind": node.kind,
        "data-selected": selectedIdSet.has(rootNodeId) ? "true" : undefined,
        "data-locked": locked ? "true" : undefined,
        opacity: node.opacity,
        transform: ((rotationPreview?.id === node.id || rotationPreview?.id === "selection" && topLevel && selectedIdSet.has(node.id)) ? `rotate(${rotationPreview.angle} ${rotationPreview.center[0]} ${rotationPreview.center[1]}) ` : "") + previewTransform(node.id, transform, topLevel),
        pointerEvents: layer.locked ? "none" as const : undefined,
        "data-draggable": topLevel && !locked && onNodeMove && rootMovable ? "true" : undefined,
        onPointerDown: (event: ReactPointerEvent<SVGElement>) => beginNodeGesture(event, rootNodeId, !layer.locked, !locked && rootMovable),
      };
      const shapePoint=view?.contactPoints.find(p=>p.shape?.nodeId===node.id);
      const contactNumber=shapePoint?logicalContacts.get(shapePoint.logicalContactId)?.number:undefined;
      const contactLabel=(x:number,y:number,w:number,h:number,ellipse=false)=>{
        if(!contactNumber)return null;
        const label=contactShapeLabel(contactNumber,x,y,w,h,strokeWidth,ellipse);
        return label.fontSize>0?<text data-contact-shape-number={contactNumber} x={x} y={y} fontFamily="Arial" fontSize={label.fontSize} textAnchor="middle" dominantBaseline="central" fill={contactLabelColor(node.fill.color)} pointerEvents="none">{contactNumber}</text>:null;
      };
      const shape = {
        fill: node.fill.color && node.fill.hatch ? `url(#${hatchPrefix}-${node.id})` : node.fill.color ?? "none",
        stroke: node.stroke.color,
        strokeWidth,
        strokeDasharray: strokeDasharray(node.stroke.dash, strokeWidth),
      };

      if (node.kind === "line" || node.kind === "polyline") {
        const evaluatedPoints = evaluatePoints(node.geometry.points, evaluate);
        if (!evaluatedPoints || evaluatedPoints.length < 2) return placeholder(node, width, height, "Координаты линии не вычисляются из параметров.");
        const points = previewPoints(node.id, evaluatedPoints);
        const bendRadius = evaluate(node.geometry.bendRadius);
        if (bendRadius === null || bendRadius < 0)
          return placeholder(node, width, height, "Радиус изгиба линии не вычисляется или является отрицательным.");
        if (node.kind === "line" && points.length === 2) {
          return <g key={node.id} {...common}>
            <line x1={points[0]![0]} y1={points[0]![1]} x2={points[1]![0]} y2={points[1]![1]} fill="none" stroke="transparent" strokeWidth={Math.max(strokeWidth, 12)} />
            <line {...shape} pointerEvents="none" x1={points[0]![0]} y1={points[0]![1]} x2={points[1]![0]} y2={points[1]![1]} />
          </g>;
        }
        if (bendRadius > 0) {
          const path = roundedPolylinePathV2(points, bendRadius);
          if (!path) return placeholder(node, width, height, "Скруглённую линию не удалось построить.");
          return <g key={node.id} {...common}>
            <path d={path} fill="none" stroke="transparent" strokeWidth={Math.max(strokeWidth, 12)} />
            <path {...shape} fill="none" pointerEvents="none" d={path} />
          </g>;
        }
        return <polyline key={node.id} {...common} {...shape} points={pointsAttribute(points)} />;
      }
      if (node.kind === "rectangle") {
        const x = evaluate(node.geometry.x);
        const y = evaluate(node.geometry.y);
        const rectangleWidth = evaluate(node.geometry.width);
        const rectangleHeight = evaluate(node.geometry.height);
        const radii = node.geometry.cornerRadii.map(radius => evaluate(radius));
        if (x === null || y === null || rectangleWidth === null || rectangleHeight === null || radii.some(radius => radius === null))
          return placeholder(node, width, height, "Размер прямоугольника не вычисляется из параметров.");
        const path = rectanglePath(x, y, rectangleWidth, rectangleHeight, radii as number[]);
        return path
          ? <g key={node.id} {...common}>
            <path data-shape-hit-region="true" d={path} fill="transparent" stroke="transparent" strokeWidth={strokeWidth} />
            <path {...shape} pointerEvents="none" d={path} />
            {contactLabel(x+rectangleWidth/2,y+rectangleHeight/2,rectangleWidth,rectangleHeight)}
          </g>
          : placeholder(node, width, height, "Размер прямоугольника должен быть положительным.");
      }
      if (node.kind === "ellipse") {
        const centerX = evaluate(node.geometry.centerX);
        const centerY = evaluate(node.geometry.centerY);
        const radiusX = evaluate(node.geometry.radiusX);
        const radiusY = evaluate(node.geometry.radiusY);
        if (centerX === null || centerY === null || radiusX === null || radiusY === null || radiusX <= 0 || radiusY <= 0)
          return placeholder(node, width, height, "Радиусы эллипса не вычисляются или не являются положительными.");
        return <g key={node.id} {...common}>
          <ellipse data-shape-hit-region="true" cx={centerX} cy={centerY} rx={radiusX} ry={radiusY} fill="transparent" stroke="transparent" strokeWidth={strokeWidth} />
          <ellipse {...shape} pointerEvents="none" cx={centerX} cy={centerY} rx={radiusX} ry={radiusY} />
          {contactLabel(centerX,centerY,radiusX*2,radiusY*2,true)}
        </g>;
      }
      if (node.kind === "bezier") {
        const evaluatedPoints = evaluatePoints(node.geometry.points, evaluate);
        const points = evaluatedPoints && previewPoints(node.id, evaluatedPoints);
        const path = points && bezierPath(points, node.geometry.closed);
        return path
          ? <path key={node.id} {...common} {...shape} d={path} />
          : placeholder(node, width, height, "Контрольные точки кривой не вычисляются из параметров.");
      }
      if (node.kind === "closedContour") {
        const evaluatedPoints = evaluatePoints(node.geometry.points, evaluate);
        const points = evaluatedPoints && previewPoints(node.id, evaluatedPoints);
        return points && points.length >= 3
          ? <polygon key={node.id} {...common} {...shape} points={pointsAttribute(points)} />
          : placeholder(node, width, height, "Точки контура не вычисляются из параметров.");
      }
      if (node.kind === "text") {
        const x = evaluate(node.geometry.x);
        const y = evaluate(node.geometry.y);
        const fontSize = evaluate(node.geometry.fontSize);
        if (x === null || y === null || fontSize === null || fontSize <= 0)
          return placeholder(node, width, height, "Положение или размер текста не вычисляется из параметров.");
        return (
          <text
            key={node.id} {...common}
            onDoubleClick={event => { event.stopPropagation(); onEditText?.(rootNodeId); }}
            x={x}
            y={y}
            fill={node.fill.color ?? node.stroke.color}
            stroke="none"
            fontSize={fontSize}
            fontFamily="Segoe UI, sans-serif"
          >
            {occurrenceNumber === undefined ? node.geometry.text : node.geometry.text.replaceAll("{{n}}", String(occurrenceNumber))}
          </text>
        );
      }
      if (node.kind === "image") {
        const x = evaluate(node.geometry.x);
        const y = evaluate(node.geometry.y);
        const imageWidth = evaluate(node.geometry.width);
        const imageHeight = evaluate(node.geometry.height);
        if (x === null || y === null || imageWidth === null || imageHeight === null || imageWidth <= 0 || imageHeight <= 0)
          return placeholder(node, width, height, "Рамка изображения не вычисляется или имеет неположительный размер.");
        if (!assetIds.has(node.geometry.assetId)) return placeholder(node, width, height, "Asset изображения отсутствует в шаблоне.");
        if (node.geometry.cropWidth <= 0 || node.geometry.cropHeight <= 0)
          return placeholder(node, width, height, "Область обрезки изображения имеет нулевой размер.");
        let href: string;
        try { href = resolveAssetUrl(node.geometry.assetId); }
        catch { return placeholder(node, width, height, "URL изображения не удалось получить."); }
        if (!href) return placeholder(node, width, height, "URL изображения пуст.");
        const crop = `${formatNumber(node.geometry.cropX)} ${formatNumber(node.geometry.cropY)} ${formatNumber(node.geometry.cropWidth)} ${formatNumber(node.geometry.cropHeight)}`;
        return (
          <g key={node.id} {...common} data-underlay={node.geometry.underlay ? "true" : "false"}>
            <svg
              data-template-image-frame={node.id}
              x={x}
              y={y}
              width={imageWidth}
              height={imageHeight}
              viewBox={crop}
              preserveAspectRatio="none"
              overflow="hidden"
            >
              <image href={href} x="0" y="0" width="1" height="1" preserveAspectRatio="none" />
            </svg>
          </g>
        );
      }

      if (ancestors.has(node.id)) return placeholder(node, width, height, "Обнаружен цикл групп.");
      const nextAncestors = new Set(ancestors);
      nextAncestors.add(node.id);
      const childIds = new Set(node.geometry.childIds);
      const missingChild = node.geometry.childIds.some(childId => !nodesById.has(childId));
      if (missingChild) return placeholder(node, width, height, "Группа ссылается на отсутствующий узел.");
      return (
        <g key={node.id} {...common} data-template-group="true">
          {layer.nodes.map(child => childIds.has(child.id) ? renderNode(child, locked, nextAncestors, rootNodeId, false, rootMovable, occurrenceNumber) : null)}
        </g>
      );
    }

    function renderRepeatedGroup(groupId: string): ReactNode {
      const group = nodesById.get(groupId);
      const occurrences = repeatPreview.get(groupId);
      if (!group || group.kind !== "group" || !occurrences) return null;
      return occurrences.map(occurrence => (
        <g
          key={occurrence.group.key}
          data-template-repeat-domain={occurrence.repeatDomainId}
          data-template-repeat-index={occurrence.index}
          data-template-occurrence-key={occurrence.group.key}
          transform={`translate(${formatNumber(occurrence.offset.x)} ${formatNumber(occurrence.offset.y)})`}
        >
          {renderNode(group, false, new Set(), group.id, false, false, occurrence.index + 1)}
        </g>
      ));
    }

    return (
      <g key={layer.id} data-template-layer-id={layer.id} data-locked={layer.locked ? "true" : undefined}>
        {layer.nodes.map(node => ownedIds.has(node.id) ? null : repeatedGroupIds.has(node.id) && repeatPreview.has(node.id) ? renderRepeatedGroup(node.id) : renderNode(node))}
      </g>
    );
  }

  function renderPoint(
    point: TemplateViewV2["contactPoints"][number],
    kind: "contact",
  ): ReactNode;
  function renderPoint(
    point: TemplateViewV2["bundlePorts"][number],
    kind: "bundle",
  ): ReactNode;
  function renderPoint(
    point: TemplateViewV2["contactPoints"][number] | TemplateViewV2["bundlePorts"][number],
    kind: "contact" | "bundle",
  ): ReactNode {
    if("shape" in point && point.shape)return null;
    const x = evaluate(point.x);
    const y = evaluate(point.y);
    if (x === null || y === null) {
      const [placeholderX, placeholderY] = stablePlaceholderPosition(point.id, width, height);
      return (
        <g key={point.id} data-template-point-id={point.id} data-render="placeholder" pointerEvents="none" transform={`translate(${placeholderX} ${placeholderY})`}>
          <circle r="7" fill="#fff7e6" stroke="#a86519" strokeDasharray="3 2" />
          <title>Положение точки не вычисляется из параметров.</title>
        </g>
      );
    }
    const logical = kind === "contact" && "logicalContactId" in point
      ? logicalContacts.get(point.logicalContactId)
      : undefined;
    const name = kind === "bundle" && "name" in point ? point.name : logical?.name ?? "Контакт";
    const number = logical?.number ?? "";
    const label = kind === "contact"
      ? `Контакт ${number || "без номера"}: ${name}; направление ${point.direction}`
      : `Общий выход пучка: ${name}; направление ${point.direction}`;
    const stem = point.direction === "left" ? "M -13 0 H -5"
      : point.direction === "right" ? "M 5 0 H 13"
        : point.direction === "up" ? "M 0 -13 V -5" : "M 0 5 V 13";
    const previewX = stretchPreview && selectedIdSet.has(point.id) ? stretchPreview.anchor[0] + (x - stretchPreview.anchor[0]) * stretchPreview.factor : x;
    const previewY = stretchPreview && selectedIdSet.has(point.id) ? stretchPreview.anchor[1] + (y - stretchPreview.anchor[1]) * stretchPreview.factor : y;
    return (
      <g
        key={point.id}
        data-template-point-id={point.id}
        data-template-point-kind={kind}
        data-selected={selectedIdSet.has(point.id) ? "true" : undefined}
        transform={`translate(${formatNumber(previewX + (dragPreview && (dragPreview.id === point.id || selectedIdSet.has(point.id) && selectedIdSet.has(dragPreview.id)) ? dragPreview.deltaX : 0))} ${formatNumber(previewY + (dragPreview && (dragPreview.id === point.id || selectedIdSet.has(point.id) && selectedIdSet.has(dragPreview.id)) ? dragPreview.deltaY : 0))})`}
        onPointerDown={event => beginNodeGesture(event, point.id, true, point.x.kind === "constant" && point.y.kind === "constant")}
        onKeyDown={event => selectFromKeyboard(event, point.id)}
        role="button"
        tabIndex={preview ? -1 : 0}
        aria-label={label}
      >
        <g transform={`scale(${preview ? 1 : Math.min(1,1/screenScale)})`}>{kind === "contact"
          ? <circle r="5" fill="#fff" stroke="#c54848" strokeWidth="2" />
          : <path d="M 0 -8 L 8 0 L 0 8 L -8 0 Z" fill="#edf7fb" stroke="#36708e" strokeWidth="2" />}
        <path d={stem} fill="none" stroke={kind === "contact" ? "#c54848" : "#36708e"} strokeWidth="2" />
        {number && <text x="12" y="-9" fill="#8f3434" fontSize="13" fontWeight="700">{number}</text>}
        <title>{label}</title></g>
      </g>
    );
  }

  function renderRepeatedPoints(): ReactNode {
    return [...repeatPreview.values()].flatMap(occurrences => occurrences.flatMap(occurrence => occurrence.contactPoints.filter(point=>repeatedContactLabels?.[`${viewId}:${point.key}`]!==null).map(original => { const point={...original,...repeatedContactLabels?.[`${viewId}:${original.key}`]}; return (
      <g
        key={`${viewId}:${point.key}`}
        data-template-point-id={point.prototypeContactPointId}
        data-template-point-kind="contact"
        data-template-occurrence-key={point.key}
        data-template-repeat-index={occurrence.index}
        transform={`translate(${formatNumber(point.x + (dragPreview?.id === point.prototypeContactPointId ? dragPreview.deltaX : 0))} ${formatNumber(point.y + (dragPreview?.id === point.prototypeContactPointId ? dragPreview.deltaY : 0))})`}
        onPointerDown={event => { const prototype = view?.contactPoints.find(p => p.id === point.prototypeContactPointId); beginNodeGesture(event, point.prototypeContactPointId, true, prototype?.x.kind === "constant" && prototype?.y.kind === "constant"); }}
        onKeyDown={event => selectFromKeyboard(event, point.prototypeContactPointId)}
        role="button"
        tabIndex={preview ? -1 : 0}
        aria-label={`Контакт ${point.number}: ${point.name}; направление ${point.direction}; повтор ${occurrence.index + 1}`}
      >
        <g transform={`scale(${preview ? 1 : Math.min(1,1/screenScale)})`}><circle r="5" fill="#fff" stroke="#c54848" strokeWidth="2" />
        <path d={point.direction === "left" ? "M -13 0 H -5" : point.direction === "right" ? "M 5 0 H 13" : point.direction === "up" ? "M 0 -13 V -5" : "M 0 5 V 13"} fill="none" stroke="#c54848" strokeWidth="2" />
        <text x="12" y="-9" fill="#8f3434" fontSize="13" fontWeight="700">{point.number}</text>
        <title>{`${point.name} · ${point.direction}`}</title></g>
      </g>
    );})));
  }

  function renderMultiSelectionOverlay(): ReactNode {
    if (!view || selectedIdSet.size < 2) return null;

    const boundsForNode = (
      node: TemplateNodeV2,
      nodesById: ReadonlyMap<string, TemplateNodeV2>,
      parentMatrix: TemplateMatrixV2,
      ancestors: ReadonlySet<string>,
    ): TemplateBoundsV2 | null => {
      if (!node.visible || ancestors.has(node.id)) return null;
      const localMatrix = evaluateTransformMatrixV2(node.transform, evaluate);
      if (!localMatrix) return null;
      const matrix = multiplyTemplateMatricesV2(parentMatrix, localMatrix);
      const transformedPoints = (points: readonly SvgPoint[]) =>
        boundsFromTemplatePointsV2(points.map(point => transformTemplatePointV2(matrix, point[0], point[1])));

      if (node.kind === "line" || node.kind === "polyline" || node.kind === "bezier" || node.kind === "closedContour") {
        const points = evaluatePoints(node.geometry.points, evaluate);
        return points ? transformedPoints(points) : null;
      }
      if (node.kind === "rectangle" || node.kind === "image") {
        const x = evaluate(node.geometry.x), y = evaluate(node.geometry.y);
        const nodeWidth = evaluate(node.geometry.width), nodeHeight = evaluate(node.geometry.height);
        if (x === null || y === null || nodeWidth === null || nodeHeight === null) return null;
        return transformedPoints([[x, y], [x + nodeWidth, y], [x + nodeWidth, y + nodeHeight], [x, y + nodeHeight]]);
      }
      if (node.kind === "ellipse") {
        const centerX = evaluate(node.geometry.centerX), centerY = evaluate(node.geometry.centerY);
        const radiusX = evaluate(node.geometry.radiusX), radiusY = evaluate(node.geometry.radiusY);
        if (centerX === null || centerY === null || radiusX === null || radiusY === null) return null;
        const center = transformTemplatePointV2(matrix, centerX, centerY);
        const extentX = Math.hypot(matrix.a * radiusX, matrix.c * radiusY);
        const extentY = Math.hypot(matrix.b * radiusX, matrix.d * radiusY);
        return { left: center.x - extentX, top: center.y - extentY, right: center.x + extentX, bottom: center.y + extentY };
      }
      if (node.kind === "text") {
        const x = evaluate(node.geometry.x), y = evaluate(node.geometry.y), fontSize = evaluate(node.geometry.fontSize);
        if (x === null || y === null || fontSize === null) return null;
        const textWidth = Math.max(fontSize * 0.6, node.geometry.text.length * fontSize * 0.6);
        return transformedPoints([[x, y - fontSize], [x + textWidth, y - fontSize], [x + textWidth, y], [x, y]]);
      }

      const nextAncestors = new Set(ancestors);
      nextAncestors.add(node.id);
      return unionTemplateBoundsV2(node.geometry.childIds.map(childId => {
        const child = nodesById.get(childId);
        return child ? boundsForNode(child, nodesById, matrix, nextAncestors) : null;
      }));
    };

    const selectedBounds: TemplateBoundsV2[] = [];
    for (const layer of view.layers) {
      if (!layer.visible) continue;
      const nodesById = new Map(layer.nodes.map(node => [node.id, node]));
      const ownedIds = new Set(layer.nodes.flatMap(node => node.kind === "group" ? node.geometry.childIds : []));
      for (const node of layer.nodes) {
        if (ownedIds.has(node.id) || !selectedIdSet.has(node.id)) continue;
        const bounds = boundsForNode(node, nodesById, IDENTITY_MATRIX_V2, new Set());
        if (bounds) selectedBounds.push(bounds);
      }
    }
    for(const point of [...view.contactPoints,...view.bundlePorts].filter(p=>selectedIdSet.has(p.id))){const x=evaluate(point.x),y=evaluate(point.y);if(x!==null&&y!==null)selectedBounds.push({left:x-5,top:y-5,right:x+5,bottom:y+5});}
    const bounds = unionTemplateBoundsV2(selectedBounds);
    if (!bounds) return null;
    const preview = dragPreview && selectedIdSet.has(dragPreview.id) ? dragPreview : null;
    const deltaX = preview?.deltaX ?? 0, deltaY = preview?.deltaY ?? 0;
    return <g className="template-selection" data-selection-kind="multi" transform={(rotationPreview?.id === "selection" ? `rotate(${rotationPreview.angle} ${rotationPreview.center[0]} ${rotationPreview.center[1]}) ` : "") + `translate(${formatNumber(deltaX)} ${formatNumber(deltaY)})`}>
      <rect
        data-multi-selection-bounds="true"
        x={bounds.left}
        y={bounds.top}
        width={bounds.right - bounds.left}
        height={bounds.bottom - bounds.top}
        style={{pointerEvents:"all",fill:"transparent",cursor:"move"}}
        onPointerDown={event => { const id=[...selectedIdSet][0]; if(id) beginNodeGesture(event,id,true,true); }}
      />
    </g>;
  }

  function renderSelectionOverlay(): ReactNode {
    if (!view || !selectedId || selectedIdSet.size > 1) return null;
    const located = view.layers.flatMap(layer => layer.nodes.map(node => ({ layer, node }))).find(item => item.node.id === selectedId);
    if (!located || located.layer.locked || located.node.locked || !located.node.visible) return null;
    const node = located.node;
    const expressionIsConstant = (expression: NumericExpressionV2) => expression.kind === "constant";
    const pointGeometryIsConstant = (node.kind === "line" || node.kind === "polyline" || node.kind === "bezier" || node.kind === "closedContour") &&
      node.geometry.points.every(point => expressionIsConstant(point.x) && expressionIsConstant(point.y));
    const transformIsConstant = [
      node.transform.translateX, node.transform.translateY, node.transform.rotationDegrees,
      node.transform.scaleX, node.transform.scaleY,
    ].every(expressionIsConstant);
    if (pointGeometryIsConstant && transformIsConstant && (onNodePointMove || onNodePointDelete || onNodePointInsert)) {
      const points = evaluatePoints(node.geometry.points, evaluate);
      const transform = evaluateTransform(node.transform, evaluate);
      if (!points || transform === null) return null;
      const previewed = previewPoints(node.id, points);
      const rotationDegrees = node.transform.rotationDegrees.kind === "constant" ? node.transform.rotationDegrees.value : 0;
      const scaleX = node.transform.scaleX.kind === "constant" ? node.transform.scaleX.value : 1;
      const scaleY = node.transform.scaleY.kind === "constant" ? node.transform.scaleY.value : 1;
      const translateX = node.transform.translateX.kind === "constant" ? node.transform.translateX.value : 0;
      const translateY = node.transform.translateY.kind === "constant" ? node.transform.translateY.value : 0;
      const handleRadii = previewed.map((point,index)=>drawingHandleRadii(screenScale,scaleX,scaleY,
        Math.min(...previewed.filter((_,i)=>i!==index).map(other=>Math.hypot((other[0]-point[0])*scaleX,(other[1]-point[1])*scaleY)))));
      const deletePoint = (event: ReactMouseEvent<SVGEllipseElement>, pointIndex: number) => {
        event.preventDefault();
        event.stopPropagation();
        if ((node.kind === "line" || node.kind === "polyline") && pointIndex > 0 && pointIndex < points.length - 1 ||
            node.kind === "closedContour" && points.length > 3)
          onNodePointDelete?.(node.id, pointIndex);
      };
      const insertPoint = (event: ReactMouseEvent<SVGLineElement>, segmentIndex: number) => {
        if ((node.kind !== "line" && node.kind !== "polyline" && node.kind !== "closedContour") || !onNodePointInsert) return;
        event.preventDefault();
        event.stopPropagation();
        const templatePoint = pointFromEvent(event);
        if (!templatePoint) return;
        const local = templatePointToNodePointV2(
          templatePoint[0], templatePoint[1], translateX, translateY, rotationDegrees, scaleX, scaleY,
        );
        if (!local) return;
        const projection = projectPointToTemplateSegmentV2(
          local,
          { x: points[segmentIndex]![0], y: points[segmentIndex]![1] },
          { x: points[(segmentIndex + 1) % points.length]![0], y: points[(segmentIndex + 1) % points.length]![1] },
        );
        if (projection) onNodePointInsert(node.id, segmentIndex, projection.x, projection.y);
      };
      return <g className="template-point-selection" transform={transform} data-selection-kind={node.kind}>
        {node.kind === "bezier" && Array.from({ length: (previewed.length - 1) / 3 }, (_, segmentIndex) => {
          const offset = segmentIndex * 3;
          return [
            <line key={`guide-${segmentIndex}-start`} x1={previewed[offset]![0]} y1={previewed[offset]![1]}
              x2={previewed[offset + 1]![0]} y2={previewed[offset + 1]![1]} data-point-guide="true" />,
            <line key={`guide-${segmentIndex}-end`} x1={previewed[offset + 2]![0]} y1={previewed[offset + 2]![1]}
              x2={previewed[offset + 3]![0]} y2={previewed[offset + 3]![1]} data-point-guide="true" />,
          ];
        })}
        {(node.kind === "line" || node.kind === "polyline" || node.kind === "closedContour") &&
          Array.from({ length: node.kind === "closedContour" ? previewed.length : previewed.length - 1 }, (_, segmentIndex) => {
            const point = previewed[segmentIndex]!;
            const end = previewed[(segmentIndex + 1) % previewed.length]!;
            return (
          <line key={`segment-${segmentIndex}`} x1={point[0]} y1={point[1]} x2={end[0]} y2={end[1]}
            data-point-segment={segmentIndex}
            onPointerDown={event => beginNodeGesture(event, node.id, true, hasMovableTransform(node))}
            onDoubleClick={event => insertPoint(event, segmentIndex)} />
            );
          })}
        {previewed.map((point, pointIndex) => <ellipse key={pointIndex} cx={point[0]} cy={point[1]} rx={handleRadii[pointIndex]!.rx} ry={handleRadii[pointIndex]!.ry}
          data-point-handle={pointIndex}
          data-point-role={node.kind === "bezier" && pointIndex % 3 !== 0 ? "control" : "anchor"}
          onPointerDown={event => beginPointGesture(
            event, node.id, pointIndex, rotationDegrees, scaleX, scaleY, translateX, translateY, points[pointIndex]!,
            node.kind === "line" || node.kind === "polyline"
              ? points[pointIndex === 0 ? 1 : pointIndex - 1] ?? null
              : null,
          )}
          onDoubleClick={event => deletePoint(event, pointIndex)} />)}
      </g>;
    }
    if (!onNodeResize) return null;
    const transformIsPlain = transformIsConstant && evaluate(node.transform.scaleX) !== 0 && evaluate(node.transform.scaleY) !== 0;
    const geometryIsConstant = node.kind === "line"
      ? node.geometry.points.length === 2 && node.geometry.points.every(point => expressionIsConstant(point.x) && expressionIsConstant(point.y))
      : node.kind === "rectangle"
        ? [node.geometry.x, node.geometry.y, node.geometry.width, node.geometry.height, ...node.geometry.cornerRadii].every(expressionIsConstant)
        : node.kind === "ellipse"
          ? [node.geometry.centerX, node.geometry.centerY, node.geometry.radiusX, node.geometry.radiusY].every(expressionIsConstant)
          : node.kind === "image"
            ? [node.geometry.x, node.geometry.y, node.geometry.width, node.geometry.height].every(expressionIsConstant)
            : false;
    if (!transformIsPlain || !geometryIsConstant) return null;
    const transform = evaluateTransform(node.transform, evaluate);
    if (transform === null) return null;
    const preview = resizePreview?.id === node.id ? resizePreview : null;
    const dx = preview?.deltaX ?? 0, dy = preview?.deltaY ?? 0;
    const handle = preview?.handle;
    const moveX = (value: number, side: "w" | "e") => value + (handle?.includes(side) ? dx : 0);
    const moveY = (value: number, side: "n" | "s") => value + (handle?.includes(side) ? dy : 0);
    if (node.kind === "line" && node.geometry.points.length === 2) {
      const points = evaluatePoints(node.geometry.points, evaluate);
      if (!points) return null;
      const start = [points[0]![0] + (handle === "start" ? dx : 0), points[0]![1] + (handle === "start" ? dy : 0)] as const;
      const end = [points[1]![0] + (handle === "end" ? dx : 0), points[1]![1] + (handle === "end" ? dy : 0)] as const;
      return <g className="template-selection" transform={transform} data-selection-kind="line">
        <line x1={start[0]} y1={start[1]} x2={end[0]} y2={end[1]} />
        <circle cx={start[0]} cy={start[1]} r="6" data-resize-handle="start" onPointerDown={event => beginResizeGesture(event, node.id, "start")} />
        <circle cx={end[0]} cy={end[1]} r="6" data-resize-handle="end" onPointerDown={event => beginResizeGesture(event, node.id, "end")} />
      </g>;
    }
    let x: number | null = null, y: number | null = null, boxWidth: number | null = null, boxHeight: number | null = null;
    if (node.kind === "rectangle" || node.kind === "image") {
      x = evaluate(node.geometry.x); y = evaluate(node.geometry.y); boxWidth = evaluate(node.geometry.width); boxHeight = evaluate(node.geometry.height);
    } else if (node.kind === "ellipse") {
      const cx = evaluate(node.geometry.centerX), cy = evaluate(node.geometry.centerY), rx = evaluate(node.geometry.radiusX), ry = evaluate(node.geometry.radiusY);
      if (cx !== null && cy !== null && rx !== null && ry !== null) { x = cx - rx; y = cy - ry; boxWidth = rx * 2; boxHeight = ry * 2; }
    }
    if (x === null || y === null || boxWidth === null || boxHeight === null) return null;
    const left = moveX(x, "w"), top = moveY(y, "n"), right = moveX(x + boxWidth, "e"), bottom = moveY(y + boxHeight, "s");
    const pixelsX=screenScale*Math.abs(evaluate(node.transform.scaleX) ?? 1),pixelsY=screenScale*Math.abs(evaluate(node.transform.scaleY) ?? 1);
    const control=spacedHandleBounds(left,top,right,bottom,pixelsX,pixelsY);
    const gripRadius=boxHandleRadius((right-left)*pixelsX,(bottom-top)*pixelsY);
    const handles: readonly [NodeResizeHandleV2, number, number][] = [
      ["nw", left, top], ["n", (left + right) / 2, top], ["ne", right, top], ["e", right, (top + bottom) / 2],
      ["se", right, bottom], ["s", (left + right) / 2, bottom], ["sw", left, bottom], ["w", left, (top + bottom) / 2],
    ];
    return <g className="template-selection" transform={transform} data-selection-kind="box">
      <rect x={Math.min(left, right)} y={Math.min(top, bottom)} width={Math.abs(right - left)} height={Math.abs(bottom - top)} />
      {handles.filter(([candidate])=>!control.compact||candidate.length===2).map(([candidate, handleX, handleY]) => <ellipse key={candidate} cx={handleX} cy={handleY} rx={gripRadius/Math.max(.0001,pixelsX)} ry={gripRadius/Math.max(.0001,pixelsY)} style={{strokeWidth:Math.min(2,gripRadius/2)}}
        data-resize-handle={candidate} onPointerDown={event => beginResizeGesture(event, node.id, candidate)} />)}
    </g>;
  }

  return (
    <svg
      ref={svgRef}
      onDoubleClick={event=>{
        if(!onEditText || !view)return;
        const point=pointFromEvent(event); if(!point)return;
        // Pointer capture retargets dblclick to the SVG, so hit-test text here as
        // well as on the glyph. Convert to local coordinates for rotated text.
        for(const layer of [...view.layers].reverse()) for(const node of [...layer.nodes].reverse()) {
          if(!layer.visible || layer.locked || !node.visible || node.locked || node.kind!=="text")continue;
          const t=node.transform, values=[t.translateX,t.translateY,t.rotationDegrees,t.scaleX,t.scaleY,node.geometry.x,node.geometry.y,node.geometry.fontSize].map(evaluate);
          if(values.some(value=>value===null))continue;
          const [tx,ty,angle,sx,sy,x,y,size]=values as number[];
          const local=templatePointToNodePointV2(point[0],point[1],tx!,ty!,angle!,sx!,sy!);
          if(local && local.x>=x! && local.x<=x!+size!*Math.max(1,node.geometry.text.length)*.65 && local.y>=y!-size! && local.y<=y!+size!*.25) {
            event.preventDefault(); onEditText(node.id); return;
          }
        }
      }}
      onKeyDown={e=>{if(e.key==="Home" && (onNodeMove || preview)){e.preventDefault();setCamera(preview?fittedCameraRef.current:{x:0,y:0,zoom:1});}}}
      tabIndex={0}
      onPointerDownCapture={e=>e.currentTarget.focus()}
      className={`template-canvas-v2${preview?" template-canvas-preview":""}${panning?" is-panning":""}`}
      viewBox={`${formatNumber(camera.x)} ${formatNumber(camera.y)} ${formatNumber(width/camera.zoom)} ${formatNumber(height/camera.zoom)}`}
      data-drawing-zoom={camera.zoom}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label={view ? `${preview?"Просмотр":"Редактор"} вида ${view.name}` : "Вид шаблона не найден"}
      data-template-view-id={view?.id}
      width="100%"
      height="100%"
      style={{ display: "block", touchAction: "none" }}
      onPointerDown={event => {
        if(event.button !== 0) return;
        if(preview) {
          event.preventDefault();event.stopPropagation();event.currentTarget.setPointerCapture(event.pointerId);
          panRef.current={pointerId:event.pointerId,clientX:event.clientX,clientY:event.clientY,camera,screenScale};setPanning(true);return;
        }
        if(!onBoxSelection) { onSelectionChange ? onSelectionChange(null,false) : onSelect(null); return; }
        const point=pointFromEvent(event); if(!point) return;
        event.preventDefault(); try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* Released pointer. */ }
        marqueeRef.current={pointerId:event.pointerId,start:point,client:[event.clientX,event.clientY],latest:point};
        setMarquee(null);
      }}
      onPointerMove={event=>{
        if(!preview){moveNodeGesture(event);return;}
        const pan=panRef.current;if(pan?.pointerId!==event.pointerId)return;
        setCamera(panDrawingCamera(pan.camera,{x:event.clientX-pan.clientX,y:event.clientY-pan.clientY},pan.screenScale));
      }}
      onPointerUp={event=>{
        if(!preview){endNodeGesture(event);return;}
        if(panRef.current?.pointerId===event.pointerId){panRef.current=null;setPanning(false);event.currentTarget.releasePointerCapture(event.pointerId);}
      }}
      onPointerCancel={event=>{if(preview){panRef.current=null;setPanning(false);}else clearNodeGesture(event);}}
      onLostPointerCapture={()=>{if(preview){panRef.current=null;setPanning(false);}}}
    >
      {preview && <title>Ctrl + колёсико — масштаб у курсора; левая кнопка — перемещение; Home — вписать рисунок.</title>}
      <rect x={camera.x} y={camera.y} width={width/camera.zoom} height={height/camera.zoom} fill="#fff" />
      <defs>{view?.layers.flatMap(layer => layer.nodes).filter(node => node.fill.hatch && node.fill.color).map(node => {
        const hatch = node.fill.hatch!, tile = hatchTile(hatch);
        return <pattern key={node.id} id={`${hatchPrefix}-${node.id}`} patternUnits="userSpaceOnUse" width={hatch.spacing} height={hatch.spacing} patternTransform={`rotate(${hatch.angle})`}>
          {hatch.backgroundColor && <rect width={hatch.spacing} height={hatch.spacing} fill={hatch.backgroundColor}/>}
          {tile.lines.map(([x1, y1, x2, y2], i) => <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke={node.fill.color!} strokeWidth="1" />)}
          {tile.dots.map(([cx, cy, r], i) => <circle key={i} cx={cx} cy={cy} r={r} fill={node.fill.color!} />)}
        </pattern>;
      })}</defs>
      <g ref={artworkRef} pointerEvents={preview?"none":undefined}>
      {view ? view.layers.map(renderLayer) : <text x="24" y="36" fill="#7b4c16" fontSize="14">Вид шаблона не найден</text>}
      {view?.contactPoints.map(point => repeatedPointIds.has(point.id) ? null : renderPoint(point, "contact"))}
      {view && renderRepeatedPoints()}
      {view?.bundlePorts.map(point => renderPoint(point, "bundle"))}
      </g>
      {renderMultiSelectionOverlay()}
      {renderSelectionOverlay()}
      {(() => {
        if(!onSelectionStretch || !view || !(selectedIdSet.size>1 || view.layers.some(l=>l.nodes.some(n=>selectedIdSet.has(n.id)&&n.kind==="group"))))return null;
        const actual=selectionBounds(view,[...selectedIdSet],evaluate);if(!actual)return null;const bounds=spacedHandleBounds(actual.left,actual.top,actual.right,actual.bottom,screenScale);
        return <g className="template-selection">{([[bounds.left,bounds.top,bounds.right,bounds.bottom],[bounds.right,bounds.top,bounds.left,bounds.bottom],[bounds.right,bounds.bottom,bounds.left,bounds.top],[bounds.left,bounds.bottom,bounds.right,bounds.top]] as const).map(([x,y,ax,ay],i)=><circle key={i} cx={x} cy={y} r={6/screenScale} data-selection-stretch-handle={i} aria-label="Растянуть выделение" onPointerDown={event=>{event.preventDefault();event.stopPropagation();try{event.currentTarget.ownerSVGElement?.setPointerCapture(event.pointerId);}catch{} stretchRef.current={pointerId:event.pointerId,anchor:[ax,ay],start:[x,y],factor:1};}}/>)}{stretchPreview&&<rect pointerEvents="none" x={bounds.left} y={bounds.top} width={bounds.right-bounds.left} height={bounds.bottom-bounds.top} transform={`translate(${stretchPreview.anchor.join(' ')}) scale(${stretchPreview.factor}) translate(${-stretchPreview.anchor[0]} ${-stretchPreview.anchor[1]})`}/>}</g>;
      })()}
      {marquee && <rect data-selection-marquee="true" x={marquee.left} y={marquee.top} width={marquee.right-marquee.left} height={marquee.bottom-marquee.top} fill="#1685d11a" stroke="#1685d1" strokeDasharray="4 3" pointerEvents="none" />}
      {(() => {
        if(!onSelectionRotate || !view || selectedIdSet.size<2 || view.repeatPlacements.some(p=>selectedIdSet.has(p.prototypeGroupId))) return null;
        const nodes=view.layers.flatMap(layer=>layer.nodes.filter(node=>selectedIdSet.has(node.id)).map(node=>({node,layer})));
        if(nodes.some(({node,layer})=>node.locked || layer.locked || Object.values(node.transform).some(value=>value.kind!=="constant"))) return null;
        const bounds=selectionBounds(view,[...selectedIdSet],evaluate); if(!bounds) return null;
        const center={x:(bounds.left+bounds.right)/2,y:(bounds.top+bounds.bottom)/2},y=bounds.top-28/screenScale;
        return <g className="template-rotation-handle"><line x1={center.x} y1={center.y} x2={center.x} y2={y} stroke="#147ca8" strokeDasharray="3 3" pointerEvents="none" /><circle data-selection-rotation-handle="true" cx={center.x} cy={y} r={7/screenScale} fill="#fff" stroke="#147ca8" strokeWidth="2" aria-label="Повернуть выделение вокруг центра" role="button" onPointerDown={event=>{event.preventDefault();event.stopPropagation();const point=pointFromEvent(event);if(!point)return;try{event.currentTarget.ownerSVGElement?.setPointerCapture(event.pointerId);}catch{} rotationRef.current={id:"selection",pointerId:event.pointerId,center:[center.x,center.y],startAngle:Math.atan2(point[1]-center.y,point[0]-center.x),rotation:0,latest:0};}} /></g>;
      })()}
      {(() => {
        if (!onNodeRotate || selectedIdSet.size !== 1) return null;
        const located = view?.layers.flatMap(layer => layer.nodes.map(node => ({ layer, node }))).find(item => item.node.id === selectedId);
        if (!located || located.layer.locked || located.node.locked || located.node.transform.rotationDegrees.kind !== "constant") return null;
        if (located.layer.nodes.some(node => node.kind === "group" && node.geometry.childIds.includes(located.node.id)) || view?.repeatPlacements.some(p => p.prototypeGroupId === located.node.id)) return null;
        let center; try { center = rootNodeRotationCenterV3(located.node, located.layer.nodes); } catch { return null; }
        const outline = outlines.find(item => item.id === selectedId);
        const y = (outline ? Math.min(...outline.points.map(p => p.y)) : center.y - 35) - 28/screenScale;
        return <g className="template-rotation-handle">
          <line x1={center.x} y1={center.y} x2={center.x} y2={y} stroke="#147ca8" strokeDasharray="3 3" pointerEvents="none" />
          <circle cx={center.x} cy={y} r={7/screenScale} fill="#fff" stroke="#147ca8" strokeWidth="2" role="button" aria-label="Повернуть вокруг центра" data-rotation-handle="true"
            onPointerDown={event => { event.stopPropagation(); event.preventDefault(); const point = pointFromEvent(event); if (!point) return;
              const rotation = evaluate(located.node.transform.rotationDegrees) ?? 0;
              try { event.currentTarget.ownerSVGElement?.setPointerCapture(event.pointerId); } catch { /* Pointer may already be released. */ }
              rotationRef.current = { id: located.node.id, pointerId: event.pointerId, center: [center.x, center.y], startAngle: Math.atan2(point[1] - center.y, point[0] - center.x), rotation, latest: rotation };
            }} /><circle cx={center.x} cy={center.y} r="2" fill="#147ca8" pointerEvents="none" />
        </g>;
      })()}
      {onNodeMove && <g className="drawing-zoom-controls" transform={`translate(${camera.x+12/screenScale} ${camera.y+(height/camera.zoom)-28/screenScale}) scale(${1/screenScale})`}>
        {[-1,0,1].map((direction,index)=><g key={direction} role="button" tabIndex={0} aria-label={direction===0?"Исходный масштаб":direction<0?"Уменьшить поле":"Увеличить поле"} onPointerDown={e=>e.stopPropagation()} onClick={()=>setCamera(previous=>direction===0?{x:0,y:0,zoom:1}:zoomDrawingCamera(previous,{x:previous.x+width/(2*previous.zoom),y:previous.y+height/(2*previous.zoom)},-direction*150))} onKeyDown={e=>{if(e.key==="Enter"){e.preventDefault();setCamera(previous=>direction===0?{x:0,y:0,zoom:1}:zoomDrawingCamera(previous,{x:previous.x+width/(2*previous.zoom),y:previous.y+height/(2*previous.zoom)},-direction*150));}}}>
          <rect x={index*45} width="43" height="23" rx="3" fill="#fff" stroke="#b9c7d0"/><text x={index*45+21.5} y="16" textAnchor="middle" fontSize="11" fill="#30495d" pointerEvents="none">{direction===0?`${Math.round(camera.zoom*100)}%`:direction<0?"−":"+"}</text>
        </g>)}
      </g>}
      {repeatPreviewError && <g data-template-repeat-error="true" pointerEvents="none">
        <rect x="16" y="16" width={Math.min(width - 32, 520)} height="42" rx="6" fill="#fff7e6" stroke="#a86519" />
        <text x="28" y="34" fill="#7b4c16" fontSize="11" fontWeight="700">Повторы показаны как прототипы</text>
        <text x="28" y="49" fill="#7b4c16" fontSize="9">{repeatPreviewError}</text>
      </g>}
    </svg>
  );
}
