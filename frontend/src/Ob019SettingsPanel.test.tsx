import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { MachineCommand, MachineControl, MachineWindow, TransferPlan } from "./machineControlApi";
import { Ob019SettingsPanel, pullFor, pullMode, sourceText } from "./Ob019SettingsPanel";
import { wireZones } from "./Ob019WireDrawing";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const field = (label: string, key: string | null, value: number | boolean | string | null, unit = "мм", kind = "u32") =>
  ({ label, value, unit, read: true, key, kind });
function machineWindow(): MachineWindow {
  return {
    inventory: "ОБ.019",
    settings: [
      { title: "Задание", fields: [
        { label: "Режим", value: 2, unit: "", read: true, key: "mode", kind: "mode", text: "Автомат" },
        field("Тираж", "total_set", 300, "шт"),
      ] },
      { title: "Провод и зачистка", fields: [
        field("Длина провода", "length_mm", 325), field("Зачистка начала", "strip_front_mm", 5), field("Стягивание начала", "pull_front_mm", 5),
        field("Зачистка конца", "strip_back_mm", 5), field("Стягивание конца", "pull_back_mm", 5), field("Диаметр провода", "diameter_mm", 0.8),
        field("Отвод ножа", "knife_back_mm", 0.1),
      ] },
      { title: "Скорости", fields: [field("Нож", null, 5, "", "u16"), field("Подъём подающих роликов", "feed_wheel", false, "", "bit0")] },
    ],
  };
}
// Stored offsets are what the plan rows add on top of their nominal (strip front +0,40 here and there).
const OFFSETS = ([["length_mm", "Длина провода", "0,00"], ["strip_front_mm", "Зачистка начала", "0,40"], ["strip_back_mm", "Зачистка конца", "0,00"]] as const)
  .map(([key, label, offset]) => ({ key, label, offset, reason: "", updated_by: "", updated_at: null, etk: key !== "length_mm" }));
const CONTROL: MachineControl = { problem: "", state: 102, commands: [], offsets: OFFSETS, can_edit: true };
const PLAN: TransferPlan = {
  group_id: "g1", number: "ГР-0007", wire: "ПВАМ 0,35", length_mm: "325,00", quantity: 12,
  rows: [
    { key: "length_mm", label: "Длина провода", unit: "мм", nominal: "325,00", source: "Задание", offset: "0,00", value: "325,00", live: "325,00" },
    { key: "strip_front_mm", label: "Зачистка начала", unit: "мм", nominal: "5,00", source: "ЭТК", offset: "0,40", value: "5,40", live: "5,00" },
    { key: "knife_back_mm", label: "Отвод ножа", unit: "мм", nominal: "0,10", source: "Рецепт, версия 2", offset: null, value: "0,10", live: "0,10" },
  ],
  job: { total_set: 12, order_quantity: 12, order_number: "GR-0007", product: "PVAM 0,35 L325,00" }, missing: [], blocking: [],
};
const WIRE_PLAN: TransferPlan = {
  group_id: null, number: null, wire: "ПВАМ 0,50", length_mm: "325,00", quantity: null,
  rows: [
    { key: "length_mm", label: "Длина провода", unit: "мм", nominal: "325,00", source: "Задание", offset: "0,00", value: "325,00", live: "325,00" },
    { key: "diameter_mm", label: "Диаметр провода", unit: "мм", nominal: "0,74", source: "Ø жилы + 0,10", offset: "0,00", value: "0,74", live: "0,80" },
  ],
  job: { total_set: null, order_quantity: null, order_number: "", product: "PVAM 0,50 L325,00" }, missing: [], blocking: [],
};
const WIRES = [{ label: "ПВАМ 0,35", conductor_mm: "0,52", has_recipe: true }, { label: "ПВАМ 0,50", conductor_mm: "0,64", has_recipe: false }];

