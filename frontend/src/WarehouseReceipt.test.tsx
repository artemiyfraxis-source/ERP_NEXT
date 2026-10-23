import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WarehousePage } from "./WarehousePage";
import type { WarehouseLot } from "./api";

const lot: WarehouseLot = {
  id: "40000000-0000-0000-0000-000000000001", version: 1, warehouse: "ЦАР", grn: "FRX.26.0000009", package: "",
  article: "GHR-04V-S", series: "", name: "Разъём", item_type: "Комплектующие", purpose: "Склад", unit: "шт",
  storage_location: "1F4", inbound: "10", written_off: "0", reserved: "0", balance: "10", available: "10", source_receiving_ids: [],
};
const receipt = {
  id: "r1", version: 1, warehouse: "ЦАР", supplier: "", invoice_number: "", invoice_date: null, upd_number: "", upd_date: null,
  purpose: "Склад", scan_file: "", created_at: "2026-10-23T10:00:00", created_by: "u", lots_count: 0,
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Call = { path: string; method: string; body: Record<string, unknown> | null };

/** The warehouse API of the supply on a mocked contract; `handlers` override single routes. */
function mockApi(handlers: Record<string, (call: Call) => Response | undefined> = {}) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const call: Call = { path, method: init?.method ?? "GET", body: typeof init?.body === "string" ? JSON.parse(init.body) : null };
    calls.push(call);
    const custom = handlers[`${call.method} ${path.split("?")[0]}`]?.(call);
    if (custom) return custom;
    if (call.method === "GET" && path.split("?")[0] === "/api/stock") return json({ lots: [], total: 0, truncated: 0, next_cursor: null });
    if (path.startsWith("/api/stock/article-info")) return json({ info: null, places: [], expected: null });
    if (path.startsWith("/api/stock/receipts?")) return json({ receipts: [] });
    if (call.method === "POST" && path === "/api/stock/receipts") return json({ ...receipt, ...call.body });
    if (call.method === "POST" && path === "/api/stock/receipts/r1/accept") {
      return json({ lot: { ...lot, storage_location: String(call.body?.storage_location) }, label_job_id: "j1", label_error: "" });
    }
    if (path.startsWith("/api/stock/places?")) return json({ warehouse: "ЦАР", shelf: "12C", occupied: ["12C1", "12C4"], next_number: 2 });
    throw new Error(`Unexpected request: ${path}`);
  });
  return calls;
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); window.localStorage.clear(); });

async function openSupply() {
  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Внести позиции" }));
  return screen.findByRole("dialog", { name: "Внести позиции" });
}

const cellOf = (label: string, row = 1) => screen.getByLabelText(`${label}, строка ${row}`) as HTMLInputElement;
const type = (label: string, value: string, row = 1) => fireEvent.change(cellOf(label, row), { target: { value } });

it("«XHP-4@JST» in Артикул: split on leaving the cell or at once when pasted, the lookup asks for the bare article", async () => {
  const calls = mockApi();
  await openSupply();
  type("Артикул", "XHP-4@JST");
  expect(cellOf("Артикул")).toHaveValue("XHP-4@JST"); // not while typing
  fireEvent.blur(cellOf("Артикул"));
  await waitFor(() => expect(cellOf("Артикул")).toHaveValue("XHP-4"));
  expect(cellOf("Производитель")).toHaveValue("JST");
  await waitFor(() => expect(calls.some((call) => call.path.startsWith("/api/stock/article-info?q=XHP-4&"))).toBe(true));

  const second = cellOf("Артикул", 2);
  fireEvent.change(cellOf("Производитель", 2), { target: { value: "Molex Inc." } });
  fireEvent.paste(second, { clipboardData: { getData: () => "0039012085@Molex" } });
  await waitFor(() => expect(cellOf("Артикул", 2)).toHaveValue("0039012085"));
  expect(cellOf("Производитель", 2)).toHaveValue("Molex Inc.");
});

