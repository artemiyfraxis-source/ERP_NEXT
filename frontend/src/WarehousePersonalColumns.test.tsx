import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WarehousePage } from "./WarehousePage";
import { DataTable } from "./DataTable";
import { agreeing, readLayout } from "./WarehouseColumns";
import { forgetPersonalViews } from "./personalViews";
import type { WarehouseLot } from "./api";

/* Owner 26.10.2026: «добавить сами столбцы, и настройка показываемых столбцов у каждого своя». The positions table
   offers «Производитель» and the document columns; a layout is the person's own, on the server. */

const lot: WarehouseLot = {
  id: "41000000-0000-0000-0000-000000000001", version: 1, warehouse: "ЦАР", grn: "OLD.26.0000069", package: "",
  article: "SMA-KWE-RG402", series: "SMA", name: "Разъём SMA вилка под RG-402", item_type: "Коаксиальный соединитель",
  purpose: "Склад", unit: "шт", storage_location: "16H5A", inbound: "40", written_off: "0", reserved: "0", balance: "40",
  available: "40", source_receiving_ids: [], manufacturer: "Amphenol RF", upd_name: "Соединитель SMA-J RG402",
  receipt: { id: "r1", supplier: "Амел", invoice_number: "", invoice_date: null, upd_number: "125", upd_date: "2026-10-20", scan_file: "/files/upd-1.pdf" },
};
const other: WarehouseLot = {
  ...lot, id: "41000000-0000-0000-0000-000000000002", grn: "OLD.26.0000070", storage_location: "16H5B", manufacturer: "Rosenberger",
  upd_name: "SMA вилка RG-402 обжим", balance: "86", available: "86",
  receipt: { ...lot.receipt!, id: "r2", upd_number: "125", scan_file: "/files/upd-2.pdf" },
};
const untyped: WarehouseLot = { ...lot, id: "41000000-0000-0000-0000-000000000003", grn: "OLD.26.0000071", article: "CAP-1", name: "Колпачок", item_type: "", manufacturer: "" };

type Call = { method: string; path: string; body: unknown };

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** The stock answers with `lots`; /api/auth/ui-settings with `views` (null — the server is out of reach). */
function mockServer(lots: WarehouseLot[], views: Record<string, unknown> | null = {}) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    if (path.startsWith("/api/auth/ui-settings")) {
      calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (views === null) return json({ detail: "Нет связи" }, 503);
      if (method === "DELETE") return new Response(null, { status: 204 });
      if (method === "PUT") return json({ view: path.split("/").pop(), value: (JSON.parse(String(init!.body)) as { value: unknown }).value });
      return json({ views });
    }
    if (path.startsWith("/api/stock/totals")) return json({ group: "series", groups: [], articles: [] });
    return json({ lots, total: lots.length, truncated: 0, next_cursor: null });
  });
  return calls;
}

const writes = (calls: Call[]) => calls.filter((call) => call.method !== "GET");

function headerNames() {
  return screen.getAllByRole("columnheader").map((header) => header.textContent?.replace(/\s*[▲▼]$/, ""));
}

function openChooser() {
  if (!screen.queryByRole("group", { name: "Показывать колонки" })) fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  return within(screen.getByRole("group", { name: "Показывать колонки" }));
}

const row = (text: string) => screen.getByText(text).closest("tr") as HTMLElement;

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); window.localStorage.clear(); forgetPersonalViews(); });

it("a position shows the value its lots agree on, else «N разн.» with every value in the tooltip", () => {
  expect(agreeing(["Amphenol", "Amphenol", ""])).toEqual({ text: "Amphenol", title: "Amphenol", many: false });
  expect(agreeing(["Amphenol", " Rosenberger ", "Amphenol"])).toEqual({
    text: "2 разн.", title: "У партий разные значения:\nAmphenol\nRosenberger", many: true,
  });
  expect(agreeing(["", "  "])).toEqual({ text: "", many: false });
  expect(agreeing(["Проект А", "Проект Б"])).toEqual({ text: "2 разн.", title: "У партий разные значения:\nПроект А\nПроект Б", many: true });
});

