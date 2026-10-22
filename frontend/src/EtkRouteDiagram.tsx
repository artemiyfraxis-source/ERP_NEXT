import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import type { EtkRoute } from "./api";
import type { PfArt } from "./etkPfArt";
import { etkOpRuns, etkPfComponentInputs, etkPfGroups, etkPfNeed, etkRoutePathDiagram, etkPfOutName, etkRouteChips, etkRouteDiagram, etkRouteSummary, formatMinutes, opNumber, type EtkPfGroup, type EtkPfOperation, type EtkPfRow } from "./etkRoute";
import { formatQty } from "./format";
import { CableArt } from "./RouteCanvas";
import "./EtkRouteDiagram.css";

/* Схема ПФ — навигация ЭТК (владелец 17.10: список ПФ слева убран, «всё, что там, надо переносить в UML и уже делать
   нормальные эскизы полуфабрикатов»). Сеть блоков слева направо — колонка на уровень, блок на ПФ, блоки колонки по
   барицентру связей (стрелки пересекаются реже). Блок ПФ: номер, название и норма; вход из других ПФ строками; эскиз
   полуфабриката (etkPfArt.ts); операции по номерам своими названиями из генератора — щелчок открывает операцию; выход
   и его количество. ПФ открытой операции раскрыт — шире, эскиз крупно, у операций код карты; остальные компактные.
   Владелец 17.10 («растяни по экрану, ну очень сжал; на планшете … пусть там можно будет листать по бокам»): колонки
   делят всю ширину рамки, но не уже читаемой; что не вошло (16", планшет) — листается вбок внутри своей рамки (полоса
   прокрутки, тени у краёв, кнопки «‹ ›»), страница вбок не едет; полос ниже больше нет. Стрелки комплекта тонкие и без
   подписи, на остальных — только «×N». Наведение или фокус на блоке подсвечивает его связи и соседей.
   Владелец 19.10 («чуть раздвинуть; чтобы связи подсвечивались, когда нажимаешь по карточке; вся карточка сначала
   кликабельна для выбора, после выбора уже проваливаться внутрь»): первый щелчок по карточке выбирает ПФ — связи и
   соседи горят, пока выбор не снят (и на планшете); щелчок по операции выбранного или открытого ПФ открывает её;
   щелчок по пустому месту схемы или Esc снимает выбор. Колонки и блоки — с запасом места между ними.
   Владелец 20.10 («расстояние между блоками по горизонтали раза в три шире, чтобы нормально были стрелочки, а её я буду
   листать по бокам»): промежуток между колонками втрое шире — на планшете 12" видно две-три колонки, остальное вбок.
   В задании монтажника (без `onPick`) операции не открываются: его ПФ раскрыт, его операции (`marked`) подсвечены.
   Владелец 20.10 («на планшете так крупно эта схема, что вообще не влезает»): в задании чужие ПФ свёрнуты до названия,
   номеров операций и выхода, схема вписана по высоте окна (не мельче ZOOM_MIN); «− N % +» в шапке меняет масштаб
   везде, щелчок по проценту возвращает «вписать» (в техкарте — 100 %); выбор помнится в этом браузере.
   На 713К.4 (16 ПФ) и так упиралось в 50 % (прод 21.10): в задании по умолчанию — путь монтажника (его входы, его ПФ и
   дальше до изделия, etkRoutePathDiagram), «Вся схема» в шапке показывает все блоки. */

/** Ширина компактной колонки: не уже — текст и эскиз не читаются; не шире — блок растянут пустотой. */
export const COLUMN_MIN = 280;
export const COLUMN_MAX = 520;
/** Раскрытый ПФ — в 1,6 раза шире компактного, в этих пределах. */
export const OPEN_MIN = 560;
export const OPEN_MAX = 820;
const OPEN_RATIO = 1.6;
/** Между колонками, px: место под стрелки (владелец 20.10 — втрое шире прежних 96). */
export const GAP = 288;
/** Между блоками колонки, px. */
const ROW_GAP = 24;
const PAD = 12;
const SHOWN_INPUTS = 5;
/** Масштаб схемы: «вписать» — не мельче ZOOM_MIN (мельче не читается); кнопки «− +» — шагом ZOOM_STEP до ZOOM_MAX. */
export const ZOOM_MIN = 0.5;
const ZOOM_MAX = 1.5;
const ZOOM_STEP = 0.1;
/** Запас под полосой прокрутки и краем окна задания, px. */
const FIT_MARGIN = 40;

