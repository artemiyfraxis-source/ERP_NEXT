import { isDark, type ConnectorHousing } from "./connectorArt";
import type { PinoutEnd, PinUnit } from "./pinoutCheck";
import { pinCountFromArt } from "./terminalPick";
import { linkWires, spliceLabel, type LinkAnchor, type LinkWire } from "./tcGenSplices";
import type { GenState, Side } from "./tcGenLogic";
import { canonicalColor } from "./wireColors";
import "./pinoutLinks.css";

/*
 * Схема связей (владелец 13.10: «не рисуются связи»): как на чертеже заказчика — разъёмы стороны А таблицами контактов
 * слева, стороны В справа, провода цветными линиями с контакта на контакт, сростки точками посередине, хвосты —
 * свободными концами с длиной. Только просмотр: провода ставят на торцах разъёмов выше, сростки и хвосты — в панели
 * провода «в руке».
 */

const ROW = 18, HEAD = 22, TABLE_W = 168, NUM_W = 24, GAP_Y = 14, MID = 440, STUB = 74, SPLICE_GAP = 30, TAIL_DROP = 13;
const NEUTRAL = "#8a94a6", OUTLINE = "#3b4652";

/** Цвет провода словом: выбранный на жиле, иначе цвет из наименования («НВ4 0,5 красный» → «красный»). */
export function colorWord(u: Pick<PinUnit, "colorName" | "name">): string {
  if (u.colorName) return u.colorName;
  const words = String(u.name || "").split(/[\s,;]+/).reverse();
  for (const word of words) { const c = canonicalColor(word); if (c) return c; }
  return "";
}

type Box = { end: PinoutEnd; x: number; y: number; rows: number };
type Pt = { x: number; y: number };

/** Раскладка схемы: таблицы разъёмов, точки сростков, концы проводов. Чистая — для теста и для рисунка. */
export function linkLayout(ends: PinoutEnd[], units: PinUnit[], housings: Record<string, ConnectorHousing | null>, S: Pick<GenState, "splices" | "tails">) {
  const wires = linkWires(ends, units, S);
  const width = TABLE_W * 2 + MID;
  const boxes: Box[] = [];
  (["A", "B"] as Side[]).forEach((side) => {
    let y = 0;
    ends.filter((e) => e.side === side).forEach((end) => {
      const used = units.map((u) => u.at[end.id] ?? 0);
      const rows = Math.min(60, Math.max(1, housings[end.conn]?.n || pinCountFromArt(end.conn) || 0, ...used));
      boxes.push({ end, x: side === "A" ? 0 : width - TABLE_W, y, rows });
      y += HEAD + rows * ROW + GAP_Y;
    });
  });
  const pinPt = (epId: string, pin: number): Pt | null => {
    const box = boxes.find((b) => b.end.id === epId);
    return box ? { x: box.end.side === "A" ? box.x + TABLE_W : box.x, y: box.y + HEAD + (pin - 0.5) * ROW } : null;
  };
  // Сросток — посередине, на высоте проводов, что в нём сходятся (их другой конец); сростки не налезают друг на друга.
  const ids = (S.splices || []).map((s) => s.id).filter((id) => wires.some((w) => [...w.a, ...w.b].some((x) => x.kind === "splice" && x.id === id)));
  const far = (id: string) => wires.flatMap((w) => {
    const here = (list: LinkAnchor[]) => list.some((x) => x.kind === "splice" && x.id === id);
    const other = here(w.a) ? w.b : here(w.b) ? w.a : [];
    return other.flatMap((x) => (x.kind === "pin" ? [pinPt(x.epId, x.pin)!.y] : []));
  });
  const nodes = ids.map((id) => { const ys = far(id); return { id, y: ys.length ? ys.reduce((s, y) => s + y, 0) / ys.length : 0 }; }).sort((a, b) => a.y - b.y);
  nodes.forEach((n, i) => { if (i && n.y < nodes[i - 1].y + SPLICE_GAP) n.y = nodes[i - 1].y + SPLICE_GAP; });
  const spliceX = TABLE_W + MID / 2;
  const splicePt = (id: string): Pt | null => { const n = nodes.find((x) => x.id === id); return n ? { x: spliceX, y: n.y } : null; };
  const pt = (x: LinkAnchor): Pt | null => (x.kind === "pin" ? pinPt(x.epId, x.pin) : x.kind === "splice" ? splicePt(x.id) : null);
  // Линии: каждый конец стороны А с каждым концом стороны В; хвост и «не задан» — отрезок от другого конца, чуть ниже
  // точки (не поверх проводов, что уходят из сростка), несколько хвостов одной точки — лесенкой.
  const drops = new Map<string, number>();
  const lines = wires.flatMap((w) => {
    const from = w.a.map(pt).filter((p): p is Pt => !!p), to = w.b.map(pt).filter((p): p is Pt => !!p);
    if (from.length && to.length) return from.flatMap((a) => to.map((b) => ({ w, a, b, free: null as null | "tail" | "open", left: false })));
    const loose = (w.a[0]?.kind === "tail" || w.b[0]?.kind === "tail") ? "tail" as const : "open" as const;
    const base = from.length ? from : to;
    const dir = from.length ? 1 : -1;
    return base.map((p) => {
      const key = `${p.x}:${p.y}:${dir}`, n = (drops.get(key) || 0) + 1;
      drops.set(key, n);
      return { w, a: p, b: { x: p.x + dir * STUB, y: p.y + n * TAIL_DROP }, free: loose, left: dir < 0 };
    });
  });
  const height = Math.max(40, ...boxes.map((b) => b.y + HEAD + b.rows * ROW), ...nodes.map((n) => n.y + 16), ...lines.map((l) => Math.max(l.a.y, l.b.y) + 8));
  return { width, height, boxes, nodes, lines, wires };
}

