import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReferencesPage } from "./ReferencesPage";
import { ColumnViewsProvider } from "./ColumnViews";
import type { DataColumn } from "./DataTable";
import type { Principal, TechOperationRecord } from "./api";
import { lugFamily, lugRows, mergeLugColumns } from "./lugList";
import { blockLines, listHeads } from "./test/blocks";

/* «Наконечники» (владелец 26.10.2026: «тип наконечник, а не терминал… в другую БД — наконечники»): своя вкладка сразу
   за «Терминалами», только строки БД.ТЕР со списком «lug» и их столбцы; «Терминалы» просят у сервера только контакты;
   строку переносят между списками в окне правки. */

const admin: Principal = {
  user: { id: "10000000-0000-0000-0000-000000000001", email: "admin@example.test", name: "Администратор", department: "ERP" },
  roles: ["admin"],
  access_level: "admin",
  permissions: { erpref: "edit", procurement: "edit" },
};

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const lug = (id: string, fields: Record<string, string>): TechOperationRecord =>
  ({ id, tab: "lug", display_text: id, source_sheet: "", fields });

const LUG_COLUMNS = ["Производитель", "Product Name", "Series", "Артикул\n контакта (REEL)", "От мм2", "До мм2", "От AWG", "До AWG", "Материал контакта", "Аппликатор"];
const LUGS = [
  lug("l1", { Производитель: "КВТ", "Product Name": "НКИ", Series: "НКИ 2.5", "Артикул\n контакта (REEL)": "НКИ 2.5-4", "От мм2": "1,5", "До мм2": "2,5", "От AWG": "16", "До AWG": "14", "Материал контакта": "медь", Аппликатор: "M32" }),
  lug("l2", { Производитель: "КВТ", "Product Name": "НКИ", Series: "НКИ 1.5", "Артикул\n контакта (REEL)": "НКИ 1.5-4", "От мм2": "0,5", "До мм2": "1,5", "От AWG": "20", "До AWG": "16", "Материал контакта": "медь", Аппликатор: "M31" }),
  lug("l3", { Производитель: "КВТ", "Product Name": "НШВИ", Series: "НШВИ 0.5", "Артикул\n контакта (REEL)": "НШВИ 0.5-8", "От мм2": "0,5", "До мм2": "0,5", "От AWG": "20", "До AWG": "20", "Материал контакта": "медь", Аппликатор: "" }),
];
const CONTACT: TechOperationRecord = { id: "c1", tab: "ter", display_text: "c1", source_sheet: "",
  fields: { Производитель: "JST", Series: "XH", "Артикул\n контакта (REEL)": "SXH-001T-P0.6" } };

type Call = { path: string; method: string; body: unknown };

function mockDb(records = LUGS, columns = LUG_COLUMNS) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path === "/api/references/column-views") return json({ views: { lug: columns.map((key) => ({ key, visible: true })) }, can_edit: false });
    if (path === "/api/references/bases") return json({ bases: [], tabs: { ter: 1, lug: 3 } });
    if (path === "/api/references/coverings") return json({ counts: {}, titles: {}, rows: [] });
    if (path.startsWith("/api/references/tech-operations?tab=mat")) return json({ metadata: null, columns: [], records: [] });
    if (path === "/api/references/crimp-codes") return json({ owners: {}, codes: [], missing: [] });
    if (path === "/api/techcards/connector-series") return json({ match: null, series: [] });
    if (path.startsWith("/api/references/tech-operations?tab=lug")) return json({ metadata: null, columns, custom_columns: [], records });
    if (path.startsWith("/api/references/tech-operations?tab=ter")) return json({ metadata: null, columns: ["Производитель", "Series", "Артикул\n контакта (REEL)"], custom_columns: [], records: [CONTACT] });
    if (path === "/api/references/tech-operations/l2/tab" && method === "PUT") return json({ ...LUGS[1], tab: "ter" });
    if (path === "/api/references/tech-operations/both" && method === "PATCH") return json(records[0]);
    throw new Error(`Unexpected request: ${method} ${path}`);
  });
  return calls;
}

afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); window.history.replaceState({}, "", "/"); });

it("«Наконечники» stands right after «Терминалы» with its own count; Терминалы ask the server for the contacts only", async () => {
  const calls = mockDb();
  render(<ReferencesPage principal={admin} initialTab="ter" />);
  await screen.findByText("SXH-001T-P0.6");

  const chips = within(screen.getByRole("navigation", { name: "Вкладки базы данных" })).getAllByRole("button").map((button) => button.textContent);
  const at = chips.findIndex((text) => text?.startsWith("Терминалы"));
  expect(chips[at + 1]).toBe("Наконечники3");
  expect(calls.some((call) => call.path.includes("tab=ter") && !call.path.includes("ter_kind"))).toBe(true);
  expect(screen.queryByText("НКИ 1.5-4")).toBeNull();
  expect(listHeads().map((head) => head.textContent?.trim())).toContain("Артикул контакта (катушка)");
  expect(listHeads().map((head) => head.textContent?.trim())).not.toContain("Артикул");
});

it("«Наконечники»: only lugs, in blocks by family, size then article; «Сечение, мм²» and «AWG» from–to after the article", async () => {
  mockDb();
  render(<ReferencesPage principal={admin} initialTab="lug" />);
  await screen.findByText("НКИ 1.5-4");

  expect(blockLines()).toEqual(["НКИ — КВТ · 2", "НШВИ — КВТ · 1"]);
  expect(screen.queryByText("SXH-001T-P0.6")).toBeNull();
  const heads = listHeads().map((head) => head.textContent?.trim());
  // «Наименование» (НКИ, НШВИ) — заголовок блока, колонкой не повторяется
  expect(heads.slice(0, 5)).toEqual(["Производитель", "Серия", "Артикул", "Сечение, мм²", "AWG"]);
  expect(heads).not.toContain("От мм2");
  expect(heads).not.toContain("Наименование");
  const rows = screen.getAllByRole("row").filter((row) => row.textContent?.includes("НКИ "));
  expect(rows.map((row) => within(row).getAllByRole("cell")[2].textContent)).toEqual(["НКИ 1.5-4", "НКИ 2.5-4"]);
  expect(within(rows[0]).getAllByRole("cell")[3].textContent).toBe("0,5–1,5");
  expect(within(rows[0]).getAllByRole("cell")[4].textContent).toBe("20–16");
  // its own «+ Столбец»: «Наконечники» is its own table (owner 28.10.2026), a column added here is not the terminals'
  expect(screen.getByRole("button", { name: "+ Столбец" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^Без аппликатора · 1/ })).toBeInTheDocument();
});

it.each([
  [{ terArticle: "НКИ-1" }, "НКИ-1"],
  [{ terArticleBag: "НКИ-2" }, "НКИ-2"],
  [{ terArticle: "НКИ-1", terArticleBag: "НКИ-1" }, "НКИ-1"],
  [{ terArticle: "НКИ-1", terArticleBag: "НКИ-2" }, "НКИ-1 / НКИ-2"],
  [{ "Артикул\n контакта (REEL)": "НКИ-1", "Артикул\n контакта (BAG)": "НКИ-2" }, "НКИ-1 / НКИ-2"],
  [{ "Артикул контакта": "НКИ-1", terArticle: "НКИ-2", terArticleBag: "НКИ-3" }, "НКИ-1 / НКИ-2 / НКИ-3"],
  [{ terArticle: "НКИ-1\nНКИ-2", terArticleBag: "НКИ-3" }, "НКИ-1\nНКИ-2 / НКИ-3"],
  [{ terArticle: "", terArticleBag: "" }, ""],
])("lug article combines distinct source values without changing fields or edit callbacks: %j", async (fields, expected) => {
  const row = lug("article", fields as Record<string, string>);
  const before = JSON.stringify(row);
  const save = vi.fn().mockResolvedValue(undefined);
  const source: DataColumn<TechOperationRecord>[] = Object.keys(fields).map((key) => ({ key, title: key, value: (record) => record.fields[key], edit: { save } }));
  const columns = mergeLugColumns(source, Object.keys(fields));
  const visible = columns.filter((column) => !column.hidden);
  expect(visible.map((column) => column.title)).toEqual(["Артикул"]);
  expect(visible[0].value(row)).toBe(expected);
  expect(visible[0].edit).toBeUndefined();
  expect(visible[0].computed).toBe(true);
  for (const original of source) {
    const kept = columns.find((column) => column.key === original.key)!;
    expect(kept.hidden).toBe(true);
    expect(kept.value).toBe(original.value);
    expect(kept.edit).toBe(original.edit);
    await kept.edit!.save(row, "исправление");
  }
  expect(save).toHaveBeenCalledTimes(source.length);
  expect(JSON.stringify(row)).toBe(before);
});

