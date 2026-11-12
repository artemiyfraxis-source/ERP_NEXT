import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { wireColorKnown } from "./connectorArt";
import { coreColor, SHIELD_COLOR } from "./pinoutCheck";
import { setCustomColors } from "./wireColors";
import { drawnWire, LinkSchemeSvg, LinkSchemeView, schemeLayout, type LinkScheme, type SchemeAnchor, type SchemeConnector, type SchemeSide, type SchemeWire } from "./PinoutLinks";
import { pinCountFromArt } from "./terminalPick";
import { parseCorePins_ } from "./tcGenLogic";
import { planTwist } from "./twistGroups";
import { etkPhysicalConfig, etkPhysicalEndLabel, etkPhysicalSegmentLabel, etkTubePlacementRows, etkTubeTargetLabel } from "./etkDraw";

/*
 * Схема связей ЭТК (владелец 14.10: «не подтянулась новая версия» — в выданной ЭТК та же схема Э4, что в генераторе):
 * модель `LinkScheme` из документа ЭТК. Источник — то, что генератор положил в `build_config`: распиновка `pinout`
 * (контакты жил по разъёмам, цвета, цепи, сростки, хвосты, ответвления), кабели `wires` (марка, жилы, цвета, длина),
 * свивка `twist`, терминалы из снимков разъёмов операций `ops`. Распиновки нет (ЭТК до 12.10) — по таблице соединений
 * `connections` (провод, откуда, куда). Нет и её — null: блок остаётся прежним (схема жгута и таблица).
 */

type Obj = Record<string, unknown>;
const obj = (value: unknown): Obj => (value && typeof value === "object" && !Array.isArray(value) ? value as Obj : {});
const list = (value: unknown): Obj[] => (Array.isArray(value) ? value.filter((item) => item && typeof item === "object" && !Array.isArray(item)) as Obj[] : []);
const text = (value: unknown) => (value == null ? "" : String(value).trim());
const int = (value: unknown) => { const n = Math.round(Number(value)); return Number.isFinite(n) ? n : 0; };

export type EtkLinkScheme = LinkScheme & { physical?: { connections: Obj[]; tubes: Obj[]; config: Obj } };

/** Сторона по ключу разъёма «А1» / «В2» (и латиницей). */
function sideOf(key: string): SchemeSide | null {
  const head = key.charAt(0).toUpperCase();
  return head === "А" || head === "A" ? "A" : head === "В" || head === "B" ? "B" : null;
}
const ordinal = (key: string) => int(key.slice(1)) || 0;

/** Корпус разъёма из топологии ЭТК (узел — артикул разъёма и сторона) или из операции монтажа. */
function housingOf(document: Obj, conn: string, side: SchemeSide): { n: number; title: string } {
  const nodes = list(obj(document.topology).nodes);
  const node = nodes.find((item) => text(item.conn) === conn && (!text(item.side) || text(item.side) === side) && obj(item.housing).n)
    ?? list(document.operations).find((item) => text(item.connector_article) === conn && obj(item.housing).n);
  const housing = obj(node?.housing);
  return { n: int(housing.n), title: text(housing.title) };
}

type EndInfo = { label: string; terminal: string };
/** Подпись и терминал разъёма по снимкам операций генератора (`ep`, ключ `epKey`; без ключа — единственный разъём стороны). */
function endInfo(config: Obj, document: Obj, keys: string[]): Map<string, EndInfo> {
  const out = new Map<string, EndInfo>();
  const single = (side: SchemeSide) => { const own = keys.filter((key) => sideOf(key) === side); return own.length === 1 ? own[0] : ""; };
  list(config.ops).forEach((op) => {
    const ep = obj(op.ep);
    if (!Object.keys(ep).length) return;
    const side = text(op.side) === "B" ? "B" : text(op.side) === "A" ? "A" : null;
    const key = text(op.epKey) || (side ? single(side) : "");
    if (!key) return;
    const was = out.get(key) ?? { label: "", terminal: "" };
    const terminal = was.terminal || (text(op.type) === "solderConn" ? "пайка" : text(ep.termArt));
    out.set(key, { label: was.label || text(ep.label), terminal });
  });
  list(document.connections ?? config.connections).forEach((link) => [obj(link.from), obj(link.to)].forEach((end) => {
    const key = text(end.end), label = text(end.label);
    if (key && label && !out.get(key)?.label) out.set(key, { terminal: out.get(key)?.terminal ?? "", label });
  }));
  return out;
}

