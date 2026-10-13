import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { AutoTextarea } from "./AutoTextarea";
import { DataTable, type DataColumn } from "./DataTable";
import { formatMinutes, pluralBlocks } from "./etkRoute";
import { PerUnit } from "./RouteDiagram";
import { RouteCanvas, type CanvasCard } from "./RouteCanvas";
import { CARD_KIND_LABEL, GEN_CARD, GEN_SPACING, layoutByDepth, widestRow, type Size } from "./routeCards";
import type { RouteBlock, RouteEdge, RouteOp } from "./routeLogic";
import {
  addBlock,
  addCustomOp,
  addInput,
  addInputBlock,
  addLibraryOp,
  blockKind,
  blockOrder,
  BLOCK_KIND_NAMES,
  cardCategory,
  cardLabel,
  cardNorms,
  genParams,
  inputCandidates,
  insertBlock,
  kdChangeText,
  manualRoute,
  manualShortTitle,
  moveBlock,
  moveOp,
  moveOpToBlock,
  nameBlockAfter,
  opMinutes,
  opTitle,
  orderedOps,
  removeBlock,
  removeInput,
  removeOp,
  renameBlock,
  selectBlock,
  setBlockChecked,
  setBlockKind,
  setBlockQuantity,
  setPerUnit,
  toggleComponent,
  updateOp,
  type EdgeEdit,
  type KdSummary,
  type LibraryCard,
  type ManualOp,
  type ManualState,
} from "./manualLogic";
import type { Comp, OpRec } from "./tcGenLogic";
import { Field, UnitInput } from "./GenControls";
import "./ManualAssembly.css";

type Change = (next: (current: ManualState) => ManualState) => void;

function normText(card: LibraryCard, opRecs: OpRec[]): string {
  const norms = cardNorms(card, opRecs);
  if (!norms) return "";
  return [norms.op ? `${formatMinutes(norms.op)} с/шт` : "", norms.prep ? `подг. ${formatMinutes(norms.prep / 60)} мин` : ""].filter(Boolean).join(" · ");
}

/** Выбор карточки из библиотеки операций: категории чипами, поиск по названию и коду; клик по строке — карточка сразу
    в блок. Несколько сразу — отметить и «Добавить выбранные». */
function LibraryPicker({ library, opRecs, target, added, onAdd, onClose }: {
  library: LibraryCard[]; opRecs: OpRec[]; target: string; added: string; onAdd: (cards: LibraryCard[]) => void; onClose: () => void;
}) {
  const categories = useMemo(() => [...new Set(library.map(cardCategory))].sort((a, b) => (a === "Прочее" ? 1 : b === "Прочее" ? -1 : a.localeCompare(b, "ru"))), [library]);
  const columns: DataColumn<LibraryCard>[] = [
    { key: "name", title: "Операция", value: (card) => `${cardLabel(card)} ${card.code}`,
      render: (card) => <span className="manual-card-name"><b>{cardLabel(card)}</b>{card.code && <small>{card.code}</small>}</span> },
    { key: "category", title: "Категория", value: cardCategory, width: 100 },
    { key: "norm", title: "Норма БД.ОП", value: (card) => normText(card, opRecs), render: (card) => normText(card, opRecs) || "—", width: 120 },
    { key: "step", title: "Первый шаг", value: (card) => card.first_step || "", width: "28%",
      render: (card) => <span className="manual-card-step" title={card.first_step || undefined}>{card.first_step}</span> },
  ];
  return <div className="manual-picker" role="region" aria-label="Библиотека операций">
    <div className="section-hdr"><strong>Операция из библиотеки → в блок «{target}»</strong>
      {added && <span className="ok-text" role="status">{added}</span>}
      <button type="button" className="btn-link manual-close" onClick={onClose}>Закрыть</button></div>
    {library.length ? <DataTable rows={library} columns={columns} rowKey={(card) => card.id} label="Карточки операций"
      presets={categories.map((category) => ({ label: category, test: (card: LibraryCard) => cardCategory(card) === category }))}
      searchLabel="Поиск карточки" searchPlaceholder="Название или код карточки" emptyText="Нет таких карточек — смените категорию или поиск." pageSize={40}
      bulkActions={[{ label: "Добавить выбранные", run: (rows) => onAdd(rows) }]}
      onRowClick={(card) => onAdd([card])} rowLabel={cardLabel} rowTitle={(card) => `Добавить «${cardLabel(card)}» в блок «${target}»${card.first_step ? `\nПервый шаг: ${card.first_step}` : ""}`} />
      : <div className="empty">Библиотека операций не загружена — добавьте свою операцию.</div>}
  </div>;
}

/** Что поменялось в КД у операции — «было → стало»; правленное мастером остаётся его. Принять изменения КД кнопкой —
    позже, пока только показ. */
