import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WarehousePage } from "./WarehousePage";
import type { WarehouseLot } from "./api";
import { capturePrintFrames } from "./testPrintFrames";

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

afterEach(() => { cleanup(); vi.restoreAllMocks(); window.localStorage.clear(); });

/** The reference tools of the page sit behind «Ещё ▾». */
async function openMore() {
  fireEvent.click(await screen.findByRole("button", { name: /^Ещё/ }));
  return within(screen.getByRole("menu"));
}

it("warns instead of printing when nothing is waiting to be put away", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.split("?")[0] === "/api/stock") return json({ lots: [], total: 0, truncated: 0, next_cursor: null });
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
  const printed = capturePrintFrames();
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.split("?")[0] === "/api/stock") return json({ lots: [], total: 0, truncated: 0, next_cursor: null });
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

  await waitFor(() => expect(printed.print).toHaveBeenCalled());
  const written = printed.html[0];
  expect(written).toContain("ЛИСТ ПЕРЕМЕЩЕНИЯ НА СКЛАД");
  expect(written).toContain("Разъём");
  expect(written).toContain("FRX.26.0000001");
  expect(written).toContain("Разложил:");
});

it("writes off stock with its optimistic version and stated reason", async () => {
  let writeoff: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path.split("?")[0] === "/api/stock") return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
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
    if (path.split("?")[0] === "/api/stock") return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
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
    if (path.split("?")[0] === "/api/stock") return json({ lots: [returned ? { ...written, version: 5, written_off: "1", balance: "9", available: "9" } : written], total: 1, truncated: 0, next_cursor: null });
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
    if (path.split("?")[0] === "/api/stock") return json({ lots: [written], total: 1, truncated: 0, next_cursor: null });
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
    if (path.split("?")[0] === "/api/stock") return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
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
    if (path.split("?")[0] === "/api/stock" && !init?.method) return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
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
    "/api/stock?sort=article&group=series&q=%D1%80%D0%B0%D0%B7%D1%8A%D1%91%D0%BC&zero=true&warehouse=%D0%A6%D0%90%D0%A0",
  ));
});

it("groups article rows by manufacturer series", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lots: [lot], total: 1, truncated: 0, next_cursor: null }));

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  fireEvent.change(screen.getByLabelText("Группировка склада"), { target: { value: "series" } });

  expect(screen.getByText(/^LOT-2409 · 1\sартикул$/)).toBeInTheDocument();
  expect(screen.getByText("GHR-04V-S")).toBeInTheDocument();
});

it("groups article rows by ГРН, packages of one ГРН under one header", async () => {
  const second = { ...lot, id: "40000000-0000-0000-0000-000000000002", package: "2", article: "GHR-06V-S" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lots: [lot, second], total: 2, truncated: 0, next_cursor: null }));

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  fireEvent.change(screen.getByLabelText("Группировка склада"), { target: { value: "grn" } });

  expect(screen.getByText(/^FRX\.26\.0000001 · 2\sартикула$/)).toBeInTheDocument();
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

const page2 = { ...lot, id: "40000000-0000-0000-0000-000000000002", grn: "FRX.26.0000002", balance: "5", available: "4" };

it("shows the first server page at once and joins the next page on «Показать ещё»", async () => {
  const paths: string[] = [];
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path !== "/api/stock/item-types" && !path.startsWith("/api/stock/totals") && !path.startsWith("/api/purchasing")) paths.push(path);
    if (path.split("?")[0] === "/api/stock" && !path.includes("after=")) return json({ lots: [lot], total: 2, truncated: 1, next_cursor: "cursor-1" });
    if (path === "/api/stock?after=cursor-1&sort=article&group=series") { await gate; return json({ lots: [page2], total: 2, truncated: 0, next_cursor: null }); }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);

  // The first page is on screen while the page2 was never asked for.
  expect(await screen.findByRole("button", { name: "Показать партии (1)" })).toBeInTheDocument();
  expect(paths).toEqual(["/api/stock?sort=article&group=series"]);
  expect(screen.getByRole("status")).toHaveTextContent("Показано 1 из 2");

  fireEvent.click(screen.getByRole("button", { name: "Показать ещё" }));
  expect(await screen.findByRole("button", { name: "Загружаем…" })).toBeDisabled();
  release();
  expect(await screen.findByRole("button", { name: "Показать партии (2)" })).toBeInTheDocument();
  expect(screen.getByText(/^14\b/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Показать ещё" })).toBeNull();
});