it("offers purpose hidden by default and preserves that default for older layouts", async () => {
  const layout = readLayout("grouped", { order: ["name", "warehouse"], hidden: [] });
  expect(layout.order.indexOf("purpose")).toBe(layout.order.indexOf("name") + 1);
  expect(layout.hidden).toContain("purpose");
  mockServer([lot, { ...other, purpose: "Иванов" }]);
  render(<WarehousePage />);
  await screen.findAllByText("SMA-KWE-RG402");
  const chooser = openChooser();
  expect(chooser.getByLabelText("Назначение")).not.toBeChecked();
  expect(headerNames()).not.toContain("Назначение");
  fireEvent.click(chooser.getByLabelText("Назначение"));
  expect(headerNames()).toContain("Назначение");
  expect(within(row("OLD.26.0000069")).getByText("Склад")).toBeInTheDocument();
  expect(within(row("OLD.26.0000070")).getByText("Иванов")).toBeInTheDocument();
});

it("the positions table offers the maker and the documents in «Колонки», off until switched on", async () => {
  mockServer([lot, other, untyped]);
  render(<WarehousePage />);
  await screen.findAllByText("SMA-KWE-RG402");
  const chooser = openChooser();
  for (const title of ["Производитель", "Наим. по УПД", "№ УПД", "Дата УПД", "Файл УПД"]) {
    expect(chooser.getByLabelText(title)).not.toBeChecked();
  }
  for (const title of ["Наим. по счёту", "№ счёта", "Дата счёта", "№ ПРД", "Дата ПРД"]) {
    expect(chooser.queryByLabelText(title)).not.toBeInTheDocument();
  }
  expect(headerNames()).not.toContain("Производитель");

  fireEvent.click(chooser.getByLabelText("Производитель"));
  fireEvent.click(chooser.getByLabelText("Наим. по УПД"));
  fireEvent.click(chooser.getByLabelText("№ УПД"));
  fireEvent.click(chooser.getByLabelText("Файл УПД"));
  expect(headerNames()).toEqual(expect.arrayContaining(["Производитель", "Наим. по УПД", "№ УПД", "Файл УПД"]));

  // A row is one lot (owner 27.10.2026): each lot of SMA-KWE-RG402 shows its own maker, УПД and file.
  const first = row("OLD.26.0000069");
  const second = row("OLD.26.0000070");
  expect(within(first).getByText("Amphenol RF")).toBeInTheDocument();
  expect(within(second).getByText("Rosenberger")).toBeInTheDocument();
  expect(within(first).getByText("125")).toBeInTheDocument();
  expect(within(first).getByRole("link", { name: "Файл УПД партии OLD.26.0000069" })).toHaveAttribute("href", "/files/upd-1.pdf");
  expect(within(second).getByRole("link", { name: "Файл УПД партии OLD.26.0000070" })).toHaveAttribute("href", "/files/upd-2.pdf");
  // A lot without a maker: a dim dash.
  const cap = row("CAP-1");
  expect(within(cap).getAllByText("—").some((cell) => cell.classList.contains("stock-dim"))).toBe(true);
  // The maker and the documents sort the whole stock on the server like every other column (owner 28.10.2026);
  // «Файл УПД» is a link, not a value to order by.
  expect(screen.getByRole("button", { name: /^Производитель/ })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Файл УПД/ })).not.toBeInTheDocument();
});

