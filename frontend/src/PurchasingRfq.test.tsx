import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PurchasingRfq } from "./PurchasingRfq";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const cell = (over: Record<string, unknown>) => ({
  quote_line_id: "ql", quote_id: "q", received_on: "2026-10-01", valid_until: null, unit_price: "100",
  vat_included: true, vat_rate: "22", gross_unit: "100", cover_qty: "2", total: "200", moq: null, pack: null, tiers: [],
  lead_days: 5, in_stock: false, analog: false, analog_note: null, declined: false, expired: false, cheapest: false,
  baseline: false, decided: false, requires: [], ...over,
});

const supplier = (id: string, name: string, isKey: boolean) => ({
  supplier_id: id, name, is_key: isKey, reason: isKey ? "key" : "manual", sent_at: "2026-10-01T10:00:00", declined_at: null,
  decline_note: null, answered: true, delivery_cost: null, min_order_sum: null, basket_total: "200", basket_lines: 1,
  chosen_total: null, min_order_warning: null,
});

const matrix = {
  rfq: { id: "r1", number: "КП-2610-001", title: "Разъёмы", created_at: "2026-10-01T10:00:00", closed_at: null, mine: true },
  settings: { gate_min_quotes: 3, gate_require_non_key: true, default_vat_rate: "22" },
  suppliers: [supplier("s1", "ООО Альфа", true), supplier("s2", "ООО Бета", false)],
  lines: [{
    id: "l1", procurement_line_id: "p1", legacy_id: "SM-1", status: "В работе", version: 3, editable: true,
    name: "Разъём", article: "SHR-02", quantity: "2", unit: "шт", need_by: "2026-10-20", budget_unit_gross: "110",
    delta_pct: "-9.1", gate: { priced: 2, required: 3, has_non_key: true, require_non_key: true, passes: false },
    cheapest_quote_line_id: "ql1", baseline_quote_line_id: "ql1", decision: null,
    cells: {
      s1: cell({ quote_line_id: "ql1", cheapest: true, baseline: true, requires: ["gate_override_reason"] }),
      s2: cell({ quote_line_id: "ql2", gross_unit: "120", total: "240", requires: ["gate_override_reason", "not_cheapest_reason"] }),
    },
  }],
  quotes: [],
};

function mockServer(posts: { path: string; body: unknown }[] = []) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") {
      posts.push({ path, body: JSON.parse(String(init.body)) });
      return json({ id: "d1" }, 201);
    }
    if (path === "/api/purchasing/rfqs?scope=mine") {
      return json({ rfqs: [{ id: "r1", number: "КП-2610-001", title: "Разъёмы", created_at: "2026-10-01T10:00:00", closed_at: null, mine: true, lines: 1, suppliers: 2, sent: 2, quotes: 2, decided: 0 }] });
    }
    if (path === "/api/purchasing/rfqs/directory") return json({ suppliers: [] });
    if (path === "/api/purchasing/rfqs/r1") return json(matrix);
    return json({}, 404);
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  cleanup(); // unmount before the reset: a live tree would refetch the options without the mock
});

it("renders the matrix with the cheapest offer marked and the gate chip", async () => {
  mockServer();
  render(<PurchasingRfq />);
  const cheap = await screen.findByRole("button", { name: "Разъём — ООО Альфа" });
  expect(within(cheap).getByText("мин")).toBeInTheDocument();
  const dear = screen.getByRole("button", { name: "Разъём — ООО Бета" });
  expect(within(dear).queryByText("мин")).not.toBeInTheDocument();
  expect(screen.getByText("КП 2/3")).toBeInTheDocument();
  expect(screen.getByText("ООО Альфа ★")).toBeInTheDocument();
  expect(screen.getByText(/Δ −?-?9,1%/)).toBeInTheDocument();
});

