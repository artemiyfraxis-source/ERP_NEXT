import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { AccessMode, Principal } from "./api";
import type { MachineCard, MachineRow, UnitSetup } from "./machinesApi";
import { MachinesPage, shortOperations } from "./MachinesPage";
import { manualJobsApi } from "./manualJobsApi";
import { visibleNav } from "./navigation";
import { machineStatus, Ob019CardView } from "./Ob019Card";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const OB019: MachineRow = {
  key: "machine:u19", kind: "machine", equipment_id: "u19", inventory: "ОБ.019",
  name: "Автомат резки и зачистки провода с лазерной маркировкой", pc: "PC-108", ip: "192.168.1.108",
  service: "Агент ERP · станок", version: "1.0.0", link: { code: "ok", text: "На связи", tone: "ok" },
  last_seen: "2026-11-18T10:00:00+03:00", device: "ПЛК линии резки", state: { text: "Аварийный стоп", tone: "danger" }, card: true,
  others: "", article: "EW-3185+L", site: "ЦАР", operations: ["Резка", "Резка с разделкой"], family: "Резка",
  controlled: true, setup: { code: "full", label: "полная" }, pending_setup: 1,
};
const TSC: MachineRow = {
  key: "printer:pc:tsc", kind: "printer", equipment_id: null, inventory: "", name: "Принтер этикеток", pc: "Склад",
  ip: "192.168.1.50", service: "Агент ERP · печать", version: "2.9.1",
  link: { code: "stale", text: "Нет связи с ПК", tone: "danger" }, last_seen: null, device: "TSC MB341",
  state: { text: "Принтер не найден", tone: "warn" }, card: false, others: "", article: "", site: "", operations: [],
  family: "Принтеры и МФУ", controlled: true,
};
const PLANAR: MachineRow = {
  key: "device:instrument:si2", kind: "device", equipment_id: null, inventory: "СИ.002", name: "Planar S50180", pc: "DESKTOP-PTA57QF",
  ip: "192.168.1.121", service: "Агент ERP · печать", version: "2.9.4", link: { code: "ok", text: "На связи", tone: "ok" },
  last_seen: "2026-11-18T10:00:00+03:00", device: "Planar, S50180, 26030095, 26.2.2/2", state: { text: "Подключён", tone: "ok" },
  card: false, others: "DESKTOP-5O46RH6 — не найден", article: "VNA S50180", site: "", operations: [],
  family: "Анализаторы цепей", controlled: true,
};

function card(overrides: Partial<MachineCard> = {}): MachineCard {
  return {
    equipment_id: "u19", inventory: "ОБ.019", name: OB019.name, pc: "PC-108", ip: "192.168.1.108",
    service: "Агент ERP · станок", service_version: "mbd 1", bridge_version: "1.0.0",
    link: { code: "ok", text: "На связи", tone: "ok" }, reported_at: "2026-11-18T10:00:00+03:00", age_seconds: 2, error: "",
    live: {
      state: { code: 105, text: "Аварийный стоп", tone: "danger" }, alarm: { code: 0, text: "Аварий нет", tone: "neutral" },
      mode: { code: 2, text: "Автомат" },
      job: { total_set: 300, total_finished: 12, batch_set: 10, batch_finished: 2, uph: 3621, program: 3, order_quantity: null,
        order_finished: null, order_number: "ED F", product: null, order_running: true },
      wire: { length_mm: 325, strip_front_mm: 5, pull_front_mm: 0, strip_back_mm: 5, pull_back_mm: 0, diameter_mm: 0.35, knife_back_mm: 0.1 },
    },
    ...overrides,
  };
}

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); window.history.replaceState({}, "", "/"); });

