import type { DrawingArrayLayout } from "./array-layout";
import { hatchKinds, type DrawingHatch } from "./drawing-hatch";
export const TEMPLATE_V2_LIMITS = Object.freeze({
  views: 34, layers: 128, nodes: 5_000, contacts: 2_000, parameters: 128,
  repeaters: 64, assets: 64, presets: 500, expressionDepth: 8, expressionNodes: 64,
  coordinate: 1_000_000, text: 4_096, assetBytes: 10 * 1024 * 1024,
});

export type ViewKindV2 = "e4" | "drawing" | "additional";
export type ContactDirectionV2 = "left" | "right" | "up" | "down";
export type ParameterTypeV2 = "number" | "integer" | "boolean" | "string";
export type ParameterValueV2 = number | boolean | string;
export type NumericExpressionV2 =
  | { kind: "constant"; value: number }
  | { kind: "parameter"; parameterId: string }
  | { kind: "negate"; operand: NumericExpressionV2 }
  | { kind: "binary"; operator: "add" | "subtract" | "multiply" | "divide"; left: NumericExpressionV2; right: NumericExpressionV2 };
export interface PointExpressionV2 { x: NumericExpressionV2; y: NumericExpressionV2; }
export interface TransformV2 {
  translateX: NumericExpressionV2; translateY: NumericExpressionV2;
  rotationDegrees: NumericExpressionV2; scaleX: NumericExpressionV2; scaleY: NumericExpressionV2;
}
export type StrokeDashV2 = "solid" | "dash" | "dot" | "dash-dot";
export interface StrokeV2 { color: string; width: NumericExpressionV2; dash?: StrokeDashV2; }
export interface FillV2 { color: string | null; hatch?: DrawingHatch; }

interface NodeBaseV2 {
  id: string; layerId: string; visible: boolean; locked: boolean; opacity: number;
  transform: TransformV2; stroke: StrokeV2; fill: FillV2;
}
export interface LineNodeV2 extends NodeBaseV2 { kind: "line"; geometry: { points: PointExpressionV2[]; bendRadius: NumericExpressionV2; }; }
export interface PolylineNodeV2 extends NodeBaseV2 { kind: "polyline"; geometry: { points: PointExpressionV2[]; bendRadius: NumericExpressionV2; }; }
export interface RectangleNodeV2 extends NodeBaseV2 { kind: "rectangle"; geometry: { x: NumericExpressionV2; y: NumericExpressionV2; width: NumericExpressionV2; height: NumericExpressionV2; cornerRadii: [NumericExpressionV2, NumericExpressionV2, NumericExpressionV2, NumericExpressionV2]; }; }
export interface EllipseNodeV2 extends NodeBaseV2 { kind: "ellipse"; geometry: { centerX: NumericExpressionV2; centerY: NumericExpressionV2; radiusX: NumericExpressionV2; radiusY: NumericExpressionV2; }; }
export interface BezierNodeV2 extends NodeBaseV2 { kind: "bezier"; geometry: { points: PointExpressionV2[]; closed: boolean; }; }
export interface ClosedContourNodeV2 extends NodeBaseV2 { kind: "closedContour"; geometry: { points: PointExpressionV2[]; }; }
export interface TextNodeV2 extends NodeBaseV2 { kind: "text"; geometry: { x: NumericExpressionV2; y: NumericExpressionV2; text: string; fontSize: NumericExpressionV2; }; }
export interface ImageNodeV2 extends NodeBaseV2 { kind: "image"; geometry: { assetId: string; x: NumericExpressionV2; y: NumericExpressionV2; width: NumericExpressionV2; height: NumericExpressionV2; cropX: number; cropY: number; cropWidth: number; cropHeight: number; underlay: boolean; }; }
export interface GroupNodeV2 extends NodeBaseV2 { kind: "group"; geometry: { childIds: string[]; }; }
export type TemplateNodeV2 = LineNodeV2 | PolylineNodeV2 | RectangleNodeV2 | EllipseNodeV2 | BezierNodeV2 | ClosedContourNodeV2 | TextNodeV2 | ImageNodeV2 | GroupNodeV2;

export interface LayerV2 { id: string; name: string; visible: boolean; locked: boolean; nodes: TemplateNodeV2[]; }
export interface ViewContactPointV2 { shape?: { nodeId: string; fillFromWire: boolean }; id: string; logicalContactId: string; x: NumericExpressionV2; y: NumericExpressionV2; direction: ContactDirectionV2; }
export interface BundlePortV2 { id: string; name: string; x: NumericExpressionV2; y: NumericExpressionV2; direction: ContactDirectionV2; }
export interface ViewRepeatPlacementV2 {
  repeatDomainId: string; prototypeGroupId: string;
  step: PointExpressionV2; contactPointIds: string[];
  arrayLayout?: DrawingArrayLayout;
}
export interface TemplateViewV2 {
  id: string; name: string; kind: ViewKindV2; layers: LayerV2[];
  contactPoints: ViewContactPointV2[]; bundlePorts: BundlePortV2[];
  repeatPlacements: ViewRepeatPlacementV2[];
}
export interface LogicalContactV2 { id: string; number: string; name: string; contactType: string; }
export interface TemplateParameterV2 { id: string; name: string; type: ParameterTypeV2; unit: string | null; defaultValue: ParameterValueV2; minimum: number | null; maximum: number | null; formula: NumericExpressionV2 | null; }
export interface RepeatDomainV2 {
  id: string; countParameterId: string; logicalContactIds: string[];
}
export interface TemplateAssetV2 { assetId: string; fileName: string; mediaType: string; sha256: string; sizeBytes: number; }
export interface ArticleParameterPresetValueV2 { parameterId: string; value: ParameterValueV2; }
export interface ArticleParameterPresetV2 { id: string; sourceId: string; entityType: string; articleKey: string; values: ArticleParameterPresetValueV2[]; }
export interface TemplateContentV2 {
  schemaVersion: 2; views: TemplateViewV2[]; logicalContacts: LogicalContactV2[];
  parameters: TemplateParameterV2[]; repeaters: RepeatDomainV2[]; assets: TemplateAssetV2[];
  articleParameterPresets: ArticleParameterPresetV2[];
}

export interface TemplateV2Diagnostic { code: string; path: string; message: string; }
export interface TemplateV2Validation { valid: boolean; diagnostics: TemplateV2Diagnostic[]; }
export interface TemplateV2Upgrade { content: TemplateContentV2; diagnostics: TemplateV2Diagnostic[]; }