/** Схема связей под торцами разъёмов. Нет ни одного провода с контактом или сростком — ничего не рисует. */
export function PinoutLinks({ ends, units, housings, joins }: {
  ends: PinoutEnd[];
  units: PinUnit[];
  housings: Record<string, ConnectorHousing | null>;
  joins: Pick<GenState, "splices" | "tails">;
}) {
  const L = linkLayout(ends, units, housings, joins);
  if (!L.wires.length) return null;
  const byPin = (epId: string, pin: number) => units.filter((u) => u.at[epId] === pin);
  const where = (w: LinkWire, side: Side) => (side === "A" ? w.a : w.b).map((x) => (x.kind === "pin" ? `${ends.find((e) => e.id === x.epId)?.key ?? ""}·${x.pin}` : x.kind === "splice" ? spliceLabel(joins, x.id) : x.kind === "tail" ? "хвост" : "—")).join(" + ");
  return <div className="pin-links" role="group" aria-label="Схема связей">
    <div className="pin-links-head"><span className="pin-links-title">Схема связей</span>
      <span className="muted">{L.wires.length} пр.{L.nodes.length ? ` · сростков ${L.nodes.length}` : ""}{L.lines.some((l) => l.free === "tail") ? ` · хвостов ${L.lines.filter((l) => l.free === "tail").length}` : ""}</span></div>
    <svg className="pin-links-svg" viewBox={`-8 -4 ${L.width + 16} ${L.height + 12}`} width={L.width + 16} role="img" aria-label="Провода с контакта на контакт, сростки и хвосты">
      {L.boxes.map((b) => <g key={b.end.id} className="pin-links-table" data-end={b.end.key}>
        <rect x={b.x} y={b.y} width={TABLE_W} height={HEAD + b.rows * ROW} rx={3} className="pin-links-frame" />
        <text x={b.x + 6} y={b.y + 15} className="pin-links-name"><tspan className="pin-links-key">{b.end.key}</tspan> {b.end.conn || "разъём не выбран"}</text>
        {Array.from({ length: b.rows }, (_, i) => {
          const pin = i + 1, here = byPin(b.end.id, pin), y = b.y + HEAD + i * ROW;
          const numX = b.end.side === "A" ? b.x + TABLE_W - NUM_W : b.x;
          const textX = b.end.side === "A" ? b.x + 6 : b.x + NUM_W + 6;
          return <g key={pin}>
            <line x1={b.x} x2={b.x + TABLE_W} y1={y} y2={y} className="pin-links-rule" />
            <line x1={numX + (b.end.side === "A" ? 0 : NUM_W)} x2={numX + (b.end.side === "A" ? 0 : NUM_W)} y1={y} y2={y + ROW} className="pin-links-rule" />
            <text x={numX + NUM_W / 2} y={y + 13} textAnchor="middle" className="pin-links-num">{pin}</text>
            <text x={textX} y={y + 13} className={here.length ? "pin-links-wire" : "pin-links-wire is-empty"}>{here.map(colorWord).filter(Boolean).join(", ") || (here.length ? here.map((u) => u.num).join(", ") : "")}</text>
          </g>;
        })}
      </g>)}
      {L.lines.map((l, i) => {
        const c = l.w.unit.color ?? NEUTRAL, dx = Math.max(24, (l.b.x - l.a.x) / 2);
        const dir = l.left ? -1 : 1;
        const d = l.free ? `M${l.a.x},${l.a.y} C${l.a.x + dir * 12},${l.a.y} ${l.a.x + dir * 12},${l.b.y} ${l.a.x + dir * 26},${l.b.y} H${l.b.x}`
          : `M${l.a.x},${l.a.y} C${l.a.x + dx},${l.a.y} ${l.b.x - dx},${l.b.y} ${l.b.x},${l.b.y}`;
        const len = l.w.unit.length ? `${l.w.unit.length} мм` : "";
        const tip = `${l.w.unit.num} · ${l.w.unit.name || "провод"} · ${where(l.w, "A")} → ${where(l.w, "B")}${len ? ` · ${len}` : ""}`;
        return <g key={i} className="pin-links-line" data-wire={l.w.unit.num} data-free={l.free ?? undefined}
          data-a={where(l.w, "A")} data-b={where(l.w, "B")}>
          <title>{tip}</title>
          {(c === NEUTRAL || !isDark(c)) && <path d={d} fill="none" stroke={OUTLINE} strokeWidth={4} strokeLinecap="round" opacity={0.7} />}
          <path d={d} fill="none" stroke={c} strokeWidth={2.4} strokeLinecap="round" strokeDasharray={l.free === "open" ? "5 4" : undefined} />
          {l.w.unit.stripe && <path d={d} fill="none" stroke={l.w.unit.stripe} strokeWidth={2.4} strokeDasharray="4 4" />}
        </g>;
      })}
      {L.nodes.map((n) => {
        const first = L.wires.find((w) => [...w.a, ...w.b].some((x) => x.kind === "splice" && x.id === n.id));
        const c = first?.unit.color ?? NEUTRAL;
        return <g key={n.id} className="pin-links-splice" data-splice={spliceLabel(joins, n.id)}>
          <circle cx={TABLE_W + MID / 2} cy={n.y} r={5.5} fill={c} stroke={OUTLINE} strokeWidth={1.2} />
          <text x={TABLE_W + MID / 2} y={n.y - 9} textAnchor="middle" className="pin-links-node">{spliceLabel(joins, n.id)}</text>
        </g>;
      })}
      {/* Подписи хвостов — поверх всех линий. */}
      {L.lines.filter((l) => l.free).map((l, i) => {
        const len = l.w.unit.length ? ` ${l.w.unit.length} мм` : "";
        return <text key={i} x={l.b.x + (l.left ? -4 : 4)} y={l.b.y + 4} textAnchor={l.left ? "end" : "start"} className="pin-links-tail">
          {l.free === "tail" ? `хвост${len}` : "не задан"}</text>;
      })}
    </svg>
  </div>;
}
