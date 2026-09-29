import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AccessPage, roleModes } from "./AccessPage";
import type { AccessOverview } from "./api";

// admin (Технолог) is a full-access role — its column is locked; receiver (Кладовщик) is driven by the matrix and is
// the role the editing tests target.
const OVERVIEW: AccessOverview = {
  sections: [{ key: "requests", label: "Заявки" }, { key: "warehouse", label: "Склад" }, { key: "staffdir", label: "Сотрудники" }],
  roles: [{ key: "admin", label: "Технолог" }, { key: "receiver", label: "Кладовщик" }],
  defaults: {
    admin: { requests: "edit", warehouse: "edit", staffdir: "edit" },
    receiver: { requests: "edit", warehouse: "view" },
  },
  matrix: { receiver: { warehouse: "none" } },
  users: [
    { id: "u1", login: "admin", name: "Админов", role: "admin", active: true, access: {} },
    { id: "u2", login: "receiver", name: "Кладовкин", role: "receiver", active: true, access: { staffdir: "view" } },
  ],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

it("computes a role's sections as defaults with the matrix on top", () => {
  expect(roleModes(OVERVIEW, "receiver")).toEqual({ requests: "edit" });
  expect(roleModes({ ...OVERVIEW, matrix: { receiver: { staffdir: "view", requests: "view" } } }, "receiver"))
    .toEqual({ requests: "view", warehouse: "view", staffdir: "view" });
  expect(roleModes(OVERVIEW, "admin")).toEqual(OVERVIEW.defaults.admin);
});

it("shows the role default in an empty cell and saves the whole matrix", async () => {
  const saved: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/auth/access/roles" && init?.method === "PUT") {
      saved.push(JSON.parse(String(init.body)));
      return json({ matrix: {} });
    }
    if (path === "/api/auth/access") return json(OVERVIEW);
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<AccessPage />);
  const warehouse = await screen.findByLabelText("Склад · Кладовщик") as HTMLSelectElement;
  expect(warehouse.value).toBe("none");
  const requests = screen.getByLabelText("Заявки · Кладовщик") as HTMLSelectElement;
  expect(requests.value).toBe("");
  expect(within(requests).getByRole("option", { name: "полный (по умолч.)" })).toBeInTheDocument();

  const save = screen.getByRole("button", { name: "Сохранить доступы ролей" });
  expect(save).toBeDisabled();
  fireEvent.change(warehouse, { target: { value: "" } });
  fireEvent.change(requests, { target: { value: "view" } });
  fireEvent.click(save);

  await waitFor(() => expect(saved).toEqual([{ matrix: { receiver: { requests: "view" } } }]));
  expect(await screen.findByText(/Доступы ролей сохранены/)).toBeInTheDocument();
});

it("locks the full-access role columns so they cannot be reduced", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (String(input) === "/api/auth/access") return json(OVERVIEW);
    throw new Error(`Unexpected request: ${String(input)}`);
  });

  render(<AccessPage />);
  // The note names the four always-full roles, and the admin (Технолог) column has no editable cell — only a lock.
  expect(await screen.findByText(/всегда имеют полный доступ ко всем разделам/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Сотрудники · Технолог")).toBeNull();
  expect(screen.getAllByText("полный 🔒").length).toBeGreaterThan(0);
});

it("edits one person's exceptions against what the role gives", async () => {
  const saved: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/auth/access/users/u2" && init?.method === "PUT") {
      saved.push(JSON.parse(String(init.body)));
      return json({ user_id: "u2", access: { warehouse: "view" } });
    }
    if (path === "/api/auth/access") return json(OVERVIEW);
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<AccessPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Исключения по сотрудникам" }));
  const person = screen.getByLabelText("Сотрудник");
  expect(within(person).getByRole("option", { name: "Кладовкин · Кладовщик · исключений: 1" })).toBeInTheDocument();
  fireEvent.change(person, { target: { value: "u2" } });

  const table = within(screen.getByRole("region", { name: "Исключения: Кладовкин" })).getByRole("table");
  const warehouseRow = within(table).getByText("Склад").closest("tr")!;
  expect(within(warehouseRow).getByText("закрыт", { selector: "span" })).toBeInTheDocument(); // the matrix closed it for the role
  expect((screen.getByLabelText("Исключение: Сотрудники") as HTMLSelectElement).value).toBe("view");

  fireEvent.change(screen.getByLabelText("Исключение: Склад"), { target: { value: "view" } });
  fireEvent.change(screen.getByLabelText("Исключение: Сотрудники"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить исключения" }));

  await waitFor(() => expect(saved).toEqual([{ access: { warehouse: "view" } }]));
  expect(await screen.findByText("Исключения сохранены: Кладовкин")).toBeInTheDocument();
});

it("NAV-SECTIONS: shows «Оформить заявку», «Мои заявки» and «Выдача со списанием» as sections of their own", async () => {
  const overview: AccessOverview = {
    ...OVERVIEW,
    sections: [
      { key: "newrequest", label: "Оформить заявку" }, { key: "myrequests", label: "Мои заявки" },
      { key: "requests", label: "Заявки" }, { key: "fulfillment", label: "Запросы на комплектацию (набор)" },
      { key: "handout", label: "Выдача со списанием" },
    ],
    defaults: {
      admin: { newrequest: "edit", myrequests: "edit", requests: "edit", fulfillment: "edit", handout: "edit" },
      receiver: { newrequest: "edit", myrequests: "edit", requests: "edit" },
    },
    matrix: {},
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (String(input) === "/api/auth/access") return json(overview);
    throw new Error(`Unexpected request: ${String(input)}`);
  });

  render(<AccessPage />);
  const filing = await screen.findByLabelText("Оформить заявку · Кладовщик") as HTMLSelectElement;
  expect(within(filing).getByRole("option", { name: "полный (по умолч.)" })).toBeInTheDocument();
  expect(screen.getByLabelText("Мои заявки · Кладовщик")).toBeInTheDocument();
  const handout = screen.getByLabelText("Выдача со списанием · Кладовщик") as HTMLSelectElement;
  expect(within(handout).getByRole("option", { name: "—" })).toBeInTheDocument();
});
