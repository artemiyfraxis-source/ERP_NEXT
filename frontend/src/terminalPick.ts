/*
 * Терминал разъёма из состава («Данные из листа») по совместимости с корпусом (владелец 13.10: «когда я выбираю разъём,
 * к нему выбираются контакты»). Единственная автоматика «Сложной сборки»: провода и распиновку технолог задаёт сам.
 *
 * Корпуса состава собираются по типу — артикул без числа контактов (SMR-02V-B и SMR-04V-B — «SMR-V-B», I-DS1071-SCB01X3
 * и …SCB02X3 — «I-DS1071-SCB»), и каждому типу ищется его терминал среди терминалов состава — по порядку, от точного к косвенному:
 *  1. пары «корпус → терминалы» базы разъёмов (`pairs`, connector-series) и колонка «Series» записи БД.ТЕР терминала
 *     («SMR» — терминал корпусов SMR);
 *  2. общее «семейство» в артикулах (I-DS1071-SCB02X3 ↔ T-DS1071-SC600 — «DS1071»);
 *  3. количество: терминалов в составе столько, сколько контактов у корпусов серии (по артикулу — SMP-02V-BC = 2,
 *     или занятых распиновкой), и ни у какой другой серии столько же;
 *  4. остался один терминал и одна серия без терминала — они друг для друга; в составе один терминал — он для всех.
 * Не нашлось однозначного — терминал не выбирается (лучше «— нет —», чем чужой терминал).
 */
import type { Comp, Gen, GenState } from "./tcGenLogic";

/** Пары базы разъёмов: нормализованный артикул корпуса → нормализованные артикулы его терминалов. */
export type TerminalPairs = Record<string, string[]>;

/** Кириллица, набранная вместо латиницы в артикуле («SМR-02V-B» с русской М), — латиницей; артикул без латиницы как есть. */
const TWINS: Record<string, string> = { А: "A", В: "B", Е: "E", К: "K", М: "M", Н: "H", О: "O", Р: "P", С: "C", Т: "T", Х: "X", У: "Y" };
export function latinArt(art: string): string {
  const s = String(art || "");
  return /[A-Za-z]/.test(s) ? s.replace(/[АВЕКМНОРСТХУавекмнорстху]/g, (ch) => TWINS[ch.toUpperCase()] ?? ch) : s;
}
const norm = (art: string) => latinArt(art).toUpperCase().replace(/[\s./()_-]/g, "");
const qtyOf = (c: Comp) => Number(String(c.qty).replace(",", ".")) || 0;

/** Число контактов корпуса по артикулу: «SMR-04V-B» → 4, «I-DS1071-SCB02X3» → 6 (2 ряда × 3), «XHP-3» → 3,
 *  «43025-0400» → 4; не распознан — null. */
export function pinCountFromArt(art: string): number | null {
  const s = latinArt(art).toUpperCase().trim();
  const grid = /(\d{1,2})X(\d{1,2})(?!\d)/.exec(s);
  if (grid && /[A-Z]{2,}\d{1,2}X\d/.test(s)) return Number(grid[1]) * Number(grid[2]) || null;
  const jst = /^[A-Z]{2,5}-(\d{2})V(?:-|$)/.exec(s);
  if (jst) return Number(jst[1]) || null;
  const short = /^(?:XHP|PHR|ZHR|EHR|VHR|PAP|NSHR|ZER|SHR|GHR|SYR|SYP)-(\d{1,2})(?!\d)/.exec(s);
  if (short) return Number(short[1]) || null;
  const molex = /^\d{5}-(\d{2})\d{2}$/.exec(s);
  if (molex) return Number(molex[1]) || null;
  return null;
}

/** Тип корпуса — артикул без числа контактов: «SMR-02V-B» и «SMR-04V-B» → «SMR-V-B», «SMP-02V-BC» → «SMP-V-BC»,
 *  «I-DS1071-SCB02X3» → «I-DS1071-SCB», «DF1B-18DS-2.5RC» → «DF1B-DS-2.5RC» (розетка и вилка одной серии — разные типы). */
export function housingSeries(art: string): string {
  return latinArt(art).toUpperCase().trim().split(/[-\s]+/).filter(Boolean).map((part) => {
    const grid = /^([A-Z]+)\d{1,2}X\d{1,2}$/.exec(part);
    if (grid) return grid[1];
    const count = /^\d{1,2}([A-Z]*)$/.exec(part);
    return count ? count[1] : part;
  }).join("-");
}

/** «Семейства» артикула — части с буквами и цифрами не короче 4 знаков (DS1071, SCB02X3). */
const families = (art: string) => new Set(latinArt(art).toUpperCase().split(/[-\s/]+/).filter((t) => t.length >= 4 && /[A-Z]/.test(t) && /\d/.test(t)));

type Group = { key: string; arts: string[]; counts: Set<number> };

