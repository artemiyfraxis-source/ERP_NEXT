import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MachineSpeedReport, ratioTone, setupText, stopsText } from "./MachineSpeedReport";
import type { MachineSpeedReport as Report, MachineSpeedRow } from "./machineSpeedApi";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function serve(answer: (path: string, init?: RequestInit) => Response) {
  const calls: Array<{ path: string; init?: RequestInit }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    calls.push({ path: String(input), init });
    return answer(String(input), init);
  });
  return calls;
}

afterEach(() => vi.restoreAllMocks());

function row(overrides: Partial<MachineSpeedRow> = {}): MachineSpeedRow {
  return {
    equipment_id: "e1", inventory_number: "ОБ.019", setup_key: "пвам 0,35|L325|S5/5|P0/0|M0",
    setup: { wire: "ПВАМ 0,35", length_mm: "325", strip_front_mm: "5", strip_back_mm: "5", pull: "0/0", marking: "Без маркировки" },
    operation: { code: "CUT_WIRE+2strip", name: "Резка с разделкой", record_id: "r1" },
    runs: 3, pieces: 640, running_seconds: 4012, fact_seconds_per_piece: "6.27", norm_seconds_per_piece: "5.90", ratio: "1.06",
    setup_fact_seconds: "540", setup_norm_seconds: "600",
    stops: [{ reason: "pause", label: "Пауза", seconds: 120 }, { reason: "alarm", label: "Авария", seconds: 30 }],
    suggestion: null,
    ...overrides,
  };
}

const SUGGESTED = row({
  setup_key: "мгтф 0,12|L1000|S4/4|P0/0|M0",
  setup: { wire: "МГТФ 0,12", length_mm: "1000", strip_front_mm: "4", strip_back_mm: "4", pull: "0/0", marking: "Маркировка: 2 текста" },
  ratio: "1.38", fact_seconds_per_piece: "8.15", norm_seconds_per_piece: "5.90",
  suggestion: {
    record_id: "r1", operation_code: "CUT_WIRE+2strip",
    cells: [{ field: "roll_speed", label: "Скорость проката, м/с", old: "0.25", new: "0.22" },
      { field: "tool_time", label: "Скорость работы инструмента, с", old: "1.5", new: "1.8" }],
    evidence: { pieces: 640, runs: 3, lengths: ["325", "1000"], fit: "6,1 с + L / 0,22 м/с", since: "2026-10-21", until: "2026-11-20" },
    note: "по одной длине — только постоянная часть",
  },
});

function report(overrides: Partial<Report> = {}): Report {
  return {
    since: "2026-10-21", until: "2026-11-20", can_accept: true,
    machines: [{ equipment_id: "e1", inventory_number: "ОБ.019", name: "Линия резки" }, { equipment_id: "e2", inventory_number: "ОБ.020", name: "Пресс" }],
    rows: [row(), SUGGESTED],
    ...overrides,
  };
}

it("formats setup, stops and ratio tones in Russian", () => {
  expect(setupText(row())).toBe("ПВАМ 0,35 · 325 мм · зачистка 5/5 мм · Без маркировки");
  expect(stopsText(row().stops)).toBe("Пауза 2 мин · Авария 30 с");
  expect(ratioTone(1.06)).toBe("tone-ok");
  expect(ratioTone(1.2)).toBe("tone-warn");
  expect(ratioTone(1.38)).toBe("tone-danger");
});

it("shows the period, the table with tones and the suggestion", async () => {
  const calls = serve(() => json(report()));
  render(<MachineSpeedReport />);

  const table = await screen.findByRole("region", { name: "Станки: скорость" });
  expect(calls[0].path).toMatch(/^\/api\/production\/machine-speed\?since=\d{4}-\d{2}-\d{2}&until=\d{4}-\d{2}-\d{2}$/);
  expect(screen.getByLabelText("Станки: начало периода")).toBeInTheDocument();
  const ok = within(table).getAllByRole("row").find((r) => r.textContent?.includes("ПВАМ 0,35"))!;
  expect(within(ok).getByText("1,06")).toHaveClass("tone-ok");
  expect(ok).toHaveTextContent("Пауза 2 мин · Авария 30 с");
  expect(ok).toHaveTextContent("6,27");
  const bad = within(table).getAllByRole("row").find((r) => r.textContent?.includes("МГТФ 0,12"))!;
  expect(within(bad).getByText("1,38")).toHaveClass("tone-danger");
  expect(bad).toHaveTextContent("Скорость проката 0,25 → 0,22");
  expect(within(bad).getByRole("button", { name: "Принять" })).toBeInTheDocument();
});

it("shows the empty hint when there is no history", async () => {
  serve(() => json(report({ rows: [] })));
  render(<MachineSpeedReport />);
  expect(await screen.findByText("Нет истории станков за период — она пишется, когда ОБ.019 на связи.")).toBeInTheDocument();
});

