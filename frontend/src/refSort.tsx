/* Порядок строк по умолчанию в «Базе данных» (владелец 25.10.2026: «а мы можем вот так сортировку сделать везде»):
   размер как число (десятичная запятая, пустые — в конце), затем наименование с числами как числами («2x0,5» < «10x0,5»).
   Щелчок по заголовку столбца сортирует иначе; «Сбросить вид» возвращает этот порядок. */

/** Первое число текста: «0,35» → 0,35, «2x0,5» → 2, «Cat6» → 6; нет числа — null. */
export function firstNumber(value: unknown): number | null {
  const match = String(value ?? "").replace(/\s/g, "").replace(",", ".").match(/\d+(\.\d+)?/);
  return match ? Number(match[0]) : null;
}

/** Числа по возрастанию, пустые (null) — в конце. */
export const compareNumbers = (a: number | null | undefined, b: number | null | undefined): number =>
  a === null || a === undefined ? (b === null || b === undefined ? 0 : 1) : b === null || b === undefined ? -1 : a - b;

/** Имена по алфавиту, числа внутри — как числа; пустое имя — в конце. */
export function compareNames(a: unknown, b: unknown): number {
  const [left, right] = [String(a ?? "").trim(), String(b ?? "").trim()];
  if (!left || !right) return Number(!left) - Number(!right);
  return left.localeCompare(right, "ru", { numeric: true, sensitivity: "base" });
}

/** Сечение контакта по AWG, мм² (для строк без сечения в мм²): d = 0,127 · 92^((36 − n)/39). */
export const awgToMm2 = (awg: number): number => Math.PI / 4 * (0.127 * 92 ** ((36 - awg) / 39)) ** 2;

const ETHERNET_CATEGORIES = ["Cat5", "Cat5e", "Cat6", "Cat6A", "Cat7", "Cat7A", "Cat8"];
/** Место категории Ethernet («Cat6a», «категория 5e») в порядке Cat5 < Cat5e < Cat6 < Cat6A < Cat7 < Cat7A < Cat8; нет — null. */
export function ethernetCategoryRank(value: unknown): number | null {
  const match = String(value ?? "").match(/cat(?:egory)?\.?\s?(5e|5|6a|6|7a|7|8)(?![0-9])/i) ?? String(value ?? "").match(/категори[яи]\s?(5e|5|6a|6|7a|7|8)/i);
  if (!match) return null;
  const name = `Cat${match[1].toUpperCase().replace("E", "e")}`;
  return ETHERNET_CATEGORIES.indexOf(name);
}

/** Подсказка порядка списков кабелей (ключ — вкладка: coax, wire, multi, lan). */
export const CABLE_SORT_HINT: Record<"coax" | "wire" | "multi" | "lan", string> = {
  coax: "наружный диаметр, затем марка",
  wire: "сечение, затем марка и цвет",
  multi: "сечение, затем число жил и марка",
  lan: "категория, затем число пар и марка",
};

/** Строка подсказки над таблицей: «Сортировка: сечение, затем марка». */
export function SortHint({ text }: { text: string }) {
  return <span className="muted sort-hint">Сортировка: {text}</span>;
}
