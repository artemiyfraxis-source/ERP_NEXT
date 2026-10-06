import type { TemplateContentV3, TemplateNodeV3 } from "./template-model-v3";
import type { DrawingTarget, DrawingContactBinding } from "./drawing-bindings";
import { materializeE4ConnectorArticle, type E4ConnectorSeriesTable } from "./e4-connector-series-table";
import { constantExpressionV3 as c, evaluateNumericExpressionV3, projectTemplateContentV3CoreToV2 } from "./template-commands-v3";
import { resolveTemplateParameterValuesV2 } from "./template-repeat-v2";

export const generatorRoles = ["start", "period", "end", "static"] as const;
export type GeneratorRole = typeof generatorRoles[number];
/** Authoring recipe, not duplicated geometry. All IDs refer to the source view. */
export interface DrawingGenerator {
  id: string; viewId: string; target: DrawingTarget;
  axis: "horizontal" | "vertical"; pitch: number; rowPitch: number; rows: number; baseColumns: number;
  traversal: "along" | "across"; numbering: "new-row" | "snake"; reverse: boolean;
  corner: "top-left" | "top-right" | "bottom-left" | "bottom-right";
  roles: Record<GeneratorRole, string[]>;
  periodPointIds: string[]; fixedPointIds: string[]; endPointIds: string[];
  articles: { articleId: string; nodeIds: string[] }[];
}
export function newDrawingGenerator(viewId: string, target: DrawingTarget): DrawingGenerator {
  return { id: crypto.randomUUID(), viewId, target, axis: "horizontal", pitch: 40, rowPitch: 40,
    rows: 1, baseColumns: 1, traversal: "along", numbering: "new-row", reverse: false, corner: "top-left",
    roles: { start: [], period: [], end: [], static: [] }, periodPointIds: [], fixedPointIds: [], endPointIds: [], articles: [] };
}

/** A legacy array is copied into a source view; its old article assignments remain untouched. */
export function generatorFromLegacyArray(content: TemplateContentV3, viewId: string, target: DrawingTarget) {
  const source = content.views.find(v=>v.id===viewId)!;
  if(source.repeatPlacements.length!==1 || !source.repeatPlacements[0]!.arrayLayout) throw new Error("Для переноса выберите вид с одним графическим массивом.");
  const repeat=source.repeatPlacements[0]!,view=structuredClone(source),ids=new Map<string,string>();
  for(const id of [view.id,...view.layers.flatMap(l=>[l.id,...l.nodes.map(n=>n.id)]),...view.contactPoints.map(p=>p.id),...view.bundlePorts.map(p=>p.id)])ids.set(id,crypto.randomUUID());
  view.id=ids.get(view.id)!;view.kind="additional";view.name=`Генератор · ${source.name}`;view.repeatPlacements=[];
  for(const layer of view.layers){layer.id=ids.get(layer.id)!;for(const node of layer.nodes){node.id=ids.get(node.id)!;node.layerId=layer.id;if(node.kind==="group")node.geometry.childIds=node.geometry.childIds.map(id=>ids.get(id)!);}}
  for(const point of view.contactPoints){point.id=ids.get(point.id)!;if(point.shape)point.shape.nodeId=ids.get(point.shape.nodeId)!;}
  for(const point of view.bundlePorts)point.id=ids.get(point.id)!;
  const g=newDrawingGenerator(view.id,target),periodId=ids.get(repeat.prototypeGroupId)!;
  g.roles.period=[periodId];g.periodPointIds=repeat.contactPointIds.map(id=>ids.get(id)!);
  const owned=new Set(view.layers.flatMap(l=>l.nodes.flatMap(n=>n.kind==="group"?n.geometry.childIds:[])));
  g.roles.static=view.layers.flatMap(l=>l.nodes.filter(n=>!owned.has(n.id)&&n.id!==periodId).map(n=>n.id));
  g.fixedPointIds=view.contactPoints.filter(p=>!g.periodPointIds.includes(p.id)).map(p=>p.id);
  g.rows=repeat.arrayLayout!.rows;g.traversal=repeat.arrayLayout!.direction==="long-side"?"along":"across";g.numbering=repeat.arrayLayout!.numbering;
  g.pitch=repeat.step.x.kind==="constant"?repeat.step.x.value:40;g.rowPitch=repeat.step.y.kind==="constant"?repeat.step.y.value:40;
  return {content:{...content,views:[...content.views,view]},generator:g};
}

