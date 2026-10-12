import { useEffect, useState, type FormEvent, type KeyboardEvent } from "react";
import { TEMPLATE_V2_LIMITS, type RepeatDomainV2, type TemplateContentV2, type TemplateNodeV2, type TemplateParameterV2, type ViewContactPointV2 } from "./template-model-v2";
import { resolveTemplateParameterValuesV2 } from "./template-repeat-v2";
import "./TemplateParametersPanelV2.css";

export interface CreateTemplateRepeatInputV2 {
  readonly viewId: string;
  readonly layerId: string;
  readonly prototypeNodeId: string;
  readonly contactPointId: string;
  readonly count: number;
  readonly stepX: number;
  readonly stepY: number;
}

export interface PlaceTemplateRepeatInActiveViewInputV2 {
  readonly repeatDomainId: string;
  readonly prototypeNodeId: string;
  readonly stepX: number;
  readonly stepY: number;
}

export type TemplateNodeDimensionV2 = "width" | "height" | "radiusX" | "radiusY";

export interface ParameterizeTemplateNodeDimensionInputV2 {
  readonly viewId: string;
  readonly layerId: string;
  readonly nodeId: string;
  readonly dimension: TemplateNodeDimensionV2;
  readonly name: string;
  readonly unit: string | null;
  readonly defaultValue: number;
  readonly minimum: number;
  readonly maximum: number;
}

export interface TemplateParametersPanelV2Props {
  readonly content: TemplateContentV2;
  readonly activeViewId: string;
  readonly activeLayerId: string | null;
  readonly selectedNodeId: string | null;
  readonly onCreateRepeat: (input: CreateTemplateRepeatInputV2) => void;
  readonly onPlaceRepeatInActiveView?: (input: PlaceTemplateRepeatInActiveViewInputV2) => void;
  readonly onSetCount: (domainId: string, count: number) => void;
  readonly onSetStep: (viewId: string, domainId: string, x: number, y: number) => void;
  readonly onDeleteRepeat: (domainId: string) => void;
  readonly onPreviewValues: (values: Readonly<Record<string, number>>) => void;
  readonly onParameterizeNodeDimension: (input: ParameterizeTemplateNodeDimensionInputV2) => void;
  readonly onSetParameterDefault: (parameterId: string, value: number) => void;
}

const MIN_REPEAT_COUNT = 1;
const MAX_REPEAT_COUNT = 1_000;

export function parseRepeatCountV2(value: string | number): number | null {
  const parsed = typeof value === "number" ? value : Number(value.trim());
  return Number.isSafeInteger(parsed) && parsed >= MIN_REPEAT_COUNT && parsed <= MAX_REPEAT_COUNT ? parsed : null;
}

export function parseRepeatStepV2(value: string | number): number | null {
  const normalized = typeof value === "number" ? value : value.trim();
  if (normalized === "") return null;
  const parsed = typeof normalized === "number" ? normalized : Number(normalized);
  return Number.isFinite(parsed) && Math.abs(parsed) <= TEMPLATE_V2_LIMITS.coordinate ? parsed : null;
}

export function calculatedRepeatContactCountV2(count: number, contactsInPrototype: number): number | null {
  if (parseRepeatCountV2(count) === null || !Number.isSafeInteger(contactsInPrototype) || contactsInPrototype < 0) return null;
  const total = count * contactsInPrototype;
  return Number.isSafeInteger(total) ? total : null;
}

export function repeatPreviewValuesV2(content: TemplateContentV2, parameterId: string, count: number): Readonly<Record<string, number>> {
  const values: Record<string, number> = {};
  for (const domain of content.repeaters) {
    const parameter = content.parameters.find(item => item.id === domain.countParameterId);
    const current = domain.countParameterId === parameterId && parameter?.formula === null
      ? parseRepeatCountV2(count)
      : parameterCount(content, domain.countParameterId);
    if (current !== null) values[domain.countParameterId] = current;
  }
  return values;
}

