import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { StaffPage } from "./StaffPage";
import type { StaffDirectory } from "./api";

const BLANK = { personnel_number: "", job_title: "", qualification: "", job_since: null, manager_id: null };
const NO_FLAGS = { shop_worker: false, site_master: false };
const LEVELS = [
  { key: "superadmin", label: "Суперадминистратор", full: true },
  { key: "commercial", label: "Коммерческое руководство", full: false },
  { key: "tech_lead", label: "Технический руководитель", full: false },
  { key: "pm", label: "Менеджер по проектам", full: false },
  { key: "storekeeper", label: "Кладовщик", full: false },
  { key: "foreman", label: "Бригадир", full: false },
  { key: "operator", label: "Оператор", full: false },
];
const DIRECTORY: StaffDirectory = {
  users: [
    { id: "u1", login: "admin", email: "admin@example.test", name: "Админов", department: "ИТ", access_level: "superadmin", site: "", active: true, pin_set: true, ...NO_FLAGS, ...BLANK },
    { id: "u2", login: "buyer@example.test", email: "buyer@example.test", name: "Закупкин", department: "Снабжение", access_level: "tech_lead", site: "ЦАР", active: true, pin_set: false, ...NO_FLAGS, ...BLANK },
  ],
  departments: ["ИТ", "Снабжение"],
  department_heads: {},
  levels: LEVELS,
  sites: [{ code: "ЦАР", city: "Москва" }, { code: "ЖУК", city: "Раменское" }],
};
// The session in «Режим админа»: full access, but not a superadministrator.
const ME_ADMIN = { user: { id: "u1", email: "admin@example.test", name: "Админов", department: "ИТ" }, roles: ["admin"], permissions: {}, access_level: "admin" };
const ME_SUPERADMIN = { ...ME_ADMIN, roles: ["superadmin"], access_level: "superadmin" };

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

/** Действие строки: меню «⋯» сотрудника, затем пункт. */
async function rowAction(action: string, person: string) {
  fireEvent.click(await screen.findByRole("button", { name: `Действия: ${person}` }));
  fireEvent.click(await screen.findByRole("menuitem", { name: action }));
}


/** Mocks the directory and the session; `handler` answers anything else (PUT / POST / DELETE), a throw is a failure. */
function serve(directory: StaffDirectory, handler: (path: string, init?: RequestInit) => Response | undefined = () => undefined, me = ME_ADMIN) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const answer = handler(path, init);
    if (answer) return answer;
    if (path === "/api/auth/me") return json(me);
    if (path === "/api/auth/staff") return json(directory);
    throw new Error(`Unexpected request: ${path}`);
  });
}

it("lists employees with the access level label and creates a new one with a level, the two flags and a password", async () => {
  let created: Record<string, unknown> | null = null;
  serve(DIRECTORY, (path, init) => {
    if (path === "/api/auth/staff" && init?.method === "POST") {
      created = JSON.parse(String(init.body));
      return json({ user: { ...DIRECTORY.users[1], id: "u3", login: "new@example.test", name: "Новиков" }, pin: "654321" }, 201);
    }
  });

  render(<StaffPage />);
  const list = await screen.findByRole("region", { name: "Сотрудники" });
  expect(within(list).getByRole("cell", { name: "Технический руководитель" })).toBeInTheDocument();
  expect(within(list).getByRole("cell", { name: "Суперадминистратор" })).toBeInTheDocument();
  expect(within(list).queryByRole("cell", { name: "Администратор" })).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "+ Сотрудник" }));
  fireEvent.change(screen.getByLabelText("E-mail"), { target: { value: "new@example.test" } });
  fireEvent.change(screen.getByLabelText("ФИО"), { target: { value: "Новиков" } });
  fireEvent.change(screen.getByLabelText("Отдел"), { target: { value: "Снабжение" } });
  fireEvent.change(screen.getByLabelText("Площадка"), { target: { value: "ЖУК" } });
  expect(screen.getByLabelText("Уровень доступа")).toHaveValue("operator"); // the lowest level until someone picks another
  fireEvent.change(screen.getByLabelText("Уровень доступа"), { target: { value: "foreman" } });
  fireEvent.click(screen.getByLabelText("Монтажник (работает в цеху)"));
  fireEvent.change(screen.getByLabelText("Пароль"), { target: { value: "first-password" } });
  fireEvent.click(screen.getByRole("button", { name: "Завести сотрудника" }));

  await waitFor(() => expect(created).toEqual({
    login: "", email: "new@example.test", name: "Новиков", department: "Снабжение", access_level: "foreman", site: "ЖУК",
    password: "first-password", pin: "", shop_worker: true, site_master: false, ...BLANK,
  }));
  expect(created).not.toHaveProperty("role");
  expect(await screen.findByText(
    "Сотрудник Новиков заведён, вход — new@example.test, PIN — 654321 (запишите, больше не покажем)",
  )).toBeInTheDocument();
});

