import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AccessPage, roleModes } from "./AccessPage";
import type { AccessOverview } from "./api";

const OVERVIEW: AccessOverview = {
  sections: [{ key: "requests", label: "Заявки" }, { key: "warehouse", label: "Склад" }, { key: "staffdir", label: "Сотрудники" }],
  roles: [{ key: "admin", label: "Технолог" }, { key: "buyer", label: "Специалист по снабжению" }],
  defaults: {
    admin: { requests: "edit", warehouse: "edit", staffdir: "edit" },
    buyer: { requests: "edit", warehouse: "view" },
  },
  matrix: { buyer: { warehouse: "none" } },
  users: [
    { id: "u1", login: "admin", name: "Админов", role: "admin", active: true, access: {} },
    { id: "u2", login: "buyer", name: "Закупкин", role: "buyer", active: true, access: { staffdir: "view" } },
  ],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

it("computes a role's sections as defaults with the matrix on top", () => {
  expect(roleModes(OVERVIEW, "buyer")).toEqual({ requests: "edit" });
  expect(roleModes({ ...OVERVIEW, matrix: { buyer: { staffdir: "view", requests: "view" } } }, "buyer"))
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
  const warehouse = await screen.findByLabelText("Склад · Специалист по снабжению") as HTMLSelectElement;
  expect(warehouse.value).toBe("none");
  const requests = screen.getByLabelText("Заявки · Специалист по снабжению") as HTMLSelectElement;
  expect(requests.value).toBe("");
  expect(within(requests).getByRole("option", { name: "полный (по умолч.)" })).toBeInTheDocument();

  const save = screen.getByRole("button", { name: "Сохранить доступы ролей" });
  expect(save).toBeDisabled();
  fireEvent.change(warehouse, { target: { value: "" } });
  fireEvent.change(requests, { target: { value: "view" } });
  fireEvent.click(save);

  await waitFor(() => expect(saved).toEqual([{ matrix: { buyer: { requests: "view" } } }]));
  expect(await screen.findByText(/Доступы ролей сохранены/)).toBeInTheDocument();
});

it("shows the server's refusal to lock every administrator out", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/auth/access/roles" && init?.method === "PUT") {
      return json({ detail: "Нужен хотя бы один администратор с доступом к «Сотрудникам» и «Доступам»" }, 409);
    }
    if (path === "/api/auth/access") return json(OVERVIEW);
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<AccessPage />);
  fireEvent.change(await screen.findByLabelText("Сотрудники · Технолог"), { target: { value: "none" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить доступы ролей" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("хотя бы один администратор");
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
  expect(within(person).getByRole("option", { name: "Закупкин · Специалист по снабжению · исключений: 1" })).toBeInTheDocument();
  fireEvent.change(person, { target: { value: "u2" } });

  const table = within(screen.getByRole("region", { name: "Исключения: Закупкин" })).getByRole("table");
  const warehouseRow = within(table).getByText("Склад").closest("tr")!;
  expect(within(warehouseRow).getByText("закрыт", { selector: "span" })).toBeInTheDocument(); // the matrix closed it for the role
  expect((screen.getByLabelText("Исключение: Сотрудники") as HTMLSelectElement).value).toBe("view");

  fireEvent.change(screen.getByLabelText("Исключение: Склад"), { target: { value: "view" } });
  fireEvent.change(screen.getByLabelText("Исключение: Сотрудники"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить исключения" }));

  await waitFor(() => expect(saved).toEqual([{ access: { warehouse: "view" } }]));
  expect(await screen.findByText("Исключения сохранены: Закупкин")).toBeInTheDocument();
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
      buyer: { newrequest: "edit", myrequests: "edit", requests: "edit" },
    },
    matrix: {},
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (String(input) === "/api/auth/access") return json(overview);
    throw new Error(`Unexpected request: ${String(input)}`);
  });

  render(<AccessPage />);
  const filing = await screen.findByLabelText("Оформить заявку · Специалист по снабжению") as HTMLSelectElement;
  expect(within(filing).getByRole("option", { name: "полный (по умолч.)" })).toBeInTheDocument();
  expect(screen.getByLabelText("Мои заявки · Специалист по снабжению")).toBeInTheDocument();
  const handout = screen.getByLabelText("Выдача со списанием · Специалист по снабжению") as HTMLSelectElement;
  expect(within(handout).getByRole("option", { name: "—" })).toBeInTheDocument();
  expect(screen.getByLabelText("Выдача со списанием · Технолог")).toBeInTheDocument();
});
