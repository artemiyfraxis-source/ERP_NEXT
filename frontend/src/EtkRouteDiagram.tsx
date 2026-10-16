import { useEffect, useRef, useState } from "react";
import type { EtkRoute } from "./api";
import { etkRouteChips, etkRouteDiagram, etkRouteSummary } from "./etkRoute";

/* Схема ПФ над ЭТК (владелец 16.10: «где и куда»): сеть блоков слева направо — колонка на уровень, блок на ПФ, стрелка на
   передачу с подписью, что именно идёт. Только чтение; блок открывает первую операцию ПФ, блок открытой операции подсвечен.
   Блоки — кнопки поверх SVG со стрелками: текст сжимается многоточием, а не вылезает из рамки. Схема шире экрана —
   прокручивается внутри своей рамки, страница вбок не едет. */

const BOX_W = 176;
const MIN_GAP = 184;
const MAX_GAP = 360;
const ROW_GAP = 10;
const SLOT = 16;
const MIN_H = 58;
const PAD = 8;

export function EtkRouteDiagram({ route, current, onPick }: { route: EtkRoute; current: number; onPick: (position: number) => void }) {
  const diagram = etkRouteDiagram(route);
  // Широкий экран растягивает промежутки между колонками (место для подписей на стрелках), узкий оставляет минимум и прокручивается.
  const scroller = useRef<HTMLDivElement>(null);
  const [room, setRoom] = useState(0);
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
  const GAP = cols > 1 ? Math.min(MAX_GAP, Math.max(MIN_GAP, (room - PAD * 2 - cols * BOX_W) / (cols - 1))) : MIN_GAP;
  const active = route.blocks.find((block) => block.ops.includes(current))?.key;
  const incoming = new Map<string, number>();
  diagram.edges.forEach((edge) => incoming.set(edge.to, (incoming.get(edge.to) ?? 0) + 1));
  const height = (key: string) => Math.max(MIN_H, ((incoming.get(key) ?? 0) + 1) * SLOT);
  const colHeight = diagram.columns.map((column) => column.boxes.reduce((sum, box) => sum + height(box.key), 0) + (column.boxes.length - 1) * ROW_GAP);
  const total = Math.max(...colHeight) + PAD * 2;
  const width = diagram.columns.length * BOX_W + (diagram.columns.length - 1) * GAP + PAD * 2;
  const pos = new Map<string, { x: number; y: number; h: number }>();
  diagram.columns.forEach((column, index) => {
    let y = PAD + (total - PAD * 2 - colHeight[index]) / 2;
    column.boxes.forEach((box) => {
      const h = height(box.key);
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
  const names = new Map(diagram.columns.flatMap((column) => column.boxes).map((box) => [box.key, `ПФ ${box.label}`]));
  return <section className="etk-route" aria-label="Схема полуфабрикатов">
    <div className="etk-route-head"><strong>Схема ПФ</strong><small>{etkRouteSummary(etkRouteChips(route))}</small></div>
    <div className="etk-dag-scroll" ref={scroller}>
      <div className="etk-dag" style={{ width, height: total }}>
        <svg width={width} height={total} aria-hidden="true">
          <defs><marker id="etk-dag-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" className="etk-dag-head" /></marker></defs>
          {ready.map((arrow) => <path key={`${arrow.edge.from}>${arrow.edge.to}`} className="etk-dag-line" markerEnd="url(#etk-dag-arrow)"
            d={`M${arrow.x1},${arrow.y1} C${arrow.x1 + GAP * 0.5},${arrow.y1} ${arrow.x2 - GAP * 0.6},${arrow.y2} ${arrow.x2},${arrow.y2}`}>
            <title>{`${names.get(arrow.edge.from)} → ${names.get(arrow.edge.to)}${arrow.edge.text ? `: ${arrow.edge.text}` : ""}`}</title>
          </path>)}
        </svg>
        {ready.filter((arrow) => arrow.edge.text).map((arrow) => <span key={`t:${arrow.edge.from}>${arrow.edge.to}`} className="etk-dag-item"
          title={arrow.edge.text} style={{ right: width - arrow.x2 + 8, top: arrow.y2 - 16, maxWidth: GAP - 18 }}>{arrow.edge.text}</span>)}
        {diagram.columns.map((column) => column.boxes.map((box) => {
          const at = pos.get(box.key)!;
          const meta = [box.ops ? `${box.ops} оп.` : "", box.perUnit].filter(Boolean).join(" · ");
          return <button key={box.key} type="button"
            className={`etk-dag-box${box.key === active ? " on" : ""}${box.final ? " final" : ""}${box.kit ? " kit" : ""}`}
            style={{ left: at.x, top: at.y, width: BOX_W, height: at.h }}
            disabled={box.first == null} aria-current={box.key === active ? "step" : undefined}
            title={`ПФ ${box.label} · ${box.name}${meta ? ` · ${meta}` : ""}`}
            onClick={() => box.first != null && onPick(box.first)}>
            <span className="etk-dag-top"><b>ПФ {box.label}</b>{box.kit && <small>комплект</small>}{box.final && <em className="etk-route-final">итог</em>}</span>
            <span className="etk-dag-name">{box.name}</span>
            {meta && <small className="etk-dag-meta">{meta}</small>}
          </button>;
        }))}
      </div>
    </div>
  </section>;
}