function KdNote({ op }: { op: ManualOp }) {
  const edited = new Set(op.edited ?? []);
  return <small className="manual-op-kd">
    {(op.kdChanges ?? []).map((change, index) => <span key={change.field} className="muted">
      {index > 0 && <br />}{kdChangeText(change)}{edited.has(change.field) ? " (оставлено значение мастера)" : ""}
    </span>)}
  </small>;
}

/** «Предложить из КД» на схеме: есть ли чертёж жгута, идёт ли чтение, устарела ли схема (КД изменена) и открытое
    предложение — сводка до применения (`summary`) или уже взятая в пустую схему схема из КД (`taken` операций). */
export type KdOffer = {
  available: boolean;
  /** Почему кнопка недоступна — подсказкой на ней. */
  hint: string;
  busy: boolean;
  stale: boolean;
  onPropose: () => void;
  proposal?: { summary?: KdSummary; taken?: number; warnings: string[]; onApply?: () => void; onCancel: () => void };
};

function kdSummaryText(summary: KdSummary): string {
  if (!summary.added && !summary.changed && !summary.gone) return "Предложение из КД: схема уже совпадает с КД";
  return `Предложение из КД: добавится ${summary.added}, изменится ${summary.changed}, нет в КД ${summary.gone}`;
}

/** Кнопка «Предложить из КД» (или отметка «КД изменена — предложить заново»); без чертежа — недоступна с подсказкой. */
function KdButton({ kd }: { kd: KdOffer }) {
  if (kd.stale && kd.available) {
    return <button type="button" className="secondary small tone-warn" style={{ background: "var(--chip-warn-bg)" }} disabled={kd.busy} title="Чертёж жгута в КД сохранён после того, как из него предложена схема"
      onClick={kd.onPropose}>{kd.busy ? "Читаю КД…" : "КД изменена — предложить заново"}</button>;
  }
  // Подсказка — на обёртке: у недоступной кнопки браузер её может не показать.
  return <span title={kd.available ? "Блоки и операции из чертежа жгута в КД" : kd.hint}>
    <button type="button" className="secondary small" disabled={!kd.available || kd.busy} onClick={kd.onPropose}>{kd.busy ? "Читаю КД…" : "Предложить из КД"}</button>
  </span>;
}

/** Сводка предложения из КД до применения («Применить» / «Отмена») или после взятия в пустую схему («Закрыть»). */
function KdProposalBar({ proposal }: { proposal: NonNullable<KdOffer["proposal"]> }) {
  // Взятая без замечаний схема — зелёным; решение или замечания КД — жёлтым.
  const calm = !proposal.summary && !proposal.warnings.length;
  return <div className={calm ? "success-banner" : "warning-inline"} role="region" aria-label="Предложение из КД" style={{ display: "grid", gap: 6, fontSize: 13, marginBottom: 0 }}>
    <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
      <b role="status">{proposal.summary ? kdSummaryText(proposal.summary) : `Схема предложена из КД: ${proposal.taken ?? 0} оп.`}</b>
      <span className="manual-add" style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
        {proposal.onApply && <button type="button" className="primary small" onClick={proposal.onApply}>Применить</button>}
        <button type="button" className="secondary small" onClick={proposal.onCancel}>{proposal.onApply ? "Отмена" : "Закрыть"}</button>
      </span>
    </div>
    {proposal.warnings.map((warning) => <span key={warning}>{warning}</span>)}
  </div>;
}

function normLabel(minutes: number) {
  return minutes > 0 ? <b>{formatMinutes(minutes)} мин</b> : <b className="tone-warn">не задана</b>;
}

/** Операция ручной сборки: название, норма, параметры, результат; у своей — ход операции, контроль, оснастка;
    комплектующие из состава КБ. ↑/↓ — внутри блока. */
