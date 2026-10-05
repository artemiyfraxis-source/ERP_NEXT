import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ReceivingPage } from "./ReceivingPage";
import type { ReceivingLine } from "./api";

const line: ReceivingLine = {
  id: "20000000-0000-0000-0000-000000000001",
  legacy_id: "SM-20260921-01",
  status: "В пути",
  version: 2,
  warehouse: "ЦАР",
  article: "CON-001",
  series: "",
  name: "Разъём",
  item_type: "Комплектующие",
  purpose: "FRX.001",
  supplier: "ООО Поставка",
  ordered_quantity: "10",
  unit: "шт",
  quality_document_required: false,
  shipping_document_url: null,
  quality_document_url: null,
  document_quantity: null,
  fact_quantity: null,
  expiry_date: null,
  disposition: null,
  verdict: null,
  nonconformities: [],
  accepted_quantity: null,
  backorder_quantity: "0",
  grn: null,
  package: "",
  storage_location: null,
  inspector: null,
  warehouse_event_emitted: false,
  inspected_on: null,
};

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const isQueue = (path: string) => path === "/api/receiving" || path.startsWith("/api/receiving?");

/** Столбцы «ID» и «Назначение» скрыты по умолчанию; остальные тесты ищут строки по ID. */
beforeEach(() => { localStorage.setItem("erp-table:receiving-v3", JSON.stringify({ hidden: [], sort: null, preset: "" })); });
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

/** Очередь — таблица; форма строки открывается кликом по строке. */
async function openRow(legacyId = "SM-20260921-01") {
  fireEvent.click(await screen.findByText(legacyId));
}