/** Разъёмы схемы по ключам: сторона А, затем В, по порядку; обозначение — по чертежу в «Сложной сборке», иначе XS. */
function connectorsOf(document: Obj, config: Obj, ends: Array<{ key: string; conn: string }>): SchemeConnector[] {
  const sorted = ends.filter((end) => sideOf(end.key)).sort((a, b) => (sideOf(a.key) === sideOf(b.key) ? ordinal(a.key) - ordinal(b.key) : sideOf(a.key) === "A" ? -1 : 1));
  const info = endInfo(config, document, sorted.map((end) => end.key));
  const complex = text(config.tab) === "complex";
  const countA = Math.max(0, ...sorted.filter((end) => sideOf(end.key) === "A").map((end) => ordinal(end.key)));
  return sorted.map((end) => {
    const side = sideOf(end.key)!;
    const housing = housingOf(document, end.conn, side);
    const xs = `XS${(side === "A" ? 0 : countA) + ordinal(end.key)}`;
    const own = info.get(end.key);
    return { id: end.key, key: end.key, side, conn: end.conn, pins: housing.n || pinCountFromArt(end.conn) || 0, series: housing.title,
      designation: (complex ? own?.label ?? "" : "") || xs, terminal: own?.terminal ?? "" };
  });
}

/** Концы провода на сторонах по контактам; без контакта — сросток, хвост или «не задан». */
function anchorsOf(connectors: SchemeConnector[], at: Record<string, number | null>, side: SchemeSide, joined: () => SchemeAnchor | null): SchemeAnchor[] {
  const pins = connectors.filter((c) => c.side === side && at[c.id] != null).map((c): SchemeAnchor => ({ kind: "pin", epId: c.id, pin: at[c.id]! }));
  return pins.length ? pins : [joined() ?? { kind: "open" }];
}

