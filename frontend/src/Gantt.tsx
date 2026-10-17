import { useState, type ReactNode } from "react";

// Shared timeline (SUPPLYMAP PortalApp prodGantt_ / prodWeekNav_ / prodX_): a 21-day scale from the
// Monday of the shown week, a working day 9:00–17:30 fills its whole cell, weekends and holidays are
// shaded, today has a left edge. Used by the production plan; the resource-planning task and
// operation Gantts reuse it with their own rows. The scale stretches to the width of the screen (a day is at
// least `dayWidth` px, then the grid scrolls sideways).

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
  /** Minimal day width in px; wider screens stretch the scale to their full width. */
  dayWidth?: number;
  /** One plaque per row and day: short bars of one day merge into «3 опер. · …» (people load). */
  mergeDay?: boolean;
  /** Week offset from the current one; omit to let the component keep it. */
  week?: number;
  onWeekChange?: (week: number) => void;
  /** Injected clock for tests. */
  now?: Date;
  empty?: ReactNode;
}

const DAY_NAMES = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
const dayMonth = (day: Date) => `${String(day.getDate()).padStart(2, "0")}.${String(day.getMonth() + 1).padStart(2, "0")}`;
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

const TONE_RANK: Record<string, number> = { late: 3, partial: 2, new: 2, busy: 1, default: 1, "": 1, done: 0 };

/** People load: bars of one row that start and finish on the same day become one plaque over that whole day
 *  («9 оп. · 2 ч · …», worst tone wins), so a day of short operations is readable; the exact windows stay
 *  in the tooltip. Multi-day bars and absences are kept as they are. */
function timeRange(bar: GanttBar) {
  const hm = (value: string | null) => { const moment = parseMoment(value); return moment ? `${moment.getHours()}:${String(moment.getMinutes()).padStart(2, "0")}` : ""; };
  return `${hm(bar.start)}–${hm(bar.finish)}`;
}

export function mergeDayBars(bars: GanttBar[]): GanttBar[] {
  const out: GanttBar[] = [];
  const days = new Map<string, GanttBar[]>();
  for (const bar of bars) {
    const from = parseMoment(bar.start), to = parseMoment(bar.finish);
    if (!from || !to || bar.tone === "absent" || isoDay(from) !== isoDay(new Date(to.getTime() - 1))) { out.push(bar); continue; }
    const key = isoDay(from);
    if (!days.has(key)) days.set(key, []);
    days.get(key)!.push(bar);
  }
  for (const [key, group] of days) {
    const day = parseMoment(key)!;
    const next = isoDay(new Date(day.getFullYear(), day.getMonth(), day.getDate() + 1));
    const tone = group.reduce<GanttTone>((worst, bar) => (TONE_RANK[bar.tone ?? ""] > TONE_RANK[worst] ? bar.tone ?? "default" : worst), "done");
    const texts = [...new Set(group.map((bar) => bar.text).filter(Boolean))];
    const minutes = group.reduce((sum, bar) => sum + (parseMoment(bar.finish)!.getTime() - parseMoment(bar.start)!.getTime()) / 60_000, 0);
    const hours = `${String(Number((minutes / 60).toFixed(1))).replace(".", ",")} ч`;
    const title = group.map((bar) => `${timeRange(bar)} ${bar.title || bar.text || ""}`.trim()).join("\n");
    out.push({
      start: key, finish: next, tone, title,
      text: group.length === 1 ? `${texts[0] ?? ""} · ${hours}` : `${group.length} оп. · ${hours} · ${texts.join(", ")}`,
    });
  }
  return out;
}

export function Gantt({
  rows, labelHead, holidays = [], days = 21, dayWidth = 34, mergeDay = false, week, onWeekChange, now, empty,
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
  // Positions in % of the track: the track fills the free width, never narrower than days × dayWidth.
  const pct = (value: number) => `${(value / days) * 100}%`;
  const background = scale.map((day, index) => {
    const holiday = day.getDay() === 0 || day.getDay() === 6 || off.has(isoDay(day));
    const isToday = day.getTime() === today;
    if (!holiday && !isToday) return null;
    return <i key={index} className={`gantt-bg${holiday ? " off" : ""}${isToday ? " today" : ""}`}
      data-day={isoDay(day)} style={{ left: pct(index), width: pct(1) }} />;
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
      style={{ left: pct(a), width: `max(4px, ${pct(z - a)})` }}>{bar.text ?? ""}</span>;
  }

  function drawDeadline(deadline: string | null | undefined) {
    const end = deadlineEnd(deadline);
    if (!end) return null;
    const x = dayOffset(end, day0);
    if (x <= 0 || x > days) return null;
    return <span className="gantt-mark" data-testid="gantt-deadline" title={`Дедлайн ${dayMonth(parseMoment(deadline)!)}`} style={{ left: `calc(${pct(x)} - 2px)` }} />;
  }

  return <div className="gantt">
    <div className="gantt-nav" role="group" aria-label="Неделя">
      <span className="gantt-period">{dayMonth(scale[0])}–{dayMonth(scale[scale.length - 1])}</span>
      <button type="button" className="secondary small" onClick={() => setWeek(shown - 1)}>← неделя</button>
      <button type="button" className="secondary small" onClick={() => setWeek(0)} disabled={shown === 0}>Сегодня</button>
      <button type="button" className="secondary small" onClick={() => setWeek(shown + 1)}>неделя →</button>
    </div>
    {rows.length ? <div className="gantt-wrap"><div className="gantt-grid">
      <div className="gantt-row gantt-head">
        <div className="gantt-label">{labelHead}</div>
        <div className="gantt-track" style={{ minWidth: width }}>{background}{scale.map((day, index) => (
          <span key={index} className="gantt-day" style={{ left: pct(index), width: pct(1) }}>
            {DAY_NAMES[day.getDay()]}<br />{dayMonth(day)}
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
          <div className="gantt-track" style={{ minWidth: width }}>
            {background}
            {(mergeDay ? mergeDayBars(row.bars) : row.bars).map((bar, index) => drawBar(bar, index, row.deadline))}
            {drawDeadline(row.deadline)}
          </div>
        </div>;
      })}
    </div></div> : empty ?? null}
  </div>;
}
