import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RfqInvoiceRequests } from "./RfqInvoiceRequests";

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
  window.history.replaceState({}, "", "/");
});

const letter = {
  supplier_id: "s1", supplier_name: "ООО Продиэл", email: "sales@prodiel.ru", quote_number: "сайт Продиэлком",
  quote_date: "2026-10-14", delivery_cost: null, total: "14010",
  lines: [{ rfq_line_id: "l1", legacy_id: "SM-1", name: "Силикон 28AWG белый", article: "", qty: "1000", unit: "м", packs: "", price: "14.01", sum: "14010", vat_included: true }],
  subject: "Запрос счёта — предложение от 14.10.2026 (сайт Продиэлком), 1 поз.",
  body: "Добрый день!\n\n1. Силикон 28AWG белый — 1 000 м × 14,01 ₽ = 14 010,00 ₽", cart: "Силикон 28AWG белый\t1 000 м",
};

it("shows one letter per supplier with mail and copy, and points the «Готов к запросу» lines to ERP's own request", async () => {
  const asked: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    asked.push(String(input));
    return new Response(JSON.stringify({ requests: [letter], ready_for_request: 2 }), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
  render(<RfqInvoiceRequests rfqId="r1" onClose={() => undefined} />);
  const block = await screen.findByRole("region", { name: "Запрос счёта: ООО Продиэл" });
  expect(asked).toEqual(["/api/purchasing/rfqs/r1/invoice-requests"]);
  expect(within(block).getByText(/1\. Силикон 28AWG белый — 1 000 м × 14,01 ₽/)).toBeInTheDocument();
  expect(within(block).getByRole("link", { name: "Открыть в почте" }).getAttribute("href")).toMatch(/^mailto:sales@prodiel\.ru\?subject=/);
  fireEvent.click(within(block).getByRole("button", { name: "Список для корзины на сайте" }));
  expect(await within(block).findByRole("button", { name: "Скопировано ✓" })).toBeInTheDocument();
  expect(navigator.clipboard.writeText).toHaveBeenCalledWith("Силикон 28AWG белый\t1 000 м");
  expect(screen.getByText(/Строк «Готов к запросу»: 2/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Очереди → «Сформировать запрос»" }));
  expect(window.location.search).toBe("?section=procurement&tab=queues");
});