it("submits document URLs and optimistic version without browser-owned presence flags", async () => {
  let inspection: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (isQueue(path)) return json({ lines: [line] });
    if (path.endsWith("/inspect") && init?.method === "POST") {
      inspection = JSON.parse(String(init.body));
      return json({ line });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReceivingPage />);
  await openRow();
  fireEvent.change(await screen.findByLabelText("Документ УПД/М-15"), {
    target: { value: "https://files.test/upd.pdf" },
  });
  fireEvent.change(screen.getByLabelText("Серия изготовителя"), {
    target: { value: "LOT-2409" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Провести входной контроль" }));

  await waitFor(() => expect(inspection).toEqual(expect.objectContaining({
    version: 2,
    fact_quantity: "10",
    document_quantity: "10",
    shipping_document_url: "https://files.test/upd.pdf",
    series: "LOT-2409",
  })));
  expect(inspection).not.toHaveProperty("has_shipping_document");
  expect(inspection).not.toHaveProperty("quality_document_present");
  expect(inspection).toEqual(expect.objectContaining({ historical: false, document_date: null }));
});

it("submits the incoming control without a УПД — it can come later by ЭДО", async () => {
  let inspection: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/receiving") return json({ lines: [line] });
    if (path.endsWith("/inspect") && init?.method === "POST") {
      inspection = JSON.parse(String(init.body));
      return json({ line });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReceivingPage />);
  await openRow();
  const upd = await screen.findByLabelText("Документ УПД/М-15") as HTMLInputElement;
  expect(upd.required).toBe(false);
  expect(screen.getByText("УПД по ЭДО можно внести позже — «Внести УПД» в списке.")).toBeTruthy();
  expect(screen.queryByText(/уйдёт в «Ожидание»/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Провести входной контроль" }));

  await waitFor(() => expect(inspection).toEqual(expect.objectContaining({ version: 2, shipping_document_url: null })));
});

it("shows the «УПД не приложен» note on an accepted line", async () => {
  const accepted = { ...line, status: "Принято", verdict: "Годен", grn: "FRX.26.0000001", shipping_document_note: "УПД не приложен — придёт по ЭДО" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lines: [accepted], next_cursor: null }));

  render(<ReceivingPage />);
  expect(await screen.findByText("ждём по ЭДО")).toBeTruthy();
  await openRow();
  expect(await screen.findByText("УПД не приложен — придёт по ЭДО")).toBeTruthy();
});

it("sends the УПД date for a historical receipt so the verdict uses it", async () => {
  let inspection: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (isQueue(path)) return json({ lines: [line] });
    if (path.endsWith("/inspect") && init?.method === "POST") {
      inspection = JSON.parse(String(init.body));
      return json({ line });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReceivingPage />);
  await openRow();
  fireEvent.change(await screen.findByLabelText("Документ УПД/М-15"), {
    target: { value: "https://files.test/upd.pdf" },
  });
  expect(screen.getByLabelText("Дата УПД/М-15")).not.toBeRequired();
  fireEvent.click(screen.getByLabelText("Историческая приёмка: срок годности сверяется с датой УПД"));
  fireEvent.change(screen.getByLabelText("Дата УПД/М-15"), { target: { value: "2026-02-10" } });
  fireEvent.click(screen.getByRole("button", { name: "Провести входной контроль" }));

  await waitFor(() => expect(inspection).toEqual(expect.objectContaining({
    historical: true,
    document_date: "2026-02-10",
  })));
});

it("sends the weighed mass and prefills the sample it was weighed with on repeated control", async () => {
  let inspection: Record<string, unknown> | undefined;
  const weighed = { ...line, mass_fields: { mass_1: "", mass_10: "", mass_100: "0.123" } };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (isQueue(path)) return json({ lines: [weighed] });
    if (path.endsWith("/inspect") && init?.method === "POST") {
      inspection = JSON.parse(String(init.body));
      return json({ line: weighed });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReceivingPage />);
  await openRow();
  expect(await screen.findByLabelText("Масса 100 шт, г")).toHaveValue("0.123");
  fireEvent.change(screen.getByLabelText("Масса 10 шт, г"), { target: { value: "0,5" } });
  fireEvent.change(screen.getByLabelText("Документ УПД/М-15"), { target: { value: "https://files.test/upd.pdf" } });
  fireEvent.click(screen.getByRole("button", { name: "Провести входной контроль" }));

  await waitFor(() => expect(inspection).toEqual(expect.objectContaining({
    mass_1: "", mass_10: "0,5", mass_100: "0.123",
  })));
});

it("keeps persisted documents and manual defects during repeated control", async () => {
  let inspection: Record<string, unknown> | undefined;
  const waiting: ReceivingLine = {
    ...line,
    status: "Ожидание",
    shipping_document_url: "https://files.test/upd.pdf",
    quality_document_url: "https://files.test/quality.pdf",
    nonconformities: ["Механическое повреждение / брак"],
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (isQueue(path)) return json({ lines: [waiting], next_cursor: null });
    if (path.endsWith("/inspect") && init?.method === "POST") {
      inspection = JSON.parse(String(init.body));
      return json({ line: waiting });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReceivingPage />);
  await openRow();
  expect(await screen.findByLabelText("Документ УПД/М-15")).toHaveValue(
    "https://files.test/upd.pdf",
  );
  expect(screen.getByLabelText("Документ качества")).toHaveValue(
    "https://files.test/quality.pdf",
  );
  expect(screen.getByLabelText("Механическое повреждение / брак")).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Провести входной контроль" }));

  await waitFor(() => expect(inspection).toEqual(expect.objectContaining({
    shipping_document_url: "https://files.test/upd.pdf",
    quality_document_url: "https://files.test/quality.pdf",
    manual_nonconformities: ["Механическое повреждение / брак"],
  })));
});

it("shows that a paid shortfall went back to procurement as a backorder", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (isQueue(String(input))) {
      return json({ lines: [{ ...line, status: "Ожидание", backorder_quantity: "2.5", nonconformities: ["Недовоз (УПД < заказ)"] }] });
    }
    throw new Error(`Unexpected request: ${String(input)}`);
  });

  render(<ReceivingPage />);
  await openRow();

  expect(await screen.findByText("Недовоз 2.5 шт — оплаченный остаток ушёл в закупку дозавозом")).toBeInTheDocument();
});

it("opens a new incoming-control revision only for a line with a held act", async () => {
  const inspected: ReceivingLine = { ...line, status: "Ожидание", verdict: "Условно годен", version: 3 };
  let revision: Record<string, unknown> | undefined;
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (isQueue(path)) return json({ lines: revision ? [{ ...inspected, status: "Архив ВК", version: 4 }] : [inspected, { ...line, id: "other", legacy_id: "SM-20260921-02" }] });
    if (path.endsWith("/vk-revision") && init?.method === "POST") {
      revision = JSON.parse(String(init.body));
      return json({ archived: { ...inspected, status: "Архив ВК" }, line });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReceivingPage />);
  await openRow("SM-20260921-02");
  expect(screen.queryByRole("button", { name: "Провести новый ВК" })).toBeNull();
  await openRow("SM-20260921-01");
  const buttons = await screen.findAllByRole("button", { name: "Провести новый ВК" });
  expect(buttons).toHaveLength(1);
  fireEvent.click(buttons[0]);

  await waitFor(() => expect(revision).toEqual({ version: 3 }));
  expect(confirm).toHaveBeenCalledOnce();
  expect(await screen.findByText(/Архив ВК: акты этой строки/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Провести новый ВК" })).toBeNull();
});

it("ID-6: view mode shows the queue without inspection, placement or a new ВК", async () => {
  const accepted = { ...line, id: "20000000-0000-0000-0000-000000000002", legacy_id: "SM-20260921-02", status: "Принято", verdict: "Годен", grn: "FRX.26.0000002" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (isQueue(path)) return json({ lines: [line, accepted], next_cursor: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReceivingPage readOnly />);

  expect(await screen.findByText(/SM-20260921-01/)).toBeInTheDocument();
  expect(screen.getByText(/SM-20260921-02/)).toBeInTheDocument();
  expect(screen.getByText("Приёмка открыта вам только на просмотр.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Провести входной контроль" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Передать на склад" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Провести новый ВК" })).not.toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
});

it("returns an accepted line to the supplier with a shared reason and the chosen quantity", async () => {
  const accepted = {
    ...line,
    status: "Принято",
    version: 5,
    grn: "FRX.26.0000001",
    fact_quantity: "10",
    accepted_quantity: "10",
    warehouse_event_emitted: true,
    storage_location: "1F4",
    stock_warehouse: "ЦАР",
    stock_balance: "10",
  };
  let sent: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (isQueue(path)) return json({ lines: [accepted] });
    if (path === "/api/receiving/supplier-return" && init?.method === "POST") {
      sent = JSON.parse(String(init.body));
      return json({ lines: [{ ...accepted, status: "Возврат" }], writeoffs: 1, notes: [], message: "Возврат оформлен (1): Разъём (FRX.26.0000001). Статус приёмки → «Возврат»; списаний со склада: 1." });
    }
    if (path === "/api/receiving/supplier-return") {
      return json({
        lines: [accepted],
        reasons: ["Механическое повреждение / брак", "Отказ от поставки (иное)"],
        modes: { wait: "Ждём замену — строка снова «В пути»", close: "Заказ закрыт — «Возврат»" },
      });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReceivingPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Возврат поставщику" }));
  fireEvent.click(await screen.findByLabelText("Вернуть SM-20260921-01"));
  expect(screen.getByText("10 шт · ЦАР")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Количество SM-20260921-01"), { target: { value: "4" } });
  fireEvent.change(screen.getByLabelText("Причина"), { target: { value: "Механическое повреждение / брак" } });
  fireEvent.change(screen.getByLabelText("№ претензии"), { target: { value: "П-17" } });
  fireEvent.click(screen.getByLabelText("Ждём замену — строка снова «В пути»"));
  fireEvent.click(screen.getByRole("button", { name: "Оформить возврат (1)" }));

  await screen.findByText(/Возврат оформлен \(1\)/);
  expect(sent).toEqual({
    items: [{ id: accepted.id, version: 5, quantity: "4" }],
    reason: "Механическое повреждение / брак",
    mode: "wait",
    claim: "П-17",
    comment: "",
  });
});

it("hides the supplier return in view mode", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lines: [line] }));

  render(<ReceivingPage readOnly />);

  await screen.findByText(/SM-20260921-01/);
  expect(screen.queryByRole("button", { name: "Возврат поставщику" })).toBeNull();
});

it("RC-13: splits a delivery into manufacturer batches, each with its own quantity and series", async () => {
  let inspection: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (isQueue(path)) return json({ lines: [line] });
    if (path.endsWith("/inspect") && init?.method === "POST") {
      inspection = JSON.parse(String(init.body));
      return json({ line: { ...line, grn: "FRX.26.0000001" }, lines: [{ ...line, grn: "FRX.26.0000001" }, { ...line, legacy_id: "SM-20260921-01-П2", grn: "FRX.26.0000002" }] });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReceivingPage />);
  await openRow();
  fireEvent.change(await screen.findByLabelText("Документ УПД/М-15"), { target: { value: "https://files.test/upd.pdf" } });
  fireEvent.change(screen.getByLabelText("№ УПД/М-15"), { target: { value: "УПД-512" } });
  fireEvent.click(screen.getByRole("button", { name: "+ партия" }));
  fireEvent.change(screen.getByLabelText("Партия 1: по УПД"), { target: { value: "6" } });
  fireEvent.change(screen.getByLabelText("Партия 1: серия"), { target: { value: "A1" } });
  fireEvent.change(screen.getByLabelText("Партия 2: по УПД"), { target: { value: "4" } });
  fireEvent.change(screen.getByLabelText("Партия 2: серия"), { target: { value: "B2" } });
  fireEvent.click(screen.getByRole("button", { name: "Провести ВК по партиям (2)" }));

  await waitFor(() => expect(inspection).toEqual(expect.objectContaining({
    document_number: "УПД-512",
    batches: [
      expect.objectContaining({ document_quantity: "6", series: "A1" }),
      expect.objectContaining({ document_quantity: "4", series: "B2" }),
    ],
  })));
  expect(await screen.findByText(/Присвоены ГРН: SM-20260921-01 — FRX.26.0000001; SM-20260921-01-П2 — FRX.26.0000002/)).toBeInTheDocument();
});

it("RC-8: unpacks an accepted line into packages before placement", async () => {
  const accepted: ReceivingLine = { ...line, status: "Принято", verdict: "Годен", grn: "FRX.26.0000001", fact_quantity: "10", version: 3 };
  let body: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (isQueue(path)) return json({ lines: [accepted] });
    if (path.endsWith("/unpack") && init?.method === "POST") {
      body = JSON.parse(String(init.body));
      return json({ lines: [{ ...accepted, package: "1", fact_quantity: "7" }, { ...accepted, legacy_id: "SM-20260921-01-П2", package: "2", fact_quantity: "3" }] });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReceivingPage />);
  await openRow();
  fireEvent.click(await screen.findByRole("button", { name: "Разукомплектовать на пакеты" }));
  fireEvent.change(screen.getByLabelText("Пакет 1"), { target: { value: "7" } });
  fireEvent.change(screen.getByLabelText("Пакет 2"), { target: { value: "3" } });
  fireEvent.click(screen.getByRole("button", { name: "Разукомплектовать (сумма = 10)" }));

  await waitFor(() => expect(body).toEqual({ version: 3, quantities: ["7", "3"] }));
  expect(await screen.findByText(/Разукомплектовано: SM-20260921-01 — пакет 1, 7 шт; SM-20260921-01-П2 — пакет 2, 3 шт/)).toBeInTheDocument();
});

it("filters the queue by status preset and searches by words", async () => {
  const accepted = { ...line, id: "20000000-0000-0000-0000-000000000002", legacy_id: "SM-20260921-02", status: "Принято", name: "Провод МГТФ" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (isQueue(String(input))) return json({ lines: [line, accepted], next_cursor: null });
    throw new Error(`Unexpected request: ${String(input)}`);
  });

  render(<ReceivingPage />);
  await screen.findByText("SM-20260921-02");
  fireEvent.change(screen.getByLabelText("Поиск: Очередь приёмки"), { target: { value: "мгтф" } });
  expect(screen.queryByText("SM-20260921-01")).toBeNull();
  expect(screen.getByText("SM-20260921-02")).toBeInTheDocument();
});

it("chips ask the server for preset / status so the filter works beyond the first page", async () => {
  const paths: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    paths.push(String(input));
    return json({ lines: [line], next_cursor: null });
  });
  render(<ReceivingPage />);
  await screen.findByText("SM-20260921-01");

  const queueCalls = () => paths.filter((path) => !path.includes("preset=open"));
  fireEvent.click(screen.getByRole("button", { name: "Принято, не разложено" }));
  await waitFor(() => expect(queueCalls()).toContain("/api/receiving?preset=to_stock"));
  fireEvent.click(screen.getByRole("button", { name: "Архив ВК" }));
  await waitFor(() => expect(queueCalls()).toContain("/api/receiving?preset=archive"));
  fireEvent.click(screen.getByRole("button", { name: "Возврат" }));
  await waitFor(() => expect(queueCalls()).toContain(`/api/receiving?status=${encodeURIComponent("Возврат")}`));
  fireEvent.click(screen.getByRole("button", { name: "На складе" }));
  await waitFor(() => expect(queueCalls()).toContain("/api/receiving?preset=done"));
});

