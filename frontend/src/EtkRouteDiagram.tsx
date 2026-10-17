import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import type { EtkRoute } from "./api";
import type { PfArt } from "./etkPfArt";
import { etkPfGroups, etkPfOutName, etkRouteChips, etkRouteDiagram, etkRouteSummary, formatMinutes, opNumber, type EtkPfGroup, type EtkPfOperation, type EtkPfRow } from "./etkRoute";
import { formatQty } from "./format";
import { CableArt } from "./RouteCanvas";
import "./EtkRouteDiagram.css";

/* Схема ПФ — навигация ЭТК (владелец 17.10: список ПФ слева убран, «всё, что там, надо переносить в UML и уже делать
   нормальные эскизы полуфабрикатов»). Сеть блоков слева направо — колонка на уровень, блок на ПФ, блоки колонки по
   барицентру связей (стрелки пересекаются реже). Блок ПФ: номер, название и норма; вход из других ПФ строками; эскиз
   полуфабриката (etkPfArt.ts); операции по номерам — щелчок открывает операцию; выход и его количество. ПФ открытой
   операции раскрыт — шире, эскиз крупно, у операций код карты; остальные компактные. Колонки, которые не входят в ширину
   рамки, переносятся следующей полосой ниже (схема растёт вниз, а не вбок); стрелка в следующую полосу идёт по
   промежутку между полосами. Стрелки комплекта тонкие и без подписи, на остальных — только «×N». Наведение или фокус на
   блоке подсвечивает его связи и соседей. Линейная схема — те же блоки в ряд. */

const COMPACT = 224;
const COMPACT_MAX = 300;
const EXPANDED_MIN = 380;
const EXPANDED_MAX = 660;
const GAP = 48;
const PAD = 10;
const SHOWN_INPUTS = 5;

type Box = { x: number; y: number; w: number; h: number };
type Geo = { boxes: Record<string, Box>; w: number; h: number };

function rowQty(row: EtkPfRow): string {
  return row.norm ? formatQty(row.norm, row.unit) || row.norm : "";
}

/** Колонки по полосам, подряд: полос столько, сколько нужно при ширине рамки, колонки между ними поровну (не одна
 *  колонка в последней полосе); нет ширины (ещё не измерена) — одна полоса. */
export function etkDiagramBands(widths: number[], room: number, gap = GAP): number[][] {
  const fill = (limit: number) => {
    const bands: number[][] = [];
    let used = 0;
    widths.forEach((width, index) => {
      const band = bands[bands.length - 1];
      if (band && band.length < limit && (room <= 0 || used + gap + width <= room)) { band.push(index); used += gap + width; return; }
      bands.push([index]);
      used = width;
    });
    return bands;
  };
  const greedy = fill(widths.length);
  if (greedy.length < 2) return greedy;
  const even = fill(Math.ceil(widths.length / greedy.length));
  return even.length === greedy.length ? even : greedy;
}