const NOT_INSTALLED: MachineRow = {
  key: "machine:u37", kind: "machine", equipment_id: null, inventory: "ОБ.037", name: "Автомат резки BZW-880", pc: "", ip: "",
  service: "Агент ERP · станок", version: "", link: { code: "not_installed", text: "Агент не установлен", tone: "neutral" },
  last_seen: null, device: "ПЛК линии резки", state: { text: "—", tone: "neutral" }, card: false, others: "",
  article: "BZW-880", site: "ЦАР", operations: ["Резка с разделкой"], family: "Резка", controlled: false,
  setup: { code: null, label: "не задано" }, pending_setup: 0,
};
const TWISTER: MachineRow = {
  key: "machine:u05", kind: "machine", equipment_id: "u05", inventory: "ОБ.005", name: "Полуавтомат для скрутки проводов", pc: "", ip: "",
  service: "", version: "", link: { code: "not_connected", text: "Нет подключения", tone: "neutral" }, last_seen: null, device: "",
  state: { text: "—", tone: "neutral" }, card: false, others: "", article: "SR-W01", site: "ЖУК", operations: ["Свивка"],
  family: "Зачистка", controlled: false, setup: { code: "simple", label: "простая" }, pending_setup: 0,
};
const SOLDER: MachineRow = {
  ...TWISTER, key: "machine:u70", equipment_id: "u70", inventory: "ОБ.070.1", name: "Станция паяльная термовоздушная", article: "8620DX-Pro",
  site: "ЦАР", operations: ["Пайка", "Усадка"], family: "Пайка и усадка", setup: { code: "none", label: "нет" },
};
const FAMILIES = ["Резка", "Резка и опрессовка", "Зачистка", "Опрессовка", "Пайка и усадка", "Маркировка", "Анализаторы цепей",
  "Принтеры и МФУ", "Прочее"];
const SETUP: UnitSetup = {
  unit_id: "u05", inventory: "ОБ.005", name: TWISTER.name, model: "SR-W01", requires_setup: { code: "simple", label: "простая" }, reason: "",
  parameters: [{ key: "twist_pitch_mm", label: "Шаг скрутки", unit: "мм", source: "ЭТК", source_field: "Шаг", register: "" }],
  configuration: { values: { twist_pitch_mm: "20" }, wire: "", contact: "", set_by: "Слава", set_at: "2026-11-18T09:00:00+03:00", version: 3 },
  references: [], history: [], online: false,
  open_tasks: [{ id: "t1", version: 1, status: "Назначена", operation: "П-104 · Жгут · Свивка · ЭТК 4", planned_start: "2026-11-19T08:00:00+03:00", planned_finish: null, mine: true, can_confirm: true },
    { id: "t2", version: 2, status: "В работе", operation: "П-105 · Жгут · Свивка · ЭТК 1", planned_start: null, planned_finish: null, mine: false, can_confirm: false }],
};

function machines(calls: string[] = [], rows = [OB019, NOT_INSTALLED, TWISTER, SOLDER, TSC, PLANAR]) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = decodeURIComponent(String(input));
    calls.push(url);
    if (url.endsWith("/u19")) return json(card());
    if (url.endsWith("/control")) return json({ problem: "", state: 105, commands: [], offsets: [], can_edit: false });
    if (url.endsWith("/transfer")) return json({ choices: [] });
    if (url.endsWith("/card/ОБ.019")) return json({ inventory: "ОБ.019", settings: [{ title: "Провод и зачистка", fields: [
      { label: "Длина провода", value: 325, unit: "мм", read: true, key: "length_mm", kind: "u32" }] }] });
    if (url.includes("/setup-model/")) return json(SETUP);
    return json({ now: "2026-11-18T10:00:02+03:00", rows, families: FAMILIES });
  });
}

it("keeps the same seven tile slots for machines, printers and analysers with missing data", async () => {
  const emptyAnalyser: MachineRow = { ...PLANAR, key: "device:empty", inventory: "СИ.003", name: "", article: "",
    site: "", operations: [], device: "", state: { text: "—", tone: "neutral" }, pc: "", ip: "", last_seen: null };
  machines([], [OB019, NOT_INSTALLED, TWISTER, TSC, PLANAR, emptyAnalyser]);
  const { container } = render(<MachinesPage />);
  await screen.findByText("СИ.003");
  const tiles = Array.from(container.querySelectorAll<HTMLButtonElement>(".machine-tile"));
  expect(tiles).toHaveLength(6);
  for (const tile of tiles) {
    expect(Array.from(tile.children, (slot) => slot.classList[0])).toEqual([
      "machine-tile-head", "machine-tile-name", "machine-tile-article", "machine-tile-ops",
      "machine-tile-device", "machine-tile-badges", "machine-tile-foot",
    ]);
    expect(tile.type).toBe("button");
    expect(tile).not.toBeDisabled();
  }
  const emptyTile = screen.getByText("СИ.003").closest("button")!;
  expect(emptyTile.querySelector(".machine-tile-name")).toHaveTextContent("—");
  expect(emptyTile.querySelector(".machine-tile-article")).toBeEmptyDOMElement();
  expect(emptyTile.querySelector(".machine-tile-ops")).toBeEmptyDOMElement();
  expect(emptyTile.querySelector(".machine-tile-device")).toBeEmptyDOMElement();
  expect(emptyTile.querySelector(".machine-tile-badges")).toBeEmptyDOMElement();
  expect(emptyTile.querySelector(".machine-tile-foot")).toHaveTextContent("ПК не известен");
  expect(emptyTile.querySelector(".machine-tile-foot")?.children).toHaveLength(1);
  const machineTile = screen.getByText("ОБ.019").closest("button")!;
  expect(machineTile.querySelector(".machine-tile-foot")?.firstElementChild).toHaveTextContent("PC-108 · 192.168.1.108");
  expect(machineTile.querySelector(".machine-tile-seen")).toBeTruthy();
  expect(screen.getByText("Нет агента")).toBeTruthy();
  const printer = container.querySelector<HTMLButtonElement>(".machine-group[aria-label='Принтеры и МФУ'] .machine-tile")!;
  expect(printer.querySelector(".machine-tile-article")).toHaveTextContent("TSC MB341");
  expect(within(printer).getByText("Принтер не найден")).toHaveClass("tone-warn");
  expect(within(screen.getByText("СИ.002").closest("button")!).getByText("Подключён")).toHaveClass("tone-ok");
  expect(within(screen.getByText("ОБ.019").closest("button")!).getByText("Аварийный стоп")).toHaveClass("tone-danger");
});

