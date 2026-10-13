import type { TechOperationRecord } from "./api";
import { parseLengthVal_, sideRu_, terFromFields, type Gen, type GenState, type TerRec } from "./tcGenLogic";

/* «Записать в БД» карточки разъёма генератора: нормы терминала (L+, L−, шаг, аппликатор) — в его запись БД.ТЕР.
   Владелец 13.10.2026: «после записывания введённое число удаляется» и «в БД нет припусков в минус» — запись проверяется
   по ответу сервера, справочник генератора обновляется этой записью без пересева формы, L− подсказывается только по
   данным БД.ТЕР (та же серия того же производителя), не выдумывается. */

export type TerKey = "lPlus" | "lMinus" | "step" | "applicator";
export type TerEdits = Partial<Record<string, string>>;

/** Поле формы → поле записи БД.ТЕР → подпись. */
export const TER_DB_FIELDS: ReadonlyArray<readonly [TerKey, string, string]> = [
  ["lPlus", "terLPlus", "L+"], ["lMinus", "terLMinus", "L−"], ["step", "terStep", "Шаг"], ["applicator", "terApplicator", "Аппликатор"],
];
const LABEL = Object.fromEntries(TER_DB_FIELDS.map(([key, , label]) => [key, label])) as Record<TerKey, string>;
const MM_NUMBER = /^\d+([.,]\d+)?$/;

const edited = (edits: TerEdits) => TER_DB_FIELDS.filter(([key]) => typeof edits[key] === "string");
const typed = (edits: TerEdits, key: TerKey) => String(edits[key] ?? "").trim();

/** L+ и L− — число в мм (запятая или точка) или пусто; шаг бывает «3 / 2» и пишется как есть. */
export function terEditProblem(edits: TerEdits): string {
  for (const key of ["lPlus", "lMinus"] as const) {
    const value = typed(edits, key);
    if (value && !MM_NUMBER.test(value)) return `${LABEL[key]}: число в мм, а не «${value}»`;
  }
  return "";
}

/** Правки записи: только изменённые поля, `old` — как в записи сейчас (сервер сверяет, не поменяли ли её). */
export function terChanges(rec: TerRec, edits: TerEdits): Array<{ field: string; old: string; value: string }> {
  return edited(edits)
    .filter(([key]) => typed(edits, key) !== String(rec[key] || "").trim())
    .map(([key, field]) => ({ field, old: rec.fields?.[field] ?? "", value: typed(edits, key) }));
}

/** Новая запись БД.ТЕР для терминала, которого в базе нет. */
export function terNewFields(article: string, edits: TerEdits): Record<string, string> {
  return { terArticle: article, ...Object.fromEntries(TER_DB_FIELDS.map(([key, field]) => [field, typed(edits, key)])) };
}

/** Запись, как её вернул сервер, — в виде справочника генератора. */
export const terSaved = (record: TechOperationRecord): TerRec => terFromFields(record.id, record.fields || {});

/** Что ввели, а сервер не сохранил (подписи полей): такая запись — ошибка, введённое остаётся в форме. */
export function terLost(edits: TerEdits, saved: TerRec): string[] {
  return edited(edits).filter(([key]) => typed(edits, key) !== String(saved[key] || "").trim()).map(([key]) => LABEL[key]);
}

/** Справочник генератора с записанной записью: заменить по id, новую — добавить. */
export function withTerRecord(list: TerRec[], saved: TerRec): TerRec[] {
  return list.some((item) => item.id === saved.id) ? list.map((item) => (item.id === saved.id ? saved : item)) : [...list, saved];
}

/** «Записано в БД.ТЕР · 500798000: L− 3 мм, Аппликатор S19». */
export function terSavedText(article: string, edits: TerEdits, created: boolean): string {
  const parts = edited(edits).map(([key]) => {
    const value = typed(edits, key);
    if (!value) return `${LABEL[key]} очищено`;
    return key === "applicator" ? `${LABEL[key]} ${value}` : `${LABEL[key]} ${value} мм`;
  });
  return `${created ? "Добавлено в БД.ТЕР" : "Записано в БД.ТЕР"} · ${article}${parts.length ? `: ${parts.join(", ")}` : ""}`;
}

const sameText = (a: string | undefined, b: string | undefined) => !!a && !!b && a.trim().toLowerCase() === b.trim().toLowerCase();

/** Подсказка L−, когда у терминала его нет: L− терминалов той же серии того же производителя в БД.ТЕР, если он у них
 *  один. Подсказка — не запись: в БД она попадает, только если её подставить и нажать «Записать в БД». */
export function lMinusSuggestion(records: TerRec[], rec: TerRec | null): { value: string; from: string[] } | null {
  if (!rec || String(rec.lMinus || "").trim()) return null;
  const maker = rec.fields?.terManufacturer, series = rec.fields?.terSeries;
  if (!maker || !series) return null;
  const kin = records.filter((item) => item.id !== rec.id && String(item.lMinus || "").trim() && parseLengthVal_(item.lMinus) > 0
    && sameText(item.fields?.terManufacturer, maker) && sameText(item.fields?.terSeries, series));
  const values = new Set(kin.map((item) => parseLengthVal_(item.lMinus)));
  if (values.size !== 1) return null;
  return { value: kin[0].lMinus.trim(), from: kin.map((item) => item.article).filter(Boolean) };
}

/** «От краёв разъёмов»: разъёмы с обжимом, у терминала которых в БД.ТЕР нет L− — длина реза этот конец не вычитает. */
export function lMinusMissing(gen: Gen, S: GenState): string[] {
  if (S.measureMode !== "outer") return [];
  return S.endpoints.filter((ep) => ep.method === "crimp" && ep.termId).flatMap((ep) => {
    const term = gen.getCompById(ep.termId);
    if (!term) return [];
    const rec = gen.findTerRecord_(term.art);
    if (rec && parseLengthVal_(rec.lMinus) > 0) return [];
    return [`${sideRu_(ep.side)}${gen.endpointOrdinal_(ep)} (${term.art || term.name})`];
  });
}