export function isTopLevelNodeV2(content: TemplateContentV2, viewId: string, layerId: string | null, nodeId: string | null): boolean {
  if (!layerId || !nodeId) return false;
  const view = content.views.find(item => item.id === viewId);
  const layer = view?.layers.find(item => item.id === layerId);
  if (!view || !layer?.nodes.some(node => node.id === nodeId)) return false;
  const childIds = new Set(view.layers.flatMap(item => item.nodes.flatMap(node => node.kind === "group" ? node.geometry.childIds : [])));
  return !childIds.has(nodeId);
}

function repeatedPrototypeNodeIdsV2(content: TemplateContentV2): ReadonlySet<string> {
  const nodes = new Map(content.views.flatMap(view => view.layers.flatMap(layer => layer.nodes)).map(node => [node.id, node]));
  const used = new Set<string>();
  const addTree = (nodeId: string) => {
    if (used.has(nodeId)) return;
    used.add(nodeId);
    const node = nodes.get(nodeId);
    if (node?.kind === "group") node.geometry.childIds.forEach(addTree);
  };
  content.views.forEach(view => view.repeatPlacements.forEach(placement => addTree(placement.prototypeGroupId)));
  return used;
}

export function availableRepeatContactPointsV2(content: TemplateContentV2, viewId: string): readonly ViewContactPointV2[] {
  const view = content.views.find(item => item.id === viewId);
  if (!view) return [];
  const usedPointIds = new Set(content.views.flatMap(item => item.repeatPlacements.flatMap(placement => placement.contactPointIds)));
  const usedLogicalContactIds = new Set(content.repeaters.flatMap(domain => domain.logicalContactIds));
  return view.contactPoints.filter(point => !usedPointIds.has(point.id) && !usedLogicalContactIds.has(point.logicalContactId));
}

export function canCreateRepeatV2(content: TemplateContentV2, viewId: string, layerId: string | null, nodeId: string | null): boolean {
  const node = content.views.find(view => view.id === viewId)?.layers.find(layer => layer.id === layerId)?.nodes.find(item => item.id === nodeId);
  return content.repeaters.length < TEMPLATE_V2_LIMITS.repeaters &&
    node?.kind !== "group" &&
    isTopLevelNodeV2(content, viewId, layerId, nodeId) &&
    Boolean(nodeId && !repeatedPrototypeNodeIdsV2(content).has(nodeId));
}

export function repeatPlacementViewNamesV2(content: TemplateContentV2, domainId: string): readonly string[] {
  return content.views
    .filter(view => view.repeatPlacements.some(placement => placement.repeatDomainId === domainId))
    .map(view => view.name);
}

export function unplacedRepeatDomainsV2(content: TemplateContentV2, viewId: string): readonly RepeatDomainV2[] {
  const placedDomainIds = new Set(content.views.find(view => view.id === viewId)?.repeatPlacements.map(placement => placement.repeatDomainId) ?? []);
  return content.repeaters.filter(domain => !placedDomainIds.has(domain.id));
}

export function missingRepeatLogicalContactIdsV2(content: TemplateContentV2, viewId: string, domainId: string): readonly string[] {
  const domain = content.repeaters.find(item => item.id === domainId);
  const placedLogicalContactIds = new Set(content.views.find(view => view.id === viewId)?.contactPoints.map(point => point.logicalContactId) ?? []);
  return domain?.logicalContactIds.filter(logicalContactId => !placedLogicalContactIds.has(logicalContactId)) ?? [];
}

export function canUseNodeForRepeatPlacementV2(content: TemplateContentV2, viewId: string, layerId: string | null, nodeId: string | null): boolean {
  if (!isTopLevelNodeV2(content, viewId, layerId, nodeId) || !layerId || !nodeId) return false;
  const layer = content.views.find(view => view.id === viewId)?.layers.find(item => item.id === layerId);
  const node = layer?.nodes.find(item => item.id === nodeId);
  return Boolean(layer && !layer.locked && node && !node.locked && !repeatedPrototypeNodeIdsV2(content).has(nodeId));
}

const dimensionLabels: Readonly<Record<TemplateNodeDimensionV2, string>> = {
  width: "ширину", height: "высоту", radiusX: "радиус X", radiusY: "радиус Y",
};

export interface AvailableNodeDimensionV2 {
  readonly dimension: TemplateNodeDimensionV2;
  readonly label: string;
  readonly defaultValue: number;
}

