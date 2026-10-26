/*
 * Поиск записи БД.ПРОВ по позиции состава (генератор техкарт: форма и пакетная генерация — одна функция).
 * Состав и база пишут одну марку по-разному: «НВ4 0,2 красный» ↔ «НВ-4 0,20» + Цвет «красный», «ПВВС 4x0,35» ↔ «ПВВС 4×0,35»,
 * «Силикон 28AWG белый» ↔ «Силиконовый провод 28AWG» + Цвет. Записи базы не переименовываются — искать надо терпимо.
 */

const COLOR_STEMS_ = ["красн", "черн", "бел", "син", "желт", "зелен", "коричнев", "оранжев", "фиолет", "голуб", "сер", "розов", "прозрачн", "натуральн"];
const COLOR_END_ = "(?:ый|ий|ой|ая|ое|ые|ие)";
const COLOR_RE_ = new RegExp(`(^|[^а-яa-z0-9])(?:${COLOR_STEMS_.join("|")})${COLOR_END_}(?=$|[^а-яa-z0-9])`, "g");
const COLOR_FIND_RE_ = new RegExp(`(?:^|[^а-яa-z0-9])(${COLOR_STEMS_.join("|")})${COLOR_END_}(?=$|[^а-яa-z0-9])`);

/** Явные соответствия «как пишут в составе» → марка базы, для которых в БД.ПРОВ нет ни такого написания, ни артикула поставщика
 *  (владелец 26.10: «Силиконовый 22AWG» — тот же провод prodiel, что UL3135 22AWG; Helukabel 15201 — LiFY 0,14).
 *  Маленькая таблица в коде, пока в базе нет столбца под другие названия; левая часть — регулярка по тексту позиции. */
export const CABLE_ALIASES_: Array<[RegExp, string]> = [
  [/^\s*силикон\S*\s+(?:провод\s+)?22\s*awg/i, "UL3135 22AWG"],
  [/helukabel[\s-]*15201|(?:^|[^\d])15201(?!\d)/i, "LiFY 0,14"],
];

const fold_ = (value: string) => String(value || "").toLowerCase().replace(/ё/g, "е");

/** Ключ марки: без регистра и «ё», без «мм²/мм2/мм», цвета, слова «провод»; «силиконовый» = «силикон»; «×/х/x/*» между числами — «x»;
 *  десятичные без добивки нулей (0,20 = 0,2 = 0.2); дефисы, пробелы и знаки выброшены («НВ4» = «НВ-4»). */
export function cableMatchKey(value: string): string {
  let s = fold_(value);
  s = s.replace(COLOR_RE_, "$1");
  s = s.replace(/(?<=\d)\s*мм\s*[²2]?|мм\^2/g, "");
  s = s.replace(/(?<=\d)\s*[×xх*]\s*(?=\d)/g, "x");
  s = s.replace(/\d+[.,]\d+/g, (n) => String(parseFloat(n.replace(",", "."))));
  s = s.replace(/силиконов\S*/g, "силикон").replace(/(^|[^а-яa-z0-9])провод(?=$|[^а-яa-z0-9])/g, "$1");
  return s.replace(/[^a-zа-я0-9.]/g, "");
}

/** Основа цвета («красный» → «красн», «чёрный» → «черн»); цвета в тексте нет — "". */
export function cableColorStem(value: string): string {
  return COLOR_FIND_RE_.exec(fold_(value))?.[1] || "";
}

export type CableMatchRec = { mark: string; color?: string; supplierCodes?: string[] };

/** Запись БД.ПРОВ позиции состава по её артикулу и наименованию:
 *  1) марка совпала по ключу; 2) совпал «Артикул поставщика»; 3) явный алиас (CABLE_ALIASES_);
 *  4) самая длинная марка (≥ 4 знаков), входящая в текст позиции.
 *  Записей одной марки несколько (строка на цвет) — берётся с тем же цветом, что в позиции, иначе первая. */
export function matchCableRec<T extends CableMatchRec>(recs: T[], texts: string[]): T | null {
  const own = texts.map((t) => String(t || "")).filter((t) => t.trim());
  if (!recs.length || !own.length) return null;
  const keys = own.map(cableMatchKey).filter(Boolean);
  const color = own.map(cableColorStem).find(Boolean) || "";
  const pick = (list: T[]) => (list.length ? list.find((r) => color && cableColorStem(r.color || "") === color) || list[0] : null);
  const markKey = (r: T) => cableMatchKey(r.mark);
  const byMark = pick(recs.filter((r) => { const k = markKey(r); return !!k && keys.includes(k); }));
  if (byMark) return byMark;
  const codeKey = (code: string) => cableMatchKey(code);
  const bySupplier = pick(recs.filter((r) => (r.supplierCodes || []).some((code) => { const k = codeKey(code); return k.length >= 4 && keys.some((t) => t === k || t.includes(k)); })));
  if (bySupplier) return bySupplier;
  for (const [re, mark] of CABLE_ALIASES_) {
    if (!own.some((t) => re.test(t))) continue;
    const aliasKey = cableMatchKey(mark);
    const hit = pick(recs.filter((r) => markKey(r) === aliasKey));
    if (hit) return hit;
  }
  const text = keys.join("|");
  const contained = recs.filter((r) => { const k = markKey(r); return k.length >= 4 && text.includes(k); });
  const longest = contained.reduce((n, r) => Math.max(n, markKey(r).length), 0);
  return pick(contained.filter((r) => markKey(r).length === longest));
}
