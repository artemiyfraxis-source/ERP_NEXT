import { CRIMP_COLUMN_TITLE } from "./CrimpCell";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReferencesPage } from "./ReferencesPage";
import type { Principal } from "./api";

const admin: Principal = {
  user: {
    id: "10000000-0000-0000-0000-000000000001",
    email: "admin@example.test",
    name: "Администратор",
    department: "ERP",
  },
  roles: ["admin"],
  access_level: "admin",
  permissions: { erpref: "edit", procurement: "edit" },
};

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); window.history.replaceState({}, "", "/"); });

it("edits and atomically saves all reference maps", async () => {
  let payload: unknown;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/references/maps" && init?.method === "PUT") {
      payload = JSON.parse(String(init.body));
      return json(payload);
    }
    if (path === "/api/references/maps") return json({
      suppliers: [{ key: "example.ru", value: "Поставщик" }],
      countries: [{ key: "Fraxis", value: "Россия" }],
      gost_awg: [{ key: "0,35", value: "22" }],
      can_edit: { suppliers: true, gost_awg: true },
    });
    if (path === "/api/references/bases") return json({ bases: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReferencesPage principal={admin} initialTab="suppliers" />);
  fireEvent.change(await screen.findByLabelText("Поставщик 1"), { target: { value: "Новый поставщик" } });
  fireEvent.click(screen.getByRole("button", { name: "Изготовитель → страна" }));
  fireEvent.change(screen.getByLabelText("Страна 1"), { target: { value: "Китай" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить справочники" }));

  await waitFor(() => expect(payload).toEqual({
    suppliers: [{ key: "example.ru", value: "Новый поставщик" }],
    countries: [{ key: "Fraxis", value: "Китай" }],
    gost_awg: [{ key: "0,35", value: "22" }],
  }));
  expect(await screen.findByText("Справочники сохранены целиком")).toBeInTheDocument();
});

it("shows the versioned technology snapshot as a table of its own columns", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path.startsWith("/api/references/tech-operations?")) return json({
      metadata: {
        schema_version: 14,
        source_updated_at: "2026-09-20T12:00:00+03:00",
        imported_at: "2026-09-22T10:00:00+03:00",
        record_count: 1,
      },
      records: [{
        id: "20000000-0000-0000-0000-000000000001",
        tab: "ter",
        display_text: "Обжим контакта",
        source_sheet: "БД.ТЕР",
        fields: { terArticle: "ОП-01" },
      }],
    });
    if (path === "/api/references/bases") return json({ bases: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReferencesPage principal={admin} />);
  fireEvent.click(await screen.findByRole("button", { name: /^Терминалы/ }));

  expect(await screen.findByText("ОП-01")).toBeInTheDocument();
  expect(screen.queryByText(/Схема v14/)).toBeNull(); // the one-time import line is gone (owner 29.09.2026)
  expect(screen.getByRole("columnheader", { name: "Артикул контакта" })).toBeInTheDocument();
  // «Операции» is the erptctpl tab; БД.ОБ / БД.ПРОВ / БД.МАТ are working bases (owner: «с оборудованием то же самое»).
  expect(screen.queryByRole("button", { name: /^Операции/ })).toBeNull();
  for (const kept of [/^Оборудование/, /^Монтажные провода/, /^Прочие кабели/, /^Материалы/]) expect(screen.getByRole("button", { name: kept })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Провода и кабели/ })).toBeNull();
});