it("hides ID and Назначение by default but keeps them in the column picker", async () => {
  localStorage.clear();
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lines: [line], next_cursor: null }));
  render(<ReceivingPage />);
  await screen.findByText("CON-001 · Разъём");
  expect(screen.queryByRole("columnheader", { name: /^ID/ })).toBeNull();
  expect(screen.queryByRole("columnheader", { name: /^Назначение/ })).toBeNull();
  expect(screen.queryByText("SM-20260921-01")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  expect(screen.getByLabelText("ID")).toBeTruthy();
  expect(screen.getByLabelText("Назначение")).toBeTruthy();
});

const deliveryLines: ReceivingLine[] = [
  { ...line, id: "30000000-0000-0000-0000-000000000001", legacy_id: "SM-1", article: "A-1", name: "Разъём", supplier: "ООО «Элитан Трейд»", order_number: "PO-004", expected_arrival: "2026-10-06", version: 3 },
  { ...line, id: "30000000-0000-0000-0000-000000000002", legacy_id: "SM-2", article: "A-2", name: "Клемма", supplier: "ООО «Элитан Трейд»", order_number: "PO-001", expected_arrival: "2026-10-06", version: 5, status: "Ожидание" },
  { ...line, id: "30000000-0000-0000-0000-000000000003", legacy_id: "SM-3", article: "A-3", name: "Провод", supplier: "ИП Иванов", order_number: "PO-009", expected_arrival: "2026-10-08" },
];

