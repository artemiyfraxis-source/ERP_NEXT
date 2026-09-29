import { useState, type ReactNode } from "react";

// Shared timeline (SUPPLYMAP PortalApp prodGantt_ / prodWeekNav_ / prodX_): a 21-day scale from the
// Monday of the shown week, a working day 9:00–17:30 fills its whole cell, weekends and holidays are
// shaded, today has a left edge. Used by the production plan; the resource-planning task and
// operation Gantts reuse it with their own rows.

export type GanttTone = "default" | "late" | "partial" | "busy" | "new" | "done" | "absent";

export interface GanttBar {
  /** ISO date or date-time; a bar without both ends is not drawn. */
  start: string | null;
  finish: string | null;
  tone?: GanttTone;
  text?: string;
  title?: string;
}

export interface GanttRow {
  key: string;
  label: ReactNode;
  sublabel?: ReactNode;
  bars: GanttBar[];
  /** ISO date: red line at the end of that day; a bar finishing later is drawn as «позже дедлайна». */
  deadline?: string | null;
  note?: ReactNode;
  dim?: boolean;
  selected?: boolean;
  onClick?: () => void;
}

export interface GanttProps {
  rows: GanttRow[];
  labelHead: string;
  /** ISO dates (YYYY-MM-DD) of non-working days besides weekends — GET /api/production/calendar. */
  holidays?: string[];
  days?: number;
  dayWidth?: number;
  /** Week offset from the current one; omit to let the component keep it. */
  week?: number;
  onWeekChange?: (week: number) => void;
  /** Injected clock for tests. */
  now?: Date;
  empty?: ReactNode;
}

const DAY_NAMES = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
const DAY_START_MIN = 540;
const DAY_LENGTH_MIN = 510;

export function parseMoment(value: string | null | undefined): Date | null {
  if (!value) return null;
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (dateOnly) return new Date(+dateOnly[1], +dateOnly[2] - 1, +dateOnly[3]);
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function midnight(value: Date) {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate());
}

export function isoDay(value: Date) {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`;
}

/** Monday of the current week shifted by `week` weeks (prodDay0_). */
export function weekStart(now: Date, week: number) {
  const day = midnight(now);
  day.setDate(day.getDate() - ((day.getDay() + 6) % 7) + 7 * week);
  return day;
}

/** Moment → fractional day on the scale (prodX_). */
export function dayOffset(moment: Date, day0: Date) {
  const days = Math.round((midnight(moment).getTime() - day0.getTime()) / 86_400_000);
  const minutes = moment.getHours() * 60 + moment.getMinutes();
  return days + Math.min(1, Math.max(0, (minutes - DAY_START_MIN) / DAY_LENGTH_MIN));
}

/** End of the deadline day: the moment after which a finish is «позже дедлайна». */
export function deadlineEnd(deadline: string | null | undefined) {
  const day = parseMoment(deadline);
  return day ? new Date(day.getFullYear(), day.getMonth(), day.getDate() + 1) : null;
}

export function finishesLate(bar: GanttBar, deadline: string | null | undefined) {
  const end = deadlineEnd(deadline);
  const finish = parseMoment(bar.finish);
  return Boolean(end && finish && finish > end);
}

export function Gantt({
  rows, labelHead, holidays = [], days = 21, dayWidth = 34, week, onWeekChange, now, empty,
}: GanttProps) {
  const [ownWeek, setOwnWeek] = useState(0);
  const shown = week ?? ownWeek;
  const setWeek = (next: number) => { if (week === undefined) setOwnWeek(next); onWeekChange?.(next); };
  const clock = now ?? new Date();
  const day0 = weekStart(clock, shown);
  const today = midnight(clock).getTime();
  const off = new Set(holidays);
  const scale = Array.from({ length: days }, (_, index) => {
    const day = new Date(day0);
    day.setDate(day.getDate() + index);
    return day;
  });
  const width = days * dayWidth;
  const background = scale.map((day, index) => {
    const holiday = day.getDay() === 0 || day.getDay() === 6 || off.has(isoDay(day));
    const isToday = day.getTime() === today;
    if (!holiday && !isToday) return null;
    return <i key={index} className={`gantt-bg${holiday ? " off" : ""}${isToday ? " today" : ""}`}
      data-day={isoDay(day)} style={{ left: index * dayWidth, width: dayWidth }} />;
  });

  function drawBar(bar: GanttBar, index: number, deadline: string | null | undefined) {
    const from = parseMoment(bar.start);
    const to = parseMoment(bar.finish);
    if (!from || !to) return null;
    const a = Math.max(0, dayOffset(from, day0));
    const z = Math.min(days, dayOffset(to, day0));
    if (z <= 0 || a >= days || z < a) return null;
    const late = finishesLate(bar, deadline);
    const tone = late ? "late" : bar.tone && bar.tone !== "default" ? bar.tone : "";
    const title = `${bar.title ?? ""}${late ? " · позже дедлайна" : ""}`;
    return <span key={index} className={`gantt-bar ${tone}`.trim()} title={title}
      style={{ left: a * dayWidth, width: Math.max(4, (z - a) * dayWidth) }}>{bar.text ?? ""}</span>;
  }

  function drawDeadline(deadline: string | null | undefined) {
    const end = deadlineEnd(deadline);
    if (!end) return null;
    const x = dayOffset(end, day0);
    if (x <= 0 || x > days) return null;
    return <span className="gantt-mark" data-testid="gantt-deadline" title={`Дедлайн ${deadline}`} style={{ left: x * dayWidth - 2 }} />;
  }

  return <div className="gantt">
    <div className="gantt-nav" role="group" aria-label="Неделя">
      <button type="button" className="secondary small" onClick={() => setWeek(shown - 1)}>← неделя</button>
      <button type="button" className="secondary small" onClick={() => setWeek(0)}>сегодня</button>
      <button type="button" className="secondary small" onClick={() => setWeek(shown + 1)}>неделя →</button>
    </div>
    {rows.length ? <div className="gantt-wrap"><div className="gantt-grid">
      <div className="gantt-row gantt-head">
        <div className="gantt-label">{labelHead}</div>
        <div className="gantt-track" style={{ width }}>{background}{scale.map((day, index) => (
          <span key={index} className="gantt-day" style={{ left: index * dayWidth, width: dayWidth }}>
            {DAY_NAMES[day.getDay()]}<br />{String(day.getDate()).padStart(2, "0")}.{String(day.getMonth() + 1).padStart(2, "0")}
          </span>
        ))}</div>
      </div>
      {rows.map((row) => {
        const late = row.bars.some((bar) => finishesLate(bar, row.deadline));
        return <div key={row.key} data-testid="gantt-row"
          className={`gantt-row${row.onClick ? " clickable" : ""}${row.dim ? " dim" : ""}${row.selected ? " selected" : ""}`}
          onClick={row.onClick} role={row.onClick ? "button" : undefined} tabIndex={row.onClick ? 0 : undefined}
          onKeyDown={row.onClick ? (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); row.onClick?.(); } } : undefined}>
          <div className="gantt-label">
            {row.label}
            {row.sublabel && <div className="gantt-sub">{row.sublabel}</div>}
            {late && <span className="gantt-late">позже дедлайна</span>}
            {row.note && <div className="gantt-note">{row.note}</div>}
          </div>
          <div className="gantt-track" style={{ width }}>
            {background}
            {row.bars.map((bar, index) => drawBar(bar, index, row.deadline))}
            {drawDeadline(row.deadline)}
          </div>
        </div>;
      })}
    </div></div> : empty ?? null}
  </div>;
}