export function generatorLayout(g: DrawingGenerator, count: number) {
  const repeats = (count - g.fixedPointIds.length) / g.periodPointIds.length;
  if (!Number.isInteger(repeats) || repeats < 1 || repeats > 1000 || repeats % g.rows !== 0)
    throw new Error(`Генератор: ${count} контактов не образуют целые периоды по ${g.periodPointIds.length} в ${g.rows} рядах.`);
  const columns = repeats / g.rows;
  const cells: { column: number; row: number }[] = [];
  const across = g.traversal === "across";
  for (let line = 0; line < (across ? columns : g.rows); line++) {
    const length = across ? g.rows : columns;
    for (let pos = 0; pos < length; pos++) {
      const offset = g.numbering === "snake" && line % 2 ? length - pos - 1 : pos;
      const column = across ? line : offset, row = across ? offset : line;
      cells.push({ column: g.corner.endsWith("right") ? columns - column - 1 : column, row: g.corner.startsWith("bottom") ? g.rows - row - 1 : row });
    }
  }
  if (g.reverse) cells.reverse();
  return { repeats, columns, cells, endOffset: (columns - g.baseColumns) * g.pitch };
}

export function generatorDescendants(content: TemplateContentV3, g: DrawingGenerator, ids: readonly string[]): Set<string> {
  const nodes = content.views.find(v => v.id === g.viewId)?.layers.flatMap(l => l.nodes) ?? [];
  const byId = new Map(nodes.map(n => [n.id, n]));
  const result = new Set<string>();
  const visit = (id: string) => { if (result.has(id)) return; result.add(id); const node = byId.get(id); if (node?.kind === "group") node.geometry.childIds.forEach(visit); };
  ids.forEach(visit); return result;
}

export function assignGeneratorRole(content: TemplateContentV3, g: DrawingGenerator, role: GeneratorRole, selectedIds: readonly string[]): DrawingGenerator {
  const view = content.views.find(v => v.id === g.viewId)!;
  const nodes = view.layers.flatMap(l => l.nodes), owned = new Set(nodes.flatMap(n => n.kind === "group" ? n.geometry.childIds : []));
  const roots = nodes.filter(n => selectedIds.includes(n.id) && !owned.has(n.id)).map(n => n.id);
  const descendants = generatorDescendants(content,g,roots);
  const points = view.contactPoints.filter(p => selectedIds.includes(p.id) || p.shape && descendants.has(p.shape.nodeId)).map(p => p.id);
  if (!roots.length && !points.length) throw new Error("Выделите корневые фигуры и точки контактов исходника.");
  const next = structuredClone(g);
  for (const key of generatorRoles) next.roles[key] = next.roles[key].filter(id => !roots.includes(id));
  next.roles[role] = [...new Set([...next.roles[role], ...roots])];
  next.periodPointIds = next.periodPointIds.filter(id => !points.includes(id));
  next.fixedPointIds = next.fixedPointIds.filter(id => !points.includes(id));
  next.endPointIds = next.endPointIds.filter(id => !points.includes(id));
  if (role === "period") next.periodPointIds.push(...points);
  else next.fixedPointIds.push(...points);
  if (role === "end") next.endPointIds.push(...points);
  return next;
}

