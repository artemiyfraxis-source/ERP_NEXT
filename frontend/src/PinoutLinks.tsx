import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { isDark, type ConnectorHousing } from "./connectorArt";
import { wireSectionOf, type PinoutEnd, type PinUnit } from "./pinoutCheck";
import { pinCountFromArt } from "./terminalPick";
import { linkWires, spliceLabel, type LinkAnchor, type LinkWire } from "./tcGenSplices";
import type { Branch, GenState, Side } from "./tcGenLogic";
import { canonicalColor } from "./wireColors";
import "./pinoutLinks.css";

/*
 * Схема связей под торцами разъёмов (владелец 13.10: «не рисуются связи»; 14.10: «примерно такое, что бы и чертёжик был и
 * схема связей была»): разъём — таблица как на схеме Э4 чертежа (Цвет · Сечение · Марка · Терминал · Цепь · №, у стороны В
 * колонки зеркально, в подвале серия и корпус), провода — линиями своего цвета с контакта на контакт с номером провода,
 * свитые пары — свитыми линиями, сростки — точками посередине, хвосты — свободными концами с длиной. Только просмотр:
 * провода ставят на торцах разъёмов выше; вторая вкладка — «Чертёж» по той же форме.
 */

const ROW = 20, TITLE = 22, COLS_H = 18, FOOT = 18, GAP_Y = 16, STUB = 22, SPLICE_GAP = 30, TAIL_DROP = 13, TAIL_STUB = 74;
/** Свивка на схеме: шаг и размах волны, px. */
const TWIST_PITCH = 18, TWIST_AMP = 4;
const NEUTRAL = "#8a94a6", OUTLINE = "#3b4652";
/** Средняя ширина знака подписи 11 px — для обрезки текста по колонке. */
const CHAR = 5.6;

type ColKey = "color" | "section" | "mark" | "terminal" | "net" | "num";
type Column = { key: ColKey; title: string; width: number };
/** Колонки на просторном поле (27") и на узком (16": колонка формы ~680 px) — без пустой «Цепи», чтобы обе таблицы и
 *  провода между ними встали без уменьшения. */
const WIDE: Column[] = [
  { key: "color", title: "Цвет", width: 36 }, { key: "section", title: "Сечение", width: 60 }, { key: "mark", title: "Марка", width: 140 },
  { key: "terminal", title: "Терминал", width: 118 }, { key: "net", title: "Цепь", width: 64 }, { key: "num", title: "№", width: 30 },
];
const NARROW: Column[] = [
  { key: "color", title: "Цвет", width: 30 }, { key: "section", title: "Сечение", width: 54 }, { key: "mark", title: "Марка", width: 98 },
  { key: "terminal", title: "Терминал", width: 90 }, { key: "net", title: "Цепь", width: 44 }, { key: "num", title: "№", width: 24 },
];
/** Поле уже этого — узкие колонки. */
const WIDE_FROM = 1080;
/** Просвет между таблицами: не уже (провода с номерами и свивка), не шире (таблицы не разъезжаются по 27"). */
const MID_MIN = 120, MID_MAX = 560;

/** Что схеме нужно кроме распиновки: обозначение разъёма по чертежу («XS1», «GIMBAL»), терминал по разъёму, свитые
 *  группы ключами жил (`PinUnit.key`). */
export type SchemeInfo = {
  designations?: Record<string, string>;
  terminals?: Record<string, string>;
  twists?: string[][];
  /** Ответвления (провод `extra` от контакта `from`·`pin` провода `main`): у ответвления через сросток на стороне источника
   *  своего контакта нет — его линия идёт от контакта основного провода. */
  branches?: Branch[];
};

/** Цвет провода словом: выбранный на жиле, иначе цвет из наименования («НВ4 0,2 красный» → «красный»). */
export function colorWord(u: Pick<PinUnit, "colorName" | "name">): string {
  if (u.colorName) return u.colorName;
  const words = String(u.name || "").split(/[\s,;]+/).reverse();
  for (const word of words) { const c = canonicalColor(word); if (c) return c; }
  return "";
}

type Box = { end: PinoutEnd; x: number; y: number; rows: number; height: number };
type Pt = { x: number; y: number };
export type LinkLine = { w: LinkWire; a: Pt; b: Pt; free: null | "tail" | "open"; left: boolean; d: string; twisted: boolean };

const cut = (text: string, width: number) => {
  const room = Math.max(2, Math.floor((width - 8) / CHAR));
  return text.length > room ? `${text.slice(0, room - 1)}…` : text;
};