/** A fake machine: a write is taken at once and read back into the window on the next poll. */
function server({ control = CONTROL, plan = PLAN, wirePlan = WIRE_PLAN, refuse = "", live = {} }: {
  control?: MachineControl; plan?: TransferPlan; wirePlan?: TransferPlan; refuse?: string; live?: Record<string, number>;
} = {}) {
  const posts: { url: string; body: Record<string, unknown> }[] = [];
  const gets: string[] = [];
  const window = machineWindow();
  for (const group of window.settings) for (const item of group.fields) if (item.key && item.key in live) item.value = live[item.key];
  const commands: MachineCommand[] = [];
  const state = { plan, wirePlan };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = decodeURIComponent(String(input));
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      posts.push({ url, body });
      if (url.endsWith("/correction")) return json({ offset: "5,00", etk: [], written: "325,00", measured: "320,00" });
      if (url.endsWith("/versions")) return json({ recipe_id: "r1", version: 3, title: "ПВАМ 0,35", created_at: "2026-11-19T14:05:00+01:00" });
      if (refuse) return json({ detail: refuse }, 409);
      const id = `c${posts.length}`;
      // «Передать на станок» writes the job's plan; a write — its values.
      const values = (body.values ?? Object.fromEntries([...state.plan.rows.filter((row) => row.value !== null).map((row) => [row.key, row.value]),
        ["total_set", String(state.plan.job.total_set)]])) as Record<string, string>;
      for (const group of window.settings) for (const item of group.fields) {
        if (item.key && item.key in values) item.value = item.kind === "bit0" ? values[item.key] === "1" : Number(values[item.key].replace(",", "."));
      }
      commands.unshift({ id, status: "done", status_text: "Записано", tone: "ok", purpose: "settings", summary: "Запись", fields: [], error: "",
        created_by: "Слава", created_at: "2026-11-19T10:00:00+03:00", finished_at: "2026-11-19T10:00:02+03:00" });
      return json({ ...commands[0], status: "queued", status_text: "Ждёт агента станка" });
    }
    gets.push(url);
    if (url.endsWith("/control")) return json({ ...control, commands });
    if (url.includes("/proposal")) return json({ wire: "ПВАМ 0,35", recipe: { recipe_id: "r1", version: 2, title: "ПВАМ 0,35", created_at: "2026-11-18T12:00:00+01:00", source: "" },
      publish: { recipe_id: "r1", expected_version: 2 }, etk: null, values: [] });
    if (url.includes("/transfer?wire=")) return json(state.wirePlan);
    if (url.includes("/transfer?")) return json(state.plan);
    if (url.endsWith("/transfer")) return json({ choices: [{ key: "g1:325", group_id: "g1", number: "ГР-0007", project: "708К.",
      wire: "ПВАМ 0,35", length_mm: "325", quantity: "12" }], wires: WIRES });
    return json(window);
  });
  return { posts, gets, state };
}

async function pickJob() {
  const pick = await screen.findByLabelText("Задание резки");
  await waitFor(() => expect(within(pick).getAllByRole("option")).toHaveLength(2));
  fireEvent.change(pick, { target: { value: "g1:325" } });
  return pick;
}

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

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

it("says where each planned value comes from", () => {
  const row = (source: string, value: string, nominal: string | null, offset: string | null) =>
    ({ key: "k", label: "L", unit: "мм", source, value, nominal, offset, live: null });
  expect(sourceText(row("Ø жилы + 0,10", "0,74", "0,74", "0,00"), "0,64", null)).toBe("0,74 = Ø жилы 0,64 + 0,10");
  expect(sourceText(row("Рецепт, версия 2", "5,00", "5,00", null), null, "2026-11-18T12:00:00+01:00")).toBe("из настройки 18.11");
  expect(sourceText(row("ЭТК", "5,00", "5,00", "0,00"), null, null)).toBe("из ЭТК");
  expect(sourceText(row("ЭТК", "5,40", "5,00", "0,40"), null, null)).toBe("ЭТК 5,00 + поправка +0,40");
});

