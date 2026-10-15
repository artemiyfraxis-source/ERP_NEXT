import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { isDark, type ConnectorHousing } from "./connectorArt";
import { wireSectionOf, type PinoutEnd, type PinUnit } from "./pinoutCheck";
import { pinCountFromArt } from "./terminalPick";
import { linkWires } from "./tcGenSplices";
import type { Branch, GenState } from "./tcGenLogic";
import { canonicalColor } from "./wireColors";
import "./pinoutLinks.css";

/*
 * Схема связей под торцами разъёмов (владелец 13.10: «не рисуются связи»; 14.10: «примерно такое, что бы и чертёжик был и
 * схема связей была»): разъём — таблица как на схеме Э4 чертежа (Цвет · Сечение · Марка · Терминал · Цепь · №, у стороны В
 * колонки зеркально, в подвале серия и корпус), провода — линиями своего цвета с контакта на контакт с номером провода,
 * свитые пары — свитыми линиями, сростки — точками посередине, хвосты — свободными концами с длиной. Только просмотр:
 * провода ставят на торцах разъёмов выше; вторая вкладка — «Чертёж» по той же форме.
 *
 * Рисунок берёт нейтральную модель `LinkScheme` (разъёмы, провода с концами, сростки, свивки): генератор строит её из
 * формы (`pinoutScheme`, обёртка `PinoutLinks`), ЭТК — из документа (etkLinkScheme.tsx, владелец 14.10: «в ЭТК та же
 * схема связей, что в генераторе»).
 */

const ROW = 20, TITLE = 22, COLS_H = 18, FOOT = 18, GAP_Y = 16, STUB = 22, SPLICE_GAP = 30, TAIL_DROP = 13, TAIL_STUB = 74;
/** Свивка на схеме: шаг и размах волны, px. */
const TWIST_PITCH = 18, TWIST_AMP = 4, TWIST_LANE = 24;
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

// ── Модель схемы ─────────────────────────────────────────────────────────────────────────────────────────────────────
export type SchemeSide = "A" | "B";
/** Куда приходит конец провода: контакт разъёма, сросток, хвост или никуда (не задан). */
export type SchemeAnchor = { kind: "pin"; epId: string; pin: number } | { kind: "splice"; id: string } | { kind: "tail" } | { kind: "open" };
/** Разъём — таблица схемы: `key` «А1»/«В2», `designation` по чертежу («XS1», «GIMBAL»), `pins` — контактов в корпусе
 *  (0 — не известно, строк по занятым), `series` — серия корпуса в подвале, `terminal` — терминал разъёма или «пайка». */
export type SchemeConnector = { id: string; key: string; side: SchemeSide; conn: string; pins: number; series: string; designation: string; terminal: string };
/** Провод (жила, экран): контакт на каждом разъёме (`at`) и концы сторон А и В. */
export type SchemeWire = {
  key: string; num: string; name: string; color: string | null; stripe: string | null;
  /** Длина, мм (0 — не задана). */
  length: number; shield?: boolean; net?: string;
  /** Цвет, выбранный на жиле («красный»); пусто — из наименования. */
  colorName?: string;
  at: Record<string, number | null>;
  a: SchemeAnchor[]; b: SchemeAnchor[];
};
/** Схема связей целиком: свитые группы — ключами проводов, сростки по порядку с подписью «С1». */
export type LinkScheme = { connectors: SchemeConnector[]; wires: SchemeWire[]; splices: Array<{ id: string; label: string }>; twists: string[][] };

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

/** Провод рисуется, когда хоть один его конец на контакте или в сростке. */
export const drawnWire = (w: Pick<SchemeWire, "a" | "b">) => [...w.a, ...w.b].some((x) => x.kind === "pin" || x.kind === "splice");