/** Колонки таблицы слева направо: сторона А — номер у правого края (к проводам), сторона В — зеркально. */
function columnsOf(side: Side, set: Column[]) {
  const list = side === "A" ? set : [...set].reverse();
  let x = 0;
  return list.map((column) => { const at = { ...column, x }; x += column.width; return at; });
}

/** Раскладка схемы: таблицы разъёмов, точки сростков, линии проводов. Чистая — для теста и для рисунка. */
export function linkLayout(ends: PinoutEnd[], units: PinUnit[], housings: Record<string, ConnectorHousing | null>,
  S: Pick<GenState, "splices" | "tails">, info: SchemeInfo = {}, room = 1200) {
  const wires = linkWires(ends, units, S).map((w) => {
    const branch = (info.branches || []).find((b) => b.extra === w.unit.idx);
    const source = branch && ends.find((e) => e.id === branch.from);
    if (!branch || !source) return w;
    const side = source.side === "A" ? "a" : "b";
    return w[side].some((x) => x.kind === "pin" || x.kind === "splice") ? w : { ...w, [side]: [{ kind: "pin", epId: branch.from, pin: branch.pin }] };
  });
  const wide = room >= WIDE_FROM;
  const nets = units.some((u) => (u.net || "").trim());
  const set = (wide ? WIDE : NARROW).filter((column) => wide || nets || column.key !== "net");
  const TABLE_W = set.reduce((sum, column) => sum + column.width, 0);
  const mid = Math.max(MID_MIN, Math.min(MID_MAX, room - 16 - TABLE_W * 2));
  const width = TABLE_W * 2 + mid;
  const boxes: Box[] = [];
  (["A", "B"] as Side[]).forEach((side) => {
    let y = 0;
    ends.filter((e) => e.side === side).forEach((end) => {
      const used = units.map((u) => u.at[end.id] ?? 0);
      const rows = Math.min(60, Math.max(1, housings[end.conn]?.n || pinCountFromArt(end.conn) || 0, ...used));
      const height = TITLE + COLS_H + rows * ROW + FOOT;
      boxes.push({ end, x: side === "A" ? 0 : width - TABLE_W, y, rows, height });
      y += height + GAP_Y;
    });
  });
  // Два провода в одном контакте (двойной обжим) — концы чуть разведены по высоте строки, видны обе линии.
  const pinPt = (epId: string, pin: number, unitKey?: string): Pt | null => {
    const box = boxes.find((b) => b.end.id === epId);
    if (!box) return null;
    const here = units.filter((u) => u.at[epId] === pin);
    const k = unitKey ? here.findIndex((u) => u.key === unitKey) : -1;
    const shift = here.length > 1 && k >= 0 ? (k - (here.length - 1) / 2) * 6 : 0;
    return { x: box.end.side === "A" ? box.x + TABLE_W : box.x, y: box.y + TITLE + COLS_H + (pin - 0.5) * ROW + shift };
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
  const spliceX = TABLE_W + mid / 2;
  const splicePt = (id: string): Pt | null => { const n = nodes.find((x) => x.id === id); return n ? { x: spliceX, y: n.y } : null; };
  const pt = (x: LinkAnchor, key: string): Pt | null => (x.kind === "pin" ? pinPt(x.epId, x.pin, key) : x.kind === "splice" ? splicePt(x.id) : null);
  /** Провод: прямой отрезок у таблицы, плавный переход, прямой отрезок у второй таблицы. */
  const route = (a: Pt, b: Pt) => {
    const dir = b.x >= a.x ? 1 : -1;
    const ax = a.x + dir * STUB, bx = b.x - dir * STUB, mx = (ax + bx) / 2;
    return `M${a.x},${a.y} H${ax} C${mx},${a.y} ${mx},${b.y} ${bx},${b.y} H${b.x}`;
  };
  // Линии: каждый конец стороны А с каждым концом стороны В; хвост и «не задан» — отрезок от другого конца, чуть ниже
  // точки (не поверх проводов, что уходят из сростка), несколько хвостов одной точки — лесенкой.
  const drops = new Map<string, number>();
  const lines: LinkLine[] = wires.flatMap((w) => {
    const from = w.a.map((x) => pt(x, w.unit.key)).filter((p): p is Pt => !!p), to = w.b.map((x) => pt(x, w.unit.key)).filter((p): p is Pt => !!p);
    if (from.length && to.length) return from.flatMap((a) => to.map((b): LinkLine => ({ w, a, b, free: null, left: false, d: route(a, b), twisted: false })));
    const loose = (w.a[0]?.kind === "tail" || w.b[0]?.kind === "tail") ? "tail" as const : "open" as const;
    const base = from.length ? from : to;
    const dir = from.length ? 1 : -1;
    return base.map((p) => {
      const key = `${p.x}:${p.y}:${dir}`, n = (drops.get(key) || 0) + 1;
      drops.set(key, n);
      const b = { x: p.x + dir * TAIL_STUB, y: p.y + n * TAIL_DROP };
      const d = `M${p.x},${p.y} C${p.x + dir * 12},${p.y} ${p.x + dir * 12},${b.y} ${p.x + dir * 26},${b.y} H${b.x}`;
      return { w, a: p, b, free: loose, left: dir < 0, d, twisted: false };
    });
  });
  // Свивка: провода группы сходятся к общей оси и идут волной со сдвигом фазы, затем расходятся к своим контактам.
  const twists = (info.twists || []).flatMap((keys) => {
    const members = keys.map((key) => lines.find((l) => !l.free && l.w.unit.key === key && l.b.x > l.a.x)).filter((l): l is LinkLine => !!l);
    if (members.length < 2) return [];
    const ax = Math.max(...members.map((l) => l.a.x)) + STUB, bx = Math.min(...members.map((l) => l.b.x)) - STUB;
    const span = bx - ax;
    if (span < TWIST_PITCH * 4) return [];
    const x0 = ax + span * 0.2, x1 = bx - span * 0.2;
    const c0 = members.reduce((s, l) => s + l.a.y, 0) / members.length, c1 = members.reduce((s, l) => s + l.b.y, 0) / members.length;
    members.forEach((l, i) => {
      const phase = (2 * Math.PI * i) / members.length;
      const yAt = (x: number) => c0 + (c1 - c0) * ((x - x0) / (x1 - x0)) + TWIST_AMP * Math.sin((2 * Math.PI * (x - x0)) / TWIST_PITCH + phase);
      const wave: string[] = [];
      for (let x = x0; x <= x1; x += 2) wave.push(`${x.toFixed(1)},${yAt(x).toFixed(1)}`);
      const lx = l.a.x + STUB, rx = l.b.x - STUB, y0 = yAt(x0), y1 = yAt(x1);
      l.d = `M${l.a.x},${l.a.y} H${lx} C${(lx + x0) / 2},${l.a.y} ${(lx + x0) / 2},${y0} ${x0},${y0} L${wave.join(" L")} `
        + `C${(x1 + rx) / 2},${y1} ${(x1 + rx) / 2},${l.b.y} ${rx},${l.b.y} H${l.b.x}`;
      l.twisted = true;
    });
    return [{ keys: members.map((l) => l.w.unit.key), x: (x0 + x1) / 2, y: Math.min(c0, c1) - TWIST_AMP - 6, count: members.length }];
  });
  const height = Math.max(40, ...boxes.map((b) => b.y + b.height), ...nodes.map((n) => n.y + 16), ...lines.map((l) => Math.max(l.a.y, l.b.y) + 8));
  return { width, height, boxes, nodes, lines, wires, twists, mid, set, tableW: TABLE_W };
}

/** Строка таблицы: что лежит в контакте (два провода — двойной обжим — через «+»). */
function rowCells(here: PinUnit[], terminal: string): Record<Exclude<ColKey, "color" | "num">, string> {
  const uniq = (list: string[]) => [...new Set(list.filter(Boolean))].join(" + ");
  return {
    section: uniq(here.map((u) => (u.shield ? "" : wireSectionOf(u.name)))),
    mark: uniq(here.map((u) => u.name || "провод")),
    terminal: here.length ? terminal : "",
    net: uniq(here.map((u) => (u.net || "").trim())),
  };
}

/** Квадрат цвета жилы в таблице: двухцветная — с полосой, два провода в контакте — два квадрата. */
function ColorChip({ units, x, y }: { units: PinUnit[]; x: number; y: number }) {
  const size = 12;
  return <g className="pin-links-chip">
    {units.slice(0, 2).map((u, i) => {
      const cx = x + i * (size + 2);
      const c = u.color ?? NEUTRAL;
      return <g key={u.key}>
        <rect x={cx} y={y} width={size} height={size} rx={2} fill={c} className="pin-links-swatch" />
        {u.stripe && <path d={`M${cx + size},${y} L${cx + size},${y + size} L${cx},${y + size} Z`} fill={u.stripe} />}
      </g>;
    })}
  </g>;
}

export type SchemeView = "scheme" | "drawing";

/** Ширина поля под схему: по ней — колонки и просвет между таблицами (без ResizeObserver — как на 27"). */
function useRoom() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(1200);
  useLayoutEffect(() => {
    const node = ref.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const measure = () => { const next = Math.round(node.clientWidth); if (next > 0) setWidth((current) => (Math.abs(current - next) > 4 ? next : current)); };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  });
  return { ref, width };
}