/** По распиновке генератора (`build_config.pinout`). */
function fromPinout(document: Obj, config: Obj): LinkScheme | null {
  const pinout = obj(config.pinout);
  const ends = list(pinout.ends).map((end) => ({ key: text(end.end), conn: text(end.conn), wires: list(end.wires) })).filter((end) => end.key);
  const cables = list(config.wires);
  if (!ends.length || !cables.length) return null;
  const connectors = connectorsOf(document, config, ends);
  // Контакты жил: кабель (позиция) → разъём → [контакт жилы 0, 1, …].
  const pinsOf = new Map<number, Record<string, Array<number | null>>>();
  ends.forEach((end) => end.wires.forEach((wire) => {
    const pos = int(wire.pos);
    if (pos < 0) return;
    (pinsOf.get(pos) ?? pinsOf.set(pos, {}).get(pos)!)[end.key] = parseCorePins_(wire.pins);
  }));
  const pick = (items: unknown, pos: number, unit: number, field: string) => text(list(items).find((item) => int(item.pos) === pos && int(item.unit) === unit)?.[field]);
  const endKey = (pos: number, unit: number, side: SchemeSide) => `${pos}.${unit}.${side}`;
  const splices: unknown[] = Array.isArray(pinout.splices) ? pinout.splices : [];
  const spliceAt = new Map<string, string>();
  splices.forEach((ends, i) => list(ends).forEach((end) => spliceAt.set(endKey(int(end.pos), int(end.unit), text(end.side) === "B" ? "B" : "A"), `s${i + 1}`)));
  const tails = new Set(list(pinout.tails).map((end) => endKey(int(end.pos), int(end.unit), text(end.side) === "B" ? "B" : "A")));
  const coresOf: string[][] = [];
  const wires: SchemeWire[] = cables.flatMap((cable, pos) => {
    const name = text(cable.art) || text(cable.name);
    const units = Math.max(1, int(cable.qty) || 1) * Math.max(1, int(cable.cores) || 1);
    const pins = pinsOf.get(pos) ?? {};
    const shields = Math.max(0, ...Object.values(pins).map((cores) => cores.length - units));
    const length = Number(cable.length) || 0;
    const colors = Array.isArray(cable.coreColors) ? cable.coreColors.map(text) : [];
    const unitOf = (k: number, shield: boolean): SchemeWire => {
      const at: Record<string, number | null> = {};
      Object.entries(pins).forEach(([key, cores]) => { if (cores[k] != null) at[key] = cores[k]; });
      const joined = (side: SchemeSide) => () => {
        const splice = spliceAt.get(endKey(pos, k, side));
        return splice ? { kind: "splice" as const, id: splice } : tails.has(endKey(pos, k, side)) ? { kind: "tail" as const } : null;
      };
      const own = shield ? null : coreColor(colors[k] || pick(pinout.cores, pos, k, "color") || undefined);
      const num = shield ? `${pos + 1}.Э${shields > 1 ? k - units + 1 : ""}` : units > 1 ? `${pos + 1}.${k + 1}` : `${pos + 1}`;
      return { key: `${pos}.${k}`, num, name: shield ? `экран ${name}`.trim() : name, color: shield ? SHIELD_COLOR : own ? own.hex : wireColorKnown(name),
        stripe: own?.stripe ?? null, length, shield, net: pick(pinout.nets, pos, k, "net"), at,
        a: anchorsOf(connectors, at, "A", joined("A")), b: anchorsOf(connectors, at, "B", joined("B")) };
    };
    coresOf[pos] = Array.from({ length: units }, (_, k) => `${pos}.${k}`);
    return [...Array.from({ length: units }, (_, k) => unitOf(k, false)), ...Array.from({ length: shields }, (_, c) => unitOf(units + c, true))];
  });
  // Ответвление: у провода-ответвления на стороне источника своего контакта нет — линия от контакта основного провода.
  list(pinout.branches).forEach((branch) => {
    const from = text(branch.from), to = text(branch.to), side = sideOf(from);
    if (!side) return;
    const extra = wires.find((w) => !w.key.startsWith(`${int(branch.pos)}.`) && w.at[to] === int(branch.toPin) && !w.shield);
    const own = extra && (side === "A" ? extra.a : extra.b);
    if (extra && own && !own.some((x) => x.kind === "pin" || x.kind === "splice")) {
      const anchor: SchemeAnchor[] = [{ kind: "pin", epId: from, pin: int(branch.pin) }];
      if (side === "A") extra.a = anchor; else extra.b = anchor;
    }
  });
  const twist = obj(config.twist);
  const plan = twist.enabled ? planTwist(text(twist.mode) === "pairs" ? "pairs" : "all",
    text(twist.mode) === "pairs" ? (Array.isArray(twist.pairs) ? twist.pairs as number[][] : []) : [Array.isArray(twist.wireIndices) ? twist.wireIndices as number[] : []],
    (pos) => coresOf[pos] ?? []) : { pairs: [], groups: [] };
  const scheme = { connectors, wires, splices: splices.map((_, i) => ({ id: `s${i + 1}`, label: `С${i + 1}` })), twists: [...plan.pairs, ...plan.groups] };
  return scheme.wires.some(drawnWire) ? scheme : null;
}

/** По таблице соединений (ЭТК без распиновки): провод «откуда» → «куда», жила с контактом на двух разъёмах — одна. */
function fromConnections(document: Obj, config: Obj): LinkScheme | null {
  const links = list(document.connections ?? config.connections).map((link) => ({ wire: text(link.wire), shield: link.shield === true, from: obj(link.from), to: obj(link.to) }))
    .filter((link) => text(link.from.end) && text(link.to.end));
  if (!links.length) return null;
  const conns = new Map<string, string>();
  links.forEach((link) => [link.from, link.to].forEach((end) => { if (!conns.has(text(end.end))) conns.set(text(end.end), text(end.conn)); }));
  const connectors = connectorsOf(document, config, [...conns].map(([key, conn]) => ({ key, conn })));
  const units = new Map<string, { wire: string; shield: boolean; at: Record<string, number | null> }>();
  links.forEach((link) => {
    const id = `${link.wire}|${text(link.from.end)}|${int(link.from.pin)}`;
    const unit = units.get(id) ?? units.set(id, { wire: link.wire, shield: link.shield, at: { [text(link.from.end)]: int(link.from.pin) } }).get(id)!;
    unit.at[text(link.to.end)] = int(link.to.pin);
  });
  const wires = [...units.values()].map((unit, i): SchemeWire => ({
    key: String(i), num: String(i + 1), name: unit.wire, color: unit.shield ? SHIELD_COLOR : wireColorKnown(unit.wire), stripe: null, length: 0,
    shield: unit.shield, at: unit.at, a: anchorsOf(connectors, unit.at, "A", () => null), b: anchorsOf(connectors, unit.at, "B", () => null),
  }));
  return { connectors, wires, splices: [], twists: [] };
}