/** Модель схемы по форме генератора: распиновка, корпуса, сростки и хвосты, обозначения, терминалы, свивки, ответвления. */
export function pinoutScheme(ends: PinoutEnd[], units: PinUnit[], housings: Record<string, ConnectorHousing | null>,
  S: Pick<GenState, "splices" | "tails">, info: SchemeInfo = {}): LinkScheme {
  const drawn = new Map(linkWires(ends, units, S).map((w) => {
    const branch = (info.branches || []).find((b) => b.extra === w.unit.idx);
    const source = branch && ends.find((e) => e.id === branch.from);
    if (!branch || !source) return [w.unit.key, w];
    const side = source.side === "A" ? "a" : "b";
    return [w.unit.key, w[side].some((x) => x.kind === "pin" || x.kind === "splice") ? w : { ...w, [side]: [{ kind: "pin", epId: branch.from, pin: branch.pin }] }];
  }));
  return {
    connectors: ends.map((end) => ({
      id: end.id, key: end.key, side: end.side, conn: end.conn, pins: housings[end.conn]?.n || pinCountFromArt(end.conn) || 0,
      series: (housings[end.conn]?.title || "").trim(), designation: info.designations?.[end.id] ?? "", terminal: info.terminals?.[end.id] ?? "",
    })),
    wires: units.map((u) => {
      const w = drawn.get(u.key);
      return { key: u.key, num: u.num, name: u.name, color: u.color, stripe: u.stripe, length: u.length, shield: u.shield, net: u.net, colorName: u.colorName, at: u.at,
        a: w ? w.a : [], b: w ? w.b : [] };
    }),
    splices: (S.splices || []).map((s, i) => ({ id: s.id, label: `С${i + 1}` })),
    twists: info.twists || [],
  };
}

type Box = { end: SchemeConnector; x: number; y: number; rows: number; height: number };
type Pt = { x: number; y: number };
export type LinkLine = { w: SchemeWire; a: Pt; b: Pt; free: null | "tail" | "open"; left: boolean; d: string; twisted: boolean };

const cut = (text: string, width: number) => {
  const room = Math.max(2, Math.floor((width - 8) / CHAR));
  return text.length > room ? `${text.slice(0, room - 1)}…` : text;
};

/** Колонки таблицы слева направо: сторона А — номер у правого края (к проводам), сторона В — зеркально. */
function columnsOf(side: SchemeSide, set: Column[]) {
  const list = side === "A" ? set : [...set].reverse();
  let x = 0;
  return list.map((column) => { const at = { ...column, x }; x += column.width; return at; });
}