it("edits a technology row with the old value and hides editing for non-admins", async () => {
  let patched: unknown;
  const record = { id: "20000000-0000-0000-0000-000000000002", tab: "prov", display_text: "МГТФ", source_sheet: "БД.ПРОВ", fields: { Марка: "МГТФ", Сечение: "0,35" } };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path.startsWith("/api/references/tech-operations/") && init?.method === "PATCH") {
      patched = JSON.parse(String(init.body));
      return json(record);
    }
    if (path.startsWith("/api/references/tech-operations?")) return json({ metadata: null, records: [record] });
    if (path === "/api/references/bases") return json({ bases: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  const { unmount } = render(<ReferencesPage principal={admin} />);
  fireEvent.click(await screen.findByRole("button", { name: "Терминалы" }));
  fireEvent.click((await screen.findAllByText("МГТФ"))[0]); // строка «Терминалов» открывает карточку контакта, правка — из неё
  fireEvent.click(screen.getByRole("button", { name: "Править запись" }));
  fireEvent.change(screen.getByLabelText("Поле Сечение"), { target: { value: "0,5" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить запись" }));

  await waitFor(() => expect(patched).toEqual({ edits: [{ field: "Сечение", old: "0,35", value: "0,5" }] }));
  expect(await screen.findByText("Запись сохранена")).toBeInTheDocument();
  unmount();

  render(<ReferencesPage principal={{ ...admin, roles: ["foreman"], access_level: "foreman", permissions: { erpref: "view" } }} />);
  expect((await screen.findAllByText("МГТФ")).length).toBeGreaterThan(0);
  expect(screen.queryByRole("button", { name: "Править" })).toBeNull();
});

it("offers the create form on an empty tab from the known column schema", async () => {
  let created: unknown;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path === "/api/references/tech-operations" && init?.method === "POST") {
      created = JSON.parse(String(init.body));
      return json({ id: "1", tab: "mat", display_text: "Трубка", source_sheet: "", fields: {} });
    }
    if (path.startsWith("/api/references/tech-operations?")) return json({
      metadata: null, columns: ["Наименование", "Группа"], records: [],
    });
    if (path === "/api/references/bases") return json({ bases: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReferencesPage principal={admin} />);
  fireEvent.click(await screen.findByRole("button", { name: "Коаксиальные разъёмы" }));
  const add = await screen.findByRole("button", { name: "+ Запись" });
  await waitFor(() => expect(add).toBeEnabled());
  fireEvent.click(add);
  fireEvent.change(screen.getByLabelText("Поле Наименование"), { target: { value: "Трубка" } });
  expect(screen.getByLabelText("Поле Группа")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Сохранить запись" }));

  await waitFor(() => expect(created).toEqual({ tab: "coax", fields: { Наименование: "Трубка", Группа: "" } }));
});

it("opens the retired «Ø изоляции по маркам» key (kab_awg) on the hookup wires and has no tab of that name", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path.startsWith("/api/references/tech-operations?")) return json({ metadata: null, records: [] });
    if (path === "/api/references/bases") return json({ bases: [] });
    if (path === "/api/references/cables") return json({ columns: [], counts: { coax: 0, wire: 0, multi: 0, other: 0 }, rows: [], gauges: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReferencesPage principal={admin} initialTab="kab_awg" />);
  expect(await screen.findByRole("button", { name: /^Монтажные провода/ })).toHaveAttribute("aria-pressed", "true");
  expect(screen.queryByRole("button", { name: /Ø изоляции по маркам/ })).toBeNull();
  expect(screen.queryByLabelText("Артикул провода")).toBeNull();
});

it("shows record counts on the tabs and opens a base from its tab", async () => {
  const opened: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path === "/api/references/bases") return json({ bases: [
      { id: "coax", label: "БД.КОАКС — коаксиал", editable: true, icon: "📡", sub: "разделка", count: 42 },
    ] });
    if (path.startsWith("/api/references/tech-operations?")) {
      opened.push(path);
      return json({ metadata: null, records: [] });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReferencesPage principal={admin} />);
  const prov = await screen.findByRole("button", { name: "Коаксиальные разъёмы" });
  await waitFor(() => expect(prov).toHaveTextContent("42"));
  expect(screen.queryByRole("button", { name: "Операции" })).toBeNull(); // no erptctpl grant
  fireEvent.click(prov);
  await waitFor(() => expect(opened.some((path) => path.includes("tab=coax"))).toBe(true));
});

it("opens the templates tab by its own key without the reference book (ID-3)", async () => {
  const paths: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    paths.push(path);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [
      { id: "cut", code: "CUT_WIRE_auto", side: "", title: "CUT_WIRE_auto | Резка", name: "Резка", category: "Резка", steps: 1, tooling: 1 },
    ] });
    if (path === "/api/techcards/connector-families") return json({ items: [], match: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReferencesPage principal={{ ...admin, roles: ["foreman"], access_level: "foreman", permissions: { erptctpl: "view" } }} />);
  expect(await screen.findByText("CUT_WIRE_auto")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "База данных", level: 1 })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Терминалы" })).toBeNull();
  await waitFor(() => expect(screen.getByRole("button", { name: /^Операции/ })).toHaveTextContent("1"));
  expect(paths.every((path) => path.startsWith("/api/techcards/"))).toBe(true); // nothing of the reference book
});

it("lets the buyer edit supplier maps and shows no separate ГОСТ→AWG map (it is the СПР.КАБ table)", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({
      suppliers: [{ key: "склад", value: "Склад" }], countries: [], gost_awg: [{ key: "0,35", value: "22" }],
      can_edit: { suppliers: true, gost_awg: false },
    });
    if (path === "/api/references/bases") return json({ bases: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReferencesPage principal={{ ...admin, roles: ["tech_lead"], access_level: "tech_lead", permissions: { erpref: "view", procurement: "edit" } }} initialTab="suppliers" />);
  expect(await screen.findByLabelText("Поставщик 1")).toBeEnabled();
  expect(screen.queryByRole("button", { name: "ГОСТ → AWG" })).toBeNull();
  expect(screen.getByRole("button", { name: "Сохранить справочники" })).toBeInTheDocument();
});

it("takes the connector's D1–D3 from the cable in СПР.КАБ and keeps them out of the edit form", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path === "/api/references/bases") return json({ bases: [] });
    if (path.startsWith("/api/references/tech-operations?tab=kab_coax")) return json({
      metadata: null, columns: ["Кабель", "D1", "D2", "D3"],
      records: [{ id: "k", tab: "kab_coax", display_text: "RG58", source_sheet: "", fields: { Кабель: "RG58", D1: "1", D2: "2,95", D3: "3,5" } }],
    });
    if (path.startsWith("/api/references/tech-operations?tab=coax")) return json({
      metadata: null, columns: ["Артикул", "Провод", "D1", "D2", "D3", "L1"],
      records: [
        { id: "c1", tab: "coax", display_text: "BNC", source_sheet: "БД.КОАКС", fields: { Артикул: "BNC-C58P", Провод: "RG-58", D1: "9", D2: "9", D3: "9", L1: "5" } },
        { id: "c2", tab: "coax", display_text: "N", source_sheet: "БД.КОАКС", fields: { Артикул: "N-111/10D", Провод: "10D-FB", D1: "", D2: "", D3: "", L1: "7" } },
      ],
    });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReferencesPage principal={admin} initialTab="coax" />);
  expect(await screen.findByText("1 · 2,95 · 3,5")).toBeInTheDocument(); // D of RG58, not the connector's own «9»
  expect(screen.queryByText(/9 · 9/)).toBeNull();
  expect(screen.getAllByText("нет у кабеля")).toHaveLength(1);
  expect(screen.getByRole("columnheader", { name: "D1 · D2 · D3 кабеля" })).toBeInTheDocument();
  // Строка открывает карточку разъёма, правка — кнопкой в ней.
  fireEvent.click(screen.getByText("BNC-C58P"));
  fireEvent.click(screen.getByRole("button", { name: "Править запись" }));
  expect(screen.getByLabelText("Поле L1")).toBeInTheDocument();
  expect(screen.queryByLabelText("Поле D1")).toBeNull();
});