export interface TemplateDimensionRangeV2 {
  readonly defaultValue: number;
  readonly minimum: number;
  readonly maximum: number;
}

export function parseTemplateDimensionRangeV2(defaultValue: string | number, minimum: string | number, maximum: string | number): TemplateDimensionRangeV2 | null {
  const parse = (value: string | number) => {
    const normalized = typeof value === "number" ? value : value.trim();
    if (normalized === "") return null;
    const parsed = typeof normalized === "number" ? normalized : Number(normalized);
    return Number.isFinite(parsed) ? parsed : null;
  };
  const parsedDefault = parse(defaultValue), parsedMinimum = parse(minimum), parsedMaximum = parse(maximum);
  if (parsedDefault === null || parsedMinimum === null || parsedMaximum === null ||
      parsedMinimum <= 0 || parsedMinimum > parsedDefault || parsedDefault > parsedMaximum || parsedMaximum > TEMPLATE_V2_LIMITS.coordinate) return null;
  return { defaultValue: parsedDefault, minimum: parsedMinimum, maximum: parsedMaximum };
}

export function availableNodeDimensionsV2(node: TemplateNodeV2 | null): readonly AvailableNodeDimensionV2[] {
  const candidates: ReadonlyArray<readonly [TemplateNodeDimensionV2, { readonly kind: string; readonly value?: number }]> =
    node?.kind === "rectangle" || node?.kind === "image"
      ? [["width", node.geometry.width], ["height", node.geometry.height]]
      : node?.kind === "ellipse"
        ? [["radiusX", node.geometry.radiusX], ["radiusY", node.geometry.radiusY]]
        : [];
  return candidates.flatMap(([dimension, expression]) => expression.kind === "constant" && typeof expression.value === "number"
    ? [{ dimension, label: `Параметризовать ${dimensionLabels[dimension]}`, defaultValue: expression.value }]
    : []);
}

export function parameterizeDimensionFormKeyV2(nodeId: string, dimensions: readonly AvailableNodeDimensionV2[]): string {
  return `${nodeId}:${dimensions.map(item => `${item.dimension}=${item.defaultValue}`).join(",")}`;
}

export function parseTemplateParameterDefaultV2(value: string | number, parameter: TemplateParameterV2): number | null {
  const normalized = typeof value === "number" ? value : value.trim();
  if (normalized === "") return null;
  const parsed = typeof normalized === "number" ? normalized : Number(normalized);
  if (!Number.isFinite(parsed) || Math.abs(parsed) > TEMPLATE_V2_LIMITS.coordinate) return null;
  if (parameter.type === "integer" && !Number.isSafeInteger(parsed)) return null;
  if (parameter.type !== "number" && parameter.type !== "integer") return null;
  if (parameter.minimum !== null && parsed < parameter.minimum) return null;
  if (parameter.maximum !== null && parsed > parameter.maximum) return null;
  return parsed;
}

function resolvedNumericParameterValuesV2(content: TemplateContentV2): ReadonlyMap<string, number> {
  try {
    const values = resolveTemplateParameterValuesV2(content);
    return new Map([...values].flatMap(([id, value]) => typeof value === "number" ? [[id, value] as const] : []));
  } catch {
    return new Map();
  }
}

function NamedParameterRow({ parameter, resolvedValue, onSetParameterDefault }: {
  readonly parameter: TemplateParameterV2;
  readonly resolvedValue: number | null;
  readonly onSetParameterDefault: TemplateParametersPanelV2Props["onSetParameterDefault"];
}) {
  const formula = parameter.formula !== null;
  const sourceValue = formula ? resolvedValue : typeof parameter.defaultValue === "number" ? parameter.defaultValue : null;
  const [value, setValue] = useState(String(sourceValue ?? ""));
  const parsed = parseTemplateParameterDefaultV2(value, parameter);
  useEffect(() => setValue(String(sourceValue ?? "")), [sourceValue]);
  const save = () => {
    if (!formula && parsed !== null && parsed !== parameter.defaultValue) onSetParameterDefault(parameter.id, parsed);
  };
  const saveOnEnter = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") { event.preventDefault(); save(); }
  };
  return (
    <li className="template-parameter-v2-row" data-parameter-id={parameter.id}>
      <span><strong>{parameter.name}</strong><small>{parameter.unit || "без единицы"}</small></span>
      <label>
        <span className="visually-hidden">Значение параметра {parameter.name}</span>
        <input type="number" step={parameter.type === "integer" ? "1" : "any"} value={value} disabled={formula} aria-invalid={parsed === null} onChange={event => setValue(event.target.value)} onBlur={save} onKeyDown={saveOnEnter} />
      </label>
      {formula && <small className="template-parameter-v2-readonly">по формуле</small>}
    </li>
  );
}

