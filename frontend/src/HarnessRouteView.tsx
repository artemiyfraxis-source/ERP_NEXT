import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, failureText, harnessApi, techcardsApi, type HarnessRouteDraft, type HarnessRouteRead } from "./api";
import { formatMinutes } from "./etkRoute";
import { formatMoment } from "./format";
import { harnessDrawing } from "./harness/drawing";
import { buildComponentTemplateViewInstances, buildProjectComponentSnapshotLookup } from "./harness/editor/HarnessDesignEditor";
import type { HarnessDesignDocument } from "./harness/editor/model";
import { createErpHarnessEditorCatalog } from "./harness/erpCatalog";
import { fragmentDrawingCopy } from "./harness/route/route-v3-model";
import { RouteSnapshot, snapshotScene, type SnapshotScene } from "./harness/route/RouteSnapshot";
import { buildRouteSourceItems, routeSourceDesignation, type RouteSourceItem } from "./harness/route/route-source";
import { captureRouteV3Selection, refKey, routeV3NodeQuantity, routeV3NodeSize, type RouteV3Document, type RouteV3Node } from "./harness/route/route-v3-model";
import { operationMinutes, type UmlOperation } from "./harness/route/uml-model";
import {
  addCard, autoLayout, connectCards, editableRoute, insertOnLink, isPrepared, KIND_OPTIONS, libraryOperation, moveCard, nodeMeta,
  nodeMinutes, ownOperation, removeCard, removeLink, sleeveThumbs, updateCard, wireThumbs,
} from "./harnessRouteLogic";
import { cardCategory, cardLabel, type LibraryCard } from "./manualLogic";
import { RouteCanvas, TubeThumbs, WireThumbs, type CanvasCard } from "./RouteCanvas";
import { CARD_KIND_LABEL, CARD_KIND_NAME, type CardKind } from "./routeCards";
import { StatusChip } from "./StatusChip";
import type { Tone } from "./statusTone";
import { pluralRu } from "./tcGenLogic";

/* «Маршрут КБn» в карточке проекта (владелец 12.10): маршрут v3 позиции с чертежом жгута — карточки полуфабрикатов
   Василия в дизайне ERP, без перехода в другое приложение. Читается GET, правки сохраняются сами (PUT с версией
   маршрута; чужое сохранение — 409 и «Загрузить сохранённый»), «Построить по чертежу» — POST после вопроса: ручные
   правки карточек пропадают. Маршрут, построенный на старой версии чертежа, помечен. Рисунок карточки — чертёж позиции
   ядром поля жгута (RouteSnapshot), чужие части приглушены; у заготовок резки — провода и оболочки. Правая панель —
   настройки выбранной карточки: название, вид, количество, операции из библиотеки ERP, входы, состав.
   «Заполнить техкарту» (владелец 13.10): сначала просмотр (dry_run) — блоки и операции по порядку ЭТК справа вместо
   панели карточки, затем «Сохранить черновик техкарты» по просмотренной версии маршрута. Пишется только черновик
   «Ручной сборки»; техкарту из него собирает и выпускает технолог. */

/** Чертёж позиции для рисунков и состава карточек: документ (нет чертежа — null) и одна сцена поля на все карточки. */
export type RouteSource = { design: HarnessDesignDocument | null; scene: SnapshotScene | null };

async function loadRouteSource(project: string, index: string): Promise<RouteSource> {
  const ports = harnessDrawing({ templates: createErpHarnessEditorCatalog().templates, readOnly: true });
  const resource = await ports.api.get(project, index);
  if (ports.revision() === null) return { design: null, scene: null };
  const design = resource.content;
  let instances: ReturnType<typeof buildComponentTemplateViewInstances> = [];
  try { instances = buildComponentTemplateViewInstances(design, buildProjectComponentSnapshotLookup(await ports.placements.list(project, index))); }
  catch { /* без закреплённых видов разъёмы рисуются по своим контактам */ }
  const resolve = (snapshotId: string, assetId: string) => ports.placements.assetContentUrl(project, index, snapshotId, assetId);
  return { design, scene: snapshotScene(design, instances, resolve) };
}

