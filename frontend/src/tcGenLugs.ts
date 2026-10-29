/*
 * Наконечники (НШВИ, НКИ, НВИ, РПИ…) для генератора техкарт — единственное место, которое знает, где живут их записи.
 *
 * Сейчас наконечники — строки БД.ТЕР со списком «Наконечники» (`ter_kind` = "lug", backend `references/lugs.py`); владелец
 * 29.10: «наконечники и контакты в одной базе БД.ТЕР — так не должно быть» — их перенесут в свой справочник. Генератор,
 * форма и пакетная сборка берут наконечники только через `lugRecordsOf` и функции этого файла, поэтому переносу хватит
 * поменять здесь, откуда читаются записи. Ключи полей — как у контактов: terArticle, terStrip (длина зачистки),
 * terLPlus / terLMinus (припуски L+ / L−), terApplicator, mm2From…awgTo.
 */
import { terFromFields, type TerRec } from "./tcGenLogic";

/** Запись наконечника: поля контакта БД.ТЕР и тип («НШВИ», «НКИ», «РПИ-М») — «Product Name» записи. */
export type LugRec = TerRec & { type: string };

/** Вид опрессовки наконечника (владелец 29.10: «отдельную операцию на НКИ, НВИ и т.д. и отдельную на НШВИ»): втулочные
 *  (НШВ, НШВИ) — своя операция, кабельные (кольцевые, вилочные, штыревые, ножевые) — своя, трубчатые (ТМ, ТМЛ, ТА) — своя. */
export type LugCrimp = "ferrule" | "lug" | "tube";

type LugSource = { id: string; fields?: Record<string, string> | null; ter_kind?: string | null };

const text = (v: unknown) => String(v ?? "").trim();
/** Тип наконечника записи: «Product Name» (terComponent), иначе первое слово серии или артикула. */
function lugType(f: Record<string, string>, art: string): string {
  const own = text(f.terComponent) || text(f["Product Name"]);
  if (own) return own;
  const series = text(f.terSeries) || text(f.Series) || art;
  return series.split(/[\s\d]/)[0] || series;
}

/** Наконечники из записей справочника (сейчас — строки БД.ТЕР списка «Наконечники»). */
export function lugRecordsOf(records: ReadonlyArray<LugSource> | null | undefined): LugRec[] {
  return (records || []).filter((r) => r.ter_kind === "lug").map((r) => {
    const f = r.fields || {};
    const rec = terFromFields(r.id, f);
    return { ...rec, type: lugType(f, rec.article || rec.articleBag || "") };
  }).filter((r) => r.article || r.articleBag);
}

/** Вид опрессовки по типу или артикулу наконечника. «ПМ» — наконечник под пайку: не обжимается (null). */
export function lugCrimpOf(typeOrArt: string): LugCrimp | null {
  const s = text(typeOrArt);
  if (/^пм(?![а-яё])/i.test(s)) return null;
  if (/^нш[вv]/i.test(s)) return "ferrule";
  if (/^(?:тмл|там|тм|та)(?![а-яё])/i.test(s)) return "tube";
  return "lug";
}

/** Название операции опрессовки по виду. */
export const LUG_CRIMP_NAMES: Record<LugCrimp, string> = {
  ferrule: "втулочных наконечников", lug: "кабельных наконечников", tube: "трубчатых наконечников",
};

/** Код карты операции вида (библиотека операций): PRS_НШВИ, PRS_HKИ/НВИ/РПИ (в коде латинские H, K), PRS_ТМЛ. */
export const LUG_CRIMP_CARD: Record<LugCrimp, RegExp> = {
  ferrule: /^PRS_НШВИ$/i, lug: /^PRS_[HН][KК]И\/НВИ\/РПИ$/i, tube: /^PRS_ТМЛ$/i,
};

/** Типы наконечников, которые обжимаются, — по справочнику, в порядке первой встречи (без «ПМ» под пайку). */
export function lugTypes(lugs: ReadonlyArray<LugRec>): string[] {
  const out: string[] = [];
  lugs.forEach((r) => { if (r.type && lugCrimpOf(r.type) && !out.includes(r.type)) out.push(r.type); });
  return out;
}

/** Запись наконечника по артикулу (REEL или BAG; регистр и пробелы не важны). */
export function lugByArt(lugs: ReadonlyArray<LugRec>, art: string): LugRec | null {
  const key = (s: string) => text(s).toLowerCase().replace(/\s+/g, " ").replace(",", ".");
  const want = key(art);
  if (!want) return null;
  return lugs.find((r) => [r.article, r.articleBag].some((a) => !!a && key(a) === want)) || null;
}

/** Подпись наконечника в форме и ЭТК: артикул (REEL, иначе BAG). */
export const lugArt = (r: LugRec) => r.article || r.articleBag || "";
