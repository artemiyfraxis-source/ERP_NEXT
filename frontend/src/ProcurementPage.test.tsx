import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ProcurementPage, migrateHiddenBlocks } from "./ProcurementPage";
import type { OrderDocumentRecognition, ProcurementLine, RecognizedOrderLine } from "./api";

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

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

it("takes a line and saves facts with its optimistic version", async () => {
  let taken = false;
  let savedBody: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") {
      return json({ lines: taken ? [] : [line] });
    }
    if (path === "/api/procurement/mine?limit=500") {
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
    if (path === "/api/procurement/mine?limit=500") {
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

/** Действие строки стадии (кнопка на самой строке, без меню; ждёт конца перезагрузки очереди). */
async function pickRowAction(id: string, item: string | RegExp) {
  const group = await screen.findByRole("group", { name: `Действия ${id}` });
  await waitFor(() => expect(within(group).getByRole("button", { name: item })).toBeEnabled());
  fireEvent.click(within(group).getByRole("button", { name: item }));
}

/** Названия действий строки, видимых на ней. */
const rowActionNames = (id: string) => within(screen.getByRole("group", { name: `Действия ${id}` })).getAllByRole("button").map((item) => item.getAttribute("aria-label") ?? item.textContent ?? "");

async function clickWhenEnabled(name: string | RegExp) {
  await waitFor(() => expect(screen.getByRole("button", { name })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name }));
}

/** Заказ в ленте свёрнут: щелчок по его заголовку («PO-001 от …», «Без № заказа …») раскрывает строки. */
async function openOrder(number: string, scope?: HTMLElement) {
  const root = scope ? within(scope) : screen;
  const toggle = await root.findByRole("button", { name: new RegExp(`^${number}`) });
  if (toggle.getAttribute("aria-expanded") !== "true") fireEvent.click(toggle);
  await waitFor(() => expect(toggle).toHaveAttribute("aria-expanded", "true"));
}

/** Раскрывает блок заказов (по умолчанию «В пути»; «Принято» свёрнут): щелчок по заголовку показывает его заказы. */
async function openPaidBlock(title: RegExp = /^В пути/) {
  const toggle = within(await screen.findByRole("heading", { name: title })).getByRole("button");
  if (toggle.getAttribute("aria-expanded") !== "true") fireEvent.click(toggle);
  await waitFor(() => expect(toggle).toHaveAttribute("aria-expanded", "true"));
}

/** Кнопка в заголовке заказа (без меню «⋯»). */
async function pickOrderAction(number: string, item: string | RegExp) {
  const order = await screen.findByRole("rowgroup", { name: new RegExp(`^${number}`) });
  await waitFor(() => expect(within(order).getByRole("button", { name: item })).toBeEnabled());
  fireEvent.click(within(order).getByRole("button", { name: item }));
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
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [current], next_cursor: null });
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
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [awaitingInvoice], next_cursor: null });
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
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [backorder], next_cursor: null });
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

it("does not offer generic approval or cancel on a backorder", async () => {
  const backorder: ProcurementLine = { ...line, status: "Согласование", is_backorder: true, legacy_id: "REQ-1-Д" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [backorder], next_cursor: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  expect(await screen.findByRole("button", { name: "Досыл подтверждён → в пути" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Одобрено — продолжить" })).not.toBeInTheDocument();
  const actions = rowActionNames("REQ-1-Д");
  expect(actions).toContain("Отменить дозавоз");
  expect(actions).not.toContain("Отменить строку");
  expect(actions).not.toContain("На согласование");
});

it("forms supplier requests: downloads each order's PDF, lists the skipped rows with the reason and groups the order", async () => {
  const ready: ProcurementLine = { ...line, status: "Готов к запросу", buyer: "Закупщик", supplier: "ООО А" };
  const noSupplier: ProcurementLine = { ...line, id: "30000000-0000-0000-0000-000000000002", legacy_id: "REQ-2", buyer: "Закупщик", status: "Готов к запросу", supplier: null };
  let formed = false;
  let requestBody: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine?limit=500") {
      return json({
        lines: [formed ? {
          ...ready, status: "Жду счет", order_number: "PO-012", order_date: "2026-10-05", actual_sum: "122", calculated_sum: "100",
          order_url: "/api/procurement/supplier-requests/PO-012/document",
        } : ready, noSupplier],
        next_cursor: null,
      });
    }
    if (path === "/api/procurement/supplier-requests" && init?.method === "POST") {
      formed = true;
      requestBody = JSON.parse(String(init.body));
      return json({
        orders: [{
          order_number: "PO-012", order_date: "2026-10-05", supplier: "ООО А", task_type: "Закупка",
          delivery_point: "Москва", buyer: "Закупщик", letter: "ООО А · REQ# 05.10.2026 - MR#D-1 PO-012",
          line_ids: [ready.id], document: null,
        }],
        lines: [],
        skipped: [{ line_id: noSupplier.id, reason: "поставщик не указан" }],
        other_buyer: 2,
      });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  const downloads: { name: string; href: string }[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function click(this: HTMLAnchorElement) { downloads.push({ name: this.download, href: this.getAttribute("href") ?? "" }); });

  render(<ProcurementPage />);
  // «Готов к запросу» — в блоке «В работе»: «Сформировать запрос» по поставщику (отметка строк, если блок её просит).
  const block = await screen.findByRole("region", { name: "В работе" });
  await waitFor(() => expect(within(block).getByLabelText("Выбрать REQ-1")).toBeEnabled());
  fireEvent.click(within(block).getByLabelText("Выбрать все"));
  fireEvent.click(screen.getByRole("button", { name: "Сформировать запрос" }));

  expect(await screen.findByText("Сформировано запросов: 1; PDF запросов скачаны файлами; строк других закупщиков не тронуто: 2")).toBeInTheDocument();
  expect(requestBody).toEqual({ line_ids: expect.arrayContaining([ready.id]) });
  expect(downloads).toEqual([{ name: "Запрос PO-012.pdf", href: "/api/procurement/supplier-requests/PO-012/document?download=1" }]);

  const warning = await screen.findByText(/В запросы не попало строк: 1/);
  expect(warning.closest(".warning-inline")).toHaveTextContent("REQ-2: поставщик не указан");

  // Сформированный заказ ушёл из «В работе» в свой блок «Жду счёт»: № и дата, поставщик, итоги, статус; свёрнут.
  const awaiting = await screen.findByRole("region", { name: "Жду счёт" });
  const group = await within(awaiting).findByRole("rowgroup", { name: "PO-012 от 05.10 · ООО А" });
  expect(within(block).queryByRole("rowgroup", { name: "PO-012 от 05.10 · ООО А" })).not.toBeInTheDocument();
  expect(within(group).getByText("1 поз. · факт 122,00 ₽ / расчёт 100,00 ₽")).toBeInTheDocument();
  expect(within(group).getByText("Жду счёт")).toHaveClass("order-chip");
  expect(within(group).queryByText("REQ-1")).not.toBeInTheDocument();
  expect(within(awaiting).getByRole("heading", { name: /^Жду счёт/ })).toBeInTheDocument();
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
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [shipped], next_cursor: null });
    if (path.endsWith("/invoice/replace") && init?.method === "POST") {
      calls.push([path, JSON.parse(String(init.body))]);
      return json({ line: { ...shipped, invoice_url: "https://files.test/invoice-v2.pdf", version: 4 }, warnings: ["REQ-1 — нет цены: ВАЛЮТА"] });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  await openPaidBlock();
  await openOrder("Без № заказа");
  await pickRowAction("REQ-1", "Заменить счёт…");
  fireEvent.change(await screen.findByLabelText("Ссылка на новый счёт"), { target: { value: "https://files.test/invoice-v2.pdf" } });
  fireEvent.change(screen.getByLabelText("№ счёта"), { target: { value: " 18 " } });
  fireEvent.click(screen.getByRole("button", { name: "Заменить" }));

  await waitFor(() => expect(calls).toHaveLength(1));
  expect(calls[0]).toEqual([
    `/api/procurement/${shipped.id}/invoice/replace`,
    { version: 3, invoice_url: "https://files.test/invoice-v2.pdf", invoice_number: "18", invoice_date: null },
  ]);
  expect((await screen.findByText("REQ-1 — нет цены: ВАЛЮТА")).closest(".warning-banner")).toBeInTheDocument();
});

it("splits an own line into supplier copies with its optimistic version", async () => {
  let splitBody: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine?limit=500") {
      return json({ lines: [{ ...line, status: "В пути", buyer: "Закупщик" }], next_cursor: null });
    }
    if (path.endsWith("/split") && init?.method === "POST") {
      splitBody = JSON.parse(String(init.body));
      return json({ line: { ...line, version: 4 }, copies: [] });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  await openPaidBlock();
  await openOrder("Без № заказа");
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
    if (path === "/api/procurement/mine?limit=500") return json({ lines: saved ? [] : [awaiting, other] });
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
      return json({ lines: [awaiting, other], invoice_error: "", warnings: ["REQ-2 — нет цены: ВАЛЮТА, КУРС"] });
    }
    throw new Error(`Unexpected ${path}`);
  });

  render(<ProcurementPage />);
  await pickOrderAction("PO-001", "Внести КП в валюте…");
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
  const warning = await screen.findByText("REQ-2 — нет цены: ВАЛЮТА, КУРС");
  expect(warning.closest(".warning-banner")).toBeInTheDocument();
});

it("«Жду счёт»: the order menu opens «Внести КП» for that order only and prints the request document", async () => {
  const order = (n: number, document: boolean): ProcurementLine => ({
    ...awaitingInvoice, id: `30000000-0000-0000-0000-00000000050${n}`, legacy_id: `REQ-5${n}`, order_number: `PO-00${n}`,
    order_url: document ? `/api/procurement/supplier-requests/PO-00${n}/document` : null,
  });
  const lines = [order(1, true), order(2, false), order(3, true)];
  const saved: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine?limit=500") return json({ lines });
    const reference = path.match(/kp\?reference=(PO-\d+)/)?.[1];
    if (reference) return json({ order_reference: reference, lines: lines.filter((item) => item.order_number === reference) });
    if (path === "/api/procurement/kp" && init?.method === "POST") {
      saved.push(JSON.parse(String(init.body)).order_reference);
      return json({ lines: [], invoice_error: "" });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  await screen.findByRole("region", { name: "Заказы: Жду счёт" });

  // Заказ без документа: «Документ запроса» закрыт; «Печать запроса» нет — печатает просмотрщик PDF.
  const noDocument = await screen.findByRole("rowgroup", { name: /^PO-002/ });
  expect(within(noDocument).queryByRole("link", { name: "Запрос" })).not.toBeInTheDocument();
  expect(within(noDocument).getByText("Запрос")).toHaveAttribute("title", "Документ запроса не сформирован");
  expect(within(noDocument).queryByRole("button", { name: "⋯" })).not.toBeInTheDocument();
  const withDocument = await screen.findByRole("rowgroup", { name: /^PO-001/ });
  expect(within(withDocument).getByRole("link", { name: "Запрос" })).toHaveAttribute("href", "/api/procurement/supplier-requests/PO-001/document");

  await pickOrderAction("PO-003", "Внести КП в валюте…");
  const form = await screen.findByRole("form", { name: "КП по заказу PO-003" });
  await within(form).findByLabelText("REQ-53 Кол-во (факт)");
  fireEvent.click(within(form).getByRole("button", { name: "Сохранить КП" }));
  await waitFor(() => expect(screen.queryByRole("form", { name: /КП по заказу/ })).not.toBeInTheDocument());
  expect(saved).toEqual(["PO-003"]);
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
    if (path === "/api/procurement/mine?limit=500") return json({ lines });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  await screen.findByText("REQ-B");
  expect(screen.queryByLabelText("Замена")).not.toBeInTheDocument();
  expect(screen.queryByText(/незакрытая замена/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Разрешение на замену/ })).not.toBeInTheDocument();

  // Меню строк: ни «Замены», ни «Разрешения на замену», ни документа качества.
  await openOrder("Без № заказа");
  const menus: Record<string, string[]> = {};
  for (const id of ["REQ-A", "REQ-B", "REQ-D"]) {
    menus[id] = rowActionNames(id);
  }
  // «Готов к запросу» стоит в блоке «В работе» с тем же меню; ручного «На согласование» нет нигде.
  expect(menus["REQ-B"]).toEqual(["Со склада", "Прослеживаемость, раздробить…", "Есть на складе, без ГРН", "Отменить строку"]);
  for (const items of Object.values(menus)) {
    expect(items.filter((item) => /замен(а|у)(?! счёт)|качества|разрешен|согласован/i.test(item))).toEqual([]);
  }
  await pickRowAction("REQ-A", /Прослеживаемость/);
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
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [], next_cursor: null });
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