it("uses a short device kind in the header and always keeps its full name below", async () => {
  const labels: MachineRow = { ...TSC, key: "printer:labels", name: "Принтер этикеток TSC MB341", station: { kind: "labels" } };
  const mfp: MachineRow = { ...TSC, key: "device:mfp", kind: "device", name: "МФУ Pantum BM2300",
    station: { kind: "mfp", machine_id: "mfp", scanners: ["scan"], printers: ["print"] } };
  const scanner: MachineRow = { ...mfp, key: "device:scanner", name: "Сканер Pantum BM2300",
    station: { kind: "mfp", machine_id: "scan", scanners: ["scan"], printers: [] } };
  const documentPrinter: MachineRow = { ...mfp, key: "printer:document", kind: "printer", name: "Принтер документов Pantum BM2300",
    station: { kind: "mfp", machine_id: "print", scanners: [], printers: ["print"] } };
  machines([], [labels, mfp, scanner, documentPrinter]);
  render(<MachinesPage />);
  await screen.findByText(mfp.name);
  for (const [row, kind] of [[labels, "Принтер этикеток"], [mfp, "МФУ"], [scanner, "Сканер"],
    [documentPrinter, "Принтер документов"]] as const) {
    const tile = screen.getByText(row.name).closest("button")!;
    expect(tile.querySelector(".machine-tile-no")).toHaveTextContent(kind);
    expect(tile.querySelector(".machine-tile-name")).toHaveTextContent(row.name);
  }
  expect(screen.queryByText("Без номера")).toBeNull();
});

