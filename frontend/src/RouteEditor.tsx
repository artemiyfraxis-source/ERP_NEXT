import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { formatMinutes } from "./etkRoute";
import { PerUnit } from "./RouteDiagram";
import { FacesThumb, FinalThumb, RouteCanvas, TubeThumbs, WireThumbs, type CanvasCard } from "./RouteCanvas";
import { CRIMP_STAGES, EMPTY_ART, GEN_CARD, GEN_SPACING, blockCards, crimpWires, genArtHeight, genCardHeight, genCards, inputsHeight, layoutByDepth, opsMinutes, widestRow, type CardKind, type GenArt, type GenStage } from "./routeCards";
import { pluralRu } from "./tcGenLogic";
import {
  addEdge,
  blockSequence,
  blockTitle,
  deriveRoute,
  dropOp,
  feedBlock,
  inputCandidates,
  inverseOrder,
  joinBlocks,
  mergeBlocks,
  moveBlock,
  moveOp,
  neighbours,
  removeEdge,
  renameBlock,
  routeLevels,
  routeProblem,
  routeTotals,
  setPerUnit,
  settleOrder,
  sortedRoute,
  splitBlock,
  type Route,
  type RouteBlock,
  type RouteEdge,
  type RouteOp,
} from "./routeLogic";

/** Куда падает перетаскиваемое: на операцию блока (до/после неё), на блок целиком, на пустое место. */
type Drop = { kind: "op"; block: string; position: number; after: boolean } | { kind: "card"; block: string; zone: "join" | "feed" } | { kind: "canvas" };
type Drag = { kind: "op" | "block"; key: string; position: number; label: string; x: number; y: number; drop: Drop | null };
/** Шаг истории: схема до правки (null — автоматическая) и, если правка переставила операции ЭТК, их порядок. */
type HistoryStep = { prev: Route | null; order: number[] | null; sig: string };
const DRAG_THRESHOLD = 4;

const coarsePointer = () => { try { return !!window.matchMedia?.("(pointer: coarse)").matches; } catch { return false; } };

/**
 * Схема полуфабрикатов сборки генератора (ARCH-TRACK-1; карточки — владелец 12.10): карточки физических полуфабрикатов
 * («Нарезанные провода», «Нарезанные трубки», «Обжатые провода», «Сторона А собрана», «Сторона В собрана», «Готовый
 * кабель») с рисунком и
 * своими операциями — название, время на штуку, код карты в подсказке; на связи — сколько штук берёт следующая
 * карточка, когда не одну. Карточки — вид поверх блоков схемы ЭТК (routeCards.genCards): операция открывает свой блок —
 * он настраивается как в генераторе. Без правок схема собирается автоматически (`value` = null); любая правка делает
 * её ручной, «Сбросить к автоматической» возвращает null. «Изменить схему» добавляет в блок «Выше/Ниже» — порядок
 * операций в ЭТК (`onMove`).
 *
 * «Изменить схему» включает управление мышью (не на сенсорном экране — там остаются «Выше/Ниже» и списки панели): карточки
 * — блоки схемы. Операцию тянут на другую операцию (встаёт до/после неё: порядок в блоке и перенос между блоками), на
 * блок (в конец блока), на пустое место (новый блок, питает прежний). Блок тянут за карточку: на нижнюю часть другого
 * блока — объединить, на верхнюю — «подать в него» (стрелки идут за ним). Опустевший блок исчезает. После каждого броска
 * операции ЭТК перенумеруются по порядку блоков (источник раньше потребителя, `onMove`), схема становится ручной;
 * «Отменить» (Ctrl+Z) возвращает последнюю правку. Схема не по правилам сервера не принимается — «Так нельзя: …».
 */
