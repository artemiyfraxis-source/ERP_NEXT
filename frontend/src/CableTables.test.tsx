import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReferencesPage } from "./ReferencesPage";
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
    record: wire("w-ul", { "Марка": "UL1061 22AWG", "Тип провода": "монтажный", "Сечение, мм2": "0,33", "AWG": "22", "Число жил": "1", "Наружный диаметр, мм": "1,3" }),
    dia: null, dia_hint: null },
  { id: "w-pvam", kind: "wire", auto_kind: "wire", reason: "одна жила", manual: false, cable: "ПВАМ 0,50",
    record: wire("w-pvam", { "Марка": "ПВАМ 0,50", "Тип провода": "силовой", "Сечение, мм2": "0,5", "Число жил": "1" }),
    dia: null, dia_hint: null },
  { id: "w-ksp", kind: "other", auto_kind: "other", reason: "жил: 4", manual: false, cable: "КСПВГ 4x0,20",
    record: wire("w-ksp", { "Марка": "КСПВГ 4x0,20", "Тип провода": "многожильный", "Число жил": "4", "Наружный диаметр, мм": "3,6" }),
    dia: null, dia_hint: null },
];
const AWG = [
  { id: "a1", tab: "kab_awg", display_text: "22", source_sheet: "", fields: { AWG: "22", "ГОСТ": "0,35", "Ø жилы": "0,64", "МГТФ": "1,2", "UL1061": "1,35", "ПВАМ": "—" } },
  { id: "a2", tab: "kab_awg", display_text: "20", source_sheet: "", fields: { AWG: "20", "ГОСТ": "0,5", "Ø жилы": "0,81", "МГТФ": "1,35", "UL1061": "1,6", "ПВАМ": "2" } },
];

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
      cables: { coax: 47, wire: 52, other: 56 },
    });
    if (path === "/api/references/cables") return json({ columns: COLUMNS, counts: { coax: 3, wire: 2, other: 1 }, rows });
    if (path === "/api/references/cables/diameters") return json(dia("d-new", "x", "", "", ""));
    if (path.startsWith("/api/references/cables/")) return json(rows[0]);
    if (path.startsWith("/api/references/tech-operations/") && method === "PATCH") return json(rows[0].record);
    if (path.startsWith("/api/references/tech-operations?tab=kab_awg")) return json({ metadata: null, records: AWG });
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
    { title: "Кабели и провода", tabs: ["Коаксиальные кабели", "Монтажные провода", "AWG ↔ сечение ↔ Ø", "Прочие кабели"] },
    { title: "Материалы", tabs: ["Материалы"] },
    { title: "Соответствия", tabs: ["Домен → поставщик", "Изготовитель → страна"] },
  ]);
  const chip = (name: string) => within(nav).getByRole("button", { name: new RegExp(`^${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`) });
  const expected: Array<[string, string]> = [
    ["Операции", "3"], // CUT_WIRE_auto (A/B — one card), STRIP_WIRE, SDR_PCB from БД.ОП
    ["Оборудование", "192"], ["Разъёмы", "2"], ["Коаксиальные разъёмы", "181"], ["Терминалы", "528"],
    ["Коаксиальные кабели", "47"], ["Монтажные провода", "52"], ["AWG ↔ сечение ↔ Ø", "22"], ["Прочие кабели", "56"],
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
  expect(screen.getByRole("columnheader", { name: "D3 оплётка, мм" })).toBeInTheDocument();
  expect(screen.getByText("1,95")).toBeInTheDocument();
  expect(screen.getByText("RG58")).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Волновое сопротивление, Ом" })).toBeInTheDocument();
  expect(screen.queryByText("UL1061 22AWG")).toBeNull();
  expect(screen.queryByText("КСПВГ 4x0,20")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: /^Монтажные провода/ }));
  expect(await screen.findByText("UL1061 22AWG")).toBeInTheDocument();
  expect(screen.queryByText("RG316")).toBeNull();
  // Ø by the AWG table, matched like the ETK generator: UL1061 + 22 AWG → 1,35; ПВАМ 0,5 → ГОСТ row 0,5 → 2
  expect(screen.getByRole("columnheader", { name: "Ø по таблице AWG, мм" })).toBeInTheDocument();
  const diaCell = (cable: string) => {
    const row = screen.getByText(cable).closest("tr") as HTMLElement;
    const index = screen.getAllByRole("columnheader").findIndex((head) => head.textContent?.includes("Ø по таблице AWG"));
    return row.querySelectorAll("td")[index];
  };
  await waitFor(() => expect(diaCell("UL1061 22AWG")).toHaveTextContent(/^1,35$/));
  expect(diaCell("ПВАМ 0,50")).toHaveTextContent(/^2$/);

  fireEvent.click(screen.getByRole("button", { name: /^Прочие кабели/ }));
  expect(await screen.findByText("КСПВГ 4x0,20")).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Число жил" })).toBeInTheDocument();
  expect(screen.queryByText("UL1061 22AWG")).toBeNull();
});

it("edits D1–D3 right in the coax tab and copies them from the cable's family", async () => {
  const calls = mockApi();
  render(<ReferencesPage principal={admin} initialTab="cab_coax" />);

  fireEvent.click(await screen.findByText("RG316"));
  fireEvent.change(screen.getByLabelText("D2 кабеля"), { target: { value: "1,5" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(calls.find((call) => call.path === "/api/references/cables/diameters")?.body).toEqual({
    cable: "RG316", dia_id: "d-316", old: { D1: "0,5", D2: "1,48", D3: "1,95" }, value: { D1: "0,5", D2: "1,5", D3: "1,95" },
  }));
  expect(calls.some((call) => call.method === "PATCH")).toBe(false); // nothing else changed
  expect(await screen.findByText("«RG316» сохранён")).toBeInTheDocument();
  await waitFor(() => expect(calls.filter((call) => call.path === "/api/references/cables")).toHaveLength(2)); // reloaded

  calls.length = 0;
  fireEvent.click(screen.getByText("RG-58 A/U"));
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
  expect(screen.queryByLabelText("D1 кабеля")).toBeNull();
  expect(screen.queryByRole("button", { name: "+ Запись" })).toBeNull();
  expect(screen.getByText("тип «коаксиальный»")).toBeInTheDocument();
});
