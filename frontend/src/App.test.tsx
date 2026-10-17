import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { Principal } from "./api";

const principal: Principal = {
  user: {
    id: "10000000-0000-0000-0000-000000000001",
    email: "staff@example.test",
    name: "Сотрудник",
    department: "КБ",
  },
  roles: ["operator"],
  access_level: "operator",
  permissions: { requests: "view", procurement: "view" },
};

// Access levels (owner 04.10.2026): the level key travels in `roles` and `access_level`; the screens read sections only.
const as = (level: string, permissions: Principal["permissions"], extra: Partial<Principal> = {}): Principal =>
  ({ ...principal, roles: [level], access_level: level, permissions, ...extra });

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  window.history.replaceState({}, "", "/");
});

it("returns to login when an active session is rejected", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(principal);
    if (path === "/api/auth/login-staff") return json({ staff: [] });
    if (path.startsWith("/api/requests")) {
      return json({ detail: "Требуется вход в ERP" }, 401);
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Заявки" }));

  expect(await screen.findByRole("button", { name: "Войти" }, { timeout: 5000 })).toBeInTheDocument();
});

it("opens procurement by the section grant alone: the level's name decides nothing", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json(principal));

  render(<App />);

  expect(await screen.findByText("Добрый день, Сотрудник")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Лист закупки" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Заявки" })).toBeInTheDocument();
});

it("draws the block tiles with line icons, never emoji (audit М-6)", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json(principal));

  render(<App />);

  await screen.findByText("Добрый день, Сотрудник");
  const tiles = document.querySelectorAll(".block-tile");
  expect(tiles.length).toBeGreaterThan(0);
  tiles.forEach((tile) => {
    expect(tile.querySelector(".tile-icon svg.block-icon")).not.toBeNull();
    expect(tile.querySelector(".tile-icon")?.textContent).toBe("");
    // Счётчик подписан (владелец 16.10: «1, 2, 2 — непонятно, что считают»).
    expect(tile.querySelector(".tile-count")?.textContent).toMatch(/^\d+ раздел(а|ов)?$/);
  });
});

it("closes procurement without a grant on it, whatever the level", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ ...principal, permissions: { requests: "view" } }));

  render(<App />);

  expect(await screen.findByText("Добрый день, Сотрудник")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Лист закупки" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Заявки" })).toBeInTheDocument();
});

