import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ReceivingPage } from "./ReceivingPage";
import { WarehousePage } from "./WarehousePage";
import type { ReceivingLine, UpdProposal, WarehouseReceiptScan } from "./api";
import { expectedBanner, reassignRow, rowQuantity, sumQuantities } from "./receivingByUpdLogic";

const base: ReceivingLine = {
  id: "", legacy_id: "", status: "В пути", version: 1, warehouse: "ЦАР", article: "", series: "", name: "",
  item_type: "Разъём", purpose: "FRX.001", supplier: "ООО «Элитан»", ordered_quantity: "10", unit: "шт",
  quality_document_required: false, shipping_document_url: null, quality_document_url: null, document_quantity: null,
  fact_quantity: null, expiry_date: null, disposition: null, verdict: null, nonconformities: [], accepted_quantity: null,
  backorder_quantity: "0", grn: null, package: "", storage_location: null, inspector: null, warehouse_event_emitted: false,
  inspected_on: null,
};
const lines: ReceivingLine[] = [
  { ...base, id: "l1", legacy_id: "SM-1", article: "XH-2P", name: "Разъём XH", order_number: "З-1", expected_arrival: "2026-10-20", version: 3 },
  { ...base, id: "l2", legacy_id: "SM-2", article: "PH-3", name: "Разъём PH", order_number: "З-2", expected_arrival: "2026-10-21", version: 4 },
  { ...base, id: "l3", legacy_id: "SM-3", article: "KK-7", name: "Клемма KK", order_number: "З-3", expected_arrival: "2026-10-22", version: 5 },
];
const scan: WarehouseReceiptScan = {
  scan_file: "/api/stock/receipts/files/f-1", note: "",
  recognised: { supplier: "ООО «Элитан»", inn: "7801234567", upd_number: "771", upd_date: "2026-10-20", invoice_number: "СЧ-45", invoice_date: "2026-10-01" },
  lines: [
    { article: "XH-2P", name: "Разъём XH-2P", quantity: "10", unit: "шт" },
    { article: "PH-3", name: "Разъём PH-3", quantity: "6", unit: "шт" },
    { article: "ZZ-9", name: "Кабель ZZ-9", quantity: "2", unit: "шт" },
  ],
};
const proposal: UpdProposal = {
  supplier: "ООО «Элитан»", warehouse: "ЦАР", other_warehouses: [], invoice_numbers: ["СЧ-45"], matched: 2, orders: ["З-1", "З-2"],
  unmatched: [2],
  lines: [
    { line: lines[0], ticked: true, document_quantity: "10", confidence: "invoice", by_invoice: true, rows: [{ index: 0, quantity: "10" }], warnings: [] },
    { line: lines[1], ticked: true, document_quantity: "6", confidence: "article", by_invoice: false, rows: [{ index: 1, quantity: "6" }], warnings: [] },
    { line: lines[2], ticked: false, document_quantity: "", confidence: "", by_invoice: false, rows: [], warnings: [] },
  ],
  rows: [
    { index: 0, article: "XH-2P", name: "Разъём XH-2P", upd_name: "Разъём XH-2P", quantity: "10", unit: "шт", invoice_quantity: "", invoice_unit: "", lines: ["l1"], confidence: "article" },
    { index: 1, article: "PH-3", name: "Разъём PH-3", upd_name: "Разъём PH-3", quantity: "6", unit: "шт", invoice_quantity: "", invoice_unit: "", lines: ["l2"], confidence: "article" },
    { index: 2, article: "ZZ-9", name: "Кабель ZZ-9", upd_name: "Кабель ZZ-9", quantity: "2", unit: "шт", invoice_quantity: "", invoice_unit: "", lines: [], confidence: "" },
  ],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Call = { path: string; method: string; body: Record<string, unknown> | null };

function mockApi(handlers: Record<string, (call: Call) => Response | undefined> = {}) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const call: Call = { path, method: init?.method ?? "GET", body: typeof init?.body === "string" ? JSON.parse(init.body) : null };
    calls.push(call);
    const custom = handlers[`${call.method} ${path.split("?")[0]}`]?.(call);
    if (custom) return custom;
    if (path === "/api/receiving" || path.startsWith("/api/receiving?")) return json({ lines, next_cursor: null });
    if (call.method === "POST" && path === "/api/receiving/upd/scan") return json({ id: "j1", status: "done", error: "", recognising: false, scan_file: scan.scan_file, result: scan });
    if (call.method === "POST" && path === "/api/receiving/upd/proposal") return json(proposal);
    if (path.startsWith("/api/warehouse") || path.startsWith("/api/purchasing")) return json({});
    if (path.startsWith("/api/stock/places?")) return json({ warehouse: "ЦАР", shelf: "1F", occupied: [], next_number: 1 });
    throw new Error(`Unexpected request: ${call.method} ${path}`);
  });
  return calls;
}