it("a lot per row: its own ГРН, no «+1»; «Тип» truncates with the full title; an empty type is «—»", async () => {
  mockServer([lot, other, untyped]);
  render(<WarehousePage />);
  await screen.findAllByText("SMA-KWE-RG402");
  fireEvent.change(screen.getByLabelText("Группировка склада"), { target: { value: "" } });
  await waitFor(() => expect(screen.getByRole("columnheader", { name: "Тип" })).toBeInTheDocument());
  const position = row("OLD.26.0000069");
  const grn = within(position).getByText("OLD.26.0000069").closest("td")!;
  expect(grn).toHaveTextContent(/^OLD\.26\.0000069$/);
  const type = within(position).getByText("Коаксиальный соединитель");
  expect(type.closest("td")).toHaveClass("cut");
  expect(type.closest("td")).toHaveAttribute("title", "Коаксиальный соединитель");
  expect(type.closest("td")).not.toHaveClass("stock-wrap");
  const cap = row("CAP-1");
  expect(cap.querySelector("td")).toHaveTextContent("—");
  expect(cap.querySelector("td")).toHaveClass("stock-dim");
  fireEvent.click(openChooser().getByLabelText("Производитель"));
  for (const text of [lot.series, lot.manufacturer!]) {
    const cell = within(position).getByText(text).closest("td");
    expect(cell).toHaveClass("cut");
    expect(cell).toHaveAttribute("title", text);
  }
  fireEvent.click(screen.getByRole("button", { name: "Таблица склада" }));
  fireEvent.click(openChooser().getByLabelText("Производитель"));
  for (const text of [lot.item_type, lot.series, lot.manufacturer!]) {
    const cell = within(row(lot.grn)).getByText(text).closest("td");
    expect(cell).toHaveClass("cut");
    expect(cell).toHaveAttribute("title", text);
  }
});

it("the layout is the person's own: read from the server, a change is saved there", async () => {
  const calls = mockServer([lot], { "stock.grouped": { order: ["name", "maker", "grn", "available"], hidden: ["type", "reserved", "lots", "place", "article", "warehouse", "balance"] } });
  render(<WarehousePage />);
  await screen.findByText("Amphenol RF");
  // The stock stays in the first screen: «Свободно» and «Места» come just before the name, whatever the saved order.
  expect(headerNames()).toEqual(["Свободно", "Места", "Наименование", "Производитель", "ГРН"]);

  fireEvent.click(openChooser().getByLabelText("Склад"));
  await waitFor(() => expect(writes(calls)).toHaveLength(1), { timeout: 2000 });
  const [put] = writes(calls);
  expect(put.method).toBe("PUT");
  expect(put.path).toBe("/api/auth/ui-settings/stock.grouped");
  const saved = (put.body as { value: { order: string[]; hidden: string[] } }).value;
  // The server's order is kept; the columns it did not name stand after their neighbours of the default order.
  expect(saved.order.filter((key) => ["name", "maker", "grn", "available"].includes(key))).toEqual(["name", "maker", "grn", "available"]);
  expect(saved.hidden).not.toContain("warehouse");
  // The browser keeps its copy for a start without the server.
  expect(JSON.parse(window.localStorage.getItem("erp-table:stock-columns:v3")!).grouped.hidden).not.toContain("warehouse");
});

it("several clicks in «Колонки» are one request", async () => {
  const calls = mockServer([lot]);
  render(<WarehousePage />);
  await screen.findByText("SMA-KWE-RG402");
  await waitFor(() => expect(calls.length).toBe(1));
  const chooser = openChooser();
  fireEvent.click(chooser.getByLabelText("Производитель"));
  fireEvent.click(chooser.getByLabelText("Наим. по УПД"));
  fireEvent.click(chooser.getByRole("button", { name: "Выше: Наименование" }));
  await waitFor(() => expect(writes(calls)).toHaveLength(1), { timeout: 2000 });
  await new Promise((resolve) => setTimeout(resolve, 700));
  expect(writes(calls)).toHaveLength(1);
});

