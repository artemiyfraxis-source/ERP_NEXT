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
    if (path.endsWith("/facts") && init?.method === "PATCH") {
      savedBody = JSON.parse(String(init.body));
      return json({ line: { ...line, supplier: "Поставщик", version: 4 } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Взять" }));
  fireEvent.change(await screen.findByLabelText(/Поставщик/), {
    target: { value: "Поставщик" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

  await waitFor(() => expect(savedBody).toEqual(expect.objectContaining({
    version: 3,
    supplier: "Поставщик",
    actual_quantity: "2",
    actual_unit: "шт",
  })));
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
  fireEvent.change(screen.getByLabelText("Новая ожидаемая дата"), { target: { value: "2026-10-12" } });
  fireEvent.click(screen.getByRole("button", { name: "Досыл подтверждён → в пути" }));
  await waitFor(() => expect(calls).toHaveLength(1));
  fireEvent.click(await screen.findByRole("button", { name: "Отменить дозавоз" }));

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
  fireEvent.click(await screen.findByRole("button", { name: "На согласование" }));
  fireEvent.click(await screen.findByRole("button", { name: "Одобрено — продолжить" }));
  fireEvent.click(await screen.findByRole("button", { name: "На согласование" }));
  await waitFor(() => expect(calls).toHaveLength(3));
  fireEvent.click(await screen.findByRole("button", { name: "Отменить строку" }));
  expect(confirm).toHaveBeenCalledTimes(1);
  expect(calls).toHaveLength(3);
  fireEvent.click(screen.getByRole("button", { name: "Отменить строку" }));

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
  expect(await screen.findByRole("button", { name: "Отменить дозавоз" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Отменить строку" })).not.toBeInTheDocument();
});

it("forms supplier requests for ready lines in one batch and shows the letters", async () => {
  const ready: ProcurementLine = { ...line, status: "Готов к запросу", buyer: "Закупщик", supplier: "ООО А" };
  let formed = false;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [], next_cursor: null });
    if (path === "/api/procurement/mine") {
      return json({
        lines: [formed ? { ...ready, status: "Жду счет", order_number: "PO-001", order_date: "2026-09-16" } : ready],
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
        }],
        lines: [],
        skipped: [],
        other_buyer: 2,
      });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  expect(await screen.findByText(/Готово к запросу: 1/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Сформировать запросы поставщикам" }));

  expect(await screen.findByText("Сформировано запросов: 1; строк других закупщиков не тронуто: 2")).toBeInTheDocument();
  expect(screen.getByText("ООО А · REQ# 16.09.2026 - MR#D-1 PO-001")).toBeInTheDocument();
  expect(await screen.findByText("Заказ PO-001 от 2026-09-16")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Сформировать запросы поставщикам" })).toBeDisabled();
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
  fireEvent.click(await screen.findByText("Заменить счёт"));
  fireEvent.change(screen.getByLabelText("Ссылка на новый счёт"), { target: { value: "https://files.test/invoice-v2.pdf" } });
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
  fireEvent.change(await screen.findByLabelText(/Раздробить по поставщикам/), { target: { value: "3" } });
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

it("warns that an open replacement blocks the supplier order and saves the decision", async () => {
  const ready: ProcurementLine = {
    ...line,
    status: "Готов к запросу",
    buyer: "Закупщик",
    supplier: "Поставщик",
    actual_quantity: "2",
    actual_unit: "шт",
    replacement: true,
    replacement_open: true,
  };
  let current = ready;
  let replacementBody: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine") return json({ lines: [current] });
    if (path.endsWith("/replacement") && init?.method === "PATCH") {
      replacementBody = JSON.parse(String(init.body));
      current = { ...ready, version: 4, replacement_decision: "Разрешено", replacement_open: false };
      return json({ line: current });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  expect(await screen.findByText(/незакрытая замена/)).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Замена"), { target: { value: "Разрешено" } });

  await waitFor(() => expect(replacementBody).toEqual({ version: 3, replacement: true, decision: "Разрешено" }));
  await waitFor(() => expect(screen.queryByText(/незакрытая замена/)).not.toBeInTheDocument());
});

it("DOC-1: prints «Разрешение на замену» once the decision is «Разрешено»", async () => {
  const allowed: ProcurementLine = {
    ...line,
    status: "Готов к запросу",
    buyer: "Закупщик",
    supplier: "Поставщик",
    design_article: "КД-АРТ-001",
    site_name: "Аналог XYZ",
    actual_quantity: "2",
    actual_unit: "шт",
    replacement: true,
    replacement_decision: "Разрешено",
    replacement_open: false,
    permission_number: "РЗ-20260929-01",
    permission_date: "2026-09-29",
  };
  const printedPaths: string[] = [];
  const printWindow = { document: { open: vi.fn(), write: vi.fn(), close: vi.fn() }, focus: vi.fn(), print: vi.fn() };
  vi.spyOn(window, "open").mockReturnValue(printWindow as unknown as Window);
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    printedPaths.push(path);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine") return json({ lines: [allowed] });
    if (path.endsWith("/replacement-permission")) return json({ sheet: {
      title: "РАЗРЕШЕНИЕ НА ЗАМЕНУ РЗ-20260929-01", subtitle: "", columns: [], rows: [], note: "", sign: "",
    } });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ProcurementPage />);
  const button = await screen.findByRole("button", { name: /Печать «Разрешение на замену» РЗ-20260929-01/ });

  fireEvent.click(button);

  await waitFor(() => expect(printedPaths).toContain(`/api/procurement/${allowed.id}/replacement-permission`));
  await waitFor(() => expect(printWindow.print).toHaveBeenCalled());
  expect(printWindow.document.write.mock.calls[0][0]).toContain("РАЗРЕШЕНИЕ НА ЗАМЕНУ РЗ-20260929-01");
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
  const unit = (await screen.findByLabelText(/Единица/)) as HTMLSelectElement;
  expect(unit.tagName).toBe("SELECT");
  await waitFor(() => expect(Array.from(unit.options).map((option) => option.value)).toEqual(["", "шт", "кг", "компл"]));
});

it("saves traceability of an own line: maker, country, quality document, standard", async () => {
  const own: ProcurementLine = { ...line, status: "Готов к запросу", buyer: "Закупщик", supplier: "Поставщик" };
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
  fireEvent.change(await screen.findByLabelText("Изготовитель"), { target: { value: "Molex" } });
  fireEvent.change(screen.getByLabelText("№ док. качества"), { target: { value: "П-17" } });
  fireEvent.change(screen.getByLabelText("Дата док. качества"), { target: { value: "2026-09-01" } });
  fireEvent.change(screen.getByLabelText("Стандарт"), { target: { value: "ГОСТ 23587-96" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить прослеживаемость" }));

  await waitFor(() => expect(body).toEqual({
    version: 3, manufacturer: "Molex", country: "", quality_document_type: "", quality_document_number: "П-17",
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
  fireEvent.click(await screen.findByText("Заменить счёт"));
  fireEvent.change(screen.getByLabelText("Файл счёта"), { target: { files: [new File(["jpg"], "scan.jpg", { type: "image/jpeg" })] } });

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