let libraryCache: Promise<LibraryCard[]> | null = null;
function loadLibrary(): Promise<LibraryCard[]> {
  if (!libraryCache) {
    libraryCache = techcardsApi.templates().then((result) => (result?.items ?? []).map((item): LibraryCard => ({
      id: item.id, code: item.code || "", side: item.side || "", title: item.title, name: item.name || "", category: item.category,
      norms: item.norms, first_step: item.first_step,
    }))).catch((reason) => { libraryCache = null; throw reason; });
  }
  return libraryCache;
}

type SaveState = "saved" | "dirty" | "saving" | "error" | "conflict";
const STATE: Record<SaveState, [string, Tone]> = {
  saved: ["Сохранено", "success"], dirty: ["Сохраняю…", "warning"], saving: ["Сохраняю…", "warning"],
  error: ["Не сохранено", "danger"], conflict: ["Не сохранено — маршрут изменили", "danger"],
};

function refusal(reason: unknown): string[] {
  const detail = reason instanceof ApiError ? reason.detail : null;
  const errors = detail && typeof detail === "object" ? (detail as { errors?: unknown }).errors : null;
  return Array.isArray(errors) && errors.length ? errors.map(String) : [failureText(reason, "Маршрут не сохранён")];
}

export function HarnessRouteView({ project, index, canEdit, onOpenDrawing, client = harnessApi, source = loadRouteSource, library = loadLibrary, saveDelay = 700 }: {
  project: string; index: string; canEdit: boolean;
  /** «Открыть чертёж» — вкладка «Чертёж КБn». */
  onOpenDrawing?: () => void;
  client?: Pick<typeof harnessApi, "route" | "saveRoute" | "generateRoute" | "routeDraft">;
  source?: (project: string, index: string) => Promise<RouteSource>;
  library?: () => Promise<LibraryCard[]>;
  /** Пауза перед автосохранением, мс (тесты — 0). */
  saveDelay?: number;
}) {
  const [read, setRead] = useState<HarnessRouteRead | null>(null);
  const [drawing, setDrawing] = useState<RouteSource>({ design: null, scene: null });
  const [doc, setDoc] = useState<RouteV3Document | null>(null);
  const [loadError, setLoadError] = useState("");
  const [state, setState] = useState<SaveState>("saved");
  const [errors, setErrors] = useState<string[]>([]);
  const [conflict, setConflict] = useState("");
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [generation, setGeneration] = useState(0);
  const [preview, setPreview] = useState<HarnessRouteDraft | null>(null);
  const [filling, setFilling] = useState<"" | "preview" | "save">("");
  const [notice, setNotice] = useState("");
  const current = useRef<RouteV3Document | null>(null);
  const revision = useRef<number | null>(null);
  const drawingRevision = useRef(0);
  const pending = useRef<RouteV3Document | null>(null);
  const saving = useRef(false);
  const blocked = useRef(false);
  const timer = useRef<number | undefined>(undefined);

  function take(fresh: HarnessRouteRead) {
    setRead(fresh);
    revision.current = fresh.route?.revision ?? null;
    drawingRevision.current = fresh.route?.drawing_revision ?? fresh.drawing?.revision ?? 0;
    const next = fresh.route ? editableRoute(fresh.route.document) : null;
    current.current = next;
    setDoc(next);
    setLoadError(fresh.route && !next ? "Маршрут на сервере не прочитан — постройте его по чертежу заново." : "");
  }

  useEffect(() => {
    let live = true;
    setRead(null); setDoc(null); setLoadError(""); setConflict(""); setErrors([]); setState("saved"); setPreview(null); setNotice("");
    blocked.current = false; pending.current = null;
    void Promise.all([client.route(project, index), source(project, index).catch(() => ({ design: null, scene: null }))])
      .then(([fresh, loaded]) => { if (live) { setDrawing(loaded); take(fresh); } })
      .catch((reason) => { if (live) setLoadError(failureText(reason, "Маршрут не прочитан")); });
    return () => { live = false; window.clearTimeout(timer.current); };
  }, [project, index, generation]); // eslint-disable-line react-hooks/exhaustive-deps

  const items = useMemo<RouteSourceItem[]>(() => (drawing.design ? buildRouteSourceItems(drawing.design) : []), [drawing.design]);

  async function flush() {
    if (saving.current || blocked.current || !pending.current) return;
    const body = pending.current;
    pending.current = null;
    saving.current = true;
    setState("saving");
    try {
      const saved = await client.saveRoute(project, index, body as unknown as Record<string, unknown>, revision.current, drawingRevision.current);
      revision.current = saved.route?.revision ?? revision.current;
      setRead(saved);
      setErrors([]);
      setState(pending.current ? "dirty" : "saved");
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) {
        blocked.current = true;
        setConflict(reason.message);
        setState("conflict");
      } else {
        setErrors(refusal(reason));
        setState("error");
      }
    } finally {
      saving.current = false;
      if (pending.current && !blocked.current) void flush();
    }
  }

  /** Правка маршрута: применяется сразу, сохраняется через паузу; ошибка правила — строкой над холстом, без правки. */
  function change(edit: (route: RouteV3Document) => RouteV3Document) {
    if (!current.current || !canEdit) return;
    let next: RouteV3Document;
    try { next = edit(current.current); } catch (reason) { setProblem(failureText(reason, "Так нельзя")); return; }
    next = { ...next, graphEdited: true };
    current.current = next;
    setDoc(next);
    setProblem("");
    setPreview(null);
    if (blocked.current) return;
    pending.current = next;
    setState("dirty");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void flush(), saveDelay);
  }

  async function generate() {
    if (!read?.drawing) return;
    if (read.route && !window.confirm("Построить маршрут заново по чертежу? Карточки и связи соберутся заново — ручные правки карточек пропадут.")) return;
    window.clearTimeout(timer.current);
    pending.current = null;
    setBusy(true); setErrors([]); setProblem(""); setPreview(null); setNotice("");
    try {
      take(await client.generateRoute(project, index, revision.current));
      setState("saved"); setSelected(null);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 409) { blocked.current = true; setConflict(reason.message); setState("conflict"); }
      else { setErrors(refusal(reason)); setState("error"); }
    } finally { setBusy(false); }
  }

  /** «Заполнить техкарту»: просмотр того, что получится, — ничего не пишется. */
  async function fill() {
    setFilling("preview"); setErrors([]); setNotice("");
    try { setPreview(await client.routeDraft(project, index, revision.current, true)); }
    catch (reason) { setPreview(null); setErrors(refusal(reason)); }
    finally { setFilling(""); }
  }

  /** «Сохранить черновик техкарты» — по той версии маршрута, что была на просмотре. */
  async function saveDraft() {
    if (!preview) return;
    setFilling("save"); setErrors([]);
    try {
      await client.routeDraft(project, index, preview.revision, false);
      setPreview(null);
      setNotice(`Черновик техкарты ${index} сохранён — технолог собирает из него ЭТК в «Ручной сборке» и выпускает её.`);
    } catch (reason) { setErrors(refusal(reason)); }
    finally { setFilling(""); }
  }

  const confirmRemoveCard = (id: string) => {
    const node = current.current?.nodes.find((item) => item.id === id);
    if (!node || !window.confirm(`Удалить карточку «${node.title}»? Состав карточек после неё пересчитается.`)) return;
    if (selected === id) setSelected(null);
    change((route) => removeCard(route, id));
  };
  const confirmRemoveLink = (edgeId: string) => {
    const edge = current.current?.edges.find((item) => item.id === edgeId);
    const title = (id?: string) => current.current?.nodes.find((node) => node.id === id)?.title ?? "";
    if (!edge || !window.confirm(`Убрать связь «${title(edge.from)}» → «${title(edge.to)}»? Состав «${title(edge.to)}» пересчитается.`)) return;
    change((route) => removeLink(route, edgeId));
  };

  // Рисунок карточки: слепок — его скрытые части приглушены; финал со всеми частями — весь чертёж; карточка без слепка —
  // её части на чертеже. Заготовки резки — провода и оболочки.
  const pictures = useMemo(() => {
    const result = new Map<string, { scene: SnapshotScene; hidden: ReadonlySet<string> }>();
    if (!doc || !drawing.scene || !drawing.design) return result;
    const all = new Set(items.map((item) => refKey(item.ref)));
    for (const node of doc.nodes) {
      if (isPrepared(node)) continue;
      const fragment = doc.fragments.find((item) => node.fragmentIds.includes(item.id));
      const copy = fragment ? fragmentDrawingCopy(fragment) : node.drawing?.isolatedDrawingCopy ?? node.drawing?.drawingCopy;
      if (copy?.document) { result.set(node.id, { scene: snapshotScene(copy.document as HarnessDesignDocument), hidden: new Set(copy.hiddenObjectIds) }); continue; }
      if (copy) { result.set(node.id, { scene: drawing.scene, hidden: new Set(copy.hiddenObjectIds) }); continue; }
      const whole = node.kind === "final" && node.refs.length >= all.size && node.refs.every((ref) => all.has(refKey(ref)));
      const hidden = whole ? [] : captureRouteV3Selection(drawing.design, drawing.scene.objects, node.refs.map((ref) => ref.id)).drawingCopy.hiddenObjectIds;
      result.set(node.id, { scene: drawing.scene, hidden: new Set(hidden) });
    }
    return result;
  }, [doc, drawing, items]);

  const missing = new Set((read?.missing_refs ?? []).map((ref) => `${ref.kind}:${ref.id}`));
  const art = (node: RouteV3Node): ReactNode => {
    if (isPrepared(node)) {
      const wires = wireThumbs(doc!, node, items), sleeves = sleeveThumbs(node, items);
      return wires.length ? <WireThumbs wires={wires} /> : sleeves.length ? <TubeThumbs tubes={sleeves} /> : null;
    }
    const picture = pictures.get(node.id);
    if (!picture) return node.refs.length ? null : <span className="rc-more">Состав пуст — добавьте вход</span>;
    return <RouteSnapshot scene={picture.scene} hidden={picture.hidden} label={`Чертёж: ${node.title}`} />;
  };

  if (loadError && !read) return <div className="error-banner" role="alert">{loadError}</div>;
  if (!read) return <div className="empty">Загрузка маршрута…</div>;
  if (!read.drawing) return <div className="hr-empty">
    <p>У {index} нет чертежа жгута — маршрут строится по чертежу.</p>
    {onOpenDrawing && <button type="button" className="secondary small" onClick={onOpenDrawing}>Открыть чертёж</button>}
  </div>;

  const route = read.route;
  const nodes = doc?.nodes ?? [];
  const node = nodes.find((item) => item.id === selected) ?? null;
  const titleOf = (id: string) => nodes.find((item) => item.id === id)?.title ?? "";
  const cards: CanvasCard[] = nodes.map((item) => {
    const lost = item.refs.filter((ref) => missing.has(`${ref.kind}:${ref.id}`)).length;
    return {
      id: item.id, kind: item.kind, title: item.title, meta: nodeMeta(item), x: item.x, y: item.y, ...routeV3NodeSize(item),
      ...(lost ? { warning: `Нет в чертеже: ${lost} ${pluralRu(lost, "часть", "части", "частей")}` } : {}),
      art: art(item),
    };
  });
  const total = nodes.reduce((sum, item) => sum + nodeMinutes(item), 0);
  const [stateText, tone] = canEdit ? STATE[state] : ["Только просмотр", "neutral" as Tone];
  return <section className="hr" aria-label={`Маршрут ${index}`}>
    <header className="hr-head">
      <h2>Маршрут {index}</h2>
      {route && <span className="hr-save" role="status" title={canEdit ? undefined : "Правят сотрудники с правом «Проекты: правка»"}><StatusChip tone={tone}>{stateText}</StatusChip></span>}
      {route && <span className="muted hr-meta">ред. {route.revision} · чертёж ред. {route.drawing_revision} · {route.updated_by} {formatMoment(route.updated_at)}</span>}
      <span className="hr-actions">
        {canEdit && doc && <button type="button" className="secondary small" onClick={() => { change((value) => addCard(value)); }}>+ Карточка</button>}
        {canEdit && route && doc && <button type="button" className="primary small" disabled={busy || filling !== "" || state !== "saved"}
          title={state !== "saved" ? "Дождитесь сохранения маршрута" : "Карточки маршрута — черновиком «Ручной сборки»: сначала просмотр"}
          onClick={() => void fill()}>{filling === "preview" ? "Проверяю…" : "Заполнить техкарту"}</button>}
        {canEdit && <button type="button" className={route ? "secondary small" : "primary small"} disabled={busy} onClick={() => void generate()}
          title="Карточки нарезки, сборки и готового жгута — заново по сохранённому чертежу">{busy ? "Строю…" : "Построить по чертежу"}</button>}
        {onOpenDrawing && <button type="button" className="secondary small" onClick={onOpenDrawing}>Открыть чертёж</button>}
      </span>
    </header>
    {read.stale && route && <div className="warning-inline hr-stale" role="status">
      Маршрут построен на чертеже ред. {route.drawing_revision}, чертёж уже ред. {read.drawing.revision}.
      {read.missing_refs.length > 0 && ` В чертеже больше нет ${read.missing_refs.length} ${pluralRu(read.missing_refs.length, "части", "частей", "частей")} маршрута.`}
      {canEdit && " «Построить по чертежу» соберёт карточки заново — ручные правки пропадут."}
    </div>}
    {conflict && <div className="error-banner hr-banner" role="alert">
      <span>Правки не сохранены: {conflict.replace(/\s*—\s*обновите.*$/i, "")}.</span>
      <button type="button" className="secondary small" onClick={() => setGeneration((value) => value + 1)}>Загрузить сохранённый</button>
    </div>}
    {errors.length > 0 && <div className="error-banner hr-banner" role="alert"><span>{errors.join("; ")}</span></div>}
    {notice && <div className="success-banner hr-banner" role="status"><span>{notice}</span></div>}
    {loadError && <div className="error-banner" role="alert">{loadError}</div>}
    {problem && <div className="warning-inline" role="alert">{problem}</div>}
    {!route || !doc ? <div className="hr-empty">
      <p>Маршрута ещё нет. Постройте его по чертежу — появятся карточки нарезанных проводов и оболочек, сборки и готового жгута.</p>
      {canEdit && <button type="button" className="primary small" disabled={busy} onClick={() => void generate()}>{busy ? "Строю…" : "Построить по чертежу"}</button>}
    </div> : <div className="hr-body">
      <RouteCanvas cards={cards} label="Карточки маршрута" selected={selected} onSelect={setSelected} height="calc(100vh - 290px)"
        links={doc.edges.map((edge) => ({ id: edge.id, from: edge.from, to: edge.to, label: `«${titleOf(edge.from)}» → «${titleOf(edge.to)}»` }))}
        onMove={canEdit ? (id, x, y) => change((value) => moveCard(value, id, x, y)) : undefined}
        onInsert={canEdit ? (edgeId) => change((value) => insertOnLink(value, edgeId)) : undefined}
        onRemoveLink={canEdit ? confirmRemoveLink : undefined}
        onRemoveCard={canEdit ? confirmRemoveCard : undefined}
        onAutoLayout={canEdit ? () => change(autoLayout) : undefined}
        tools={<span className="hr-sum">{nodes.length} {pluralRu(nodes.length, "карточка", "карточки", "карточек")} · {doc.edges.length} {pluralRu(doc.edges.length, "связь", "связи", "связей")} · {formatMinutes(total)} мин{read.complete ? "" : <span className="tone-warn"> · нет финала со всеми частями чертежа</span>}</span>} />
      {preview ? <DraftPreview preview={preview} index={index} saving={filling === "save"} onSave={() => void saveDraft()} onClose={() => setPreview(null)} />
        : <CardPanel node={node} doc={doc} items={items} canEdit={canEdit} library={library} onClose={() => setSelected(null)}
          onChange={(edit) => change(edit)} onRemove={confirmRemoveCard} />}
    </div>}
  </section>;
}