it("shows БД.ОБ as cards by type with every column and the БД.ОП operations that use the unit", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path === "/api/references/bases") return json({ bases: [] });
    if (path.includes("tab=ob")) return json({ metadata: null, records: [
      { id: "e1", tab: "ob", display_text: "EW-05F", source_sheet: "БД.ОБ", fields: { baseValue: "EW-05F+M/Автомат", "Тип": "Оборудование", "Инв. номер": "ОБ.015", "Наменование": "Автомат мерной резки", "Производитель": "Eraser", "Для базы": "EW-05F+M/Автомат", "Стоимость": "900 000,00" } },
      { id: "e2", tab: "ob", display_text: "Линейка", source_sheet: "БД.ОБ", fields: { "Тип": "Инструмент", "Инв. номер": "ОБ.048", "Наменование": "Линейка", "Для базы": "Линейка" } },
    ] });
    if (path.includes("tab=op")) return json({ metadata: null, records: [
      { id: "o1", tab: "op", display_text: "Резка", source_sheet: "БД.ОП", fields: { opNumber: "CUT_WIRE_auto", "Машина": "EW-05F+M/Автомат" } },
    ] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReferencesPage principal={admin} initialTab="ob" />);
  expect(await screen.findByText("ОБ.015")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Все · 2" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Инструмент · 1" })).toBeInTheDocument();
  // «Операций» — число операций БД.ОП, что ссылаются на единицу.
  await waitFor(() => expect(within(screen.getByText("ОБ.015").closest("tr") as HTMLElement).getAllByRole("cell").map((cell) => cell.textContent)).toContain("1"));

  fireEvent.click(screen.getByText("ОБ.015"));
  expect(screen.getByText("900 000,00")).toBeInTheDocument();
  expect(screen.getByText("CUT_WIRE_auto")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: /^Править/ }).length).toBeGreaterThan(0);
  expect(screen.getByRole("button", { name: "Ещё такой же" })).toBeInTheDocument();
  expect(screen.queryByRole("columnheader", { name: "Код терминалов" })).toBeNull(); // only crimp tools and dies have terminal codes
});

