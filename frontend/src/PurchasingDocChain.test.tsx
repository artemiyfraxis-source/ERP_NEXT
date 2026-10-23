import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PrimaryDocsPage } from "./PrimaryDocsPage";
import { PurchasingDocChain } from "./PurchasingDocChain";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const invoice = {
  id: "inv1", title: "Счет № А-1", number: "А-1", date: "2026-09-01", seller_name: 'ООО "Ромашка"', seller_inn: "7701000019",
  supplier_id: null, supplier_name: null, total: 100000, status: "paid", payments: [{ number: "801", date: "2026-09-01", sum: 100000 }],
  invoice_file_id: "f1",
};
const upd = (over: Record<string, unknown> = {}) => ({
  id: "g-1", file_name: "g-1.pdf", number: "УТ-1714", date: "2026-09-05", kind: "УПД", origin: "edo", status: "parsed",
  seller_name: 'ООО "Ромашка"', seller_inn: "7701000019", supplier_id: null, total_gross: 40000, services_only: false,
  method: "basis_date", basis: "Счет № А-1 от 01.09.2026", payment_doc: "", buyer_kind: "ours", buyer_org_inn: null,
  receiving: { lines: 2, grns: ["FRX.26.0000101", "FRX.26.0000102"], statuses: [{ status: "Принято", count: 2 }] }, ...over,
});
const chain = (over: Record<string, unknown> = {}) => ({
  key: "invoice:inv1", invoice, upds: [upd()], suggestions: [upd({ id: "g-2", number: "77", method: "amount", receiving: null })],
  closed_gross: 40000, state: "partial", problem: true,
  checks: [{ code: "partial", level: "warning", text: "Закрыто 400,00 ₽ из 1 000,00 ₽ — ждём остальные документы" }], ...over,
});
const listing = (chains: unknown[], total = chains.length) => ({
  summary: { by_state: { closed: 3, partial: 1, over: 0, waiting: 0, missing: 0, unpaid: 0, return: 0, orphan: 0 }, problems: 1, by_closing: { edo: 1, paper: 0, none: 2 } },
  chains, total, page: 1, page_size: 150, receiving_available: true,
});

function mockServer(calls: { method: string; path: string; body?: string }[], pages: Record<string, unknown> = {}) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = decodeURIComponent(String(input));
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ method, path, body: init?.body as string | undefined });
    if (path.startsWith("/api/purchasing/upd/")) return json({ linked: [], candidates: [], rejected: [], basis_refs: [], payment_refs: [], checks: [], receiving: null });
    if (path.startsWith("/api/purchasing/doc-chain")) {
      const page = new URLSearchParams(path.split("?")[1] ?? "").get("page") ?? "1";
      return json(pages[page] ?? listing([chain()]));
    }
    return json({});
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

it("lists chains with payments, closing documents, receiving and what disagrees; problems first by default", async () => {
  const calls: { method: string; path: string }[] = [];
  mockServer(calls);
  render(<PurchasingDocChain />);
  const list = await screen.findByRole("list", { name: "Цепочки документов" });
  const row = within(list).getAllByRole("listitem")[0];
  expect(row.textContent).toContain("А-1");
  expect(row.textContent).toContain("№ 801 от 01.09.2026");
  expect(row.textContent).toContain("УПД № УТ-1714 от 05.09.2026");
  expect(row.textContent).toContain("ЭДО");
  expect(row.textContent).toMatch(/2\sстроки/);
  expect(row.textContent).toContain("Закрыт частично");
  expect(row.textContent).toContain("ждём остальные документы");
  expect(calls[0].path).toBe("/api/purchasing/doc-chain?view=problems");
  expect(screen.getByRole("group", { name: "Что показать" }).textContent).toContain("Требуют внимания 1");
  expect(screen.getByLabelText("Состояния цепочек").textContent).toContain("Закрыт: 3");
});