/** Схема связей под торцами разъёмов и переключатель «Схема связей | Чертёж». `drawing` — чертёж по той же форме
 *  (рисуется, только пока открыта его вкладка). Разъёмов нет — ничего не рисует. */
export function PinoutLinks({ ends, units, housings, joins, info = {}, drawing }: {
  ends: PinoutEnd[];
  units: PinUnit[];
  housings: Record<string, ConnectorHousing | null>;
  joins: Pick<GenState, "splices" | "tails">;
  info?: SchemeInfo;
  drawing?: () => ReactNode;
}) {
  const [view, setView] = useState<SchemeView>("scheme");
  const room = useRoom();
  if (!ends.length) return null;
  const L = linkLayout(ends, units, housings, joins, info, room.width);
  const TABLE_W = L.tableW;
  const byPin = (epId: string, pin: number) => units.filter((u) => u.at[epId] === pin);
  const where = (w: LinkWire, side: Side) => (side === "A" ? w.a : w.b).map((x) => (x.kind === "pin" ? `${ends.find((e) => e.id === x.epId)?.key ?? ""}·${x.pin}` : x.kind === "splice" ? spliceLabel(joins, x.id) : x.kind === "tail" ? "хвост" : "—")).join(" + ");
  const tails = L.lines.filter((l) => l.free === "tail").length;
  const twisted = new Set(L.twists.flatMap((t) => t.keys));
  const shown = drawing && view === "drawing" ? "drawing" : "scheme";
  return <div className="pin-links" role="group" aria-label="Схема связей">
    <div className="pin-links-head">
      {drawing ? <div className="seg-tabs pin-links-tabs" role="tablist" aria-label="Вид соединений">
        {([["scheme", "Схема связей"], ["drawing", "Чертёж"]] as const).map(([id, title]) =>
          <button type="button" role="tab" key={id} aria-selected={shown === id} className={shown === id ? "on" : undefined} onClick={() => setView(id)}>{title}</button>)}
      </div> : <span className="pin-links-title">Схема связей</span>}
      <span className="muted">{L.wires.length} пр.{L.nodes.length ? ` · сростков ${L.nodes.length}` : ""}{tails ? ` · хвостов ${tails}` : ""}{L.twists.length ? ` · свивок ${L.twists.length}` : ""}</span>
    </div>
    {shown === "drawing" ? drawing!() : <div className="pin-links-scroll" ref={room.ref}>
      <svg className="pin-links-svg" viewBox={`-8 -4 ${L.width + 16} ${L.height + 12}`} width={L.width + 16} role="img" aria-label="Провода с контакта на контакт, сростки и хвосты">
        {L.boxes.map((b) => {
          const cols = columnsOf(b.end.side, L.set);
          const designation = info.designations?.[b.end.id] ?? "";
          const hs = housings[b.end.conn];
          const series = (hs?.title || "").trim();
          const terminal = info.terminals?.[b.end.id] ?? "";
          const top = b.y + TITLE + COLS_H;
          return <g key={b.end.id} className="pin-links-table" data-end={b.end.key}>
            <rect x={b.x} y={b.y} width={TABLE_W} height={b.height} className="pin-links-frame" />
            <rect x={b.x} y={b.y} width={TABLE_W} height={TITLE} className="pin-links-band" />
            <text x={b.x + 8} y={b.y + 15} className="pin-links-name"><tspan className="pin-links-key">{b.end.key}</tspan>
              {designation ? <tspan dx={8}>{designation}</tspan> : null}</text>
            {cols.map((c) => <text key={c.key} x={b.x + c.x + c.width / 2} y={b.y + TITLE + 13} textAnchor="middle" className="pin-links-colhead">{c.title}</text>)}
            <line x1={b.x} x2={b.x + TABLE_W} y1={b.y + TITLE + COLS_H} y2={b.y + TITLE + COLS_H} className="pin-links-rule is-strong" />
            {cols.slice(1).map((c) => <line key={c.key} x1={b.x + c.x} x2={b.x + c.x} y1={b.y + TITLE} y2={top + b.rows * ROW} className="pin-links-rule" />)}
            {Array.from({ length: b.rows }, (_, i) => {
              const pin = i + 1, here = byPin(b.end.id, pin), y = top + i * ROW;
              const cells = rowCells(here, terminal);
              return <g key={pin} className={here.length ? "pin-links-row is-used" : "pin-links-row"} data-pin={pin} data-wires={here.map((u) => u.num).join(" ") || undefined}>
                {i > 0 && <line x1={b.x} x2={b.x + TABLE_W} y1={y} y2={y} className="pin-links-rule" />}
                {cols.map((c) => {
                  const cx = b.x + c.x;
                  if (c.key === "color") return here.length ? <ColorChip key={c.key} units={here} x={cx + (c.width - (here.length > 1 ? 26 : 12)) / 2} y={y + 4} /> : null;
                  if (c.key === "num") return <text key={c.key} x={cx + c.width / 2} y={y + 14} textAnchor="middle" className="pin-links-num">{pin}</text>;
                  const value = cells[c.key];
                  if (!value) return null;
                  const shown = cut(value, c.width);
                  return <text key={c.key} x={cx + 5} y={y + 14} className="pin-links-cell">{shown !== value && <title>{value}</title>}{shown}</text>;
                })}
              </g>;
            })}
            <line x1={b.x} x2={b.x + TABLE_W} y1={top + b.rows * ROW} y2={top + b.rows * ROW} className="pin-links-rule is-strong" />
            <text x={b.x + 8} y={top + b.rows * ROW + 13} className="pin-links-foot"><title>{[series, b.end.conn].filter(Boolean).join(" · ")}</title>
              {cut([series, b.end.conn || "разъём не выбран"].filter(Boolean).join("  "), TABLE_W)}</text>
          </g>;
        })}
        {L.lines.map((l, i) => {
          const c = l.w.unit.color ?? NEUTRAL;
          const len = l.w.unit.length ? `${l.w.unit.length} мм` : "";
          const tip = `${l.w.unit.num} · ${l.w.unit.name || "провод"} · ${where(l.w, "A")} → ${where(l.w, "B")}${len ? ` · ${len}` : ""}${twisted.has(l.w.unit.key) ? " · свивка" : ""}`;
          return <g key={i} className="pin-links-line" data-wire={l.w.unit.num} data-free={l.free ?? undefined} data-twisted={l.twisted || undefined}
            data-a={where(l.w, "A")} data-b={where(l.w, "B")}>
            <title>{tip}</title>
            {(c === NEUTRAL || !isDark(c) || l.twisted) && <path d={l.d} fill="none" stroke={OUTLINE} strokeWidth={4} strokeLinecap="round" strokeLinejoin="round" opacity={0.7} />}
            <path d={l.d} fill="none" stroke={c} strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round" strokeDasharray={l.free === "open" ? "5 4" : undefined} />
            {l.w.unit.stripe && <path d={l.d} fill="none" stroke={l.w.unit.stripe} strokeWidth={2.4} strokeDasharray="4 4" />}
          </g>;
        })}
        {/* Номер провода — у начала линии, над ней; провод с двух концов на одной стороне — у каждого конца. */}
        {L.lines.filter((l, i, all) => all.findIndex((x) => x.w === l.w && x.a.x === l.a.x && x.a.y === l.a.y) === i).map((l, i) =>
          <text key={i} x={l.left ? l.a.x - 6 : l.a.x + 6} y={l.a.y - 3} textAnchor={l.left ? "end" : "start"} className="pin-links-label">{l.w.unit.num}</text>)}
        {L.twists.map((t, i) => <text key={i} x={t.x} y={t.y} textAnchor="middle" className="pin-links-twist">{t.count > 2 ? `свивка ×${t.count}` : "свитая пара"}</text>)}
        {L.nodes.map((n) => {
          const first = L.wires.find((w) => [...w.a, ...w.b].some((x) => x.kind === "splice" && x.id === n.id));
          const c = first?.unit.color ?? NEUTRAL;
          return <g key={n.id} className="pin-links-splice" data-splice={spliceLabel(joins, n.id)}>
            <circle cx={TABLE_W + L.mid / 2} cy={n.y} r={5.5} fill={c} stroke={OUTLINE} strokeWidth={1.2} />
            <text x={TABLE_W + L.mid / 2} y={n.y - 9} textAnchor="middle" className="pin-links-node">{spliceLabel(joins, n.id)}</text>
          </g>;
        })}
        {/* Подписи хвостов — поверх всех линий. */}
        {L.lines.filter((l) => l.free).map((l, i) => {
          const len = l.w.unit.length ? ` ${l.w.unit.length} мм` : "";
          return <text key={i} x={l.b.x + (l.left ? -4 : 4)} y={l.b.y + 4} textAnchor={l.left ? "end" : "start"} className="pin-links-tail">
            {l.free === "tail" ? `хвост${len}` : "не задан"}</text>;
        })}
      </svg>
      {!L.wires.length && <small className="muted pin-links-empty">Линии появятся, когда провод встанет на контакт.</small>}
    </div>}
  </div>;
}
