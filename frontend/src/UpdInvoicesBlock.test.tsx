import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { UpdInvoicesBlock } from "./UpdInvoicesBlock";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const invoice = (over: Record<string, unknown> = {}) => ({
  id: "inv1", title: "Счет № Д-1", number: "Д-1", date: "2026-09-01", seller_name: "ООО Ромашка", seller_inn: "7701000019",
  supplier_id: null, supplier_name: null, total: 100000, status: "paid", payments: [{ number: "830", date: "2026-09-01", sum: 100000 }],
  invoice_file_id: "f1", method: "basis_date", decision: null, ...over,
});
const state = (over: Record<string, unknown> = {}) => ({
  linked: [invoice()],
  candidates: [invoice({ id: "inv2", number: "Д-2", total: 55500, payments: [], status: "to_pay", method: "amount", suggested: true, invoice_file_id: null })],
  rejected: [invoice({ id: "inv3", number: "Д-3", method: "manual", decision: "rejected" })],
  basis_refs: [{ kind: "invoice", number: "Д-1", date: "2026-09-01", raw: "Счет № Д-1 от 01.09.2026" }],
  payment_refs: [{ number: "830", date: "2026-09-01" }],
  checks: [{ code: "payment_not_in_invoice", level: "warning", text: "ссылается на чужую платёжку" }],
  receiving: { lines: 1, grns: ["FRX.26.0000101"], statuses: [{ status: "Принято", count: 1 }], items: [] },
  ...over,
});

function mockServer(calls: { method: string; path: string; body?: string }[]) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = decodeURIComponent(String(input));
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ method, path, body: init?.body as string | undefined });
    if (method === "PUT") {
      const { decision } = JSON.parse(String(init?.body));
      return json(state(decision === "confirmed" ? { linked: [invoice({ decision: "confirmed", method: "manual" })] } : {}));
    }
    if (path === "/api/purchasing/upd/g-card/invoices") return json(state());
    return json({});
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

it("shows linked invoices with how they were found, the references, checks, candidates, rejected and receiving", async () => {
  mockServer([]);
  render(<UpdInvoicesBlock documentId="g-card" />);
  const linked = await screen.findByRole("list", { name: "Связанные счета" });
  expect(linked.textContent).toContain("счёт № Д-1");
  expect(linked.textContent).toContain("оплачен № 830 от 01.09.2026");
  expect(linked.textContent).toContain("по основанию и дате");
  expect(within(linked).getByRole("link", { name: "PDF" }).getAttribute("href")).toBe("/api/purchasing/invoices/inv1/files/f1");
  expect(screen.getByText(/В основании: Счет № Д-1 от 01\.09\.2026/)).toBeInTheDocument();
  expect(screen.getByText(/Платёжки в документе: № 830 от 01\.09\.2026/)).toBeInTheDocument();
  expect(screen.getByText("ссылается на чужую платёжку")).toBeInTheDocument();
  const candidates = screen.getByRole("list", { name: "Возможные счета" });
  expect(candidates.textContent).toContain("совпала сумма");
  expect(screen.getByLabelText("Отклонённые счета").textContent).toContain("Д-3");
  expect(screen.getByText(/Приёмка: 1\sстрока · ГРН FRX\.26\.0000101/)).toBeInTheDocument();
});

it("confirm, reject, link a candidate and undo send the buyer's word", async () => {
  const calls: { method: string; path: string; body?: string }[] = [];
  mockServer(calls);
  render(<UpdInvoicesBlock documentId="g-card" />);
  const puts = () => calls.filter((c) => c.method === "PUT").map((c) => [c.path, JSON.parse(c.body!).decision]);
  fireEvent.click(await screen.findByRole("button", { name: "Не тот счёт" }));
  await waitFor(() => expect(puts()).toEqual([["/api/purchasing/upd/g-card/invoices/inv1", "rejected"]]));
  fireEvent.click(await screen.findByRole("button", { name: "Связать" }));
  await waitFor(() => expect(puts().at(-1)).toEqual(["/api/purchasing/upd/g-card/invoices/inv2", "confirmed"]));
  // Confirmed by the buyer: the link is undone, not rejected.
  fireEvent.click(await screen.findByRole("button", { name: "Отменить связь" }));
  await waitFor(() => expect(puts().at(-1)).toEqual(["/api/purchasing/upd/g-card/invoices/inv1", null]));
  fireEvent.click(await screen.findByRole("button", { name: "Вернуть" }));
  await waitFor(() => expect(puts().at(-1)).toEqual(["/api/purchasing/upd/g-card/invoices/inv3", null]));
});

it("read-only shows the invoices without the buyer's buttons", async () => {
  mockServer([]);
  render(<UpdInvoicesBlock documentId="g-card" readOnly />);
  await screen.findByRole("list", { name: "Связанные счета" });
  expect(screen.queryByRole("button", { name: /Подтвердить|Не тот счёт|Связать|Вернуть/ })).toBeNull();
  expect(screen.queryByRole("list", { name: "Возможные счета" })).toBeNull();
});

it("a failed load says so in words", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ detail: "Опция выключена" }, 404));
  render(<UpdInvoicesBlock documentId="g-card" />);
  expect(await screen.findByRole("alert")).toBeInTheDocument();
});
