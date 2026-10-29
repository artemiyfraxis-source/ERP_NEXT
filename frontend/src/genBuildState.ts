import type { Comp, Endpoint, GenState, WireEntry } from "./tcGenLogic";

/*
 * Снимок формы генератора в собранной карте (владелец 14.10: «когда пересобираю, прошлые настройки должны сохраняться,
 * править только нужное»). При сборке типовой вкладки форма (GenState + правки БД.ТЕР + допданные коакса — тот же вид,
 * что хранит черновик) кладётся в `build_config.gen_state`; при открытии собранной карты форма берётся из него, а не из
 * засева. Строки состава в наборе нумеруются по порядку (`kit-N`) и сдвигаются, когда строку убирают, поэтому строки
 * сверяются по типу и артикулу: убранные уходят из формы вместе с кабелями и разъёмами, новые добавляются засевом.
 * Движок ЭТК ключ не читает — ЭТК от него не меняется.
 */

export const GEN_STATE_KEY = "gen_state";
const FORMAT = 1;

export type GenSnapshot = {
  v: number;
  /** Состав набора на момент сборки: id строки и ключ (тип + артикул) — по ключу строки находятся после сдвига id. */
  kit: Array<{ id: string; key: string }>;
  S: GenState;
  terEdits: Record<string, Record<string, string>>;
  coaxAdd: Record<string, Record<string, string>>;
};

const kitKey = (c: Comp) => `${c.type || ""}|${c.art || c.name || ""}`.toLowerCase().trim();
const isPlain = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);

export function genSnapshot(components: Comp[], S: GenState, terEdits: GenSnapshot["terEdits"], coaxAdd: GenSnapshot["coaxAdd"]): GenSnapshot {
  return { v: FORMAT, kit: components.map((c) => ({ id: c.id, key: kitKey(c) })), S, terEdits, coaxAdd };
}

/** Снимок из build_config собранной карты; нет или не тот формат — null (старая карта: форма засевается, как раньше). */
export function snapshotFromConfig(config: unknown): GenSnapshot | null {
  const raw = isPlain(config) ? config[GEN_STATE_KEY] : null;
  if (!isPlain(raw) || raw.v !== FORMAT || !Array.isArray(raw.kit) || !isPlain(raw.S) || !Array.isArray(raw.S.wires) || !Array.isArray(raw.S.endpoints)) return null;
  return {
    v: FORMAT, kit: (raw.kit as GenSnapshot["kit"]).filter((item) => isPlain(item) && typeof item.id === "string"),
    S: raw.S as unknown as GenState,
    terEdits: isPlain(raw.terEdits) ? (raw.terEdits as GenSnapshot["terEdits"]) : {},
    coaxAdd: isPlain(raw.coaxAdd) ? (raw.coaxAdd as GenSnapshot["coaxAdd"]) : {},
  };
}

/** Поля, которых нет в снимке старой версии формы, берутся из засева (как mergeSeed черновика). */
function overSeed(seed: GenState, saved: GenState): GenState {
  const out: Record<string, unknown> = { ...seed };
  for (const [key, value] of Object.entries(saved)) {
    const base = (seed as unknown as Record<string, unknown>)[key];
    out[key] = isPlain(base) && isPlain(value) ? { ...base, ...value } : value;
  }
  return out as unknown as GenState;
}

export type Restored = { S: GenState; terEdits: GenSnapshot["terEdits"]; coaxAdd: GenSnapshot["coaxAdd"]; note: string };

