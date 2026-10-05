import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WarehousePage } from "./WarehousePage";
import type { WarehouseLot } from "./api";
import { resetStageOptions } from "./StageOptions";

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

afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); resetStageOptions(); });

/** The reference tools of the page sit behind «Ещё ▾». */
async function openMore() {
  fireEvent.click(await screen.findByRole("button", { name: /^Ещё/ }));
  return within(screen.getByRole("menu"));
}

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
  // The first test of the file pays for the cold import; under a full parallel run 1 s is not enough.
  fireEvent.click(await screen.findByRole("button", { name: "Лист перемещения" }, { timeout: 5000 }));

  expect(await screen.findByRole("alert", {}, { timeout: 5000 })).toHaveTextContent("Нет позиций для раскладки");
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
  fireEvent.click(await screen.findByRole("button", { name: "Лист перемещения" }));

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
  fireEvent.click(await screen.findByRole("button", { name: "− Списать" }));
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

it("offers «Выдача в производство» as the write-off basis and needs a purpose before writing off", async () => {
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
  fireEvent.click(await screen.findByRole("button", { name: "− Списать" }));
  expect(screen.getByRole("dialog", { name: "Списать" })).toBeInTheDocument();
  expect(screen.getByLabelText("Основание")).toHaveValue("Выдача в производство");
  fireEvent.change(screen.getByLabelText("Количество"), { target: { value: "1" } });
  fireEvent.change(screen.getByLabelText("Назначение списания"), { target: { value: "   " } });
  expect(screen.getByLabelText("Назначение списания")).toBeInvalid();
  fireEvent.change(screen.getByLabelText("Назначение списания"), { target: { value: "Иванов" } });
  expect(screen.getByLabelText("Назначение списания")).toBeValid();
  fireEvent.click(screen.getByRole("button", { name: "Списать" }));

  await waitFor(() => expect(writeoff).toEqual({ version: 4, quantity: "1", purpose: "Иванов", reason: "Выдача в производство", date: new Date().toLocaleDateString("sv-SE") }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Списать" })).not.toBeInTheDocument());
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
  fireEvent.click(await screen.findByRole("button", { name: "Вернуть на склад" }));
  expect(screen.getByLabelText("Количество возврата")).toHaveAttribute("max", "4");
  expect(screen.getByLabelText("Откуда возврат")).toBeRequired();
  fireEvent.change(screen.getByLabelText("Количество возврата"), { target: { value: "3" } });
  fireEvent.change(screen.getByLabelText("Откуда возврат"), { target: { value: " FRX.001 " } });
  fireEvent.change(screen.getByLabelText("Основание возврата"), { target: { value: "Возврат: ошибочное списание" } });
  fireEvent.click(screen.getByRole("button", { name: "Вернуть" }));

  await waitFor(() => expect(returned).toEqual({
    version: 4,
    quantity: "3",
    source: "FRX.001",
    reason: "Возврат: ошибочное списание",
  }));
  await waitFor(() => expect(screen.queryByLabelText("Откуда возврат")).not.toBeInTheDocument());
});

function returnStock(destinations: unknown[] | null) {
  const written = { ...lot, written_off: "4", balance: "6", available: "6" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/stock") return json({ lots: [written], total: 1, truncated: 0, next_cursor: null });
    if (path === `/api/stock/batches/${lot.id}/issued-to`) {
      if (destinations === null) throw new Error("no history");
      return json({ destinations });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
}

async function openReturn() {
  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  fireEvent.click(await screen.findByRole("button", { name: "Вернуть на склад" }));
}

const out = (name: string, outstanding: string) => ({ name, written_off: "4", returned: "0", outstanding, unit: "шт", last_date: "2026-09-21T12:00:00" });

it("lists where the lot was issued to and caps the return by the chosen destination", async () => {
  returnStock([out("FRX.001", "3"), out("Иванов", "1")]);
  await openReturn();

  const group = await screen.findByRole("group", { name: "Куда выдавали" });
  expect(within(group).getAllByRole("button").map((button) => button.textContent)).toEqual(["FRX.001 · осталось 3 шт", "Иванов · осталось 1 шт"]);
  expect(screen.getByLabelText("Откуда возврат")).toHaveValue("");
  fireEvent.click(within(group).getByRole("button", { name: /Иванов/ }));

  expect(screen.getByLabelText("Откуда возврат")).toHaveValue("Иванов");
  expect(screen.getByLabelText("Количество возврата")).toHaveAttribute("max", "1");
  expect(screen.getByLabelText("Количество возврата")).toHaveAttribute("placeholder", "не больше 1");
});

it("preselects the only destination of the lot", async () => {
  returnStock([out("FRX.001", "3")]);
  await openReturn();

  await waitFor(() => expect(screen.getByLabelText("Откуда возврат")).toHaveValue("FRX.001"));
  expect(screen.getByLabelText("Количество возврата")).toHaveAttribute("max", "3");
});

it("keeps «Откуда» free text over the whole lot when it has no issue history", async () => {
  returnStock([]);
  await openReturn();

  await waitFor(() => expect(screen.getByLabelText("Откуда возврат")).toBeRequired());
  expect(screen.queryByRole("group", { name: "Куда выдавали" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("Откуда возврат")).toHaveValue("");
  expect(screen.getByLabelText("Количество возврата")).toHaveAttribute("max", "4");
});

it("offers no return on a lot that was never written off", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/stock") return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  fireEvent.click(await screen.findByRole("button", { name: "− Списать" }));

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
  fireEvent.click(await screen.findByRole("button", { name: "✎ Правка" }));
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
  expect(await screen.findByRole("columnheader", { name: "ГРН" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: /^Артикул/ })).toHaveAttribute("aria-sort", "ascending");
  expect(screen.getByRole("columnheader", { name: "Места" })).toBeInTheDocument();
  expect(await screen.findByText("FRX.26.0000001")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Поиск по складу"), {
    target: { value: "разъём" },
  });
  fireEvent.change(screen.getByLabelText("Склад"), {
    target: { value: "ЦАР" },
  });
  fireEvent.click(screen.getByLabelText("показывать нулевые"));

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

it("groups article rows by ГРН, packages of one ГРН under one header", async () => {
  const second = { ...lot, id: "40000000-0000-0000-0000-000000000002", package: "2", article: "GHR-06V-S" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lots: [lot, second], total: 2, truncated: 0, next_cursor: null }));

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  fireEvent.change(screen.getByLabelText("Группировка склада"), { target: { value: "grn" } });

  expect(screen.getByText("FRX.26.0000001 · 2")).toBeInTheDocument();
  expect(screen.getByText("GHR-06V-S")).toBeInTheDocument();
});

it("opens the lot cards by a click anywhere on the article row", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lots: [lot], total: 1, truncated: 0, next_cursor: null }));

  render(<WarehousePage />);
  fireEvent.click(await screen.findByText("GHR-04V-S"));
  expect(await screen.findByText("ГРН FRX.26.0000001")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Скрыть партии (1)" }));
  expect(screen.queryByText("ГРН FRX.26.0000001")).not.toBeInTheDocument();
});

it("keeps an unnamed legacy lot expandable so its article can be entered", async () => {
  const unnamed = { ...lot, article: "", name: "", series: "", grn: "OLD.26.0000299" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lots: [unnamed], total: 1, truncated: 0, next_cursor: null }));

  render(<WarehousePage />);
  expect(await screen.findByText("Без наименования")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Показать партии (1)" }));
  fireEvent.click(await screen.findByRole("button", { name: "✎ Правка" }));

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

it("shows every filled field of a listed row and brings it back with «Править»", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stock" && !init?.method) return json({ lots: [], total: 0, truncated: 0, next_cursor: null });
    if (path.startsWith("/api/stock/article-info")) return json({ info: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Внести позиции" }));
  fireEvent.change(screen.getByLabelText("Артикул"), { target: { value: "13-014-" } });
  fireEvent.change(screen.getByLabelText("Серия"), { target: { value: "13" } });
  fireEvent.change(screen.getByLabelText("Тип"), { target: { value: "Соединитель" } });
  fireEvent.change(screen.getByLabelText("Масса 10 шт, г"), { target: { value: "12" } });
  fireEvent.change(screen.getByLabelText("Производитель"), { target: { value: "Амфенол" } });
  fireEvent.click(screen.getByRole("button", { name: "+ В список" }));

  const [, row] = within(screen.getByRole("table", { name: "Позиции к внесению" })).getAllByRole("row");
  expect(row).toHaveTextContent("13-014-");
  expect(row).toHaveTextContent("Соединитель");
  expect(row).toHaveTextContent("Масса 10 шт: 12 г · Производитель: Амфенол");
  expect(row).not.toHaveTextContent("— шт");
  expect(screen.queryByRole("columnheader", { name: "Не принята" })).toBeNull();

  fireEvent.click(within(row).getByRole("button", { name: "Править строку 1" }));
  expect(screen.queryByRole("table", { name: "Позиции к внесению" })).toBeNull();
  expect(screen.getByLabelText("Артикул")).toHaveValue("13-014-");
  expect(screen.getByLabelText("Тип")).toHaveValue("Соединитель");
  expect(screen.getByLabelText("Масса 10 шт, г")).toHaveValue("12");
  expect(screen.getByLabelText("Производитель")).toHaveValue("Амфенол");
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
  expect(screen.getByText(/^15\b/)).toBeInTheDocument();
});

const layout = { layout: { racks: 20, shelves: 6, perRack: {} } };

it("moves a whole storage location, the target picked from the rack layout with the first free №", async () => {
  let payload: unknown;
  const onShelf = { ...lot, id: "40000000-0000-0000-0000-000000000009", grn: "FRX.26.0000009", storage_location: "18C3B" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stock" && !init?.method) return json({ lots: [lot, onShelf], total: 2, truncated: 0, next_cursor: null });
    if (path === "/api/stock/rack-layout") return json(layout);
    if (path === "/api/stock/places?warehouse=%D0%A6%D0%90%D0%A0&shelf=20B") return json({ warehouse: "ЦАР", shelf: "20B", occupied: ["20B1", "20B2", "20B3", "20B4"], next_number: 5 });
    if (path === "/api/stock/locations/move" && init?.method === "POST") {
      payload = JSON.parse(String(init.body));
      return json({ lots: [{ ...onShelf, storage_location: "20B5B" }] });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  await screen.findByRole("button", { name: "Показать партии (2)" });
  fireEvent.click(screen.getByRole("button", { name: "↔ Переместить" }));
  const dialog = screen.getByRole("dialog", { name: "Переместить" });
  fireEvent.change(within(dialog).getByLabelText("Исходное место"), { target: { value: "18С3" } });
  expect(within(dialog).getByText(/Уедет партий: 1 — 18C3B/)).toBeInTheDocument();
  fireEvent.change(await within(dialog).findByLabelText("Стеллаж"), { target: { value: "20" } });
  fireEvent.change(within(dialog).getByLabelText("Полка"), { target: { value: "B" } });
  await waitFor(() => expect(within(dialog).getByLabelText("№ на полке")).toHaveValue(5));
  expect(within(dialog).getByText(/первый свободный № на полке 20B/)).toBeInTheDocument();
  expect(within(dialog).queryByLabelText("Другой склад")).toBeNull();
  fireEvent.click(within(dialog).getByRole("button", { name: "Переместить" }));

  await waitFor(() => expect(payload).toEqual({ warehouse: "ЦАР", source_location: "18C3", target_location: "20B5" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

it("moves the lot opened from its card: ГРН preset, no package field, other warehouse keeps the place", async () => {
  let payload: unknown;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stock" && !init?.method) return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
    if (path === "/api/stock/rack-layout") return json(layout);
    if (path === `/api/stock/batches/${lot.id}/move` && init?.method === "POST") {
      payload = JSON.parse(String(init.body));
      return json({ lot: { ...lot, version: 5, warehouse: "ЖУК" } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  fireEvent.click(screen.getAllByRole("button", { name: "Переместить" })[0]);
  const dialog = screen.getByRole("dialog", { name: "Переместить" });
  expect(within(dialog).getByLabelText("Партия")).toHaveTextContent("ГРН FRX.26.0000001");
  expect(within(dialog).getByLabelText("Партия")).toHaveTextContent("Разъём · 10 шт · ЦАР, место 1F4");
  expect(within(dialog).queryByLabelText("ГРН партии")).toBeNull();
  expect(within(dialog).queryByLabelText("Пакет")).toBeNull();
  fireEvent.click(within(dialog).getByRole("button", { name: "Переместить" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("Укажи новое место или склад.");
  fireEvent.change(within(dialog).getByLabelText("Другой склад"), { target: { value: "ЖУК" } });
  expect(within(dialog).getByText(/уедет на ЖУК целиком.*место останется прежним/)).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Переместить" }));

  await waitFor(() => expect(payload).toEqual({ version: 4, storage_location: "", warehouse: "ЖУК" }));
});

it("asks for the package only when one typed ГРН has several", async () => {
  let path_: string | undefined;
  const second = { ...lot, id: "40000000-0000-0000-0000-000000000002", package: "2", storage_location: "1F5" };
  const first = { ...lot, package: "1" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stock" && !init?.method) return json({ lots: [first, second], total: 2, truncated: 0, next_cursor: null });
    if (path === "/api/stock/rack-layout") return json(layout);
    if (path.endsWith("/move") && init?.method === "POST") {
      path_ = path;
      return json({ lot: second });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  await screen.findByRole("button", { name: "Показать партии (2)" });
  fireEvent.click(screen.getByRole("button", { name: "↔ Переместить" }));
  const dialog = screen.getByRole("dialog", { name: "Переместить" });
  fireEvent.click(within(dialog).getByRole("button", { name: "Одну партию (ГРН)" }));
  fireEvent.change(within(dialog).getByLabelText("ГРН партии"), { target: { value: "frx.26.0000001" } });
  fireEvent.change(within(dialog).getByLabelText("Пакет"), { target: { value: second.id } });
  expect(within(dialog).getByRole("status")).toHaveTextContent("Разъём · 10 шт · ЦАР, место 1F5");
  fireEvent.change(within(dialog).getByLabelText("Другой склад"), { target: { value: "ЖУК" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Переместить" }));

  await waitFor(() => expect(path_).toBe(`/api/stock/batches/${second.id}/move`));
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
  fireEvent.click(await screen.findByRole("button", { name: "✎ Правка" }));
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
  fireEvent.click(await screen.findByRole("button", { name: "✎ Правка" }));
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
  fireEvent.click(await screen.findByRole("button", { name: "✎ Правка" }));
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
  fireEvent.click(await screen.findByRole("button", { name: "+ Внести позиции" }));
  fireEvent.change(screen.getByLabelText("Место хранения для внесения"), { target: { value: "18A1" } });

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
  expect((await openMore()).queryByRole("menuitem", { name: "Журнал склада" })).not.toBeInTheDocument();
  unmount();

  render(<WarehousePage admin />);
  fireEvent.click((await openMore()).getByRole("menuitem", { name: "Журнал склада" }));
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
  expect(await screen.findByText("ГРН FRX.26.0000001")).toBeInTheDocument();
  expect(screen.getByText("Склад открыт вам только на просмотр.")).toBeInTheDocument();
  for (const name of ["+ Внести позиции", "↔ Переместить", "Стеллажи", "Типы", "Выгрузить", "Загрузить правки", "Лист перемещения", "− Списать", "✎ Правка"]) {
    expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
  }
  // Nothing is left for «Ещё» to hold, so the menu itself is gone; the read-only tools stay.
  expect(screen.queryByRole("button", { name: /^Ещё/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Обновить" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Таблица склада" })).toBeInTheDocument();
  // The page only reads (the stage-options probe is a read too).
  expect(requested.every((call) => call === "GET /api/stock" || call === "GET /api/stage-options")).toBe(true);
});

it("«История» on a lot opens a window with the journal of its ГРН only (administrator)", async () => {
  const requested: string[] = [];
  const at = "2026-09-01T10:00:00+00:00";
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.startsWith("/api/stock/journal")) {
      requested.push(path);
      return json({ rows: [
        { occurred_at: at, kind: "Списание", lot: "FRX.26.0000001", name: "Разъём", event: "2 шт · Выдача в производство", warehouse: "ЦАР", who: "Иванов" },
        { occurred_at: at, kind: "Приход", lot: "FRX.26.00000011", name: "Другое", event: "5 шт", warehouse: "ЦАР", who: "Петров" },
      ], total: 2, truncated: 0 });
    }
    return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
  });

  render(<WarehousePage admin />);
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  fireEvent.click(screen.getByRole("button", { name: "История" }));

  const dialog = within(await screen.findByRole("dialog", { name: "История" }));
  expect(await dialog.findByText("2 шт · Выдача в производство")).toBeInTheDocument();
  expect(dialog.queryByText("Петров")).toBeNull();
  expect(requested.some((path) => path.includes("FRX.26.0000001"))).toBe(true);
  fireEvent.click(dialog.getByRole("button", { name: "Закрыть" }));
  expect(screen.queryByRole("dialog", { name: "История" })).not.toBeInTheDocument();
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

it("keeps the daily actions in the row and the reference tools behind «Ещё ▾»", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lots: [lot], total: 1, truncated: 0, next_cursor: null }));

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  for (const name of ["+ Внести позиции", "↔ Переместить", "Лист перемещения", "Обновить"]) {
    expect(screen.getByRole("button", { name })).toBeInTheDocument();
  }
  // Not in the row any more, and not rendered while the menu is closed.
  for (const name of ["Стеллажи", "Типы", "Выгрузить", "Загрузить правки", "Журнал склада"]) {
    expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name })).not.toBeInTheDocument();
  }
  const trigger = screen.getByRole("button", { name: /^Ещё/ });
  expect(trigger).toHaveAttribute("aria-expanded", "false");

  const menu = await openMore();
  expect(trigger).toHaveAttribute("aria-expanded", "true");
  expect(menu.getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Стеллажи", "Типы", "Выгрузить", "Загрузить правки"]);
});

it("«Ещё ▾» holds «Журнал склада» too for the administrator", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lots: [lot], total: 1, truncated: 0, next_cursor: null }));

  render(<WarehousePage admin />);
  const menu = await openMore();
  expect(menu.getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Стеллажи", "Типы", "Выгрузить", "Загрузить правки", "Журнал склада"]);
});

it("a menu item runs its action and closes the menu; Escape and a tap outside close it as well", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lots: [lot], total: 1, truncated: 0, next_cursor: null }));

  render(<WarehousePage />);
  fireEvent.click((await openMore()).getByRole("menuitem", { name: "Загрузить правки" }));
  expect(screen.getByRole("region", { name: "Загрузить правки склада" })).toBeInTheDocument();
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();

  const trigger = screen.getByRole("button", { name: /^Ещё/ });
  await openMore();
  fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();

  await openMore();
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();

  // Pressing the trigger again closes it, too.
  await openMore();
  fireEvent.click(trigger);
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
});

it("the column the rows are grouped by is the group header, not a column of every row", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lots: [lot], total: 1, truncated: 0, next_cursor: null }));

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  // Grouped by series (the default): «Серия» is the header «LOT-2409 · 1».
  expect(screen.queryByRole("columnheader", { name: "Серия" })).not.toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Тип" })).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Группировка склада"), { target: { value: "type" } });
  expect(screen.getByRole("columnheader", { name: "Серия" })).toBeInTheDocument();
  expect(screen.queryByRole("columnheader", { name: "Тип" })).not.toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Группировка склада"), { target: { value: "" } });
  expect(screen.getByRole("columnheader", { name: "Серия" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Тип" })).toBeInTheDocument();
});

// Two lots of different articles, series, types and warehouses: the rows to sort and to pin a column on.
const second: WarehouseLot = { ...lot, id: "40000000-0000-0000-0000-000000000002", grn: "FRX.26.0000002", article: "AAA-01", name: "Винт", item_type: "Крепёж", series: "LOT-2410", storage_location: "2A1", balance: "40", available: "40", warehouse: "ЖУК" };

/** The headers of the table on screen, left to right (the sort arrow cut off). */
function headerNames(root: { getAllByRole: typeof screen.getAllByRole } = screen) {
  return root.getAllByRole("columnheader").map((header) => header.textContent?.replace(/\s*[▲▼]$/, ""));
}

function mockStock(lots: WarehouseLot[]) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lots, total: lots.length, truncated: 0, next_cursor: null }));
}