it("asks only the reasons the choice needs and sends them with the decision", async () => {
  const posts: { path: string; body: unknown }[] = [];
  mockServer(posts);
  render(<PurchasingRfq />);
  fireEvent.click(await screen.findByRole("button", { name: "Разъём — ООО Бета" }));
  const dialog = await screen.findByRole("dialog", { name: "Разъём: выбор поставщика" });
  expect(within(dialog).getByLabelText("Гейт конкуренции не пройден — причина обхода")).toBeInTheDocument();
  expect(within(dialog).getByLabelText("Не самое дешёвое КП — причина")).toBeInTheDocument();
  expect(within(dialog).queryByLabelText("Аналог — с кем согласовано")).not.toBeInTheDocument();
  expect(within(dialog).getByText("Укажите причину обхода правила КП")).toBeInTheDocument();
  const choose = within(dialog).getByRole("button", { name: /Выбрать «ООО Бета»/ });
  expect(choose).toBeDisabled();
  fireEvent.change(within(dialog).getByLabelText("Гейт конкуренции не пройден — причина обхода"), { target: { value: "other" } });
  expect(choose).toBeDisabled(); // «Другое» needs the buyer's words
  fireEvent.change(within(dialog).getByLabelText("Гейт конкуренции не пройден — причина обхода: своими словами"), { target: { value: "Срочно для сборки" } });
  fireEvent.change(within(dialog).getByLabelText("Не самое дешёвое КП — причина"), { target: { value: "lead_time" } });
  expect(choose).toBeEnabled();
  fireEvent.click(choose);
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].path).toBe("/api/purchasing/rfqs/r1/decisions");
  expect(posts[0].body).toEqual({
    rfq_line_id: "l1", quote_line_id: "ql2", expected_version: 3, gate_override_code: "other", gate_override_note: "Срочно для сборки",
    not_cheapest_code: "lead_time", not_cheapest_note: null,
  });
});

it("the cheapest offer asks only for the gate reason when too few quotes came", async () => {
  mockServer();
  render(<PurchasingRfq />);
  fireEvent.click(await screen.findByRole("button", { name: "Разъём — ООО Альфа" }));
  const dialog = await screen.findByRole("dialog", { name: "Разъём: выбор поставщика" });
  expect(within(dialog).getByLabelText("Гейт конкуренции не пройден — причина обхода")).toBeInTheDocument();
  expect(within(dialog).queryByLabelText("Не самое дешёвое КП — причина")).not.toBeInTheDocument();
});

it("a line not asked of a supplier is «не запрашивали», the header edits his positions, the quote form lists his only", async () => {
  const second = {
    ...matrix.lines[0], id: "l2", procurement_line_id: "p2", legacy_id: "SM-2", name: "Клемма", article: null,
    gate: { priced: 1, required: 3, has_non_key: false, require_non_key: true, passes: false },
    cells: { s1: cell({ quote_line_id: "ql3" }), s2: { asked: false } },
  };
  const partial = {
    ...matrix,
    suppliers: [supplier("s1", "ООО Альфа", true), { ...supplier("s2", "ООО Бета", false), line_ids: ["l1"] }],
    lines: [matrix.lines[0], second],
  };
  const puts: { path: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "PUT") {
      puts.push({ path, body: JSON.parse(String(init.body)) });
      return json({ line_ids: null });
    }
    if (path === "/api/purchasing/rfqs?scope=mine") {
      return json({ rfqs: [{ id: "r1", number: "КП-2610-001", title: "Разъёмы", created_at: "2026-10-01T10:00:00", closed_at: null, mine: true, lines: 2, suppliers: 2, sent: 2, quotes: 3, decided: 0 }] });
    }
    if (path === "/api/purchasing/rfqs/directory") return json({ suppliers: [] });
    if (path === "/api/purchasing/rfqs/r1") return json(partial);
    return json({}, 404);
  });
  render(<PurchasingRfq />);
  const row = (await screen.findByText("Клемма")).closest("tr") as HTMLElement;
  expect(within(row).getByText("не запрашивали")).toBeInTheDocument();
  expect(within(row).queryByRole("button", { name: "Клемма — ООО Бета" })).not.toBeInTheDocument();
  expect(within(row).getByRole("button", { name: "Клемма — ООО Альфа" })).toBeInTheDocument();
  const head = screen.getByText("ООО Бета").closest("th") as HTMLElement;
  expect(head).toHaveTextContent("позиций: 1 из 2 · изменить");
  expect(screen.getByText("ООО Альфа ★").closest("th")).toHaveTextContent("позиций: 2 из 2");

  fireEvent.click(within(head).getByRole("button", { name: "+ КП ООО Бета" }));
  const quote = await screen.findByRole("region", { name: "Внести КП" });
  expect(within(quote).getByLabelText("Поставщик КП")).toHaveValue("s2");
  expect(within(quote).getByLabelText("Цена Разъём")).toBeInTheDocument();
  expect(within(quote).queryByLabelText("Цена Клемма")).not.toBeInTheDocument();
  fireEvent.click(within(quote).getByRole("button", { name: "Отмена" }));

  fireEvent.click(within(head).getByRole("button", { name: "Изменить позиции ООО Бета" }));
  const editor = await screen.findByRole("dialog", { name: "Позиции для «ООО Бета»" });
  const first = within(editor).getByLabelText("Запросить SM-1");
  expect(first).toBeChecked();
  expect(first).toBeDisabled();  // he has quoted it: it stays
  expect(within(editor).getByLabelText("Запросить SM-2")).not.toBeChecked();
  fireEvent.click(within(editor).getByLabelText("Запросить SM-2"));
  fireEvent.click(within(editor).getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(puts).toHaveLength(1));
  expect(puts[0]).toEqual({ path: "/api/purchasing/rfqs/r1/suppliers/s2/lines", body: { line_ids: ["l1", "l2"] } });
});