/** Форма из снимка, сверенная с текущим составом: `seed` — засев по нынешнему набору (initState). */
export function restoreFromSnapshot(components: Comp[], seed: GenState, snap: GenSnapshot): Restored {
  // Строки состава: старый id → новый (по ключу, дубли — по порядку); не нашлась — убрана.
  const free = new Map<string, Comp[]>();
  components.forEach((c) => free.set(kitKey(c), [...(free.get(kitKey(c)) || []), c]));
  const idMap = new Map<string, string>();
  const used = new Set<string>();
  snap.kit.forEach((old) => {
    const hit = (free.get(old.key) || []).shift();
    if (hit) { idMap.set(old.id, hit.id); used.add(hit.id); }
  });
  const added = components.filter((c) => !used.has(c.id));
  const removed = snap.kit.filter((old) => !idMap.has(old.id));
  const addedIds = new Set(added.map((c) => c.id));
  const known = new Set(snap.kit.map((old) => old.id));
  // Id, которого не было в составе при сборке (ручной выбор «пусто», служебный), не трогаем; убранный — сбрасываем.
  const map = (id: string | undefined) => (!id ? id || "" : idMap.has(id) ? idMap.get(id)! : known.has(id) ? "" : id);

  let S = overSeed(seed, snap.S);
  // Кабели: убранные уходят, ссылки на них — тоже; новые строки состава приходят из засева с новыми номерами.
  const gone = new Set<number>();
  let wires: WireEntry[] = [];
  S.wires.forEach((w) => {
    if (w.compId && known.has(w.compId) && !idMap.has(w.compId)) gone.add(w.idx);
    else wires.push({ ...w, compId: map(w.compId), ...(w.jacketShrink ? { jacketShrink: { ...w.jacketShrink, mat: map(w.jacketShrink.mat) || "" } } : {}) });
  });
  let nextWireIdx = Math.max(S.nextWireIdx || 0, ...S.wires.map((w) => w.idx + 1), 0);
  seed.wires.filter((w) => w.compId && addedIds.has(w.compId)).forEach((w) => { wires.push({ ...w, idx: nextWireIdx }); nextWireIdx += 1; });
  const live = (idx: number) => !gone.has(idx);
  const dropKeys = <T,>(rec: Record<number, T> | undefined) => (rec ? Object.fromEntries(Object.entries(rec).filter(([k]) => live(Number(k)))) as Record<number, T> : rec);

  let nextEndpointId = Math.max(S.nextEndpointId || 0, S.endpoints.length);
  const endpoints: Endpoint[] = S.endpoints.map((e) => ({
    ...e, connId: map(e.connId), termId: map(e.termId), shrinkMat: e.shrinkMat ? map(e.shrinkMat) : e.shrinkMat,
    wireIdxs: (e.wireIdxs || []).filter(live), ...(e.pins ? { pins: dropKeys(e.pins) } : {}),
  }));
  seed.endpoints.filter((e) => e.connId && addedIds.has(e.connId)).forEach((e) => {
    nextEndpointId += 1;
    endpoints.push({ ...e, id: `${e.side.toLowerCase()}${nextEndpointId}` });
  });

  const keep = (id: string) => map(id) || "";
  // Свивка и лужение хранят позиции среди кабелей с составом: после ухода кабеля позиции сдвигаются.
  const oldActive = snap.S.wires.filter((w) => w.compId).map((w) => w.idx);
  const newActive = wires.filter((w) => w.compId).map((w) => w.idx);
  const posOf = (oldPos: number) => (oldActive[oldPos] === undefined ? -1 : newActive.indexOf(oldActive[oldPos]));
  const posList = (list: number[] | null) => (list === null ? null : list.map(posOf).filter((pos) => pos >= 0));
  S = {
    ...S, wires, nextWireIdx, endpoints, nextEndpointId,
    branches: S.branches?.filter((b) => live(b.main) && live(b.extra)),
    splices: S.splices?.map((sp) => ({ ...sp, ends: sp.ends.filter((e) => live(e.idx)) })).filter((sp) => sp.ends.length),
    tails: S.tails?.filter((e) => live(e.idx)),
    twist: { ...S.twist, wires: posList(S.twist.wires),
      pairs: S.twist.pairs.flatMap((p) => { const x = posOf(Number(p.a)), y = posOf(Number(p.b)); return x >= 0 && y >= 0 ? [{ ...p, a: String(x), b: String(y) }] : []; }) },
    tin: { ...S.tin, wires: posList(S.tin.wires) },
    hs: { ...S.hs, rows: S.hs.rows.filter((r) => !known.has(r.compId) || idMap.has(r.compId)).map((r) => ({ ...r, compId: keep(r.compId) })) },
    wrap: { ...S.wrap, slots: S.wrap.slots.map((slot) => ({ ...slot, mat: keep(slot.mat) })) },
    seal: { ...S.seal, mat: keep(S.seal.mat) },
    cap: { ...S.cap, tut: keep(S.cap.tut) },
    // Способа сростка в снимке до 16.10 нет — это пайка (засев новой формы — опрессовка гильзой, ему не верим).
    spl: { ...S.spl, tutMat: keep(S.spl.tutMat), method: snap.S.spl?.method ?? "solder",
      ...(S.spl.sleeves ? { sleeves: Object.fromEntries(Object.entries(S.spl.sleeves).map(([key, id]) => [key, keep(id)])) } : {}),
      ...(S.spl.band ? { band: keep(S.spl.band) } : {}) },
    // Наконечники хвостов по проводам: провод ушёл из состава — его выбор тоже.
    ...(S.tailLug?.arts ? { tailLug: { ...S.tailLug, arts: Object.fromEntries(Object.entries(S.tailLug.arts).filter(([idx]) => live(Number(idx)))) } } : {}),
    ...(S.jacketShrink ? { jacketShrink: Object.fromEntries(Object.entries(S.jacketShrink).map(([side, js]) => [side, js ? { ...js, mat: keep(js.mat) } : js])) } : {}),
  };
  const note = added.length || removed.length
    ? `Состав изменился: добавлено ${added.length}, убрано ${removed.length} — проверьте` : "";
  return { S, terEdits: snap.terEdits, coaxAdd: snap.coaxAdd, note };
}