/** Preserve role ownership across source grouping/ungrouping and prune deleted refs. */
export function reconcileDrawingGenerators(before: TemplateContentV3, after: TemplateContentV3, generators: readonly DrawingGenerator[]): DrawingGenerator[] {
  return generators.filter(g => after.views.some(v => v.id === g.viewId)).map(g => {
    const view = after.views.find(v => v.id === g.viewId)!;
    const nodes = view.layers.flatMap(l => l.nodes), ids = new Set(nodes.map(n => n.id));
    const children = new Set(nodes.flatMap(n => n.kind === "group" ? n.geometry.childIds : []));
    const points = new Set(view.contactPoints.map(p => p.id));
    const next = structuredClone(g);
    const roots = nodes.filter(n => !children.has(n.id));
    for (const role of generatorRoles) {
      const previous = generatorDescendants(before, g, g.roles[role]);
      next.roles[role] = roots.filter(n => previous.has(n.id) || [...generatorDescendants(after, g, [n.id])].some(id => previous.has(id))).map(n => n.id);
    }
    // A group containing differently owned objects must be rejected by validation,
    // never silently move an article's addition into the shared source.
    next.articles = g.articles.filter(a => after.articleVariants.some(v => v.id === a.articleId)).map(a => {
      const previous = generatorDescendants(before, g, a.nodeIds);
      return {...a, nodeIds: [...new Set([
        ...a.nodeIds.filter(id => ids.has(id)),
        ...roots.filter(n => [...generatorDescendants(after, g, [n.id])].some(id => previous.has(id))).map(n => n.id),
      ])]};
    });
    next.periodPointIds = g.periodPointIds.filter(id => points.has(id));
    next.fixedPointIds = g.fixedPointIds.filter(id => points.has(id));
    next.endPointIds = g.endPointIds.filter(id => points.has(id));
    for(const point of view.contactPoints) if(point.shape) {
      const role=generatorRoles.find(role=>generatorDescendants(after,next,next.roles[role]).has(point.shape!.nodeId));
      if(!role)continue;
      next.periodPointIds=next.periodPointIds.filter(id=>id!==point.id);next.fixedPointIds=next.fixedPointIds.filter(id=>id!==point.id);next.endPointIds=next.endPointIds.filter(id=>id!==point.id);
      (role==="period"?next.periodPointIds:next.fixedPointIds).push(point.id); if(role==="end")next.endPointIds.push(point.id);
    }
    return next;
  });
}

