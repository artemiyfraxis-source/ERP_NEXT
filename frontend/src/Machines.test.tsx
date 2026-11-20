import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { AccessMode, Principal } from "./api";
import type { MachineCard, MachineRow, UnitSetup } from "./machinesApi";
import { MachinesPage } from "./MachinesPage";
import { visibleNav } from "./navigation";
import { Ob019CardView } from "./Ob019Card";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const OB019: MachineRow = {
  key: "machine:u19", kind: "machine", equipment_id: "u19", inventory: "ОБ.019",
  name: "Автомат резки и зачистки провода с лазерной маркировкой", pc: "PC-108", ip: "192.168.1.108",
  service: "Агент ERP · станок", version: "1.0.0", link: { code: "ok", text: "На связи", tone: "ok" },
  last_seen: "2026-11-18T10:00:00+03:00", device: "ПЛК линии резки", state: { text: "Аварийный стоп", tone: "danger" }, card: true,
  others: "", article: "EW-3185+L", site: "ЦАР", operations: ["Резка", "Резка с разделкой"], family: "Резка и зачистка",
  controlled: true, setup: { code: "full", label: "полная" }, pending_setup: 1,
};
const GODEX: MachineRow = {
  key: "printer:pc:godex", kind: "printer", equipment_id: "u101", inventory: "ОБ.101", name: "Godex EZ2350i", pc: "Цех",
  ip: "192.168.1.50", service: "Агент ERP · печать", version: "2.9.1",
  link: { code: "stale", text: "Нет связи с ПК", tone: "danger" }, last_seen: null, device: "Godex EZ2350i",
  state: { text: "Принтер не найден", tone: "warn" }, card: false, others: "", article: "", site: "", operations: [], family: "",
};
const PLANAR: MachineRow = {
  key: "device:instrument:si2", kind: "device", equipment_id: null, inventory: "СИ.002", name: "Planar S50180", pc: "DESKTOP-PTA57QF",
  ip: "192.168.1.121", service: "Агент ERP · печать", version: "2.9.4", link: { code: "ok", text: "На связи", tone: "ok" },
  last_seen: "2026-11-18T10:00:00+03:00", device: "Planar, S50180, 26030095, 26.2.2/2", state: { text: "Подключён", tone: "ok" },
  card: false, others: "DESKTOP-5O46RH6 — не найден", article: "VNA S50180", site: "", operations: [], family: "",
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

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

const NOT_INSTALLED: MachineRow = {
  key: "machine:u37", kind: "machine", equipment_id: null, inventory: "ОБ.037", name: "Автомат резки BZW-880", pc: "", ip: "",
  service: "Агент ERP · станок", version: "", link: { code: "not_installed", text: "Агент не установлен", tone: "neutral" },
  last_seen: null, device: "ПЛК линии резки", state: { text: "—", tone: "neutral" }, card: false, others: "",
  article: "BZW-880", site: "ЦАР", operations: ["Резка с разделкой"], family: "Резка и зачистка", controlled: false,
  setup: { code: null, label: "не задано" }, pending_setup: 0,
};
const TWISTER: MachineRow = {
  key: "machine:u05", kind: "machine", equipment_id: "u05", inventory: "ОБ.005", name: "Полуавтомат для скрутки проводов", pc: "", ip: "",
  service: "", version: "", link: { code: "not_connected", text: "Нет подключения к ERP", tone: "neutral" }, last_seen: null, device: "",
  state: { text: "—", tone: "neutral" }, card: false, others: "", article: "SR-W01", site: "ЖУК", operations: ["Свивка"],
  family: "Резка и зачистка", controlled: false, setup: { code: "simple", label: "простая" }, pending_setup: 0,
};
const SOLDER: MachineRow = {
  ...TWISTER, key: "machine:u70", equipment_id: "u70", inventory: "ОБ.070.1", name: "Станция паяльная термовоздушная", article: "8620DX-Pro",
  site: "ЦАР", operations: ["Пайка", "Усадка"], family: "Пайка и усадка", setup: { code: "none", label: "нет" },
};
const FAMILIES = ["Резка и зачистка", "Опрессовка", "Пайка и усадка", "Маркировка", "Прочее оборудование"];
const SETUP: UnitSetup = {
  unit_id: "u05", inventory: "ОБ.005", name: TWISTER.name, model: "SR-W01", requires_setup: { code: "simple", label: "простая" }, reason: "",
  parameters: [{ key: "twist_pitch_mm", label: "Шаг скрутки", unit: "мм", source: "ЭТК", source_field: "Шаг", register: "" }],
  configuration: { values: { twist_pitch_mm: "20" }, wire: "", contact: "", set_by: "Слава", set_at: "2026-11-18T09:00:00+03:00", version: 3 },
  references: [], history: [], online: false,
  open_tasks: [{ id: "t1", version: 1, status: "Назначена", operation: "П-104 · Жгут · Свивка · ЭТК 4", planned_start: "2026-11-19T08:00:00+03:00", planned_finish: null, mine: true, can_confirm: true },
    { id: "t2", version: 2, status: "В работе", operation: "П-105 · Жгут · Свивка · ЭТК 1", planned_start: null, planned_finish: null, mine: false, can_confirm: false }],
};

function machines(calls: string[] = []) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = decodeURIComponent(String(input));
    calls.push(url);
    if (url.endsWith("/u19")) return json(card());
    if (url.endsWith("/control")) return json({ problem: "", state: 105, commands: [], offsets: [], can_edit: false });
    if (url.endsWith("/transfer")) return json({ choices: [] });
    if (url.endsWith("/card/ОБ.019")) return json({ inventory: "ОБ.019", settings: [{ title: "Провод и зачистка", fields: [
      { label: "Длина провода", value: 325, unit: "мм", read: true, key: "length_mm", kind: "u32" }] }] });
    if (url.includes("/setup-model/")) return json(SETUP);
    return json({ now: "2026-11-18T10:00:02+03:00", rows: [OB019, NOT_INSTALLED, TWISTER, SOLDER, GODEX, PLANAR], families: FAMILIES });
  });
}