it("types an article and a quantity: the lookup fills only the empty cells, quietly, with the supply's data", async () => {
  const calls = mockApi({
    "GET /api/stock/article-info": () => json({
      info: { name: "N-male RG-58", series: "N", item_type: "Коаксиальный соединитель", manufacturer: "Amphenol", source: "разбор кода" },
      places: [], expected: null,
    }),
  });
  await openSupply();
  fireEvent.change(screen.getByLabelText("Поставщик"), { target: { value: "ООО Ромашка" } });
  fireEvent.change(screen.getByLabelText("№ счёта"), { target: { value: "С-7" } });
  type("Серия", "МОЯ");
  type("Артикул", "N-C58P");
  type("Кол-во", "10");

  await waitFor(() => expect(cellOf("Наименование")).toHaveValue("N-male RG-58"));
  expect(cellOf("Серия")).toHaveValue("МОЯ");
  expect(cellOf("Производитель")).toHaveValue("Amphenol");
  expect(cellOf("Наименование")).toHaveClass("wr-auto");
  expect(cellOf("Серия")).not.toHaveClass("wr-auto");
  const lookup = calls.find((call) => call.path.startsWith("/api/stock/article-info"))!;
  const query = new URLSearchParams(lookup.path.split("?")[1]);
  expect(Object.fromEntries(query)).toEqual({ q: "N-C58P", supplier: "ООО Ромашка", invoice_number: "С-7", warehouse: "ЦАР" });

  fireEvent.change(cellOf("Наименование"), { target: { value: "Моё" } });
  expect(cellOf("Наименование")).not.toHaveClass("wr-auto");
});

it("pastes a legacy block with a header row: lines and the empty header fields", async () => {
  mockApi();
  await openSupply();
  const block = [
    "Контрагент\t№ УПД\tДата УПД\tНомер счета\tДата счета\tарт ПОСТАВЩИКА\tСЕРИЯ\tприход\tЕд. изм",
    "ООО Рога\tУПД-5\t01.10.2026\tС-7\t02.10.2026\tA-1\t13\t1 500,5\tшт",
    "\t\t\t\t\tA-2\t\t3\tм",
  ].join("\n");
  fireEvent.paste(cellOf("Артикул"), { clipboardData: { getData: () => block } });

  expect(cellOf("Артикул", 1)).toHaveValue("A-1");
  expect(cellOf("Серия", 1)).toHaveValue("13");
  expect(cellOf("Кол-во", 1)).toHaveValue("1500.5");
  expect(cellOf("Артикул", 2)).toHaveValue("A-2");
  expect(cellOf("Ед.", 2)).toHaveValue("м");
  expect(screen.getByLabelText("Поставщик")).toHaveValue("ООО Рога");
  expect(screen.getByLabelText("№ УПД")).toHaveValue("УПД-5");
  expect(screen.getByLabelText("Дата УПД")).toHaveValue("2026-10-01");
  expect(screen.getByLabelText("№ счёта")).toHaveValue("С-7");
  expect(screen.getByLabelText("Дата счёта")).toHaveValue("2026-10-02");
  expect(screen.getByText(/Вставлено строк: 2, из таблицы заполнена шапка \(5\)/)).toBeInTheDocument();
});

it("accepts a row: the supply is created first, then the position with its place and labels; the row turns read-only", async () => {
  const calls = mockApi();
  await openSupply();
  fireEvent.change(screen.getByLabelText("Поставщик"), { target: { value: "ООО Ромашка" } });
  type("Артикул", "GHR-04V-S");
  type("Кол-во", "10");
  fireEvent.click(screen.getByRole("button", { name: "Принять строку 1" }));

  const dialog = within(await screen.findByRole("dialog", { name: "Принять позицию" }));
  expect(dialog.getByText("GHR-04V-S · 10 шт")).toBeInTheDocument();
  fireEvent.click(dialog.getByRole("button", { name: "Принять" }));
  expect(await dialog.findByRole("alert")).toHaveTextContent("Укажите место");
  fireEvent.change(dialog.getByLabelText("Место хранения"), { target: { value: "1F4" } });
  fireEvent.change(dialog.getByLabelText("Этикеток"), { target: { value: "3" } });
  fireEvent.click(dialog.getByRole("button", { name: "Принять" }));

  expect(await screen.findByText(/Принято · ГРН FRX\.26\.0000009 · место 1F4/)).toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "Принять позицию" })).toBeNull();
  expect(screen.getByText(/этикеток 3/)).toBeInTheDocument();
});