it("«Загрузить всё» reads every remaining page", async () => {
  const third = { ...lot, id: "40000000-0000-0000-0000-000000000003", grn: "FRX.26.0000003" };
  const paths: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path !== "/api/stock/item-types" && !path.startsWith("/api/stock/totals") && !path.startsWith("/api/purchasing")) paths.push(path);
    if (path.split("?")[0] === "/api/stock" && !path.includes("after=")) return json({ lots: [lot], total: 3, truncated: 2, next_cursor: "c1" });
    if (path === "/api/stock?after=c1&sort=article&group=series") return json({ lots: [page2], total: 3, truncated: 1, next_cursor: "c2" });
    if (path === "/api/stock?after=c2&sort=article&group=series") return json({ lots: [third], total: 3, truncated: 0, next_cursor: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "Загрузить всё" }));
  expect(await screen.findByRole("button", { name: "Показать партии (3)" })).toBeInTheDocument();
  expect(paths).toEqual(["/api/stock?sort=article&group=series", "/api/stock?after=c1&sort=article&group=series", "/api/stock?after=c2&sort=article&group=series"]);
});

it("shows the same group and article totals on page 1 as after «Загрузить всё», the numbers come from the server", async () => {
  const totals = {
    group: "series",
    groups: [{ group: "LOT-2409", articles: 1, balance: "15", reserved: "0", available: "14", lots: 2, units: ["шт"] }],
    articles: [{ group: "LOT-2409", article: "ghr-04v-s", balance: "15", reserved: "0", available: "14", lots: 2, units: ["шт"] }],
  };
  const requested: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    requested.push(path);
    if (path.startsWith("/api/stock/totals")) return json(totals);
    if (path.split("?")[0] === "/api/stock" && !path.includes("after=")) return json({ lots: [lot], total: 2, truncated: 1, next_cursor: "cursor-1" });
    if (path === "/api/stock?after=cursor-1&sort=article&group=series") return json({ lots: [page2], total: 2, truncated: 0, next_cursor: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  const header = await screen.findByText(/2\sпартии · остаток 15 шт/);
  expect(screen.getByText(/^LOT-2409 · 1\sартикул$/)).toBeInTheDocument();
  const row = screen.getByRole("button", { name: "Показать партии (1)" }).closest("tr")!;
  const cells = () => within(row).getAllByRole("cell").map((cell) => cell.textContent);
  expect(cells()).toContain("14 шт");           // 10 loaded, 14 free on the shelf: the server number, not the loaded sum
  expect(cells()).toContain("2");
  expect(screen.getByRole("status")).not.toHaveTextContent("по показанным");
  const sums = (texts: Array<string | null>) => texts.filter((text) => text === "14 шт" || text === "2");
  const onPageOne = [header.textContent, sums(cells())];

  fireEvent.click(screen.getByRole("button", { name: "Загрузить всё" }));
  await screen.findByRole("button", { name: "Показать партии (2)" });
  expect(screen.getByText(/2\sпартии · остаток 15 шт/).textContent).toBe(onPageOne[0]);
  expect(sums(within(screen.getByRole("button", { name: "Показать партии (2)" }).closest("tr")!).getAllByRole("cell").map((cell) => cell.textContent))).toEqual(onPageOne[1]);
  expect(requested.filter((path) => path.startsWith("/api/stock/totals")).length).toBeGreaterThan(0);
});

it("updates only the edited row after a write-off and does not re-read the list", async () => {
  const paths: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path !== "/api/stock/item-types") paths.push(`${init?.method ?? "GET"} ${path}`);
    if (path.split("?")[0] === "/api/stock") return json({ lots: [lot, page2], total: 2, truncated: 0, next_cursor: null });
    if (path.endsWith("/writeoff")) return json({ lot: { ...lot, version: 5, written_off: "2", balance: "8", available: "8" } });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (2)" }));
  fireEvent.click((await screen.findAllByRole("button", { name: "− Списать" }))[0]);
  fireEvent.change(await screen.findByLabelText("Количество"), { target: { value: "2" } });
  fireEvent.change(screen.getByLabelText("Назначение списания"), { target: { value: "FRX.001" } });
  fireEvent.change(screen.getByLabelText("Основание"), { target: { value: "Выдача в производство" } });
  fireEvent.click(screen.getByRole("button", { name: "Списать" }));

  await waitFor(() => expect(paths.some((entry) => entry.endsWith("/writeoff"))).toBe(true));
  await waitFor(() => expect(screen.getByText("8 шт", { exact: false })).toBeInTheDocument());
  expect(paths.filter((entry) => entry === "GET /api/stock?sort=article&group=series")).toHaveLength(1);
});

const layout = { layout: { racks: 20, shelves: 6, perRack: {} } };

it("moves a whole storage location, the target picked from the rack layout with the first free №", async () => {
  let payload: unknown;
  const onShelf = { ...lot, id: "40000000-0000-0000-0000-000000000009", grn: "FRX.26.0000009", storage_location: "18C3B" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path.split("?")[0] === "/api/stock" && !init?.method) return json({ lots: [lot, onShelf], total: 2, truncated: 0, next_cursor: null });
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
    if (path.split("?")[0] === "/api/stock" && !init?.method) return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
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
    if (path.split("?")[0] === "/api/stock" && !init?.method) return json({ lots: [first, second], total: 2, truncated: 0, next_cursor: null });
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
    if (path.split("?")[0] === "/api/stock" && !init?.method) return json({ lots: [unplaced], total: 1, truncated: 0, next_cursor: null });
    if (path.endsWith("/location") && init?.method === "PUT") {
      payload = JSON.parse(String(init.body));
      return json({ lot: { ...lot, version: 5, storage_location: "A-01" } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<WarehousePage />);
  fireEvent.change(await screen.findByLabelText("Группировка склада"), { target: { value: "place" } });
  expect(await screen.findByText(/^Без места · 1\sартикул$/)).toBeInTheDocument();
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
    if (path.split("?")[0] === "/api/stock" && !init?.method) return json({ lots: [stocked], total: 1, truncated: 0, next_cursor: null });
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
    if (path.split("?")[0] === "/api/stock") return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
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
  // The page only reads.
  expect(requested.every((call) => call === "GET /api/stock?sort=article&group=series" || call.startsWith("GET /api/stock/totals") || call.startsWith("GET /api/purchasing/items/for-lots"))).toBe(true);
});

it("«История» is on a lot for the storekeeper too, not for a view-only user (owner 12.10.2026)", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lots: [lot], total: 1, truncated: 0, next_cursor: null }));
  const { unmount } = render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  expect(screen.getByRole("button", { name: "История" })).toBeInTheDocument();
  unmount();

  render(<WarehousePage readOnly />);
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  expect(screen.queryByRole("button", { name: "История" })).not.toBeInTheDocument();
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

it("«Ещё ▾» holds «Журнал склада» and «Распознавание УПД» too for the administrator", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lots: [lot], total: 1, truncated: 0, next_cursor: null }));

  render(<WarehousePage admin />);
  const menu = await openMore();
  expect(menu.getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Стеллажи", "Типы", "Выгрузить", "Загрузить правки", "Журнал склада", "Распознавание УПД"]);
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
  expect(headerNames()).toEqual(["Тип", "ГРН", "Свободно", "Резерв", "Партий", "Места", "Артикул", "Наименование", "Склад"]);

  fireEvent.change(screen.getByLabelText("Группировка склада"), { target: { value: "" } });
  expect(headerNames()).toEqual(["Тип", "ГРН", "Свободно", "Резерв", "Партий", "Места", "Серия", "Артикул", "Наименование", "Склад"]);
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
  expect(headerNames()).toEqual(["ГРН", "Свободно", "Резерв", "Партий", "Артикул", "Наименование", "Склад"]);
  expect(screen.getByText("GHR-04V-S")).toBeInTheDocument();
  unmount();

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  expect(headerNames()).toEqual(["ГРН", "Свободно", "Резерв", "Партий", "Артикул", "Наименование", "Склад"]);
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
  expect(headerNames()).toEqual(["ГРН", "Тип", "Свободно", "Резерв", "Партий", "Места", "Артикул", "Наименование", "Склад"]);
  fireEvent.click(chooser.getByRole("button", { name: "Выше: Склад" }));
  fireEvent.click(chooser.getByRole("button", { name: "Выше: Склад" }));
  expect(headerNames()).toEqual(["ГРН", "Тип", "Свободно", "Резерв", "Партий", "Места", "Склад", "Артикул", "Наименование"]);
  // The list of the chooser follows the table, with the name in its place.
  expect(chooser.getAllByRole("listitem").map((item) => item.textContent?.replace(/[↑↓]/g, ""))).toEqual(["ГРН", "Тип", "Свободно", "Резерв", "Остаток", "Партий", "Места", "Склад", "Артикул", "Наименование"]);
  unmount();

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  expect(headerNames()).toEqual(["ГРН", "Тип", "Свободно", "Резерв", "Партий", "Места", "Склад", "Артикул", "Наименование"]);
});