it("shows what is on the machine now in the drawing; rare HMI pages stay under «Остальные параметры»", async () => {
  server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const drawing = screen.getByRole("region", { name: "Провод: длина и зачистка" });
  await waitFor(() => expect(within(drawing).getByLabelText("Длина провода")).toHaveValue("325,00"));
  expect(within(drawing).getByLabelText("Диаметр провода")).toHaveValue("0,80");
  expect(screen.getByLabelText("Тираж")).toHaveValue("300");
  expect(screen.queryByText(/на станке —/)).toBeNull();
  expect(screen.queryByRole("button", { name: /Записать на станок/ })).toBeNull();
  const toggle = screen.getByRole("button", { name: /Остальные параметры/ });
  fireEvent.click(toggle);
  // The rollers are in the command row; the exact pull-off is fine-tuned here, the drawing has its mode.
  expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Провод и зачистка", "Скорости"]);
  expect(screen.getAllByRole("table")[0]).toHaveTextContent("Стягивание начала");
  fireEvent.click(screen.getByRole("tab", { name: "Скорости" }));
  expect(screen.queryByLabelText("Подъём подающих роликов")).toBeNull();
  expect(screen.getByText("только просмотр")).toBeInTheDocument();
});

it("writes an edited field on Enter at once, without a dialog, and shows it read back from the machine", async () => {
  const { posts } = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const length = await screen.findByLabelText("Длина провода");
  await waitFor(() => expect(length).toHaveValue("325,00"));
  fireEvent.change(length, { target: { value: "326" } });
  expect(posts).toEqual([]);
  fireEvent.keyDown(length, { key: "Enter" });
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toEqual({ url: "/api/production/machines/card/ОБ.019/write", body: { values: { length_mm: "326" }, confirm_state: 102 } });
  expect(screen.queryByRole("alertdialog")).toBeNull();
  expect(await screen.findByText("✓ на станке", {}, { timeout: 3000 })).toBeInTheDocument();
  expect(length).toHaveValue("326,00");
});

it("does not write while typing, writes on leaving the field, and skips a value equal to the machine's", async () => {
  const { posts } = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const strip = await screen.findByLabelText("Зачистка начала");
  await waitFor(() => expect(strip).toHaveValue("5,00"));
  fireEvent.change(strip, { target: { value: "5" } });
  fireEvent.blur(strip);
  fireEvent.change(strip, { target: { value: "5,5" } });
  expect(strip).toHaveValue("5,5");
  expect(posts).toEqual([]);
  fireEvent.blur(strip);
  await waitFor(() => expect(posts.map((post) => post.body)).toEqual([{ values: { strip_front_mm: "5,5", pull_front_mm: "7,50" }, confirm_state: 102 }]));
});

it("a refused write says why next to the field and the field goes back to the machine value", async () => {
  server({ refuse: "Открыт пульт станка на ПК — запись из ERP недоступна" });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const length = await screen.findByLabelText("Длина провода");
  await waitFor(() => expect(length).toHaveValue("325,00"));
  fireEvent.change(length, { target: { value: "400" } });
  fireEvent.keyDown(length, { key: "Enter" });
  expect(await screen.findByText("отказ: На ПК станка открыт пульт PXS_HMI — закройте его, ERP ждёт")).toBeInTheDocument();
  expect(length).toHaveValue("325,00");
});

it("the − / + buttons write once after the operator stops pressing", async () => {
  const { posts } = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const diameter = await screen.findByLabelText("Диаметр провода");
  await waitFor(() => expect(diameter).toHaveValue("0,80"));
  fireEvent.click(screen.getByRole("button", { name: "Диаметр провода: меньше" }));
  fireEvent.click(screen.getByRole("button", { name: "Диаметр провода: меньше" }));
  expect(diameter).toHaveValue("0,78");
  await waitFor(() => expect(posts).toHaveLength(1), { timeout: 2000 });
  expect(posts[0].body).toEqual({ values: { diameter_mm: "0,78" }, confirm_state: 102 });
});

it("picking an ETK job sends it to the machine at once and tells where each value comes from", async () => {
  const { posts } = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  await pickJob();
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toEqual({ url: "/api/production/machines/card/ОБ.019/transfer", body: { group_id: "g1", length_mm: "325", confirm_state: 102 } });
  expect(screen.queryByRole("button", { name: /Передать на станок|Записать на станок/ })).toBeNull();
  expect(await screen.findByText("ЭТК 5,00 + поправка +0,40", {}, { timeout: 3000 })).toBeInTheDocument();
});

