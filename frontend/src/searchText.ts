/* Поиск по базам (владелец 16.10.2026: «настроить нормальный поиск во всех базах»). Кладовщик и технолог набирают
   марку или артикул как придётся: RG58 / RG-58 / rg 58, 0511910600 / 051191-0600, SHR-02V-S-B / SHR02VSB, МГТФ
   латинской M, 0,35 вместо 0.35. Строка и запрос приводятся к одному ключу: регистр не важен, буквы-двойники кириллицы
   и латиницы (А/A, В/B, Е/E, К/K, М/M, Н/H, О/O, Р/P, С/C, Т/T, У/Y, Х/X) — одна буква, десятичная запятая — точка, пробелы, дефисы, точки, запятые,
   косые черты и подчёркивания внутри марки не считаются. Слова запроса — в любом порядке, каждое должно найтись в одной
   из ячеек строки. Тот же ключ — на сервере (backend references/search_text.py), чтобы поиск в окнах выбора записи
   находил то же, что поиск таблицы. */

// Кириллица → латинский двойник (после перевода в нижний регистр): «С» и «C» в запросе и в ячейке — одна буква.
const TWINS: Record<string, string> = {
  а: "a", в: "b", е: "e", ё: "e", к: "k", м: "m", н: "h", о: "o", р: "p", с: "c", т: "t", у: "y", х: "x",
};
// Разделители внутри марки и артикула: пробелы (и неразрывный), дефисы и тире, точка, запятая, косые, подчёркивание.
const SEPARATORS = /[\s\-‐‑‒–—−_.,/\\·]+/gu;
// Десятичная запятая или точка между цифрами — точка, и она остаётся в ключе: «0,35» = «0.35», но «3,00 - 3,50» не
// склеивается в «300350» и не находится по «0,35».
const DECIMAL = /(\d)[.,](?=\d)/gu;
const POINT = "";

const cache = new Map<string, string>();
const CACHE_LIMIT = 50_000;

/** Ключ поиска строки: нижний регистр, двойники латиницей, без разделителей («RG-58» → «rg58», «МГТФ 0,35» → «mгtф0.35»). */
export function searchKey(value: unknown): string {
  const text = String(value ?? "");
  if (!text) return "";
  const known = cache.get(text);
  if (known !== undefined) return known;
  let out = "";
  const marked = text.toLocaleLowerCase("ru-RU").replace(DECIMAL, `$1${POINT}`).replace(SEPARATORS, "");
  for (const char of marked) out += char === POINT ? "." : TWINS[char] ?? char;
  if (cache.size >= CACHE_LIMIT) cache.clear();
  cache.set(text, out);
  return out;
}

/** Слова запроса в виде ключей; пустые (запрос из одних пробелов или дефисов) отброшены. */
export function searchWords(query: string): string[] {
  return String(query ?? "").split(/\s+/u).map(searchKey).filter(Boolean);
}

/** Каждое слово запроса — хотя бы в одной ячейке; пустой запрос подходит всем. */
export function matchesSearch(values: readonly unknown[], query: string): boolean {
  const words = searchWords(query);
  if (!words.length) return true;
  const cells = values.map(searchKey).filter(Boolean);
  return words.every((word) => cells.some((cell) => cell.includes(word)));
}

/** Строки, подходящие под запрос. Запрос из нескольких слов — сперва как одна марка («rg 58» — это RG58 / RG-58, а не
    любая строка с «RG» и где-то «58»): есть строки, где он целиком в одной ячейке, — только они; нет — слова в любом
    порядке, каждое в своей ячейке («molex 5557», «0,35 мгтф»). */
export function searchRows<T>(rows: readonly T[], values: (row: T) => readonly unknown[], query: string): T[] {
  const words = searchWords(query);
  if (!words.length) return [...rows];
  if (words.length > 1) {
    const joined = words.join("");
    const whole = rows.filter((row) => values(row).some((value) => searchKey(value).includes(joined)));
    if (whole.length) return whole;
  }
  return rows.filter((row) => {
    const cells = values(row).map(searchKey).filter(Boolean);
    return words.every((word) => cells.some((cell) => cell.includes(word)));
  });
}
