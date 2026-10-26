/*
 * Сростки и хвосты «Сложной сборки» (владелец 13.10, чертёж «Жгут НВ4»): провод 0,5 с контакта разъёма приходит в
 * обжимную гильзу, из неё — три провода 0,2 к контактам и в хвост; два контакта разных разъёмов сходятся в один хвост.
 * Технолог рисует это руками на схеме соединений — генератор ничего не угадывает.
 *
 * Модель: у каждого провода (жила `unit` строки «Кабели» `idx`) два конца — сторона А и сторона В. Конец стоит на контакте
 * разъёма своей стороны (распиновка), в сростке (`S.splices`) или свободен — хвост (`S.tails`). Длина каждого отрезка —
 * своя строка «Кабели» (провод 0,5 150 мм, провод 0,2 160 мм ×2, провод 0,2 60 мм — хвост). Сросток уходит в операции
 * генератора как «Сростки» по числу проводов в каждом (S.spl), хвост — как свободный конец (заделка/зачистка концов).
 */
import { assignPin, syncRoutes, type PinoutEnd, type PinUnit } from "./pinoutCheck";
import { endJoined, formatCorePins_, makeGen, NO_WIRES, parseCorePins_, spliceName, type GenData, type GenState, type Side, type Splice, type WireEnd } from "./tcGenLogic";

const same = (a: WireEnd, b: WireEnd) => a.idx === b.idx && a.unit === b.unit && a.side === b.side;

/** «С1», «С2» — по порядку сростков. */
export const spliceLabel = (S: Pick<GenState, "splices">, id: string) => {
  const sp = (S.splices || []).find((s) => s.id === id);
  return sp ? spliceName(S, sp) : "С?";
};

/** Своё название сростка («Spl1»); пустое возвращает «С1», «С2» по порядку. */
export const renameSplice = (S: GenState, id: string, name: string): GenState =>
  ({ ...S, splices: (S.splices || []).map((s) => (s.id === id ? { ...s, name: name.trim() ? name : undefined } : s)) });

/** Сросток, в котором стоит конец, или null. */
export const spliceOf = (S: Pick<GenState, "splices">, end: WireEnd): Splice | null => (S.splices || []).find((s) => s.ends.some((e) => same(e, end))) || null;

/** Конец уходит из сростка и из хвостов (пустой сросток убирается). */
function release(S: GenState, end: WireEnd): GenState {
  const splices = (S.splices || []).map((s) => ({ ...s, ends: s.ends.filter((e) => !same(e, end)) })).filter((s) => s.ends.length);
  return { ...S, splices, tails: (S.tails || []).filter((e) => !same(e, end)) };
}

/** Конец в сростке или хвосте не стоит на контакте разъёма своей стороны. */
function clearPins(S: GenState, end: WireEnd): GenState {
  return { ...S, endpoints: S.endpoints.map((ep) => {
    if (ep.side !== end.side) return ep;
    const list = parseCorePins_(ep.pins?.[end.idx]);
    if (list[end.unit] == null) return ep;
    list[end.unit] = null;
    const pins = { ...(ep.pins || {}) };
    const text = formatCorePins_(list);
    if (text) pins[end.idx] = text; else delete pins[end.idx];
    return { ...ep, pins };
  }) };
}

/** Сторона с одним разъёмом: провод, все концы которого здесь в сростках и хвостах, на разъём не идёт (у стороны с
 *  несколькими разъёмами провод идёт только туда, где у него контакт, — syncRoutes). */
function routeSingle(D: GenData, S: GenState, idx: number, side: Side): GenState {
  const eps = S.endpoints.filter((ep) => ep.side === side);
  if (eps.length !== 1) return S;
  const gen = makeGen(D, S);
  const wire = gen.activeWires_().find((w) => w.idx === idx);
  if (!wire) return S;
  const units = gen.unitCount_(wire);
  const detached = Array.from({ length: units }, (_, k) => k).every((k) => endJoined(S, idx, k, side));
  const all = gen.activeWires_().map((w) => w.idx);
  const routed = new Set(gen.routedWireIdxs_(eps[0]));
  if (detached) routed.delete(idx); else routed.add(idx);
  const list = all.filter((i) => routed.has(i));
  const wireIdxs = list.length === all.length ? [] : list.length ? list : [NO_WIRES];
  return { ...S, endpoints: S.endpoints.map((ep) => (ep.id === eps[0].id ? { ...ep, wireIdxs } : ep)) };
}

