import { useEffect, useRef, useState } from "react";
import type { EtkRoute } from "./api";
import { etkRouteChips, etkRouteDiagram, etkRouteSummary } from "./etkRoute";

/* Схема ПФ над ЭТК (владелец 16.10: «где и куда», и «не читаемо» на 713К.4 КБ1 — 15 ПФ, ~28 стрелок): сеть блоков слева
   направо — колонка на уровень, блок на ПФ. Читается так: блоки колонки стоят по барицентру связей (стрелки пересекаются
   реже), стрелки от блоков комплекта тонкие и без подписи — что приходит, написано списком внутри блока-потребителя, на
   остальных стрелках только «×N». Наведение или фокус на блоке подсвечивает его стрелки и соседей, остальное тускнеет;
   клик открывает первую операцию ПФ. Название переносится на две строки, а не режется. Колонки подгоняются под ширину рамки
   (16" без прокрутки); на планшете схема шире рамки прокручивается внутри неё, страница вбок не едет. */

const MIN_BOX = 150;
const MAX_BOX = 290;
const MIN_GAP = 84;
const MAX_GAP = 300;
const ROW_GAP = 8;
const PAD = 8;
const SHOWN_INPUTS = 5;

/** Сколько строк (до двух) займёт название блока при ширине блока `width`. */
function nameLines(name: string, width: number): number {
  const perLine = Math.max(8, Math.floor((width - 22) / 6.9));
  return Math.min(2, Math.max(1, Math.ceil(name.length / perLine)));
}