/** The chooser list of «Колонки», opened (it stays open while the viewer works the table). */
function openChooser() {
  if (!screen.queryByRole("group", { name: "Показывать колонки" })) fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  return within(screen.getByRole("group", { name: "Показывать колонки" }));
}

it("the default order is the one of the SUPPLYMAP stock view: тип, ГРН, остаток, партий, места, серия, артикул, наименование, склад", async () => {
  mockStock([lot]);

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  // Grouped by series (the default): «Серия» is the group header, not a column.
  expect(headerNames()).toEqual(["Тип", "ГРН", "Остаток", "Партий", "Места", "Артикул", "Наименование", "Склад"]);

  fireEvent.change(screen.getByLabelText("Группировка склада"), { target: { value: "" } });
  expect(headerNames()).toEqual(["Тип", "ГРН", "Остаток", "Партий", "Места", "Серия", "Артикул", "Наименование", "Склад"]);
});

it("«Колонки» switches the optional columns off and remembers the choice; the name stays", async () => {
  mockStock([lot]);

  const { unmount } = render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  const chooser = openChooser();
  expect(chooser.getByLabelText("Наименование")).toBeDisabled();
  expect(chooser.queryByLabelText("Серия")).not.toBeInTheDocument();
  fireEvent.click(chooser.getByLabelText("Места"));
  fireEvent.click(chooser.getByLabelText("Тип"));
  expect(headerNames()).toEqual(["ГРН", "Остаток", "Партий", "Артикул", "Наименование", "Склад"]);
  expect(screen.getByText("GHR-04V-S")).toBeInTheDocument();
  unmount();

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  expect(headerNames()).toEqual(["ГРН", "Остаток", "Партий", "Артикул", "Наименование", "Склад"]);
});

