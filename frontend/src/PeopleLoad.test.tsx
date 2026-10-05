import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ProductionAbsence, ProductionOperator, ProductionShift, ProductionTask } from "./api";
import { PeopleLoadGrid, absenceLabel, dayPart } from "./PeopleLoad";

const shift: ProductionShift = { weekdays: [0, 1, 2, 3, 4], start: "09:00", finish: "17:00", breaks: [], effective_hours: "7" };
const busy = { id: "o1", name: "Иванов Иван", site: "ЦАР" } as unknown as ProductionOperator;
const idle = { id: "o2", name: "Петров Пётр", site: "ЦАР" } as unknown as ProductionOperator;
const share = { operator_id: "o1", quantity: 10, planned_start: "2026-10-05T16:08:00", planned_finish: "2026-10-06T15:57:00", started_at: null, finished_at: null };
const task = {
  id: "t1", number: "ПЗ-1", project: "713К", item: "КБ1", status: "Запущено", handed_to: "", deadline: null,
  operations: [{ id: "op1", position: 1, stage: "Опрессовка", description: "", state: "не начата", operator_ids: ["o1"], shares: [share], planned_start: share.planned_start, planned_finish: share.planned_finish }],
} as unknown as ProductionTask;

beforeEach(() => { vi.useFakeTimers({ toFake: ["Date"] }); vi.setSystemTime(new Date("2026-10-05T10:00:00")); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

const renderGrid = (absences: ProductionAbsence[] = []) => render(<PeopleLoadGrid operators={[busy, idle]} tasks={[task]} absences={absences} holidays={[]} shift={shift} week={0} onWeekChange={() => {}} />);

it("свободная рабочая ячейка пустая, «0/7» только в подсказке", () => {
  renderGrid();
  const cell = screen.getByTitle("Петров Пётр, 05.10: 0 из 7 ч");
  expect(cell.textContent).toBe("");
});

it("отсутствие подписано полным словом", () => {
  expect(absenceLabel("Отпуск")).toBe("отпуск");
  expect(absenceLabel("Больничный")).toBe("больн.");
  renderGrid([{ operator_id: "o2", from_date: "2026-10-05", to_date: "2026-10-05", kind: "Отпуск" }]);
  expect(screen.getByTitle("Петров Пётр, 05.10: Отпуск").textContent).toBe("отпуск");
});

it("панель дня показывает часть операции в этот день и всё окно отдельной строкой", () => {
  renderGrid();
  fireEvent.click(screen.getByLabelText("Загрузка Иванов Иван 05.10"));
  const text = screen.getByRole("group").textContent ?? "";
  expect(text).toContain("16:08–17:00");
  expect(text).toContain("продолжение вт");
  expect(text).toContain("вся операция 05.10 16:08 – 06.10 15:57");
});

it("dayPart режет окно сменой дня", () => {
  const item = { start: new Date("2026-10-05T16:08:00").getTime(), finish: new Date("2026-10-06T15:57:00").getTime() } as Parameters<typeof dayPart>[0];
  const second = dayPart(item, "2026-10-06", shift);
  expect(new Date(second.start).getHours()).toBe(9);
  expect(second.resumed).toBe("пн");
  expect(second.continues).toBe("");
});
