import { Fragment, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  kitsetsApi,
  productionApi,
  type BoardAssignItem,
  type BoardData,
  type BoardTask,
  type KitsetTile,
  type ProductionAbsence,
  type ProductionLaunchable,
  type ProductionOperation,
  type ProductionOperator,
  type ProductionShare,
  type ProductionShift,
  type ProductionSkill,
  type ProductionTask,
} from "./api";
import { formatDayMonth, kbLabel } from "./format";
import { MachineLoadGrid, MachineWarning } from "./MachineLoad";
import { LoadCell, LoadLegend, shiftCapacity } from "./PeopleLoad";
import { periodWords } from "./PlanLoadSheet";
import { KitTile } from "./ProjectCard";
import { deadlineChips, feedsText, operationPlanText, taskItemLabel, upstreamIds, windowText } from "./productionGraph";
import { StatusChip } from "./StatusChip";
import { PageWindow } from "./PageWindow";
import { AutoplanPanel } from "./AutoplanPanel";
import { isPfOperation, operationName, pfOpsText } from "./PfTaskBlocks";

// Экран мастера (владелец, 29.09): после выдачи техкарты проект приходит мастеру, разбитый на операции маршрута,
// и мастер раздаёт их монтажникам. Слева проекты → задания → операции, справа монтажники с загрузкой.
// Назначение идёт тем же assign, что и карточка с Гантом: время считает сервер (первое свободное окно).

// Открытый проект переживает переход в Гант задания и обратно (доска при этом монтируется заново).
let lastProject = "";
export function forgetOpenProject() { lastProject = ""; }

// Вид проекта на доске: по этапам КБ или по оборудованию (владелец 19.10: «раскидать сразу резку на одно и т. д.»).
type BoardView = "stages" | "equipment";
const VIEW_KEY = "erp.masterBoard.view";
function readView(): BoardView {
  try { return localStorage.getItem(VIEW_KEY) === "equipment" ? "equipment" : "stages"; } catch { return "stages"; }
}
function plural(count: number, forms: [string, string, string]) {
  const tail = count % 100;
  if (tail >= 11 && tail <= 14) return forms[2];
  return count % 10 === 1 ? forms[0] : count % 10 >= 2 && count % 10 <= 4 ? forms[1] : forms[2];
}
type GroupItem = { task: ProductionTask; op: ProductionOperation };
type EquipmentGroup = { label: string; items: GroupItem[]; seconds: number };

type Mutate = (key: string, operation: () => Promise<unknown>) => Promise<void>;

// Список монтажников отбирает сервер (/operators: активный «Сотрудник» с площадкой или из отдела «Монтаж»).
export function installers(operators: ProductionOperator[]) {
  return operators;
}

function itemId(task: { project: string; item: string }) {
  return task.item.toUpperCase().startsWith(task.project.toUpperCase()) ? task.item : `${task.project}${task.item.toUpperCase()}`;
}

function hours(seconds: number) {
  return `${(seconds / 3600).toLocaleString("ru-RU", { maximumFractionDigits: 1, minimumFractionDigits: 1 })} ч`;
}

const formatDay = (value: string | null | undefined) => formatDayMonth(value);

/** Дедлайн проекта из производственного плана: ключ — номер проекта строчными. */
export type ProjectDeadlines = Record<string, { deadline: string | null; days_left: number | null }>;

