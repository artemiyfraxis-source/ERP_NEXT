import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { NOTIFICATIONS_CHANGED } from "./NotificationBell";
import { NotificationsPage } from "./NotificationsPage";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const DIGEST = {
  id: "10000000-0000-0000-0000-000000000001", created_at: "2026-10-04T00:00:00", type: "digest", reference: "",
  title: "Дайджест — просрочки и Карантин", body: "Просрочено: 3\nВ Карантине: 2", read: false,
};
const STATUS = {
  id: "10000000-0000-0000-0000-000000000002", created_at: "2026-10-03T09:30:00", type: "status", reference: "SM-1",
  title: "Статус изменён", body: "Заявка передана в закупку", read: true,
};
const MYSTERY = {
  id: "10000000-0000-0000-0000-000000000003", created_at: "2026-10-02T09:30:00", type: "brand_new_kind", reference: "",
  title: "Новый вид события", body: "…", read: true,
};

type Call = { method: string; path: string };

/** Подменяет ERP: список уведомлений и ответы на удаление; все запросы остаются в `calls`. */
function mockInbox(items: unknown[], answers: Record<string, () => Response> = {}) {
  const calls: Call[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ method, path });
    const answer = answers[`${method} ${path}`];
    if (answer) return answer();
    if (method === "GET" && path === "/api/notifications") return json({ notifications: items, unread: 1 });
    throw new Error(`Unexpected request: ${method} ${path}`);
  });
  return calls;
}

afterEach(() => vi.restoreAllMocks());

it("shows Russian type labels and dates, never a raw type code", async () => {
  mockInbox([DIGEST, STATUS, MYSTERY]);

  render(<NotificationsPage />);

  expect(await screen.findByText("Дайджест — просрочки и Карантин")).toBeInTheDocument();
  expect(screen.getByText("04.10.2026 00:00")).toBeInTheDocument();
  expect(screen.getByText("03.10.2026 09:30")).toBeInTheDocument();
  const table = screen.getByRole("table");
  const badges = [...table.querySelectorAll(".status-chip")].map((badge) => badge.textContent);
  expect(badges).toEqual(["Сводка", "Статус заявки", "Событие"]); // an unknown kind falls back, not «brand_new_kind»
  for (const raw of ["digest", "status", "brand_new_kind"]) expect(table.textContent).not.toContain(raw);
  expect(screen.getByText("1 непрочитанных")).toBeInTheDocument();
});

it("marks a notification read from its row", async () => {
  const calls = mockInbox([DIGEST, STATUS], { "POST /api/notifications/read": () => json({ unread: 0 }) });

  render(<NotificationsPage />);
  const row = (await screen.findByText("Дайджест — просрочки и Карантин")).closest("tr")!;
  fireEvent.click(within(row).getByRole("button", { name: "Прочитано" }));

  await waitFor(() => expect(calls).toContainEqual({ method: "POST", path: "/api/notifications/read" }));
  await waitFor(() => expect(within(row).queryByRole("button", { name: "Прочитано" })).not.toBeInTheDocument());
});

it("deletes one notification from its row and tells the bell the new unread count", async () => {
  const calls = mockInbox([DIGEST, STATUS], { [`DELETE /api/notifications/${DIGEST.id}`]: () => json({ unread: 0 }) });
  const heard: number[] = [];
  const listener = (event: Event) => heard.push((event as CustomEvent<number>).detail);
  window.addEventListener(NOTIFICATIONS_CHANGED, listener);

  try {
    render(<NotificationsPage />);
    const row = (await screen.findByText("Дайджест — просрочки и Карантин")).closest("tr")!;
    fireEvent.click(within(row).getByRole("button", { name: "Удалить" }));

    await waitFor(() => expect(screen.queryByText("Дайджест — просрочки и Карантин")).not.toBeInTheDocument());
    expect(calls).toContainEqual({ method: "DELETE", path: `/api/notifications/${DIGEST.id}` });
    expect(screen.getByText("Статус изменён")).toBeInTheDocument();
    expect(heard).toEqual([0]);
    expect(screen.getByText("всё прочитано")).toBeInTheDocument();
  } finally {
    window.removeEventListener(NOTIFICATIONS_CHANGED, listener);
  }
});

it("«Удалить прочитанные» removes only the read ones", async () => {
  const calls = mockInbox([DIGEST, STATUS, MYSTERY], {
    "DELETE /api/notifications?scope=read": () => json({ deleted: 2, unread: 1 }),
  });

  render(<NotificationsPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Удалить прочитанные" }));

  expect(await screen.findByText("Удалено уведомлений: 2")).toBeInTheDocument();
  expect(calls).toContainEqual({ method: "DELETE", path: "/api/notifications?scope=read" });
  expect(screen.getByText("Дайджест — просрочки и Карантин")).toBeInTheDocument();
  expect(screen.queryByText("Статус изменён")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Удалить прочитанные" })).not.toBeInTheDocument();
});

it("«Удалить все» empties the inbox", async () => {
  const calls = mockInbox([DIGEST, STATUS], { "DELETE /api/notifications?scope=all": () => json({ deleted: 2, unread: 0 }) });

  render(<NotificationsPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Удалить все" }));

  expect(await screen.findByText("Новых событий пока нет.")).toBeInTheDocument();
  expect(calls).toContainEqual({ method: "DELETE", path: "/api/notifications?scope=all" });
  expect(screen.queryByRole("button", { name: "Удалить все" })).not.toBeInTheDocument();
  expect(screen.getByText("Удалено уведомлений: 2")).toBeInTheDocument();
});

it("deletes the selected rows in one request", async () => {
  const path = `/api/notifications?id=${DIGEST.id}&id=${MYSTERY.id}`;
  const calls = mockInbox([DIGEST, STATUS, MYSTERY], { [`DELETE ${path}`]: () => json({ deleted: 2, unread: 0 }) });

  render(<NotificationsPage />);
  await screen.findByText("Статус изменён");
  fireEvent.click(screen.getByLabelText(`Выбрать ${DIGEST.id}`));
  fireEvent.click(screen.getByLabelText(`Выбрать ${MYSTERY.id}`));
  fireEvent.click(screen.getByRole("button", { name: "Удалить выбранные" }));

  await waitFor(() => expect(screen.queryByText("Новый вид события")).not.toBeInTheDocument());
  expect(calls).toContainEqual({ method: "DELETE", path });
  expect(screen.getByText("Статус изменён")).toBeInTheDocument();
});

it("keeps the row and shows the error when the server refuses the delete", async () => {
  mockInbox([DIGEST], { [`DELETE /api/notifications/${DIGEST.id}`]: () => json({ detail: "Уведомление не найдено" }, 404) });

  render(<NotificationsPage />);
  const row = (await screen.findByText("Дайджест — просрочки и Карантин")).closest("tr")!;
  fireEvent.click(within(row).getByRole("button", { name: "Удалить" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("Уведомление не найдено");
  expect(screen.getByText("Дайджест — просрочки и Карантин")).toBeInTheDocument();
});

it("opens the full text of a notification under its row", async () => {
  mockInbox([DIGEST]);

  render(<NotificationsPage />);
  fireEvent.click((await screen.findByText("Дайджест — просрочки и Карантин")).closest("tr")!);

  const detail = document.querySelector(".notification-detail");
  expect(detail?.textContent).toBe("Просрочено: 3\nВ Карантине: 2");
});
