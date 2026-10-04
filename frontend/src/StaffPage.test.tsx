import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { StaffPage } from "./StaffPage";
import type { StaffDirectory } from "./api";

const BLANK = { personnel_number: "", job_title: "", qualification: "", job_since: null, manager_id: null };
const DIRECTORY: StaffDirectory = {
  users: [
    { id: "u1", login: "admin", email: "admin@example.test", name: "Админов", department: "ИТ", role: "admin", site: "", active: true, pin_set: true, ...BLANK },
    { id: "u2", login: "buyer@example.test", email: "buyer@example.test", name: "Закупкин", department: "Снабжение", role: "buyer", site: "ЦАР", active: true, pin_set: false, ...BLANK },
  ],
  departments: ["ИТ", "Снабжение"],
  department_heads: {},
  roles: [{ key: "admin", label: "Технолог" }, { key: "buyer", label: "Специалист по снабжению" }, { key: "master", label: "Мастер" }],
  sites: [{ code: "ЦАР", city: "Москва" }, { code: "ЖУК", city: "Раменское" }],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

it("lists employees with access labels and creates a new one with a password", async () => {
  let created: Record<string, unknown> | null = null;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/auth/staff" && init?.method === "POST") {
      created = JSON.parse(String(init.body));
      return json({ user: { ...DIRECTORY.users[1], id: "u3", login: "new@example.test", name: "Новиков" }, pin: "654321" }, 201);
    }
    if (path === "/api/auth/staff") return json(DIRECTORY);
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<StaffPage />);
  expect(await screen.findByText("Специалист по снабжению")).toBeInTheDocument();
  expect(screen.getByText("Технолог")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "+ Сотрудник" }));
  fireEvent.change(screen.getByLabelText("E-mail"), { target: { value: "new@example.test" } });
  fireEvent.change(screen.getByLabelText("ФИО"), { target: { value: "Новиков" } });
  fireEvent.change(screen.getByLabelText("Отдел"), { target: { value: "Снабжение" } });
  fireEvent.change(screen.getByLabelText("Площадка"), { target: { value: "ЖУК" } });
  fireEvent.change(screen.getByLabelText("Роль доступа"), { target: { value: "master" } });
  fireEvent.change(screen.getByLabelText("Пароль"), { target: { value: "first-password" } });
  fireEvent.click(screen.getByRole("button", { name: "Завести сотрудника" }));

  await waitFor(() => expect(created).toEqual({
    login: "", email: "new@example.test", name: "Новиков", department: "Снабжение", role: "master", site: "ЖУК",
    password: "first-password", pin: "", ...BLANK,
  }));
  expect(await screen.findByText(
    "Сотрудник Новиков заведён, вход — new@example.test, PIN — 654321 (запишите, больше не покажем)",
  )).toBeInTheDocument();
});

it("edits an employee, keeps the password when the field is empty and shows the last-admin refusal", async () => {
  const updates: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/auth/staff/u1" && init?.method === "PUT") {
      updates.push(JSON.parse(String(init.body)));
      return json({ detail: "Нужен хотя бы один администратор — иначе некому будет править справочник." }, 409);
    }
    if (path === "/api/auth/staff") return json(DIRECTORY);
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<StaffPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Править Админов" }));
  fireEvent.click(screen.getByLabelText("Вход разрешён"));
  fireEvent.click(screen.getByRole("button", { name: "Сохранить сотрудника" }));

  await waitFor(() => expect(updates).toEqual([
    { name: "Админов", department: "ИТ", role: "admin", site: "", active: false, password: null, pin: null, ...BLANK },
  ]));
  expect(await screen.findByRole("alert")).toHaveTextContent("Нужен хотя бы один администратор");
});

