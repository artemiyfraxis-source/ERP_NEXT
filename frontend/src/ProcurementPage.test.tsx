import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProcurementPage } from "./ProcurementPage";
import type { ProcurementLine } from "./api";

const line: ProcurementLine = {
  id: "30000000-0000-0000-0000-000000000001",
  legacy_id: "REQ-1",
  revision: "A",
  status: "В работе",
  version: 3,
  request_date: "2026-09-16",
  initiator: "Иванов",
  department: "КБ",
  designation: "D-1",
  project_reference: "591К.3",
  priority: "Обычный",
  delivery_point: "Москва",
  planned_date: "2026-09-30",
  link: null,
  item_type: "Комплектующие",
  design_article: null,
  category: "Общепром",
  calculated_name: "Разъём",
  calculated_quantity: "2",
  calculated_unit: "шт",
  buyer: null,
  supplier: null,
  site_name: null,
  actual_quantity: null,
  actual_unit: null,
  quality_document: "Нет",
  supplier_request_reference: null,
  order_number: null,
  order_date: null,
  expected_arrival: null,
  invoice_url: null,
  paid_at: null,
  currency: null,
  actual_price: null,
  actual_vat_rate: null,
  actual_price_with_vat: null,
  actual_sum: null,
  source_currency: null,
  source_price: null,
  source_price_with_vat: null,
  source_sum: null,
  exchange_rate: null,
  exchange_rate_date: null,
  exchange_rate_manual: false,
  invoice_item_name: null,
  invoice_number: null,
  invoice_date: null,
  pay_missing: ["ВАЛЮТА", "ЦЕНА (факт)", "ЦЕНА С НДС (факт)", "СУММА (факт)", "НАИМЕНОВАНИЕ (Счет)", "ОЖИД. ДАТА"],
  is_backorder: false,
  replacement: null,
  replacement_decision: null,
  replacement_open: false,
  permission_number: null,
  permission_date: null,
};

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => vi.restoreAllMocks());

