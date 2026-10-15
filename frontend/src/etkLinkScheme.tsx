import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { wireColorKnown } from "./connectorArt";
import { coreColor, SHIELD_COLOR } from "./pinoutCheck";
import { setCustomColors } from "./wireColors";
import { drawnWire, LinkSchemeSvg, schemeLayout, type LinkScheme, type SchemeAnchor, type SchemeConnector, type SchemeSide, type SchemeWire } from "./PinoutLinks";
import { pinCountFromArt } from "./terminalPick";
import { parseCorePins_ } from "./tcGenLogic";
import { planTwist } from "./twistGroups";

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

/** Схема связей документа ЭТК; данных нет (старая ЭТК, ручная сборка, КОАКС) — null. */
export function etkLinkScheme(document: unknown): LinkScheme | null {
  const doc = obj(document), config = obj(doc.build_config);
  setCustomColors(config.colorHex as Record<string, string> | undefined);
  try {
    const scheme = fromPinout(doc, config) ?? fromConnections(doc, config);
    return scheme && scheme.connectors.length ? scheme : null;
  } catch {
    return null;
  }
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
export function etkLinkSchemeMarkup(scheme: LinkScheme): string {
  if (typeof document === "undefined") return "";
  const host = document.createElement("div");
  const root = createRoot(host);
  try {
    flushSync(() => root.render(<LinkSchemeSvg scheme={scheme} layout={schemeLayout(scheme, PRINT_ROOM)} />));
    const svg = host.innerHTML;
    return svg ? `<div class="etk-print-scheme"><style>${PRINT_CSS}</style>${svg}</div>` : "";
  } finally {
    root.unmount();
  }
}