it("saves the department list and renames a department", async () => {
  const calls: Array<{ path: string; body: unknown }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "PUT" || init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      calls.push({ path, body });
      if (path === "/api/auth/departments") return json({ departments: ["ИТ", "Снабжение", "Цех"] });
      if (path === "/api/auth/departments/rename") return json({ departments: ["ИТ", "Отдел снабжения"], users: 1 });
    }
    if (path === "/api/auth/staff") return json(DIRECTORY);
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<StaffPage />);
  fireEvent.change(await screen.findByLabelText("Список отделов"), { target: { value: "ИТ\nСнабжение\nЦех" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить отделы" }));
  expect(await screen.findByText("Список отделов сохранён")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Переименовать отдел"), { target: { value: "Снабжение" } });
  fireEvent.change(screen.getByLabelText("Новое название отдела"), { target: { value: "Отдел снабжения" } });
  fireEvent.click(screen.getByRole("button", { name: "Переименовать" }));
  expect(await screen.findByText("Отдел переименован, сотрудников перенесено: 1")).toBeInTheDocument();
  expect(calls).toEqual([
    { path: "/api/auth/departments", body: { departments: ["ИТ", "Снабжение", "Цех"] } },
    { path: "/api/auth/departments/rename", body: { source: "Снабжение", target: "Отдел снабжения" } },
  ]);
});

it("sets a new PIN from the employee form (portalStaffSave pin)", async () => {
  const updates: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/auth/staff/u2" && init?.method === "PUT") {
      updates.push(JSON.parse(String(init.body)));
      return json({ user: { ...DIRECTORY.users[1], pin_set: true } });
    }
    if (path === "/api/auth/staff") return json(DIRECTORY);
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<StaffPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Править Закупкин" }));
  fireEvent.change(screen.getByLabelText("Новый PIN"), { target: { value: "7654321" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить сотрудника" }));

  await waitFor(() => expect(updates).toEqual([
    { name: "Закупкин", department: "Снабжение", role: "buyer", site: "ЦАР", active: true, password: null, pin: "7654321", ...BLANK },
  ]));
  expect(await screen.findByText("Сохранено: Закупкин, PIN назначен")).toBeInTheDocument();
});

it("deletes an employee after confirmation and hides switched-off ones until asked", async () => {
  let directory: StaffDirectory = DIRECTORY;
  const deleted: string[] = [];
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/auth/staff/u2" && init?.method === "DELETE") {
      deleted.push(path);
      directory = { ...DIRECTORY, users: DIRECTORY.users.map((user) => user.id === "u2" ? { ...user, active: false } : user) };
      return json({ user: { ...DIRECTORY.users[1], active: false }, deleted: false });
    }
    if (path === "/api/auth/staff") return json(directory);
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<StaffPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Удалить Закупкин" }));

  expect(await screen.findByText("Сотрудник Закупкин отключён и скрыт: за ним есть история работы")).toBeInTheDocument();
  expect(deleted).toEqual(["/api/auth/staff/u2"]);
  expect(screen.queryByText("Закупкин")).not.toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("Показать отключённых (1)"));
  expect(screen.getByText("Закупкин")).toBeInTheDocument();
});

it("opens the personal card of a row and, when sent from a card, the edit form of that person at once", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (String(input) === "/api/auth/staff") return json(DIRECTORY);
    throw new Error(`Unexpected request: ${String(input)}`);
  });
  const openCard = vi.fn();
  const view = render(<StaffPage openCard={openCard} />);
  fireEvent.click(await screen.findByRole("button", { name: "Карточка Закупкин" }));
  expect(openCard).toHaveBeenCalledWith("u2");
  expect(screen.queryByRole("heading", { name: "Правка: Закупкин" })).not.toBeInTheDocument();
  view.unmount();

  render(<StaffPage openUserId="u2" />);
  expect(await screen.findByRole("heading", { name: "Правка: Закупкин" })).toBeInTheDocument();
});

// «Личная карточка»: Мастеров reports to Закупкин, Отключенов is switched off.
const TEAM: StaffDirectory = {
  ...DIRECTORY,
  users: [
    ...DIRECTORY.users,
    { id: "u3", login: "master", email: "master@example.test", name: "Мастеров", department: "Цех", role: "master", site: "ЖУК", active: true, ...BLANK, personnel_number: "FRX-0003", job_title: "Мастер участка", qualification: "5 разряд", manager_id: "u2" },
    { id: "u4", login: "off", email: "off@example.test", name: "Отключенов", department: "Цех", role: "staff", site: "", active: false, ...BLANK },
  ],
  departments: ["ИТ", "Снабжение", "Цех"],
  department_heads: { "ИТ": "u1" },
};

