import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ProductionInstrument, WarehouseLot } from "./api";
import { ReferenceInstrumentRegistry } from "./ReferenceInstrumentRegistry";

/* Всё, что умела вкладка «ОТК → Средства измерения» (ОТК-1, 27.11.2026): теперь это «База данных · Средства измерений»,
   право правки «ОТК» (`can_edit_identity`) даёт полное окно прибора. */
const refBase = "/api/references/instruments";

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
async function setup(rows: ProductionInstrument[] = [instrument], handle?: (request: Request) => Response | null) {
  let records = [...rows];
  let lists = 0;
  const onChanged = vi.fn(async () => {});
  const requests: Request[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const request = { path: String(input), method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : {} };
    requests.push(request);
    const custom = handle?.(request);
    if (custom) return custom;
    if (request.path === `${base}/next-number`) return json({ inventory_number: "СИ.006" });
    if (request.path.endsWith("/matings")) return json({ instrument_id: "x", resource: null, status: "unknown", has_connectors: false, ends: [{ end: "A", total: 0, version: 0, percent_used: null, status: "unknown", initial_recorded: false, can_initialize: true }], events: [] });
    if (request.path.endsWith("/vna-checks")) return json({ instrument_id: "x", applicable: false, due: [], records: [] });
    if (request.path.endsWith("/rf-interfaces")) return json({ rf: { version: 0, interfaces: [], role: "unknown", confirmed: false, source: "", f_min_ghz: null, max_points: null, kit_definition: "" } });
    if (request.path === refBase && request.method === "GET") {
      if (++lists > 1) void onChanged();
      return json({ instruments: records, count: records.length, quantity: String(records.length), custom_columns: [], can_edit_identity: true, can_edit_custom: true });
    }
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
  const rendered = render(<ReferenceInstrumentRegistry />);
  await waitFor(() => expect(requests.some((item) => item.path === refBase)).toBe(true));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  return { requests, onChanged, ...rendered };
}

function open(number = instrument.inventory_number) { fireEvent.click(screen.getByText(number, { selector: "td.key .instrument-cell" })); }
function fill(label: string, value: string) { fireEvent.change(screen.getByLabelText(label), { target: { value } }); }
const save = () => fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
const writes = (requests: Request[]) => requests.filter((request) => request.method !== "GET");
afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); window.history.replaceState({}, "", "/"); });

it("has no link back to ОТК: the registry lives only in the database", async () => {
  await setup();
  expect(screen.getByRole("heading", { name: "Средства измерений" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "База данных · Средства измерений" })).not.toBeInTheDocument();
  open();
  const dialog = screen.getByRole("dialog", { name: "Средство измерений: Анализатор цепей" });
  expect(within(dialog).queryByRole("link", { name: "Открыть в ОТК" })).not.toBeInTheDocument();
  expect(within(dialog).queryByRole("link", { name: "Открыть в базе данных" })).not.toBeInTheDocument();
  expect(within(dialog).getByLabelText("Инв. №")).toHaveValue(instrument.inventory_number);
  // Поля реестра базы данных есть и у окна с правом ОТК.
  expect(within(dialog).getByLabelText("Артикул")).toBeEnabled();
  expect(within(dialog).getByLabelText("Примечание")).toBeEnabled();
  expect(within(dialog).getByRole("button", { name: "Показать историю" })).toBeInTheDocument();
  await waitFor(() => expect(within(dialog).queryByText("Загрузка…")).not.toBeInTheDocument());
});