const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
function exact(v: unknown, keys: string[]): asserts v is Record<string, unknown> {
  if (!record(v) || Object.keys(v).length !== keys.length || !keys.every(k => k in v)) throw new Error("Некорректный формат генератора.");
}
function idList(value: unknown, allowed: Set<string>): asserts value is string[] {
  if (!Array.isArray(value) || value.some(id => typeof id !== "string" || !allowed.has(id)) || new Set(value).size !== value.length)
    throw new Error("Объекты генератора отсутствуют или повторяются.");
}
/** Same contract is checked by the server; incomplete recipes may have no assigned articles. */
export function validateDrawingGenerators(content: TemplateContentV3, table: E4ConnectorSeriesTable, value: unknown, authoring = false): asserts value is DrawingGenerator[] {
  if (value === undefined) return;
  if (!Array.isArray(value) || value.length > 32) throw new Error("Ожидается не более 32 генераторов.");
  const ids = new Set<string>(), views = new Set<string>(), targets = new Set<string>();
  for (const raw of value) {
    exact(raw, ["id", "viewId", "target", "axis", "pitch", "rowPitch", "rows", "baseColumns", "traversal", "numbering", "reverse", "corner", "roles", "periodPointIds", "fixedPointIds", "endPointIds", "articles"]);
    if (typeof raw.id !== "string" || !/^[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}$/i.test(raw.id) || ids.has(raw.id)) throw new Error("ID генератора некорректен или повторяется.");
    ids.add(raw.id);
    const view = content.views.find(v => v.id === raw.viewId);
    if (!view || view.kind === "e4" || views.has(view.id)) throw new Error("Выберите отдельный графический вид генератора.");
    views.add(view.id);
    if (!["e4", "drawing", "route"].includes(String(raw.target)) || !["horizontal", "vertical"].includes(String(raw.axis)) || !["along", "across"].includes(String(raw.traversal)) || !["new-row", "snake"].includes(String(raw.numbering)) || typeof raw.reverse !== "boolean") throw new Error("Некорректные настройки генератора.");
    if (!["top-left","top-right","bottom-left","bottom-right"].includes(String(raw.corner))) throw new Error("Некорректный исходный угол нумерации.");
    if (![raw.pitch, raw.rowPitch].every(n => typeof n === "number" && Number.isFinite(n) && n > 0 && n <= 10000) || !Number.isInteger(raw.rows) || Number(raw.rows) < 1 || Number(raw.rows) > 4 || !Number.isInteger(raw.baseColumns) || Number(raw.baseColumns) < 1 || Number(raw.baseColumns) > 1000) throw new Error("Шаг 0…10000, ряды 1…4, базовые колонки 1…1000.");
    if (view.repeatPlacements.length) throw new Error("Генератор требует вид без прежних массивов. Прежние рисунки сохраняются в своих видах.");
    const nodes = view.layers.flatMap(l => l.nodes), nodeIds = new Set(nodes.map(n => n.id));
    const pointIds = new Set(view.contactPoints.map(p => p.id));
    const owned = new Set(nodes.flatMap(n => n.kind === "group" ? n.geometry.childIds : []));
    const claimed = new Set<string>();
    exact(raw.roles, [...generatorRoles]);
    for (const role of generatorRoles) {
      idList(raw.roles[role], nodeIds);
      for (const id of raw.roles[role]) { if (owned.has(id) || claimed.has(id)) throw new Error("Назначайте каждой корневой фигуре одну роль."); claimed.add(id); }
    }
    idList(raw.periodPointIds, pointIds); idList(raw.fixedPointIds, pointIds);
    idList(raw.endPointIds, new Set(raw.fixedPointIds));
    if (raw.periodPointIds.some(id => (raw.fixedPointIds as string[]).includes(id))) throw new Error("Контакт назначен двум ролям.");
    if (!Array.isArray(raw.articles)) throw new Error("Артикулы генератора должны быть массивом.");
    const articleIds = new Set(content.articleVariants.map(a => a.id));
    const seen = new Set<string>();
    for (const article of raw.articles) {
      exact(article, ["articleId", "nodeIds"]);
      if (typeof article.articleId !== "string" || !articleIds.has(article.articleId) || seen.has(article.articleId) || targets.has(`${raw.target}:${article.articleId}`)) throw new Error("Артикул генератора отсутствует или повторяется в разделе.");
      seen.add(article.articleId); targets.add(`${raw.target}:${article.articleId}`);
      idList(article.nodeIds, nodeIds);
      for (const id of article.nodeIds) { if (claimed.has(id)) throw new Error("Дополнение артикула не может быть общей фигурой."); claimed.add(id); }
      const g = raw as unknown as DrawingGenerator;
      if (authoring) continue;
      if (!g.roles.period.length || !g.periodPointIds.length) throw new Error("Назначьте фигуры и контакты периода.");
      const layout = generatorLayout(g, materializeE4ConnectorArticle(table, article.articleId).rows.length);
      if (generatorDescendants(content, g, g.roles.period).size * layout.repeats + nodes.length > 5000) throw new Error("Вариант генератора превышает 5000 объектов.");
    }
    // Child ownership must not cross an article boundary or a source role.
    const base = generatorDescendants(content, raw as unknown as DrawingGenerator, [...claimed].filter(id => generatorRoles.some(r => (raw.roles as Record<string, string[]>)[r]!.includes(id))));
    const extras = new Set<string>();
    for (const article of raw.articles as DrawingGenerator["articles"]) for (const id of generatorDescendants(content, raw as unknown as DrawingGenerator, article.nodeIds)) {
      if (base.has(id) || extras.has(id)) throw new Error("Дополнения разных артикулов не могут владеть одной фигурой."); extras.add(id);
    }
  }
}

