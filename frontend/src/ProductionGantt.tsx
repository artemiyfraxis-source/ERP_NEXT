import type { ReactNode } from "react";

// Шкала Ганта ресурсного планирования — перенос prodGantt_ (SUPPLYMAP PortalApp.html): 21 день от понедельника
// выбранной недели, рабочий день 9:00–17:30 занимает всю клетку, выходные и праздники затенены, сегодня выделено.
export const GANTT_DAYS = 21;
const DAY_WIDTH = 34;
const WEEKDAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];

export type GanttBar = { from: Date | null; to: Date | null; text?: string; tone?: string; title?: string };
export type GanttRow = {
  key: string;
  label: ReactNode;
  bars?: GanttBar[];
  marks?: Date[];
  site?: boolean;
  className?: string;
  onClick?: () => void;
};

export function ganttDay0(week: number, today = new Date()) {
  const day = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  day.setDate(day.getDate() - ((day.getDay() + 6) % 7) + 7 * week);
  return day;
}

function isoDay(day: Date) {
  return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, "0")}-${String(day.getDate()).padStart(2, "0")}`;
}

function position(moment: Date, day0: Date) {
  const days = Math.round((new Date(moment.getFullYear(), moment.getMonth(), moment.getDate()).getTime() - day0.getTime()) / 86_400_000);
  return days + Math.min(1, Math.max(0, (moment.getHours() * 60 + moment.getMinutes() - 540) / 510));
}

export function GanttWeekNav({ week, setWeek }: { week: number; setWeek: (value: number) => void }) {
  return <div className="pg-nav">
    <button className="secondary small" onClick={() => setWeek(week - 1)}>◀ неделя</button>
    <button className="secondary small" onClick={() => setWeek(0)}>сегодня</button>
    <button className="secondary small" onClick={() => setWeek(week + 1)}>неделя ▶</button>
  </div>;
}

export function ProductionGantt({ rows, labelHead, week, holidays = [], today = new Date() }: {
  rows: GanttRow[];
  labelHead: string;
  week: number;
  holidays?: string[];
  today?: Date;
}) {
  const day0 = ganttDay0(week, today);
  const todayKey = isoDay(today);
  const off = new Set(holidays);
  const days = Array.from({ length: GANTT_DAYS }, (_, index) => new Date(day0.getFullYear(), day0.getMonth(), day0.getDate() + index));
  const width = GANTT_DAYS * DAY_WIDTH;
  const background = days.map((day, index) => {
    const weekend = day.getDay() === 0 || day.getDay() === 6 || off.has(isoDay(day));
    const now = isoDay(day) === todayKey;
    return weekend || now ? <i key={index} className={`pg-bg${weekend ? " off" : ""}${now ? " today" : ""}`} style={{ left: index * DAY_WIDTH, width: DAY_WIDTH }} /> : null;
  });
  return <div className="pg-wrap"><div className="pg">
    <div className="pg-row pg-head"><div className="pg-lab">{labelHead}</div><div className="pg-track" style={{ width }}>{background}{days.map((day, index) => <span key={index} className="pg-day" style={{ left: index * DAY_WIDTH, width: DAY_WIDTH }}>{WEEKDAYS[day.getDay()]}<br />{String(day.getDate()).padStart(2, "0")}.{String(day.getMonth() + 1).padStart(2, "0")}</span>)}</div></div>
    {rows.map((row) => <div
      key={row.key}
      className={`pg-row ${row.site ? "pg-site" : ""} ${row.onClick ? "pg-click" : ""} ${row.className ?? ""}`}
      onClick={row.onClick}
      role={row.onClick ? "button" : undefined}
    >
      <div className="pg-lab">{row.label}</div>
      <div className="pg-track" style={{ width }}>{background}{!row.site && <>
        {(row.bars ?? []).map((bar, index) => {
          if (!bar.from || !bar.to) return null;
          const start = Math.max(0, position(bar.from, day0));
          const finish = Math.min(GANTT_DAYS, position(bar.to, day0));
          if (finish <= 0 || start >= GANTT_DAYS || finish < start) return null;
          return <span key={index} className={`pg-bar ${bar.tone ?? ""}`} title={bar.title} style={{ left: start * DAY_WIDTH, width: Math.max(4, (finish - start) * DAY_WIDTH) }}>{bar.text}</span>;
        })}
        {(row.marks ?? []).map((mark, index) => {
          const at = position(mark, day0);
          return at > 0 && at <= GANTT_DAYS ? <span key={`m${index}`} className="pg-mark" style={{ left: at * DAY_WIDTH - 2 }} /> : null;
        })}
      </>}</div>
    </div>)}
  </div></div>;
}
