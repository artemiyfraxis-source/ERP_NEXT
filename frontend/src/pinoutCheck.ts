/*
 * Проверка распиновки в генераторе ТК (владелец 10.10: «распиновка спрятана и я чуть не забыл про неё … около
 * обязательной с предупреждением»). У каждого разъёма с проводами — флаг «распиновка проверена»: подпись того, что
 * проверено (артикул разъёма, провода, контакты и пары с другой стороной). Сменились контакты, провода или разъём —
 * подпись другая, флаг снимается сам. Флаг и контакты уходят в build_config (`pinout`) и возвращаются в форму при
 * следующем открытии.
 */
import { wireColorKnown } from "./connectorArt";
import { PALETTE, canonicalColor } from "./wireColors";
import { formatCorePins_, makeGen, NO_WIRES, parseCorePins_, parsePinList_, sideRu_, updateWire, type Branch, type Connection, type Endpoint, type Gen, type GenData, type GenState, type Side } from "./tcGenLogic";

/** Цвет жилы по названию палитры: одноцветная или двухцветная («бело-синий» — основной белый, полоса синяя). */
export function coreColor(text: string | undefined): { name: string; baseName: string; hex: string; stripe: string | null } | null {
  const name = text ? canonicalColor(text) : null;
  if (!name) return null;
  const single = PALETTE.find((color) => color.name === name);
  if (single) return { name, baseName: single.name, hex: single.hex, stripe: null };
  const [head, tail] = name.split("-");
  const first = PALETTE.find((color) => color.stem === head), second = PALETTE.find((color) => color.name === tail);
  return first && second ? { name, baseName: first.name, hex: first.hex, stripe: second.hex } : null;
}

/** Жил в проводе: кабелей × жил в кабеле — каждая жила отдельно разводится по контактам и красится (владелец 12.10:
 *  «многожильный кабель — жилы отдельно, а сам кабель — оболочка»). Провод без оболочки — одна жила на штуку. */
export const coreCount = (gen: Gen, w: { qty: number | string; cores?: number }) => gen.unitCount_(w as never);

/** Цвет жилы `unit` кабеля `idx` с палитры (одно название или двухцветное); null — вернуть цвет из наименования. */
export function setCoreColor(D: GenData, S: GenState, idx: number, unit: number, color: string | null): GenState {
  const canon = color ? coreColor(color)?.name ?? null : null;
  if (color && !canon) return S;
  const gen = makeGen(D, S);
  return { ...S, wires: S.wires.map((w) => {
    if (w.idx !== idx) return w;
    const qty = coreCount(gen, w);
    const list = Array.from({ length: Math.max(qty, unit + 1) }, (_, k) => w.coreColors?.[k] || "");
    list[unit] = canon ?? "";
    const { coreColors: _old, ...rest } = w;
    return list.some(Boolean) ? { ...rest, coreColors: list.slice(0, qty) } : rest;
  }) };
}

/** Провод у разъёма: `pins` — контакты подряд, `cores` — контакт каждой жилы (null — жила сюда не идёт), `qty` — сколько
 *  жил ждут контакта здесь: у кабеля, жилы которого уже розданы, — розданные; иначе все (кабелей × жил). */
export type PinoutWire = { idx: number; name: string; qty: number; text: string; pins: number[]; cores: Array<number | null>; color: string | null;
  /** Кабель в оболочке (жил больше одной): `qty` — жилы, а не провода. */
  cable: boolean };