it("a layout kept in this browser moves to the server once, when the server has none", async () => {
  window.localStorage.setItem("erp-table:stock-columns:v3", JSON.stringify({ grouped: { order: ["warehouse", "name", "grn"], hidden: ["type"] } }));
  let calls = mockServer([lot]);
  const { unmount } = render(<WarehousePage />);
  await screen.findByText("SMA-KWE-RG402");
  await waitFor(() => expect(writes(calls)).toHaveLength(1));
  expect(writes(calls)[0]).toMatchObject({ method: "PUT", path: "/api/auth/ui-settings/stock.grouped" });
  const moved = (writes(calls)[0].body as { value: { order: string[]; hidden: string[] } }).value;
  expect(moved.order.filter((key) => ["warehouse", "name", "grn"].includes(key))).toEqual(["warehouse", "name", "grn"]);
  expect(moved.hidden).toContain("type");
  // Nothing was kept here for «Таблица склада»: nothing to move.
  expect(writes(calls).some((call) => call.path.endsWith("stock.flat"))).toBe(false);
  unmount();

  // Another page of the same browser: the server (in this mock) still says nothing, the layout is not sent again.
  forgetPersonalViews();
  vi.restoreAllMocks();
  calls = mockServer([lot]);
  render(<WarehousePage />);
  await screen.findByText("SMA-KWE-RG402");
  await waitFor(() => expect(calls.length).toBe(1));
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(writes(calls)).toHaveLength(0);
});

it("a browser that only ever had the default sends nothing up", async () => {
  // Every visit used to write the default layout to this browser: that is not the person's own.
  window.localStorage.setItem("erp-table:stock-columns:v3", JSON.stringify({
    grouped: { order: ["type", "grn", "available", "reserved", "balance", "lots", "place", "series", "article", "name", "warehouse"], hidden: ["balance"] },
  }));
  const calls = mockServer([lot]);
  render(<WarehousePage />);
  await screen.findByText("SMA-KWE-RG402");
  await waitFor(() => expect(calls.length).toBe(1));
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(writes(calls)).toHaveLength(0);
});

it("the server's layout wins over this browser's; without the server the browser's copy is used", async () => {
  window.localStorage.setItem("erp-table:stock-columns:v3", JSON.stringify({ grouped: { order: ["warehouse", "name"], hidden: [] } }));
  const offline = mockServer([lot], null);
  const { unmount } = render(<WarehousePage />);
  await screen.findByText("SMA-KWE-RG402");
  await waitFor(() => expect(offline.length).toBe(1));
  // The browser's order leads with «Склад»; «Свободно» and «Места» stand just before the name.
  expect(headerNames().slice(0, 4)).toEqual(["Склад", "Свободно", "Места", "Наименование"]);
  expect(writes(offline)).toHaveLength(0);
  unmount();

  forgetPersonalViews();
  vi.restoreAllMocks();
  mockServer([lot], { "stock.grouped": { order: ["grn", "name"], hidden: ["type", "available", "reserved", "lots", "place", "article", "warehouse"] } });
  render(<WarehousePage />);
  await waitFor(() => expect(headerNames()).toEqual(["ГРН", "Свободно", "Места", "Наименование"]));
});

it("«По умолчанию» drops the person's layout on the server", async () => {
  const calls = mockServer([lot], { "stock.grouped": { order: ["name", "grn"], hidden: ["type"] } });
  render(<WarehousePage />);
  await waitFor(() => expect(headerNames().slice(0, 3)).toEqual(["Свободно", "Места", "Наименование"]));
  await screen.findByText(lot.article);
  fireEvent.click(openChooser().getByRole("button", { name: "По умолчанию" }));
  expect(headerNames()).toEqual(["Тип", "ГРН", "Свободно", "Резерв", "Партий", "Места", "Артикул номенклатуры", "Наименование", "Склад"]);
  await waitFor(() => expect(writes(calls)).toEqual([{ method: "DELETE", path: "/api/auth/ui-settings/stock.grouped", body: undefined }]));
});