function mockDelivery(respond: (body: Record<string, unknown>) => Response | Promise<Response>) {
  const calls: Record<string, unknown>[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (isQueue(path)) return json({ lines: deliveryLines, next_cursor: null });
    if (path === "/api/receiving/deliveries" && init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      calls.push(body);
      return respond(body);
    }
    if (path.startsWith("/api/warehouse")) return json({});
    throw new Error(`unexpected ${path}`);
  });
  return calls;
}

const accepted = (lines: ReceivingLine[]) => json({
  delivery_id: "d1",
  lines: lines.map((item, index) => ({ ...item, status: "Принято", grn: `CSM-0${index + 1}`, version: item.version + 1 })),
});

it("groups «Ожидаются» by supplier with orders, positions and the expected date", async () => {
  mockDelivery(() => accepted([]));
  render(<ReceivingPage />);
  const block = await screen.findByRole("region", { name: "Ожидаются" });
  const text = block.textContent ?? "";
  expect(text).toContain("ООО «Элитан Трейд» · PO-001, PO-004 · 2 поз. · ждём 06.10.2026");
  expect(text).toContain("ИП Иванов · PO-009 · 1 поз. · ждём 08.10.2026");
  expect(text.indexOf("Элитан")).toBeLessThan(text.indexOf("Иванов"));
});

