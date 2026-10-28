import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PrimaryDocsPage } from "./PrimaryDocsPage";
import { PurchasingUpd } from "./PurchasingUpd";
import { docIssues, inputToKop, lineIssues, rub } from "./purchasingUpdApi";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const card = (over: Record<string, unknown> = {}) => ({
  id: "file-dart-0001", file_name: "Дарт 500.pdf", kind: "УПД", origin: "google", status: "parsed", parser_status: "Разобран",
  number: "2937", date: "2026-04-21", seller_name: "ООО «Дарт»", seller_inn: "7707083893", seller_org_inn: "7707083893",
  seller_note: "", supplier_id: "s1", supplier_name: "ООО «Дарт»", supplier_manual: false, total_gross: 50_000, line_count: 2,
  issues: [], has_file: true, project_reference: null, manual: false, verified: false, changed_in_parser: false, twin_of: null,
  copies: 0, buyer_inn: "5040191181", buyer_kind: "ours", buyer_org_inn: "5040191181", buyer_note: "", ...over,
});

const listing = (items: unknown[]) => ({
  items, total: items.length, page: 1, page_size: 100, kinds: { upd: 2, m15: 1, tn_sf: 0, other: 3 },
  counts: { waiting: 1, parsed: 1, needs_review: 1, verified: 0, duplicate: 2, not_purchase: 0 }, review: 1,
  origins: { edo: 1, google: 2 }, buyers: [{ key: "ours", count: 2 }, { key: "7726465440", count: 1 }],
  suppliers: [{ id: "s1", name: "ООО «Дарт»", count: 1 }], no_supplier: 2, sum_gross: 1_523_000,
});

const header = {
  number: "2937", date: "2026-04-21", seller_name: "ООО «Дарт»", seller_inn: "7707083893", seller_kpp: "", buyer_inn: "5040191181",
  total_net: 40_984, total_vat: 9_016, total_gross: 50_000,
};
const line = {
  seq: 1, name: "Разъём SMA-J", article: "SMA-J", unit: "шт", quantity: "1", price: "409.84", net: 40_984, vat_rate: "22", vat: 9_016,
  gross: 50_000, is_service: false, product_type_code: "", unit_code: "", excise: "", country_code: "", country: "", declaration: "",
  issues: [], fixes: [],
};
const detail = (over: Record<string, unknown> = {}) => ({
  ...card(), folder: "/Обработано", tag: "", model: "gemini", basis: "Счет № 77", payment_doc: "№ 901 от 10.09.2026",
  file_amount: null, dup_of: null, drive_url: "", parser_updated: "2026-09-23T20:00:00", synced_at: null, verified_at: null,
  manual_saved_at: null, header, parser_header: header, lines: [line], parser_lines: [line],
  twins: { main: null, copies: [{ id: "g-copy", origin: "google", file_name: "Дарт (2).pdf", kind: "УПД", number: "2937", date: "2026-04-21", status: "duplicate", total_gross: 50_000, seller_name: "ООО «Дарт»" }] },
  file: { name: "Дарт 500.pdf", size: 2048, kind: "pdf" }, ...over,
});

type Call = { method: string; path: string; body?: string };