it("shows «Мои позиции» as separate blocks: В работе → Жду счёт → Ждёт оплаты → В пути → Карантин → Принято → Дозавоз → Прочие позиции, newest orders first inside a block", async () => {
  const base: ProcurementLine = { ...line, buyer: "Закупщик", supplier: "Поставщик", actual_quantity: "2", actual_unit: "шт" };
  const at = (n: number, status: string, extra: Partial<ProcurementLine> = {}): ProcurementLine => ({
    ...base, id: `30000000-0000-0000-0000-0000000001${n}0`, legacy_id: `REQ-${n}`, status, ...extra,
  });
  const lines = [
    at(1, "В работе"),
    at(2, "Готов к запросу"),
    at(3, "Жду счет", { order_number: "PO-001", order_date: "2026-10-01", supplier: "ООО А" }),
    at(4, "В оплату", { order_number: "PO-002", order_date: "2026-10-03", supplier: "ООО Б", invoice_url: "https://files.test/i.pdf", pay_missing: [] }),
    at(5, "Согласование", { legacy_id: "REQ-5-Д", is_backorder: true, invoice_url: "https://files.test/i.pdf", invoice_number: "17" }),
    at(6, "В пути", { order_number: "PO-003", order_date: "2026-10-04", supplier: "ООО В", invoice_url: "https://files.test/i.pdf", expected_arrival: "2026-10-12" }),
    at(7, "Карантин", { order_number: "PO-003", order_date: "2026-10-04", supplier: "ООО В", invoice_url: "https://files.test/j.pdf" }),
    at(8, "Согласование"),
    at(9, "Готово", { order_number: "PO-004", order_date: "2026-10-02", supplier: "ООО Г", actual_price_with_vat: "61", actual_sum: "122" }),
    at(10, "В пути", { order_number: "PO-005", order_date: "2026-10-03", supplier: "ООО Д" }),
    at(11, "Готово", { order_number: "PO-005", order_date: "2026-10-03", supplier: "ООО Д" }),
  ];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine?limit=500") return json({ lines });
    throw new Error(`Unexpected request: ${path}`);
  });

  const { container } = render(<ProcurementPage />);
  const work = await screen.findByRole("region", { name: "В работе" });
  await screen.findByRole("region", { name: "Прочие позиции" });
  const blocks = ["В работе", "Жду счёт", "Ждёт оплаты", "В пути", "Карантин", "Принято", "Дозавоз", "Прочие позиции"];
  const headings = screen.getAllByRole("heading", { level: 2 })
    .map((heading) => blocks.find((name) => heading.textContent?.replace(/^[▸▾]/, "").startsWith(name))).filter(Boolean);
  // Блоки отдельные и идут в этом порядке; в «В работе» заказов нет — только строки и группы поставщиков.
  expect(headings).toEqual(blocks);
  expect(container.querySelectorAll(".procurement-card")).toHaveLength(0);
  expect(within(work).getByText("REQ-1")).toBeInTheDocument();
  expect(within(within(work).getByText("REQ-2").closest("tr")!).getByText("Готов к запросу")).toBeInTheDocument();
  expect(within(work).queryByRole("region", { name: /^Заказы/ })).not.toBeInTheDocument();
  const positions = blocks.map((name) => screen.getByRole("region", { name: name === "Принято" ? "Принято за 30 дней" : name }));
  positions.slice(1).forEach((block, index) => {
    expect(positions[index].compareDocumentPosition(block) & Node.DOCUMENT_POSITION_FOLLOWING, blocks[index + 1]).toBeTruthy();
  });

  // Каждый блок заказов — своя лента со своим статусом заказа.
  const labelsOf = (region: HTMLElement) => within(region).getAllByRole("rowgroup")
    .filter((group) => group.getAttribute("aria-label")).map((group) => group.getAttribute("aria-label"));
  const awaiting = screen.getByRole("region", { name: "Заказы: Жду счёт" });
  expect(labelsOf(awaiting)).toEqual(["PO-001 от 01.10 · ООО А"]);
  expect(within(awaiting).getByText("Жду счёт", { selector: ".order-chip" })).toBeInTheDocument();
  expect(within(awaiting).getByRole("button", { name: "Вставить счёт" })).toBeInTheDocument();
  expect(within(awaiting).getByRole("button", { name: "Вставить КП" })).toBeInTheDocument();
  const toPay = screen.getByRole("region", { name: "Заказы: Ждёт оплаты" });
  expect(labelsOf(toPay)).toEqual(["PO-002 от 03.10 · ООО Б"]);
  expect(within(toPay).getByText("Ждёт оплаты", { selector: ".order-chip" })).toBeInTheDocument();
  expect(within(toPay).getByRole("button", { name: "Оплачено" })).toBeInTheDocument();

  // «В пути»: заказ с открытыми строками «В пути»; частично принятый остаётся здесь с «принято N из M».
  await openPaidBlock();
  const transit = within(screen.getByRole("region", { name: "Заказы: В пути" }));
  expect(labelsOf(screen.getByRole("region", { name: "Заказы: В пути" }))).toEqual(["PO-005 от 03.10 · ООО Д"]);
  expect(transit.getByRole("rowgroup", { name: "PO-005 от 03.10 · ООО Д" }).querySelector(".order-chip")?.textContent).toBe("В пути · принято 1 из 2");

  // «Карантин»: заказ с любой строкой в карантине, хоть другие строки и в пути; в строке названо, какие именно.
  const quarantine = within(screen.getByRole("region", { name: "Заказы: Карантин" }));
  const quarantined = quarantine.getByRole("rowgroup", { name: "PO-003 от 04.10 · ООО В" });
  expect(quarantined.querySelector(".order-chip")?.textContent).toBe("Карантин: Разъём");
  expect(quarantined.querySelector(".order-chip")).toHaveClass("tone-warn");

  // «Принято за 30 дней» свёрнут по умолчанию: заказов не видно, пока не раскрыть заголовок.
  expect(screen.getByRole("heading", { name: /^Принято за 30 дней/ })).toHaveTextContent("Принято за 30 дней 1");
  expect(screen.queryByRole("region", { name: "Заказы: Принято за 30 дней" })).not.toBeInTheDocument();
  await openPaidBlock(/^Принято/);
  const accepted = within(screen.getByRole("region", { name: "Заказы: Принято за 30 дней" }));
  const groups = accepted.getAllByRole("rowgroup").filter((group) => group.getAttribute("aria-label"));
  expect(groups.map((group) => group.getAttribute("aria-label"))).toEqual(["PO-004 от 02.10 · ООО Г"]);
  expect(groups[0].querySelector(".order-chip")?.textContent).toBe("Принято");

  // Заказ свёрнут; щелчок по заголовку раскрывает строки, второй — сворачивает.
  fireEvent.click(within(quarantined).getByText("PO-003 от 04.10 · ООО В"));
  expect(within(quarantined).getByRole("button", { name: "PO-003 от 04.10 · ООО В" })).toHaveAttribute("aria-expanded", "true");
  expect(within(within(quarantined).getByText("REQ-6").closest("tr")!).getByText("В пути")).toBeInTheDocument();
  expect(within(within(quarantined).getByText("REQ-7").closest("tr")!).getByText("Карантин")).toHaveClass("tone-warn");
  expect(within(quarantined).getByText("12.10.2026")).toBeInTheDocument();
  expect(quarantine.getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
    "ID", "Наименование", "Кол-во факт", "Цена факт, ₽", "Сумма факт, ₽", "Расчёт, ₽", "Счёт", "Ожид. дата", "Факт. дата", "Статус", "Комментарий", "",
  ]);
  fireEvent.click(within(groups[0]).getByRole("button", { name: "PO-004 от 02.10 · ООО Г" }));
  // Цена в строке только показана — её вносит окно «Вставить счёт» / «Вставить КП».
  expect(within(within(groups[0]).getByText("REQ-9").closest("tr")!).getByText("61,00")).toBeInTheDocument();
  expect(screen.queryByLabelText(/Цена факт REQ/)).not.toBeInTheDocument();
  fireEvent.click(quarantined.querySelector("tr.order-head")!);
  expect(quarantine.queryByText("REQ-6")).not.toBeInTheDocument();
  // «Дозавоз» — только строки дозавоза, со своим решением; обычная строка «Согласование» — в «Прочих».
  const head = (name: string) => within(screen.getByRole("region", { name })).getAllByRole("columnheader").map((cell) => cell.textContent);
  expect(head("Дозавоз")).toEqual(["ID", "Наименование", "Поставщик", "Кол-во", "Основание", "Комментарий", "Решение", ""]);
  expect(within(screen.getByRole("region", { name: "Дозавоз" })).queryByText("REQ-8")).not.toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Прочие позиции" })).getByText("REQ-8")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Одобрено — продолжить" })).not.toBeInTheDocument();
});

