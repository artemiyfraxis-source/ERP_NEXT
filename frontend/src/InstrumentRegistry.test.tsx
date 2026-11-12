import { afterEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ProductionInstrument, WarehouseLot } from "./api";
import { InstrumentRegistry } from "./InstrumentRegistry";

const base = "/api/production/instruments";
const instrument: ProductionInstrument = {
  id: "vna", version: 7, inventory_number: "СИ.001", name: "Анализатор цепей", instrument_type: "ВАЦ",
  kind: "Средство измерений", parent_id: null, serial_number: "А123", manufacturer: "Производитель",
  doc_designation: "ВАЦ-01", quantity: 1, storage_location: "ОТК", warehouse_grn: "",
  verification_number: "П-1", verified_at: "2026-01-01T09:30:00", verification_due: "2027-01-01T09:30:00",
  status: "Действует", files: [],
};
const accessory: ProductionInstrument = {
  ...instrument, id: "adapter", inventory_number: "СИ.002", name: "Переходник", kind: "Принадлежность", parent_id: instrument.id,
  quantity: "2", verification_number: null, verified_at: null, verification_due: null,
};
const kit: ProductionInstrument = { ...instrument, id: "kit", inventory_number: "СИ.003", name: "Набор калибровки", parent_id: instrument.id };
const unverified: ProductionInstrument = {
  ...instrument, id: "ruler", inventory_number: "СИ.005", name: "Линейка", status: "Поверка не подтверждена",
  verification_number: null, verified_at: null, verification_due: null,
};
const lot: WarehouseLot = {
  id: "lot", version: 12, warehouse: "ЦАР", grn: "ГРН-126", package: "1", article: "ПР-02", series: "",
  name: "Переходник складской", item_type: "", purpose: "", unit: "шт", storage_location: "Ячейка 1",
  inbound: "8", written_off: "0", reserved: "5", balance: "8", available: "3", source_receiving_ids: [],
};

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
}

type Request = { path: string; method: string; body: Record<string, unknown> };
function setup(rows: ProductionInstrument[] = [instrument], handle?: (request: Request) => Response | null) {
  let records = [...rows];
  const requests: Request[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const request = { path: String(input), method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : {} };
    requests.push(request);
    const custom = handle?.(request);
    if (custom) return custom;
    if (request.path === `${base}/next-number`) return json({ inventory_number: "СИ.006" });
    if (request.path === base && request.method === "GET") return json({ instruments: records });
    if (request.path.startsWith("/api/stock")) return json({ lots: [lot], total: 1, truncated: 0, next_cursor: null });
    if (request.path === `${base}/from-stock`) return json({ instrument: accessory });
    if (request.method === "PATCH") {
      const old = records.find((item) => request.path === `${base}/${item.id}`)!;
      const saved = { ...old, ...request.body, version: old.version + 1 } as ProductionInstrument;
      records = records.map((item) => item.id === saved.id ? saved : item);
      return json({ instrument: saved });
    }
    if (request.path === base && request.method === "POST") {
      const saved = { ...request.body, id: "new", version: 1, files: [] } as unknown as ProductionInstrument;
      records.push(saved); return json({ instrument: saved });
    }
    if (request.method === "POST" && request.path.endsWith("/files")) {
      records = records.map((item) => request.path === `${base}/${item.id}/files` ? { ...item, files: [{ id: "file", kind: "Паспорт", file_name: String(request.body.file_name), size: 3, uploaded_at: "2026-11-12", uploaded_by_name: "Иванов" }] } : item);
      return json({});
    }
    if (request.method === "DELETE") { records = records.map((item) => ({ ...item, files: [] })); return new Response(null, { status: 204 }); }
    throw new Error(`Unexpected request: ${request.method} ${request.path}`);
  });
  const onChanged = vi.fn(async () => {});
  render(<InstrumentRegistry instruments={rows} onChanged={onChanged} />);
  return { requests, onChanged };
}

function open(number = instrument.inventory_number) { fireEvent.click(screen.getByText(number)); }
function fill(label: string, value: string) { fireEvent.change(screen.getByLabelText(label), { target: { value } }); }
const save = () => fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
const writes = (requests: Request[]) => requests.filter((request) => request.method !== "GET");
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