function mockServer(calls: Call[] = []) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ method, path, body: init?.body as string | undefined });
    if (path === "/api/purchasing/upd/import-edo") {
      const body = JSON.parse(String(init?.body));
      const status = body.files[0].file_name.endsWith(".png") ? "error" : "created";
      return json({ dry_run: body.dry_run, files: [{ name: body.files[0].file_name, status, document_id: "edo-1", number: "РЭ-0915", seller_name: "ООО Ромашка", message: status === "error" ? "«x.png»: нужен XML или ZIP из ЭДО" : undefined }] });
    }
    if (path === "/api/purchasing/upd/import-pdf") {
      const body = JSON.parse(String(init?.body));
      const name = body.files[0].file_name;
      const status = name.startsWith("копия") ? "unchanged" : "created";
      return json({ files: [{ name, status, document_id: "pdf-1", message: status === "unchanged" ? "уже есть в реестре: «Дарт 500.pdf»" : undefined }], queued: status === "created" ? ["pdf-1"] : [], model_ready: false });
    }
    if (path === "/api/purchasing/upd/file-dart-0001/reparse") return json(detail({ status: "waiting", parser_status: "В очереди" }));
    if (path === "/api/purchasing/upd/sellers") return json({ sellers: [{ inn: "7707083893", name: "ООО «Дарт»", documents: 2, total_gross: 110_000, last_date: "2026-05-01", needs_review: 1 }], skipped_invalid_inn: 1 });
    if (path === "/api/purchasing/upd/sellers/import") {
      const body = JSON.parse(String(init?.body));
      return json({ created: [{ inn: "7707083893", name: "ООО «Дарт»", supplier_id: "s9" }], skipped: [], suppliers_changed: body.dry_run ? 0 : 2, dry_run: body.dry_run });
    }
    if (path === "/api/purchasing/upd/file-dart-0001/verified") return json(detail({ status: "verified", verified: true, verified_at: "2026-10-13T12:00:00" }));
    if (path === "/api/purchasing/upd/file-dart-0001/manual" && method === "PUT") {
      const body = JSON.parse(String(init?.body));
      return json(detail({ manual: true, header: body.header, manual_saved_at: "2026-10-13T12:00:00" }));
    }
    if (path === "/api/purchasing/upd/file-dart-0001/supplier") return json(detail({ supplier_id: "s2", supplier_name: "ООО Маркет", supplier_manual: true }));
    if (path === "/api/purchasing/upd/file-dart-0001/project") return json(detail({ project_reference: "591К.3" }));
    if (path === "/api/purchasing/upd/file-dart-0001") return json(detail());
    if (path === "/api/purchasing/suppliers") return json([{ id: "s2", name: "ООО Маркет", inn: null }]);
    if (path === "/api/procurement/today") return json({ today: "2026-10-21", sections: [], calm: true });
    if (path === "/api/procurement/meta") return json({ units: ["шт"], statuses: [] });
    if (path.startsWith("/api/procurement")) return json({ lines: [], buyers: [], stages: [], next_cursor: null });
    if (path.startsWith("/api/purchasing/upd")) {
      const params = new URLSearchParams(path.split("?")[1] ?? "");
      if (params.get("duplicates") === "true") return json(listing([card({ id: "g-copy", status: "duplicate", twin_of: "file-dart-0001" })]));
      return json(listing([
        card(),
        card({ id: "file-krox-0002", status: "needs_review", seller_name: "ООО Крокс", supplier_id: null, supplier_name: null,
          seller_note: "ИНН продавца прочитан с ошибкой: 3862192701", seller_org_inn: "3662192701", issues: ["ИНН продавца не проходит проверку"],
          total_gross: 1_473_000, origin: "edo", buyer_kind: "other", buyer_org_inn: "7726465440" }),
      ]));
    }
    return json({});
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  cleanup(); // unmount before the reset: a live tree would refetch the options without the mock
});

const query = (calls: Call[]) => calls.filter((call) => call.method === "GET" && call.path.startsWith("/api/purchasing/upd?"))
  .map((call) => new URLSearchParams(call.path.split("?")[1]));