it("keeps the blocks separate: «Принято» starts collapsed, a heading click collapses/expands its block and the choice is remembered", async () => {
  const awaiting = { ...awaitingInvoice, order_number: "PO-001", order_date: "2026-10-01", supplier: "ООО А" };
  const transit = { ...awaitingInvoice, id: "30000000-0000-0000-0000-000000000003", legacy_id: "REQ-3", status: "Готово", order_number: "PO-003", order_date: "2026-10-04", supplier: "ООО В", invoice_url: "https://files.test/i.pdf", pay_missing: [] };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [awaiting, transit], next_cursor: null });
    if (path === "/api/procurement/meta") return json({ units: ["шт"], statuses: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
  const toggleOf = (title: RegExp) => within(screen.getByRole("heading", { name: title })).getByRole("button");
  const stored = (block: string) => window.localStorage.getItem(`procurement-block-collapsed:${block}`);

  const first = render(<ProcurementPage buyerName="Артемий" />);
  await screen.findByRole("rowgroup", { name: "PO-001 от 01.10 · ООО А" });
  // Блоки раздельные; «Жду счёт» открыт, «Принято» свёрнут (заголовок и счётчик на месте, заказов нет).
  expect(toggleOf(/^Жду счёт/)).toHaveAttribute("aria-expanded", "true");
  expect(toggleOf(/^Принято/)).toHaveAttribute("aria-expanded", "false");
  expect(toggleOf(/^Принято/)).toHaveTextContent("Принято за 30 дней 1");
  expect(screen.queryByRole("region", { name: "Заказы: Принято за 30 дней" })).not.toBeInTheDocument();
  expect(screen.queryByRole("rowgroup", { name: "PO-003 от 04.10 · ООО В" })).not.toBeInTheDocument();
  expect(screen.queryByRole("group", { name: "Показ заказов" })).not.toBeInTheDocument();

  // Раскрыли «Принято», свернули «Жду счёт»: каждый блок сам по себе.
  fireEvent.click(toggleOf(/^Принято/));
  expect(await screen.findByRole("rowgroup", { name: "PO-003 от 04.10 · ООО В" })).toBeInTheDocument();
  fireEvent.click(toggleOf(/^Жду счёт/));
  expect(screen.queryByRole("region", { name: "Заказы: Жду счёт" })).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /^Жду счёт/ })).toBeInTheDocument();
  expect(stored("orders-accepted")).toBe("0");
  expect(stored("orders-awaiting")).toBe("1");
  expect(stored("inwork")).toBeNull();
  first.unmount();

  // Выбор помнится после перезагрузки: «Принято» открыт, «Жду счёт» свёрнут.
  const second = render(<ProcurementPage buyerName="Артемий" />);
  await screen.findByRole("rowgroup", { name: "PO-003 от 04.10 · ООО В" });
  expect(toggleOf(/^Принято/)).toHaveAttribute("aria-expanded", "true");
  expect(toggleOf(/^Жду счёт/)).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByRole("rowgroup", { name: "PO-001 от 01.10 · ООО А" })).not.toBeInTheDocument();
  // Раскрыли обратно — «Жду счёт» снова с заказом, ключ перезаписан.
  fireEvent.click(toggleOf(/^Жду счёт/));
  expect(await screen.findByRole("rowgroup", { name: "PO-001 от 01.10 · ООО А" })).toBeInTheDocument();
  expect(stored("orders-awaiting")).toBe("0");
  second.unmount();
});

const BLOCK_NAMES = ["В работе", "Жду счёт", "Ждёт оплаты", "В пути", "Карантин", "Принято", "Дозавоз", "Прочие позиции"];
// «Прочие позиции» прячутся, пока в них нет строк.
const FLOW_BLOCK_NAMES = BLOCK_NAMES.slice(0, -1);

function mockMine(lines: ProcurementLine[]) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine?limit=500") return json({ lines });
    throw new Error(`Unexpected request: ${path}`);
  });
}

/** Строка заказа: n — номер заказа и строки, status — этап. */
const orderLine = (n: number, status: string, extra: Partial<ProcurementLine> = {}): ProcurementLine => ({
  ...line, buyer: "Закупщик", supplier: "Поставщик", actual_quantity: "2", actual_unit: "шт", status,
  id: `30000000-0000-0000-0000-${String(n).padStart(12, "0")}`, legacy_id: `REQ-${n}`,
  order_number: `PO-${String(n).padStart(3, "0")}`, order_date: "2026-10-01", calculated_sum: "100", ...extra,
});

const headingNames = () => screen.getAllByRole("heading", { level: 2 })
  .map((heading) => BLOCK_NAMES.find((name) => heading.textContent?.replace(/^[▸▾]/, "").startsWith(name))).filter(Boolean);

it("keeps every «Мои позиции» block on screen even with no data: seven headings in order, each with 0 and one grey line; «Прочие позиции» only with lines", async () => {
  mockMine([]);

  const { container } = render(<ProcurementPage />);
  await screen.findByText(/Нет строк в работе/);
  expect(headingNames()).toEqual(FLOW_BLOCK_NAMES);
  for (const name of FLOW_BLOCK_NAMES) expect(screen.getByRole("heading", { name: new RegExp(`^${name}`) })).toHaveTextContent(`${name === "Принято" ? "Принято за 30 дней" : name} 0`);
  expect(screen.getByText(/Нет строк в работе/)).toHaveClass("stage-empty");
  expect(container.querySelectorAll(".stage-empty")).toHaveLength(FLOW_BLOCK_NAMES.length);
  expect(screen.queryByRole("region", { name: "Прочие позиции" })).not.toBeInTheDocument();
  expect(screen.queryByRole("region", { name: /^Заказы/ })).not.toBeInTheDocument();
  expect(container.querySelectorAll("table")).toHaveLength(0);
  // В «Блоках» «Прочие позиции» перечислены и тогда, когда строк нет.
  fireEvent.click(screen.getByRole("button", { name: "Блоки" }));
  expect(screen.getByRole("checkbox", { name: "Прочие позиции" })).toBeChecked();
});

it("shows «Прочие позиции» only with lines, and says in the heading hint what is inside", async () => {
  mockMine([{ ...line, buyer: "Закупщик", status: "Согласование" }]);

  render(<ProcurementPage />);
  await screen.findByRole("region", { name: "Прочие позиции" });
  expect(screen.getByRole("note", { name: "Строки вне потока: новые, на согласовании" })).toBeInTheDocument();
});

it("puts an order with a Карантин line into «Карантин», a fully accepted one into «Принято», a partly accepted one stays in «В пути» with «принято 1 из 2»", async () => {
  mockMine([
    orderLine(1, "Карантин"),
    orderLine(1, "В пути", { id: "30000000-0000-0000-0000-0000000000b1", legacy_id: "REQ-1b" }),
    orderLine(2, "Готово"),
    orderLine(2, "Готово", { id: "30000000-0000-0000-0000-0000000000b2", legacy_id: "REQ-2b" }),
    orderLine(3, "Готово"),
    orderLine(3, "В пути", { id: "30000000-0000-0000-0000-0000000000b3", legacy_id: "REQ-3b" }),
  ]);

  render(<ProcurementPage />);
  await screen.findByRole("heading", { name: /^Карантин/ });
  const heading = (name: string) => screen.getByRole("heading", { name: new RegExp(`^${name}`) });
  expect(heading("Карантин")).toHaveTextContent("Карантин 1");
  expect(heading("Принято")).toHaveTextContent("Принято за 30 дней 1");
  expect(heading("В пути")).toHaveTextContent("В пути 1");
  const group = (block: string, number: string) => within(screen.getByRole("region", { name: `Заказы: ${block}` })).getByRole("rowgroup", { name: new RegExp(`^${number}`) });
  expect(group("Карантин", "PO-001").querySelector(".order-chip")?.textContent).toBe("Карантин: Разъём");
  expect(group("В пути", "PO-003").querySelector(".order-chip")?.textContent).toBe("В пути · принято 1 из 2");
  await openPaidBlock(/^Принято/);
  expect(group("Принято за 30 дней", "PO-002").querySelector(".order-chip")?.textContent).toBe("Принято");
});

it("migrates the old hidden «Оплачено» block key into the three new blocks", async () => {
  window.localStorage.setItem("erp-table:procurement-blocks", JSON.stringify({ hidden: ["paid", "other"] }));
  expect(migrateHiddenBlocks(["paid", "other"]).sort()).toEqual(["accepted", "other", "quarantine", "transit"]);
  expect(migrateHiddenBlocks(["other"])).toEqual(["other"]);
  mockMine([orderLine(1, "В пути"), orderLine(2, "Готово")]);

  render(<ProcurementPage />);
  await screen.findByRole("heading", { name: /^Жду счёт/ });
  expect(headingNames()).toEqual(["В работе", "Жду счёт", "Ждёт оплаты", "Дозавоз"]);
  expect(screen.getByText("скрыто: В пути 1, Принято за 30 дней 1")).toBeInTheDocument();
});