export function EtkRouteDiagram<T extends EtkPfOperation & { position?: number }>({ route, list, batch, current, onPick, title, sub, art, actions }: {
  route: EtkRoute;
  list: T[];
  batch: number;
  /** 1-based позиция открытой операции в `list`. */
  current: number;
  onPick: (position: number) => void;
  /** Русское название операции и вторая строка (код карты, разъём, «правлена»). */
  title: (operation: T) => string;
  sub: (operation: T) => string;
  /** Эскиз ПФ (etkPfArt). */
  art: (group: EtkPfGroup, inputs: string[]) => PfArt | null;
  /** Кнопки в шапке схемы («Править ПФ»). */
  actions?: ReactNode;
}) {
  const diagram = etkRouteDiagram(route);
  const groups = new Map(etkPfGroups(route, list, batch).map((group) => [group.key, group]));
  const scroller = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLDivElement>(null);
  const [room, setRoom] = useState(0);
  const [hot, setHot] = useState<string | null>(null);
  // Касание планшета: подсветка связей — только мышью и клавиатурой, иначе после касания чужие блоки остаются тусклыми.
  const touch = useRef(false);
  const [geo, setGeo] = useState<Geo>({ boxes: {}, w: 0, h: 0 });
  const measure = useCallback(() => {
    const node = frame.current;
    if (!node) return;
    const base = node.getBoundingClientRect();
    const boxes: Record<string, Box> = {};
    node.querySelectorAll<HTMLElement>("[data-pf]").forEach((element) => {
      const rect = element.getBoundingClientRect();
      boxes[element.dataset.pf || ""] = { x: rect.left - base.left, y: rect.top - base.top, w: rect.width, h: rect.height };
    });
    const next = { boxes, w: node.scrollWidth, h: node.scrollHeight };
    setGeo((previous) => (JSON.stringify(previous) === JSON.stringify(next) ? previous : next));
  }, []);
  useEffect(() => {
    const node = scroller.current;
    if (!node) return undefined;
    const fit = () => setRoom(node.clientWidth);
    fit();
    if (typeof ResizeObserver === "undefined") return undefined;
    const watcher = new ResizeObserver(() => { fit(); measure(); });
    watcher.observe(node);
    if (frame.current) watcher.observe(frame.current);
    return () => watcher.disconnect();
  }, [measure]);
  // Стрелки — по измеренным блокам: высота блока зависит от эскиза и числа операций.
  useLayoutEffect(measure);

  const active = route.blocks.find((block) => block.ops.includes(current))?.key;
  const expanded = Math.round(Math.min(EXPANDED_MAX, Math.max(EXPANDED_MIN, room * 0.42)));
  const open = diagram.columns.map((column) => column.boxes.some((box) => box.key === active));
  const bands = etkDiagramBands(open.map((wide) => (wide ? expanded : COMPACT)), room - PAD * 2);
  // Свободное место полос (27") — в ширину компактных блоков, до COMPACT_MAX: эскиз крупнее.
  const grow = room > 0 ? Math.min(...bands.map((band) => {
    const narrow = band.filter((column) => !open[column]).length;
    const spare = room - PAD * 2 - (band.length - 1) * GAP - band.reduce((sum, column) => sum + (open[column] ? expanded : COMPACT), 0);
    return narrow ? spare / narrow : Infinity;
  })) : 0;
  const compact = Math.floor(Math.min(COMPACT_MAX, COMPACT + Math.max(0, Number.isFinite(grow) ? grow : 0)));
  const widths = open.map((wide) => (wide ? expanded : compact));
  const bandOf = new Map<string, number>();
  bands.forEach((band, at) => band.forEach((column) => diagram.columns[column].boxes.forEach((box) => bandOf.set(box.key, at))));
  const bandBottom = bands.map((band) => Math.max(0, ...band.flatMap((column) => diagram.columns[column].boxes.map((box) => {
    const found = geo.boxes[box.key];
    return found ? found.y + found.h : 0;
  }))));

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
    const fromBand = bandOf.get(arrow.edge.from) ?? 0;
    // В той же полосе — кривая; в следующую полосу — вправо, вниз в промежуток под полосой, влево и в блок слева.
    const d = fromBand === (bandOf.get(arrow.edge.to) ?? 0)
      ? `M${x1},${y1} C${x1 + (x2 - x1) * 0.5},${y1} ${x2 - (x2 - x1) * 0.6},${y2} ${x2},${y2}`
      : (() => {
        const right = x1 + GAP / 2, under = bandBottom[fromBand] + 18, left = Math.max(2, x2 - 16);
        return `M${x1},${y1} H${right} V${under} H${left} V${y2} H${x2}`;
      })();
    return { ...arrow, x2, y2, d };
  });
  const names = new Map(diagram.columns.flatMap((column) => column.boxes).map((box) => [box.key, `ПФ ${box.label}`]));
  const linked = (key: string) => hot === key || arrows.some(({ edge }) => (edge.from === hot && edge.to === key) || (edge.to === hot && edge.from === key));
  const note = (arrow: (typeof arrows)[number]) => `${names.get(arrow.edge.from)} → ${names.get(arrow.edge.to)}${arrow.edge.text ? `: ${arrow.edge.text}` : ""}`;
  const numberOf = (position: number) => opNumber(list[position - 1]?.position || position);

  return <section className="etk-route" aria-label="Схема полуфабрикатов">
    <div className="etk-route-head">
      <strong>Схема ПФ</strong><small>{etkRouteSummary(etkRouteChips(route))}</small>
      {actions && <span className="etk-route-actions">{actions}</span>}
    </div>
    <div className="etk-dag-scroll" ref={scroller}>
      <div className={`etk-dag${hot ? " hot" : ""}`} ref={frame} style={{ padding: PAD, rowGap: 36 }}>
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
          title={note(arrow)} style={{ left: arrow.x2 - 34, top: arrow.y2 - 18 }}>{arrow.edge.count}</span>)}
        {bands.map((band, at) => <div key={at} className="etk-dag-band" style={{ columnGap: GAP }}>
          {band.map((column) => <div key={column} className="etk-dag-col" style={{ width: widths[column] }}>
            {diagram.columns[column].boxes.map((box) => {
              const group = groups.get(box.key);
              const open = box.key === active;
              const inputs = open ? box.inputs : box.inputs.slice(0, SHOWN_INPUTS);
              const sketch = group ? art(group, box.inputs.map((input) => input.text)) : null;
              const output = group?.output[0];
              const state = hot ? (linked(box.key) ? " near" : " dim") : "";
              const label = `ПФ ${box.label}`;
              return <section key={box.key} data-pf={box.key} aria-label={`${label} · ${box.name}`} aria-current={open ? "step" : undefined}
                className={`etk-dag-box${open ? " on" : ""}${box.final ? " final" : ""}${box.kit ? " kit" : ""}${state}`}
                onPointerDown={(event) => { touch.current = event.pointerType !== "mouse"; }}
                onPointerMove={(event) => { if (event.pointerType === "mouse") touch.current = false; }}
                onMouseEnter={() => !touch.current && setHot(box.key)} onMouseLeave={() => setHot(null)}
                onFocus={() => !touch.current && setHot(box.key)} onBlur={() => setHot(null)}>
                <button type="button" className="etk-dag-title" disabled={!group?.ops.length}
                  title={[`${label} · ${box.name}`, ...box.inputs.map((input) => `← ПФ ${input.label}: ${input.text}${input.times ? ` ${input.times}` : ""}`)].join("\n")}
                  onClick={() => group?.ops.length && onPick(group.ops[0])}>
                  <span className="etk-dag-top"><b>{label}</b>{box.kit && <small>комплект</small>}{box.final && <em className="etk-route-final">изделие</em>}
                    {group && group.minutes > 0 && <span className="etk-dag-time" title="Норма ПФ на партию">{formatMinutes(group.minutes)} мин</span>}</span>
                  <span className="etk-dag-name">{box.name}</span>
                </button>
                {inputs.length > 0 && <span className="etk-dag-ins" aria-label="Вход">
                  {inputs.map((input) => <span key={`${input.label}:${input.text}`} className="etk-dag-in">
                    <b>{input.label}</b> {input.text}{input.times && <i> {input.times}</i>}</span>)}
                  {box.inputs.length > inputs.length && <span className="etk-dag-in more">ещё {box.inputs.length - inputs.length}</span>}
                </span>}
                {sketch && <div className="etk-dag-art" aria-label={`Эскиз ${label}`} role="img">
                  {sketch.main && ("svg" in sketch.main ? <div dangerouslySetInnerHTML={{ __html: sketch.main.svg }} />
                    : "cable" in sketch.main ? <div className="etk-dag-cable" title={sketch.main.cable.title}><CableArt wire={sketch.main.cable} /></div>
                    : <img src={sketch.main.image} alt="" />)}
                  {sketch.layers && <div dangerouslySetInnerHTML={{ __html: sketch.layers }} />}
                </div>}
                {group && group.ops.length > 0 && <ol className="etk-dag-ops" aria-label={`Операции ${label}`}>{group.ops.map((position) => {
                  const operation = list[position - 1];
                  if (!operation) return null;
                  const second = open ? sub(operation) : "";
                  return <li key={position}><button type="button" className={position === current ? "on" : undefined}
                    aria-current={position === current ? "step" : undefined} title={title(operation)} onClick={() => onPick(position)}>
                    <span className="etk-no">{numberOf(position)}</span>
                    <span className="etk-dag-op">{title(operation)}{second && <small>{second}</small>}</span>
                  </button></li>;
                })}</ol>}
                {output && <div className="etk-dag-out" aria-label={`Выход ${label}`}>
                  <span title={group.output.map((row) => row.name).filter(Boolean).join("; ") || undefined}>→ {etkPfOutName(group) || "—"}{group.output.length > 1 ? ` и ещё ${group.output.length - 1}` : ""}</span>
                  <span className="num">{rowQty(output)}</span>
                </div>}
              </section>;
            })}
          </div>)}
        </div>)}
      </div>
    </div>
  </section>;
}
