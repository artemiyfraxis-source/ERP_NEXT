import type { ReactNode } from "react";
import { statusLabel, statusTone, type Tone } from "./statusTone";

/* Чип статуса — один вид на всё приложение (аудит дизайна 10.10, Общ-1): короткое русское слово на приглушённом фоне
   своего тона. Тон и подпись берутся из словаря `statusTone.ts`; экран может задать тон или подпись сам, если слово
   не статус (например, «ДЛ 04.11» с тоном срока). */

export interface StatusChipProps {
  /** Слово статуса, как его отдал сервер; пусто — «—». */
  status?: string | null;
  /** Тон вместо словарного. */
  tone?: Tone;
  /** Подпись вместо словарной (тон всё равно по `status`, если не задан `tone`). */
  children?: ReactNode;
  /** Подсказка; по умолчанию — исходное слово, если на экране показано слово ERP вместо слова листа. */
  title?: string;
  /** Сколько раз статус встречается (×2 рядом с чипом), показывается от 2. */
  count?: number;
  className?: string;
}

export function StatusChip({ status, tone, children, title, count, className }: StatusChipProps) {
  const word = String(status ?? "").trim();
  const classes = ["status-chip", className].filter(Boolean).join(" ");
  if (!word && children === undefined) {
    return <span className={`${classes} is-empty`} data-tone={tone ?? "neutral"} title={title}>—</span>;
  }
  const label = statusLabel(word);
  return (
    <span className={classes} data-tone={tone ?? statusTone(word)} title={title ?? (label !== word ? word : undefined)}>
      {children ?? label}
      {count !== undefined && count > 1 && <span className="status-chip-count">×{count}</span>}
    </span>
  );
}
