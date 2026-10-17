import { useEffect, useLayoutEffect, useMemo, useState } from "react";
import { failureText, type ProductionShift } from "./api";
import { formatDayMonth } from "./format";
import { machineLoadApi, type MachineConflict, type MachineLoad, type MachineLoadDay, type MachineLoadItem, type MachineLoadRow } from "./machineLoadApi";
import { DEFAULT_SHIFT, workingDay } from "./MasterBoard";
import { LOAD_DAYS, LOAD_DAYS_WIDE, LOAD_THRESHOLDS, isoOf, loadDays } from "./PeopleLoad";
import { periodWords } from "./PlanLoadSheet";
import { windowText } from "./productionGraph";

/* «Загрузка станков» (владелец, 17.10: «в планировании надо учитывать загрузку станка»): станок × день, процент
   занятости всех его единиц из БД.ОБ. Занятость — операции ЭТК со станком в «Оборудовании» у запланированных долей
   (сервер, `production/machine_load.py`). Та же шкала, что у людей: частично — до 85 %, занят — 85–105 %, перегруз —
   больше 105 % или одновременно больше работ, чем единиц станка. */

/** Ширина блока, с которой сетка показывает четыре недели (как «Загрузка по людям»). */
const WIDE_FROM = 1600;
const WARNINGS_SHOWN = 3;

const oneDecimal = (value: number) => value.toLocaleString("ru-RU", { maximumFractionDigits: 1 });
const hoursOf = (seconds: number) => oneDecimal(seconds / 3600);
const weekdayOf = (day: string) => new Date(`${day}T00:00:00`).toLocaleDateString("ru-RU", { weekday: "short" });

export type MachineLevel = "off" | "free" | "low" | "full" | "over";

/** Уровень дня станка: выходной, свободен, частично, занят, перегруз. */
export function machineLevel(day: MachineLoadDay): MachineLevel {
  if (day.capacity_seconds <= 0) return day.busy_seconds > 0 ? "over" : "off";
  if (day.over) return "over";
  if (day.busy_seconds <= 0) return "free";
  return day.busy_seconds / day.capacity_seconds < LOAD_THRESHOLDS.full ? "low" : "full";
}

/** «ОБ.027 Пресс, 22.09: занято 3,5 из 14 ч (25 %) · одновременно 2 работы из 1 ед.» */
function dayTitle(row: MachineLoadRow, day: MachineLoadDay) {
  const head = `${row.label}, ${formatDayMonth(day.day)}: `;
  if (day.capacity_seconds <= 0 && !day.busy_seconds) return `${head}нерабочий день`;
  const peak = day.peak > row.units ? ` · одновременно ${day.peak} из ${row.units} ед.` : "";
  return `${head}занято ${hoursOf(day.busy_seconds)} из ${hoursOf(day.capacity_seconds)} ч${day.percent != null ? ` (${day.percent} %)` : ""}${peak}`;
}

/** «ОБ.005 SR-W01 | Машина крутки» → «ОБ.005 Машина крутки» и модель «SR-W01» второй строкой: в узкой колонке
 *  видно, что за станок, а не только модель. Без «|» — название как есть. */
export function machineName(row: Pick<MachineLoadRow, "machine" | "label">) {
  const rest = row.label.startsWith(row.machine) ? row.label.slice(row.machine.length).trim() : row.label;
  const cut = rest.indexOf(" | ");
  if (cut < 0) return { title: row.label, model: "" };
  return { title: `${row.machine} ${rest.slice(cut + 3).trim()}`.trim(), model: rest.slice(0, cut).trim() };
}

function itemText(item: MachineLoadItem) {
  const etk = item.etk_stage && item.etk_stage !== item.stage ? ` · оп. ЭТК ${item.etk_position} «${item.etk_stage}»` : "";
  return `этап ${item.position} «${item.stage}»${etk}`;
}

/** Ширина блока: по ней сетка решает, две недели показывать или четыре. */
function useBlockWidth() {
  const [node, ref] = useState<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    if (!node) return;
    setWidth(node.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setWidth(node.clientWidth));
    observer.observe(node);
    return () => observer.disconnect();
  }, [node]);
  return { ref, width };
}