it("shows one list grouped by purpose; the link is a badge; «Только подключённые» filters", async () => {
  const calls: string[] = [];
  machines(calls);
  render(<MachinesPage />);
  expect(await screen.findByRole("region", { name: "Резка" })).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Запросы на наладку" })).toBeNull();
  expect(screen.getByRole("tab", { name: "Журнал работ" })).toBeVisible();
  expect(screen.queryByText(/С управлением|Без управления/)).toBeNull();
  // Groups follow the order of the API's families; an empty group is not drawn.
  expect(screen.getAllByRole("region").map((region) => region.getAttribute("aria-label"))).toEqual(
    ["Резка", "Зачистка", "Пайка и усадка", "Анализаторы цепей", "Принтеры и МФУ"]);
  // ОБ.019 — in its group, with the article, the site, the operations and «Ждёт наладки».
  const cutting = screen.getByRole("region", { name: "Резка" });
  const tile = within(cutting).getByText("ОБ.019").closest("button")!;
  expect(within(tile).getByText("EW-3185+L · ЦАР")).toBeTruthy();
  // Short capabilities under «Резка»; the full operation names stay in the tooltip and the search.
  expect(within(tile).getByText("Резка + разделка").getAttribute("title")).toBe("Резка, Резка с разделкой");
  expect(within(tile).getByText("Ждёт наладки").className).toContain("tone-warn");
  expect(within(tile).getByText("Наладка: полная")).toBeTruthy();
  expect(within(tile).getByText("Аварийный стоп").className).toContain("tone-danger");
  // The connection stays a badge on the tile, also for equipment without control.
  const absent = screen.getByText("ОБ.037").closest("button")!;
  expect(absent.className).toContain("is-absent");
  const twister = screen.getByText("ОБ.005").closest("button")!;
  expect(within(twister).getByText("Нет подключения")).toBeTruthy();
  expect(within(twister).getByText("SR-W01 · ЖУК")).toBeTruthy();
  expect(within(screen.getByText("ОБ.070.1").closest("button")!).getByText("Наладка: нет")).toBeTruthy();
  const printers = screen.getByRole("region", { name: "Принтеры и МФУ" });
  const devices = screen.getByRole("region", { name: "Анализаторы цепей" });
  expect(within(printers).getByText("Нет связи с ПК").className).toContain("tone-danger");
  expect(within(devices).getAllByText("СИ.002")).toHaveLength(1);

  const only = screen.getByRole("checkbox", { name: /Только подключённые/ });
  expect(only.closest("label")!.textContent).toBe("Только подключённые2");  // ОБ.019 и Planar; TSC — нет связи с ПК
  fireEvent.click(only);
  expect(screen.queryByText("ОБ.005")).toBeNull();
  expect(screen.queryByText("ОБ.037")).toBeNull();
  expect(screen.getByText("ОБ.019")).toBeTruthy();
  expect(screen.queryByRole("region", { name: "Зачистка" })).toBeNull();
  fireEvent.click(only);
  expect(screen.getByText("ОБ.005")).toBeTruthy();

  fireEvent.click(within(devices).getByText("СИ.002"));
  const info = await screen.findByRole("dialog", { name: /СИ\.002/ });
  expect(within(info).getByText("DESKTOP-5O46RH6 — не найден")).toBeTruthy();
  expect(within(info).getByText("VNA S50180")).toBeTruthy();
  fireEvent.click(within(info).getByRole("button", { name: "Закрыть" }));
  expect(calls.some((url) => url.includes("/setup-model/"))).toBe(false);

  fireEvent.click(screen.getByText("ОБ.019"));
  const dialog = await screen.findByRole("dialog", { name: /ОБ\.019/ });
  // The drawing shows what is on the machine now.
  await waitFor(() => expect(within(dialog).getByLabelText("Длина провода")).toHaveValue("325,00"));
  expect(within(dialog).getByText("3 621 шт/ч")).toBeTruthy();
  expect(calls).toContain("/api/production/machines/u19");
  expect(within(dialog).queryByRole("button", { name: "Наладка · ждёт" })).toBeNull();
});

it("«Только подключённые» keeps only what is on line now, not everything with an agent", async () => {
  const stale: MachineRow = { ...OB019, key: "machine:u94", equipment_id: "u94", inventory: "ОБ.094", card: false,
    link: { code: "stale", text: "Нет связи с ПК станка", tone: "danger" }, state: { text: "—", tone: "neutral" } };
  const lost: MachineRow = { ...TSC, key: "printer:pc:lost", device: "Godex G500", pc: "Маркировка",
    link: { code: "ok", text: "На связи", tone: "ok" }, state: { text: "Принтер не найден", tone: "warn" } };
  const seen: MachineRow = { ...TSC, key: "printer:pc:seen", device: "TSC TE200", pc: "Склад-2",
    link: { code: "ok", text: "На связи", tone: "ok" }, state: { text: "Принтер виден", tone: "ok" } };
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    json({ now: "2026-11-18T10:00:02+03:00", rows: [OB019, stale, lost, seen, PLANAR], families: FAMILIES }));
  render(<MachinesPage />);
  const only = await screen.findByRole("checkbox", { name: /Только подключённые/ });
  expect(only.closest("label")!.textContent).toBe("Только подключённые3");
  fireEvent.click(only);
  expect(screen.getByText("ОБ.019")).toBeTruthy();
  expect(screen.queryByText("ОБ.094")).toBeNull();  // мост станка молчит
  expect(screen.queryByText("Godex G500")).toBeNull();  // агент на связи, а принтера не видно
  expect(screen.getByText("Склад-2", { exact: false })).toBeTruthy();
  expect(screen.getByText("СИ.002")).toBeTruthy();
});