const num = (value: string, min = 0) => { const parsed = Number(value.replace(",", ".")); return Number.isFinite(parsed) ? Math.max(min, parsed) : min; };

/** Настройки выбранной карточки — справа от холста (не окно): название, вид, количество, операции, входы, состав. */
function CardPanel({ node, doc, items, canEdit, library, onClose, onChange, onRemove }: {
  node: RouteV3Node | null; doc: RouteV3Document; items: RouteSourceItem[]; canEdit: boolean; library: () => Promise<LibraryCard[]>;
  onClose: () => void; onChange: (edit: (route: RouteV3Document) => RouteV3Document) => void; onRemove: (id: string) => void;
}) {
  const [addFrom, setAddFrom] = useState("");
  useEffect(() => { setAddFrom(""); }, [node?.id]);
  if (!node) return <aside className="hr-panel" aria-label="Карточка не выбрана">
    <p className="hr-hint">Выберите карточку — здесь её название, вид, количество, операции и состав.</p>
    {canEdit && <p className="hr-hint">«+» на связи вставляет карточку, «×» убирает связь; карточку можно перетащить или сдвинуть стрелками.</p>}
  </aside>;
  const set = (patch: Parameters<typeof updateCard>[2]) => onChange((route) => updateCard(route, node.id, patch));
  const ops = node.operations ?? [];
  const setOp = (id: string, patch: Partial<UmlOperation>) => set({ operations: ops.map((op) => (op.id === id ? { ...op, ...patch } : op)) });
  const incoming = doc.edges.filter((edge) => edge.to === node.id);
  const below = new Set<string>([node.id]);
  const queue = [node.id];
  while (queue.length) { const next = queue.pop()!; doc.edges.filter((edge) => edge.from === next).forEach((edge) => { if (!below.has(edge.to)) { below.add(edge.to); queue.push(edge.to); } }); }
  const candidates = doc.nodes.filter((other) => !below.has(other.id) && other.kind !== "final" && !incoming.some((edge) => edge.from === other.id));
  const byKey = new Map(items.map((item) => [refKey(item.ref), item]));
  return <aside className="hr-panel" aria-label={`Карточка «${node.title}»`}>
    <div className="hr-panel-head">
      <span className={`rc-kind is-${node.kind}`}>{CARD_KIND_LABEL[node.kind]}</span>
      <button type="button" className="hr-link" onClick={onClose}>Закрыть</button>
    </div>
    <label className="hr-field">Название
      <input value={node.title} maxLength={512} disabled={!canEdit} onChange={(event) => set({ title: event.target.value || "Карточка" })} /></label>
    <div className="hr-pair">
      <label className="hr-field">Вид
        <select value={node.kind} disabled={!canEdit} onChange={(event) => set({ kind: event.target.value as CardKind })}>
          {KIND_OPTIONS.map((kind) => <option key={kind} value={kind}>{CARD_KIND_NAME[kind]}</option>)}
        </select></label>
      <label className="hr-field">Количество, шт
        <input type="number" min={1} step={1} inputMode="numeric" value={routeV3NodeQuantity(node)} disabled={!canEdit}
          onChange={(event) => set({ quantity: Math.max(1, Math.round(num(event.target.value, 1))) })} /></label>
    </div>
    <section className="hr-section" aria-label="Операции карточки">
      <h3>Операции <span className="muted">· {formatMinutes(nodeMinutes(node))} мин</span></h3>
      {ops.map((op) => <div className="hr-op" key={op.id}>
        <input className="hr-op-name" aria-label="Название операции" value={op.title} disabled={!canEdit} title={op.code ? `Карта ${op.code}` : undefined}
          onChange={(event) => setOp(op.id, { title: event.target.value })} />
        <button type="button" className="hr-x" aria-label={`Удалить операцию «${op.title}»`} title="Удалить операцию" disabled={!canEdit}
          onClick={() => set({ operations: ops.filter((item) => item.id !== op.id) })}>×</button>
        <select aria-label={`Как считать время: ${op.title}`} value={op.kind} disabled={!canEdit} onChange={(event) => setOp(op.id, { kind: event.target.value as UmlOperation["kind"] })}>
          <option value="static">Штучная</option><option value="running">Погонная</option>
        </select>
        {op.kind === "static" ? <>
          <label className="hr-num">шт<input type="number" min={0} step="any" value={op.quantity} disabled={!canEdit} aria-label={`Штук: ${op.title}`} onChange={(event) => setOp(op.id, { quantity: num(event.target.value) })} /></label>
          <label className="hr-num">мин/шт<input type="number" min={0} step="any" value={op.minutesEach} disabled={!canEdit} aria-label={`Минут на штуку: ${op.title}`} onChange={(event) => setOp(op.id, { minutesEach: num(event.target.value) })} /></label>
        </> : <>
          <label className="hr-num">мм<input type="number" min={0} step="any" value={op.lengthMm} disabled={!canEdit} aria-label={`Длина, мм: ${op.title}`} onChange={(event) => setOp(op.id, { lengthMm: num(event.target.value) })} /></label>
          <label className="hr-num">мм/мин<input type="number" min={1} step="any" value={op.speedMmPerMinute} disabled={!canEdit} aria-label={`Скорость, мм/мин: ${op.title}`} onChange={(event) => setOp(op.id, { speedMmPerMinute: num(event.target.value, 1) })} /></label>
          <label className="hr-num">чел.<input type="number" min={1} step={1} value={op.employees} disabled={!canEdit} aria-label={`Сотрудников: ${op.title}`} onChange={(event) => setOp(op.id, { employees: Math.max(1, Math.round(num(event.target.value, 1))) })} /></label>
        </>}
        <span className="hr-op-time">{formatMinutes(operationMinutes(op))} мин</span>
      </div>)}
      {!ops.length && <p className="hr-hint">Операций нет — добавьте из библиотеки.</p>}
      {canEdit && <LibraryAdd library={library} onPick={(card) => set({ operations: [...ops, libraryOperation(card)] })}
        onOwn={() => set({ operations: [...ops, ownOperation()] })} />}
    </section>
    <section className="hr-section" aria-label="Входы карточки">
      <h3>Входы <span className="muted">· {incoming.length}</span></h3>
      {incoming.map((edge) => <div className="hr-row" key={edge.id}>
        <span>{doc.nodes.find((item) => item.id === edge.from)?.title}</span>
        {canEdit && <button type="button" className="hr-x" aria-label={`Убрать вход «${doc.nodes.find((item) => item.id === edge.from)?.title}»`}
          onClick={() => onChange((route) => removeLink(route, edge.id))}>×</button>}
      </div>)}
      {canEdit && candidates.length > 0 && <div className="hr-add">
        <select aria-label="Добавить вход из карточки" value={addFrom} onChange={(event) => setAddFrom(event.target.value)}>
          <option value="">Добавить вход из карточки…</option>
          {candidates.map((other) => <option key={other.id} value={other.id}>{other.title}</option>)}
        </select>
        <button type="button" className="secondary small" disabled={!addFrom} onClick={() => { onChange((route) => connectCards(route, addFrom, node.id)); setAddFrom(""); }}>Добавить</button>
      </div>}
    </section>
    <section className="hr-section" aria-label="Состав карточки">
      <h3>Состав <span className="muted">· {node.refs.length}</span></h3>
      <ul className="hr-parts">{node.refs.map((ref) => {
        const item = byKey.get(refKey(ref));
        return <li key={refKey(ref)} title={item ? routeSourceDesignation(item) : undefined}>
          <span>{item?.title ?? "нет в чертеже"}</span><small className="muted">{item ? routeSourceDesignation(item) : ref.id}</small>
        </li>;
      })}</ul>
    </section>
    {canEdit && <button type="button" className="danger small hr-remove" onClick={() => onRemove(node.id)}>Удалить карточку</button>}
  </aside>;
}

