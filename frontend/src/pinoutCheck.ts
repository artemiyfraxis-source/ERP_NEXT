/*
 * Проверка распиновки в генераторе ТК (владелец 10.10: «распиновка спрятана и я чуть не забыл про неё … около
 * обязательной с предупреждением»). У каждого разъёма с проводами — флаг «распиновка проверена»: подпись того, что
 * проверено (артикул разъёма, провода, контакты и пары с другой стороной). Сменились контакты, провода или разъём —
 * подпись другая, флаг снимается сам. Флаг и контакты уходят в build_config (`pinout`) и возвращаются в форму при
 * следующем открытии.
 */
import { wireColorKnown } from "./connectorArt";
import { makeGen, parsePinList_, sideRu_, type Connection, type Endpoint, type Gen, type GenData, type GenState, type Side } from "./tcGenLogic";

export type PinoutWire = { idx: number; name: string; qty: number; text: string; pins: number[]; color: string | null };
export type PinoutEnd = {
  id: string; key: string; side: Side; conn: string; double: boolean;
  wires: PinoutWire[];
  /** Пары контактов, где участвует этот разъём (from — разъём раньше по порядку А→В). */
  links: Connection[];
  /** Провода без контактов. */
  missing: string[];
  /** Контакты, занятые дважды (кроме двойного/группового обжима). */
  dups: number[];
  sig: string;
  checked: boolean;
  /** «4 провода, перекрёст 3→2, 5→3, 7→4». */
  summary: string;
};

export function plural(n: number, one: string, few: string, many: string) {
  const d = n % 10, dd = n % 100;
  return d === 1 && dd !== 11 ? one : d >= 2 && d <= 4 && (dd < 12 || dd > 14) ? few : many;
}

const endKey = (gen: Gen, ep: Endpoint) => sideRu_(ep.side) + gen.endpointOrdinal_(ep);
const compName = (gen: Gen, id: string) => { const c = gen.getCompById(id); return c ? c.art || c.name || "" : ""; };

function summaryOf(end: Omit<PinoutEnd, "summary" | "checked" | "sig">): string {
  const contacts = end.wires.reduce((n, w) => n + w.pins.length, 0);
  if (!contacts) return "контакты не заданы";
  const parts = [`${contacts} ${plural(contacts, "провод", "провода", "проводов")}`];
  if (end.links.length) {
    const cross = end.links.filter((l) => l.from.pin !== l.to.pin).map((l) => `${l.from.pin}→${l.to.pin}`);
    parts.push(cross.length ? `перекрёст ${cross.slice(0, 8).join(", ")}${cross.length > 8 ? ` … ещё ${cross.length - 8}` : ""}` : "прямой порядок");
  } else {
    const pins = end.wires.flatMap((w) => w.pins);
    parts.push(`контакты ${pins.slice(0, 10).join(", ")}${pins.length > 10 ? " …" : ""}`);
  }
  if (end.missing.length) parts.push(`без контакта: ${end.missing.length}`);
  if (end.dups.length) parts.push(`${plural(end.dups.length, "контакт", "контакты", "контакты")} ${end.dups.join(", ")} дважды`);
  return parts.join(", ");
}