/** Масштаб, при котором схема высотой `height` входит в `room` по высоте: шагом 5 %, от ZOOM_MIN до 1. */
export function etkDiagramFit(height: number, room: number): number {
  if (height <= 0 || room <= 0) return 1;
  return clamp(Math.floor((room / height) * 20) / 20, ZOOM_MIN, 1);
}

function readZoom(key: string): number | null {
  try {
    const value = Number(window.localStorage.getItem(key));
    return value >= ZOOM_MIN && value <= ZOOM_MAX ? value : null;
  } catch { return null; }
}

function writeZoom(key: string, value: number | null) {
  try {
    if (value == null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, String(value));
  } catch { /* приватное окно: масштаб просто не запомнится */ }
}

type Box = { x: number; y: number; w: number; h: number };
type Geo = { boxes: Record<string, Box>; w: number; h: number };

function rowQty(row: EtkPfRow): string {
  return row.norm ? formatQty(row.norm, row.unit) || row.norm : "";
}

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

/** Ширины колонок в один ряд: вся ширина рамки `room` делится между колонками, раскрытая (`open`) — в 1,6 раза шире;
 *  уже COLUMN_MIN колонка не бывает — тогда ряд шире рамки и листается вбок. Рамка ещё не измерена — минимальные. */
export function etkDiagramWidths(open: boolean[], room: number, gap = GAP, pad = PAD): number[] {
  const count = open.length, opened = open.filter(Boolean).length;
  if (!count) return [];
  const avail = room - pad * 2 - (count - 1) * gap;
  if (room <= 0) return open.map((wide) => (wide ? OPEN_MIN : COLUMN_MIN));
  let narrow = avail / (count - opened + opened * OPEN_RATIO);
  let wide = clamp(narrow * OPEN_RATIO, OPEN_MIN, OPEN_MAX);
  if (count > opened) narrow = (avail - wide * opened) / (count - opened);
  narrow = clamp(Math.floor(narrow), COLUMN_MIN, COLUMN_MAX);
  if (count === opened) wide = clamp(avail / count, OPEN_MIN, OPEN_MAX);
  return open.map((on) => (on ? Math.floor(wide) : narrow));
}

