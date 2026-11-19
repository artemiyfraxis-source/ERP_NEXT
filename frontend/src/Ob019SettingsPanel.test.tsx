import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { MachineControl, MachineWindow, TransferPlan } from "./machineControlApi";
import { Ob019SettingsPanel } from "./Ob019SettingsPanel";
import { wireZones } from "./Ob019WireDrawing";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const WINDOW: MachineWindow = {
  inventory: "ОБ.019",
  settings: [
    { title: "Задание", fields: [
      { label: "Режим", value: 2, unit: "", read: true, key: "mode", kind: "mode", text: "Автомат" },
      { label: "Тираж", value: 300, unit: "шт", read: true, key: "total_set", kind: "u32" },
    ] },
    { title: "Провод и зачистка", fields: [
      { label: "Длина провода", value: 325, unit: "мм", read: true, key: "length_mm", kind: "u32" },
    ] },
    { title: "Скорости", fields: [{ label: "Нож", value: 5, unit: "", read: true, key: null, kind: "u16" }] },
  ],
};
const OFFSETS = ["length_mm", "strip_front_mm", "strip_back_mm"].map((key) => ({
  key, label: { length_mm: "Длина провода", strip_front_mm: "Зачистка начала", strip_back_mm: "Зачистка конца" }[key]!,
  offset: "0,00", reason: "", updated_by: "", updated_at: null, etk: key !== "length_mm",
}));
const CONTROL: MachineControl = { problem: "", state: 102, commands: [], offsets: OFFSETS, can_edit: true };
const PLAN: TransferPlan = {
  group_id: "g1", number: "ГР-0007", wire: "ПВАМ 0,35", length_mm: "325,00", quantity: 12,
  rows: [
    { key: "length_mm", label: "Длина провода", unit: "мм", nominal: "325,00", source: "Задание", offset: "0,00", value: "325,00", live: "325,00" },
    { key: "strip_front_mm", label: "Зачистка начала", unit: "мм", nominal: "5,00", source: "ЭТК", offset: "0,40", value: "5,40", live: "5,00" },
    { key: "knife_back_mm", label: "Отвод ножа", unit: "мм", nominal: null, source: "Нет данных", offset: "0,00", value: null, live: "0,10" },
  ],
  job: { total_set: 12, order_quantity: 12, order_number: "GR-0007", product: "PVAM 0,35 L325,00" }, missing: ["Отвод ножа"], blocking: [],
};

function server(control: MachineControl = CONTROL, { window = WINDOW, plan = PLAN, planGate }: {
  window?: MachineWindow; plan?: TransferPlan; planGate?: Promise<void>;
} = {}) {
  const posts: { url: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (init?.method === "POST") {
      posts.push({ url, body: JSON.parse(String(init.body)) });
      if (url.endsWith("/correction")) return json({ offset: "0,40", etk: [] });
      return json({ id: "c1", status: "queued", status_text: "Ждёт агента станка", tone: "info", purpose: "settings",
        summary: "Запись", fields: [], error: "", created_by: "Слава", created_at: "2026-11-19T10:00:00+03:00", finished_at: null });
    }
    if (url.endsWith("/control")) return json(control);
    if (url.includes("/transfer?")) { await planGate; return json(plan); }
    if (url.endsWith("/transfer")) return json({ choices: [{ key: "g1:325", group_id: "g1", number: "ГР-0007", project: "708К.",
      wire: "ПВАМ 0,35", length_mm: "325", quantity: "12" }] });
    return json(window);
  });
  return posts;
}

async function pickJob() {
  const pick = await screen.findByLabelText("Задание резки");
  await waitFor(() => expect(within(pick).getAllByRole("option")).toHaveLength(2));
  fireEvent.change(pick, { target: { value: "g1:325" } });
  return pick;
}

afterEach(() => { vi.restoreAllMocks(); });

it("scales the stripped ends with their size but keeps room for the fields", () => {
  const zones = (strip: number | null, pull: number | null) => wireZones({ strip_front: strip, pull_front: pull, strip_back: 0, pull_back: 0 });
  expect(zones(0, 0).front).toBe(20);
  expect(zones(5, 0).front).toBe(25);
  expect(zones(80, 0).front).toBe(30);
  expect(zones(null, null).back).toBe(20);
  expect(zones(5, 0).slugFront).toBe(0);
  expect(zones(5, 2.5).slugFront).toBe(0.5);
  expect(zones(5, 4.9).slugFront).toBe(0.25);
});