it("shows the terminal-code column in the instances table of a crimp tool only", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path === "/api/references/bases") return json({ bases: [] });
    if (path.includes("tab=ob")) return json({ metadata: null, records: [
      { id: "c1", tab: "ob", display_text: "Пресс", source_sheet: "БД.ОБ", fields: { baseValue: "Пресс", "Тип": "Обжимной инструмент", "Инв. номер": "ОБ.101", "Наменование": "Пресс", "Для базы": "Пресс", "Код терминалов": "KT-77" } },
    ] });
    if (path.includes("tab=op")) return json({ metadata: null, records: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReferencesPage principal={admin} initialTab="ob" />);
  fireEvent.click(await screen.findByText("ОБ.101"));
  expect(await screen.findByRole("columnheader", { name: "Код терминалов" })).toBeInTheDocument();
});

it("БД-1: terminals show Russian headers and «гнездо/штырь» instead of the sheet's English words", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path === "/api/references/bases") return json({ bases: [] });
    if (path.startsWith("/api/references/tech-operations?")) return json({
      metadata: null,
      columns: ["Product Name", "Series", "Тип\n контакта", "Артикул\n контакта (REEL)", "Высота обжима проводника , мм", "Усилие обрыва контакта, N", "От AWG", "До AWG", "Максисмальная сила тока, А", "Ссылка на DATASHEET на сайте производителя"],
      records: [
        { id: "t1", tab: "ter", display_text: "DS1070", source_sheet: "БД.ТЕР", fields: { "Product Name": "DS1070", "Series": "I-DS1070", "Тип\n контакта": "Female", "Артикул\n контакта (REEL)": "T-DS1070-SC600", "Высота обжима проводника , мм": "28 AWG: 0,52–0,58; 26 AWG: 0,56–0,62", "Усилие обрыва контакта, N": "28 AWG: 10\n26 AWG: 20", "От AWG": "30", "До AWG": "26", "Максисмальная сила тока, А": "3", "Ссылка на DATASHEET на сайте производителя": "https://example.test/ds1070-datasheet-with-a-very-long-address.pdf" } },
        { id: "t2", tab: "ter", display_text: "DS1073", source_sheet: "БД.ТЕР", fields: { "Product Name": "DS1073", "Series": "I-DS1073", "Тип\n контакта": "Male (Pin)", "Артикул\n контакта (REEL)": "T-DS1073-PC800", "Высота обжима проводника , мм": "", "Усилие обрыва контакта, N": "", "От AWG": "22", "До AWG": "22", "Максисмальная сила тока, А": "3", "Ссылка на DATASHEET на сайте производителя": "" } },
      ],
    });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReferencesPage principal={admin} initialTab="ter" />);
  expect(await screen.findByText("T-DS1070-SC600")).toBeInTheDocument();
  const headers = screen.getAllByRole("columnheader").map((header) => header.textContent?.trim());
  for (const russian of ["Наименование", "Серия", "Тип контакта", "Артикул контакта (катушка)", CRIMP_COLUMN_TITLE, "AWG", "Максимальная сила тока, А"]) expect(headers).toContain(russian);
  expect(headers.join("|")).not.toMatch(/Product|Series|REEL|DATASHEET|, N/);
  // Обжим: по строке на сечение, сечение один раз; пустой обжим — одно «—»; «От/До AWG» — одна колонка «30–26».
  const crimpRows = Array.from(document.querySelectorAll(".crimp-row")).map((row) => row.textContent);
  expect(crimpRows).toEqual(["28 AWG0,52–0,5810 Н", "26 AWG0,56–0,6220 Н"]);
  expect(screen.getByText("30–26")).toBeInTheDocument();
  expect(headers).not.toContain("От AWG");
  // Female/Male → гнездо/штырь (значение в форме правки остаётся как на листе).
  expect(screen.getByText("гнездо")).toBeInTheDocument();
  expect(screen.getByText("штырь")).toBeInTheDocument();
  expect(screen.queryByText("Female")).toBeNull();
  // Ссылка на даташит по умолчанию скрыта — строка остаётся одной строкой, колонка включается в «Колонки».
  expect(headers.join("|")).not.toMatch(/Даташит/);
});

