import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReferencesPage } from "./ReferencesPage";
import type { Principal, TechOperationRecord } from "./api";

/* «База данных» по всей базе (владелец 14.10: «пройдись по базе в целом, улучши её»): правка ячеек по месту тем же
   путём, что окно записи (со старым значением), «+ Столбец» и «Удалить столбец», быстрые отборы «Без …», порядок по
   умолчанию — на вкладках «Терминалы», «Материалы», «Домен → поставщик», «Сечения AWG ↔ мм²». */

const admin: Principal = {
  user: { id: "10000000-0000-0000-0000-000000000001", email: "admin@example.test", name: "Администратор", department: "ERP" },
  roles: ["admin"],
  access_level: "admin",
  permissions: { erpref: "edit", procurement: "edit" },
};
const viewer: Principal = { ...admin, roles: ["foreman"], access_level: "foreman", permissions: { erpref: "view" } };

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const rec = (id: string, tab: string, fields: Record<string, string>): TechOperationRecord => ({ id, tab: tab as TechOperationRecord["tab"], display_text: id, source_sheet: "", fields });

const TER_COLUMNS = ["Тип\nразъема", "Производитель", "Series", "Тип\n контакта", "Артикул\n контакта (REEL)", "Аппликатор", "С зачисткой", "Длина зачистки, мм", "Высота обжима проводника , мм"];
const TER = [
  rec("t1", "ter", { "Тип\nразъема": "Обжимной контакт", Производитель: "Molex", Series: "Micro-Fit", "Тип\n контакта": "Male", "Артикул\n контакта (REEL)": "430310001", Аппликатор: "S8/M8", "С зачисткой": "TRUE", "Длина зачистки, мм": "2,5", "Высота обжима проводника , мм": "0,83 - 0,93" }),
  rec("t2", "ter", { "Тип\nразъема": "Обжимной контакт", Производитель: "Molex", Series: "Mini-Fit", "Тип\n контакта": "Female", "Артикул\n контакта (REEL)": "39000038", Аппликатор: "", "С зачисткой": "FALSE", "Длина зачистки, мм": "", "Высота обжима проводника , мм": "" }),
  rec("t3", "ter", { "Тип\nразъема": "Обжимная клемма", Производитель: "КВТ", Series: "НВИ", "Тип\n контакта": "", "Артикул\n контакта (REEL)": "НВИ 1.5-3", Аппликатор: "M31", "С зачисткой": "", "Длина зачистки, мм": "", "Высота обжима проводника , мм": "" }),
];
const MAT_COLUMNS = ["Наименование", "Группа", "Производитель", "Цвет", "Закупок", "Поставщик"];
const MAT = [
  rec("m1", "mat", { Наименование: "Стяжка 100 мм", Группа: "стяжка", Производитель: "", Цвет: "чёрный", Закупок: "2", Поставщик: "" }),
  rec("m2", "mat", { Наименование: "ТУТ 10/5", Группа: "ТУТ", Производитель: "Raychem", Цвет: "чёрный", Закупок: "1", Поставщик: "chipdip.ru" }),
  rec("m3", "mat", { Наименование: "ТУТ 2/1", Группа: "ТУТ", Производитель: "Raychem", Цвет: "красный", Закупок: "0", Поставщик: "chipdip.ru" }),
  rec("m4", "mat", { Наименование: "Изолента", Группа: "лента", Производитель: "3M", Цвет: "", Закупок: "0", Поставщик: "" }),
];

type Call = { path: string; method: string; body: unknown };