it("opens a chain: the invoice PDF in a new tab, receiving of each UPD, «Связать» confirms the suggestion", async () => {
  const calls: { method: string; path: string; body?: string }[] = [];
  mockServer(calls);
  const opened: string[] = [];
  render(<PurchasingDocChain onOpenUpd={(id) => opened.push(id)} />);
  fireEvent.click(await screen.findByText("Закрыт частично"));
  const pdf = await screen.findByRole("link", { name: "Открыть PDF счёта" });
  expect(pdf.getAttribute("href")).toBe("/api/purchasing/invoices/inv1/files/f1");
  expect(pdf.getAttribute("target")).toBe("_blank");
  expect(screen.getByText(/Приёмка: 2\sстроки · ГРН FRX\.26\.0000101, FRX\.26\.0000102/)).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole("button", { name: "УПД № УТ-1714 от 05.09.2026" })[0]);
  expect(opened).toEqual(["g-1"]);
  fireEvent.click(screen.getByRole("button", { name: "Связать" }));
  await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
  const put = calls.find((c) => c.method === "PUT")!;
  expect(put.path).toBe("/api/purchasing/upd/g-2/invoices/inv1");
  expect(JSON.parse(put.body!)).toEqual({ decision: "confirmed" });
});

it("read-only: no «Связать»", async () => {
  mockServer([]);
  render(<PurchasingDocChain readOnly />);
  fireEvent.click(await screen.findByText("Закрыт частично"));
  await screen.findByText("Возможно, закрывает этот счёт");
  expect(screen.queryByRole("button", { name: "Связать" })).toBeNull();
});

it("filters go to the server; «Показать ещё» appends the next page", async () => {
  const calls: { method: string; path: string }[] = [];
  const second = chain({ key: "upd:g-9", invoice: null, upds: [upd({ id: "g-9", seller_name: "ООО Лютик" })], suggestions: [], state: "orphan", checks: [] });
  mockServer(calls, { "1": listing([chain()], 2), "2": listing([second], 2) });
  render(<PurchasingDocChain />);
  await screen.findByRole("list", { name: "Цепочки документов" });
  fireEvent.click(screen.getByRole("button", { name: "Показать ещё (1)" }));
  await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(2));
  expect(calls.at(-1)!.path).toBe("/api/purchasing/doc-chain?view=problems&page=2");
  expect(screen.getByText("счёт не найден")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "ЭДО 1" }));
  await waitFor(() => expect(calls.at(-1)!.path).toBe("/api/purchasing/doc-chain?view=problems&closing=edo"));
  fireEvent.change(screen.getByLabelText("Покупатель"), { target: { value: "other" } });
  await waitFor(() => expect(calls.at(-1)!.path).toBe("/api/purchasing/doc-chain?view=problems&closing=edo&buyer=other"));
  fireEvent.click(screen.getByRole("button", { name: "Все" }));
  await waitFor(() => expect(calls.at(-1)!.path).toBe("/api/purchasing/doc-chain?view=all&closing=edo&buyer=other"));
});

function procurementServer() {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = decodeURIComponent(String(input));
    if (path === "/api/procurement/today") return json({ today: "2026-10-21", sections: [], calm: true });
    if (path === "/api/procurement/meta") return json({ units: ["шт"], statuses: [] });
    if (path.startsWith("/api/purchasing/invoices")) return json({ items: [], total: 0, page: 1, page_size: 100, counts: { to_pay: 0, paid: 0, return: 0, other: 0 }, sum: 0 });
    // «Первичная документация» opens on «УПД».
    if (path.startsWith("/api/purchasing/upd")) return json({ items: [], total: 0, page: 1, page_size: 50, counts: {}, review: 0, origins: {}, buyers: [] });
    return json({ lines: [], buyers: [], stages: [], next_cursor: null });
  });
}

it("«Первичная документация» shows «Счета Kaiten», «Цепочка документов», «Поиск по документам»", async () => {
  procurementServer();
  render(<PrimaryDocsPage />);
  const tabs = await screen.findByRole("tablist", { name: "Первичная документация" });
  await waitFor(() => expect(within(tabs).getByRole("tab", { name: "Счета Kaiten" })).toBeInTheDocument());
  expect(within(tabs).getByRole("tab", { name: "Цепочка документов" })).toBeInTheDocument();
  expect(within(tabs).getByRole("tab", { name: "Поиск по документам" })).toBeInTheDocument();
  fireEvent.click(within(tabs).getByRole("tab", { name: "Счета Kaiten" }));
  expect(await screen.findByText("Счетов ещё нет: их загружает перенос из Kaiten.")).toBeInTheDocument();
});