it("keeps accessories and calibration kits below their parent with indentation and accessory quantity", () => {
  setup([accessory, kit, instrument]);
  const rows = screen.getAllByRole("row").slice(1);
  expect(rows.map((row) => within(row).getByText(/СИ\.00/).textContent)).toEqual(["СИ.001", "СИ.002", "СИ.003"]);
  expect(within(rows[1]).getByText(/Переходник/)).toHaveStyle({ paddingLeft: "16px" });
  expect(within(rows[1]).getByText(/2 шт/)).toBeInTheDocument();
  expect(within(rows[2]).getByText(/Набор калибровки/)).toHaveStyle({ paddingLeft: "16px" });
  fireEvent.change(screen.getByLabelText("Найти средство измерений"), { target: { value: "Набор" } });
  expect(screen.getByText(/Набор калибровки/)).toBeInTheDocument();
  expect(screen.queryByText("СИ.002")).not.toBeInTheDocument();
});

it("marks overdue and near-due verification dates, and uses the existing pending-verification tone", () => {
  setup([{ ...instrument, verification_due: "2000-01-01" }, unverified, { ...kit, verification_due: new Date().toISOString().slice(0, 10) }]);
  expect(screen.getByText("Просрочена").parentElement).toHaveClass("is-overdue");
  expect(screen.getByText("До 30 дней").parentElement).toHaveClass("is-soon");
  expect(screen.getByText("Поверка не подтверждена", { selector: ".status-chip" })).toHaveAttribute("data-tone", "warning");
});

it("prefills a new SI number and registers an unverified instrument without fabricated verification", async () => {
  const { requests } = setup([]);
  fireEvent.click(screen.getByRole("button", { name: "+ Средство измерений" }));
  await waitFor(() => expect(screen.getByLabelText("Инв. №")).toHaveValue("СИ.006"));
  fill("Наименование", "Мультиметр"); fill("Тип", "Мультиметр цифровой"); save();
  await waitFor(() => expect(writes(requests)).toHaveLength(1));
  expect(writes(requests)[0]).toMatchObject({ method: "POST", path: base, body: { inventory_number: "СИ.006", name: "Мультиметр", kind: "Средство измерений", parent_id: null,
    status: "Поверка не подтверждена", verification_number: null, verified_at: null, verification_due: null } });
  await screen.findByText("Запись сохранена");
  expect(screen.getByLabelText("Загрузить файл")).toBeInTheDocument();
});