it("legacy personal and shared lug layouts cannot restore the packaging split; both source fields still open in RowForm", async () => {
  const fields = { Производитель: "КВТ", "Product Name": "НКИ", terArticle: "НКИ-1", terArticleBag: "НКИ-2" };
  const columns = Object.keys(fields);
  const legacy = { hidden: [], known: columns, order: columns, titles: { terArticle: "Артикул (катушка)", terArticleBag: "Артикул (пакет)" } };
  window.localStorage.setItem("erp-table:db:lug", JSON.stringify(legacy));
  const calls = mockDb([lug("both", fields)], columns);
  render(<ColumnViewsProvider><ReferencesPage principal={admin} initialTab="lug" /></ColumnViewsProvider>);
  const article = await screen.findByText("НКИ-1 / НКИ-2");
  await waitFor(() => expect(calls.some((call) => call.path === "/api/references/column-views")).toBe(true));
  expect(listHeads().map((head) => head.textContent?.trim())).toEqual(["Производитель", "Артикул"]);
  expect(window.localStorage.getItem("erp-table:db:lug")).toBe(JSON.stringify(legacy));
  fireEvent.click(article);
  expect(screen.getByRole("textbox", { name: "Поле terArticle" })).toHaveValue("НКИ-1");
  expect(screen.getByRole("textbox", { name: "Поле terArticleBag" })).toHaveValue("НКИ-2");
  expect(screen.queryByRole("textbox", { name: "Поле __article" })).toBeNull();
  fireEvent.change(screen.getByRole("textbox", { name: "Поле terArticleBag" }), { target: { value: "НКИ-3" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить запись" }));
  await waitFor(() => expect(calls.find((call) => call.method === "PATCH")).toEqual({
    path: "/api/references/tech-operations/both", method: "PATCH", body: { edits: [{ field: "terArticleBag", old: "НКИ-2", value: "НКИ-3" }] },
  }));
});

it("«Наконечники»: a row opens its window, «Перенести в» moves it to the terminals and the list is read again", async () => {
  const calls = mockDb();
  render(<ReferencesPage principal={admin} initialTab="lug" />);
  fireEvent.click(await screen.findByText("НКИ 1.5-4"));

  const select = await screen.findByRole("combobox", { name: "Перенести в" });
  fireEvent.change(select, { target: { value: "ter" } });
  expect(screen.getByText("Строка перейдёт в «Терминалы», её значения сохранятся; у базы свои столбцы.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Сохранить запись" }));

  await waitFor(() => expect(calls.find((call) => call.method === "PUT")).toEqual({
    path: "/api/references/tech-operations/l2/tab", method: "PUT", body: { tab: "ter" } }));
  expect(await screen.findByText("Запись перенесена в «Терминалы»")).toBeInTheDocument();
});

it("lugRows: family, then the smaller section, then the article; a row without a family goes last", () => {
  const nameless = lug("l4", { "От мм2": "0,25" });
  expect(lugRows([LUGS[2], nameless, LUGS[0], LUGS[1]]).map((row) => row.id)).toEqual(["l2", "l1", "l3", "l4"]);
  expect(lugFamily(nameless)).toBe("Без семейства");
  expect(lugFamily({ ...LUGS[0], fields: { terComponent: "РПИ-М" } })).toBe("РПИ-М");
});