it("splits machines into «С управлением» and «Без управления», printers and instruments have their own tab", async () => {
  const calls: string[] = [];
  machines(calls);
  render(<MachinesPage />);
  const controlled = await screen.findByRole("tab", { name: /С управлением/ });
  expect(controlled.getAttribute("aria-selected")).toBe("true");
  expect(controlled.textContent).toBe("С управлением1");
  expect(screen.getByRole("tab", { name: /Без управления/ }).textContent).toBe("Без управления3");
  expect(screen.getByRole("tab", { name: /Принтеры и приборы/ }).textContent).toBe("Принтеры и приборы2");
  expect(screen.getByRole("tab", { name: /^Все/ }).textContent).toBe("Все6");
  expect(screen.queryByRole("tab", { name: "Запросы на наладку" })).toBeNull();  // наладка этому сотруднику закрыта
  // ОБ.019 — in its family block, with the article, the site, the operations and «Ждёт наладки».
  const cutting = screen.getByRole("region", { name: "Резка и зачистка" });
  const tile = within(cutting).getByText("ОБ.019").closest("button")!;
  expect(within(tile).getByText("EW-3185+L · ЦАР")).toBeTruthy();
  expect(within(tile).getByText("Резка, Резка с разделкой")).toBeTruthy();
  expect(within(tile).getByText("Ждёт наладки").className).toContain("tone-warn");
  expect(within(tile).getByText("Наладка: полная")).toBeTruthy();
  expect(within(tile).getByText("Аварийный стоп").className).toContain("tone-danger");
  expect(screen.queryByText("ОБ.037")).toBeNull();

  fireEvent.click(screen.getByRole("tab", { name: /Без управления/ }));
  expect(screen.getByRole("region", { name: "Резка и зачистка" })).toBeTruthy();
  expect(screen.getByRole("region", { name: "Пайка и усадка" })).toBeTruthy();
  const absent = screen.getByText("ОБ.037").closest("button")!;
  expect(absent.className).toContain("is-absent");
  const twister = screen.getByText("ОБ.005").closest("button")!;
  expect(within(twister).getByText("Нет подключения к ERP")).toBeTruthy();
  expect(within(twister).getByText("SR-W01 · ЖУК")).toBeTruthy();
  expect(within(screen.getByText("ОБ.070.1").closest("button")!).getByText("Без наладки")).toBeTruthy();

  fireEvent.click(screen.getByRole("tab", { name: /Принтеры и приборы/ }));
  const printers = screen.getByRole("region", { name: "Принтеры" });
  const devices = screen.getByRole("region", { name: "Приборы и сканеры" });
  expect(within(printers).getByText("Нет связи с ПК").className).toContain("tone-danger");
  expect(within(devices).getAllByText("СИ.002")).toHaveLength(1);
  fireEvent.click(within(devices).getByText("СИ.002"));
  const info = await screen.findByRole("dialog", { name: /СИ\.002/ });
  expect(within(info).getByText("DESKTOP-5O46RH6 — не найден")).toBeTruthy();
  expect(within(info).getByText("VNA S50180")).toBeTruthy();
  fireEvent.click(within(info).getByRole("button", { name: "Закрыть" }));
  expect(calls.some((url) => url.includes("/setup-model/"))).toBe(false);

  fireEvent.click(screen.getByRole("tab", { name: /^Все/ }));
  for (const title of ["Резка и зачистка", "Пайка и усадка", "Принтеры", "Приборы и сканеры"]) expect(screen.getByRole("region", { name: title })).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: /С управлением/ }));
  fireEvent.click(screen.getByText("ОБ.019"));
  const dialog = await screen.findByRole("dialog", { name: /ОБ\.019/ });
  // The drawing shows what is on the machine now.
  await waitFor(() => expect(within(dialog).getByLabelText("Длина провода")).toHaveValue("325,00"));
  expect(within(dialog).getByText("3 621 шт/ч")).toBeTruthy();
  expect(calls).toContain("/api/production/machines/u19");
  fireEvent.click(within(dialog).getByRole("button", { name: "Наладка · ждёт" }));
  expect(await screen.findByRole("dialog", { name: "Наладка ОБ.019" })).toBeTruthy();
});

