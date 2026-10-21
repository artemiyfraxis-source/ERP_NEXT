import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { addDays, answerBy, cartList, letterBody, letterGroups, letterSubject, qtyList, RfqLetters } from "./RfqLetters";
import type { RfqLine, RfqMatrix, RfqSupplier } from "./purchasingRfqApi";

const line = (id: string, name: string, over: Partial<RfqLine> = {}): RfqLine => ({
  id, procurement_line_id: `p-${id}`, legacy_id: `SM-${id}`, status: "В работе", version: 1, editable: true, name,
  article: null, quantity: "2", unit: "шт", need_by: null, budget_unit_gross: "110", delta_pct: null,
  gate: { priced: 0, required: 3, has_non_key: false, require_non_key: true, passes: false },
  cheapest_quote_line_id: null, baseline_quote_line_id: null, decision: null, cells: {}, ...over,
});

const supplier = (id: string, name: string, over: Partial<RfqSupplier> = {}): RfqSupplier => ({
  supplier_id: id, name, is_key: false, reason: "manual", sent_at: null, line_ids: null, declined_at: null,
  decline_note: null, answered: false, delivery_cost: null, min_order_sum: null, basket_total: null, basket_lines: 0,
  chosen_total: null, min_order_warning: null, email: null, ...over,
});

const matrix = (suppliers: RfqSupplier[], lines: RfqLine[]): RfqMatrix => ({
  rfq: { id: "r1", number: "КП-2610-001", title: null, created_at: "2026-10-01T10:00:00+03:00", closed_at: null, mine: true },
  settings: { gate_min_quotes: 3, gate_require_non_key: true, default_vat_rate: "22", quote_wait_days: 3 },
  suppliers, lines, quotes: [],
});

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

it("the letter lists the positions with the part number, the maker and the date, never the planned price", () => {
  const lines = [
    line("a", "Разъём", { article: "SHR-02V", manufacturer: "JST", need_by: "2026-10-20", quantity: "1500" }),
    line("b", "Кабель RG-316", { article: "RG-316", quantity: "87.5", unit: "м" }),
  ];
  const body = letterBody(lines, "2026-10-04", { b: 100 }, [500, 1000]);
  expect(body).toContain("1. Разъём (SHR-02V), JST — 1 500 шт — нужно к 20.10.2026");
  expect(body).toContain("2. Кабель RG-316 — 100 м"); // the article is in the name already; the letter asks for more
  expect(body).toContain("Также просим указать цену при заказе от 500 и от 1 000 (в тех же единицах) по каждой позиции.");
  expect(body).toContain("Ответ просим направить до 04.10.2026.");
  expect(body).not.toContain("110");
  expect(letterSubject(matrix([], lines), lines)).toBe("Запрос КП № КП-2610-001 от 01.10.2026 — 2 позиции");
  expect(cartList(lines)).toBe("SHR-02V\t1 500 шт\nRG-316\t87,5 м");
  expect(qtyList("500; 1 000, 2000 мусор")).toEqual([500, 1, 2000]);
});

it("answer by: the first sending + the wait from the settings, else today", () => {
  expect(addDays("2026-10-30", 3)).toBe("2026-11-02");
  expect(answerBy([supplier("s1", "А", { sent_at: "2026-10-05T09:00:00" }), supplier("s2", "Б", { sent_at: "2026-10-02T09:00:00" })], 3)).toBe("2026-10-05");
  expect(answerBy([supplier("s1", "А")], 2, "2026-10-10")).toBe("2026-10-12");
});

it("each supplier gets only its lines; the same lines share a letter; one who refused gets none", () => {
  const lines = [line("a", "Разъём"), line("b", "Клемма"), line("c", "Отменённая", { cancelled: true })];
  const groups = letterGroups(matrix([
    supplier("s1", "Альфа"), supplier("s2", "Бета", { line_ids: ["a"] }), supplier("s3", "Гамма"),
    supplier("s4", "Дельта", { declined_at: "2026-10-02T10:00:00" }),
  ], lines));
  expect(groups.map((group) => [group.suppliers.map((s) => s.name), group.lines.map((l) => l.name)])).toEqual([
    [["Альфа", "Гамма"], ["Разъём", "Клемма"]],
    [["Бета"], ["Разъём"]],
  ]);
});

it("«Открыть в почте» carries the letter and marks the supplier sent; copying a one-recipient letter marks him too", async () => {
  const posts: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (init?.method === "POST") posts.push(String(input));
    return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
  });
  Object.assign(navigator, { clipboard: { writeText: vi.fn().mockResolvedValue(undefined) } });
  const onSent = vi.fn();
  const m = matrix([
    supplier("s1", "Альфа", { email: "sales@alfa.ru", contact_person: "Иван" }),
    supplier("s2", "Бета", { line_ids: ["a"], sent_at: "2026-10-01T10:00:00" }),
  ], [line("a", "Разъём"), line("b", "Клемма")]);
  render(<RfqLetters matrix={m} editable onClose={() => undefined} onSent={onSent} />);
  const alfa = screen.getByRole("region", { name: "Письмо: Альфа" });
  const mail = within(alfa).getByRole("link", { name: "Открыть в почте" });
  expect(mail.getAttribute("href")).toMatch(/^mailto:sales@alfa\.ru\?subject=/);
  expect(decodeURIComponent(mail.getAttribute("href") ?? "")).toContain("2. Клемма — 2 шт");
  mail.addEventListener("click", (event) => event.preventDefault());
  fireEvent.click(mail);
  await waitFor(() => expect(posts).toEqual(["/api/purchasing/rfqs/r1/suppliers/s1/sent"]));
  expect(onSent).toHaveBeenCalledWith("Отмечено отправленным: Альфа");

  const beta = screen.getByRole("region", { name: "Письмо: Бета" });
  expect(within(beta).getByText(/нет в карточке поставщика/)).toBeInTheDocument();
  expect(within(beta).queryByText(/Клемма/)).not.toBeInTheDocument();
  fireEvent.click(within(beta).getByRole("button", { name: "Скопировать текст" }));
  expect(await within(beta).findByRole("button", { name: "Скопировано ✓" })).toBeInTheDocument();
  expect(posts).toHaveLength(1); // Бета was sent already
});

it("on view only nothing is marked", async () => {
  const fetch = vi.spyOn(globalThis, "fetch");
  render(<RfqLetters matrix={matrix([supplier("s1", "Альфа", { email: "a@a.ru" })], [line("a", "Разъём")])} editable={false} onClose={() => undefined} onSent={() => undefined} />);
  const mail = screen.getByRole("link", { name: "Открыть в почте" });
  mail.addEventListener("click", (event) => event.preventDefault());
  fireEvent.click(mail);
  expect(screen.queryByRole("button", { name: "отметить" })).not.toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});
