import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReferencesPage } from "./ReferencesPage";
import { HookupWireTable, parseTempRange } from "./CableStructure";
import { CableTable, rangeText } from "./CableTables";
import { normalizeColors } from "./wireColors";
import { CATEGORIES } from "./cableCategory";
import type { CableRow, Principal } from "./api";

/* «База данных» → «Кабели и провода» (owner 04.10.2026): coax / hookup / other cables, D1–D3 edited in the coax tab,
   a row moved between the lists, and the regrouped tab bar with a count on every chip. */

const admin: Principal = {
  user: { id: "10000000-0000-0000-0000-000000000001", email: "admin@example.test", name: "Администратор", department: "ERP" },
  roles: ["admin"],
  access_level: "admin",
  permissions: { erpref: "edit", erptctpl: "edit", procurement: "edit" },
};
const COLUMNS = ["Марка", "Тип провода", "Сечение, мм2", "AWG", "Число жил", "Наружный диаметр, мм", "Волновое сопротивление, Ом"];
const wire = (id: string, fields: Record<string, string>) => ({ id, tab: "prov" as const, display_text: fields["Марка"], source_sheet: "БД.ПРОВ", fields });
const dia = (id: string, cable: string, D1: string, D2: string, D3: string) => ({ id, cable, D1, D2, D3 });
const ROWS: CableRow[] = [
  { id: "w-rg316", kind: "coax", auto_kind: "coax", reason: "тип «коаксиальный»", manual: false, cable: "RG316",
    record: wire("w-rg316", { "Марка": "RG316", "Тип провода": "коаксиальный", "Число жил": "1", "Наружный диаметр, мм": "2,45", "Волновое сопротивление, Ом": "50" }),
    dia: dia("d-316", "RG-316", "0,5", "1,48", "1,95"), dia_hint: null },
  { id: "w-rg58au", kind: "coax", auto_kind: "coax", reason: "тип «коаксиальный»", manual: false, cable: "RG-58 A/U",
    record: wire("w-rg58au", { "Марка": "RG-58 A/U", "Тип провода": "коаксиальный", "Число жил": "1", "Наружный диаметр, мм": "4,95" }),
    dia: null, dia_hint: dia("d-58", "RG58", "1", "2,95", "3,5") },
  { id: "dia:d-58", kind: "coax", auto_kind: "coax", reason: "есть в таблице D1 · D2 · D3", manual: false, cable: "RG58",
    record: null, dia: dia("d-58", "RG58", "1", "2,95", "3,5"), dia_hint: null },
  { id: "w-ul", kind: "wire", auto_kind: "wire", reason: "одна жила", manual: false, cable: "UL1061 22AWG",
    record: wire("w-ul", { "Марка": "UL1061 22AWG", "Тип провода": "монтажный", "Сечение, мм2": "0,33", "AWG": "22", "Число жил": "1", "Наружный диаметр, мм": "1,3", "Цвета": "чёрный" }),
    dia: null, dia_hint: null,
    hookup: { series: "UL1061", gauge_id: "g22", insulation: "ПВХ", outer_d_mm: 1.3, voltage_v: 300, temp_min_c: -10, temp_max_c: 80 } },
  { id: "w-pvam", kind: "wire", auto_kind: "wire", reason: "одна жила", manual: false, cable: "ПВАМ 0,50",
    record: wire("w-pvam", { "Марка": "ПВАМ 0,50", "Тип провода": "силовой", "Сечение, мм2": "0,5", "Число жил": "1" }),
    dia: null, dia_hint: null, hookup: null, hookup_problem: "",
    hookup_suggestion: { series: "ПВАМ", gauge_id: "g20", insulation: "", outer_d_mm: null, voltage_v: 48, temp_min_c: null, temp_max_c: null } },
  { id: "w-agr", kind: "wire", auto_kind: "wire", reason: "тип «силиконовый»", manual: false, cable: "AGR2 Core 0,3",
    record: wire("w-agr", { "Марка": "AGR2 Core 0,3", "Тип провода": "силиконовый", "Сечение, мм2": "0,3" }),
    dia: null, dia_hint: null, hookup: null, hookup_problem: "сечения 0,3 мм² нет в таблице сечений",
    hookup_suggestion: { series: "AGR2 Core", gauge_id: "", insulation: "", outer_d_mm: null, voltage_v: null, temp_min_c: null, temp_max_c: null } },
  { id: "w-ksp", kind: "multi", auto_kind: "multi", reason: "жил: 4", manual: false, cable: "КСПВГ 4x0,20",
    record: wire("w-ksp", { "Марка": "КСПВГ 4x0,20", "Тип провода": "многожильный", "Число жил": "4", "Наружный диаметр, мм": "3,6" }),
    dia: null, dia_hint: null },
  { id: "w-hv", kind: "other", auto_kind: "other", reason: "тип не указан", manual: false, cable: "Проволока",
    record: wire("w-hv", { "Марка": "Проволока" }), dia: null, dia_hint: null },
];
const GAUGES = [
  { id: "g22", awg: "22", section_mm2: 0.35, conductor_d_mm: 0.64, strands: "", label: "22 AWG · 0,35 мм²", wires: 1 },
  { id: "g20", awg: "20", section_mm2: 0.5, conductor_d_mm: 0.81, strands: "", label: "20 AWG · 0,5 мм²", wires: 0 },
  { id: "g07", awg: "", section_mm2: 0.07, conductor_d_mm: null, strands: "", label: "0,07 мм²", wires: 0 },
];
ROWS[0].card = {
  id: "c-316", name: "RG-316", family: "Гибкий тонкий", verified: false, confidence: "не проверено", changes: [], sources: [],
  layers: [{ layer: "core", label: "Жила", material: "SCCS", diameter_mm: 0.51 }],
  connectors: [{ family: "SMA", max_ghz: 3, max_note: "", span_to_ghz: null }, { family: "BNC", max_ghz: 2, max_note: "2–3 ГГц", span_to_ghz: 2 }],
  markers: [
    { freq_mhz: 433, peak: false, vswr_max: 1.1, vswr_ref: 1.4, note: "строже паспорта сборки" },
    { freq_mhz: 2450, peak: false, vswr_max: 1.22, vswr_ref: 1.4, note: "" },
    { freq_mhz: null, peak: true, vswr_max: 1.25, vswr_ref: null, note: "" },
  ],
  attenuation: [
    { freq_mhz: 433, kind: "nominal", value: 0.548, unit: "dB/m", ref_length_m: 1, temp_c: null, loss_db_m: 0.548, derived: false, interpolated: true, source: "", basis: "интерполировано (лог-лог) между 400 и 1000 МГц" },
    { freq_mhz: 2450, kind: "typical", value: 50.3, unit: "dB/100ft", ref_length_m: 30.48, temp_c: 20, loss_db_m: 1.6503, derived: true, interpolated: false, source: "https://example.test/rg316.pdf", basis: "" },
  ],
  span_from_mhz: 10, span_to_ghz: 3, span_note: "", fc_ghz: 65.3, fc_note: "calc (D 1.52, d 0.51, Vp 69.5 %)",
  max_operating_ghz: 3, max_operating_note: "3 (MIL M17/113)", notes: "Одинарный экран выше 3 ГГц излучает.",
  params: { impedance_ohm: 50, max_power_w: 173, max_power_at_mhz: 1000 }, param_notes: { impedance_ohm: "50 ±2" },
};
ROWS[0].card.verified = true;
ROWS[0].card.confidence = "средняя";
ROWS[0].card_exact = true;
function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

type Call = { path: string; method: string; body: unknown };

