import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { identityApi } from "./api";
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

beforeEach(() => {
  vi.spyOn(identityApi, "uiSettings").mockResolvedValue({ views: {} });
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
  const alfa = await screen.findByRole("region", { name: "Письмо: Альфа" });
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
  const mail = await screen.findByRole("link", { name: "Открыть в почте" });
  mail.addEventListener("click", (event) => event.preventDefault());
  fireEvent.click(mail);
  expect(screen.queryByRole("button", { name: "отметить" })).not.toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});

const showLetters = () => render(<RfqLetters matrix={matrix([supplier("s", "Поставщик")], [line("a", "Разъём")])} editable onClose={() => undefined} onSent={() => undefined} />);

it("loads a personal template, saves only its unfilled text and reloads it", async () => {
  vi.mocked(identityApi.uiSettings).mockResolvedValue({ views: { "rfq.letters": { body: "Здравствуйте!\n{{позиции}}" } } });
  const save = vi.spyOn(identityApi, "saveUiSetting").mockResolvedValue({ view: "rfq.letters", value: {} });
  const view = showLetters();
  expect(await screen.findByText(/Здравствуйте!/)).toHaveTextContent("Разъём — 2 шт");
  fireEvent.click(screen.getByRole("button", { name: "Изменить шаблон" }));
  fireEvent.change(screen.getByLabelText("Текст шаблона"), { target: { value: "Просим КП:\n{{позиции}}\n{{срок_ответа}}" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить шаблон" }));
  await screen.findByText("Шаблон сохранён");
  expect(save).toHaveBeenCalledWith("rfq.letters", { body: "Просим КП:\n{{позиции}}\n{{срок_ответа}}" });
  view.unmount();
  vi.mocked(identityApi.uiSettings).mockResolvedValue({ views: { "rfq.letters": { body: "Просим КП:\n{{позиции}}\n{{срок_ответа}}" } } });
  showLetters();
  expect(await screen.findByText(/Просим КП:/)).toHaveTextContent("Разъём — 2 шт");
});

it("does not leak another user's template and always regenerates current quantities and supplier lines", async () => {
  vi.mocked(identityApi.uiSettings).mockResolvedValueOnce({ views: { "rfq.letters": { body: "Личный текст\n{{позиции}}" } } });
  const view = showLetters();
  await screen.findByText(/Личный текст/);
  view.rerender(<RfqLetters matrix={matrix([supplier("s2", "Другой")], [line("b", "Кабель", { quantity: "17", budget_unit_gross: "123456" })])} editable onClose={() => undefined} onSent={() => undefined} />);
  const letter = screen.getByRole("region", { name: "Письмо: Другой" });
  expect(letter).toHaveTextContent("Кабель — 17 шт");
  expect(letter).not.toHaveTextContent("Разъём");
  expect(letter).not.toHaveTextContent("123456");
  view.unmount();
  showLetters();
  await screen.findByText(/Добрый день!/);
  expect(screen.queryByText(/Личный текст/)).not.toBeInTheDocument();
});

it("blocks output during loading, allows retry and reports save failure without changing the active template", async () => {
  vi.mocked(identityApi.uiSettings).mockRejectedValueOnce(new Error("offline"));
  vi.spyOn(identityApi, "saveUiSetting").mockRejectedValue(new Error("offline"));
  showLetters();
  expect(screen.queryByRole("button", { name: "Изменить шаблон" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Скопировать текст" })).not.toBeInTheDocument();
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Повторить загрузку шаблона" }));
  await screen.findByText(/Добрый день!/);
  fireEvent.click(screen.getByRole("button", { name: "Изменить шаблон" }));
  fireEvent.change(screen.getByLabelText("Текст шаблона"), { target: { value: "Не сохранено\n{{позиции}}" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить шаблон" }));
  await screen.findByRole("alert");
  expect(screen.queryByText("Шаблон сохранён")).not.toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Письмо: Поставщик" })).toHaveTextContent("Добрый день!");
  expect(screen.getByLabelText("Текст шаблона")).toHaveValue("Не сохранено\n{{позиции}}");
});

it("rejects unknown placeholders, missing positions and overlong templates but permits ordinary braces", async () => {
  const save = vi.spyOn(identityApi, "saveUiSetting").mockResolvedValue({ view: "rfq.letters", value: {} });
  showLetters();
  fireEvent.click(await screen.findByRole("button", { name: "Изменить шаблон" }));
  for (const value of ["{{позиции}} {{цена}}", "Без позиций", "x".repeat(6001) + "{{позиции}}"] ) {
    fireEvent.change(screen.getByLabelText("Текст шаблона"), { target: { value } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить шаблон" }));
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(save).not.toHaveBeenCalled();
  }
  fireEvent.change(screen.getByLabelText("Текст шаблона"), { target: { value: "{Обычный текст}\n{{позиции}}" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить шаблон" }));
  await screen.findByText("Шаблон сохранён");
  expect(save).toHaveBeenCalledTimes(1);
});

it("does not allow editing before load or duplicate saves while saving", async () => {
  let loaded!: (value: { views: Record<string, unknown> }) => void;
  let saved!: (value: { view: string; value: unknown }) => void;
  vi.mocked(identityApi.uiSettings).mockReturnValue(new Promise((resolve) => { loaded = resolve; }));
  const save = vi.spyOn(identityApi, "saveUiSetting").mockReturnValue(new Promise((resolve) => { saved = resolve; }));
  showLetters();
  expect(screen.getByRole("status")).toHaveTextContent("Загрузка шаблона");
  expect(screen.queryByRole("button", { name: "Изменить шаблон" })).not.toBeInTheDocument();
  await act(async () => loaded({ views: { "rfq.letters": { body: "Загружен\n{{позиции}}" } } }));
  fireEvent.click(screen.getByRole("button", { name: "Изменить шаблон" }));
  fireEvent.change(screen.getByLabelText("Текст шаблона"), { target: { value: "Изменён\n{{позиции}}" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить шаблон" }));
  fireEvent.click(screen.getByRole("button", { name: "Сохраняю…" }));
  expect(save).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText("Текст шаблона")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Свернуть" })).toBeDisabled();
  await act(async () => saved({ view: "rfq.letters", value: { body: "Изменён\n{{позиции}}" } }));
  expect(screen.getByRole("region", { name: "Письмо: Поставщик" })).toHaveTextContent("Изменён");
});

it("does not silently use corrupt saved data; explicit default does not write any setting", async () => {
  vi.mocked(identityApi.uiSettings).mockResolvedValue({ views: { "rfq.letters": { body: "{{budget}}" } } });
  const save = vi.spyOn(identityApi, "saveUiSetting");
  showLetters();
  await screen.findByRole("alert");
  expect(screen.queryByRole("button", { name: "Скопировать текст" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Использовать стандартный шаблон" }));
  expect(screen.getByRole("region", { name: "Письмо: Поставщик" })).toHaveTextContent("Добрый день!");
  expect(save).not.toHaveBeenCalled();
});

it("template expansion is single-pass and optional markers never suppress chosen quantity tiers or deadline", () => {
  const body = letterBody([line("a", "Кабель {{срок_ответа}}")], "2026-11-12", { a: 9 }, [500], "Запрос:\n{{позиции}}");
  expect(body).toContain("Кабель {{срок_ответа}} — 9 шт");
  expect(body).toContain("при заказе от 500");
  expect(body).toContain("Ответ просим направить до 12.11.2026");
});