it("«Блоки» hides blocks, the choice survives a remount, and a hidden block with data is named in the summary", async () => {
  mockMine([orderLine(1, "Жду счет"), orderLine(2, "Готово"), orderLine(3, "Готово"), { ...line, buyer: "Закупщик", status: "Согласование" }]);

  const first = render(<ProcurementPage />);
  await screen.findByRole("heading", { name: /^Принято/ });
  fireEvent.click(screen.getByRole("button", { name: "Блоки" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Принято за 30 дней" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Прочие позиции" }));
  expect(headingNames()).toEqual(["В работе", "Жду счёт", "Ждёт оплаты", "В пути", "Карантин", "Дозавоз"]);
  expect(screen.getByText("скрыто: Принято за 30 дней 2, Прочие позиции 1")).toBeInTheDocument();
  first.unmount();

  render(<ProcurementPage />);
  await screen.findByRole("heading", { name: /^Жду счёт/ });
  expect(headingNames()).toEqual(["В работе", "Жду счёт", "Ждёт оплаты", "В пути", "Карантин", "Дозавоз"]);
  expect(screen.getByText("скрыто: Принято за 30 дней 2, Прочие позиции 1")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Блоки" }));
  expect(screen.getByRole("checkbox", { name: "Принято за 30 дней" })).not.toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Жду счёт" })).toBeChecked();
});
it("one search narrows every block and its counts (ID, name, supplier, order number, invoice number)", async () => {
  mockMine([
    orderLine(1, "Жду счет", { supplier: "ООО Альфа" }),
    orderLine(2, "В оплату", { supplier: "ООО Бета", invoice_number: "СЧ-777", invoice_url: "https://files.test/i.pdf", pay_missing: [] }),
    orderLine(3, "В пути", { supplier: "ООО Альфа" }),
    { ...line, id: "30000000-0000-0000-0000-000000000099", legacy_id: "REQ-99", status: "В работе", supplier: "ООО Альфа" },
  ]);

  render(<ProcurementPage />);
  await screen.findByRole("heading", { name: /^Жду счёт/ });
  const heading = (name: string) => screen.getByRole("heading", { name: new RegExp(`^${name}`) });
  const search = screen.getByRole("searchbox", { name: "Поиск по моим позициям" });

  fireEvent.change(search, { target: { value: "альфа" } });
  expect(heading("В работе")).toHaveTextContent("В работе 1");
  expect(heading("Жду счёт")).toHaveTextContent("Жду счёт 1");
  expect(heading("Ждёт оплаты")).toHaveTextContent("Ждёт оплаты 0");
  expect(heading("В пути")).toHaveTextContent("В пути 1");

  fireEvent.change(search, { target: { value: "сч-777" } });
  expect(heading("Ждёт оплаты")).toHaveTextContent("Ждёт оплаты 1");
  expect(heading("Жду счёт")).toHaveTextContent("Жду счёт 0");
  expect(heading("В работе")).toHaveTextContent("В работе 0");

  fireEvent.change(search, { target: { value: "po-003" } });
  expect(heading("В пути")).toHaveTextContent("В пути 1");
  expect(heading("Ждёт оплаты")).toHaveTextContent("Ждёт оплаты 0");
});

it("shows orders · positions · расчёт / факт in a block heading", async () => {
  mockMine([
    orderLine(1, "Жду счет", { calculated_sum: "100", actual_sum: "120" }),
    orderLine(1, "Жду счет", { id: "30000000-0000-0000-0000-0000000000a1", legacy_id: "REQ-1b", calculated_sum: "50", actual_sum: "60" }),
    orderLine(2, "Жду счет", { calculated_sum: "10", actual_sum: null }),
  ]);

  render(<ProcurementPage />);
  const block = await screen.findByRole("region", { name: "Жду счёт" });
  expect(within(block).getByText(/^2 зак\. · 3 поз\. · расчёт 160,00 ₽ \/ факт 180,00 ₽$/)).toBeInTheDocument();
});

it("shows 30 orders and «Показать ещё» for the rest", async () => {
  mockMine(Array.from({ length: 31 }, (_, index) => orderLine(index + 1, "Жду счет")));

  render(<ProcurementPage />);
  const block = await screen.findByRole("region", { name: "Заказы: Жду счёт" });
  const orders = () => within(block).getAllByRole("rowgroup").filter((group) => group.getAttribute("aria-label"));
  expect(orders()).toHaveLength(30);
  const section = screen.getByRole("region", { name: "Жду счёт" });
  fireEvent.click(within(section).getByRole("button", { name: /Показать ещё/ }));
  expect(orders()).toHaveLength(31);
  expect(within(section).queryByRole("button", { name: /Показать ещё/ })).not.toBeInTheDocument();
});

it("renders no lines for a closed order and a column hidden in «Колонки» stays hidden after a remount", async () => {
  mockMine([orderLine(1, "Жду счет")]);

  const first = render(<ProcurementPage />);
  const block = await screen.findByRole("region", { name: "Заказы: Жду счёт" });
  expect(within(block).queryByText("REQ-1")).not.toBeInTheDocument();
  const section = screen.getByRole("region", { name: "Жду счёт" });
  fireEvent.click(within(section).getByRole("button", { name: "Колонки" }));
  fireEvent.click(within(section).getByRole("checkbox", { name: "Комментарий" }));
  expect(within(block).queryByRole("columnheader", { name: "Комментарий" })).not.toBeInTheDocument();
  expect(within(block).getByRole("columnheader", { name: "Расчёт, ₽" })).toBeInTheDocument();
  first.unmount();

  render(<ProcurementPage />);
  const again = await screen.findByRole("region", { name: "Заказы: Жду счёт" });
  expect(within(again).queryByRole("columnheader", { name: "Комментарий" })).not.toBeInTheDocument();
  expect(within(again).getByRole("columnheader", { name: "Наименование" })).toBeInTheDocument();
  // Другой блок свои колонки не потерял.
  expect(JSON.parse(window.localStorage.getItem("erp-table:procurement-orders-awaiting") ?? "{}").hidden).toEqual(["note"]);
  expect(window.localStorage.getItem("erp-table:procurement-orders-topay")).toBeNull();
});

it("takes away the manual approval stage: no «На согласование» anywhere, a line parked before goes on from «Прочие позиции»", async () => {
  const working: ProcurementLine = { ...line, buyer: "Закупщик" };
  const parked: ProcurementLine = { ...line, id: "30000000-0000-0000-0000-000000000009", legacy_id: "REQ-9", buyer: "Закупщик", status: "Согласование" };
  const awaiting: ProcurementLine = { ...awaitingInvoice, id: "30000000-0000-0000-0000-000000000007", legacy_id: "REQ-7", order_number: "PO-001" };
  const toPay: ProcurementLine = { ...line, id: "30000000-0000-0000-0000-000000000008", legacy_id: "REQ-8", buyer: "Закупщик", status: "В оплату", invoice_url: "https://files.test/i.pdf", pay_missing: [] };
  let current = [working, parked, awaiting, toPay];
  const calls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine?limit=500") return json({ lines: current, next_cursor: null });
    if (init?.method === "POST") {
      calls.push(`${path.replace(`/api/procurement/${parked.id}`, "")} ${init.body}`);
      current = current.map((item) => (item.id === parked.id ? { ...item, status: "В работе", version: item.version + 1 } : item));
      return json({ line: current.find((item) => item.id === parked.id) });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  await openOrder("PO-001");
  await openOrder("Без № заказа");
  for (const id of ["REQ-1", "REQ-7", "REQ-8", "REQ-9"]) {
    expect(rowActionNames(id), id).not.toContain("На согласование");
  }
  expect(screen.queryByRole("button", { name: "Одобрено — продолжить" })).not.toBeInTheDocument();

  await pickRowAction("REQ-9", "Продолжить закупку");
  expect(await screen.findByText("REQ-9: строка вернулась в работу")).toBeInTheDocument();
  expect(calls).toEqual(['/approval/resume {"version":3}']);
  await waitFor(() => expect(within(screen.getByRole("region", { name: "В работе" })).getByText("REQ-9")).toBeInTheDocument());
});

it("cancels the chosen in-work lines in bulk after one confirmation, with progress and a per-line error summary", async () => {
  const at = (n: number): ProcurementLine => ({ ...line, id: `30000000-0000-0000-0000-00000000020${n}`, legacy_id: `REQ-2${n}`, buyer: "Закупщик", version: n });
  const lines = [at(1), at(2), at(3), at(4), at(5), at(6)];
  const cancelled: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine?limit=500") return json({ lines: lines.filter((item) => !cancelled.includes(item.id)) });
    if (path.endsWith("/cancel") && init?.method === "POST") {
      const id = path.split("/")[3];
      if (id === lines[3].id) return problem(422, "Строка оплачена и передана в приёмку — согласование и отмена уже невозможны");
      cancelled.push(id);
      return json({ line: { ...lines.find((item) => item.id === id), status: "Отменено" } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);

  render(<ProcurementPage />);
  const block = await screen.findByRole("region", { name: "В работе" });
  await waitFor(() => expect(within(block).getByLabelText("Выбрать REQ-26")).toBeEnabled());
  fireEvent.click(within(block).getByLabelText("Выбрать REQ-22"));
  fireEvent.click(within(block).getByLabelText("Выбрать REQ-25"), { shiftKey: true });
  const bar = within(block).getByRole("toolbar", { name: "Действия с выбранными" });
  expect(bar).toHaveTextContent("Выбрано: 4");

  fireEvent.click(within(bar).getByRole("button", { name: "Отменить строку" }));
  expect(confirm).toHaveBeenLastCalledWith("Отменить строк: 4? Отмена окончательная.");
  expect(cancelled).toEqual([]);
  fireEvent.click(within(bar).getByRole("button", { name: "Отменить строку" }));

  const status = await within(block).findByText("Отменено: 3 из 4");
  expect(status.closest("[role=status]")).toHaveTextContent("REQ-24: Строка оплачена и передана в приёмку — согласование и отмена уже невозможны");
  expect([...cancelled].sort()).toEqual([lines[1].id, lines[2].id, lines[4].id].sort());
  await waitFor(() => expect(within(block).queryByText("REQ-22")).not.toBeInTheDocument());
  expect(within(block).getByText("REQ-24")).toBeInTheDocument();
});

it("shows the progress of a bulk run while lines are still being processed", async () => {
  const at = (n: number): ProcurementLine => ({ ...line, id: `30000000-0000-0000-0000-00000000030${n}`, legacy_id: `REQ-3${n}`, buyer: "Закупщик" });
  const lines = [at(1), at(2), at(3), at(4), at(5)];
  const release: Array<() => void> = [];
  let inFlight = 0;
  let peak = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine?limit=500") return json({ lines });
    if (path.endsWith("/cancel") && init?.method === "POST") {
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      await new Promise<void>((resolve) => release.push(resolve));
      inFlight -= 1;
      return json({ line: { ...lines[0], status: "Отменено" } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  vi.spyOn(window, "confirm").mockReturnValue(true);

  render(<ProcurementPage />);
  const block = await screen.findByRole("region", { name: "В работе" });
  await waitFor(() => expect(within(block).getByLabelText("Выбрать все")).toBeEnabled());
  fireEvent.click(within(block).getByLabelText("Выбрать все"));
  fireEvent.click(within(within(block).getByRole("toolbar")).getByRole("button", { name: "Отменить строку" }));
  expect(await within(block).findByText("Отменяем строки: 0 из 5")).toBeInTheDocument();
  await waitFor(() => expect(release).toHaveLength(4));
  expect(peak).toBe(4);
  release.shift()!();
  release.shift()!();
  expect(await within(block).findByText("Отменяем строки: 2 из 5")).toBeInTheDocument();
  for (let left = 3; left > 0; left -= 1) {
    await waitFor(() => expect(release.length).toBeGreaterThan(0));
    release.shift()!();
  }
  expect(await within(block).findByText("Отменено: 5 из 5")).toBeInTheDocument();
});

it("forms supplier requests only for the chosen ready lines", async () => {
  const ready = (n: number): ProcurementLine => ({ ...line, id: `30000000-0000-0000-0000-00000000040${n}`, legacy_id: `REQ-4${n}`, status: "Готов к запросу", buyer: "Закупщик", supplier: "ООО А" });
  const lines = [{ ...line, buyer: "Закупщик" }, ready(1), ready(2)];
  const bodies: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine?limit=500") return json({ lines });
    if (path === "/api/procurement/supplier-requests" && init?.method === "POST") {
      bodies.push(JSON.parse(String(init.body)));
      return json({ orders: [], lines: [], skipped: [], other_buyer: 0 });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  const block = await screen.findByRole("region", { name: "В работе" });
  await waitFor(() => expect(within(block).getByLabelText("Выбрать REQ-1")).toBeEnabled());
  fireEvent.click(within(block).getByLabelText("Выбрать REQ-1"));
  const bar = within(block).getByRole("toolbar", { name: "Действия с выбранными" });
  expect(within(bar).getByRole("button", { name: "Сформировать запросы поставщикам" })).toBeDisabled();
  fireEvent.click(within(block).getByLabelText("Выбрать REQ-42"));
  fireEvent.click(within(bar).getByRole("button", { name: "Сформировать запросы поставщикам (1)" }));
  await waitFor(() => expect(bodies).toEqual([{ line_ids: [ready(2).id] }]));
});

it("re-reads «Мои позиции» about 4 and 10 s after a take and shows «заполняется…» until the server fills the row", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    const taken: ProcurementLine = { ...line, buyer: "Закупщик" };
    let mineCalls = 0;
    let serverLine = taken;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/procurement/available") return json({ lines: mineCalls ? [] : [line] });
      if (path === "/api/procurement/mine?limit=500") {
        mineCalls += 1;
        return json({ lines: mineCalls > 1 ? [serverLine] : [] });
      }
      if (path === "/api/procurement/take" && init?.method === "POST") return json({ lines: [taken] });
      throw new Error(`Unexpected request: ${path}`);
    });

    render(<ProcurementPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Взять" }));
    const block = await screen.findByRole("region", { name: "В работе" });
    await within(block).findByText("REQ-1");
    expect(within(block).getByText("заполняется…")).toBeInTheDocument();
    const callsAfterTake = mineCalls;
    // Набранное в ячейке не стирается перечитыванием.
    fireEvent.change(within(block).getByLabelText("Артикул REQ-1"), { target: { value: "МОЙ-1" } });

    // Сервер ещё не дозаполнил строку: через ~4 с перечитали, отметка осталась.
    await vi.advanceTimersByTimeAsync(4000);
    await waitFor(() => expect(mineCalls).toBe(callsAfterTake + 1));
    expect(within(block).getByText("заполняется…")).toBeInTheDocument();

    // Дозаполнил: через ~10 с — поставщик и наименование на сайте на месте, отметки нет, «Артикул» — свой.
    serverLine = { ...taken, version: 4, supplier: "ЧипДип", site_name: "SMA-J на сайте" };
    await vi.advanceTimersByTimeAsync(6000);
    await waitFor(() => expect(mineCalls).toBe(callsAfterTake + 2));
    await waitFor(() => expect((within(block).getByLabelText("Поставщик REQ-1") as HTMLInputElement).value).toBe("ЧипДип"));
    expect((within(block).getByLabelText("Наименование на сайте REQ-1") as HTMLInputElement).value).toBe("SMA-J на сайте");
    expect((within(block).getByLabelText("Артикул REQ-1") as HTMLInputElement).value).toBe("МОЙ-1");
    expect(within(block).queryByText("заполняется…")).not.toBeInTheDocument();

    await vi.advanceTimersByTimeAsync(20000);
    expect(mineCalls).toBe(callsAfterTake + 2);
  } finally {
    vi.useRealTimers();
  }
});

it("groups waiting orders with their header totals and opens «Внести КП в валюте» for the chosen order only", async () => {
  const first = { ...line, status: "Жду счет" as const, buyer: "Закупщик", supplier: "ООО А", order_number: "PO-001", order_date: "2026-10-01", actual_quantity: "2", actual_unit: "шт" };
  const second = { ...first, id: "30000000-0000-0000-0000-000000000002", legacy_id: "REQ-2", calculated_name: "Кабель", version: 4 };
  const third = { ...first, id: "30000000-0000-0000-0000-000000000003", legacy_id: "REQ-3", calculated_name: "Болт", supplier: "ООО Б", order_number: "PO-002", version: 5, actual_sum: "120" };
  const requested: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    requested.push(path);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [first, second, third] });
    if (path === "/api/procurement/kp?reference=PO-002") return json({ order_reference: "PO-002", lines: [third] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  const table = await screen.findByRole("region", { name: "Заказы: Жду счёт" });
  const one = within(table).getByRole("rowgroup", { name: "PO-001 от 01.10 · ООО А" });
  const two = within(table).getByRole("rowgroup", { name: "PO-002 от 01.10 · ООО Б" });
  expect(within(one).getByText("2 поз. · факт — / расчёт 0,00 ₽")).toBeInTheDocument();
  expect(within(two).getByText("1 поз. · факт 120,00 ₽ / расчёт 0,00 ₽")).toBeInTheDocument();
  // Строки заказа — после раскрытия; «Вставить счёт» и «Вставить КП» — в заголовке каждого заказа.
  expect(within(one).queryByText("Кабель")).not.toBeInTheDocument();
  await openOrder("PO-001");
  expect(within(one).getByText("Кабель")).toBeInTheDocument();
  // Цена счёт не держит (владелец, 06.10): у строки без цены — предупреждение, не «не хватает».
  expect(within(within(one).getByText("Кабель").closest("tr")!).getByText("нет цены")).toHaveClass("tone-warn");
  expect(within(within(one).getByText("Кабель").closest("tr")!).queryByText(/не хватает/)).not.toBeInTheDocument();
  expect(within(table).getAllByRole("button", { name: "Вставить счёт" })).toHaveLength(2);
  expect(within(table).getAllByRole("button", { name: "Вставить КП" })).toHaveLength(2);

  await pickOrderAction("PO-002", "Внести КП в валюте…");
  const form = await screen.findByRole("form", { name: "КП по заказу PO-002" });
  expect(await within(form).findByLabelText("REQ-3 Кол-во (факт)")).toBeInTheDocument();
  expect(within(form).queryByLabelText("REQ-1 Кол-во (факт)")).not.toBeInTheDocument();
  expect(requested).toContain("/api/procurement/kp?reference=PO-002");
  expect(requested).not.toContain("/api/procurement/kp?reference=PO-001");
});

it("lists orders waiting for payment with header totals and closes «Оплачено» for an order that cannot be paid yet", async () => {
  const ready = { ...line, id: "30000000-0000-0000-0000-000000000005", legacy_id: "REQ-5", status: "В оплату", buyer: "Закупщик", supplier: "Поставщик", order_number: "PO-005", order_date: "2026-10-05", invoice_url: "https://files.test/i.pdf", pay_missing: [], version: 7, actual_sum: "244", calculated_sum: "200" };
  const noInvoice = { ...line, id: "30000000-0000-0000-0000-000000000006", legacy_id: "REQ-6", status: "В оплату", buyer: "Закупщик", invoice_url: null, pay_missing: ["СЧЕТ"], version: 2 };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [ready, noInvoice] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  const table = await screen.findByRole("region", { name: "Заказы: Ждёт оплаты" });
  const group = within(table).getByRole("rowgroup", { name: "PO-005 от 05.10 · Поставщик" });
  expect(within(group).getByText("1 поз. · факт 244,00 ₽ / расчёт 200,00 ₽")).toBeInTheDocument();
  expect(within(group).getByText("Ждёт оплаты")).toHaveClass("order-chip");
  await openOrder("PO-005");
  await openOrder("Без № заказа");
  expect(within(within(table).getByText("REQ-5").closest("tr")!).queryByText(/не хватает|нет счёта/)).not.toBeInTheDocument();
  expect(within(within(table).getByText("REQ-6").closest("tr")!).getByText("нет счёта")).toHaveClass("tone-warn");
  // «Счёт» — в заголовке заказа; у строк с тем же счётом колонка «Счёт» — «—».
  expect(within(group).getByRole("link", { name: "Счёт" })).toHaveAttribute("href", "https://files.test/i.pdf");
  expect(within(table).queryByRole("link", { name: "счёт" })).not.toBeInTheDocument();
  expect(within(table).getByRole("rowgroup", { name: "Без № заказа" })).toBeInTheDocument();

  const paid = within(table).getAllByRole("button", { name: "Оплачено" });
  expect(paid).toHaveLength(2);
  expect(paid[0]).toBeEnabled();
  expect(paid[1]).toBeDisabled();
  expect(screen.queryByLabelText("Цена факт REQ-5")).not.toBeInTheDocument();
});

it("pays one «В оплату» line from its menu and asks for the rate when the CBR is silent", async () => {
  const ready = { ...line, status: "В оплату", buyer: "Закупщик", invoice_url: "https://files.test/i.pdf", pay_missing: [], expected_arrival: "2026-10-05" };
  const bodies: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [ready] });
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
  await openOrder("Без № заказа");
  await pickRowAction("REQ-1", "Оплатить одну строку…");
  fireEvent.click(await screen.findByRole("button", { name: "Оплачено → в пути" }));
  fireEvent.change(await screen.findByLabelText(/Курс, ₽ за 1 USD/), { target: { value: "95,5" } });
  fireEvent.click(screen.getByRole("button", { name: "Оплачено → в пути" }));
  await waitFor(() => expect(bodies).toEqual([{ version: 3 }, { version: 3, manual_exchange_rate: "95,5" }]));
});

it("paid orders: offer to reconcile a hand-typed rate only for lines that have one, one by one or from the order menu", async () => {
  const manual = {
    ...line, status: "В пути", buyer: "Закупщик", invoice_url: "https://files.test/i.pdf", paid_at: "2026-10-01T12:00:00+03:00", pay_missing: [],
    source_currency: "USD", source_sum: "100", exchange_rate: "95.5", exchange_rate_date: "2026-10-01", exchange_rate_manual: true, actual_sum: "9550",
  };
  const plain = { ...line, id: "30000000-0000-0000-0000-000000000002", legacy_id: "REQ-2", status: "В пути", buyer: "Закупщик", invoice_url: "https://files.test/j.pdf", pay_missing: [] };
  const posted: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [manual, plain] });
    if (path.endsWith("/exchange-rate/reconcile") && init?.method === "POST") {
      posted.push(`${path} ${init.body}`);
      return json({ line: { ...manual, exchange_rate_manual: false, version: 4 } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  await openPaidBlock();
  const table = await screen.findByRole("region", { name: "Заказы: В пути" });
  await openOrder("Без № заказа");
  expect(within(table).getByText(/вручную, сверьте/)).toBeInTheDocument();
  expect(within(table).getByText("100 USD")).toBeInTheDocument();
  expect(rowActionNames("REQ-2")).not.toContain("Сверить курс с ЦБ");
  await pickRowAction("REQ-1", "Сверить курс с ЦБ");
  await waitFor(() => expect(posted).toEqual([`/api/procurement/${manual.id}/exchange-rate/reconcile {"version":3}`]));

  // Пачкой: пункт меню заказа сверяет только строки с курсом, введённым вручную.
  posted.length = 0;
  await waitFor(() => expect(screen.getByRole("button", { name: "Обновить" })).toBeEnabled());
  await pickOrderAction("Без № заказа", "Сверить курс с ЦБ (1)");
  await waitFor(() => expect(posted).toEqual([`/api/procurement/${manual.id}/exchange-rate/reconcile {"version":3}`]));
  expect(await screen.findByText("Курс сверен: 1 из 1")).toBeInTheDocument();
});

it("ID-6: view mode lists free and in-work lines without any buyer control", async () => {
  const requested: string[] = [];
  const free = { ...line, id: "30000000-0000-0000-0000-000000000009", legacy_id: "REQ-9", status: "Новый", buyer: null };
  const taken = { ...line, buyer: "Петров", supplier: "ООО Поставка", order_number: "PO-001", status: "В пути", expected_arrival: "2026-10-05" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    requested.push(`${init?.method ?? "GET"} ${path}`);
    if (path === "/api/procurement/available") return json({ lines: [free], next_cursor: null });
    if (path === "/api/procurement/in-work?limit=500") return json({ lines: [taken], next_cursor: null });
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
  expect(requested).not.toContain("GET /api/procurement/mine?limit=500");
});

it("takes several free lines in one request", async () => {
  const bodies: Record<string, unknown>[] = [];
  const first = { ...line, status: "Новый" };
  const second = { ...line, id: "30000000-0000-0000-0000-000000000002", legacy_id: "REQ-2", status: "Новый" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.body) bodies.push({ path, ...JSON.parse(String(init.body)) });
    if (path === "/api/procurement/available") return json({ lines: [first, second], next_cursor: null });
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/meta") return json({ units: ["шт"], statuses: [] });
    if (path === "/api/procurement/take") return json({ lines: [first, second] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  fireEvent.click(await screen.findByLabelText(`Выбрать ${first.id}`));
  fireEvent.click(screen.getByLabelText(`Выбрать ${second.id}`));
  fireEvent.click(screen.getByRole("button", { name: "Взять в работу" }));
  await waitFor(() => expect(bodies).toContainEqual({ path: "/api/procurement/take", line_ids: [first.id, second.id] }));
  expect(await screen.findByText("Взято в работу: 2")).toBeInTheDocument();
});

it("offers the actual unit as a list", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [{ ...line, buyer: "Закупщик" }], next_cursor: null });
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
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [own] });
    if (path.endsWith("/traceability") && init?.method === "PATCH") {
      body = JSON.parse(String(init.body));
      return json({ line: { ...own, version: 4, manufacturer: "Molex", country: "США" } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  // «Готов к запросу» — строка блока «В работе»: прослеживаемость раскрывается под ней.
  await pickRowAction("REQ-1", "Прослеживаемость, раздробить…");
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
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [ready], next_cursor: null });
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
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [shipped], next_cursor: null });
    if (path === "/api/procurement/invoice-files") {
      return json({ ...UPLOADED, invoice_number: "", invoice_date: "", file_name: "Счёт.jpg", hint: "Текст из файла не читается (скан или картинка) — № и дату счёта впишите вручную." });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  await openPaidBlock();
  await openOrder("Без № заказа");
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
    if (path === "/api/procurement/mine?limit=500") return json({ lines: saved ? [] : [awaiting, other] });
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
  await pickOrderAction("PO-001", "Внести КП в валюте…");
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

it("edits «Комментарий» on every stage from «В работе» and «Ожид. дата» from «Жду счет» to «Карантин»", async () => {
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
    if (path === "/api/procurement/mine?limit=500") return json({ lines });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  const awaitingBlock = await screen.findByRole("region", { name: "Жду счёт" });
  const toPayBlock = screen.getByRole("region", { name: "Ждёт оплаты" });
  const paidBlock = screen.getByRole("region", { name: "Карантин" }); // PO-001: «В пути» + «Карантин» — заказ в «Карантине"
  await openOrder("PO-009", awaitingBlock);
  await openOrder("Без № заказа", toPayBlock);
  await openOrder("PO-001", paidBlock);

  const noteOn = (id: string) => screen.queryByRole("button", { name: `Комментарий ${id}` });
  const dateOn = (id: string) => screen.queryByRole("button", { name: `Ожид. дата ${id}` });
  // Комментарий — на каждой стадии, и в листе «В работе» (там же «Готов к запросу»).
  for (const id of ["REQ-1", "REQ-2", "REQ-3", "REQ-4", "REQ-5", "REQ-6", "REQ-7", "REQ-8"]) expect(noteOn(id), id).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "В работе" })).getByRole("button", { name: "Комментарий REQ-2" })).toHaveTextContent("Ждём ответ поставщика");
  // Ожид. дата — от «Жду счет» до «Карантин»; в «В работе», «Готов к запросу» и «Согласование» её нет, в «Готово» только показ.
  for (const id of ["REQ-3", "REQ-4", "REQ-6", "REQ-7"]) expect(dateOn(id), id).toBeInTheDocument();
  for (const id of ["REQ-1", "REQ-2", "REQ-5", "REQ-8"]) expect(dateOn(id), id).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Ожид. дата REQ-6" })).toHaveTextContent("12.10.2026");
  // «Факт. дата» — день приёмки, только показ. Заказ без № (REQ-4 «В оплату» + принятая REQ-8) целиком в «Ждёт оплаты»: блок — по стадии заказа.
  const orders = within(screen.getByRole("region", { name: "Заказы: Карантин" }));
  expect(orders.getByText("03.10.2026")).toBeInTheDocument();
  expect(orders.getByText("04.10.2026")).toBeInTheDocument();
  expect(within(toPayBlock).getByText("02.10.2026")).toBeInTheDocument();
  expect(within(screen.getByRole("region", { name: "Прочие позиции" })).getByText("REQ-5")).toBeInTheDocument();
});