it("a column moves past the one that is switched off, and the group column keeps its place meanwhile", async () => {
  mockStock([lot]);

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  const chooser = openChooser();
  fireEvent.click(chooser.getByLabelText("Партий"));
  // «Партий» is off, but still in the list: «Места» steps over it and over «Доступно», «Резерв», «Остаток» and «ГРН» to the front.
  for (let step = 0; step < 6; step += 1) fireEvent.click(chooser.getByRole("button", { name: "Выше: Места" }));
  expect(headerNames()).toEqual(["Места", "Тип", "ГРН", "Свободно", "Резерв", "Артикул", "Наименование", "Склад"]);

  // «Серия» is the group header now: grouping by type frees it, and it stands where the default order put it.
  fireEvent.change(screen.getByLabelText("Группировка склада"), { target: { value: "type" } });
  expect(headerNames()).toEqual(["Места", "ГРН", "Свободно", "Резерв", "Серия", "Артикул", "Наименование", "Склад"]);
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
  expect(headerNames()).toEqual(["ГРН", "Свободно", "Резерв", "Партий", "Места", "Артикул", "Склад", "Наименование"]);

  fireEvent.click(chooser.getByRole("button", { name: "По умолчанию" }));
  expect(headerNames()).toEqual(["Тип", "ГРН", "Свободно", "Резерв", "Партий", "Места", "Артикул", "Наименование", "Склад"]);
  unmount();

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  expect(headerNames()).toEqual(["Тип", "ГРН", "Свободно", "Резерв", "Партий", "Места", "Артикул", "Наименование", "Склад"]);
});