it("filters by site and searches by number, name, article, PC or IP", async () => {
  machines();
  render(<MachinesPage />);
  fireEvent.click(await screen.findByRole("tab", { name: /Без управления/ }));
  fireEvent.click(screen.getByRole("radio", { name: /ЖУК/ }));
  expect(screen.getByText("ОБ.005")).toBeTruthy();
  expect(screen.queryByText("ОБ.037")).toBeNull();
  fireEvent.click(screen.getByRole("radio", { name: /Все участки/ }));
  const search = screen.getByRole("searchbox", { name: "Поиск оборудования" });
  fireEvent.change(search, { target: { value: "8620dx" } });
  expect(screen.getByText("ОБ.070.1")).toBeTruthy();
  expect(screen.queryByText("ОБ.005")).toBeNull();
  expect(screen.getByRole("tab", { name: /Без управления/ }).textContent).toBe("Без управления1");
  fireEvent.change(search, { target: { value: "pc-108" } });
  fireEvent.click(screen.getByRole("tab", { name: /С управлением/ }));
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
    return json({ now: "", rows: [TWISTER], families: FAMILIES });
  });
  render(<MachinesPage canWrite />);
  fireEvent.click(await screen.findByRole("tab", { name: /Без управления/ }));
  fireEvent.click(screen.getByText("ОБ.005"));
  const dialog = await screen.findByRole("dialog", { name: /ОБ\.005/ });
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

it("shows an honest empty text on each tab", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ now: "", rows: [] }));
  render(<MachinesPage />);
  expect(await screen.findByText(/Нет станков с агентом ERP/)).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: /Без управления/ }));
  expect(screen.getByText(/отметьте станок в БД\.ОБ галочкой/)).toBeTruthy();
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

it("keeps an agent answer the state line does not say in the folded «Подключение»", () => {
  render(<Ob019CardView card={card({ link: { code: "no_plc", text: "ПЛК не отвечает", tone: "danger" }, error: "COM8 занят", live: null })} />);
  expect(within(screen.getByRole("status", { name: "Состояние станка" })).getByText("ПЛК не отвечает")).toBeTruthy();
  expect(screen.getByText("COM8 занят").closest("details")).not.toBeNull();
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
  expect(visibleNav(as({ warehouse: "edit" })).some((item) => item.id === "machines")).toBe(false);
});

it("a planned machine without a БД.ОБ entry says so calmly instead of a setup error", async () => {
  const calls: string[] = [];
  machines(calls);
  render(<MachinesPage canWrite />);
  fireEvent.click(await screen.findByRole("tab", { name: /Без управления/ }));
  fireEvent.click(screen.getByText("ОБ.037"));
  const dialog = await screen.findByRole("dialog", { name: /ОБ\.037/ });
  expect(within(dialog).getByText(/Станка нет в БД\.ОБ/)).toBeTruthy();
  expect(within(dialog).queryByRole("alert")).toBeNull();
  expect(calls.some((url) => url.includes("/setup-model/"))).toBe(false);
});
