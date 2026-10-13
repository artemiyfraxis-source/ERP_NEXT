import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ShipDatePlan, type ShipPlan } from "./ShipDatePlan";

const A = "10000000-0000-0000-0000-00000000000a";
const task = (n: number, item: string, extra: Partial<ShipPlan["tasks"][number]> = {}) => ({
  task_id: `70000000-0000-0000-0000-00000000000${n}`, number: `ПЗ-000${n}`, new: false, project: "708К", item, name: "Жгут",
  quantity: 50, latest_start: "2026-10-08T09:00:00+03:00", latest_finish: "2026-10-09T17:30:00+03:00", slack_seconds: 72_000, ...extra,
});

const fits: ShipPlan = {
  ship_date: "2026-10-09", start: "2026-10-06T09:00:00+03:00", due: "2026-10-09T17:30:00+03:00", fits: true, assigned_finish: null,
  start_by: "2026-10-08T09:00:00+03:00", slack_seconds: 57_600,
  tasks: [task(1, "КБ1"), task(2, "КБ2", { new: true, quantity: 10 })],
  late: [], unplaced: [], warnings: [],
  first_delivery: [
    { kind: "items", ready: "2026-10-06T11:42:00+03:00", share: 0.091, lots: [{ ...task(2, "КБ2"), lot: 10, total: 10 }] },
    { kind: "share", ready: "2026-10-06T14:00:00+03:00", share: 0.1, lots: [{ ...task(1, "КБ1"), lot: 5, total: 50 }, { ...task(2, "КБ2"), lot: 1, total: 10 }] },
    { kind: "full", ready: "2026-10-12T12:00:00+03:00", share: 1, lots: [] },
  ],
};

const late: ShipPlan = {
  ...fits, fits: false, slack_seconds: -57_600, start_by: "2026-10-01T09:00:00+03:00",
  tasks: [task(1, "КБ1", { slack_seconds: -57_600, latest_start: "2026-10-01T09:00:00+03:00" })],
  late: [{ ...task(1, "КБ1"), position: 1, stage: "Опрессовка терминалов", code: "PRS", operator_name: "Иванов И.", latest_start: "2026-10-01T09:00:00+03:00", latest_finish: "2026-10-02T13:00:00+03:00" }],
  first_delivery: [],
};

afterEach(() => vi.restoreAllMocks());

function mockShip(body: ShipPlan) {
  const calls: Array<Record<string, unknown>> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input) !== "/api/production/autoplan/ship-plan") throw new Error(`Unexpected request: ${String(input)}`);
    calls.push(JSON.parse(String(init?.body)));
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  return calls;
}

it("plans back from the project deadline and offers the first delivery", async () => {
  const calls = mockShip(fits);
  render(<ShipDatePlan project="708К" people={[A]} deadline="2026-10-09" />);

  expect(screen.getByLabelText("Дата отгрузки")).toHaveValue("2026-10-09");
  fireEvent.click(screen.getByRole("button", { name: "Рассчитать к 09.10.2026" }));

  expect(await screen.findByText("Успевает к 09.10: начать не позже 08.10 09:00, запас 16 ч")).toBeInTheDocument();
  expect(calls).toEqual([{ mode: "project", project: "708К", people: [A], ship_date: "2026-10-09" }]);
  expect(screen.getByText("новое задание")).toBeInTheDocument();
  expect(screen.getByText("Целиком: КБ2 × 10")).toBeInTheDocument();
  expect(screen.getByText("Комплекты: КБ1 × 5 из 50, КБ2 × 1 из 10")).toBeInTheDocument();
  expect(screen.getByText("Весь проект")).toBeInTheDocument();
  expect(screen.getByText("раньше всего")).toBeInTheDocument();
  // Полная отгрузка позже даты отгрузки — видно в строке.
  expect(screen.getByText("позже отгрузки")).toBeInTheDocument();
  expect(screen.getByText("9 %")).toBeInTheDocument();
});

it("shows where the plan does not fit and how much time is missing", async () => {
  mockShip(late);
  render(<ShipDatePlan project="708К" people={[A]} deadline="2026-10-09" />);
  fireEvent.click(screen.getByRole("button", { name: "Рассчитать к 09.10.2026" }));

  expect(await screen.findByText("Не успевает к 09.10: не хватает 16 ч рабочего времени — начинать надо было 01.10 09:00")).toBeInTheDocument();
  expect(screen.getByText("не хватает 16 ч", { selector: "td" })).toBeInTheDocument();
  expect(screen.getByText("Где не успеваем · 1 этап")).toBeInTheDocument();
  expect(screen.getByText("КБ1 · этап 1")).toBeInTheDocument();
});

it("says when all the project's work is already assigned", async () => {
  mockShip({ ...fits, fits: false, tasks: [], first_delivery: [], start_by: null, slack_seconds: 0, assigned_finish: "2026-10-12T15:00:00+03:00" });
  render(<ShipDatePlan project="708К" people={[A]} deadline="2026-10-09" />);
  fireEvent.click(screen.getByRole("button", { name: "Рассчитать к 09.10.2026" }));

  expect(await screen.findByText("Вся работа проекта уже назначена и кончится 12.10 15:00 — к 09.10 не успевает")).toBeInTheDocument();
});

it("needs people and a date before it can calculate", () => {
  mockShip(fits);
  const { rerender } = render(<ShipDatePlan project="708К" people={[]} deadline="2026-10-09" />);
  expect(screen.getByRole("button", { name: "Рассчитать к 09.10.2026" })).toBeDisabled();
  // Проект без срока: дату отгрузки выбирают сами.
  rerender(<ShipDatePlan project="815Р" people={[A]} deadline={null} />);
  expect(screen.getByRole("button", { name: "Рассчитать" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Дата отгрузки"), { target: { value: "2099-10-20" } });
  expect(screen.getByRole("button", { name: "Рассчитать к 20.10.2099" })).not.toBeDisabled();
});
