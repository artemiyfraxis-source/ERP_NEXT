/*
 * Проверка распиновки в генераторе ТК (владелец 10.10: «распиновка спрятана и я чуть не забыл про неё … около
 * обязательной с предупреждением»). У каждого разъёма с проводами — флаг «распиновка проверена»: подпись того, что
 * проверено (артикул разъёма, провода, контакты и пары с другой стороной). Сменились контакты, провода или разъём —
 * подпись другая, флаг снимается сам. Флаг и контакты уходят в build_config (`pinout`) и возвращаются в форму при
 * следующем открытии.
 */
import { wireColorKnown } from "./connectorArt";
import { makeGen, parsePinList_, sideRu_, updateWire, type Connection, type Endpoint, type Gen, type GenData, type GenState, type Side } from "./tcGenLogic";

export type PinoutWire = { idx: number; name: string; qty: number; text: string; pins: number[]; color: string | null };
export type PinoutEnd = {
  id: string; key: string; side: Side; conn: string; double: boolean;
  /** Проводов в один контакт: 1, при двойном/групповом обжиме — 2 и больше. */
  per: number;
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
    const base = { id: ep.id, key, side: ep.side, conn, double: !!ep.double, per: ep.double ? gen.epPer_(ep) : 1, wires, links: own, missing: wires.filter((w) => !w.pins.length).map((w) => w.name), dups };
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

// ── Редактор распиновки (владелец 12.10): провод в руку → в контакт, автозаполнение, перестановки, цвет ──────────────
/*
 * Контакты хранятся как раньше — строкой на провод у разъёма (`ep.pins[idx]`, «1» или «1, 3» у провода ×N), ЭТК их
 * читает без изменений. Провод ×N — N проводов: k-й из них — k-я позиция в строке. Функции ниже правят эти строки.
 */

/** Один провод для палитры: номер в списке кабелей («2», у провода ×N — «2.1», «2.2»), цвет и контакт на каждом разъёме. */
export type PinUnit = {
  key: string; idx: number; unit: number; num: string; compId: string; name: string; color: string | null;
  /** Длина по чертежу, мм (0 — не задана). */
  length: number;
  /** Разъём (id) → контакт этого провода; null — провод заведён в разъём, контакт не задан. */
  at: Record<string, number | null>;
};

export function pinUnits(gen: Gen, S: GenState): PinUnit[] {
  const ends = pinoutEnds(gen, S);
  return gen.activeWires_().flatMap((w, pos) => {
    const qty = Math.max(1, Math.round(Number(w.qty) || 1));
    const name = compName(gen, w.compId);
    const length = S.advancedLength ? Number(w.length) || 0 : Number(S.drawingLength) || 0;
    return Array.from({ length: qty }, (_, k) => {
      const at: Record<string, number | null> = {};
      ends.forEach((end) => {
        const wire = end.wires.find((x) => x.idx === w.idx);
        if (wire) at[end.id] = wire.pins[k] ?? null;
      });
      return { key: `${w.idx}.${k}`, idx: w.idx, unit: k, num: qty > 1 ? `${pos + 1}.${k + 1}` : `${pos + 1}`, compId: w.compId, name, color: wireColorKnown(name), length, at };
    });
  });
}

type Lists = Record<number, number[]>;

function editPins(D: GenData, S: GenState, epId: string, edit: (lists: Lists, ep: Endpoint, gen: Gen) => boolean): GenState {
  const gen = makeGen(D, S);
  const ep = S.endpoints.find((e) => e.id === epId);
  if (!ep) return S;
  const routed = gen.routedWireIdxs_(ep);
  const lists: Lists = Object.fromEntries(routed.map((idx) => [idx, parsePinList_(ep.pins?.[idx])]));
  if (!edit(lists, ep, gen)) return S;
  const pins: Record<number, string> = {};
  Object.entries(lists).forEach(([idx, list]) => { if (list.length) pins[Number(idx)] = list.join(", "); });
  return { ...S, endpoints: S.endpoints.map((e) => (e.id === epId ? { ...e, pins } : e)) };
}

/** Провод `idx` (k-й из ×N) — в контакт `pin` разъёма. Контакт занят: занявший провод уходит на прежний контакт
 *  этого провода (поменялись местами), а если прежнего не было — остаётся без контакта. При двойном/групповом
 *  обжиме в контакт помещается `per` проводов — до заполнения никто не вытесняется. */
export function assignPin(D: GenData, S: GenState, epId: string, idx: number, unit: number, pin: number): GenState {
  if (!(pin > 0)) return S;
  return editPins(D, S, epId, (lists, ep, gen) => {
    const own = lists[idx];
    if (!own) return false;
    const k = Math.min(unit, own.length);
    const old = own[k];
    if (old === pin) return false;
    const others: Array<[number, number]> = [];
    Object.entries(lists).forEach(([i, list]) => list.forEach((p, j) => {
      if (p === pin && !(Number(i) === idx && j === k)) others.push([Number(i), j]);
    }));
    const per = ep.double ? gen.epPer_(ep) : 1;
    if (k < own.length) own[k] = pin; else own.push(pin);
    if (others.length >= per) {
      const [i, j] = others[0];
      if (old !== undefined) lists[i][j] = old; else lists[i].splice(j, 1);
    }
    return true;
  });
}

/** Снять провод с контакта: k-я позиция строки уходит, следующие провода ×N сдвигаются. */
export function unassignPin(D: GenData, S: GenState, epId: string, idx: number, unit: number): GenState {
  return editPins(D, S, epId, (lists) => {
    const own = lists[idx];
    if (!own || unit >= own.length) return false;
    own.splice(unit, 1);
    return true;
  });
}

/** «Поменять местами» два контакта разъёма: все провода контакта a — в b, и наоборот (свободный b — перенос). */
export function swapPins(D: GenData, S: GenState, epId: string, a: number, b: number): GenState {
  if (a === b || !(a > 0) || !(b > 0)) return S;
  return editPins(D, S, epId, (lists) => {
    let changed = false;
    Object.values(lists).forEach((list) => list.forEach((p, j) => {
      if (p === a) { list[j] = b; changed = true; } else if (p === b) { list[j] = a; changed = true; }
    }));
    return changed;
  });
}

/** Сменить провод (цвет) кабеля. Такой провод уже есть у другого кабеля — они меняются цветами, контакты остаются. */
export function recolorWire(S: GenState, idx: number, compId: string): GenState {
  const wire = S.wires.find((w) => w.idx === idx);
  if (!wire || !compId || wire.compId === compId) return S;
  const other = S.wires.find((w) => w.idx !== idx && w.compId === compId);
  const next = updateWire(S, idx, { compId });
  return other ? updateWire(next, other.idx, { compId: wire.compId }) : next;
}

export type AutoFill = { state: GenState; notes: string[]; warnings: string[] };

const range = (a: number, b: number) => (a === b ? `${a}` : `${a}–${b}`);
const contacts = (n: number) => `${n} ${plural(n, "контакт", "контакта", "контактов")}`;
const wiresCount = (n: number) => `${n} ${plural(n, "провод", "провода", "проводов")}`;

/** «Заполнить автоматически»: на каждом разъёме его провода по порядку кабелей — в контакты 1…N (провод ×N — подряд,
 *  двойной/групповой обжим — по `per` в контакт). Разъём с одной стороны — контакты по числу проводов, разъёмы с двух
 *  сторон — пин-в-пин (k-й провод: А k → В k). `counts` — число контактов разъёма (id → n, null — неизвестно).
 *  Проводам, которым не хватило контакта, контакт не задаётся — они названы в предупреждении. */
export function autoFillPinout(D: GenData, S: GenState, counts: Record<string, number | null | undefined>): AutoFill {
  const gen = makeGen(D, S);
  const units = pinUnits(gen, S);
  const notes: string[] = [], warnings: string[] = [];
  let state = S;
  pinoutEnds(gen, S).forEach((end) => {
    const mine = units.filter((u) => end.id in u.at);
    const n = counts[end.id] || 0;
    const lists: Lists = {};
    const left: PinUnit[] = [];
    mine.forEach((u, i) => {
      const pin = Math.floor(i / end.per) + 1;
      if (n && pin > n) { left.push(u); return; }
      (lists[u.idx] = lists[u.idx] || []).push(pin);
    });
    const used = Math.ceil((mine.length - left.length) / end.per);
    const title = `${end.key}${end.conn ? ` (${end.conn})` : ""}`;
    if (left.length) warnings.push(`${title}: ${contacts(n)}, а ${wiresCount(mine.length)} — без контакта: ${left.map((u) => `${u.num} · ${u.name}`).join(", ")}.`);
    else if (!n) notes.push(`${title}: число контактов разъёма неизвестно — ${wiresCount(mine.length)} по порядку на ${range(1, Math.max(1, used))}.`);
    else if (used < n) notes.push(`${title}: ${wiresCount(mine.length)} на ${contacts(n)}, свободны ${range(used + 1, n)}.`);
    const pins: Record<number, string> = {};
    Object.entries(lists).forEach(([idx, list]) => { pins[Number(idx)] = list.join(", "); });
    state = { ...state, endpoints: state.endpoints.map((e) => (e.id === end.id ? { ...e, pins } : e)) };
  });
  return { state, notes, warnings };
}

/** Распиновка не помещается в разъём: проводов больше, чем контактов, или контакт с номером больше, чем есть. */
export function pinFitWarnings(ends: PinoutEnd[], counts: Record<string, number | null | undefined>): string[] {
  return ends.flatMap((end) => {
    const n = counts[end.id] || 0;
    if (!n) return [];
    const title = `${end.key}${end.conn ? ` (${end.conn})` : ""}`;
    const out: string[] = [];
    const total = end.wires.reduce((s, w) => s + Math.max(1, Math.round(w.qty || 1)), 0);
    if (Math.ceil(total / end.per) > n) out.push(`${title}: ${contacts(n)}, а ${wiresCount(total)} — всем контакта не хватит.`);
    const beyond = [...new Set(end.wires.flatMap((w) => w.pins).filter((p) => p > n))].sort((a, b) => a - b);
    if (beyond.length) out.push(`${title}: ${plural(beyond.length, "контакта", "контактов", "контактов")} ${beyond.join(", ")} нет — у разъёма ${contacts(n)}.`);
    return out;
  });
}

// ── Свободные концы (разъём только с одной стороны) ───────────────────────────────────────────────────────────────
export type FreeEndKind = "tin" | "strip" | "none";
export type FreeEnd = { idx: number; kind: FreeEndKind; text: string };
export type FreeSide = { side: Side; ends: FreeEnd[] };

/** Сторона без разъёмов и что генератор делает с концами проводов на ней — по тем же правилам, что и операции
 *  (длина зачистки стороны В в getActiveOps): лужение свободных концов у выбранных проводов (длина — «Зачистка под
 *  лужение», иначе длина свободной зачистки), иначе «Зачистка свободных концов», иначе конец без обработки.
 *  Лужение и свободная зачистка у генератора есть только для стороны В. */
export function freeSide(gen: Gen, S: GenState): FreeSide | null {
  if (S.tab === "coax" || S.tab === "manual") return null;
  const hasA = gen.endpointsOnSide_("A").length > 0, hasB = gen.endpointsOnSide_("B").length > 0;
  if (hasA === hasB) return null;
  const side: Side = hasA ? "B" : "A";
  const tinned = new Set(side === "B" && S.tin.enabled ? gen.tinWireIndices_() : []);
  const freeLen = S.freeStrip.enabled ? (S.freeStrip.len || "").trim() : "";
  const mm = (len: string) => (len ? ` L=${len} мм` : " (длина не задана)");
  return { side, ends: gen.activeWires_().map((w, pos): FreeEnd => {
    if (tinned.has(pos)) return { idx: w.idx, kind: "tin", text: `лужение${mm((S.tin.stripLen || "").trim() || freeLen)}` };
    if (side === "B" && S.freeStrip.enabled) return { idx: w.idx, kind: "strip", text: `зачистка${mm(freeLen)}` };
    return { idx: w.idx, kind: "none", text: "без обработки" };
  }) };
}