function isoDay(date: Date) {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

const done = (operation: ProductionOperation) => operation.state === "выполнена";

// Прогресс операции для мастера (владелец, 05.10: «мастер видит, что сдано и что просрочено»). Всё считается из полей,
// которые API уже отдаёт: fact.good, wip.closed (ждут ОТК или исправления), required_quantity, планы и дедлайн задания.
export type OpFlag = "late" | "wait" | "over";
export const FLAG_TEXT: Record<OpFlag, string> = { late: "не начато, план истёк", wait: "ждёт ОТК", over: "позже срока задания" };
export type OpProgress = { required: number; good: number; waiting: number; percent: number; flags: OpFlag[] };

export function opProgress(task: ProductionTask, operation: ProductionOperation, now: number = Date.now()): OpProgress {
  const required = Number(operation.required_quantity ?? task.quantity) || 0;
  const good = Number(operation.fact?.good ?? 0) || 0;
  const waiting = Number(operation.wip?.closed ?? 0) || 0;
  const percent = required > 0 ? Math.min(100, Math.round((good / required) * 100)) : 0;
  const flags: OpFlag[] = [];
  if (!done(operation)) {
    const started = Number(operation.fact?.processed ?? 0) > 0 || (operation.shares ?? []).some((share) => share.started_at);
    if (!started && operation.planned_start && new Date(operation.planned_start).getTime() < now) flags.push("late");
    if (waiting > 0) flags.push("wait");
    if (task.deadline && operation.planned_finish && operation.planned_finish.slice(0, 10) > task.deadline.slice(0, 10)) flags.push("over");
  }
  return { required, good, waiting, percent, flags };
}

function progressTone(progress: OpProgress, finished: boolean) {
  if (progress.flags.includes("late") || progress.flags.includes("over")) return "danger";
  if (progress.flags.includes("wait")) return "warn";
  if (finished) return "ok";
  return progress.good > 0 ? "info" : "neutral";
}

// Доля монтажника в операции (владелец, 30.09: «дробить операцию между людьми»): у раздробленной — свои штуки и своё
// окно. Ответ без долей (старый) — окно всей операции.
export function sharesOf(operation: ProductionOperation, personId: string): ProductionShare[] {
  if (operation.shares) return operation.shares.filter((share) => share.operator_id === personId);
  return operation.operator_ids.includes(personId)
    ? [{ operator_id: personId, quantity: null, planned_start: operation.planned_start, planned_finish: operation.planned_finish, started_at: null, finished_at: null }]
    : [];
}

// Сколько ещё можно записать готовыми на этап: нужно этапу минус сданное (годные + брак).
export function madeLeft(task: ProductionTask, operation: ProductionOperation) {
  const required = Number(operation.required_quantity ?? task.quantity) || 0;
  return Math.max(0, Math.round(required - (Number(operation.fact?.processed ?? 0) || 0)));
}

const shareDone = (operation: ProductionOperation, share: ProductionShare) => done(operation) || Boolean(share.finished_at);

// Норма доли: подготовка + штучное время на свои штуки; бригада делит норму всей операции поровну.
function shareSeconds(operation: ProductionOperation, share: ProductionShare) {
  if (share.quantity == null) return operation.norm_seconds / Math.max(1, operation.operator_ids.length);
  return Number(operation.setup_seconds) + Number(operation.seconds_per_unit) * share.quantity;
}

// Нераспределённые штуки: у бригады и «всей операции» — ноль, у раздробленной — задание минус доли.
export function remainderOf(task: ProductionTask, operation: ProductionOperation) {
  const shares = operation.shares ?? [];
  if (!shares.length && !operation.operator_ids.length) return task.quantity;
  if (!shares.some((share) => share.quantity != null)) return 0;
  return Math.max(0, task.quantity - shares.reduce((sum, share) => sum + (share.quantity ?? 0), 0));
}

// Штуки по умолчанию (как на сервере): своя доля (у бригады — поровну), иначе нераспределённое,
// иначе поровну с уже назначенными — новый человек добавляется к операции, а не вытесняет назначенных.
export function defaultPieces(task: ProductionTask, operation: ProductionOperation, personId?: string) {
  const shares = operation.shares ?? [];
  const own = personId ? shares.find((share) => share.operator_id === personId) : undefined;
  if (own) return own.quantity ?? Math.ceil(task.quantity / Math.max(1, shares.length));
  return remainderOf(task, operation) || Math.ceil(task.quantity / (shares.length + 1));
}

// «· 1500 шт» — только у раздробленной операции; одна доля на всё задание — это просто «вся операция».
function pieceLabel(task: ProductionTask, operation: ProductionOperation, share: ProductionShare) {
  if (share.quantity == null || (share.quantity >= task.quantity && (operation.shares ?? []).length <= 1)) return "";
  return ` · ${share.quantity} шт`;
}

export type InstallerLoad = { operations: number; seconds: number; until: string | null; absent: string };

// Загрузка монтажника: его невыполненные доли в живых заданиях.
export function installerLoad(operatorId: string, tasks: ProductionTask[], absences: ProductionAbsence[], today: string): InstallerLoad {
  let operations = 0;
  let seconds = 0;
  let until: string | null = null;
  for (const task of tasks) {
    if (task.status === "Отменено") continue;
    for (const operation of task.operations) {
      for (const share of sharesOf(operation, operatorId)) {
        if (shareDone(operation, share)) continue;
        operations += 1;
        seconds += shareSeconds(operation, share);
        if (share.planned_finish && (!until || share.planned_finish > until)) until = share.planned_finish;
      }
    }
  }
  const absence = absences.find((item) => item.operator_id === operatorId && item.from_date <= today && today <= item.to_date);
  return { operations, seconds, until, absent: absence ? `${absence.kind} до ${formatDay(absence.to_date)}` : "" };
}

// ── Неделя монтажников (владелец, 30.09: «не понимаю, на какой период я закидываю»). Мастер видит загрузку
// каждого монтажника по дням в часах и ставит операцию кликом в день: сервер планирует её с начала смены этого
// дня в первое свободное окно (тот же assign и план, что в карточке с Гантом, prodPlan_).
// Площадка без нормирования: задание ей отдают целиком (сервер: UNPLANNED_SITES).
const HANDOVER_SITE = "ЖУК";
export const DEFAULT_SHIFT: ProductionShift = { weekdays: [0, 1, 2, 3, 4], start: "09:00", finish: "17:30", breaks: [["13:00", "13:30"]], effective_hours: "7" };
const WEEK_DAYS = 7;
/** Ширина доски, с которой видно две недели (27"); уже — одна. */
const TWO_WEEKS_FROM = 1500;

function addDays(day: string, count: number) {
  const date = new Date(`${day}T00:00:00`);
  date.setDate(date.getDate() + count);
  return isoDay(date);
}

function minutes(value: string) {
  const [h, m] = value.split(":").map(Number);
  return (h || 0) * 60 + (m || 0);
}

export function workingDay(day: string, shift: ProductionShift, holidays: string[]) {
  const weekday = (new Date(`${day}T00:00:00`).getDay() + 6) % 7;
  return shift.weekdays.includes(weekday) && !holidays.includes(day);
}

// Рабочие отрезки смены без перерывов (обеда), в минутах от полуночи.
function workSpans(shift: ProductionShift): [number, number][] {
  let spans: [number, number][] = [[minutes(shift.start), minutes(shift.finish)]];
  for (const [a, b] of shift.breaks ?? []) {
    const x = minutes(a), y = minutes(b);
    spans = spans.flatMap(([from, to]): [number, number][] => y <= from || x >= to ? [[from, to]] : [
      ...(from < x ? [[from, Math.min(to, x)] as [number, number]] : []),
      ...(y < to ? [[Math.max(from, y), to] as [number, number]] : []),
    ]);
  }
  return spans;
}

// Доля чистой работы: обед вычтен, остальные перерывы (туалет, перекуры) размазаны по смене — как на сервере.
function workRate(shift: ProductionShift) {
  const available = workSpans(shift).reduce((sum, [from, to]) => sum + to - from, 0) / 60;
  return available > 0 ? Math.min(1, Number(shift.effective_hours || 7) / available) : 1;
}

/** «Сейчас» для загрузки: по нему затянувшаяся доля занимает монтажника после своего плана (loadWindow). */
export type LoadClock = { now: number; holidays: string[]; absences: ProductionAbsence[]; skills?: ProductionSkill[] };
/** Затянувшаяся работа держит монтажника не меньше часа его работы (сервер: dispatch.OVERRUN_FLOOR_SECONDS). */
const OVERRUN_FLOOR_SECONDS = 3600;

export type LoadWindow = { start: number; finish: number; overrun: boolean };

// Окно, которое доля занимает у монтажника (то же правило, что dispatch.overrun_window на сервере). Взятая и не сданная
// доля, чей план истёк («затянулось», владелец 22.10), идёт от «сейчас» на норму несданных штук (у бригады — всей
// операции), не меньше часа, по смене, праздникам и отсутствиям; пауза не освобождает. Иначе — свой план.
// Без load — только план. План в задании не меняется.
export function loadWindow(task: ProductionTask, operation: ProductionOperation, share: ProductionShare,
  shift: ProductionShift, load?: LoadClock): LoadWindow | null {
  if (!share.planned_start || !share.planned_finish) return null;
  const planned = { start: new Date(share.planned_start).getTime(), finish: new Date(share.planned_finish).getTime(), overrun: false };
  if (!load || task.status === "Отменено" || task.handed_to || done(operation)) return planned;
  const group = share.quantity == null
    ? (operation.shares ?? [share]).filter((item) => item.quantity == null && item.planned_start && item.planned_finish)
    : [share];
  if (!group.some((item) => item.started_at && !item.finished_at)) return planned;
  if (Math.max(...group.map((item) => new Date(item.planned_finish!).getTime())) >= load.now) return planned;
  const required = Number(operation.required_quantity ?? task.quantity) || 0;
  const processed = Number(operation.fact?.processed ?? 0) || 0;
  if (processed >= required) return planned;
  // Штуки операции: доля в изделиях × штук на изделие минус сданное ею; бригада — всё, что не сдано по операции.
  const pieces = share.quantity == null ? required - processed
    : share.quantity * (task.quantity > 0 ? required / task.quantity : 1) - (Number(share.output ?? 0) || 0);
  if (!(pieces > 0)) return planned;
  const rate = group.reduce((sum, item) => sum + rateOf(load.skills ?? [], operation.code, item.operator_id), 0) || 1;
  const seconds = Math.max(OVERRUN_FLOOR_SECONDS, Math.ceil((Number(operation.seconds_per_unit) || 0) * pieces / rate));
  const people = new Set(group.map((item) => item.operator_id));
  const dayAfter = (day: string) => new Date(`${addDays(day, 1)}T00:00:00`).getTime();
  let start = workFinish(load.now, 0, shift, load.holidays);
  for (let guard = 0; guard < 200; guard += 1) {
    const finish = workFinish(start, seconds, shift, load.holidays);
    const away = load.absences.filter((item) => people.has(item.operator_id)
      && new Date(`${item.from_date}T00:00:00`).getTime() < finish && start < dayAfter(item.to_date));
    if (!away.length) return { start, finish, overrun: true };
    start = workFinish(Math.max(...away.map((item) => dayAfter(item.to_date))), 0, shift, load.holidays);
  }
  return planned;
}

type Window = { task: ProductionTask; operation: ProductionOperation; share: ProductionShare; start: number; finish: number; overrun: boolean };

// Окна невыполненных долей монтажника (loadWindow; skip — выбранная операция: её можно переставить).
function windowsOf(operatorId: string, tasks: ProductionTask[], shift: ProductionShift, skip = "", load?: LoadClock): Window[] {
  const result: Window[] = [];
  for (const task of tasks) {
    if (task.status === "Отменено" || task.handed_to) continue;
    for (const operation of task.operations) {
      if (operation.id === skip) continue;
      for (const share of sharesOf(operation, operatorId)) {
        if (shareDone(operation, share)) continue;
        const window = loadWindow(task, operation, share, shift, load);
        if (window) result.push({ task, operation, share, ...window });
      }
    }
  }
  return result;
}

// Часы окна в день: пересечение с рабочими отрезками смены, приведённое к чистому времени (как считает сервер).
export function windowDayHours(start: number, finish: number, day: string, shift: ProductionShift) {
  let total = 0;
  for (const [from, to] of workSpans(shift)) {
    const a = Math.max(dayMoment(day, from), start);
    const b = Math.min(dayMoment(day, to), finish);
    if (b > a) total += (b - a) / 3_600_000;
  }
  return total * workRate(shift);
}

// Часы монтажника в день: окна его невыполненных долей (затянувшаяся — от «сейчас») в рабочих отрезках смены.
export function dayHours(operatorId: string, day: string, tasks: ProductionTask[], shift: ProductionShift, load?: LoadClock) {
  return windowsOf(operatorId, tasks, shift, "", load).reduce((sum, window) => sum + windowDayHours(window.start, window.finish, day, shift), 0);
}

// Затянувшиеся доли монтажника в этот день — подписью ячейки: «затянулось: ПФ-12 · Ветвь … · 708К.4 · КБ1».
export function overrunsOn(operatorId: string, day: string, tasks: ProductionTask[], shift: ProductionShift, load?: LoadClock) {
  return windowsOf(operatorId, tasks, shift, "", load)
    .filter((window) => window.overrun && windowDayHours(window.start, window.finish, day, shift) > 0)
    .map((window) => `${operationName(window.operation)} · ${taskItemLabel(window.task)}`);
}

// Самое раннее начало доли монтажника: не раньше «сейчас» (с округлением вверх до 15 минут — по сетке, а не 09:47)
// и не раньше конца его же этапов этой сборки, чей полуфабрикат доходит до операции по схеме (сервер ставит так же,
// dispatch.place). Ответ без входов операций — цепочка: все меньшие номера.
export function earliestStart(operatorId: string, task: ProductionTask, operation: ProductionOperation, now: Date) {
  const quarter = 15 * 60_000;
  let result = Math.ceil(now.getTime() / quarter) * quarter;
  const upstream = upstreamIds(task, operation);
  for (const item of task.operations) {
    if (upstream ? !upstream.has(item.id) : item.position >= operation.position) continue;
    for (const share of sharesOf(item, operatorId)) {
      if (!shareDone(item, share) && share.planned_finish) result = Math.max(result, new Date(share.planned_finish).getTime());
    }
  }
  return result;
}

function dayMoment(day: string, value: number) {
  return new Date(`${day}T${clock(value)}:00`).getTime();
}

// Рабочий момент не раньше time в этот день: из перерыва — на его конец; после смены — null.
function workingMoment(day: string, time: number, shift: ProductionShift) {
  for (const [from, to] of workSpans(shift)) {
    const a = dayMoment(day, from), b = dayMoment(day, to);
    if (time < b) return Math.max(time, a);
  }
  return null;
}

// «Назначить на день»: после уже запланированной на этот день работы монтажника — ничего из неё не двигается,
// хвост уходит на следующие дни. null — после работы в этот день места нет.
export function appendStart(operatorId: string, day: string, tasks: ProductionTask[], shift: ProductionShift, skip: string, earliest: number, load?: LoadClock) {
  const dayStart = dayMoment(day, minutes(shift.start));
  const dayEnd = dayMoment(day, minutes(shift.finish));
  let time = Math.max(dayStart, earliest);
  for (const window of windowsOf(operatorId, tasks, shift, skip, load)) {
    if (window.start < dayEnd && window.finish > dayStart) time = Math.max(time, window.finish);
  }
  time = Math.ceil(time / 60_000) * 60_000;  // план — в минутах, как на сервере
  return time < dayEnd ? workingMoment(day, time, shift) : null;
}

// Конец работы с момента start: норма растягивается на мелкие перерывы, обед и нерабочие дни пропускаются
// (как add_work_seconds на сервере) — чтобы мастер видел, куда уйдёт хвост.
export function workFinish(start: number, seconds: number, shift: ProductionShift, holidays: string[]) {
  const rate = workRate(shift);
  let rest = rate < 1 ? Math.ceil(seconds / rate / 60) * 60_000 : seconds * 1000;
  let day = isoDay(new Date(start));
  let time = start;
  for (let guard = 0; guard < 400; guard += 1) {
    if (workingDay(day, shift, holidays)) {
      for (const [from, to] of workSpans(shift)) {
        const a = Math.max(time, dayMoment(day, from)), b = dayMoment(day, to);
        if (b <= a) continue;
        if (rest <= b - a) return a + rest;
        rest -= b - a;
      }
    }
    day = addDays(day, 1);
    time = dayMoment(day, 0);
  }
  return time;
}

// «Назначить только …»: ближайший с дня from рабочий день монтажника (не отпуск), где после его работы есть место, и
// момент начала — как «Назначить на день» (appendStart). null — за 60 дней места нет.
export function firstFreeStart(operatorId: string, from: string, task: ProductionTask, operation: ProductionOperation,
  tasks: ProductionTask[], shift: ProductionShift, holidays: string[], absences: ProductionAbsence[], now: Date = new Date(),
  skills: ProductionSkill[] = []) {
  const earliest = earliestStart(operatorId, task, operation, now);
  const load: LoadClock = { now: now.getTime(), holidays, absences, skills };
  for (let index = 0; index < 60; index += 1) {
    const day = addDays(from, index);
    if (!workingDay(day, shift, holidays)) continue;
    if (absences.some((item) => item.operator_id === operatorId && item.from_date <= day && day <= item.to_date)) continue;
    const start = appendStart(operatorId, day, tasks, shift, operation.id, earliest, load);
    if (start != null) return { day, start };
  }
  return null;
}

// Почему весь этап одному не отдать: штуки начатых и законченных долей сервер не забирает (dispatch.split_shares).
export function onlyBlocker(operation: ProductionOperation, operatorId: string, operators: ProductionOperator[]) {
  const nameOf = (id: string) => operators.find((item) => item.id === id)?.name ?? "Монтажник";
  const own = (operation.shares ?? []).find((share) => share.operator_id === operatorId);
  if (own && (own.started_at || own.finished_at)) return `${nameOf(operatorId)} уже взял(а) этап — время идёт по факту`;
  const locked = (operation.shares ?? []).find((share) => share.operator_id !== operatorId && (share.started_at || share.finished_at));
  return locked ? `${nameOf(locked.operator_id)} уже ${locked.finished_at ? "сделал(а) свою часть" : "взял(а) этап"} — его штуки не забрать` : "";
}

// Снимают последнего исполнителя задания, по которому работы не было (никто не брал, ничего не сдано): сервер отменит
// задание, его тираж снова станет новым заданием КБ (владелец 22.10: «если поставили и убрали, то оно уже не запущено»).
export function cancelsTask(task: ProductionTask, operation: ProductionOperation, operatorId: string) {
  const others = task.operations.some((op) => op.operator_ids.some((id) => op.id !== operation.id || id !== operatorId));
  const worked = task.operations.some((op) => (op.state && op.state !== "не начата") || Number(op.fact?.processed ?? 0) > 0
    || (op.shares ?? []).some((share) => share.started_at || share.finished_at));
  return !others && !worked && !task.handed_to && task.status === "Запущено";
}

// Что изменится, если снять монтажника с этапа (окно подтверждения у «×»): то же, что делает сервер (_remove_share).
export function removalChanges(operation: ProductionOperation, operatorId: string, operators: ProductionOperator[], task?: ProductionTask): string[] {
  const nameOf = (id: string) => operators.find((item) => item.id === id)?.name ?? "Монтажник";
  const own = sharesOf(operation, operatorId)[0];
  const rest = operation.operator_ids.filter((id) => id !== operatorId);
  const lines: string[] = [];
  if (own?.planned_start) lines.push(`План ${nameOf(operatorId)} ${windowText(own.planned_start, own.planned_finish)} освободится.`);
  if (!rest.length) lines.push("Этап останется без исполнителя — его нужно будет назначить заново.");
  else if (own?.quantity != null) lines.push(`${own.quantity} шт станут нераспределёнными — отдайте их другому монтажнику (${rest.map(nameOf).join(", ")} остаются со своими штуками).`);
  else lines.push(`Этап сделают ${rest.map(nameOf).join(", ")}: окно пересчитается на меньший состав, их работа после этапа сдвинется.`);
  if (own?.started_at) lines.push(`${nameOf(operatorId)} уже брал(а) этап в работу ${formatDay(own.started_at.slice(0, 10))}.`);
  if (task && cancelsTask(task, operation, operatorId)) {
    lines.push(`Других исполнителей у задания нет и работы по нему не было — задание ${task.number} отменится, ${task.quantity} шт снова станут новым заданием КБ.`);
  }
  return lines;
}

// Коэффициент монтажника по операции (prodSkillOf_): нет строк по коду — 1; есть — допущенный со своим коэффициентом.
function rateOf(skills: ProductionSkill[], code: string, operatorId: string) {
  const skill = code ? skills.find((item) => item.operator_id === operatorId && item.operation_code === code) : undefined;
  return skill?.allowed && Number(skill.coefficient) > 0 ? Number(skill.coefficient) : 1;
}

function clock(value: number) {
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
}

export interface DaySlot { start: string; finish: string; rest: boolean; busy: string[]; own: boolean; pause?: string }

// Раскладка дня монтажника по часам смены: что занято (операция · сборка) и где перерыв.
export function daySlots(operatorId: string, day: string, tasks: ProductionTask[], shift: ProductionShift, skip = "", load?: LoadClock): DaySlot[] {
  const slots: DaySlot[] = [];
  const end = minutes(shift.finish);
  const windows = windowsOf(operatorId, tasks, shift, skip, load);
  // Выбранная операция у этого монтажника не занятость (её можно передвинуть), но видна в своих часах.
  const mine = skip ? windowsOf(operatorId, tasks, shift, "", load).filter((window) => window.operation.id === skip) : [];
  for (let from = minutes(shift.start); from < end; from += 60) {
    const to = Math.min(end, from + 60);
    const a = new Date(`${day}T${clock(from)}:00`).getTime();
    const b = new Date(`${day}T${clock(to)}:00`).getTime();
    const busy = windows.filter((window) => window.start < b && a < window.finish)
      .map((window) => `${window.overrun ? "затянулось: " : ""}${operationName(window.operation)} · ${taskItemLabel(window.task)}${pieceLabel(window.task, window.operation, window.share)}`);
    const rest = (shift.breaks ?? []).some(([x, y]) => minutes(x) <= from && to <= minutes(y));
    // Перерыв внутри часа (обед 13:00–13:30) — подписью в ячейке: работы в этом часу меньше.
    const inside = (shift.breaks ?? []).find(([x, y]) => minutes(x) < to && from < minutes(y));
    const own = mine.some((window) => window.start < b && a < window.finish);
    slots.push({ start: clock(from), finish: clock(to), rest, busy, own, pause: !rest && inside ? (minutes(inside[0]) <= from ? `обед до ${inside[1]}` : `обед с ${inside[0]}`) : undefined });
  }
  return slots;
}

function oneDecimal(value: number) {
  return value.toLocaleString("ru-RU", { maximumFractionDigits: 1 });
}

/** Ширина узла (ResizeObserver): по ней доска решает, сколько дней показать. */
function useWidth() {
  const [node, ref] = useState<HTMLElement | null>(null);
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


// Тираж нового задания (владелец 22.10: «Запустить задание» убран — задание создаёт первое действие из окна проекта):
// мастер может запустить меньше тиража КБ. Нормы и штуки будущего задания пересчитываются пропорционально — сервер
// построит задание на этот тираж тем же расчётом.
export function withRun<T extends ProductionTask>(task: T, run: number): T {
  if (!task.pending || run === task.quantity || task.quantity <= 0) return task;
  const ratio = run / task.quantity;
  return {
    ...task, quantity: run,
    operations: task.operations.map((op) => {
      const setup = Number(op.setup_seconds) || 0;
      return {
        ...op,
        required_quantity: op.required_quantity == null ? op.required_quantity : String(Math.round(Number(op.required_quantity) * ratio)),
        norm_seconds: Math.round(setup + Math.max(0, op.norm_seconds - setup) * ratio),
      };
    }),
  } as T;
}

type Selected = { taskId: string; operationId: string };

export function MasterBoard({ tasks, operators, site = "", skills, absences, shift, holidays = [], projectDeadlines = {}, canEdit, busy, mutate, openTask, error = "", notice = "" }: {
  tasks: ProductionTask[];
  operators: ProductionOperator[];
  site?: string;
  skills: ProductionSkill[];
  absences: ProductionAbsence[];
  shift?: ProductionShift | null;
  holidays?: string[];
  projectDeadlines?: ProjectDeadlines;
  canEdit: boolean;
  busy: string;
  mutate: Mutate;
  openTask: (taskId: string, operationId?: string) => void;
  /** Ошибка и итог последнего действия страницы — повторяются в открытом окне, иначе их не видно под ним. */
  error?: string;
  notice?: string;
}) {
  const [weekStart, setWeekStart] = useState(() => isoDay(new Date()));
  const [ready, setReady] = useState<ProductionLaunchable[]>([]);
  const [selected, setSelected] = useState<Selected | null>(null);
  const [onlyFree, setOnlyFree] = useState(false);
  const [flagFilter, setFlagFilter] = useState<OpFlag | null>(null);
  const [tiles, setTiles] = useState<{ items: KitsetTile[]; statuses: string[] }>({ items: [], statuses: [] });
  const [openProject, setOpenProjectState] = useState(lastProject);
  const [openDay, setOpenDay] = useState<{ personId: string; day: string } | null>(null);
  // Штук монтажнику (владелец, 30.09: «дробить операцию между людьми»): по умолчанию — нераспределённое.
  const [pieces, setPieces] = useState("");
  // «Назначить только …» (владелец 16.10): весь этап одному монтажнику одним действием; день — ближайший рабочий
  // со свободным временем, его видно на кнопке и можно сменить.
  const [onlyWho, setOnlyWho] = useState("");
  const [onlyDay, setOnlyDay] = useState("");
  // «Уже сделано» (владелец 20.10: «люди уже начали работать и сделали какое-то кол-во — нужен ручной ввод и отбития
  // готовых»): штуки мимо терминала, чьи они и отбить ли готовыми пропущенные этапы-входы.
  const [madeWho, setMadeWho] = useState<string | null>(null);
  const [madePieces, setMadePieces] = useState("");
  const [madeFill, setMadeFill] = useState(true);
  // Снятие исполнителя — через окно подтверждения с тем, что изменится (владелец 16.10: «×» сразу пересобирал план).
  const [removing, setRemoving] = useState<{ taskId: string; operationId: string; personId: string } | null>(null);
  const board = useWidth();
  const [boardData, setBoardData] = useState<BoardData | null | undefined>(undefined);
  const [boardTick, setBoardTick] = useState(0);
  const [view, setViewState] = useState<BoardView>(readView);
  // Ответ сервера на назначение: что не встало и предупреждения (допуск, станок занят).
  const [note, setNote] = useState<{ refused: string[]; warnings: string[] } | null>(null);
  const [groupSel, setGroupSel] = useState<Record<string, { who?: string; day?: string }>>({});
  // Тираж нового задания по id техкарты КБ, как ввёл мастер в окне проекта.
  const [runs, setRuns] = useState<Record<string, string>>({});
  // Автопланирование из окна проекта (владелец 22.10): автоплан «По проекту» на этот проект.
  const [autoplanFor, setAutoplanFor] = useState("");

  function setView(next: BoardView) {
    setViewState(next);
    try { localStorage.setItem(VIEW_KEY, next); } catch { /* без хранилища вид просто не запомнится */ }
  }

  function setOpenProject(project: string) {
    lastProject = project;
    setOpenProjectState(project);
    setSelected(null);
    setNote(null);
  }

  // Плитки как в «Проектах» (владелец, 29.09: «примерный вид проектов»): статус, ТК, заказчик, сборки.
  useEffect(() => {
    kitsetsApi.list("upd").then(setTiles).catch(() => setTiles({ items: [], statuses: [] }));
  }, []);

  useEffect(() => {
    if (!canEdit) return;
    productionApi.launchable().then((result) => setReady(result.items)).catch(() => setReady([]));
  }, [canEdit, tasks.length]);

  const live = tasks.filter((task) => task.status !== "Отменено");
  // КБ к запуску: остаток тиража больше нуля (тираж − запущенное не отменёнными заданиями), без тиража — пока заданий нет.
  // Запущенное — большее из счётчика сервера и живых заданий (список заданий бывает свежее счётчика).
  const launchedQty = new Map<string, number>();
  for (const task of live) {
    const key = `${task.project.toLowerCase()}|${itemId(task).toUpperCase()}`;
    launchedQty.set(key, (launchedQty.get(key) ?? 0) + task.quantity);
  }
  const waiting = ready.filter((item) => {
    const launched = Math.max(item.launched, launchedQty.get(`${item.project.toLowerCase()}|${itemId({ project: item.project, item: item.index }).toUpperCase()}`) ?? 0);
    const run = Number(item.run);
    return Number.isInteger(run) && run > 0 ? launched < run : !launched;
  });
  const projects = Array.from(new Set([...live.map((task) => task.project), ...waiting.map((item) => item.project)]));
  // Два срока (РП-1): дедлайн проекта — из плана; срок задания — самый ранний у незавершённых заданий проекта.
  const planOf = (project: string) => projectDeadlines[project.toLowerCase()];
  const taskDeadlineOf = (project: string) => live.filter((task) => task.project === project && task.status !== "Выполнено")
    .map((task) => task.deadline).filter((value): value is string => Boolean(value)).sort()[0] ?? "";
  const deadlineOf = (project: string) => planOf(project)?.deadline || taskDeadlineOf(project)
    || waiting.filter((item) => item.project === project).map((item) => item.deadline).filter(Boolean).sort()[0] || "";
  projects.sort((a, b) => (deadlineOf(a) || "9999").localeCompare(deadlineOf(b) || "9999") || a.localeCompare(b, "ru"));
  const deadlineTags = (project: string) => deadlineChips(planOf(project), taskDeadlineOf(project))
    .map((chip) => <StatusChip key={chip.kind} className="mb-deadline" tone={chip.tone} title={chip.title}>{chip.text}</StatusChip>);

  const shown = projects.find((project) => project === openProject) ?? "";
  // Полоса «Сегодня»: операции живых заданий (не отданных на площадку) с каждым флагом; клик фильтрует список.
  const nowMs = Date.now();
  const progressOf = (item: ProductionTask, op: ProductionOperation) => opProgress(item, op, nowMs);
  const flagCounts: Record<OpFlag, number> = { late: 0, wait: 0, over: 0 };
  for (const item of live) {
    if (item.handed_to) continue;
    for (const op of item.operations) for (const flag of progressOf(item, op).flags) flagCounts[flag] += 1;
  }
  const hasFlag = (item: ProductionTask, op: ProductionOperation) => !flagFilter || (!item.handed_to && progressOf(item, op).flags.includes(flagFilter));
  const projectHasFlag = (project: string) => live.some((item) => item.project === project && item.operations.some((op) => hasFlag(item, op)));
  const freeOf = (project: string) => live.filter((item) => item.project === project && !item.handed_to)
    .reduce((sum, item) => sum + item.operations.filter((op) => !done(op) && !op.operator_ids.length).length, 0);
  function tileFacts(project: string) {
    const free = freeOf(project);
    const total = live.filter((item) => item.project === project && !item.handed_to).reduce((sum, item) => sum + item.operations.length, 0);
    const toLaunch = waiting.filter((item) => item.project === project).length;
    const handed = live.filter((item) => item.project === project && item.handed_to).length;
    return <>
      {deadlineTags(project)}
      {total > 0 && <span className={free ? "mb-free" : "mb-done"}>{free ? `без исполнителя ${free} из ${total}` : `распределено ${total}`}</span>}
      {toLaunch > 0 && <span className="mb-launch" title="КБ с выданной техкартой: задания нет или запущен не весь тираж">к запуску {toLaunch}</span>}
      {handed > 0 && <span className="mb-handed">в {HANDOVER_SITE}: {handed}</span>}
    </>;
  }

  // Мастер площадки (Степан в Жуковском) видит и ставит только своих монтажников; без площадки — всех.
  const people = installers(operators).filter((person) => !site || person.site === site);
  const today = isoDay(new Date());
  const unassigned = live.filter((item) => !item.handed_to).reduce((sum, item) => sum + item.operations.filter((op) => !done(op) && !op.operator_ids.length).length, 0);

  const workShift = shift ?? DEFAULT_SHIFT;
  // На 27" доска во всю ширину — две недели, на 16" и планшете — одна (РП-3, РП-6); листают по неделе.
  const dayCount = board.width >= TWO_WEEKS_FROM ? WEEK_DAYS * 2 : WEEK_DAYS;
  const days = Array.from({ length: dayCount }, (_, index) => addDays(weekStart, index));
  // Сетка станков перечитывает занятость после каждого назначения (версии заданий меняются).
  const planVersion = tasks.map((item) => `${item.id}:${item.version}`).join(",");

  // Операции КБ с выданной техкартой и группы оборудования — с сервера, по открытому проекту.
  useEffect(() => {
    if (!shown) { setBoardData(undefined); return; }
    let alive = true;
    productionApi.board(shown)
      .then((result) => { if (alive) setBoardData({ project: shown, tasks: result.tasks ?? [], future: result.future ?? [], skipped: result.skipped ?? [] }); })
      .catch(() => { if (alive) setBoardData(null); });
    return () => { alive = false; };
  }, [shown, planVersion, boardTick]);
  const boardNow = boardData && boardData.project === shown ? boardData : boardData === null ? null : undefined;
  const futureRaw: BoardTask[] = boardNow?.future ?? [];
  const fullRun = new Map(futureRaw.map((item) => [item.id, item.quantity]));
  // Тираж нового задания: введённый мастером (от 1 до тиража КБ), иначе весь тираж КБ.
  function typedRun(item: ProductionTask) {
    const full = fullRun.get(item.id) ?? item.quantity;
    const text = runs[item.techcard_id ?? ""];
    const typed = Number(text);
    return { full, text: text ?? String(full), valid: text === undefined || (Number.isInteger(typed) && typed >= 1 && typed <= full), value: Number.isInteger(typed) && typed >= 1 && typed <= full ? typed : full };
  }
  const futureTasks: BoardTask[] = futureRaw.map((item) => withRun(item, typedRun(item).value));
  const runParam = (item: ProductionTask) => item.pending && item.quantity !== fullRun.get(item.id) ? { run: item.quantity } : {};
  // Сколько по КБ было запущено, когда доску читали: сервер отвечает 409, если с тех пор запустили ещё.
  const launchedParam = (item: ProductionTask) => {
    const launched = (item as BoardTask).launched;
    return item.pending && launched != null ? { launched } : {};
  };
  const pool: ProductionTask[] = [...live, ...futureTasks];
  const equipmentOf = new Map<string, string>();
  for (const item of [...(boardNow?.tasks ?? []), ...futureTasks]) for (const op of item.operations) equipmentOf.set(op.id, op.equipment.label);

  const task = selected ? pool.find((item) => item.id === selected.taskId) ?? null : null;
  const operation = task?.operations.find((item) => item.id === selected?.operationId) ?? null;
  const free = task && operation ? remainderOf(task, operation) : 0;
  const quantity = task ? Math.min(task.quantity, Math.max(0, Math.floor(Number(pieces) || 0))) : 0;

  function select(item: ProductionTask, op: ProductionOperation | null) {
    setSelected(op ? { taskId: item.id, operationId: op.id } : null);
    setPieces(op ? String(defaultPieces(item, op)) : "");
    setOnlyWho("");
    setOnlyDay("");
    setMadeWho(null);
    setMadePieces(op ? String(madeLeft(item, op)) : "");
    setMadeFill(true);
    setOpenDay(null);
  }

  // Мастер выбирает день (и час); доля встаёт с первого свободного времени монтажника, хвост — на следующие дни,
  // его работа после неё сдвигается (сервер, dispatch.place).
  function boardItem(item: ProductionTask, op: ProductionOperation, pieces?: number): BoardAssignItem {
    return item.pending
      ? { techcard_id: item.techcard_id ?? "", techcard_version: item.techcard_version ?? 0, position: op.position, ...(pieces ? { quantity: pieces } : {}), ...runParam(item), ...launchedParam(item) }
      : { task_id: item.id, task_version: item.version, operation_id: op.id };
  }

  function refusedText(entry: { task_id: string | null; techcard_id: string | null; operation_id: string | null; position: number | null; reason: string }) {
    const owner = pool.find((item) => (entry.task_id && item.id === entry.task_id) || (entry.techcard_id && item.techcard_id === entry.techcard_id));
    const op = owner?.operations.find((item) => item.id === entry.operation_id || item.position === entry.position);
    return `${owner ? taskItemLabel(owner) : "Операция"}${op ? ` · этап ${op.position}` : ""}: ${entry.reason}`;
  }

  // Назначение через доску: будущие задания создаются из выданной техкарты, настоящие — как есть; одна транзакция.
  // Из окна операции (reopen) окно остаётся на той же операции уже созданного задания.
  function runBoard(key: string, operatorId: string, start: string, items: BoardAssignItem[], reopen = false) {
    setNote(null);
    const out: { next?: Selected | null } = {};
    return mutate(key, async () => {
      try {
        const result = await productionApi.boardAssign({ operator_id: operatorId, requested_start: start, items });
        setNote({ refused: result.refused.map(refusedText), warnings: result.warnings });
        const placed = result.placed[0];
        out.next = reopen && placed ? { taskId: placed.task_id, operationId: placed.operation_id } : null;
        return {};
      } finally {
        setBoardTick((value) => value + 1);
      }
    }).then(() => { if (out.next !== undefined) setSelected(out.next); });
  }

  const at = (moment: number) => clock(new Date(moment).getHours() * 60 + new Date(moment).getMinutes());
  const requestedAt = (moment: number) => `${isoDay(new Date(moment))}T${at(moment)}`;

  function assign(personId: string, start: number) {
    if (!task || !operation || !quantity) return;
    if (task.pending) {
      void runBoard(operation.id, personId, requestedAt(start), [boardItem(task, operation, quantity)], true);
      return;
    }
    void mutate(operation.id, () => productionApi.share(task.id, operation.id, task.version, personId, quantity, requestedAt(start)));
  }
  const capacity = shiftCapacity(workShift);
  // «Сейчас» для загрузки: затянувшаяся взятая работа занимает монтажника и после своего плана (loadWindow).
  const loadClock: LoadClock = { now: Date.now(), holidays, absences, skills };

  function unassign(item: ProductionTask, op: ProductionOperation, operatorId: string) {
    setRemoving(null);
    void mutate(op.id, () => productionApi.share(item.id, op.id, item.version, operatorId, null, null, true));
  }

  // «Назначить только»: монтажник по умолчанию — уже назначенный на этап (первый), иначе первый в списке.
  const onlyPerson = task && operation
    ? people.find((person) => person.id === onlyWho) ?? people.find((person) => operation.operator_ids.includes(person.id)) ?? people[0]
    : undefined;
  const onlySlot = task && operation && onlyPerson
    ? firstFreeStart(onlyPerson.id, onlyDay || today, task, operation, tasks, workShift, holidays, absences, new Date(), skills)
    : null;
  const onlyBlock = task && operation && onlyPerson ? onlyBlocker(operation, onlyPerson.id, operators) : "";
  // Окно «Назначить только …» — для предупреждения о занятом станке.
  const onlyFinish = task && operation && onlyPerson && onlySlot
    ? workFinish(onlySlot.start, Number(operation.setup_seconds) + Number(operation.seconds_per_unit) * task.quantity / rateOf(skills, operation.code, onlyPerson.id), workShift, holidays)
    : null;
  function assignOnly() {
    if (!task || !operation || !onlyPerson || !onlySlot) return;
    if (task.pending) {
      void runBoard(operation.id, onlyPerson.id, requestedAt(onlySlot.start), [boardItem(task, operation)], true);
      return;
    }
    void mutate(operation.id, () => productionApi.share(task.id, operation.id, task.version, onlyPerson.id, task.quantity, requestedAt(onlySlot.start)));
  }

  // «Уже сделано»: по умолчанию — первый назначенный, кто не закончил свою долю, иначе без исполнителя (отбить готовыми).
  // У нового задания исполнителей нет — только «без исполнителя»: задание создаётся вместе с записью (владелец 22.10).
  const madePerson = task && operation
    ? madeWho ?? operation.operator_ids.find((id) => !sharesOf(operation, id)[0]?.finished_at) ?? ""
    : "";
  const madeCount = Math.floor(Number(madePieces));
  const madeMax = task && operation ? madeLeft(task, operation) : 0;
  function recordMade() {
    if (!task || !operation || !(madeCount > 0)) return;
    const fill = madeFill && Boolean(operation.inputs?.length);
    const out: { next?: Selected } = {};
    // Записано — поле пустеет, чтобы второй клик не записал те же штуки ещё раз; отказ — число остаётся для правки.
    void mutate(`made:${operation.id}`, async () => {
      if (task.pending) {
        const result = await productionApi.boardOutput({
          techcard_id: task.techcard_id ?? "", techcard_version: task.techcard_version ?? 0, position: operation.position,
          good_quantity: madeCount, fill_inputs: fill, ...runParam(task), ...launchedParam(task),
        });
        out.next = { taskId: result.task.id, operationId: result.operation_id };
        setMadePieces("");
        setBoardTick((value) => value + 1);
        return result;
      }
      const result = await productionApi.manualOutput(task.id, operation.id, madeCount, madePerson, fill);
      setMadePieces("");
      return result;
    }).then(() => { if (out.next) setSelected(out.next); });
  }

  function openPersonDay(personId: string, day: string, open: boolean) {
    setOpenDay(open ? { personId, day } : null);
    // Своя доля у монтажника уже есть — переставляем её с теми же штуками.
    const own = open && operation ? sharesOf(operation, personId)[0] : undefined;
    if (own && task && operation) setPieces(String(defaultPieces(task, operation, personId)));
  }

  // ЖУК работает не нормированно (владелец, 30.09): задание отдают площадке целиком, без людей и часов.
  function handOver(item: ProductionTask, site: string) {
    if (selected?.taskId === item.id) setSelected(null);
    void mutate(`handover:${item.id}`, () => productionApi.handOver(item.id, item.version, site));
  }

  // «По оборудованию»: все нераспределённые операции проекта (настоящие и будущие) по группе оборудования операции.
  function renderGroups(project: string) {
    if (boardNow === undefined) return <div className="empty">Загрузка…</div>;
    if (boardNow === null) return <div className="empty">Не удалось получить группы оборудования — закройте окно и откройте проект снова.</div>;
    const groups = new Map<string, EquipmentGroup>();
    for (const item of pool) {
      if (item.project !== project || item.status === "Отменено" || item.handed_to) continue;
      for (const op of item.operations) {
        if (done(op) || op.operator_ids.length) continue;
        const label = equipmentOf.get(op.id) ?? "Без оборудования";
        const group = groups.get(label) ?? { label, items: [], seconds: 0 };
        group.items.push({ task: item, op });
        group.seconds += op.norm_seconds;
        groups.set(label, group);
      }
    }
    const list = Array.from(groups.values()).sort((a, b) => a.label.localeCompare(b.label, "ru"));
    if (!list.length) return <div className="empty">Все операции проекта распределены.</div>;
    return <>{list.map((group) => {
      const choice = groupSel[group.label] ?? {};
      const person = people.find((item) => item.id === choice.who) ?? people[0];
      const first = group.items[0];
      const slot = person ? firstFreeStart(person.id, choice.day || today, first.task, first.op, tasks, workShift, holidays, absences, new Date(), skills) : null;
      const day = choice.day || slot?.day || today;
      const requested = slot ? requestedAt(slot.start) : `${day}T${workShift.start}`;
      const count = group.items.length;
      return <div className="mb-task mb-group" key={group.label}>
        <div className="mb-task-head">
          <b>{group.label}</b>
          <small>{count} {plural(count, ["операция", "операции", "операций"])} · {hours(group.seconds)} по норме</small>
          {canEdit && person && <span className="mb-group-assign" role="group" aria-label={`Назначить группу ${group.label}`}>
            <label>Монтажник<select aria-label={`Монтажник на группу ${group.label}`} value={person.id} onChange={(event) => setGroupSel({ ...groupSel, [group.label]: { ...choice, who: event.target.value } })}>
              {people.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select></label>
            <label>с дня<input type="date" aria-label={`День начала группы ${group.label}`} min={today} value={day} onChange={(event) => setGroupSel({ ...groupSel, [group.label]: { ...choice, day: event.target.value } })} /></label>
            <button className="primary small" disabled={Boolean(busy)} onClick={() => void runBoard(`group:${group.label}`, person.id, requested, group.items.map((entry) => boardItem(entry.task, entry.op)))}>
              Назначить группу на {formatDay(day)}{slot ? ` с ${at(slot.start)}` : ""}
            </button>
          </span>}
        </div>
        <table className="mb-ops">
          <thead><tr><th>КБ</th><th>Этап</th><th>Штук</th><th>Норма</th></tr></thead>
          <tbody>{group.items.map(({ task: item, op }) => <tr key={op.id}>
            <td><b>{taskItemLabel(item)}</b>{item.pending && <StatusChip className="mb-new" tone="info">новое задание</StatusChip>}</td>
            <td>{canEdit ? <button className="mb-op" onClick={() => select(item, op)}>{op.position}. {operationName(op)}</button> : <>{op.position}. {operationName(op)}</>}</td>
            <td>{item.quantity}</td>
            <td>{hours(op.norm_seconds)}</td>
          </tr>)}</tbody>
        </table>
      </div>;
    })}</>;
  }

  const banners = <>
    {error && <div className="error-banner" role="alert">{error}</div>}
    {notice && <div className="success-banner">{notice}</div>}
    {note && (note.refused.length > 0 || note.warnings.length > 0) && <div className="warning-inline mb-note" role="status">
      {note.refused.map((line) => <div key={`r:${line}`}>Не назначено: {line}</div>)}
      {note.warnings.map((line) => <div key={`w:${line}`}>{line}</div>)}
    </div>}
  </>;

  // Тираж нового задания в окне проекта: по умолчанию весь остаток тиража КБ (запущенное раньше вычтено), можно меньше.
  function runField(item: ProductionTask) {
    const run = typedRun(item);
    const launched = (item as BoardTask).launched ?? 0;
    const rest = launched > 0 ? <small>остаток: тираж КБ {(item as BoardTask).run}, запущено {launched}</small> : null;
    if (!canEdit) return <><small>тираж {item.quantity}</small>{rest}</>;
    return <label className="mb-run">Тираж
      <input type="number" min={1} max={run.full} step={1} inputMode="numeric" aria-label={`Тираж ${taskItemLabel(item)}`} value={run.text}
        onChange={(event) => setRuns({ ...runs, [item.techcard_id ?? ""]: event.target.value })} />
      <span>из {run.full}</span>{rest}
      {!run.valid && <span className="danger-text">от 1 до {run.full}</span>}
    </label>;
  }

  function renderProject(project: string) {
    const projectTasks = live.filter((item) => item.project === project);
    const projectReady = waiting.filter((item) => item.project === project);
    const skipped = boardNow?.skipped ?? [];
    const free = freeOf(project);
    return <div className="panel mb-project-window">
      <div className="manual-stock-head mb-window-head">
        <h2>{project}</h2>{deadlineTags(project)}
        <span className="mb-count">{free ? `без исполнителя: ${free}` : projectTasks.length ? "всё распределено" : ""}</span>
        <div className="seg-tabs mb-view" role="radiogroup" aria-label="Вид проекта">
          <button type="button" role="radio" aria-checked={view === "stages"} className={view === "stages" ? "on" : ""} onClick={() => setView("stages")}>По этапам КБ</button>
          <button type="button" role="radio" aria-checked={view === "equipment"} className={view === "equipment" ? "on" : ""} onClick={() => setView("equipment")}>По оборудованию</button>
        </div>
        {canEdit && site !== HANDOVER_SITE && <button className="primary small" onClick={() => setAutoplanFor(project)}>Автопланирование</button>}
        <button className="secondary small" onClick={() => setOpenProject("")}>Закрыть</button>
      </div>
      {banners}
      {view === "equipment" && renderGroups(project)}
      {view === "stages" && boardNow === undefined && !projectTasks.length && <div className="empty">Загрузка…</div>}
      {view === "stages" && [...projectTasks, ...futureTasks].map((item) => {
        const operations = item.operations.filter((op) => (!onlyFree || (!done(op) && !op.operator_ids.length)) && hasFlag(item, op));
        if ((onlyFree || flagFilter) && !operations.length) return null;
        return <div className="mb-task" key={item.id}>
          <div className="mb-task-head">
            {item.pending
              ? <StatusChip className="mb-new" tone="info">новое задание</StatusChip>
              : <button className="link-button" onClick={() => openTask(item.id)}>{item.number} · Гант</button>}
            <span><b>{taskItemLabel(item)}</b> × {item.quantity}{item.name ? ` · ${item.name}` : ""}</span>
            {item.status !== "Выполнено" && deadlineChips(planOf(project), item.deadline).filter((chip) => chip.kind === "task")
              .map((chip) => <StatusChip key={chip.kind} className="mb-deadline" tone={chip.tone} title={chip.title}>{chip.text}</StatusChip>)}
            {item.pending
              ? <>{runField(item)}{(item as BoardTask).warnings?.length ? <small>{(item as BoardTask).warnings!.join("; ")}</small> : null}</>
              : item.status === "Выполнено"
              ? <><span className="status status-done">Выполнено</span>{item.handed_to && <small>{item.handed_to}: сдано {item.delivered ?? item.quantity} из {item.quantity}</small>}</>
              : item.handed_to
              ? <><span className="mb-handed">Отдано в {item.handed_to}</span>{item.delivered ? <small>сдано {item.delivered} из {item.quantity}</small> : null}{canEdit && !item.delivered && <button className="secondary small" disabled={Boolean(busy)} onClick={() => handOver(item, "")}>Вернуть на распределение</button>}{canEdit && <button className="secondary small" onClick={() => openTask(item.id)}>Отметить сдачу</button>}</>
              : <><small>назначено {item.assigned} из {item.operations.length}</small>{canEdit && <button className="secondary small" disabled={Boolean(busy)} onClick={() => handOver(item, HANDOVER_SITE)}>Отдать в {HANDOVER_SITE}</button>}</>}
          </div>
          <table className="mb-ops">
            <thead><tr><th>№</th><th>Операция</th><th>Норма</th><th>Исполнители</th><th>План</th></tr></thead>
            <tbody>{operations.map((op) => {
              const progress = progressOf(item, op);
              const crew = op.operator_ids.map((id) => ({ id, name: operators.find((operator) => operator.id === id)?.name ?? "?" }));
              return <tr key={op.id} className={done(op) ? "done" : ""}>
                <td>{op.position}</td>
                <td>{canEdit && !done(op) && !item.handed_to
                  ? <button className="mb-op" onClick={() => select(item, op)}>{operationName(op)}</button>
                  : <span>{operationName(op)}</span>}
                  {isPfOperation(op) ? <small className="op-code">{pfOpsText(op)}</small> : op.code ? <small className="op-code">{op.code}</small> : null}
                  {feedsText(item, op) && <div className="ap-man">{feedsText(item, op)}</div>}
                  {op.fact && progress.required > 0 && <div className={`mb-progress tone-${progressTone(progress, done(op))}`}>
                    <span>сдано {progress.good}/{progress.required}{progress.waiting > 0 ? ` · ${progress.waiting} ждут ОТК` : ""}</span>
                    <span className="mb-bar" role="progressbar" aria-label="Сдано" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress.percent}><i style={{ width: `${progress.percent}%` }} /></span>
                    {!item.handed_to && progress.flags.map((flag) => <em key={flag} className={`mb-flag ${flag === "wait" ? "warn" : "danger"}`}>{FLAG_TEXT[flag]}</em>)}
                  </div>}</td>
                <td>{hours(op.norm_seconds)}</td>
                <td>{done(op) ? <span className="mb-done">выполнена</span> : item.handed_to ? <span className="mb-handed-text">—</span> : <>
                  {crew.map((person) => {
                    const own = sharesOf(op, person.id)[0];
                    return <span className={`mb-chip${own?.finished_at ? " done" : ""}`} key={person.id} title={own?.planned_start ? windowText(own.planned_start, own.planned_finish) : undefined}>
                      {person.name}{own && pieceLabel(item, op, own) ? <b>{pieceLabel(item, op, own)}</b> : null}{own?.finished_at ? " ✓" : own?.started_at ? " · взял" : ""}
                      {canEdit && <button className="mb-chip-x" aria-label={`Снять ${person.name}`} title="Снять с операции" disabled={Boolean(busy)} onClick={() => setRemoving({ taskId: item.id, operationId: op.id, personId: person.id })}>×</button>}
                    </span>;
                  })}
                  {!crew.length ? <span className="mb-free">не назначено</span> : remainderOf(item, op) > 0 ? <span className="mb-free">не распределено {remainderOf(item, op)} шт</span> : null}
                </>}</td>
                <td className="mb-plan">{operationPlanText(op)}</td>
              </tr>;
            })}</tbody>
          </table>
        </div>;
      })}
      {/* КБ с выданной техкартой без задания, из которой задание не строится, — справкой с причиной. */}
      {!onlyFree && skipped.map((item) => <div className="mb-ready" key={item.techcard_id}>
        <span><b>{kbLabel(item.project, item.index)}</b>{item.name ? ` · ${item.name}` : ""}<small> · {item.reason}</small></span>
      </div>)}
      {view === "stages" && !onlyFree && boardNow === null && projectReady.map((item) => <div className="mb-ready" key={item.techcard_id}>
        <span><b>{kbLabel(item.project, item.index)}</b>{item.name ? ` · ${item.name}` : ""}<small> · техкарта выдана, задания ещё нет · этапов {item.operations}, тираж {item.run || "—"}{item.deadline ? `, срок ${formatDay(item.deadline)}` : ""}</small></span>
      </div>)}
    </div>;
  }

  const weekNav = <>
    <span className="mb-period" title="Период доски">{periodWords(days[0], days[days.length - 1])}</span>
    <div className="mb-week-nav">
      <button className="secondary small" onClick={() => setWeekStart(addDays(weekStart, -WEEK_DAYS))}>← неделя</button>
      <button className="secondary small" onClick={() => setWeekStart(today)} disabled={weekStart === today}>Сегодня</button>
      <button className="secondary small" onClick={() => setWeekStart(addDays(weekStart, WEEK_DAYS))}>неделя →</button>
    </div>
  </>;

  // Неделя монтажников. На странице — обзор (день — раскладка по часам); в окне операции — с выбранной операцией:
  // день и час ставят её монтажнику. Раскрытый день — в той сетке, где сейчас работают.
  function renderWeek(inWindow: boolean) {
    const target = inWindow && task && operation ? { task, operation } : null;
    const active = inWindow === Boolean(task && operation);
    return <table className="mb-week">
      <thead><tr><th>Монтажник</th>{days.map((day) => {
        const date = new Date(`${day}T00:00:00`);
        const working = workingDay(day, workShift, holidays);
        return <th key={day} className={`${working ? "" : "off"}${day === today ? " today" : ""}`}
          title={`${date.toLocaleDateString("ru-RU", { weekday: "short" })} ${formatDay(day)}${day === today ? " · сегодня" : working ? "" : " · выходной"}`}>
          <b>{formatDay(day)}</b><span>{date.toLocaleDateString("ru-RU", { weekday: "short" })}</span>
        </th>;
      })}</tr></thead>
      <tbody>{people.map((person) => {
        const op = target?.operation;
        const on = Boolean(op?.operator_ids.includes(person.id));
        const skill = op?.code ? skills.find((item) => item.operator_id === person.id && item.operation_code === op.code) : undefined;
        const coded = Boolean(op?.code && skills.some((item) => item.operation_code === op.code));
        const expanded = active && openDay?.personId === person.id ? openDay.day : "";
        return <Fragment key={person.id}><tr className={`${on ? "on" : ""}${expanded ? " open" : ""}`}>
          <th scope="row"><b>{on ? "✓ " : ""}{person.name}</b><small>{person.site}{coded && (!skill || !skill.allowed) ? <span className="danger-text"> · нет допуска</span> : null}</small></th>
          {days.map((day) => {
            const absent = absences.find((item) => item.operator_id === person.id && item.from_date <= day && day <= item.to_date);
            const working = workingDay(day, workShift, holidays);
            const load = working && !absent ? dayHours(person.id, day, tasks, workShift, loadClock) : 0;
            const late = working && !absent ? overrunsOn(person.id, day, tasks, workShift, loadClock) : [];
            const title = `${person.name}, ${formatDay(day)}: ${absent ? absent.kind : !working ? "нерабочий день" : `${oneDecimal(load)} из ${oneDecimal(capacity)} ч`}${late.length ? ` · затянулось: ${late.join("; ")}` : ""}`;
            // Та же ячейка, что в «Загрузке по людям» производственного плана (РП-4): свободный — пусто, выходной — штриховка.
            return <LoadCell key={day} hours={load} capacity={capacity} working={working} absence={absent?.kind}
              today={day === today} picked={expanded === day} title={title} label={`День ${person.name} ${formatDay(day)}`}
              onClick={working && !absent && active ? () => openPersonDay(person.id, day, expanded !== day) : undefined} />;
          })}
        </tr>
        {expanded && <tr className="mb-day-row"><td colSpan={days.length + 1}>{renderDay(person, expanded, target)}</td></tr>}
        </Fragment>;
      })}</tbody>
    </table>;
  }

  function renderDay(person: ProductionOperator, expanded: string, target: { task: ProductionTask; operation: ProductionOperation } | null) {
    const operation = target?.operation ?? null;
    const task = target?.task ?? null;
    // Своя операция не считается занятостью: её можно передвинуть на другой час.
    const slots = daySlots(person.id, expanded, tasks, workShift, operation?.id ?? "", loadClock);
    const earliest = task && operation ? earliestStart(person.id, task, operation, new Date()) : Date.now();
    const past = (slot: DaySlot) => dayMoment(expanded, minutes(slot.finish)) <= earliest;
    const startOf = (slot: DaySlot) => { const time = Math.max(dayMoment(expanded, minutes(slot.start)), earliest); return workingMoment(expanded, time, workShift) ?? time; };
    const pick = (start: number) => { assign(person.id, start); setOpenDay(null); };
    const rate = operation ? rateOf(skills, operation.code, person.id) : 1;
    const seconds = operation ? Number(operation.setup_seconds) + Number(operation.seconds_per_unit) * quantity / rate : 0;
    const append = operation && canEdit && quantity ? appendStart(person.id, expanded, tasks, workShift, operation.id, earliest, loadClock) : null;
    const until = append != null ? workFinish(append, seconds, workShift, holidays) : null;
    const hasFree = slots.some((slot) => !past(slot) && !slot.rest && !slot.busy.length);
    // Уже назначено этому монтажнику: показываем окно; начатую долю мастер не переставляет.
    const placed = operation ? sharesOf(operation, person.id).find((share) => share.planned_start && share.planned_finish) : undefined;
    const locked = Boolean(placed && (placed.started_at || placed.finished_at));
    // Уже стоит в этом дне — двигают кликом по часу, отдельная кнопка не нужна.
    const here = Boolean(placed && isoDay(new Date(placed.planned_start!)) <= expanded && expanded <= isoDay(new Date(placed.planned_finish!)));
    // Окно для проверки станков: уже стоит в этом дне — его окно, иначе то, что предлагает кнопка.
    const machineWindow = placed && here ? { start: new Date(placed.planned_start!).getTime(), finish: new Date(placed.planned_finish!).getTime(), quantity: placed.quantity }
      : !locked && append != null && until != null ? { start: append, finish: until, quantity } : null;
    return <div className="mb-day">
      <div className="mb-day-head"><b>{person.name} · {new Date(`${expanded}T00:00:00`).toLocaleDateString("ru-RU", { weekday: "short" })} {formatDay(expanded)}</b>
        <span>занято {oneDecimal(dayHours(person.id, expanded, tasks, workShift, loadClock))} из {oneDecimal(capacity)} ч</span>
        <button className="mb-day-close" aria-label="Закрыть день" onClick={() => setOpenDay(null)}>×</button></div>
      <div className="mb-hours">{slots.map((slot) => {
        const can = Boolean(operation && canEdit && quantity && !locked && !past(slot) && !slot.rest && !slot.busy.length);
        const state = slot.rest ? "rest" : slot.busy.length ? "busy" : slot.own ? "own" : "free";
        const hint = slot.rest ? "перерыв" : slot.busy.length ? slot.busy.join("; ") : slot.own ? "эта операция" : "свободно";
        // Неполный час (конец смены 17:00–17:30) подписан диапазоном, чтобы было видно, где кончается смена.
        const label = minutes(slot.finish) - minutes(slot.start) < 60 ? `${slot.start}–${slot.finish}` : slot.start;
        return <div key={slot.start} className={`mb-hour ${state}${past(slot) ? " past" : ""}`} title={`${slot.start}–${slot.finish}: ${hint}`}>
          {can
            ? <button disabled={Boolean(busy)} aria-label={`Назначить ${person.name} на ${formatDay(expanded)} с ${at(startOf(slot))}`} onClick={() => pick(startOf(slot))}>{label}</button>
            : <span>{label}</span>}
          <small>{slot.rest ? "обед" : slot.busy.length ? slot.busy[0] : slot.own ? "эта операция" : slot.pause ?? ""}</small>
        </div>;
      })}</div>
      {operation && task && canEdit && <div className="mb-day-hint">
        {placed && <b className="mb-placed">Назначено: {windowText(placed.planned_start!, placed.planned_finish!)}{locked ? " · уже взял(а)" : here ? " · передвинуть — нажмите свободный час" : ""}</b>}
        {locked || here ? null : append != null && until != null
          ? <><button className="primary small" disabled={Boolean(busy)} onClick={() => pick(append)}>{placed ? "Переставить" : "Назначить"} на {formatDay(expanded)} с {at(append)}</button>
            <b className="mb-until">до {isoDay(new Date(until)) === expanded ? at(until) : `${formatDay(isoDay(new Date(until)))} ${at(until)}`}</b></>
          : <span className="danger-text">{hasFree ? "После работы этого дня места нет — нажмите свободный час или выберите другой день." : "Свободных часов в этот день нет — выберите другой день."}</span>}
        <span>Не влезает в день — хвост уйдёт на следующий рабочий день, работа монтажника после неё сдвинется.</span>
        {!task.pending && machineWindow && <MachineWarning taskId={task.id} operationId={operation.id} operatorId={person.id}
          quantity={machineWindow.quantity} start={machineWindow.start} finish={machineWindow.finish} />}
      </div>}
    </div>;
  }

  function renderOperation(task: ProductionTask, operation: ProductionOperation) {
    const crew = operation.operator_ids.map((id) => operators.find((item) => item.id === id)?.name ?? "?");
    return <div className="panel mb-op-window">
      <div className="manual-stock-head mb-window-head">
        <div className="mb-op-title">
          <h2>{operation.position}. {operationName(operation)}{isPfOperation(operation) ? ` — ${pfOpsText(operation)}` : ""}</h2>
          <small>{task.pending ? "новое задание" : task.number} · {taskItemLabel(task)} × {task.quantity} · норма {hours(operation.norm_seconds)}
            {" · "}{crew.length ? `исполнители: ${crew.join(", ")}` : "не назначено"}{operationPlanText(operation) ? ` · ${operationPlanText(operation)}` : ""}</small>
        </div>
        {!task.pending && <button className="link-button" onClick={() => openTask(task.id, operation.id)}>Точное время — на Ганте</button>}
        <button className="secondary small" onClick={() => select(task, null)}>Закрыть</button>
      </div>
      {banners}
      {site !== HANDOVER_SITE && canEdit && <div className="mb-target">
        <label className="mb-pieces">Штук монтажнику<input type="number" min={1} max={task.quantity} step={1} value={pieces} aria-label="Штук монтажнику" onChange={(event) => setPieces(event.target.value)} /><span>из {task.quantity}{free > 0 && free < task.quantity ? ` · не распределено ${free}` : ""} · норма {hours(Number(operation.setup_seconds) + Number(operation.seconds_per_unit) * quantity)}</span></label>
        {quantity > free && free < task.quantity && <small>Сверх нераспределённых штуки заберём у того, кто заканчивает позже.</small>}
        {people.length > 0 && onlyPerson && <div className="mb-only" role="group" aria-label="Весь этап одному монтажнику">
          <label>Только<select aria-label="Монтажник на весь этап" value={onlyPerson.id} onChange={(event) => setOnlyWho(event.target.value)}>
            {people.map((person) => <option key={person.id} value={person.id}>{person.name}</option>)}
          </select></label>
          <label>с дня<input type="date" aria-label="День начала" min={today} value={onlyDay || onlySlot?.day || today} onChange={(event) => setOnlyDay(event.target.value)} /></label>
          <button className="primary small" disabled={Boolean(busy) || Boolean(onlyBlock) || !onlySlot} onClick={assignOnly}>
            {onlySlot ? `Назначить только ${onlyPerson.name} на ${formatDay(onlySlot.day)} с ${at(onlySlot.start)}` : `Назначить только ${onlyPerson.name}`}
          </button>
          {(onlyBlock || !onlySlot) && <small className="danger-text">{onlyBlock || "Свободного времени в ближайшие 60 дней нет — выберите другой день или монтажника."}</small>}
          {!onlyBlock && onlySlot && operation.operator_ids.some((id) => id !== onlyPerson.id) && <small>Остальные исполнители будут сняты — все {task.quantity} шт получит {onlyPerson.name}.</small>}
          {!onlyBlock && onlySlot && !task.pending && <MachineWarning taskId={task.id} operationId={operation.id} operatorId={onlyPerson.id} quantity={task.quantity} start={onlySlot.start} finish={onlyFinish} />}
        </div>}
        {madeMax > 0 && <div className="mb-only mb-made" role="group" aria-label="Уже сделано без терминала">
          <b>Уже сделано</b>
          <label>чьи<select aria-label="Чьи штуки" value={madePerson} onChange={(event) => setMadeWho(event.target.value)}>
            {operation.operator_ids.map((id) => <option key={id} value={id}>{operators.find((item) => item.id === id)?.name ?? "?"}</option>)}
            <option value="">без исполнителя</option>
          </select></label>
          <label>штук<input type="number" min={1} max={madeMax} step={1} aria-label="Готовых штук" value={madePieces} onChange={(event) => setMadePieces(event.target.value)} /></label>
          <span>осталось {madeMax}</span>
          {Boolean(operation.inputs?.length) && <label><input type="checkbox" checked={madeFill} onChange={(event) => setMadeFill(event.target.checked)} />пропущенные этапы-входы отбить готовыми</label>}
          <button className="primary small" disabled={Boolean(busy) || !(madeCount > 0) || madeCount > madeMax} onClick={recordMade}>
            {madeCount > 0 ? `Записать готовыми ${madeCount} шт` : "Записать готовыми"}
          </button>
          {task.pending && <small>Задание {taskItemLabel(task)} × {task.quantity} создастся вместе с записью.</small>}
        </div>}
      </div>}
      {site === HANDOVER_SITE
        ? <div className="mb-target muted">{HANDOVER_SITE} работает без раскладки по людям и часам.</div>
        : <section className="mb-people mb-window-week" aria-label="Монтажники на операцию">
          <div className="mb-head"><h3>Монтажники</h3>{canEdit && <span className="mb-count">нажмите на день — час и «Назначить»</span>}{weekNav}</div>
          {renderWeek(true)}
          <LoadLegend capacity={capacity} late={false} />
        </section>}
    </div>;
  }

  // Тиражи новых заданий проекта, отличные от тиража КБ, — в автоплан (задания создаст «Применить»).
  const autoplanRuns = Object.fromEntries(futureTasks.filter((item) => item.project === autoplanFor && item.quantity !== fullRun.get(item.id))
    .map((item) => [item.techcard_id ?? "", item.quantity]));

  return <div className="master-board">
    <section className="panel mb-projects" aria-label="Проекты и операции">
      <div className="mb-head">
        <h2>Проекты и операции</h2>
        <span className="mb-count">{unassigned ? `без исполнителя: ${unassigned}` : "всё распределено"}</span>
        <label className="mb-toggle"><input type="checkbox" checked={onlyFree} onChange={(event) => setOnlyFree(event.target.checked)} />только нераспределённые</label>
      </div>
      {projects.length > 0 && <div className="mb-today" role="group" aria-label="Сегодня">
        <b>Сегодня</b>
        {(["late", "wait", "over"] as OpFlag[]).map((flag) => <button key={flag} type="button" aria-pressed={flagFilter === flag}
          className={`mb-today-chip ${flag === "wait" ? "warn" : "danger"}${flagCounts[flag] ? "" : " zero"}${flagFilter === flag ? " on" : ""}`}
          onClick={() => setFlagFilter(flagFilter === flag ? null : flag)}>{FLAG_TEXT[flag]}: <b>{flagCounts[flag]}</b></button>)}
        {flagFilter && <button type="button" className="link-button" onClick={() => setFlagFilter(null)}>Сбросить</button>}
      </div>}
      {!projects.length && <div className="empty">Проектов с выданной техкартой пока нет.</div>}
      {projects.length > 0 && <div className="tiles kit-tiles mb-tiles">{projects.map((project) => {
        if (onlyFree && !freeOf(project)) return null;
        if (flagFilter && !projectHasFlag(project)) return null;
        const tile = tiles.items.find((item) => item.project.toLowerCase() === project.toLowerCase());
        const extra = <div className="mb-tile-extra">{tileFacts(project)}</div>;
        return tile
          ? <KitTile key={project} item={tile} statuses={tiles.statuses} canEdit={false} open={() => setOpenProject(project)} extra={extra} />
          : <div key={project} className="tile kit-tile" role="button" tabIndex={0} onClick={() => setOpenProject(project)} onKeyDown={(event) => { if (event.key === "Enter") setOpenProject(project); }}>
            <div className="tile-body"><div className="kit-tile-head"><strong className="t-h">{project}</strong></div>{extra}</div>
          </div>;
      })}</div>}
      {flagFilter && projects.length > 0 && !projects.some(projectHasFlag) && <div className="empty">Нет операций с отметкой «{FLAG_TEXT[flagFilter]}».</div>}
    </section>
    <aside className="panel mb-people" aria-label="Монтажники" ref={board.ref}>
      <div className="mb-head">
        <h2>Монтажники</h2><span className="mb-count">{site ? `${site} · ` : ""}{people.length}</span>
        {site !== HANDOVER_SITE && weekNav}
      </div>
      {site === HANDOVER_SITE
        ? <div className="mb-target muted">{HANDOVER_SITE} работает без раскладки по людям и часам: сюда падают задания с пометкой «Отдано в {HANDOVER_SITE}».</div>
        : <div className="mb-target muted">{canEdit ? "Назначить — в проекте: нажмите операцию. День у монтажника — раскладка по часам." : "Нажмите на день — раскладка по часам."}</div>}
      {site !== HANDOVER_SITE && renderWeek(false)}
      {site !== HANDOVER_SITE && <LoadLegend capacity={capacity} late={false} />}
      {site !== HANDOVER_SITE && <section className="mb-machines" aria-label="Загрузка станков" style={{ marginTop: 18 }}>
        <div className="mb-head"><h2>Загрузка станков</h2><span className="mb-count">процент смены всех единиц станка</span></div>
        <MachineLoadGrid days={days} holidays={holidays} shift={workShift} refreshKey={planVersion} />
      </section>}
    </aside>
    {shown && <PageWindow label={`Проект ${shown}`} width={1600} close={() => setOpenProject("")}>{renderProject(shown)}</PageWindow>}
    {shown && task && operation && <PageWindow label={`Операция ${operation.position}. ${operationName(operation)}`} width={1600} close={() => select(task, null)}>
      {renderOperation(task, operation)}
    </PageWindow>}
    {autoplanFor && canEdit && <AutoplanPanel projectDeadlines={projectDeadlines} preset={{ project: autoplanFor, runs: autoplanRuns }}
      close={() => setAutoplanFor("")}
      onApplied={(count, created) => {
        setAutoplanFor("");
        setNote({ refused: [], warnings: [`Назначено ${count} операций${created ? `, создано заданий: ${created}` : ""}`] });
        void mutate("autoplan", async () => { setBoardTick((value) => value + 1); return {}; });
      }} />}
    {(() => {
      const item = removing ? live.find((entry) => entry.id === removing.taskId) : undefined;
      const op = item?.operations.find((entry) => entry.id === removing?.operationId);
      if (!removing || !item || !op) return null;
      const name = operators.find((entry) => entry.id === removing.personId)?.name ?? "Монтажник";
      return <PageWindow label={`Снять ${name} с этапа`} width={560} dismissable close={() => setRemoving(null)}>
        <div className="panel mb-remove">
          <div className="manual-stock-head"><h2>Снять {name} с этапа {op.position}?</h2></div>
          <p><b>{item.number} · {taskItemLabel(item)}</b> · этап {op.position} «{operationName(op)}»</p>
          <ul>{removalChanges(op, removing.personId, operators, item).map((line) => <li key={line}>{line}</li>)}</ul>
          <div className="form-actions">
            <button className="secondary" onClick={() => setRemoving(null)}>Отмена</button>
            <button className="danger" disabled={Boolean(busy)} onClick={() => unassign(item, op, removing.personId)}>Снять {name}</button>
          </div>
        </div>
      </PageWindow>;
    })()}
  </div>;
}