it("saves a typed «Комментарий» and «Ожид. дата» of a stage line with its version and shows the saved line", async () => {
  const transit = staged(6, "В пути", { expected_arrival: "2026-10-12", note: null });
  const sent: Array<{ path: string; body: Record<string, unknown> }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [transit] });
    if (init?.method === "PATCH") {
      const body = JSON.parse(String(init.body));
      sent.push({ path, body });
      return json({ line: path.endsWith("/note") ? { ...transit, note: body.note, version: 4 } : { ...transit, expected_arrival: body.expected_arrival, version: 5 } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  await openPaidBlock();
  await openOrder("Без № заказа");
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
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [waiting] });
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
  expect(within(screen.getByRole("region", { name: "Заказы: Жду счёт" })).getAllByText("15.10.2026").length).toBeGreaterThanOrEqual(1);

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
    if (path === "/api/procurement/mine?limit=500") {
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
  await openOrder("Без № заказа");
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
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [] });
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

it("shows «Неразобранные» only when there is something to take; view mode keeps the block", async () => {
  let free: ProcurementLine[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: free, next_cursor: null });
    if (path === "/api/procurement/mine?limit=500" || path === "/api/procurement/in-work?limit=500") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/meta") return json({ units: ["шт"], statuses: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  const empty = render(<ProcurementPage />);
  await screen.findByText(/Нет строк в работе/);
  expect(screen.queryByRole("region", { name: "Неразобранные" })).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: /Неразобранные/ })).not.toBeInTheDocument();
  empty.unmount();

  render(<ProcurementPage readOnly />);
  expect(await screen.findByRole("region", { name: "Неразобранные" })).toBeInTheDocument();
});