it("edits personnel fields and picks the manager from a searchable list without self, subordinates or switched-off people", async () => {
  const updates: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/auth/staff/u2" && init?.method === "PUT") {
      updates.push(JSON.parse(String(init.body)));
      return json({ user: { ...TEAM.users[1], pin_set: false } });
    }
    if (path === "/api/auth/staff") return json(TEAM);
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<StaffPage />);
  const staffTable = await screen.findByRole("table", { name: "Сотрудники" });
  expect(within(staffTable).getByText("FRX-0003")).toBeInTheDocument();
  expect(within(staffTable).getByText("Мастер участка")).toBeInTheDocument();
  expect(within(staffTable).getByText("5 разряд")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Править Закупкин" }));
  fireEvent.change(screen.getByLabelText("Табельный номер"), { target: { value: "FRX-0017" } });
  fireEvent.change(screen.getByLabelText("Должность"), { target: { value: "Специалист по снабжению" } });
  fireEvent.change(screen.getByLabelText("Категория / квалификация"), { target: { value: "1 категория" } });
  fireEvent.change(screen.getByLabelText("В должности с"), { target: { value: "2024-03-01" } });
  const manager = screen.getByRole("combobox", { name: "Руководитель" });
  fireEvent.focus(manager);
  const suggestions = screen.getByRole("listbox", { name: "Варианты: Руководитель" });
  expect(within(suggestions).getAllByRole("option").map((option) => option.textContent)).toEqual(["Админов · ИТ"]);
  fireEvent.mouseDown(screen.getByRole("option", { name: "Админов · ИТ" }));
  fireEvent.click(screen.getByRole("button", { name: "Сохранить сотрудника" }));

  await waitFor(() => expect(updates).toEqual([{
    name: "Закупкин", department: "Снабжение", role: "buyer", site: "ЦАР", active: true, password: null, pin: null,
    personnel_number: "FRX-0017", job_title: "Специалист по снабжению", qualification: "1 категория",
    job_since: "2024-03-01", manager_id: "u1",
  }]));
});

it("splits the form into «Кадры» and «Доступ в ERP», each with its own fields in a fixed order", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (String(input) === "/api/auth/staff") return json(TEAM);
    throw new Error(`Unexpected request: ${String(input)}`);
  });
  const fieldsOf = (block: HTMLElement) => Array.from(block.querySelectorAll("[aria-label]")).map((field) => field.getAttribute("aria-label"));

  render(<StaffPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Править Закупкин" }));
  const form = document.getElementById("staff-edit-form") as HTMLElement;
  expect(within(form).getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent)).toEqual(["Кадры", "Доступ в ERP"]);
  expect(fieldsOf(screen.getByRole("region", { name: "Кадры" }))).toEqual([
    "ФИО", "Должность", "Категория / квалификация", "Отдел", "Руководитель", "Табельный номер", "В должности с",
  ]);
  expect(fieldsOf(screen.getByRole("region", { name: "Доступ в ERP" }))).toEqual([
    "Роль доступа", "Площадка", "Новый пароль", "Новый PIN", "Вход разрешён",
  ]);
  // The old mixed label is gone: the access role is never called just «Роль».
  expect(screen.queryByLabelText("Роль")).toBeNull();
  expect(screen.queryByLabelText("Место работы")).toBeNull();
});

it("puts the e-mail and login of a new employee into the access block, before the role", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (String(input) === "/api/auth/staff") return json(DIRECTORY);
    throw new Error(`Unexpected request: ${String(input)}`);
  });
  render(<StaffPage />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Сотрудник" }));
  const access = screen.getByRole("region", { name: "Доступ в ERP" });
  expect(Array.from(access.querySelectorAll("[aria-label]")).map((field) => field.getAttribute("aria-label"))).toEqual([
    "E-mail", "Логин", "Роль доступа", "Площадка", "Пароль", "PIN",
  ]);
  expect(within(screen.getByRole("region", { name: "Кадры" })).queryByLabelText("E-mail")).toBeNull();
});

