import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
        tab: "op",
        display_text: "Обжим контакта",
        source_sheet: "БД.ОП",
        fields: { opNumber: "ОП-01" },
      }],
    });
    if (path === "/api/references/bases") return json({ bases: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReferencesPage principal={admin} />);

  expect(await screen.findByText("ОП-01")).toBeInTheDocument();
  expect(screen.getByText(/Схема v14/)).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Номер" })).toBeInTheDocument();
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
  fireEvent.click(await screen.findByRole("button", { name: "Провода" }));
  fireEvent.click(await screen.findByRole("button", { name: "Править" }));
  fireEvent.change(screen.getByLabelText("Поле Сечение"), { target: { value: "0,5" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить запись" }));

  await waitFor(() => expect(patched).toEqual({ edits: [{ field: "Сечение", old: "0,35", value: "0,5" }] }));
  expect(await screen.findByText("Запись сохранена")).toBeInTheDocument();
  unmount();

  render(<ReferencesPage principal={{ ...admin, roles: ["receiver"], permissions: { erpref: "view" } }} />);
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
  fireEvent.click(await screen.findByRole("button", { name: "Материалы" }));
  const add = await screen.findByRole("button", { name: "+ Запись" });
  await waitFor(() => expect(add).toBeEnabled());
  fireEvent.click(add);
  fireEvent.change(screen.getByLabelText("Поле Наименование"), { target: { value: "Трубка" } });
  expect(screen.getByLabelText("Поле Группа")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Сохранить запись" }));

  await waitFor(() => expect(created).toEqual({ tab: "mat", fields: { Наименование: "Трубка", Группа: "" } }));
});

it("shows the СПР.КАБ tables as read-only grids", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path.startsWith("/api/references/tech-operations?tab=kab_coax")) return json({
      metadata: null, columns: ["Кабель", "D1", "D2", "D3"],
      records: [{ id: "3", tab: "kab_coax", display_text: "RG-316", source_sheet: "Коаксиал: D1 · D2 · D3", fields: { Кабель: "RG-316", D1: "0,5", D2: "1,48", D3: "1,95" } }],
    });
    if (path.startsWith("/api/references/tech-operations?tab=kab_awg")) return json({
      metadata: null, columns: ["AWG", "ГОСТ"],
      records: [{ id: "4", tab: "kab_awg", display_text: "22", source_sheet: "Провода: AWG · ГОСТ · Ø по маркам", fields: { AWG: "22", ГОСТ: "0,35" } }],
    });
    if (path === "/api/references/bases") return json({ bases: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReferencesPage principal={admin} />);
  fireEvent.click(await screen.findByRole("button", { name: "Коаксиальные кабели: D1 · D2 · D3" }));
  expect(await screen.findByText("1,95")).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "D3" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Править" })).toBeNull();
  expect(screen.queryByRole("button", { name: "+ Запись" })).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "Провода: AWG · ГОСТ · Ø по маркам" }));
  expect(await screen.findByText("0,35")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Править" })).toBeNull();
});

it("shows record counts on the tabs and opens a base from its tab", async () => {
  const opened: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path === "/api/references/bases") return json({ bases: [
      { id: "prov", label: "БД.ПРОВ — провода", editable: true, icon: "🧵", sub: "марки · сечения", count: 42 },
    ] });
    if (path.startsWith("/api/references/tech-operations?")) {
      opened.push(path);
      return json({ metadata: null, records: [] });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReferencesPage principal={admin} />);
  const prov = await screen.findByRole("button", { name: "Провода" });
  await waitFor(() => expect(prov).toHaveTextContent("42"));
  expect(screen.queryByRole("button", { name: "Шаблоны техкарт" })).toBeNull(); // no erptctpl grant
  fireEvent.click(prov);
  await waitFor(() => expect(opened.some((path) => path.includes("tab=prov"))).toBe(true));
});

it("opens the templates tab by its own key without the reference book (ID-3)", async () => {
  const paths: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    paths.push(path);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [
      { id: "cut", code: "CUT_WIRE_auto", side: "", title: "CUT_WIRE_auto | Резка", name: "Резка", category: "Резка", steps: 1, tooling: 1 },
    ] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReferencesPage principal={{ ...admin, roles: ["master"], permissions: { erptctpl: "view" } }} />);
  expect(await screen.findByText("CUT_WIRE_auto")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "База данных", level: 1 })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Операции" })).toBeNull();
  expect(paths).toEqual(["/api/techcards/templates"]);
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

  render(<ReferencesPage principal={{ ...admin, roles: ["buyer"], permissions: { erpref: "view", procurement: "edit" } }} initialTab="suppliers" />);
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
  expect(screen.getAllByText("нет в СПР.КАБ")).toHaveLength(3);
  expect(screen.getByRole("columnheader", { name: "D1 кабеля" })).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "Править" })[0]);
  expect(screen.getByLabelText("Поле L1")).toBeInTheDocument();
  expect(screen.queryByLabelText("Поле D1")).toBeNull();
});