it("a delivery with only the УПД requisites sends the lines without quantities and shows the ГРН", async () => {
  const calls = mockDelivery((body) => accepted(deliveryLines.slice(0, (body.lines as unknown[]).length)));
  render(<ReceivingPage />);
  await screen.findByRole("region", { name: "Ожидаются" });
  fireEvent.click(screen.getAllByRole("button", { name: "Принять поставку" })[0]);
  fireEvent.change(await screen.findByLabelText("УПД №"), { target: { value: "УПД-5" } });
  fireEvent.change(screen.getByLabelText("от"), { target: { value: "2026-10-05" } });
  fireEvent.change(screen.getByLabelText("Место для всех"), { target: { value: "1F4" } });
  fireEvent.click(screen.getByRole("button", { name: "Провести поставку (2 поз.)" }));

  await waitFor(() => expect(calls).toHaveLength(1));
  expect(calls[0]).toEqual({
    lines: [
      { id: deliveryLines[0].id, version: 3, storage_location: "1F4" },
      { id: deliveryLines[1].id, version: 5, storage_location: "1F4" },
    ],
    shipping_document_url: null,
    document_number: "УПД-5",
    document_date: "2026-10-05",
    warehouse: "ЦАР",
  });
  expect(await screen.findByText("Принято 2 позиции, ГРН присвоены")).toBeTruthy();
  expect(screen.getByText("CSM-01")).toBeTruthy();
});