export function MachineLoadGrid({ holidays, shift, days: given, week = 0, onWeekChange, refreshKey = "" }: {
  holidays: string[];
  shift?: ProductionShift | null;
  /** Дни доски, если их задаёт экран (доска мастера); без них — неделя `week` по ширине блока. */
  days?: string[];
  week?: number;
  /** Листать по неделе (без `days`). */
  onWeekChange?: (week: number) => void;
  /** Меняется, когда план перезагружен: сетка запрашивает занятость заново. */
  refreshKey?: string;
}) {
  const { ref, width } = useBlockWidth();
  const workShift = shift && shift.weekdays ? shift : DEFAULT_SHIFT;
  const count = width >= WIDE_FROM ? LOAD_DAYS_WIDE : LOAD_DAYS;
  const own = useMemo(() => loadDays(week, new Date(), count), [week, count]);
  const days = given?.length ? given : own;
  const from = days[0];
  const to = days[days.length - 1];
  const [data, setData] = useState<MachineLoad | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<{ machine: string; day: string } | null>(null);
  const today = isoOf(new Date());

  useEffect(() => {
    let live = true;
    setError("");
    machineLoadApi.load(from, to)
      .then((result) => { if (live) setData(result); })
      .catch((reason) => { if (live) { setData(null); setError(failureText(reason, "Загрузка станков не получена")); } });
    return () => { live = false; };
  }, [from, to, refreshKey]);

  const period = `${formatDayMonth(from)}–${formatDayMonth(to)}`;
  const current = data && data.date_from === from && data.date_to === to ? data : null;
  const rows = current?.machines ?? [];
  const weeks = Math.max(1, Math.round(days.length / 7));
  const detail = open ? rows.find((row) => row.machine === open.machine) : undefined;
  const warnings = current?.warnings ?? [];

  return <div className="pl" ref={ref}>
    {!given?.length && <div className="pl-head">
      <span className="pl-period" title={`Период загрузки станков ${period}`}><b>{periodWords(from, to)}</b> · {weeks} нед.</span>
      {onWeekChange && <div className="mb-week-nav">
        <button className="secondary small" onClick={() => onWeekChange(week - 1)}>← неделя</button>
        <button className="secondary small" onClick={() => onWeekChange(0)} disabled={week === 0}>Сегодня</button>
        <button className="secondary small" onClick={() => onWeekChange(week + 1)}>неделя →</button>
      </div>}
    </div>}
    {error ? <div className="error-banner" role="alert">{error}</div>
      : !current ? <div className="empty">Загрузка…</div>
      : !rows.length ? <div className="empty">За {period} станки не заняты: в плане нет операций ЭТК со станком в «Оборудовании».</div>
      // На доске мастера — та же таблица, что у монтажников над ней; в плане — как «Загрузка по людям».
      : <table className={given?.length ? "mb-week machine-load" : "mb-week pl-table machine-load"}>
        <thead><tr>
          <th scope="col">Станок</th>
          {days.map((day) => {
            const working = workingDay(day, workShift, holidays);
            return <th key={day} scope="col" className={`${working ? "" : "off"}${day === today ? " today" : ""}`}
              title={`${weekdayOf(day)} ${formatDayMonth(day)}${day === today ? " · сегодня" : working ? "" : " · выходной"}`}><b>{formatDayMonth(day)}</b><span>{weekdayOf(day)}</span></th>;
          })}
        </tr></thead>
        <tbody>{rows.map((row) => {
          const total = row.days.reduce((sum, day) => sum + day.busy_seconds, 0);
          return <tr key={row.machine}>
            <th scope="row" title={row.label}><b>{machineName(row).title}</b>
              <small>{machineName(row).model ? `${machineName(row).model} · ` : ""}{row.units} ед.{row.known ? "" : " · нет в БД.ОБ"} · {total > 0 ? `${hoursOf(total)} ч за ${weeks} нед.` : "свободен"}</small></th>
            {days.map((day) => {
              const value = row.days.find((item) => item.day === day);
              if (!value) return <td key={day} className="mb-cell off"><span /></td>;
              const level = machineLevel(value);
              const picked = open?.machine === row.machine && open.day === day;
              const text = level === "off" || level === "free" ? "" : `${value.percent ?? "—"} %`;
              const title = dayTitle(row, value);
              return <td key={day} className={`mb-cell ${level}${day === today ? " today" : ""}${picked ? " picked" : ""}`}>
                {value.busy_seconds > 0
                  ? <button title={title} aria-label={`Загрузка ${row.machine} ${formatDayMonth(day)}`} aria-expanded={picked}
                    onClick={() => setOpen(picked ? null : { machine: row.machine, day })}>{text}</button>
                  : <span title={title}>{text}</span>}
              </td>;
            })}
          </tr>;
        })}</tbody>
      </table>}
    {open && detail && <MachineDay row={detail} day={open.day} onClose={() => setOpen(null)} />}
    {rows.length > 0 && <MachineLegend />}
    {warnings.length > 0 && <div className="warning-inline machine-load-warnings" role="note">
      {warnings.slice(0, WARNINGS_SHOWN).join("\n")}{warnings.length > WARNINGS_SHOWN ? `\nи ещё ${warnings.length - WARNINGS_SHOWN}` : ""}
    </div>}
  </div>;
}

