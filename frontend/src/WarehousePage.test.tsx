import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => vi.restoreAllMocks());

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
  fireEvent.change(screen.getByLabelText("Основание"), {
    target: { value: "Выдача в производство" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Списать" }));

  await waitFor(() => expect(writeoff).toEqual({
    version: 4,
    quantity: "2",
    reason: "Выдача в производство",
  }));
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
      return json({ lots: [lot] });
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
