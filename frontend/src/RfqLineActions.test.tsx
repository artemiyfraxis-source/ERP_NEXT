import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { linesByType, quantityInput, RfqLineToolbar, RfqSelectionBar } from "./RfqLineActions";
import type { RfqLine, RfqMatrix } from "./purchasingRfqApi";
import { resetStageOptions } from "./StageOptions";

const line = (id: string, name: string, over: Partial<RfqLine> = {}): RfqLine => ({
  id, procurement_line_id: `p-${id}`, legacy_id: `SM-${id}`, status: "В работе", version: 4, editable: true, name,
  article: null, quantity: "10", unit: "шт", need_by: null, budget_unit_gross: null, delta_pct: null,
  gate: { priced: 0, required: 3, has_non_key: false, require_non_key: true, passes: false },
  cheapest_quote_line_id: null, baseline_quote_line_id: null, decision: null, cells: {}, ...over,
});
const matrix = (lines: RfqLine[]): RfqMatrix => ({
  rfq: { id: "r1", number: "КП-2610-001", title: null, created_at: "2026-10-01T10:00:00", closed_at: null, mine: true, version: 7 },
  settings: { gate_min_quotes: 3, gate_require_non_key: true, default_vat_rate: "22" }, suppliers: [], lines, quotes: [],
  removed: [{ id: "x", procurement_line_id: "p-x", legacy_id: "SM-x", name: "Убранная", article: null, quantity: "1", unit: "шт",
    removed_at: "2026-10-02T10:00:00", reason: "requote", reason_label: "перезапрос", note: "Альфа — отказ" }],
});

function server(handler: (path: string, body: any) => Response) {
  const posts: { path: string; body: any }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stage-options") {
      return json({ available: true, options: [{ key: "procurement.requests_rb", block: "b", title: "t", before: "", after: "", enabled: true }] });
    }
    const body = JSON.parse(String(init?.body ?? "{}"));
    posts.push({ path, body });
    return handler(path, body);
  });
  return posts;
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
  resetStageOptions();
});

it("lines go by type, a chosen one never; quantities are read the Russian way", () => {
  const lines = [line("a", "Маркер 2", { item_type: "Маркировка" }), line("b", "Маркер 3", { item_type: "Маркировка" }),
    line("c", "Трубка"), line("d", "Провод", { item_type: "Маркировка", decision: { id: "d" } as RfqLine["decision"] })];
  expect(linesByType(lines)).toEqual([["Маркировка", ["a", "b"]], ["Тип не указан", ["c"]]]);
  expect(quantityInput("1 500,5")).toBe("1500.5");
  expect(quantityInput("0")).toBe("");
  expect(quantityInput("много")).toBe("");
});

it("«Выбрать по типу…» ticks the whole type; the removed lines are counted", () => {
  const onPick = vi.fn();
  const m = matrix([line("a", "Маркер 2", { item_type: "Маркировка" }), line("c", "Трубка", { cancelled: true })]);
  render(<RfqLineToolbar matrix={m} picked={new Set()} onPick={onPick} showRemoved={false} onShowRemoved={() => undefined} />);
  fireEvent.change(screen.getByLabelText("Выбрать строки по типу"), { target: { value: "Маркировка" } });
  expect(onPick).toHaveBeenCalledWith(["a"], true);
  expect(screen.getByRole("button", { name: "показать отменённые и убранные (2)" })).toBeInTheDocument();
});

it("«Перезапрос» with prices asks once and sends drop_prices; the request's version goes along", async () => {
  const posts = server((_, body) => (body.drop_prices ? json({ withdrawn: 1 })
    : json({ detail: { code: "has_prices", message: "У строк есть цены поставщиков: Маркер 2. Убрать вместе с ценами?", lines: ["Маркер 2"] } }, 409)));
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  const onDone = vi.fn();
  render(<RfqSelectionBar matrix={matrix([line("a", "Маркер 2")])} picked={new Set(["a"])} onClear={() => undefined} onDone={onDone} onError={() => undefined} />);
  fireEvent.click(screen.getByRole("button", { name: "Перезапрос" }));
  await waitFor(() => expect(onDone).toHaveBeenCalled());
  expect(confirm.mock.calls[0][0]).toContain("Цены сохранятся текстом в истории строки.");
  expect(posts.map((post) => post.body)).toEqual([
    { rfq_line_ids: ["a"], to: "requote", drop_prices: false, rfq_version: 7 },
    { rfq_line_ids: ["a"], to: "requote", drop_prices: true, rfq_version: 7 },
  ]);
  expect(posts[0].path).toBe("/api/purchasing/rfqs/r1/lines/withdraw");
});

it("«Отменить» calls the requests slice's cancel-many with the versions and says what was skipped", async () => {
  const posts = server(() => json({ cancelled: ["p-a"], skipped: [{ id: "p-b", reason: "уже оплачена" }] }));
  vi.spyOn(window, "prompt").mockReturnValue("маркировку не закупаем");
  const onDone = vi.fn();
  render(<RfqSelectionBar matrix={matrix([line("a", "Маркер 2"), line("b", "Маркер 3")])} picked={new Set(["a", "b"])} onClear={() => undefined} onDone={onDone} onError={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: "Отменить" }));
  await waitFor(() => expect(onDone).toHaveBeenCalledWith("Отменено строк: 1; не отменены: SM-b — уже оплачена"));
  expect(posts[0]).toEqual({
    path: "/api/procurement/cancel-many",
    body: { lines: [{ id: "p-a", version: 4 }, { id: "p-b", version: 4 }], reason: "маркировку не закупаем" },
  });
});

it("«Разделить» one line sends the quantity to split-qty with the line's version", async () => {
  const posts = server(() => json({ original: {}, created: {} }));
  vi.spyOn(window, "prompt").mockReturnValue("4");
  const onDone = vi.fn();
  render(<RfqSelectionBar matrix={matrix([line("a", "Кабель")])} picked={new Set(["a"])} onClear={() => undefined} onDone={onDone} onError={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: "Разделить" }));
  await waitFor(() => expect(onDone).toHaveBeenCalled());
  expect(posts[0]).toEqual({ path: "/api/procurement/p-a/split-qty", body: { version: 4, quantity: "4" } });
});