function NamedDimensions({ content, onSetParameterDefault }: Pick<TemplateParametersPanelV2Props, "content" | "onSetParameterDefault">) {
  const repeatCountIds = new Set(content.repeaters.map(domain => domain.countParameterId));
  const parameters = content.parameters.filter(parameter =>
    (parameter.type === "number" || parameter.type === "integer") && !repeatCountIds.has(parameter.id));
  const resolved = resolvedNumericParameterValuesV2(content);
  return (
    <section className="template-dimensions-v2" aria-label="Именованные размеры">
      <div className="template-dimensions-v2-heading"><strong>Именованные размеры</strong><span>{parameters.length}</span></div>
      {parameters.length ? <ul>{parameters.map(parameter => <NamedParameterRow key={parameter.id} parameter={parameter} resolvedValue={resolved.get(parameter.id) ?? null} onSetParameterDefault={onSetParameterDefault} />)}</ul>
        : <p>Размеры ещё не созданы. Параметры количества повторов показаны отдельно.</p>}
    </section>
  );
}

function ParameterizeDimensionForm({ viewId, layerId, node, dimensions, onParameterizeNodeDimension }: {
  readonly viewId: string;
  readonly layerId: string;
  readonly node: TemplateNodeV2;
  readonly dimensions: readonly AvailableNodeDimensionV2[];
  readonly onParameterizeNodeDimension: TemplateParametersPanelV2Props["onParameterizeNodeDimension"];
}) {
  const [chosenDimension, setChosenDimension] = useState<TemplateNodeDimensionV2>(dimensions[0]!.dimension);
  const [name, setName] = useState("");
  const [unit, setUnit] = useState("мм");
  const dimension = dimensions.some(item => item.dimension === chosenDimension) ? chosenDimension : dimensions[0]!.dimension;
  const selectedDimension = dimensions.find(item => item.dimension === dimension)!;
  const [defaultValue, setDefaultValue] = useState(String(selectedDimension.defaultValue));
  const [minimum, setMinimum] = useState("0.001");
  const [maximum, setMaximum] = useState(String(TEMPLATE_V2_LIMITS.coordinate));
  const range = parseTemplateDimensionRangeV2(defaultValue, minimum, maximum);
  const chooseDimension = (next: TemplateNodeDimensionV2) => {
    const nextDimension = dimensions.find(item => item.dimension === next) ?? dimensions[0]!;
    setChosenDimension(nextDimension.dimension);
    setDefaultValue(String(nextDimension.defaultValue));
    setMinimum("0.001");
    setMaximum(String(TEMPLATE_V2_LIMITS.coordinate));
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const normalizedName = name.trim(), normalizedUnit = unit.trim();
    if (!normalizedName || !range) return;
    onParameterizeNodeDimension({ viewId, layerId, nodeId: node.id, dimension, name: normalizedName, unit: normalizedUnit || null, ...range });
    setName("");
  };
  return (
    <form className="template-dimension-v2-create" aria-label="Параметризовать размер выбранного объекта" onSubmit={submit}>
      <strong>Размер выбранного объекта</strong>
      <select aria-label="Размер для параметризации" value={dimension} onChange={event => chooseDimension(event.target.value as TemplateNodeDimensionV2)}>
        {dimensions.map(item => <option key={item.dimension} value={item.dimension}>{item.label}</option>)}
      </select>
      <div><label>Имя<input value={name} onChange={event => setName(event.target.value)} placeholder="Например, ширина корпуса" /></label><label>Единица<input value={unit} onChange={event => setUnit(event.target.value)} placeholder="мм" /></label></div>
      <div className="template-dimension-v2-range">
        <label>Начальное<input type="number" value={defaultValue} aria-invalid={!range} onChange={event => setDefaultValue(event.target.value)} /></label>
        <label>Минимум<input type="number" value={minimum} aria-invalid={!range} onChange={event => setMinimum(event.target.value)} /></label>
        <label>Максимум<input type="number" value={maximum} aria-invalid={!range} onChange={event => setMaximum(event.target.value)} /></label>
      </div>
      {!range && <p className="template-repeat-v2-error" role="alert">Нужно: 0 &lt; минимум ≤ начальное значение ≤ максимум ≤ {TEMPLATE_V2_LIMITS.coordinate}.</p>}
      <button type="submit" disabled={!name.trim() || !range}>Создать параметр</button>
    </form>
  );
}