it("a damaged or old saved set gives the default order; a column the saved order does not know is added", async () => {
  mockStock([lot]);
  // The key of the previous version (hidden columns only) is not read at all.
  window.localStorage.setItem("erp-table:stock-columns", JSON.stringify(["type", "grn"]));
  window.localStorage.setItem("erp-table:stock-columns:v3", "{not json");

  const { unmount } = render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  expect(headerNames()).toEqual(["Тип", "ГРН", "Свободно", "Резерв", "Партий", "Места", "Артикул", "Наименование", "Склад"]);
  unmount();

  window.localStorage.setItem("erp-table:stock-columns:v3", JSON.stringify({ grouped: { order: ["warehouse", "name", "bogus", "grn", "grn"], hidden: ["name", "type", "bogus"] } }));
  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  // The name cannot be hidden by a saved set; the unknown ones are dropped, the missing ones follow in the default order.
  expect(headerNames()).toEqual(["Склад", "Наименование", "ГРН", "Свободно", "Резерв", "Партий", "Места", "Артикул"]);
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
  fireEvent.click(chooser.getByRole("button", { name: "Выше: Свободно" }));
  expect(pinned()).toEqual(["Свободно", "10 шт", "40 шт"]);
  fireEvent.click(chooser.getByLabelText("Свободно"));
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

  fireEvent.click(screen.getByRole("button", { name: /^Свободно/ }));
  expect(screen.getByRole("columnheader", { name: /^Свободно/ })).toHaveAttribute("aria-sort", "ascending");
  expect(articles()).toEqual(["GHR-04V-S", "AAA-01"]);

  // Moving the sorted column does not drop the sort.
  fireEvent.click(openChooser().getByRole("button", { name: "Ниже: Свободно" }));
  expect(headerNames()).toEqual(["Тип", "ГРН", "Резерв", "Свободно", "Партий", "Места", "Серия", "Артикул", "Наименование", "Склад"]);
  expect(screen.getByRole("columnheader", { name: /^Свободно/ })).toHaveAttribute("aria-sort", "ascending");
});