export function ManualOpCard({ op, position, first, last, partQty, components, onChange, open: openProp, onToggle, moveTargets = [] }: {
  op: ManualOp; position: number; first: boolean; last: boolean; partQty: number; components: Comp[]; onChange: Change;
  open?: boolean; onToggle?: () => void;
  /** Другие блоки схемы — «Перенести в блок…». */
  moveTargets?: Array<{ key: string; title: string }>;
}) {
  const [openOwn, setOpenOwn] = useState(false);
  const open = openProp ?? openOwn;
  const toggle = onToggle ?? (() => setOpenOwn(!openOwn));
  const set = (patch: Partial<ManualOp>) => onChange((current) => updateOp(current, op.seq, patch));
  const title = opTitle(op, position);
  const minutes = opMinutes(op, partQty);
  const picked = new Map(op.components.map((item) => [item.id, item]));
  // Ключа операции больше нет в КД — метка в начале строки «откуда операция».
  const gone = op.kdGone ? <><span className="order-chip tone-warn" title="Этой операции больше нет в КД — проверьте, нужна ли она">нет в КД</span>{" "}</> : null;
  // Подпись короткая, единица — в поле; полное имя поля — в aria-label («Подготовка, мин: Резка»).
  const field = (key: "qty" | "prepMin" | "opSec", label: string, unit: string, name: string, placeholder: string) =>
    <Field label={label}><UnitInput unit={unit} inputMode="decimal" aria-label={`${name}: ${title}`} value={op[key]} placeholder={placeholder}
      onChange={(event) => set({ [key]: event.target.value })} /></Field>;
  return <div className={open ? "manual-op is-open" : "manual-op"} aria-label={`Операция ${position}: ${title}`} data-seq={op.seq}>
    <div className="manual-op-head">
      <button type="button" className="manual-op-fold" aria-expanded={open} aria-label={`${open ? "Свернуть" : "Развернуть"} операцию ${position}: ${title}`}
        title={open ? "Свернуть" : "Открыть: норма, параметры, результат"} onClick={toggle}><span className="op-badge">{position}</span></button>
      <div className="manual-op-name">
        {op.gen
          ? <b className="manual-op-title" aria-label={`Название операции ${position}`}>{title}</b>
          : <input aria-label={`Название операции ${position}`} value={op.label} placeholder={op.kind === "custom" ? "Название своей операции" : ""}
            className={op.kind === "custom" && !op.label.trim() ? "f-empty" : undefined} onChange={(event) => set({ label: event.target.value })} />}
        {op.gen
          ? <small className="manual-op-params" title={genParams(op.gen)}>{gone}{genParams(op.gen)}{op.code && <>{genParams(op.gen) && " · "}<span className="op-code">{op.code}</span></>}</small>
          : <small>{gone}{op.kind === "library" ? <>из библиотеки{op.code && <> · <span className="op-code">{op.code}</span></>}</> : "своя операция"}</small>}
        {!!op.kdChanges?.length && <KdNote op={op} />}
      </div>
      <div className="manual-op-tools">
        {!open && <button type="button" className="manual-op-norm" aria-label={`Норма: ${title}`} title="Открыть" onClick={toggle}>{normLabel(minutes)}</button>}
        {moveTargets.length > 0 && <select className="manual-op-move" aria-label={`Перенести в блок: ${title}`} title="Перенести операцию в другой блок" value=""
          onChange={(event) => { const key = event.target.value; if (key) onChange((current) => moveOpToBlock(current, op.seq, key)); }}>
          <option value="">Перенести в блок…</option>
          {moveTargets.map((target) => <option key={target.key} value={target.key}>{target.title}</option>)}
        </select>}
        <button type="button" className="secondary small" aria-label={`Выше: ${title}`} title="Выше в блоке" disabled={first} onClick={() => onChange((current) => moveOp(current, op.seq, -1))}>↑</button>
        <button type="button" className="secondary small" aria-label={`Ниже: ${title}`} title="Ниже в блоке" disabled={last} onClick={() => onChange((current) => moveOp(current, op.seq, 1))}>↓</button>
        <button type="button" className="btn-remove" aria-label={`Удалить операцию ${position}: ${title}`} title="Удалить" onClick={() => onChange((current) => removeOp(current, op.seq))}>×</button>
      </div>
    </div>
    {open && <>
    <div className="manual-grid">
      {!op.gen && field("qty", "На изделие", "шт", "На изделие", "1")}
      {field("prepMin", "Подготовка", "мин", "Подготовка, мин", "0")}
      {field("opSec", "Операция", "с/шт", "Операция, сек/шт", "0")}
      <div className="manual-norm" aria-label={`Норма на партию: ${title}`}><small>На партию {partQty} шт.</small>{normLabel(minutes)}</div>
      {!op.gen && <>
      <Field label="Параметры" className="wide"><input aria-label={`Параметры: ${title}`} value={op.params} placeholder="напр. L = 300 мм, зачистка 5 мм" onChange={(event) => set({ params: event.target.value })} /></Field>
      <Field label="Результат — полуфабрикат" className="result"><input aria-label={`Результат: ${title}`} value={op.result} placeholder={`Полуфабрикат после «${title}»`} onChange={(event) => set({ result: event.target.value })} /></Field>
      <Field label="Примечание" className="wide"><AutoTextarea aria-label={`Примечание: ${title}`} value={op.note} onChange={(note) => set({ note })} /></Field>
      </>}
    </div>
    {op.kind === "custom" && !op.gen && <div className="manual-text">
      <label className="gfield"><span className="gfield-label">Ход операции<small>шаг на строку</small></span><textarea aria-label={`Ход операции: ${title}`} rows={4} value={op.steps} onChange={(event) => set({ steps: event.target.value })} /></label>
      <label className="gfield"><span className="gfield-label">Контроль<small>«что проверить | допуск» на строку</small></span><textarea aria-label={`Контроль: ${title}`} rows={4} value={op.controls} placeholder="Пайка без наплывов | IPC-A-620, разд. 4" onChange={(event) => set({ controls: event.target.value })} /></label>
      <label className="gfield"><span className="gfield-label">Оснастка<small>строкой; «Оборудование: …», «Расходники: …»</small></span><textarea aria-label={`Оснастка: ${title}`} rows={4} value={op.tooling} placeholder={"Пинцет\nОборудование: паяльная станция\nРасходники: припой ПОС-61"} onChange={(event) => set({ tooling: event.target.value })} /></label>
    </div>}
    {components.length > 0 && !op.gen && <details className="manual-comps" open={op.components.length > 0}>
      <summary>Комплектующие из КБ{op.components.length ? ` · ${op.components.length}` : ""}</summary>
      <div className="manual-comp-list">{components.map((comp) => {
        const item = picked.get(comp.id);
        return <span className={item ? "manual-comp on" : "manual-comp"} key={comp.id}>
          <label><input type="checkbox" checked={!!item} onChange={() => onChange((current) => toggleComponent(current, op.seq, comp))} /> {comp.art || comp.name}</label>
          {item && <input aria-label={`Количество ${comp.art || comp.name} на изделие: ${title}`} inputMode="decimal" value={item.qty}
            onChange={(event) => set({ components: op.components.map((other) => (other.id === comp.id ? { ...other, qty: event.target.value } : other)) })} />}
          {item && <small>{item.unit}</small>}
        </span>;
      })}</div>
    </details>}
    </>}
  </div>;
}

