import { describe, expect, it } from "vitest";
import type { ProductionShift, ProductionTask } from "./api";
import {
  appendStart, dayHours, daySlots, defaultPieces, earliestStart, overrunsOn, remainderOf, workFinish, type LoadClock,
} from "./MasterBoard";

const shift: ProductionShift = { weekdays: [0, 1, 2, 3, 4], start: "09:00", finish: "17:30", breaks: [["13:00", "13:30"]], effective_hours: "7" };
const installer = "10000000-0000-0000-0000-000000000002";
const operation = {
  id: "op-1", position: 1, code: "CUT", stage: "Автомат резка + опрессовка", description: "",
  seconds_per_unit: "5.04", setup_seconds: "0", norm_seconds: 13680, operator_ids: [installer],
  planned_start: "2026-09-30T10:00:00", planned_finish: "2026-09-30T14:50:00", assigned_by: "Мастер",
  shares: [{ operator_id: installer, quantity: null, planned_start: "2026-09-30T10:00:00", planned_finish: "2026-09-30T14:50:00", started_at: null, finished_at: null }],
};
const task = {
  id: "task-1", number: "ПЗ-0002", version: 3, created_at: "2026-09-30T08:00:00", project: "713К.2.1", item: "КБ1",
  name: "", quantity: 2500, fulfillment_request_id: null, deadline: null, status: "Запущено", launched_by: "Мастер",
  handed_to: "", planned_start: null, planned_finish: null, assigned: 1,
  operations: [operation, { ...operation, id: "op-2", position: 2, code: "INS", stage: "Монтаж терминалов (А)", norm_seconds: 20160, operator_ids: [], shares: [], planned_start: null, planned_finish: null }],
} as unknown as ProductionTask;
const at = (value: string) => new Date(value).getTime();
const clockOf = (value: number) => new Date(value).toTimeString().slice(0, 5);

describe("неделя монтажников: длинная операция в частично занятый день (скрин владельца, 30.09)", () => {
  it("ставит после работы дня и уводит хвост на следующий рабочий день", () => {
    const earliest = earliestStart(installer, task, task.operations[1], new Date("2026-09-30T09:47:00"));
    // Этап 2 не раньше своего этапа 1 у того же монтажника.
    expect(clockOf(earliest)).toBe("14:50");
    const start = appendStart(installer, "2026-09-30", [task], shift, "op-2", earliest);
    expect(start && clockOf(start)).toBe("14:50");
    // 5,6 ч чистых = 6 ч 24 мин на часах: 14:50–17:30 и хвост 01.10 9:00–12:44.
    const finish = workFinish(start!, 20160, shift, []);
    expect(new Date(finish).getDate()).toBe(1);
    expect(clockOf(finish)).toBe("12:44");
  });

  it("округляет «сейчас» вверх до четверти часа и перепрыгивает обед и выходные", () => {
    expect(clockOf(earliestStart(installer, task, task.operations[0], new Date("2026-09-30T09:47:00")))).toBe("10:00");
    expect(clockOf(appendStart(installer, "2026-10-01", [task], shift, "", at("2026-10-01T13:10:00"))!)).toBe("13:30");
    // Пятница 16:30 + 2 ч чистых (2 ч 18 мин на часах): до 17:30 час, остаток в понедельник 9:00–10:18.
    const finish = workFinish(at("2026-10-02T16:30:00"), 7200, shift, []);
    expect(new Date(finish).getDay()).toBe(1);
    expect(clockOf(finish)).toBe("10:18");
  });

  it("считает нераспределённые штуки только у раздробленной операции", () => {
    const split = { ...operation, shares: [{ ...operation.shares[0], quantity: 1500 }] };
    expect(remainderOf(task, split)).toBe(1000);
    expect(remainderOf(task, operation)).toBe(0);
    expect(remainderOf(task, task.operations[1])).toBe(2500);
  });

  it("последняя ячейка дня — неполный час до конца смены 17:30", () => {
    const slots = daySlots(installer, "2026-10-01", [task], shift);
    expect(slots.map((slot) => slot.start)).toEqual(["09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00"]);
    expect(slots[slots.length - 1].finish).toBe("17:30");
    expect(slots[4].pause).toBe("обед до 13:30");
  });

  it("показывает выбранную операцию в её часах, не считая занятостью (скрин владельца: «не выставилось»)", () => {
    const slots = daySlots(installer, "2026-09-30", [task], shift, "op-1");
    expect(slots.filter((slot) => slot.own).map((slot) => slot.start)).toEqual(["10:00", "11:00", "12:00", "13:00", "14:00"]);
    expect(slots.every((slot) => slot.busy.length === 0)).toBe(true);
  });

  it("по умолчанию добавляет нового монтажника к назначенным, а не вытесняет их", () => {
    // Операция целиком у одного: новому — половина, самому назначенному — его доля.
    expect(defaultPieces(task, operation)).toBe(1250);
    expect(defaultPieces(task, operation, installer)).toBe(2500);
    const split = { ...operation, shares: [{ ...operation.shares[0], quantity: 1500 }] };
    expect(defaultPieces(task, split)).toBe(1000);
    expect(defaultPieces(task, task.operations[1])).toBe(2500);
  });
});