it("БД-5: equipment shows «ЦАР» whatever case the sheet wrote and lists units as table rows", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path === "/api/references/bases") return json({ bases: [] });
    if (path.includes("tab=ob")) return json({ metadata: null, records: [
      { id: "e1", tab: "ob", display_text: "x", source_sheet: "БД.ОБ", fields: { "Тип": "Инструмент", "Инв. номер": "ОБ.048", "Наменование": "Дифманометр", "Расположение": "Цар", "Для базы": "DT-8920" } },
    ] });
    if (path.includes("tab=op")) return json({ metadata: null, records: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
  render(<ReferencesPage principal={admin} initialTab="ob" />);
  const row = (await screen.findByText("ОБ.048")).closest("tr") as HTMLElement;
  expect(within(row).getByText("ЦАР")).toBeInTheDocument();
  expect(screen.queryByText("Цар")).toBeNull();
  fireEvent.click(row);
  expect(screen.getByText("Экземпляры")).toBeInTheDocument();
  expect(within(screen.getByText("Экземпляры").closest(".tr-block") as HTMLElement).getByText("ЦАР")).toBeInTheDocument();
});

const unitFields = (inv: string, extra: Record<string, string> = {}) => ({
  "Тип": "Оборудование", "Инв. номер": inv, "Наменование": "Станция паяльная", "Артикул": "8620DX-Pro", "Производитель": "SUGON",
  "Расположение": "ЦАР", "Для базы": "8620DX-Pro/Станция паяльная", ...extra,
});

