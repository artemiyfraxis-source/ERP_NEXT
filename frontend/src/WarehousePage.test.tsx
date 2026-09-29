import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WarehousePage } from "./WarehousePage";
import type { WarehouseLot } from "./api";

const lot: WarehouseLot = {
  id: "40000000-0000-0000-0000-000000000001",
  version: 4,
  warehouse: "ЦАР",
  grn: "FRX.26.0000001",
  package: "",
  article: "GHR-04V-S",
  series: "LOT-2409",
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
  source_receiving_ids: ["20000000-0000-0000-0000-000000000001"],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function added(entered: WarehouseLot[], errors: Array<{ i: number; error: string }> = []) {
  return json({ added: entered.length, lots: entered, assigned: [], errors });
}

afterEach(() => vi.restoreAllMocks());

it("warns instead of printing when nothing is waiting to be put away", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/stock") return json({ lots: [], total: 0, truncated: 0, next_cursor: null });
    if (path === "/api/receiving/putaway-sheet") {
      return json({
        detail: "Нет позиций для раскладки: нужны строки «Принято» с ГРН и пустым местом на складе.",
      }, 422);
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "🖨 Лист перемещения" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Нет позиций для раскладки");
});

it("prints a putaway sheet for accepted lots waiting for a shelf — same mechanism as FF-8", async () => {
  const printWindow = { document: { open: vi.fn(), write: vi.fn(), close: vi.fn() }, focus: vi.fn(), print: vi.fn() };
  vi.spyOn(window, "open").mockReturnValue(printWindow as unknown as Window);
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/stock") return json({ lots: [], total: 0, truncated: 0, next_cursor: null });
    if (path === "/api/receiving/putaway-sheet") {
      return json({ sheet: {
        title: "ЛИСТ ПЕРЕМЕЩЕНИЯ НА СКЛАД",
        subtitle: "Позиций: 1 · сформирован 29.09.2026 10:00",
        columns: ["№", "Наименование", "ГРН", "Пакет", "Кол-во", "Ед.", "Место на складе"],
        rows: [["1", "Разъём", "FRX.26.0000001", "", "10", "шт", ""]],
        note: "",
        sign: "Разложил: ______________________        Дата: ______________",
      } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "🖨 Лист перемещения" }));

  await waitFor(() => expect(printWindow.print).toHaveBeenCalled());
  const written = printWindow.document.write.mock.calls[0][0] as string;
  expect(written).toContain("ЛИСТ ПЕРЕМЕЩЕНИЯ НА СКЛАД");
  expect(written).toContain("Разъём");
  expect(written).toContain("FRX.26.0000001");
  expect(written).toContain("Разложил:");
});

it("writes off stock with its optimistic version and stated reason", async () => {
  let writeoff: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stock") return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
    if (path.endsWith("/writeoff") && init?.method === "POST") {
      writeoff = JSON.parse(String(init.body));
      return json({ lot: { ...lot, version: 5, written_off: "2", balance: "8" } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  fireEvent.click(await screen.findByRole("button", { name: "Операции" }));
  fireEvent.change(await screen.findByLabelText("Количество"), {
    target: { value: "2" },
  });
  expect(screen.getByLabelText("Назначение списания")).toBeRequired();
  fireEvent.change(screen.getByLabelText("Назначение списания"), {
    target: { value: " FRX.001 " },
  });
  fireEvent.change(screen.getByLabelText("Основание"), {
    target: { value: "Выдача в производство" },
  });
  fireEvent.change(screen.getByLabelText("Дата списания"), { target: { value: "2026-09-01" } });
  fireEvent.click(screen.getByRole("button", { name: "Списать" }));

  await waitFor(() => expect(writeoff).toEqual({
    version: 4,
    quantity: "2",
    purpose: "FRX.001",
    reason: "Выдача в производство",
    date: "2026-09-01",
  }));
});

it("offers «Выдача» as the write-off basis and needs a purpose before writing off", async () => {
  let writeoff: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stock") return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
    if (path.endsWith("/writeoff") && init?.method === "POST") {
      writeoff = JSON.parse(String(init.body));
      return json({ lot: { ...lot, version: 5, written_off: "1", balance: "9" } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  fireEvent.click(await screen.findByRole("button", { name: "Операции" }));
  expect(screen.getByLabelText("Основание")).toHaveValue("Выдача");
  fireEvent.change(screen.getByLabelText("Количество"), { target: { value: "1" } });
  fireEvent.change(screen.getByLabelText("Назначение списания"), { target: { value: "   " } });
  expect(screen.getByLabelText("Назначение списания")).toBeInvalid();
  fireEvent.change(screen.getByLabelText("Назначение списания"), { target: { value: "Иванов" } });
  expect(screen.getByLabelText("Назначение списания")).toBeValid();
  fireEvent.click(screen.getByRole("button", { name: "Списать" }));

  await waitFor(() => expect(writeoff).toEqual({ version: 4, quantity: "1", purpose: "Иванов", reason: "Выдача", date: new Date().toLocaleDateString("sv-SE") }));
});

it("returns a write-off to the lot with where it comes back from", async () => {
  const written = { ...lot, written_off: "4", balance: "6", available: "6" };
  let returned: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stock") return json({ lots: [returned ? { ...written, version: 5, written_off: "1", balance: "9", available: "9" } : written], total: 1, truncated: 0, next_cursor: null });
    if (path === `/api/stock/batches/${lot.id}/return` && init?.method === "POST") {
      returned = JSON.parse(String(init.body));
      return json({ lot: { ...written, version: 5, written_off: "1", balance: "9", available: "9" } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  fireEvent.click(await screen.findByRole("button", { name: "Операции" }));
  fireEvent.click(screen.getByRole("button", { name: "Вернуть на склад" }));
  expect(screen.getByLabelText("Количество возврата")).toHaveAttribute("max", "4");
  expect(screen.getByLabelText("Откуда возврат")).toBeRequired();
  fireEvent.change(screen.getByLabelText("Количество возврата"), { target: { value: "3" } });
  fireEvent.change(screen.getByLabelText("Откуда возврат"), { target: { value: " FRX.001 " } });
  fireEvent.change(screen.getByLabelText("Основание возврата"), { target: { value: "не израсходовано" } });
  fireEvent.click(screen.getByRole("button", { name: "Вернуть" }));

  await waitFor(() => expect(returned).toEqual({
    version: 4,
    quantity: "3",
    source: "FRX.001",
    reason: "не израсходовано",
  }));
  await waitFor(() => expect(screen.queryByLabelText("Откуда возврат")).not.toBeInTheDocument());
});

it("offers no return on a lot that was never written off", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/stock") return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  fireEvent.click(await screen.findByRole("button", { name: "Операции" }));

  expect(screen.getByRole("button", { name: "Списать" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Вернуть на склад" })).not.toBeInTheDocument();
});

it("edits article, description, and manufacturer series without changing the lot number", async () => {
  let payload: unknown;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stock" && !init?.method) return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
    if (path.endsWith("/identity") && init?.method === "PUT") {
      payload = JSON.parse(String(init.body));
      return json({ lot: { ...lot, version: 5 } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  fireEvent.click(await screen.findByRole("button", { name: "Операции" }));
  fireEvent.change(screen.getAllByLabelText("Артикул")[0], { target: { value: "CON-001" } });
  fireEvent.change(screen.getAllByLabelText("Наименование")[0], { target: { value: "Разъём питания" } });
  fireEvent.change(screen.getAllByLabelText("Серия")[0], { target: { value: "LOT-2410" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить карточку" }));

  await waitFor(() => expect(payload).toEqual({
    version: 4,
    article: "CON-001",
    name: "Разъём питания",
    series: "LOT-2410",
  }));
});

it("renders the target stock table and sends its filters to the native API", async () => {
  const paths: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    paths.push(String(input));
    return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
  });

  render(<WarehousePage />);
  expect(await screen.findByRole("columnheader", { name: "Партия" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Артикул" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Место" })).toBeInTheDocument();
  expect(await screen.findByText("FRX.26.0000001")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Поиск по складу"), {
    target: { value: "разъём" },
  });
  fireEvent.change(screen.getByLabelText("Склад"), {
    target: { value: "ЦАР" },
  });
  fireEvent.click(screen.getByLabelText("С нулевым остатком"));

  await waitFor(() => expect(paths).toContain(
    "/api/stock?q=%D1%80%D0%B0%D0%B7%D1%8A%D1%91%D0%BC&zero=true&warehouse=%D0%A6%D0%90%D0%A0",
  ));
});

it("groups article rows by manufacturer series", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lots: [lot], total: 1, truncated: 0, next_cursor: null }));

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  fireEvent.change(screen.getByLabelText("Группировка склада"), { target: { value: "series" } });

  expect(screen.getByText("LOT-2409 · 1")).toBeInTheDocument();
  expect(screen.getByText("GHR-04V-S")).toBeInTheDocument();
});

it("keeps an unnamed legacy lot expandable so its article can be entered", async () => {
  const unnamed = { ...lot, article: "", name: "", series: "", grn: "OLD.26.0000299" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lots: [unnamed], total: 1, truncated: 0, next_cursor: null }));

  render(<WarehousePage />);
  expect(await screen.findByText("Без артикула")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Показать партии (1)" }));
  fireEvent.click(await screen.findByRole("button", { name: "Операции" }));

  expect(screen.getByRole("button", { name: "Сохранить карточку" })).toBeInTheDocument();
  expect(screen.getAllByLabelText("Артикул")[0]).toHaveValue("");
});

it("adds a manually counted lot through the live stock route", async () => {
  let payload: unknown;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stock" && !init?.method) {
      return json({ lots: [], total: 0, truncated: 0, next_cursor: null });
    }
    if (path === "/api/stock/batches" && init?.method === "POST") {
      payload = JSON.parse(String(init.body));
      return added([lot]);
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Внести позиции" }));
  fireEvent.change(screen.getByLabelText("Артикул"), { target: { value: "CON-001" } });
  fireEvent.change(screen.getByLabelText("Наименование"), { target: { value: "Разъём" } });
  fireEvent.change(screen.getByLabelText("Серия"), { target: { value: "LOT-2409" } });
  fireEvent.change(screen.getByLabelText("Количество для внесения"), { target: { value: "10" } });
  fireEvent.change(screen.getByLabelText("Место хранения для внесения"), { target: { value: "1F4" } });
  fireEvent.click(screen.getByRole("button", { name: "Внести" }));

  await waitFor(() => expect(payload).toEqual({ items: [{
    warehouse: "ЦАР",
    article: "CON-001",
    series: "LOT-2409",
    name: "Разъём",
    item_type: "",
    purpose: "Склад",
    unit: "шт",
    quantity: "10",
    storage_location: "1F4",
  }] }));
});

it("sends the filled lot passport with a manual entry and shows it in the lot operations", async () => {
  let payload: { items: Array<Record<string, unknown>> } | undefined;
  const weighed = {
    ...lot, unit_mass_g: "0.048", expiry_date: "2030-05-01", manufacturer: "Molex",
    description: "", storage_conditions: "Сухо", datasheet: "", certificate: "РОСС RU.0001",
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stock" && !init?.method) {
      return json({ lots: payload ? [weighed] : [], total: payload ? 1 : 0, truncated: 0, next_cursor: null });
    }
    if (path === "/api/stock/batches" && init?.method === "POST") {
      payload = JSON.parse(String(init.body));
      return added([weighed]);
    }
    if (path.startsWith("/api/stock/article-info")) return json({ info: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Внести позиции" }));
  fireEvent.change(screen.getByLabelText("Артикул"), { target: { value: "CON-001" } });
  fireEvent.change(screen.getByLabelText("Количество для внесения"), { target: { value: "10" } });
  fireEvent.change(screen.getByLabelText("Место хранения для внесения"), { target: { value: "1F4" } });
  fireEvent.change(screen.getByLabelText("Масса 100 шт, г"), { target: { value: "4,8" } });
  fireEvent.change(screen.getByLabelText("Срок годности"), { target: { value: "2030-05-01" } });
  fireEvent.change(screen.getByLabelText("Производитель"), { target: { value: "Molex" } });
  fireEvent.click(screen.getByRole("button", { name: "Внести" }));

  await waitFor(() => expect(payload?.items[0]).toMatchObject({
    article: "CON-001", mass_100: "4,8", expiry_date: "2030-05-01", manufacturer: "Molex",
  }));
  expect(payload?.items[0]).not.toHaveProperty("mass_1");
  expect(payload?.items[0]).not.toHaveProperty("certificate");

  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  fireEvent.click(await screen.findByRole("button", { name: "Операции" }));
  expect(screen.getByLabelText("Паспорт партии")).toHaveTextContent(
    "Масса 1 шт: 0.048 г · Годен до 01.05.2030 · Производитель: Molex · Условия хранения: Сухо · Сертификат: РОСС RU.0001",
  );
});

it("enters a list of rows and keeps the refused ones with the reason (SK-10)", async () => {
  let payload: { items: Array<Record<string, unknown>> } | undefined;
  let reloads = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stock" && !init?.method) {
      reloads += 1;
      return json({ lots: [], total: 0, truncated: 0, next_cursor: null });
    }
    if (path === "/api/stock/batches" && init?.method === "POST") {
      payload = JSON.parse(String(init.body));
      return added([lot], [{ i: 1, error: "кол-во должно быть больше нуля" }]);
    }
    if (path.startsWith("/api/stock/article-info")) return json({ info: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Внести позиции" }));
  fireEvent.change(screen.getByLabelText("Наименование"), { target: { value: "Корпус" } });
  fireEvent.change(screen.getByLabelText("Количество для внесения"), { target: { value: "3" } });
  fireEvent.change(screen.getByLabelText("Место хранения для внесения"), { target: { value: "2A1" } });
  fireEvent.click(screen.getByRole("button", { name: "+ В список" }));

  expect(screen.getByLabelText("Наименование")).toHaveValue("");
  expect(screen.getByLabelText("Место хранения для внесения")).toHaveValue("2A1");
  fireEvent.change(screen.getByLabelText("Артикул"), { target: { value: "CON-002" } });
  fireEvent.change(screen.getByLabelText("Количество для внесения"), { target: { value: "0" } });
  const before = reloads;
  fireEvent.click(screen.getByRole("button", { name: "Внести (2)" }));

  await waitFor(() => expect(payload?.items).toHaveLength(2));
  expect(payload?.items[0]).toMatchObject({ article: "", name: "Корпус", quantity: "3", storage_location: "2A1" });
  expect(payload?.items[1]).toMatchObject({ article: "CON-002", quantity: "0" });
  expect(await screen.findByRole("log")).toHaveTextContent(
    "Записано: 1. Не приняты строки (1): 2 — кол-во должно быть больше нуля",
  );
  const refused = within(screen.getByRole("table", { name: "Позиции к внесению" })).getAllByRole("row");
  expect(refused).toHaveLength(2);
  expect(refused[1]).toHaveTextContent("CON-002");
  expect(refused[1]).toHaveTextContent("2 — кол-во должно быть больше нуля");
  expect(screen.getByRole("button", { name: "Внести" })).toBeInTheDocument();
  await waitFor(() => expect(reloads).toBeGreaterThan(before));  // one row was entered → the list reloads
});

it("fills name, series, and type from the current article catalog", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/stock") return json({ lots: [], total: 0, truncated: 0, next_cursor: null });
    if (path === "/api/stock/article-info?q=N-C58P") return json({ info: {
      series: "N", name: "N-male RG-58", item_type: "Коаксиальный соединитель", source: "разбор кода",
    } });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Внести позиции" }));
  fireEvent.change(screen.getByLabelText("Артикул"), { target: { value: "N-C58P" } });

  await waitFor(() => expect(screen.getByLabelText("Наименование")).toHaveValue("N-male RG-58"));
  expect(screen.getByLabelText("Серия")).toHaveValue("N");
  expect(screen.getByLabelText("Тип")).toHaveValue("Коаксиальный соединитель");
  expect(screen.getByRole("status")).toHaveTextContent("разбор кода");
});

it("loads every cursor page before calculating article totals", async () => {
  const second = { ...lot, id: "40000000-0000-0000-0000-000000000002", grn: "FRX.26.0000002", balance: "5", available: "4" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/stock") return json({ lots: [lot], total: 2, truncated: 1, next_cursor: "cursor-1" });
    if (path === "/api/stock?after=cursor-1") return json({ lots: [second], total: 2, truncated: 1, next_cursor: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);

  expect(await screen.findByRole("button", { name: "Показать партии (2)" })).toBeInTheDocument();
  expect(screen.getByText("15")).toBeInTheDocument();
});

it("moves a whole storage location from the unified movement form", async () => {
  let payload: unknown;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stock" && !init?.method) {
      return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
    }
    if (path === "/api/stock/locations/move" && init?.method === "POST") {
      payload = JSON.parse(String(init.body));
      return json({ lots: [{ ...lot, storage_location: "20B5" }] });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "↔ Переместить" }));
  fireEvent.change(screen.getByLabelText("Исходное место"), { target: { value: "18C3" } });
  fireEvent.change(screen.getByLabelText("Новое место"), { target: { value: "20B5" } });
  fireEvent.click(screen.getByRole("button", { name: "Переместить" }));

  await waitFor(() => expect(payload).toEqual({
    warehouse: "ЦАР", source_location: "18C3", target_location: "20B5",
  }));
});

it("moves a whole lot to the other warehouse without asking for a new place", async () => {
  let payload: unknown;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stock" && !init?.method) return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
    if (path === `/api/stock/batches/${lot.id}/move` && init?.method === "POST") {
      payload = JSON.parse(String(init.body));
      return json({ lot: { ...lot, version: 5, warehouse: "ЖУК" } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  fireEvent.click(await screen.findByRole("button", { name: "Операции" }));
  fireEvent.click(screen.getAllByRole("button", { name: "Переместить" })[0]);
  fireEvent.change(screen.getByLabelText("Склад назначения"), { target: { value: "ЖУК" } });
  expect(screen.getByLabelText("Новое место")).not.toBeRequired();
  expect(screen.getByText(/уедет на ЖУК целиком/)).toBeInTheDocument();
  fireEvent.submit(screen.getByLabelText("Склад назначения").closest("form")!);

  await waitFor(() => expect(payload).toEqual({ version: 4, storage_location: "", warehouse: "ЖУК" }));
});

it("writes an old address as is from the lot operations and shows a lot without a place", async () => {
  let payload: unknown;
  const unplaced = { ...lot, storage_location: "" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stock" && !init?.method) return json({ lots: [unplaced], total: 1, truncated: 0, next_cursor: null });
    if (path.endsWith("/location") && init?.method === "PUT") {
      payload = JSON.parse(String(init.body));
      return json({ lot: { ...lot, version: 5, storage_location: "A-01" } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.change(await screen.findByLabelText("Группировка склада"), { target: { value: "place" } });
  expect(await screen.findByText("Без места · 1")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Показать партии (1)" }));
  expect(screen.getByText("без места")).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "Операции" }));
  fireEvent.change(screen.getByLabelText("Место как есть"), { target: { value: " A-01 " } });
  fireEvent.click(screen.getByRole("button", { name: "Записать место" }));

  await waitFor(() => expect(payload).toEqual({ version: 4, storage_location: "A-01" }));
});

it("edits the whole stock row and sends only what changed, an emptied number untouched", async () => {
  let payload: Record<string, unknown> | undefined;
  const stocked = { ...lot, inbound: "10", written_off: "2", balance: "8", available: "8", manufacturer: "Molex" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stock" && !init?.method) return json({ lots: [stocked], total: 1, truncated: 0, next_cursor: null });
    if (path.endsWith("/row") && init?.method === "PUT") {
      payload = JSON.parse(String(init.body));
      return json({ lot: { ...stocked, version: 5 }, changes: [], message: "Строка FRX.26.0000001 обновлена: 4 поле(й)." });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  fireEvent.click(await screen.findByRole("button", { name: "Операции" }));
  fireEvent.click(screen.getByRole("button", { name: "Править строку" }));
  const form = within(screen.getByRole("form", { name: "Правка строки" }));
  expect(form.getByText(/ГРН FRX\.26\.0000001 .* ключ строки, не правится/)).toBeInTheDocument();
  expect(form.queryByLabelText("ГРН")).toBeNull();
  fireEvent.change(form.getByLabelText("Тип"), { target: { value: " Разъёмы " } });
  fireEvent.change(form.getByLabelText("Место хранения"), { target: { value: "A-01" } });
  fireEvent.change(form.getByLabelText("Производитель"), { target: { value: "" } });
  fireEvent.change(form.getByLabelText("Приход"), { target: { value: "12" } });
  fireEvent.change(form.getByLabelText("Списано"), { target: { value: "" } });
  expect(form.getByRole("status")).toHaveTextContent("Баланс станет: 10 шт");
  fireEvent.click(form.getByRole("button", { name: "Сохранить строку" }));

  await waitFor(() => expect(payload).toEqual({
    version: 4,
    item_type: "Разъёмы",
    storage_location: "A-01",
    manufacturer: "",
    inbound: "12",
  }));
});

it("does not send a row edit when nothing changed", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lots: [lot], total: 1, truncated: 0, next_cursor: null }));

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  fireEvent.click(await screen.findByRole("button", { name: "Операции" }));
  fireEvent.click(screen.getByRole("button", { name: "Править строку" }));
  const form = within(screen.getByRole("form", { name: "Правка строки" }));
  fireEvent.click(form.getByRole("button", { name: "Сохранить строку" }));

  expect(form.getByRole("status")).toHaveTextContent("Изменений нет.");
  expect(fetch.mock.calls.every(([, init]) => !init?.method)).toBe(true);
});

it("hints the smallest free number on the shelf and marks a taken place without blocking it (SK-15)", async () => {
  const places: string[] = [];
  let payload: { items: Array<{ storage_location: string }> } | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stock" && !init?.method) return json({ lots: [], total: 0, truncated: 0, next_cursor: null });
    if (path.startsWith("/api/stock/places?")) {
      places.push(path);
      return json({ warehouse: "ЦАР", shelf: "12C", occupied: ["12C1", "12C2B", "12C4"], next_number: 3 });
    }
    if (path === "/api/stock/batches" && init?.method === "POST") {
      payload = JSON.parse(String(init.body));
      return added([lot]);
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Внести позиции" }));
  const place = screen.getByLabelText("Место хранения для внесения");
  fireEvent.change(place, { target: { value: "12c" } });

  expect(await screen.findByText(/первый свободный № — 3/)).toBeInTheDocument();
  expect(places).toEqual(["/api/stock/places?warehouse=%D0%A6%D0%90%D0%A0&shelf=12C"]);
  expect(place).not.toHaveClass("place-busy");

  fireEvent.click(screen.getByRole("button", { name: "Подставить 12C3" }));
  expect(place).toHaveValue("12C3");
  expect(place).not.toHaveClass("place-busy");

  fireEvent.change(place, { target: { value: "12C4" } });
  await waitFor(() => expect(place).toHaveClass("place-busy"));
  expect(screen.getByText(/Здесь уже лежит другая позиция/)).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Артикул"), { target: { value: "CON-001" } });
  fireEvent.change(screen.getByLabelText("Количество для внесения"), { target: { value: "1" } });
  fireEvent.click(screen.getByRole("button", { name: "Внести" }));
  await waitFor(() => expect(payload?.items[0].storage_location).toBe("12C4"));
});

it("counts the places already listed for entry as taken when it offers a number (SK-15)", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stock" && !init?.method) return json({ lots: [], total: 0, truncated: 0, next_cursor: null });
    if (path.startsWith("/api/stock/places?")) return json({ warehouse: "ЦАР", shelf: "1F", occupied: ["1F1"], next_number: 2 });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Внести позиции" }));
  fireEvent.change(screen.getByLabelText("Артикул"), { target: { value: "CON-001" } });
  fireEvent.change(screen.getByLabelText("Место хранения для внесения"), { target: { value: "1F2" } });
  fireEvent.click(screen.getByRole("button", { name: "+ В список" }));
  fireEvent.change(screen.getByLabelText("Место хранения для внесения"), { target: { value: "1F" } });

  expect(await screen.findByText(/первый свободный № — 3/)).toBeInTheDocument();
});

it("shows no hint for an unknown shelf (SK-15)", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stock" && !init?.method) return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
    if (path.startsWith("/api/stock/places?")) return new Response(JSON.stringify({ detail: "Полки 18A нет" }), { status: 422 });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "↔ Переместить" }));
  fireEvent.change(screen.getByLabelText("Новое место"), { target: { value: "18A1" } });

  await new Promise((resolve) => setTimeout(resolve, 400));
  expect(screen.queryByRole("status")).toBeNull();
});

it("offers «Журнал склада» to the administrator only", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (String(input).startsWith("/api/stock/journal")) return json({ rows: [], total: 0, truncated: 0 });
    return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
  });

  const { unmount } = render(<WarehousePage />);
  expect(await screen.findByText("FRX.26.0000001")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Журнал склада" })).not.toBeInTheDocument();
  unmount();

  render(<WarehousePage admin />);
  fireEvent.click(await screen.findByRole("button", { name: "Журнал склада" }));
  expect(await screen.findByText("Ничего не нашлось.")).toBeInTheDocument();
});

it("ID-6: view mode reads the stock with no entry, move, writeoff or edit control", async () => {
  const requested: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    requested.push(`${init?.method ?? "GET"} ${path}`);
    if (path === "/api/stock") return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage readOnly />);

  fireEvent.click(await screen.findByRole("button", { name: /Показать партии/ }));
  expect(await screen.findAllByText("FRX.26.0000001")).toHaveLength(2);
  expect(screen.getByText("Склад открыт вам только на просмотр.")).toBeInTheDocument();
  for (const name of ["+ Внести позиции", "↔ Переместить", "Справочник типов", "⬇ Выгрузить", "⬆ Загрузить правки", "🖨 Лист перемещения", "Операции"]) {
    expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
  }
  expect(requested.every((call) => call === "GET /api/stock")).toBe(true);
});

it("«История» on a lot opens the stock journal filtered by its ГРН (administrator)", async () => {
  const requested: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.startsWith("/api/stock/journal")) { requested.push(path); return json({ rows: [], total: 0, truncated: 0 }); }
    return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
  });

  render(<WarehousePage admin />);
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  fireEvent.click(await screen.findByRole("button", { name: "Операции" }));
  fireEvent.click(screen.getByRole("button", { name: "История" }));

  expect(await screen.findByLabelText("Поиск по журналу склада")).toHaveValue("FRX.26.0000001");
  await waitFor(() => expect(requested.some((path) => path.includes("FRX.26.0000001"))).toBe(true));
});

it("shows the ГРН assigned to entered rows and copies them tab-separated", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.assign(navigator, { clipboard: { writeText } });
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stock" && !init?.method) return json({ lots: [], total: 0, truncated: 0, next_cursor: null });
    if (path === "/api/stock/batches" && init?.method === "POST") {
      return json({ added: 1, lots: [lot], errors: [], assigned: [{ i: 0, id: lot.id, grn: "OLD.26.0000007", article: "CON-001", quantity: "10", unit: "шт", storage_location: "1F4", warehouse: "ЦАР" }] });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Внести позиции" }));
  fireEvent.change(screen.getByLabelText("Артикул"), { target: { value: "CON-001" } });
  fireEvent.change(screen.getByLabelText("Количество для внесения"), { target: { value: "10" } });
  fireEvent.change(screen.getByLabelText("Место хранения для внесения"), { target: { value: "1F4" } });
  fireEvent.click(screen.getByRole("button", { name: "Внести" }));

  expect(await screen.findByText("Записано на склад: 1")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Скопировать" }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith("OLD.26.0000007\tCON-001\t10 шт\t1F4\tЦАР"));
});