function mockDb({ ter = TER, mat = MAT, terOwn = [] as string[], matOwn = [] as string[] } = {}) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path === "/api/references/bases") return json({ bases: [] });
    if (path === "/api/references/crimp-codes") return json({ owners: {}, codes: [], missing: [] });
    if (path.startsWith("/api/references/tech-operations?tab=ter")) return json({ metadata: null, columns: [...TER_COLUMNS, ...terOwn], custom_columns: terOwn, records: ter });
    if (path.startsWith("/api/references/tech-operations?tab=mat")) return json({ metadata: null, columns: [...MAT_COLUMNS, ...matOwn], custom_columns: matOwn, records: mat });
    if (path.startsWith("/api/references/tech-operations/") && method === "PATCH") return json(ter[0]);
    if (path === "/api/references/tech-operations/columns/remove") return json({ tab: "ter", name: "Полка", records: 3 });
    throw new Error(`Unexpected request: ${method} ${path}`);
  });
  return calls;
}

afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); window.history.replaceState({}, "", "/"); });

const headers = () => screen.getAllByRole("columnheader").map((head) => head.textContent?.trim() ?? "");
function cellOf(rowText: string, column: string): HTMLElement {
  const row = screen.getAllByRole("row").find((item) => item.textContent?.includes(rowText) && !item.classList.contains("group-row"))!;
  return within(row).getAllByRole("cell")[headers().indexOf(column)] as HTMLElement;
}
function typeInCell(cell: HTMLElement, value: string) {
  fireEvent.click(cell);
  fireEvent.keyDown(cell, { key: "Enter" });
  const input = within(cell).getByRole("textbox");
  fireEvent.change(input, { target: { value } });
  fireEvent.keyDown(input, { key: "Enter" });
}

it("Терминалы: plain cells are edited in place with the old value; the type, maker, series, REEL article, gender and yes/no stay in the window", async () => {
  const calls = mockDb();
  render(<ReferencesPage principal={admin} initialTab="ter" />);
  await screen.findByText("39000038");

  for (const fixed of ["Тип разъёма", "Производитель", "Серия", "Тип контакта", "Артикул контакта (катушка)", "С зачисткой"]) {
    expect(cellOf("39000038", fixed)).not.toHaveClass("dt-editable");
  }
  expect(cellOf("39000038", "Аппликатор")).toHaveClass("dt-editable");
  typeInCell(cellOf("39000038", "Длина зачистки, мм"), "3,1");

  await waitFor(() => expect(calls.find((call) => call.method === "PATCH")).toEqual({
    path: "/api/references/tech-operations/t2", method: "PATCH", body: { edits: [{ field: "Длина зачистки, мм", old: "", value: "3,1" }] },
  }));
  // После записи таблица перечитывается с сервера.
  await waitFor(() => expect(calls.filter((call) => call.path.startsWith("/api/references/tech-operations?tab=ter")).length).toBe(2));

  // Щелчок по ключевой ячейке по-прежнему открывает карточку контакта.
  fireEvent.click(screen.getByText("39000038"));
  expect(await screen.findByRole("button", { name: "Править запись" })).toBeInTheDocument();
});

it("Терминалы: grouped by maker, chips «Без высоты обжима» and «Без аппликатора»", async () => {
  mockDb();
  render(<ReferencesPage principal={admin} initialTab="ter" />);
  await screen.findByText("39000038");
  const groups = screen.getAllByRole("row").filter((row) => row.classList.contains("group-row")).map((row) => row.textContent);
  expect(groups).toEqual(["Molex · 2", "КВТ · 1"]);

  fireEvent.click(screen.getByRole("button", { name: /^Без высоты обжима · 2/ }));
  expect(screen.queryByText("430310001")).toBeNull();
  expect(screen.getByText("39000038")).toBeInTheDocument();
  expect(screen.getByText("НВИ 1.5-3")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /^Без аппликатора · 1/ }));
  expect(screen.queryByText("НВИ 1.5-3")).toBeNull();
  expect(screen.getByText("39000038")).toBeInTheDocument();
});

