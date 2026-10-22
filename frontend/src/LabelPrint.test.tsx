import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { WarehouseLot } from "./api";
import { PrinterSettingsCard, ReceiptLabels, minutesSince, printResultText } from "./LabelPrint";
import { WarehousePage } from "./WarehousePage";

const lot: WarehouseLot = {
  id: "40000000-0000-0000-0000-000000000001",
  version: 4,
  warehouse: "ЦАР",
  grn: "FRX.26.0000001",
  package: "",
  article: "GHR-04V-S",
  series: "",
  name: "Разъём",
  item_type: "Комплектующие",
  purpose: "FRX.001",
  unit: "шт",
  storage_location: "1F4",
  inbound: "10",
  written_off: "0",
  reserved: "0",
  balance: "10",
  available: "10",
  source_receiving_ids: [],
};

const SETTINGS = {
  printer_name: "", gap_mm: 2, density: 12, speed: 2, width_mm: 58, height_mm: 30, dpi: 300, code_type: "both",
  offset_x_mm: 0, offset_y_mm: 0,
};
const CODE_TYPES = { qr: "QR-код", code128: "Штрихкод Code128", both: "QR и Code128" };

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

type Call = { method: string; path: string; body?: unknown };

const AGENT_OK = { last_seen: new Date().toISOString(), host: "SKLAD-PC", printer: "TSC MB341", queue: 0, last_error: "", last_job: null };

function stage(routes: Record<string, (call: Call) => Response> = {}) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const call = { method: init?.method ?? "GET", path: String(input), body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    if (call.path.split("?")[0] === "/api/stock" && call.method === "GET") return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
    const route = routes[`${call.method} ${call.path}`];
    if (route) return route(call);
    if (call.path === "/api/stock/labels/agent/status") return json(AGENT_OK);
    return json({ detail: `Unexpected request: ${call.path}` }, 500);
  });
  return calls;
}

const queued = { status: "queued", reason: "В очереди принтера.", file: "job-1", copies: 2, job_id: "job-1" };

it("words of the result: queued, printed", () => {
  expect(printResultText(queued as never)).toBe("В очереди принтера.");
  expect(printResultText({ status: "printed", reason: null, file: "/x/a.prn", copies: 3 })).toBe("Отправлено на принтер: 3 шт.");
});

it("minutesSince counts whole minutes and tolerates a missing moment", () => {
  expect(minutesSince(null)).toBeNull();
  expect(minutesSince("2026-10-22T10:00:00Z", Date.parse("2026-10-22T10:02:30Z"))).toBe(2);
});

async function openLotDialog() {
  fireEvent.click((await screen.findAllByRole("button", { name: /Показать партии/ }))[0]);
  const card = within(await screen.findByRole("article", { name: "Партия FRX.26.0000001" }));
  fireEvent.click(await card.findByRole("button", { name: "Этикетка…" }));
  return within(await screen.findByRole("dialog", { name: "Этикетка" }));
}

it("«Этикетка…» on the lot card prints the copies and shows «В очереди принтера» as a success line", async () => {
  const calls = stage({ [`POST /api/stock/batches/${lot.id}/label`]: () => json(queued) });
  render(<WarehousePage />);
  const dialog = await openLotDialog();
  expect(dialog.getByRole("img", { name: "Этикетка партии" }).getAttribute("src")).toMatch(new RegExp(`^/api/stock/batches/${lot.id}/label\\.png\\?t=`));
  fireEvent.change(dialog.getByLabelText("Копий"), { target: { value: "2" } });
  fireEvent.click(dialog.getByRole("button", { name: "Печать" }));

  const line = await dialog.findByText("В очереди принтера.");
  expect(line).toHaveClass("success-banner");
  expect(dialog.queryByRole("alert")).not.toBeInTheDocument();
  expect(calls.find((call) => call.method === "POST")).toEqual({ method: "POST", path: `/api/stock/batches/${lot.id}/label`, body: { copies: 2 } });
});