/** IDs are derived from immutable source identity and cell position, never numbering. */
function generatedId(g: DrawingGenerator, role: string, cell: string, id: string) { return `generator:${g.id}:${role}:${cell}:${id}`; }
export function materializeGenerator(content: TemplateContentV3, table: E4ConnectorSeriesTable, bindings: readonly DrawingContactBinding[], g: DrawingGenerator, articleId: string, previewPeriods?: number) {
  const source = content.views.find(v => v.id === g.viewId);
  if (!source) throw new Error("Исходный вид удалён. Создайте новый генератор.");
  const pointById = (id: string) => { const point=source.contactPoints.find(p=>p.id===id); if(!point) throw new Error("Контакт исходника удалён. Добавьте контакт и назначьте его периоду заново."); return point; };
  if(!g.periodPointIds.length) throw new Error("Добавьте контакт исходника и назначьте его периоду.");
  let rows = materializeE4ConnectorArticle(table, articleId).rows;
  if (previewPeriods !== undefined) {
    if (!Number.isInteger(previewPeriods) || previewPeriods < 1 || previewPeriods > 1000 || previewPeriods % g.rows !== 0) throw new Error("Проверочное N должно образовать целые ряды, 1…1000 периодов.");
    if (!rows[0]) throw new Error("Добавьте контакты артикула для проверки генератора.");
    const count = previewPeriods * g.periodPointIds.length + g.fixedPointIds.length;
    rows = Array.from({length: count}, (_,i)=>rows[i] ?? {...rows[0]!,seriesRowId:`preview:${i}`,number:String(i+1)});
  }
  const layout = generatorLayout(g, rows.length);
  const values = resolveTemplateParameterValuesV2(projectTemplateContentV3CoreToV2(content), { articlePreset: articleId });
  const evaluate = (v: Parameters<typeof evaluateNumericExpressionV3>[0]) => evaluateNumericExpressionV3(v, values);
  const offset = (along: number, cross = 0) => g.axis === "horizontal" ? { x: along, y: cross } : { x: cross, y: along };
  const fixedRows = g.fixedPointIds.map(id => {
    const point = pointById(id);
    const row = rows.find(r => r.seriesRowId === bindings.find(b => b.logicalContactId === point.logicalContactId)?.seriesRowId);
    if (!row) throw new Error("Неповторяемый контакт не связан со строкой артикула."); return row;
  });
  if (new Set(fixedRows.map(r => r.seriesRowId)).size !== fixedRows.length) throw new Error("Неповторяемые контакты связаны с одной строкой.");
  const repeatedRows = rows.filter(r => !fixedRows.includes(r));
  const allNodes = source.layers.flatMap(l => l.nodes);
  const selectedExtras = generatorDescendants(content, g, g.articles.find(a => a.articleId === articleId)?.nodeIds ?? []);
  const layers = source.layers.map(layer => ({ ...layer, nodes: [] as TemplateNodeV3[] }));
  const emitted = new Map<string, TemplateNodeV3[]>();
  const append = (role: GeneratorRole, cell: string, along: number, cross: number, number?: string) => {
    const ids = generatorDescendants(content, g, g.roles[role]), roots = new Set(g.roles[role]);
    const shift = offset(along, cross);
    for (const original of allNodes) if (ids.has(original.id)) {
      const node = structuredClone(original); node.id = generatedId(g, role, cell, original.id); node.locked = true;
      if (roots.has(original.id)) { node.transform.translateX = c(evaluate(original.transform.translateX) + shift.x); node.transform.translateY = c(evaluate(original.transform.translateY) + shift.y); }
      if (node.kind === "group") node.geometry.childIds = node.geometry.childIds.map(id => generatedId(g, role, cell, id));
      if (node.kind === "text" && number !== undefined) node.geometry.text = node.geometry.text.replaceAll("{{n}}", number);
      emitted.set(original.id, [...(emitted.get(original.id) ?? []), node]);
    }
  };
  // Emit each cell in geometric order; traversal affects only the row assignment.
  for (const role of ["start", "static"] as const) append(role, "fixed", 0, 0);
  for (let row = 0; row < g.rows; row++) for (let column = 0; column < layout.columns; column++) {
    const index = layout.cells.findIndex(p => p.column === column && p.row === row);
    append("period", `${column},${row}`, column * g.pitch, row * g.rowPitch, repeatedRows[index * g.periodPointIds.length]?.number);
  }
  append("end", "fixed", layout.endOffset, 0);
  // Preserve source layer order, then local additions in their original layer.
  for (const node of allNodes) layers.find(l => l.id === node.layerId)!.nodes.push(...(emitted.get(node.id) ?? (selectedExtras.has(node.id) ? [structuredClone(node)] : [])));
  const points: { point: typeof source.contactPoints[number]; row: typeof rows[number] }[] = [];
  const placePoint = (id: string, key: string, along: number, cross: number, row: typeof rows[number]) => {
    const p = pointById(id); const shift = offset(along, cross);
    const role = generatorRoles.find(role=>generatorDescendants(content,g,g.roles[role]).has(p.shape?.nodeId ?? ""));
    if(p.shape && !role) throw new Error("Назначьте контактную фигуру роли генератора.");
    points.push({ row, point: { ...p, ...(p.shape ? {shape:{...p.shape,nodeId:generatedId(g,role!,key,p.shape.nodeId)}} : {}), id: generatedId(g, "point", key, id), logicalContactId: `generator-row:${row.seriesRowId}`, x: c(evaluate(p.x) + shift.x), y: c(evaluate(p.y) + shift.y) } });
  };
  g.fixedPointIds.forEach((id, i) => placePoint(id, "fixed", g.endPointIds.includes(id) ? layout.endOffset : 0, 0, fixedRows[i]!));
  layout.cells.forEach((cell, i) => g.periodPointIds.forEach((id, slot) => placePoint(id, `${cell.column},${cell.row}`, cell.column * g.pitch, cell.row * g.rowPitch, repeatedRows[i * g.periodPointIds.length + slot]!)));
  const view = { ...source, layers, contactPoints: points.map(p => p.point), bundlePorts: [], repeatPlacements: [] };
  const logicalContacts = points.map(({ point, row }) => ({ id: point.logicalContactId, number: row.number, name: row.name, circuitText: row.circuitText, contactTypeGroupId: row.contactTypeGroupId }));
  return { layout, view, points, content: { ...content, views: content.views.map(v => v.id === view.id ? view : v), logicalContacts: [...content.logicalContacts, ...logicalContacts] } };
}