it("lists documents with status, source, other-buyer chips and the corrected seller INN", async () => {
  mockServer();
  render(<PurchasingUpd />);
  const table = await screen.findByRole("list", { name: "Реестр УПД" });
  const rows = [table, ...within(table).getAllByRole("listitem")];
  expect(rows).toHaveLength(3);
  expect(rows[1].textContent).toContain("Дарт 500.pdf");
  expect(rows[1].textContent).toContain("21.04.2026");
  expect(rows[1].textContent).toContain("500,00");
  expect(rows[2].textContent).toContain("Требует ручной проверки");
  expect(rows[2].textContent).toContain("ИНН 7726465440");
  expect(rows[2].textContent).toContain("ИНН продавца прочитан с ошибкой: 3862192701 → 3662192701");
  expect(rows[2].textContent).toContain("без поставщика");
  expect(screen.getByRole("button", { name: "Все 3" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Дубликаты 2" })).toBeInTheDocument();
  expect(screen.getByRole("status").textContent).toContain("2 док.");
});

it("sends the filters to the server: status, review, duplicates, source, buyer, supplier, period and text", async () => {
  const calls: Call[] = [];
  mockServer(calls);
  render(<PurchasingUpd />);
  await screen.findByRole("list", { name: "Реестр УПД" });
  fireEvent.click(screen.getByRole("button", { name: "Требует ручной проверки 1" }));
  await waitFor(() => expect(query(calls).some((p) => p.get("status") === "needs_review")).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "Требует проверки 1" }));
  await waitFor(() => expect(query(calls).some((p) => p.get("review") === "true" && !p.get("status"))).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "Дубликаты 2" }));
  await waitFor(() => expect(query(calls).some((p) => p.get("duplicates") === "true")).toBe(true));
  fireEvent.click(screen.getByRole("button", { name: "ЭДО 1" }));
  await waitFor(() => expect(query(calls).some((p) => p.get("origin") === "edo")).toBe(true));
  fireEvent.change(screen.getByLabelText("Покупатель"), { target: { value: "other" } });
  await waitFor(() => expect(query(calls).some((p) => p.get("buyer") === "other")).toBe(true));
  fireEvent.change(screen.getByLabelText("Поставщик"), { target: { value: "none" } });
  await waitFor(() => expect(query(calls).some((p) => p.get("no_supplier") === "true")).toBe(true));
  fireEvent.change(screen.getByLabelText("Дата с"), { target: { value: "2026-04-01" } });
  await waitFor(() => expect(query(calls).some((p) => p.get("date_from") === "2026-04-01")).toBe(true));
  fireEvent.change(screen.getByLabelText("Поиск по реестру УПД"), { target: { value: "дарт" } });
  await waitFor(() => expect(query(calls).some((p) => p.get("q") === "дарт")).toBe(true));
});

it("opens the card: header, lines, the file in a new tab, twins, «Проверено», supplier and project", async () => {
  const calls: Call[] = [];
  mockServer(calls);
  render(<PurchasingUpd extraBlocks={(id) => <div>Счета по УПД {id}</div>} />);
  fireEvent.click(await screen.findByRole("button", { name: "ООО «Дарт»" }));
  const view = await screen.findByRole("region", { name: "Карточка УПД" });
  expect(within(view).getByRole("table", { name: "Шапка документа" }).textContent).toContain("2937");
  expect(within(view).getByRole("table", { name: "Позиции" }).textContent).toContain("Разъём SMA-J");
  const file = within(view).getByRole("link", { name: "Открыть файл" });
  expect(file.getAttribute("href")).toBe("/api/purchasing/upd/file-dart-0001/file");
  expect(file.getAttribute("target")).toBe("_blank");
  expect(view.textContent).toContain("его копии в реестре");
  expect(view.textContent).toContain("Счета по УПД file-dart-0001");
  expect(view.textContent).toContain("по ИНН продавца");

  fireEvent.click(within(view).getByRole("button", { name: "Проверено" }));
  await within(view).findByRole("button", { name: "Снять отметку «Проверено»" });
  expect(calls.find((call) => call.path.endsWith("/verified"))?.body).toBe(JSON.stringify({ verified: true }));

  fireEvent.click(within(view).getByRole("button", { name: "Сменить поставщика" }));
  fireEvent.click(await within(view).findByRole("button", { name: "ООО Маркет" }));
  await within(view).findByText("выбран вручную");
  expect(calls.find((call) => call.path.endsWith("/supplier"))?.body).toBe(JSON.stringify({ supplier_id: "s2" }));

  fireEvent.change(within(view).getByLabelText("Проект документа"), { target: { value: "591К.3 " } });
  fireEvent.click(within(view).getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(calls.find((call) => call.path.endsWith("/project"))?.body).toBe(JSON.stringify({ project_reference: "591К.3" })));

  fireEvent.click(within(view).getByRole("button", { name: "← Реестр УПД" }));
  await screen.findByRole("list", { name: "Реестр УПД" });
});