export type PinoutEnd = {
  id: string; key: string; side: Side; conn: string; double: boolean;
  /** Одна из нескольких разъёмов стороны: провод идёт сюда, только если у него здесь есть контакт. */
  multi: boolean;
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

/** Сторона с несколькими разъёмами в «Сложной сборке» (владелец 12.10: «галки с проводами — рудимент, буду растыкивать на
 *  распиновке»): провод идёт на разъём такой стороны тогда и только тогда, когда у него там есть контакт. */
export const multiSide = (gen: Gen, S: GenState, side: Side) => S.tab === "complex" && gen.endpointsOnSide_(side).length >= 2;
const hasPin = (ep: Endpoint, idx: number) => parsePinList_(ep.pins?.[idx]).length > 0;
const sidePinned = (gen: Gen, side: Side) => gen.endpointsOnSide_(side).some((e) => gen.activeWires_().some((w) => hasPin(e, w.idx)));
/** Старое сохранение: на стороне с несколькими разъёмами заданы `wireIdxs`, а контактов ещё нет — это начальная раздача. */
const legacyRouting = (gen: Gen, side: Side) => !sidePinned(gen, side) && gen.endpointsOnSide_(side).some((e) => e.wireIdxs?.length);

/** Провода разъёма: у разъёма стороны с несколькими — те, у кого тут контакт (старое сохранение без контактов — по `wireIdxs`). */
export function pinRouted(gen: Gen, S: GenState, ep: Endpoint): number[] {
  if (!multiSide(gen, S, ep.side)) return gen.routedWireIdxs_(ep);
  if (legacyRouting(gen, ep.side)) return gen.routedWireIdxs_(ep);
  return gen.activeWires_().filter((w) => hasPin(ep, w.idx)).map((w) => w.idx);
}

/** Генератор читает `routedWireIdxs_` из `wireIdxs`: на стороне с несколькими разъёмами держим их равными тому, у кого
 *  где контакт. Пока на стороне нет ни одного контакта, раздача не трогается (старое сохранение, свежая форма). */
export function syncRoutes(D: GenData, S: GenState): GenState {
  if (S.tab !== "complex") return S;
  const gen = makeGen(D, S);
  const all = gen.activeWires_().map((w) => w.idx);
  let changed = false;
  const endpoints = S.endpoints.map((ep) => {
    if (!multiSide(gen, S, ep.side) || !sidePinned(gen, ep.side)) return ep;
    const mine = all.filter((i) => hasPin(ep, i));
    const next = mine.length === all.length ? [] : mine.length ? mine : [NO_WIRES];
    if (JSON.stringify(next) === JSON.stringify(ep.wireIdxs || [])) return ep;
    changed = true;
    return { ...ep, wireIdxs: next };
  });
  return changed ? { ...S, endpoints } : S;
}

function summaryOf(end: Omit<PinoutEnd, "summary" | "checked" | "sig">): string {
  const contacts = end.wires.reduce((n, w) => n + w.pins.length, 0);
  if (!contacts) return "контакты не заданы";
  // Жилы кабеля в оболочке — «жил», отдельные провода — «проводов».
  const parts = [`${contacts} ${end.wires.every((w) => w.cable) ? plural(contacts, "жила", "жилы", "жил") : plural(contacts, "провод", "провода", "проводов")}`];
  // Жилы разведены на несколько разъёмов (жилы 1–5 → В1, 6–10 → В2) — сколько куда, а не «перекрёст 6→1».
  const peers = [...new Set(end.links.map((l) => (l.from.end === end.key ? l.to.end : l.from.end)))];
  if (peers.length > 1) {
    parts.push(`по разъёмам: ${peers.map((key) => `${key} — ${end.links.filter((l) => l.from.end === key || l.to.end === key).length}`).join(", ")}`);
  } else if (end.links.length) {
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
    const routed = new Set(pinRouted(gen, S, ep));
    const multi = multiSide(gen, S, ep.side);
    const wires = active.filter((w) => routed.has(w.idx)).map((w): PinoutWire => {
      const name = compName(gen, w.compId);
      const text = ep.pins?.[w.idx] || "";
      const cores = parseCorePins_(text);
      const pinned = cores.filter((p) => p != null).length;
      const cable = gen.wireCores_(w) > 1;
      const qty = pinned && (multi || cable) ? pinned : coreCount(gen, w);
      return { idx: w.idx, name, qty, text, pins: parsePinList_(text), cores, color: wireColorKnown(name), cable };
    });
    if (!wires.length && !multi) return [];
    const key = endKey(gen, ep);
    const own = links.filter((l) => l.from.end === key || l.to.end === key);
    const count: Record<number, number> = {};
    wires.forEach((w) => w.pins.forEach((p) => { count[p] = (count[p] || 0) + 1; }));
    const dups = ep.double ? [] : Object.keys(count).map(Number).filter((p) => count[p] > 1).sort((a, b) => a - b);
    const conn = compName(gen, ep.connId);
    const base = { id: ep.id, key, side: ep.side, conn, multi, double: !!ep.double, per: ep.double ? gen.epPer_(ep) : 1, wires, links: own, missing: wires.filter((w) => !w.pins.length).map((w) => w.name), dups };
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

export type PinoutRecord = {
  ends: Array<{ end: string; conn: string; checked: boolean; sig: string; wires: Array<{ pos: number; art: string; pins: string }> }>;
  /** Ответвления: провод (позиция в списке кабелей), разъёмы и контакты по ключам «А1»/«В2». */
  branches?: Array<{ pos: number; mode: Branch["mode"]; from: string; pin: number; to: string; toPin: number }>;
  /** Цвета жил, выбранные на палитре: кабель (позиция), жила и название цвета. */
  cores?: Array<{ pos: number; unit: number; color: string }>;
};

/** build_config.pinout: контакты по разъёмам и флаг проверки — чтобы пережить перезагрузку формы. Ни контактов, ни
 *  проверки — ключа нет (конфиг как раньше, как у пакетной сборки tcGenBatch). */
export function pinoutRecord(gen: Gen, S: GenState): PinoutRecord | null {
  const ends = pinoutEnds(gen, S);
  const colored = gen.activeWires_().flatMap((w, pos) => (w.coreColors || []).flatMap((color, unit) => (color ? [{ pos, unit, color }] : [])));
  if (!colored.length && !ends.some((end) => end.checked || end.wires.some((w) => w.text))) return null;
  const pos = new Map(gen.activeWires_().map((w, i) => [w.idx, i]));
  const keyOf = new Map(S.endpoints.map((ep) => [ep.id, endKey(gen, ep)]));
  const branches = (S.branches || []).filter((b) => pos.has(b.main) && pos.has(b.extra) && keyOf.has(b.from) && keyOf.has(b.to))
    .map((b) => ({ pos: pos.get(b.main)!, mode: b.mode, from: keyOf.get(b.from)!, pin: b.pin, to: keyOf.get(b.to)!, toPin: b.toPin }));
  return { ...(branches.length ? { branches } : {}), ...(colored.length ? { cores: colored } : {}), ends: ends.map((end) => ({
    end: end.key, conn: end.conn, checked: end.checked, sig: end.sig,
    wires: end.wires.filter((w) => w.text).map((w) => ({ pos: pos.get(w.idx) ?? -1, art: w.name, pins: w.text })),
  })) };
}

/** Распиновка и флаги из сохранённого build_config.pinout — в свежую форму, если разъём и провода те же. */
export function restorePinout(D: GenData, S: GenState, config: unknown): GenState {
  const saved = (config as { pinout?: PinoutRecord } | null | undefined)?.pinout;
  if (!saved || !Array.isArray(saved.ends) || S.tab === "coax" || S.tab === "manual") return S;
  const gen0 = makeGen(D, S);
  const keys = new Map(S.endpoints.map((ep) => [endKey(gen0, ep), ep.id]));
  let next: GenState = S;
  (Array.isArray(saved.branches) ? saved.branches : []).forEach((b) => {
    const main = gen0.activeWires_()[b.pos], from = keys.get(b.from), to = keys.get(b.to);
    if (main && from && to) next = addBranch(D, next, { epId: from, idx: main.idx, pin: b.pin }, to, b.toPin, b.mode);
  });
  (Array.isArray(saved.cores) ? saved.cores : []).forEach((item) => {
    const wire = makeGen(D, next).activeWires_()[item.pos];
    if (wire && typeof item.color === "string") next = setCoreColor(D, next, wire.idx, item.unit, item.color);
  });
  const gen = makeGen(D, next);
  const active = gen.activeWires_();
  const byKey = new Map(next.endpoints.map((ep) => [endKey(gen, ep), ep]));
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
 * Контакты хранятся строкой на провод у разъёма (`ep.pins[idx]`) по жилам: k-я позиция — k-я жила (провод ×N — N
 * проводов, кабель — кабелей × жил), после пропуска — «11:1» (с 11-й жилы), parseCorePins_/formatCorePins_. Строка без
 * пропусков та же, что раньше («1, 3»); в ЭТК уходят контакты подряд. Функции ниже правят эти строки.
 */

/** Один провод для палитры: номер в списке кабелей («2», у провода ×N — «2.1», «2.2»), цвет и контакт на каждом разъёме. */
export type PinUnit = {
  key: string; idx: number; unit: number; num: string; compId: string; name: string; color: string | null;
  /** Вторая краска двухцветной жилы (цвет полосы), иначе null. */
  stripe: string | null;
  /** Цвет, выбранный на жиле, каноническим названием («красный», «бело-синий»); "" — цвет из наименования. */
  colorName: string;
  /** Название основного цвета выбранной жилы — по нему рисуется торец разъёма. */
  baseName: string | null;
  /** Длина по чертежу, мм (0 — не задана). */
  length: number;
  /** Разъём (id) → контакт этого провода; null — провод заведён в разъём, контакт не задан. */
  at: Record<string, number | null>;
};

export function pinUnits(gen: Gen, S: GenState): PinUnit[] {
  const ends = pinoutEnds(gen, S);
  return gen.activeWires_().flatMap((w, pos) => {
    const qty = coreCount(gen, w);
    const name = compName(gen, w.compId);
    const length = S.advancedLength ? Number(w.length) || 0 : Number(S.drawingLength) || 0;
    // Цвет жилы: выбранный на палитре, иначе по «Цвета» записи БД.ПРОВ (по порядку жил), иначе из наименования.
    const colors = gen.coreColors_(w);
    return Array.from({ length: qty }, (_, k) => {
      const at: Record<string, number | null> = {};
      ends.forEach((end) => {
        const wire = end.wires.find((x) => x.idx === w.idx);
        if (!wire) return;
        // На стороне с несколькими разъёмами жила идёт только туда, где у неё контакт (жилы кабеля — по разным разъёмам).
        const pin = wire.cores[k] ?? null;
        if (pin != null || !end.multi) at[end.id] = pin;
      });
      const own = coreColor(colors[k]);
      return { key: `${w.idx}.${k}`, idx: w.idx, unit: k, num: qty > 1 ? `${pos + 1}.${k + 1}` : `${pos + 1}`, compId: w.compId, name,
        color: own ? own.hex : wireColorKnown(name), stripe: own?.stripe ?? null, colorName: own?.name ?? "", baseName: own?.baseName ?? null, length, at };
    });
  });
}

/** Контакты жил по проводам разъёма: idx → [контакт жилы 0, жилы 1, …], null — жила сюда не идёт. */
type Lists = Record<number, Array<number | null>>;

const writePins = (lists: Lists) => {
  const pins: Record<number, string> = {};
  Object.entries(lists).forEach(([idx, list]) => { const text = formatCorePins_(list); if (text) pins[Number(idx)] = text; });
  return pins;
};

function editPins(D: GenData, S: GenState, epId: string, edit: (lists: Lists, ep: Endpoint, gen: Gen) => boolean): GenState {
  const gen = makeGen(D, S);
  const ep = S.endpoints.find((e) => e.id === epId);
  if (!ep) return S;
  const routed = multiSide(gen, S, ep.side) ? gen.activeWires_().map((w) => w.idx) : gen.routedWireIdxs_(ep);
  const lists: Lists = Object.fromEntries(routed.map((idx) => [idx, parseCorePins_(ep.pins?.[idx])]));
  if (!edit(lists, ep, gen)) return S;
  return syncRoutes(D, { ...S, endpoints: S.endpoints.map((e) => (e.id === epId ? { ...e, pins: writePins(lists) } : e)) });
}

/** Жила `unit` провода `idx` — в контакт `pin` разъёма. Контакт занят: занявшая жила уходит на прежний контакт этой
 *  (поменялись местами), а если прежнего не было — остаётся без контакта. При двойном/групповом обжиме в контакт
 *  помещается `per` проводов — до заполнения никто не вытесняется. Остальные жилы кабеля остаются на своих местах. */
export function assignPin(D: GenData, S: GenState, epId: string, idx: number, unit: number, pin: number): GenState {
  if (!(pin > 0) || !(unit >= 0)) return S;
  return editPins(D, S, epId, (lists, ep, gen) => {
    const own = lists[idx];
    if (!own) return false;
    const old = own[unit] ?? null;
    if (old === pin) return false;
    const others: Array<[number, number]> = [];
    Object.entries(lists).forEach(([i, list]) => list.forEach((p, j) => {
      if (p === pin && !(Number(i) === idx && j === unit)) others.push([Number(i), j]);
    }));
    const per = ep.double ? gen.epPer_(ep) : 1;
    while (own.length <= unit) own.push(null);
    own[unit] = pin;
    if (others.length >= per) {
      const [i, j] = others[0];
      lists[i][j] = old;
    }
    return true;
  });
}

/** Снять жилу с контакта: остальные жилы провода остаются на своих контактах. */
export function unassignPin(D: GenData, S: GenState, epId: string, idx: number, unit: number): GenState {
  return editPins(D, S, epId, (lists) => {
    const own = lists[idx];
    if (!own || own[unit] == null) return false;
    own[unit] = null;
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
export function autoFillPinout(D: GenData, S0: GenState, counts: Record<string, number | null | undefined>, fresh = false): AutoFill {
  // «Заполнить автоматически» кнопкой (fresh): сторона с несколькими разъёмами раздаётся заново — иначе разъём, добавленный
  // после первой раздачи, так и остался бы пустым. С ответвлениями раздача не трогается (их контакты заданы вручную).
  const g0 = makeGen(D, S0);
  const reset = (["A", "B"] as Side[]).filter((side) => fresh && !(S0.branches || []).length && multiSide(g0, S0, side));
  const S: GenState = reset.length ? { ...S0, endpoints: S0.endpoints.map((ep) => (reset.includes(ep.side) ? { ...ep, pins: {}, wireIdxs: [] } : ep)) } : S0;
  const gen = makeGen(D, S);
  const all = pinUnits(gen, S);
  const active = gen.activeWires_();
  const notes: string[] = [], warnings: string[] = [];
  const ends = pinoutEnds(gen, S);
  // Сторона с несколькими разъёмами и без контактов: провода раздаются по порядку, пока хватает контактов разъёма.
  // Не знаем число контактов или проводов больше, чем мест — не угадываем: провод остаётся «не назначен».
  // Раздача — по жилам (ключ «idx.k»): провод или кабель целиком — на первый разъём, где хватает мест; кабель, который
  // целиком не помещается ни в один, делится по разъёмам по порядку (жилы 1–10 → В1, 11–19 → В2).
  const assigned = new Map<string, Set<string>>();
  (["A", "B"] as Side[]).forEach((side) => {
    if (!multiSide(gen, S, side) || sidePinned(gen, side) || legacyRouting(gen, side)) return;
    const eps = gen.endpointsOnSide_(side);
    const title = `Сторона ${sideRu_(side)}`;
    eps.forEach((ep) => assigned.set(ep.id, new Set()));
    if (eps.some((ep) => !counts[ep.id])) {
      warnings.push(`${title}: число контактов разъёмов неизвестно — раздайте провода по разъёмам на схеме.`);
      return;
    }
    const free = new Map(eps.map((ep) => [ep.id, (counts[ep.id] || 0) * (ep.double ? gen.epPer_(ep) : 1)]));
    const unitsOf = (idx: number) => all.filter((u) => u.idx === idx);
    let rest = active.map((w) => w.idx);
    eps.forEach((ep) => {
      rest = rest.filter((idx) => {
        const units = unitsOf(idx);
        if (units.length > free.get(ep.id)!) return true;
        free.set(ep.id, free.get(ep.id)! - units.length);
        units.forEach((u) => assigned.get(ep.id)!.add(u.key));
        return false;
      });
    });
    const left: PinUnit[] = [];
    rest.forEach((idx) => unitsOf(idx).forEach((u) => {
      const ep = unitsOf(idx).length > 1 ? eps.find((e) => free.get(e.id)! > 0) : undefined;
      if (!ep) { left.push(u); return; }
      free.set(ep.id, free.get(ep.id)! - 1);
      assigned.get(ep.id)!.add(u.key);
    }));
    const parts = eps.flatMap((ep) => {
      const nums = all.filter((u) => assigned.get(ep.id)!.has(u.key)).map((u) => u.num);
      return nums.length ? [`${endKey(gen, ep)}: ${nums.join(", ")}`] : [];
    });
    if (parts.length) notes.push(`${title}: провода разведены по разъёмам — ${parts.join("; ")}.`);
    if (left.length) warnings.push(`${title}: контактов разъёмов не хватает — не назначены: ${left.map((u) => `${u.num} · ${u.name}`).join(", ")}.`);
  });
  let state = S;
  S.endpoints.forEach((ep) => {
    const end = ends.find((e) => e.id === ep.id);
    const keys = assigned.get(ep.id);
    const idxs = end?.wires.map((w) => w.idx) ?? [];
    const mine = all.filter((u) => (keys ? keys.has(u.key) : idxs.includes(u.idx)));
    if (!mine.length) {
      if (keys) state = { ...state, endpoints: state.endpoints.map((e) => (e.id === ep.id ? { ...e, pins: {} } : e)) };
      return;
    }
    const per = ep.double ? gen.epPer_(ep) : 1;
    const n = counts[ep.id] || 0;
    const lists: Lists = {};
    const left: PinUnit[] = [];
    mine.forEach((u, i) => {
      const pin = Math.floor(i / per) + 1;
      if (n && pin > n) { left.push(u); return; }
      const list = (lists[u.idx] = lists[u.idx] || []);
      while (list.length < u.unit) list.push(null);
      list[u.unit] = pin;
    });
    const used = Math.ceil((mine.length - left.length) / per);
    const conn = compName(gen, ep.connId);
    const title = `${endKey(gen, ep)}${conn ? ` (${conn})` : ""}`;
    if (left.length) warnings.push(`${title}: ${contacts(n)}, а ${wiresCount(mine.length)} — без контакта: ${left.map((u) => `${u.num} · ${u.name}`).join(", ")}.`);
    else if (!n) notes.push(`${title}: число контактов разъёма неизвестно — ${wiresCount(mine.length)} по порядку на ${range(1, Math.max(1, used))}.`);
    else if (used < n) notes.push(`${title}: ${wiresCount(mine.length)} на ${contacts(n)}, свободны ${range(used + 1, n)}.`);
    state = { ...state, endpoints: state.endpoints.map((e) => (e.id === ep.id ? { ...e, pins: writePins(lists) } : e)) };
  });
  return { state: syncRoutes(D, state), notes, warnings };
}

// ── Ответвление: с одного контакта на два вывода ──────────────────────────────────────────────────────────────────
/** Провод `idx`, пришедший на контакт `pin` разъёма `epId`, получает второй вывод — на контакт `toPin` разъёма `toEpId`.
 *  Второй вывод — отдельный провод того же кабеля (его длина — длина чертежа до дальнего конца): он попадает в состав,
 *  в раскрой и в операции генератора. «Двойной обжим»: оба провода обжимаются в один контакт источника (у разъёма включён
 *  двойной обжим). «Сросток»: второй провод идёт только к дальнему разъёму, а с первым его сращивают (включается «Сростки»).
 *  Только в «Сложной сборке» — там у разъёма свой список проводов. */
export function addBranch(D: GenData, S: GenState, from: { epId: string; idx: number; pin: number }, toEpId: string, toPin: number, mode: Branch["mode"]): GenState {
  if (S.tab !== "complex" || !(toPin > 0) || from.epId === toEpId) return S;
  const wire = S.wires.find((w) => w.idx === from.idx);
  const src = S.endpoints.find((e) => e.id === from.epId), dst = S.endpoints.find((e) => e.id === toEpId);
  if (!wire || !wire.compId || !src || !dst) return S;
  const extra = S.nextWireIdx;
  const before = makeGen(D, S);
  const keep = new Map(S.endpoints.map((e) => [e.id, before.routedWireIdxs_(e)]));
  const core = Math.max(0, parseCorePins_(src.pins?.[from.idx]).indexOf(from.pin));
  const color = before.coreColors_(wire)[core];
  const { coreColors: _colors, ...plain } = wire;
  let next: GenState = { ...S, wires: [...S.wires, { ...plain, idx: extra, qty: 1, cores: 1, ...(color ? { coreColors: [color] } : {}) }], nextWireIdx: extra + 1 };
  const all = makeGen(D, next).activeWires_().map((w) => w.idx);
  const endpoints = next.endpoints.map((e) => {
    const has = new Set(keep.get(e.id) || []);
    const wants = e.id === toEpId || (mode === "double" && e.id === from.epId);
    if (wants) has.add(extra); else has.delete(extra);
    const list = all.filter((i) => has.has(i));
    const pins = { ...(e.pins || {}) };
    if (e.id === toEpId) pins[extra] = String(toPin);
    else if (mode === "double" && e.id === from.epId) pins[extra] = String(from.pin);
    return { ...e, wireIdxs: list.length === all.length ? [] : list.length ? list : [NO_WIRES], pins, ...(mode === "double" && e.id === from.epId ? { double: true } : {}) };
  });
  next = { ...next, endpoints, branches: [...(S.branches || []), { main: from.idx, extra, mode, from: from.epId, pin: from.pin, to: toEpId, toPin }] };
  if (mode === "splice") {
    const spl = next.spl;
    const list = String(spl.wires || "").split(/[,;\s]+/).filter(Boolean);
    next = { ...next, spl: !spl.enabled ? { ...spl, enabled: true, count: "1", wires: "2" }
      : list.length > 1 ? { ...spl, wires: `${spl.wires},2` } : { ...spl, count: String((parseInt(spl.count, 10) || 0) + 1) } };
  }
  return syncRoutes(D, next);
}

/** Убрать ответвление: второй провод и его контакты уходят, сросток или двойной обжим снимается. */
export function removeBranch(D: GenData, S: GenState, extra: number): GenState {
  const b = (S.branches || []).find((x) => x.extra === extra);
  if (!b) return S;
  const branches = (S.branches || []).filter((x) => x.extra !== extra);
  let next: GenState = { ...S, branches, wires: S.wires.filter((w) => w.idx !== extra), endpoints: S.endpoints.map((e) => {
    const pins = { ...(e.pins || {}) };
    delete pins[extra];
    const list = (e.wireIdxs || []).filter((i) => i !== extra);
    const stillDouble = e.id === b.from && b.mode === "double" && branches.some((x) => x.mode === "double" && x.from === e.id);
    return { ...e, pins, wireIdxs: e.wireIdxs?.length && !list.length ? [NO_WIRES] : list, ...(e.id === b.from && b.mode === "double" && !stillDouble ? { double: false } : {}) };
  }) };
  if (b.mode === "splice") {
    const spl = next.spl;
    const list = String(spl.wires || "").split(/[,;\s]+/).filter(Boolean);
    const count = list.length > 1 ? list.length - 1 : (parseInt(spl.count, 10) || 0) - 1;
    next = { ...next, spl: count <= 0 ? { ...spl, enabled: false } : list.length > 1 ? { ...spl, wires: list.slice(0, -1).join(",") } : { ...spl, count: String(count) } };
  }
  return syncRoutes(D, next);
}

/** Сросток ↔ двойной обжим для уже созданного ответвления. */
export function setBranchMode(D: GenData, S: GenState, extra: number, mode: Branch["mode"]): GenState {
  const b = (S.branches || []).find((x) => x.extra === extra);
  if (!b || b.mode === mode) return S;
  return addBranch(D, removeBranch(D, S, extra), { epId: b.from, idx: b.main, pin: b.pin }, b.to, b.toPin, mode);
}

/** Предупреждения о раздаче проводов по разъёмам: генератор не угадывает, куда идёт провод. */
export function routingWarnings(D: GenData, S: GenState): string[] {
  const gen = makeGen(D, S);
  const out: string[] = [];
  const units = pinUnits(gen, S);
  const extras = new Set((S.branches || []).map((b) => b.extra));
  (["A", "B"] as Side[]).forEach((side) => {
    if (!multiSide(gen, S, side)) return;
    const eps = gen.endpointsOnSide_(side);
    const title = `Сторона ${sideRu_(side)}`;
    if (!sidePinned(gen, side)) {
      if (!legacyRouting(gen, side)) out.push(`${title}: провода не розданы по разъёмам — раздайте их на схеме, пока генератор считает все провода на каждом разъёме.`);
      return;
    }
    const names = [...new Set(units.filter((u) => !extras.has(u.idx) && !eps.some((ep) => ep.id in u.at)).map((u) => `${u.num} · ${u.name}`))];
    if (names.length) out.push(`${title}: не назначены на разъём — ${names.join(", ")}.`);
  });
  // Кабель в оболочке: жила без контакта ни на одном разъёме — не задействована (подрезать и изолировать или назначить контакт).
  const jacketed = new Set(S.wires.filter((w) => gen.wireCores_(w) > 1).map((w) => w.idx));
  const idle = units.filter((u) => jacketed.has(u.idx) && Object.values(u.at).every((p) => p == null));
  if (idle.length && idle.length < units.filter((u) => jacketed.has(u.idx)).length) {
    out.push(`Не задействованы жилы: ${idle.map((u) => `${u.num} · ${u.name}`).join(", ")} — назначьте контакт или подрежьте и заизолируйте.`);
  }
  const branched = units.filter((u) => !extras.has(u.idx) && (["A", "B"] as Side[]).some((side) => gen.endpointsOnSide_(side).filter((ep) => ep.id in u.at && u.at[ep.id] != null).length > 1));
  if (branched.length) out.push(`Разветвление: ${[...new Set(branched.map((u) => u.name))].join(", ")} — нужен сросток или перемычка (щёлкните контакт и второй разъём — «Добавить ответвление»).`);
  return out;
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