it("edits an employee, keeps the password when the field is empty and shows the last-admin refusal", async () => {
  const updates: unknown[] = [];
  serve(DIRECTORY, (path, init) => {
    if (path === "/api/auth/staff/u1" && init?.method === "PUT") {
      updates.push(JSON.parse(String(init.body)));
      return json({ detail: "Нужен хотя бы один активный суперадминистратор или администратор — иначе некому будет вернуть права." }, 409);
    }
  }, ME_SUPERADMIN);

  render(<StaffPage />);
  await rowAction("Править", "Админов");
  fireEvent.click(screen.getByLabelText("Вход разрешён"));
  fireEvent.click(screen.getByRole("button", { name: "Сохранить сотрудника" }));

  // The flags were not touched, so they are not sent and the server keeps them (KEEP).
  await waitFor(() => expect(updates).toEqual([
    { name: "Админов", department: "ИТ", access_level: "superadmin", site: "", active: false, password: null, pin: null, ...BLANK },
  ]));
  expect(await screen.findByRole("alert")).toHaveTextContent("Нужен хотя бы один активный суперадминистратор");
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
  serve(DIRECTORY, (path, init) => {
    if (path === "/api/auth/staff/u2" && init?.method === "PUT") {
      updates.push(JSON.parse(String(init.body)));
      return json({ user: { ...DIRECTORY.users[1], pin_set: true } });
    }
  });

  render(<StaffPage />);
  await rowAction("Править", "Закупкин");
  fireEvent.change(screen.getByLabelText("Новый PIN"), { target: { value: "7654321" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить сотрудника" }));

  await waitFor(() => expect(updates).toEqual([
    { name: "Закупкин", department: "Снабжение", access_level: "tech_lead", site: "ЦАР", active: true, password: null, pin: "7654321", ...BLANK },
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
  await rowAction("Удалить", "Закупкин");

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
  await rowAction("Открыть карточку", "Закупкин");
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
    { id: "u3", login: "master", email: "master@example.test", name: "Мастеров", department: "Цех", access_level: "foreman", site: "ЖУК", active: true, shop_worker: false, site_master: true, ...BLANK, personnel_number: "FRX-0003", job_title: "Мастер участка", qualification: "5 разряд", manager_id: "u2" },
    { id: "u4", login: "off", email: "off@example.test", name: "Отключенов", department: "Цех", access_level: "operator", site: "", active: false, shop_worker: true, site_master: false, ...BLANK },
  ],
  departments: ["ИТ", "Снабжение", "Цех"],
  department_heads: { "ИТ": "u1" },
};

it("edits personnel fields and picks the manager from a searchable list without self, subordinates or switched-off people", async () => {
  const updates: unknown[] = [];
  serve(TEAM, (path, init) => {
    if (path === "/api/auth/staff/u2" && init?.method === "PUT") {
      updates.push(JSON.parse(String(init.body)));
      return json({ user: { ...TEAM.users[1], pin_set: false } });
    }
  });

  render(<StaffPage />);
  const staffTable = await screen.findByRole("region", { name: "Сотрудники" });
  expect(within(staffTable).getByText("FRX-0003")).toBeInTheDocument();
  expect(within(staffTable).getByText("Мастер участка")).toBeInTheDocument();
  expect(within(staffTable).getByText("5 разряд")).toBeInTheDocument();

  await rowAction("Править", "Закупкин");
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
    name: "Закупкин", department: "Снабжение", access_level: "tech_lead", site: "ЦАР", active: true, password: null, pin: null,
    personnel_number: "FRX-0017", job_title: "Специалист по снабжению", qualification: "1 категория",
    job_since: "2024-03-01", manager_id: "u1",
  }]));
});

it("splits the form into «Кадры» and «Доступ в ERP», each with its own fields in a fixed order", async () => {
  serve(TEAM);
  const fieldsOf = (block: HTMLElement) => Array.from(block.querySelectorAll("[aria-label]")).map((field) => field.getAttribute("aria-label"));

  render(<StaffPage />);
  await rowAction("Править", "Закупкин");
  const form = document.getElementById("staff-edit-form") as HTMLElement;
  expect(within(form).getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent)).toEqual(["Кадры", "Доступ в ERP"]);
  expect(fieldsOf(screen.getByRole("region", { name: "Кадры" }))).toEqual([
    "ФИО", "Должность", "Категория / квалификация", "Отдел", "Руководитель", "Табельный номер", "В должности с",
  ]);
  expect(fieldsOf(screen.getByRole("region", { name: "Доступ в ERP" }))).toEqual([
    "Уровень доступа", "Площадка", "Новый пароль", "Новый PIN", "Монтажник (работает в цеху)", "Мастер площадки", "Вход разрешён",
  ]);
  // The old mixed labels are gone: the access level is never called just «Роль» or «Роль доступа».
  expect(screen.queryByLabelText("Роль")).toBeNull();
  expect(screen.queryByLabelText("Роль доступа")).toBeNull();
  expect(screen.queryByLabelText("Место работы")).toBeNull();
});

it("puts the e-mail and login of a new employee into the access block, before the level", async () => {
  serve(DIRECTORY);
  render(<StaffPage />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Сотрудник" }));
  const access = screen.getByRole("region", { name: "Доступ в ERP" });
  expect(Array.from(access.querySelectorAll("[aria-label]")).map((field) => field.getAttribute("aria-label"))).toEqual([
    "E-mail", "Логин", "Уровень доступа", "Площадка", "Пароль", "PIN", "Монтажник (работает в цеху)", "Мастер площадки",
  ]);
  expect(within(screen.getByRole("region", { name: "Кадры" })).queryByLabelText("E-mail")).toBeNull();
});

it("explains the level, the site and the two flags in tooltips", async () => {
  serve(DIRECTORY);
  render(<StaffPage />);
  await rowAction("Править", "Закупкин");
  expect(screen.getByLabelText("Уровень доступа").closest("label")).toHaveAttribute(
    "title", "что сотрудник может открывать и править в ERP; должность — в блоке «Кадры»",
  );
  expect(screen.getByLabelText("Площадка").closest("label")).toHaveAttribute(
    "title", "ЦАР / ЖУК — площадка, на которой работает сотрудник; пусто — все площадки",
  );
  expect(screen.getByLabelText("Монтажник (работает в цеху)").closest("label")).toHaveAttribute(
    "title", "Исполнитель операций в цеху: ему назначают задания производства",
  );
  expect(screen.getByLabelText("Мастер площадки").closest("label")).toHaveAttribute(
    "title", "Руководит монтажниками площадки: они видны в его личной карточке",
  );
});

it("offers the seven levels with the owner's names, no «Администратор»; «Суперадминистратор» is shut below a superadministrator", async () => {
  serve(DIRECTORY);
  render(<StaffPage />);
  await rowAction("Править", "Закупкин");
  const select = screen.getByLabelText("Уровень доступа");
  expect(select).toHaveValue("tech_lead");
  expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual([
    "Суперадминистратор", "Коммерческое руководство", "Технический руководитель", "Менеджер по проектам", "Кладовщик",
    "Бригадир", "Оператор",
  ]);
  expect(within(select).getAllByRole("option").map((option) => (option as HTMLOptionElement).value)).toEqual([
    "superadmin", "commercial", "tech_lead", "pm", "storekeeper", "foreman", "operator",
  ]);
  await waitFor(() => expect(within(select).getByRole("option", { name: "Суперадминистратор" })).toBeDisabled());
  expect(within(select).getByRole("option", { name: "Суперадминистратор" })).toHaveAttribute(
    "title", "Уровень «Суперадминистратор» выдаёт и снимает только суперадминистратор",
  );
  for (const name of ["Коммерческое руководство", "Технический руководитель", "Менеджер по проектам", "Кладовщик", "Бригадир", "Оператор"]) {
    expect(within(select).getByRole("option", { name })).toBeEnabled();
  }
});

it("a superadministrator can give any level, an administrator cannot move a holder of a full level", async () => {
  serve(DIRECTORY, () => undefined, ME_SUPERADMIN);
  const view = render(<StaffPage />);
  await rowAction("Править", "Закупкин");
  await waitFor(() => expect(within(screen.getByLabelText("Уровень доступа")).getByRole("option", { name: "Суперадминистратор" })).toBeEnabled());
  expect(within(screen.getByLabelText("Уровень доступа")).getByRole("option", { name: "Кладовщик" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
  await rowAction("Править", "Админов");
  expect(screen.getByLabelText("Уровень доступа")).toBeEnabled();
  view.unmount();
  vi.restoreAllMocks();

  serve(DIRECTORY); // the session is in «Режим админа»
  render(<StaffPage />);
  await rowAction("Править", "Админов");
  const select = screen.getByLabelText("Уровень доступа");
  expect(select).toHaveValue("superadmin");
  expect(select).toBeDisabled();
  expect(select.closest("label")).toHaveAttribute("title", expect.stringContaining("только суперадминистратор"));
});

it("sends a flag only when its switch was touched (the rest is KEEP) and shows the server's refusal in the banner", async () => {
  const updates: Array<Record<string, unknown>> = [];
  serve(TEAM, (path, init) => {
    if (path === "/api/auth/staff/u3" && init?.method === "PUT") {
      updates.push(JSON.parse(String(init.body)));
      return updates.length === 1
        ? json({ detail: "Уровень «Суперадминистратор» выдаёт и снимает только суперадминистратор." }, 403)
        : json({ user: { ...TEAM.users[2], shop_worker: true, site_master: false } });
    }
  });

  render(<StaffPage />);
  await rowAction("Править", "Мастеров");
  expect(screen.getByLabelText("Монтажник (работает в цеху)")).not.toBeChecked();
  expect(screen.getByLabelText("Мастер площадки")).toBeChecked();
  expect(screen.getByLabelText("Мастер площадки")).toHaveAttribute("role", "switch");

  // Nothing touched: no flag in the request.
  fireEvent.click(screen.getByRole("button", { name: "Сохранить сотрудника" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("выдаёт и снимает только суперадминистратор");
  expect(updates[0]).not.toHaveProperty("shop_worker");
  expect(updates[0]).not.toHaveProperty("site_master");
  expect(updates[0]).toMatchObject({ access_level: "foreman" });

  // Both switches turned: both flags are sent, as booleans.
  fireEvent.click(screen.getByLabelText("Монтажник (работает в цеху)"));
  fireEvent.click(screen.getByLabelText("Мастер площадки"));
  fireEvent.click(screen.getByRole("button", { name: "Сохранить сотрудника" }));
  await waitFor(() => expect(updates).toHaveLength(2));
  expect(updates[1]).toMatchObject({ shop_worker: true, site_master: false });

  // A switch turned and turned back is untouched again.
  await rowAction("Править", "Мастеров");
  fireEvent.click(screen.getByLabelText("Монтажник (работает в цеху)"));
  fireEvent.click(screen.getByLabelText("Монтажник (работает в цеху)"));
  fireEvent.click(screen.getByRole("button", { name: "Сохранить сотрудника" }));
  await waitFor(() => expect(updates).toHaveLength(3));
  expect(updates[2]).not.toHaveProperty("shop_worker");
});

it("keeps the position and the access level in separate table columns", async () => {
  serve(TEAM);
  render(<StaffPage />);
  const table = await screen.findByRole("region", { name: "Сотрудники" });
  const headers = within(table).getAllByRole("columnheader").map((header) => header.textContent);
  expect(headers.slice(0, 8)).toEqual(["ФИО", "Таб. №", "Должность", "Логин / e-mail", "Отдел", "Площадка", "Доступ", "Вход"]);
  const cell = (name: string, column: string) => {
    const row = within(table).getByText(name).closest("tr") as HTMLElement;
    return within(row).getAllByRole("cell")[headers.indexOf(column)];
  };
  // The position column carries the position and the qualification, never the access level.
  expect(cell("Мастеров", "Должность")).toHaveTextContent("Мастер участка5 разряд");
  expect(cell("Мастеров", "Доступ")).toHaveTextContent("Бригадир");
  expect(cell("Закупкин", "Должность")).toHaveTextContent("—");
  expect(cell("Закупкин", "Должность")).not.toHaveTextContent("Технический руководитель");
  expect(cell("Закупкин", "Доступ")).toHaveTextContent("Технический руководитель");
  expect(cell("Админов", "Доступ")).toHaveTextContent("Суперадминистратор");
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
  await rowAction("Править", "Мастеров");
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

it("opens the card by a click on the whole row; edit and delete sit in the row menu, delete last and in red", async () => {
  serve(DIRECTORY);
  const openCard = vi.fn();
  render(<StaffPage openCard={openCard} />);
  const list = await screen.findByRole("region", { name: "Сотрудники" });
  expect(within(list).queryByRole("button", { name: /^(Править|Удалить|Карточка)/ })).not.toBeInTheDocument();

  fireEvent.click(within(list).getByText("Закупкин"));
  expect(openCard).toHaveBeenCalledWith("u2");
  expect(screen.queryByRole("heading", { name: "Правка: Закупкин" })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Действия: Закупкин" }));
  const items = screen.getAllByRole("menuitem");
  expect(items.map((item) => item.textContent)).toEqual(["Открыть карточку", "Править", "Удалить"]);
  expect(items[2]).toHaveClass("danger");
  expect(openCard).toHaveBeenCalledTimes(1); // the menu itself does not open the card
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
});

it("without a card handler a click on the row opens the edit form", async () => {
  serve(DIRECTORY);
  render(<StaffPage />);
  const list = await screen.findByRole("region", { name: "Сотрудники" });
  fireEvent.click(within(list).getByText("Закупкин"));
  expect(await screen.findByRole("heading", { name: "Правка: Закупкин" })).toBeInTheDocument();
});