it("sends the supply header before the first acceptance and the labels count with it", async () => {
  const calls = mockApi();
  await openSupply();
  fireEvent.change(screen.getByLabelText("Поставщик"), { target: { value: "ООО Ромашка" } });
  fireEvent.change(screen.getByLabelText("Дата счёта"), { target: { value: "2026-10-02" } });
  type("Артикул", "GHR-04V-S");
  type("Кол-во", "10");
  fireEvent.click(screen.getByRole("button", { name: "Принять строку 1" }));
  const dialog = within(await screen.findByRole("dialog", { name: "Принять позицию" }));
  fireEvent.change(dialog.getByLabelText("Место хранения"), { target: { value: "1F4" } });
  fireEvent.change(dialog.getByLabelText("Этикеток"), { target: { value: "3" } });
  fireEvent.click(dialog.getByRole("button", { name: "Принять" }));
  await screen.findByText(/Принято · ГРН/);

  const posts = calls.filter((call) => call.method === "POST").map((call) => call.path);
  expect(posts).toEqual(["/api/stock/receipts", "/api/stock/receipts/r1/accept"]);
  expect(calls.find((call) => call.path === "/api/stock/receipts" && call.method === "POST")!.body).toEqual({
    warehouse: "ЦАР", supplier: "ООО Ромашка", invoice_number: "", invoice_date: "2026-10-02", upd_number: "", upd_date: null, purpose: "Склад",
  });
  expect(calls.find((call) => call.path.endsWith("/accept"))!.body).toMatchObject({
    article: "GHR-04V-S", quantity: "10", unit: "шт", purpose: "Склад", storage_location: "1F4", labels: 3, confirm_expected: false,
  });
});

it("a later header edit goes up with the version before the next acceptance", async () => {
  const calls = mockApi({ "PUT /api/stock/receipts/r1": (call) => json({ ...receipt, ...call.body, version: 2 }) });
  await openSupply();
  for (const row of [1, 2]) {
    type("Артикул", `ART-${row}`, row);
    type("Кол-во", "1", row);
  }
  fireEvent.click(screen.getByRole("button", { name: "Принять строку 1" }));
  let dialog = within(await screen.findByRole("dialog", { name: "Принять позицию" }));
  fireEvent.change(dialog.getByLabelText("Место хранения"), { target: { value: "1F4" } });
  fireEvent.click(dialog.getByRole("button", { name: "Принять" }));
  await screen.findByText(/Принято · ГРН/);

  fireEvent.change(screen.getByLabelText("№ счёта"), { target: { value: "С-9" } });
  fireEvent.click(screen.getByRole("button", { name: "Принять строку 2" }));
  dialog = within(await screen.findByRole("dialog", { name: "Принять позицию" }));
  fireEvent.change(dialog.getByLabelText("Место хранения"), { target: { value: "1F5" } });
  fireEvent.click(dialog.getByRole("button", { name: "Принять" }));
  await waitFor(() => expect(calls.filter((call) => call.path.endsWith("/accept"))).toHaveLength(2));

  const put = calls.find((call) => call.method === "PUT")!;
  expect(put.body).toMatchObject({ invoice_number: "С-9", version: 1 });
  expect(calls.filter((call) => call.method === "POST" && call.path === "/api/stock/receipts")).toHaveLength(1);
});

