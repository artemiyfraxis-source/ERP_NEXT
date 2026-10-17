import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ProductionAbsence, ProductionOperator, ProductionShift, ProductionTask } from "./api";
import { LoadLegend, PeopleLoadGrid, absenceLabel, dayPart, loadLevel, loadThresholdsText, shiftCapacity } from "./PeopleLoad";

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

it("цвет загрузки — по доле реальной смены: частично < 85 %, занята 85–105 %, перегруз > 105 % (ПП-9)", () => {
  expect(loadLevel(0, 8)).toBe("free");
  expect(loadLevel(5.6, 8)).toBe("low");
  expect(loadLevel(6.7, 8)).toBe("low");
  expect(loadLevel(6.8, 8)).toBe("full");
  expect(loadLevel(8.4, 8)).toBe("full");
  expect(loadLevel(8.5, 8)).toBe("over");
  expect(loadLevel(5.6, 7)).toBe("low");
  expect(loadLevel(6, 7)).toBe("full");
  expect(shiftCapacity({ effective_hours: "8" })).toBe(8);
  expect(shiftCapacity({ effective_hours: "" })).toBe(7);
  expect(shiftCapacity(undefined)).toBe(7);
});

it("легенда — примеры из реальной смены и пороги словами", () => {
  const { container } = render(<LoadLegend capacity={8} />);
  const cells = Array.from(container.querySelectorAll(".mb-cell")).map((item) => item.textContent);
  expect(cells.slice(1, 4)).toEqual(["4/8", "8/8", "10/8"]);
  expect(container.firstElementChild?.getAttribute("title")).toBe(loadThresholdsText(8));
  expect(loadThresholdsText(8)).toBe("Смена 8 ч. Частично — меньше 6,8 ч (85 % смены); смена занята — 6,8–8,4 ч (85–105 %); перегруз — больше 8,4 ч");
});

it("5,6 ч из 8-часовой смены — «частично», а не «смена занята»", () => {
  const long: ProductionShift = { ...shift, finish: "18:00", effective_hours: "8" };
  render(<PeopleLoadGrid operators={[busy]} tasks={[task]} absences={[]} holidays={[]} shift={long} week={0} onWeekChange={() => {}} />);
  const cell = screen.getByLabelText("Загрузка Иванов Иван 06.10").closest("td")!;
  expect(cell.textContent).toMatch(/\/8$/);
  expect(cell.className).toContain("low");
  expect(screen.getByText("8/8").className).toContain("full");
});
