import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Principal, WarehouseLot } from "./api";
import { EquipmentPage } from "./EquipmentPage";
import { PrinterSettingsCard, ReceiptLabels, printResultText } from "./LabelPrint";
import { visibleNav } from "./navigation";
import { resetStageOptions } from "./StageOptions";
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

// Options must be forgotten after the components that read them are gone.
afterEach(() => { cleanup(); vi.restoreAllMocks(); window.localStorage.clear(); resetStageOptions(); });

type Call = { method: string; path: string; body?: unknown };

function stage(enabled: string[], routes: Record<string, (call: Call) => Response> = {}) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const call = { method: init?.method ?? "GET", path: String(input), body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    if (call.path === "/api/stage-options") {
      return json({ available: true, options: ["warehouse.scan", "warehouse.labels"].map((key) => (
        { key, block: "Склад", title: "t", before: "", after: "", enabled: enabled.includes(key) })) });
    }
    if (call.path.split("?")[0] === "/api/stock" && call.method === "GET") return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
    const route = routes[`${call.method} ${call.path}`];
    if (route) return route(call);
    return json({ detail: `Unexpected request: ${call.path}` }, 500);
  });
  return calls;
}

const saved = { status: "saved", reason: "Принтер не выбран в настройках — задание сохранено, напечатайте вручную", file: "C:\\stage\\print\\2026-10-19T10-00-00-000000-FRX.26.0000001.prn", copies: 2 };

it("words of the result are Roller_Buyer's", () => {
  expect(printResultText({ status: "printed", reason: null, file: "/x/a.prn", copies: 3 })).toBe("Отправлено на принтер: 3 шт.");
  expect(printResultText(saved as never)).toBe("Принтер не выбран в настройках — задание сохранено, напечатайте вручную Задание: 2026-10-19T10-00-00-000000-FRX.26.0000001.prn");
});

it("the lot card has no «Этикетка…» while the option is off", async () => {
  stage([]);
  render(<WarehousePage />);
  fireEvent.click((await screen.findAllByRole("button", { name: /Показать партии/ }))[0]);
  const card = within(await screen.findByRole("article", { name: "Партия FRX.26.0000001" }));
  expect(card.getByRole("button", { name: "− Списать" })).toBeInTheDocument();
  expect(card.queryByRole("button", { name: "Этикетка…" })).not.toBeInTheDocument();
});

it("«Этикетка…» on the lot card shows the preview and prints the copies", async () => {
  const calls = stage(["warehouse.labels"], {
    [`POST /api/stock/batches/${lot.id}/label`]: () => json(saved),
  });
  render(<WarehousePage />);
  fireEvent.click((await screen.findAllByRole("button", { name: /Показать партии/ }))[0]);
  const card = within(await screen.findByRole("article", { name: "Партия FRX.26.0000001" }));
  fireEvent.click(await card.findByRole("button", { name: "Этикетка…" }));

  const dialog = within(await screen.findByRole("dialog", { name: "Этикетка" }));
  expect(dialog.getByRole("img", { name: "Этикетка партии" }).getAttribute("src")).toMatch(new RegExp(`^/api/stock/batches/${lot.id}/label\\.png\\?t=`));
  fireEvent.change(dialog.getByLabelText("Копий"), { target: { value: "2" } });
  fireEvent.click(dialog.getByRole("button", { name: "Печать" }));

  expect(await dialog.findByRole("status")).toHaveTextContent("Принтер не выбран в настройках — задание сохранено, напечатайте вручную Задание: 2026-10-19T10-00-00-000000-FRX.26.0000001.prn");
  expect(calls.find((call) => call.method === "POST")).toEqual({ method: "POST", path: `/api/stock/batches/${lot.id}/label`, body: { copies: 2 } });
});

it("copies outside 1–50 are stopped before anything is sent", async () => {
  const calls = stage(["warehouse.labels"]);
  render(<WarehousePage />);
  fireEvent.click((await screen.findAllByRole("button", { name: /Показать партии/ }))[0]);
  fireEvent.click(await within(await screen.findByRole("article", { name: "Партия FRX.26.0000001" })).findByRole("button", { name: "Этикетка…" }));
  const dialog = within(await screen.findByRole("dialog", { name: "Этикетка" }));
  fireEvent.change(dialog.getByLabelText("Копий"), { target: { value: "51" } });
  fireEvent.click(dialog.getByRole("button", { name: "Печать" }));
  expect(await dialog.findByRole("alert")).toHaveTextContent("Копий — от 1 до 50");
  expect(calls.some((call) => call.method === "POST")).toBe(false);
});