it("edits the buyer's version with live checks and saves it against the version it was opened on", async () => {
  const calls: Call[] = [];
  mockServer(calls);
  render(<PurchasingUpd focusId="file-dart-0001" />);
  const view = await screen.findByRole("region", { name: "Карточка УПД" });
  fireEvent.click(within(view).getByRole("button", { name: "Исправить" }));
  expect(await within(view).findByText("Арифметика сходится, реквизиты на месте.")).toBeInTheDocument();
  fireEvent.change(within(view).getByLabelText("Строка 1: С НДС"), { target: { value: "600" } });
  expect(within(view).getByText(/После сохранения останутся замечания/).textContent).toContain("строк с замечаниями: 1");
  expect(within(view).getByLabelText("Строка 1: С НДС").closest("tr")?.getAttribute("title")).toBe("без НДС + НДС ≠ с НДС");
  fireEvent.change(within(view).getByLabelText("Строка 1: С НДС"), { target: { value: "500,00" } });
  fireEvent.change(within(view).getByLabelText("ИНН продавца"), { target: { value: "3662192701" } });
  fireEvent.click(within(view).getByRole("button", { name: "Добавить строку" }));
  fireEvent.change(within(view).getByLabelText("Строка 2: Наименование"), { target: { value: "Доставка" } });
  fireEvent.change(within(view).getByLabelText("Строка 2: Кол-во"), { target: { value: "1" } });
  fireEvent.change(within(view).getByLabelText("Строка 2: Цена без НДС"), { target: { value: "100" } });
  fireEvent.change(within(view).getByLabelText("Строка 2: Ставка"), { target: { value: "22" } });
  fireEvent.click(within(view).getByRole("button", { name: "Досчитать пустые" }));
  expect((within(view).getByLabelText("Строка 2: С НДС") as HTMLInputElement).value).toBe("122.00");
  fireEvent.click(within(view).getByRole("button", { name: "Сохранить свою версию" }));
  await within(view).findByRole("table", { name: "Шапка документа" });
  const body = JSON.parse(calls.find((call) => call.method === "PUT" && call.path.endsWith("/manual"))?.body ?? "{}");
  expect(body.parser_updated).toBe("2026-09-23T20:00:00");
  expect(body.manual_saved_at).toBeNull();
  expect(body.header.seller_inn).toBe("3662192701");
  expect(body.lines).toEqual([
    { seq: 1, name: "Разъём SMA-J", article: "SMA-J", unit: "шт", quantity: "1", price: "409.84", net: 40_984, vat_rate: "22", vat: 9_016, gross: 50_000, is_service: false },
    { seq: 2, name: "Доставка", article: "", unit: "", quantity: "1", price: "100", net: 10_000, vat_rate: "22", vat: 2_200, gross: 12_200, is_service: false },
  ]);
});

it("in view mode the card offers no edits", async () => {
  mockServer();
  render(<PurchasingUpd readOnly focusId="file-dart-0001" />);
  const view = await screen.findByRole("region", { name: "Карточка УПД" });
  expect(within(view).queryByRole("button", { name: "Исправить" })).not.toBeInTheDocument();
  expect(within(view).queryByRole("button", { name: "Проверено" })).not.toBeInTheDocument();
  expect(within(view).queryByLabelText("Проект документа")).not.toBeInTheDocument();
});