function mockApi(calls: Call[] = [], rows: CableRow[] = ROWS) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    if (path === "/api/references/maps") return json({ suppliers: [{ key: "склад", value: "Склад" }], countries: [], gost_awg: [], can_edit: { suppliers: true, gost_awg: true } });
    if (path === "/api/references/bases") return json({
      bases: [], tabs: { ob: 192, ter: 528, coax: 181, kab_awg: 22, mat: 115, kab_coax: 30, prov: 139 },
      cables: { coax: 47, wire: 52, multi: 9, other: 56 }, gauges: 24,
    });
    if (path === "/api/references/cables") return json({ columns: [...COLUMNS, "Цвета"], counts: { coax: 3, wire: 3, multi: 1, other: 1 }, rows, gauges: GAUGES });
    if (path === "/api/references/coverings") return json({ counts: {}, titles: {}, rows: [] });
    if (path.endsWith("/hookup")) return json({ hookup: rows[4].hookup_suggestion, record: rows[4].record });
    if (path.startsWith("/api/references/wire-gauges")) return json(GAUGES[0]);
    if (path === "/api/references/cables/diameters") return json(dia("d-new", "x", "", "", ""));
    if (path.startsWith("/api/references/cables/")) return json(rows[0]);
    if (path.startsWith("/api/references/tech-operations/") && method === "PATCH") return json(rows[0].record);
    if (path.startsWith("/api/references/tech-operations?tab=op")) return json({ metadata: null, records: [
      { id: "o1", tab: "op", display_text: "Резка", source_sheet: "БД.ОП", fields: { opNumber: "CUT_WIRE_auto" } },
      { id: "o2", tab: "op", display_text: "Пайка", source_sheet: "БД.ОП", fields: { opNumber: "SDR_PCB" } },
    ] });
    if (path.startsWith("/api/references/tech-operations?")) return json({ metadata: null, records: [] });
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [
      { id: "cut-a", code: "CUT_WIRE_auto", side: "A", title: "CUT_WIRE_auto | Резка (A)", name: "Резка", category: "Резка", steps: 1, tooling: 1 },
      { id: "cut-b", code: "CUT_WIRE_auto", side: "B", title: "CUT_WIRE_auto | Резка (B)", name: "Резка", category: "Резка", steps: 1, tooling: 1 },
      { id: "strip", code: "STRIP_WIRE", side: "", title: "STRIP_WIRE | Разделка", name: "Разделка", category: "Разделка", steps: 1, tooling: 1 },
    ] });
    if (path === "/api/techcards/connector-families") return json({ items: [{ family: "mf", title: "Micro-Fit", sample: "43025", housing: {} }, { family: "jst", title: "JST", sample: "XH", housing: {} }], match: null });
    throw new Error(`Unexpected request: ${method} ${path}`);
  });
  return calls;
}

afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); });
// The coax table shows its key columns only (owner 19.10); a test that reads another column shows them all, as «Колонки» does.
const showAllCoax = (hidden: string[] = []) => window.localStorage.setItem("erp-table:db:cables:coax:5-2", JSON.stringify({ hidden }));
// what the table before the card kept in «Колонки»
const WAS_HIDDEN = ["Сечение, мм2", "AWG", "Число жил", "__reason", "Тип провода", "Изоляция", "Экран", "Цвета", "Источник данных", "__span", "__op", "__fc", "__kind"];