it("ID-6: a view grant opens procurement, receiving and stock read-only", async () => {
  window.history.replaceState({}, "", "/login?section=stock");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json({
      ...as("commercial", { procurement: "view", receiving: "view", warehouse: "view" }),
    });
    if (path === "/api/stock") return json({ lots: [], total: 0, truncated: 0, next_cursor: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);

  expect(await screen.findByText("Склад открыт вам только на просмотр.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "+ Внести позиции" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Лист закупки" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Приёмка" })).toBeInTheDocument();
});

it("ID-6: a storekeeper keeps the edit controls", async () => {
  window.history.replaceState({}, "", "/login?section=stock");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(as("foreman", { warehouse: "edit" }));
    if (path === "/api/stock") return json({ lots: [], total: 0, truncated: 0, next_cursor: null });
    if (path === "/api/stock/item-types") return json({ types: [], choices: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);

  expect(await screen.findByRole("button", { name: "+ Внести позиции" })).toBeInTheDocument();
  expect(screen.queryByText("Склад открыт вам только на просмотр.")).not.toBeInTheDocument();
});

it("opens the native warehouse at the live ERP stock route", async () => {
  window.history.replaceState({}, "", "/login?section=stock");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(as("foreman", { warehouse: "edit" }));
    if (path === "/api/stock") {
      return json({ lots: [], total: 0, truncated: 0, next_cursor: null });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);

  expect(await screen.findByRole("heading", { name: "Склад", level: 1 })).toBeInTheDocument();
  expect(screen.getAllByText("Склад").length).toBeGreaterThan(1);
});

it("opens native production fulfillment at the issues route", async () => {
  window.history.replaceState({}, "", "/login?section=issues");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(as("foreman", { fulfillment: "view" }));
    if (path === "/api/issues") return json({ requests: [], next_cursor: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);

  expect(await screen.findByRole("heading", { name: "Запросы на комплектацию", level: 1 })).toBeInTheDocument();
  expect(await screen.findByText("Заявок на комплектацию нет. Они появятся из состава производственного комплекта.")).toBeInTheDocument();
});

it("opens native resource planning at the production route", async () => {
  window.history.replaceState({}, "", "/login?section=production");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(as("tech_lead", { production: "view" }));
    if (path === "/api/production/tasks") return json({ tasks: [], next_cursor: null });
    if (path === "/api/production/operators") return json({ operators: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);

  expect(await screen.findByRole("heading", { name: "Ресурсное планирование", level: 1 })).toBeInTheDocument();
  expect(await screen.findByText("Проектов с выданной техкартой пока нет.")).toBeInTheDocument();
});

it("opens the ОТК section at the quality route for an edit grant on «ОТК»", async () => {
  window.history.replaceState({}, "", "/login?section=quality");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(as("foreman", { quality: "edit", production: "view" }));
    if (path === "/api/production/tasks") return json({ tasks: [], next_cursor: null });
    if (path === "/api/production/boxes") return json({ boxes: [] });
    if (path === "/api/production/instruments") return json({ instruments: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);

  expect(await screen.findByRole("heading", { name: "ОТК", level: 1 })).toBeInTheDocument();
  expect(await screen.findByText(/Коробок на проверку нет/)).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Средства измерения" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "ОТК" })).toBeInTheDocument();
});

it("does not open the ОТК section without the quality key, even with edit on planning", async () => {
  window.history.replaceState({}, "", "/login?section=quality");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(as("foreman", { production: "edit", myops: "edit" }));
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);

  expect(await screen.findByText("Добрый день, Сотрудник")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "ОТК", level: 1 })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "ОТК" })).not.toBeInTheDocument();
});

it("opens the native production plan at the prodplan route", async () => {
  window.history.replaceState({}, "", "/login?section=prodplan");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(as("tech_lead", { production: "view" }));
    if (path === "/api/production/plan") return json({ blockers: [], stages: [], rows: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);

  expect(await screen.findByRole("heading", { name: "Производственный план", level: 1 })).toBeInTheDocument();
  expect(await screen.findByText("Проектов в плане нет — заведите проект в «Проектах».")).toBeInTheDocument();
});

it("opens native finished-goods and shipping at the shipping route", async () => {
  window.history.replaceState({}, "", "/login?section=shipping");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(as("foreman", { shipping_goods: "edit" }));
    if (path === "/api/shipping/goods") return json({ goods: [] });
    if (path === "/api/shipping/shipments") return json({ shipments: [] });
    if (path === "/api/shipping/project-addressees") return json({ addressees: [] });
    if (path === "/api/production/finished-goods-ready") return json({ boxes: [] });
    if (path === "/api/shipping/finished-goods/from-production") return json({ rows: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);

  expect(await screen.findByRole("heading", { name: "Отгрузка", level: 1 })).toBeInTheDocument();
  // Приёмка с производства — отдельный раздел; «Отгрузка» открывается на «На складе».
  expect(await screen.findByText(/На складе ничего не ждёт упаковки/)).toBeInTheDocument();
});

it("opens native references at the references route", async () => {
  window.history.replaceState({}, "", "/login?section=references");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json({
      ...principal,
      permissions: { erpref: "view" },
    });
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path === "/api/references/bases") return json({ bases: [] });
    if (path.startsWith("/api/references/tech-operations?")) return json({ metadata: null, records: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);

  expect(await screen.findByRole("heading", { name: "База данных", level: 1 })).toBeInTheDocument();
  expect(await screen.findByText("Записей нет.")).toBeInTheDocument();
});

it("opens the personal notification inbox for every authenticated user", async () => {
  window.history.replaceState({}, "", "/login?section=notifications");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(principal);
    if (path === "/api/notifications") return json({ notifications: [], unread: 0 });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);

  expect(await screen.findByRole("heading", { name: "Уведомления", level: 1 })).toBeInTheDocument();
  expect(await screen.findByText("Новых событий пока нет.")).toBeInTheDocument();
});

const CARD = {
  as_of: "2026-10-04",
  header: { id: principal.user.id, name: "Сотрудник Первый", department: "КБ", roles: [{ key: "staff", label: "Сотрудник" }], site: null, site_label: null, active: true },
  subordinates: [], projects: [], skills: [],
  absences: { upcoming: [], next_vacation: null },
  hours_this_month: { month: "2026-10", label: "октябрь 2026", hours: null, sessions: null },
  requests: { count: null, items: [] },
  tasks: { count: null, items: [] },
};

it("opens the own card from «Моя карточка» in the sidebar profile and keeps the menu free of it", async () => {
  const paths: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    paths.push(path);
    if (path === "/api/auth/me") return json(principal);
    if (path === "/api/employee-card/me") return json(CARD);
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);
  await screen.findByText("Добрый день, Сотрудник");
  expect(screen.queryAllByRole("button", { name: "Личная карточка" })).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Моя карточка" }));

  expect(await screen.findByRole("heading", { name: "Сотрудник Первый", level: 1 })).toBeInTheDocument();
  expect(window.location.search).toBe("?section=card");
  expect(paths).toContain("/api/employee-card/me");
});

it("opens someone else's card from ?section=card&user= and edits through the staff form", async () => {
  const other = "20000000-0000-0000-0000-000000000002";
  window.history.replaceState({}, "", `/login?section=card&user=${other}`);
  const admin = as("admin", { staffdir: "edit" });
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(admin);
    if (path === `/api/employee-card/${other}`) return json({ ...CARD, header: { ...CARD.header, id: other, name: "Сидоров Олег" } });
    if (path === "/api/auth/staff") return json({
      users: [{ id: other, login: "o@example.test", email: "o@example.test", name: "Сидоров Олег", department: "КБ", access_level: "operator", site: "", active: true, shop_worker: false, site_master: false, pin_set: false }],
      departments: ["КБ"], levels: [{ key: "operator", label: "Оператор", full: false }], sites: [],
    });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);
  expect(await screen.findByRole("heading", { name: "Сидоров Олег", level: 1 })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Редактировать" }));

  expect(await screen.findByRole("heading", { name: "Правка: Сидоров Олег" })).toBeInTheDocument();
  expect(window.location.search).toBe(`?section=staff&user=${other}`);
});

it("opens the restricted operations journal by the operations key", async () => {
  window.history.replaceState({}, "", "/login?section=operations");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(as("tech_lead", { operations: "view" }));
    if (path === "/api/operations/audit") return json({ events: [] });
    if (path === "/api/operations/outbox") return json({ total: 0, published: 0, pending: 0, dead: 0, failures: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);

  expect(await screen.findByRole("heading", { name: "Системный журнал", level: 1 })).toBeInTheDocument();
  expect(await screen.findByText("Событий в этом разделе нет.")).toBeInTheDocument();
});

it("does not pretend to log out when the server cannot revoke the session", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (String(input) === "/api/auth/me") return json(principal);
    if (String(input) === "/api/auth/logout") throw new TypeError("network offline");
    throw new Error(`Unexpected request: ${String(input)}`);
  });

  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Выйти" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Выход не выполнен: network offline",
  );
  expect(screen.getByText("Добрый день, Сотрудник")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Войти" })).not.toBeInTheDocument();
});