function parameterCount(content: TemplateContentV2, parameterId: string): number | null {
  const parameter = content.parameters.find(item => item.id === parameterId);
  if (parameter?.type !== "integer") return null;
  try {
    const resolved = resolveTemplateParameterValuesV2(content).get(parameterId);
    return typeof resolved === "number" ? parseRepeatCountV2(resolved) : null;
  } catch {
    return null;
  }
}

function constantValue(expression: { readonly kind: string; readonly value?: number }): number | null {
  return expression.kind === "constant" ? parseRepeatStepV2(expression.value ?? Number.NaN) : null;
}

function inputError(count: string, stepX: string, stepY: string): string | null {
  if (parseRepeatCountV2(count) === null) return "Количество должно быть целым числом от 1 до 1000.";
  if (parseRepeatStepV2(stepX) === null || parseRepeatStepV2(stepY) === null) return "Шаг должен быть конечным числом в допустимом диапазоне.";
  return null;
}

interface CreateRepeatFormProps extends Pick<TemplateParametersPanelV2Props,
  "content" | "activeViewId" | "activeLayerId" | "selectedNodeId" | "onCreateRepeat"> {
  readonly contactPoints: readonly ViewContactPointV2[];
}

function CreateRepeatForm({ content, activeViewId, activeLayerId, selectedNodeId, contactPoints, onCreateRepeat }: CreateRepeatFormProps) {
  const [chosenContactPointId, setChosenContactPointId] = useState(contactPoints[0]?.id ?? "");
  const [count, setCount] = useState("2");
  const [stepX, setStepX] = useState("0");
  const [stepY, setStepY] = useState("20");
  const contactPointId = contactPoints.some(point => point.id === chosenContactPointId) ? chosenContactPointId : contactPoints[0]?.id ?? "";
  const error = inputError(count, stepX, stepY);
  const ready = Boolean(activeLayerId && selectedNodeId && contactPointId && !error);

  const changeCount = (value: string) => {
    setCount(value);
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const parsedCount = parseRepeatCountV2(count), parsedX = parseRepeatStepV2(stepX), parsedY = parseRepeatStepV2(stepY);
    if (!activeLayerId || !selectedNodeId || !contactPointId || parsedCount === null || parsedX === null || parsedY === null) return;
    onCreateRepeat({ viewId: activeViewId, layerId: activeLayerId, prototypeNodeId: selectedNodeId, contactPointId, count: parsedCount, stepX: parsedX, stepY: parsedY });
  };

  return (
    <form className="template-repeat-v2-create" aria-label="Новый повторяемый сегмент" onSubmit={submit}>
      <strong>Повторяемый сегмент</strong>
      <p>Выбранный объект станет прототипом. Его контакт и геометрия будут повторяться с заданным шагом.</p>
      <label>
        Точка контакта
        <select value={contactPointId} onChange={event => setChosenContactPointId(event.target.value)} disabled={!contactPoints.length}>
          {!contactPoints.length && <option value="">Нет свободных точек в активном виде</option>}
          {contactPoints.map(point => {
            const logical = content.logicalContacts.find(item => item.id === point.logicalContactId);
            const label = logical ? `${logical.number} · ${logical.name}` : point.id;
            return <option key={point.id} value={point.id}>{label}</option>;
          })}
        </select>
      </label>
      <div className="template-repeat-v2-grid">
        <label>Количество<input type="number" min={MIN_REPEAT_COUNT} max={MAX_REPEAT_COUNT} step="1" value={count} onChange={event => changeCount(event.target.value)} /></label>
        <label>Шаг X<input type="number" value={stepX} onChange={event => setStepX(event.target.value)} /></label>
        <label>Шаг Y<input type="number" value={stepY} onChange={event => setStepY(event.target.value)} /></label>
      </div>
      <output>{parseRepeatCountV2(count) === null ? "Проверьте количество" : `Вычислено контактов: ${parseRepeatCountV2(count)}`}</output>
      {error && <p className="template-repeat-v2-error" role="alert">{error}</p>}
      {!contactPoints.length && <p className="template-repeat-v2-hint">Добавьте точку контакта или освободите существующую точку от другого повтора.</p>}
      <button type="submit" disabled={!ready}>Сделать повторяемым</button>
    </form>
  );
}

