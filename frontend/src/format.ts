/* Числа и даты на экранах — по-русски (skill erp-screens §2, аудит дизайна 10.10, Общ-8): 01.10.2026, 01.10.2026 14:30,
   01.10; 12 345,5 — разряды узким неразрывным пробелом, дробь через запятую; никогда ISO 2026-10-01 и 12345.5. */

const ISO_DAY = /^(\d{4})-(\d{2})-(\d{2})$/;
const ISO_MOMENT = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/;
const ISO_DAY_PREFIX = /^(\d{4})-(\d{2})-(\d{2})/;

/** Узкий неразрывный пробел — разделитель разрядов. */
export const THIN_SPACE = " ";
/** Неразрывный пробел — между числом и единицей. */
export const NBSP = " ";
/** Пустое значение в таблице и карточке. */
export const EMPTY = "—";

const MOMENT_FORMAT = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

function momentParts(moment: Date) {
  const parts: Record<string, string> = {};
  for (const part of MOMENT_FORMAT.formatToParts(moment)) parts[part.type] = part.value;
  return parts;
}

function parseMoment(value: string): Date | null {
  if (!ISO_MOMENT.test(value)) return null;
  const moment = new Date(value);
  return Number.isNaN(moment.getTime()) ? null : moment;
}

/** «2026-10-01» → «01.10.2026»; дата со временем → «01.10.2026 14:30» по местному времени. Иное — как есть. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "";
  const day = ISO_DAY.exec(value);
  if (day) return `${day[3]}.${day[2]}.${day[1]}`;
  const moment = parseMoment(value);
  if (!moment) return value;
  const p = momentParts(moment);
  return `${p.day}.${p.month}.${p.year} ${p.hour}:${p.minute}`;
}

/** То же, что `formatDate`: день — без времени, момент — с временем. Имя для мест, где время важно. */
export const formatDateTime = formatDate;

/** Момент плана коротко: «01.10 14:30» по местному времени; пусто — пустая строка. */
export function formatMoment(value: string | null | undefined): string {
  if (!value) return "";
  const moment = new Date(value);
  if (Number.isNaN(moment.getTime())) return value;
  const p = momentParts(moment);
  return `${p.day}.${p.month} ${p.hour}:${p.minute}`;
}

/** Только день: «2026-10-01T00:00:00+03:00» → «01.10.2026»; не ISO — как есть, пусто — пустая строка. */
export function formatDay(value: string | null | undefined): string {
  const match = String(value ?? "").match(ISO_DAY_PREFIX);
  return match ? `${match[3]}.${match[2]}.${match[1]}` : String(value ?? "");
}

/** День без года: «2026-10-01…» → «01.10»; не ISO — как есть. */
export function formatDayMonth(value: string | null | undefined): string {
  const match = String(value ?? "").match(ISO_DAY_PREFIX);
  return match ? `${match[3]}.${match[2]}` : String(value ?? "");
}

/** Ячейка таблицы: ISO-дату показываем по-русски, остальное не трогаем. */
export function displayCell<T>(value: T): T | string {
  return typeof value === "string" ? formatDate(value) : value;
}

const NUMERIC_TEXT = /^[-−]?\d+(?:[.,]\d+)?$/;

/** Число из числа или из строки сервера («400.000», «1,5», «12 345»); не число — null. */
export function toNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  const text = value.trim().replace(/[\s  ]/g, "");
  if (!NUMERIC_TEXT.test(text)) return null;
  const parsed = Number(text.replace("−", "-").replace(",", "."));
  return Number.isFinite(parsed) ? parsed : null;
}

const NUMBER_FORMATS = new Map<string, Intl.NumberFormat>();

function numberFormat(digits: number, minDigits: number) {
  const key = `${digits}:${minDigits}`;
  let format = NUMBER_FORMATS.get(key);
  if (!format) {
    format = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: digits, minimumFractionDigits: minDigits });
    NUMBER_FORMATS.set(key, format);
  }
  return format;
}

export interface NumberOptions {
  /** Сколько знаков после запятой самое большее (по умолчанию 3 — метры и килограммы). */
  digits?: number;
  /** Сколько знаков после запятой всегда (2 — для рублей). */
  minDigits?: number;
}

/** «12345.5» → «12 345,5» (Intl ru-RU, разряды узким пробелом); пусто — пустая строка; не число — как есть. */
export function formatNumber(value: number | string | null | undefined, options: NumberOptions = {}): string {
  if (value === null || value === undefined || value === "") return "";
  const number = toNumber(value);
  if (number === null) return String(value);
  const digits = options.digits ?? 3;
  const minDigits = Math.min(options.minDigits ?? 0, digits);
  return numberFormat(digits, minDigits).formatToParts(number)
    .map((part) => (part.type === "group" ? THIN_SPACE : part.value))
    .join("");
}

const UNIT_NAMES: Record<string, string> = {
  "м": "м", "мм": "мм", "см": "см", "км": "км", "шт": "шт", "кг": "кг", "г": "г", "л": "л", "мл": "мл",
  "уп": "уп", "компл": "компл", "к-т": "к-т", "пар": "пар", "рул": "рул", "бух": "бух", "лист": "лист",
  "м2": "м²", "м²": "м²", "п.м": "п. м", "пм": "п. м",
};

/** Единица по-русски строчными и без точки: «М.» → «м», «Шт.» → «шт»; незнакомая — как есть. */
export function unitLabel(unit: string | null | undefined): string {
  const text = String(unit ?? "").trim();
  const key = text.toLowerCase().replace(/\.$/, "");
  return UNIT_NAMES[key] ?? text;
}

/** «184.8», «М.» → «184,8 м» (число и единица через неразрывный пробел); без числа — пустая строка. */
export function formatQty(value: number | string | null | undefined, unit?: string | null, options?: NumberOptions): string {
  const number = formatNumber(value, options);
  if (!number) return "";
  const name = unitLabel(unit);
  return name ? `${number}${NBSP}${name}` : number;
}

/** Форма слова по числу: plural(2, ["партия", "партии", "партий"]) → «партии»; дробное — «партии» (1,5 партии). */
export function plural(count: number, forms: readonly [string, string, string]): string {
  if (!Number.isInteger(count)) return forms[1];
  const n = Math.abs(count) % 100;
  const last = n % 10;
  if (n > 10 && n < 20) return forms[2];
  if (last === 1) return forms[0];
  if (last >= 2 && last <= 4) return forms[1];
  return forms[2];
}

/** «1 партия», «5 партий», «12 345 позиций». */
export function countLabel(count: number, forms: readonly [string, string, string]): string {
  return `${formatNumber(count)}${NBSP}${plural(count, forms)}`;
}

const GLUED_KB = /^(.+?)\s*(КБ\s*\d+.*)$/i;

/** Сборка проекта: kbLabel("614К.1", "КБ1") → «614К.1 · КБ1»; слитное «614К.1КБ1» разделяется так же. */
export function kbLabel(project: string | null | undefined, index?: string | null): string {
  const left = String(project ?? "").trim();
  const right = String(index ?? "").trim();
  if (!right) {
    const glued = GLUED_KB.exec(left);
    return glued ? `${glued[1]} · ${glued[2]}` : left;
  }
  return left ? `${left} · ${right}` : right;
}