/** Physical segments are authoritative: never reconstruct them by walking the legacy pinout. */
function fromPhysical(document: Obj, config: Obj): EtkLinkScheme | null {
  const connections = list(config.physicalConnections);
  if (!connections.length) return null;
  const pins = connections.flatMap((link) => [obj(link.from), obj(link.to)]).filter((end) => end.kind === "pin");
  const ids = [...new Set(pins.map((end) => text(end.epId)))];
  const connectors: SchemeConnector[] = ids.map((id, index) => {
    const ends = pins.filter((end) => text(end.epId) === id), end = ends[0];
    const op = list(config.ops).find((item) => text(item.epId) === id), ep = obj(op?.ep);
    const key = text(op?.epKey) || text(end.label) || `XS${index + 1}`;
    const side = text(op?.side) === "B" || (!op && text(obj(end.end).side) === "B") ? "B" : "A";
    const conn = text(end.conn), housing = housingOf(document, conn, side);
    return { id, key, side, conn, pins: Math.max(housing.n, ...ends.map((end) => int(end.pin))), series: housing.title,
      designation: text(end.label) || key, terminal: text(op?.type) === "solderConn" ? "пайка" : text(ep.termArt) };
  });
  const spliceEnds = connections.flatMap((link) => [obj(link.from), obj(link.to)]).filter((end) => end.kind === "splice");
  const splices = [...new Set(spliceEnds.map((end) => text(end.spliceId)))].map((id) => ({
    id, label: text(spliceEnds.find((end) => text(end.spliceId) === id)?.label) || "Сросток",
  }));
  const anchor = (end: Obj): SchemeAnchor => end.kind === "pin" ? { kind: "pin", epId: text(end.epId), pin: int(end.pin) }
    : end.kind === "splice" ? { kind: "splice", id: text(end.spliceId) } : { kind: "tail" };
  const wires: SchemeWire[] = connections.map((link) => {
    const cable = list(config.wires).find((item) => item.idx === link.idx);
    const colors = Array.isArray(cable?.coreColors) ? cable.coreColors : [];
    const own = coreColor(text(colors[int(link.unit)]) || undefined), from = obj(link.from), to = obj(link.to);
    const at: Record<string, number | null> = {};
    [from, to].filter((end) => end.kind === "pin").forEach((end) => { at[text(end.epId)] = int(end.pin); });
    return { key: `${int(link.idx)}.${int(link.unit)}`, num: etkPhysicalSegmentLabel(link), name: text(link.wire),
      color: link.shield ? SHIELD_COLOR : own?.hex ?? wireColorKnown(text(link.wire)), stripe: own?.stripe ?? null,
      length: Number(cable?.length) || 0, shield: link.shield === true, at, a: [anchor(from)], b: [anchor(to)] };
  });
  return { connectors, wires, splices, twists: [], physical: { connections, tubes: list(config.tubePlacements), config } };
}

/** Схема связей документа ЭТК; данных нет (старая ЭТК, ручная сборка, КОАКС) — null. */
export function etkLinkScheme(document: unknown): EtkLinkScheme | null {
  const doc = obj(document), config = obj(doc.build_config);
  setCustomColors(config.colorHex as Record<string, string> | undefined);
  try {
    if (etkPhysicalConfig(doc)) return fromPhysical(doc, config);
    const scheme = fromPinout(doc, config) ?? fromConnections(doc, config);
    return scheme && scheme.connectors.length ? scheme : null;
  } catch {
    return null;
  }
}

type PhysicalNode = { id: string; end: Obj; column: number; x: number; y: number; rows: string[]; desired: number[] };