it("groups the tabs by what the shop picks from and shows a count on every chip", async () => {
  mockApi();
  render(<ReferencesPage principal={admin} initialTab="mat" />);
  const nav = screen.getByRole("navigation", { name: "Вкладки базы данных" });
  const groups = [...nav.querySelectorAll(".db-tab-group")].map((group) => ({
    title: group.querySelector(".db-tab-group-title")?.textContent,
    tabs: [...group.querySelectorAll("button")].map((button) => button.firstChild?.textContent),
  }));
  expect(groups).toEqual([
    { title: "Техкарты", tabs: ["Операции", "Оборудование"] },
    { title: "Разъёмы и контакты", tabs: ["Разъёмы", "Коаксиальные разъёмы", "Терминалы"] },
    { title: "Кабели и провода", tabs: ["Коаксиальные кабели", "Монтажные провода", "Многожильные кабели", "Прочие кабели"] },
    // owner 15.10.2026: «Материалы» расформированы; the old key opens «Оболочки»
    { title: "Материалы", tabs: ["Оболочки", "Расходники"] },
    { title: "Соответствия", tabs: ["Домен → поставщик", "Изготовитель → страна", "Сечения AWG ↔ мм²"] },
  ]);
  const chip = (name: string) => within(nav).getByRole("button", { name: new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`) });
  const expected: Array<[string, string]> = [
    ["Операции", "3"], // CUT_WIRE_auto (A/B — one card), STRIP_WIRE, SDR_PCB from БД.ОП
    ["Оборудование", "192"], ["Разъёмы", "2"], ["Коаксиальные разъёмы", "181"], ["Терминалы", "528"],
    ["Коаксиальные кабели", "47"], ["Монтажные провода", "52"], ["Сечения AWG ↔ мм²", "24"], ["Многожильные кабели", "9"],
    ["Прочие кабели", "56"],
    ["Домен → поставщик", "1"], ["Изготовитель → страна", "0"],
  ];
  for (const [name, n] of expected) await waitFor(() => expect(chip(name).querySelector(".db-tab-count")).toHaveTextContent(new RegExp(`^${n}$`)));
  expect(screen.queryByText("СПР.КАБ")).toBeNull();
});

it("opens the old tab keys on the tabs that replaced them", async () => {
  mockApi();
  const { unmount } = render(<ReferencesPage principal={admin} initialTab="kab_coax" />);
  expect(await screen.findByRole("button", { name: /^Коаксиальные кабели/ })).toHaveAttribute("aria-pressed", "true");
  unmount();
  window.localStorage.setItem("erp-db-tab", "prov");
  render(<ReferencesPage principal={admin} />);
  expect(await screen.findByRole("button", { name: /^Монтажные провода/ })).toHaveAttribute("aria-pressed", "true");
});

it("shows each cable in its own list with the parameters of that list", async () => {
  mockApi();
  render(<ReferencesPage principal={admin} initialTab="cab_coax" />);

  // coax: D1–D3 of the generator's row next to the cable's own parameters; a D-table-only cable is a row too
  expect(await screen.findByText("RG316")).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "D1 жила, мм" })).toBeInTheDocument();
  expect(screen.getByText("0,5")).toBeInTheDocument();
  expect(screen.queryByRole("columnheader", { name: "D3 оплётка, мм" })).toBeNull(); // D2 and D3 are in «Колонки» and in the card
  expect(screen.getAllByText("RG58").length).toBeGreaterThan(0);
  expect(screen.getByRole("columnheader", { name: "Волновое сопротивление, Ом" })).toBeInTheDocument();
  expect(screen.queryByText("UL1061 22AWG")).toBeNull();
  expect(screen.queryByText("КСПВГ 4x0,20")).toBeNull();

  // the frequencies are in «Колонки» and in the card, not in the table by default
  for (const name of ["Диапазон частот", "Диапазон VNA", "Частота среза, ГГц"]) expect(screen.queryByRole("columnheader", { name })).toBeNull();

  // hook-up wires: the conductor comes from the size table; an unparsed wire says so
  fireEvent.click(screen.getByRole("button", { name: /^Монтажные провода/ }));
  expect(await screen.findByText("UL1061 22AWG")).toBeInTheDocument();
  expect(screen.queryByText("RG316")).toBeNull();
  const cell = (cable: string, header: string) => {
    const row = screen.getByText(cable).closest("tr") as HTMLElement;
    const index = screen.getAllByRole("columnheader").findIndex((head) => head.textContent === header);
    return row.querySelectorAll("td")[index];
  };
  // «Серия» is the group header (owner 16.10), hidden; the materials follow «Ø наружный».
  expect(screen.queryByRole("columnheader", { name: "Серия" })).toBeNull();
  expect(cell("UL1061 22AWG", "Сечение, мм²")).toHaveTextContent(/^0,35$/); // the size row, not the БД.ПРОВ 0,33
  expect(cell("UL1061 22AWG", "Ø жилы, мм")).toHaveTextContent(/^0,64$/);
  expect(cell("UL1061 22AWG", "Температура, °C")).toHaveTextContent("−10 … +80");
  expect(cell("AGR2 Core 0,3", "Марка провода")).toHaveTextContent("не разобран");
  expect(screen.getByRole("button", { name: /Не разобраны · 2/ })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /^Многожильные кабели/ }));
  expect(await screen.findByText("КСПВГ 4x0,20")).toBeInTheDocument();
  expect(screen.getByText("КСПВГ")).toBeInTheDocument(); // owner 15.10.2026: one group per mark, not «КСПВ / КСПВГ»
  expect(screen.getByRole("columnheader", { name: "Число жил" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /^Прочие кабели/ }));
  expect(await screen.findByText("Проволока")).toBeInTheDocument();
  expect(screen.queryByText("КСПВГ 4x0,20")).toBeNull();
});

describe("coax column order (owner 15.10: «сначала конструктивные характеристики — диаметры, потом материал…»)", () => {
  const OWN = ["Артикул поставщика", "Цена поставщика, ₽/м", "Дата цены", "Материал жилы", "Материал диэлектрика", "Материал экрана", "Материал оболочки"];
  const BASE = [...COLUMNS, "Изоляция", "Экран", "Стандарт", "Производители", "Поставщик", "Цвета", "Источник данных"];
  const VISIBLE = ["Марка кабеля", "D1 жила, мм", "Наружный диаметр, мм", "Волновое сопротивление, Ом", "Поставщик", "Артикул поставщика", "Цена поставщика, ₽/м"];
  const ORDER = [
    "Марка кабеля", "D1 жила, мм", "D2 диэлектрик, мм", "D3 оплётка, мм", "Наружный диаметр, мм",
    "Материал жилы", "Материал диэлектрика", "Материал экрана", "Материал оболочки", "Волновое сопротивление, Ом",
    // owner 19.10: «Параметры» of the card — table columns after the impedance
    "Погонная ёмкость, пФ/м", "Радиус изгиба неподвижно, мм", "Радиус изгиба многократно, мм", "Напряжение макс., В", "Масса, г/м",
    "Диапазон частот", "Стандарт", "Производители", "Поставщик", "Артикул поставщика", "Цена поставщика, ₽/м", "Дата цены",
    "Карточка",
  ];
  const withColumns = (columns: string[], own: string[]) => {
    mockApi();
    const answer = vi.mocked(globalThis.fetch).getMockImplementation()!;
    vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => String(input) === "/api/references/cables"
      ? json({ columns, custom_columns: own, counts: { coax: 3, wire: 3, multi: 1, other: 1 }, rows: ROWS, gauges: GAUGES })
      : answer(input, init));
  };
  const heads = () => screen.getAllByRole("columnheader").map((head) => head.textContent ?? "").filter(Boolean);

  it("shows only the key columns by default (owner 19.10: the rest is in «Колонки» and in the card)", async () => {
    withColumns([...BASE, ...OWN], OWN);
    render(<ReferencesPage principal={admin} initialTab="cab_coax" />);
    await screen.findByText("RG316");
    expect(heads()).toEqual(VISIBLE);
  });

  it("keeps construction, materials, impedance, one range, standard, maker, then supplier, article and price apart in «Колонки»", async () => {
    showAllCoax(WAS_HIDDEN);
    withColumns([...BASE, ...OWN], OWN);
    render(<ReferencesPage principal={admin} initialTab="cab_coax" />);
    await screen.findByText("RG316");
    expect(heads()).toEqual(ORDER); // «Изоляция», «Экран» and the card's frequencies are kept in «Колонки», hidden
  });

  it("does not draw the material columns before they are added", async () => {
    showAllCoax(WAS_HIDDEN);
    withColumns([...BASE, "Артикул поставщика", "Цена поставщика, ₽/м", "Дата цены"], ["Артикул поставщика", "Цена поставщика, ₽/м", "Дата цены"]);
    render(<ReferencesPage principal={admin} initialTab="cab_coax" />);
    await screen.findByText("RG316");
    expect(heads()).toEqual(ORDER.filter((name) => !name.startsWith("Материал")));
  });

  it("ignores a coax layout saved before the card (the old key showed every column)", async () => {
    window.localStorage.setItem("erp-table:db:cables:coax:5", JSON.stringify({ order: ["__card", "cable"], hidden: [] }));
    withColumns([...BASE, ...OWN], OWN);
    render(<ReferencesPage principal={admin} initialTab="cab_coax" />);
    await screen.findByText("RG316");
    expect(heads()).toEqual(VISIBLE);
  });

  it("keeps the coax materials off the multicore list; its supply columns stay apart", async () => {
    withColumns([...BASE, ...OWN, "Материал изоляции жилы"], [...OWN, "Материал изоляции жилы"]);
    render(<ReferencesPage principal={admin} initialTab="cab_coax" />);
    await screen.findByText("RG316");
    fireEvent.click(screen.getByRole("button", { name: /^Многожильные кабели/ }));
    await screen.findByText("КСПВГ 4x0,20");
    const shown = heads();
    // owner 15.10: «у многожильных нужно материал жилы, материал изоляции жилы, материал экрана если есть, материал оболочки»
    expect(shown.filter((name) => name.startsWith("Материал"))).toEqual(["Материал жилы", "Материал изоляции жилы", "Материал экрана", "Материал оболочки"]);
    expect(shown).not.toContain("Изоляция");
    expect(shown).not.toContain("Экран");
    expect(shown).toEqual(expect.arrayContaining(["Поставщик", "Артикул поставщика", "Цена поставщика, ₽/м"]));
  });

  it("fills a parameter column from БД.ПРОВ, else from the card; a family card's value is muted «как у»", async () => {
    const family: CableRow = { ...ROWS[1], card: { ...ROWS[0].card!, name: "RG-58", params: { capacitance_pf_m: 101, weight_g_m: 38 } }, card_exact: false };
    const exact: CableRow = { ...ROWS[0], card: { ...ROWS[0].card!, params: { impedance_ohm: 51, capacitance_pf_m: 95, bend_static_mm: 10 } } };
    showAllCoax();
    mockApi([], [exact, family, ROWS[2]]);
    render(<ReferencesPage principal={admin} initialTab="cab_coax" />);
    await screen.findByText("RG316");
    const cell = (cable: string, header: string) => {
      const line = screen.getByText(cable, { selector: "td.key strong" }).closest("tr") as HTMLElement;
      const index = screen.getAllByRole("columnheader").findIndex((head) => head.textContent === header);
      return line.querySelectorAll("td")[index] as HTMLElement;
    };
    expect(cell("RG316", "Волновое сопротивление, Ом")).toHaveTextContent(/^50$/); // the БД.ПРОВ cell beats the card's 51
    expect(cell("RG316", "Погонная ёмкость, пФ/м")).toHaveTextContent(/^95$/);
    expect(within(cell("RG316", "Погонная ёмкость, пФ/м")).getByText("95")).toHaveAttribute("title", "по карточке RG-316");
    expect(cell("RG316", "Радиус изгиба неподвижно, мм")).toHaveTextContent(/^10$/);
    expect(cell("RG316", "Масса, г/м")).toHaveTextContent(/^—$/);
    const borrowed = within(cell("RG-58 A/U", "Погонная ёмкость, пФ/м")).getByText("101");
    expect(borrowed).not.toHaveClass("muted"); // one colour in the table (owner 19.10); whose value — in the tooltip
    expect(borrowed).toHaveAttribute("title", "как у RG-58");
    expect(cell("RG58", "Погонная ёмкость, пФ/м")).toHaveTextContent(/^—$/); // no card, no cell
  });

  it("shows the supplier's page of a cable as «открыть» next to the supplier columns", async () => {
    const linked: CableRow = { ...ROWS[0], record: { ...ROWS[0].record!, fields: { ...ROWS[0].record!.fields, "Ссылка": "https://gsm-repiteri.ru/prodazha/cable/kabel-rg-316-50-om" } } };
    const columns = [...BASE, "Артикул поставщика", "Ссылка", "Цена поставщика, ₽/м", "Дата цены"];
    showAllCoax();
    mockApi([], [linked, ROWS[1], ROWS[2]]);
    const answer = vi.mocked(globalThis.fetch).getMockImplementation()!;
    vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => String(input) === "/api/references/cables"
      ? json({ columns, custom_columns: ["Артикул поставщика", "Ссылка", "Цена поставщика, ₽/м", "Дата цены"], counts: { coax: 3, wire: 0, multi: 0, other: 0 }, rows: [linked, ROWS[1], ROWS[2]], gauges: GAUGES })
      : answer(input, init));
    render(<ReferencesPage principal={admin} initialTab="cab_coax" />);
    await screen.findByText("RG316");
    const shown = heads();
    expect(shown.slice(shown.indexOf("Поставщик"), shown.indexOf("Поставщик") + 5)).toEqual(["Поставщик", "Артикул поставщика", "Ссылка", "Цена поставщика, ₽/м", "Дата цены"]);
    const link = screen.getByRole("link", { name: "Открыть страницу RG316 у поставщика" });
    expect(link).toHaveTextContent(/^открыть$/);
    expect(link).toHaveAttribute("href", "https://gsm-repiteri.ru/prodazha/cable/kabel-rg-316-50-om");
  });

  it("gives the range of the VNA span, else DC up to the rated maximum, never the cut-off", () => {
    const card = ROWS[0].card!;
    expect(rangeText(card)).toBe("10 МГц – 3 ГГц");
    expect(rangeText({ ...card, span_to_ghz: null, max_operating_ghz: 6 })).toBe("DC – 6 ГГц");
    expect(rangeText({ ...card, span_to_ghz: null, max_operating_ghz: null, fc_ghz: 65.3 })).toBe("");
    expect(rangeText(null)).toBe("");
  });
});

it("lists coax cables like a catalogue: series groups in order, base mark before its variants, groups collapse", async () => {
  const lmr: CableRow = { id: "w-lmr", kind: "coax", auto_kind: "coax", reason: "тип «коаксиальный»", manual: false, cable: "LMR-195",
    record: wire("w-lmr", { "Марка": "LMR-195" }), dia: null, dia_hint: null };
  mockApi([], [lmr, ...ROWS]);
  render(<ReferencesPage principal={admin} initialTab="cab_coax" />);
  const rg = await screen.findByRole("button", { name: /RG58 · 2/ });
  // owner 19.10: a group per mark family (RG58 = RG-58 and its A/U); single-row families of a head with no mate go to «Прочие», last
  const lmrGroup = screen.getByRole("button", { name: /Прочие · 2/ });
  expect(rg.compareDocumentPosition(lmrGroup) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  const marks = screen.getAllByRole("row").map((line) => line.querySelector("td.key")?.textContent).filter(Boolean);
  expect(marks).toEqual(["RG58", "RG-58 A/U", "LMR-195", "RG316"]); // RG58 = RG-58: the base mark, then its A/U
  expect(rg).toHaveAttribute("aria-expanded", "true");
  fireEvent.click(rg);
  expect(rg).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("RG-58 A/U")).toBeNull();
  expect(screen.getByText("LMR-195")).toBeInTheDocument();
});

it("opens a coax card a tester reads at the VNA: markers, the source's loss, compatible connectors — nothing else", async () => {
  const connector = (id: string, fields: Record<string, string>) => ({ id, fields: { coaxWire: "RG-316", ...fields } });
  const withConnectors: CableRow = { ...ROWS[0], connectors: [
    connector("k1", { coaxArticle: "S-111L", coaxType: "SMA-male", "Серия": "SMA", "Монтаж": "обжимной", "Ссылка": "https://gsm-repiteri.ru/a", "Цена, ₽": "250" }),
    connector("k2", { coaxArticle: "S-111L NGT", coaxType: "SMA-male", "Серия": "SMA", "Монтаж": "обжимной", "Ссылка": "https://gsm-repiteri.ru/b", "Цена, ₽": "310,5" }),
    connector("k3", { coaxArticle: "BNC-C316P", coaxType: "BNC-male" }),
    connector("k4", { coaxArticle: "SMA-C316J", coaxType: "SMA-female" }),
  ] };
  mockApi([], [withConnectors, ...ROWS.slice(1)]);
  render(<ReferencesPage principal={admin} initialTab="cab_coax" />);
  fireEvent.click(await screen.findByText("RG316"));
  const card = screen.getByRole("region", { name: "Карточка кабеля RG-316" });
  expect(within(card).getByText("проверено по datasheet · достоверность средняя")).toBeInTheDocument();
  // the rated operating limit and the TE11 cut-off are two numbers, never one
  const electrical = screen.getByRole("region", { name: "Электрические" });
  expect(within(electrical).getByText("Рабочая частота").parentElement).toHaveTextContent(/Рабочая частота\s*до 3 ГГц/);
  expect(within(electrical).getByText("Частота среза").parentElement).toHaveTextContent(/≈ 65,3 ГГц/);
  const markers = within(card).getByRole("table", { name: "Маркеры VNA" });
  expect(within(markers).getByText("КСВН — норма приёмки (предложено)")).toBeInTheDocument();
  expect(within(markers).getByRole("columnheader", { name: "Паспорт ≤" })).toBeInTheDocument();
  const rows = within(markers).getAllByRole("row").slice(1).map((row) => row.textContent);
  expect(rows).toEqual(["433 МГц1,11,4≈ 0,55", "2,45 ГГц1,221,41,65", "Peak — максимум по диапазону1,25——"]);
  // owner 19.10: construction, the card file's connector list and the parameters are not in the card any more
  for (const name of ["Конструкция", "Разъёмы", "Параметры кабеля"]) expect(within(card).queryByRole("table", { name })).toBeNull();
  expect(within(card).queryByText("Волновое сопротивление")).toBeNull();
  // compatible connectors come from БД.КОАКС: series → type, mount, supplier, price, count; SMA before BNC (the shop's order)
  const connectors = within(card).getByRole("table", { name: "Совместимые разъёмы" });
  expect(within(connectors).getByText("из БД.КОАКС · 4")).toBeInTheDocument();
  expect(within(connectors).getAllByRole("row").slice(1).map((row) => [...row.querySelectorAll("td")].map((cell) => cell.textContent)))
    .toEqual([
      ["SMA", "SMA-female", "—", "—", "—", "1"],
      ["", "SMA-male", "обжимной", "gsm-repiteri.ru", "250–310,5", "2"],
      ["BNC", "BNC-male", "—", "—", "—", "1"],
    ]);
  expect(within(connectors).getByText("SMA-male")).toHaveAttribute("title", "S-111L, S-111L NGT");
  const datasheet = within(card).getByRole("table", { name: "Затухание по источнику" });
  expect(datasheet).toHaveTextContent("50,3 дБ/100 фут");
  expect(within(datasheet).queryByRole("columnheader", { name: "t, °C" })).toBeInTheDocument(); // one point has a temperature
  expect(datasheet).toHaveTextContent("интерполяция 400–1000 МГц");
  expect(within(datasheet).getByText("пересчёт")).toBeInTheDocument();
  expect(within(datasheet).queryByRole("columnheader", { name: "Единица" })).toBeNull(); // the unit sits next to the source value, not in a column of its own
  expect(within(datasheet).getByRole("link", { name: "example.test" })).toHaveAttribute("href", "https://example.test/rg316.pdf");
});

describe("coax cable card window (owner 19.10: «нормальная карточка, открывающаяся, с нужными данными»)", () => {
  const FIELDS = {
    "Тип провода": "коаксиальный", "Материал жилы": "SCCS", "Жила: проволок × диаметр, мм": "7 × 0,17", "Материал диэлектрика": "FEP",
    "Материал экрана": "посеребрённая медь", "Материал оболочки": "FEP", "Поставщик": "gsm-repiteri.ru", "Артикул поставщика": "RG316-50",
    "Ссылка": "https://gsm-repiteri.ru/prodazha/cable/rg316", "Цена поставщика, ₽/м": "86", "Дата цены": "2026-10-19",
    "Стандарт": "MIL-C-17/113", "Темп. от, °C": "-55", "Темп. до, °C": "200", "Напряжение, В": "1000",
    "Радиус изгиба неподвижно, мм": "10", "Погонная ёмкость, пФ/м": "96",
  };
  const full: CableRow = { ...ROWS[0], record: { ...ROWS[0].record!, fields: { ...ROWS[0].record!.fields, ...FIELDS } } };
  const section = (name: string) => within(screen.getByRole("dialog", { name: "Карточка кабеля RG316" })).getByRole("region", { name });
  const value = (scope: HTMLElement, label: string) => within(scope).getByText(label).parentElement!.querySelector("dd")!.textContent;

  it("opens as a window on a row click with the cable's data in sections: construction, electrical, mechanics, then the VNA blocks", async () => {
    mockApi([], [full, ...ROWS.slice(1)]);
    render(<ReferencesPage principal={admin} initialTab="cab_coax" />);
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(await screen.findByText("RG316"));
    const window_ = within(screen.getByRole("dialog", { name: "Карточка кабеля RG316" })).getByLabelText("Основное");
    // header: family, supplier, article, link, price with its date, standard
    expect(value(window_, "Тип / семейство")).toBe("Гибкий тонкий");
    expect(value(window_, "Поставщик")).toBe("gsm-repiteri.ru");
    expect(value(window_, "Артикул поставщика")).toBe("RG316-50");
    expect(within(window_).getByRole("link", { name: "Открыть страницу RG316 у поставщика" })).toHaveAttribute("href", "https://gsm-repiteri.ru/prodazha/cable/rg316");
    expect(value(window_, "Цена")).toBe("86 ₽/м от 19.10.2026");
    expect(value(window_, "Стандарт")).toBe("MIL-C-17/113");
    const construction = section("Конструкция");
    expect(value(construction, "Жила (D1)")).toBe("SCCS · 7 × 0,17 · 0,5 мм");
    expect(value(construction, "Диэлектрик (D2)")).toBe("FEP · 1,48 мм");
    expect(value(construction, "Экран (D3)")).toBe("посеребрённая медь · 1,95 мм");
    expect(value(construction, "Оболочка (наружный)")).toBe("FEP · 2,45 мм");
    const electrical = section("Электрические");
    expect(value(electrical, "Волновое сопротивление")).toBe("50 Ом");
    expect(value(electrical, "Погонная ёмкость")).toBe("96 пФ/м");
    expect(value(electrical, "Диапазон частот")).toBe("10 МГц – 3 ГГц");
    expect(value(electrical, "Частота среза")).toBe("≈ 65,3 ГГц");
    expect(value(electrical, "Затухание")).toBe("≈ 0,55 на 433 МГц · 1,65 на 2,45 ГГц (дБ/м)");
    expect(value(electrical, "Напряжение")).toBe("1000 В");
    const mechanics = section("Механика и условия");
    expect(value(mechanics, "Радиус изгиба неподвижно")).toBe("10 мм");
    expect(value(mechanics, "Температура")).toBe("−55 … +200 °C");
    // the existing blocks stay
    expect(screen.getByRole("table", { name: "Маркеры VNA" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Совместимые разъёмы" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Закрыть карточку" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows «—» for what the cable lacks, never an empty value or a raw key", async () => {
    mockApi();
    render(<ReferencesPage principal={admin} initialTab="cab_coax" />);
    fireEvent.click(await screen.findByText("RG-58 A/U"));
    const dialog = screen.getByRole("dialog", { name: "Карточка кабеля RG-58 A/U" });
    const window_ = within(dialog).getByLabelText("Основное");
    for (const [name, label] of [["Конструкция", "Жила (D1)"], ["Конструкция", "Диэлектрик (D2)"], ["Электрические", "Погонная ёмкость"],
      ["Электрические", "Диапазон частот"], ["Механика и условия", "Масса"], ["Механика и условия", "Температура"]]) {
      expect(value(within(dialog).getByRole("region", { name }), label)).toBe("—");
    }
    expect(value(window_, "Цена")).toBe("—");
    expect(value(window_, "Поставщик")).toBe("—");
    expect(dialog.textContent).not.toMatch(/undefined|null|NaN/);
  });

  it("opens the existing editor behind «Править» and comes back to the card", async () => {
    mockApi([], [full, ...ROWS.slice(1)]);
    render(<ReferencesPage principal={admin} initialTab="cab_coax" />);
    fireEvent.click(await screen.findByText("RG316"));
    fireEvent.click(screen.getByRole("button", { name: "Править" }));
    expect(screen.getByLabelText("Поле Поставщик")).toHaveValue("gsm-repiteri.ru");
    expect(screen.queryByRole("region", { name: "Конструкция" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(screen.getByRole("region", { name: "Конструкция" })).toBeInTheDocument();
  });
});

it("lists no compatible connectors when БД.КОАКС has none for the cable, with or without a card", async () => {
  mockApi([], [{ ...ROWS[0], connectors: [] }, { ...ROWS[1], connectors: [] }, ROWS[2]]);
  render(<ReferencesPage principal={admin} initialTab="cab_coax" />);
  fireEvent.click(await screen.findByText("RG316"));
  expect(within(screen.getByRole("table", { name: "Совместимые разъёмы" })).getByText("Разъёмов в БД.КОАКС нет")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Закрыть карточку" }));
  fireEvent.click(screen.getByText("RG-58 A/U")); // no card: the block stands alone
  expect(screen.queryByRole("region", { name: /Карточка кабеля/ })).toBeNull();
  expect(within(screen.getByRole("table", { name: "Совместимые разъёмы" })).getByText("Разъёмов в БД.КОАКС нет")).toBeInTheDocument();
});

it("names the family when a variant borrows its compatible connectors", async () => {
  const connector = (id: string, fields: Record<string, string>) => ({ id, fields: { coaxWire: "RG-316", ...fields } });
  mockApi([], [{ ...ROWS[0], connectors: [connector("k1", { coaxArticle: "S-111L", coaxType: "SMA-male" })], connectors_family: "RG-316" }, ...ROWS.slice(1)]);
  render(<ReferencesPage principal={admin} initialTab="cab_coax" />);
  fireEvent.click(await screen.findByText("RG316"));
  expect(within(screen.getByRole("table", { name: "Совместимые разъёмы" })).getByText("из БД.КОАКС · как у RG-316 · 1")).toBeInTheDocument();
});

it("structures a hook-up wire from its БД.ПРОВ suggestion and edits a size row", async () => {
  const calls = mockApi();
  render(<ReferencesPage principal={admin} initialTab="cab_wire" />);
  fireEvent.click(await screen.findByText("ПВАМ 0,50"));
  expect(screen.getByLabelText("Жила — сечение из таблицы")).toHaveValue("g20");
  fireEvent.change(screen.getByLabelText("Ø наружный, мм"), { target: { value: "1,6" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(calls.find((call) => call.path === "/api/references/cables/w-pvam/hookup")?.body).toEqual({
    series: "ПВАМ", gauge_id: "g20", insulation: "", outer_d_mm: "1,6", voltage_v: "48", temp_min_c: "", temp_max_c: "", old: null,
  }));
  expect(calls.some((call) => call.method === "PATCH")).toBe(false); // the mirrored cells are written by the server

  fireEvent.click(screen.getByRole("button", { name: /^Сечения AWG/ }));
  fireEvent.click(await screen.findByText("только ГОСТ"));
  fireEvent.change(screen.getByLabelText("Ø жилы, мм"), { target: { value: "0,3" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить сечение" }));
  await waitFor(() => expect(calls.find((call) => call.path === "/api/references/wire-gauges/g07")).toMatchObject({
    method: "PUT", body: { awg: "", section_mm2: "0,07", conductor_d_mm: "0,3", strands: "" },
  }));
  expect(screen.queryByRole("button", { name: "Удалить сечение" })).toBeInTheDocument(); // no wire on 0,07
});

it("edits D1–D3 right in the coax tab and copies them from the cable's family", async () => {
  const calls = mockApi();
  render(<ReferencesPage principal={admin} initialTab="cab_coax" />);

  fireEvent.click(await screen.findByText("RG316"));
  expect(screen.queryByLabelText("D2 кабеля")).toBeNull(); // the card first, the editor behind «Править»
  fireEvent.click(screen.getByRole("button", { name: "Править" }));
  fireEvent.change(screen.getByLabelText("D2 кабеля"), { target: { value: "1,5" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(calls.find((call) => call.path === "/api/references/cables/diameters")?.body).toEqual({
    cable: "RG316", dia_id: "d-316", old: { D1: "0,5", D2: "1,48", D3: "1,95" }, value: { D1: "0,5", D2: "1,5", D3: "1,95" },
  }));
  expect(calls.some((call) => call.method === "PATCH")).toBe(false); // nothing else changed
  expect(await screen.findByText("«RG316» сохранён")).toBeInTheDocument();
  await waitFor(() => expect(calls.filter((call) => call.path === "/api/references/cables")).toHaveLength(2)); // reloaded

  calls.length = 0;
  fireEvent.click(screen.getByRole("button", { name: "Закрыть карточку" }));
  fireEvent.click(screen.getByText("RG-58 A/U"));
  fireEvent.click(screen.getByRole("button", { name: "Править" }));
  fireEvent.click(screen.getByRole("button", { name: "Как у RG58: 1 · 2,95 · 3,5" }));
  fireEvent.click(screen.getAllByRole("button", { name: "Сохранить" }).at(-1)!);
  await waitFor(() => expect(calls.find((call) => call.path === "/api/references/cables/diameters")?.body).toEqual({
    cable: "RG-58 A/U", dia_id: null, old: { D1: "", D2: "", D3: "" }, value: { D1: "1", D2: "2,95", D3: "3,5" },
  }));
});

it("moves a cable to another list from its editor", async () => {
  const calls = mockApi();
  render(<ReferencesPage principal={admin} initialTab="cab_wire" />);

  fireEvent.click(await screen.findByText("ПВАМ 0,50"));
  expect(screen.getByText("по правилу: одна жила")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Вкладка"), { target: { value: "other" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

  await waitFor(() => expect(calls.find((call) => call.path === "/api/references/cables/w-pvam/kind")).toMatchObject({ method: "PUT", body: { kind: "other" } }));
  expect(await screen.findByText("«ПВАМ 0,50» перенесён во вкладку «Прочие кабели»")).toBeInTheDocument();
});

it("shows the cable lists read-only without the reference edit right", async () => {
  mockApi();
  render(<ReferencesPage principal={{ ...admin, roles: ["foreman"], access_level: "foreman", permissions: { erpref: "view" } }} initialTab="cab_coax" />);
  fireEvent.click(await screen.findByText("RG316"));
  expect(screen.getByRole("dialog", { name: "Карточка кабеля RG316" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Править" })).toBeNull();
  expect(screen.queryByLabelText("D1 кабеля")).toBeNull();
  expect(screen.queryByRole("button", { name: "+ Запись" })).toBeNull();
});

it("groups the hook-up wires by series with the shared values in the header, sizes by number, colours as dots", () => {
  const gauge = (id: string, section: number) => ({ id, awg: "", section_mm2: section, conductor_d_mm: null, strands: "", label: `${section} мм²`, wires: 1 });
  const wire = (series: string, mark: string, gauge_id: string, colors: string, voltage: number) => ({
    id: mark, kind: "wire" as const, auto_kind: "wire" as const, reason: "", manual: false, cable: mark, dia: null, dia_hint: null,
    record: { id: mark, tab: "prov" as const, display_text: mark, source_sheet: "БД.ПРОВ", fields: { "Марка": mark, "Цвета": colors, "Материал оболочки": "ПВХ-пластикат" } },
    hookup: { series, gauge_id, insulation: "ПВХ", outer_d_mm: 2, voltage_v: voltage, temp_min_c: -50, temp_max_c: 65 },
  });
  const data = {
    columns: ["Марка", "Цвета", "Материал оболочки"], counts: { coax: 0, wire: 4, multi: 0, other: 0 }, gauges: [gauge("s10", 10), gauge("s16", 16), gauge("s25", 2.5), gauge("s4", 4)],
    rows: [wire("ПуГВ", "ПуГВ 10", "s10", "синий", 450), wire("ПуГВ", "ПуГВ 16", "s16", "", 450), wire("ПуГВ", "ПуГВ 2,5", "s25", "белый, жёлто-зелёный", 450), wire("ПуГВ", "ПуГВ 4", "s4", "красный", 450)],
  } as unknown as Parameters<typeof HookupWireTable>[0]["data"];
  render(<HookupWireTable data={data} canEdit={false} onSaved={() => undefined} onError={() => undefined} kindSelect={() => null} />);

  // no «Серия» column (the group header says it); the materials right after «Ø наружный» (owner 16.10)
  const heads = screen.getAllByRole("columnheader").map((head) => head.textContent);
  expect(heads).not.toContain("Серия");
  expect(heads[heads.indexOf("Ø наружный, мм") + 1]).toBe("Материал оболочки");
  // one header: series, the jacket material (not the rudimentary «Изоляция»), what all sizes share, the number of sizes
  expect(screen.getByText("ПуГВ · ПВХ-пластикат · 450 В · −50 … +65 °C")).toBeInTheDocument();
  // not repeated in the rows
  expect(screen.queryAllByText("450")).toHaveLength(0);
  expect(screen.queryAllByText("ПВХ-пластикат", { selector: "td *" })).toHaveLength(0);
  // 2,5 · 4 · 10 · 16, not the text order 10 · 16 · 2,5 · 4
  const marks = [...document.querySelectorAll("tbody tr:not(.group-row) strong")].map((node) => node.textContent);
  expect(marks).toEqual(["ПуГВ 2,5", "ПуГВ 4", "ПуГВ 10", "ПуГВ 16"]);
  // colours: a dot per colour with its name; no colours — a dash
  expect(screen.getByRole("img", { name: "жёлто-зелёный" })).toBeInTheDocument();
  expect(screen.getAllByRole("img", { name: "красный" })).toHaveLength(1);
  const row16 = screen.getByText("ПуГВ 16").closest("tr") as HTMLElement;
  expect(row16.querySelector(".wire-dot")).toBeNull();
  expect(within(row16).getAllByText("—").length).toBeGreaterThan(0);
});

describe("coax cells edited right in the table (owner 14.10: «ещё бы как-то столбцы отредактировать»)", () => {
  const cellOf = (cable: string, header: string) => {
    const line = screen.getByText(cable, { selector: "td.key strong" }).closest("tr") as HTMLElement;
    const index = screen.getAllByRole("columnheader").findIndex((head) => head.textContent === header);
    return line.querySelectorAll("td")[index] as HTMLElement;
  };
  const type = (cell: HTMLElement, value: string) => {
    fireEvent.click(cell);
    fireEvent.keyDown(cell, { key: "Enter" });
    const input = within(cell).getByRole("textbox");
    fireEvent.change(input, { target: { value } });
    fireEvent.keyDown(input, { key: "Enter" });
    return input;
  };

  it("shows «Производители», then «Поставщик» (owner 15.10) and writes a БД.ПРОВ cell with its old value, then reads the list again", async () => {
    showAllCoax();
    const calls = mockApi();
    render(<ReferencesPage principal={admin} initialTab="cab_coax" />);
    await screen.findByText("RG316");
    const heads = screen.getAllByRole("columnheader").map((head) => head.textContent);
    expect(heads.indexOf("Поставщик")).toBe(heads.indexOf("Производители") + 1);

    type(cellOf("RG316", "Волновое сопротивление, Ом"), "75");
    await waitFor(() => expect(calls.find((call) => call.method === "PATCH")).toMatchObject({
      path: "/api/references/tech-operations/w-rg316",
      body: { edits: [{ field: "Волновое сопротивление, Ом", old: "50", value: "75" }] },
    }));
    await waitFor(() => expect(calls.filter((call) => call.path === "/api/references/cables")).toHaveLength(2)); // reloaded
    expect(screen.queryByText(/сохранён/)).toBeNull(); // no banner per cell
    expect(screen.queryByText("Карточка RG-316")).toBeNull(); // the row did not open
  });

  it("writes D1–D3 of a cell the way the editor does: the old three and the new one", async () => {
    showAllCoax();
    const calls = mockApi();
    render(<ReferencesPage principal={admin} initialTab="cab_coax" />);
    await screen.findByText("RG316");
    type(cellOf("RG316", "D2 диэлектрик, мм"), "1,5");
    await waitFor(() => expect(calls.find((call) => call.path === "/api/references/cables/diameters")?.body).toEqual({
      cable: "RG316", dia_id: "d-316", old: { D1: "0,5", D2: "1,48", D3: "1,95" }, value: { D1: "0,5", D2: "1,5", D3: "1,95" },
    }));
  });

  it("keeps the typed value with the reason when the cell was changed meanwhile", async () => {
    mockApi();
    const fetchMock = vi.mocked(globalThis.fetch);
    const answer = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => init?.method === "PATCH"
      ? new Response(JSON.stringify({ detail: "Не записано ничего — отклонено полей: 1; Экран: запись уже поменяли — откройте её заново" }), { status: 409, headers: { "Content-Type": "application/json" } })
      : answer(input, init));
    render(<ReferencesPage principal={admin} initialTab="cab_coax" />);
    await screen.findByText("RG316");
    const input = type(cellOf("RG316", "Наружный диаметр, мм"), "2,5");
    expect(await screen.findByRole("alert")).toHaveTextContent("Ячейку уже поменяли — в таблице свежее значение. Enter запишет ваше, Esc отменит.");
    expect(input).toHaveValue("2,5");
  });

  it("leaves the mark, the card and fields the record lacks to the record window; no edit right — no editable cells", async () => {
    showAllCoax();
    mockApi();
    const { unmount } = render(<ReferencesPage principal={admin} initialTab="cab_coax" />);
    await screen.findByText("RG316");
    expect(cellOf("RG316", "Марка кабеля")).not.toHaveClass("dt-editable");
    expect(cellOf("RG316", "Карточка")).not.toHaveClass("dt-editable");
    expect(cellOf("RG316", "Стандарт")).not.toHaveClass("dt-editable"); // the БД.ПРОВ row has no such cell: the server would refuse it
    expect(cellOf("RG316", "Поставщик")).not.toHaveClass("dt-editable");
    expect(cellOf("RG58", "Стандарт")).not.toHaveClass("dt-editable"); // a D-table-only cable has no БД.ПРОВ row
    expect(cellOf("RG58", "D1 жила, мм")).toHaveClass("dt-editable");
    unmount();

    render(<ReferencesPage principal={{ ...admin, roles: ["foreman"], access_level: "foreman", permissions: { erpref: "view" } }} initialTab="cab_coax" />);
    await screen.findByText("RG316");
    expect(document.querySelector("td.dt-editable")).toBeNull();
  });
});

describe("«+ Столбец» (owner 14.10: «добавить свои столбцы»)", () => {
  it("asks the name in a window, adds the column to the table and lets it be edited in the cell", async () => {
    const calls = mockApi();
    const answer = vi.mocked(globalThis.fetch).getMockImplementation()!;
    let added = false;
    vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/references/tech-operations/columns" && init?.method === "POST") {
        calls.push({ path, method: "POST", body: JSON.parse(String(init.body)) });
        added = true;
        return json({ tab: "prov", name: "Полка", records: 7 });
      }
      if (path === "/api/references/cables" && added) {
        calls.push({ path, method: "GET", body: null });
        const rows = ROWS.map((row) => row.record ? { ...row, record: { ...row.record, fields: { ...row.record.fields, "Полка": "" } } } : row);
        return json({ columns: [...COLUMNS, "Цвета", "Полка"], custom_columns: ["Полка"], counts: { coax: 3, wire: 3, multi: 1, other: 1 }, rows, gauges: GAUGES });
      }
      return answer(input, init);
    });
    render(<ReferencesPage principal={admin} initialTab="cab_coax" />);
    await screen.findByText("RG316");
    expect(screen.queryByRole("columnheader", { name: "Полка" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "+ Столбец" }));
    const dialog = screen.getByRole("dialog", { name: "Новый столбец" });
    expect(within(dialog).getByRole("button", { name: "Добавить столбец" })).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("Название столбца"), { target: { value: "Полка" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Добавить столбец" }));

    expect(await screen.findByRole("columnheader", { name: "Полка" })).toBeInTheDocument();
    expect(calls.find((call) => call.method === "POST")?.body).toEqual({ tab: "prov", name: "Полка" });
    expect(screen.queryByRole("dialog", { name: "Новый столбец" })).toBeNull();

    const line = screen.getByText("RG316", { selector: "td.key strong" }).closest("tr") as HTMLElement;
    const index = screen.getAllByRole("columnheader").findIndex((head) => head.textContent === "Полка");
    const cell = line.querySelectorAll("td")[index] as HTMLElement;
    expect(cell).toHaveClass("dt-editable");
    fireEvent.click(cell);
    fireEvent.keyDown(cell, { key: "Enter" });
    const input = within(cell).getByRole("textbox");
    fireEvent.change(input, { target: { value: "А-12" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(calls.find((call) => call.method === "PATCH")).toMatchObject({
      path: "/api/references/tech-operations/w-rg316", body: { edits: [{ field: "Полка", old: "", value: "А-12" }] },
    }));
  });

  it("shows the server's reason in the window and keeps it open", async () => {
    mockApi();
    const answer = vi.mocked(globalThis.fetch).getMockImplementation()!;
    vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => String(input).endsWith("/tech-operations/columns")
      ? new Response(JSON.stringify({ detail: "Столбец «Цвета» уже есть в таблице" }), { status: 422, headers: { "Content-Type": "application/json" } })
      : answer(input, init));
    render(<ReferencesPage principal={admin} initialTab="cab_coax" />);
    await screen.findByText("RG316");
    fireEvent.click(screen.getByRole("button", { name: "+ Столбец" }));
    fireEvent.change(screen.getByLabelText("Название столбца"), { target: { value: "Цвета" } });
    fireEvent.click(screen.getByRole("button", { name: "Добавить столбец" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Столбец «Цвета» уже есть в таблице");
    expect(screen.getByRole("dialog", { name: "Новый столбец" })).toBeInTheDocument();
  });

  it("is on every cable list and hidden without the edit right", async () => {
    mockApi();
    const { unmount } = render(<ReferencesPage principal={admin} initialTab="cab_coax" />);
    await screen.findByText("RG316");
    for (const list of ["Монтажные провода", "Многожильные кабели", "Прочие кабели", "Коаксиальные кабели"]) {
      fireEvent.click(screen.getByRole("button", { name: new RegExp(`^${list}`) }));
      await waitFor(() => expect(screen.getByRole("button", { name: "+ Столбец" })).toBeInTheDocument());
    }
    unmount();
    render(<ReferencesPage principal={{ ...admin, roles: ["foreman"], access_level: "foreman", permissions: { erpref: "view" } }} initialTab="cab_coax" />);
    await screen.findByText("RG316");
    expect(screen.queryByRole("button", { name: "+ Столбец" })).toBeNull();
  });
});

describe("«Монтажные провода» and the other cable lists across the base (owner 14.10: «пройдись по базе в целом»)", () => {
  const headerIndex = (name: string) => screen.getAllByRole("columnheader").findIndex((head) => head.textContent === name);
  const cellIn = (mark: string, column: string) =>
    screen.getByText(mark, { selector: "td.key strong" }).closest("tr")!.querySelectorAll("td")[headerIndex(column)] as HTMLElement;

  describe("every cell of a parsed wire like a sheet (owner 16.10: «редактирование везде сделать как в экселе»)", () => {
    const old = { series: "UL1061", gauge_id: "g22", insulation: "ПВХ", outer_d_mm: "1,3", voltage_v: "300", temp_min_c: "-10", temp_max_c: "80" };
    const hookupPut = (calls: Call[]) => calls.find((call) => call.method === "PUT" && call.path.endsWith("/hookup"));
    async function open() {
      const calls = mockApi();
      render(<ReferencesPage principal={admin} initialTab="cab_wire" />);
      await screen.findByText("UL1061 22AWG", { selector: "td.key strong" });
      return calls;
    }

    it("picks the size from «Сечения AWG ↔ мм²» in the cell and writes it with the wire's old values", async () => {
      const calls = await open();
      const cell = cellIn("UL1061 22AWG", "Сечение, мм²");
      fireEvent.click(cell);
      fireEvent.keyDown(cell, { key: "Enter" });
      const select = within(cell).getByRole("combobox", { name: "Сечение, мм²: UL1061 22AWG" });
      expect(within(select).getAllByRole("option").map((option) => option.textContent))
        .toEqual(["0,07 мм²", "22 AWG · 0,35 мм² · жила Ø 0,64 мм", "20 AWG · 0,5 мм² · жила Ø 0,81 мм"]);
      expect(select).toHaveValue("g22");
      fireEvent.keyDown(select, { key: "ArrowDown" });
      fireEvent.change(select, { target: { value: "g20" } });
      expect(hookupPut(calls)).toBeUndefined(); // an arrow only moves the pick
      fireEvent.keyDown(select, { key: "Enter" });
      await waitFor(() => expect(hookupPut(calls)?.body).toEqual({ ...old, gauge_id: "g20", old }));
      expect(cellIn("UL1061 22AWG", "Ø жилы, мм")).toHaveClass("dt-computed"); // by the size, not a cell of its own
      expect(cellIn("ПВАМ 0,50", "Сечение, мм²")).not.toHaveClass("dt-editable"); // not parsed — its window sets it up
    });

    it("pastes «20» into AWG as the size row of 20 AWG", async () => {
      const calls = await open();
      const cell = cellIn("UL1061 22AWG", "AWG");
      fireEvent.click(cell);
      fireEvent.paste(cell, { clipboardData: { getData: () => "20" } });
      await waitFor(() => expect(hookupPut(calls)?.body).toEqual({ ...old, gauge_id: "g20", old }));
    });

    it("types the temperature as «−60 … +200» into the pair; a text that is no range is refused in the cell", async () => {
      const calls = await open();
      const cell = cellIn("UL1061 22AWG", "Температура, °C");
      fireEvent.click(cell);
      fireEvent.keyDown(cell, { key: "ж" });
      const input = within(cell).getByRole("textbox");
      fireEvent.change(input, { target: { value: "жарко" } });
      fireEvent.keyDown(input, { key: "Enter" });
      expect(await within(cell).findByRole("alert")).toHaveTextContent("Температура — «от … до», например −60 … +200");
      expect(hookupPut(calls)).toBeUndefined();
      fireEvent.change(input, { target: { value: "−60 … +200" } });
      fireEvent.keyDown(input, { key: "Enter" });
      await waitFor(() => expect(hookupPut(calls)?.body).toEqual({ ...old, temp_min_c: "-60", temp_max_c: "200", old }));
    });

    it("writes the colours typed as text by the palette, «Тип провода» as text and moves the wire from its «Вкладка» cell", async () => {
      const calls = await open();
      const colors = cellIn("UL1061 22AWG", "Цвета");
      fireEvent.click(colors);
      fireEvent.keyDown(colors, { key: "Enter" });
      const input = within(colors).getByRole("textbox");
      expect(input).toHaveValue("чёрный");
      fireEvent.change(input, { target: { value: "чёрный; красный" } });
      fireEvent.keyDown(input, { key: "Enter" });
      await waitFor(() => expect(calls.find((call) => call.method === "PATCH")?.body)
        .toEqual({ edits: [{ field: "Цвета", old: "чёрный", value: normalizeColors("чёрный; красный") }] }));

      fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
      fireEvent.click(screen.getByRole("checkbox", { name: "Тип провода" }));
      fireEvent.click(screen.getByRole("checkbox", { name: "Вкладка" }));
      expect(cellIn("UL1061 22AWG", "Тип провода")).toHaveClass("dt-editable");
      const tab = cellIn("UL1061 22AWG", "Вкладка");
      expect(tab).toHaveTextContent("Монтажные провода");
      fireEvent.doubleClick(tab);
      const select = within(tab).getByRole("combobox");
      fireEvent.pointerDown(select);
      fireEvent.change(select, { target: { value: "multi" } });
      await waitFor(() => expect(calls.find((call) => call.path.endsWith("/kind"))).toEqual({
        path: "/api/references/cables/w-ul/kind", method: "PUT", body: { kind: "multi" },
      }));
      expect(await screen.findByText("«UL1061 22AWG» перенесён во вкладку «Многожильные кабели»")).toBeInTheDocument();
    });
  });

  it("shows every multi-core cable at once with every group open, and a header click sorts inside every group", () => {
    // owner 16.10: «раскрыть до конца … выключить ограничение на 300 шт»; 19.10: «сделай просто вверху столбцов сортировку»
    const many: CableRow[] = Array.from({ length: 380 }, (_, index) => {
      const family = index % 2 ? "КСПВГ" : "МКЭШ";
      const mark = `${family} ${index + 2}x0,5`;
      return { id: `m${index}`, kind: "multi", auto_kind: "multi", reason: "жил", manual: false, cable: mark, dia: null, dia_hint: null,
        record: wire(`m${index}`, { "Марка": mark, "Тип провода": "многожильный", "Число жил": String(index + 2), "Наружный диаметр, мм": String((index % 7) + 3) }) };
    });
    const data = { columns: COLUMNS, counts: { coax: 0, wire: 0, multi: many.length, other: 0 }, rows: many, gauges: [] } as unknown as Parameters<typeof CableTable>[0]["data"];
    const { unmount } = render(<CableTable kind="multi" data={data} canEdit={false} onSaved={() => undefined} onError={() => undefined} />);
    expect(document.querySelectorAll("tbody tr:not(.group-row)")).toHaveLength(380);
    expect(screen.getByText("Показано 380 из 380")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Показать ещё" })).toBeNull();
    const sections = () => {
      const result: Array<{ name: string; diameters: string[] }> = [];
      const head = screen.getAllByRole("columnheader").findIndex((cell) => cell.textContent?.startsWith("Наружный диаметр, мм"));
      for (const line of document.querySelectorAll("tbody tr")) {
        if (line.classList.contains("group-row")) result.push({ name: line.querySelector("strong")?.textContent ?? "", diameters: [] });
        else result[result.length - 1].diameters.push(line.querySelectorAll("td")[head].textContent ?? "");
      }
      return result;
    };
    const before = sections();
    expect(before).toHaveLength(2);
    expect(before.map((group) => group.diameters.length)).toEqual([190, 190]); // both groups open to the end
    const head = screen.getByRole("button", { name: "Наружный диаметр, мм" });
    fireEvent.click(head);
    fireEvent.click(screen.getByRole("button", { name: /Наружный диаметр, мм/ }));
    const after = sections();
    expect(after.map((group) => group.name)).toEqual(before.map((group) => group.name));
    const down = (values: string[]) => [...values].sort((a, b) => Number(b) - Number(a));
    expect(after.map((group) => group.diameters)).toEqual(before.map((group) => down(group.diameters)));
    unmount();
    render(<CableTable kind="multi" data={data} canEdit={false} onSaved={() => undefined} onError={() => undefined} />);
    expect(sections()[0].diameters).toEqual(after[0].diameters); // remembered with the list's layout
  });

  it("picks «Категория» of a cable in its cell from the editor's list, the rule's choice first", async () => {
    const calls = mockApi();
    const row: CableRow = { id: "w-x", kind: "other", auto_kind: "other", reason: "тип не указан", manual: false, cable: "ПВ-3 1,5", dia: null, dia_hint: null,
      record: wire("w-x", { "Марка": "ПВ-3 1,5", "Категория": "" }) };
    const data = { columns: ["Марка", "Категория"], counts: { coax: 0, wire: 0, multi: 0, other: 1 }, rows: [row], gauges: [] } as unknown as Parameters<typeof CableTable>[0]["data"];
    const onSaved = vi.fn();
    render(<CableTable kind="other" data={data} canEdit onSaved={onSaved} onError={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
    const shown = screen.getByRole("checkbox", { name: "Категория" }) as HTMLInputElement;
    if (!shown.checked) fireEvent.click(shown);
    const index = screen.getAllByRole("columnheader").findIndex((head) => head.textContent === "Категория");
    const cell = screen.getByText("ПВ-3 1,5", { selector: "td.key strong" }).closest("tr")!.querySelectorAll("td")[index] as HTMLElement;
    fireEvent.doubleClick(cell);
    const select = within(cell).getByRole("combobox", { name: "Категория: ПВ-3 1,5" });
    const options = within(select).getAllByRole("option").map((option) => option.textContent ?? "");
    expect(options[0]).toMatch(/^по правилу: /);
    expect(options.slice(1)).toEqual([...CATEGORIES]);
    fireEvent.pointerDown(select);
    fireEvent.change(select, { target: { value: CATEGORIES[1] } });
    await waitFor(() => expect(calls.find((call) => call.method === "PATCH")?.body)
      .toEqual({ edits: [{ field: "Категория", old: "", value: CATEGORIES[1] }] }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith("")); // the list is read again
  });

  it("parses a temperature range the way the shop writes it", () => {
    expect(parseTempRange("−60 … +200")).toEqual({ min: "-60", max: "200" });
    expect(parseTempRange("от -40 до +85 °C")).toEqual({ min: "-40", max: "85" });
    expect(parseTempRange("-60...200")).toEqual({ min: "-60", max: "200" });
    expect(parseTempRange("-60 - 200")).toEqual({ min: "-60", max: "200" });
    expect(parseTempRange("-55–125")).toEqual({ min: "-55", max: "125" });
    expect(parseTempRange("… +80")).toEqual({ min: "", max: "80" });
    expect(parseTempRange("-10,5 … 70")).toEqual({ min: "-10,5", max: "70" });
    expect(parseTempRange("  ")).toEqual({ min: "", max: "" });
    expect(() => parseTempRange("200")).toThrow("например −60 … +200");
    expect(() => parseTempRange("+200 … -60")).toThrow("«от» больше «до»");
  });

  it("writes the outer Ø of a parsed wire in the cell with the wire's old values", async () => {
    const calls = mockApi();
    render(<ReferencesPage principal={admin} initialTab="cab_wire" />);
    await screen.findByText("UL1061 22AWG", { selector: "td.key strong" });
    expect(cellIn("ПВАМ 0,50", "Ø наружный, мм")).not.toHaveClass("dt-editable"); // не разобран — правка в окне провода
    const cell = cellIn("UL1061 22AWG", "Ø наружный, мм");
    fireEvent.click(cell);
    fireEvent.keyDown(cell, { key: "Enter" });
    const input = within(cell).getByRole("textbox");
    fireEvent.change(input, { target: { value: "1,4" } });
    fireEvent.keyDown(input, { key: "Enter" });
    const old = { series: "UL1061", gauge_id: "g22", insulation: "ПВХ", outer_d_mm: "1,3", voltage_v: "300", temp_min_c: "-10", temp_max_c: "80" };
    await waitFor(() => expect(calls.find((call) => call.method === "PUT" && call.path.endsWith("/hookup"))).toEqual({
      path: "/api/references/cables/w-ul/hookup", method: "PUT", body: { ...old, outer_d_mm: "1,4", old },
    }));
  });

  it("has «Без Ø» and «Без цветов» chips on the hook-up wires and «Без Ø» on the multi-core cables", async () => {
    mockApi();
    render(<ReferencesPage principal={admin} initialTab="cab_wire" />);
    await screen.findByText("UL1061 22AWG", { selector: "td.key strong" });
    fireEvent.click(screen.getByRole("button", { name: /^Без Ø · 2/ }));
    expect(screen.queryByText("UL1061 22AWG", { selector: "td.key strong" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /^Без цветов · 2/ }));
    expect(screen.getByText("ПВАМ 0,50", { selector: "td.key strong" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /^Многожильные кабели/ }));
    await screen.findByText("КСПВГ 4x0,20", { selector: "td.key strong" });
    expect(screen.queryByRole("button", { name: /^Без Ø/ })).toBeNull(); // у всех многожильных Ø есть — чипа нет
    fireEvent.click(screen.getByRole("button", { name: /^Прочие кабели/ }));
    expect(await screen.findByRole("button", { name: /^Без Ø · 1/ })).toBeInTheDocument();
  });

  it("removes an empty own БД.ПРОВ column from «Колонки» after a confirm window", async () => {
    const calls = mockApi();
    const answer = vi.mocked(globalThis.fetch).getMockImplementation()!;
    vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/references/tech-operations/columns/remove") {
        calls.push({ path, method: "POST", body: JSON.parse(String(init?.body)) });
        return json({ tab: "prov", name: "Полка", records: 7 });
      }
      if (path === "/api/references/cables") {
        calls.push({ path, method: "GET", body: null });
        const rows = ROWS.map((row) => row.record ? { ...row, record: { ...row.record, fields: { ...row.record.fields, "Полка": "" } } } : row);
        return json({ columns: [...COLUMNS, "Цвета", "Полка"], custom_columns: ["Полка"], counts: { coax: 3, wire: 3, multi: 1, other: 1 }, rows, gauges: GAUGES });
      }
      return answer(input, init);
    });
    render(<ReferencesPage principal={admin} initialTab="cab_multi" />);
    await screen.findByText("КСПВГ 4x0,20", { selector: "td.key strong" });
    fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
    fireEvent.click(screen.getByRole("button", { name: "Удалить столбец: Полка" }));
    const dialog = screen.getByRole("dialog", { name: "Удалить столбец" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Удалить столбец" }));
    await waitFor(() => expect(calls.find((call) => call.path.endsWith("/columns/remove"))?.body).toEqual({ tab: "prov", name: "Полка" }));
    expect(await screen.findByText("Столбец «Полка» удалён")).toBeInTheDocument();
  });
});
