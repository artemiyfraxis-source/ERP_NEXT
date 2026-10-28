import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PrimaryDocsPage } from "./PrimaryDocsPage";
import { ProcurementPage } from "./ProcurementPage";
import type { TodayItem, TodaySection } from "./ProcurementToday";

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

function mockServer(sections: TodaySection[], extra: (path: string) => Response | null = () => null) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/procurement/today") return json({ today: "2026-10-21", sections, calm: sections.length === 0 });
    const answer = extra(path);
    if (answer) return answer;
    if (path === "/api/procurement/meta") return json({ units: ["шт"], statuses: [] });
    return json({ lines: [], buyers: [], stages: [], next_cursor: null });
  });
}

const row = (title: string, link: Record<string, string>, days: number | null = null): TodayItem => ({ title, detail: `${title}: подробности`, link, days });

const section = (key: string, title: string, items: TodayItem[], extra: Partial<TodaySection> = {}): TodaySection => ({
  key, title, action: `шаг: ${title}`, tone: "warning", count: items.length, items, ...extra,
});

beforeEach(() => {
  window.history.replaceState({}, "", "/?section=procurement");
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  window.localStorage.clear();
  cleanup();
});

it("says everything is calm when nothing is to do", async () => {
  mockServer([]);
  render(<ProcurementPage />);
  expect(await screen.findByRole("heading", { name: "Всё спокойно" })).toBeInTheDocument();
  expect(screen.getByText("21.10.2026 · дел нет")).toBeInTheDocument();
});