it("the lot table has its own order and set of columns; «Назначение» is off until asked for", async () => {
  mockStock([lot]);

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  // A change of the grouped table does not touch the lot table.
  fireEvent.click(openChooser().getByRole("button", { name: "Ниже: Тип" }));
  fireEvent.click(screen.getByRole("button", { name: "Таблица склада" }));
  const table = screen.getByRole("table", { name: "Таблица склада" });
  expect(headerNames(within(table))).toEqual(["Тип", "ГРН", "Свободно", "Резерв", "Место", "Серия", "Артикул", "Наименование", "Склад"]);

  const chooser = openChooser();
  fireEvent.click(chooser.getByLabelText("Назначение"));
  expect(headerNames(within(table))).toEqual(["Тип", "ГРН", "Свободно", "Резерв", "Место", "Серия", "Артикул", "Наименование", "Назначение", "Склад"]);
  expect(within(table).getByText("FRX.001")).toBeInTheDocument();
  fireEvent.click(chooser.getByRole("button", { name: "Выше: Назначение" }));
  fireEvent.click(chooser.getByRole("button", { name: "Выше: Назначение" }));
  expect(headerNames(within(table))).toEqual(["Тип", "ГРН", "Свободно", "Резерв", "Место", "Серия", "Назначение", "Артикул", "Наименование", "Склад"]);

  // Back to the article table: its own order is as the viewer left it.
  fireEvent.click(screen.getByRole("button", { name: "Остатки по позициям" }));
  expect(headerNames()).toEqual(["ГРН", "Тип", "Свободно", "Резерв", "Партий", "Места", "Артикул", "Наименование", "Склад"]);
});

const kitHolder = { kind: "kit", label: "REQ.26.0000012 · 719К.2 КБ12", quantity: "100", unit: "шт" };
const reservedLot: WarehouseLot = { ...lot, reserved: "100", available: "0", balance: "100", reserve_holders: [kitHolder] };
const reservedSecond: WarehouseLot = { ...reservedLot, id: "40000000-0000-0000-0000-000000000003", grn: "FRX.26.0000003", reserved: "50", available: "10", balance: "60", reserve_holders: [{ ...kitHolder, label: "REQ.26.0000013 · 720К.1", quantity: "50" }] };

it("«Резерв» stands after «Остаток» in both tables: the sum per article, the holders in the tooltip, a dim dash when nothing is held", async () => {
  mockStock([reservedLot, reservedSecond, { ...second, reserved: "0" }]);

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  const rows = () => [...document.querySelectorAll(".stock-article-row")] as HTMLElement[];
  const cell = (row: HTMLElement) => row.querySelectorAll("td")[3];
  // Article table: ГРН · Свободно · Резерв, the two lots of GHR-04V-S add up.
  const held = rows().find((row) => within(row).queryByText("GHR-04V-S"))!;
  expect(cell(held)).toHaveTextContent("150 шт");
  expect(cell(held)).toHaveAttribute("title", "REQ.26.0000012 · 719К.2 КБ12 — 100 шт\nREQ.26.0000013 · 720К.1 — 50 шт");
  const free = rows().find((row) => within(row).queryByText("AAA-01"))!;
  expect(cell(free)).toHaveTextContent("—");
  expect(cell(free)).toHaveClass("stock-dim");

  // The group header says it, too.
  expect(screen.getByText(/свободно 10 шт \(остаток 160, резерв 150\)/)).toBeInTheDocument();
  // The free stock leads: «Свободно» is the first quantity, the raw balance is in its tooltip.
  expect(cell(held).previousElementSibling).toHaveTextContent("10 шт");
  expect(cell(held).previousElementSibling).toHaveAttribute("title", "остаток 160 шт · резерв 150 шт");

  // Lot table: the reserve of one lot, right after «Остаток».
  fireEvent.click(screen.getByRole("button", { name: "Таблица склада" }));
  const table = within(screen.getByRole("table", { name: "Таблица склада" }));
  expect(headerNames(table).slice(2, 4)).toEqual(["Свободно", "Резерв"]);
  expect(screen.getByRole("columnheader", { name: "Свободно" })).toHaveAttribute("title", "остаток минус резерв");
  const lotRow = table.getByText("FRX.26.0000003").closest("tr")!;
  expect(within(lotRow).getByText("50 шт")).toHaveAttribute("title", "REQ.26.0000013 · 720К.1 — 50 шт");
  expect(within(lotRow).getByText("10 шт").closest("td")!).toHaveAttribute("title", "остаток 60 шт · резерв 50 шт");
});