it("shows and retries number-fetch failures while keeping the modal editable", async () => {
  let failed = true;
  setup([], ({ path }) => path.endsWith("/next-number") && failed ? json({ detail: "Нет номера" }, 503) : null);
  fireEvent.click(screen.getByRole("button", { name: "+ Средство измерений" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Нет номера");
  failed = false; fireEvent.click(screen.getByRole("button", { name: "Повторить получение номера" }));
  await waitFor(() => expect(screen.getByLabelText("Инв. №")).toHaveValue("СИ.006"));
});

it("PATCHes only changed fields with the record version and retains the returned version", async () => {
  const { requests, onChanged } = setup([{ ...instrument, quantity: "1.000" }]); open();
  fill("Обозначение для документов", "Анализатор ВАЦ"); save();
  await screen.findByText("Запись сохранена");
  expect(writes(requests)[0]).toEqual({ path: `${base}/vna`, method: "PATCH", body: { version: 7, doc_designation: "Анализатор ВАЦ" } });
  fill("Место хранения", "Лаборатория"); save();
  await waitFor(() => expect(writes(requests)).toHaveLength(2));
  expect(writes(requests)[1].body).toEqual({ version: 8, storage_location: "Лаборатория" });
  expect(onChanged).toHaveBeenCalled();
});

it("edits the VNA application on an analyzer and sends it with the record version", async () => {
  const { requests } = setup(); open();
  expect(screen.getByLabelText("Программа ВАЦ")).toHaveValue("");
  fill("Программа ВАЦ", "S2VNA"); save();
  await screen.findByText("Запись сохранена");
  expect(writes(requests)[0].body).toEqual({ version: 7, vna_app: "S2VNA" });
  expect(screen.getByLabelText("Программа ВАЦ")).toHaveValue("S2VNA");
});

it("keeps a configured application editable even if the instrument was renamed", () => {
  setup([{ ...instrument, name: "Прибор", instrument_type: "Прибор", vna_app: "Arinst VL" }]); open();
  expect(screen.getByLabelText("Программа ВАЦ")).toHaveValue("Arinst VL");
});

it("hides VNA configuration for ordinary SI and accessories", () => {
  setup([{ ...instrument, name: "Мультиметр", instrument_type: "Мультиметр" }, accessory]); open();
  expect(screen.queryByLabelText("Программа ВАЦ")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Закрыть" })); open(accessory.inventory_number);
  expect(screen.queryByLabelText("Программа ВАЦ")).not.toBeInTheDocument();
});

it("keeps edits on 409, blocks another save, and explicitly reloads the latest version", async () => {
  const { requests } = setup([instrument], ({ method }) => method === "PATCH" ? json({ detail: "Version mismatch" }, 409) : null);
  open(); fill("Наименование", "Мой анализатор"); save();
  expect(await screen.findByRole("alert")).toHaveTextContent("Запись изменил другой пользователь — обновите");
  expect(screen.getByLabelText("Наименование")).toHaveValue("Мой анализатор");
  expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Обновить запись" }));
  await waitFor(() => expect(screen.getByLabelText("Наименование")).toHaveValue(instrument.name));
  expect(writes(requests)).toHaveLength(1);
});

it("adds verification to an unverified SI and makes it active only on save", async () => {
  const { requests } = setup([unverified]); open(unverified.inventory_number);
  fireEvent.click(screen.getByRole("button", { name: "Добавить поверку" }));
  fill("№ свидетельства о поверке", "П-101"); fill("Поверено", "2026-11-01"); fill("Поверка до", "2027-11-01"); save();
  await waitFor(() => expect(writes(requests)).toHaveLength(1));
  expect(writes(requests)[0].body).toMatchObject({ version: 7, verification_number: "П-101", verified_at: "2026-11-01", verification_due: "2027-11-01", status: "Действует" });
});

it("honors an inactive status selected after starting verification", async () => {
  const { requests } = setup([unverified]); open(unverified.inventory_number);
  fireEvent.click(screen.getByRole("button", { name: "Добавить поверку" }));
  fill("Статус", "Не используется"); save();
  await waitFor(() => expect(writes(requests)).toHaveLength(1));
  expect(writes(requests)[0].body).toEqual({ version: 7, status: "Не используется" });
});

it("rejects an active SI without verification and prevents cyclical parent selection", () => {
  const { requests } = setup([instrument, kit, unverified]); open(unverified.inventory_number);
  fill("Статус", "Действует"); save();
  expect(screen.getByRole("alert")).toHaveTextContent("Укажите номер свидетельства и даты поверки");
  expect(writes(requests)).toHaveLength(0);
  vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(screen.getByRole("button", { name: "Закрыть" }));
  // Dirty close must be approved before changing the parent card.
  vi.mocked(window.confirm).mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Закрыть" }));
  open();
  expect(within(screen.getByLabelText("В составе прибора")).queryByRole("option", { name: /Набор калибровки/ })).not.toBeInTheDocument();
});

it("creates an accessory with the parent preset and no verification fields", async () => {
  const { requests } = setup(); open();
  fireEvent.click(screen.getByRole("button", { name: "+ Принадлежность" }));
  await waitFor(() => expect(screen.getByLabelText("Инв. №")).toHaveValue("СИ.006"));
  expect(screen.getByLabelText("Вид")).toHaveValue("Принадлежность");
  expect(screen.getByLabelText("В составе прибора")).toHaveValue(instrument.id);
  expect(screen.queryByLabelText("Поверка до")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Тип")).not.toBeRequired();
  expect(screen.getByLabelText("Тип")).toHaveValue("");
  fill("Наименование", "Переходник"); save();
  await waitFor(() => expect(writes(requests)).toHaveLength(1));
  expect(writes(requests)[0].body).toMatchObject({ parent_id: instrument.id, kind: "Принадлежность", instrument_type: "Принадлежность", storage_location: "ОТК", verification_number: null, verified_at: null, verification_due: null });
});

it("returns a stock accessory only after confirmation and sends its current version", async () => {
  const stockAccessory = { ...accessory, warehouse_writeoff_id: "writeoff" };
  const { requests, onChanged } = setup([instrument, stockAccessory], ({ path }) => path.endsWith("/return-to-stock")
    ? json({ instrument: { ...stockAccessory, version: stockAccessory.version + 1, status: "Не используется" } }) : null);
  open(stockAccessory.inventory_number);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(screen.getByRole("button", { name: "Вернуть на склад" }));
  expect(writes(requests)).toHaveLength(0);
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Вернуть на склад" }));
  await screen.findByText("Принадлежность возвращена на склад");
  expect(writes(requests)).toEqual([{ method: "POST", path: `${base}/${stockAccessory.id}/return-to-stock`, body: { version: stockAccessory.version } }]);
  expect(screen.getByLabelText("Количество, шт")).toHaveValue(Number(stockAccessory.quantity));
  expect(screen.getByLabelText("Статус")).toHaveValue("Не используется");
  expect(screen.queryByRole("button", { name: "Вернуть на склад" })).not.toBeInTheDocument();
  expect(onChanged).toHaveBeenCalled();
});

it("blocks stock return on conflict and preserves the unsaved draft", async () => {
  const stockAccessory = { ...accessory, warehouse_writeoff_id: "writeoff" };
  const { requests } = setup([instrument, stockAccessory], ({ path }) => path.endsWith("/return-to-stock") ? json({ detail: "Конфликт" }, 409) : null);
  open(stockAccessory.inventory_number); fill("Наименование", "Мои изменения");
  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Вернуть на склад" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Запись изменил другой пользователь — обновите");
  expect(screen.getByLabelText("Наименование")).toHaveValue("Мои изменения");
  expect(screen.getByRole("button", { name: "Вернуть на склад" })).toBeDisabled();
  expect(writes(requests)).toHaveLength(1);
});

it.each([accessory, { ...accessory, warehouse_writeoff_id: "writeoff", status: "Не используется" as const }, instrument])(
  "hides return-to-stock when the record is ineligible: $kind / $status", (item) => {
    setup([item]); open(item.inventory_number);
    expect(screen.queryByRole("button", { name: "Вернуть на склад" })).not.toBeInTheDocument();
  },
);

it("uploads a passport as base64, displays its link and metadata, and deletes only after confirmation", async () => {
  const { requests } = setup([unverified]); open(unverified.inventory_number);
  fireEvent.change(screen.getByLabelText("Загрузить файл"), { target: { files: [new File(["abc"], "паспорт.pdf", { type: "application/pdf" })] } });
  const link = await screen.findByRole("link", { name: "паспорт.pdf" });
  expect(writes(requests)[0]).toEqual({ path: `${base}/ruler/files`, method: "POST", body: { kind: "Паспорт", file_name: "паспорт.pdf", content_base64: "YWJj" } });
  expect(link).toHaveAttribute("href", `${base}/ruler/files/file`);
  expect(link).toHaveAttribute("target", "_blank");
  expect(screen.getByText(/Иванов/)).toBeInTheDocument();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(screen.getByRole("button", { name: "Удалить файл паспорт.pdf" }));
  expect(writes(requests)).toHaveLength(1);
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Удалить файл паспорт.pdf" }));
  await waitFor(() => expect(screen.queryByRole("link", { name: "паспорт.pdf" })).not.toBeInTheDocument());
  expect(writes(requests)[1]).toMatchObject({ path: `${base}/ruler/files/file`, method: "DELETE" });
});

it("shows passport presence and file counts in the registry", () => {
  setup([{ ...instrument, files: [{ id: "p", kind: "Паспорт", file_name: "паспорт.pdf", size: 12, uploaded_at: "2026-11-12", uploaded_by_name: "Иванов" }] }]);
  expect(screen.getByText("1 · Паспорт")).toBeInTheDocument();
});

it("writes off an instrument only after confirmation using its version", async () => {
  const { requests } = setup(); open();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(screen.getByRole("button", { name: "Списать / Не используется" }));
  expect(writes(requests)).toHaveLength(0);
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Списать / Не используется" }));
  await waitFor(() => expect(writes(requests)).toHaveLength(1));
  expect(writes(requests)[0].body).toEqual({ version: 7, status: "Не используется" });
});

async function pickLot() {
  open(); fireEvent.click(screen.getByRole("button", { name: "+ Со склада" }));
  const dialog = screen.getByRole("dialog", { name: "Принадлежность со склада" });
  fireEvent.click(await within(dialog).findByText("ГРН-126"));
  return dialog;
}

it("searches lots by server query, shows balance, caps available quantity and calls from-stock with the lot version", async () => {
  const { requests } = setup(); const dialog = await pickLot();
  expect(within(dialog).getByText(/Остаток: 8 шт · Доступно: 3 шт/)).toBeInTheDocument();
  fill("Найти партию", "ПР-02"); fireEvent.click(within(dialog).getByRole("button", { name: "Найти" }));
  await waitFor(() => expect(requests.some((request) => request.path === "/api/stock?q=%D0%9F%D0%A0-02")).toBe(true));
  fireEvent.click(await within(dialog).findByText("ГРН-126"));
  fill("Количество со склада", "4");
  expect(within(dialog).getByRole("button", { name: "Передать со склада" })).toBeDisabled();
  fill("Количество со склада", "2");
  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(within(dialog).getByRole("button", { name: "Передать со склада" }));
  await waitFor(() => expect(writes(requests)).toHaveLength(1));
  expect(writes(requests)[0]).toEqual({ path: `${base}/from-stock`, method: "POST", body: { parent_id: "vna", lot_id: "lot", lot_version: 12, quantity: 2, name: lot.name, storage_location: "ОТК" } });
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Принадлежность со склада" })).not.toBeInTheDocument());
});

it("requires a fresh lot selection after a stock version conflict", async () => {
  const { requests } = setup([instrument], ({ path }) => path.endsWith("/from-stock") ? json({ detail: "Устаревшая партия" }, 409) : null);
  const dialog = await pickLot(); vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(within(dialog).getByRole("button", { name: "Передать со склада" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("Запись изменил другой пользователь — обновите");
  expect(within(dialog).queryByRole("button", { name: "Передать со склада" })).not.toBeInTheDocument();
  expect(within(dialog).queryByText("ГРН-126")).not.toBeInTheDocument();
  expect(writes(requests)).toHaveLength(1);
});

it("blocks a second stock write after an ambiguous transport failure", async () => {
  const { requests } = setup([instrument], ({ path }) => { if (path.endsWith("/from-stock")) throw new TypeError("Connection lost"); return null; });
  const dialog = await pickLot(); vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(within(dialog).getByRole("button", { name: "Передать со склада" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("Результат неизвестен");
  expect(within(dialog).getByRole("button", { name: "Передать со склада" })).toBeDisabled();
  fireEvent.click(within(dialog).getByRole("button", { name: "Передать со склада" }));
  expect(writes(requests)).toHaveLength(1);
});

it("opens a card with Enter and closes only the top stock window with Escape", async () => {
  setup();
  fireEvent.keyDown(screen.getByText("СИ.001").closest("tr")!, { key: "Enter" });
  const card = screen.getByRole("dialog");
  fireEvent.click(screen.getByRole("button", { name: "+ Со склада" }));
  const stockWindow = screen.getByRole("dialog", { name: "Принадлежность со склада" });
  await within(stockWindow).findByText("ГРН-126");
  fireEvent.keyDown(stockWindow, { key: "Escape" });
  expect(stockWindow).not.toBeInTheDocument();
  expect(card).toBeInTheDocument();
  fireEvent.keyDown(card, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