it("picking a wire by hand asks the wire plan and writes its differing values", async () => {
  const { posts, gets } = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  await waitFor(() => expect(screen.getByLabelText("Длина провода")).toHaveValue("325,00"));
  const picker = screen.getByRole("combobox", { name: "Провод" });
  fireEvent.focus(picker);
  fireEvent.change(picker, { target: { value: "0.5" } });
  const options = within(screen.getByRole("listbox", { name: "Провода" })).getAllByRole("option");
  expect(options).toHaveLength(1);
  fireEvent.click(options[0]);
  await waitFor(() => expect(gets.some((url) => url.endsWith("/transfer?wire=ПВАМ 0,50&length_mm=325"))).toBe(true));
  await waitFor(() => expect(posts).toHaveLength(1));
  // Length is already 325,00 on the machine: only the diameter and the product go out.
  expect(posts[0].body).toEqual({ values: { diameter_mm: "0,74" }, confirm_state: 102 });
  expect(await screen.findByText("0,74 = Ø жилы 0,64 + 0,10", {}, { timeout: 3000 })).toBeInTheDocument();
});

it("lists the wires with a saved setting first and marks them", async () => {
  server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const picker = await screen.findByRole("combobox", { name: "Провод" });
  await waitFor(() => expect(screen.getByLabelText("Задание резки")).not.toHaveTextContent("Загрузка"));
  fireEvent.focus(picker);
  const options = within(screen.getByRole("listbox", { name: "Провода" })).getAllByRole("option");
  expect(options[0]).toHaveTextContent("ПВАМ 0,35");
  expect(options[0]).toHaveTextContent("есть настройка");
  expect(options[1]).not.toHaveTextContent("есть настройка");
});

it("an ETK job without a size writes what it has and asks for the rest", async () => {
  const plan: TransferPlan = { ...PLAN, rows: [...PLAN.rows, { key: "strip_back_mm", label: "Зачистка конца", unit: "мм",
    nominal: null, source: "Нет данных", offset: "0,00", value: null, live: "5,00" }], blocking: ["Зачистка конца"] };
  const { posts } = server({ plan });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  await pickJob();
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toEqual({ values: { strip_front_mm: "5,40", total_set: "12" }, confirm_state: 102 });
  expect(screen.getByText(/В ЭТК не задано: Зачистка конца — на станке осталось прежнее/)).toBeInTheDocument();
  expect(screen.getByText("в ЭТК не задано — введите")).toBeInTheDocument();
});

it("fields are read-only with one reason while the machine runs; the busy console is named in operator words", async () => {
  server({ control: { ...CONTROL, problem: "Открыт пульт станка на ПК — запись из ERP недоступна", state: 103 } });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  expect(await screen.findByText("Изменения не записываются: На ПК станка открыт пульт PXS_HMI — закройте его, ERP ждёт")).toBeInTheDocument();
  expect(screen.getByLabelText("Длина провода")).toBeDisabled();
});

it("after a run of own edits saves the machine values as the wire's setting, once", async () => {
  const { posts } = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" equipmentId="u19" />);
  await pickJob();
  await waitFor(() => expect(posts).toHaveLength(1));
  const diameter = screen.getByLabelText("Диаметр провода");
  fireEvent.change(diameter, { target: { value: "0,82" } });
  fireEvent.keyDown(diameter, { key: "Enter" });
  await waitFor(() => expect(screen.getByLabelText("Диаметр провода")).toHaveValue("0,82"), { timeout: 4000 });
  await waitFor(() => expect(screen.queryByText("записывается…")).toBeNull(), { timeout: 4000 });
  await waitFor(() => expect(posts.map((post) => post.url)).toContain("/api/production/equipment-setup/ob019/u19/versions"), { timeout: 8000 });
  const saved = posts.find((post) => post.url.endsWith("/versions"))!;
  expect(saved.url).toBe("/api/production/equipment-setup/ob019/u19/versions");
  expect(saved.body).toMatchObject({ recipe_id: "r1", expected_version: 2, wire: "ПВАМ 0,35",
    values: { strip_front_mm: "5.4", pull_front_mm: "5", strip_back_mm: "5", pull_back_mm: "5", diameter_mm: "0.82", knife_back_mm: "0.1" } });
  expect(await screen.findByText(/Настройка провода ПВАМ 0,35 сохранена \d\d:\d\d/)).toBeInTheDocument();
}, 15000);

