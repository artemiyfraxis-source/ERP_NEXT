import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProductionNormFact, hoursText, lastDays, pieceTime, ratioText } from "./ProductionNormFact";
import type { NormFactFact, NormFactReport } from "./normFactApi";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function serve(answer: (path: string) => Response) {
  const calls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    calls.push(String(input));
    return answer(String(input));
  });
  return calls;
}

afterEach(() => vi.restoreAllMocks());

const spread = { no_norm: 0, verdict: "медленнее" as const, actual_seconds: 36000, norm_seconds: 24000 };

function fact(overrides: Partial<NormFactFact> = {}): NormFactFact {
  return {
    task_id: "t1", task_number: "ПЗ-0001", project: "П3-0007", item: "КБ1", operation_id: "o1", position: 1,
    template: "CUT_WIRE+2crimp", operation_code: "CUT_WIRE+2crimp", stage: "Резка с опрессовкой",
    operator_id: "u1", operator_name: "Элина", started_at: "2026-10-20T09:00:00+03:00", finished_at: "2026-10-20T11:00:00+03:00",
    submissions: 1, actual_seconds: 7200, quantity: "100", norm_seconds: 4800, actual_per_piece: 72, norm_per_piece: 48, ratio: 1.5,
    ...overrides,
  };
}

function report(overrides: Partial<NormFactReport> = {}): NormFactReport {
  return {
    date_from: "2026-08-31", date_to: "2026-10-29", thresholds: { min_facts: 5, slow: 1.25, fast: 0.8 },
    templates: [
      { ...spread, template: "CUT_WIRE+2crimp", operation_code: "CUT_WIRE+2crimp", stage: "Резка с опрессовкой", facts: 6, enough: true,
        median: 1.5, low: 1.3, high: 1.6, seconds_per_unit: 48, suggestion: "Норма занижена: факт ×1,50 — предложение: штучное время 1 мин 12 с вместо 48 с", suggested_seconds_per_unit: 72 },
      { ...spread, verdict: "в норме", template: "Пайка", operation_code: "", stage: "Пайка", facts: 2, enough: false,
        median: 1.0, low: 0.95, high: 1.05, seconds_per_unit: 30, suggestion: "", suggested_seconds_per_unit: null },
    ],
    operators: [
      { ...spread, operator_id: "u1", operator_name: "Элина", facts: 6, enough: true, median: 1.5, low: 1.3, high: 1.6,
        relative: 2.0, coefficient: 0.5, suggestion: "Работает медленнее нормы — коэффициент скорости в «Компетенциях»: 0,50" },
      { ...spread, verdict: "быстрее", operator_id: "u2", operator_name: "Борис", facts: 2, enough: false, median: 0.7, low: 0.7, high: 0.7,
        relative: 0.7, coefficient: null, suggestion: "" },
    ],
    facts: [
      fact(),
      fact({ operation_id: "o2", operator_id: "u2", operator_name: "Борис", template: "Пайка", operation_code: "", stage: "Пайка", ratio: 0.7 }),
      fact({ operation_id: "o3", task_number: "ПЗ-0002", finished_at: "2026-10-22T15:30:00+03:00", ratio: null, norm_seconds: 0, norm_per_piece: null }),
    ],
    ...overrides,
  };
}

it("formats ratios, piece times and hours in Russian", () => {
  expect(ratioText(1.4237)).toBe("1,42");
  expect(ratioText(null)).toBe("—");
  expect(pieceTime(8.42)).toBe("8,4 с");
  expect(pieceTime(45.4)).toBe("45 с");
  expect(pieceTime(65)).toBe("1 мин 05 с");
  expect(pieceTime(120)).toBe("2 мин");
  expect(hoursText(0)).toBe("—");
  expect(hoursText(2100)).toBe("35 мин");
  expect(hoursText(45000)).toBe("12,5 ч");
  expect(lastDays(60, new Date(2026, 9, 29))).toEqual({ from: "2026-08-31", to: "2026-10-29" });
});