it("uploads EDO files: a dry run first, then the apply", async () => {
  const calls: Call[] = [];
  mockServer(calls);
  render(<PurchasingUpd />);
  fireEvent.click(await screen.findByRole("button", { name: "Загрузить из ЭДО" }));
  const dialog = screen.getByRole("dialog", { name: "Загрузить из ЭДО" });
  const xml = new File(["<Файл/>"], "ON_NSCHFDOPPR_x.xml", { type: "text/xml" });
  const png = new File(["x"], "x.png", { type: "image/png" });
  fireEvent.change(within(dialog).getByLabelText("Файлы из ЭДО"), { target: { files: [xml, png] } });
  const table = await within(dialog).findByRole("table", { name: "Документы из ЭДО" });
  expect(table.textContent).toContain("добавлен");
  expect(table.textContent).toContain("нужен XML или ZIP из ЭДО");
  const posts = () => calls.filter((call) => call.path === "/api/purchasing/upd/import-edo").map((call) => JSON.parse(String(call.body)));
  expect(posts().map((body) => body.dry_run)).toEqual([true, true]);
  fireEvent.click(within(dialog).getByRole("button", { name: "Загрузить в реестр" }));
  await within(dialog).findByText(/Готово:/);
  expect(posts().map((body) => [body.files[0].file_name, body.dry_run])).toEqual([
    ["ON_NSCHFDOPPR_x.xml", true], ["x.png", true], ["ON_NSCHFDOPPR_x.xml", false],
  ]);
});

it("«Продавцы без поставщика» previews the names and creates the chosen suppliers", async () => {
  const calls: Call[] = [];
  mockServer(calls);
  render(<PurchasingUpd />);
  fireEvent.click(await screen.findByRole("button", { name: "Продавцы без поставщика" }));
  const dialog = screen.getByRole("dialog", { name: "Продавцы без поставщика" });
  const table = await within(dialog).findByRole("table", { name: "Продавцы без поставщика" });
  expect(table.textContent).toContain("7707083893");
  expect(dialog.textContent).toContain("Ещё 1 — с ИНН, который не проходит проверку");
  fireEvent.click(within(dialog).getByRole("button", { name: "Завести поставщиков (1)" }));
  await within(dialog).findByText(/Заведено поставщиков: 1, документов привязано: 2/);
  const bodies = calls.filter((call) => call.path.endsWith("/sellers/import")).map((call) => JSON.parse(String(call.body)));
  expect(bodies).toEqual([{ dry_run: true, inns: null }, { dry_run: false, inns: ["7707083893"] }]);
});

it("the kind tabs УПД · М-15 · ТН+СФ · Прочие send the group and show its count", async () => {
  const calls: Call[] = [];
  mockServer(calls);
  render(<PurchasingUpd />);
  const tabs = await screen.findByRole("group", { name: "Вид документа" });
  await waitFor(() => expect(tabs.textContent).toContain("Все виды 6"));
  expect(within(tabs).getByRole("button", { name: "М-15 1" })).toBeInTheDocument();
  expect(within(tabs).getByRole("button", { name: "ТН+СФ 0" })).toBeInTheDocument();
  fireEvent.click(within(tabs).getByRole("button", { name: "Прочие 3" }));
  await waitFor(() => expect(query(calls).at(-1)?.get("kind")).toBe("other"));
  fireEvent.click(within(tabs).getByRole("button", { name: "УПД 2" }));
  await waitFor(() => expect(query(calls).at(-1)?.get("kind")).toBe("upd"));
  fireEvent.click(within(tabs).getByRole("button", { name: "Все виды 6" }));
  await waitFor(() => expect(calls.at(-1)?.path).toBe("/api/purchasing/upd"));
});

it("the «УПД» tab of «Первичная документация» exists", async () => {
  mockServer();
  render(<PrimaryDocsPage />);
  fireEvent.click(await screen.findByRole("tab", { name: "УПД" }));
  expect(await screen.findByRole("list", { name: "Реестр УПД" })).toBeInTheDocument();
});