it("a row whose group the API does not list stands in its own group before «Прочее»", async () => {
  const odd: MachineRow = { ...TWISTER, key: "machine:u12", equipment_id: "u12", inventory: "ОБ.012", family: "Плазма" };
  const other: MachineRow = { ...TWISTER, key: "machine:u13", equipment_id: "u13", inventory: "ОБ.013", family: "Прочее" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    json({ now: "2026-11-18T10:00:02+03:00", rows: [other, odd, OB019], families: FAMILIES }));
  render(<MachinesPage />);
  expect(await screen.findByRole("region", { name: "Плазма" })).toBeTruthy();
  expect(screen.getAllByRole("region").map((region) => region.getAttribute("aria-label"))).toEqual(["Резка", "Плазма", "Прочее"]);
  expect(within(screen.getByRole("region", { name: "Плазма" })).getByText("ОБ.012")).toBeTruthy();
});

it("hides site switch when machines are on one site and shows devices", async () => {
  machines([], [OB019, NOT_INSTALLED, TSC, PLANAR]);
  render(<MachinesPage />);
  expect(await screen.findByText("ОБ.019")).toBeTruthy();
  expect(screen.queryByRole("radiogroup", { name: "Участок" })).toBeNull();
  expect(screen.getByRole("region", { name: "Принтеры и МФУ" })).toBeTruthy();
  expect(screen.getByRole("region", { name: "Анализаторы цепей" })).toBeTruthy();
});

it("filters by site and searches by number, name, article, PC or IP", async () => {
  machines();
  render(<MachinesPage />);
  const zhuk = await screen.findByRole("radio", { name: /ЖУК/ });
  expect(zhuk.textContent).toBe("ЖУК1");
  fireEvent.click(zhuk);
  expect(screen.getByText("ОБ.005")).toBeTruthy();
  expect(screen.queryByText("ОБ.037")).toBeNull();
  expect(screen.queryByRole("region", { name: "Принтеры и МФУ" })).toBeNull();
  expect(screen.queryByRole("region", { name: "Анализаторы цепей" })).toBeNull();
  fireEvent.click(screen.getByRole("radio", { name: /Все участки/ }));
  expect(screen.getByRole("region", { name: "Принтеры и МФУ" })).toBeTruthy();
  expect(screen.getByRole("region", { name: "Анализаторы цепей" })).toBeTruthy();
  const search = screen.getByRole("searchbox", { name: "Поиск оборудования" });
  fireEvent.change(search, { target: { value: "8620dx" } });
  expect(screen.getByText("ОБ.070.1")).toBeTruthy();
  expect(screen.queryByText("ОБ.005")).toBeNull();
  fireEvent.change(search, { target: { value: "pc-108" } });
  expect(screen.getByText("ОБ.019")).toBeTruthy();
  fireEvent.change(search, { target: { value: "нет такого" } });
  expect(screen.getByText("Ничего не найдено по «нет такого»")).toBeTruthy();
});

it("records the machine setup by the task from its card, by hand only with a reason", async () => {
  const posts: { url: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = decodeURIComponent(String(input));
    if (init?.method === "POST") { posts.push({ url, body: JSON.parse(String(init.body)) }); return json({ ok: true, version: 4, values: {} }); }
    if (url.includes("/setup-model/")) return json(SETUP);
    if (url.includes("/machine-queue")) return json({ equipment_id: "u05", inventory: "ОБ.005", version: "v", rows: [] });
    if (url.includes("/cutting-queue")) return json({ machine_id: null, version: "v", rows: [] });
    if (url.includes("/manual-jobs/options")) return json({ machines: [{ id: "u05", inventory: "ОБ.005", name: "Станок", running: null }], wires: [], contacts: [], references: [], can_edit: true });
    if (url.includes("/manual-jobs")) return json({ date_from: "", date_to: "", rows: [], can_edit: true, downtimes: [] });
    return json({ now: "", rows: [TWISTER], families: FAMILIES });
  });
  render(<MachinesPage canWrite />);
  fireEvent.click(await screen.findByText("ОБ.005"));
  // A machine without control opens its work card; «Наладка» (the setup table) is a window over it.
  const card = await screen.findByRole("dialog", { name: /ОБ\.005/ });
  fireEvent.click(within(card).getByRole("button", { name: "Наладка" }));
  const dialog = await screen.findByRole("dialog", { name: "Наладка ОБ.005" });
  const part = await within(dialog).findByRole("region", { name: "Наладка" });
  expect(await within(part).findByText("Требует наладки: простая")).toBeTruthy();
  expect(within(part).getByRole("radio", { name: /П-104 · Жгут · Свивка · ЭТК 4/ })).toHaveProperty("checked", true);
  const pitch = within(part).getByRole("textbox", { name: "Шаг скрутки, настроено" }) as HTMLInputElement;
  expect(pitch.value).toBe("20");
  fireEvent.change(pitch, { target: { value: "22" } });
  fireEvent.click(within(part).getByRole("button", { name: "Настроено по заданию" }));
  expect(await within(part).findByText("Настройка записана по заданию — задание наладки готово")).toBeTruthy();
  expect(posts[0]).toEqual({ url: "/api/production/setup-model/ОБ.005/configuration",
    body: { expected_version: 3, task_id: "t1", task_version: 1, values: { twist_pitch_mm: "22" }, reason: "" } });
  // Another technician's task is visible but «Настроено по заданию» is not theirs to press.
  expect(within(part).getByRole("radio", { name: /П-105/ })).toHaveProperty("disabled", true);
  fireEvent.click(within(part).getByRole("radio", { name: /Без задания/ }));
  const byHand = within(part).getByRole("button", { name: "Настроено вручную" }) as HTMLButtonElement;
  expect(byHand.disabled).toBe(true);
  fireEvent.change(within(part).getByRole("textbox", { name: "Причина" }), { target: { value: "смена ролика" } });
  expect(byHand.disabled).toBe(false);
});