it("NAV-1: home shows the blocks as tiles and a block shows its section cards in order", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json({
    ...principal,
    permissions: {
      newrequest: "edit", myrequests: "edit", requests: "edit", receiving: "view", warehouse: "view", production: "view",
      erpref: "view",
    },
  }));

  render(<App />);

  const main = await screen.findByRole("main");
  const tiles = within(main).getAllByRole("button").map((button) => button.getAttribute("aria-label"));
  expect(tiles).toEqual(["Производство", "Снабжение", "Склад", "Менеджмент", "Справочники"]);

  fireEvent.click(within(main).getByRole("button", { name: "Снабжение" }));

  expect(await screen.findByRole("heading", { name: /Снабжение/, level: 1 })).toBeInTheDocument();
  expect(within(screen.getByRole("main")).getAllByRole("button").map((button) => button.getAttribute("aria-label")))
    .toEqual([null, "Оформить заявку", "Мои заявки", "Заявки"]);
  expect(window.location.search).toBe("?block=supply");
  // Sidebar groups agree with the blocks.
  const nav = screen.getByRole("navigation", { name: "Основная навигация" });
  expect(within(nav).getAllByRole("heading", { level: 4 }).map((heading) => heading.textContent))
    .toEqual(["Производство", "Снабжение", "Склад", "Менеджмент", "Справочники"]);
});