/** Операция «Сростки» генератора — по сросткам формы: число и провода в каждом (вместе со сростками ответвлений). */
function syncSpl(D: GenData, S0: GenState): GenState {
  const gen = makeGen(D, S0);
  const alive = new Map(gen.activeWires_().map((w) => [w.idx, gen.unitCount_(w)]));
  const valid = (e: WireEnd) => (alive.get(e.idx) ?? 0) > e.unit;
  const S: GenState = { ...S0, splices: (S0.splices || []).map((s) => ({ ...s, ends: s.ends.filter(valid) })).filter((s) => s.ends.length),
    tails: (S0.tails || []).filter(valid) };
  const nodes = (S.splices || []).filter((s) => s.ends.length >= 2).map((s) => s.ends.length);
  // Сростков-узлов не было и нет — «Сростки», включённые руками, не трогаем.
  if (!nodes.length && !(S0.splices || []).some((s) => s.ends.length >= 2)) return syncRoutes(D, S);
  const sizes = [...(S.branches || []).filter((b) => b.mode === "splice").map(() => 2), ...nodes];
  const spl = sizes.length ? { ...S.spl, enabled: true, count: String(sizes.length), wires: sizes.join(",") } : { ...S.spl, enabled: false };
  return syncRoutes(D, { ...S, spl });
}

/** Конец провода — в сросток `target` (id) или в новый ("new"). Снимается с контакта и из хвостов. */
export function joinSplice(D: GenData, S: GenState, end: WireEnd, target: string): GenState {
  if (S.tab !== "complex") return S;
  let next = clearPins(release(S, end), end);
  const splices = next.splices || [];
  if (target === "new" || !splices.some((s) => s.id === target)) {
    let n = splices.length + 1;
    while (splices.some((s) => s.id === `s${n}`)) n++;
    next = { ...next, splices: [...splices, { id: `s${n}`, ends: [end] }] };
  } else {
    next = { ...next, splices: splices.map((s) => (s.id === target ? { ...s, ends: [...s.ends, end] } : s)) };
  }
  return syncSpl(D, routeSingle(D, next, end.idx, end.side));
}

/** Конец провода — свободный (хвост без разъёма, своей длины). */
export function setTail(D: GenData, S: GenState, end: WireEnd): GenState {
  if (S.tab !== "complex") return S;
  const next = clearPins(release(S, end), end);
  return syncSpl(D, routeSingle(D, { ...next, tails: [...(next.tails || []), end] }, end.idx, end.side));
}

/** Конец провода снова ждёт контакта разъёма (из сростка или хвоста). */
export function leaveJoin(D: GenData, S: GenState, end: WireEnd): GenState {
  if (!endJoined(S, end.idx, end.unit, end.side)) return S;
  return syncSpl(D, routeSingle(D, release(S, end), end.idx, end.side));
}

/** Контакт провода на разъёме (assignPin): конец, бывший в сростке или хвосте, оттуда уходит. */
export function assignPinJoined(D: GenData, S: GenState, epId: string, idx: number, unit: number, pin: number): GenState {
  const ep = S.endpoints.find((e) => e.id === epId);
  const end = ep ? { idx, unit, side: ep.side } : null;
  const freed = end && endJoined(S, idx, unit, end.side) ? syncSpl(D, routeSingle(D, release(S, end), idx, end.side)) : S;
  return assignPin(D, freed, epId, idx, unit, pin);
}

// ── Связи на схеме соединений ─────────────────────────────────────────────────────────────────────────────────────
/** Куда приходит конец провода: контакт разъёма, сросток, хвост или никуда (не задан). */
export type LinkAnchor = { kind: "pin"; epId: string; pin: number } | { kind: "splice"; id: string } | { kind: "tail" } | { kind: "open" };
export type LinkWire = { unit: PinUnit; a: LinkAnchor[]; b: LinkAnchor[] };

/** Провода схемы связей: у каждого — концы сторон А и В (контакты всех разъёмов стороны, где он стоит; иначе сросток,
 *  хвост или «не задан»). Провод без единого контакта и сростка — не рисуется. */
export function linkWires(ends: PinoutEnd[], units: PinUnit[], S: Pick<GenState, "splices" | "tails">): LinkWire[] {
  const sideAnchors = (u: PinUnit, side: Side): LinkAnchor[] => {
    const pins = ends.filter((e) => e.side === side && u.at[e.id] != null).map((e): LinkAnchor => ({ kind: "pin", epId: e.id, pin: u.at[e.id]! }));
    if (pins.length) return pins;
    const end = { idx: u.idx, unit: u.unit, side };
    const sp = spliceOf(S, end);
    if (sp) return [{ kind: "splice", id: sp.id }];
    return [{ kind: endJoined(S, u.idx, u.unit, side) === "tail" ? "tail" : "open" }];
  };
  return units.flatMap((u) => {
    const a = sideAnchors(u, "A"), b = sideAnchors(u, "B");
    const real = [...a, ...b].filter((x) => x.kind === "pin" || x.kind === "splice");
    return real.length ? [{ unit: u, a, b }] : [];
  });
}

/** Подпись конца провода для палитры и подсказок: «А1·2», «С1», «хвост», "" — не задан. */
export function anchorText(anchor: LinkAnchor, ends: PinoutEnd[], S: Pick<GenState, "splices">): string {
  if (anchor.kind === "pin") return `${ends.find((e) => e.id === anchor.epId)?.key ?? ""}·${anchor.pin}`;
  if (anchor.kind === "splice") return `сросток ${spliceLabel(S, anchor.id)}`;
  return anchor.kind === "tail" ? "хвост" : "";
}