it("↑/↓ in «Колонки» move a column one step; the order is remembered in this browser", async () => {
  mockStock([lot]);

  const { unmount } = render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  const chooser = openChooser();
  // The first one cannot go up, the last one cannot go down.
  expect(chooser.getByRole("button", { name: "Выше: Тип" })).toBeDisabled();
  expect(chooser.getByRole("button", { name: "Ниже: Склад" })).toBeDisabled();

  fireEvent.click(chooser.getByRole("button", { name: "Ниже: Тип" }));
  expect(headerNames()).toEqual(["ГРН", "Тип", "Остаток", "Партий", "Места", "Артикул", "Наименование", "Склад"]);
  fireEvent.click(chooser.getByRole("button", { name: "Выше: Склад" }));
  fireEvent.click(chooser.getByRole("button", { name: "Выше: Склад" }));
  expect(headerNames()).toEqual(["ГРН", "Тип", "Остаток", "Партий", "Места", "Склад", "Артикул", "Наименование"]);
  // The list of the chooser follows the table, with the name in its place.
  expect(chooser.getAllByRole("listitem").map((item) => item.textContent?.replace(/[↑↓]/g, ""))).toEqual(["ГРН", "Тип", "Остаток", "Партий", "Места", "Склад", "Артикул", "Наименование"]);
  unmount();

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  expect(headerNames()).toEqual(["ГРН", "Тип", "Остаток", "Партий", "Места", "Склад", "Артикул", "Наименование"]);
});

