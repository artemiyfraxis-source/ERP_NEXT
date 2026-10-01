import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { StaffPage } from "./StaffPage";
import type { StaffDirectory } from "./api";

const DIRECTORY: StaffDirectory = {
  users: [
    { id: "u1", login: "admin", email: "admin@example.test", name: "Админов", department: "ИТ", role: "admin", site: "", active: true, pin_set: true },
    { id: "u2", login: "buyer@example.test", email: "buyer@example.test", name: "Закупкин", department: "Снабжение", role: "buyer", site: "ЦАР", active: true, pin_set: false },
  ],
  departments: ["ИТ", "Снабжение"],
  roles: [{ key: "admin", label: "Технолог" }, { key: "buyer", label: "Специалист по снабжению" }, { key: "master", label: "Мастер" }],
  sites: [{ code: "ЦАР", city: "Москва" }, { code: "ЖУК", city: "Раменское" }],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

it("lists employees with role labels and creates a new one with a password", async () => {
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
  fireEvent.change(screen.getByLabelText("Место работы"), { target: { value: "ЖУК" } });
  fireEvent.change(screen.getByLabelText("Роль"), { target: { value: "master" } });
  fireEvent.change(screen.getByLabelText("Пароль"), { target: { value: "first-password" } });
  fireEvent.click(screen.getByRole("button", { name: "Завести сотрудника" }));

  await waitFor(() => expect(created).toEqual({
    login: "", email: "new@example.test", name: "Новиков", department: "Снабжение", role: "master", site: "ЖУК",
    password: "first-password", pin: "",
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
    { name: "Админов", department: "ИТ", role: "admin", site: "", active: false, password: null, pin: null },
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
    { name: "Закупкин", department: "Снабжение", role: "buyer", site: "ЦАР", active: true, password: null, pin: "7654321" },
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
