import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { App, resolveLegacyUrl } from "./App";
import type { Principal } from "./api";

const principal: Principal = {
  user: {
    id: "10000000-0000-0000-0000-000000000001",
    email: "staff@example.test",
    name: "Сотрудник",
    department: "КБ",
  },
  roles: ["staff"],
  permissions: { requests: "view", procurement: "view" },
};

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
    if (path.startsWith("/api/requests")) {
      return json({ detail: "Требуется вход в ERP" }, 401);
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Заявки" }));

  expect(await screen.findByRole("button", { name: "Войти" })).toBeInTheDocument();
});

it("does not show procurement to a role outside its view set, even with a view grant", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json(principal));

  render(<App />);

  expect(await screen.findByText("Добрый день, Сотрудник")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Лист закупки" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Заявки" })).toBeInTheDocument();
});

it("does not show procurement to a non-buyer even with an edit override", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    json({ ...principal, permissions: { ...principal.permissions, procurement: "edit" } }),
  );

  render(<App />);

  expect(await screen.findByText("Добрый день, Сотрудник")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Лист закупки" })).not.toBeInTheDocument();
});

it("ID-6: a view grant opens procurement, receiving and stock read-only", async () => {
  window.history.replaceState({}, "", "/login?section=stock");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json({
      ...principal,
      roles: ["management"],
      permissions: { procurement: "view", receiving: "view", warehouse: "view" },
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
    if (path === "/api/auth/me") return json({ ...principal, roles: ["receiver"], permissions: { warehouse: "edit" } });
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
    if (path === "/api/auth/me") return json({
      ...principal,
      roles: ["receiver"],
      permissions: { warehouse: "edit" },
    });
    if (path === "/api/stock") {
      return json({ lots: [], total: 0, truncated: 0, next_cursor: null });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);

  expect(await screen.findByRole("heading", { name: "Склад", level: 1 })).toBeInTheDocument();
  expect(screen.getByText("Остатки партиями: одна поставка — одна партия со своим номером.")).toBeInTheDocument();
  expect(screen.getAllByText("Склад").length).toBeGreaterThan(1);
});

it("opens native production fulfillment at the issues route", async () => {
  window.history.replaceState({}, "", "/login?section=issues");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json({
      ...principal,
      roles: ["master"],
      permissions: { fulfillment: "view" },
    });
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
    if (path === "/api/auth/me") return json({
      ...principal,
      roles: ["prodhead"],
      permissions: { production: "view" },
    });
    if (path === "/api/production/tasks") return json({ tasks: [], next_cursor: null });
    if (path === "/api/production/operators") return json({ operators: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);

  expect(await screen.findByRole("heading", { name: "Ресурсное планирование", level: 1 })).toBeInTheDocument();
  expect(await screen.findByText("Производственных заданий пока нет.")).toBeInTheDocument();
});

it("opens the native production plan at the prodplan route", async () => {
  window.history.replaceState({}, "", "/login?section=prodplan");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json({
      ...principal,
      roles: ["prodhead"],
      permissions: { production: "view" },
    });
    if (path === "/api/production/plan") return json({ blockers: [], stages: [], rows: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);

  expect(await screen.findByRole("heading", { name: "Производственный план", level: 1 })).toBeInTheDocument();
  expect(await screen.findByText("Проектов в этом фильтре нет.")).toBeInTheDocument();
});

it("opens native finished-goods and shipping at the shipping route", async () => {
  window.history.replaceState({}, "", "/login?section=shipping");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json({
      ...principal,
      roles: ["receiver"],
      permissions: { shipping: "edit" },
    });
    if (path === "/api/shipping/goods") return json({ goods: [] });
    if (path === "/api/shipping/shipments") return json({ shipments: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);

  expect(await screen.findByRole("heading", { name: "Готовая продукция и отгрузка", level: 1 })).toBeInTheDocument();
  expect(await screen.findByText("Заявок на отгрузку пока нет.")).toBeInTheDocument();
});

it("opens native references at the references route", async () => {
  window.history.replaceState({}, "", "/login?section=references");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json({
      ...principal,
      permissions: { references: "view" },
    });
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);

  expect(await screen.findByRole("heading", { name: "Справочники", level: 1 })).toBeInTheDocument();
  expect(await screen.findByText("Карты соответствий пока не заполнены.")).toBeInTheDocument();
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

it("opens the restricted operations journal for management", async () => {
  window.history.replaceState({}, "", "/login?section=operations");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/auth/me") return json({
      ...principal,
      roles: ["management"],
      permissions: { operations: "view" },
    });
    if (path === "/api/operations/audit") return json({ events: [] });
    if (path === "/api/operations/outbox") return json({ total: 0, published: 0, pending: 0, dead: 0, failures: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<App />);

  expect(await screen.findByRole("heading", { name: "Системный журнал", level: 1 })).toBeInTheDocument();
  expect(await screen.findByText("Событий по выбранному фильтру нет.")).toBeInTheDocument();
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

it("rejects a same-origin legacy ERP frame", () => {
  expect(
    resolveLegacyUrl("https://erp.example.test/legacy", "https://erp.example.test/", "projects", "grant"),
  ).toBeNull();
  expect(
    resolveLegacyUrl("https://erp.example.test:8443", "https://erp.example.test/", "projects", "grant"),
  ).toBeNull();
  expect(
    resolveLegacyUrl("https://legacy.example.test", "https://erp.example.test/", "projects", "grant"),
  ).toBe("https://legacy.example.test/login?section=projects&ticket=grant");
  expect(
    resolveLegacyUrl("javascript:alert(1)", "https://erp.example.test/", "projects", "grant"),
  ).toBeNull();
});

it("NAV-1: home shows the blocks as tiles and a block shows its section cards in order", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(json({
    ...principal,
    permissions: {
      newrequest: "edit", myrequests: "edit", requests: "edit", receiving: "view", warehouse: "view", production: "view",
      references: "view",
    },
  }));

  render(<App />);

  const main = await screen.findByRole("main");
  const tiles = within(main).getAllByRole("button").map((button) => button.getAttribute("aria-label"));
  expect(tiles).toEqual(["Производство", "Снабжение", "Склад", "Менеджмент"]);

  fireEvent.click(within(main).getByRole("button", { name: "Снабжение" }));

  expect(await screen.findByRole("heading", { name: /Снабжение/, level: 1 })).toBeInTheDocument();
  expect(within(screen.getByRole("main")).getAllByRole("button").map((button) => button.getAttribute("aria-label")))
    .toEqual([null, "Оформить заявку", "Мои заявки", "Заявки"]);
  expect(window.location.search).toBe("?block=supply");
  // Sidebar groups agree with the blocks.
  const nav = screen.getByRole("navigation", { name: "Основная навигация" });
  expect(within(nav).getAllByRole("heading", { level: 4 }).map((heading) => heading.textContent))
    .toEqual(["Производство", "Снабжение", "Склад", "Менеджмент"]);
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
    if (path === "/api/auth/me") return json({ ...principal, roles: ["master"], permissions: { handout: "view" } });
    if (path === "/api/issues") return json({ requests: [
      { ...base, id: "i1", number: "FRX-ISS-1", status: "Новая" },
      { ...base, id: "i2", number: "FRX-ISS-2", status: "Готов к выдаче" },
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
