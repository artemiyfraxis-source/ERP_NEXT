import type { ReactNode } from "react";

/** Small pieces the «Запросы КП» screens share: number formats and the dialog frame. Numbers come as strings. */

export const number = (value: string | number | null | undefined, digits = 2) =>
  value === null || value === undefined || value === ""
    ? "—"
    : Number(value).toLocaleString("ru-RU", { minimumFractionDigits: digits, maximumFractionDigits: digits });
export const money = (value: string | number | null | undefined) => (value === null || value === undefined ? "—" : `${number(value)} ₽`);
export const quantity = (value: string | number | null | undefined) =>
  value === null || value === undefined ? "—" : Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 6 });
export const decimalInput = (value: string) => value.replace(/\s/g, "").replace(",", ".");
export const localToday = () => {
  const now = new Date();
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
};

export function Dialog({ title, wide = false, onClose, children }: { title: string; wide?: boolean; onClose: () => void; children: ReactNode }) {
  return (
    <div className="dialog-overlay" role="presentation" onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className={`panel move-dialog${wide ? " wide" : ""}`} role="dialog" aria-label={title}>
        <h2>{title}</h2>
        {children}
      </div>
    </div>
  );
}
