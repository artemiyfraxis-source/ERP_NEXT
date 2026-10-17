import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { DowntimeControl } from "./DowntimeControl";
import { DowntimeReport, presetPeriod } from "./DowntimeReport";
import { downtimeDuration, resetOpenDowntimes, type Downtime } from "./downtimeApi";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function downtime(overrides: Partial<Downtime> = {}): Downtime {
  return {
    id: "d1", task_id: "t1", operation_id: "o1", operator_id: "u1", operator_name: "Элина", whole_task: false,
    reason: "no_material", reason_name: "Нет материала", comment: "нет МГТФ", started_at: "2026-10-13T10:00:00+03:00",
    started_by_name: "Элина", ended_at: null, ended_by_name: "", open: true, seconds: 1500,
    project: "П3-0007", task_number: "ПЗ-0001", item: "КБ1", stage: "Резка",
    ...overrides,
  };
}

type Call = { path: string; method: string; body: unknown };

function serve(answer: (call: Call) => Response) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const call = { path: String(input), method: (init?.method ?? "GET").toUpperCase(), body: init?.body ? JSON.parse(String(init.body)) : null };
    calls.push(call);
    return answer(call);
  });
  return calls;
}

beforeEach(() => resetOpenDowntimes());
afterEach(() => vi.restoreAllMocks());

it("starts a downtime with a reason, needs a comment for «Другое» and then shows it on the card", async () => {
  let open: Downtime[] = [];
  const calls = serve((call) => {
    if (call.path === "/api/production/downtime/open") return json({ downtimes: open });
    if (call.method === "POST") { open = [downtime({ reason: "other", reason_name: "Другое", comment: "нет света" })]; return json({ downtime: open[0] }); }
    return json({}, 404);
  });
  render(<DowntimeControl taskId="t1" operationId="o1" performerId="u1" onBehalfOf="u1" />);

  fireEvent.click(await screen.findByRole("button", { name: "Простой…" }));
  const start = screen.getByRole("button", { name: "Начать простой" });
  expect(start).toBeDisabled();  // без причины
  fireEvent.click(screen.getByRole("radio", { name: "Другое" }));
  expect(start).toBeDisabled();  // «Другое» без комментария
  fireEvent.change(screen.getByLabelText("Комментарий к простою"), { target: { value: " нет света " } });
  expect(start).toBeEnabled();
  fireEvent.click(start);

  expect(await screen.findByText("Закончить простой")).toBeInTheDocument();
  const post = calls.find((call) => call.method === "POST");
  expect(post?.path).toBe("/api/production/downtime?on_behalf_of=u1");
  expect(post?.body).toEqual({ task_id: "t1", operation_id: "o1", reason: "other", comment: "нет света", whole_task: false });
  expect(screen.getByRole("status")).toHaveTextContent("Другое · с 13.10 10:00");
});

it("shows an open downtime of the task and lets only its fitter or a master end it", async () => {
  let open = [downtime()];
  const calls = serve((call) => {
    if (call.path === "/api/production/downtime/open") return json({ downtimes: open });
    if (call.path === "/api/production/downtime/d1/stop") { open = []; return json({ downtime: downtime({ open: false }) }); }
    return json({}, 404);
  });
  const { unmount } = render(<DowntimeControl taskId="t1" performerId="u2" />);
  const strip = await screen.findByRole("status");
  expect(strip).toHaveTextContent("Нет материала");
  expect(strip).toHaveTextContent("Элина");
  expect(within(strip).queryByRole("button")).toBeNull();  // чужой простой монтажник не закрывает
  unmount();

  render(<DowntimeControl taskId="t1" performerId="" wholeTask master />);
  fireEvent.click(await screen.findByRole("button", { name: "Закончить простой" }));
  expect(await screen.findByRole("button", { name: "Простой задания…" })).toBeInTheDocument();
  expect(calls.some((call) => call.method === "POST" && call.path === "/api/production/downtime/d1/stop")).toBe(true);
});

it("shows the server refusal when the task already has an open downtime", async () => {
  serve((call) => {
    if (call.path === "/api/production/downtime/open") return json({ downtimes: [] });
    return json({ detail: "У задания уже открыт простой — сначала закончите его" }, 409);
  });
  render(<DowntimeControl taskId="t1" performerId="u1" />);
  fireEvent.click(await screen.findByRole("button", { name: "Простой…" }));
  fireEvent.click(screen.getByRole("radio", { name: "Оборудование" }));
  fireEvent.click(screen.getByRole("button", { name: "Начать простой" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("уже открыт простой");
});

it("reports the period sums by reason and switches to people", async () => {
  const calls = serve((call) => {
    const by = new URL(call.path, "http://x").searchParams.get("by");
    return json({
      by, date_from: "2026-10-01", date_to: "2026-10-13", total_seconds: 9000, total_count: 3, work_seconds: 90000,
      rows: by === "reason"
        ? [{ key: "no_material", label: "Нет материала", count: 2, seconds: 6300, open: 1, work_seconds: null },
           { key: "equipment", label: "Оборудование", count: 1, seconds: 2700, open: 0, work_seconds: null }]
        : [{ key: "u1", label: "Элина", count: 3, seconds: 9000, open: 1, work_seconds: 90000 }],
      entries: [downtime({ period_seconds: 1500 })],
    });
  });
  render(<DowntimeReport close={() => undefined} />);

  const month = presetPeriod("month");
  await screen.findByRole("region", { name: "Простои по группам" });
  expect(calls[0].path).toBe(`/api/production/downtime/report?date_from=${month.from}&date_to=${month.to}&by=reason`);
  const groups = screen.getByRole("region", { name: "Простои по группам" });
  const material = within(groups).getAllByRole("row").find((row) => row.textContent?.includes("Нет материала"));
  expect(material).toHaveTextContent("1 ч 45 мин");
  expect(material).toHaveTextContent("70 %");
  expect(screen.getByText("2 ч 30 мин · 3 сл.")).toBeInTheDocument();
  expect(screen.getByText("10 %")).toBeInTheDocument();  // простои к работе по журналу

  fireEvent.click(screen.getByRole("radio", { name: "По сотрудникам" }));
  await waitFor(() => expect(calls.at(-1)?.path).toContain("by=operator"));
  expect(await screen.findByText("Работа по журналу", { selector: "th, th *" })).toBeInTheDocument();
});

it("formats durations and week periods", () => {
  expect(downtimeDuration(0)).toBe("0 мин");
  expect(downtimeDuration(1500)).toBe("25 мин");
  expect(downtimeDuration(3900)).toBe("1 ч 05 мин");
  expect(presetPeriod("week", new Date(2026, 9, 15))).toEqual({ from: "2026-10-12", to: "2026-10-15" });
  expect(presetPeriod("today", new Date(2026, 9, 15))).toEqual({ from: "2026-10-15", to: "2026-10-15" });
});

it("shows a short note without buttons on the other cards of a stopped task", async () => {
  serve(() => json({ downtimes: [downtime({ operation_id: "o1" })] }));
  render(<DowntimeControl taskId="t1" operationId="o2" primary={false} performerId="u1" />);
  expect(await screen.findByText("Задание стоит: нет материала с 13.10 10:00 (Резка)")).toBeInTheDocument();
  expect(screen.queryByRole("button")).toBeNull();
});