/** Предупреждения сборщика ЭТК «Операция N (название): причина» с одной причиной — одной строкой «Причина: операции
 *  1, 2, 5»; остальные как есть, в своём порядке. */
function groupWarnings(warnings: readonly string[]): string[] {
  const byReason = new Map<string, number[]>();
  const result: Array<string | { reason: string }> = [];
  for (const warning of warnings) {
    const match = /^Операция (\d+) \(.*\): (.+)$/.exec(warning);
    if (!match) { result.push(warning); continue; }
    const [, position, reason] = match;
    if (!byReason.has(reason)) { byReason.set(reason, []); result.push({ reason }); }
    byReason.get(reason)!.push(Number(position));
  }
  return result.map((item) => {
    if (typeof item === "string") return item;
    const positions = byReason.get(item.reason)!;
    const reason = item.reason.charAt(0).toUpperCase() + item.reason.slice(1);
    return `${reason}: ${pluralRu(positions.length, "операция", "операции", "операции")} ${positions.join(", ")}`;
  });
}

/** Просмотр «Заполнить техкарту» — справа от холста: блоки схемы полуфабрикатов в порядке ЭТК, у каждого — откуда он
 *  берёт полуфабрикаты (с количеством на штуку) и его операции с номером ЭТК и временем на жгут; предупреждения сборщика. */