/** Раскладка схемы: таблицы разъёмов, точки сростков, линии проводов. Чистая — для теста и для рисунка. */
export function schemeLayout(M: LinkScheme, room = 1200, showNet = false) {
  const units = M.wires;
  const wires = units.filter(drawnWire);
  const wide = room >= WIDE_FROM;
  const nets = units.some((u) => (u.net || "").trim());
  const set = (wide ? WIDE : NARROW).filter((column) => wide || nets || showNet || column.key !== "net");
  const TABLE_W = set.reduce((sum, column) => sum + column.width, 0);
  const mid = Math.max(MID_MIN, Math.min(MID_MAX, room - 16 - TABLE_W * 2));
  const width = TABLE_W * 2 + mid;
  const boxes: Box[] = [];
  (["A", "B"] as SchemeSide[]).forEach((side) => {
    let y = 0;
    M.connectors.filter((e) => e.side === side).forEach((end) => {
      const used = units.map((u) => u.at[end.id] ?? 0);
      const rows = Math.min(60, Math.max(1, end.pins || 0, ...used));
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
  const ids = M.splices.map((s) => s.id).filter((id) => wires.some((w) => [...w.a, ...w.b].some((x) => x.kind === "splice" && x.id === id)));
  const far = (id: string) => wires.flatMap((w) => {
    const here = (list: SchemeAnchor[]) => list.some((x) => x.kind === "splice" && x.id === id);
    const other = here(w.a) ? w.b : here(w.b) ? w.a : [];
    return other.flatMap((x) => { const p = x.kind === "pin" ? pinPt(x.epId, x.pin) : null; return p ? [p.y] : []; });
  });
  const nodes = ids.map((id) => { const ys = far(id); return { id, y: ys.length ? ys.reduce((s, y) => s + y, 0) / ys.length : 0 }; }).sort((a, b) => a.y - b.y);
  nodes.forEach((n, i) => { if (i && n.y < nodes[i - 1].y + SPLICE_GAP) n.y = nodes[i - 1].y + SPLICE_GAP; });
  const spliceX = TABLE_W + mid / 2;
  const splicePt = (id: string): Pt | null => { const n = nodes.find((x) => x.id === id); return n ? { x: spliceX, y: n.y } : null; };
  const pt = (x: SchemeAnchor, key: string): Pt | null => (x.kind === "pin" ? pinPt(x.epId, x.pin, key) : x.kind === "splice" ? splicePt(x.id) : null);
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
    const from = w.a.map((x) => pt(x, w.key)).filter((p): p is Pt => !!p), to = w.b.map((x) => pt(x, w.key)).filter((p): p is Pt => !!p);
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
  // Каждая группа — своя дорожка: оси групп не ближе LANE друг к другу (владелец 14.10: две пары не сливаются в один шнур).
  const groups = M.twists.flatMap((keys) => {
    const members = keys.map((key) => lines.find((l) => !l.free && l.w.key === key && l.b.x > l.a.x)).filter((l): l is LinkLine => !!l);
    if (members.length < 2) return [];
    const ax = Math.max(...members.map((l) => l.a.x)) + STUB, bx = Math.min(...members.map((l) => l.b.x)) - STUB;
    if (bx - ax < TWIST_PITCH * 4) return [];
    return [{ members, ax, bx, c0: members.reduce((s, l) => s + l.a.y, 0) / members.length, c1: members.reduce((s, l) => s + l.b.y, 0) / members.length }];
  }).sort((p, q) => p.c0 + p.c1 - q.c0 - q.c1);
  groups.forEach((g, i) => {
    if (!i) return;
    const prev = groups[i - 1], shift = Math.max(0, prev.c0 + TWIST_LANE - g.c0, prev.c1 + TWIST_LANE - g.c1);
    g.c0 += shift; g.c1 += shift;
  });
  const twists = groups.map(({ members, ax, bx, c0, c1 }) => {
    const span = bx - ax;
    const x0 = ax + span * 0.2, x1 = bx - span * 0.2;
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
    return { keys: members.map((l) => l.w.key), x: (x0 + x1) / 2, y: Math.min(c0, c1) - TWIST_AMP - 6, count: members.length };
  });
  const height = Math.max(40, ...boxes.map((b) => b.y + b.height), ...nodes.map((n) => n.y + 16), ...lines.map((l) => Math.max(l.a.y, l.b.y) + 8));
  return { width, height, boxes, nodes, lines, wires, twists, mid, set, tableW: TABLE_W };
}
export type SchemeLayout = ReturnType<typeof schemeLayout>;

/** Раскладка по форме генератора (тесты сложной сборки). */
export function linkLayout(ends: PinoutEnd[], units: PinUnit[], housings: Record<string, ConnectorHousing | null>,
  S: Pick<GenState, "splices" | "tails">, info: SchemeInfo = {}, room = 1200) {
  return schemeLayout(pinoutScheme(ends, units, housings, S, info), room);
}

/** Строка таблицы: что лежит в контакте (два провода — двойной обжим — через «+»). */
function rowCells(here: SchemeWire[], terminal: string): Record<Exclude<ColKey, "color" | "num">, string> {
  const uniq = (list: string[]) => [...new Set(list.filter(Boolean))].join(" + ");
  return {
    section: uniq(here.map((u) => (u.shield ? "" : wireSectionOf(u.name)))),
    mark: uniq(here.map((u) => u.name || "провод")),
    terminal: here.length ? terminal : "",
    net: uniq(here.map((u) => (u.net || "").trim())),
  };
}

/** Квадрат цвета жилы в таблице: двухцветная — с полосой, два провода в контакте — два квадрата. */
function ColorChip({ units, x, y }: { units: SchemeWire[]; x: number; y: number }) {
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

/** Подпись конца провода: «А1·2», «С1», «хвост», «—» — не задан. */
function whereOf(M: LinkScheme, w: SchemeWire, side: SchemeSide) {
  return (side === "A" ? w.a : w.b).map((x) => (x.kind === "pin" ? `${M.connectors.find((e) => e.id === x.epId)?.key ?? ""}·${x.pin}`
    : x.kind === "splice" ? M.splices.find((s) => s.id === x.id)?.label ?? "" : x.kind === "tail" ? "хвост" : "—")).join(" + ");
}

/** Правка цепи в ячейке таблицы: поле с подсказкой уже занятых цепей; Enter и уход из поля сохраняют, Esc отменяет. */
function NetEditor({ x, y, width, wire, options, onDone }: { x: number; y: number; width: number; wire: SchemeWire; options: string[]; onDone: (net: string | null) => void }) {
  const finished = useRef(false);
  const finish = (net: string | null) => { if (finished.current) return; finished.current = true; onDone(net); };
  return <foreignObject x={x} y={y} width={width} height={ROW - 2} className="pin-links-net-edit">
    <input autoFocus defaultValue={wire.net ?? ""} maxLength={64} list="pin-links-nets" aria-label={`Цепь ${wire.num}`} className="pin-links-net-input"
      onFocus={(event) => event.currentTarget.select()}
      onKeyDown={(event) => {
        if (event.key === "Enter") { event.preventDefault(); finish(event.currentTarget.value.trim()); }
        else if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); finish(null); }
      }}
      onBlur={(event) => finish(event.currentTarget.value.trim())} />
    <datalist id="pin-links-nets">{options.map((name) => <option key={name} value={name} />)}</datalist>
  </foreignObject>;
}

/** Рисунок схемы связей — SVG по раскладке (экран и печать ЭТК). `onNet` — только в генераторе: ячейка «Цепь» правится по щелчку
 *  (цепь принадлежит проводу, поэтому видна на обоих разъёмах); в ЭТК схема только для чтения. */
export function LinkSchemeSvg({ scheme: M, layout: L, onNet, onColor }: { scheme: LinkScheme; layout: SchemeLayout; onNet?: (wireKey: string, net: string) => void;
  onColor?: ColorOpen }) {
  const TABLE_W = L.tableW;
  const [editing, setEditing] = useState<string | null>(null);
  const nets = [...new Set(M.wires.map((w) => (w.net || "").trim()).filter(Boolean))];
  const byPin = (epId: string, pin: number) => M.wires.filter((u) => u.at[epId] === pin);
  const twisted = new Set(L.twists.flatMap((t) => t.keys));
  const label = (id: string) => M.splices.find((s) => s.id === id)?.label ?? "";
  return <svg className="pin-links-svg" viewBox={`-8 -4 ${L.width + 16} ${L.height + 12}`} width={L.width + 16} role="img" aria-label="Провода с контакта на контакт, сростки и хвосты">
    {L.boxes.map((b) => {
      const cols = columnsOf(b.end.side, L.set);
      const designation = b.end.designation;
      const series = b.end.series;
      const terminal = b.end.terminal;
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
              if (c.key === "color") {
                if (!here.length) return null;
                const chip = <ColorChip units={here} x={cx + (c.width - (here.length > 1 ? 26 : 12)) / 2} y={y + 4} />;
                // Генератор: щелчок по «Цвет» открывает палитру жилы этого контакта (у экрана цвета нет); в ЭТК — только чтение.
                const core = onColor ? here.find((u) => !u.shield) : undefined;
                if (!core) return <g key={c.key}>{chip}</g>;
                const open = (el: Element) => { const r = el.getBoundingClientRect(); onColor?.(core.key, { left: r.left, top: r.bottom + 4, above: r.top }); };
                return <g key={c.key} className="pin-links-color">
                  {chip}
                  <rect x={cx} y={y} width={c.width} height={ROW} className="pin-links-color-hit" role="button" tabIndex={0} data-color-opener
                    aria-label={`Цвет, ${b.end.key} контакт ${pin}: ${core.colorName || "не задан"}. Изменить`}
                    onClick={(event) => open(event.currentTarget)}
                    onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(event.currentTarget); } }}>
                    <title>{core.colorName ? `${core.colorName} — изменить цвет жилы` : "выбрать цвет жилы"}</title></rect>
                </g>;
              }
              if (c.key === "num") return <text key={c.key} x={cx + c.width / 2} y={y + 14} textAnchor="middle" className="pin-links-num">{pin}</text>;
              const value = cells[c.key];
              const target = c.key === "net" && onNet && here.length ? here[0] : null;
              if (target) {
                const id = `${b.end.id}:${pin}`;
                return <g key={c.key} className="pin-links-net">
                  <rect x={cx} y={y} width={c.width} height={ROW} className="pin-links-net-hit" role="button" tabIndex={0} data-net-cell={id}
                    aria-label={`Цепь, ${b.end.key} контакт ${pin}: ${value || "не задана"}. Изменить`}
                    onClick={() => setEditing(id)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setEditing(id); } }} />
                  {editing === id
                    ? <NetEditor x={cx + 1} y={y + 1} width={c.width - 2} wire={target} options={nets}
                      onDone={(net) => { setEditing(null); if (net !== null && net !== (target.net ?? "").trim()) onNet?.(target.key, net); }} />
                    : value ? <text x={cx + 5} y={y + 14} className="pin-links-cell">{cut(value, c.width)}</text>
                      : null}
                </g>;
              }
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
      const c = l.w.color ?? NEUTRAL;
      const len = l.w.length ? `${l.w.length} мм` : "";
      const tip = `${l.w.num} · ${l.w.name || "провод"} · ${whereOf(M, l.w, "A")} → ${whereOf(M, l.w, "B")}${len ? ` · ${len}` : ""}${twisted.has(l.w.key) ? " · свивка" : ""}`;
      return <g key={i} className="pin-links-line" data-wire={l.w.num} data-free={l.free ?? undefined} data-twisted={l.twisted || undefined}
        data-a={whereOf(M, l.w, "A")} data-b={whereOf(M, l.w, "B")}>
        <title>{tip}</title>
        {(c === NEUTRAL || !isDark(c) || l.twisted) && <path d={l.d} fill="none" stroke={OUTLINE} strokeWidth={4} strokeLinecap="round" strokeLinejoin="round" opacity={0.7} />}
        <path d={l.d} fill="none" stroke={c} strokeWidth={2.4} strokeLinecap="round" strokeLinejoin="round" strokeDasharray={l.free === "open" ? "5 4" : undefined} />
        {l.w.stripe && <path d={l.d} fill="none" stroke={l.w.stripe} strokeWidth={2.4} strokeDasharray="4 4" />}
      </g>;
    })}
    {/* Номер провода — у начала линии, над ней; провод с двух концов на одной стороне — у каждого конца. */}
    {L.lines.filter((l, i, all) => all.findIndex((x) => x.w === l.w && x.a.x === l.a.x && x.a.y === l.a.y) === i).map((l, i) =>
      <text key={i} x={l.left ? l.a.x - 6 : l.a.x + 6} y={l.a.y - 3} textAnchor={l.left ? "end" : "start"} className="pin-links-label">{l.w.num}</text>)}
    {L.twists.map((t, i) => <text key={i} x={t.x} y={t.y} textAnchor="middle" className="pin-links-twist">{t.count > 2 ? `свивка ×${t.count}` : "свитая пара"}</text>)}
    {L.nodes.map((n) => {
      const first = L.wires.find((w) => [...w.a, ...w.b].some((x) => x.kind === "splice" && x.id === n.id));
      const c = first?.color ?? NEUTRAL;
      return <g key={n.id} className="pin-links-splice" data-splice={label(n.id)}>
        <circle cx={TABLE_W + L.mid / 2} cy={n.y} r={5.5} fill={c} stroke={OUTLINE} strokeWidth={1.2} />
        <text x={TABLE_W + L.mid / 2} y={n.y - 9} textAnchor="middle" className="pin-links-node">{label(n.id)}</text>
      </g>;
    })}
    {/* Подписи хвостов — поверх всех линий. */}
    {L.lines.filter((l) => l.free).map((l, i) => {
      const len = l.w.length ? ` ${l.w.length} мм` : "";
      return <text key={i} x={l.b.x + (l.left ? -4 : 4)} y={l.b.y + 4} textAnchor={l.left ? "end" : "start"} className="pin-links-tail">
        {l.free === "tail" ? `хвост${len}` : "не задан"}</text>;
    })}
  </svg>;
}