it("shows an honest empty text, and another one when the filter hides everything", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ now: "", rows: [] }));
  render(<MachinesPage />);
  expect(await screen.findByText(/отметьте станок в БД\.ОБ галочкой/)).toBeTruthy();
  fireEvent.click(screen.getByRole("checkbox", { name: /Только подключённые/ }));
  expect(screen.getByText(/Нет подключённого оборудования/)).toBeTruthy();
});

it("polls every 3 seconds only while the tab is visible", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ now: "", rows: [OB019] }));
  render(<MachinesPage />);
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(fetch).toHaveBeenCalledTimes(2);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  document.dispatchEvent(new Event("visibilitychange"));
  await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
  expect(fetch).toHaveBeenCalledTimes(2);
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  document.dispatchEvent(new Event("visibilitychange"));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3));
});

it("keeps the last PLC values greyed when the machine PC is gone and says so once, in one line", () => {
  const { container } = render(<Ob019CardView card={card({
    link: { code: "stale", text: "Нет связи с ПК станка", tone: "danger" }, age_seconds: 75 })} />);
  const status = screen.getByRole("status", { name: "Состояние станка" });
  expect(status.className).toContain("is-danger");
  expect(within(status).getByText("Нет связи с ПК станка")).toBeTruthy();
  expect(within(status).getByText(/1 мин назад/)).toBeTruthy();
  expect(screen.getAllByText("Нет связи с ПК станка")).toHaveLength(1);
  expect(container.querySelector(".machine-old")).not.toBeNull();
  expect(screen.queryByText(/на станке —/)).toBeNull();
});

it("names the open operator panel in operator words instead of the agent's raw error, without an empty panel", () => {
  const text = "Открыт пульт станка на ПК — закройте его, чтобы ERP могла управлять станком";
  render(<Ob019CardView card={card({ link: { code: "port_busy", text, tone: "warn" },
    error: "mbd: busy: PXS_HMI is running, bus refused", live: null })} />);
  const status = screen.getByRole("status", { name: "Состояние станка" });
  expect(status.className).toContain("is-warn");
  expect(within(status).getByText(text)).toBeTruthy();
  expect(screen.getAllByText(text)).toHaveLength(1);
  expect(screen.queryByText(/Нет данных ПЛК/)).toBeNull();
  // An untranslated raw agent line is the same cause: not repeated in the folded «Подключение».
  expect(screen.queryByText(/PXS_HMI|busy/)).toBeNull();
});

it("keeps PC, agent, link and an extra agent answer in the status line without a connection fold", () => {
  render(<Ob019CardView card={card({ link: { code: "no_plc", text: "ПЛК не отвечает", tone: "danger" }, error: "COM8 занят", live: null })} />);
  expect(within(screen.getByRole("status", { name: "Состояние станка" })).getByText("ПЛК не отвечает")).toBeTruthy();
  const status = screen.getByRole("status", { name: "Состояние станка" });
  expect(status).toHaveTextContent("ПК: PC-108 · агент: 1.0.0");
  expect(status).toHaveTextContent("COM8 занят");
  expect(screen.queryByText("Подключение")).toBeNull();
});