it("opens the inventory deep link when records arrive and does not reopen after closing", async () => {
  window.history.replaceState({}, "", "/?section=references&tab=ob_measuring&si=" + encodeURIComponent(accessory.inventory_number));
  await setup([instrument, accessory]);
  const dialog = await screen.findByRole("dialog", { name: "Средство измерений: Переходник" });
  expect(within(dialog).getByRole("link", { name: "Паспорт (PDF)" })).toHaveAttribute("href", `${base}/adapter/passport.pdf`);
  expect(within(dialog).getByRole("link", { name: "Паспорт (PDF)" })).toHaveAttribute("target", "_blank");
  fireEvent.click(within(dialog).getByRole("button", { name: "Закрыть" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(new URLSearchParams(window.location.search).has("si")).toBe(false);
});

it("explains an unknown inventory deep link", async () => {
  window.history.replaceState({}, "", "/?section=references&tab=ob_measuring&si=" + encodeURIComponent("СИ.999"));
  await setup();
  expect(screen.getByRole("alert")).toHaveTextContent("СИ.999 — запись не найдена");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("opens the print confirmation for filtered rows and for explicit selection", async () => {
  await setup([instrument, accessory], (request) => request.path === `${base}/label-agent` ? json({ online: true }) : null);
  fireEvent.change(screen.getByLabelText("Найти средство измерений"), { target: { value: "Переходник" } });
  fireEvent.click(screen.getByRole("button", { name: "Печать этикеток" }));
  let dialog = screen.getByRole("dialog", { name: "Печать этикеток" });
  expect(within(dialog).getByText("Записей: 1")).toBeInTheDocument();
  expect(within(dialog).getByRole("img")).toHaveAttribute("src", expect.stringContaining("/adapter/label.png"));
  fireEvent.click(within(dialog).getByRole("button", { name: "Отмена" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Печать этикеток" })).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Найти средство измерений"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "Выбрать: СИ.001" }));
  fireEvent.click(screen.getByRole("button", { name: "Печать выбранных этикеток" }));
  dialog = screen.getByRole("dialog", { name: "Печать этикеток" });
  expect(within(dialog).getByText("Записей: 1")).toBeInTheDocument();
  expect(within(dialog).getByRole("img")).toHaveAttribute("src", expect.stringContaining("/vna/label.png"));
  await waitFor(() => expect(within(dialog).queryByText("Проверка связи с принтером…")).not.toBeInTheDocument());
});

it("assigns the server-suggested serial with the saved version and displays it in the card", async () => {
  const empty = { ...instrument, serial_number: "", suggested_serial_number: "ADP-sS-26-001" };
  const { requests } = await setup([empty], (request) => request.path === `${base}/vna/serial`
    ? json({ instrument: { ...empty, serial_number: "ADP-sS-26-001", suggested_serial_number: null, version: 8 } }) : null);
  open();
  fireEvent.click(screen.getByRole("button", { name: "Присвоить номер ADP-sS-26-001" }));
  await screen.findByText("Номер присвоен");
  expect(screen.getByLabelText("Зав. №")).toHaveValue("ADP-sS-26-001");
  expect(writes(requests)).toEqual([{ path: `${base}/vna/serial`, method: "POST", body: { version: 7 } }]);
  expect(screen.queryByRole("button", { name: /Присвоить номер/ })).not.toBeInTheDocument();
});

it("refreshes the suggested serial after saving RF ends and preserves unsaved card fields", async () => {
  const empty = { ...instrument, serial_number: "", suggested_serial_number: "SI-26-001" };
  const { onChanged } = await setup([empty], (request) => {
    if (request.path !== `${base}/vna/rf-interfaces` || request.method !== "PUT") return null;
    empty.suggested_serial_number = "SI-s-26-001";
    return json({ rf: { ...request.body, version: 1 }, suggested_serial_number: empty.suggested_serial_number });
  });
  open();
  fireEvent.change(screen.getByLabelText("Место хранения"), { target: { value: "Ячейка 2" } });
  fireEvent.click(await screen.findByRole("button", { name: "Добавить интерфейсы" }));
  fireEvent.click(screen.getByRole("button", { name: "Добавить интерфейс" }));
  fireEvent.change(screen.getByLabelText("Серия 1"), { target: { value: "SMA" } });
  fireEvent.change(screen.getByLabelText("Корпус 1"), { target: { value: "розетка" } });
  fireEvent.change(screen.getByLabelText("Центральный контакт 1"), { target: { value: "гнездо" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить без подтверждения" }));
  await screen.findByText("ВЧ-интерфейсы сохранены без подтверждения");
  expect(screen.getByRole("button", { name: "Присвоить номер SI-s-26-001" })).toBeInTheDocument();
  expect(screen.getByLabelText("Место хранения")).toHaveValue("Ячейка 2");
  expect(onChanged).toHaveBeenCalledOnce();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Закрыть" }));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: /Средство измерений:/ })).not.toBeInTheDocument());
  open();
  expect(await screen.findByRole("button", { name: "Присвоить номер SI-s-26-001" })).toBeInTheDocument();
});

it("opens the saved-card preview and closes it without closing the card", async () => {
  await setup([instrument], (request) => request.path === `${base}/label-agent` ? json({ online: true }) : null);
  open();
  fireEvent.click(screen.getByRole("button", { name: "Печать этикетки" }));
  const dialog = screen.getByRole("dialog", { name: "Печать этикеток" });
  expect(within(dialog).getByRole("img")).toHaveAttribute("src", expect.stringContaining("/vna/label.png"));
  await waitFor(() => expect(within(dialog).queryByText("Проверка связи с принтером…")).not.toBeInTheDocument());
  fireEvent.keyDown(dialog, { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "Печать этикеток" })).not.toBeInTheDocument();
  expect(screen.getByRole("dialog", { name: /Средство измерений:/ })).toBeInTheDocument();
});

it("asks to save card edits before printing", async () => {
  await setup(); open();
  fill("Наименование", "Изменено");
  expect(screen.getByRole("button", { name: "Печать этикетки" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Печать этикетки" })).toHaveAttribute("title", "Сначала сохраните изменения");
});

const shared: ProductionInstrument = { ...accessory, id: "shared", inventory_number: "СИ.004", name: "Общий переходник", parent_id: null };

it("marks shared accessories in the flat list without confusing missing parents with shared ones", async () => {
  await setup([instrument, accessory, shared, { ...shared, id: "orphan", inventory_number: "СИ.007", parent_id: "missing" }]);
  const row = screen.getByText(shared.inventory_number, { selector: "td.key .instrument-cell" }).closest("tr")!;
  expect(within(row).getByText("общая")).toHaveAttribute("title", "общая");
  const orphan = screen.getByText("СИ.007", { selector: "td.key .instrument-cell" }).closest("tr")!;
  expect(within(orphan).queryByText("общая")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Найти средство измерений"), { target: { value: "общая" } });
  expect(inventoryOrder()).toEqual([shared.inventory_number]);
});

it("puts shared accessories in a separate searchable tree group and retains linked children", async () => {
  await setup([shared, accessory, instrument, kit]);
  fireEvent.click(screen.getByRole("button", { name: "По приборам" }));
  const group = screen.getByRole("region", { name: "Общие принадлежности" });
  expect(within(group).getByText(shared.inventory_number)).toBeInTheDocument();
  expect(within(group).queryByText(instrument.inventory_number)).not.toBeInTheDocument();
  const mainTable = screen.getByText(instrument.inventory_number, { selector: "td.key .instrument-cell" }).closest("table")!;
  expect(within(mainTable).getByText(accessory.inventory_number)).toBeInTheDocument();
  expect(within(mainTable).queryByText(shared.inventory_number)).not.toBeInTheDocument();
  fireEvent.click(within(mainTable).getByRole("button", { name: "Наименование" }));
  expect(within(mainTable).getAllByRole("row").slice(1).map(row => row.querySelector("td.key")?.textContent))
    .toEqual([instrument.inventory_number, kit.inventory_number, accessory.inventory_number]);
  fireEvent.change(screen.getByLabelText("Найти общую принадлежность"), { target: { value: "нет такого" } });
  expect(within(group).getByText("Общие принадлежности не найдены.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Списком" }));
  expect(screen.queryByRole("heading", { name: "Общие принадлежности" })).not.toBeInTheDocument();
  expect(screen.getByText("общая")).toBeInTheDocument();
});

it("creates a shared accessory directly without any measuring instruments", async () => {
  const { requests } = await setup([]);
  fireEvent.click(screen.getByRole("button", { name: "+ Общая принадлежность" }));
  await waitFor(() => expect(screen.getByLabelText("Инв. №")).toHaveValue("СИ.006"));
  expect(screen.getByLabelText("В составе прибора")).not.toBeRequired();
  expect(screen.getByRole("option", { name: "Без прибора (общая)" })).toBeInTheDocument();
  expect(screen.queryByLabelText("Поверка до")).not.toBeInTheDocument();
  fill("Наименование", "Общий переходник"); save();
  await waitFor(() => expect(writes(requests)).toHaveLength(1));
  expect(writes(requests)[0]).toMatchObject({ method: "POST", body: {
    parent_id: null, kind: "Принадлежность", instrument_type: "Принадлежность",
    verification_number: null, verified_at: null, verification_due: null,
  } });
});

it("keeps shared group sorting independent of instrument tree sorting", async () => {
  await setup([instrument, accessory, { ...shared, name: "Ящик переходников" },
    { ...shared, id: "another", inventory_number: "СИ.006", name: "Адаптер" }]);
  fireEvent.click(screen.getByRole("button", { name: "По приборам" }));
  const group = screen.getByRole("region", { name: "Общие принадлежности" });
  const main = screen.getByRole("region", { name: "Средства измерений" });
  const order = () => within(group).getAllByRole("row").slice(1).map(row => row.querySelector("td.key")?.textContent);
  fireEvent.click(within(main).getByRole("button", { name: "Наименование" }));
  expect(order()).toEqual(["СИ.004", "СИ.006"]);
  expect(within(group).getByRole("columnheader", { name: "Наименование" })).toHaveAttribute("aria-sort", "none");
  const header = within(group).getByRole("button", { name: "Наименование" });
  fireEvent.click(header);
  expect(order()).toEqual(["СИ.006", "СИ.004"]);
  fireEvent.click(header); fireEvent.click(header);
  expect(order()).toEqual(["СИ.004", "СИ.006"]);
});

it("detaches a parented accessory through an explicit null and can attach a shared one", async () => {
  const { requests } = await setup([instrument, accessory]); open(accessory.inventory_number);
  fill("В составе прибора", ""); save();
  await waitFor(() => expect(writes(requests)).toHaveLength(1));
  expect(writes(requests)[0]).toMatchObject({ method: "PATCH", body: { version: accessory.version, parent_id: null } });
  await screen.findByText("Запись сохранена");
  fill("В составе прибора", instrument.id); save();
  await waitFor(() => expect(writes(requests)).toHaveLength(2));
  expect(writes(requests)[1]).toMatchObject({ method: "PATCH", body: { version: accessory.version + 1, parent_id: instrument.id } });
});

it("keeps accessories and calibration kits below their parent with indentation and accessory quantity", async () => {
  await setup([accessory, kit, instrument]);
  fireEvent.click(screen.getByRole("button", { name: "По приборам" }));
  const rows = screen.getAllByRole("row").slice(1);
  expect(inventoryOrder()).toEqual(["СИ.001", "СИ.002", "СИ.003"]);
  expect(within(rows[1]).getByText(/Переходник/)).toHaveStyle({ paddingLeft: "16px" });
  expect(within(rows[1]).getByText(/2 шт/)).toBeInTheDocument();
  expect(within(rows[2]).getByText(/Набор калибровки/)).toHaveStyle({ paddingLeft: "16px" });
  fireEvent.change(screen.getByLabelText("Найти средство измерений"), { target: { value: "Набор" } });
  expect(screen.getByText(/Набор калибровки/)).toBeInTheDocument();
  expect(screen.queryByText("СИ.002")).not.toBeInTheDocument();
});

function inventoryOrder() {
  return screen.getAllByRole("row").slice(1).map((row) => row.querySelector("td.key")?.textContent);
}

it("defaults to a flat list with alphabetic prefixes and natural inventory numbers", async () => {
  await setup([
    { ...instrument, id: "hundred", inventory_number: "СИ.100" },
    { ...instrument, id: "ten", inventory_number: "СИ.010" },
    { ...instrument, id: "equipment", inventory_number: "ОБ.040" },
    accessory,
  ]);
  expect(screen.getByRole("button", { name: "Списком" })).toHaveAttribute("aria-pressed", "true");
  expect(inventoryOrder()).toEqual(["ОБ.040", "СИ.002", "СИ.010", "СИ.100"]);
  expect(screen.getByText(/Переходник/, { selector: ".instrument-name" })).toHaveStyle({ paddingLeft: "0px" });
  fireEvent.click(screen.getByRole("button", { name: "Инв. №" }));
  expect(inventoryOrder()).toEqual(["ОБ.040", "СИ.002", "СИ.010", "СИ.100"]);
  expect(screen.getByRole("columnheader", { name: /Инв/ })).toHaveAttribute("aria-sort", "ascending");
  fireEvent.click(screen.getByRole("button", { name: /Инв. №/ }));
  expect(inventoryOrder()).toEqual(["СИ.100", "СИ.010", "СИ.002", "ОБ.040"]);
});

it("compares unpadded inventory numbers numerically without mutating the input", async () => {
  const records = ["СИ.100", "СИ.10", "СИ.2"].map((number) => ({ ...instrument, id: number, inventory_number: number }));
  await setup(records);
  expect(inventoryOrder()).toEqual(["СИ.2", "СИ.10", "СИ.100"]);
  expect(records.map((item) => item.inventory_number)).toEqual(["СИ.100", "СИ.10", "СИ.2"]);
});

const sortRows: ProductionInstrument[] = [
  { ...instrument, id: "a", inventory_number: "СИ.001", name: "Якорь", doc_designation: "Я", kind: "Средство измерений", serial_number: "9", verification_due: "2027-01-01", status: "Не используется", storage_location: "Ячейка" },
  { ...instrument, id: "b", inventory_number: "СИ.002", name: "Анализатор", doc_designation: "А", kind: "Принадлежность", serial_number: "2", verification_due: "2026-01-01", status: "Действует", storage_location: "Архив", files: [{ id: "f", kind: "Паспорт", file_name: "f.pdf", size: 1, uploaded_at: "2026-01-01", uploaded_by_name: "Иванов" }] },
];

it.each(["Наименование", "Обозн.", "Вид", "Зав. №", "Поверка до", "Статус", "Место", "Файлы"])(
  "sorts the flat list by %s in both directions and clears sorting", async (title) => {
    await setup(sortRows);
    if (title === "Обозн.") {
      fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
      fireEvent.click(screen.getByRole("checkbox", { name: "Обозн." }));
    }
    const ascending = title === "Файлы" ? ["СИ.001", "СИ.002"] : ["СИ.002", "СИ.001"];
    const header = screen.getByRole("columnheader", { name: title });
    fireEvent.click(within(header).getByRole("button"));
    expect(inventoryOrder()).toEqual(ascending);
    expect(header).toHaveAttribute("aria-sort", "ascending");
    fireEvent.click(within(header).getByRole("button"));
    expect(inventoryOrder()).toEqual([...ascending].reverse());
    expect(header).toHaveAttribute("aria-sort", "descending");
    fireEvent.click(within(header).getByRole("button"));
    expect(inventoryOrder()).toEqual(["СИ.001", "СИ.002"]);
    expect(header).toHaveAttribute("aria-sort", "none");
  },
);

it("shows and sorts the direct parent by its stable id, leaving top-level and orphan parent cells empty", async () => {
  const other = { ...instrument, id: "other", inventory_number: "СИ.010", name: "Другой прибор" };
  await setup([instrument, accessory, other, { ...kit, parent_id: other.id }, { ...unverified, parent_id: "missing" }]);
  const rows = screen.getAllByRole("row").slice(1);
  const parentCells = rows.map((row) => row.querySelector('td[data-label="Прибор"]')!);
  expect(parentCells.map((cell) => cell.textContent)).toEqual(["—", "СИ.001", "СИ.010", "—", "—"]);
  expect(parentCells[1].firstElementChild).toHaveAttribute("title", "СИ.001 · Анализатор цепей");
  fireEvent.click(screen.getByRole("button", { name: "Прибор" }));
  expect(inventoryOrder().slice(0, 2)).toEqual(["СИ.002", "СИ.003"]);
});

it("sorts parents and siblings in tree mode without separating descendants", async () => {
  const other = { ...instrument, id: "other", inventory_number: "СИ.010", name: "Анализатор А" };
  await setup([instrument, accessory, other, { ...kit, inventory_number: "СИ.020" }]);
  fireEvent.click(screen.getByRole("button", { name: "По приборам" }));
  expect(inventoryOrder()).toEqual(["СИ.001", "СИ.002", "СИ.020", "СИ.010"]);
  fireEvent.click(screen.getByRole("button", { name: "Наименование" }));
  expect(inventoryOrder()).toEqual(["СИ.010", "СИ.001", "СИ.020", "СИ.002"]);
  fireEvent.click(screen.getByRole("button", { name: /Наименование/ }));
  expect(inventoryOrder()).toEqual(["СИ.001", "СИ.002", "СИ.020", "СИ.010"]);
  fireEvent.click(screen.getByRole("button", { name: "Списком" }));
  expect(inventoryOrder()).toEqual(["СИ.002", "СИ.020", "СИ.001", "СИ.010"]);
});

it("remembers the view and restores saved sorting in tree mode", async () => {
  const { unmount } = await setup([instrument, accessory, kit]);
  fireEvent.click(screen.getByRole("button", { name: "По приборам" }));
  fireEvent.click(screen.getByRole("button", { name: "Наименование" }));
  unmount();
  await setup([instrument, accessory, kit]);
  expect(screen.getByRole("button", { name: "По приборам" })).toHaveAttribute("aria-pressed", "true");
  expect(inventoryOrder()).toEqual(["СИ.001", "СИ.003", "СИ.002"]);
  expect(screen.getByRole("columnheader", { name: /Наименование/ })).toHaveAttribute("aria-sort", "ascending");
  fireEvent.click(screen.getByRole("button", { name: "Списком" }));
  expect(localStorage.getItem("quality-instruments-view")).toBe("list");
});

it("keeps the registry usable when browser storage is blocked", async () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("Blocked"); });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Blocked"); });
  await setup([accessory, instrument]);
  expect(screen.getByRole("button", { name: "Списком" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(screen.getByRole("button", { name: "По приборам" }));
  expect(screen.getByRole("button", { name: "По приборам" })).toHaveAttribute("aria-pressed", "true");
  expect(inventoryOrder()).toEqual(["СИ.001", "СИ.002"]);
});

it("replaces the previous column layout with compact defaults while preserving tree view", async () => {
  localStorage.setItem("quality-instruments-view", "tree");
  localStorage.setItem("erp-table:quality-instruments-v2", JSON.stringify({
    hidden: [], known: ["inventory_number", "name", "parentLabel", "doc_designation", "kind", "serial_number", "verification_due", "status", "storage_location", "files"],
    widths: { name: 80, status: 300, storage_location: 300 },
  }));
  await setup([instrument, accessory]);
  expect(screen.getByRole("button", { name: "По приборам" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.queryByRole("columnheader", { name: "Обозн." })).not.toBeInTheDocument();
  expect(screen.queryByRole("columnheader", { name: "Прибор" })).not.toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Статус" })).toHaveStyle({ width: "144px" });
  fireEvent.click(screen.getByRole("button", { name: "Списком" }));
  expect(screen.queryByRole("columnheader", { name: "Обозн." })).not.toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Прибор" })).toBeInTheDocument();
});

it("marks overdue and near-due verification dates, and uses the existing pending-verification tone", async () => {
  await setup([{ ...instrument, verification_due: "2000-01-01" }, unverified, { ...kit, verification_due: new Date(Date.now() + 10 * 86_400_000).toISOString().slice(0, 10) }]);
  expect(screen.getByText("Просрочена").parentElement).toHaveClass("is-overdue");
  expect(screen.getByText("До 30 дней").parentElement).toHaveClass("is-soon");
  expect(screen.getByText("Не подтверждена", { selector: ".status-chip" })).toHaveAttribute("data-tone", "warning");
  expect(screen.getByText("Не подтверждена", { selector: ".status-chip" })).toHaveAttribute("title", "Поверка не подтверждена");
});

it("prefills a new SI number and registers an unverified instrument without fabricated verification", async () => {
  const { requests } = await setup([]);
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
  await setup([], ({ path }) => path.endsWith("/next-number") && failed ? json({ detail: "Нет номера" }, 503) : null);
  fireEvent.click(screen.getByRole("button", { name: "+ Средство измерений" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Нет номера");
  failed = false; fireEvent.click(screen.getByRole("button", { name: "Повторить получение номера" }));
  await waitFor(() => expect(screen.getByLabelText("Инв. №")).toHaveValue("СИ.006"));
});

it("PATCHes only changed fields with the record version and retains the returned version", async () => {
  const { requests, onChanged } = await setup([{ ...instrument, quantity: "1.000" }]); open();
  fill("Обозначение для документов", "Анализатор ВАЦ"); save();
  await screen.findByText("Запись сохранена");
  expect(writes(requests)[0]).toEqual({ path: `${base}/vna`, method: "PATCH", body: { version: 7, doc_designation: "Анализатор ВАЦ" } });
  fill("Место хранения", "Лаборатория"); save();
  await waitFor(() => expect(writes(requests)).toHaveLength(2));
  expect(writes(requests)[1].body).toEqual({ version: 8, storage_location: "Лаборатория" });
  expect(onChanged).toHaveBeenCalled();
});

it("edits the VNA application on an analyzer and sends it with the record version", async () => {
  const { requests } = await setup(); open();
  expect(screen.getByLabelText("Программа ВАЦ")).toHaveValue("");
  fill("Программа ВАЦ", "S2VNA"); save();
  await screen.findByText("Запись сохранена");
  expect(writes(requests)[0].body).toEqual({ version: 7, vna_app: "S2VNA" });
  expect(screen.getByLabelText("Программа ВАЦ")).toHaveValue("S2VNA");
});

it("keeps a configured application editable even if the instrument was renamed", async () => {
  await setup([{ ...instrument, name: "Прибор", instrument_type: "Прибор", vna_app: "Arinst VL" }]); open();
  expect(screen.getByLabelText("Программа ВАЦ")).toHaveValue("Arinst VL");
});

it("hides VNA configuration for ordinary SI and accessories", async () => {
  await setup([{ ...instrument, name: "Мультиметр", instrument_type: "Мультиметр" }, accessory]); open();
  expect(screen.queryByLabelText("Программа ВАЦ")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Закрыть" })); open(accessory.inventory_number);
  expect(screen.queryByLabelText("Программа ВАЦ")).not.toBeInTheDocument();
});

it("keeps edits on 409, blocks another save, and explicitly reloads the latest version", async () => {
  const { requests } = await setup([instrument], ({ method }) => method === "PATCH" ? json({ detail: "Version mismatch" }, 409) : null);
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
  const { requests } = await setup([unverified]); open(unverified.inventory_number);
  fireEvent.click(screen.getByRole("button", { name: "Добавить поверку" }));
  fill("№ свидетельства о поверке", "П-101"); fill("Поверено", "2026-11-01"); fill("Поверка до", "2027-11-01"); save();
  await waitFor(() => expect(writes(requests)).toHaveLength(1));
  expect(writes(requests)[0].body).toMatchObject({ version: 7, verification_number: "П-101", verified_at: "2026-11-01", verification_due: "2027-11-01", status: "Действует" });
});

it("honors an inactive status selected after starting verification", async () => {
  const { requests } = await setup([unverified]); open(unverified.inventory_number);
  fireEvent.click(screen.getByRole("button", { name: "Добавить поверку" }));
  fill("Статус", "Не используется"); save();
  await waitFor(() => expect(writes(requests)).toHaveLength(1));
  expect(writes(requests)[0].body).toEqual({ version: 7, status: "Не используется" });
});

it("rejects an active SI without verification and prevents cyclical parent selection", async () => {
  const { requests } = await setup([instrument, kit, unverified]); open(unverified.inventory_number);
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
  const { requests } = await setup(); open();
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
  const { requests, onChanged } = await setup([instrument, stockAccessory], ({ path }) => path.endsWith("/return-to-stock")
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
  const { requests } = await setup([instrument, stockAccessory], ({ path }) => path.endsWith("/return-to-stock") ? json({ detail: "Конфликт" }, 409) : null);
  open(stockAccessory.inventory_number); fill("Наименование", "Мои изменения");
  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Вернуть на склад" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Запись изменил другой пользователь — обновите");
  expect(screen.getByLabelText("Наименование")).toHaveValue("Мои изменения");
  expect(screen.getByRole("button", { name: "Вернуть на склад" })).toBeDisabled();
  expect(writes(requests)).toHaveLength(1);
});

it.each([accessory, { ...accessory, warehouse_writeoff_id: "writeoff", status: "Не используется" as const }, instrument])(
  "hides return-to-stock when the record is ineligible: $kind / $status", async (item) => {
    await setup([item]); open(item.inventory_number);
    expect(screen.queryByRole("button", { name: "Вернуть на склад" })).not.toBeInTheDocument();
  },
);

it("uploads a passport as base64, displays its link and metadata, and deletes only after confirmation", async () => {
  const { requests } = await setup([unverified]); open(unverified.inventory_number);
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

it("marks one .s2p measurement as the reference curve, asking before replacing the current one", async () => {
  const curve = (id: string, name: string, is_reference = false) => ({ id, kind: "Прочее" as const, file_name: name, size: 9, uploaded_at: "2026-11-16", uploaded_by_name: "Иванов", is_reference });
  let files = [curve("old", "Замер ВАЦ 16.11.2026, СИ.005.s2p", true), curve("new", "Замер ВАЦ 18.11.2026, СИ.005.s2p"), curve("pdf", "паспорт.pdf")];
  const { requests } = await setup([{ ...unverified, files }], ({ path, method }) => {
    if (method === "POST" && path.endsWith("/reference")) {
      files = files.map((file) => ({ ...file, is_reference: path.includes("/files/new/") ? file.id === "new" : file.is_reference }));
      return json({ file: files[1] });
    }
    if (method === "GET" && path === base) return json({ instruments: [{ ...unverified, files }] });
    return null;
  });
  open(unverified.inventory_number);
  expect(screen.getByText("Эталонная кривая")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Сделать эталонной кривой: паспорт.pdf" })).not.toBeInTheDocument();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(screen.getByRole("button", { name: "Сделать эталонной кривой: Замер ВАЦ 18.11.2026, СИ.005.s2p" }));
  expect(writes(requests)).toHaveLength(0);
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Сделать эталонной кривой: Замер ВАЦ 18.11.2026, СИ.005.s2p" }));
  expect(await screen.findByText("Эталонная кривая назначена")).toBeInTheDocument();
  expect(writes(requests)).toEqual([{ path: `${base}/ruler/files/new/reference`, method: "POST", body: {} }]);
  await waitFor(() => expect(screen.getByRole("button", { name: "Сделать эталонной кривой: Замер ВАЦ 16.11.2026, СИ.005.s2p" })).toBeInTheDocument());
});

it("shows passport presence and file counts in the registry", async () => {
  await setup([{ ...instrument, files: [{ id: "p", kind: "Паспорт", file_name: "паспорт.pdf", size: 12, uploaded_at: "2026-11-12", uploaded_by_name: "Иванов" }] }]);
  expect(screen.getByText("1 · паспорт")).toBeInTheDocument();
});

it("hides the parent column in tree view and restores it in list view", async () => {
  await setup([instrument, accessory]);
  expect(screen.getByRole("columnheader", { name: "Прибор" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "По приборам" }));
  expect(screen.queryByRole("columnheader", { name: "Прибор" })).not.toBeInTheDocument();
  expect(screen.getByText("Переходник", { exact: false, selector: ".instrument-name" })).toHaveStyle({ paddingLeft: "16px" });
  fireEvent.click(screen.getByRole("button", { name: "Списком" }));
  expect(screen.getByRole("columnheader", { name: "Прибор" })).toBeInTheDocument();
});

it("keeps full kind and status values in filters while showing compact labels", async () => {
  await setup([instrument, accessory, unverified]);
  expect(screen.getByText("Принадл.")).toHaveAttribute("title", "Принадлежность");
  fireEvent.click(screen.getByRole("button", { name: "Фильтры" }));
  const kind = screen.getByRole("combobox", { name: "Фильтр: Вид" });
  expect(within(kind).getByRole("option", { name: "Средство измерений" })).toBeInTheDocument();
  expect(within(kind).getByRole("option", { name: "Принадлежность" })).toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox", { name: "Фильтр: Статус" }), { target: { value: "Поверка не подтверждена" } });
  expect(inventoryOrder()).toEqual([unverified.inventory_number]);
});

it("hides designation by default but exports it with full kind and status labels", async () => {
  let blob: Blob | null = null;
  vi.spyOn(URL, "createObjectURL").mockImplementation((value) => { blob = value as Blob; return "blob:instruments"; });
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  await setup([unverified]);
  expect(screen.queryByRole("columnheader", { name: "Обозн." })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Excel" }));
  const text = new TextDecoder("utf-16le").decode(await blob!.arrayBuffer());
  expect(text).toContain("Обозн.");
  expect(text).toContain("ВАЦ-01");
  expect(text).toContain("Средство измерений");
  expect(text).toContain("Поверка не подтверждена");
  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  expect(screen.getByRole("checkbox", { name: "Обозн." })).not.toBeChecked();
  fireEvent.click(screen.getByRole("checkbox", { name: "Обозн." }));
  expect(screen.getByRole("columnheader", { name: "Обозн." })).toBeInTheDocument();
});

it("shows an em dash for no files and the count with passport presence", async () => {
  await setup([instrument, { ...accessory, files: [
    { id: "p", kind: "Паспорт", file_name: "паспорт.pdf", size: 12, uploaded_at: "2026-11-12", uploaded_by_name: "Иванов" },
    { id: "s", kind: "Прочее", file_name: "схема.pdf", size: 12, uploaded_at: "2026-11-12", uploaded_by_name: "Иванов" },
  ] }]);
  const rows = screen.getAllByRole("row").slice(1);
  expect(rows[0].querySelector('td[data-label="Файлы"]')).toHaveTextContent("—");
  expect(rows[1].querySelector('td[data-label="Файлы"]')).toHaveTextContent("2 · паспорт");
});

it("writes off an instrument only after confirmation using its version", async () => {
  const { requests } = await setup(); open();
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
  const { requests } = await setup(); const dialog = await pickLot();
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
  const { requests } = await setup([instrument], ({ path }) => path.endsWith("/from-stock") ? json({ detail: "Устаревшая партия" }, 409) : null);
  const dialog = await pickLot(); vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(within(dialog).getByRole("button", { name: "Передать со склада" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("Запись изменил другой пользователь — обновите");
  expect(within(dialog).queryByRole("button", { name: "Передать со склада" })).not.toBeInTheDocument();
  expect(within(dialog).queryByText("ГРН-126")).not.toBeInTheDocument();
  expect(writes(requests)).toHaveLength(1);
});

it("blocks a second stock write after an ambiguous transport failure", async () => {
  const { requests } = await setup([instrument], ({ path }) => { if (path.endsWith("/from-stock")) throw new TypeError("Connection lost"); return null; });
  const dialog = await pickLot(); vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(within(dialog).getByRole("button", { name: "Передать со склада" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("Результат неизвестен");
  expect(within(dialog).getByRole("button", { name: "Передать со склада" })).toBeDisabled();
  fireEvent.click(within(dialog).getByRole("button", { name: "Передать со склада" }));
  expect(writes(requests)).toHaveLength(1);
});

it("opens a card with Enter and closes only the top stock window with Escape", async () => {
  await setup();
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

it("opens the instrument editor in a modal and lists verification dates without time", async () => {
  await setup([instrument]);
  const table = screen.getByRole("heading", { name: "Средства измерений" }).closest(".panel") as HTMLElement;
  expect(within(table).getByRole("columnheader", { name: /Поверка до/ })).toBeInTheDocument();
  expect(within(table).getByText("01.01.2027")).toBeInTheDocument();
  expect(within(table).queryByText(/09:30/)).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  open();
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(screen.getByLabelText("Инв. №")).toHaveValue(instrument.inventory_number);
});