it("«Таблица склада» is its own view on the server", async () => {
  const calls = mockServer([lot]);
  render(<WarehousePage />);
  await screen.findByText("SMA-KWE-RG402");
  fireEvent.click(screen.getByRole("button", { name: "Таблица склада" }));
  fireEvent.click(openChooser().getByLabelText("Производитель"));
  await waitFor(() => expect(writes(calls).map((call) => call.path)).toEqual(["/api/auth/ui-settings/stock.flat"]), { timeout: 2000 });
  expect(within(screen.getByRole("table", { name: "Таблица склада" })).getByText("Amphenol RF")).toBeInTheDocument();
});

it.each(["grouped", "flat"] as const)("restores required quantity and place in an old %s view without saving it", async (kind) => {
  const saved = { order: ["grn", "name"], hidden: ["available", "place", "type", "reserved", "lots", "series", "article", "warehouse"] };
  const original = JSON.stringify(saved);
  const calls = mockServer([{ ...lot, storage_location: "", balance: "0", available: "0" }], { [`stock.${kind}`]: saved });
  render(<WarehousePage />);
  await screen.findByText(lot.name);
  if (kind === "flat") fireEvent.click(screen.getByRole("button", { name: "Таблица склада" }));
  const place = kind === "flat" ? "Место" : "Места";
  expect(headerNames()).toEqual(["ГРН", "Свободно", place, "Наименование"]);
  expect(screen.getByText("0 шт")).toBeInTheDocument();
  const chooser = openChooser();
  for (const title of ["Наименование", "Свободно", place]) {
    expect(chooser.getByLabelText(title)).toBeChecked();
    expect(chooser.getByLabelText(title)).toBeDisabled();
  }
  await new Promise((resolve) => setTimeout(resolve, 700));
  expect(writes(calls)).toHaveLength(0);
  expect(JSON.stringify(saved)).toBe(original);

  // The checkbox is locked; the keyboard-friendly move controls still change the order.
  // Pass the optional quantities (flat has no lot count), then the other required column.
  for (let step = 0; step < (kind === "flat" ? 3 : 4); step += 1) fireEvent.click(chooser.getByRole("button", { name: `Выше: ${place}` }));
  expect(headerNames()).toEqual(["ГРН", place, "Свободно", "Наименование"]);
  await waitFor(() => expect(writes(calls)).toHaveLength(1), { timeout: 2000 });
  expect(writes(calls)[0].path).toBe(`/api/auth/ui-settings/stock.${kind}`);
});

it("omits every empty optional column even if the saved view enables it, while numeric zero is meaningful", async () => {
  const empty: WarehouseLot = {
    ...lot, article: "", series: "", item_type: "", manufacturer: "  ", upd_name: "", invoice_name: " \t ",
    maker_article: "", supplier_article: "", purpose: "", receipt: null, storage_location: "", balance: "0", available: "0",
  };
  const layout = readLayout("grouped", null);
  const calls = mockServer([empty], { "stock.grouped": { ...layout, hidden: [] } });
  render(<WarehousePage />);
  await screen.findByText(lot.name);
  expect(headerNames()).toEqual(["ГРН", "Свободно", "Резерв", "Остаток", "Партий", "Места", "Наименование", "Склад"]);
  const chooser = openChooser();
  for (const label of ["Тип", "Серия", "Артикул номенклатуры", "Артикул производителя", "Артикул поставщика", "Назначение", "Производитель", "Наим. по УПД", "Наим. по счёту", "№ счёта", "Дата счёта", "№ УПД", "Дата УПД", "№ ПРД", "Дата ПРД", "Файл УПД"]) {
    expect(chooser.queryByLabelText(label)).not.toBeInTheDocument();
  }
  expect(chooser.getByLabelText("Резерв")).toBeChecked();
  expect(writes(calls)).toHaveLength(0);
});