function DraftPreview({ preview, index, saving, onSave, onClose }: {
  preview: HarnessRouteDraft; index: string; saving: boolean; onSave: () => void; onClose: () => void;
}) {
  const ops = preview.build_config.ops ?? [];
  const blocks = preview.route?.blocks ?? [];
  const names = new Map(blocks.map((block) => [block.key, block.name]));
  const minutes = (position: number) => {
    const op = ops[position - 1];
    return op ? (Number(op.tOp || 0) * Number(op.qty || 1)) / 60 : 0;
  };
  const total = ops.reduce((sum, _op, place) => sum + minutes(place + 1), 0);
  return <aside className="hr-panel" aria-label="Техкарта из маршрута">
    <div className="hr-panel-head">
      <strong>Техкарта {index} из маршрута</strong>
      <button type="button" className="hr-link" onClick={onClose}>Закрыть</button>
    </div>
    <span className="hr-sum">{preview.operations} {pluralRu(preview.operations, "операция", "операции", "операций")} · {blocks.length} {pluralRu(blocks.length, "блок", "блока", "блоков")} · {formatMinutes(total)} мин на жгут · маршрут ред. {preview.revision}</span>
    <div className="hr-add">
      <button type="button" className="primary small" disabled={saving} onClick={onSave}>{saving ? "Сохраняю…" : "Сохранить черновик техкарты"}</button>
      <button type="button" className="secondary small" disabled={saving} onClick={onClose}>Отмена</button>
    </div>
    {preview.warnings.length > 0 && <div className="warning-inline" role="status">
      {groupWarnings(preview.warnings).map((warning) => <div key={warning}>{warning}</div>)}
    </div>}
    {blocks.map((block) => {
      const inputs = (preview.route?.edges ?? []).filter((edge) => edge.to === block.key)
        .map((edge) => `${names.get(edge.from) ?? edge.from}${edge.per_unit === 1 ? "" : ` × ${formatMinutes(edge.per_unit)}`}`);
      return <section className="hr-section" key={block.key} aria-label={`Блок «${block.name}»`}>
        <h3>{block.name} <span className="muted">· {block.final ? "изделие" : "полуфабрикат"}</span></h3>
        {inputs.length > 0 && <span className="hr-sum">из: {inputs.join(", ")}</span>}
        {block.ops.map((position) => <div className="hr-row" key={position}>
          <span>{position}. {ops[position - 1]?.label ?? `Операция ${position}`}</span>
          <span className="hr-sum">{formatMinutes(minutes(position))} мин</span>
        </div>)}
      </section>;
    })}
  </aside>;
}