it("reads every page of «Мои позиции» before grouping, so one order is not cut at a page border", async () => {
  const at = (n: number): ProcurementLine => ({
    ...line, id: `30000000-0000-0000-0000-00000000030${n}`, legacy_id: `REQ-3${n}`, buyer: "Закупщик", supplier: "ООО А", status: "Жду счет",
    order_number: "PO-030", order_date: "2026-10-05", actual_quantity: "2", actual_unit: "шт",
  });
  const requested: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    requested.push(path);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [at(1)], next_cursor: "p2" });
    if (path === "/api/procurement/mine?limit=500&after=p2") return json({ lines: [at(2)], next_cursor: "p3" });
    if (path === "/api/procurement/mine?limit=500&after=p3") return json({ lines: [at(3)], next_cursor: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  const group = await screen.findByRole("rowgroup", { name: "PO-030 от 05.10 · ООО А" });
  expect(within(group).getByText(/^3 поз\./)).toBeInTheDocument();
  expect(requested.filter((path) => path.startsWith("/api/procurement/mine?limit=500"))).toEqual([
    "/api/procurement/mine?limit=500", "/api/procurement/mine?limit=500&after=p2", "/api/procurement/mine?limit=500&after=p3",
  ]);
  expect(screen.queryByRole("button", { name: "Ещё мои" })).not.toBeInTheDocument();
});

it("stops reading «Мои позиции» after 50 pages and leaves «Ещё мои» for the rest", async () => {
  let pages = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path.startsWith("/api/procurement/mine?limit=500")) {
      pages += 1;
      return json({ lines: [{ ...line, id: `30000000-0000-0000-0000-${String(pages).padStart(12, "0")}`, legacy_id: `REQ-${pages}`, buyer: "Закупщик" }], next_cursor: `p${pages + 1}` });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  expect(await screen.findByRole("button", { name: "Ещё мои" })).toBeInTheDocument();
  expect(pages).toBe(50);
});

// ── Окно «Вставить счёт» / «Вставить КП»: файл → распознанные цены → все строки заказа или ни одной ──────

const FILE_URL = "/api/procurement/files/40000000-0000-0000-0000-000000000001";

function recognized(kind: "invoice" | "kp", lines: Array<Partial<RecognizedOrderLine> & { line_id: string }>, extra: Partial<OrderDocumentRecognition> = {}): OrderDocumentRecognition {
  return {
    file: { url: FILE_URL, name: kind === "kp" ? "КП Элитан.xlsx" : "Счёт № 58.pdf" },
    kind,
    invoice_number: kind === "kp" ? null : "58",
    invoice_date: kind === "kp" ? null : "2026-09-14",
    currency: "RUB",
    lines: lines.map((item) => ({
      legacy_id: "", version: 0, price_with_vat: null, price: null, vat_rate: "22", quantity: "2", unit: "шт", sum: null,
      matched_name: null, confidence: "name", ...item,
    })),
    unmatched_items: [],
    unmatched_lines: [],
    totals: null,
    ...extra,
  };
}