export function EtkRouteDiagram({ route, current, onPick }: { route: EtkRoute; current: number; onPick: (position: number) => void }) {
  const diagram = etkRouteDiagram(route);
  const scroller = useRef<HTMLDivElement>(null);
  const [room, setRoom] = useState(0);
  const [hot, setHot] = useState<string | null>(null);
  useEffect(() => {
    const node = scroller.current;
    if (!node) return undefined;
    const measure = () => setRoom(node.clientWidth);
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const watcher = new ResizeObserver(measure);
    watcher.observe(node);
    return () => watcher.disconnect();
  }, []);
  const cols = diagram.columns.length;
  // Блок — на сколько хватает ширины рамки после минимальных промежутков; лишнее место уходит в промежутки (на 27").
  const fit = room > 0 ? (room - PAD * 2 - (cols - 1) * MIN_GAP) / cols : 176;
  const BOX_W = Math.round(Math.min(MAX_BOX, Math.max(MIN_BOX, fit)));
  const GAP = cols > 1 ? Math.min(MAX_GAP, Math.max(MIN_GAP, (room - PAD * 2 - cols * BOX_W) / (cols - 1))) : MIN_GAP;
  const active = route.blocks.find((block) => block.ops.includes(current))?.key;
  const incoming = new Map<string, number>();
  diagram.edges.forEach((edge) => incoming.set(edge.to, (incoming.get(edge.to) ?? 0) + 1));
  const boxes = new Map(diagram.columns.flatMap((column) => column.boxes).map((box) => [box.key, box]));
  const shownOf = (key: string) => boxes.get(key)!.inputs.slice(0, SHOWN_INPUTS);
  const heightOf = (key: string) => {
    const box = boxes.get(key)!;
    const meta = box.ops || box.perUnit ? 15 : 0;
    const shown = shownOf(key).length, extra = box.inputs.length > SHOWN_INPUTS ? 1 : 0;
    return 12 + 17 + nameLines(box.name, BOX_W) * 16 + meta + (shown ? 8 + (shown + extra) * 15 : 0);
  };
  const colHeight = diagram.columns.map((column) => column.boxes.reduce((sum, box) => sum + heightOf(box.key), 0) + (column.boxes.length - 1) * ROW_GAP);
  const total = Math.max(...colHeight) + PAD * 2;
  const width = cols * BOX_W + (cols - 1) * GAP + PAD * 2;
  const pos = new Map<string, { x: number; y: number; h: number }>();
  diagram.columns.forEach((column, index) => {
    let y = PAD + (total - PAD * 2 - colHeight[index]) / 2;
    column.boxes.forEach((box) => {
      const h = heightOf(box.key);
      pos.set(box.key, { x: PAD + index * (BOX_W + GAP), y, h });
      y += h + ROW_GAP;
    });
  });
  // Стрелки входят в блок по одной в свою щель, сверху вниз — по высоте источника, чтобы не пересекались у входа.
  const arrows = diagram.edges.flatMap((edge) => {
    const from = pos.get(edge.from), to = pos.get(edge.to);
    return from && to ? [{ edge, from, to }] : [];
  });
  const slots = new Map<string, number>();
  const ready = arrows.sort((a, b) => a.from.y + a.from.h / 2 - (b.from.y + b.from.h / 2))
    .map((arrow) => {
      const at = slots.get(arrow.edge.to) ?? 0;
      slots.set(arrow.edge.to, at + 1);
      const n = incoming.get(arrow.edge.to) ?? 1;
      return { ...arrow, x1: arrow.from.x + BOX_W, y1: arrow.from.y + arrow.from.h / 2, x2: arrow.to.x, y2: arrow.to.y + (arrow.to.h * (at + 1)) / (n + 1) };
    });
  const names = new Map([...boxes.values()].map((box) => [box.key, `ПФ ${box.label}`]));
  const linked = (key: string) => hot === key || ready.some(({ edge }) => (edge.from === hot && edge.to === key) || (edge.to === hot && edge.from === key));
  const note = (arrow: (typeof ready)[number]) => `${names.get(arrow.edge.from)} → ${names.get(arrow.edge.to)}${arrow.edge.text ? `: ${arrow.edge.text}` : ""}`;
  return <section className="etk-route" aria-label="Схема полуфабрикатов">
    <div className="etk-route-head"><strong>Схема ПФ</strong><small>{etkRouteSummary(etkRouteChips(route))}</small></div>
    <div className="etk-dag-scroll" ref={scroller}>
      <div className={`etk-dag${hot ? " hot" : ""}`} style={{ width, height: total }}>
        <svg width={width} height={total} aria-hidden="true">
          <defs><marker id="etk-dag-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" className="etk-dag-head" /></marker></defs>
          {[...ready].sort((a, b) => Number(b.edge.kit) - Number(a.edge.kit)).map((arrow) => {
            const on = hot != null && (arrow.edge.from === hot || arrow.edge.to === hot);
            return <path key={`${arrow.edge.from}>${arrow.edge.to}`}
              className={`etk-dag-line${arrow.edge.kit ? " kit" : ""}${on ? " on" : hot ? " dim" : ""}`} markerEnd="url(#etk-dag-arrow)"
              d={`M${arrow.x1},${arrow.y1} C${arrow.x1 + GAP * 0.5},${arrow.y1} ${arrow.x2 - GAP * 0.6},${arrow.y2} ${arrow.x2},${arrow.y2}`}>
              <title>{note(arrow)}</title>
            </path>;
          })}
        </svg>
        {ready.filter((arrow) => arrow.edge.count && !arrow.edge.kit).map((arrow) => <span key={`t:${arrow.edge.from}>${arrow.edge.to}`}
          className={`etk-dag-item${hot && arrow.edge.from !== hot && arrow.edge.to !== hot ? " dim" : ""}`}
          title={note(arrow)} style={{ right: width - arrow.x2 + 8, top: arrow.y2 - 8 }}>{arrow.edge.count}</span>)}
        {diagram.columns.map((column) => column.boxes.map((box) => {
          const at = pos.get(box.key)!;
          const meta = [box.ops ? `${box.ops} оп.` : "", box.perUnit].filter(Boolean).join(" · ");
          const shown = shownOf(box.key);
          const state = hot ? (linked(box.key) ? " near" : " dim") : "";
          return <button key={box.key} type="button"
            className={`etk-dag-box${box.key === active ? " on" : ""}${box.final ? " final" : ""}${box.kit ? " kit" : ""}${state}`}
            style={{ left: at.x, top: at.y, width: BOX_W, height: at.h }}
            disabled={box.first == null} aria-current={box.key === active ? "step" : undefined}
            title={[`ПФ ${box.label} · ${box.name}${meta ? ` · ${meta}` : ""}`, ...box.inputs.map((input) => `← ПФ ${input.label}: ${input.text}${input.times ? ` ${input.times}` : ""}`)].join("\n")}
            onMouseEnter={() => setHot(box.key)} onMouseLeave={() => setHot(null)}
            onFocus={() => setHot(box.key)} onBlur={() => setHot(null)}
            onClick={() => box.first != null && onPick(box.first)}>
            <span className="etk-dag-top"><b>ПФ {box.label}</b>{box.kit && <small>комплект</small>}{box.final && <em className="etk-route-final">итог</em>}</span>
            <span className="etk-dag-name">{box.name}</span>
            {meta && <small className="etk-dag-meta">{meta}</small>}
            {shown.length > 0 && <span className="etk-dag-ins" aria-label="Вход">
              {shown.map((input) => <span key={`${input.label}:${input.text}`} className="etk-dag-in">
                <b>{input.label}</b> {input.text}{input.times && <i> {input.times}</i>}</span>)}
              {box.inputs.length > SHOWN_INPUTS && <span className="etk-dag-in more">ещё {box.inputs.length - SHOWN_INPUTS}</span>}
            </span>}
          </button>;
        }))}
      </div>
    </div>
  </section>;
}