/** «17 пр. · сростков 4 · хвостов 4 · свивок 1». */
export function schemeCounts(L: SchemeLayout) {
  const tails = L.lines.filter((l) => l.free === "tail").length;
  return `${L.wires.length} пр.${L.nodes.length ? ` · сростков ${L.nodes.length}` : ""}${tails ? ` · хвостов ${tails}` : ""}${L.twists.length ? ` · свивок ${L.twists.length}` : ""}`;
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

/** Схема связей и переключатель «Схема связей | Чертёж». `drawing` — чертёж по той же форме (рисуется, только пока открыта
 *  его вкладка). `titled={false}` — без заголовка «Схема связей» (его несёт блок ЭТК). Разъёмов нет — ничего не рисует. */
export function LinkSchemeView({ scheme, drawing, titled = true, onNet, onColor }: { scheme: LinkScheme; drawing?: () => ReactNode; titled?: boolean;
  onNet?: (wireKey: string, net: string) => void; onColor?: ColorOpen }) {
  const [view, setView] = useState<SchemeView>("scheme");
  const room = useRoom();
  if (!scheme.connectors.length) return null;
  const L = schemeLayout(scheme, room.width, !!onNet);
  const shown = drawing && view === "drawing" ? "drawing" : "scheme";
  return <div className="pin-links" role="group" aria-label="Схема связей">
    <div className="pin-links-head">
      {drawing ? <div className="seg-tabs pin-links-tabs" role="tablist" aria-label="Вид соединений">
        {([["scheme", "Схема связей"], ["drawing", "Чертёж"]] as const).map(([id, title]) =>
          <button type="button" role="tab" key={id} aria-selected={shown === id} className={shown === id ? "on" : undefined} onClick={() => setView(id)}>{title}</button>)}
      </div> : titled ? <span className="pin-links-title">Схема связей</span> : null}
      <span className="muted">{schemeCounts(L)}</span>
    </div>
    {shown === "drawing" ? drawing!() : <div className="pin-links-scroll" ref={room.ref}>
      <LinkSchemeSvg scheme={scheme} layout={L} onNet={onNet} onColor={onColor} />
      {!L.wires.length && <small className="muted pin-links-empty">Линии появятся, когда провод встанет на контакт.</small>}
    </div>}
  </div>;
}

/** Схема связей генератора под торцами разъёмов: форма → модель (`pinoutScheme`) → рисунок. */
/** Ячейка «Цвет» в генераторе: ключ жилы и где открыть палитру (координаты экрана). */
export type ColorOpen = (wireKey: string, at: { left: number; top: number; above: number }) => void;

export function PinoutLinks({ ends, units, housings, joins, info = {}, drawing, onNet, onColor }: {
  ends: PinoutEnd[];
  units: PinUnit[];
  housings: Record<string, ConnectorHousing | null>;
  joins: Pick<GenState, "splices" | "tails">;
  info?: SchemeInfo;
  drawing?: () => ReactNode;
  /** Цепь провода из ячейки таблицы: ключ провода (`PinUnit.key`), имя ("" — снять). */
  onNet?: (wireKey: string, net: string) => void;
  onColor?: ColorOpen;
}) {
  return <LinkSchemeView scheme={pinoutScheme(ends, units, housings, joins, info)} drawing={drawing} onNet={onNet} onColor={onColor} />;
}