export function RouteEditor({ ops, finalName = "", quantity = 1, value, onChange, error, notice, skipped, renderOp, derive = deriveRoute, defaultOpen = true, timeSource = "время из последней сборки ЭТК", onMove, orderChanged = false, onResetOrder, art = EMPTY_ART, blockView = false, preview }: {
  /** Операции, которые попадут в сборку: номер, название, норма на партию (null — ЭТК ещё не собиралась). */
  ops: RouteOp[];
  /** Наименование изделия — второй строкой у «Готового кабеля». */
  finalName?: string;
  /** Партия, на которую заданы нормы ops. */
  quantity?: number;
  /** Ручная схема; null — автоматическая. */
  value: Route | null;
  onChange: (route: Route | null) => void;
  /** Ответ сервера «Схема полуфабрикатов: …» (422). */
  error?: string;
  notice?: string;
  /** Номера операций формы без шаблона — в сборку и в схему они не входят. */
  skipped?: number[];
  /** Операция так, как её настраивает генератор (шаблон и времена) — показывается в открытом блоке. */
  renderOp?: (position: number) => ReactNode;
  /** Схема без правок (`value` = null). По умолчанию — по правилам ЭТК. */
  derive?: (ops: RouteOp[]) => Route;
  /** Схема раскрыта сразу. */
  defaultOpen?: boolean;
  /** Откуда время в итоге «на изделие». */
  timeSource?: string;
  /** «Выше/Ниже»: `order[n − 1]` — старый номер операции на месте n; `route` — ручная схема с новыми номерами (у
      автоматической — null, она выводится заново). */
  onMove?: (order: number[], route: Route | null) => void;
  /** Порядок операций свой, не генератора — «Вернуть порядок генератора». */
  orderChanged?: boolean;
  onResetOrder?: () => void;
  /** Рисунки карточек: нарезанные провода, трубки, торцы разъёмов сторон. */
  art?: GenArt;
  /** Карточка — блок схемы как есть (схема ПФ «Сложной сборки», владелец 16.10): уровень, название, что входит; иначе —
      карточки полуфабрикатов генератора по стадиям (routeCards.genCards). */
  blockView?: boolean;
  /** Схема ПФ собранной из формы ЭТК (GenPfScheme) — показывается вместо карточек, пока схему не правят; `summary` —
      её итог («4 ПФ · 9 операций»). null — схемы с сервера ещё нет, видны карточки. */
  preview?: { node: ReactNode; summary: ReactNode | null } | null;
}) {
  const [selected, setSelected] = useState("");
  const [shown, setShown] = useState<boolean | null>(null);
  const [problem, setProblem] = useState("");
  const [mergeWith, setMergeWith] = useState("");
  const [splitAfter, setSplitAfter] = useState("");
  const [addFrom, setAddFrom] = useState("");
  const [editing, setEditing] = useState(false);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [history, setHistory] = useState<HistoryStep[]>([]);
  const swallowClick = useRef(false);
  const panelRef = useRef<HTMLDivElement>(null);
  // Ширина места под схемой — карточки делят её (см. cardWidth); окно холста появляется и пропадает со «Свернуть».
  const [boxWidth, setBoxWidth] = useState(0);
  const observer = useRef<ResizeObserver | null>(null);
  const boxRef = useCallback((element: HTMLDivElement | null) => {
    observer.current?.disconnect();
    observer.current = null;
    if (!element) return;
    setBoxWidth(element.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    observer.current = new ResizeObserver(() => setBoxWidth(element.clientWidth));
    observer.current.observe(element);
  }, []);

  const route = useMemo(() => value ?? derive(ops), [value, ops, derive]);
  const totals = useMemo(() => routeTotals(route, ops, quantity), [route, ops, quantity]);
  // Блок назван своей последней операцией (имя — если технолог его дал); изделие — у карточки «Готовый кабель».
  const title = (block: RouteBlock) => blockTitle(block, ops, finalName, false);
  const numberOf = (position: number) => ops.find((op) => op.position === position)?.number ?? position;
  const byKey = new Map(route.blocks.map((block) => [block.key, block]));
  const finalBlock = route.blocks.find((block) => block.final);
  const total = finalBlock ? totals.get(finalBlock.key) : undefined;
  const open = editing || (shown ?? (defaultOpen || value !== null || !!error));
  const block = byKey.get(selected);
  const sequence = blockSequence(route).map((item) => item.key);
  const move = onMove ? (key: string, dir: -1 | 1) => {
    const moved = moveBlock(route, key, dir, ops.length);
    if (!moved) return;
    setProblem("");
    setHistory([]);
    onMove(moved.order, value !== null ? moved.route : null);
  } : undefined;
  useEffect(() => { if (block) panelRef.current?.scrollIntoView?.({ block: "nearest" }); }, [selected]); // eslint-disable-line react-hooks/exhaustive-deps

  function apply(next: Route): boolean {
    const wrong = routeProblem(next, ops, finalName);
    setProblem(wrong ? `Так нельзя: ${wrong}` : "");
    if (wrong) return false;
    setHistory([]);
    onChange(next);
    return true;
  }
  const edit = (next: Route) => { apply(next); };
  const select = (key: string) => { setSelected(selected === key ? "" : key); setProblem(""); setMergeWith(""); setSplitAfter(""); setAddFrom(""); };
  const edgeLabel = (edge: RouteEdge) => {
    const from = byKey.get(edge.from), to = byKey.get(edge.to);
    return `Штук из «${from ? title(from) : edge.from}» на один «${to ? title(to) : edge.to}»`;
  };

  // Карточки: стадии генератора или блоки схемы как есть (blockView) — дальше одним списком.
  type ViewCard = { id: string; kind: CardKind; title: string; ops: RouteOp[]; final: boolean; level?: string; inputs?: string[] };
  // Карточки — блоки схемы, когда её правят мышью или она уже ручная: карточки по стадиям сводят блоки по виду операций.
  const asBlocks = blockView || editing || value !== null;
  const gen = useMemo((): { cards: ViewCard[]; links: Array<{ id: string; from: string; to: string; qty: number | null }> } => (asBlocks
    ? blockCards(route, ops, (item) => blockTitle(item, ops, finalName, blockView), blockView ? routeLevels(route) : new Map<string, string>())
    : genCards(route, ops)), [route, ops, asBlocks, blockView, finalName]);
  const blockOf = new Map(route.blocks.flatMap((item) => item.ops.map((position) => [position, item.key] as const)));
  // Схема ПФ называет изделие в самой карточке — второй строкой его не повторяем.
  const subtitle = (card: ViewCard) => (!blockView && card.final && finalName.trim() ? finalName.trim() : "");
  const opRow = coarsePointer() ? 40 : GEN_CARD.opRow;
  // Карточки делят ширину итога по самому широкому ряду (220…400 px); уже 220 — холст уменьшается по ширине.
  const columns = widestRow(gen.cards.map((card) => card.id), gen.links);
  const cardWidth = boxWidth ? Math.max(220, Math.min(400, Math.floor((boxWidth - 2 * GEN_SPACING.margin - GEN_SPACING.gapX * (columns - 1)) / columns))) : GEN_CARD.width;
  const artHeight = (card: ViewCard) => (blockView ? inputsHeight(card.inputs?.length ?? 0) : asBlocks ? 0 : genArtHeight(card.id as GenStage, art, card.final));
  const sizes = new Map(gen.cards.map((card) => [card.id, {
    width: cardWidth, height: genCardHeight(card.ops.length, artHeight(card), opRow) + (subtitle(card) ? 16 : 0),
  }]));
  const places = layoutByDepth(gen.cards.map((card) => card.id), gen.links, (id) => sizes.get(id)!, GEN_SPACING);
  const cardArt = (card: ViewCard): ReactNode => {
    if (blockView) {
      const inputs = card.inputs ?? [];
      return inputs.length ? <ul className="rc-inputs" aria-label={`Входит: ${card.title}`}>
        {inputs.slice(0, 4).map((item, index) => <li key={index} title={item}>{item}</li>)}
        {inputs.length > 4 && <li className="rc-more" title={inputs.slice(4).join("; ")}>и ещё {inputs.length - 4}</li>}
      </ul> : null;
    }
    if (asBlocks) return null;
    if (card.id === "wires") return art.wires.length ? <WireThumbs wires={art.wires} /> : null;
    if (card.id === "tubes") return art.tubes.length ? <TubeThumbs tubes={art.tubes} /> : null;
    if (CRIMP_STAGES.has(card.id as GenStage) && !card.final) {
      const wires = crimpWires(card.id as GenStage, art);
      return wires.length ? <WireThumbs wires={wires} /> : null;
    }
    if (card.final) return art.faces.A.length || art.faces.B.length ? <FinalThumb a={art.faces.A} b={art.faces.B} color={art.wires[0]?.color} /> : null;
    const faces = art.faces[card.id === "B" ? "B" : "A"];
    return faces.length ? <FacesThumb faces={faces} /> : null;
  };
  // ── Мышь: операцию тянут на операцию / блок / пустое место, блок — на другой блок ──────────────────────────────────────
  const sig = ops.map((op) => op.key).join("|");
  const sameBlock = (position: number, block: string) => blockOf.get(position) === block;
  const blockName = (key: string) => `«${byKey.get(key) ? title(byKey.get(key)!) : key}»`;
  function resolveDrop(from: Drag, x: number, y: number): Drop | null {
    const element = document.elementFromPoint?.(x, y) ?? null;
    if (!element) return null;
    const row = element.closest<HTMLElement>("[data-op-row]");
    const card = element.closest<HTMLElement>("[data-card]");
    const block = card?.dataset.card ?? "";
    if (from.kind === "op") {
      const own = byKey.get(blockOf.get(from.position) ?? "");
      if (card && row) {
        const position = Number(row.dataset.opRow);
        if (position === from.position) return null;
        const rect = row.getBoundingClientRect();
        return { kind: "op", block, position, after: y > rect.top + rect.height / 2 };
      }
      if (card) return block === own?.key && own.ops.length < 2 ? null : { kind: "card", block, zone: "join" };
      return element.closest(".rc-view") && (own?.ops.length ?? 0) > 1 ? { kind: "canvas" } : null;
    }
    if (!card || block === from.key) return null;
    const rect = card.getBoundingClientRect();
    const final = !!byKey.get(from.key)?.final;
    return { kind: "card", block, zone: !final && y < rect.top + rect.height * 0.4 ? "feed" : "join" };
  }
  function dropText(from: Drag): string {
    const to = from.drop;
    if (!to) return from.kind === "op" ? "Бросьте на операцию, блок или пустое место" : "Бросьте на другой блок";
    if (to.kind === "op") return `${to.after ? "После" : "Перед"} операцией ${numberOf(to.position)}${sameBlock(from.position, to.block) ? "" : ` · блок ${blockName(to.block)}`}`;
    if (to.kind === "canvas") return "Новый блок — питает прежний";
    if (from.kind === "op") return sameBlock(from.position, to.block) ? "В конец блока" : `В блок ${blockName(to.block)}`;
    return to.zone === "feed" ? `Подать в ${blockName(to.block)}` : `Объединить с ${blockName(to.block)}`;
  }
  function cardClass(id: string): string {
    if (!drag) return "";
    if (drag.kind === "block" && drag.key === id) return "is-drag-src";
    const to = drag.drop;
    if (!to || to.kind === "canvas" || to.block !== id) return "";
    return to.kind === "op" ? "is-drop-into" : to.zone === "feed" ? "is-drop-feed" : "is-drop-join";
  }
  function rowClass(position: number): string {
    if (!drag) return "";
    if (drag.kind === "op" && drag.position === position) return "is-drag-src";
    const to = drag.drop;
    return to?.kind === "op" && to.position === position ? (to.after ? "is-drop-after" : "is-drop-before") : "";
  }
  /** Правка мышью: схема проверяется как сервером, операции ЭТК перенумеруются по порядку блоков, шаг уходит в историю. */
  function commit(next: Route) {
    if (next === route) return;
    const wrong = routeProblem(next, ops, finalName);
    if (wrong) { setProblem(`Так нельзя: ${wrong}`); return; }
    setProblem("");
    const settled = settleOrder(next, ops.length);
    const renumber = !!settled?.changed && !!onMove;
    const after = renumber ? settled!.order.map((position) => ops[position - 1].key).join("|") : sig;
    setHistory((steps) => steps.concat({ prev: value, order: renumber ? settled!.order : null, sig: after }));
    if (renumber) onMove!(settled!.order, settled!.route);
    else onChange(settled && !settled.changed ? settled.route : sortedRoute(next));
  }
  function dropDrag(from: Drag) {
    const to = from.drop;
    if (!to) return;
    if (from.kind === "block") { commit(to.kind === "card" ? (to.zone === "feed" ? feedBlock(route, from.key, to.block) : joinBlocks(route, from.key, to.block)) : route); return; }
    if (to.kind === "canvas") { commit(dropOp(route, from.position, null)); return; }
    if (to.kind === "card") { commit(dropOp(route, from.position, to.block)); return; }
    const rest = (byKey.get(to.block)?.ops ?? []).filter((position) => position !== from.position);
    commit(dropOp(route, from.position, to.block, rest.indexOf(to.position) + (to.after ? 1 : 0)));
  }
  function startDrag(event: ReactPointerEvent<HTMLElement>) {
    swallowClick.current = false;
    if (!editing || event.button !== 0 || event.pointerType === "touch") return;
    const target = event.target as Element;
    const card = target.closest<HTMLElement>("[data-card]");
    if (!card || target.closest("input, select, textarea, a, .rc-link-tools")) return;
    const row = target.closest<HTMLElement>("[data-op-row]");
    const key = card.dataset.card ?? "";
    const position = row ? Number(row.dataset.opRow) : 0;
    const op = ops.find((item) => item.position === position);
    const from: Drag = { kind: row ? "op" : "block", key, position, x: event.clientX, y: event.clientY, drop: null,
      label: op ? `${numberOf(position)}. ${op.label}` : byKey.get(key) ? title(byKey.get(key)!) : key };
    const origin = { x: event.clientX, y: event.clientY };
    let current = from, active = false;
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", cancel);
      window.removeEventListener("keydown", escape);
      setDrag(null);
    };
    function move(e: PointerEvent) {
      if (!active && Math.hypot(e.clientX - origin.x, e.clientY - origin.y) < DRAG_THRESHOLD) return;
      active = true;
      current = { ...from, x: e.clientX, y: e.clientY, drop: resolveDrop(from, e.clientX, e.clientY) };
      setDrag(current);
    }
    function up() { stop(); if (active) { swallowClick.current = true; dropDrag(current); } }
    function cancel() { stop(); if (active) swallowClick.current = true; }
    function escape(e: KeyboardEvent) { if (e.key === "Escape") cancel(); }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
    window.addEventListener("keydown", escape);
  }
  function undo() {
    const last = history[history.length - 1];
    if (!last || last.sig !== sig) return;
    setHistory((steps) => steps.slice(0, -1));
    setProblem("");
    if (last.order && onMove) onMove(inverseOrder(last.order), last.prev);
    else onChange(last.prev);
  }
  const canUndo = history.length > 0 && history[history.length - 1].sig === sig;
  const undoRef = useRef(undo);
  undoRef.current = undo;
  useEffect(() => {
    if (!editing) return undefined;
    const onKey = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.shiftKey || event.altKey || (event.code !== "KeyZ" && event.key.toLowerCase() !== "z")) return;
      const target = event.target as Element | null;
      if (target?.closest?.("input, textarea, select, [contenteditable='true']")) return;
      event.preventDefault();
      undoRef.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editing]);
  // Вышли из «Изменить схему» — шаги отмены не нужны.
  useEffect(() => { if (!editing) setHistory([]); }, [editing]);

  const canvasCards: CanvasCard[] = gen.cards.map((card) => {
    const minutes = opsMinutes(card.ops, quantity);
    return {
      id: card.id, kind: card.kind, title: card.level ? `${card.level} · ${card.title}` : card.title, subtitle: subtitle(card) || undefined,
      meta: `${card.ops.length} оп.${minutes != null ? ` · ${formatMinutes(minutes)} мин/шт` : ""}${blockView && route.blocks.find((item) => item.key === card.id)?.kit ? " · комплект на изделие" : ""}`,
      ...places.get(card.id)!, ...sizes.get(card.id)!,
      art: cardArt(card), className: cardClass(card.id),
      body: <ol className="rc-ops" aria-label={`Операции: ${card.title}`}>
        {card.ops.map((op) => {
          const key = blockOf.get(op.position) ?? "";
          const hint = [op.label, op.code ? `Карта ${op.code}` : "", op.codeNote ? `Карта заменена: ${op.codeNote}` : ""].filter(Boolean).join("\n");
          const mark = rowClass(op.position);
          return <li key={op.position} data-op-row={op.position} className={mark || undefined}>
            <button type="button" className={selected === key ? "rc-op is-selected" : "rc-op"} aria-pressed={selected === key} title={hint} onClick={() => select(key)}>
              <span className="rc-op-no">{numberOf(op.position)}</span>
              <span className="rc-op-name">{op.label}</span>
              {op.codeNote && <span className="rc-op-note" title={`Карта заменена: ${op.codeNote}`}>заменена</span>}
              <span className="rc-op-time">{op.minutes != null ? `${formatMinutes(op.minutes / (quantity > 0 ? quantity : 1))} мин` : "—"}</span>
            </button>
          </li>;
        })}
      </ol>,
    };
  });
  const cardTitle = new Map(gen.cards.map((card) => [card.id, card.title]));

  function panel(item: RouteBlock) {
    const { before, after } = neighbours(route, item.key);
    const adjacent = [...before, ...after];
    const candidates = inputCandidates(route, item.key);
    const others = route.blocks.filter((other) => other.key !== item.key);
    const merge = () => {
      const target = byKey.get(mergeWith);
      if (!target) return;
      const upstream = before.includes(target) ? target.key : item.key;
      const downstream = upstream === item.key ? target.key : item.key;
      if (apply(mergeBlocks(route, upstream, downstream))) { setSelected(downstream); setMergeWith(""); }
    };
    return <div className="gcard route-panel" role="region" aria-label={`Блок «${title(item)}»`} ref={panelRef}>
      <div className="route-panel-head">
        <label className="field-label">Название блока
          <input value={item.name} placeholder={title({ ...item, name: "" })} onChange={(event) => edit(renameBlock(route, item.key, event.target.value))} /></label>
        {move && editing && <span className="route-order" role="group" aria-label="Порядок в ЭТК">
          <button type="button" className="secondary small" disabled={sequence.indexOf(item.key) <= 0} onClick={() => move(item.key, -1)}>↑ Выше</button>
          <button type="button" className="secondary small" disabled={sequence.indexOf(item.key) >= sequence.length - 1} onClick={() => move(item.key, 1)}>↓ Ниже</button>
        </span>}
        <button type="button" className="btn-link" onClick={() => select(item.key)}>Закрыть блок</button>
      </div>
      <div className="route-ops">
        {item.ops.map((position) => <div className="route-op" key={position}>
          {renderOp ? renderOp(position) : <div className="route-op-row"><span className="op-badge">{numberOf(position)}</span><span className="op-title">{ops.find((op) => op.position === position)?.label}</span></div>}
          {item.ops.length > 1 && <label className="field-label route-move">Перенести операцию {numberOf(position)} в блок
            <select aria-label={`Перенести операцию ${numberOf(position)} в блок`} value="" onChange={(event) => { if (event.target.value) edit(moveOp(route, position, event.target.value)); }}>
              <option value="">— выберите —</option>
              {others.map((other) => <option key={other.key} value={other.key}>{title(other)}</option>)}
            </select></label>}
        </div>)}
      </div>
      <div className="route-actions">
        {before.length > 0 && <div className="route-inputs"><span className="field-label">Входы блока — сколько штук берёт</span>
          {route.edges.filter((edge) => edge.to === item.key).map((edge) => <div className="route-input-row" key={edge.from}>
            <span>{title(byKey.get(edge.from)!)}{edge.item?.trim() && <small className="route-input-item"> — {edge.item.trim()}</small>}</span>
            <PerUnit value={edge.per_unit} label={edgeLabel(edge)} unit="шт" lead="берёт" onChange={(next) => edit(setPerUnit(route, edge.from, edge.to, next))} />
            <button type="button" className="btn-remove" title="Убрать вход" aria-label={`Убрать вход «${title(byKey.get(edge.from)!)}»`} onClick={() => edit(removeEdge(route, edge.from, edge.to))}>×</button>
          </div>)}</div>}
        {candidates.length > 0 && <div className="route-act"><label className="field-label">Добавить вход из блока
          <select aria-label="Добавить вход из блока" value={addFrom} onChange={(event) => setAddFrom(event.target.value)}>
            <option value="">— выберите —</option>{candidates.map((other) => <option key={other.key} value={other.key}>{title(other)}</option>)}</select></label>
          <button type="button" className="secondary small" disabled={!addFrom} onClick={() => { if (apply(addEdge(route, addFrom, item.key))) setAddFrom(""); }}>Добавить вход</button></div>}
        {adjacent.length > 0 && <div className="route-act"><label className="field-label">Объединить с блоком
          <select aria-label="Объединить с блоком" value={mergeWith} onChange={(event) => setMergeWith(event.target.value)}>
            <option value="">— выберите —</option>{adjacent.map((other) => <option key={other.key} value={other.key}>{title(other)}</option>)}</select></label>
          <button type="button" className="secondary small" disabled={!mergeWith} onClick={merge}>Объединить</button></div>}
        {item.ops.length > 1 && <div className="route-act"><label className="field-label">Разделить после операции
          <select aria-label="Разделить после операции" value={splitAfter} onChange={(event) => setSplitAfter(event.target.value)}>
            <option value="">— выберите —</option>{item.ops.slice(0, -1).map((position) => <option key={position} value={position}>{numberOf(position)}. {ops.find((op) => op.position === position)?.label}</option>)}</select></label>
          <button type="button" className="secondary small" disabled={!splitAfter} onClick={() => { if (apply(splitBlock(route, item.key, Number(splitAfter)))) setSplitAfter(""); }}>Разделить</button></div>}
      </div>
    </div>;
  }

  if (!ops.length) return null;
  const manual = value !== null;
  return <section className="route-editor" aria-label="Схема полуфабрикатов">
    <div className="route-head">
      <h3>Схема полуфабрикатов</h3>
      <span className={manual ? "route-badge manual" : "route-badge"}>{manual ? "правлена вручную" : "автоматическая"}</span>
      {orderChanged && <span className="route-badge manual">свой порядок операций</span>}
      {manual && <button type="button" className="btn-link" onClick={() => { setProblem(""); onChange(null); }}>Сбросить к автоматической</button>}
      {orderChanged && onResetOrder && <button type="button" className="btn-link" onClick={() => { setProblem(""); onResetOrder(); }}>Вернуть порядок генератора</button>}
      <span className="route-head-actions">
        {editing && <button type="button" className="secondary small" disabled={!canUndo} title="Вернуть последнюю правку схемы (Ctrl+Z)" onClick={undo}>Отменить</button>}
        <button type="button" className={editing ? "primary small" : "secondary small"} aria-pressed={editing}
          onClick={() => { setEditing(!editing); setProblem(""); if (editing) { setShown(true); if (preview) setSelected(""); } }}>{editing ? "Готово" : "Изменить схему"}</button>
        {!editing && <button type="button" className="btn-link route-toggle" aria-expanded={open} onClick={() => setShown(!open)}>{open ? "Свернуть схему" : "Показать схему"}</button>}
      </span>
    </div>
    <div className="route-summary">
      {!editing && preview?.summary ? preview.summary : <>{gen.cards.length} {blockView ? "ПФ" : pluralRu(gen.cards.length, "карточка", "карточки", "карточек")} · {ops.length} {pluralRu(ops.length, "операция", "операции", "операций")}
      {total?.rolled != null && <> · на изделие <strong>{formatMinutes(total.rolled)} мин</strong> ({timeSource})</>}</>}
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="warning-inline">{notice}</div>}
    {open && <>
      {editing && <p className="route-link-hint" role="status">Тяните операцию на другую операцию, на блок или на пустое место; блок — за карточку на другой блок. Ctrl+Z — отменить. Нажатие на операцию открывает её блок.</p>}
      {!editing && preview?.node ? preview.node : <div ref={boxRef} className={["route-dnd", editing ? "is-editing" : "", drag ? "is-dragging" : "", drag?.drop?.kind === "canvas" ? "is-drop-canvas" : ""].filter(Boolean).join(" ")}
        onPointerDown={startDrag} onClickCapture={(event) => { if (swallowClick.current) { swallowClick.current = false; event.preventDefault(); event.stopPropagation(); } }}><RouteCanvas cards={canvasCards} label="Карточки схемы" controls={false} fit="width" margin={GEN_SPACING.margin}
        links={gen.links.map((link) => ({ id: link.id, from: link.from, to: link.to, qty: link.qty, label: `«${cardTitle.get(link.from)}» → «${cardTitle.get(link.to)}»` }))} /></div>}
      {drag && <div className="route-ghost" aria-hidden="true" style={{ left: drag.x + 14, top: drag.y + 14 }}>
        <strong>{drag.label}</strong><span className={drag.drop ? "" : "is-none"}>{dropText(drag)}</span></div>}
      {!!skipped?.length && <div className="route-summary">Без шаблона, в схему не входят: {skipped.join(", ")}</div>}
      {problem && <div className="warning-inline" role="alert">{problem}</div>}
      {block && (editing || !preview?.node) && panel(block)}
    </>}
  </section>;
}
