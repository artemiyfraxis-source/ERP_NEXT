/*
 * «№ изделия» в надписи метки по КД (генератор «Маркировка жгута» → ЭТК → задание). Надпись хранится как есть в
 * `op.marks[].text`, номер изделия в ней — метка-заполнитель:
 *   `{№}`   — номер без нулей: КБ1-{№} → КБ1-1, КБ1-2 …
 *   `{№:3}` — номер в 3 цифры с нулями: КБ1-{№:3} → КБ1-001, КБ1-002 … (ширина 1…6)
 * Любое другое написание («{№:x}», «{N}», «№ 1») — обычный текст, надпись без номера (старые карты не меняются).
 * Номера даёт задание: `MarkSerialRange` — первый и последний номер изделий задания (сервер, production
 * `mark_serial_range`). Без задания (техкарта, шаблон) номер не известен — «№ по партии».
 */

/** Как номер вставляется кнопкой «№ изделия». */
export const MARK_SERIAL = "{№}";
/** Ширины номера в выборе «Цифр»: 0 — без нулей. */
export const MARK_SERIAL_WIDTHS = [0, 2, 3, 4] as const;
/** Без задания номер изделия не известен. */
export const MARK_SERIAL_UNKNOWN = "№ по партии";

const TOKEN = /\{№(?::([1-6]))?\}/g;

/** Номера изделий задания: первый и последний (включительно); `task` — номер задания для печати. */
export type MarkSerialRange = { first: number; last: number; task?: string };

export function hasMarkSerial(text: string | undefined): boolean {
  return new RegExp(TOKEN.source).test(String(text ?? ""));
}

/** Ширина номера первой метки-заполнителя: 0 — без нулей (и когда номера нет). */
export function markSerialWidth(text: string): number {
  const found = new RegExp(TOKEN.source).exec(text);
  return found?.[1] ? Number(found[1]) : 0;
}

/** Все номера надписи — в `width` цифр (0 — без нулей). */
export function withMarkSerialWidth(text: string, width: number): string {
  const token = width > 0 ? `{№:${Math.min(Math.max(Math.trunc(width), 1), 6)}}` : MARK_SERIAL;
  return text.replace(TOKEN, token);
}

/** Вставка номера в позицию курсора (нет позиции — в конец); ширина — как у номера, что уже есть в надписи. */
export function insertMarkSerial(text: string, at?: number | null): { text: string; caret: number } {
  const position = at === undefined || at === null ? text.length : Math.min(Math.max(at, 0), text.length);
  const width = markSerialWidth(text);
  const token = width ? `{№:${width}}` : MARK_SERIAL;
  return { text: text.slice(0, position) + token + text.slice(position), caret: position + token.length };
}

/** Надпись изделия `n`: каждый номер — `n`, с нулями до своей ширины. */
export function expandMarkSerial(text: string, n: number): string {
  return text.replace(TOKEN, (_all, width: string | undefined) => String(n).padStart(width ? Number(width) : 0, "0"));
}

/** Надпись как шаблон для чтения: номер любой ширины — «{№}». */
export function markSerialTemplate(text: string): string {
  return text.replace(TOKEN, MARK_SERIAL);
}

/** Диапазон надписей: «КБ1-001 … КБ1-010», два номера через запятую, один — сам; без задания — «№ по партии». */
export function markSerialSpan(text: string, range?: MarkSerialRange | null): string {
  if (!range || range.last < range.first) return MARK_SERIAL_UNKNOWN;
  const first = expandMarkSerial(text, range.first);
  if (range.last === range.first) return first;
  const last = expandMarkSerial(text, range.last);
  return range.last === range.first + 1 ? `${first}, ${last}` : `${first} … ${last}`;
}

/** Надпись в ЭТК: без номера — как была (байт в байт); с номером — «КБ1-{№} → КБ1-001 … КБ1-010». */
export function markSerialText(text: string, range?: MarkSerialRange | null): string {
  return hasMarkSerial(text) ? `${markSerialTemplate(text)} → ${markSerialSpan(text, range)}` : text;
}

/** Надписи изделий задания по порядку номеров. */
export function markSerialList(text: string, range: MarkSerialRange): string[] {
  const count = Math.max(range.last - range.first + 1, 0);
  return Array.from({ length: count }, (_, index) => expandMarkSerial(text, range.first + index));
}