function wrapPhysicalLabel(value: string, width: number): string[] {
  const rows: string[] = [];
  const words = value.split(/\s+/).flatMap((word) => word.length > width ? word.match(new RegExp(`.{1,${width}}`, "g")) ?? [] : [word]);
  words.forEach((word) => {
    const last = rows.length - 1;
    if (last >= 0 && rows[last].length + word.length + 1 <= width) rows[last] += ` ${word}`;
    else rows.push(word);
  });
  return rows;
}

function physicalCurve(from: PhysicalNode, to: PhysicalNode, offset = 0) {
  const sameColumn = from.x === to.x, bend = sameColumn ? (from.column === 2 ? -95 : 95) : (to.x - from.x) / 2;
  const c1 = from.x + bend, c2 = sameColumn ? to.x + bend : to.x - bend;
  return {
    d: `M${from.x},${from.y} C${c1},${from.y + offset} ${c2},${to.y + offset} ${to.x},${to.y}`,
    labelX: sameColumn ? from.x + bend * .75 : (from.x + to.x) / 2,
    at: (t: number) => {
      const u = 1 - t;
      const x = u ** 3 * from.x + 3 * u ** 2 * t * c1 + 3 * u * t ** 2 * c2 + t ** 3 * to.x;
      const y = u ** 3 * from.y + 3 * u ** 2 * t * (from.y + offset) + 3 * u * t ** 2 * (to.y + offset) + t ** 3 * to.y;
      const dx = 3 * u ** 2 * (c1 - from.x) + 6 * u * t * (c2 - c1) + 3 * t ** 2 * (to.x - c2);
      const dy = 6 * u * t * (to.y - from.y) + 3 * (u ** 2 - t ** 2) * offset;
      return { x, y, angle: Math.atan2(dy, dx) * 180 / Math.PI };
    },
  };
}