it("shows «Сегодня» first: sections in the order of doing with their tone, action and count; the header counts the work", async () => {
  mockServer([
    section("late", "Просрочены поставки", [row("Кабель", { tab: "orders", order: "PO-1" }, 2)], { tone: "danger" }),
    section("rfq", "Запросить КП", [row("П-100", { tab: "requests", project: "П-100" }, 10)], { tone: "accent", count: 3 }),
  ]);
  render(<ProcurementPage />);
  expect(await screen.findByRole("heading", { level: 1, name: "Сегодня" })).toBeInTheDocument(); // the default page of a buyer
  const cards = await screen.findAllByRole("region");
  expect(cards.map((card) => card.getAttribute("aria-label"))).toEqual(["Просрочены поставки", "Запросить КП"]);
  expect(cards[0]).toHaveClass("today-card", "today-tone-danger");
  expect(within(cards[0]).getByText("шаг: Просрочены поставки")).toBeInTheDocument();
  expect(within(cards[0]).getByText("2 дн")).toBeInTheDocument();
  expect(screen.getByText("21.10.2026 · дел: 4")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Счета и поставки" }));
  expect(Object.fromEntries(new URL(window.location.href).searchParams)).toEqual({ section: "procurement", tab: "orders" });
});

it("shows eight items of a section, then «и ещё N»", async () => {
  const items = Array.from({ length: 8 }, (_, index) => row(`Позиция ${index + 1}`, { tab: "orders" }, 20 - index));
  mockServer([section("invoice", "Ждём счёт", items, { count: 13 })]);
  render(<ProcurementPage />);
  expect(await screen.findByText("Позиция 8")).toBeInTheDocument();
  expect(screen.getByText("и ещё 5")).toBeInTheDocument();
});

it("opens a project card in place with its positions; a position and «открыть все» lead to the place", async () => {
  const card: TodayItem = {
    title: "ВП-001", detail: "3 позиции · перезапрос: 1", link: { tab: "requests", project: "ВП-001" }, days: 10,
    project: { key: "ВП-001", kind: "project", value: "ВП-001" }, lines_count: 3,
    lines: [
      row("Разъём 1", { tab: "requests", project: "ВП-001", line: "l-1" }, 10),
      row("Разъём 2", { tab: "requests", project: "ВП-001", line: "l-2" }, 4),
    ],
  };
  mockServer([section("rfq", "Запросить КП", [card], { positions: 3 })]);
  render(<ProcurementPage />);
  const group = await screen.findByRole("button", { name: /ВП-001/ });
  expect(group).toHaveAttribute("aria-expanded", "false");
  expect(screen.getByText("3 поз.")).toBeInTheDocument();
  expect(screen.queryByText("Разъём 1")).not.toBeInTheDocument();
  fireEvent.click(group);
  const positions = screen.getByRole("list", { name: "Позиции: ВП-001" });
  expect(within(positions).getByRole("link", { name: /Разъём 1/ })).toHaveAttribute(
    "href", "?section=procurement&tab=requests&project=%D0%92%D0%9F-001&line=l-1");
  expect(within(positions).getByRole("link", { name: "и ещё 1 — открыть все →" })).toBeInTheDocument();

  const moved = vi.fn();
  window.addEventListener("popstate", moved);
  fireEvent.click(within(positions).getByRole("link", { name: /Разъём 2/ }));
  window.removeEventListener("popstate", moved);
  expect(moved).toHaveBeenCalled(); // in-app: the address changes and the shell follows, no reload
  const url = new URL(window.location.href);
  expect(Object.fromEntries(url.searchParams)).toEqual({ section: "procurement", tab: "requests", project: "ВП-001", line: "l-2" });
});

it("«Новая заявка» opens the request form in the app", async () => {
  mockServer([]);
  render(<ProcurementPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Новая заявка" }));
  expect(new URL(window.location.href).searchParams.get("section")).toBe("newrequest");
});

it("refreshes itself every minute", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const fetch = mockServer([]);
  render(<ProcurementPage />);
  await screen.findByRole("heading", { name: "Всё спокойно" });
  const calls = () => fetch.mock.calls.filter(([input]) => String(input) === "/api/procurement/today").length;
  expect(calls()).toBe(1);
  await act(async () => { vi.advanceTimersByTime(60_000); });
  await waitFor(() => expect(calls()).toBe(2));
});

it("a document row leads to «Первичная документация»: by the link's own section, and an old tab-only link too", async () => {
  mockServer([
    section("kaiten", "Оплатить счета Kaiten", [row("Счёт 12", { section: "primarydocs", tab: "invoices", status: "to_pay" }, 2)]),
    section("upd", "Проверить УПД", [row("3 документа", { tab: "upd", status: "needs_review" })]),
  ]);
  render(<ProcurementPage />);
  const invoice = await screen.findByRole("link", { name: /Счёт 12/ });
  expect(invoice).toHaveAttribute("href", "?section=primarydocs&tab=invoices&status=to_pay");
  // An old server's link names only the tab: MOVED_TABS sends it to the new section as well.
  expect(screen.getByRole("link", { name: /3 документа/ })).toHaveAttribute("href", "?section=primarydocs&tab=upd&status=needs_review");
  fireEvent.click(invoice);
  expect(Object.fromEntries(new URL(window.location.href).searchParams)).toEqual({ section: "primarydocs", tab: "invoices", status: "to_pay" });
});

it("the documents tabs open on the status a «Сегодня» link names", async () => {
  window.history.replaceState({}, "", "/?section=primarydocs&tab=invoices&status=to_pay");
  const fetch = mockServer([], (path) =>
    path.startsWith("/api/purchasing/invoices") ? json({ items: [], total: 0, page: 1, page_size: 50, counts: {} }) : null);
  render(<PrimaryDocsPage />);
  await waitFor(() => expect(fetch.mock.calls.some(([input]) => String(input).startsWith("/api/purchasing/invoices") && String(input).includes("status=to_pay"))).toBe(true));
  cleanup();
  vi.restoreAllMocks();

  window.history.replaceState({}, "", "/?section=primarydocs&tab=upd&status=needs_review");
  const second = mockServer([], (path) =>
    path.startsWith("/api/purchasing/upd") ? json({ items: [], total: 0, page: 1, page_size: 50, counts: {}, review: 0, origins: {}, buyers: [] }) : null);
  render(<PrimaryDocsPage />);
  await waitFor(() => expect(second.mock.calls.some(([input]) => String(input).startsWith("/api/purchasing/upd?") && String(input).includes("status=needs_review"))).toBe(true));
});