beforeEach(() => { localStorage.setItem("erp-table:receiving-v3", JSON.stringify({ hidden: [], sort: null, preset: "" })); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });

async function scanInReceiving() {
  render(<ReceivingPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Принять по УПД" }));
  const window = await screen.findByRole("dialog", { name: "Принять по УПД" });
  fireEvent.change(within(window).getByLabelText("Файл УПД"), { target: { files: [new File(["%PDF"], "upd.pdf", { type: "application/pdf" })] } });
  await screen.findByRole("heading", { name: "Поставка: ООО «Элитан»" });
  return window;
}

it("a recognised УПД opens its supplier's delivery pre-filled: matched lines ticked with «По УПД», how found, rows without a pair", async () => {
  const calls = mockApi();
  await scanInReceiving();

  expect(screen.getByLabelText("Пришло: XH-2P · Разъём XH")).toBeChecked();
  expect(screen.getByLabelText("По УПД: XH-2P · Разъём XH")).toHaveValue(10);
  expect(screen.getByLabelText("Пришло: PH-3 · Разъём PH")).toBeChecked();
  expect(screen.getByLabelText("По УПД: PH-3 · Разъём PH")).toHaveValue(6);
  expect(screen.getByLabelText("Пришло: KK-7 · Клемма KK")).not.toBeChecked();
  expect(screen.getByLabelText("По УПД: KK-7 · Клемма KK")).toHaveValue(null);
  expect(screen.getByText("по счёту")).toBeInTheDocument();
  expect(screen.getByText("по артикулу")).toBeInTheDocument();
  expect(screen.getByLabelText("УПД №")).toHaveValue("771");
  expect(screen.getByLabelText("Дата УПД")).toHaveValue("2026-10-20");
  const rows = within(screen.getByRole("region", { name: "Строки УПД" }));
  expect(rows.getByLabelText("Позиция приёмки для строки УПД 3")).toHaveValue("");
  expect(screen.getByText("без пары: 1")).toBeInTheDocument();
  const asked = calls.find((call) => call.path === "/api/receiving/upd/proposal");
  expect(asked?.body?.recognised).toEqual(scan.recognised);
  expect((asked?.body?.lines as unknown[]).length).toBe(3);
  expect(screen.getByRole("button", { name: "Провести поставку (2 поз.)" })).toBeEnabled();
});

it("unticked lines stay out, a changed quantity goes, the scan and its header go with «Провести поставку»", async () => {
  const calls = mockApi({
    "POST /api/receiving/upd/accept": (call) => json({
      delivery_id: "d1", upd_file: "/api/receiving/files/u1", registry: { status: "created" },
      lines: (call.body?.lines as { id: string }[]).map((item) => ({ ...lines.find((line) => line.id === item.id)!, status: "Принято", grn: "CSM-01" })),
    }),
  });
  await scanInReceiving();
  fireEvent.click(screen.getByLabelText("Пришло: PH-3 · Разъём PH"));
  fireEvent.change(screen.getByLabelText("Факт: XH-2P · Разъём XH"), { target: { value: "9" } });
  fireEvent.change(screen.getByLabelText("Место для всех"), { target: { value: "1F4" } });
  fireEvent.click(screen.getByRole("button", { name: "Провести поставку (1 поз.)" }));

  await waitFor(() => expect(calls.some((call) => call.path === "/api/receiving/upd/accept")).toBe(true));
  const sent = calls.find((call) => call.path === "/api/receiving/upd/accept")!.body!;
  expect(sent.lines).toEqual([{ id: "l1", version: 3, document_quantity: "10", fact_quantity: "9", storage_location: "1F4" }]);
  expect(sent).toMatchObject({ scan_file: scan.scan_file, recognised: scan.recognised, document_number: "771", document_date: "2026-10-20", warehouse: "ЦАР", shipping_document_url: null });
  expect(await screen.findByText("CSM-01")).toBeInTheDocument();
  expect(await screen.findByText(/Принято 1 позиция, ГРН присвоены/)).toBeInTheDocument();
});

it("a УПД row without a pair is assigned to a waiting line: the line is ticked with the row's quantity", async () => {
  mockApi();
  await scanInReceiving();
  fireEvent.change(screen.getByLabelText("Позиция приёмки для строки УПД 3"), { target: { value: "l3" } });

  expect(screen.getByLabelText("Пришло: KK-7 · Клемма KK")).toBeChecked();
  expect(screen.getByLabelText("По УПД: KK-7 · Клемма KK")).toHaveValue(2);
  expect(screen.queryByText("без пары: 1")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Позиция приёмки для строки УПД 1"), { target: { value: "" } });
  expect(screen.getByLabelText("Пришло: XH-2P · Разъём XH")).not.toBeChecked();
});

it("nothing waiting for the УПД's supplier says where to take it in", async () => {
  mockApi({ "POST /api/receiving/upd/proposal": () => json({ ...proposal, lines: [], matched: 0, orders: [], unmatched: [0, 1, 2] }) });
  render(<ReceivingPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Принять по УПД" }));
  fireEvent.change(await screen.findByLabelText("Файл УПД"), { target: { files: [new File(["%PDF"], "upd.pdf")] } });

  expect(await screen.findByText(/В листе ожидания нет позиций поставщика «ООО «Элитан»» и счёта СЧ-45/)).toBeInTheDocument();
});

it("«По УПД» on a waiting group sends that group's supplier and warehouse with the scan", async () => {
  const calls = mockApi();
  render(<ReceivingPage />);
  fireEvent.click(await screen.findByRole("button", { name: "По УПД: ООО «Элитан»" }));
  fireEvent.change(await screen.findByLabelText("Файл УПД"), { target: { files: [new File(["%PDF"], "upd.pdf")] } });
  await screen.findByRole("heading", { name: "Поставка: ООО «Элитан»" });

  expect(calls.find((call) => call.path === "/api/receiving/upd/proposal")?.body).toMatchObject({ warehouse: "ЦАР", supplier: "ООО «Элитан»" });
});

it("«Склад → Поставка»: a scanned УПД awaited in «Приёмка» shows the banner and opens the same pre-filled delivery", async () => {
  mockApi({
    "GET /api/stock": () => json({ lots: [], total: 0, truncated: 0, next_cursor: null }),
    "GET /api/stock/receipts": () => json({ receipts: [] }),
    "GET /api/stock/article-info": () => json({ info: null, places: [], expected: null }),
    "POST /api/stock/receipts/scan": () => json({ id: "j1", status: "done", error: "", recognising: false, scan_file: scan.scan_file, result: scan }),
  });
  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Внести позиции" }));
  fireEvent.change(await screen.findByLabelText("Файл УПД"), { target: { files: [new File(["%PDF"], "upd.pdf")] } });

  expect(await screen.findByText(/Эти позиции ожидаются в Приёмке \(2 строки, заказы З-1, З-2\)/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Принять в Приёмке" }));
  const window = await screen.findByRole("dialog", { name: "Принять по УПД" });
  expect(within(window).getByLabelText("По УПД: PH-3 · Разъём PH")).toHaveValue(6);
});

// --- logic ----------------------------------------------------------------------------------------------------

it("sums quantities without floating noise and takes the invoiced figure in the order's unit", () => {
  expect(sumQuantities(["0.1", "0.2"])).toBe("0.3");
  expect(sumQuantities(["6", "4"])).toBe("10");
  const roll = { ...proposal.rows[0], quantity: "300", unit: "м", invoice_quantity: "3", invoice_unit: "рул." };
  expect(rowQuantity(roll, { ...lines[0], unit: "рул" })).toBe("3");
  expect(rowQuantity(roll, { ...lines[0], unit: "м" })).toBe("300");
});

it("reassigning a shared row takes it off every line it was on", () => {
  const rows = [
    { line: lines[0], ticked: true, document: "10", shares: [{ index: 0, quantity: "10" }] },
    { line: lines[1], ticked: true, document: "8", shares: [{ index: 0, quantity: "5" }, { index: 1, quantity: "3" }] },
    { line: lines[2], ticked: false, document: "", shares: [] },
  ];
  const moved = reassignRow(rows, { ...proposal.rows[0], quantity: "15" }, "l3");
  expect(moved.map((row) => [row.ticked, row.document])).toEqual([[false, ""], [true, "3"], [true, "15"]]);
});

it("the banner names the matched lines and orders; nothing matched — no banner", () => {
  expect(expectedBanner(proposal)).toBe("Эти позиции ожидаются в Приёмке (2 строки, заказы З-1, З-2)");
  expect(expectedBanner({ ...proposal, lines: proposal.lines.map((item) => ({ ...item, rows: [] })) })).toBe("");
  expect(expectedBanner(null)).toBe("");
});