it("a column moves past the one that is switched off, and the group column keeps its place meanwhile", async () => {
  mockStock([lot]);

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  const chooser = openChooser();
  fireEvent.click(chooser.getByLabelText("Партий"));
  // «Партий» is off, but still in the list: «Места» steps over it and over «Остаток» and «ГРН» to the front.
  fireEvent.click(chooser.getByRole("button", { name: "Выше: Места" }));
  fireEvent.click(chooser.getByRole("button", { name: "Выше: Места" }));
  fireEvent.click(chooser.getByRole("button", { name: "Выше: Места" }));
  fireEvent.click(chooser.getByRole("button", { name: "Выше: Места" }));
  expect(headerNames()).toEqual(["Места", "Тип", "ГРН", "Остаток", "Артикул", "Наименование", "Склад"]);

  // «Серия» is the group header now: grouping by type frees it, and it stands where the default order put it.
  fireEvent.change(screen.getByLabelText("Группировка склада"), { target: { value: "type" } });
  expect(headerNames()).toEqual(["Места", "ГРН", "Остаток", "Серия", "Артикул", "Наименование", "Склад"]);
});

it("the chooser of «Колонки» keeps the focus on the arrow that was pressed; Escape and a tap outside close it", async () => {
  mockStock([lot]);

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  const chooser = openChooser();
  const down = chooser.getByRole("button", { name: "Ниже: Тип" });
  down.focus();
  fireEvent.click(down);
  // The list is re-ordered under the finger: the same arrow of the same column is focused again.
  expect(chooser.getByRole("button", { name: "Ниже: Тип" })).toHaveFocus();

  const trigger = screen.getByRole("button", { name: "Колонки" });
  expect(trigger).toHaveAttribute("aria-expanded", "true");
  fireEvent.keyDown(chooser.getByRole("button", { name: "Ниже: Тип" }), { key: "Escape" });
  expect(screen.queryByRole("group", { name: "Показывать колонки" })).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
  expect(trigger).toHaveAttribute("aria-expanded", "false");

  openChooser();
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole("group", { name: "Показывать колонки" })).not.toBeInTheDocument();
});