describe("сетевой график: свои этапы монтажника, до которых операции есть дело", () => {
  const plan = (id: string, position: number, from: string, to: string, inputs: Array<{ operation_id: string; position: number; per_unit: string; transfer_batch: null }>) => ({
    ...operation, id, position, planned_start: `2026-09-30T${from}:00`, planned_finish: `2026-09-30T${to}:00`, inputs,
    shares: [{ operator_id: installer, quantity: null, planned_start: `2026-09-30T${from}:00`, planned_finish: `2026-09-30T${to}:00`, started_at: null, finished_at: null }],
  });
  const network = {
    ...task,
    operations: [
      plan("op-1", 1, "10:00", "11:00", []),
      plan("op-2", 2, "11:00", "15:00", []),
      { ...plan("op-3", 3, "09:00", "10:00", [{ operation_id: "op-1", position: 1, per_unit: "1", transfer_batch: null }]), operator_ids: [], shares: [] },
    ],
  } as unknown as ProductionTask;

  it("ждёт только свои входы: соседняя ветка с меньшим номером не задерживает", () => {
    const now = new Date("2026-09-30T09:00:00");
    expect(clockOf(earliestStart(installer, network, network.operations[2], now))).toBe("11:00");
  });

  it("ответ без входов операций — как цепочка: ждёт все меньшие номера", () => {
    const chain = { ...network, operations: network.operations.map((item) => ({ ...item, inputs: undefined })) } as unknown as ProductionTask;
    expect(clockOf(earliestStart(installer, chain, chain.operations[2], new Date("2026-09-30T09:00:00")))).toBe("15:00");
  });
});

// Владелец, 22.10: Рыбкина взяла ПФ-12 20.10 в 12:46 (план до 21.10 17:01, норма 10 ч на 500 шт) и не сдала —
// 22.10 её ячейка была пустой, и «Назначить» ставил новую работу с начала смены.
describe("затянувшаяся доля занимает монтажника от «сейчас» (loadWindow)", () => {
  const now = at("2026-10-22T09:00:00");
  const load: LoadClock = { now, holidays: [], absences: [] };
  const overdue = ({ output = "0", started = true, finished = false } = {}) => ({
    id: "task-12", number: "ПЗ-0012", version: 1, created_at: "2026-10-19T09:00:00", project: "713К.2.1", item: "КБ1",
    name: "", quantity: 500, status: "Запущено", handed_to: "", deadline: null,
    operations: [{
      id: "pf-12", position: 12, code: "PF", stage: "Ветвь X4 «А6», сторона А", description: "", etk_ops: [{ position: 1, code: "CUT", stage: "Резка" }],
      seconds_per_unit: "72", setup_seconds: "0", norm_seconds: 36000, operator_ids: [installer], required_quantity: "500",
      planned_start: "2026-10-20T12:46:00", planned_finish: "2026-10-21T17:01:00", assigned_by: "Мастер", state: Number(output) ? "в работе" : "не начата",
      fact: { good: output, defect: "0", processed: output },
      shares: [{
        operator_id: installer, quantity: 500, planned_start: "2026-10-20T12:46:00", planned_finish: "2026-10-21T17:01:00",
        started_at: started ? "2026-10-20T12:46:00" : null, finished_at: finished ? "2026-10-21T16:00:00" : null, output,
      }],
    }],
  }) as unknown as ProductionTask;

  it("ничего не сдано — весь день занят, место только 23.10 после остатка 10 ч", () => {
    expect(dayHours(installer, "2026-10-22", [overdue()], shift)).toBe(0);  // без «сейчас» — только истёкший план
    expect(dayHours(installer, "2026-10-22", [overdue()], shift, load)).toBeCloseTo(7);
    expect(appendStart(installer, "2026-10-22", [overdue()], shift, "", now, load)).toBeNull();
    // 10 ч чистых = 11 ч 26 мин на часах: 22.10 целиком и 23.10 до 12:26.
    expect(clockOf(appendStart(installer, "2026-10-23", [overdue()], shift, "", now, load)!)).toBe("12:26");
  });

  it("остаток — по несданным штукам, не меньше часа работы", () => {
    // Сдано 450: 50 × 72 с = 1 ч чистых = 1 ч 09 мин на часах.
    expect(clockOf(appendStart(installer, "2026-10-22", [overdue({ output: "450" })], shift, "", now, load)!)).toBe("10:09");
    // Сдано 490: 12 мин нормы, но держит час.
    expect(clockOf(appendStart(installer, "2026-10-22", [overdue({ output: "490" })], shift, "", now, load)!)).toBe("10:09");
  });

  it("сданная и не взятая доли не занимают день", () => {
    expect(clockOf(appendStart(installer, "2026-10-22", [overdue({ finished: true })], shift, "", now, load)!)).toBe("09:00");
    expect(clockOf(appendStart(installer, "2026-10-22", [overdue({ started: false })], shift, "", now, load)!)).toBe("09:00");
    expect(dayHours(installer, "2026-10-22", [overdue({ started: false })], shift, load)).toBe(0);
  });

  it("часы дня и подпись ячейки — «затянулось: ПФ-12 …»", () => {
    const slots = daySlots(installer, "2026-10-22", [overdue({ output: "450" })], shift, "", load);
    expect(slots[0].busy).toEqual(["затянулось: ПФ-12 · Ветвь X4 «А6», сторона А · 713К.2.1 · КБ1"]);
    expect(slots[1].busy).toHaveLength(1);
    expect(slots[2].busy).toHaveLength(0);
    expect(overrunsOn(installer, "2026-10-22", [overdue()], shift, load)).toEqual(["ПФ-12 · Ветвь X4 «А6», сторона А · 713К.2.1 · КБ1"]);
  });

  it("по календарю: в отгул затянувшаяся работа ждёт монтажника", () => {
    const away: LoadClock = { ...load, absences: [{ operator_id: installer, from_date: "2026-10-22", to_date: "2026-10-22", kind: "Отгул" }] };
    expect(dayHours(installer, "2026-10-22", [overdue({ output: "450" })], shift, away)).toBe(0);
    expect(clockOf(appendStart(installer, "2026-10-23", [overdue({ output: "450" })], shift, "", now, away)!)).toBe("10:09");
  });
});
