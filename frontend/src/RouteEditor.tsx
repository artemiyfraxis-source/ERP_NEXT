import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { formatMinutes } from "./etkRoute";
import { PerUnit } from "./RouteDiagram";
import { FacesThumb, FinalThumb, RouteCanvas, TubeThumbs, WireThumbs, type CanvasCard } from "./RouteCanvas";
import { EMPTY_ART, GEN_CARD, GEN_SPACING, genArtHeight, genCardHeight, genCards, layoutByDepth, opsMinutes, widestRow, type GenArt, type GenCard } from "./routeCards";
import { pluralRu } from "./tcGenLogic";
import {
  addEdge,
  blockSequence,
  blockTitle,
  deriveRoute,
  inputCandidates,
  mergeBlocks,
  moveBlock,
  moveOp,
  neighbours,
  removeEdge,
  renameBlock,
  routeProblem,
  routeTotals,
  setPerUnit,
  splitBlock,
  type Route,
  type RouteBlock,
  type RouteEdge,
  type RouteOp,
} from "./routeLogic";

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
 */
export function RouteEditor({ ops, finalName = "", quantity = 1, value, onChange, error, notice, skipped, renderOp, derive = deriveRoute, defaultOpen = true, timeSource = "время из последней сборки ЭТК", onMove, orderChanged = false, onResetOrder, art = EMPTY_ART }: {
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
}) {
  const [selected, setSelected] = useState("");
  const [shown, setShown] = useState<boolean | null>(null);
  const [problem, setProblem] = useState("");
  const [mergeWith, setMergeWith] = useState("");
  const [splitAfter, setSplitAfter] = useState("");
  const [addFrom, setAddFrom] = useState("");
  const [editing, setEditing] = useState(false);
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
    onMove(moved.order, value !== null ? moved.route : null);
  } : undefined;
  useEffect(() => { if (block) panelRef.current?.scrollIntoView?.({ block: "nearest" }); }, [selected]); // eslint-disable-line react-hooks/exhaustive-deps

  function apply(next: Route): boolean {
    const wrong = routeProblem(next, ops, finalName);
    setProblem(wrong ? `Так нельзя: ${wrong}` : "");
    if (wrong) return false;
    onChange(next);
    return true;
  }
  const edit = (next: Route) => { apply(next); };
  const select = (key: string) => { setSelected(selected === key ? "" : key); setProblem(""); setMergeWith(""); setSplitAfter(""); setAddFrom(""); };
  const edgeLabel = (edge: RouteEdge) => {
    const from = byKey.get(edge.from), to = byKey.get(edge.to);
    return `Штук из «${from ? title(from) : edge.from}» на один «${to ? title(to) : edge.to}»`;
  };

  const gen = useMemo(() => genCards(route, ops), [route, ops]);
  const blockOf = new Map(route.blocks.flatMap((item) => item.ops.map((position) => [position, item.key] as const)));
  const subtitle = (card: GenCard) => (card.final && finalName.trim() ? finalName.trim() : "");
  const opRow = coarsePointer() ? 40 : GEN_CARD.opRow;
  // Карточки делят ширину итога по самому широкому ряду (220…400 px); уже 220 — холст уменьшается по ширине.
  const columns = widestRow(gen.cards.map((card) => card.id), gen.links);
  const cardWidth = boxWidth ? Math.max(220, Math.min(400, Math.floor((boxWidth - 2 * GEN_SPACING.margin - GEN_SPACING.gapX * (columns - 1)) / columns))) : GEN_CARD.width;
  const sizes = new Map(gen.cards.map((card) => [card.id, {
    width: cardWidth, height: genCardHeight(card.ops.length, genArtHeight(card.id, art, card.final), opRow) + (subtitle(card) ? 16 : 0),
  }]));
  const places = layoutByDepth(gen.cards.map((card) => card.id), gen.links, (id) => sizes.get(id as GenCard["id"])!, GEN_SPACING);
  const cardArt = (card: GenCard): ReactNode => {
    if (card.id === "wires") return art.wires.length ? <WireThumbs wires={art.wires} /> : null;
    if (card.id === "tubes") return art.tubes.length ? <TubeThumbs tubes={art.tubes} /> : null;
    if (card.id === "crimped" && !card.final) return art.crimped.length ? <WireThumbs wires={art.crimped} /> : null;
    if (card.final) return art.faces.A.length || art.faces.B.length ? <FinalThumb a={art.faces.A} b={art.faces.B} color={art.wires[0]?.color} /> : null;
    const faces = art.faces[card.id === "B" ? "B" : "A"];
    return faces.length ? <FacesThumb faces={faces} /> : null;
  };
  const canvasCards: CanvasCard[] = gen.cards.map((card) => {
    const minutes = opsMinutes(card.ops, quantity);
    return {
      id: card.id, kind: card.kind, title: card.title, subtitle: subtitle(card) || undefined,
      meta: `${card.ops.length} оп.${minutes != null ? ` · ${formatMinutes(minutes)} мин/шт` : ""}`,
      ...places.get(card.id)!, ...sizes.get(card.id)!,
      art: cardArt(card),
      body: <ol className="rc-ops" aria-label={`Операции: ${card.title}`}>
        {card.ops.map((op) => {
          const key = blockOf.get(op.position) ?? "";
          const hint = [op.label, op.code ? `Карта ${op.code}` : "", op.codeNote ? `Карта заменена: ${op.codeNote}` : ""].filter(Boolean).join("\n");
          return <li key={op.position}>
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
            <span>{title(byKey.get(edge.from)!)}</span>
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
        <button type="button" className={editing ? "primary small" : "secondary small"} aria-pressed={editing}
          onClick={() => { setEditing(!editing); setProblem(""); if (editing) setShown(true); }}>{editing ? "Готово" : "Изменить схему"}</button>
        {!editing && <button type="button" className="btn-link route-toggle" aria-expanded={open} onClick={() => setShown(!open)}>{open ? "Свернуть схему" : "Показать схему"}</button>}
      </span>
    </div>
    <div className="route-summary">
      {gen.cards.length} {pluralRu(gen.cards.length, "карточка", "карточки", "карточек")} · {ops.length} {pluralRu(ops.length, "операция", "операции", "операций")}
      {total?.rolled != null && <> · на изделие <strong>{formatMinutes(total.rolled)} мин</strong> ({timeSource})</>}
    </div>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="warning-inline">{notice}</div>}
    {open && <>
      {editing && <p className="route-link-hint" role="status">Нажмите операцию — откроется её блок ЭТК: {onMove ? "порядок в ЭТК (↑ Выше, ↓ Ниже), " : ""}объединить, разделить, входы и сколько штук берёт вход.</p>}
      <div ref={boxRef}><RouteCanvas cards={canvasCards} label="Карточки схемы" controls={false} fit="width" margin={GEN_SPACING.margin}
        links={gen.links.map((link) => ({ id: link.id, from: link.from, to: link.to, qty: link.qty, label: `«${cardTitle.get(link.from)}» → «${cardTitle.get(link.to)}»` }))} /></div>
      {!!skipped?.length && <div className="route-summary">Без шаблона, в схему не входят: {skipped.join(", ")}</div>}
      {problem && <div className="warning-inline" role="alert">{problem}</div>}
      {block && panel(block)}
    </>}
  </section>;
}