it("«По умолчанию» puts the order and the shown columns back", async () => {
  mockStock([lot]);

  const { unmount } = render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  const chooser = openChooser();
  fireEvent.click(chooser.getByLabelText("Тип"));
  fireEvent.click(chooser.getByRole("button", { name: "Выше: Склад" }));
  expect(headerNames()).toEqual(["ГРН", "Остаток", "Партий", "Места", "Артикул", "Склад", "Наименование"]);

  fireEvent.click(chooser.getByRole("button", { name: "По умолчанию" }));
  expect(headerNames()).toEqual(["Тип", "ГРН", "Остаток", "Партий", "Места", "Артикул", "Наименование", "Склад"]);
  unmount();

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  expect(headerNames()).toEqual(["Тип", "ГРН", "Остаток", "Партий", "Места", "Артикул", "Наименование", "Склад"]);
});

it("a damaged or old saved set gives the default order; a column the saved order does not know is added", async () => {
  mockStock([lot]);
  // The key of the previous version (hidden columns only) is not read at all.
  window.localStorage.setItem("erp-table:stock-columns", JSON.stringify(["type", "grn"]));
  window.localStorage.setItem("erp-table:stock-columns:v2", "{not json");

  const { unmount } = render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  expect(headerNames()).toEqual(["Тип", "ГРН", "Остаток", "Партий", "Места", "Артикул", "Наименование", "Склад"]);
  unmount();

  window.localStorage.setItem("erp-table:stock-columns:v2", JSON.stringify({ grouped: { order: ["warehouse", "name", "bogus", "grn", "grn"], hidden: ["name", "type", "bogus"] } }));
  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  // The name cannot be hidden by a saved set; the unknown ones are dropped, the missing ones follow in the default order.
  expect(headerNames()).toEqual(["Склад", "Наименование", "ГРН", "Остаток", "Партий", "Места", "Артикул"]);
});