interface PlaceRepeatFormProps extends Pick<TemplateParametersPanelV2Props,
  "content" | "activeViewId" | "activeLayerId" | "selectedNodeId" | "onPlaceRepeatInActiveView"> {
  readonly domains: readonly RepeatDomainV2[];
}

function PlaceRepeatInActiveViewForm({ content, activeViewId, activeLayerId, selectedNodeId, domains, onPlaceRepeatInActiveView }: PlaceRepeatFormProps) {
  const [chosenDomainId, setChosenDomainId] = useState(domains[0]?.id ?? "");
  const [stepX, setStepX] = useState("0");
  const [stepY, setStepY] = useState("20");
  const domain = domains.find(item => item.id === chosenDomainId) ?? domains[0] ?? null;
  const domainId = domain?.id ?? "";
  const missingLogicalContactIds = domain ? missingRepeatLogicalContactIdsV2(content, activeViewId, domain.id) : [];
  const selectedNodeReady = canUseNodeForRepeatPlacementV2(content, activeViewId, activeLayerId, selectedNodeId);
  const stepXValue = parseRepeatStepV2(stepX), stepYValue = parseRepeatStepV2(stepY);
  const ready = Boolean(onPlaceRepeatInActiveView && domain && selectedNodeId && selectedNodeReady && !missingLogicalContactIds.length && stepXValue !== null && stepYValue !== null);
  const missingLabels = missingLogicalContactIds.map(id => {
    const logical = content.logicalContacts.find(item => item.id === id);
    return logical ? `${logical.number} · ${logical.name}` : id;
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!onPlaceRepeatInActiveView || !domain || !selectedNodeId || !selectedNodeReady || missingLogicalContactIds.length || stepXValue === null || stepYValue === null) return;
    onPlaceRepeatInActiveView({ repeatDomainId: domain.id, prototypeNodeId: selectedNodeId, stepX: stepXValue, stepY: stepYValue });
  };

  return (
    <form className="template-repeat-v2-place" aria-label="Разместить повтор в этом виде" onSubmit={submit}>
      <strong>Разместить повтор в этом виде</strong>
      <p>Выбранный объект станет прототипом геометрии для существующего домена.</p>
      <label>
        Домен повтора
        <select value={domainId} onChange={event => setChosenDomainId(event.target.value)}>
          {domains.map(item => {
            const parameter = content.parameters.find(parameter => parameter.id === item.countParameterId);
            return <option key={item.id} value={item.id}>{parameter?.name || "Количество повторов"} · {item.logicalContactIds.length} конт.</option>;
          })}
        </select>
      </label>
      <div className="template-repeat-v2-grid">
        <label>Шаг X<input type="number" value={stepX} aria-invalid={stepXValue === null} onChange={event => setStepX(event.target.value)} /></label>
        <label>Шаг Y<input type="number" value={stepY} aria-invalid={stepYValue === null} onChange={event => setStepY(event.target.value)} /></label>
      </div>
      {!selectedNodeReady && <p className="template-repeat-v2-hint">Выберите свободный объект верхнего уровня в незаблокированном слое.</p>}
      {missingLabels.length > 0 && <p className="template-repeat-v2-hint">Сначала разместите в этом виде точки логических контактов домена: {missingLabels.join(", ")}.</p>}
      {(stepXValue === null || stepYValue === null) && <p className="template-repeat-v2-error" role="alert">Шаг должен быть конечным числом в допустимом диапазоне.</p>}
      <button type="submit" disabled={!ready}>Разместить повтор в этом виде</button>
    </form>
  );
}