it("«Вставить счёт» opens a window: recognised prices and match per line, editable fallback, confirm sends every line with its version; 409 and 422 keep it open", async () => {
  const first = { ...line, status: "Жду счет" as const, buyer: "Закупщик", supplier: "ООО А", order_number: "PO-001", order_date: "2026-10-01", actual_quantity: "2", actual_unit: "шт" };
  const second = { ...first, id: "30000000-0000-0000-0000-000000000002", legacy_id: "REQ-2", calculated_name: "Кабель", version: 5 };
  const third = { ...first, id: "30000000-0000-0000-0000-000000000003", legacy_id: "REQ-3", calculated_name: "Болт", version: 6 };
  let bump = 0;
  let paid = false;
  let recognizeBody: { kind?: string; file_name?: string; content_base64?: string } | undefined;
  let recognizeType: string | null = "";
  const bodies: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine?limit=500") {
      const status = paid ? "В оплату" : "Жду счет";
      return json({ lines: [first, second, third].map((item) => ({ ...item, status, version: item.version + bump })), next_cursor: null });
    }
    if (path === "/api/procurement/orders/PO-001/recognize" && init?.method === "POST") {
      recognizeBody = JSON.parse(String(init.body));
      recognizeType = new Headers(init.headers).get("Content-Type");
      return json(recognized("invoice", [
        { line_id: first.id, legacy_id: "REQ-1", version: 3, price_with_vat: "122", matched_name: "Разъём SMA-J", confidence: "article" },
        { line_id: second.id, legacy_id: "REQ-2", version: 5, price_with_vat: "50", quantity: "3", matched_name: "Кабель RG-58", confidence: "weak" },
      ], {
        unmatched_items: [{ name: "Доставка", quantity: "1", price_with_vat: "500" }],
        unmatched_lines: ["REQ-3"],
        totals: { sum: "700", vat: "144", sum_with_vat: "844" },
      }));
    }
    if (path === "/api/procurement/orders/PO-001/apply-document" && init?.method === "POST") {
      bodies.push(JSON.parse(String(init.body)));
      if (bodies.length === 1) { bump = 1; return problem(409, "Конкурентное изменение: обновите данные и повторите команду"); }
      if (bodies.length === 2) return problem(422, "REQ-2: цена с НДС не сходится с суммой по счёту");
      paid = true;
      return json({ lines: [] });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  const group = await screen.findByRole("rowgroup", { name: "PO-001 от 01.10 · ООО А" });
  await waitFor(() => expect(within(group).getByRole("button", { name: "Вставить счёт" })).toBeEnabled());
  fireEvent.click(within(group).getByRole("button", { name: "Вставить счёт" }));
  // Окно, а не форма под заголовком: строки заказа, ожидаемая дата видна и обязательна, подтвердить — после файла.
  const dialog = await screen.findByRole("dialog", { name: "Вставить счёт PO-001" });
  expect(group.querySelector("form, .group-form")).toBeNull();
  expect((within(dialog).getByLabelText("Ожидаемая дата *") as HTMLInputElement).value).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  expect(within(dialog).getByRole("button", { name: "В оплату (3)" })).toBeDisabled();
  expect(within(dialog).getByText("Выберите файл счёта")).toBeInTheDocument();

  fireEvent.change(within(dialog).getByLabelText("Файл счёта"), { target: { files: [new File(["%PDF"], "счёт.pdf", { type: "application/pdf" })] } });
  expect(await within(dialog).findByText(/Счёт № 58\.pdf · распознано: № 58 от 14\.09\.2026 · найдено строк 2 из 3/)).toBeInTheDocument();
  expect(recognizeBody?.kind).toBe("invoice");
  expect(recognizeBody?.file_name).toBe("счёт.pdf");
  expect(recognizeBody?.content_base64).toBeTruthy();
  expect(recognizeType).toBe("application/json");
  expect(within(dialog).getByLabelText("№ счёта")).toHaveValue("58");
  expect(within(dialog).getByLabelText("Дата счёта")).toHaveValue("2026-09-14");
  expect(within(dialog).getByLabelText("Цена с НДС REQ-1")).toHaveValue("122");
  expect(within(dialog).getByLabelText("Цена с НДС REQ-2")).toHaveValue("50");
  expect(within(dialog).getByLabelText("Цена с НДС REQ-3")).toHaveValue("");
  // «Наименование в счёте» подставлено из строки документа; без строки в документе — пусто, подсказка — название строки.
  expect(within(dialog).getByLabelText("Наименование в счёте REQ-1")).toHaveValue("Разъём SMA-J");
  expect(within(dialog).getByLabelText("Наименование в счёте REQ-2")).toHaveValue("Кабель RG-58");
  expect(within(dialog).getByLabelText("Наименование в счёте REQ-3")).toHaveValue("");
  expect(within(dialog).getByLabelText("Наименование в счёте REQ-3")).toHaveAttribute("placeholder", "Болт");
  fireEvent.change(within(dialog).getByLabelText("Наименование в счёте REQ-2"), { target: { value: "" } });
  fireEvent.change(within(dialog).getByLabelText("Наименование в счёте REQ-3"), { target: { value: "Болт М3 (счёт)" } });
  // Уверенность совпадения: по артикулу — спокойно; слабое и «нет в документе» — подсвечены.
  const row = (id: string) => within(dialog).getByLabelText(`Цена с НДС ${id}`).closest("tr")!;
  expect(within(row("REQ-1")).getByText("по артикулу")).toHaveClass("tone-ok");
  expect(row("REQ-1")).not.toHaveClass("doc-warn");
  expect(within(row("REQ-1")).getByText("в документе: Разъём SMA-J")).toBeInTheDocument();
  expect(within(row("REQ-2")).getByText("слабое — проверьте")).toHaveClass("tone-warn");
  expect(row("REQ-2")).toHaveClass("doc-warn");
  expect(within(row("REQ-2")).getByText("в документе 3 шт")).toBeInTheDocument();
  expect(within(row("REQ-3")).getByText("нет в документе")).toHaveClass("tone-warn");
  expect(row("REQ-3")).toHaveClass("doc-warn");
  expect(within(dialog).getByText(/В документе, но не найдено в заказе: 1/).closest(".warning-inline")).toHaveTextContent("Доставка · 1 · 500,00 ₽");
  expect(within(dialog).getByText(/в документе 844,00 ₽/)).toHaveClass("tone-warn");
  // Без цены строки счёт не держится — предупреждение; цена правится в окне, и оно уходит.
  expect(within(dialog).getByText(/^Нет цены: REQ-3 — строки уйдут «В оплату»/)).toHaveClass("warning-inline");
  expect(within(dialog).getByRole("button", { name: "В оплату (3)" })).toBeEnabled();
  fireEvent.change(within(dialog).getByLabelText("Цена с НДС REQ-3"), { target: { value: "7,5" } });
  expect(within(dialog).queryByText(/^Нет цены/)).not.toBeInTheDocument();
  expect(row("REQ-3")).toHaveTextContent("15,00");
  fireEvent.change(within(dialog).getByLabelText("№ счёта"), { target: { value: "58/1" } });
  fireEvent.change(within(dialog).getByLabelText("Ожидаемая дата *"), { target: { value: "2026-10-08" } });

  // 409: набор строк устарел — очередь перечитана, окно осталось, повтор идёт со свежими версиями.
  fireEvent.click(within(dialog).getByRole("button", { name: "В оплату (3)" }));
  expect(await within(dialog).findByText(/Строки заказа изменились — список обновлён/)).toBeInTheDocument();
  expect(bodies[0]).toEqual({
    kind: "invoice", file_url: FILE_URL, invoice_number: "58/1", invoice_date: "2026-09-14", expected_arrival: "2026-10-08",
    lines: [
      { line_id: first.id, version: 3, price_with_vat: "122", invoice_item_name: "Разъём SMA-J" },
      { line_id: second.id, version: 5, price_with_vat: "50", invoice_item_name: null },
      { line_id: third.id, version: 6, price_with_vat: "7.5", invoice_item_name: "Болт М3 (счёт)" },
    ],
  });

  // 422: сервер назвал строку — его текст в окне, набранное на месте.
  await waitFor(() => expect(within(dialog).getByRole("button", { name: "В оплату (3)" })).toBeEnabled());
  fireEvent.click(within(dialog).getByRole("button", { name: "В оплату (3)" }));
  expect(await within(dialog).findByText("REQ-2: цена с НДС не сходится с суммой по счёту")).toBeInTheDocument();
  expect(bodies[1]).toMatchObject({ lines: [{ version: 4 }, { version: 6 }, { version: 7 }] });
  expect(within(dialog).getByLabelText("Цена с НДС REQ-3")).toHaveValue("7,5");

  // Успех: окно закрыто, заказ остался на месте — его статус сменился на «Ждёт оплаты».
  await waitFor(() => expect(within(dialog).getByRole("button", { name: "В оплату (3)" })).toBeEnabled());
  fireEvent.click(within(dialog).getByRole("button", { name: "В оплату (3)" }));
  expect(await screen.findByText("PO-001: счёт передан в оплату, строк: 3")).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  const same = await screen.findByRole("rowgroup", { name: "PO-001 от 01.10 · ООО А" });
  await waitFor(() => expect(same.querySelector(".order-chip")).toHaveTextContent("Ждёт оплаты"));
  expect(within(same).getByRole("button", { name: "Оплачено" })).toBeInTheDocument();
  expect(bodies).toHaveLength(3);
});

it("«Вставить счёт» without a price for a line warns and still sends the invoice; the row in payment says «нет цены» and «Оплачено» waits", async () => {
  const first = { ...line, status: "Жду счет" as const, buyer: "Закупщик", supplier: "ООО А", order_number: "PO-001", order_date: "2026-10-01", actual_quantity: "2", actual_unit: "шт" };
  const second = { ...first, id: "30000000-0000-0000-0000-000000000002", legacy_id: "REQ-2", calculated_name: "Кабель", version: 5 };
  const priceFields = ["ВАЛЮТА", "ЦЕНА (факт)", "ЦЕНА С НДС (факт)", "СУММА (факт)"];
  let invoiced = false;
  const bodies: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine?limit=500") {
      if (!invoiced) return json({ lines: [first, second], next_cursor: null });
      const toPay = { status: "В оплату", invoice_url: FILE_URL, expected_arrival: "2026-10-08", invoice_item_name: "Разъём" };
      return json({ lines: [
        { ...first, ...toPay, version: 4, pay_missing: [], actual_price_with_vat: "122", actual_sum: "244" },
        { ...second, ...toPay, version: 6, pay_missing: priceFields },
      ], next_cursor: null });
    }
    if (path === "/api/procurement/orders/PO-001/recognize" && init?.method === "POST") {
      return json(recognized("invoice", [
        { line_id: first.id, legacy_id: "REQ-1", version: 3, price_with_vat: "122", matched_name: "Разъём SMA-J", confidence: "article" },
      ], { unmatched_lines: ["REQ-2"] }));
    }
    if (path === "/api/procurement/orders/PO-001/apply-document" && init?.method === "POST") {
      bodies.push(JSON.parse(String(init.body)));
      invoiced = true;
      return json({ kind: "invoice", lines: [], warnings: ["REQ-2 — нет цены: ВАЛЮТА, ЦЕНА (факт), ЦЕНА С НДС (факт), СУММА (факт)"] });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  const group = await screen.findByRole("rowgroup", { name: "PO-001 от 01.10 · ООО А" });
  await waitFor(() => expect(within(group).getByRole("button", { name: "Вставить счёт" })).toBeEnabled());
  fireEvent.click(within(group).getByRole("button", { name: "Вставить счёт" }));
  const dialog = await screen.findByRole("dialog", { name: "Вставить счёт PO-001" });
  fireEvent.change(within(dialog).getByLabelText("Файл счёта"), { target: { files: [new File(["%PDF"], "счёт.pdf", { type: "application/pdf" })] } });
  expect(await within(dialog).findByText(/^Нет цены: REQ-2 — строки уйдут «В оплату», «Оплачено» — после цены/)).toHaveClass("warning-inline");
  expect(within(dialog).getByLabelText("Цена с НДС REQ-2")).toHaveValue("");
  expect(within(dialog).queryByText(/^Нет цены: REQ-2$/)).not.toBeInTheDocument();

  fireEvent.click(within(dialog).getByRole("button", { name: "В оплату (2)" }));
  expect(await screen.findByText("PO-001: счёт передан в оплату, строк: 2")).toBeInTheDocument();
  expect(bodies).toHaveLength(1);
  expect((bodies[0].lines as { price_with_vat: string | null }[]).map((row) => row.price_with_vat)).toEqual(["122", null]);
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

  const same = await screen.findByRole("rowgroup", { name: "PO-001 от 01.10 · ООО А" });
  await waitFor(() => expect(same.querySelector(".order-chip")).toHaveTextContent("Ждёт оплаты"));
  await openOrder("PO-001");
  const row = (id: string) => within(same).getByText(id).closest("tr")!;
  expect(within(row("REQ-2")).getByText("нет цены: ВАЛЮТА, ЦЕНА (факт), ЦЕНА С НДС (факт), СУММА (факт)")).toHaveClass("tone-warn");
  expect(within(row("REQ-1")).queryByText(/нет цены/)).not.toBeInTheDocument();
  const pay = within(same).getByRole("button", { name: "Оплачено" });
  expect(pay).toBeDisabled();
  expect(pay).toHaveAttribute("title", "Оплата закрыта — нет цены: REQ-2. Цену внесите через «Заменить счёт»");
});

it("«Убрать» in the invoice window drops the recognised file: the picker is back, prices and № / date return to the start", async () => {
  const first = { ...line, status: "Жду счет" as const, buyer: "Закупщик", supplier: "ООО А", order_number: "PO-001", order_date: "2026-10-01", actual_quantity: "2", actual_unit: "шт" };
  const second = { ...first, id: "30000000-0000-0000-0000-000000000002", legacy_id: "REQ-2", calculated_name: "Кабель", version: 5 };
  let recognizeCalls = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [first, second], next_cursor: null });
    if (path === "/api/procurement/orders/PO-001/recognize" && init?.method === "POST") {
      recognizeCalls += 1;
      return json(recognized("invoice", [
        { line_id: first.id, legacy_id: "REQ-1", version: 3, price_with_vat: "122", confidence: "article" },
        { line_id: second.id, legacy_id: "REQ-2", version: 5, price_with_vat: "50", confidence: "article" },
      ]));
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  const group = await screen.findByRole("rowgroup", { name: "PO-001 от 01.10 · ООО А" });
  await waitFor(() => expect(within(group).getByRole("button", { name: "Вставить счёт" })).toBeEnabled());
  fireEvent.click(within(group).getByRole("button", { name: "Вставить счёт" }));
  const dialog = await screen.findByRole("dialog", { name: "Вставить счёт PO-001" });
  expect(within(dialog).queryByRole("button", { name: "Убрать" })).not.toBeInTheDocument();
  expect(within(dialog).getByLabelText("№ счёта")).toHaveValue("");
  expect(within(dialog).getByLabelText("Цена с НДС REQ-1")).toHaveValue("");

  fireEvent.change(within(dialog).getByLabelText("Файл счёта"), { target: { files: [new File(["%PDF"], "счёт.pdf", { type: "application/pdf" })] } });
  // После распознавания выбор файла заменён именем файла и кнопкой «Убрать»; цены и № — из файла.
  expect(await within(dialog).findByText("Счёт № 58.pdf", { selector: ".doc-file-name" })).toBeInTheDocument();
  expect(within(dialog).queryByLabelText("Файл счёта")).not.toBeInTheDocument();
  expect(within(dialog).getByLabelText("№ счёта")).toHaveValue("58");
  expect(within(dialog).getByLabelText("Дата счёта")).toHaveValue("2026-09-14");
  expect(within(dialog).getByLabelText("Цена с НДС REQ-1")).toHaveValue("122");
  expect(within(dialog).getByLabelText("Цена с НДС REQ-2")).toHaveValue("50");

  fireEvent.click(within(dialog).getByRole("button", { name: "Убрать" }));
  // Окно вернулось к началу: поле файла на месте, имени и «Убрать» нет, распознанное стёрто.
  expect(within(dialog).getByLabelText("Файл счёта")).toBeInTheDocument();
  expect(within(dialog).queryByRole("button", { name: "Убрать" })).not.toBeInTheDocument();
  expect(within(dialog).queryByText("Счёт № 58.pdf")).not.toBeInTheDocument();
  expect(within(dialog).queryByText(/распознано/)).not.toBeInTheDocument();
  expect(within(dialog).getByLabelText("№ счёта")).toHaveValue("");
  expect(within(dialog).getByLabelText("Дата счёта")).toHaveValue("");
  expect(within(dialog).getByLabelText("Цена с НДС REQ-1")).toHaveValue("");
  expect(within(dialog).getByLabelText("Цена с НДС REQ-2")).toHaveValue("");
  expect(within(dialog).getByText("Выберите файл счёта")).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "В оплату (2)" })).toBeDisabled();

  // Можно выбрать другой файл: распознавание идёт заново.
  fireEvent.change(within(dialog).getByLabelText("Файл счёта"), { target: { files: [new File(["%PDF"], "v2.pdf", { type: "application/pdf" })] } });
  await waitFor(() => expect(within(dialog).getByLabelText("№ счёта")).toHaveValue("58"));
  expect(recognizeCalls).toBe(2);
});