function mockImportServer(posts: { path: string; body: any }[]) {
  const proposal = {
    source: "paste", vat_included: true, default_vat_rate: "22",
    lines: [{
      source_name: "Разъём SHR-02 (100 шт)", source_article: "", qty: "1", unit: "уп", price: "5", vat_rate: null, lead_days: 7,
      in_stock: false, declined: false, analog: false, analog_note: "", rfq_line_id: "l1", matched_by: "article",
      pack: "100", moq: "100", pack_note: "КП за упаковку 100 шт: цена пересчитана за шт, упаковка и мин. партия — 100",
    }],
    unmatched: [{
      source_name: "Что-то ещё", source_article: "", qty: "3", unit: "шт", price: "10", vat_rate: null, lead_days: null,
      in_stock: false, declined: false, analog: false, analog_note: "", rfq_line_id: null, matched_by: null, pack: null, moq: null, pack_note: "",
    }],
    unread: ["мусор 12,50"],
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") {
      posts.push({ path, body: JSON.parse(String(init.body)) });
      return json(path.endsWith("/quotes/import") ? proposal : { id: "q9" }, path.endsWith("/quotes/import") ? 200 : 201);
    }
    if (path === "/api/purchasing/rfqs?scope=mine") {
      return json({ rfqs: [{ id: "r1", number: "КП-2610-001", title: "Разъёмы", created_at: "2026-10-01T10:00:00", closed_at: null, mine: true, lines: 1, suppliers: 2, sent: 2, quotes: 2, decided: 0 }] });
    }
    if (path === "/api/purchasing/rfqs/directory") return json({ suppliers: [] });
    if (path === "/api/purchasing/rfqs/r1") return json(matrix);
    return json({}, 404);
  });
}

it("the pasted text fills the form with the pack recalculated, lists the rest and sends the wording as an alias", async () => {
  const posts: { path: string; body: any }[] = [];
  mockImportServer(posts);
  render(<PurchasingRfq />);
  fireEvent.click(await screen.findByRole("button", { name: "+ КП ООО Бета" }));
  const quote = await screen.findByRole("region", { name: "Внести КП" });
  fireEvent.click(await within(quote).findByRole("button", { name: "Вставить текст КП" }));
  fireEvent.change(within(quote).getByLabelText("Текст КП"), { target: { value: "Разъём SHR-02 (100 шт) 1 уп 500,00 7 дн" } });
  fireEvent.click(within(quote).getByRole("button", { name: "Разобрать текст" }));
  // Every row is matched or skipped before anything reaches the form.
  const review = await within(quote).findByRole("region", { name: "Проверка импорта" });
  expect(within(review).getByText(/мусор 12,50/)).toBeInTheDocument();
  expect(within(review).getByText("Не решено строк: 1 — выберите позицию или «пропустить».")).toBeInTheDocument();
  const apply = within(review).getByRole("button", { name: "Перенести в КП" });
  expect(apply).toBeDisabled();
  fireEvent.change(within(review).getByLabelText("Позиция для «Что-то ещё»"), { target: { value: "skip" } });
  expect(apply).toBeEnabled();
  fireEvent.click(apply);
  expect(within(quote).getByLabelText("Цена Разъём")).toHaveValue("5");
  expect(within(quote).getByLabelText("Упаковка Разъём")).toHaveValue("100");
  expect(within(quote).getByLabelText("Мин. партия Разъём")).toHaveValue("100");
  expect(within(quote).getByLabelText("Срок Разъём")).toHaveValue("7");
  expect(within(quote).getByText(/цена пересчитана за шт/)).toBeInTheDocument();
  expect(posts[0].path).toBe("/api/purchasing/rfqs/r1/quotes/import");
  expect(posts[0].body).toEqual({ supplier_id: "s2", text: "Разъём SHR-02 (100 шт) 1 уп 500,00 7 дн" });

  fireEvent.click(within(quote).getByRole("button", { name: "Сохранить КП" }));
  await waitFor(() => expect(posts).toHaveLength(2));
  expect(posts[1].body.source).toBe("paste");
  expect(posts[1].body.aliases).toEqual([{ rfq_line_id: "l1", text: "Разъём SHR-02 (100 шт)" }]);
  expect(posts[1].body.lines[0]).toMatchObject({ unit_price: "5", pack: "100", moq: "100", lead_days: 7 });
});