interface RepeatCardProps {
  readonly content: TemplateContentV2;
  readonly activeViewId: string;
  readonly domainId: string;
  readonly onSetCount: TemplateParametersPanelV2Props["onSetCount"];
  readonly onSetStep: TemplateParametersPanelV2Props["onSetStep"];
  readonly onDeleteRepeat: TemplateParametersPanelV2Props["onDeleteRepeat"];
  readonly onPreviewValues: TemplateParametersPanelV2Props["onPreviewValues"];
}

function RepeatCard({ content, activeViewId, domainId, onSetCount, onSetStep, onDeleteRepeat, onPreviewValues }: RepeatCardProps) {
  const domain = content.repeaters.find(item => item.id === domainId)!;
  const parameter = content.parameters.find(item => item.id === domain.countParameterId);
  const formulaCount = Boolean(parameter && parameter.formula !== null);
  const placement = content.views.find(item => item.id === activeViewId)?.repeatPlacements.find(item => item.repeatDomainId === domain.id);
  const initialCount = parameterCount(content, domain.countParameterId);
  const initialX = placement ? constantValue(placement.step.x) : null;
  const initialY = placement ? constantValue(placement.step.y) : null;
  const [count, setCount] = useState(String(initialCount ?? ""));
  const [stepX, setStepX] = useState(String(initialX ?? ""));
  const [stepY, setStepY] = useState(String(initialY ?? ""));
  const countValue = parseRepeatCountV2(count);
  const stepXValue = parseRepeatStepV2(stepX), stepYValue = parseRepeatStepV2(stepY);
  const calculated = countValue === null ? null : calculatedRepeatContactCountV2(countValue, domain.logicalContactIds.length);
  const placementViewNames = repeatPlacementViewNamesV2(content, domain.id);

  useEffect(() => setCount(String(initialCount ?? "")), [initialCount]);
  useEffect(() => setStepX(String(initialX ?? "")), [initialX]);
  useEffect(() => setStepY(String(initialY ?? "")), [initialY]);

  const previewCount = (value: string) => {
    if (formulaCount) return;
    setCount(value);
    const parsed = parseRepeatCountV2(value);
    onPreviewValues(parsed === null ? {} : repeatPreviewValuesV2(content, domain.countParameterId, parsed));
  };
  const saveCount = () => {
    if (formulaCount) return;
    const parsed = parseRepeatCountV2(count);
    if (parsed !== null && parsed !== initialCount) onSetCount(domain.id, parsed);
  };
  const saveStep = () => {
    if (placement && stepXValue !== null && stepYValue !== null && (stepXValue !== initialX || stepYValue !== initialY)) {
      onSetStep(activeViewId, domain.id, stepXValue, stepYValue);
    }
  };
  const applyQuickCount = (value: number) => {
    if (formulaCount) return;
    setCount(String(value));
    onPreviewValues(repeatPreviewValuesV2(content, domain.countParameterId, value));
    if (value !== initialCount) onSetCount(domain.id, value);
  };
  const saveOnEnter = (event: KeyboardEvent<HTMLInputElement>, action: () => void) => {
    if (event.key === "Enter") { event.preventDefault(); action(); }
  };

  return (
    <article className="template-repeat-v2-card" data-repeat-domain-id={domain.id}>
      <header>
        <span><strong>{parameter?.name || "Количество контактов"}</strong><small>{placement ? "Размещён в активном виде" : "Нет размещения в активном виде"}</small></span>
        <button type="button" className="template-repeat-v2-delete" onClick={() => onDeleteRepeat(domain.id)} aria-label={`Удалить повтор ${parameter?.name || domain.id} из всех видов`} title="Удалить домен и его размещения во всех видах">×</button>
      </header>
      <div className="template-repeat-v2-quick" aria-label="Быстрый выбор количества">
        <button type="button" disabled={formulaCount} onClick={() => applyQuickCount(2)}>2</button>
        <button type="button" disabled={formulaCount} onClick={() => applyQuickCount(10)}>10</button>
      </div>
      <label>Количество<input type="number" min={MIN_REPEAT_COUNT} max={MAX_REPEAT_COUNT} step="1" value={count} disabled={formulaCount} aria-invalid={countValue === null} onChange={event => previewCount(event.target.value)} onBlur={saveCount} onKeyDown={event => saveOnEnter(event, saveCount)} /></label>
      {formulaCount && <p className="template-repeat-v2-formula">Задано формулой; редактор формул пока только для чтения</p>}
      <div className="template-repeat-v2-grid">
        <label>Шаг X<input type="number" value={stepX} disabled={!placement} aria-invalid={placement && stepXValue === null} onChange={event => setStepX(event.target.value)} onBlur={saveStep} onKeyDown={event => saveOnEnter(event, saveStep)} /></label>
        <label>Шаг Y<input type="number" value={stepY} disabled={!placement} aria-invalid={placement && stepYValue === null} onChange={event => setStepY(event.target.value)} onBlur={saveStep} onKeyDown={event => saveOnEnter(event, saveStep)} /></label>
      </div>
      <output>{calculated === null ? "Проверьте количество" : `Вычислено контактов: ${calculated}`}</output>
      <p className="template-repeat-v2-scope">Охват: {placementViewNames.length ? `${placementViewNames.length} · ${placementViewNames.join(", ")}` : "размещений нет"}</p>
    </article>
  );
}