it("shows two tables with colored ratios, few-data marker and suggestions with links", async () => {
  const calls = serve(() => json(report()));
  render(<ProductionNormFact />);

  const operations = await screen.findByRole("region", { name: "По операциям" });
  const period = lastDays(60);
  expect(calls[0]).toBe(`/api/production/norm-fact?date_from=${period.from}&date_to=${period.to}`);
  const cut = within(operations).getAllByRole("row").find((row) => row.textContent?.includes("Резка с опрессовкой"))!;
  expect(within(cut).getByText("1,50")).toHaveClass("nf-slow");
  expect(within(cut).getByText("1,30 – 1,60")).toBeInTheDocument();
  expect(cut).toHaveTextContent("штучное время 1 мин 12 с вместо 48 с");
  expect(within(cut).getByRole("link", { name: "Изменить норму в библиотеке операций" })).toHaveAttribute("href", "?section=references&tab=templates");
  const solder = within(operations).getAllByRole("row").find((row) => row.textContent?.includes("Пайка"))!;
  expect(solder).toHaveTextContent("мало данных");
  expect(within(solder).getByText("1,00")).toHaveClass("nf-ok");

  const fitters = screen.getByRole("region", { name: "По монтажникам" });
  const elina = within(fitters).getAllByRole("row").find((row) => row.textContent?.includes("Элина"))!;
  expect(elina).toHaveTextContent("0,50");
  expect(within(elina).getByRole("link", { name: "Открыть компетенции" })).toHaveAttribute("href", "?section=card&user=u1&tab=skills");
  const boris = within(fitters).getAllByRole("row").find((row) => row.textContent?.includes("Борис"))!;
  expect(within(boris).getAllByText("0,70")[0]).toHaveClass("nf-fast");
  // Период и легенда цветов видны.
  expect(screen.getByLabelText("Начало периода")).toHaveValue(period.from);
  expect(screen.getByRole("list", { name: "Цвета факт / норма" })).toHaveTextContent("мало данных — меньше 5 фактов");
});

it("opens the individual facts of a template in a window", async () => {
  serve(() => json(report()));
  render(<ProductionNormFact />);
  const operations = await screen.findByRole("region", { name: "По операциям" });
  const cut = within(operations).getAllByRole("row").find((row) => row.textContent?.includes("Резка с опрессовкой"))!;

  fireEvent.click(cut);

  const window = await screen.findByRole("dialog", { name: "Факты: Резка с опрессовкой" });
  const rows = within(window).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(2);  // факт Бориса — другая операция
  expect(rows[0]).toHaveTextContent("ПЗ-0002");  // свежие сверху
  expect(rows[0]).toHaveTextContent("без нормы");
  expect(rows[1]).toHaveTextContent("1 мин 12 с");
  fireEvent.click(within(window).getByRole("button", { name: "Закрыть" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("says honestly when there are no facts yet", async () => {
  serve(() => json(report({ templates: [], operators: [], facts: [] })));
  render(<ProductionNormFact />);
  expect(await screen.findByText(/Фактов пока мало — отчёт наполнится, когда монтажники отмечают начало и сдачу работы/)).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "По операциям" })).not.toBeInTheDocument();
});

it("refetches for a chosen period and shows a load error", async () => {
  const calls = serve((path) => (path.includes("date_from=2026-10-01") ? json({ detail: "Конец периода раньше начала" }, 422) : json(report())));
  render(<ProductionNormFact />);
  await screen.findByRole("region", { name: "По операциям" });

  fireEvent.change(screen.getByLabelText("Начало периода"), { target: { value: "2026-10-01" } });

  expect(await screen.findByRole("alert")).toHaveTextContent("Конец периода раньше начала");
  expect(calls.at(-1)).toContain("date_from=2026-10-01");
});

it("is a tab of «Ресурсное планирование» next to the master board", async () => {
  const { ProductionPage } = await import("./ProductionPage");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.startsWith("/api/production/norm-fact")) return json(report());
    if (path === "/api/production/tasks") return json({ tasks: [], next_cursor: null });
    if (path === "/api/production/operators") return json({ operators: [] });
    if (path === "/api/production/calendar") return json({ holidays: [], absences: [] });
    if (path === "/api/production/skills") return json({ skills: [], operation_codes: [] });
    if (path === "/api/issues") return json({ requests: [], next_cursor: null });
    return json({});
  });
  const principal = {
    user: { id: "m1", email: "master@example.test", name: "Мастер", department: "Производство" },
    roles: ["foreman"], access_level: "foreman", permissions: { production: "edit" },
  };
  render(<ProductionPage principal={principal as never} />);

  const tabs = await screen.findByRole("tablist", { name: "Вид" });
  expect(within(tabs).getByRole("tab", { name: "Доска" })).toHaveAttribute("aria-selected", "true");
  fireEvent.click(within(tabs).getByRole("tab", { name: "Норма и факт" }));

  expect(await screen.findByRole("region", { name: "По операциям" })).toBeInTheDocument();
  expect(within(tabs).getByRole("tab", { name: "Норма и факт" })).toHaveAttribute("aria-selected", "true");
});