export function EtkRouteDiagram<T extends EtkPfOperation & { position?: number }>({ route, list, batch, current, marked, onPick, title, sub, art, actions, heading = true }: {
  route: EtkRoute;
  list: T[];
  batch: number;
  /** 1-based позиция открытой операции в `list`. */
  current: number;
  /** Подсвеченные операции (1-based): задание монтажника; по умолчанию — открытая `current`. */
  marked?: number[];
  /** Открыть операцию; нет — схема только показывает (задание монтажника). */
  onPick?: (position: number) => void;
  /** Название операции в блоке — своё, из генератора («Надеть ТТК X4 «A6» — ТТК 12/4»), и вторая строка раскрытого
   *  блока (код карты, разъём, «правлена»). */
  title: (operation: T) => string;
  sub: (operation: T) => string;
  /** Эскиз ПФ (etkPfArt). */
  art: (group: EtkPfGroup, inputs: string[]) => PfArt | null;
  /** Кнопки в шапке схемы («Править ПФ»). */
  actions?: ReactNode;
  /** «Схема ПФ» и итог в шапке; нет — их пишет тот, кто встроил схему (генератор: «Схема полуфабрикатов»). */
  heading?: boolean;
}) {
  const active = route.blocks.find((block) => block.ops.includes(current))?.key;
  // Задание: только путь монтажника, пока не попросили «Вся схема».
  const [whole, setWhole] = useState(false);
  const pathOnly = !onPick && !whole && active != null;
  const diagram = pathOnly && active ? etkRoutePathDiagram(route, active) : etkRouteDiagram(route);
  const hidden = route.blocks.length - diagram.columns.reduce((sum, column) => sum + column.boxes.length, 0);
  const groups = new Map(etkPfGroups(route, list, batch).map((group) => [group.key, group]));
  // Сколько штук ПФ отдаёт на партию — выходу без нормы в результате (типовая сборка пишет её только изделию).
  const need = etkPfNeed(route, batch);
  // Комплектующие ПФ — строками входа, когда схема без блоков комплекта (у «Сложной сборки» они приходят блоками).
  const ownParts = !route.blocks.some((block) => block.kit);
  const scroller = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLDivElement>(null);
  const [room, setRoom] = useState(0);
  const [hover, setHot] = useState<string | null>(null);
  // Выбранный щелчком ПФ: его связи горят, пока выбор не снят; наведение на другие блоки его не перебивает.
  const [picked, setPicked] = useState<string | null>(null);
  const hot = picked ?? hover;
  // Сколько схемы спрятано слева и справа: тени у краёв и кнопки «‹ ›».
  const [edges, setEdges] = useState({ left: false, right: false });
  // Касание планшета: подсветка связей — только мышью и клавиатурой, иначе после касания чужие блоки остаются тусклыми.
  const touch = useRef(false);
  const [geo, setGeo] = useState<Geo>({ boxes: {}, w: 0, h: 0 });
  // Масштаб: выбранный кнопками (помнится отдельно для задания и техкарты) или null — «вписать» в задании, 100 % в техкарте.
  const zoomKey = `etk-dag-zoom:${onPick ? "card" : "task"}`;
  const [zoom, setZoomState] = useState<number | null>(() => readZoom(zoomKey));
  const setZoom = (value: number | null) => { setZoomState(value); writeZoom(zoomKey, value); };
  // Высота под схему в задании: от её верхнего края на экране (шапки окна и задания выше) до низа окна.
  const [fitRoom, setFitRoom] = useState(0);
  const scale = zoom ?? (onPick ? 1 : etkDiagramFit(geo.h, fitRoom));
  const scaleRef = useRef(scale);
  scaleRef.current = scale;
  const measure = useCallback(() => {
    const node = frame.current;
    if (!node) return;
    // Блоки меряются на экране (уже в масштабе), стрелки рисуются внутри рамки до масштаба.
    const base = node.getBoundingClientRect(), k = scaleRef.current || 1;
    const boxes: Record<string, Box> = {};
    node.querySelectorAll<HTMLElement>("[data-pf]").forEach((element) => {
      const rect = element.getBoundingClientRect();
      boxes[element.dataset.pf || ""] = { x: (rect.left - base.left) / k, y: (rect.top - base.top) / k, w: rect.width / k, h: rect.height / k };
    });
    const next = { boxes, w: node.scrollWidth, h: node.scrollHeight };
    setGeo((previous) => (JSON.stringify(previous) === JSON.stringify(next) ? previous : next));
  }, []);
  const sides = useCallback(() => {
    const node = scroller.current;
    if (!node) return;
    const next = { left: node.scrollLeft > 2, right: node.scrollLeft + node.clientWidth < node.scrollWidth - 2 };
    setEdges((previous) => (previous.left === next.left && previous.right === next.right ? previous : next));
  }, []);
  useEffect(() => {
    const node = scroller.current;
    if (!node) return undefined;
    const fit = () => {
      setRoom(node.clientWidth);
      const top = Math.min(Math.max(node.getBoundingClientRect().top, 0), window.innerHeight / 2);
      setFitRoom(Math.max(0, window.innerHeight - top - FIT_MARGIN));
    };
    fit();
    if (typeof ResizeObserver === "undefined") return undefined;
    const watcher = new ResizeObserver(() => { fit(); measure(); sides(); });
    watcher.observe(node);
    if (frame.current) watcher.observe(frame.current);
    return () => watcher.disconnect();
  }, [measure, sides]);
  // Стрелки — по измеренным блокам: высота блока зависит от эскиза и числа операций.
  useLayoutEffect(() => { measure(); sides(); });

  // Открытый ПФ вне рамки (выбрали операцию ниже, «Далее →») — рамка листается к нему, страница не двигается.
  useEffect(() => {
    const node = scroller.current;
    const box = [...(frame.current?.querySelectorAll<HTMLElement>("[data-pf]") ?? [])].find((element) => element.dataset.pf === active);
    if (!node || !box) return;
    // Положение блока в ряду — от рамки, не от колонки (offsetLeft считался бы от колонки).
    const rect = box.getBoundingClientRect();
    const left = rect.left - node.getBoundingClientRect().left + node.scrollLeft, right = left + rect.width;
    if (left >= node.scrollLeft && right <= node.scrollLeft + node.clientWidth) return;
    const target = Math.max(0, Math.min(left - PAD, right - node.clientWidth + PAD));
    if (typeof node.scrollTo === "function") node.scrollTo({ left: right - left > node.clientWidth ? left - PAD : target, behavior: "smooth" });
  }, [active, room, scale]);
  const page = (direction: number) => {
    const node = scroller.current;
    if (node && typeof node.scrollBy === "function") node.scrollBy({ left: direction * node.clientWidth * 0.8, behavior: "smooth" });
  };

  const open = diagram.columns.map((column) => column.boxes.some((box) => box.key === active));
  // Колонки делят видимую ширину рамки в масштабе схемы.
  const widths = etkDiagramWidths(open, room / scale);
  const zoomBy = (direction: number) => setZoom(clamp(Math.round((scale + direction * ZOOM_STEP) * 10) / 10, ZOOM_MIN, ZOOM_MAX));

  const incoming = new Map<string, number>();
  diagram.edges.forEach((edge) => incoming.set(edge.to, (incoming.get(edge.to) ?? 0) + 1));
  const slots = new Map<string, number>();
  const arrows = diagram.edges.flatMap((edge) => {
    const from = geo.boxes[edge.from], to = geo.boxes[edge.to];
    return from && to ? [{ edge, from, to }] : [];
  }).sort((a, b) => a.from.y + a.from.h / 2 - (b.from.y + b.from.h / 2)).map((arrow) => {
    const at = slots.get(arrow.edge.to) ?? 0;
    slots.set(arrow.edge.to, at + 1);
    const n = incoming.get(arrow.edge.to) ?? 1;
    const x1 = arrow.from.x + arrow.from.w, y1 = arrow.from.y + arrow.from.h / 2;
    const x2 = arrow.to.x, y2 = arrow.to.y + (arrow.to.h * (at + 1)) / (n + 1);
    const d = `M${x1},${y1} C${x1 + (x2 - x1) * 0.5},${y1} ${x2 - (x2 - x1) * 0.6},${y2} ${x2},${y2}`;
    return { ...arrow, x2, y2, d };
  });
  const names = new Map(diagram.columns.flatMap((column) => column.boxes).map((box) => [box.key, `ПФ ${box.label}`]));
  const linked = (key: string) => hot === key || arrows.some(({ edge }) => (edge.from === hot && edge.to === key) || (edge.to === hot && edge.from === key));
  const note = (arrow: (typeof arrows)[number]) => `${names.get(arrow.edge.from)} → ${names.get(arrow.edge.to)}${arrow.edge.text ? `: ${arrow.edge.text}` : ""}`;
  const numberOf = (position: number) => opNumber(list[position - 1]?.position || position);
  const wide = edges.left || edges.right;
  const mine = new Set(marked ?? [current]);

  return <section className="etk-route" aria-label="Схема полуфабрикатов">
    <div className="etk-route-head">
      {heading && <><strong>Схема ПФ</strong><small>{etkRouteSummary(etkRouteChips(route))}</small></>}
      {!onPick && (hidden > 0 || whole) && <button type="button" className="secondary small" aria-pressed={whole} onClick={() => setWhole(!whole)}>
        {whole ? "Только мой путь" : `Вся схема · ещё ${hidden}`}</button>}
      <span className="etk-dag-zoomer" role="group" aria-label="Масштаб схемы">
        <button type="button" className="secondary small" aria-label="Схема мельче" title="Мельче" disabled={scale <= ZOOM_MIN + 0.001} onClick={() => zoomBy(-1)}>−</button>
        <button type="button" className="secondary small etk-dag-zoom-now" title={onPick ? "Обычный размер" : "Вписать по высоте окна"}
          aria-label={`Масштаб ${Math.round(scale * 100)} %, ${onPick ? "вернуть обычный" : "вписать по высоте"}`} onClick={() => setZoom(null)}>{Math.round(scale * 100)} %</button>
        <button type="button" className="secondary small" aria-label="Схема крупнее" title="Крупнее" disabled={scale >= ZOOM_MAX - 0.001} onClick={() => zoomBy(1)}>+</button>
      </span>
      {wide && <span className="etk-dag-pager">
        <small>листайте схему вбок</small>
        <button type="button" className="secondary small" aria-label="Схема влево" title="Схема влево" disabled={!edges.left} onClick={() => page(-1)}>‹</button>
        <button type="button" className="secondary small" aria-label="Схема вправо" title="Схема вправо" disabled={!edges.right} onClick={() => page(1)}>›</button>
      </span>}
      {actions && <span className="etk-route-actions">{actions}</span>}
    </div>
    <div className={`etk-dag-view${edges.left ? " more-left" : ""}${edges.right ? " more-right" : ""}`}>
      <div className="etk-dag-scroll" ref={scroller} onScroll={sides} tabIndex={wide ? 0 : undefined} role={wide ? "group" : undefined}
        aria-label={wide ? "Схема ПФ, листается вбок" : undefined}>
        <div className="etk-dag-fit" style={scale !== 1 && geo.w ? { width: geo.w * scale, height: geo.h * scale, overflow: "hidden" } : undefined}>
        <div className={`etk-dag${hot ? " hot" : ""}`} ref={frame}
          style={{ padding: PAD, columnGap: GAP, ...(scale !== 1 ? { transform: `scale(${scale})`, transformOrigin: "0 0" } : {}) }}
          onClick={(event) => { if (!(event.target as HTMLElement).closest?.("[data-pf]")) setPicked(null); }}>
          <svg className="etk-dag-svg" width={geo.w} height={geo.h} aria-hidden="true">
            <defs><marker id="etk-dag-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
              <path d="M0,0 L8,4 L0,8 z" className="etk-dag-head" /></marker></defs>
            {[...arrows].sort((a, b) => Number(b.edge.kit) - Number(a.edge.kit)).map((arrow) => {
              const on = hot != null && (arrow.edge.from === hot || arrow.edge.to === hot);
              return <path key={`${arrow.edge.from}>${arrow.edge.to}`} d={arrow.d} markerEnd="url(#etk-dag-arrow)"
                className={`etk-dag-line${arrow.edge.kit ? " kit" : ""}${on ? " on" : hot ? " dim" : ""}`}>
                <title>{note(arrow)}</title>
              </path>;
            })}
          </svg>
          {arrows.filter((arrow) => arrow.edge.count && !arrow.edge.kit).map((arrow) => <span key={`t:${arrow.edge.from}>${arrow.edge.to}`}
            className={`etk-dag-item${hot && arrow.edge.from !== hot && arrow.edge.to !== hot ? " dim" : ""}`}
            title={note(arrow)} style={{ left: arrow.x2 - 38, top: arrow.y2 - 20 }}>{arrow.edge.count}</span>)}
          {diagram.columns.map((column, at) => <div key={at} className="etk-dag-col" style={{ width: widths[at], rowGap: ROW_GAP }}>
            {column.boxes.map((box) => {
              const group = groups.get(box.key);
              const open = box.key === active;
              // Задание: чужой ПФ — только название, номера операций и выход (схема входит в планшет).
              const brief = !onPick && !open;
              const sketch = group && !brief ? art(group, box.inputs.map((input) => input.text)) : null;
              const output = group?.output[0];
              const parts = group && !brief && ownParts ? etkPfComponentInputs(group, batch) : [];
              const room = open ? Number.MAX_SAFE_INTEGER : SHOWN_INPUTS;
              const shownParts = parts.slice(0, room);
              const inputs = brief ? [] : box.inputs.slice(0, Math.max(0, room - shownParts.length));
              const hiddenInputs = parts.length - shownParts.length + box.inputs.length - inputs.length;
              const made = output ? rowQty(output) || (need.get(box.key) ? formatQty(need.get(box.key), output.unit || "шт") : "") : "";
              const state = hot ? (linked(box.key) ? " near" : " dim") : "";
              const label = `ПФ ${box.label}`;
              // Внутрь — только у выбранного или открытого ПФ; у прочих щелчок сначала выбирает блок.
              const inside = picked === box.key || open;
              const enter = (go: () => void) => { if (inside) { setPicked(box.key); go(); } else setPicked(box.key); };
              return <section key={box.key} data-pf={box.key} aria-label={`${label} · ${box.name}`} aria-current={open ? "step" : undefined}
                className={`etk-dag-box${open ? " on" : ""}${picked === box.key ? " picked" : ""}${box.final ? " final" : ""}${box.kit ? " kit" : ""}${state}`}
                title={inside ? undefined : onPick ? "Щелчок — выбрать ПФ и подсветить связи; затем щелчок по операции — открыть её" : "Щелчок — подсветить связи ПФ"}
                onClick={() => { if (picked !== box.key) setPicked(box.key); }}
                onKeyDown={(event) => { if (event.key === "Escape") setPicked(null); }}
                onPointerDown={(event) => { touch.current = event.pointerType !== "mouse"; }}
                onPointerMove={(event) => { if (event.pointerType === "mouse") touch.current = false; }}
                onMouseEnter={() => !touch.current && setHot(box.key)} onMouseLeave={() => setHot(null)}
                onFocus={() => !touch.current && setHot(box.key)} onBlur={() => setHot(null)}>
                <button type="button" className="etk-dag-title" disabled={!group?.ops.length}
                  title={[`${label} · ${box.name}`, ...box.inputs.map((input) => `← ПФ ${input.label}: ${input.text}${input.times ? ` ${input.times}` : ""}`)].join("\n")}
                  onClick={(event) => { event.stopPropagation(); enter(() => group?.ops.length && onPick?.(group.ops[0])); }}>
                  <span className="etk-dag-top"><b>{label}</b>{box.kit && <small>комплект</small>}{box.final && <em className="etk-route-final">изделие</em>}
                    {!onPick && open && <em className="etk-route-final etk-dag-here">ваше задание</em>}
                    {group && group.minutes > 0 && <span className="etk-dag-time" title="Норма ПФ на партию">{formatMinutes(group.minutes)} мин</span>}</span>
                  <span className="etk-dag-name">{box.name}</span>
                  {brief && group && group.ops.length > 0 && <small className="etk-dag-brief">оп. {etkOpRuns(group.ops.map((position) => list[position - 1]?.position || position), opNumber)}</small>}
                </button>
                {inputs.length + shownParts.length > 0 && <span className="etk-dag-ins" aria-label="Вход">
                  {shownParts.map((part) => <span key={`part:${part.title}`} className="etk-dag-in part" title={part.title}>
                    {part.text}{part.qty && <> — <i className="num">{part.qty}</i></>}</span>)}
                  {inputs.map((input) => <span key={`${input.label}:${input.text}`} className="etk-dag-in">
                    <b>{input.label}</b> {input.text}{input.times && <i> {input.times}</i>}</span>)}
                  {hiddenInputs > 0 && <span className="etk-dag-in more">ещё {hiddenInputs}</span>}
                </span>}
                {sketch && <div className="etk-dag-art" aria-label={`Эскиз ${label}`} role="img">
                  {sketch.main && ("svg" in sketch.main ? <div dangerouslySetInnerHTML={{ __html: sketch.main.svg }} />
                    : "cable" in sketch.main ? <div className="etk-dag-cable" title={sketch.main.cable.title}><CableArt wire={sketch.main.cable} /></div>
                    : <img src={sketch.main.image} alt="" />)}
                  {sketch.layers && <div dangerouslySetInnerHTML={{ __html: sketch.layers }} />}
                </div>}
                {group && group.ops.length > 0 && !brief && <ol className="etk-dag-ops" aria-label={`Операции ${label}`}>{group.ops.map((position) => {
                  const operation = list[position - 1];
                  if (!operation) return null;
                  const second = open ? sub(operation) : "";
                  const line = <>
                    <span className="etk-no">{numberOf(position)}</span>
                    <span className="etk-dag-op">{title(operation)}{second && <small>{second}</small>}</span>
                  </>;
                  const on = mine.has(position);
                  if (!onPick) return <li key={position}><span className={`etk-dag-opline${on ? " on" : ""}`}
                    aria-current={on ? "step" : undefined} title={title(operation)}>{line}</span></li>;
                  return <li key={position}><button type="button" className={on ? "on" : undefined}
                    aria-current={on ? "step" : undefined} title={title(operation)}
                    onClick={(event) => { event.stopPropagation(); enter(() => onPick(position)); }}>{line}</button></li>;
                })}</ol>}
                {output && <div className="etk-dag-out" aria-label={`Выход ${label}`}>
                  <span title={group.output.map((row) => row.name).filter(Boolean).join("; ") || undefined}>→ {etkPfOutName(group) || "—"}{group.output.length > 1 ? ` и ещё ${group.output.length - 1}` : ""}</span>
                  <span className="num">{made}</span>
                </div>}
              </section>;
            })}
          </div>)}
        </div>
        </div>
      </div>
    </div>
  </section>;
}
