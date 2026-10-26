import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { applyChoices } from "./RfqQuoteImport";
import { QuoteEntry } from "./RfqQuoteDialog";
import type { ImportedQuoteLine, RfqLine, RfqMatrix, RfqSupplier } from "./purchasingRfqApi";

const row = (over: Partial<ImportedQuoteLine>): ImportedQuoteLine => ({
  source_name: "Клеммник", source_article: "", qty: "2", unit: "шт", price: "100", vat_rate: null, lead_days: null,
  in_stock: false, declined: false, analog: false, analog_note: "", rfq_line_id: null, matched_by: null, pack: null, moq: null,
  pack_note: "", ...over,
});
const line = (id: string, name: string): RfqLine => ({
  id, procurement_line_id: `p-${id}`, legacy_id: `SM-${id}`, status: "В работе", version: 1, editable: true, name,
  article: null, quantity: "2", unit: "шт", need_by: null, budget_unit_gross: null, delta_pct: null,
  gate: { priced: 0, required: 3, has_non_key: false, require_non_key: true, passes: false },
  cheapest_quote_line_id: null, baseline_quote_line_id: null, decision: null, cells: {},
});
const supplier: RfqSupplier = {
  supplier_id: "s1", name: "ООО Альфа", is_key: false, reason: "manual", sent_at: null, line_ids: null, declined_at: null,
  decline_note: null, answered: false, delivery_cost: null, min_order_sum: null, basket_total: null, basket_lines: 0,
  chosen_total: null, min_order_warning: null,
};
const matrix: RfqMatrix = {
  rfq: { id: "r1", number: "КП-2610-001", title: null, created_at: "2026-10-01T10:00:00", closed_at: null, mine: true },
  settings: { gate_min_quotes: 3, gate_require_non_key: true, default_vat_rate: "22" }, suppliers: [supplier],
  lines: [line("a", "Разъём"), line("b", "Клемма")], quotes: [],
};
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

it("a kept row goes to its line; a wording from memory is not remembered twice", () => {
  const rows = [row({ source_name: "Разъём хитрый", rfq_line_id: "a", matched_by: "alias" }), row({ source_name: "Клемма", pack_note: "КП за упаковку" }), row({ source_name: "Мусор" })];
  const result = applyChoices(rows, ["a", "b", "skip"]);
  expect([...result.byLine.keys()]).toEqual(["a", "b"]);
  expect(result.aliases).toEqual([{ rfq_line_id: "b", text: "Клемма" }]);
  expect(result.notes).toEqual(["«Клемма»: КП за упаковку"]);
});

