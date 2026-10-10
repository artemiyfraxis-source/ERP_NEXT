/* Даты на экранах — по-русски: 01.10.2026 и 01.10.2026 14:30, никогда ISO 2026-10-01 (skill erp-screens §2). */

const ISO_DAY = /^(\d{4})-(\d{2})-(\d{2})$/;
const ISO_MOMENT = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/;

/** «2026-10-01» → «01.10.2026»; дата со временем → «01.10.2026 14:30» по местному времени. Иное — как есть. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "";
  const day = ISO_DAY.exec(value);
  if (day) return `${day[3]}.${day[2]}.${day[1]}`;
  if (!ISO_MOMENT.test(value)) return value;
  const moment = new Date(value);
  if (Number.isNaN(moment.getTime())) return value;
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${pad(moment.getDate())}.${pad(moment.getMonth() + 1)}.${moment.getFullYear()} ${pad(moment.getHours())}:${pad(moment.getMinutes())}`;
}

/** Момент плана коротко: «01.10 14:30» по местному времени; пусто — пустая строка. */
export function formatMoment(value: string | null | undefined): string {
  if (!value) return "";
  const moment = new Date(value);
  if (Number.isNaN(moment.getTime())) return value;
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${pad(moment.getDate())}.${pad(moment.getMonth() + 1)} ${pad(moment.getHours())}:${pad(moment.getMinutes())}`;
}

/** Только день: «2026-10-01T00:00:00+03:00» → «01.10.2026»; не ISO — как есть, пусто — пустая строка. */
export function formatDay(value: string | null | undefined): string {
  const match = String(value ?? "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match ? `${match[3]}.${match[2]}.${match[1]}` : String(value ?? "");
}

/** Ячейка таблицы: ISO-дату показываем по-русски, остальное не трогаем. */
export function displayCell<T>(value: T): T | string {
  return typeof value === "string" ? formatDate(value) : value;
}