/** Reject an entire mixed edit, even if a caller bypasses disabled UI controls. */
export function guardGeneratorArticleEdit(before: TemplateContentV3, after: TemplateContentV3, g: DrawingGenerator, articleId: string, selection: readonly string[] = []): DrawingGenerator {
  const allowed = generatorDescendants(before, g, g.articles.find(a => a.articleId === articleId)?.nodeIds ?? []);
  if(selection.some(id=>!allowed.has(id))) throw new Error("Основа защищена. Снимите смешанное выделение или перейдите в «Исходник».");
  const oldIds = new Set(before.views.flatMap(v => v.layers.flatMap(l => l.nodes.map(n => n.id))));
  const strip = (content: TemplateContentV3) => ({ ...content, views: content.views.map(v => v.id !== g.viewId ? v : {
    ...v, layers: v.layers.map(l => ({ ...l, nodes: l.nodes.filter(n => !allowed.has(n.id) && oldIds.has(n.id)) })),
  }) });
  // V3 commands reconstruct the core from V2. Property order can change after
  // loading a persisted V5 template; that is not an edit to the protected basis.
  const stable = (value: unknown): unknown => Array.isArray(value) ? value.map(stable)
    : record(value) ? Object.fromEntries(Object.keys(value).sort().map(key => [key, stable(value[key])])) : value;
  if (JSON.stringify(stable(strip(before))) !== JSON.stringify(stable(strip(after)))) throw new Error("Основа защищена. Перейдите в «Исходник», чтобы изменить общие фигуры или контакты.");
  const view = after.views.find(v => v.id === g.viewId)!;
  const newNodes = view.layers.flatMap(l => l.nodes).filter(n => allowed.has(n.id) || !oldIds.has(n.id));
  return { ...g, articles: g.articles.map(a => a.articleId === articleId ? { ...a, nodeIds: newNodes.map(n => n.id) } : a) };
}