it("puts the wire on a drawing and the rare HMI pages under «Остальные параметры»", async () => {
  server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const toggle = await screen.findByRole("button", { name: /Остальные параметры/ });
  expect(screen.queryByRole("tab")).toBeNull();
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  // Only pages with something not on the drawing: «Задание» and «Провод и зачистка» here are all on top.
  expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Скорости"]);
  expect(screen.getByText("только просмотр")).toBeInTheDocument();
  const drawing = screen.getByRole("region", { name: "Провод: длина и зачистка" });
  expect(within(drawing).getByLabelText("Длина провода: новое значение")).toBeInTheDocument();
  expect(within(drawing).getByText("Зачистка начала")).toBeInTheDocument();
  expect(within(drawing).getByText("Стягивание конца")).toBeInTheDocument();
  expect(within(drawing).getByText("Отвод ножа")).toBeInTheDocument();
});

it("shows the machine value beside each field and highlights an entered value that differs", async () => {
  server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  expect(await screen.findByText("на станке 325,00 мм")).toBeInTheDocument();
  expect(screen.getByText("на станке 300 шт")).toBeInTheDocument();
  const length = screen.getByLabelText("Длина провода: новое значение");
  fireEvent.change(length, { target: { value: "325,00" } });
  expect(length.closest("label")!.className).not.toContain("is-diff");
  fireEvent.change(length, { target: { value: "325,5" } });
  expect(length.closest("label")!.className).toContain("is-diff");
  expect(screen.getByText("≠ на станке 325,00 мм")).toBeInTheDocument();
});