it("a lot card leads with the free quantity; the balance and the reserve are the small line", async () => {
  mockStock([reservedSecond]);

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  const card = within(screen.getByRole("article", { name: "Партия FRX.26.0000003" }));
  expect(card.getAllByText("10 шт")[0]).toHaveClass("lot-card-big");
  expect(card.getByText("свободно · остаток 60 · резерв 50")).toBeInTheDocument();
});

it("the reserve of an article, of a group and of the page head come from the server sums, not from the loaded lots", async () => {
  const totals = {
    group: "series",
    groups: [{ group: "LOT-2409", articles: 1, balance: "300", reserved: "180", available: "120", lots: 4, units: ["шт"] }],
    articles: [{ group: "LOT-2409", article: "ghr-04v-s", balance: "300", reserved: "180", available: "120", lots: 4, units: ["шт"] }],
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.startsWith("/api/stock/totals")) return json(totals);
    return json({ lots: [reservedLot], total: 4, truncated: 3, next_cursor: "cursor-1" });
  });

  render(<WarehousePage />);
  expect(await screen.findByText("в резерве 180 шт · свободно 120 шт")).toBeInTheDocument();
  expect(await screen.findByText(/свободно 120 шт \(остаток 300, резерв 180\)/)).toBeInTheDocument();
  expect(document.querySelectorAll(".stock-article-row td")[3]).toHaveTextContent("180 шт");
});

it("the article table sorts by «Резерв» and «Свободно»; «Остаток» is an optional column there", async () => {
  mockStock([reservedLot, { ...second, reserved: "0" }]);

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  expect(screen.queryByRole("columnheader", { name: "Остаток" })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Группировка склада"), { target: { value: "" } });
  const articles = () => [...document.querySelectorAll(".stock-article-row")].map((row) => within(row as HTMLElement).queryByText(/^(GHR-04V-S|AAA-01)$/)?.textContent);
  fireEvent.click(screen.getByRole("button", { name: /^Резерв/ }));
  expect(articles()).toEqual(["AAA-01", "GHR-04V-S"]);
  fireEvent.click(screen.getByRole("button", { name: /^Резерв/ }));
  expect(articles()).toEqual(["GHR-04V-S", "AAA-01"]);

  fireEvent.click(screen.getByRole("button", { name: /^Свободно/ }));
  expect(articles()).toEqual(["GHR-04V-S", "AAA-01"]);
  fireEvent.click(screen.getByRole("button", { name: /^Свободно/ }));
  expect(articles()).toEqual(["AAA-01", "GHR-04V-S"]);

  fireEvent.click(openChooser().getByLabelText("Остаток"));
  expect(headerNames()).toContain("Остаток");
});

it("a saved set that does not know «Резерв» or «Свободно» gets them next to their neighbours of the default order", async () => {
  mockStock([lot]);
  window.localStorage.setItem("erp-table:stock-columns:v3", JSON.stringify({
    grouped: { order: ["grn", "balance", "lots", "place", "series", "article", "name", "warehouse", "type"], hidden: ["type"] },
    flat: { order: ["warehouse", "balance", "available", "name", "grn"], hidden: ["purpose"] },
  }));

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  expect(headerNames()).toEqual(["ГРН", "Свободно", "Резерв", "Остаток", "Партий", "Места", "Артикул", "Наименование", "Склад"]);
  fireEvent.click(screen.getByRole("button", { name: "Таблица склада" }));
  expect(headerNames(within(screen.getByRole("table", { name: "Таблица склада" })))).toEqual(["Склад", "Остаток", "Место", "Серия", "Артикул", "Свободно", "Резерв", "Наименование", "ГРН", "Тип"]);
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

it("asks the server to sort the whole stock when a column is clicked, from the first page", async () => {
  const paths: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    paths.push(path);
    if (path.split("?")[0] === "/api/stock") return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
    return json({ group: "series", groups: [], articles: [] });
  });

  render(<WarehousePage />);
  await screen.findByText("GHR-04V-S");
  fireEvent.change(screen.getByLabelText("Группировка склада"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: /^ГРН/ }));
  fireEvent.click(screen.getByRole("button", { name: /^ГРН/ }));

  await waitFor(() => expect(paths).toContain("/api/stock?sort=grn&desc=true"));
});