it("a 409 «ожидается в Приёмке» shows the reason and accepts only on «Всё равно принять»", async () => {
  let attempts = 0;
  const calls = mockApi({
    "GET /api/stock/article-info": () => json({ info: null, places: ["1F4", "2A3"], expected: { order_number: "17", supplier: "ООО" } }),
    "POST /api/stock/receipts/r1/accept": (call) => {
      attempts += 1;
      if (!call.body?.confirm_expected) return json({ detail: "Артикул ждут в Приёмке по заказу № 17" }, 409);
      return json({ lot, label_job_id: null, label_error: "Принтер не отвечает" });
    },
  });
  await openSupply();
  type("Артикул", "GHR-04V-S");
  type("Кол-во", "2");
  expect(await screen.findByText("ожидается в Приёмке: заказ № 17")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Принять строку 1" }));
  const dialog = within(await screen.findByRole("dialog", { name: "Принять позицию" }));
  expect(dialog.getByText("уже лежит:")).toBeInTheDocument();
  fireEvent.click(dialog.getByRole("button", { name: "2A3" }));
  expect(dialog.getByLabelText("Место хранения")).toHaveValue("2A3");
  fireEvent.click(dialog.getByRole("button", { name: "Принять" }));

  expect(await dialog.findByText("Артикул ждут в Приёмке по заказу № 17")).toBeInTheDocument();
  expect(screen.queryByText(/Принято · ГРН/)).toBeNull();
  fireEvent.click(dialog.getByRole("button", { name: "Всё равно принять" }));

  expect(await screen.findByText(/Принято · ГРН/)).toBeInTheDocument();
  expect(screen.getByText("Принтер не отвечает")).toBeInTheDocument();
  expect(attempts).toBe(2);
  expect(calls.filter((call) => call.path.endsWith("/accept")).map((call) => call.body?.confirm_expected)).toEqual([false, true]);
});

it("marks a busy place in the acceptance window without blocking it, and prints the label once more on request", async () => {
  const calls = mockApi({
    "POST /api/stock/batches/40000000-0000-0000-0000-000000000001/label": () => json({ status: "queued", reason: "", file: "j", copies: 1, job_id: "j" }),
  });
  await openSupply();
  type("Артикул", "GHR-04V-S");
  type("Кол-во", "2");
  fireEvent.click(screen.getByRole("button", { name: "Принять строку 1" }));
  const dialog = within(await screen.findByRole("dialog", { name: "Принять позицию" }));
  const place = dialog.getByLabelText("Место хранения");
  fireEvent.change(place, { target: { value: "12C4" } });
  await waitFor(() => expect(place).toHaveClass("place-busy"));
  expect(dialog.getByText(/Здесь уже лежит другая позиция/)).toBeInTheDocument();
  fireEvent.click(dialog.getByRole("button", { name: "Принять" }));

  fireEvent.click(await screen.findByRole("button", { name: "Этикетка ещё раз" }));
  await waitFor(() => expect(calls.some((call) => call.path.endsWith("/label"))).toBe(true));
  expect(await screen.findByText(/этикеток 1/)).toBeInTheDocument();
});

it("resumes a recent supply: its accepted lots come back as read-only rows", async () => {
  mockApi({
    "GET /api/stock/receipts": () => json({ receipts: [{ ...receipt, supplier: "ООО Ромашка", upd_number: "5", lots_count: 1 }] }),
    "GET /api/stock/receipts/r1": () => json({ receipt: { ...receipt, supplier: "ООО Ромашка", upd_number: "5", lots_count: 1 }, lots: [lot] }),
  });
  await openSupply();
  fireEvent.change(await screen.findByLabelText("Продолжить поставку"), { target: { value: "r1" } });

  expect(await screen.findByText(/Принято · ГРН FRX\.26\.0000009 · место 1F4/)).toBeInTheDocument();
  expect(screen.getByLabelText("Поставщик")).toHaveValue("ООО Ромашка");
  expect(screen.getByLabelText("№ УПД")).toHaveValue("5");
});

it("scans a УПД: fills the empty header fields, appends the lines and shows the note", async () => {
  mockApi({
    "POST /api/stock/receipts/scan": () => json({
      scan_file: "scan-1.pdf", note: "Распознано: 1 строка. Проверьте.",
      recognised: { supplier: "ООО Скан", inn: "", upd_number: "17", upd_date: "03.10.2026", invoice_number: "", invoice_date: "" },
      lines: [{ article: "Z-1", name: "Зет", quantity: "4", unit: "шт" }],
    }),
  });
  await openSupply();
  fireEvent.change(screen.getByLabelText("№ счёта"), { target: { value: "С-1" } });
  fireEvent.change(screen.getByLabelText("Файл УПД"), { target: { files: [new File(["x"], "upd.pdf", { type: "application/pdf" })] } });

  expect(await screen.findByText("Распознано: 1 строка. Проверьте.")).toBeInTheDocument();
  expect(screen.getByLabelText("Поставщик")).toHaveValue("ООО Скан");
  expect(screen.getByLabelText("Дата УПД")).toHaveValue("2026-10-03");
  expect(screen.getByLabelText("№ счёта")).toHaveValue("С-1");
  expect(cellOf("Артикул")).toHaveValue("Z-1");
  expect(cellOf("Наименование")).toHaveValue("Зет");
});

it("scans from the scanner: waits for the PC, then fills the header like a file upload", async () => {
  const calls = mockApi({
    "POST /api/stock/receipts/scan-jobs": () => json({ id: "s1", status: "pending" }),
    "GET /api/stock/receipts/scan-jobs/s1": () => json({
      id: "s1", status: "done", error: "",
      result: {
        scan_file: "scan-2.pdf", note: "Распознано: 1 строка.",
        recognised: { supplier: "ООО Сканер", inn: "", upd_number: "21", upd_date: "", invoice_number: "", invoice_date: "" },
        lines: [{ article: "S-1", name: "Эс", quantity: "2", unit: "шт" }],
      },
    }),
  });
  await openSupply();
  fireEvent.click(screen.getByRole("button", { name: "Сканировать" }));

  expect(await screen.findByText("Распознано: 1 строка.")).toBeInTheDocument();
  expect(screen.getByLabelText("Поставщик")).toHaveValue("ООО Сканер");
  expect(cellOf("Артикул")).toHaveValue("S-1");
  expect(calls.some((call) => call.method === "POST" && call.path === "/api/stock/receipts/scan-jobs")).toBe(true);
  expect(screen.getByRole("button", { name: "+ Страница" })).toBeEnabled();
});

it("shows the scanner error in red and frees the buttons", async () => {
  mockApi({
    "POST /api/stock/receipts/scan-jobs": () => json({ id: "s2", status: "pending" }),
    "GET /api/stock/receipts/scan-jobs/s2": () => json({ id: "s2", status: "failed", error: "Сканер не отвечает", result: null }),
  });
  await openSupply();
  fireEvent.click(screen.getByRole("button", { name: "Сканировать" }));

  const status = await screen.findByText("Сканер не отвечает");
  expect(status).toHaveClass("error");
  expect(screen.getByRole("button", { name: "Сканировать" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Файл…" })).toBeEnabled();
});

it("cancels a waiting scan: DELETE goes out and the buttons come back", async () => {
  const calls = mockApi({
    "POST /api/stock/receipts/scan-jobs": () => json({ id: "s3", status: "pending" }),
    "GET /api/stock/receipts/scan-jobs/s3": () => json({ id: "s3", status: "pending", error: "", result: null }),
    "DELETE /api/stock/receipts/scan-jobs/s3": () => json({ id: "s3", status: "failed" }),
  });
  await openSupply();
  fireEvent.click(screen.getByRole("button", { name: "Сканировать" }));

  expect(await screen.findByText(/Положите УПД на стекло сканера/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Сканировать" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Файл…" })).toBeDisabled();
  fireEvent.click(screen.getByText("Отменить"));

  await waitFor(() => expect(screen.getByRole("button", { name: "Сканировать" })).toBeEnabled());
  expect(calls.some((call) => call.method === "DELETE" && call.path === "/api/stock/receipts/scan-jobs/s3")).toBe(true);
  expect(screen.queryByText(/Положите УПД/)).toBeNull();
});

it("starts with one blank row, adds the next when the article is typed, and offers no buttons on blank rows", async () => {
  mockApi();
  await openSupply();
  expect(screen.getAllByLabelText(/^Артикул, строка/)).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "Принять строку 1" })).toBeNull();
  type("Артикул", "GHR-04V-S");
  expect(screen.getAllByLabelText(/^Артикул, строка/)).toHaveLength(2);
  expect(screen.getByRole("button", { name: "Принять строку 1" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Принять строку 2" })).toBeNull();
});


const answer = (scan_file: string, article: string) => ({
  scan_file, note: "Распознано.",
  recognised: { supplier: "ООО Скан", inn: "", upd_number: "17", upd_date: "", invoice_number: "", invoice_date: "" },
  lines: [{ article, name: "Н", quantity: "1", unit: "шт" }],
});

function scannerPages(files: string[]) {
  let started = 0;
  return mockApi({
    "POST /api/stock/receipts/scan-jobs": () => json({ id: `p${++started}`, status: "pending" }),
    "GET /api/stock/receipts/scan-jobs/p1": () => json({ id: "p1", status: "done", error: "", result: answer(files[0], "A-1") }),
    "GET /api/stock/receipts/scan-jobs/p2": () => json({ id: "p2", status: "done", error: "", result: answer(files[1], "A-2") }),
    "GET /api/stock/receipts/scan-jobs/p3": () => json({ id: "p3", status: "done", error: "", result: answer(files[2], "A-3") }),
    "POST /api/stock/receipts/scan-merge": () => json(answer("merged.pdf", "A-2")),
  });
}

it("a second sheet is merged with the first one: scan-merge gets both links, the line count is page-wide", async () => {
  const calls = scannerPages(["p1.pdf", "p2.pdf", "p3.pdf"]);
  await openSupply();
  fireEvent.click(screen.getByRole("button", { name: "Сканировать" }));
  expect(await screen.findByText("Скан:", { exact: false })).toBeInTheDocument();
  expect(screen.getByText(/1 стр\./)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "+ Страница" }));
  expect(await screen.findByText(/2 стр\./)).toBeInTheDocument();
  const merge = calls.filter((call) => call.path === "/api/stock/receipts/scan-merge");
  expect(merge).toHaveLength(1);
  expect(merge[0].body).toEqual({ files: ["p1.pdf", "p2.pdf"] });
  expect(screen.getByRole("link", { name: "открыть" })).toHaveAttribute("href", "merged.pdf");
  expect(cellOf("Артикул", 1)).toHaveValue("A-1");
  expect(cellOf("Артикул", 2)).toHaveValue("A-2");
});

it("«Сканировать заново» drops the pages and the scan", async () => {
  const calls = scannerPages(["p1.pdf", "p2.pdf", "p3.pdf"]);
  await openSupply();
  fireEvent.click(screen.getByRole("button", { name: "Сканировать" }));
  expect(await screen.findByText(/1 стр\./)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Сканировать заново" }));
  await waitFor(() => expect(calls.filter((call) => call.path === "/api/stock/receipts/scan-jobs" && call.method === "POST")).toHaveLength(2));
  expect(await screen.findByText(/1 стр\./)).toBeInTheDocument();
  expect(calls.some((call) => call.path === "/api/stock/receipts/scan-merge")).toBe(false);
});

it("several files are uploaded one by one and merged once", async () => {
  let uploaded = 0;
  const calls = mockApi({
    "POST /api/stock/receipts/scan": () => json(answer(`f${++uploaded}.pdf`, `F-${uploaded}`)),
    "POST /api/stock/receipts/scan-merge": () => json(answer("merged.pdf", "F-1")),
  });
  await openSupply();
  const files = ["a", "b", "c"].map((name) => new File(["x"], `${name}.pdf`, { type: "application/pdf" }));
  fireEvent.change(screen.getByLabelText("Файл УПД"), { target: { files } });
  expect(await screen.findByText(/3 стр\./)).toBeInTheDocument();
  const merge = calls.filter((call) => call.path === "/api/stock/receipts/scan-merge");
  expect(merge).toHaveLength(1);
  expect(merge[0].body).toEqual({ files: ["f1.pdf", "f2.pdf", "f3.pdf"] });
});