function MachineDay({ row, day, onClose }: { row: MachineLoadRow; day: string; onClose: () => void }) {
  const value = row.days.find((item) => item.day === day);
  const lower = new Date(`${day}T00:00:00`).getTime();
  const upper = lower + 86_400_000;
  const items = row.items.filter((item) => new Date(item.start).getTime() < upper && new Date(item.finish).getTime() > lower);
  return <div className="pl-detail" role="group" aria-label={`Что на станке: ${row.machine}, ${formatDayMonth(day)}`}>
    <div className="pl-detail-head"><b>{row.label} · {weekdayOf(day)} {formatDayMonth(day)}</b>
      {value && <span>{hoursOf(value.busy_seconds)} из {hoursOf(value.capacity_seconds)} ч · {row.units} ед.</span>}
      <button className="mb-day-close" aria-label="Закрыть день" title="Закрыть" onClick={onClose}>×</button></div>
    <ul>{items.map((item) => <li key={`${item.operation_id}-${item.etk_position}-${item.start}`}>
      <b>{item.task_number} · {item.item}</b> <span>{itemText(item)}</span>
      <small> · {windowText(item.start, item.finish)}{item.operator_names.length ? ` · ${item.operator_names.join(", ")}` : ""}</small>
    </li>)}</ul>
  </div>;
}

function MachineLegend() {
  return <div className="mb-legend pl-legend" title="Процент — занятое время смены всех единиц станка из БД.ОБ">
    <span className="mb-cell free">&nbsp;</span> свободен
    <span className="mb-cell low">40</span> частично, до 85 %
    <span className="mb-cell full">95</span> занят
    <span className="mb-cell over">120</span> перегруз: больше 105 % или работ одновременно больше единиц
    <span className="mb-cell off">&nbsp;</span> выходной
  </div>;
}

const pad = (value: number) => String(value).padStart(2, "0");
/** Момент в часах цеха «2026-10-17T10:00» — как назначение доли на доске мастера. */
export const wallTime = (value: number) => {
  const date = new Date(value);
  return `${isoOf(date)}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

/** Предупреждение для ручной постановки: станки операции в этом окне уже заняты всеми единицами. Не запрещает. */
export function MachineWarning({ taskId, operationId, operatorId, quantity, start, finish }: {
  taskId: string;
  operationId: string;
  operatorId: string | null;
  quantity: number | null;
  start: number | null;
  finish: number | null;
}) {
  const [conflicts, setConflicts] = useState<MachineConflict[]>([]);
  useEffect(() => {
    if (start == null || finish == null || finish <= start) { setConflicts([]); return; }
    let live = true;
    machineLoadApi.check({ task_id: taskId, operation_id: operationId, operator_id: operatorId, quantity, start: wallTime(start), finish: wallTime(finish) })
      .then((result) => { if (live) setConflicts(Array.isArray(result?.conflicts) ? result.conflicts : []); })
      // Подсказка, не запрет: не получили ответ — назначение работает как раньше.
      .catch(() => { if (live) setConflicts([]); });
    return () => { live = false; };
  }, [taskId, operationId, operatorId, quantity, start, finish]);
  if (!conflicts.length) return null;
  return <div className="warning-inline machine-warning" role="status">
    {conflicts.map((item) => <div key={`${item.machine}-${item.start}`}>{item.text}</div>)}
  </div>;
}