it("a queued job turns into «Напечатано» or the error once the agent reports it finished", async () => {
  const finished = (status: string, error: string) => () => json({ ...AGENT_OK, last_job: { status, finished_at: new Date(Date.now() + 60_000).toISOString(), error } });
  stage({ [`POST /api/stock/batches/${lot.id}/label`]: () => json(queued), "GET /api/stock/labels/agent/status": finished("printed", "") });
  render(<WarehousePage />);
  const dialog = await openLotDialog();
  fireEvent.click(dialog.getByRole("button", { name: "Печать" }));
  expect(await dialog.findByText("Напечатано")).toHaveClass("success-banner");
  cleanup();
  vi.restoreAllMocks();

  stage({ [`POST /api/stock/batches/${lot.id}/label`]: () => json(queued), "GET /api/stock/labels/agent/status": finished("failed", "Принтер не отвечает") });
  render(<WarehousePage />);
  const again = await openLotDialog();
  fireEvent.click(again.getByRole("button", { name: "Печать" }));
  expect(await again.findByRole("alert")).toHaveTextContent("Принтер не отвечает");
});

it("copies outside 1–50 are stopped before anything is sent", async () => {
  const calls = stage();
  render(<WarehousePage />);
  const dialog = await openLotDialog();
  fireEvent.change(dialog.getByLabelText("Копий"), { target: { value: "51" } });
  fireEvent.click(dialog.getByRole("button", { name: "Печать" }));
  expect(await dialog.findByRole("alert")).toHaveTextContent("Копий — от 1 до 50");
  expect(calls.some((call) => call.method === "POST")).toBe(false);
});

const settingsRoute = { "GET /api/stock/labels/settings": () => json({ settings: SETTINGS, code_types: CODE_TYPES, print_dir: "очередь печати ERP", copies_max: 50 }) };

it("the settings card has no printer list or print_dir, saves numbers and queues a test label", async () => {
  const calls = stage({
    ...settingsRoute,
    "PUT /api/stock/labels/settings": (call) => json({ settings: call.body }),
    "POST /api/stock/labels/test": () => json({ ...queued, copies: 1 }),
  });
  render(<PrinterSettingsCard />);
  const card = within(await screen.findByRole("region", { name: "Печать этикеток ГРН" }));
  expect(await card.findByLabelText("Нагрев 0–15")).toHaveValue("12");
  expect(card.queryByLabelText("Принтер")).not.toBeInTheDocument();
  expect(card.queryByText(/не выбран|не на Windows|очередь печати ERP|\/app\/data/)).not.toBeInTheDocument();
  const before = card.getByRole("img", { name: "Пробная этикетка" }).getAttribute("src");

  fireEvent.change(card.getByLabelText("Зазор, мм"), { target: { value: "2,5" } });
  fireEvent.change(card.getByLabelText("Сдвиг X, мм"), { target: { value: "-0.5" } });
  fireEvent.click(card.getByRole("button", { name: "Сохранить" }));
  expect(await card.findByText("Настройки сохранены")).toBeInTheDocument();
  expect(calls.find((call) => call.method === "PUT")?.body).toEqual({ ...SETTINGS, gap_mm: 2.5, offset_x_mm: -0.5 });
  expect(card.getByRole("img", { name: "Пробная этикетка" }).getAttribute("src")).not.toBe(before);

  fireEvent.click(card.getByRole("button", { name: "Пробная этикетка" }));
  expect(await card.findByText("В очереди принтера.")).toHaveClass("success-banner");
});