it("keeps only journal and close in the machine header", async () => {
  machines();
  vi.spyOn(manualJobsApi, "list").mockResolvedValue({ date_from: "", date_to: "", rows: [], can_edit: false, waiting_count: 0 });
  globalThis.window.history.replaceState({}, "", "/?section=machines&equipment=u19");
  const { container } = render(<MachinesPage />);
  await screen.findByRole("button", { name: "Журнал" });
  expect(screen.getAllByRole("button", { name: "Журнал" })).toHaveLength(1);
  expect(container.querySelector(".machine-connection, .machine-journal, .machine-more")).toBeNull();
  expect(screen.queryByText(/Остальные параметры/)).toBeNull();
  const journal = screen.getByRole("button", { name: "Журнал" });
  expect(journal.closest("header")).not.toBeNull();
  fireEvent.click(journal);
  const window = await screen.findByRole("dialog", { name: "Журнал станка" });
  expect(await within(window).findByText("Записей пока нет")).toBeInTheDocument();
  expect(within(window).getAllByRole("region", { name: "Журнал работ" })[0]).toBeVisible();
  await waitFor(() => expect(manualJobsApi.list).toHaveBeenCalledWith(
    expect.any(String), expect.any(String), "u19", expect.any(AbortSignal), false));
  fireEvent.keyDown(window, { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "Журнал станка" })).toBeNull();
  expect(within(journal.closest("header")!).getAllByRole("button").map((button) => button.textContent)).toEqual(["Журнал", "Закрыть"]);
  expect(screen.queryByRole("button", { name: "Все параметры" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Ручная работа" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Наладка" })).toBeNull();
  expect(screen.queryByRole("dialog", { name: "Все параметры" })).toBeNull();
});

it("says the state, the link and the next step once at the top; an alarm leads", () => {
  const { rerender } = render(<Ob019CardView card={card()} />);
  let status = screen.getByRole("status", { name: "Состояние станка" });
  expect(within(status).getByText("Аварийный стоп")).toBeTruthy();
  expect(within(status).getByText(/На связи · сигнал/)).toBeTruthy();
  expect(within(status).getByText("Отпустите аварийную кнопку на станке и нажмите «Сброс»")).toBeTruthy();
  expect(within(status).getByText("Аварийная остановка — кнопкой на станке")).toBeTruthy();
  const running = card();
  running.live = { ...running.live!, state: { code: 103, text: "Работает", tone: "ok" } };
  rerender(<Ob019CardView card={running} />);
  status = screen.getByRole("status", { name: "Состояние станка" });
  expect(within(status).getByText("Режет 12 из 300")).toBeTruthy();
  expect(status.className).not.toContain("is-");
  const alarm = card();
  alarm.live = { ...alarm.live!, alarm: { code: 6, text: "Нет провода", tone: "danger" } };
  rerender(<Ob019CardView card={alarm} />);
  expect(within(screen.getByRole("status", { name: "Состояние станка" })).getByText("Авария: Нет провода")).toBeTruthy();
});

it("says once, in plain words, that the operator panel is open on the PC (the agent's raw line is translated)", () => {
  const text = "Открыт пульт станка на ПК — закройте его, чтобы ERP могла управлять станком";
  render(<Ob019CardView card={card({ link: { code: "port_busy", text, tone: "warn" }, error: text, live: null })} />);
  expect(screen.getAllByText(text)).toHaveLength(1);
  expect(screen.queryByText(/PXS_HMI|busy/)).toBeNull();
});

it("opens «Оборудование» for Производство or Задания view", () => {
  const user = { id: "1", email: "u@example.test", name: "Сотрудник", department: "Цех" };
  const as = (permissions: Record<string, AccessMode>): Principal => ({ user, roles: ["operator"], access_level: "operator", permissions });
  expect(visibleNav(as({ myops: "view" })).some((item) => item.id === "machines")).toBe(true);
  expect(visibleNav(as({ production: "view" })).some((item) => item.id === "machines")).toBe(true);
  expect(visibleNav(as({ quality: "edit" })).some((item) => item.id === "machines")).toBe(true);  // карточка ВАЦ (20.11)
  // Склад открывает его тоже: шаблоны этикеток ГРН живут в карточке принтера TSC (владелец 23.11.2026).
  expect(visibleNav(as({ warehouse: "edit" })).some((item) => item.id === "machines")).toBe(true);
  expect(visibleNav(as({ receiving: "edit" })).some((item) => item.id === "machines")).toBe(false);
});