it("writes a changed value only after the confirmation with the machine state", async () => {
  const posts = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  expect(await screen.findByText("Можно записывать: готов, ждёт пуска, пульт PXS_HMI закрыт")).toBeInTheDocument();
  await screen.findByText("на станке 325,00 мм");
  fireEvent.change(screen.getByLabelText("Длина провода: новое значение"), { target: { value: "326" } });
  fireEvent.click(screen.getByRole("button", { name: "Записать на станок (1)" }));
  const dialog = screen.getByRole("alertdialog");
  expect(within(dialog).getByText("Станок: Готов, ждёт пуска.")).toBeInTheDocument();
  expect(within(dialog).getByText("Длина провода: 325,00 мм → 326 мм")).toBeInTheDocument();
  expect(posts).toEqual([]);
  fireEvent.click(within(dialog).getByRole("button", { name: "Подтвердить запись" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toEqual({ url: "/api/production/machines/card/%D0%9E%D0%91.019/write",
    body: { values: { length_mm: "326" }, confirm_state: 102 } });
  expect(await screen.findByText("Запись настроек: ждёт агента станка")).toBeInTheDocument();
});

it("writes a switch of another HMI page as Вкл / Выкл and leaves unknown registers read-only", async () => {
  const window: MachineWindow = { ...WINDOW, settings: [...WINDOW.settings, { title: "Маркировка", fields: [
    { label: "Маркировка", value: false, unit: "", read: true, key: "marking", kind: "bit0" },
    { label: "Параметр D390 (назначение не установлено)", value: true, unit: "", read: true, key: null, kind: "bit0" }] }] };
  const posts = server(CONTROL, { window });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  fireEvent.click(await screen.findByRole("button", { name: /Остальные параметры/ }));
  fireEvent.click(screen.getByRole("tab", { name: "Маркировка" }));
  const table = screen.getByRole("table", { name: "Маркировка" });
  expect(within(table).getByRole("row", { name: /^Маркировка Выкл/ })).toBeInTheDocument();
  expect(within(table).queryByLabelText("Параметр D390 (назначение не установлено): новое значение")).toBeNull();
  const marking = within(table).getByLabelText("Маркировка: новое значение");
  fireEvent.change(marking, { target: { value: "1" } });
  expect(marking.closest("tr")!.className).toContain("is-diff");
  fireEvent.click(screen.getByRole("button", { name: "Записать на станок (1)" }));
  const dialog = screen.getByRole("alertdialog");
  expect(within(dialog).getByText("Маркировка: Выкл → Вкл")).toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole("button", { name: "Подтвердить запись" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toEqual({ values: { marking: "1" }, confirm_state: 102 });
});

it("keeps the other HMI pages read-only while the writer on the machine PC is older than 1.2.0", async () => {
  const window: MachineWindow = { ...WINDOW, settings: [...WINDOW.settings, { title: "Маркировка", fields: [
    { label: "Маркировка", value: false, unit: "", read: true, key: "marking", kind: "bit0" },
    { label: "Текст у начала", value: "X1", unit: "", read: true, key: "mark_front", kind: "text15" }] }] };
  server({ ...CONTROL, runner_outdated: true, runner_keys: ["marking"] }, { window });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  fireEvent.click(await screen.findByRole("button", { name: /Остальные параметры/ }));
  fireEvent.click(screen.getByRole("tab", { name: "Маркировка" }));
  expect(screen.getByText(/Служба записи на ПК станка устарела — обновите \(нужна 1\.2\.0\)/)).toBeInTheDocument();
  expect(screen.getByLabelText("Маркировка: новое значение")).toBeDisabled();
  expect(screen.getByLabelText("Текст у начала: новое значение")).toBeEnabled();
  fireEvent.change(screen.getByLabelText("Длина провода: новое значение"), { target: { value: "326" } });
  expect(screen.getByRole("button", { name: "Записать на станок (1)" })).toBeEnabled();
});

it("transfers the ETK job with the machine correction and refuses while the machine runs", async () => {
  const posts = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const pick = await screen.findByLabelText("Задание резки");
  await waitFor(() => expect(within(pick).getAllByRole("option")).toHaveLength(2));
  fireEvent.change(pick, { target: { value: "g1:325" } });
  // The ETK job fills the drawing with what «Передать на станок» writes.
  await waitFor(() => expect(screen.getByLabelText("Зачистка начала: новое значение")).toHaveValue("5,40"));
  expect(screen.getByLabelText("Длина провода: новое значение")).toHaveValue("325,00");
  expect(screen.getByLabelText("Тираж: новое значение")).toHaveValue("12");
  expect(screen.getByText(/Отвод ножа — остаётся как на станке/)).toBeInTheDocument();
  expect(screen.getByText(/Зачистка начала: ЭТК 5,00, поправка станка 0,40/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Передать на станок" }));
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Подтвердить запись" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toEqual({ group_id: "g1", length_mm: "325", confirm_state: 102 });
});

it("writes the edited ETK values as settings once a prefilled field is changed", async () => {
  const posts = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const pick = await screen.findByLabelText("Задание резки");
  await waitFor(() => expect(within(pick).getAllByRole("option")).toHaveLength(2));
  fireEvent.change(pick, { target: { value: "g1:325" } });
  await waitFor(() => expect(screen.getByLabelText("Длина провода: новое значение")).toHaveValue("325,00"));
  fireEvent.change(screen.getByLabelText("Длина провода: новое значение"), { target: { value: "326" } });
  expect(screen.queryByRole("button", { name: "Передать на станок" })).toBeNull();
  // Only fields the machine page knows are written (this window: length and run), through the same /write path.
  fireEvent.click(screen.getByRole("button", { name: "Записать на станок (2)" }));
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Подтвердить запись" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toEqual({ url: "/api/production/machines/card/%D0%9E%D0%91.019/write",
    body: { values: { length_mm: "326", total_set: "12" }, confirm_state: 102 } });
});

it("blocks writing with the reason when the machine is not idle", async () => {
  server({ ...CONTROL, problem: "Записывать можно, когда станок готов и ждёт пуска или тираж готов; сейчас: Работает", state: 103 });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  fireEvent.change(await screen.findByLabelText("Тираж: новое значение"), { target: { value: "50" } });
  expect(await screen.findByText(/сейчас: Работает/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Записать на станок (1)" })).toBeDisabled();
});

it("records «станок режет не так» with the offset suggested from the measured size", async () => {
  const posts = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const pick = await screen.findByLabelText("Задание резки");
  await waitFor(() => expect(within(pick).getAllByRole("option")).toHaveLength(2));
  fireEvent.change(pick, { target: { value: "g1:325" } });
  await waitFor(() => expect(screen.getByLabelText("Зачистка начала: новое значение")).toHaveValue("5,40"));
  fireEvent.change(screen.getByLabelText("Получилось, мм"), { target: { value: "4,6" } });
  expect(screen.getByLabelText("Поправка станка, мм")).toHaveValue("0,40");
  fireEvent.click(screen.getByRole("button", { name: "Записать правку" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toEqual({ parameter: "strip_front_mm", case: "machine", measured: "4,6", offset: "0,40" });
  expect(await screen.findByText("Правка записана: поправка станка 0,40 мм")).toBeInTheDocument();
});

it("refuses to write an ETK job with a size missing in the ETK until that size is typed by hand", async () => {
  const window: MachineWindow = { ...WINDOW, settings: [...WINDOW.settings, { title: "Зачистка", fields: [
    { label: "Зачистка конца", value: 5, unit: "мм", read: true, key: "strip_back_mm", kind: "u32" }] }] };
  const plan: TransferPlan = { ...PLAN, rows: [...PLAN.rows, { key: "strip_back_mm", label: "Зачистка конца", unit: "мм",
    nominal: null, source: "Нет данных", offset: "0,00", value: null, live: "5,00" }], blocking: ["Зачистка конца"] };
  const posts = server(CONTROL, { window, plan });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  await pickJob();
  expect(await screen.findByRole("button", { name: "Передать на станок" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Длина провода: новое значение"), { target: { value: "326" } });
  // Editing any field must not open the /write path with the old machine value standing in for the missing size.
  expect(screen.getByRole("button", { name: "Записать на станок (2)" })).toBeDisabled();
  expect(screen.getByText("В ЭТК не задано: Зачистка конца — введите на чертеже или выберите другое задание")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Зачистка конца: новое значение"), { target: { value: "6" } });
  expect(screen.getByText("В ЭТК не задано: Зачистка конца — введено вручную")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Записать на станок (3)" }));
  fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Подтвердить запись" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toEqual({ values: { length_mm: "326", total_set: "12", strip_back_mm: "6" }, confirm_state: 102 });
});

it("keeps what was typed while the ETK job loaded and asks before replacing hand edits", async () => {
  let release = () => {};
  const planGate = new Promise<void>((resolve) => { release = resolve; });
  server(CONTROL, { planGate });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const pick = await pickJob();
  fireEvent.change(screen.getByLabelText("Длина провода: новое значение"), { target: { value: "330" } });
  release();
  await waitFor(() => expect(screen.getByLabelText("Зачистка начала: новое значение")).toHaveValue("5,40"));
  expect(screen.getByLabelText("Длина провода: новое значение")).toHaveValue("330");
  const ask = vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.change(pick, { target: { value: "" } });
  expect(ask).toHaveBeenCalledWith("Заменить введённые значения?");
  expect(pick).toHaveValue("g1:325");
  expect(screen.getByLabelText("Длина провода: новое значение")).toHaveValue("330");
  ask.mockReturnValue(true);
  fireEvent.change(pick, { target: { value: "" } });
  expect(pick).toHaveValue("");
  expect(screen.getByLabelText("Длина провода: новое значение")).toHaveValue("");
});

it("highlights a differing value under «Остальные параметры» and counts it on the toggle", async () => {
  const window: MachineWindow = { ...WINDOW, settings: [...WINDOW.settings, { title: "Маркировка", fields: [
    { label: "Текст у начала", value: "X1", unit: "", read: true, key: "mark_front", kind: "text15" }] }] };
  server(CONTROL, { window });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  fireEvent.click(await screen.findByRole("button", { name: /Остальные параметры/ }));
  fireEvent.click(await screen.findByRole("tab", { name: "Маркировка" }));
  const input = screen.getByLabelText("Текст у начала: новое значение");
  fireEvent.change(input, { target: { value: "X1" } });
  expect(input.closest("tr")!.className).not.toContain("is-diff");
  expect(screen.getByRole("button", { name: /Остальные параметры/ }).textContent).not.toContain("изменено");
  fireEvent.change(input, { target: { value: "X2" } });
  expect(input.closest("tr")!.className).toContain("is-diff");
  expect(screen.getByRole("button", { name: /Остальные параметры/ }).textContent).toContain("изменено 1");
});