it("the first column stays in view when the table is scrolled sideways: Тип, or ГРН when the rows are grouped by type", async () => {
  mockStock([lot, second]);

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  const pinned = () => [...document.querySelectorAll(".stock-table .stock-pin")].map((cell) => cell.textContent?.replace(/\s*[▲▼]$/, ""));
  // Grouped by series: «Тип» leads, in the header and in both rows.
  expect(pinned()).toEqual(["Тип", "Комплектующие", "Крепёж"]);

  // Grouped by type: the column is the group header, ГРН leads.
  fireEvent.change(screen.getByLabelText("Группировка склада"), { target: { value: "type" } });
  expect(pinned()).toEqual(["ГРН", "FRX.26.0000001", "FRX.26.0000002"]);

  // The viewer's order decides: the pin follows the first column on screen.
  const chooser = openChooser();
  fireEvent.click(chooser.getByRole("button", { name: "Выше: Остаток" }));
  expect(pinned()).toEqual(["Остаток", "10 шт", "40 шт"]);
  fireEvent.click(chooser.getByLabelText("Остаток"));
  expect(pinned()).toEqual(["ГРН", "FRX.26.0000001", "FRX.26.0000002"]);
});

it("a click on a header still sorts the rows; the sort column keeps its sort after a reorder", async () => {
  mockStock([lot, second]);

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  fireEvent.change(screen.getByLabelText("Группировка склада"), { target: { value: "" } });
  const articles = () => [...document.querySelectorAll(".stock-article-row")].map((row) => within(row as HTMLElement).queryByText(/^(GHR-04V-S|AAA-01)$/)?.textContent);
  // «Артикул» ascending to begin with.
  expect(articles()).toEqual(["AAA-01", "GHR-04V-S"]);

  fireEvent.click(screen.getByRole("button", { name: /^Артикул/ }));
  expect(screen.getByRole("columnheader", { name: /^Артикул/ })).toHaveAttribute("aria-sort", "descending");
  expect(articles()).toEqual(["GHR-04V-S", "AAA-01"]);

  fireEvent.click(screen.getByRole("button", { name: /^Остаток/ }));
  expect(screen.getByRole("columnheader", { name: /^Остаток/ })).toHaveAttribute("aria-sort", "ascending");
  expect(articles()).toEqual(["GHR-04V-S", "AAA-01"]);

  // Moving the sorted column does not drop the sort.
  fireEvent.click(openChooser().getByRole("button", { name: "Ниже: Остаток" }));
  expect(headerNames()).toEqual(["Тип", "ГРН", "Партий", "Остаток", "Места", "Серия", "Артикул", "Наименование", "Склад"]);
  expect(screen.getByRole("columnheader", { name: /^Остаток/ })).toHaveAttribute("aria-sort", "ascending");
});

