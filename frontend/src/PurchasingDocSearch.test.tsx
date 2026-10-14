import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PurchasingDocSearch } from "./PurchasingDocSearch";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const hits = [
  {
    kind: "upd", id: "g-2", doc_kind: "УПД", number: "15", date: "2026-09-20", seller_name: "ООО Ромашка", seller_inn: "7701000019",
    origin: "edo", total: 120000, file_id: null, title: null, snippet: null, matched: "lines",
    lines: [{ seq: 1, name: "Разъём SMA J (RG-316)", article: "", qty: "10", unit: "шт", price: 12000, sum: 120000 }],
  },
  {
    kind: "invoice", id: "inv8", doc_kind: "Счёт", number: "5", date: "2026-09-02", seller_name: null, seller_inn: null, origin: null,
    total: null, file_id: "f8", title: "Счёт Мицар", lines: [], matched: "text",
    snippet: "…1 095-329-110 Устр-во для зачистки коаксиал. кабеля…",
  },
];

function mockServer(calls: string[]) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = decodeURIComponent(String(input));
    calls.push(path);
    if (path.startsWith("/api/purchasing/doc-search")) return json({ hits, found: 2 });
    return json({});
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

it("searches once two characters are typed and shows positions, the PDF and the words around a text hit", async () => {
  const calls: string[] = [];
  mockServer(calls);
  const opened: string[] = [];
  render(<PurchasingDocSearch onOpenUpd={(id) => opened.push(id)} />);
  expect(screen.getByText(/Введите хотя бы два знака/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Что найти в документах"), { target: { value: "х" } });
  await new Promise((resolve) => setTimeout(resolve, 300));
  expect(calls).toEqual([]);
  fireEvent.change(screen.getByLabelText("Что найти в документах"), { target: { value: "sma-j rg316" } });
  const upd = await screen.findByRole("article", { name: "УПД № 15 от 20.09.2026" });
  expect(calls.at(-1)).toBe("/api/purchasing/doc-search?q=sma-j+rg316");
  expect(within(upd).getByRole("table", { name: "Позиции" }).textContent).toContain("Разъём SMA J (RG-316)");
  expect(within(upd).getByText("Цена с НДС")).toBeInTheDocument();
  fireEvent.click(within(upd).getByRole("button", { name: "УПД № 15 от 20.09.2026" }));
  expect(opened).toEqual(["g-2"]);
  const invoice = screen.getByRole("article", { name: "Счёт № 5 от 02.09.2026" });
  expect(within(invoice).getByRole("link", { name: "PDF счёта" }).getAttribute("href")).toBe("/api/purchasing/invoices/inv8/files/f8");
  expect(within(invoice).getByText(/095-329-110 Устр-во/)).toBeInTheDocument();
  expect(within(invoice).getByText("Карточка Kaiten: Счёт Мицар")).toBeInTheDocument();
  expect(screen.getByText("2 док.")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Счета из Kaiten" }));
  await waitFor(() => expect(calls.at(-1)).toBe("/api/purchasing/doc-search?q=sma-j+rg316&kind=invoice"));
});