it("shows an error banner when loading fails", async () => {
  serve(() => json({ detail: "Нет доступа к станкам" }, 403));
  render(<MachineSpeedReport />);
  expect(await screen.findByRole("alert")).toBeInTheDocument();
});

it("refetches when the period or the machine changes", async () => {
  const calls = serve(() => json(report()));
  render(<MachineSpeedReport />);
  await screen.findByRole("region", { name: "Станки: скорость" });

  fireEvent.change(screen.getByLabelText("Станки: начало периода"), { target: { value: "2026-11-01" } });
  await waitFor(() => expect(calls.some((c) => c.path.includes("since=2026-11-01"))).toBe(true));

  fireEvent.change(screen.getByLabelText("Станок"), { target: { value: "e2" } });
  await waitFor(() => expect(calls.some((c) => c.path.includes("equipment_id=e2"))).toBe(true));
});

it("opens the runs of a clicked row", async () => {
  const calls = serve((path) => path.includes("/runs")
    ? json({ runs: [{ started_at: "2026-11-10T09:00:00+03:00", ended_at: "2026-11-10T09:20:00+03:00", kind: "cutting", pieces: 200, running_seconds: 1250, fact_seconds_per_piece: "6.25", setup_fact_seconds: "540" }] })
    : json(report()));
  render(<MachineSpeedReport />);
  const table = await screen.findByRole("region", { name: "Станки: скорость" });
  fireEvent.click(within(table).getAllByRole("row").find((r) => r.textContent?.includes("ПВАМ 0,35"))!);

  const window = await screen.findByRole("dialog", { name: "Прогоны: ОБ.019" });
  expect(await within(window).findByText("6,25")).toBeInTheDocument();
  const runs = calls.find((c) => c.path.includes("/runs"))!;
  expect(runs.path).toContain("equipment_id=e1");
  expect(runs.path).toContain(`setup_key=${encodeURIComponent("пвам 0,35|L325|S5/5|P0/0|M0").replace(/%20/g, "+")}`);
});

async function openAccept() {
  render(<MachineSpeedReport />);
  const table = await screen.findByRole("region", { name: "Станки: скорость" });
  const bad = within(table).getAllByRole("row").find((r) => r.textContent?.includes("МГТФ 0,12"))!;
  fireEvent.click(within(bad).getByRole("button", { name: "Принять" }));
  return screen.findByRole("dialog", { name: "Принять скорость: ОБ.019" });
}

it("accepts a suggestion and reloads the report", async () => {
  let loads = 0;
  const calls = serve((path, init) => {
    if (init?.method === "POST") return json({ record_id: "r1", written: [] });
    loads += 1;
    return json(report());
  });
  const window = await openAccept();
  expect(within(window).getByText("0,25")).toBeInTheDocument();
  expect(within(window).getByText("0,22")).toBeInTheDocument();
  expect(window).toHaveTextContent("6,1 с + L / 0,22 м/с");
  expect(window).toHaveTextContent("по одной длине — только постоянная часть");

  fireEvent.click(within(window).getByRole("button", { name: "Записать в циклограмму" }));

  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  const post = calls.find((c) => c.init?.method === "POST")!;
  expect(post.path).toBe("/api/production/machine-speed/norm-suggestions/accept");
  const body = JSON.parse(String(post.init?.body));
  expect(body).toMatchObject({ record_id: "r1", equipment_id: "e1", since: "2026-10-21", until: "2026-11-20" });
  expect(body.cells[0]).toEqual({ field: "roll_speed", old: "0.25", new: "0.22" });
  expect(await screen.findByRole("status")).toHaveTextContent("Записано в циклограмму");
  await waitFor(() => expect(loads).toBe(2));
});

it("says the data changed on 409 and offers a refresh", async () => {
  let loads = 0;
  serve((path, init) => {
    if (init?.method === "POST") return json({ detail: "stale" }, 409);
    loads += 1;
    return json(report());
  });
  const window = await openAccept();
  fireEvent.click(within(window).getByRole("button", { name: "Записать в циклограмму" }));

  expect(await within(window).findByRole("alert")).toHaveTextContent("Данные изменились, обновите");
  fireEvent.click(within(window).getByRole("button", { name: "Обновить отчёт" }));
  await waitFor(() => expect(loads).toBe(2));
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("hides «Принять» without the references edit right", async () => {
  serve(() => json(report({ can_accept: false })));
  render(<MachineSpeedReport />);
  const table = await screen.findByRole("region", { name: "Станки: скорость" });
  expect(within(table).getAllByRole("row").some((r) => r.textContent?.includes("Скорость проката"))).toBe(true);
  expect(within(table).queryByRole("button", { name: "Принять" })).toBeNull();
});
