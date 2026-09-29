import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
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

afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

/** Очередь — таблица; форма строки открывается кликом по строке. */
async function openRow(legacyId = "SM-20260921-01") {
  fireEvent.click(await screen.findByText(legacyId));
}

it("submits document URLs and optimistic version without browser-owned presence flags", async () => {
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
  fireEvent.change(await screen.findByLabelText("Ссылка на УПД/М-15"), {
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

it("sends the УПД date for a historical receipt so the verdict uses it", async () => {
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
  fireEvent.change(await screen.findByLabelText("Ссылка на УПД/М-15"), {
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
    if (path === "/api/receiving") return json({ lines: [weighed] });
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
  fireEvent.change(screen.getByLabelText("Ссылка на УПД/М-15"), { target: { value: "https://files.test/upd.pdf" } });
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
    if (path === "/api/receiving") return json({ lines: [waiting], next_cursor: null });
    if (path.endsWith("/inspect") && init?.method === "POST") {
      inspection = JSON.parse(String(init.body));
      return json({ line: waiting });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReceivingPage />);
  await openRow();
  expect(await screen.findByLabelText("Ссылка на УПД/М-15")).toHaveValue(
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
    if (String(input) === "/api/receiving") {
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
    if (path === "/api/receiving") return json({ lines: revision ? [{ ...inspected, status: "Архив ВК", version: 4 }] : [inspected, { ...line, id: "other", legacy_id: "SM-20260921-02" }] });
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
    if (path === "/api/receiving") return json({ lines: [line, accepted], next_cursor: null });
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
    if (path === "/api/receiving") return json({ lines: [accepted] });
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
    if (path === "/api/receiving") return json({ lines: [line] });
    if (path.endsWith("/inspect") && init?.method === "POST") {
      inspection = JSON.parse(String(init.body));
      return json({ line: { ...line, grn: "FRX.26.0000001" }, lines: [{ ...line, grn: "FRX.26.0000001" }, { ...line, legacy_id: "SM-20260921-01-П2", grn: "FRX.26.0000002" }] });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReceivingPage />);
  await openRow();
  fireEvent.change(await screen.findByLabelText("Ссылка на УПД/М-15"), { target: { value: "https://files.test/upd.pdf" } });
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
    if (path === "/api/receiving") return json({ lines: [accepted] });
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
    if (String(input) === "/api/receiving") return json({ lines: [line, accepted], next_cursor: null });
    throw new Error(`Unexpected request: ${String(input)}`);
  });

  render(<ReceivingPage />);
  await screen.findByText("SM-20260921-02");
  fireEvent.click(screen.getByRole("button", { name: /^Принято, не разложено/ }));
  expect(screen.queryByText("SM-20260921-01")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /^Все/ }));
  fireEvent.change(screen.getByLabelText("Поиск: Очередь приёмки"), { target: { value: "мгтф" } });
  expect(screen.queryByText("SM-20260921-01")).toBeNull();
  expect(screen.getByText("SM-20260921-02")).toBeInTheDocument();
});

it("RC-9: prints an issued act from the line card", async () => {
  const accepted: ReceivingLine = { ...line, status: "Принято", verdict: "Годен", grn: "FRX.26.0000001", acts: [{ kind: "incoming", title: "Акт входного контроля", number: "ВК-20260921-01", date: "2026-09-21" }] };
  const requested: string[] = [];
  vi.spyOn(window, "open").mockReturnValue(null);
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/receiving") return json({ lines: [accepted] });
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
