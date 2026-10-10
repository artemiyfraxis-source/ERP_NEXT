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

afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); });

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
  fireEvent.click((await screen.findAllByText("МГТФ"))[0]); // строка целиком открывает правку
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

it("shows the AWG table as a read-only grid right after the hookup wires", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path.startsWith("/api/references/tech-operations?tab=kab_awg")) return json({
      metadata: null, columns: ["AWG", "ГОСТ"],
      records: [{ id: "4", tab: "kab_awg", display_text: "22", source_sheet: "СПР.КАБ · AWG", fields: { AWG: "22", ГОСТ: "0,35" } }],
    });
    if (path.startsWith("/api/references/tech-operations?")) return json({ metadata: null, records: [] });
    if (path === "/api/references/bases") return json({ bases: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReferencesPage principal={admin} />);
  fireEvent.click(await screen.findByRole("button", { name: "Ø изоляции по маркам" }));
  expect(await screen.findByText("0,35")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Править" })).toBeNull();
  expect(screen.queryByRole("button", { name: "+ Запись" })).toBeNull();
  expect(screen.queryByText(/СПР\.КАБ/)).toBeNull(); // the code-like group name is gone (owner 04.10.2026)
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
  expect(await screen.findByText("3,5")).toBeInTheDocument(); // D3 of RG58, not the connector's own «9»
  expect(screen.queryByText("9")).toBeNull();
  expect(screen.getAllByText("нет у кабеля")).toHaveLength(3);
  expect(screen.getByRole("columnheader", { name: "D1 кабеля" })).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("row")[1]);
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
  expect(screen.getByRole("button", { name: "Править" })).toBeInTheDocument();
});

it("matches a wire article to AWG, ГОСТ section and Ø by cable type, as the generator does", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path === "/api/references/bases") return json({ bases: [] });
    if (path.includes("tab=kab_awg")) return json({ metadata: null, records: [
      { id: "a1", tab: "kab_awg", display_text: "24", source_sheet: "", fields: { AWG: "24", "ГОСТ": "0,2", "Ø жилы": "0,51", "МГТФ": "1", "UL1061": "1,15", "UL1571": "1,2" } },
      { id: "a2", tab: "kab_awg", display_text: "22", source_sheet: "", fields: { AWG: "22", "ГОСТ": "0,35", "Ø жилы": "0,64", "МГТФ": "1,2", "UL1061": "1,35", "UL1571": "1,4" } },
    ] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReferencesPage principal={admin} initialTab="kab_awg" />);
  fireEvent.change(await screen.findByLabelText("Артикул провода"), { target: { value: "UL1571 22AWG" } });
  expect(screen.getByText("Ø по изоляции UL1571, мм").nextSibling).toHaveTextContent("1,4");
  fireEvent.change(screen.getByLabelText("Артикул провода"), { target: { value: "МГТФ 0,2" } });
  expect(screen.getByText("Ø по изоляции МГТФ, мм").nextSibling).toHaveTextContent("1");
  expect(screen.getByText("AWG", { selector: ".wire-result small" }).nextSibling).toHaveTextContent("24");
  fireEvent.change(screen.getByLabelText("Артикул провода"), { target: { value: "ПуГВ 1,5" } });
  expect(screen.getByText(/Марка не распознана/)).toBeInTheDocument();
});

it("БД-1: terminals show Russian headers and «гнездо/штырь» instead of the sheet's English words", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path === "/api/references/bases") return json({ bases: [] });
    if (path.startsWith("/api/references/tech-operations?")) return json({
      metadata: null,
      columns: ["Product Name", "Series", "Тип\n контакта", "Артикул\n контакта (REEL)", "Усилие обрыва контакта, N", "Максисмальная сила тока, А", "Ссылка на DATASHEET на сайте производителя"],
      records: [
        { id: "t1", tab: "ter", display_text: "DS1070", source_sheet: "БД.ТЕР", fields: { "Product Name": "DS1070", "Series": "I-DS1070", "Тип\n контакта": "Female", "Артикул\n контакта (REEL)": "T-DS1070-SC600", "Усилие обрыва контакта, N": "30", "Максисмальная сила тока, А": "3", "Ссылка на DATASHEET на сайте производителя": "https://example.test/ds1070-datasheet-with-a-very-long-address.pdf" } },
        { id: "t2", tab: "ter", display_text: "DS1073", source_sheet: "БД.ТЕР", fields: { "Product Name": "DS1073", "Series": "I-DS1073", "Тип\n контакта": "Male (Pin)", "Артикул\n контакта (REEL)": "T-DS1073-PC800", "Усилие обрыва контакта, N": "31", "Максисмальная сила тока, А": "3", "Ссылка на DATASHEET на сайте производителя": "" } },
      ],
    });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReferencesPage principal={admin} initialTab="ter" />);
  expect(await screen.findByText("T-DS1070-SC600")).toBeInTheDocument();
  const headers = screen.getAllByRole("columnheader").map((header) => header.textContent?.trim());
  for (const russian of ["Наименование", "Серия", "Тип контакта", "Артикул контакта (катушка)", "Усилие обрыва контакта, Н", "Максимальная сила тока, А"]) expect(headers).toContain(russian);
  expect(headers.join("|")).not.toMatch(/Product|Series|REEL|DATASHEET|, N/);
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
  expect(screen.getByText("Строка БД.ОБ")).toBeInTheDocument();
  expect(screen.getByText("ЦАР", { selector: "dd" })).toBeInTheDocument();
});