/** Pins are identified by endpoint AND pin; splice nodes are shared across every physical segment. */
function PhysicalSchemeSvg({ scheme }: { scheme: EtkLinkScheme }) {
  const physical = scheme.physical!;
  const { config, connections } = physical;
  const nodes = new Map<string, PhysicalNode>();
  const idOf = (end: Obj) => end.kind === "pin" ? `pin:${text(end.epId)}:${int(end.pin)}`
    : end.kind === "splice" ? `splice:${text(end.spliceId)}`
      : `${text(end.kind)}:${text(obj(end.end).idx)}.${text(obj(end.end).unit)}.${text(obj(end.end).side)}`;
  connections.forEach((link, i) => [obj(link.from), obj(link.to)].forEach((end) => {
    const id = idOf(end), existing = nodes.get(id);
    if (existing) { existing.desired.push(45 + i * 52); return; }
    const connector = scheme.connectors.find((item) => item.id === text(end.epId));
    const side = connector?.side ?? text(obj(end.end).side);
    const column = end.kind === "splice" ? 1 : side === "B" ? 2 : 0;
    const label = `${etkPhysicalEndLabel(end, config)}${end.kind === "pin" ? ` · конт. ${text(end.pin)}` : ""}`;
    nodes.set(id, { id, end, column, x: [185, 408, 631][column], y: 0, rows: wrapPhysicalLabel(label, column === 1 ? 22 : 26), desired: [45 + i * 52] });
  }));
  for (const column of [0, 1, 2]) {
    let bottom = 20;
    [...nodes.values()].filter((node) => node.column === column)
      .sort((a, b) => a.desired[0] - b.desired[0]).forEach((node) => {
        node.y = Math.max(bottom + 25, node.desired.reduce((a, b) => a + b, 0) / node.desired.length);
        bottom = node.y + Math.max(28, node.rows.length * 12);
      });
  }
  const parallel = new Map<string, number>();
  const curves = connections.map((link) => {
    const from = nodes.get(idOf(obj(link.from)))!, to = nodes.get(idOf(obj(link.to)))!;
    const pair = [from.id, to.id].sort().join("|"), lane = parallel.get(pair) ?? 0;
    parallel.set(pair, lane + 1);
    return physicalCurve(from, to, lane * 22);
  });
  const graphHeight = Math.max(100, ...[...nodes.values()].map((node) => node.y + node.rows.length * 12 + 28))
    + Math.max(0, ...parallel.values()) * 22;
  const tubes = etkTubePlacementRows({ build_config: config }).map((row) => wrapPhysicalLabel(
    `${row[0]} · ${row[1]} мм × ${row[2]} · ${row[3]} · ${row[4]}${row[5] ? ` · Надеть через: ${row[5]}` : ""}`, 108));
  const tubeHeight = tubes.reduce((height, rows) => height + rows.length * 14 + 14, 0);
  return <svg xmlns="http://www.w3.org/2000/svg" viewBox={`0 0 816 ${graphHeight + tubeHeight}`} role="img"
    aria-label="Физические соединения: контакты разъёмов, сростки, свободные концы и наконечники" style={{ display: "block", width: "100%", maxWidth: 1024, height: "auto" }}>
    {connections.map((link, i) => {
      const wire = scheme.wires[i];
      const { d, labelX, at } = curves[i], labelY = at(.5).y - 8;
      return <g key={wire.key} data-segment={wire.key} data-from={idOf(obj(link.from))} data-to={idOf(obj(link.to))}>
        <path d={d} stroke="#3b4652" strokeWidth="4.5" fill="none" />
        <path d={d} stroke={wire.color || "#6b7c86"} strokeWidth="2.5" fill="none" strokeDasharray={wire.shield ? "6 3" : undefined} />
        {wire.stripe && <path d={d} stroke={wire.stripe} strokeWidth="2.5" fill="none" strokeDasharray="4 4" />}
        <text x={labelX} y={labelY} textAnchor="middle" className="pin-links-label">{wire.num}{wire.shield ? " · экран" : ""}</text>
        <title>{`${wire.num} · ${wire.name} · ${etkPhysicalEndLabel(obj(link.from), config)} → ${etkPhysicalEndLabel(obj(link.to), config)}`}</title>
      </g>;
    })}
    {physical.tubes.map((tube, index) => {
      const target = obj(tube.target), end = obj(target.end);
      let at: { x: number; y: number; angle: number } | undefined;
      if (target.kind === "splice") {
        const node = nodes.get(`splice:${text(target.spliceId)}`);
        if (node) at = { x: node.x, y: node.y, angle: 0 };
      } else {
        const linkIndex = connections.findIndex((item) => item.idx === end.idx && item.unit === end.unit);
        if (linkIndex >= 0) {
          const t = target.kind === "cup" ? .06 : .2;
          at = curves[linkIndex].at(end.side === "B" ? 1 - t : t);
        }
      }
      return at && <g key={index} data-tube-target={text(target.kind)} data-tube-phase={text(tube.phase)}>
        <rect x={at.x - 14} y={at.y - 7} width="28" height="14" rx="3" transform={`rotate(${at.angle} ${at.x} ${at.y})`}
          fill="#3b4652" fillOpacity=".8" stroke="#17272b" />
        <text x={at.x} y={at.y - 12} textAnchor="middle" className="pin-links-label">Т{index + 1}</text>
        <title>{etkTubeTargetLabel(target, config)}</title>
      </g>;
    })}
    {[...nodes.values()].map((node) => <g key={node.id} data-kind={text(node.end.kind)} data-node={node.id}>
      {node.end.kind === "lug" ? <><circle cx={node.x} cy={node.y} r="7" fill="#fff" stroke="#3b4652" strokeWidth="2" /><circle cx={node.x} cy={node.y} r="3" fill="#fff" stroke="#3b4652" /></>
        : <circle cx={node.x} cy={node.y} r={node.end.kind === "splice" ? 5 : 4} fill={node.end.kind === "free" ? "#fff" : "#3b4652"} stroke="#3b4652" strokeWidth="1.5" />}
      <text x={node.column === 0 ? 12 : node.column === 2 ? 646 : node.x} y={node.column === 1 ? node.y + 19 : node.y - (node.rows.length - 1) * 6}
        textAnchor={node.column === 1 ? "middle" : "start"} className="pin-links-cell" fontSize="11">
        {node.rows.map((row, i) => <tspan key={i} x={node.column === 0 ? 12 : node.column === 2 ? 646 : node.x} dy={i ? 12 : 0}>{row}</tspan>)}
      </text>
    </g>)}
    {(() => {
      let y = graphHeight;
      return tubes.map((rows, i) => {
        const top = y; y += rows.length * 14 + 14;
        return <g key={i} data-tube-placement={text(physical.tubes[i].id)}>
          <rect x="12" y={top - 9} width="20" height="10" rx="3" fill="#3b4652" />
          <text x="40" y={top} fontSize="11" className="pin-links-cell">{rows.map((row, k) => <tspan key={k} x="40" dy={k ? 14 : 0}>{k ? row : `Т${i + 1} · ${row}`}</tspan>)}</text>
        </g>;
      });
    })()}
  </svg>;
}