it("collapses the units of one model into a row with a count and an inventory range; search finds a unit", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path === "/api/references/bases") return json({ bases: [] });
    if (path.includes("tab=ob")) return json({ metadata: null, records: [
      ...[1, 2, 3].map((k) => ({ id: `u${k}`, tab: "ob", display_text: "x", source_sheet: "БД.ОБ", fields: unitFields(`ОБ.070.${k}`) })),
      { id: "s1", tab: "ob", display_text: "y", source_sheet: "БД.ОБ", fields: { "Тип": "Оборудование", "Инв. номер": "ОБ.044", "Наменование": "Блок питания", "Артикул": "APS3010H", "Для базы": "APS3010H/Блок питания" } },
    ] });
    if (path.includes("tab=op")) return json({ metadata: null, records: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
  render(<ReferencesPage principal={admin} initialTab="ob" />);
  expect(await screen.findByText("ОБ.070.1–3")).toBeInTheDocument();
  expect(screen.getByText("3 шт.")).toBeInTheDocument();
  expect(screen.getByText("ОБ.044")).toBeInTheDocument();
  expect(screen.queryByText("ОБ.070.2")).toBeNull();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "ОБ.070.2" } });
  expect(screen.getByText("ОБ.070.1–3")).toBeInTheDocument();
  expect(screen.queryByText("ОБ.044")).toBeNull();
  fireEvent.click(screen.getByText("ОБ.070.1–3"));
  expect(screen.getByText("Экземпляры")).toBeInTheDocument();
  expect(screen.getByText("ОБ.070.2")).toBeInTheDocument();
});