it("the УПД is optional: a delivery goes without number and date and the banner keeps the ЭДО note", async () => {
  const calls = mockDelivery(() => json({
    delivery_id: "d1",
    lines: [deliveryLines[0], deliveryLines[1]].map((item, index) => ({
      ...item, status: "Принято", grn: `CSM-0${index + 1}`, version: item.version + 1, shipping_document_note: "УПД не приложен — придёт по ЭДО",
    })),
  }));
  render(<ReceivingPage />);
  await screen.findByRole("region", { name: "Ожидаются" });
  fireEvent.click(screen.getAllByRole("button", { name: "Принять поставку" })[0]);
  expect(await screen.findByText(/УПД по ЭДО можно внести позже/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Провести поставку (2 поз.)" }));
  await waitFor(() => expect(calls).toHaveLength(1));
  expect(calls[0]).toMatchObject({ document_number: "", document_date: null, shipping_document_url: null });
  expect(await screen.findByText("Принято 2 позиции, ГРН присвоены; УПД не приложен — придёт по ЭДО (внесите кнопкой «Внести УПД»)")).toBeTruthy();
});

it("a discrepancy row sends the fact, an unticked line stays out, the deviation select sends the deviation", async () => {
  const calls = mockDelivery(() => accepted([deliveryLines[0]]));
  render(<ReceivingPage />);
  await screen.findByRole("region", { name: "Ожидаются" });
  fireEvent.click(screen.getAllByRole("button", { name: "Принять поставку" })[0]);
  fireEvent.click(await screen.findByLabelText("Пришло: A-2 · Клемма"));
  fireEvent.change(screen.getByLabelText("Факт: A-1 · Разъём"), { target: { value: "8" } });
  fireEvent.change(screen.getByLabelText("Отклонение: A-1 · Разъём"), { target: { value: "brak" } });
  fireEvent.change(screen.getByLabelText("Брак, шт: A-1 · Разъём"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("button", { name: "Провести поставку (1 поз.)" }));

  await waitFor(() => expect(calls).toHaveLength(1));
  expect(calls[0].lines).toEqual([
    { id: deliveryLines[0].id, version: 3, fact_quantity: "8", deviation: "brak", deviation_quantity: "2" },
  ]);
});

it("names the shortfall backorder in the success banner", async () => {
  mockDelivery(() => json({
    delivery_id: "d1",
    lines: [{ ...deliveryLines[0], status: "Принято", grn: "CSM-01", backorder_quantity: "4" }],
  }));
  render(<ReceivingPage />);
  await screen.findByRole("region", { name: "Ожидаются" });
  fireEvent.click(screen.getAllByRole("button", { name: "Принять поставку" })[0]);
  fireEvent.click(await screen.findByRole("button", { name: "Провести поставку (2 поз.)" }));
  expect(await screen.findByText("Принято 1 позиция, ГРН присвоены; недовоз → дозавоз: A-1 · Разъём 4 шт")).toBeTruthy();
});

it("409 asks to refresh the list; another error shows the server text", async () => {
  let status = 409;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (isQueue(path)) return json({ lines: deliveryLines, next_cursor: null });
    if (path === "/api/receiving/deliveries" && init?.method === "POST") {
      return new Response(JSON.stringify({ detail: status === 409 ? "Конкурентное изменение" : "В одной поставке позиции разных складов" }), { status, headers: { "Content-Type": "application/json" } });
    }
    return json({});
  });
  render(<ReceivingPage />);
  await screen.findByRole("region", { name: "Ожидаются" });
  fireEvent.click(screen.getAllByRole("button", { name: "Принять поставку" })[0]);
  fireEvent.click(await screen.findByRole("button", { name: "Провести поставку (2 поз.)" }));
  expect(await screen.findByText("Строки изменились — обновите список")).toBeTruthy();
  status = 422;
  fireEvent.click(screen.getByRole("button", { name: "Провести поставку (2 поз.)" }));
  expect(await screen.findByText("В одной поставке позиции разных складов")).toBeTruthy();
});

it("RC-9: prints an issued act from the line card", async () => {
  const accepted: ReceivingLine = { ...line, status: "Принято", verdict: "Годен", grn: "FRX.26.0000001", acts: [{ kind: "incoming", title: "Акт входного контроля", number: "ВК-20260921-01", date: "2026-09-21" }] };
  const requested: string[] = [];
  vi.spyOn(window, "open").mockReturnValue(null);
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (isQueue(path)) return json({ lines: [accepted] });
    if (path.includes("/acts/")) {
      requested.push(path);
      return json({ sheet: { title: "АКТ", subtitle: "", columns: [], rows: [], sign: "", note: "" } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReceivingPage />);
  await openRow();
  fireEvent.click(await screen.findByRole("button", { name: "Акт входного контроля № ВК-20260921-01" }));
  await waitFor(() => expect(requested).toEqual([`/api/receiving/${line.id}/acts/incoming`]));
});

it("stores the УПД scan in ERP and puts its link into the inspection", async () => {
  let upload: Record<string, unknown> | undefined;
  let inspection: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (isQueue(path)) return json({ lines: [{ ...line, article: "Разъём", grn_prefix: "FRX" }] });
    if (path === "/api/receiving/files" && init?.method === "POST") {
      upload = JSON.parse(String(init.body));
      return json({ url: "/api/receiving/files/30000000-0000-0000-0000-000000000001", file_name: "УПД SM-20260921-01.pdf", size: 3 });
    }
    if (path.endsWith("/inspect") && init?.method === "POST") {
      inspection = JSON.parse(String(init.body));
      return json({ line });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReceivingPage />);
  expect(await screen.findByText("Разъём")).toBeInTheDocument(); // артикул = наименование — один раз
  await openRow();
  expect(screen.getByText("Автоматически: FRX")).toBeInTheDocument();
  const scan = new File(["pdf"], "скан.pdf", { type: "application/pdf" });
  fireEvent.change(screen.getAllByText("Файл PDF/фото")[0].querySelector("input")!, { target: { files: [scan] } });

  await waitFor(() => expect(screen.getByLabelText("Документ УПД/М-15")).toHaveValue("/api/receiving/files/30000000-0000-0000-0000-000000000001"));
  expect(upload).toEqual(expect.objectContaining({ line_id: line.id, kind: "upd", file_name: "скан.pdf", content_type: "application/pdf" }));
  fireEvent.click(screen.getByRole("button", { name: "Провести входной контроль" }));
  await waitFor(() => expect(inspection).toEqual(expect.objectContaining({
    shipping_document_url: "/api/receiving/files/30000000-0000-0000-0000-000000000001",
  })));
});

it("asks for CSM or PPM up front when the purpose gives no ГРН prefix", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (isQueue(String(input))) return json({ lines: [{ ...line, grn_prefix: null }] });
    throw new Error(`Unexpected request: ${String(input)}`);
  });

  render(<ReceivingPage />);
  await openRow();
  const prefix = await screen.findByLabelText("Идентификатор ГРН *");
  expect(prefix).toBeRequired();
  expect(prefix).toHaveValue("");
});