it("takes a line and saves facts with its optimistic version", async () => {
  let taken = false;
  let savedBody: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") {
      return json({ lines: taken ? [] : [line] });
    }
    if (path === "/api/procurement/mine") {
      return json({ lines: taken ? [{ ...line, buyer: "Закупщик" }] : [] });
    }
    if (path === "/api/procurement/take" && init?.method === "POST") {
      taken = true;
      return json({ lines: [{ ...line, buyer: "Закупщик" }] });
    }
    if (path === "/api/procurement/facts" && init?.method === "POST") {
      savedBody = JSON.parse(String(init.body));
      return json({ lines: [], saved: 1, ready: 1, skipped: [], failed: [] });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Взять" }));
  fireEvent.change(await screen.findByLabelText("Поставщик REQ-1"), {
    target: { value: "Поставщик" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить всё (1)" }));

  await waitFor(() => expect(savedBody).toEqual({ rows: [expect.objectContaining({
    version: 3,
    supplier: "Поставщик",
    actual_quantity: "2",
    actual_unit: "шт",
  })] }));
});

it("blocks duplicate page loads and reports refresh failures", async () => {
  let pageCalls = 0;
  let failRefresh = false;
  let releasePage: (() => void) | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (failRefresh && !path.includes("after=")) throw new TypeError("network offline");
    if (path === "/api/procurement/available") {
      return json({ lines: [line], next_cursor: "next-page" });
    }
    if (path === "/api/procurement/mine") {
      return json({ lines: [], next_cursor: null });
    }
    if (path === "/api/procurement/available?after=next-page") {
      pageCalls += 1;
      await new Promise<void>((resolve) => { releasePage = resolve; });
      return json({ lines: [line], next_cursor: null });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  const more = await screen.findByRole("button", { name: "Ещё неразобранные" });
  fireEvent.click(more);
  fireEvent.click(more);
  expect(pageCalls).toBe(1);
  releasePage?.();
  await waitFor(() => expect(screen.queryByText("Загружаем…")).not.toBeInTheDocument());
  expect(screen.getAllByText("REQ-1")).toHaveLength(1);

  failRefresh = true;
  fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("network offline");
});

const awaitingInvoice: ProcurementLine = {
  ...line,
  status: "Жду счет",
  buyer: "Закупщик",
  supplier: "Поставщик",
  actual_quantity: "2",
  actual_unit: "шт",
};

/** Меню «⋯» строки стадии → пункт меню (кнопка ждёт конца перезагрузки очереди). */
async function pickRowAction(id: string, item: string | RegExp) {
  await clickWhenEnabled(`Действия ${id}`);
  fireEvent.click(await screen.findByRole("menuitem", { name: item }));
}

async function clickWhenEnabled(name: string | RegExp) {
  await waitFor(() => expect(screen.getByRole("button", { name })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name }));
}

/** «Жду счет»: строки заказа раскрываются по строке таблицы заказов. */
async function openOrder(number: string) {
  fireEvent.click(await screen.findByText(number));
}

function problem(status: number, detail: unknown) {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

it("keeps the invoice closed until fact money is saved with the edited field as driver", async () => {
  let current = awaitingInvoice;
  let moneyBody: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine") return json({ lines: [current], next_cursor: null });
    if (path.endsWith("/money") && init?.method === "PATCH") {
      moneyBody = JSON.parse(String(init.body));
      current = {
        ...current, version: 5, currency: "RUB", actual_price: "100", actual_vat_rate: "22",
        actual_price_with_vat: "122", actual_sum: "244", invoice_item_name: "Разъём", pay_missing: ["ОЖИД. ДАТА"],
      };
      return json({ line: current });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  await openOrder("Без № заказа");
  await pickRowAction("REQ-1", "Суммы и счёт строки…");
  const toPay = await screen.findByRole("button", { name: "В оплату" });
  expect(toPay).toBeDisabled();
  expect(screen.getByText(/Для счёта не хватает: ВАЛЮТА/)).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Цена без НДС"), { target: { value: "100" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить суммы" }));

  await waitFor(() => expect(moneyBody).toEqual(expect.objectContaining({
    version: 3, driver: "net", currency: "RUB", price: "100", vat_rate: "22", invoice_item_name: "Разъём",
  })));
  await waitFor(() => expect(screen.getByRole("button", { name: "В оплату" })).toBeEnabled());
});

it("asks for a manual rate when the CBR is down and resends the money with it", async () => {
  const bodies: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine") return json({ lines: [awaitingInvoice], next_cursor: null });
    if (path.endsWith("/money") && init?.method === "PATCH") {
      const body = JSON.parse(String(init.body));
      bodies.push(body);
      if (!body.manual_exchange_rate) {
        return problem(409, {
          code: "manual_rate_required", currency: "USD", date: "2026-09-28",
          message: "ЦБ не отдал курс USD на 28.09.2026 — введите курс вручную",
        });
      }
      return json({ line: { ...awaitingInvoice, exchange_rate_manual: true } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  await openOrder("Без № заказа");
  await pickRowAction("REQ-1", "Суммы и счёт строки…");
  fireEvent.change(await screen.findByLabelText(/Валюта/), { target: { value: "USD" } });
  fireEvent.change(screen.getByLabelText("Цена без НДС"), { target: { value: "10" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить суммы" }));

  fireEvent.change(await screen.findByLabelText(/Курс, ₽ за 1 USD/), { target: { value: "95,5" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить суммы" }));

  await waitFor(() => expect(bodies).toHaveLength(2));
  expect(bodies[1]).toEqual(expect.objectContaining({ currency: "USD", manual_exchange_rate: "95,5" }));
});

it("confirms a paid backorder with a new date or cancels it", async () => {
  const backorder: ProcurementLine = {
    ...awaitingInvoice,
    id: "30000000-0000-0000-0000-00000000000d",
    legacy_id: "REQ-1-Д",
    status: "Согласование",
    is_backorder: true,
    invoice_number: "17",
    actual_sum: "305",
    pay_missing: [],
  };
  const calls: Array<[string, Record<string, unknown>]> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine") return json({ lines: [backorder], next_cursor: null });
    if (path.includes("/backorder/") && init?.method === "POST") {
      calls.push([path, JSON.parse(String(init.body))]);
      return json({ line: backorder });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  expect(await screen.findByText(/Дозавоз по REQ-1: недовоз уже оплачен по счёту № 17/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Новая ожидаемая дата REQ-1-Д"), { target: { value: "2026-10-12" } });
  fireEvent.click(screen.getByRole("button", { name: "Досыл подтверждён → в пути" }));
  await waitFor(() => expect(calls).toHaveLength(1));
  await pickRowAction("REQ-1-Д", "Отменить дозавоз");

  await waitFor(() => expect(calls).toHaveLength(2));
  expect(calls[0]).toEqual([`/api/procurement/${backorder.id}/backorder/confirm`, { version: 3, expected_arrival: "2026-10-12" }]);
  expect(calls[1]).toEqual([`/api/procurement/${backorder.id}/backorder/cancel`, { version: 3 }]);
});

it("parks a line on approval, resumes it and cancels only after confirmation", async () => {
  const working: ProcurementLine = { ...line, buyer: "Закупщик" };
  let current = working;
  const calls: Array<[string, Record<string, unknown>]> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine") return json({ lines: [current], next_cursor: null });
    if (init?.method === "POST") {
      calls.push([path, JSON.parse(String(init.body))]);
      const status = path.endsWith("/approval") ? "Согласование" : path.endsWith("/resume") ? "В работе" : "Отменено";
      current = { ...current, status, version: current.version + 1 };
      return json({ line: current });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);

  render(<ProcurementPage />);
  await pickRowAction("REQ-1", "На согласование");
  expect(await screen.findByRole("heading", { name: /Согласование/ })).toBeInTheDocument();
  await clickWhenEnabled("Одобрено — продолжить");
  expect(await screen.findByText("REQ-1: одобрено, строка вернулась в работу")).toBeInTheDocument();
  await pickRowAction("REQ-1", "На согласование");
  await waitFor(() => expect(calls).toHaveLength(3));
  await pickRowAction("REQ-1", "Отменить строку");
  expect(confirm).toHaveBeenCalledTimes(1);
  expect(calls).toHaveLength(3);
  await pickRowAction("REQ-1", "Отменить строку");

  await waitFor(() => expect(calls).toHaveLength(4));
  expect(calls.map(([path, body]) => [path.replace(`/api/procurement/${line.id}`, ""), body.version])).toEqual([
    ["/approval", 3], ["/approval/resume", 4], ["/approval", 5], ["/cancel", 6],
  ]);
});

it("does not offer generic approval or cancel on a backorder", async () => {
  const backorder: ProcurementLine = { ...line, status: "Согласование", is_backorder: true, legacy_id: "REQ-1-Д" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine") return json({ lines: [backorder], next_cursor: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  expect(await screen.findByRole("button", { name: "Досыл подтверждён → в пути" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Одобрено — продолжить" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Действия REQ-1-Д" }));
  expect(screen.getByRole("menuitem", { name: "Отменить дозавоз" })).toBeInTheDocument();
  expect(screen.queryByRole("menuitem", { name: "Отменить строку" })).not.toBeInTheDocument();
  expect(screen.queryByRole("menuitem", { name: "На согласование" })).not.toBeInTheDocument();
});

it("forms supplier requests for ready lines in one batch and shows the letters", async () => {
  const ready: ProcurementLine = { ...line, status: "Готов к запросу", buyer: "Закупщик", supplier: "ООО А" };
  let formed = false;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine") {
      return json({
        lines: [formed ? {
          ...ready, status: "Жду счет", order_number: "PO-001", order_date: "2026-09-16",
          order_url: "/api/procurement/supplier-requests/PO-001/document",
        } : ready],
        next_cursor: null,
      });
    }
    if (path === "/api/procurement/supplier-requests" && init?.method === "POST") {
      formed = true;
      return json({
        orders: [{
          order_number: "PO-001", order_date: "2026-09-16", supplier: "ООО А", task_type: "Закупка",
          delivery_point: "Москва", buyer: "Закупщик", letter: "ООО А · REQ# 16.09.2026 - MR#D-1 PO-001",
          line_ids: [ready.id],
          document: {
            url: "/api/procurement/supplier-requests/PO-001/document",
            file_name: "PO-001 от 16.09.2026.html",
            folder: "Запросы поставщикам/2026/09 Сентябрь/ООО А",
          },
        }],
        lines: [],
        skipped: [],
        other_buyer: 2,
      });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  expect(await screen.findByRole("heading", { name: /Готов к запросу/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Сформировать запросы поставщикам" }));

  expect(await screen.findByText("Сформировано запросов: 1; строк других закупщиков не тронуто: 2")).toBeInTheDocument();
  expect(screen.getByText("ООО А · REQ# 16.09.2026 - MR#D-1 PO-001")).toBeInTheDocument();
  // Сформированный заказ — строка таблицы «Жду счет»: № заказа, поставщик, дата, документ.
  const order = (await screen.findByText("PO-001", { selector: "td" })).closest("tr")!;
  expect(within(order).getByText("ООО А")).toBeInTheDocument();
  expect(within(order).getByText("16.09.2026")).toBeInTheDocument();
  expect(screen.getByText("Запросы поставщикам/2026/09 Сентябрь/ООО А/PO-001 от 16.09.2026.html")).toBeInTheDocument();
  const links = screen.getAllByRole("link", { name: "Открыть документ" });
  expect(links.map((link) => link.getAttribute("href"))).toEqual([
    "/api/procurement/supplier-requests/PO-001/document", "/api/procurement/supplier-requests/PO-001/document",
  ]);
  expect(screen.getAllByRole("button", { name: "Печать" })).toHaveLength(2);
  expect(screen.queryByRole("button", { name: "Сформировать запросы поставщикам" })).not.toBeInTheDocument();
});

it("replaces the invoice of a paid line without touching its payment", async () => {
  const shipped: ProcurementLine = {
    ...line,
    id: "30000000-0000-0000-0000-00000000000e",
    status: "В пути",
    invoice_url: "https://files.test/invoice.pdf",
    invoice_number: "17",
    expected_arrival: "2026-10-01",
    paid_at: "2026-09-22T12:00:00+03:00",
    pay_missing: [],
  };
  const calls: Array<[string, Record<string, unknown>]> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine") return json({ lines: [shipped], next_cursor: null });
    if (path.endsWith("/invoice/replace") && init?.method === "POST") {
      calls.push([path, JSON.parse(String(init.body))]);
      return json({ line: { ...shipped, invoice_url: "https://files.test/invoice-v2.pdf", version: 4 } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  await pickRowAction("REQ-1", "Заменить счёт…");
  fireEvent.change(await screen.findByLabelText("Ссылка на новый счёт"), { target: { value: "https://files.test/invoice-v2.pdf" } });
  fireEvent.change(screen.getByLabelText("№ счёта"), { target: { value: " 18 " } });
  fireEvent.click(screen.getByRole("button", { name: "Заменить" }));

  await waitFor(() => expect(calls).toHaveLength(1));
  expect(calls[0]).toEqual([
    `/api/procurement/${shipped.id}/invoice/replace`,
    { version: 3, invoice_url: "https://files.test/invoice-v2.pdf", invoice_number: "18", invoice_date: null },
  ]);
});

it("splits an own line into supplier copies with its optimistic version", async () => {
  let splitBody: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine") {
      return json({ lines: [{ ...line, status: "В пути", buyer: "Закупщик" }], next_cursor: null });
    }
    if (path.endsWith("/split") && init?.method === "POST") {
      splitBody = JSON.parse(String(init.body));
      return json({ line: { ...line, version: 4 }, copies: [] });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  await pickRowAction("REQ-1", "Раздробить по поставщикам…");
  fireEvent.change(await screen.findByLabelText(/Раздробить по поставщикам, копий/), { target: { value: "3" } });
  fireEvent.click(screen.getByRole("button", { name: "Раздробить" }));

  await waitFor(() => expect(splitBody).toEqual({ version: 3, copies: 3 }));
  expect(await screen.findByText(/создано копий — 3/)).toBeInTheDocument();
});

it("fills the whole supplier order from a КП file and sends one invoice for it", async () => {
  const awaiting = {
    ...line,
    status: "Жду счет" as const,
    order_number: "PO-001",
    actual_quantity: "2",
    actual_unit: "шт",
  };
  const other = { ...awaiting, id: "30000000-0000-0000-0000-000000000002", legacy_id: "REQ-2", calculated_name: "Кабель", version: 5 };
  let parsed: Record<string, unknown> | undefined;
  let saved: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine") return json({ lines: saved ? [] : [awaiting, other] });
    if (path === "/api/procurement/kp?reference=PO-001") return json({ order_reference: "PO-001", lines: [awaiting, other] });
    if (path === "/api/procurement/kp/parse") {
      parsed = JSON.parse(String(init?.body));
      return json({
        items: [{ name: "Разъём SMA", qty: "3", price: "100", vat: "20" }, { name: "Болт", qty: "", price: "", vat: "" }],
        matches: [{ line_id: awaiting.id, name: "Разъём SMA", qty: "3", price: "100", vat: "20" }],
        not_found: ["Болт"],
      });
    }
    if (path === "/api/procurement/kp" && init?.method === "POST") {
      saved = JSON.parse(String(init.body));
      return json({ lines: [awaiting, other], invoice_error: "" });
    }
    throw new Error(`Unexpected ${path}`);
  });

  render(<ProcurementPage />);
  fireEvent.click((await screen.findAllByRole("button", { name: "Внести КП" }))[0]);
  expect(await screen.findByLabelText("REQ-2 Кол-во (факт)")).toHaveValue("2");

  const file = new File(["Наименование;Кол-во\n"], "kp.csv", { type: "text/csv" });
  fireEvent.change(screen.getByLabelText("Файл КП (.xlsx, .csv)"), { target: { files: [file] } });
  expect(await screen.findByText("Подтянуто строк: 1 из 2 · не найдены в заказе: Болт")).toBeInTheDocument();
  expect(parsed).toMatchObject({ order_reference: "PO-001", file_name: "kp.csv" });
  expect(screen.getByLabelText("REQ-1 Кол-во (факт)")).toHaveValue("3");
  expect(screen.getByLabelText("REQ-1 Наименование (счёт)")).toHaveValue("Разъём SMA");

  fireEvent.change(screen.getByLabelText("Ожид. дата — всем"), { target: { value: "2026-10-01" } });
  fireEvent.change(screen.getByLabelText("Ссылка на счёт (один на заказ, необязательно)"), { target: { value: "https://files.test/inv.pdf" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить КП" }));

  await waitFor(() => expect(saved).toBeDefined());
  expect(saved).toMatchObject({ order_reference: "PO-001", invoice_url: "https://files.test/inv.pdf" });
  const rows = saved?.rows as Record<string, unknown>[];
  expect(rows[0]).toMatchObject({ line_id: awaiting.id, version: 3, actual_quantity: "3", price: "100", vat_rate: "20", driver: "net", expected_arrival: "2026-10-01" });
  expect(rows[1]).toMatchObject({ line_id: other.id, version: 5, actual_quantity: "2", expected_arrival: "2026-10-01" });
  expect(await screen.findByText("Заказ PO-001: КП сохранено по 2 строкам, счёт передан в оплату")).toBeInTheDocument();
});

it("hides the retired substitution and quality-document fields on every stage", async () => {
  const base: ProcurementLine = { ...line, buyer: "Закупщик", supplier: "Поставщик", actual_quantity: "2", actual_unit: "шт" };
  const lines: ProcurementLine[] = [
    { ...base, id: "30000000-0000-0000-0000-0000000000a1", legacy_id: "REQ-A", status: "В работе", replacement: true },
    {
      ...base, id: "30000000-0000-0000-0000-0000000000a2", legacy_id: "REQ-B", status: "Готов к запросу",
      replacement: true, replacement_open: true, quality_document: "Да",
    },
    {
      ...base, id: "30000000-0000-0000-0000-0000000000a3", legacy_id: "REQ-C", status: "Жду счет", order_number: "PO-001",
      replacement: true, replacement_decision: "Разрешено", permission_number: "РЗ-20260929-01", permission_date: "2026-09-29",
    },
    {
      ...base, id: "30000000-0000-0000-0000-0000000000a4", legacy_id: "REQ-D", status: "В оплату", invoice_url: "https://files.test/i.pdf",
      pay_missing: [], replacement: true, replacement_decision: "Разрешено", permission_number: "РЗ-20260929-02",
    },
  ];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine") return json({ lines });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  await screen.findByRole("heading", { name: /Готов к запросу/ });
  expect(screen.queryByLabelText("Замена")).not.toBeInTheDocument();
  expect(screen.queryByText(/незакрытая замена/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Разрешение на замену/ })).not.toBeInTheDocument();

  // Меню строк: ни «Замены», ни «Разрешения на замену», ни документа качества.
  const menus: Record<string, string[]> = {};
  for (const id of ["REQ-A", "REQ-B", "REQ-D"]) {
    fireEvent.click(screen.getByRole("button", { name: `Действия ${id}` }));
    menus[id] = screen.getAllByRole("menuitem").map((item) => item.textContent ?? "");
    fireEvent.mouseDown(document.body);
  }
  expect(menus["REQ-B"]).toEqual(["Прослеживаемость…", "Раздробить по поставщикам…", "На согласование", "Отменить строку"]);
  for (const items of Object.values(menus)) {
    expect(items.filter((item) => /замен(а|у)(?! счёт)|качества|разрешен/i.test(item))).toEqual([]);
  }
  fireEvent.click(screen.getByRole("button", { name: "Действия REQ-A" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Прослеживаемость, раздробить…" }));
  expect(await screen.findByLabelText("Изготовитель")).toBeInTheDocument();
  expect(screen.queryByLabelText("№ док. качества")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Тип док.")).not.toBeInTheDocument();
});

it("lists unsorted lines with their supplier and says when a line has none", async () => {
  const withSupplier = { ...line, status: "Новый", supplier: "ЧипДип" };
  const without = { ...line, id: "30000000-0000-0000-0000-000000000002", legacy_id: "REQ-2", status: "Новый", supplier: null };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [withSupplier, without], next_cursor: null });
    if (path === "/api/procurement/mine") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/meta") return json({ units: ["шт"], statuses: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  const table = await screen.findByRole("region", { name: "Неразобранные" });
  expect(within(table).getByRole("columnheader", { name: "Поставщик" })).toBeInTheDocument();
  const first = within(table).getByText("REQ-1").closest("tr")!;
  const second = within(table).getByText("REQ-2").closest("tr")!;
  expect(within(first).getByText("ЧипДип")).toBeInTheDocument();
  expect(within(first).queryByText(/нет \(запрос не сформируется\)/)).not.toBeInTheDocument();
  expect(within(second).getByText("нет (запрос не сформируется)")).toBeInTheDocument();
});

it("shows each stage after «В работе» as its own table, not a card per line", async () => {
  const base: ProcurementLine = { ...line, buyer: "Закупщик", supplier: "Поставщик", actual_quantity: "2", actual_unit: "шт" };
  const at = (n: number, status: string, extra: Partial<ProcurementLine> = {}): ProcurementLine => ({
    ...base, id: `30000000-0000-0000-0000-0000000001${n}0`, legacy_id: `REQ-${n}`, status, ...extra,
  });
  const lines = [
    at(1, "В работе"),
    at(2, "Готов к запросу"),
    at(3, "Жду счет", { order_number: "PO-001" }),
    at(4, "В оплату", { invoice_url: "https://files.test/i.pdf", pay_missing: [] }),
    at(5, "Согласование"),
    at(6, "В пути", { invoice_url: "https://files.test/i.pdf", expected_arrival: "2026-10-12", order_number: "PO-001" }),
    at(7, "Карантин", { invoice_url: "https://files.test/j.pdf", order_number: "PO-001" }),
  ];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine") return json({ lines });
    throw new Error(`Unexpected request: ${path}`);
  });

  const { container } = render(<ProcurementPage />);
  await screen.findByRole("region", { name: "В пути" });
  const headings = screen.getAllByRole("heading", { level: 2 });
  const stages = ["В работе", "Готов к запросу", "Жду счет", "В оплату", "Согласование", "В пути", "Карантин"];
  const order = headings.map((heading) => stages.find((stage) => heading.textContent?.startsWith(stage))).filter(Boolean);
  expect(order).toEqual(stages);
  expect(container.querySelectorAll(".procurement-card")).toHaveLength(0);

  const head = (name: string) => within(screen.getByRole("region", { name })).getAllByRole("columnheader").map((cell) => cell.textContent);
  expect(head("Готов к запросу")).toEqual(["ID", "Наименование", "Наименование на сайте", "Назначение", "Поставщик", "Кол-во (факт)", "Точка отгрузки", "Срок", "Комментарий", ""]);
  expect(head("Жду счет")).toEqual(["№ заказа", "Поставщик", "Позиций", "Заказ от", "Ожидается", "КП внесено", "Документ", ""]);
  expect(head("В оплату")).toEqual(["", "ID", "№ заказа", "Поставщик", "Наименование", "Кол-во (факт)", "Сумма, ₽", "Счёт", "Ожид. дата", "Готовность", "Комментарий", ""]);
  // «Основание» (дозавоз), «Сумма» и валютные колонки появляются, когда есть что в них показать.
  expect(head("Согласование")).toEqual(["ID", "Наименование", "Поставщик", "Кол-во", "Вид", "Комментарий", "Решение", ""]);
  // «Ожид. дата» правится в ячейке, «Факт. дата» (приёмка) только показана; «Комментарий» — последний перед меню.
  expect(head("В пути")).toEqual(["ID", "Наименование", "Поставщик", "№ заказа", "Кол-во (факт)", "Ожид. дата", "Факт. дата", "Счёт", "Сумма, ₽", "Комментарий", ""]);
  expect(head("Карантин")).toEqual(["ID", "Наименование", "Поставщик", "№ заказа", "Кол-во (факт)", "Ожид. дата", "Факт. дата", "Счёт", "Сумма, ₽", "Комментарий", ""]);
  // «Ожид. дата» в пути — датой по-русски.
  expect(within(screen.getByRole("region", { name: "В пути" })).getByText("12.10.2026")).toBeInTheDocument();
});

it("groups «Жду счет» by order and opens «Внести КП» for the chosen order only", async () => {
  const first = { ...line, status: "Жду счет" as const, buyer: "Закупщик", supplier: "ООО А", order_number: "PO-001", order_date: "2026-10-01", actual_quantity: "2", actual_unit: "шт" };
  const second = { ...first, id: "30000000-0000-0000-0000-000000000002", legacy_id: "REQ-2", calculated_name: "Кабель", version: 4 };
  const third = { ...first, id: "30000000-0000-0000-0000-000000000003", legacy_id: "REQ-3", calculated_name: "Болт", supplier: "ООО Б", order_number: "PO-002", version: 5, actual_sum: "120" };
  const requested: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    requested.push(path);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine") return json({ lines: [first, second, third] });
    if (path === "/api/procurement/kp?reference=PO-002") return json({ order_reference: "PO-002", lines: [third] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  const table = await screen.findByRole("region", { name: "Жду счет" });
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows).toHaveLength(2);
  expect(within(rows[0]).getByText("PO-001")).toBeInTheDocument();
  expect(within(rows[0]).getByText("ООО А")).toBeInTheDocument();
  expect(within(rows[0]).getByText("2")).toBeInTheDocument();
  expect(within(rows[0]).getByText("0 из 2")).toBeInTheDocument();
  expect(within(rows[1]).getByText("PO-002")).toBeInTheDocument();
  expect(within(rows[1]).getByText("ООО Б")).toBeInTheDocument();
  expect(within(rows[1]).getByText("1 из 1")).toBeInTheDocument();
  expect(within(table).getAllByRole("button", { name: "Внести КП" })).toHaveLength(2);

  fireEvent.click(within(rows[1]).getByRole("button", { name: "Внести КП" }));
  const form = await screen.findByRole("form", { name: "КП по заказу PO-002" });
  expect(await within(form).findByLabelText("REQ-3 Кол-во (факт)")).toBeInTheDocument();
  expect(within(form).queryByLabelText("REQ-1 Кол-во (факт)")).not.toBeInTheDocument();
  expect(requested).toContain("/api/procurement/kp?reference=PO-002");
  expect(requested).not.toContain("/api/procurement/kp?reference=PO-001");
  // Кнопка не раскрыла строки заказа: они открываются по строке таблицы.
  expect(within(table).queryByText("Кабель")).not.toBeInTheDocument();
  fireEvent.click(within(rows[0]).getByText("PO-001"));
  expect(within(table).getByText("Кабель")).toBeInTheDocument();
  expect(within(table).getByRole("button", { name: "Действия REQ-2" })).toBeInTheDocument();
});

it("lists «В оплату» to tick as paid and says what a line lacks before it can be paid", async () => {
  const bodies: Record<string, unknown>[] = [];
  const ready = { ...line, id: "30000000-0000-0000-0000-000000000005", legacy_id: "REQ-5", status: "В оплату", buyer: "Закупщик", invoice_url: "https://files.test/i.pdf", pay_missing: [], version: 7, actual_sum: "244" };
  const noInvoice = { ...line, id: "30000000-0000-0000-0000-000000000006", legacy_id: "REQ-6", status: "В оплату", buyer: "Закупщик", invoice_url: null, pay_missing: ["СЧЕТ"], version: 2 };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.body) bodies.push({ path, ...JSON.parse(String(init.body)) });
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine") return json({ lines: [ready, noInvoice] });
    if (path === "/api/procurement/paid") return json({ lines: [ready], done: 1, already: 0, no_invoice: ["REQ-6"], failed: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  const table = await screen.findByRole("region", { name: "В оплату" });
  expect(within(within(table).getByText("REQ-5").closest("tr")!).getByText("можно оплачивать")).toBeInTheDocument();
  expect(within(within(table).getByText("REQ-6").closest("tr")!).getByText("нет счёта")).toBeInTheDocument();
  expect(within(table).getByRole("link", { name: "счёт" })).toHaveAttribute("href", "https://files.test/i.pdf");

  fireEvent.click(screen.getByLabelText(`Выбрать ${ready.id}`));
  fireEvent.click(screen.getByLabelText(`Выбрать ${noInvoice.id}`));
  fireEvent.click(screen.getByRole("button", { name: "Оплачено → В пути" }));
  await waitFor(() => expect(bodies).toContainEqual({
    path: "/api/procurement/paid",
    items: [{ line_id: ready.id, version: 7 }, { line_id: noInvoice.id, version: 2 }],
  }));
  expect(await screen.findByText("Оплачено → В пути: 1; без счёта: REQ-6")).toBeInTheDocument();
});

it("pays one «В оплату» line from its menu and asks for the rate when the CBR is silent", async () => {
  const ready = { ...line, status: "В оплату", buyer: "Закупщик", invoice_url: "https://files.test/i.pdf", pay_missing: [], expected_arrival: "2026-10-05" };
  const bodies: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine") return json({ lines: [ready] });
    if (path.endsWith("/paid") && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      bodies.push(body);
      if (!body.manual_exchange_rate) {
        return problem(409, { code: "manual_rate_required", currency: "USD", date: "2026-10-04", message: "ЦБ не отдал курс USD на 04.10.2026 — введите курс вручную" });
      }
      return json({ line: { ...ready, status: "В пути", version: 4 } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  await pickRowAction("REQ-1", "Оплатить одну строку…");
  fireEvent.click(await screen.findByRole("button", { name: "Оплачено → в пути" }));
  fireEvent.change(await screen.findByLabelText(/Курс, ₽ за 1 USD/), { target: { value: "95,5" } });
  fireEvent.click(screen.getByRole("button", { name: "Оплачено → в пути" }));
  await waitFor(() => expect(bodies).toEqual([{ version: 3 }, { version: 3, manual_exchange_rate: "95,5" }]));
});

it("«В пути»: offers to reconcile a hand-typed rate only for lines that have one", async () => {
  const manual = {
    ...line, status: "В пути", buyer: "Закупщик", invoice_url: "https://files.test/i.pdf", paid_at: "2026-10-01T12:00:00+03:00", pay_missing: [],
    source_currency: "USD", source_sum: "100", exchange_rate: "95.5", exchange_rate_date: "2026-10-01", exchange_rate_manual: true, actual_sum: "9550",
  };
  const plain = { ...line, id: "30000000-0000-0000-0000-000000000002", legacy_id: "REQ-2", status: "В пути", buyer: "Закупщик", invoice_url: "https://files.test/j.pdf", pay_missing: [] };
  const posted: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine") return json({ lines: [manual, plain] });
    if (path.endsWith("/exchange-rate/reconcile") && init?.method === "POST") {
      posted.push(`${path} ${init.body}`);
      return json({ line: { ...manual, exchange_rate_manual: false, version: 4 } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  const table = await screen.findByRole("region", { name: "В пути" });
  expect(within(table).getByText(/вручную, сверьте/)).toBeInTheDocument();
  expect(within(table).getByText("100 USD")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Действия REQ-2" }));
  expect(screen.queryByRole("menuitem", { name: "Сверить курс с ЦБ" })).not.toBeInTheDocument();
  fireEvent.mouseDown(document.body);
  await pickRowAction("REQ-1", "Сверить курс с ЦБ");
  await waitFor(() => expect(posted).toEqual([`/api/procurement/${manual.id}/exchange-rate/reconcile {"version":3}`]));
});

it("ID-6: view mode lists free and in-work lines without any buyer control", async () => {
  const requested: string[] = [];
  const free = { ...line, id: "30000000-0000-0000-0000-000000000009", legacy_id: "REQ-9", status: "Новый", buyer: null };
  const taken = { ...line, buyer: "Петров", supplier: "ООО Поставка", order_number: "PO-001", status: "В пути", expected_arrival: "2026-10-05" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    requested.push(`${init?.method ?? "GET"} ${path}`);
    if (path === "/api/procurement/available") return json({ lines: [free], next_cursor: null });
    if (path === "/api/procurement/in-work") return json({ lines: [taken], next_cursor: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage readOnly />);

  expect(await screen.findByRole("heading", { name: "В работе у закупщиков" })).toBeInTheDocument();
  expect(await screen.findByText("Петров")).toBeInTheDocument();
  expect(screen.getByText("REQ-9")).toBeInTheDocument();
  expect(screen.getByText("Лист закупки открыт вам только на просмотр: что свободно и что едет.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Взять" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Сформировать запросы поставщикам" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Мои позиции" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Сохранить" })).not.toBeInTheDocument();
  expect(requested.every((call) => call.startsWith("GET "))).toBe(true);
  expect(requested).not.toContain("GET /api/procurement/mine");
});

it("takes several free lines in one request and marks invoiced lines paid in one batch", async () => {
  const bodies: Record<string, unknown>[] = [];
  const first = { ...line, status: "Новый" };
  const second = { ...line, id: "30000000-0000-0000-0000-000000000002", legacy_id: "REQ-2", status: "Новый" };
  const invoiced = { ...line, id: "30000000-0000-0000-0000-000000000005", legacy_id: "REQ-5", status: "В оплату", buyer: "Закупщик", invoice_url: "https://files.test/i.pdf", pay_missing: [], version: 7 };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.body) bodies.push({ path, ...JSON.parse(String(init.body)) });
    if (path === "/api/procurement/available") return json({ lines: [first, second], next_cursor: null });
    if (path === "/api/procurement/mine") return json({ lines: [invoiced], next_cursor: null });
    if (path === "/api/procurement/meta") return json({ units: ["шт"], statuses: [] });
    if (path === "/api/procurement/take") return json({ lines: [first, second] });
    if (path === "/api/procurement/paid") return json({ lines: [invoiced], done: 1, already: 0, no_invoice: [], failed: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  fireEvent.click(await screen.findByLabelText(`Выбрать ${first.id}`));
  fireEvent.click(screen.getByLabelText(`Выбрать ${second.id}`));
  fireEvent.click(screen.getByRole("button", { name: "Взять в работу" }));
  await waitFor(() => expect(bodies).toContainEqual({ path: "/api/procurement/take", line_ids: [first.id, second.id] }));
  expect(await screen.findByText("Взято в работу: 2")).toBeInTheDocument();

  fireEvent.click(await screen.findByLabelText(`Выбрать ${invoiced.id}`));
  fireEvent.click(screen.getByRole("button", { name: "Оплачено → В пути" }));
  await waitFor(() => expect(bodies).toContainEqual({ path: "/api/procurement/paid", items: [{ line_id: invoiced.id, version: 7 }] }));
  expect(await screen.findByText("Оплачено → В пути: 1")).toBeInTheDocument();
});

it("offers the actual unit as a list", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine") return json({ lines: [{ ...line, buyer: "Закупщик" }], next_cursor: null });
    if (path === "/api/procurement/meta") return json({ units: ["шт", "кг", "компл"], statuses: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  const unit = (await screen.findByLabelText("Ед. REQ-1")) as HTMLSelectElement;
  expect(unit.tagName).toBe("SELECT");
  await waitFor(() => expect(Array.from(unit.options).map((option) => option.value)).toEqual(["", "шт", "кг", "компл"]));
});

it("saves traceability of an own line: maker, country, standard; the retired quality-document fields stay untouched", async () => {
  const own: ProcurementLine = {
    ...line, status: "Готов к запросу", buyer: "Закупщик", supplier: "Поставщик",
    quality_document_type: "Паспорт", quality_document_number: "П-17", quality_document_date: "2026-09-01",
  };
  let body: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine") return json({ lines: [own] });
    if (path.endsWith("/traceability") && init?.method === "PATCH") {
      body = JSON.parse(String(init.body));
      return json({ line: { ...own, version: 4, manufacturer: "Molex", country: "США" } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  await pickRowAction("REQ-1", "Прослеживаемость…");
  fireEvent.change(await screen.findByLabelText("Изготовитель"), { target: { value: "Molex" } });
  fireEvent.change(screen.getByLabelText("Стандарт"), { target: { value: "ГОСТ 23587-96" } });
  expect(screen.queryByLabelText("Тип док.")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("№ док. качества")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Дата док. качества")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Сохранить прослеживаемость" }));

  await waitFor(() => expect(body).toEqual({
    version: 3, manufacturer: "Molex", country: "", quality_document_type: "Паспорт", quality_document_number: "П-17",
    quality_document_date: "2026-09-01", standard: "ГОСТ 23587-96",
  }));
});

const UPLOADED = {
  url: "/api/procurement/files/40000000-0000-0000-0000-000000000001",
  file_name: "Счёт № 58 от 14.09.2026 Поставщик D-1 PO-001.pdf",
  invoice_number: "58",
  invoice_date: "2026-09-14",
  hint: "",
};

it("uploads the invoice file, prefills its recognised № and date and sends the line to payment", async () => {
  const ready = { ...awaitingInvoice, order_number: "PO-001", pay_missing: ["ОЖИД. ДАТА"], invoice_number: "17" };
  let uploadBody: Record<string, unknown> | undefined;
  let invoiceBody: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine") return json({ lines: [ready], next_cursor: null });
    if (path === "/api/procurement/invoice-files" && init?.method === "POST") {
      uploadBody = JSON.parse(String(init.body));
      return json(UPLOADED);
    }
    if (path.endsWith("/invoice") && init?.method === "POST") {
      invoiceBody = JSON.parse(String(init.body));
      return json({ line: { ...ready, status: "В оплату", version: 4 } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  await openOrder("PO-001");
  await pickRowAction("REQ-1", "Суммы и счёт строки…");
  const file = new File(["%PDF-1.4"], "счёт.pdf", { type: "application/pdf" });
  fireEvent.change(await screen.findByLabelText("Файл счёта"), { target: { files: [file] } });

  expect(await screen.findByText(/распознано: № 58, дата 2026-09-14 — проверьте/)).toBeInTheDocument();
  expect(uploadBody).toMatchObject({ line_id: ready.id, file_name: "счёт.pdf", content_type: "application/pdf" });
  expect(screen.getByLabelText("Ссылка на счёт")).toHaveValue(UPLOADED.url);
  expect(screen.getByLabelText("№ счёта по файлу")).toHaveValue("58");
  expect(screen.getByLabelText("Дата счёта по файлу")).toHaveValue("2026-09-14");

  fireEvent.change(screen.getByLabelText("Ожидаемая дата"), { target: { value: "2026-10-01" } });
  fireEvent.click(screen.getByRole("button", { name: "В оплату" }));

  await waitFor(() => expect(invoiceBody).toEqual({
    version: 3, invoice_url: UPLOADED.url, expected_arrival: "2026-10-01", invoice_number: "58", invoice_date: "2026-09-14",
  }));
});

it("says to type № and date by hand when the invoice file has no text", async () => {
  const shipped = { ...line, status: "В пути" as const, buyer: "Закупщик", invoice_url: "https://files.test/invoice.pdf", paid_at: "2026-09-22T12:00:00+03:00", pay_missing: [] };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine") return json({ lines: [shipped], next_cursor: null });
    if (path === "/api/procurement/invoice-files") {
      return json({ ...UPLOADED, invoice_number: "", invoice_date: "", file_name: "Счёт.jpg", hint: "Текст из файла не читается (скан или картинка) — № и дату счёта впишите вручную." });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  await pickRowAction("REQ-1", "Заменить счёт…");
  fireEvent.change(await screen.findByLabelText("Файл счёта"), { target: { files: [new File(["jpg"], "scan.jpg", { type: "image/jpeg" })] } });

  expect(await screen.findByText(/Счёт\.jpg · Текст из файла не читается/)).toBeInTheDocument();
  expect(screen.getByLabelText("Ссылка на новый счёт")).toHaveValue(UPLOADED.url);
  expect(screen.getByLabelText("№ счёта")).toHaveValue("");
});

it("uploads one invoice file for the whole order in the КП form and fills № and date of every row", async () => {
  const awaiting = { ...line, status: "Жду счет" as const, buyer: "Закупщик", order_number: "PO-001", actual_quantity: "2", actual_unit: "шт" };
  const other = { ...awaiting, id: "30000000-0000-0000-0000-000000000002", legacy_id: "REQ-2", version: 5 };
  let uploadBody: Record<string, unknown> | undefined;
  let saved: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine") return json({ lines: saved ? [] : [awaiting, other] });
    if (path === "/api/procurement/kp?reference=PO-001") return json({ order_reference: "PO-001", lines: [awaiting, other] });
    if (path === "/api/procurement/invoice-files") {
      uploadBody = JSON.parse(String(init?.body));
      return json(UPLOADED);
    }
    if (path === "/api/procurement/kp" && init?.method === "POST") {
      saved = JSON.parse(String(init.body));
      return json({ lines: [awaiting, other], invoice_error: "" });
    }
    throw new Error(`Unexpected ${path}`);
  });

  render(<ProcurementPage />);
  fireEvent.click((await screen.findAllByRole("button", { name: "Внести КП" }))[0]);
  const form = await screen.findByRole("form", { name: "КП по заказу PO-001" });
  await within(form).findByLabelText("REQ-2 Кол-во (факт)");
  fireEvent.change(within(form).getByLabelText("Файл счёта"), { target: { files: [new File(["%PDF"], "inv.pdf", { type: "application/pdf" })] } });

  await waitFor(() => expect(within(form).getByLabelText("REQ-2 № счёта")).toHaveValue("58"));
  expect(uploadBody).toMatchObject({ line_id: awaiting.id, file_name: "inv.pdf" });
  expect(within(form).getByLabelText("REQ-1 Дата счёта")).toHaveValue("2026-09-14");
  expect(within(form).getByLabelText("Ссылка на счёт (один на заказ, необязательно)")).toHaveValue(UPLOADED.url);

  fireEvent.click(within(form).getByRole("button", { name: "Сохранить КП" }));
  await waitFor(() => expect(saved).toBeDefined());
  expect(saved).toMatchObject({ order_reference: "PO-001", invoice_url: UPLOADED.url });
  const rows = saved?.rows as Record<string, unknown>[];
  expect(rows.map((row) => [row.invoice_number, row.invoice_date])).toEqual([["58", "2026-09-14"], ["58", "2026-09-14"]]);
});

// ── Комментарий и Ожид. дата — прямо в ячейке стадии ─────────────────────────────────────────────────

const staged = (n: number, status: string, extra: Partial<ProcurementLine> = {}): ProcurementLine => ({
  ...line, buyer: "Закупщик", supplier: "Поставщик", actual_quantity: "2", actual_unit: "шт",
  id: `30000000-0000-0000-0000-0000000002${n}0`, legacy_id: `REQ-${n}`, status, version: 3, ...extra,
});

it("edits «Комментарий» on every stage after «В работе» and «Ожид. дата» from «Жду счет» to «Карантин»", async () => {
  const lines = [
    staged(1, "В работе"),
    staged(2, "Готов к запросу", { note: "Ждём ответ поставщика" }),
    staged(3, "Жду счет", { order_number: "PO-009", expected_arrival: "2026-10-10" }),
    staged(4, "В оплату", { invoice_url: "https://files.test/i.pdf", pay_missing: [], expected_arrival: "2026-10-11" }),
    staged(5, "Согласование"),
    staged(6, "В пути", { expected_arrival: "2026-10-12", actual_arrival: "2026-10-03", order_number: "PO-001" }),
    staged(7, "Карантин", { expected_arrival: "2026-10-13", actual_arrival: "2026-10-04", order_number: "PO-001" }),
    staged(8, "Готово", { actual_arrival: "2026-10-02" }),
  ];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine") return json({ lines });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  await screen.findByRole("region", { name: "В пути" });
  fireEvent.click(await screen.findByText("PO-009")); // раскрыть заказ «Жду счет»

  const noteOn = (id: string) => screen.queryByRole("button", { name: `Комментарий ${id}` });
  const dateOn = (id: string) => screen.queryByRole("button", { name: `Ожид. дата ${id}` });
  // Комментарий — на каждой стадии после «В работе» (в самой «В работе» таблица своя, без него).
  for (const id of ["REQ-2", "REQ-3", "REQ-4", "REQ-5", "REQ-6", "REQ-7", "REQ-8"]) expect(noteOn(id), id).toBeInTheDocument();
  expect(noteOn("REQ-1")).not.toBeInTheDocument();
  // Ожид. дата — от «Жду счет» до «Карантин»; в «Готов к запросу» и «Согласование» её нет, в «Готово» только показ.
  for (const id of ["REQ-3", "REQ-4", "REQ-6", "REQ-7"]) expect(dateOn(id), id).toBeInTheDocument();
  for (const id of ["REQ-2", "REQ-5", "REQ-8"]) expect(dateOn(id), id).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Ожид. дата REQ-6" })).toHaveTextContent("12.10.2026");
  // «Факт. дата» — день приёмки, только показ.
  const transit = within(screen.getByRole("region", { name: "В пути" }));
  expect(transit.getByText("03.10.2026")).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Карантин" })).getByText("04.10.2026")).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Прочие позиции" })).getByText("02.10.2026")).toBeInTheDocument();
});

it("saves a typed «Комментарий» and «Ожид. дата» of a stage line with its version and shows the saved line", async () => {
  const transit = staged(6, "В пути", { expected_arrival: "2026-10-12", note: null });
  const sent: Array<{ path: string; body: Record<string, unknown> }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine") return json({ lines: [transit] });
    if (init?.method === "PATCH") {
      const body = JSON.parse(String(init.body));
      sent.push({ path, body });
      return json({ line: path.endsWith("/note") ? { ...transit, note: body.note, version: 4 } : { ...transit, expected_arrival: body.expected_arrival, version: 5 } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Комментарий REQ-6" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Комментарий REQ-6" }), { target: { value: "Груз в Казани" } });
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Комментарий REQ-6" }), { key: "Enter" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Комментарий REQ-6" })).toHaveTextContent("Груз в Казани"));

  fireEvent.click(screen.getByRole("button", { name: "Ожид. дата REQ-6" }));
  fireEvent.change(screen.getByLabelText("Ожид. дата REQ-6"), { target: { value: "2026-10-18" } });
  fireEvent.blur(screen.getByLabelText("Ожид. дата REQ-6"));
  await waitFor(() => expect(screen.getByRole("button", { name: "Ожид. дата REQ-6" })).toHaveTextContent("18.10.2026"));
  expect(sent).toEqual([
    { path: `/api/procurement/${transit.id}/note`, body: { version: 3, note: "Груз в Казани" } },
    { path: `/api/procurement/${transit.id}/expected-arrival`, body: { version: 4, expected_arrival: "2026-10-18" } },
  ]);
});

it("edits the date and the note of a line inside an opened «Жду счет» order", async () => {
  const waiting = staged(3, "Жду счет", { order_number: "PO-001", expected_arrival: "2026-10-10" });
  const sent: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine") return json({ lines: [waiting] });
    if (init?.method === "PATCH") {
      const body = JSON.parse(String(init.body));
      sent.push(`${path} ${init.body}`);
      return json({ line: { ...waiting, ...("note" in body ? { note: body.note } : { expected_arrival: body.expected_arrival }), version: 4 } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  await openOrder("PO-001");
  fireEvent.click(screen.getByRole("button", { name: "Ожид. дата REQ-3" }));
  fireEvent.change(screen.getByLabelText("Ожид. дата REQ-3"), { target: { value: "2026-10-15" } });
  fireEvent.keyDown(screen.getByLabelText("Ожид. дата REQ-3"), { key: "Enter" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Ожид. дата REQ-3" })).toHaveTextContent("15.10.2026"));
  // Дата строки сдвинула и «Ожидается» самого заказа — группа считается по строкам.
  expect(within(screen.getByRole("region", { name: "Жду счет" })).getAllByText("15.10.2026").length).toBeGreaterThanOrEqual(2);

  fireEvent.click(screen.getByRole("button", { name: "Комментарий REQ-3" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Комментарий REQ-3" }), { target: { value: "Счёт обещали во вторник" } });
  fireEvent.blur(screen.getByRole("textbox", { name: "Комментарий REQ-3" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Комментарий REQ-3" })).toHaveTextContent("Счёт обещали во вторник"));
  expect(sent).toEqual([
    `/api/procurement/${waiting.id}/expected-arrival {"version":3,"expected_arrival":"2026-10-15"}`,
    `/api/procurement/${waiting.id}/note {"version":4,"note":"Счёт обещали во вторник"}`,
  ]);
});

it("on 409 in a stage table says the line changed and rereads the queue; on 422 shows the server text", async () => {
  const toPay = staged(4, "В оплату", { invoice_url: "https://files.test/i.pdf", pay_missing: [], expected_arrival: "2026-10-11", note: "старый" });
  const fresh = { ...toPay, note: "поправили в другой вкладке", version: 8 };
  let reloaded = false;
  const patches: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine") {
      const result = json({ lines: [reloaded ? fresh : toPay] });
      reloaded = true;
      return result;
    }
    if (init?.method === "PATCH") {
      patches.push(`${path.split("/").pop()} ${init.body}`);
      return path.endsWith("/note")
        ? problem(409, "Конкурентное изменение: обновите данные и повторите команду")
        : problem(422, "Ожидаемая дата поступления не может быть раньше заказа");
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Комментарий REQ-4" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Комментарий REQ-4" }), { target: { value: "мой текст" } });
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Комментарий REQ-4" }), { key: "Enter" });
  expect(await screen.findByText("Строку изменили — обновите")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByRole("button", { name: "Комментарий REQ-4" })).toHaveTextContent("поправили в другой вкладке"));

  fireEvent.click(screen.getByRole("button", { name: "Ожид. дата REQ-4" }));
  fireEvent.change(screen.getByLabelText("Ожид. дата REQ-4"), { target: { value: "2020-01-01" } });
  fireEvent.keyDown(screen.getByLabelText("Ожид. дата REQ-4"), { key: "Enter" });
  expect(await screen.findByText("Ожидаемая дата поступления не может быть раньше заказа")).toBeInTheDocument();
  expect(patches).toEqual(['note {"version":3,"note":"мой текст"}', 'expected-arrival {"version":8,"expected_arrival":"2020-01-01"}']);
});

it("opens the buyer's own line of the whole sheet for editing by the signed-in name", async () => {
  const mine = { ...line, id: "30000000-0000-0000-0000-000000000077", legacy_id: "REQ-77", status: "В пути", buyer: "Артемий", expected_arrival: "2026-10-12" };
  const other = { ...mine, id: "30000000-0000-0000-0000-000000000078", legacy_id: "REQ-78", buyer: "Петров" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine") return json({ lines: [] });
    if (path === "/api/procurement/meta") return json({ units: [], statuses: [] });
    if (path === "/api/procurement/lines") return json({ lines: [mine, other], next_cursor: null, buyers: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage buyerName="Артемий" />);
  fireEvent.click(screen.getByRole("button", { name: "Весь лист" }));
  expect(await screen.findByRole("button", { name: "Ожид. дата REQ-77" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Ожид. дата REQ-78" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Комментарий REQ-77" })).toBeInTheDocument();
});