it("checks match the server's rules", () => {
  const facts = { quantity: 1, price: 409.84, net: 40_984, vat_rate: "22", vat: 9_016, gross: 50_000 };
  expect(lineIssues(facts)).toEqual([]);
  expect(lineIssues({ ...facts, gross: 60_000 })).toEqual(["без НДС + НДС ≠ с НДС"]);
  expect(lineIssues({ ...facts, quantity: 2 })).toEqual(["кол-во × цена ≠ без НДС"]);
  expect(lineIssues({ ...facts, vat: 5_000, gross: 45_984 })).toEqual(["НДС не равен ставке 22%"]);
  expect(docIssues("УПД", { ...header, total_gross: 60_000 }, [facts])).toEqual(["сумма строк 500 ≠ итог документа 600"]);
  expect(docIssues("УПД", { ...header, seller_inn: "971038484" }, [facts])).toEqual(["ИНН продавца не проходит проверку: 971038484"]);
  expect(inputToKop("1 234,56")).toBe(123_456);
  expect(Number.isNaN(inputToKop("abc"))).toBe(true);
  expect(rub(null)).toBe("—");
});

it("shows a warehouse scan as «Скан склада» and filters the registry by it", async () => {
  const calls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    calls.push(path);
    if (path.startsWith("/api/purchasing/upd")) {
      return json({
        ...listing([card({ id: "wh-5b1f", file_name: "УПД.pdf", origin: "scan", status: "needs_review", parser_status: "Требует проверки" })]),
        origins: { edo: 1, google: 2, scan: 1 },
      });
    }
    return json({});
  });
  render(<PurchasingUpd />);
  const table = await screen.findByRole("list", { name: "Реестр УПД" });
  const chip = within(table).getByText("Скан склада");
  expect(chip.getAttribute("title")).toContain("принятого на складе");
  const button = screen.getByRole("button", { name: "Скан склада 1" });
  expect(button.getAttribute("title")).toContain("распознавание модели");
  fireEvent.click(button);
  await waitFor(() => expect(calls.some((path) => new URLSearchParams(path.split("?")[1] ?? "").get("origin") === "scan")).toBe(true));
});

it("uploads PDFs into the parser's queue and says when the model key is missing", async () => {
  const calls: Call[] = [];
  mockServer(calls);
  render(<PurchasingUpd />);
  fireEvent.click(await screen.findByRole("button", { name: "Загрузить PDF" }));
  const dialog = screen.getByRole("dialog", { name: "Загрузить PDF" });
  const scan = new File(["%PDF-1.4"], "Сафетин 1 220.pdf", { type: "application/pdf" });
  const copy = new File(["%PDF-1.4"], "копия.pdf", { type: "application/pdf" });
  fireEvent.change(within(dialog).getByLabelText("Файлы PDF"), { target: { files: [scan, copy] } });
  const table = await within(dialog).findByRole("table", { name: "Загруженные PDF" });
  await waitFor(() => expect(table.textContent).toContain("уже был"));
  expect(table.textContent).toContain("в очереди на разбор");
  expect(within(dialog).getByRole("alert").textContent).toContain("Ключ модели не задан");
  const names = calls.filter((call) => call.path === "/api/purchasing/upd/import-pdf").map((call) => JSON.parse(String(call.body)).files[0].file_name);
  expect(names).toEqual(["Сафетин 1 220.pdf", "копия.pdf"]);
});

it("«Разобрать заново» queues the PDF and the card offers «Обновить» while it waits", async () => {
  const calls: Call[] = [];
  mockServer(calls);
  render(<PurchasingUpd />);
  fireEvent.click(await screen.findByRole("button", { name: "ООО «Дарт»" }));
  const view = await screen.findByRole("region", { name: "Карточка УПД" });
  fireEvent.click(within(view).getByRole("button", { name: "Разобрать заново" }));
  await within(view).findByRole("button", { name: "Обновить" });
  expect(within(view).queryByRole("button", { name: "Разобрать заново" })).toBeNull();
  expect(calls.some((call) => call.path === "/api/purchasing/upd/file-dart-0001/reparse" && call.method === "POST")).toBe(true);
});