const orderLines: ReceivingLine[] = [1, 2, 3].map((n) => ({
  ...line,
  id: `20000000-0000-0000-0000-00000000010${n}`,
  legacy_id: `SM-20260921-0${n}`,
  order_number: "PO-001",
  version: n,
}));

it("groups by order by default and applies one УПД to the ticked rows of a group only", async () => {
  let sent: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (isQueue(path)) return json({ lines: orderLines, next_cursor: null });
    if (path === "/api/receiving/documents" && init?.method === "POST") {
      sent = JSON.parse(String(init.body));
      return json({ lines: [] });
    }
    throw new Error(`unexpected ${path}`);
  });
  render(<ReceivingPage />);

  const header = await screen.findByText(`Заказ PO-001 · ${line.supplier}`, { selector: "strong" });
  expect(header.parentElement?.textContent).toBe(`Заказ PO-001 · ${line.supplier} · 3`);
  fireEvent.click(screen.getByLabelText("Выбрать группу"));
  fireEvent.click(screen.getByLabelText(`Выбрать ${orderLines[1].id}`));
  expect(header.parentElement?.textContent).toBe(`Заказ PO-001 · ${line.supplier} · 3 · выбрано 2`);
  fireEvent.click(screen.getByRole("button", { name: "Внести УПД" }));

  expect(await screen.findByText("УПД для 2 позиций")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("№ УПД/М-15"), { target: { value: "УПД-7" } });
  fireEvent.change(screen.getByLabelText("Дата УПД/М-15"), { target: { value: "2026-09-20" } });
  fireEvent.click(screen.getByRole("button", { name: "Внести УПД в 2 позиции" }));

  await waitFor(() => expect(sent).toBeTruthy());
  expect(sent).toEqual({
    lines: [
      { id: orderLines[0].id, version: 1 },
      { id: orderLines[2].id, version: 3 },
    ],
    shipping_document_url: null,
    document_number: "УПД-7",
    document_date: "2026-09-20",
  });
  expect(await screen.findByText("УПД № УПД-7 от 20.09.2026 внесён: 2 позиции")).toBeTruthy();
});