export function TemplateParametersPanelV2(props: TemplateParametersPanelV2Props) {
  const { content, activeViewId, activeLayerId, selectedNodeId } = props;
  const canCreate = canCreateRepeatV2(content, activeViewId, activeLayerId, selectedNodeId);
  const availableContactPoints = availableRepeatContactPointsV2(content, activeViewId);
  const selectedLayer = content.views.find(view => view.id === activeViewId)?.layers.find(layer => layer.id === activeLayerId);
  const selectedNode = selectedLayer?.nodes.find(node => node.id === selectedNodeId) ?? null;
  const availableDimensions = availableNodeDimensionsV2(selectedNode);
  const unplacedDomains = unplacedRepeatDomainsV2(content, activeViewId);

  return (
    <section className="template-parameters-v2" aria-label="Параметры и повторы шаблона">
      <div className="template-parameters-v2-heading">
        <strong>Параметры</strong>
        <span>{content.repeaters.length}</span>
      </div>
      <NamedDimensions content={content} onSetParameterDefault={props.onSetParameterDefault} />
      {selectedNode && activeLayerId && availableDimensions.length > 0 && <ParameterizeDimensionForm key={parameterizeDimensionFormKeyV2(selectedNode.id, availableDimensions)} viewId={activeViewId} layerId={activeLayerId} node={selectedNode} dimensions={availableDimensions} onParameterizeNodeDimension={props.onParameterizeNodeDimension} />}
      {content.repeaters.length > 0 && (
        <div className="template-repeat-v2-list">
          {content.repeaters.map(domain => <RepeatCard key={domain.id} content={content} activeViewId={activeViewId} domainId={domain.id} onSetCount={props.onSetCount} onSetStep={props.onSetStep} onDeleteRepeat={props.onDeleteRepeat} onPreviewValues={props.onPreviewValues} />)}
        </div>
      )}
      {props.onPlaceRepeatInActiveView && unplacedDomains.length > 0 && <PlaceRepeatInActiveViewForm content={content} activeViewId={activeViewId} activeLayerId={activeLayerId} selectedNodeId={selectedNodeId} domains={unplacedDomains} onPlaceRepeatInActiveView={props.onPlaceRepeatInActiveView} />}
      {canCreate && <CreateRepeatForm content={content} activeViewId={activeViewId} activeLayerId={activeLayerId} selectedNodeId={selectedNodeId} contactPoints={availableContactPoints} onCreateRepeat={props.onCreateRepeat} />}
      {content.repeaters.length === 0 && !canCreate && (
        <p className="template-repeat-v2-empty" role="status">Выберите объект верхнего уровня в активном слое, чтобы настроить повторение.</p>
      )}
    </section>
  );
}