/** Терминал (id позиции состава) каждого разъёма с корпусом, у которого он нашёлся однозначно: id разъёма → id терминала. */
export function pickTerminals(gen: Gen, S: GenState, pairs?: TerminalPairs): Record<string, string> {
  const { terms, conns } = gen.bomLists();
  const out: Record<string, string> = {};
  if (!terms.length) return out;
  const crimpEps = S.endpoints.filter((ep) => ep.method === "crimp" && ep.connId);
  // Серии корпусов: из состава (там все корпуса изделия, даже ещё не добавленные в форму) и из разъёмов формы.
  const groups = new Map<string, Group>();
  const groupOf = (art: string) => {
    const key = housingSeries(art);
    if (!groups.has(key)) groups.set(key, { key, arts: [], counts: new Set() });
    const g = groups.get(key)!;
    if (!g.arts.includes(art)) g.arts.push(art);
    return g;
  };
  const positions = new Map<string, number | null>();
  conns.forEach((c) => {
    const art = c.art || c.name || "";
    if (!art) return;
    groupOf(art);
    const n = pinCountFromArt(art);
    const key = housingSeries(art);
    const prev = positions.has(key) ? positions.get(key)! : 0;
    positions.set(key, prev === null || n === null ? null : prev + n * Math.max(1, qtyOf(c)));
  });
  const used = new Map<string, number>();
  crimpEps.forEach((ep) => {
    const c = gen.getCompById(ep.connId);
    const art = c ? c.art || c.name || "" : "";
    if (!art) return;
    groupOf(art);
    const pins = gen.activeWires_().reduce((n, w) => n + gen.corePinsOf_(ep, w.idx).filter((p) => p != null).length, 0);
    used.set(housingSeries(art), (used.get(housingSeries(art)) || 0) + pins);
  });
  groups.forEach((g) => {
    const p = positions.get(g.key);
    if (p) g.counts.add(p);
    const u = used.get(g.key);
    if (u) g.counts.add(u);
  });

  const decided = new Map<string, Comp>();
  const list = [...groups.values()];
  // 1. База разъёмов и колонка «Series» БД.ТЕР.
  const seriesOf = (t: Comp) => {
    const rec = gen.findTerRecord_(t.art || t.name);
    const f = rec?.fields || {};
    return new Set([...String(f.terSeries || "").split(/[,;]/), String(f.terComponent || "")].map(norm).filter(Boolean));
  };
  const explicit = (g: Group, t: Comp) => {
    const tn = norm(t.art || t.name);
    if (g.arts.some((art) => (pairs?.[norm(art)] || []).includes(tn))) return true;
    const series = seriesOf(t);
    // «Series» БД.ТЕР — начала артикулов корпусов: «SMR, SYP», «I-DS1071-SCB01, I-DS1071-SCB02», «SM» (вся серия SM).
    return g.arts.some((art) => { const a = norm(art); return [...series].some((token) => token.length >= 2 && a.startsWith(token)); });
  };
  const cands = new Map<string, Comp[]>();
  list.forEach((g) => {
    const hit = terms.filter((t) => explicit(g, t));
    if (hit.length === 1) decided.set(g.key, hit[0]);
    cands.set(g.key, hit.length ? hit : terms);
  });
  // 2. Общее семейство в артикулах.
  list.forEach((g) => {
    if (decided.has(g.key)) return;
    const fam = new Set(g.arts.flatMap((art) => [...families(art)]));
    const hit = cands.get(g.key)!.filter((t) => [...families(t.art || t.name)].some((x) => fam.has(x)));
    // Семейство общее у нескольких типов корпусов (розетка DF1B-…DS и вилка DF1B-…EP) — оно их не различает.
    const shared = list.some((o) => o !== g && !decided.has(o.key) && o.arts.some((art) => hit.some((t) => [...families(t.art || t.name)].some((x) => families(art).has(x)))));
    if (hit.length === 1 && !shared) decided.set(g.key, hit[0]);
  });
  // 3–4. Количество и последний оставшийся — один терминал на одну серию.
  const free = () => terms.filter((t) => ![...decided.values()].includes(t));
  for (let changed = true; changed;) {
    changed = false;
    const open = list.filter((g) => !decided.has(g.key));
    if (!open.length) break;
    const left = free();
    for (const g of open) {
      const mine = cands.get(g.key)!.filter((t) => left.includes(t) && g.counts.has(qtyOf(t)));
      const rivals = open.filter((o) => o !== g && mine.some((t) => o.counts.has(qtyOf(t))));
      if (mine.length === 1 && !rivals.length) { decided.set(g.key, mine[0]); changed = true; break; }
    }
    if (changed) continue;
    if (open.length === 1 && left.length === 1 && cands.get(open[0].key)!.includes(left[0])) { decided.set(open[0].key, left[0]); changed = true; }
  }
  if (terms.length === 1) list.forEach((g) => { if (!decided.has(g.key)) decided.set(g.key, terms[0]); });

  crimpEps.forEach((ep) => {
    const c = gen.getCompById(ep.connId);
    const t = c ? decided.get(housingSeries(c.art || c.name || "")) : undefined;
    if (t) out[ep.id] = t.id;
  });
  return out;
}

/** Пары «корпус → терминалы» из ответа базы разъёмов (connector-series по артикулу): терминалы серии найденного корпуса
 *  (`terminals` — БД.ТЕР по «Series», `contacts` — контакты каталога производителя) и, если они есть, терминалы самого
 *  корпуса (`match.terminals` / `match.housing.terminals`). Формат ответа не тот — пар нет. */
export function pairsFromSeries(article: string, answer: unknown): string[] {
  const read = (list: unknown) => (Array.isArray(list) ? list : []).map((item) => (typeof item === "string" ? item : (item as { article?: unknown })?.article))
    .filter((a): a is string => typeof a === "string" && !!a.trim()).map(norm);
  const data = answer as { match?: { series?: string | null; terminals?: unknown; housing?: { terminals?: unknown } } | null; series?: Array<{ id?: string; terminals?: unknown; contacts?: unknown }> } | null;
  const match = data?.match;
  if (!match) return [];
  const own = [...read(match.terminals), ...read(match.housing?.terminals)];
  if (own.length) return [...new Set(own)];
  const series = (data?.series || []).find((s) => s.id === match.series);
  return series ? [...new Set([...read(series.terminals), ...read(series.contacts)])] : [];
}

export const normalizeTerminalArt = norm;