it("opens the request named in the address and keeps the address on the request chosen", async () => {
  window.history.replaceState({}, "", "/?section=procurement&tab=rfq&rfq=r2");
  const asked: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    asked.push(path);
    if (path === "/api/purchasing/rfqs?scope=mine") {
      return json({ rfqs: [
        { id: "r1", number: "КП-2610-001", title: "Разъёмы", created_at: "2026-10-01T10:00:00", closed_at: null, mine: true, lines: 1, suppliers: 2, sent: 2, quotes: 2, decided: 0 },
        { id: "r2", number: "КП-2610-002", title: "Кабель", created_at: "2026-10-02T10:00:00", closed_at: null, mine: true, lines: 1, suppliers: 2, sent: 2, quotes: 2, decided: 0 },
      ] });
    }
    if (path === "/api/purchasing/rfqs/directory") return json({ suppliers: [] });
    if (path === "/api/purchasing/rfqs/r1") return json(matrix);
    if (path === "/api/purchasing/rfqs/r2") return json({ ...matrix, rfq: { ...matrix.rfq, id: "r2", number: "КП-2610-002", title: "Кабель" } });
    return json({}, 404);
  });
  render(<PurchasingRfq />);
  expect(await screen.findByRole("heading", { name: "КП-2610-002 · Кабель" })).toBeInTheDocument();
  expect(asked).not.toContain("/api/purchasing/rfqs/r1");
  fireEvent.click(screen.getByRole("button", { name: /КП-2610-001/ }));
  expect(await screen.findByRole("heading", { name: "КП-2610-001 · Разъёмы" })).toBeInTheDocument();
  await waitFor(() => expect(window.location.search).toBe("?section=procurement&tab=rfq&rfq=r1"));
  window.history.replaceState({}, "", "/");
});

it("«&letters=1» opens the supplier letters with the request; «Свернуть» drops it from the address", async () => {
  window.history.replaceState({}, "", "/?section=procurement&tab=rfq&rfq=r1&letters=1");
  mockServer();
  render(<PurchasingRfq />);
  const letters = await screen.findByRole("region", { name: "Письма поставщикам" });
  expect(within(letters).getAllByText(/Запрос КП № КП-2610-001 от 01\.10\.2026 — 1 позиция/).length).toBeGreaterThan(0);
  expect(screen.queryByRole("button", { name: "Письма поставщикам" })).not.toBeInTheDocument();
  fireEvent.click(within(letters).getByRole("button", { name: "Свернуть" }));
  expect(screen.queryByRole("region", { name: "Письма поставщикам" })).not.toBeInTheDocument();
  await waitFor(() => expect(window.location.search).toBe("?section=procurement&tab=rfq&rfq=r1"));
  fireEvent.click(screen.getByRole("button", { name: "Письма поставщикам" }));
  await waitFor(() => expect(window.location.search).toBe("?section=procurement&tab=rfq&rfq=r1&letters=1"));
  window.history.replaceState({}, "", "/");
});