export function repeatOccurrenceKeyV2(repeatDomainId: string, index: number, memberId: string): string {
  if (!UUID.test(repeatDomainId) || !UUID.test(memberId) || !Number.isSafeInteger(index) || index < 0)
    throw new RangeError("repeatDomainId/memberId must be UUIDs and index must be a non-negative safe integer.");
  return `${repeatDomainId}:${index}:${memberId}`;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA256 = /^[0-9a-f]{64}$/;
const COLOR = /^#[0-9a-f]{6}([0-9a-f]{2})?$/i;
const assetMedia = new Set(["image/png"]);
const nodeKinds = new Set(["line", "polyline", "rectangle", "ellipse", "bezier", "closedContour", "text", "image", "group"]);
const viewKinds = new Set(["e4", "drawing", "additional"]);
const directions = new Set(["left", "right", "up", "down"]);
const parameterTypes = new Set(["number", "integer", "boolean", "string"]);
const strokeDashes = new Set(["solid", "dash", "dot", "dash-dot"]);
const hasOwn = (value: Record<string, unknown>, key: string) => Object.prototype.hasOwnProperty.call(value, key);
const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const isFiniteNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const constant = (value: number): NumericExpressionV2 => ({ kind: "constant", value });
const identity = (): TransformV2 => ({ translateX: constant(0), translateY: constant(0), rotationDegrees: constant(0), scaleX: constant(1), scaleY: constant(1) });

function exact(value: unknown, keys: readonly string[], path: string, diagnostics: TemplateV2Diagnostic[]): value is Record<string, unknown> {
  if (!isRecord(value)) { diagnostics.push({ code: "object_required", path, message: "Ожидается объект." }); return false; }
  const actual = Object.keys(value);
  const expected = new Set(keys);
  for (const key of actual) if (!expected.has(key)) diagnostics.push({ code: "unexpected_key", path: `${path}.${key}`, message: "Неизвестное поле." });
  for (const key of keys) if (!hasOwn(value, key)) diagnostics.push({ code: "missing_key", path: `${path}.${key}`, message: "Обязательное поле отсутствует." });
  return actual.length === keys.length && actual.every(key => expected.has(key));
}
function uuid(value: unknown, path: string, diagnostics: TemplateV2Diagnostic[]): value is string {
  if (typeof value === "string" && UUID.test(value)) return true;
  diagnostics.push({ code: "invalid_uuid", path, message: "Требуется UUID." }); return false;
}
function shortText(value: unknown, path: string, diagnostics: TemplateV2Diagnostic[], maximum = 256): value is string {
  if (typeof value === "string" && value.trim() && value.length <= maximum && !/[\u0000-\u001f]/.test(value)) return true;
  diagnostics.push({ code: "invalid_text", path, message: `Нужен непустой текст не длиннее ${maximum} символов.` }); return false;
}
function boundedNumber(value: unknown, path: string, diagnostics: TemplateV2Diagnostic[], min: number = -TEMPLATE_V2_LIMITS.coordinate, max: number = TEMPLATE_V2_LIMITS.coordinate): value is number {
  if (isFiniteNumber(value) && value >= min && value <= max) return true;
  diagnostics.push({ code: "invalid_number", path, message: `Требуется конечное число от ${min} до ${max}.` }); return false;
}

interface ExpressionContext { diagnostics: TemplateV2Diagnostic[]; parameterIds: Set<string>; numericParameterIds: Set<string>; }
interface ExpressionWalk { nodes: number; ancestors: Set<unknown>; }
function expression(value: unknown, path: string, context: ExpressionContext, depth = 1, walk: ExpressionWalk = { nodes: 0, ancestors: new Set() }): void {
  walk.nodes++;
  if (depth > TEMPLATE_V2_LIMITS.expressionDepth || walk.nodes > TEMPLATE_V2_LIMITS.expressionNodes) {
    context.diagnostics.push({ code: "expression_limit", path, message: "Выражение превышает допустимую глубину или размер." }); return;
  }
  if (isRecord(value) && walk.ancestors.has(value)) { context.diagnostics.push({ code: "expression_cycle", path, message: "Обнаружен цикл объектов выражения." }); return; }
  if (!isRecord(value) || typeof value.kind !== "string") { context.diagnostics.push({ code: "invalid_expression", path, message: "Ожидается типизированное числовое выражение." }); return; }
  walk.ancestors.add(value);
  if (value.kind === "constant") {
    if (exact(value, ["kind", "value"], path, context.diagnostics)) boundedNumber(value.value, `${path}.value`, context.diagnostics);
  } else if (value.kind === "parameter") {
    if (exact(value, ["kind", "parameterId"], path, context.diagnostics) && uuid(value.parameterId, `${path}.parameterId`, context.diagnostics) && !context.numericParameterIds.has(value.parameterId)) context.diagnostics.push({ code: "missing_numeric_parameter", path: `${path}.parameterId`, message: "Числовой параметр не найден." });
  } else if (value.kind === "negate") {
    if (exact(value, ["kind", "operand"], path, context.diagnostics)) expression(value.operand, `${path}.operand`, context, depth + 1, walk);
  } else if (value.kind === "binary") {
    if (exact(value, ["kind", "operator", "left", "right"], path, context.diagnostics)) {
      if (!["add", "subtract", "multiply", "divide"].includes(String(value.operator))) context.diagnostics.push({ code: "invalid_operator", path: `${path}.operator`, message: "Неизвестная операция." });
      expression(value.left, `${path}.left`, context, depth + 1, walk); expression(value.right, `${path}.right`, context, depth + 1, walk);
      if (value.operator === "divide" && isRecord(value.right) && value.right.kind === "constant" && value.right.value === 0) context.diagnostics.push({ code: "division_by_zero", path: `${path}.right`, message: "Деление на ноль запрещено." });
    }
  } else context.diagnostics.push({ code: "invalid_expression_kind", path: `${path}.kind`, message: "Неизвестный тип выражения." });
  walk.ancestors.delete(value);
}

function point(value: unknown, path: string, context: ExpressionContext): void {
  if (exact(value, ["x", "y"], path, context.diagnostics)) { expression(value.x, `${path}.x`, context); expression(value.y, `${path}.y`, context); }
}
function transform(value: unknown, path: string, context: ExpressionContext): void {
  if (!exact(value, ["translateX", "translateY", "rotationDegrees", "scaleX", "scaleY"], path, context.diagnostics)) return;
  for (const key of ["translateX", "translateY", "rotationDegrees", "scaleX", "scaleY"] as const) expression(value[key], `${path}.${key}`, context);
}

function validateNode(value: unknown, path: string, layerId: string, context: ExpressionContext, allIds: Set<string>, assetIds: Set<string>): void {
  if (!exact(value, ["id", "kind", "layerId", "visible", "locked", "opacity", "transform", "stroke", "fill", "geometry"], path, context.diagnostics)) return;
  if (uuid(value.id, `${path}.id`, context.diagnostics)) { if (allIds.has(value.id)) context.diagnostics.push({ code: "duplicate_id", path: `${path}.id`, message: "ID уже используется." }); else allIds.add(value.id); }
  if (!nodeKinds.has(String(value.kind))) context.diagnostics.push({ code: "invalid_node_kind", path: `${path}.kind`, message: "Неизвестный тип узла." });
  if (value.layerId !== layerId) context.diagnostics.push({ code: "layer_reference", path: `${path}.layerId`, message: "Узел ссылается не на содержащий его слой." });
  if (typeof value.visible !== "boolean" || typeof value.locked !== "boolean") context.diagnostics.push({ code: "invalid_flags", path, message: "visible и locked должны быть логическими." });
  boundedNumber(value.opacity, `${path}.opacity`, context.diagnostics, 0, 1);
  transform(value.transform, `${path}.transform`, context);
  if (!isRecord(value.stroke)) context.diagnostics.push({ code: "object_required", path: `${path}.stroke`, message: "Ожидается объект." });
  else {
    const strokeKeys = Object.keys(value.stroke);
    for (const key of strokeKeys) if (!["color", "width", "dash"].includes(key)) context.diagnostics.push({ code: "unexpected_key", path: `${path}.stroke.${key}`, message: "Неизвестное поле." });
    for (const key of ["color", "width"]) if (!hasOwn(value.stroke, key)) context.diagnostics.push({ code: "missing_key", path: `${path}.stroke.${key}`, message: "Обязательное поле отсутствует." });
    if (typeof value.stroke.color !== "string" || !COLOR.test(value.stroke.color)) context.diagnostics.push({ code: "invalid_color", path: `${path}.stroke.color`, message: "Нужен цвет #RRGGBB или #RRGGBBAA." });
    expression(value.stroke.width, `${path}.stroke.width`, context);
    if (hasOwn(value.stroke, "dash") && !strokeDashes.has(String(value.stroke.dash))) context.diagnostics.push({ code: "invalid_stroke_dash", path: `${path}.stroke.dash`, message: "Неизвестный тип штриха." });
  }
  if (exact(value.fill, isRecord(value.fill) && hasOwn(value.fill, "hatch") ? ["color", "hatch"] : ["color"], `${path}.fill`, context.diagnostics)) {
    if (value.fill.color !== null && (typeof value.fill.color !== "string" || !COLOR.test(value.fill.color))) context.diagnostics.push({ code: "invalid_color", path: `${path}.fill.color`, message: "Нужен цвет #RRGGBB, #RRGGBBAA или null." });
    if (hasOwn(value.fill, "hatch")) {
      const hatch = value.fill.hatch;
      if (isRecord(hatch) && hatch.backgroundColor !== undefined && hatch.backgroundColor !== null && (typeof hatch.backgroundColor !== "string" || !COLOR.test(hatch.backgroundColor))) context.diagnostics.push({code:"invalid_color",path:`${path}.fill.hatch.backgroundColor`,message:"Некорректный цвет фона штриховки."});
      if (exact(hatch, isRecord(hatch) && hasOwn(hatch, "backgroundColor") ? ["kind", "spacing", "angle", "backgroundColor"] : ["kind", "spacing", "angle"], `${path}.fill.hatch`, context.diagnostics) &&
          (!hatchKinds.includes(hatch.kind as DrawingHatch["kind"]) || typeof hatch.spacing !== "number" || !Number.isFinite(hatch.spacing) || hatch.spacing < 2 || hatch.spacing > 100 || typeof hatch.angle !== "number" || !Number.isFinite(hatch.angle) || Math.abs(hatch.angle) > 360))
        context.diagnostics.push({ code: "invalid_hatch", path: `${path}.fill.hatch`, message: "Некорректный вид, шаг (2–100) или угол штриховки (−360…360)." });
    }
  }
  validateGeometry(value.kind, value.geometry, `${path}.geometry`, context, assetIds);
}

function validateGeometry(kind: unknown, value: unknown, path: string, context: ExpressionContext, assetIds: Set<string>): void {
  if (kind === "line" || kind === "polyline") {
    if (exact(value, ["points", "bendRadius"], path, context.diagnostics)) {
      if (!Array.isArray(value.points) || value.points.length < 2 || value.points.length > 512) context.diagnostics.push({ code: "invalid_points", path: `${path}.points`, message: "Нужно от 2 до 512 точек." }); else value.points.forEach((entry, index) => point(entry, `${path}.points[${index}]`, context));
      expression(value.bendRadius, `${path}.bendRadius`, context);
    }
  } else if (kind === "rectangle") {
    if (exact(value, ["x", "y", "width", "height", "cornerRadii"], path, context.diagnostics)) {
      for (const key of ["x", "y", "width", "height"] as const) expression(value[key], `${path}.${key}`, context);
      if (!Array.isArray(value.cornerRadii) || value.cornerRadii.length !== 4) context.diagnostics.push({ code: "invalid_corner_radii", path: `${path}.cornerRadii`, message: "Нужно четыре радиуса." }); else value.cornerRadii.forEach((entry, index) => expression(entry, `${path}.cornerRadii[${index}]`, context));
    }
  } else if (kind === "ellipse") {
    if (exact(value, ["centerX", "centerY", "radiusX", "radiusY"], path, context.diagnostics)) for (const key of ["centerX", "centerY", "radiusX", "radiusY"] as const) expression(value[key], `${path}.${key}`, context);
  } else if (kind === "bezier") {
    if (exact(value, ["points", "closed"], path, context.diagnostics)) {
      if (!Array.isArray(value.points) || value.points.length < 4 || (value.points.length - 1) % 3 !== 0 || value.points.length > 514) context.diagnostics.push({ code: "invalid_bezier", path: `${path}.points`, message: "Кривая должна содержать 1+3n контрольных точек." }); else value.points.forEach((entry, index) => point(entry, `${path}.points[${index}]`, context));
      if (typeof value.closed !== "boolean") context.diagnostics.push({ code: "invalid_flag", path: `${path}.closed`, message: "closed должен быть логическим." });
    }
  } else if (kind === "closedContour") {
    if (exact(value, ["points"], path, context.diagnostics)) { if (!Array.isArray(value.points) || value.points.length < 3 || value.points.length > 512) context.diagnostics.push({ code: "invalid_contour", path: `${path}.points`, message: "Нужно от 3 до 512 точек." }); else value.points.forEach((entry, index) => point(entry, `${path}.points[${index}]`, context)); }
  } else if (kind === "text") {
    if (exact(value, ["x", "y", "text", "fontSize"], path, context.diagnostics)) { expression(value.x, `${path}.x`, context); expression(value.y, `${path}.y`, context); expression(value.fontSize, `${path}.fontSize`, context); if (typeof value.text !== "string" || value.text.length > TEMPLATE_V2_LIMITS.text) context.diagnostics.push({ code: "invalid_text", path: `${path}.text`, message: "Текст слишком длинный." }); }
  } else if (kind === "image") {
    if (exact(value, ["assetId", "x", "y", "width", "height", "cropX", "cropY", "cropWidth", "cropHeight", "underlay"], path, context.diagnostics)) {
      if (uuid(value.assetId, `${path}.assetId`, context.diagnostics) && !assetIds.has(value.assetId)) context.diagnostics.push({ code: "missing_asset", path: `${path}.assetId`, message: "Asset не найден." });
      for (const key of ["x", "y", "width", "height"] as const) expression(value[key], `${path}.${key}`, context);
      for (const key of ["cropX", "cropY", "cropWidth", "cropHeight"] as const) boundedNumber(value[key], `${path}.${key}`, context.diagnostics, 0, 1);
      if (typeof value.underlay !== "boolean") context.diagnostics.push({ code: "invalid_flag", path: `${path}.underlay`, message: "underlay должен быть логическим." });
    }
  } else if (kind === "group") {
    if (exact(value, ["childIds"], path, context.diagnostics) && (!Array.isArray(value.childIds) || value.childIds.length === 0 || value.childIds.some(id => typeof id !== "string"))) context.diagnostics.push({ code: "invalid_children", path: `${path}.childIds`, message: "Группа должна ссылаться на дочерние ID." });
  }
}

export function validateTemplateContentV2(value: unknown): TemplateV2Validation {
  const diagnostics: TemplateV2Diagnostic[] = [];
  const rootKeys = ["schemaVersion", "views", "logicalContacts", "parameters", "repeaters", "assets", "articleParameterPresets"];
  if (!exact(value, rootKeys, "$", diagnostics)) return { valid: false, diagnostics };
  if (value.schemaVersion !== 2) diagnostics.push({ code: "schema_version", path: "$.schemaVersion", message: "Поддерживается только schemaVersion 2." });
  const arrays = ["views", "logicalContacts", "parameters", "repeaters", "assets", "articleParameterPresets"] as const;
  for (const key of arrays) if (!Array.isArray(value[key])) diagnostics.push({ code: "array_required", path: `$.${key}`, message: "Ожидается массив." });
  if (diagnostics.length) return { valid: false, diagnostics };
  const views = value.views as unknown[], contacts = value.logicalContacts as unknown[], parameters = value.parameters as unknown[], repeaters = value.repeaters as unknown[], assets = value.assets as unknown[], presets = value.articleParameterPresets as unknown[];
  for (const [name, count, limit] of [["views", views.length, TEMPLATE_V2_LIMITS.views], ["logicalContacts", contacts.length, TEMPLATE_V2_LIMITS.contacts], ["parameters", parameters.length, TEMPLATE_V2_LIMITS.parameters], ["repeaters", repeaters.length, TEMPLATE_V2_LIMITS.repeaters], ["assets", assets.length, TEMPLATE_V2_LIMITS.assets], ["articleParameterPresets", presets.length, TEMPLATE_V2_LIMITS.presets]] as const) if (count > limit) diagnostics.push({ code: "limit", path: `$.${name}`, message: `Превышен лимит ${limit}.` });
  const allIds = new Set<string>(), parameterIds = new Set<string>(), numericParameterIds = new Set<string>(), assetIds = new Set<string>(), logicalIds = new Set<string>();

  parameters.forEach((raw, index) => { const path = `$.parameters[${index}]`; if (!exact(raw, ["id", "name", "type", "unit", "defaultValue", "minimum", "maximum", "formula"], path, diagnostics)) return; if (uuid(raw.id, `${path}.id`, diagnostics)) { if (allIds.has(raw.id)) diagnostics.push({ code: "duplicate_id", path: `${path}.id`, message: "ID уже используется." }); else { allIds.add(raw.id); parameterIds.add(raw.id); if (raw.type === "number" || raw.type === "integer") numericParameterIds.add(raw.id); } } shortText(raw.name, `${path}.name`, diagnostics); if (!parameterTypes.has(String(raw.type))) diagnostics.push({ code: "parameter_type", path: `${path}.type`, message: "Неизвестный тип параметра." }); if (raw.unit !== null && (typeof raw.unit !== "string" || raw.unit.length > 32)) diagnostics.push({ code: "invalid_unit", path: `${path}.unit`, message: "Некорректная единица." }); validateParameterValue(raw.defaultValue, raw.type, `${path}.defaultValue`, diagnostics); if (raw.minimum !== null) boundedNumber(raw.minimum, `${path}.minimum`, diagnostics); if (raw.maximum !== null) boundedNumber(raw.maximum, `${path}.maximum`, diagnostics); if (isFiniteNumber(raw.minimum) && isFiniteNumber(raw.maximum) && raw.minimum > raw.maximum) diagnostics.push({ code: "parameter_range", path, message: "minimum больше maximum." }); if (raw.formula !== null && raw.type !== "number" && raw.type !== "integer") diagnostics.push({ code: "parameter_formula_type", path: `${path}.formula`, message: "Формула допустима только у числового параметра." }); });
  const expressionContext: ExpressionContext = { diagnostics, parameterIds, numericParameterIds };
  parameters.forEach((raw, index) => { if (isRecord(raw) && raw.formula !== null && hasOwn(raw, "formula")) expression(raw.formula, `$.parameters[${index}].formula`, expressionContext); });
  validateParameterCycles(parameters, diagnostics);

  assets.forEach((raw, index) => { const path = `$.assets[${index}]`; if (!exact(raw, ["assetId", "fileName", "mediaType", "sha256", "sizeBytes"], path, diagnostics)) return; if (uuid(raw.assetId, `${path}.assetId`, diagnostics)) { if (allIds.has(raw.assetId)) diagnostics.push({ code: "duplicate_id", path: `${path}.assetId`, message: "ID уже используется." }); else { allIds.add(raw.assetId); assetIds.add(raw.assetId); } } shortText(raw.fileName, `${path}.fileName`, diagnostics, 255); if (!assetMedia.has(String(raw.mediaType))) diagnostics.push({ code: "asset_media_type", path: `${path}.mediaType`, message: "Формат изображения не разрешён." }); if (typeof raw.sha256 !== "string" || !SHA256.test(raw.sha256)) diagnostics.push({ code: "asset_sha256", path: `${path}.sha256`, message: "Некорректный SHA-256." }); if (!Number.isSafeInteger(raw.sizeBytes) || Number(raw.sizeBytes) < 1 || Number(raw.sizeBytes) > TEMPLATE_V2_LIMITS.assetBytes) diagnostics.push({ code: "asset_size", path: `${path}.sizeBytes`, message: "Некорректный размер asset." }); });
  const logicalNumbers = new Set<string>();
  contacts.forEach((raw, index) => { const path = `$.logicalContacts[${index}]`; if (!exact(raw, ["id", "number", "name", "contactType"], path, diagnostics)) return; if (uuid(raw.id, `${path}.id`, diagnostics)) { if (allIds.has(raw.id)) diagnostics.push({ code: "duplicate_id", path: `${path}.id`, message: "ID уже используется." }); else { allIds.add(raw.id); logicalIds.add(raw.id); } } if (shortText(raw.number, `${path}.number`, diagnostics, 128)) { if (logicalNumbers.has(raw.number)) diagnostics.push({ code: "duplicate_contact_number", path: `${path}.number`, message: "Номер логического контакта повторяется." }); else logicalNumbers.add(raw.number); } shortText(raw.name, `${path}.name`, diagnostics); if (typeof raw.contactType !== "string" || raw.contactType.length > 128) diagnostics.push({ code: "invalid_contact_type", path: `${path}.contactType`, message: "Некорректный тип контакта." }); });

  const repeatDomains = validateRepeatDomains(repeaters, parameterIds, parameters, logicalIds, allIds, diagnostics);
  let nodeCount = 0, e4 = 0, drawing = 0;
  const groupChildren = new Map<string, string[]>(), nodeLayer = new Map<string, string>(), repeatedGroups = new Map<string, string>();
  views.forEach((raw, viewIndex) => { const path = `$.views[${viewIndex}]`; if (!exact(raw, ["id", "name", "kind", "layers", "contactPoints", "bundlePorts", "repeatPlacements"], path, diagnostics)) return; if (uuid(raw.id, `${path}.id`, diagnostics)) { if (allIds.has(raw.id)) diagnostics.push({ code: "duplicate_id", path: `${path}.id`, message: "ID уже используется." }); else allIds.add(raw.id); } shortText(raw.name, `${path}.name`, diagnostics); if (!viewKinds.has(String(raw.kind))) diagnostics.push({ code: "view_kind", path: `${path}.kind`, message: "Неизвестный вид." }); else { if (raw.kind === "e4") e4++; if (raw.kind === "drawing") drawing++; }
    const viewNodeIds = new Set<string>();
    if (!Array.isArray(raw.layers) || raw.layers.length === 0 || raw.layers.length > TEMPLATE_V2_LIMITS.layers) diagnostics.push({ code: "layers", path: `${path}.layers`, message: "Нужен хотя бы один слой в пределах лимита." }); else raw.layers.forEach((layer, layerIndex) => { const layerPath = `${path}.layers[${layerIndex}]`; if (!exact(layer, ["id", "name", "visible", "locked", "nodes"], layerPath, diagnostics)) return; const layerIdOk = uuid(layer.id, `${layerPath}.id`, diagnostics); if (layerIdOk) { if (allIds.has(layer.id as string)) diagnostics.push({ code: "duplicate_id", path: `${layerPath}.id`, message: "ID уже используется." }); else allIds.add(layer.id as string); } shortText(layer.name, `${layerPath}.name`, diagnostics); if (typeof layer.visible !== "boolean" || typeof layer.locked !== "boolean") diagnostics.push({ code: "invalid_flags", path: layerPath, message: "visible и locked должны быть логическими." }); if (!Array.isArray(layer.nodes)) diagnostics.push({ code: "array_required", path: `${layerPath}.nodes`, message: "Ожидается массив." }); else layer.nodes.forEach((node, nodeIndex) => { nodeCount++; validateNode(node, `${layerPath}.nodes[${nodeIndex}]`, String(layer.id), expressionContext, allIds, assetIds); if (isRecord(node) && typeof node.id === "string") { viewNodeIds.add(node.id); nodeLayer.set(node.id, String(layer.id)); if (node.kind === "group" && isRecord(node.geometry) && Array.isArray(node.geometry.childIds)) groupChildren.set(node.id, node.geometry.childIds.filter((id): id is string => typeof id === "string")); } }); });
    const viewContactPointIds = new Set<string>(), bundlePortIds = new Set<string>(), pointLogicalIds = new Map<string, string>();
    validateViewPoints(raw.contactPoints, `${path}.contactPoints`, logicalIds, expressionContext, allIds, viewContactPointIds, pointLogicalIds, false);
    if(Array.isArray(raw.contactPoints) && Array.isArray(raw.layers)) {
      const shapes=new Set<string>(); const nodes=raw.layers.filter(isRecord).flatMap(l=>Array.isArray(l.nodes)?l.nodes.filter(isRecord):[]);
      for(const p of raw.contactPoints.filter(isRecord)) if(isRecord(p.shape)) {
        const shape=p.shape; const node=nodes.find(n=>n.id===shape.nodeId);
        if(!node || !["rectangle","ellipse"].includes(String(node.kind)) || shapes.has(String(node.id))) diagnostics.push({code:"invalid_shape_contact",path:path+".contactPoints",message:"Контактная фигура должна быть уникальным прямоугольником или эллипсом в этом виде."});
        if(node)shapes.add(String(node.id));
      }
    }
    validateViewPoints(raw.bundlePorts, `${path}.bundlePorts`, logicalIds, expressionContext, allIds, bundlePortIds, pointLogicalIds, true);
    validateRepeatPlacements(raw.repeatPlacements, `${path}.repeatPlacements`, repeatDomains, groupChildren, viewNodeIds, viewContactPointIds, pointLogicalIds, expressionContext, repeatedGroups, diagnostics);
  });
  if (e4 !== 1 || drawing !== 1) diagnostics.push({ code: "required_views", path: "$.views", message: "Нужны ровно по одному виду e4 и drawing." });
  if (nodeCount > TEMPLATE_V2_LIMITS.nodes) diagnostics.push({ code: "limit", path: "$.views", message: `Превышен лимит узлов ${TEMPLATE_V2_LIMITS.nodes}.` });
  validateGroups(groupChildren, nodeLayer, diagnostics);
  validateNestedRepeaters(repeatedGroups, groupChildren, diagnostics);
  validatePresets(presets, parameters, parameterIds, allIds, diagnostics);
  return { valid: diagnostics.length === 0, diagnostics };
}

function validateParameterValue(value: unknown, type: unknown, path: string, diagnostics: TemplateV2Diagnostic[]) {
  const valid = type === "number" ? isFiniteNumber(value) : type === "integer" ? Number.isSafeInteger(value) : type === "boolean" ? typeof value === "boolean" : type === "string" ? typeof value === "string" && value.length <= 1024 : false;
  if (!valid) diagnostics.push({ code: "parameter_value", path, message: "Значение не соответствует типу параметра." });
}
function expressionRefs(value: unknown, result = new Set<string>()): Set<string> { if (!isRecord(value)) return result; if (value.kind === "parameter" && typeof value.parameterId === "string") result.add(value.parameterId); if (value.kind === "negate") expressionRefs(value.operand, result); if (value.kind === "binary") { expressionRefs(value.left, result); expressionRefs(value.right, result); } return result; }
function validateParameterCycles(parameters: unknown[], diagnostics: TemplateV2Diagnostic[]) {
  const formulas = new Map<string, Set<string>>(); parameters.forEach(raw => { if (isRecord(raw) && typeof raw.id === "string" && raw.formula !== null) formulas.set(raw.id, expressionRefs(raw.formula)); });
  const visiting = new Set<string>(), visited = new Set<string>();
  const visit = (id: string): boolean => { if (visiting.has(id)) return true; if (visited.has(id)) return false; visiting.add(id); for (const ref of formulas.get(id) ?? []) if (formulas.has(ref) && visit(ref)) return true; visiting.delete(id); visited.add(id); return false; };
  for (const id of formulas.keys()) if (visit(id)) { diagnostics.push({ code: "expression_cycle", path: "$.parameters", message: "Обнаружен цикл зависимостей параметров." }); break; }
}
function validateViewPoints(value: unknown, path: string, logicalIds: Set<string>, context: ExpressionContext, allIds: Set<string>, pointIds: Set<string>, pointLogicalIds: Map<string, string>, bundle: boolean) {
  if (!Array.isArray(value)) { context.diagnostics.push({ code: "array_required", path, message: "Ожидается массив." }); return; }
  const contactsInView = new Set<string>();
  value.forEach((raw, index) => { const itemPath = `${path}[${index}]`, keys = bundle ? ["id", "name", "x", "y", "direction"] : ["id", "logicalContactId", "x", "y", "direction"]; if (!bundle && isRecord(raw) && hasOwn(raw, "shape")) keys.push("shape"); if (!exact(raw, keys, itemPath, context.diagnostics)) return; if (!bundle && hasOwn(raw,"shape") && exact(raw.shape,["nodeId","fillFromWire"],`${itemPath}.shape`,context.diagnostics)) { uuid(raw.shape.nodeId,`${itemPath}.shape.nodeId`,context.diagnostics); if(typeof raw.shape.fillFromWire!=="boolean") context.diagnostics.push({code:"invalid_shape_contact",path:itemPath,message:"Ожидается переключатель цвета провода."}); } if (uuid(raw.id, `${itemPath}.id`, context.diagnostics)) { if (allIds.has(raw.id)) context.diagnostics.push({ code: "duplicate_id", path: `${itemPath}.id`, message: "ID уже используется." }); else { allIds.add(raw.id); pointIds.add(raw.id); if (!bundle && typeof raw.logicalContactId === "string") pointLogicalIds.set(raw.id, raw.logicalContactId); } } if (bundle) shortText(raw.name, `${itemPath}.name`, context.diagnostics); else if (uuid(raw.logicalContactId, `${itemPath}.logicalContactId`, context.diagnostics)) { if (!logicalIds.has(raw.logicalContactId)) context.diagnostics.push({ code: "missing_logical_contact", path: `${itemPath}.logicalContactId`, message: "Логический контакт не найден." }); if (contactsInView.has(raw.logicalContactId)) context.diagnostics.push({ code: "duplicate_contact_point", path: `${itemPath}.logicalContactId`, message: "В одном виде допустима одна точка логического контакта." }); contactsInView.add(raw.logicalContactId); } expression(raw.x, `${itemPath}.x`, context); expression(raw.y, `${itemPath}.y`, context); if (!directions.has(String(raw.direction))) context.diagnostics.push({ code: "direction", path: `${itemPath}.direction`, message: "Неизвестное направление." }); });
}
function validateGroups(groups: Map<string, string[]>, nodeLayer: Map<string, string>, diagnostics: TemplateV2Diagnostic[]) {
  const owner = new Map<string, string>();
  for (const [group, children] of groups) for (const child of children) { if (!nodeLayer.has(child)) diagnostics.push({ code: "missing_child", path: `group:${group}`, message: `Дочерний узел ${child} не найден.` }); else if (nodeLayer.get(child) !== nodeLayer.get(group)) diagnostics.push({ code: "cross_layer_group", path: `group:${group}`, message: "Группа не может владеть узлом другого слоя." }); const previous = owner.get(child); if (previous && previous !== group) diagnostics.push({ code: "repeated_ownership", path: `group:${group}`, message: "Узел уже принадлежит другой группе." }); else owner.set(child, group); }
  const visiting = new Set<string>(), visited = new Set<string>(); const visit = (id: string): boolean => { if (visiting.has(id)) return true; if (visited.has(id)) return false; visiting.add(id); for (const child of groups.get(id) ?? []) if (groups.has(child) && visit(child)) return true; visiting.delete(id); visited.add(id); return false; };
  for (const id of groups.keys()) if (visit(id)) { diagnostics.push({ code: "group_cycle", path: `group:${id}`, message: "Обнаружен цикл групп." }); break; }
}
function validateRepeatDomains(repeaters: unknown[], parameterIds: Set<string>, parameters: unknown[], logicalIds: Set<string>, allIds: Set<string>, diagnostics: TemplateV2Diagnostic[]): Map<string, Set<string>> {
  const domains = new Map<string, Set<string>>();
  repeaters.forEach((raw, index) => { const path = `$.repeaters[${index}]`; if (!exact(raw, ["id", "countParameterId", "logicalContactIds"], path, diagnostics)) return; let repeatId: string | undefined; if (uuid(raw.id, `${path}.id`, diagnostics)) { repeatId = raw.id; if (allIds.has(raw.id)) diagnostics.push({ code: "duplicate_id", path: `${path}.id`, message: "ID уже используется." }); else allIds.add(raw.id); } if (uuid(raw.countParameterId, `${path}.countParameterId`, diagnostics)) { const parameter = parameters.find(item => isRecord(item) && item.id === raw.countParameterId); if (!parameterIds.has(raw.countParameterId) || !isRecord(parameter) || parameter.type !== "integer") diagnostics.push({ code: "repeater_count", path: `${path}.countParameterId`, message: "Нужен целочисленный параметр количества." }); } validateIdRefs(raw.logicalContactIds, `${path}.logicalContactIds`, logicalIds, diagnostics); if (repeatId && Array.isArray(raw.logicalContactIds)) domains.set(repeatId, new Set(raw.logicalContactIds.filter((id): id is string => typeof id === "string" && logicalIds.has(id)))); });
  return domains;
}
function validateRepeatPlacements(value: unknown, path: string, domains: Map<string, Set<string>>, groups: Map<string, string[]>, viewNodeIds: Set<string>, pointIds: Set<string>, pointLogicalIds: Map<string, string>, context: ExpressionContext, repeatedGroups: Map<string, string>, diagnostics: TemplateV2Diagnostic[]): void {
  if (!Array.isArray(value)) { diagnostics.push({ code: "array_required", path, message: "Ожидается массив." }); return; }
  const domainsInView = new Set<string>();
  value.forEach((raw, index) => { const itemPath = `${path}[${index}]`; if (!exact(raw, ["repeatDomainId", "prototypeGroupId", "step", "contactPointIds", ...(isRecord(raw) && hasOwn(raw, "arrayLayout") ? ["arrayLayout"] : [])], itemPath, diagnostics)) return;
    if (hasOwn(raw, "arrayLayout")) {
      const grid = raw.arrayLayout;
      if (exact(grid, ["rows", "direction", "numbering", "countSource"], `${itemPath}.arrayLayout`, diagnostics)) {
        if (!Number.isInteger(grid.rows) || Number(grid.rows) < 1 || Number(grid.rows) > 4 ||
            !["long-side", "short-side"].includes(String(grid.direction)) ||
            !["new-row", "snake"].includes(String(grid.numbering)) ||
            !["article", "parameter"].includes(String(grid.countSource)))
          diagnostics.push({ code: "array_layout", path: `${itemPath}.arrayLayout`, message: "Некорректная настройка массива." });
      }
    } let domainContacts: Set<string> | undefined; if (uuid(raw.repeatDomainId, `${itemPath}.repeatDomainId`, diagnostics)) { domainContacts = domains.get(raw.repeatDomainId); if (!domainContacts) diagnostics.push({ code: "missing_repeat_domain", path: `${itemPath}.repeatDomainId`, message: "Домен повтора не найден." }); if (domainsInView.has(raw.repeatDomainId)) diagnostics.push({ code: "duplicate_repeat_placement", path: `${itemPath}.repeatDomainId`, message: "В одном виде допустимо одно размещение домена повтора." }); domainsInView.add(raw.repeatDomainId); } if (uuid(raw.prototypeGroupId, `${itemPath}.prototypeGroupId`, diagnostics)) { if (!viewNodeIds.has(raw.prototypeGroupId) || !groups.has(raw.prototypeGroupId)) diagnostics.push({ code: "missing_prototype", path: `${itemPath}.prototypeGroupId`, message: "Группа-прототип не найдена в этом виде." }); const previous = repeatedGroups.get(raw.prototypeGroupId); if (previous) diagnostics.push({ code: "nested_repeater", path: `${itemPath}.prototypeGroupId`, message: "Одну группу нельзя повторять повторно или вкладывать в другой повтор." }); else if (typeof raw.repeatDomainId === "string") repeatedGroups.set(raw.prototypeGroupId, raw.repeatDomainId); } point(raw.step, `${itemPath}.step`, context); validateIdRefs(raw.contactPointIds, `${itemPath}.contactPointIds`, pointIds, diagnostics); if (domainContacts && Array.isArray(raw.contactPointIds)) raw.contactPointIds.forEach((pointId, pointIndex) => { const logicalId = typeof pointId === "string" ? pointLogicalIds.get(pointId) : undefined; if (logicalId && !domainContacts.has(logicalId) && !(hasOwn(raw,"arrayLayout") && domainContacts.size===0)) diagnostics.push({ code: "repeat_contact_mismatch", path: `${itemPath}.contactPointIds[${pointIndex}]`, message: "Точка не принадлежит логическим контактам домена повтора." }); }); });
}
function validateNestedRepeaters(repeatedGroups: Map<string, string>, groups: Map<string, string[]>, diagnostics: TemplateV2Diagnostic[]): void {
  for (const prototype of repeatedGroups.keys()) if ([...descendants(prototype, groups)].some(child => repeatedGroups.has(child))) diagnostics.push({ code: "nested_repeater", path: `group:${prototype}`, message: "Вложенные repeaters запрещены." });
}
function descendants(groupId: string, groups: Map<string, string[]>, result = new Set<string>()): Set<string> { for (const child of groups.get(groupId) ?? []) if (!result.has(child)) { result.add(child); if (groups.has(child)) descendants(child, groups, result); } return result; }
function validateIdRefs(value: unknown, path: string, validIds: Set<string>, diagnostics: TemplateV2Diagnostic[]) { if (!Array.isArray(value)) { diagnostics.push({ code: "array_required", path, message: "Ожидается массив." }); return; } const seen = new Set<string>(); value.forEach((id, index) => { if (typeof id !== "string" || !UUID.test(id) || !validIds.has(id)) diagnostics.push({ code: "missing_reference", path: `${path}[${index}]`, message: "Ссылка не найдена." }); else if (seen.has(id)) diagnostics.push({ code: "duplicate_reference", path: `${path}[${index}]`, message: "Ссылка повторяется." }); else seen.add(id); }); }
function validatePresets(presets: unknown[], parameters: unknown[], parameterIds: Set<string>, allIds: Set<string>, diagnostics: TemplateV2Diagnostic[]) { presets.forEach((raw, index) => { const path = `$.articleParameterPresets[${index}]`; if (!exact(raw, ["id", "sourceId", "entityType", "articleKey", "values"], path, diagnostics)) return; if (uuid(raw.id, `${path}.id`, diagnostics)) { if (allIds.has(raw.id)) diagnostics.push({ code: "duplicate_id", path: `${path}.id`, message: "ID уже используется." }); else allIds.add(raw.id); } shortText(raw.sourceId, `${path}.sourceId`, diagnostics, 128); shortText(raw.entityType, `${path}.entityType`, diagnostics, 64); shortText(raw.articleKey, `${path}.articleKey`, diagnostics, 512); if (!Array.isArray(raw.values)) diagnostics.push({ code: "array_required", path: `${path}.values`, message: "Ожидается массив." }); else { const seen = new Set<string>(); raw.values.forEach((entry, valueIndex) => { const valuePath = `${path}.values[${valueIndex}]`; if (!exact(entry, ["parameterId", "value"], valuePath, diagnostics)) return; if (uuid(entry.parameterId, `${valuePath}.parameterId`, diagnostics)) { if (!parameterIds.has(entry.parameterId)) diagnostics.push({ code: "missing_parameter", path: `${valuePath}.parameterId`, message: "Параметр не найден." }); if (seen.has(entry.parameterId)) diagnostics.push({ code: "duplicate_parameter", path: `${valuePath}.parameterId`, message: "Значение параметра повторяется." }); seen.add(entry.parameterId); const parameter = parameters.find(item => isRecord(item) && item.id === entry.parameterId); if (isRecord(parameter)) validateParameterValue(entry.value, parameter.type, `${valuePath}.value`, diagnostics); } }); } }); }

function derivedUuid(seed: string): string {
  let a = 0x811c9dc5, b = 0x9e3779b9, c = 0x85ebca6b, d = 0xc2b2ae35;
  for (let i = 0; i < seed.length; i++) { const code = seed.charCodeAt(i); a = Math.imul(a ^ code, 0x01000193); b = Math.imul(b ^ code, 0x27d4eb2d); c = Math.imul(c ^ code, 0x165667b1); d = Math.imul(d ^ code, 0x85ebca77); }
  const hex = [a, b, c, d].map(value => (value >>> 0).toString(16).padStart(8, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-5${hex.slice(13, 16)}-${((parseInt(hex.slice(16, 18), 16) & 0x3f) | 0x80).toString(16).padStart(2, "0")}${hex.slice(18, 20)}-${hex.slice(20, 32)}`;
}
function stableId(value: unknown, seed: string, path: string, diagnostics: TemplateV2Diagnostic[]): string { if (typeof value === "string" && UUID.test(value)) return value; const id = derivedUuid(`${seed}:${String(value ?? "")}`); diagnostics.push({ code: "legacy_id_replaced", path, message: `Устаревший ID заменён детерминированным UUID ${id}.` }); return id; }
function v1Primitive(raw: Record<string, unknown>, layerId: string, seed: string, path: string, diagnostics: TemplateV2Diagnostic[]): TemplateNodeV2 {
  const id = stableId(raw.id, `${seed}:primitive`, `${path}.id`, diagnostics), x = isFiniteNumber(raw.x) ? raw.x : 0, y = isFiniteNumber(raw.y) ? raw.y : 0, width = isFiniteNumber(raw.width) ? raw.width : 0, height = isFiniteNumber(raw.height) ? raw.height : 0;
  const kind = raw.kind === "ellipse" ? "ellipse" : raw.kind === "text" ? "text" : raw.kind === "line" ? "line" : "rectangle";
  const base = { id, kind, layerId, visible: true, locked: false, opacity: 1, transform: identity(), stroke: { color: typeof raw.color === "string" && COLOR.test(raw.color) ? raw.color : "#27445a", width: constant(2) }, fill: { color: null } };
  if (kind === "line") return { ...base, kind, geometry: { points: [{ x: constant(x), y: constant(y) }, { x: constant(x + width), y: constant(y + height) }], bendRadius: constant(0) } };
  if (kind === "ellipse") return { ...base, kind, geometry: { centerX: constant(x + width / 2), centerY: constant(y + height / 2), radiusX: constant(Math.abs(width / 2)), radiusY: constant(Math.abs(height / 2)) } };
  if (kind === "text") return { ...base, kind, geometry: { x: constant(x), y: constant(y), text: typeof raw.text === "string" ? raw.text : "", fontSize: constant(18) } };
  return { ...base, kind: "rectangle", geometry: { x: constant(x), y: constant(y), width: constant(width), height: constant(height), cornerRadii: [constant(0), constant(0), constant(0), constant(0)] } };
}

export function upgradeTemplateContentV1ToV2(value: unknown): TemplateV2Upgrade {
  const diagnostics: TemplateV2Diagnostic[] = [];
  if (!isRecord(value) || value.schemaVersion !== 1 || !Array.isArray(value.views)) throw new Error("Ожидается шаблон schemaVersion 1.");
  const logicalContacts: LogicalContactV2[] = [], numberToLogical = new Map<string, string>(), ambiguous = new Set<string>();
  const seenPerView = value.views.map(raw => { const counts = new Map<string, number>(); if (isRecord(raw) && Array.isArray(raw.contactPoints)) raw.contactPoints.forEach(point => { if (isRecord(point)) { const number = String(point.contactNumber ?? "").trim(); counts.set(number, (counts.get(number) ?? 0) + 1); } }); return counts; });
  seenPerView.forEach(counts => counts.forEach((count, number) => { if (!number || count > 1) ambiguous.add(number); }));
  const views: TemplateViewV2[] = value.views.map((raw, viewIndex) => {
    if (!isRecord(raw)) throw new Error(`Некорректный вид v1 [${viewIndex}].`);
    const viewId = stableId(raw.id, `view:${viewIndex}`, `$.views[${viewIndex}].id`, diagnostics);
    const layerId = derivedUuid(`${viewId}:default-layer`);
    const primitives = Array.isArray(raw.primitives) ? raw.primitives.map((primitive, index) => v1Primitive(isRecord(primitive) ? primitive : {}, layerId, viewId, `$.views[${viewIndex}].primitives[${index}]`, diagnostics)) : [];
    const contactPoints: ViewContactPointV2[] = Array.isArray(raw.contactPoints) ? raw.contactPoints.map((pointRaw, pointIndex) => {
      const point = isRecord(pointRaw) ? pointRaw : {}, number = String(point.contactNumber ?? "").trim(), pointPath = `$.views[${viewIndex}].contactPoints[${pointIndex}]`;
      let logicalId = !ambiguous.has(number) ? numberToLogical.get(number) : undefined;
      if (logicalId) diagnostics.push({ code: "logical_contact_mapping_inferred", path: pointPath, message: "Логическая связь с контактом другого вида выведена из совпавшего номера контакта." });
      if (!logicalId) { logicalId = derivedUuid(`${viewId}:logical:${String(point.id ?? pointIndex)}`); logicalContacts.push({ id: logicalId, number: number || String(pointIndex + 1), name: typeof point.name === "string" ? point.name : `Контакт ${pointIndex + 1}`, contactType: "" }); if (!ambiguous.has(number) && number) numberToLogical.set(number, logicalId); }
      if (ambiguous.has(number) || !number) diagnostics.push({ code: "logical_contact_mapping_ambiguous", path: pointPath, message: "Логическую связь между видами нельзя определить однозначно; создан отдельный контакт." });
      return { id: stableId(point.id, `${viewId}:point:${pointIndex}`, `${pointPath}.id`, diagnostics), logicalContactId: logicalId!, x: constant(isFiniteNumber(point.x) ? point.x : 0), y: constant(isFiniteNumber(point.y) ? point.y : 0), direction: directions.has(String(point.direction)) ? point.direction as ContactDirectionV2 : "right" };
    }) : [];
    return { id: viewId, name: typeof raw.name === "string" ? raw.name : `Вид ${viewIndex + 1}`, kind: viewKinds.has(String(raw.kind)) ? raw.kind as ViewKindV2 : "additional", layers: [{ id: layerId, name: "Основной", visible: true, locked: false, nodes: primitives }], contactPoints, bundlePorts: [], repeatPlacements: [] };
  });
  const content: TemplateContentV2 = { schemaVersion: 2, views, logicalContacts, parameters: [], repeaters: [], assets: [], articleParameterPresets: [] };
  const validation = validateTemplateContentV2(content);
  if (!validation.valid) diagnostics.push(...validation.diagnostics.map(item => ({ ...item, code: `upgrade_${item.code}` })));
  return { content, diagnostics };
}
