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

it("edits and atomically saves both reference maps", async () => {
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
    });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReferencesPage principal={admin} />);
  fireEvent.change(await screen.findByLabelText("Поставщик 1"), { target: { value: "Новый поставщик" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить справочники" }));

  await waitFor(() => expect(payload).toEqual({
    suppliers: [{ key: "example.ru", value: "Новый поставщик" }],
    countries: [{ key: "Fraxis", value: "Россия" }],
  }));
  expect(await screen.findByText("Справочники сохранены целиком")).toBeInTheDocument();
});

it("searches the versioned technology snapshot", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [] });
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