it("Excel paste: the buyer re-maps a column and ticks «цены с НДС» off; the rows go only after the review", async () => {
  const posts: any[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      posts.push({ path, body });
      if (path.endsWith("/quotes/paste")) {
        return json({
          header: ["Товар", "Код", "Шт", "Цена"], width: 4, columns: { name: 0, article: 1, qty: 2, unit: -1, price: 3, vat: -1, lead: -1, ...(body.columns ?? {}) },
          vat_included: body.vat_included, default_vat_rate: "22",
          rows: [row({ source_name: "Клемма", rfq_line_id: body.columns ? "b" : null, matched_by: body.columns ? "name" : null })],
        });
      }
      return json({ id: "q1" }, 201);
    }
    return json({ available: false, options: [] });
  });
  render(<QuoteEntry matrix={matrix} supplierId="s1" onClose={() => undefined} onSaved={() => undefined} />);
  fireEvent.click(screen.getByRole("button", { name: "Вставить из Excel" }));
  fireEvent.change(screen.getByLabelText("Таблица из Excel"), { target: { value: "Товар\tКод\tШт\tЦена\nКлемма\tK1\t2\t100" } });
  fireEvent.click(screen.getByLabelText("цены с НДС"));
  fireEvent.click(screen.getByRole("button", { name: "Разобрать" }));
  const review = await screen.findByRole("region", { name: "Проверка импорта" });
  expect(within(review).getByText(/цены без НДС/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Столбец «Наименование»"), { target: { value: "1" } });
  await waitFor(() => expect(posts).toHaveLength(2));
  expect(posts[1].body).toMatchObject({ vat_included: false, columns: { name: 1 } });
  await waitFor(() => expect(within(screen.getByRole("region", { name: "Проверка импорта" })).getByText("по названию")).toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Перенести в КП" }));
  expect(screen.getByLabelText("Цена Клемма")).toHaveValue("100");
  expect(screen.getByLabelText("НДС в цене Клемма")).not.toBeChecked();
});

it("a PDF: the header fills «Номер КП» and «Действует до», the file goes with the quote", async () => {
  const posts: any[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      posts.push({ path, body });
      if (path.endsWith("/quotes/import")) {
        return json({
          source: "file", vat_included: true, default_vat_rate: "22", unread: [], unmatched: [],
          lines: [row({ source_name: "Разъём", rfq_line_id: "a", matched_by: "article", price: "55" })],
          header: { number: "КП-123/45", valid_until: "2026-10-30", inn: "7701234567", supplier_id: "s1", supplier_name: "ООО Альфа", in_request: true },
        });
      }
      return json({ id: "q1" }, 201);
    }
    return json({});
  });
  const onSaved = vi.fn();
  render(<QuoteEntry matrix={matrix} supplierId="s1" onClose={() => undefined} onSaved={onSaved} />);
  const file = new File(["%PDF-1.4 test"], "КП Альфа.pdf", { type: "application/pdf" });
  fireEvent.change(screen.getByLabelText("PDF или фото КП"), { target: { files: [file] } });
  const review = await screen.findByRole("region", { name: "Проверка импорта" });
  expect(within(review).getByText(/поставщик в документе: ООО Альфа, ИНН 7701234567/)).toBeInTheDocument();
  expect(within(review).getByText("Прочитано по тексту")).toBeInTheDocument();
  fireEvent.click(within(review).getByRole("button", { name: "Перенести в КП" }));
  expect(screen.getByLabelText("Номер КП")).toHaveValue("КП-123/45");
  expect(screen.getByText("Файл «КП Альфа.pdf» сохранится вместе с КП.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Сохранить КП" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  const saved = posts.find((post) => post.path.endsWith("/quotes")).body;
  expect(saved).toMatchObject({ source: "file", number: "КП-123/45", valid_until: "2026-10-30", file_name: "КП Альфа.pdf" });
  expect(atob(saved.file_base64)).toBe("%PDF-1.4 test");
  expect(saved.aliases).toEqual([{ rfq_line_id: "a", text: "Разъём" }]);
});

it("a PDF dropped on the zone is read like a picked one; the ИНН of another supplier of the request picks him", async () => {
  const posts: any[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") {
      posts.push({ path, body: JSON.parse(String(init.body)) });
      return json({
        source: "file", vat_included: true, default_vat_rate: "22", unread: [], unmatched: [],
        lines: [row({ source_name: "Клемма", rfq_line_id: "b", matched_by: "name", price: "7" })],
        header: { number: "17", valid_until: null, inn: "7700000002", supplier_id: "s2", supplier_name: "ООО Бета", in_request: true },
      });
    }
    return json({});
  });
  const beta: RfqSupplier = { ...supplier, supplier_id: "s2", name: "ООО Бета" };
  render(<QuoteEntry matrix={{ ...matrix, suppliers: [supplier, beta] }} supplierId="s1" onClose={() => undefined} onSaved={() => undefined} />);
  const zone = screen.getByRole("group", { name: "Файл КП" });
  fireEvent.dragOver(zone, { dataTransfer: { files: [] } });
  expect(zone).toHaveClass("over");
  const file = new File(["%PDF-1.4 beta"], "Бета.pdf", { type: "application/pdf" });
  fireEvent.drop(zone, { dataTransfer: { files: [file], getData: () => "" } });
  expect(zone).not.toHaveClass("over");
  const review = await screen.findByRole("region", { name: "Проверка импорта" });
  expect(posts[0]).toMatchObject({ path: "/api/purchasing/rfqs/r1/quotes/import", body: { supplier_id: "s1", file_name: "Бета.pdf" } });
  fireEvent.click(within(review).getByRole("button", { name: "Перенести в КП" }));
  expect(screen.getByLabelText("Поставщик КП")).toHaveValue("s2");
  expect(screen.getByText("Поставщик выбран по ИНН в документе: «ООО Бета».")).toBeInTheDocument();
  expect(screen.getByLabelText("Цена Клемма")).toHaveValue("7");
});