/** «+ Операция из библиотеки»: поиск по названию и коду, до 8 карточек; библиотека читается при первом открытии. */
function LibraryAdd({ library, onPick, onOwn }: { library: () => Promise<LibraryCard[]>; onPick: (card: LibraryCard) => void; onOwn: () => void }) {
  const [open, setOpen] = useState(false);
  const [cards, setCards] = useState<LibraryCard[] | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => {
    if (!open || cards) return;
    let live = true;
    library().then((list) => { if (live) setCards(list); }).catch((reason) => { if (live) setError(failureText(reason, "Библиотека операций не загружена")); });
    return () => { live = false; };
  }, [open, cards, library]);
  if (!open) return <div className="hr-add">
    <button type="button" className="secondary small" onClick={() => setOpen(true)}>+ Операция из библиотеки</button>
    <button type="button" className="secondary small" onClick={onOwn}>+ Своя операция</button>
  </div>;
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const found = (cards ?? []).filter((card) => words.every((word) => `${cardLabel(card)} ${card.code} ${cardCategory(card)}`.toLocaleLowerCase().includes(word))).slice(0, 8);
  return <div className="hr-library" role="group" aria-label="Операция из библиотеки">
    <div className="hr-add">
      <input type="search" autoFocus aria-label="Найти операцию в библиотеке" placeholder="Название или код операции" value={query}
        onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") setOpen(false); }} />
      <button type="button" className="hr-link" onClick={() => setOpen(false)}>Закрыть</button>
    </div>
    {error && <p className="tone-danger hr-hint">{error}</p>}
    {!cards && !error && <p className="hr-hint">Загрузка библиотеки…</p>}
    {cards && <ul className="hr-found">{found.map((card) => <li key={card.id}>
      <button type="button" title={card.code ? `Карта ${card.code}` : undefined} onClick={() => { onPick(card); setQuery(""); }}>
        <span>{cardLabel(card)}</span><small className="muted">{cardCategory(card)}</small>
      </button></li>)}
      {!found.length && <li className="hr-hint">Нет таких операций — измените поиск или добавьте свою.</li>}
    </ul>}
  </div>;
}

export default HarnessRouteView;