it("a planned machine without a БД.ОБ entry says so calmly instead of a setup error", async () => {
  const calls: string[] = [];
  machines(calls);
  render(<MachinesPage canWrite />);
  fireEvent.click(await screen.findByText("ОБ.037"));
  const dialog = await screen.findByRole("dialog", { name: /ОБ\.037/ });
  expect(within(dialog).getByText(/Станка нет в БД\.ОБ/)).toBeTruthy();
  expect(within(dialog).queryByRole("alert")).toBeNull();
  expect(calls.some((url) => url.includes("/setup-model/"))).toBe(false);
});

it("the next step of a ready machine is the Пуск reason of the command row, or «нажмите Пуск» when nothing is missing", () => {
  const ready = card();
  ready.live = { ...ready.live!, state: { code: 102, text: "Готов, ждёт пуска", tone: "ok" } };
  expect(machineStatus(ready, "").hint).toBe("Значения на станке ✓ — нажмите «Пуск»");
  expect(machineStatus(ready, "выберите задание").hint).toBe("Выберите задание");
  expect(machineStatus(ready, "опустите ролики").hint).toBe("Опустите ролики");
  expect(machineStatus(ready, "ждём записи задания").hint).toBe("Ждём записи задания");
  // Unknown (no right to start, not loaded yet): the general step.
  expect(machineStatus(ready).hint).toBe("Выберите, что режем, проверьте значения на чертеже и нажмите «Пуск»");
  const running = card();
  running.live = { ...running.live!, state: { code: 103, text: "Работает", tone: "ok" } };
  expect(machineStatus(running, "уже режет").hint).not.toMatch(/Стоп/);  // «Стоп» is a button, not a step to repeat
});

it("opens the work card only for a machine without control that is in БД.ОБ; others keep the facts window", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = decodeURIComponent(String(input));
    if (url.includes("/machine-queue")) return json({ equipment_id: "u70", inventory: "ОБ.070.1", version: "v", rows: [] });
    if (url.includes("/cutting-queue")) return json({ machine_id: null, version: "v", rows: [] });
    if (url.includes("/manual-jobs/options")) return json({ machines: [{ id: "u05", inventory: "ОБ.005", name: "Станок", running: null }], wires: [], contacts: [], references: [], can_edit: true });
    if (url.includes("/manual-jobs")) return json({ date_from: "", date_to: "", rows: [], can_edit: true, downtimes: [] });
    return json({ now: "", rows: [SOLDER, NOT_INSTALLED, TSC], families: FAMILIES });
  });
  render(<MachinesPage canWrite />);
  fireEvent.click(await screen.findByText("ОБ.070.1"));
  const card = await screen.findByRole("dialog", { name: /ОБ\.070\.1/ });
  expect(within(card).getByRole("status", { name: "Состояние станка" }).textContent).toContain("Свободен");
  expect(within(card).queryByRole("button", { name: "Ручная работа" })).toBeNull();
  fireEvent.click(within(card).getByRole("button", { name: "Закрыть" }));
  // Without a БД.ОБ record the old facts window stays (nothing to start a job on).
  fireEvent.click(screen.getByText("ОБ.037"));
  const info = await screen.findByRole("dialog", { name: /ОБ\.037/ });
  expect(within(info).getByRole("region", { name: "Сведения" })).toBeTruthy();
  expect(within(info).queryByRole("status", { name: "Состояние станка" })).toBeNull();
});

it("shortens cutting capabilities to «Резка + …», drops duplicates and keeps other operations readable", () => {
  // ОБ.019 as БД.ОБ lists it (owner 23.11: «Резка + разделка, маркировка»).
  expect(shortOperations(["Резка", "Резка с разделкой", "Резка с разделкой+ маркировка", "Резка линейных материалов"]))
    .toBe("Резка + разделка, маркировка");
  expect(shortOperations(["Резка линейных материалов"])).toBe("Резка линейных материалов");
  expect(shortOperations(["Резка", "Резка линейных материалов"])).toBe("Резка");
  expect(shortOperations(["Резка"])).toBe("Резка");
  expect(shortOperations(["Резка с разделкой", "резка  с разделкой"])).toBe("Резка + разделка");
  expect(shortOperations(["Опрессовка терминалов", "Резка с разделкой + маркировка", "Опрессовка терминалов"]))
    .toBe("Опрессовка терминалов, Резка + разделка, маркировка");
  expect(shortOperations(["Резка линейных материалов", "Плазменная зачистка"])).toBe("Резка линейных материалов, Плазменная зачистка");
  expect(shortOperations([])).toBe("");
});