it("explains «Роль доступа» and «Площадка» in tooltips", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (String(input) === "/api/auth/staff") return json(DIRECTORY);
    throw new Error(`Unexpected request: ${String(input)}`);
  });
  render(<StaffPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Править Закупкин" }));
  expect(screen.getByLabelText("Роль доступа").closest("label")).toHaveAttribute(
    "title", "что сотрудник может открывать и править в ERP; должность — в блоке «Кадры»",
  );
  expect(screen.getByLabelText("Площадка").closest("label")).toHaveAttribute(
    "title", "ЦАР / ЖУК — сотрудник цеха с площадкой считается монтажником",
  );
});

it("keeps the position and the access role in separate table columns", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (String(input) === "/api/auth/staff") return json(TEAM);
    throw new Error(`Unexpected request: ${String(input)}`);
  });
  render(<StaffPage />);
  const table = await screen.findByRole("table", { name: "Сотрудники" });
  const headers = within(table).getAllByRole("columnheader").map((header) => header.textContent);
  expect(headers).toEqual(["ФИО", "Таб. №", "Должность", "Логин / e-mail", "Отдел", "Площадка", "Доступ", "Вход", ""]);
  const cell = (name: string, column: string) => {
    const row = within(table).getByText(name).closest("tr") as HTMLElement;
    return within(row).getAllByRole("cell")[headers.indexOf(column)];
  };
  // The position column carries the position and the qualification, never the access role.
  expect(cell("Мастеров", "Должность")).toHaveTextContent("Мастер участка5 разряд");
  expect(cell("Мастеров", "Доступ")).toHaveTextContent("Мастер");
  expect(cell("Закупкин", "Должность")).toHaveTextContent("—");
  expect(cell("Закупкин", "Должность")).not.toHaveTextContent("Специалист по снабжению");
  expect(cell("Закупкин", "Доступ")).toHaveTextContent("Специалист по снабжению");
});

it("shows the current manager and refuses a name typed but not picked from the list", async () => {
  const updates: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "PUT") updates.push(path);
    if (path === "/api/auth/staff") return json(TEAM);
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<StaffPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Править Мастеров" }));
  const manager = screen.getByRole("combobox", { name: "Руководитель" });
  expect(manager).toHaveValue("Закупкин · Снабжение");
  expect(screen.getByLabelText("Табельный номер")).toHaveValue("FRX-0003");

  fireEvent.change(manager, { target: { value: "Кто-то" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить сотрудника" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Руководитель «Кто-то» не найден — выберите из списка");
  expect(updates).toEqual([]);
});

it("opened from the card, the form already names the manager", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (String(input) === "/api/auth/staff") return json(TEAM);
    throw new Error(`Unexpected request: ${String(input)}`);
  });

  render(<StaffPage openUserId="u3" />);

  expect(await screen.findByRole("heading", { name: "Правка: Мастеров" })).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Руководитель" })).toHaveValue("Закупкин · Снабжение");
});

it("assigns and removes a department head", async () => {
  const calls: unknown[] = [];
  let heads: Record<string, string> = { "ИТ": "u1" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/auth/departments/head" && init?.method === "PUT") {
      const body = JSON.parse(String(init.body));
      calls.push(body);
      heads = body.head_id ? { ...heads, [body.department]: body.head_id } : { "ИТ": "u1" };
      return json({ departments: TEAM.departments, department_heads: heads });
    }
    if (path === "/api/auth/staff") return json(TEAM);
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<StaffPage />);
  const headsTable = await screen.findByRole("table", { name: "Руководители отделов" });
  expect(within(headsTable).getByText("Админов")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Отдел для руководителя"), { target: { value: "Снабжение" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Руководитель отдела" }), { target: { value: "Закупкин · Снабжение" } });
  fireEvent.click(screen.getByRole("button", { name: "Назначить руководителя" }));
  expect(await screen.findByText("Руководитель отдела «Снабжение» — Закупкин")).toBeInTheDocument();
  expect(within(headsTable).getByText("Закупкин")).toBeInTheDocument();

  fireEvent.change(screen.getByRole("combobox", { name: "Руководитель отдела" }), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Снять руководителя" }));
  expect(await screen.findByText("У отдела «Снабжение» больше нет руководителя")).toBeInTheDocument();
  expect(calls).toEqual([{ department: "Снабжение", head_id: "u2" }, { department: "Снабжение", head_id: null }]);
});