it("the lot table has its own order and set of columns; «Назначение» is off until asked for", async () => {
  mockStock([lot]);

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  // A change of the grouped table does not touch the lot table.
  fireEvent.click(openChooser().getByRole("button", { name: "Ниже: Тип" }));
  fireEvent.click(screen.getByRole("button", { name: "Таблица склада" }));
  const table = screen.getByRole("table", { name: "Таблица склада" });
  expect(headerNames(within(table))).toEqual(["Тип", "ГРН", "Остаток", "Доступно", "Место", "Серия", "Артикул", "Наименование", "Склад"]);

  const chooser = openChooser();
  fireEvent.click(chooser.getByLabelText("Назначение"));
  expect(headerNames(within(table))).toEqual(["Тип", "ГРН", "Остаток", "Доступно", "Место", "Серия", "Артикул", "Наименование", "Назначение", "Склад"]);
  expect(within(table).getByText("FRX.001")).toBeInTheDocument();
  fireEvent.click(chooser.getByRole("button", { name: "Выше: Назначение" }));
  fireEvent.click(chooser.getByRole("button", { name: "Выше: Назначение" }));
  expect(headerNames(within(table))).toEqual(["Тип", "ГРН", "Остаток", "Доступно", "Место", "Серия", "Назначение", "Артикул", "Наименование", "Склад"]);

  // Back to the article table: its own order is as the viewer left it.
  fireEvent.click(screen.getByRole("button", { name: "Остатки по позициям" }));
  expect(headerNames()).toEqual(["ГРН", "Тип", "Остаток", "Партий", "Места", "Артикул", "Наименование", "Склад"]);
});