const coarsePointer = () => { try { return !!window.matchMedia?.("(pointer: coarse)").matches; } catch { return false; } };
/** Карточка блока: ширина 200…400 по месту (как «Схема полуфабрикатов»), строка подписи под названием, низ карточки с «+ Вход». */
const CARD_MIN = 200, CARD_MAX = 400, SUB_ROW = 16, FOOT = 34, FOOT_COARSE = 48;
const SPACING = { ...GEN_SPACING, margin: 16 };

/**
 * «Ручная сборка» — схема первой (владелец 05.10; карточками, как «Маршрут КБn», — 13.10): блоки схемы — карточки
 * полуфабрикатов холста маршрута (RouteCanvas): вид, название, «N оп. · мин», операции блока строками; связи — стрелками
 * с числом штук, «+» на связи вставляет блок, «×» убирает связь. Карточка или её операция открывает справа панель блока:
 * название, вид, входы и сколько штук берёт каждый, операции — «+ Операция из библиотеки» (клик по строке) и «+ Своя
 * операция». Раскладка карточек — рядами по входам (positions не хранятся, build_config прежний). Номера операций идут
 * по схеме: входы раньше блоков, которые их берут.
 */
export function ManualAssembly({ state, onChange, library, opRecs, components, partQty, finalName, error, kd }: {
  state: ManualState; onChange: Change; library: LibraryCard[]; opRecs: OpRec[]; components: Comp[]; partQty: number;
  finalName: string; error?: string; kd?: KdOffer;
}) {
  const [picking, setPicking] = useState(false);
  const [added, setAdded] = useState("");
  const [problem, setProblem] = useState("");
  const [nameOnPick, setNameOnPick] = useState(false);
  const [opened, setOpened] = useState<Set<number>>(new Set());
  // Панель блока — по выбору блока; «Готово», × или Esc её закрывают, схема остаётся. Пустая сборка открывается с ней.
  const [paneOpen, setPaneOpen] = useState(() => !state.ops.length);
  const panel = useRef<HTMLDivElement>(null);
  const seen = useRef(state.selected);
  // Операция, нажатая на карточке, — к ней панель прокручивается после отрисовки.
  const reveal = useRef<number | null>(null);
  // Ширина места под холстом — карточки делят её по самому широкому ряду.
  const [boxWidth, setBoxWidth] = useState(0);
  const observer = useRef<ResizeObserver | null>(null);
  const canvasBox = useRef<HTMLDivElement | null>(null);
  const boxRef = useCallback((element: HTMLDivElement | null) => {
    observer.current?.disconnect();
    observer.current = null;
    canvasBox.current = element;
    if (!element) return;
    setBoxWidth(element.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    observer.current = new ResizeObserver(() => setBoxWidth(element.clientWidth));
    observer.current.observe(element);
  }, []);
  const { route, ops: routeOps } = useMemo(() => manualRoute(state, partQty), [state, partQty]);
  const ordered = useMemo(() => orderedOps(state), [state]);
  // Длинное название блока от генератора — коротко по операциям, целиком — в подсказке.
  const named = (block: RouteBlock, ops: RouteOp[] = routeOps) => manualShortTitle(block, ops, finalName);
  const title = (block: RouteBlock) => named(block).title;
  const byKey = new Map(route.blocks.map((block) => [block.key, block]));
  const block = byKey.get(state.selected) ?? route.blocks.find((item) => item.final)!;
  const own = state.blocks.find((item) => item.key === block.key)!;
  const bySeq = new Map(state.ops.map((op) => [op.seq, op]));
  const position = new Map(ordered.map((op, index) => [op.seq, index + 1]));
  const total = ordered.reduce((sum, op) => sum + opMinutes(op, partQty), 0);
  const blockName = title(block);
  const ownByKey = new Map(state.blocks.map((item) => [item.key, item]));

  const select = (key: string) => { onChange((current) => selectBlock(current, key)); setProblem(""); setAdded(""); setNameOnPick(false); setPaneOpen(true); };
  // Выбрали блок (на схеме или в «Итоге») — панель блока открывается и подъезжает в видимую часть.
  useEffect(() => {
    if (seen.current === block.key) return;
    seen.current = block.key;
    setPaneOpen(true);
    panel.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }, [block.key]);
  // Нажатая на карточке операция — открыта в панели и видна.
  useEffect(() => {
    const seq = reveal.current;
    if (seq === null) return;
    reveal.current = null;
    panel.current?.querySelector(`[data-seq="${seq}"]`)?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  });
  const openOp = (key: string, seq: number) => {
    reveal.current = seq;
    setOpened((current) => new Set(current).add(seq));
    if (key !== block.key || !paneOpen) select(key);
  };
  const order = blockOrder(state);
  const move = (key: string, dir: -1 | 1) => {
    const result = moveBlock(state, key, dir, (item) => { const found = byKey.get(item); return found ? title(found) : item; });
    setProblem(result.problem ? `Так нельзя: ${result.problem}` : "");
    if (!result.problem) onChange(() => result.state);
  };
  // Клавиатура на схеме: ↑/↓ — соседний блок, Alt+↑/↓ — сдвинуть выбранный блок, Esc — закрыть панель блока.
  const onKey = (event: KeyboardEvent<HTMLElement>) => {
    const field = (event.target as HTMLElement).closest?.("input, textarea, select, [role='dialog']");
    if (event.key === "Escape" && paneOpen && !(event.target as HTMLElement).closest?.("[role='dialog']")) { event.preventDefault(); setPaneOpen(false); return; }
    if (field || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) return;
    const dir = event.key === "ArrowUp" ? -1 : 1;
    event.preventDefault();
    if (event.altKey) { move(block.key, dir); return; }
    const next = order[order.indexOf(block.key) + dir];
    if (!next) return;
    select(next);
    // С карточки — фокус на соседнюю карточку, чтобы стрелки шли дальше.
    if ((event.target as HTMLElement).closest?.("[data-card]")) {
      window.setTimeout(() => canvasBox.current?.querySelector<HTMLElement>(`[data-card="${next}"]`)?.focus(), 0);
    }
  };
  const toggleOp = (seq: number) => setOpened((current) => { const next = new Set(current); if (!next.delete(seq)) next.add(seq); return next; });
  const addInputTo = (to: string) => {
    onChange((current) => addInputBlock(current, to));
    setProblem(""); setAdded(""); setPicking(true); setNameOnPick(true); setPaneOpen(true);
  };
  // «+» на связи: новый блок между её концами, библиотека — сразу в него (блок назовётся по операции).
  const insertOn = (linkId: string) => {
    const edge = state.edges.find((item) => `${item.from}>${item.to}` === linkId);
    if (!edge) return;
    onChange((current) => insertBlock(current, edge.from, edge.to));
    setProblem(""); setAdded(""); setPicking(true); setNameOnPick(true); setPaneOpen(true);
  };
  const edgeEdit = (edit: (current: ManualState) => EdgeEdit) => {
    const result = edit(state);
    setProblem(result.problem ? `Так нельзя: ${result.problem}` : "");
    if (!result.problem) onChange(() => result.state);
    return !result.problem;
  };
  const edgeLabel = (edge: RouteEdge) => {
    const from = byKey.get(edge.from), to = byKey.get(edge.to);
    return `Штук из «${from ? title(from) : edge.from}» на один «${to ? title(to) : edge.to}»`;
  };
  const deleteBlock = () => {
    const count = own.ops.length;
    if (count && !window.confirm(`Удалить блок «${blockName}» и его операции (${count})?`)) return;
    onChange((current) => removeBlock(current, block.key));
    setProblem(""); setAdded("");
  };
  const inputs = state.edges.filter((edge) => edge.to === block.key);
  const candidates = inputCandidates(state, block.key);
  const blockOps = own.ops.map((seq) => bySeq.get(seq)).filter((op): op is ManualOp => !!op);
  const moveTargets = order.filter((key) => key !== block.key).map((key) => ({ key, title: title(byKey.get(key)!) }));

  // Карточки блоков — по порядку схемы, рядами по входам (как «Маршрут КБn»); высота — по числу операций.
  const coarse = coarsePointer();
  const opRow = coarse ? 40 : GEN_CARD.opRow;
  const columns = widestRow(order, state.edges);
  const cardWidth = boxWidth
    ? Math.max(CARD_MIN, Math.min(CARD_MAX, Math.floor((boxWidth - 2 * SPACING.margin - SPACING.gapX * (columns - 1)) / columns)))
    : GEN_CARD.width;
  const product = finalName.trim();
  const subtitleOf = (item: RouteBlock) => (item.final && product && title(item) !== product ? `итог: ${product}` : "");
  const sizes = new Map(order.map((key): [string, Size] => {
    const item = byKey.get(key)!;
    const rows = ownByKey.get(key)?.ops.length ?? 0;
    return [key, { width: cardWidth, height: GEN_CARD.head + (subtitleOf(item) ? SUB_ROW : 0) + rows * opRow + (coarse ? FOOT_COARSE : FOOT) + GEN_CARD.pad }];
  }));
  const places = layoutByDepth(order, state.edges, (key) => sizes.get(key)!, SPACING);
  const cards: CanvasCard[] = order.map((key) => {
    const item = byKey.get(key)!;
    const meta = ownByKey.get(key)!;
    const kind = blockKind(state, key);
    const ops = meta.ops.map((seq) => bySeq.get(seq)).filter((op): op is ManualOp => !!op);
    const minutes = ops.reduce((sum, op) => sum + opMinutes(op, partQty), 0);
    const name = title(item);
    const quantity = meta.quantity && meta.quantity !== 1 ? `${meta.quantity} шт · ` : "";
    const gone = ops.filter((op) => op.kdGone).length;
    return {
      id: key, kind, title: name, subtitle: subtitleOf(item) || undefined, ...places.get(key)!, ...sizes.get(key)!,
      meta: ops.length ? `${quantity}${ops.length} оп. · ${formatMinutes(minutes)} мин` : `${quantity}нет операций`,
      body: <>
        {ops.length > 0 && <ol className="rc-ops" aria-label={`Операции блока «${name}»`}>
          {ops.map((op) => {
            const number = position.get(op.seq) ?? 0;
            const label = opTitle(op, number);
            const time = opMinutes(op, partQty);
            return <li key={op.seq}>
              <button type="button" className={paneOpen && opened.has(op.seq) && block.key === key ? "rc-op is-selected" : "rc-op"}
                title={[`Открыть операцию ${number}: ${label}`, op.code ? `Карта ${op.code}` : ""].filter(Boolean).join("\n")}
                onClick={() => openOp(key, op.seq)}>
                <span className="rc-op-no">{number}</span>
                <span className="rc-op-name">{label}</span>
                {op.kdGone && <span className="rc-op-note" title="Этой операции больше нет в КД">нет в КД</span>}
                <span className={time > 0 ? "rc-op-time" : "rc-op-time tone-warn"}>{time > 0 ? `${formatMinutes(time)} мин` : "нет нормы"}</span>
              </button>
            </li>;
          })}
        </ol>}
        <div className="ma-card-foot">
          {meta.checked && <span className="ma-checked" title="Проверено мастером">✓ проверено</span>}
          {gone > 0 && !meta.checked && <span className="ma-gone">нет в КД: {gone}</span>}
          <button type="button" className="ma-card-add" title="Добавить вход — новый блок, который идёт в этот"
            aria-label={`Добавить вход в блок «${name}»`} onClick={() => addInputTo(key)}>+ Вход</button>
        </div>
      </>,
    };
  });
  const titleOf = (key: string) => { const found = byKey.get(key); return found ? title(found) : key; };
  const links = state.edges.map((edge) => ({
    id: `${edge.from}>${edge.to}`, from: edge.from, to: edge.to, qty: edge.per_unit, label: `«${titleOf(edge.from)}» → «${titleOf(edge.to)}»`,
  }));

  return <section className="gsec full manual-assembly" aria-label="Схема сборки" onKeyDown={onKey}>
    <div className="gsec-head manual-hdr">
      <h3>Схема сборки</h3>
      <span className="gsec-meta">{pluralBlocks(route.blocks.length)} · {ordered.length} оп.{ordered.length > 0 && <> · {formatMinutes(total)} мин на партию {partQty} шт.</>}</span>
      <span className="manual-add">
        {kd && <KdButton kd={kd} />}
        <button type="button" className="secondary small" title={`Новый полуфабрикат — вход блока «${blockName}»`}
          onClick={() => { onChange((current) => addBlock(current)); setProblem(""); setAdded(""); setNameOnPick(false); setPaneOpen(true); }}>+ Блок</button>
      </span>
    </div>
    {kd?.proposal && <KdProposalBar proposal={kd.proposal} />}
    <div className={paneOpen ? "manual-body has-pane" : "manual-body"}>
    <div className="manual-diagram">
    <p className="route-link-hint manual-keys">Карточка или операция — открыть блок · «+» на стрелке — блок между · ↑/↓ — соседний блок, Alt+↑/↓ — сдвинуть</p>
    <div className="ma-canvas" ref={boxRef}>
      <RouteCanvas cards={cards} links={links} label="Блоки схемы" controls={false} fit="width" margin={SPACING.margin}
        selected={paneOpen ? block.key : null} onSelect={(key) => { if (key) select(key); else setPaneOpen(false); }}
        onInsert={insertOn}
        onRemoveLink={(id) => { const edge = state.edges.find((item) => `${item.from}>${item.to}` === id); if (edge) edgeEdit((current) => removeInput(current, edge.from, edge.to, finalName)); }} />
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {problem && <div className="warning-inline" role="alert">{problem}</div>}
    </div>

    {paneOpen && <div className="manual-block" ref={panel} role="region" aria-label={`Блок «${blockName}»`}>
      <div className="manual-block-bar">
        <span className={`ma-kind is-${blockKind(state, block.key)}`}>{CARD_KIND_LABEL[blockKind(state, block.key)]}</span>
        <strong title={named(block).full}>Блок «{blockName}»</strong>
        <span className="manual-block-order" role="group" aria-label="Порядок блока">
          <button type="button" className="secondary small" disabled={order.indexOf(block.key) <= 0} onClick={() => move(block.key, -1)}>↑ Выше</button>
          <button type="button" className="secondary small" disabled={order.indexOf(block.key) >= order.length - 1} onClick={() => move(block.key, 1)}>↓ Ниже</button>
        </span>
        <button type="button" className="primary small" onClick={() => setPaneOpen(false)}>Готово</button>
        <button type="button" className="btn-remove" title="Закрыть панель блока (Esc)" aria-label="Закрыть панель блока" onClick={() => setPaneOpen(false)}>×</button>
      </div>
      <div className="manual-block-head">
        <Field label={block.final ? "Название блока · итог — изделие" : "Название блока"} className="manual-block-name">
          <input aria-label="Название блока" value={own.name} placeholder={title({ ...block, name: "" })}
            onChange={(event) => onChange((current) => renameBlock(current, block.key, event.target.value))} /></Field>
        <div className="manual-inputs">
          <span className="gfield-label">Входы</span>
          <div className="manual-input-list">
            {inputs.map((edge) => {
              const from = byKey.get(edge.from);
              const name = from ? title(from) : edge.from;
              return <span className="manual-input" key={edge.from}>
                <button type="button" className="btn-link" onClick={() => select(edge.from)}>{name}</button>
                <PerUnit value={edge.per_unit} label={edgeLabel(edge)} unit="шт" lead="берёт"
                  onChange={(value) => onChange((current) => setPerUnit(current, edge.from, edge.to, value))} />
                <button type="button" className="btn-remove" title="Убрать вход" aria-label={`Убрать вход «${name}»`}
                  onClick={() => edgeEdit((current) => removeInput(current, edge.from, block.key, finalName))}>×</button>
              </span>;
            })}
            {!inputs.length && <span className="muted">нет — блок начинает сборку</span>}
            {candidates.length > 0 && <span className="manual-input-add">
              <select aria-label="Добавить вход из блока" value="" onChange={(event) => { if (event.target.value) edgeEdit((current) => addInput(current, event.target.value, block.key, finalName)); }}>
                <option value="">+ вход из блока…</option>
                {candidates.map((other) => <option key={other.key} value={other.key}>{title(byKey.get(other.key)!)}</option>)}
              </select>
            </span>}
          </div>
        </div>
        {!block.final && <button type="button" className="secondary small manual-block-delete" onClick={deleteBlock}>Удалить блок</button>}
        <div className="manual-input-list manual-block-attrs" style={{ gridColumn: "1 / -1" }}>
          {!block.final && <>
            <span className="muted">Вид</span>
            <select aria-label="Вид блока" style={{ width: "auto" }} value={blockKind(state, block.key)}
              onChange={(event) => onChange((current) => setBlockKind(current, block.key, event.target.value as "semiFinished" | "assembly"))}>
              <option value="semiFinished">{BLOCK_KIND_NAMES.semiFinished}</option>
              <option value="assembly">{BLOCK_KIND_NAMES.assembly}</option>
            </select>
            <span className="muted">Штук в блоке</span>
            <PerUnit value={own.quantity ?? 1} label={`Штук в блоке «${blockName}»`} unit="шт"
              onChange={(value) => onChange((current) => setBlockQuantity(current, block.key, value))} />
          </>}
          <span className={own.checked ? "manual-comp on" : "manual-comp"}>
            <label><input type="checkbox" checked={!!own.checked} onChange={(event) => onChange((current) => setBlockChecked(current, block.key, event.target.checked))} /> Проверено мастером</label>
          </span>
          {!!own.refs?.length && <span className="muted" title={own.refs.join(", ")}>Объекты КД: {own.refs.length}</span>}
        </div>
      </div>

      <div className="manual-ops-head">
        <strong>Операции блока «{blockName}»{blockOps.length ? ` · ${blockOps.length}` : ""}</strong>
        <span className="manual-add">
          <button type="button" className="secondary small" aria-expanded={picking} onClick={() => { setPicking(!picking); setAdded(""); setNameOnPick(false); }}>+ Операция из библиотеки</button>
          <button type="button" className="secondary small" onClick={() => { const seq = state.nextSeq; setOpened((current) => new Set(current).add(seq)); onChange((current) => addCustomOp(current, block.key)); }}>+ Своя операция</button>
        </span>
      </div>
      {picking && <LibraryPicker library={library} opRecs={opRecs} target={blockName} added={added} onClose={() => { setPicking(false); setAdded(""); }} onAdd={(cards) => {
        onChange((current) => {
          const next = cards.reduce((acc, card) => addLibraryOp(acc, card, opRecs, block.key), current);
          return nameOnPick ? nameBlockAfter(next, block.key, cardLabel(cards[0])) : next;
        });
        setAdded(`Добавлено в «${blockName}»: ${cards.length === 1 ? `«${cardLabel(cards[0])}»` : `${cards.length} оп.`}`);
      }} />}
      <div className="manual-ops">
        {!blockOps.length && <div className="gempty">{ordered.length
          ? `В блоке «${blockName}» нет операций — добавьте операцию из библиотеки или свою.`
          : "Выберите блок и добавьте операцию из библиотеки или свою."}</div>}
        {blockOps.map((op, index) => <ManualOpCard key={op.seq} op={op} position={position.get(op.seq) ?? index + 1} first={index === 0} last={index === blockOps.length - 1}
          partQty={partQty} components={components} onChange={onChange}
          open={opened.has(op.seq)} onToggle={() => toggleOp(op.seq)} moveTargets={moveTargets} />)}
      </div>
    </div>}
    </div>
  </section>;
}

/** Итог ручной сборки (правая панель): операции по блокам в порядке номеров, норма на партию, всего. */
export function ManualSummary({ state, partQty, finalName, onSelect }: { state: ManualState; partQty: number; finalName: string; onSelect?: (key: string) => void }) {
  const { route, ops } = manualRoute(state, partQty);
  const ordered = orderedOps(state);
  const position = new Map(ordered.map((op, index) => [op.seq, index + 1]));
  const bySeq = new Map(state.ops.map((op) => [op.seq, op]));
  const byKey = new Map(route.blocks.map((block) => [block.key, block]));
  const total = ordered.reduce((sum, op) => sum + opMinutes(op, partQty), 0);
  const named = (block: RouteBlock) => manualShortTitle(block, ops, finalName);
  if (!ordered.length) return <div className="gempty">Добавьте блок и операции в него — здесь появится порядок операций.</div>;
  return <div className="manual-summary" role="group" aria-label="Порядок операций">
    {blockOrder(state).map((key) => {
      const block = state.blocks.find((item) => item.key === key)!;
      const shown = byKey.get(key)!;
      return <div className="manual-summary-block" key={key}>
        <div className={state.selected === key ? "manual-summary-name is-selected" : "manual-summary-name"}>
          {onSelect ? <button type="button" className="manual-summary-pick" aria-pressed={state.selected === key} title={`${named(shown).full} — показать блок на схеме`} onClick={() => onSelect(key)}>{named(shown).title}</button>
            : <span title={named(shown).full}>{named(shown).title}</span>}{block.final && <span className="route-block-tag">итог</span>}</div>
        {block.ops.length ? <ol>{block.ops.map((seq) => {
          const op = bySeq.get(seq);
          if (!op) return null;
          const number = position.get(seq)!;
          return <li key={seq}><span className="op-badge">{number}</span><span className="manual-summary-op">{opTitle(op, number)}{op.kdGone && <> <span className="order-chip tone-warn">нет в КД</span></>}</span>
            <span className="manual-summary-norm">{normLabel(opMinutes(op, partQty))}</span></li>;
        })}</ol> : <div className="tone-warn manual-summary-empty">нет операций</div>}
      </div>;
    })}
    <div className="manual-summary-total">Всего на партию {partQty} шт.: <b>{formatMinutes(total)} мин</b></div>
  </div>;
}
