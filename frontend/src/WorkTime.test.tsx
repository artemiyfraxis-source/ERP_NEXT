import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WorkTime } from "./WorkTime";
import type { WorkTimeView } from "./api";

function view(overrides: Partial<WorkTimeView> = {}): WorkTimeView {
  return {
    employee_id: "e1", month: "2026-10", label: "октябрь 2026", as_of: "2026-10-09",
    source: "по данным выработки", note: "Переработка справочная: не согласована и не оплачивается.",
    available: true, no_data_reason: null, shift_hours: 8,
    days: [
      { date: "2026-10-05", weekday: "пн", workday: true, absence: null, day_off: false, future: false, norm_hours: 8, worked_hours: 9.5, overtime_hours: 1.5 },
      { date: "2026-10-06", weekday: "вт", workday: true, absence: "отпуск", day_off: false, future: false, norm_hours: 0, worked_hours: 0, overtime_hours: 0 },
      { date: "2026-10-07", weekday: "ср", workday: true, absence: "Отгул", day_off: true, future: false, norm_hours: 0, worked_hours: 0, overtime_hours: 0 },
      { date: "2026-10-10", weekday: "сб", workday: false, absence: null, day_off: false, future: false, norm_hours: 0, worked_hours: 0, overtime_hours: 0 },
      { date: "2026-10-12", weekday: "пн", workday: true, absence: null, day_off: false, future: true, norm_hours: 8, worked_hours: null, overtime_hours: null },
    ],
    totals: { norm_hours: 16, worked_hours: 9.5, overtime_hours: 1.5 },
    balance: {
      year: 2026, from: "2026-01-01", to: "2026-10-31", overtime_hours: 12, day_off_days: 3, day_off_hours: 24,
      balance_hours: -12, state: "debt", label: "долг с отработкой",
    },
    ...overrides,
  };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function serve(answer: (path: string) => Response) {
  const seen: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    seen.push(String(input));
    return answer(String(input));
  });
  return seen;
}

afterEach(() => vi.restoreAllMocks());

it("shows the month with its year, the day table, totals and the year debt", async () => {
  const seen = serve(() => json(view()));
  render(<WorkTime />);

  expect(await screen.findByText("октябрь 2026")).toBeInTheDocument();
  expect(seen[0]).toBe("/api/employee-card/me/worktime");
  const days = screen.getByRole("region", { name: "Дни месяца" });
  const rows = within(days).getAllByRole("row");
  const monday = rows.find((row) => row.textContent?.startsWith("05.10"));
  expect(monday).toHaveTextContent("пн");
  expect(monday).toHaveTextContent("8");
  expect(monday).toHaveTextContent("9,5");
  expect(monday).toHaveTextContent("1,5");
  expect(rows.find((row) => row.textContent?.startsWith("06.10"))).toHaveTextContent("Отпуск");
  expect(rows.find((row) => row.textContent?.startsWith("07.10"))).toHaveTextContent("Отгул");
  expect(rows.find((row) => row.textContent?.startsWith("10.10"))).toHaveTextContent("Выходной");
  const future = rows.find((row) => row.textContent?.startsWith("12.10"));
  expect(future).toHaveClass("wt-future"); // будущий день приглушён и без «отработано»
  expect(within(future as HTMLElement).queryByText("0")).toBeNull();
  expect(document.body.textContent).not.toMatch(/2026-10-05|NaN|undefined|null/);

  const month = screen.getByRole("region", { name: "Месяц" });
  expect(within(month).getByText("16 ч")).toBeInTheDocument();
  expect(within(month).getByText("9,5 ч")).toBeInTheDocument();

  const balance = screen.getByRole("region", { name: "Баланс 2026 года" });
  expect(within(balance).getByText("-12 ч · долг с отработкой")).toHaveClass("tone-danger");
  expect(within(balance).getByText("3 дн. · 24 ч")).toBeInTheDocument();
  expect(within(balance).getByText("01.01.2026 – 31.10.2026")).toBeInTheDocument();
  expect(within(balance).getByText("по данным выработки; переработка справочная")).toBeInTheDocument();
});

it("marks overtime and zero balances in their own state", async () => {
  serve(() => json(view({ balance: { ...view().balance!, balance_hours: 6.5, state: "overtime", label: "переработка" } })));
  render(<WorkTime />);
  expect(await screen.findByText("+6,5 ч · переработка")).toHaveClass("tone-ok");
});

it("switches the month with the arrows and the picker, asking the server for that month", async () => {
  const seen = serve((path) => json(view(path.endsWith("2026-09") ? { month: "2026-09", label: "сентябрь 2026" } : {})));
  render(<WorkTime userId="u-2" />);

  await screen.findByText("октябрь 2026");
  expect(seen[0]).toBe("/api/employee-card/u-2/worktime");
  fireEvent.click(screen.getByRole("button", { name: "Предыдущий месяц" }));
  expect(await screen.findByText("сентябрь 2026")).toBeInTheDocument();
  expect(seen.at(-1)).toBe("/api/employee-card/u-2/worktime?month=2026-09");
  fireEvent.change(screen.getByLabelText("Месяц", { selector: "input" }), { target: { value: "2026-01" } });
  await waitFor(() => expect(seen.at(-1)).toBe("/api/employee-card/u-2/worktime?month=2026-01"));
});

it("says «Нет данных: …» with the server's reason instead of zeros", async () => {
  serve(() => json(view({
    available: false, no_data_reason: "Нет данных: площадка ЖУК работает без нормирования, время не учитывается.",
    days: [], totals: null, balance: null,
  })));
  render(<WorkTime />);

  expect(await screen.findByText("Нет данных: площадка ЖУК работает без нормирования, время не учитывается.")).toBeInTheDocument();
  expect(screen.getByText("октябрь 2026")).toBeInTheDocument(); // месяц выбирается и без данных
  expect(screen.queryByRole("region", { name: "Дни месяца" })).toBeNull();
  expect(screen.queryByRole("region", { name: /Баланс/ })).toBeNull();
});

it("shows the server's refusal when the month is not loaded", async () => {
  serve(() => json({ detail: "Месяц нужен в виде ГГГГ-ММ, например 2026-10." }, 422));
  render(<WorkTime />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Месяц нужен в виде ГГГГ-ММ");
});