it("enables «Внести УПД» for accepted rows, also on stock — УПД по ЭДО comes after acceptance", async () => {
  const accepted = { ...orderLines[1], status: "Принято" };
  const onStock = { ...orderLines[2], status: "Принято", warehouse_event_emitted: true };
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lines: [orderLines[0], accepted, onStock], next_cursor: null }));
  render(<ReceivingPage />);

  await screen.findByText(/^Заказ PO-001/, { selector: "strong" });
  fireEvent.click(screen.getByLabelText("Выбрать группу"));

  expect((screen.getByRole("button", { name: "Внести УПД" }) as HTMLButtonElement).disabled).toBe(false);
});

it("disables «Внести УПД» when an archived ВК row is selected", async () => {
  const archived = { ...orderLines[1], status: "Архив ВК" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lines: [orderLines[0], archived], next_cursor: null }));
  render(<ReceivingPage />);

  await screen.findByText(/^Заказ PO-001/, { selector: "strong" });
  fireEvent.click(screen.getByLabelText("Выбрать группу"));

  expect((screen.getByRole("button", { name: "Внести УПД" }) as HTMLButtonElement).disabled).toBe(true);
});

it("hides «Скопировать ГРН» when no selected row has a ГРН", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lines: [orderLines[0], { ...orderLines[1], grn: "ГРН-1" }], next_cursor: null }));
  render(<ReceivingPage />);
  await screen.findByText(/^Заказ PO-001/, { selector: "strong" });
  fireEvent.click(screen.getByLabelText(`Выбрать ${orderLines[0].id}`));
  expect(screen.queryByRole("button", { name: "Скопировать ГРН" })).toBeNull();
  fireEvent.click(screen.getByLabelText(`Выбрать ${orderLines[1].id}`));
  expect(screen.getByRole("button", { name: "Скопировать ГРН" })).toBeTruthy();
});

it("shows the УПД column as dd.mm.yyyy", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lines: [{ ...orderLines[0], document_number: "771", document_date: "2026-10-04" }], next_cursor: null }));
  render(<ReceivingPage />);
  expect(await screen.findByText("771 от 04.10.2026")).toBeTruthy();
});

it("names the document in the success banner", async () => {
  const { updDoneText } = await import("./ReceivingPage");
  expect(updDoneText({ url: "", number: "771", date: "2026-10-04" }, 6)).toBe("УПД № 771 от 04.10.2026 внесён: 6 позиций");
  expect(updDoneText({ url: "", number: "771", date: "" }, 1)).toBe("УПД № 771 внесён: 1 позиция");
  expect(updDoneText({ url: "", number: "", date: "2026-10-04" }, 3)).toBe("УПД от 04.10.2026 внесён: 3 позиции");
  expect(updDoneText({ url: "/f.pdf", number: " ", date: "" }, 5)).toBe("Файл УПД внесён: 5 позиций");
});