it("NAV-1: a block with a single visible section opens that section directly", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json({ ...principal, permissions: {} });
    if (path === "/api/notifications") return json({ notifications: [], unread: 0 });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);

  const main = await screen.findByRole("main");
  expect(within(main).getAllByRole("button").map((button) => button.getAttribute("aria-label"))).toEqual(["Менеджмент"]);
  fireEvent.click(within(main).getByRole("button", { name: "Менеджмент" }));

  expect(await screen.findByRole("heading", { name: "Уведомления", level: 1 })).toBeInTheDocument();
});

it("NAV-1: a block URL the principal cannot open falls back to home", async () => {
  window.history.replaceState({}, "", "/?block=stock");
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ ...principal, permissions: { requests: "view" } }));

  render(<App />);

  expect(await screen.findByText("Добрый день, Сотрудник")).toBeInTheDocument();
  expect(within(screen.getByRole("main")).queryByRole("button", { name: "Склад" })).not.toBeInTheDocument();
});

it("NAV-1: handout is its own route and shows only requests waiting for the shop", async () => {
  window.history.replaceState({}, "", "/?section=handout");
  const base = { id: "i1", version: 1, number: "FRX-ISS-1", purpose: "КБ1", requester: "Инженер", lines: [] };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(as("foreman", { handout: "view" }));
    if (path === "/api/issues") return json({ requests: [
      { ...base, id: "i1", number: "FRX-ISS-1", status: "Новая" },
      // The handout screen opens on «Укомплектован» + «КБ Возвращена» (owner, 10.10.2026).
      { ...base, id: "i2", number: "FRX-ISS-2", status: "Укомплектован" },
    ], next_cursor: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);

  expect(await screen.findByRole("heading", { name: "Выдача со списанием", level: 1 })).toBeInTheDocument();
  expect(await screen.findByText("FRX-ISS-2")).toBeInTheDocument();
  expect(screen.queryByText("FRX-ISS-1")).not.toBeInTheDocument();
});

it("shows the unread bell on every screen and opens the inbox, which clears it", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(principal);
    if (path === "/api/notifications/unread") return json({ unread: 3 });
    if (path === "/api/notifications") return json({
      notifications: [{ id: "n1", created_at: "2026-09-29T09:00:00", type: "digest", reference: "", title: "Дайджест — просрочки и Карантин", body: "…", read: false }],
      unread: 3,
    });
    if (path === "/api/notifications/read" && init?.method === "POST") return json({ unread: 0 });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);

  expect(await screen.findByTestId("notif-badge")).toHaveTextContent("3");
  fireEvent.click(screen.getByTitle("Уведомления: 3 непрочитанных"));
  expect(await screen.findByRole("heading", { name: "Уведомления", level: 1 })).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "Прочитать все" }));
  expect(await screen.findByTitle("Уведомления")).toBeInTheDocument();
  expect(screen.queryByTestId("notif-badge")).not.toBeInTheDocument();
});

it("switches «Режим админа» for the allowed account and shows it in the menu (portalToggleAdmin)", async () => {
  const victor = { ...as("commercial", {}), role_label: "Коммерческое руководство", can_admin_mode: true, admin_mode: false };
  const lifted = { ...victor, ...as("admin", { staffdir: "edit" }), role_label: "Администратор · режим", admin_mode: true };
  const bodies: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(victor);
    if (path === "/api/auth/admin-mode") {
      bodies.push(JSON.parse(String(init?.body)));
      return json(lifted);
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);
  expect(await screen.findByText("Коммерческое руководство")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Режим админа" }));

  expect(await screen.findByText("Администратор · режим")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Выйти из админа" })).toBeInTheDocument();
  expect(bodies).toEqual([{ on: true }]);
});

it("offers no admin mode to other accounts", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ ...principal, can_admin_mode: false }));
  render(<App />);
  expect(await screen.findByText("Добрый день, Сотрудник")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Режим админа" })).not.toBeInTheDocument();
});

function navigationOf() {
  return within(screen.getByRole("navigation", { name: "Основная навигация" }));
}