/** Versioned topology uses the same diagram on screen and in print, including connectorless branches. */
export function EtkLinkSchemeView({ scheme, titled = true }: { scheme: EtkLinkScheme; titled?: boolean }) {
  if (!scheme.physical) return <LinkSchemeView scheme={scheme} titled={titled} />;
  return <div className="pin-links" role="group" aria-label="Схема связей">
    <div className="pin-links-head">{titled && <span className="pin-links-title">Схема связей</span>}
      <span className="muted">{scheme.wires.length} уч. · сростков {scheme.splices.length}</span>
    </div>
    <PhysicalSchemeSvg scheme={scheme} />
  </div>;
}

/** Ширина поля схемы на листе А4 (узкие колонки, без уменьшения шрифта). */
const PRINT_ROOM = 816;
/** Цвета схемы на бумаге — без переменных темы. */
const PRINT_CSS = [
  ".etk-print-scheme svg { display: block; max-width: 100%; height: auto; margin: 0 auto; overflow: visible; }",
  ".etk-print-scheme .pin-links-frame { fill: #fff; stroke: #3b4652; stroke-width: 1.2; }",
  ".etk-print-scheme .pin-links-band { fill: #eef2f4; stroke: #3b4652; stroke-width: 1.2; }",
  ".etk-print-scheme .pin-links-rule { stroke: #c4ccd2; stroke-width: 1; }",
  ".etk-print-scheme .pin-links-rule.is-strong { stroke: #3b4652; }",
  ".etk-print-scheme .pin-links-name { fill: #111; font-size: 12.5px; font-weight: 700; }",
  ".etk-print-scheme .pin-links-key { fill: #00554f; }",
  ".etk-print-scheme .pin-links-colhead { fill: #555; font-size: 10.5px; font-weight: 600; }",
  ".etk-print-scheme .pin-links-num { fill: #111; font-size: 11px; font-weight: 600; }",
  ".etk-print-scheme .pin-links-row:not(.is-used) .pin-links-num { fill: #888; font-weight: 400; }",
  ".etk-print-scheme .pin-links-cell { fill: #111; font-size: 11px; }",
  ".etk-print-scheme .pin-links-foot { fill: #555; font-size: 11px; white-space: pre; }",
  ".etk-print-scheme .pin-links-swatch { stroke: #3b4652; stroke-width: .8; }",
  ".etk-print-scheme .pin-links-label, .etk-print-scheme .pin-links-node { fill: #111; font-size: 10.5px; font-weight: 700; paint-order: stroke; stroke: #fff; stroke-width: 3px; stroke-linejoin: round; }",
  ".etk-print-scheme .pin-links-twist, .etk-print-scheme .pin-links-tail { fill: #555; font-size: 10.5px; paint-order: stroke; stroke: #fff; stroke-width: 3px; stroke-linejoin: round; }",
].join("\n");

/** Схема связей для печати ЭТК: SVG той же схемы, что на экране, со своими цветами для бумаги. */
export function etkLinkSchemeMarkup(scheme: EtkLinkScheme): string {
  if (typeof document === "undefined") return "";
  const host = document.createElement("div");
  const root = createRoot(host);
  try {
    flushSync(() => root.render(scheme.physical ? <PhysicalSchemeSvg scheme={scheme} /> : <LinkSchemeSvg scheme={scheme} layout={schemeLayout(scheme, PRINT_ROOM)} />));
    const svg = host.innerHTML;
    return svg ? `<div class="etk-print-scheme"><style>${PRINT_CSS}</style>${svg}</div>` : "";
  } finally {
    root.unmount();
  }
}