it("Терминалы: «+ Столбец» is offered; an own column shows at once, is editable and removable only when empty, after a confirm window", async () => {
  const calls = mockDb({ terOwn: ["Полка"], ter: TER.map((item) => ({ ...item, fields: { ...item.fields, Полка: item.id === "t1" ? "А-1" : "" } })) });
  render(<ReferencesPage principal={admin} initialTab="ter" />);
  await screen.findByText("39000038");
  expect(screen.getByRole("button", { name: "+ Столбец" })).toBeInTheDocument();
  expect(headers()).toContain("Полка");
  expect(cellOf("39000038", "Полка")).toHaveClass("dt-editable");

  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  fireEvent.click(screen.getByRole("button", { name: "Удалить столбец: Полка" }));
  const dialog = screen.getByRole("dialog", { name: "Удалить столбец" });
  expect(within(dialog).getByText(/записей — 1/)).toBeInTheDocument();
  expect(within(dialog).getByRole("button", { name: "Удалить столбец" })).toBeDisabled();
  fireEvent.click(within(dialog).getByRole("button", { name: "Отмена" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(calls.some((call) => call.path.endsWith("/columns/remove"))).toBe(false);
});

it("Терминалы: an empty own column is removed after the confirm and the table is read again", async () => {
  const calls = mockDb({ terOwn: ["Полка"], ter: TER.map((item) => ({ ...item, fields: { ...item.fields, Полка: "" } })) });
  render(<ReferencesPage principal={admin} initialTab="ter" />);
  await screen.findByText("39000038");
  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  // Свой столбец — единственный с «Удалить»: колонки листа не удаляются.
  expect(screen.getAllByRole("button", { name: /^Удалить столбец:/ })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Удалить столбец: Полка" }));
  fireEvent.click(within(screen.getByRole("dialog", { name: "Удалить столбец" })).getByRole("button", { name: "Удалить столбец" }));

  await waitFor(() => expect(calls.find((call) => call.path.endsWith("/columns/remove"))?.body).toEqual({ tab: "ter", name: "Полка" }));
  expect(await screen.findByText("Столбец «Полка» удалён")).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("Терминалы: a viewer gets no editable cells, no «+ Столбец», no «Удалить»", async () => {
  mockDb({ terOwn: ["Полка"], ter: TER.map((item) => ({ ...item, fields: { ...item.fields, Полка: "" } })) });
  render(<ReferencesPage principal={viewer} initialTab="ter" />);
  await screen.findByText("39000038");
  expect(document.querySelector("td.dt-editable")).toBeNull();
  expect(screen.queryByRole("button", { name: "+ Столбец" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  expect(screen.queryByRole("button", { name: /^Удалить столбец/ })).toBeNull();
});

it("Материалы: grouped by the sheet's group, names in natural order; the name, group and purchases are not cells to edit; chips «Без …»", async () => {
  const calls = mockDb();
  render(<ReferencesPage principal={admin} initialTab="mat" />);
  await screen.findByText("Изолента");
  const groups = screen.getAllByRole("row").filter((row) => row.classList.contains("group-row")).map((row) => row.textContent);
  expect(groups).toEqual(["лента · 1", "стяжка · 1", "ТУТ · 2"]);
  const names = screen.getAllByRole("row").filter((row) => !row.classList.contains("group-row")).map((row) => row.textContent ?? "").filter((text) => text.startsWith("ТУТ "));
  expect(names[0]).toMatch(/^ТУТ 2\/1/); // 2/1 до 10/5: числа как числа

  expect(headers()).not.toContain("Группа"); // группа — заголовок группы строк, не колонка
  for (const fixed of ["Наименование", "Закупок"]) expect(cellOf("ТУТ 2/1", fixed)).not.toHaveClass("dt-editable");
  expect(screen.getByRole("button", { name: "+ Столбец" })).toBeInTheDocument();
  typeInCell(cellOf("ТУТ 2/1", "Цвет"), "белый");
  await waitFor(() => expect(calls.find((call) => call.method === "PATCH")?.body).toEqual({ edits: [{ field: "Цвет", old: "красный", value: "белый" }] }));

  fireEvent.click(screen.getByRole("button", { name: /^Без производителя · 1/ }));
  expect(screen.getByText("Стяжка 100 мм")).toBeInTheDocument();
  expect(screen.queryByText("Изолента")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /^Без поставщика · 2/ }));
  expect(screen.getByText("Изолента")).toBeInTheDocument();
  expect(screen.queryByText("ТУТ 2/1")).toBeNull();
});

it("Домен → поставщик: sorted by domain with new empty rows last; a column pasted from Excel fills every row before one save", async () => {
  let payload: unknown;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/references/maps" && init?.method === "PUT") { payload = JSON.parse(String(init.body)); return json({ ...(payload as object), can_edit: { suppliers: true } }); }
    if (path === "/api/references/maps") return json({
      suppliers: [{ key: "zeta.ru", value: "Зета" }, { key: "", value: "" }, { key: "alfa.ru", value: "Альфа" }, { key: "chipdip.ru", value: "ЧипДип" }],
      countries: [], gost_awg: [], can_edit: { suppliers: true },
    });
    if (path === "/api/references/bases") return json({ bases: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
  render(<ReferencesPage principal={admin} initialTab="suppliers" />);
  await screen.findByText("alfa.ru");
  const keys = screen.getAllByRole("row").slice(1).map((row) => within(row).getAllByRole("cell")[0]?.textContent ?? "");
  expect(keys.slice(0, 4)).toEqual(["alfa.ru", "chipdip.ru", "zeta.ru", "—"]);

  const first = screen.getByText("Альфа", { selector: "td" });
  fireEvent.click(first);
  const paste = new Event("paste", { bubbles: true, cancelable: true }) as Event & { clipboardData: { getData: () => string } };
  Object.defineProperty(paste, "clipboardData", { value: { getData: () => "Альфа-М\r\nЧипДип ООО\r\nЗета-Т\r\n" } });
  fireEvent(first, paste);
  await screen.findByText("Зета-Т", { selector: "td" });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить справочники" }));
  await waitFor(() => expect(payload).toEqual({
    suppliers: [{ key: "zeta.ru", value: "Зета-Т" }, { key: "", value: "" }, { key: "alfa.ru", value: "Альфа-М" }, { key: "chipdip.ru", value: "ЧипДип ООО" }],
    countries: [], gost_awg: [],
  }));
});

it("Сечения AWG ↔ мм²: Ø жилы is written in the cell with the size's other values; a «Без Ø жилы» chip", async () => {
  const saved: unknown[] = [];
  const gauges = [
    { id: "g1", awg: "22", section_mm2: 0.35, conductor_d_mm: 0.76, strands: "7×0,26", label: "22 AWG · 0,35 мм²", wires: 3 },
    { id: "g2", awg: "", section_mm2: 0.12, conductor_d_mm: null, strands: "", label: "0,12 мм²", wires: 0 },
  ];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/references/wire-gauges/g2" && init?.method === "PUT") { saved.push(JSON.parse(String(init.body))); return json(gauges[1]); }
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path === "/api/references/bases") return json({ bases: [] });
    if (path === "/api/references/cables") return json({ columns: [], counts: { coax: 0, wire: 0, multi: 0, other: 0 }, rows: [], gauges });
    throw new Error(`Unexpected request: ${path}`);
  });
  render(<ReferencesPage principal={admin} initialTab="gauges" />);
  await screen.findByText("только ГОСТ");
  expect(cellOf("22", "AWG")).not.toHaveClass("dt-editable");
  typeInCell(cellOf("только ГОСТ", "Ø жилы, мм"), "0,39");
  await waitFor(() => expect(saved).toEqual([{ awg: "", section_mm2: "0,12", conductor_d_mm: "0,39", strands: "" }]));
  fireEvent.click(screen.getByRole("button", { name: /^Без Ø жилы · 1/ }));
  expect(screen.queryByText("22")).toBeNull();
});