/** Разъёмы формы, у которых есть провода — им нужна проверка распиновки (типовые вкладки, не КОАКС и не ручная сборка). */
export function pinoutEnds(gen: Gen, S: GenState): PinoutEnd[] {
  if (S.tab === "coax" || S.tab === "manual") return [];
  const links = gen.connections_().list;
  const active = gen.activeWires_();
  return (["A", "B"] as Side[]).flatMap((side) => gen.endpointsOnSide_(side)).flatMap((ep) => {
    const routed = new Set(gen.routedWireIdxs_(ep));
    const wires = active.filter((w) => routed.has(w.idx)).map((w): PinoutWire => {
      const name = compName(gen, w.compId);
      const text = ep.pins?.[w.idx] || "";
      return { idx: w.idx, name, qty: w.qty || 1, text, pins: parsePinList_(text), color: wireColorKnown(name) };
    });
    if (!wires.length) return [];
    const key = endKey(gen, ep);
    const own = links.filter((l) => l.from.end === key || l.to.end === key);
    const count: Record<number, number> = {};
    wires.forEach((w) => w.pins.forEach((p) => { count[p] = (count[p] || 0) + 1; }));
    const dups = ep.double ? [] : Object.keys(count).map(Number).filter((p) => count[p] > 1).sort((a, b) => a - b);
    const conn = compName(gen, ep.connId);
    const base = { id: ep.id, key, side: ep.side, conn, double: !!ep.double, wires, links: own, missing: wires.filter((w) => !w.pins.length).map((w) => w.name), dups };
    const sig = JSON.stringify([conn, wires.map((w) => [w.name, w.qty, w.pins.join(",")]),
      own.map((l) => `${l.wire}:${l.from.end}.${l.from.pin}>${l.to.end}.${l.to.pin}`)]);
    return [{ ...base, sig, checked: ep.pinChecked === sig, summary: summaryOf(base) }];
  });
}

/** «Подтвердить распиновку»: флаг — подпись того, что сейчас на экране. */
export function confirmPinout(D: GenData, S: GenState, epId: string): GenState {
  const end = pinoutEnds(makeGen(D, S), S).find((e) => e.id === epId);
  if (!end) return S;
  return { ...S, endpoints: S.endpoints.map((ep) => (ep.id === epId ? { ...ep, pinChecked: end.sig } : ep)) };
}

export type PinoutRecord = { ends: Array<{ end: string; conn: string; checked: boolean; sig: string; wires: Array<{ pos: number; art: string; pins: string }> }> };

/** build_config.pinout: контакты по разъёмам и флаг проверки — чтобы пережить перезагрузку формы. Ни контактов, ни
 *  проверки — ключа нет (конфиг как раньше, как у пакетной сборки tcGenBatch). */
export function pinoutRecord(gen: Gen, S: GenState): PinoutRecord | null {
  const ends = pinoutEnds(gen, S);
  if (!ends.some((end) => end.checked || end.wires.some((w) => w.text))) return null;
  const pos = new Map(gen.activeWires_().map((w, i) => [w.idx, i]));
  return { ends: ends.map((end) => ({
    end: end.key, conn: end.conn, checked: end.checked, sig: end.sig,
    wires: end.wires.filter((w) => w.text).map((w) => ({ pos: pos.get(w.idx) ?? -1, art: w.name, pins: w.text })),
  })) };
}

/** Распиновка и флаги из сохранённого build_config.pinout — в свежую форму, если разъём и провода те же. */
export function restorePinout(D: GenData, S: GenState, config: unknown): GenState {
  const saved = (config as { pinout?: PinoutRecord } | null | undefined)?.pinout;
  if (!saved || !Array.isArray(saved.ends) || S.tab === "coax" || S.tab === "manual") return S;
  const gen = makeGen(D, S);
  const active = gen.activeWires_();
  const byKey = new Map(S.endpoints.map((ep) => [endKey(gen, ep), ep]));
  let next: GenState = S;
  const matched: Array<[string, string]> = [];
  saved.ends.forEach((item) => {
    const ep = item && byKey.get(item.end);
    if (!ep || compName(gen, ep.connId) !== item.conn) return;
    const pins: Record<number, string> = { ...(ep.pins || {}) };
    (Array.isArray(item.wires) ? item.wires : []).forEach((w) => {
      const wire = active[w.pos];
      if (wire && compName(gen, wire.compId) === w.art && typeof w.pins === "string") pins[wire.idx] = w.pins;
    });
    next = { ...next, endpoints: next.endpoints.map((e) => (e.id === ep.id ? { ...e, pins } : e)) };
    if (item.checked) matched.push([ep.id, item.sig]);
  });
  const ends = pinoutEnds(makeGen(D, next), next);
  return { ...next, endpoints: next.endpoints.map((ep) => {
    const sig = matched.find(([id]) => id === ep.id)?.[1];
    return sig && ends.find((e) => e.id === ep.id)?.sig === sig ? { ...ep, pinChecked: sig } : ep;
  }) };
}
