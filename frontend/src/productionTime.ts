import { useEffect, useState } from "react";
import type { ProductionWorkSession } from "./api";

function parseDate(value: string | null | undefined) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Текущее время, обновляемое раз в period мс: таймер начатой работы на карточке. */
export function useNow(period: number): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), period);
    return () => window.clearInterval(timer);
  }, [period]);
  return now;
}

/** Часы:минуты длительности: 5880 с → «1:38». */
export function clock(seconds: number): string {
  const minutes = Math.max(0, Math.floor((Number(seconds) || 0) / 60));
  return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}`;
}

/** Сколько сессия проработала к now: от начала без пауз (идущая пауза тоже не считается). */
export function workedSeconds(session: ProductionWorkSession, now: Date): number {
  const started = parseDate(session.started_at);
  if (!started) return 0;
  const pausedSince = session.status === "Пауза" ? parseDate(session.pause_started_at) : null;
  const end = pausedSince ?? now;
  return Math.max(0, Math.round((end.getTime() - started.getTime()) / 1000) - (Number(session.pause_seconds) || 0));
}

