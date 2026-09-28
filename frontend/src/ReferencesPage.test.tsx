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
  permissions: { references: "edit" },
};

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => vi.restoreAllMocks());

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
    });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReferencesPage principal={admin} />);
  fireEvent.change(await screen.findByLabelText("Поставщик 1"), { target: { value: "Новый поставщик" } });
  fireEvent.change(screen.getByLabelText("AWG 1"), { target: { value: "23" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить справочники" }));

  await waitFor(() => expect(payload).toEqual({
    suppliers: [{ key: "example.ru", value: "Новый поставщик" }],
    countries: [{ key: "Fraxis", value: "Россия" }],
    gost_awg: [{ key: "0,35", value: "23" }],
  }));
  expect(await screen.findByText("Справочники сохранены целиком")).toBeInTheDocument();
});

it("searches the versioned technology snapshot", async () => {
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
        fields: { code: "ОП-01" },
      }],
    });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReferencesPage principal={admin} />);
  fireEvent.click(await screen.findByRole("button", { name: "База техопераций" }));

  expect(await screen.findByText("Обжим контакта")).toBeInTheDocument();
  expect(screen.getByText(/Схема v14/)).toBeInTheDocument();
  expect(screen.getByText("ОП-01")).toBeInTheDocument();
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
    throw new Error(`Unexpected request: ${path}`);
  });

  const { unmount } = render(<ReferencesPage principal={admin} />);
  fireEvent.click(await screen.findByRole("button", { name: "База техопераций" }));
  fireEvent.click(await screen.findByRole("button", { name: "Провода" }));
  fireEvent.click(await screen.findByRole("button", { name: "Править" }));
  fireEvent.change(screen.getByLabelText("Поле Сечение"), { target: { value: "0,5" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить запись" }));

  await waitFor(() => expect(patched).toEqual({ edits: [{ field: "Сечение", old: "0,35", value: "0,5" }] }));
  expect(await screen.findByText("Запись сохранена")).toBeInTheDocument();
  unmount();

  render(<ReferencesPage principal={{ ...admin, roles: ["receiver"], permissions: { references: "view" } }} />);
  fireEvent.click(await screen.findByRole("button", { name: "База техопераций" }));
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
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReferencesPage principal={admin} />);
  fireEvent.click(await screen.findByRole("button", { name: "База техопераций" }));
  fireEvent.click(await screen.findByRole("button", { name: "Материалы" }));
  const add = await screen.findByRole("button", { name: "+ Запись" });
  await waitFor(() => expect(add).toBeEnabled());
  fireEvent.click(add);
  fireEvent.change(screen.getByLabelText("Поле Наименование"), { target: { value: "Трубка" } });
  expect(screen.getByLabelText("Поле Группа")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Сохранить запись" }));

  await waitFor(() => expect(created).toEqual({ tab: "mat", fields: { Наименование: "Трубка", Группа: "" } }));
});