it("the new record form asks for the essentials first, proposes the inventory number and keeps the rest folded", async () => {
  const posted: Array<Record<string, unknown>> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path === "/api/references/bases") return json({ bases: [] });
    if (path.startsWith("/api/references/tech-operations/equipment-number")) return json({ inventory: "ОБ.105", row: "190" });
    if (path.includes("tab=ob")) return json({ metadata: null, columns: ["№", "Тип", "Инв. номер", "Артикул", "Наменование", "Производитель", "Расположение", "Стоимость", "Для базы"], records: [
      { id: "s1", tab: "ob", display_text: "y", source_sheet: "БД.ОБ", fields: unitFields("ОБ.104") },
    ] });
    if (path.includes("tab=op")) return json({ metadata: null, records: [] });
    if (path === "/api/references/tech-operations" && init?.method === "POST") {
      posted.push(JSON.parse(String(init.body)));
      return json({ id: "n1", tab: "ob", display_text: "x", source_sheet: "", fields: {} });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  render(<ReferencesPage principal={admin} initialTab="ob" />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Новая запись" }));
  await waitFor(() => expect(screen.getByLabelText("Инв. номер")).toHaveValue("ОБ.105"));
  expect(screen.getByLabelText("Тип")).toHaveValue("Оборудование");
  expect(screen.queryByLabelText("Поле Стоимость")).not.toBeVisible();
  expect(screen.queryByLabelText("Поле №")).toBeNull();
  fireEvent.change(screen.getByLabelText("Наименование"), { target: { value: "Новый станок" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить запись" }));
  await waitFor(() => expect(posted).toHaveLength(1));
  expect((posted[0].fields as Record<string, string>)["Наменование"]).toBe("Новый станок");
  expect((posted[0].fields as Record<string, string>)["Инв. номер"]).toBe("ОБ.105");
});

it("БД.ОБ: the machine card is a side panel — the list stays, another row switches it, × and Esc close it, a reload reopens it", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path === "/api/references/bases") return json({ bases: [] });
    if (path.includes("tab=ob")) return json({ metadata: null, records: [
      { id: "a1", tab: "ob", display_text: "x", source_sheet: "БД.ОБ", fields: unitFields("ОБ.010", { "Срок окупаемости": "14 мес.", "Стоимость": "900 000,00" }) },
      { id: "b1", tab: "ob", display_text: "y", source_sheet: "БД.ОБ", fields: { "Тип": "Оборудование", "Инв. номер": "ОБ.044", "Наменование": "Блок питания", "Артикул": "APS3010H", "Для базы": "APS3010H/Блок питания" } },
    ] });
    if (path.includes("tab=op")) return json({ metadata: null, records: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
  window.history.replaceState({}, "", "/");
  const { unmount } = render(<ReferencesPage principal={admin} initialTab="ob" />);
  fireEvent.click(await screen.findByText("ОБ.010"));
  const panel = screen.getByRole("complementary", { name: "Карточка оборудования" });
  expect(within(panel).getByText("Станция паяльная")).toBeInTheDocument();
  expect(within(panel).getByText("8620DX-Pro · SUGON · Оборудование · ЦАР")).toBeInTheDocument();
  expect(within(panel).getByText("расчёт")).toBeInTheDocument(); // окупаемость is derived
  expect(screen.getByText("Блок питания")).toBeInTheDocument(); // the list is still there
  expect(window.location.search).toContain("equipment=a1");

  fireEvent.click(screen.getByText("Блок питания"));
  expect(within(screen.getByRole("complementary", { name: "Карточка оборудования" })).getByText("APS3010H · Оборудование")).toBeInTheDocument();
  expect(window.location.search).toContain("equipment=b1");

  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("complementary", { name: "Карточка оборудования" })).toBeNull();
  expect(window.location.search).not.toContain("equipment");
  expect(screen.getByText("ОБ.010")).toBeInTheDocument();

  fireEvent.click(screen.getByText("ОБ.010"));
  fireEvent.click(screen.getByRole("button", { name: "Закрыть карточку" }));
  expect(screen.queryByRole("complementary", { name: "Карточка оборудования" })).toBeNull();

  fireEvent.click(screen.getByText("ОБ.010"));
  unmount();
  render(<ReferencesPage principal={admin} initialTab="ob" />);
  expect(await screen.findByRole("complementary", { name: "Карточка оборудования" })).toBeInTheDocument();
  window.history.replaceState({}, "", "/");
});

it("БД.ОБ: a picked row opens the edit form in the right panel, the list stays, saving patches the record and returns to the card", async () => {
  const patched: Array<Record<string, unknown>> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path === "/api/references/bases") return json({ bases: [] });
    if (path.includes("tab=ob")) return json({ metadata: null, records: [
      { id: "a1", tab: "ob", display_text: "x", source_sheet: "БД.ОБ", fields: unitFields("ОБ.010") },
      { id: "b1", tab: "ob", display_text: "y", source_sheet: "БД.ОБ", fields: { "Тип": "Оборудование", "Инв. номер": "ОБ.044", "Наменование": "Блок питания", "Для базы": "X/Блок питания" } },
    ] });
    if (path.includes("tab=op")) return json({ metadata: null, records: [] });
    if (path === "/api/references/tech-operations/crimp-codes") return json({ missing: [] });
    if (path === "/api/references/tech-operations/a1" && init?.method === "PATCH") {
      patched.push(JSON.parse(String(init.body)));
      return json({ id: "a1", tab: "ob", display_text: "x", source_sheet: "БД.ОБ", fields: {} });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  window.history.replaceState({}, "", "/");
  render(<ReferencesPage principal={admin} initialTab="ob" />);
  fireEvent.click(await screen.findByText("ОБ.010"));
  fireEvent.click(within(screen.getByRole("complementary", { name: "Карточка оборудования" })).getByRole("button", { name: /^Править/ }));
  const panel = screen.getByRole("complementary", { name: "Правка оборудования" });
  expect(within(panel).getByLabelText("Артикул")).toHaveValue("8620DX-Pro");
  expect(screen.getByText("Блок питания")).toBeInTheDocument(); // the list stays on the left
  expect(screen.queryByRole("complementary", { name: "Карточка оборудования" })).toBeNull();
  fireEvent.change(within(panel).getByLabelText("Артикул"), { target: { value: "9999" } });
  fireEvent.click(within(panel).getByRole("button", { name: "Сохранить запись" }));
  await waitFor(() => expect(patched).toHaveLength(1));
  expect((patched[0].edits as Array<{ field: string; value: string }>).some((edit) => edit.field === "Артикул" && edit.value === "9999")).toBe(true);
  await waitFor(() => expect(screen.queryByRole("complementary", { name: "Правка оборудования" })).toBeNull());
  expect(screen.getByRole("complementary", { name: "Карточка оборудования" })).toBeInTheDocument();
  window.history.replaceState({}, "", "/");
});