it("reveals a saved column when a later page has a value and retains it when search hides that lot", async () => {
  const filled = { ...other, invoice_name: "Соединитель по счёту" };
  const layout = readLayout("grouped", null);
  const stockReads: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/ui-settings") return json({ views: { "stock.grouped": { ...layout, hidden: layout.hidden.filter((key) => key !== "invoice_name") } } });
    if (path.split("?")[0] === "/api/stock") {
      stockReads.push(path);
      const params = new URL(path, "http://test").searchParams;
      if (params.get("q")) return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
      if (params.has("after")) return json({ lots: [filled], total: 2, truncated: 0, next_cursor: null });
      return json({ lots: [lot], total: 2, truncated: 1, next_cursor: "next" });
    }
    return json({ group: "series", groups: [], articles: [] });
  });
  render(<WarehousePage />);
  await screen.findByText(lot.grn);
  expect(headerNames()).not.toContain("Наим. по счёту");
  expect(openChooser().queryByLabelText("Наим. по счёту")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Показать ещё" }));
  await screen.findByText(filled.invoice_name);
  expect(headerNames()).toContain("Наим. по счёту");
  expect(openChooser().getByLabelText("Наим. по счёту")).toBeChecked();

  fireEvent.change(screen.getByLabelText("Поиск по складу"), { target: { value: lot.grn } });
  await waitFor(() => expect(screen.queryByText(filled.grn)).not.toBeInTheDocument());
  expect(stockReads.some((path) => path.includes("q="))).toBe(true);
  expect(headerNames()).toContain("Наим. по счёту");
  expect(openChooser().getByLabelText("Наим. по счёту")).toBeChecked();
  expect(within(row(lot.grn)).queryByText(filled.invoice_name)).not.toBeInTheDocument();
});

it.each([false, true])("suppresses duplicate maker articles in cells and chooser (flat=%s)", async (flat) => {
  const same = { ...lot, article: " SMA-KWE-RG402 ", maker_article: " sma-kwe-rg402 " };
  const different = { ...other, maker_article: " MFG-402 " };
  const views = Object.fromEntries(["grouped", "flat"].map((kind) => {
    const layout = readLayout(kind as "grouped" | "flat", null);
    return [`stock.${kind}`, { ...layout, hidden: layout.hidden.filter((key) => key !== "maker_article") }];
  }));
  mockServer([same, different], views);
  render(<WarehousePage />);
  await screen.findAllByText(/SMA-KWE-RG402/);
  if (flat) fireEvent.click(screen.getByRole("button", { name: "Таблица склада" }));
  expect(headerNames()).toContain("Артикул производителя");
  const index = headerNames().indexOf("Артикул производителя");
  expect(row(same.grn).querySelectorAll("td")[index]).toHaveTextContent(/^—$/);
  expect(row(same.grn).querySelectorAll("td")[index]).toHaveClass("stock-dim");
  expect(row(different.grn).querySelectorAll("td")[index]).toHaveTextContent(/^MFG-402$/);
  expect(row(different.grn).querySelectorAll("td")[index]).toHaveAttribute("title", "MFG-402");
});

it("does not offer a maker article column when all maker articles duplicate ours", async () => {
  mockServer([{ ...lot, maker_article: ` ${lot.article.toLowerCase()} ` }], {
    "stock.grouped": { order: ["article", "maker_article", "name"], hidden: [] },
  });
  render(<WarehousePage />);
  await screen.findByText(lot.article);
  expect(headerNames()).not.toContain("Артикул производителя");
  expect(openChooser().queryByLabelText("Артикул производителя")).not.toBeInTheDocument();
});