it("after a correction asks the plan again and writes the corrected length to the machine", async () => {
  const { posts, state } = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  await pickJob();
  await waitFor(() => expect(posts).toHaveLength(1));
  state.plan = { ...PLAN, rows: PLAN.rows.map((row) => row.key === "length_mm" ? { ...row, offset: "5,00", value: "330,00" } : row) };
  fireEvent.change(await screen.findByLabelText("Получилось, мм"), { target: { value: "320" } });
  fireEvent.click(screen.getByRole("button", { name: "Записать поправку" }));
  await waitFor(() => expect(posts.map((post) => post.url.split("/").pop())).toEqual(["transfer", "correction", "write"]));
  expect(posts[1].body).toEqual({ parameter: "length_mm", case: "machine", measured: "320" });
  expect(posts[2].body).toEqual({ values: { length_mm: "330,00" }, confirm_state: 102 });
  expect(await screen.findByText(/Длина провода 330,00 мм записывается на станок/)).toBeInTheDocument();
});

it("derives the pull-off mode from the machine values and gives the pull-off for a mode", () => {
  expect(pullMode(5, 5)).toBe("full");
  expect(pullMode(5, 6)).toBe("full");
  expect(pullMode(5, 2.5)).toBe("half");
  expect(pullMode(5, 0)).toBe("none");
  expect(pullMode(5, null)).toBeNull();
  expect(pullFor("full", 5)).toBe(7);
  expect(pullMode(5, 7)).toBe("full");
  expect(pullFor("half", 4.15)).toBe(2.08);
  expect(pullFor("none", 5)).toBe(0);
});

it("shows each end's pull-off as a three-way switch read from the machine; a click writes the pull-off at once", async () => {
  const { posts } = server({ live: { pull_back_mm: 0 } });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const front = await screen.findByRole("radiogroup", { name: "Стягивание начала" });
  await waitFor(() => expect(within(front).getByRole("radio", { name: "Стягивание" })).toHaveAttribute("aria-checked", "true"));
  const back = screen.getByRole("radiogroup", { name: "Стягивание конца" });
  expect(within(back).getByRole("radio", { name: "Без" })).toHaveAttribute("aria-checked", "true");
  expect(screen.queryByLabelText("Стягивание начала")).toBe(front);
  expect(screen.getAllByText("5,00 мм").length).toBeGreaterThan(0);
  fireEvent.click(within(front).getByRole("radio", { name: "Полу" }));
  await waitFor(() => expect(posts.map((post) => post.body)).toEqual([{ values: { pull_front_mm: "2,50" }, confirm_state: 102 }]));
  await waitFor(() => expect(within(front).getByRole("radio", { name: "Полу" })).toHaveAttribute("aria-checked", "true"), { timeout: 3000 });
  expect(await screen.findByText("2,50 мм")).toBeInTheDocument();
  // «Стягивание» pulls past the strip: strip + 2,00.
  fireEvent.click(within(back).getByRole("radio", { name: "Стягивание" }));
  await waitFor(() => expect(posts).toHaveLength(2), { timeout: 3000 });
  expect(posts[1].body).toEqual({ values: { pull_back_mm: "7,00" }, confirm_state: 102 });
});

it("a strip edit writes that end's pull-off with it by the current mode", async () => {
  const { posts } = server({ live: { pull_front_mm: 2.5, pull_back_mm: 0 } });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const front = await screen.findByLabelText("Зачистка начала");
  await waitFor(() => expect(front).toHaveValue("5,00"));
  fireEvent.change(front, { target: { value: "6" } });
  fireEvent.keyDown(front, { key: "Enter" });
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toEqual({ values: { strip_front_mm: "6", pull_front_mm: "3,00" }, confirm_state: 102 });
  // «Без» stays 0: only the strip goes out.
  const back = screen.getByLabelText("Зачистка конца");
  fireEvent.change(back, { target: { value: "7" } });
  fireEvent.keyDown(back, { key: "Enter" });
  await waitFor(() => expect(posts).toHaveLength(2), { timeout: 3000 });
  expect(posts[1].body).toEqual({ values: { strip_back_mm: "7" }, confirm_state: 102 });
});