it("«Вставить КП» reads the prices from the file, lets a wrong one be fixed and keeps the order waiting for the invoice", async () => {
  const first = { ...line, status: "Жду счет" as const, buyer: "Закупщик", supplier: "ООО «Элитан Трейд»", order_number: "PO-002", order_date: "2026-10-05", actual_quantity: "2", actual_unit: "шт" };
  const second = { ...first, id: "30000000-0000-0000-0000-000000000002", legacy_id: "REQ-2", calculated_name: "Кабель", version: 5, actual_quantity: "10", actual_unit: "м" };
  let saved = false;
  let reads = 0;
  const bodies: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine?limit=500") {
      return json({ lines: saved ? [{ ...first, actual_price_with_vat: "120", version: 4 }, { ...second, actual_price_with_vat: "99", version: 6 }] : [first, second], next_cursor: null });
    }
    if (path === "/api/procurement/orders/PO-002/recognize") {
      reads += 1;
      if (reads === 1) return problem(422, "Файл не читается: ни одной строки с ценой");
      return json(recognized("kp", [
        { line_id: first.id, legacy_id: "REQ-1", version: 3, price_with_vat: "120", confidence: "name" },
        { line_id: second.id, legacy_id: "REQ-2", version: 5, price_with_vat: "9.9", quantity: "10", unit: "м", confidence: "name" },
      ]));
    }
    if (path === "/api/procurement/orders/PO-002/apply-document" && init?.method === "POST") {
      bodies.push(JSON.parse(String(init.body)));
      saved = true;
      return json({ lines: [] });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  await clickWhenEnabled("Вставить КП");
  const dialog = await screen.findByRole("dialog", { name: "Вставить КП PO-002" });
  // КП — без счёта: ни № и даты, ни ожидаемой даты.
  expect(within(dialog).queryByLabelText("№ счёта")).not.toBeInTheDocument();
  expect(within(dialog).queryByLabelText("Ожидаемая дата *")).not.toBeInTheDocument();
  const file = within(dialog).getByLabelText("Файл КП");
  fireEvent.change(file, { target: { files: [new File(["x"], "kp.pdf", { type: "application/pdf" })] } });
  expect(await within(dialog).findByText("Файл не читается: ни одной строки с ценой")).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Внести цены (2)" })).toBeDisabled();

  fireEvent.change(file, { target: { files: [new File(["x"], "kp.xlsx")] } });
  await waitFor(() => expect(within(dialog).getByLabelText("Цена с НДС REQ-2")).toHaveValue("9.9"));
  expect(within(dialog).getAllByText("по названию")).toHaveLength(2);
  fireEvent.change(within(dialog).getByLabelText("Цена с НДС REQ-2"), { target: { value: "99" } });
  expect(within(dialog).getByLabelText("Цена с НДС REQ-2").closest("tr")).toHaveTextContent("990,00");
  fireEvent.click(within(dialog).getByRole("button", { name: "Внести цены (2)" }));

  await waitFor(() => expect(bodies).toEqual([{
    kind: "kp", file_url: FILE_URL,
    lines: [{ line_id: first.id, version: 3, price_with_vat: "120" }, { line_id: second.id, version: 5, price_with_vat: "99" }],
  }]));
  expect(await screen.findByText("PO-002: цены из КП внесены, строк: 2")).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  // Заказ ждёт счёт, как ждал; цены видны в строках только для чтения.
  const group = screen.getByRole("rowgroup", { name: "PO-002 от 05.10 · ООО «Элитан Трейд»" });
  expect(group.querySelector(".order-chip")).toHaveTextContent("Жду счёт");
  await openOrder("PO-002");
  await waitFor(() => expect(within(group).getByText("99,00")).toBeInTheDocument());
});

it("replaces the invoice of an order waiting for payment from its menu over the same window, for its «В оплату» rows; Esc closes the window", async () => {
  const toPay = { ...line, status: "В оплату", buyer: "Закупщик", supplier: "ООО А", order_number: "PO-004", order_date: "2026-10-02", invoice_url: "https://files.test/i.pdf", invoice_number: "17", invoice_date: "2026-10-01", expected_arrival: "2026-10-09", pay_missing: [], actual_price_with_vat: "122", actual_sum: "244" };
  const shipped = { ...toPay, id: "30000000-0000-0000-0000-000000000002", legacy_id: "REQ-2", status: "В пути", version: 9, paid_at: "2026-10-03T10:00:00+03:00" };
  const bodies: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [toPay, shipped], next_cursor: null });
    if (path === "/api/procurement/orders/PO-004/recognize") {
      return json(recognized("invoice", [{ line_id: toPay.id, legacy_id: "REQ-1", version: 3, price_with_vat: "125", confidence: "article" }]));
    }
    if (path === "/api/procurement/orders/PO-004/apply-document" && init?.method === "POST") {
      bodies.push(JSON.parse(String(init.body)));
      return json({ lines: [toPay] });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  await pickOrderAction("PO-004", "Заменить счёт…");
  expect(await screen.findByRole("dialog", { name: "Заменить счёт PO-004" })).toBeInTheDocument();
  fireEvent.keyDown(window, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

  await pickOrderAction("PO-004", "Заменить счёт…");
  const dialog = await screen.findByRole("dialog", { name: "Заменить счёт PO-004" });
  expect(within(dialog).getByLabelText("№ счёта")).toHaveValue("17");
  expect(within(dialog).getByLabelText("Ожидаемая дата *")).toHaveValue("2026-10-09");
  expect(within(dialog).getByLabelText("Цена с НДС REQ-1")).toHaveValue("122");
  expect(within(dialog).queryByLabelText("Цена с НДС REQ-2")).not.toBeInTheDocument();
  fireEvent.change(within(dialog).getByLabelText("Файл счёта"), { target: { files: [new File(["%PDF"], "v2.pdf", { type: "application/pdf" })] } });
  await waitFor(() => expect(within(dialog).getByLabelText("№ счёта")).toHaveValue("58"));
  expect(within(dialog).getByLabelText("Цена с НДС REQ-1")).toHaveValue("125");
  fireEvent.click(within(dialog).getByRole("button", { name: "Заменить счёт" }));
  await waitFor(() => expect(bodies).toEqual([{
    kind: "invoice", file_url: FILE_URL, invoice_number: "58", invoice_date: "2026-09-14", expected_arrival: "2026-10-09",
    lines: [{ line_id: toPay.id, version: 3, price_with_vat: "125", invoice_item_name: null }],
  }]));
  expect(await screen.findByText("PO-004: счёт заменён, строк: 1")).toBeInTheDocument();
});

it("marks the order paid in one call for its «В оплату» rows only and asks for the rate when the CBR is silent", async () => {
  const at = (n: number): ProcurementLine => ({
    ...line, id: `30000000-0000-0000-0000-00000000040${n}`, legacy_id: `REQ-4${n}`, status: "В оплату", buyer: "Закупщик", supplier: "ООО А",
    order_number: "PO-003", order_date: "2026-10-05", invoice_url: "https://files.test/i.pdf", pay_missing: [], version: 6 + n, actual_sum: "100",
  });
  // Строка того же заказа, уже оплаченная раньше, в «Оплачено» не уходит повторно.
  const shipped = { ...at(3), status: "В пути", paid_at: "2026-10-04T12:00:00+03:00" };
  const bodies: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine?limit=500") return json({ lines: [at(1), at(2), shipped], next_cursor: null });
    if (path === "/api/procurement/orders/PO-003/paid" && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      bodies.push(body);
      if (!body.manual_exchange_rate) {
        return problem(409, { code: "manual_rate_required", currency: "USD", date: "2026-10-04", message: "ЦБ не отдал курс USD на 04.10.2026 — введите курс вручную" });
      }
      return json({ lines: [] });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  const table = await screen.findByRole("rowgroup", { name: "PO-003 от 05.10 · ООО А" });
  expect(within(table).getByText("Ждёт оплаты")).toHaveClass("order-chip");
  await waitFor(() => expect(within(table).getByRole("button", { name: "Оплачено" })).toBeEnabled());
  fireEvent.click(within(table).getByRole("button", { name: "Оплачено" }));
  fireEvent.change(await within(table).findByLabelText(/Курс, ₽ за 1 USD/), { target: { value: "95,5" } });
  fireEvent.click(within(table).getByRole("button", { name: "Оплачено" }));

  await waitFor(() => expect(bodies).toHaveLength(2));
  const lines = [{ line_id: at(1).id, version: 7 }, { line_id: at(2).id, version: 8 }];
  expect(bodies[0]).toEqual({ lines });
  expect(bodies[1]).toEqual({ lines, manual_exchange_rate: "95,5" });
  expect(await screen.findByText("PO-003: оплачено, строк в пути: 2")).toBeInTheDocument();
});

it("on 409 from «Оплачено» says the order changed and rereads the queue", async () => {
  const ready: ProcurementLine = {
    ...line, status: "В оплату", buyer: "Закупщик", supplier: "ООО А", order_number: "PO-003", invoice_url: "https://files.test/i.pdf", pay_missing: [], actual_sum: "100",
  };
  let reads = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine?limit=500") { reads += 1; return json({ lines: [ready], next_cursor: null }); }
    if (path === "/api/procurement/orders/PO-003/paid" && init?.method === "POST") return problem(409, "Конкурентное изменение: обновите данные и повторите команду");
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Оплачено" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Оплачено" }));
  expect(await screen.findByText(/Строки заказа изменились — список обновлён/)).toBeInTheDocument();
  await waitFor(() => expect(reads).toBe(2));
});