it("the agent block: connected, silent for minutes, never connected", async () => {
  const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
  const agent = (body: object) => ({ ...settingsRoute, "GET /api/stock/labels/agent/status": () => json({ ...AGENT_OK, ...body }) });
  const show = async (body: object) => {
    stage(agent(body));
    const view = render(<PrinterSettingsCard />);
    const block = await screen.findByRole("status", { name: "Агент печати" });
    return { block, done: () => { view.unmount(); vi.restoreAllMocks(); } };
  };

  let shown = await show({ last_seen: ago(0), queue: 3, last_error: "Нет бумаги" });
  expect(shown.block).toHaveTextContent("на связи · ПК SKLAD-PC, принтер TSC MB341");
  expect(shown.block).toHaveClass("tone-ok");
  expect(shown.block).toHaveTextContent("В очереди: 3");
  expect(shown.block).toHaveTextContent("Нет бумаги");
  shown.done();

  shown = await show({ last_seen: ago(3) });
  expect(shown.block).toHaveTextContent("не на связи 3 мин");
  expect(shown.block).toHaveClass("tone-warn");
  shown.done();

  shown = await show({ last_seen: ago(12) });
  expect(shown.block).toHaveClass("tone-danger");
  shown.done();

  shown = await show({ last_seen: null, host: "", printer: "" });
  expect(shown.block).toHaveTextContent("ещё не подключался — установите агент");
  expect(within(shown.block).getByRole("link", { name: "инструкция" })).toHaveAttribute("href", expect.stringContaining("tools/print-agent"));
  expect(shown.block).toHaveTextContent("install.ps1");
});

it("the agent status is polled every 10 s while the card is open", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const calls = stage(settingsRoute);
  const { unmount } = render(<PrinterSettingsCard />);
  await screen.findByRole("status", { name: "Агент печати" });
  const count = () => calls.filter((call) => call.path === "/api/stock/labels/agent/status").length;
  const first = count();
  await vi.advanceTimersByTimeAsync(10_100);
  expect(count()).toBe(first + 1);
  unmount();
  await vi.advanceTimersByTimeAsync(30_000);
  expect(count()).toBe(first + 1);
});

it("a viewer sees the settings but no «Сохранить» or «Пробная этикетка»", async () => {
  stage(settingsRoute);
  render(<PrinterSettingsCard readOnly />);
  const card = within(await screen.findByRole("region", { name: "Печать этикеток ГРН" }));
  expect(await card.findByLabelText("Нагрев 0–15")).toBeDisabled();
  expect(card.queryByRole("button", { name: "Сохранить" })).not.toBeInTheDocument();
  expect(card.queryByRole("button", { name: "Пробная этикетка" })).not.toBeInTheDocument();
});

it("after receiving, each new lot is found by its ГРН and queued with its own copies", async () => {
  const calls = stage({
    "GET /api/stock/labels/lot?warehouse=%D0%A6%D0%90%D0%A0&grn=PPM.26.0000002&package=2": () => json({ lot_id: lot.id }),
    "GET /api/stock/labels/lot?warehouse=%D0%A6%D0%90%D0%A0&grn=PPM.26.0000003&package=": () => json({ detail: "Партия ещё не на складе" }, 404),
    [`POST /api/stock/batches/${lot.id}/label`]: () => json({ ...queued, copies: 3 }),
  });
  render(<ReceiptLabels lots={[
    { key: "a", warehouse: "ЦАР", grn: "PPM.26.0000002", package: "2", title: "Клемма", quantity: "120 шт" },
    { key: "b", warehouse: "ЦАР", grn: "PPM.26.0000003", package: "", title: "Провод", quantity: "200 м" },
  ]} />);
  fireEvent.change(screen.getByLabelText("Копий: PPM.26.0000002 / 2"), { target: { value: "3" } });
  fireEvent.click(screen.getByRole("button", { name: "Печать всех (2)" }));

  expect(await screen.findByText("В очереди принтера.")).toHaveClass("success-banner");
  expect(await screen.findByText("Партия ещё не на складе")).toBeInTheDocument();
  expect(calls.filter((call) => call.method === "POST")).toEqual([{ method: "POST", path: `/api/stock/batches/${lot.id}/label`, body: { copies: 3 } }]);
});