it("a dropped file that is neither PDF nor text is not sent", () => {
  const fetch = vi.spyOn(globalThis, "fetch");
  render(<QuoteEntry matrix={matrix} supplierId="s1" onClose={() => undefined} onSaved={() => undefined} />);
  const file = new File(["PK"], "КП.xlsx", { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
  fireEvent.drop(screen.getByRole("group", { name: "Файл КП" }), { dataTransfer: { files: [file], getData: () => "" } });
  expect(screen.getByRole("alert")).toHaveTextContent("Перетащите PDF, фото (JPG, PNG) или текстовый файл");
  expect(fetch).not.toHaveBeenCalled();
});

it("a photo of a quote is sent, read by the model: the reader, the date and another supplier in the document are shown", async () => {
  const posts: any[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") {
      posts.push({ path, body: JSON.parse(String(init.body)) });
      if (path.endsWith("/quotes/import")) {
        return json({
          source: "file", reader: "model", model: "main", vat_included: false, default_vat_rate: "22", unread: [],
          lines: [row({ source_name: "Розетка 2 конт.", rfq_line_id: "a", matched_by: "model", price: "12" })],
          unmatched: [row({ source_name: "Доставка курьером", price: "500" })],
          header: { number: "77", date: "2026-10-20", valid_until: null, inn: "", document_name: "ООО «Гамма»", supplier_id: null,
            supplier_name: null, in_request: false, supplier_matches: false },
        });
      }
      return json({ id: "q1" }, 201);
    }
    return json({});
  });
  render(<QuoteEntry matrix={matrix} supplierId="s1" onClose={() => undefined} onSaved={() => undefined} />);
  const photo = new File(["\xff\xd8\xff photo"], "КП фото.jpg", { type: "image/jpeg" });
  fireEvent.drop(screen.getByRole("group", { name: "Файл КП" }), { dataTransfer: { files: [photo], getData: () => "" } });
  const review = await screen.findByRole("region", { name: "Проверка импорта" });
  expect(posts[0]).toMatchObject({ path: "/api/purchasing/rfqs/r1/quotes/import", body: { file_name: "КП фото.jpg" } });
  expect(within(review).getByText("Распознано моделью")).toBeInTheDocument();
  expect(within(review).getByText(/поставщик в документе: ООО «Гамма» · № 77 от 20\.10\.2026/)).toBeInTheDocument();
  expect(within(review).getByRole("status")).toHaveTextContent("В документе другой поставщик, чем выбран для КП: «ООО «Гамма»»");
  expect(within(review).getByText("по модели")).toBeInTheDocument();
  fireEvent.change(within(review).getByLabelText("Позиция для «Доставка курьером»"), { target: { value: "skip" } });
  fireEvent.click(within(review).getByRole("button", { name: "Перенести в КП" }));
  expect(screen.getByLabelText("Цена Разъём")).toHaveValue("12");
  expect(screen.getByText(/В документе поставщик «ООО «Гамма»», а КП вносится от «ООО Альфа»/)).toBeInTheDocument();
});
