// Обжим контакта по сечениям: один компонент для вкладки «Терминалы» и карточки серии разъёмов.
// Текст приходит с сервера в одном виде («28 AWG: 0,52–0,58; 26 AWG: 0,56–0,62», см. normalize_crimp_text);
// здесь он режется на строки «сечение │ по жиле │ по изоляции │ отрыв» — сечение написано один раз.
// Owner 16.10: в ячейке только число или диапазон, всегда три колонки значений; пояснения старых записей
// («2,80–3,35 (справочно…)», «1,55 (UL1061) / 1,65 (UL1007)») уходят во всплывающую подсказку.

export const CRIMP_COLUMN_TITLE = "Обжим: AWG │ по жиле, мм │ по изоляции, мм │ отрыв, Н";

const LABELLED = /^((?:\d+(?:–\d+)? AWG)|(?:[\d,]+(?:–[\d,]+)? мм[2²]))\s*:\s*(.*)$/;

/** Части записи через «;» или перевод строки; разделитель внутри скобок частью не считается. */
function splitParts(text: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let current = "";
  for (const char of text) {
    if (char === "(") depth += 1;
    if (char === ")") depth = Math.max(0, depth - 1);
    if (depth === 0 && (char === ";" || char === "\n")) { parts.push(current); current = ""; } else current += char;
  }
  parts.push(current);
  return parts.map((item) => item.trim()).filter((item) => item && item !== "—");
}

/** Сечение → значение; значения без сечения («0,85–0,90») лежат под пустым ключом. */
export function parseCrimp(text: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const part of splitParts(text)) {
    const match = LABELLED.exec(part);
    const key = match ? match[1] : "";
    const value = match ? match[2] : part;
    out.set(key, out.has(key) ? `${out.get(key)} / ${value}` : value);
  }
  return out;
}

// Число значения: не часть обозначения (UL1061, T-DS1071, мм2) — перед ним нет буквы или цифры.
const NUMBER = /(?<![\p{L}\d.,])\d+(?:[.,]\d+)?/gu;

/** Чистое значение записи: число «0,65» или диапазон «0,65–0,70» от меньшего к большему; без чисел — "". */
export function cleanCrimpValue(raw: string): string {
  const text = raw.replace(/\([^)]*\)?/g, " ");
  const numbers = (text.match(NUMBER) ?? []).map((item) => ({ text: item.replace(".", ","), value: Number(item.replace(",", ".")) }));
  if (numbers.length === 0) return "";
  const low = numbers.reduce((best, item) => (item.value < best.value ? item : best));
  const high = numbers.reduce((best, item) => (item.value > best.value ? item : best));
  return low.value === high.value ? low.text : `${low.text}–${high.text}`;
}

export interface CrimpValues { conductor?: string; insulation?: string; pull?: string }

const PARTS = [
  { key: "conductor", title: "Высота обжима по жиле, мм" },
  { key: "insulation", title: "Высота обжима по изоляции, мм" },
  { key: "pull", title: "Усилие отрыва, Н" },
] as const;

/** Одна строка текста для поиска, сортировки и выгрузки — исходные записи, чтобы искалось и «UL1007». */
export function crimpText(values: CrimpValues): string {
  return PARTS.map((part) => (values[part.key] ?? "").trim()).filter(Boolean).join(" | ");
}

export function CrimpCell({ values }: { values: CrimpValues }) {
  const parsed = PARTS.map((part) => ({ ...part, map: parseCrimp(values[part.key] ?? "") }));
  if (parsed.every((part) => part.map.size === 0)) return <span className="muted">—</span>;
  const keys: string[] = [];
  for (const part of parsed) for (const key of part.map.keys()) if (!keys.includes(key)) keys.push(key);
  // Значение без сечения («8» к «30 AWG: 0,52–0,56») относится ко всем сечениям строки, отдельной строкой не идёт.
  const withAwg = keys.some((key) => key !== "");
  if (withAwg) keys.splice(0, keys.length, ...keys.filter((key) => key !== ""));
  // Колонка сечения есть всегда (пустая у записей без AWG), значения — всегда три: столбцы ровные во всей таблице.
  return <span className="crimp">
    {keys.map((key) => <span key={key} className={withAwg ? "crimp-row has-awg" : "crimp-row"}>
      {withAwg ? <b className="crimp-awg" title={key}>{key}</b> : <span className="crimp-awg-gap" aria-hidden="true" />}
      {parsed.map((part) => {
        const raw = (part.map.get(key) ?? (withAwg ? part.map.get("") : undefined))?.trim() ?? "";
        const shown = raw ? cleanCrimpValue(raw) : "";
        // Подсказка — исходная запись целиком: пояснение старой записи и значение, если ячейка его обрезала.
        const title = raw ? `${part.title}: ${raw}` : part.title;
        return <span key={part.key} className={shown ? "crimp-val" : "crimp-val crimp-none"} title={title}>
          {shown || "—"}{shown && part.key === "pull" && <i className="crimp-unit"> Н</i>}
        </span>;
      })}
    </span>)}
  </span>;
}