it("the card shows projects, «решено N/M» and the sums; the archive is its own list (Витя via the owner 23.10)", async () => {
  const summary = (over: Record<string, unknown>) => ({
    id: "r1", number: "КП-2610-001", seq: 1, title: "Разъёмы", created_at: "2026-10-01T10:00:00", closed_at: null, mine: true,
    lines: 3, suppliers: 2, sent: 2, quotes: 4, decided: 1, to_decide: 2, projects: ["П-0042", "П-0614"],
    plan_sum: "12270.4", decided_sum: "5535", invoiced: 0, archived: false, ...over,
  });
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/purchasing/rfqs?scope=mine") {
      return json({ rfqs: [
        summary({}),
        summary({ id: "r2", number: "КП-2610-002", seq: 2, title: "Кабель", decided: 0, decided_sum: null, projects: [] }),
        summary({ id: "r3", number: "КП-2610-003", seq: 3, title: "Расходники", decided: 1, to_decide: 1, invoiced: 1, archived: true }),
      ] });
    }
    if (path === "/api/purchasing/rfqs/directory") return json({ suppliers: [] });
    if (/^\/api\/purchasing\/rfqs\/r\d$/.test(path)) {
      const id = path.split("/").pop()!;
      return json({ ...matrix, rfq: { ...matrix.rfq, id, number: `КП-2610-00${id.slice(1)}`, title: null } });
    }
    return json({}, 404);
  });
  render(<PurchasingRfq />);
  const first = (await screen.findByText("№ 1")).closest("button") as HTMLElement;
  expect(within(first).getByText("П-0042, П-0614")).toBeInTheDocument();
  expect(within(first).getByText("1/2")).toBeInTheDocument();
  expect(within(first).getByText("01.10.2026")).toBeInTheDocument();
  expect(within(first).getByText("План").nextSibling).toHaveTextContent(/^12\s270 ₽$/);
  expect(within(first).getByText("Решено").nextSibling).toHaveTextContent(/^5\s535 ₽$/);
  const second = screen.getByText("№ 2").closest("button") as HTMLElement;
  expect(within(second).queryByText("Решено")).not.toBeInTheDocument(); // nothing decided — the plan only
  expect(screen.queryByText("№ 3")).not.toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: "КП-2610-001" })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("tab", { name: "Архив 1" }));
  const archived = (await screen.findByText("№ 3")).closest("button") as HTMLElement;
  expect(within(archived).getByText("счета получены")).toBeInTheDocument();
  expect(screen.queryByText("№ 1")).not.toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: "КП-2610-003" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "В работе 2" })).toHaveAttribute("aria-selected", "false");
});

it("«Внести КП» opens a list under the matrix with the request's suppliers to pick from", async () => {
  mockServer();
  render(<PurchasingRfq />);
  fireEvent.click(await screen.findByRole("button", { name: "Внести КП" }));
  const entry = screen.getByRole("region", { name: "Внести КП" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  // Below the matrix in the page order.
  const table = screen.getByRole("button", { name: "Разъём — ООО Альфа" });
  expect(table.compareDocumentPosition(entry) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  const picker = within(entry).getByLabelText("Поставщик КП");
  expect(within(picker).getAllByRole("option").map((option) => option.textContent)).toEqual(["ООО Альфа ★ — КП есть", "ООО Бета — КП есть"]);
  expect(within(entry).getByRole("group", { name: "Файл КП" })).toHaveTextContent("Перетащите сюда PDF с КП");
  fireEvent.click(screen.getByRole("button", { name: "Свернуть КП" }));
  expect(screen.queryByRole("region", { name: "Внести КП" })).not.toBeInTheDocument();
});

it("opens «Все» at once when the user has no requests of his own but another buyer does (owner 22.10)", async () => {
  const asked: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    asked.push(path);
    if (path === "/api/purchasing/rfqs?scope=mine") return json({ rfqs: [] });
    if (path === "/api/purchasing/rfqs?scope=all") {
      return json({ rfqs: [{ id: "r1", number: "КП-2610-001", seq: 1, title: "Roller_Buyer № 2", created_at: "2026-10-01T10:00:00", closed_at: null, mine: false, lines: 53, suppliers: 3, sent: 3, quotes: 2, decided: 0 }] });
    }
    if (path === "/api/purchasing/rfqs/directory") return json({ suppliers: [] });
    if (path === "/api/purchasing/rfqs/r1") return json({ ...matrix, rfq: { ...matrix.rfq, mine: false } });
    return json({}, 404);
  });
  render(<PurchasingRfq />);
  const ordinal = await screen.findByText("№ 1");
  expect(ordinal).toHaveAttribute("title", "КП-2610-001");  // the short ordinal, the full number on hover
  expect(screen.getByRole("tab", { name: "Все" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("Все закупщики — менять можно только свои запросы.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Мои" }));
  expect(await screen.findByText("Запросов нет — нажмите «+ Запрос» и отметьте строки «В работе».")).toBeInTheDocument();
  expect(asked.filter((path) => path === "/api/purchasing/rfqs?scope=mine").length).toBeGreaterThanOrEqual(2);
});