it("the settings card lists Windows printers, saves numbers and prints a test label", async () => {
  const calls = stage(["warehouse.labels"], {
    "GET /api/stock/labels/settings": () => json({ settings: SETTINGS, code_types: CODE_TYPES, print_dir: "C:\\stage\\print", copies_max: 50 }),
    "GET /api/stock/labels/printers": () => json({ printers: ["Microsoft Print to PDF", "TSC MB341"], on_windows: true }),
    "PUT /api/stock/labels/settings": (call) => json({ settings: call.body }),
    "POST /api/stock/labels/test": () => json({ status: "printed", reason: null, file: "C:\\stage\\print\\x.prn", copies: 1 }),
  });
  render(<PrinterSettingsCard />);
  const card = within(await screen.findByRole("region", { name: "Печать этикеток ГРН" }));
  const printer = await card.findByLabelText("Принтер");
  await waitFor(() => expect(within(printer).getByRole("option", { name: "TSC MB341" })).toBeInTheDocument());
  expect(within(printer).getByRole("option", { name: "— не выбран (задание сохраняется файлом) —" })).toBeInTheDocument();
  expect(card.getByLabelText("Нагрев 0–15")).toHaveValue("12");
  expect(card.getByLabelText("Скорость")).toHaveValue("2");
  const preview = card.getByRole("img", { name: "Пробная этикетка" });
  const before = preview.getAttribute("src");

  fireEvent.change(printer, { target: { value: "TSC MB341" } });
  fireEvent.change(card.getByLabelText("Зазор, мм"), { target: { value: "2,5" } });
  fireEvent.change(card.getByLabelText("Сдвиг X, мм"), { target: { value: "-0.5" } });
  fireEvent.click(card.getByRole("button", { name: "Сохранить" }));

  expect(await card.findByText("Настройки сохранены")).toBeInTheDocument();
  expect(calls.find((call) => call.method === "PUT")?.body).toEqual({ ...SETTINGS, printer_name: "TSC MB341", gap_mm: 2.5, offset_x_mm: -0.5 });
  expect(card.getByRole("img", { name: "Пробная этикетка" }).getAttribute("src")).not.toBe(before);

  fireEvent.click(card.getByRole("button", { name: "Пробная этикетка" }));
  await waitFor(() => expect(card.getByText("Отправлено на принтер: 1 шт.")).toBeInTheDocument());
});

it("a viewer sees the settings but no «Сохранить» or «Пробная этикетка»", async () => {
  stage(["warehouse.labels"], {
    "GET /api/stock/labels/settings": () => json({ settings: SETTINGS, code_types: CODE_TYPES, print_dir: "/tmp/erp-label-print", copies_max: 50 }),
    "GET /api/stock/labels/printers": () => json({ printers: [], on_windows: false }),
  });
  render(<PrinterSettingsCard readOnly />);
  const card = within(await screen.findByRole("region", { name: "Печать этикеток ГРН" }));
  expect(await card.findByLabelText("Принтер")).toBeDisabled();
  expect(card.queryByRole("button", { name: "Сохранить" })).not.toBeInTheDocument();
  expect(card.queryByRole("button", { name: "Пробная этикетка" })).not.toBeInTheDocument();
  expect(await card.findByText(/не на Windows/)).toBeInTheDocument();
});

it("«Оборудование» has no label card while the option is off, and the menu hides the section", async () => {
  stage([]);
  render(<EquipmentPage />);
  expect(await screen.findByRole("heading", { name: "Оборудование", level: 1 })).toBeInTheDocument();
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledWith("/api/stage-options", expect.anything()));
  expect(screen.queryByRole("region", { name: "Печать этикеток ГРН" })).not.toBeInTheDocument();

  const principal = { permissions: { warehouse: "edit" }, roles: ["receiver"] } as unknown as Principal;
  expect(visibleNav(principal).some((item) => item.id === "equipment")).toBe(false);
  expect(visibleNav(principal, new Set(["warehouse.labels"])).some((item) => item.id === "equipment")).toBe(true);
  expect(visibleNav(principal, new Set(["warehouse.tsd"])).some((item) => item.id === "equipment")).toBe(true);
  expect(visibleNav({ ...principal, permissions: {} } as Principal, new Set(["warehouse.labels"])).some((item) => item.id === "equipment")).toBe(false);
});

it("after receiving, each new lot is found by its ГРН and printed with its own copies", async () => {
  const calls = stage(["warehouse.labels"], {
    "GET /api/stock/labels/lot?warehouse=%D0%A6%D0%90%D0%A0&grn=PPM.26.0000002&package=2": () => json({ lot_id: lot.id }),
    "GET /api/stock/labels/lot?warehouse=%D0%A6%D0%90%D0%A0&grn=PPM.26.0000003&package=": () => json({ detail: "Партия ещё не на складе" }, 404),
    [`POST /api/stock/batches/${lot.id}/label`]: () => json({ status: "printed", reason: null, file: "x.prn", copies: 3 }),
  });
  render(<ReceiptLabels lots={[
    { key: "a", warehouse: "ЦАР", grn: "PPM.26.0000002", package: "2", title: "Клемма", quantity: "120 шт" },
    { key: "b", warehouse: "ЦАР", grn: "PPM.26.0000003", package: "", title: "Провод", quantity: "200 м" },
  ]} />);
  fireEvent.change(screen.getByLabelText("Копий: PPM.26.0000002 / 2"), { target: { value: "3" } });
  fireEvent.click(screen.getByRole("button", { name: "Печать всех (2)" }));

  expect(await screen.findByText("Отправлено на принтер: 3 шт.")).toBeInTheDocument();
  expect(await screen.findByText("Партия ещё не на складе")).toBeInTheDocument();
  expect(calls.filter((call) => call.method === "POST")).toEqual([{ method: "POST", path: `/api/stock/batches/${lot.id}/label`, body: { copies: 3 } }]);
});
