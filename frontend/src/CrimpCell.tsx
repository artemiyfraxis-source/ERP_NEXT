import type { CSSProperties } from "react";

// Обжим контакта по сечениям: один компонент для вкладки «Терминалы» и карточки серии разъёмов.
// Текст приходит с сервера в одном виде («28 AWG: 0,52–0,58; 26 AWG: 0,56–0,62», см. normalize_crimp_text);
// здесь он режется на строки «сечение │ проводник │ изоляция │ отрыв» — сечение написано один раз.

export const CRIMP_COLUMN_TITLE = "Обжим по AWG";

const LABELLED = /^((?:\d+(?:–\d+)? AWG)|(?:[\d,]+(?:–[\d,]+)? мм[2²]))\s*:\s*(.*)$/;

/** Сечение → значение; значения без сечения («0,85–0,90») лежат под пустым ключом. */
export function parseCrimp(text: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const part of text.split(/;|\n/).map((item) => item.trim()).filter((item) => item && item !== "—")) {
    const match = LABELLED.exec(part);
    const key = match ? match[1] : "";
    const value = match ? match[2] : part;
    out.set(key, out.has(key) ? `${out.get(key)} / ${value}` : value);
  }
  return out;
}

export interface CrimpValues { conductor?: string; insulation?: string; pull?: string }

const PARTS = [
  { key: "conductor", title: "высота обжима проводника, мм" },
  { key: "insulation", title: "высота обжима изоляции, мм" },
  { key: "pull", title: "усилие отрыва, Н" },
] as const;

/** Одна строка текста для поиска, сортировки и выгрузки. */
export function crimpText(values: CrimpValues): string {
  return PARTS.map((part) => (values[part.key] ?? "").trim()).filter(Boolean).join(" | ");
}

export function CrimpCell({ values }: { values: CrimpValues }) {
  const parsed = PARTS.map((part) => ({ ...part, map: parseCrimp(values[part.key] ?? "") })).filter((part) => part.map.size > 0);
  if (parsed.length === 0) return <span className="muted">—</span>;
  const keys: string[] = [];
  for (const part of parsed) for (const key of part.map.keys()) if (!keys.includes(key)) keys.push(key);
  // Значение без сечения («8» к «30 AWG: 0,52–0,56») относится ко всем сечениям строки, отдельной строкой не идёт.
  const withAwg = keys.some((key) => key !== "");
  if (withAwg) keys.splice(0, keys.length, ...keys.filter((key) => key !== ""));
  return <span className="crimp" style={{ "--crimp-n": parsed.length } as CSSProperties}>
    {keys.map((key) => <span key={key} className={withAwg ? "crimp-row has-awg" : "crimp-row"}>
      {withAwg && <b className="crimp-awg">{key}</b>}
      {parsed.map((part) => {
        const value = part.map.get(key) ?? (withAwg ? part.map.get("") : undefined);
        return <span key={part.key} className="crimp-val" title={part.title}>{value ?? "—"}{value && part.key === "pull" && <i className="crimp-unit"> Н</i>}</span>;
      })}
    </span>)}
  </span>;
}