function lotFetch() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path.split("?")[0] === "/api/stock" && !init?.method) return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
    throw new Error(`Unexpected request: ${path}`);
  });
}

it("lets the quantities be edited by hand again (owner 23.10) and shows the new search hint", async () => {
  lotFetch();

  render(<WarehousePage />);
  expect(await screen.findByPlaceholderText("Скан, ГРН, место или слова: артикул, название…")).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  fireEvent.click(await screen.findByRole("button", { name: "✎ Правка" }));
  fireEvent.click(screen.getByRole("button", { name: "Править строку" }));
  const form = within(screen.getByRole("form", { name: "Правка строки" }));
  await waitFor(() => expect(form.getByLabelText("Приход")).not.toHaveAttribute("readonly"));
  expect(form.getByLabelText("Списано")).not.toHaveAttribute("readonly");
  expect(form.getByLabelText("Резерв")).not.toHaveAttribute("readonly");
  expect(form.queryByText(/меняются только движениями/)).not.toBeInTheDocument();
});

function itemsFetch(calls: string[], posted: string[] = []) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    calls.push(path);
    if (path.split("?")[0] === "/api/stock" && !init?.method) return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
    if (path.startsWith("/api/purchasing/items/for-lots")) {
      return json({ [lot.id]: { item_id: "i1", name: "Разъём SMA (номенклатура)", category: "connector", via: "chain" } });
    }
    if (path.startsWith("/api/purchasing/items?")) {
      return json({ items: [{ id: "i2", name: "Другая позиция", part_number: null, unit: "шт" }], counts: {}, total: 1, page: 1, page_size: 100 });
    }
    if (path === "/api/purchasing/items/link-lot") { posted.push(String(init?.body)); return json({}); }
    throw new Error(`Unexpected request: ${path}`);
  });
}

it("shows the nomenclature item under the lot and links a lot by hand", async () => {
  const calls: string[] = [];
  const posted: string[] = [];
  itemsFetch(calls, posted);
  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  expect(await screen.findByText(/Позиция: Разъём SMA \(номенклатура\)/)).toBeInTheDocument();
  expect(calls.filter((path) => path.startsWith("/api/purchasing/items/for-lots"))).toEqual([`/api/purchasing/items/for-lots?ids=${lot.id}`]);
  fireEvent.click(screen.getByRole("button", { name: "Позиция номенклатуры…" }));
  fireEvent.change(screen.getByLabelText("Найти позицию номенклатуры"), { target: { value: "друг" } });
  fireEvent.click(await screen.findByRole("button", { name: "Связать" }));
  await waitFor(() => expect(posted).toEqual([JSON.stringify({ lot_id: lot.id, item_id: "i2" })]));
});

it("shows the supply a lot came with on its card, leaving empty parts out", async () => {
  const supplied: WarehouseLot = {
    ...lot,
    receipt: { id: "r1", supplier: "ООО Ромашка", invoice_number: "С-7", invoice_date: "2026-10-02", upd_number: "", upd_date: "2026-10-01" },
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ lots: [supplied], total: 1, truncated: 0, next_cursor: null }));

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  expect(await screen.findByText("Приход: ООО Ромашка · УПД от 01.10.2026 · счёт С-7 от 02.10.2026")).toBeInTheDocument();
});