it("a long name is cut to its column and carries the full text as a tooltip", async () => {
  const long = "Разъём вилка GHR-04V-S, 4 контакта, шаг 1.25 мм, угловой, на плату, позолоченный";
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lots: [{ ...lot, name: long }], total: 1, truncated: 0, next_cursor: null }));

  render(<WarehousePage />);
  const name = await screen.findByRole("button", { name: "Показать партии (1)" });
  expect(name).toHaveTextContent(long);
  expect(name).toHaveAttribute("title", long);
  expect(screen.getByText("GHR-04V-S").closest("td")).toHaveAttribute("title", "GHR-04V-S");
});

it("quick entry suggests already entered articles by article or name and copies the position", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stock" && !init?.method) return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
    if (path.startsWith("/api/stock/article-info")) return json({ info: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Внести позиции" }));
  const article = screen.getByRole("combobox", { name: "Артикул" });
  fireEvent.focus(article);
  expect(screen.queryByRole("listbox")).toBeNull();

  fireEvent.change(article, { target: { value: lot.name.slice(0, 4) } });
  const option = await screen.findByRole("option", { name: new RegExp(lot.article) });
  expect(option).toHaveTextContent(`${lot.article} — ${lot.name}`);
  fireEvent.mouseDown(option);
  expect(article).toHaveValue(lot.article);
  await waitFor(() => expect(screen.getByLabelText("Наименование")).toHaveValue(lot.name));

  fireEvent.change(article, { target: { value: `${lot.article}-NEW` } });
  expect(screen.getByLabelText("Наименование")).toHaveValue(lot.name);
});

function stageFetch(enabledKeys: string[]) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stage-options") {
      return json({ available: true, options: ["warehouse.word_search", "warehouse.strict_quantities"].map((key) => (
        { key, block: "Склад", title: key, before: "", after: "", enabled: enabledKeys.includes(key) })) });
    }
    if (path === "/api/stock" && !init?.method) return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
    throw new Error(`Unexpected request: ${path}`);
  });
}

it("makes the quantity inputs read-only and the search hint new when the stage options are on", async () => {
  stageFetch(["warehouse.word_search", "warehouse.strict_quantities"]);

  render(<WarehousePage />);
  expect(await screen.findByPlaceholderText("Слова в любом порядке: артикул, название, ГРН, место…")).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  fireEvent.click(await screen.findByRole("button", { name: "✎ Правка" }));
  fireEvent.click(screen.getByRole("button", { name: "Править строку" }));
  const form = within(screen.getByRole("form", { name: "Правка строки" }));
  await waitFor(() => expect(form.getByLabelText("Приход")).toHaveAttribute("readonly"));
  expect(form.getByLabelText("Списано")).toHaveAttribute("readonly");
  expect(form.getByLabelText("Резерв")).toHaveAttribute("readonly");
  expect(form.getByLabelText("Тип")).not.toHaveAttribute("readonly");
  expect(form.getByText(/меняются только движениями/)).toBeInTheDocument();
});

it("keeps the quantity inputs editable and the old search hint when the stage options are off", async () => {
  stageFetch([]);

  render(<WarehousePage />);
  expect(await screen.findByPlaceholderText("поиск: резистор 6-1 …")).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  fireEvent.click(await screen.findByRole("button", { name: "✎ Правка" }));
  fireEvent.click(screen.getByRole("button", { name: "Править строку" }));
  const form = within(screen.getByRole("form", { name: "Правка строки" }));
  expect(form.getByLabelText("Приход")).not.toHaveAttribute("readonly");
  expect(form.queryByText(/меняются только движениями/)).toBeNull();
});