it("retains populated receipt columns when a mutation answers with lot fields but no receipt", async () => {
  const layout = readLayout("grouped", null);
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/auth/ui-settings") return json({ views: { "stock.grouped": {
      ...layout, hidden: layout.hidden.filter((key) => key !== "upd_number" && key !== "upd_file"),
    } } });
    if (path.endsWith("/writeoff") && init?.method === "POST") {
      return json({ lot: { ...lot, receipt: undefined, version: 2, written_off: "1", balance: "39", available: "39" } });
    }
    if (path.split("?")[0] === "/api/stock") return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
    return json({ group: "series", groups: [], articles: [] });
  });
  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "Показать партии (1)" }));
  fireEvent.click(screen.getByRole("button", { name: "− Списать" }));
  fireEvent.change(screen.getByLabelText("Количество"), { target: { value: "1" } });
  fireEvent.change(screen.getByLabelText("Назначение списания"), { target: { value: "Склад" } });
  fireEvent.click(screen.getByRole("button", { name: "Списать" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Списать" })).not.toBeInTheDocument());
  expect(headerNames()).toEqual(expect.arrayContaining(["№ УПД", "Файл УПД"]));
  const position = within(document.querySelector<HTMLElement>(".stock-article-row")!);
  expect(position.getByText("125")).toBeInTheDocument();
  expect(position.getByRole("link", { name: `Файл УПД партии ${lot.grn}` })).toHaveAttribute("href", lot.receipt!.scan_file);
  expect(openChooser().getByLabelText("Файл УПД")).toBeChecked();
});

type Item = { id: string; a: string; b: string; c: string };
const items: Item[] = Array.from({ length: 12 }, (_, index) => ({ id: String(index), a: `a${index}`, b: `b${index}`, c: `c${index}` }));
const columns = [
  { key: "a", title: "Альфа", value: (item: Item) => item.a },
  { key: "b", title: "Бета", value: (item: Item) => item.b },
  { key: "c", title: "Гамма", value: (item: Item) => item.c },
];

it("a table with a personal view (Готовая продукция) reads it from the server and «Сбросить вид» drops it", async () => {
  const calls = mockServer([], { "t.goods": { hidden: ["b"], known: ["a", "b", "c"] } });
  render(<DataTable label="Тест" storageKey="t-goods" personalKey="t.goods" rows={items} columns={columns} rowKey={(item) => item.id} />);
  await waitFor(() => expect(screen.queryByRole("columnheader", { name: /Бета/ })).not.toBeInTheDocument());
  // Read, not changed: nothing goes back.
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(writes(calls)).toHaveLength(0);

  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  fireEvent.click(screen.getByRole("button", { name: "Сбросить вид" }));
  expect(screen.getByRole("columnheader", { name: /Бета/ })).toBeInTheDocument();
  await waitFor(() => expect(writes(calls).map((call) => call.method)).toEqual(["DELETE"]));
  await new Promise((resolve) => setTimeout(resolve, 700));
  expect(writes(calls).map((call) => call.method)).toEqual(["DELETE"]);
});

it("a personal table with nothing of its own sends nothing until the person changes it", async () => {
  const calls = mockServer([], {});
  render(<DataTable label="Тест" storageKey="t-goods" personalKey="t.goods" rows={items} columns={columns} rowKey={(item) => item.id} />);
  await waitFor(() => expect(calls.length).toBe(1));
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(writes(calls)).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: /^Бета/ })); // a sort is the person's too
  await waitFor(() => expect(writes(calls).map((call) => call.path)).toEqual(["/api/auth/ui-settings/t.goods"]), { timeout: 2000 });
});

it("a personal table sends the person's change, and a browser layout moves up once", async () => {
  window.localStorage.setItem("erp-table:t-goods", JSON.stringify({ hidden: ["c"], known: ["a", "b", "c"] }));
  const calls = mockServer([], {});
  render(<DataTable label="Тест" storageKey="t-goods" personalKey="t.goods" rows={items} columns={columns} rowKey={(item) => item.id} />);
  await waitFor(() => expect(writes(calls)).toHaveLength(1));
  expect(writes(calls)[0]).toMatchObject({ method: "PUT", path: "/api/auth/ui-settings/t.goods" });
  expect((writes(calls)[0].body as { value: { hidden: string[] } }).value.hidden).toEqual(["c"]);

  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  fireEvent.click(screen.getByRole("button", { name: "Сбросить вид" }));
  await waitFor(() => expect(writes(calls).map((call) => call.method)).toEqual(["PUT", "DELETE"]));
});