it("shows superadmin «Доступы» and «Сотрудники» in the menu, whatever the permissions map holds", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json(as("superadmin", {})));

  render(<App />);
  await screen.findByText("Добрый день, Сотрудник");

  expect(navigationOf().getByRole("button", { name: "Доступы" })).toBeInTheDocument();
  expect(navigationOf().getByRole("button", { name: "Сотрудники" })).toBeInTheDocument();
  expect(navigationOf().getByRole("button", { name: "Системный журнал" })).toBeInTheDocument();
  expect(screen.getByText("Суперадминистратор")).toBeInTheDocument();
});

it("hides «Доступы» and «Сотрудники» from every other level, even with an edit grant on the directory", async () => {
  for (const level of ["commercial", "tech_lead", "foreman", "operator"]) {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json(as(level, { staffdir: "edit", requests: "view" })));
    const view = render(<App />);
    await screen.findByText("Добрый день, Сотрудник");
    expect(navigationOf().queryByRole("button", { name: "Доступы" })).not.toBeInTheDocument();
    expect(navigationOf().queryByRole("button", { name: "Сотрудники" })).not.toBeInTheDocument();
    view.unmount();
    vi.restoreAllMocks();
  }
});

it("shows an operator «Задания» only with the myops grant", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json(as("operator", { newrequest: "edit", myrequests: "edit", production: "edit" })));
  const without = render(<App />);
  await screen.findByText("Добрый день, Сотрудник");
  expect(navigationOf().queryByRole("button", { name: "Задания" })).not.toBeInTheDocument();
  without.unmount();
  vi.restoreAllMocks();

  vi.spyOn(globalThis, "fetch").mockResolvedValue(json(as("operator", { newrequest: "edit", myrequests: "edit", myops: "edit" })));
  render(<App />);
  await screen.findByText("Добрый день, Сотрудник");
  expect(navigationOf().getByRole("button", { name: "Задания" })).toBeInTheDocument();
});

it("marks a card «только просмотр» when every key that opens it is a view grant, «Отгрузка» included", async () => {
  window.history.replaceState({}, "", "/?block=stock");
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json(as("commercial", { warehouse: "edit", shipping: "view", shipping_goods: "view", receiving: "view" })));

  render(<App />);
  await screen.findByRole("heading", { name: /Склад/, level: 1 });

  const tile = (name: string) => within(screen.getByRole("main")).getByRole("button", { name });
  expect(within(tile("Отгрузка")).getByText("только просмотр")).toBeInTheDocument();
  expect(within(tile("Приёмка")).getByText("только просмотр")).toBeInTheDocument();
  expect(within(tile("Склад")).queryByText("только просмотр")).not.toBeInTheDocument();
});

it("marks no card «только просмотр» for a level with full access", async () => {
  window.history.replaceState({}, "", "/?block=stock");
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json(as("admin", { shipping: "view" })));

  render(<App />);
  await screen.findByRole("heading", { name: /Склад/, level: 1 });

  expect(within(screen.getByRole("main")).queryByText("только просмотр")).not.toBeInTheDocument();
});

it("names the level under the user from `access_level` when the payload has no role_label", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json(as("foreman", { requests: "view" })));
  render(<App />);
  await screen.findByText("Добрый день, Сотрудник");
  expect(screen.getByText("Бригадир")).toBeInTheDocument();
});

describe("the update-failure banner", () => {
  const failure = { ok: false, kind: "update", reason: "обновление не собралось" };
  const serve = (me: Principal) => vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json(me);
    if (path === "/update-status.json") return json(failure);
    if (path === "/version.json") return json({ version: "1" });
    throw new Error(`Unexpected request: ${path}`);
  });

  it("is shown to someone who edits more than their own work, and to full access", async () => {
    for (const me of [as("foreman", { warehouse: "edit" }), as("commercial", { requests: "edit" }), as("admin", {})]) {
      serve(me);
      const view = render(<App />);
      expect(await screen.findByText(/обновление не собралось/)).toBeInTheDocument();
      view.unmount();
      vi.restoreAllMocks();
    }
  });

  it("is not shown to an operator whose edit rights are their own tasks and requests", async () => {
    serve(as("operator", { newrequest: "edit", myrequests: "edit", myops: "edit", warehouse: "view" }));
    render(<App />);
    await screen.findByText("Добрый день, Сотрудник");
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByText(/обновление не собралось/)).not.toBeInTheDocument();
  });
});
