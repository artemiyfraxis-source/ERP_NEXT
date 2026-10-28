import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "./App";
import type { EmployeeCard, Principal } from "./api";

const ADMIN = "10000000-0000-0000-0000-000000000001";
const WORKER = "20000000-0000-0000-0000-000000000002";
const GONE = "30000000-0000-0000-0000-000000000003";

const admin: Principal = {
  user: { id: ADMIN, email: "admin@example.test", name: "Админ Главный", department: "ИТ" },
  roles: ["admin"], access_level: "admin", permissions: {}, role_label: "Администратор", viewing: null,
};
// What the server answers while viewing: the worker's identity and rights, the administrator as the viewer.
const viewing: Principal = {
  user: { id: WORKER, email: "worker@example.test", name: "Иванов Пётр", department: "Монтаж" },
  roles: ["operator"], access_level: "operator", permissions: { requests: "view", procurement: "view" },
  role_label: "Оператор",
  viewing: { viewer: { id: ADMIN, name: "Админ Главный" }, until: "2026-10-12T14:30:00+03:00" },
};

function card(id: string, name: string): EmployeeCard {
  return {
    as_of: "2026-10-12",
    header: { id, name, department: "ИТ", roles: [{ key: "admin", label: "Администратор" }], site: null, site_label: null, active: true },
    subordinates: [], projects: [], skills: [],
    absences: { upcoming: [], next_vacation: null },
    hours_this_month: { month: "2026-10", label: "октябрь 2026", hours: null, sessions: null },
    requests: { count: null, items: [] },
    tasks: { count: null, items: [] },
  };
}

const STAFF = {
  users: [
    { id: ADMIN, login: "admin", email: "admin@example.test", name: "Админ Главный", department: "ИТ", access_level: "admin", site: "", active: true, shop_worker: false, site_master: false },
    { id: WORKER, login: "worker", email: "worker@example.test", name: "Иванов Пётр", department: "Монтаж", access_level: "operator", access_level_label: "Оператор", site: "ЦАР", active: true, shop_worker: true, site_master: false },
    { id: GONE, login: "gone", email: "gone@example.test", name: "Уволенный Сидор", department: "Монтаж", access_level: "operator", site: "ЦАР", active: false, shop_worker: true, site_master: false },
    { id: "40000000-0000-0000-0000-000000000004", login: "buyer", email: "buyer@example.test", name: "Закупщица Анна", department: "Снабжение", access_level: "foreman", site: "", active: true, shop_worker: false, site_master: false },
  ],
  departments: [], department_heads: {}, levels: [], sites: [],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Call = { method: string; path: string; body?: string };

/** The server: `me` first, then whatever the view-as calls switch it to. */
function serve(first: Principal) {
  let current = first;
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ method, path, body: typeof init?.body === "string" ? init.body : undefined });
    if (path === "/api/auth/me") return json(current);
    if (path === "/api/auth/view-as" && method === "POST") { current = viewing; return json(viewing); }
    if (path === "/api/auth/view-as" && method === "DELETE") { current = admin; return json(admin); }
    if (path === "/api/auth/staff") return json(STAFF);
    if (path.startsWith("/api/employee-card/")) return json(current.viewing ? card(WORKER, "Иванов Пётр") : card(ADMIN, "Админ Главный"));
    if (path.includes("/leave/") || path.includes("/documents/")) return json({ count: 0 });
    return json({ detail: "нет в тесте" }, 404);
  });
  return calls;
}

afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState({}, "", "/");
});

it("keeps the «only view» banner on every page and returns to the own card", async () => {
  const calls = serve(viewing);
  render(<App />);

  const banner = await screen.findByRole("status");
  expect(banner).toHaveTextContent("Вы смотрите как Иванов Пётр (только просмотр) · до 12.10 14:30");
  // The whole app is the worker's: their greeting and their sections.
  expect(screen.getByText("Добрый день, Иванов Пётр")).toBeInTheDocument();
  const nav = within(screen.getByRole("navigation", { name: "Основная навигация" }));
  expect(nav.getByRole("button", { name: "Запросы КП" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Сотрудники" })).not.toBeInTheDocument();

  fireEvent.click(nav.getByRole("button", { name: "Первичная документация" }));
  expect(screen.getByText(/Вы смотрите как/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Вернуться к себе" }));

  expect(await screen.findByRole("heading", { name: "Админ Главный" })).toBeInTheDocument();
  expect(screen.queryByText(/Вы смотрите как/)).not.toBeInTheDocument();
  expect(calls.some((call) => call.method === "DELETE" && call.path === "/api/auth/view-as")).toBe(true);
  expect(window.location.search).toBe("?section=card");
});

it("starts from the administrator's own card: pick a working employee, then the app is theirs", async () => {
  window.history.replaceState({}, "", "/?section=card");
  const calls = serve(admin);
  render(<App />);

  fireEvent.click(await screen.findByRole("button", { name: "Смотреть как сотрудник" }));
  const dialog = await screen.findByRole("dialog", { name: "Смотреть как сотрудник" });
  const list = await within(dialog).findByRole("list", { name: "Сотрудники" });
  // Working people only, never oneself.
  expect(within(list).getAllByRole("button").map((button) => button.querySelector("strong")?.textContent)).toEqual([
    "Закупщица Анна", "Иванов Пётр",
  ]);
  fireEvent.change(within(dialog).getByRole("searchbox", { name: "Поиск сотрудника" }), { target: { value: "монтаж" } });
  expect(within(list).getAllByRole("button")).toHaveLength(1);

  fireEvent.click(within(list).getByRole("button", { name: /Иванов Пётр/ }));

  expect(await screen.findByText(/Вы смотрите как/)).toBeInTheDocument();
  expect(screen.getByText("Добрый день, Иванов Пётр")).toBeInTheDocument();
  const start = calls.find((call) => call.method === "POST" && call.path === "/api/auth/view-as");
  expect(JSON.parse(start?.body ?? "{}")).toEqual({ user_id: WORKER });
});

it("offers the view only to an administrator on their own card, never from inside a view", async () => {
  window.history.replaceState({}, "", "/?section=card");
  serve({ ...viewing, viewing: null, permissions: {} });
  const { unmount } = render(<App />);
  expect(await screen.findByRole("heading", { name: "Админ Главный" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Смотреть как сотрудник" })).not.toBeInTheDocument();
  unmount();
  vi.restoreAllMocks();

  // Viewing as another administrator: their own card shows no second «Смотреть как».
  serve({ ...viewing, roles: ["admin"], access_level: "admin" });
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Иванов Пётр" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Смотреть как сотрудник" })).not.toBeInTheDocument();
});
