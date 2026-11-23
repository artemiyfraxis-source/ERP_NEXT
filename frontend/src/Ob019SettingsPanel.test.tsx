import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { productionApi } from "./api";
import type { MachineTaskContext } from "./machinesApi";
import { afterEach, expect, it, vi } from "vitest";
import type { MachineCommand, MachineControl, MachineRun, MachineWindow, TransferPlan } from "./machineControlApi";
import { NO_DATA, Ob019SettingsPanel, PULT_NO_DATA, pullFor, pullMode, SELECT_WAIT_MS, sourceText } from "./Ob019SettingsPanel";
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
const CONFIRMATION = { expected_source: { group_id: "g1", version: 4, length_mm: "325", plan_hash: "a".repeat(64) },
  expected_active_run_id: null };
const PLAN: TransferPlan = {
  ...CONFIRMATION,
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
    { key: "diameter_mm", label: "Диаметр провода", unit: "мм", nominal: "0,79", source: "Ø жилы + 0,15", offset: "0,00", value: "0,79", live: "0,80" },
  ],
  job: { total_set: null, order_quantity: null, order_number: "", product: "PVAM 0,50 L325,00" }, missing: [], blocking: [],
};
const WIRES = [{ label: "ПВАМ 0,35", conductor_mm: "0,52", has_recipe: true }, { label: "ПВАМ 0,50", conductor_mm: "0,64", has_recipe: false }];

/** A fake machine: a write is taken at once and read back into the window on the next poll. */
function server({ control = CONTROL, plan = PLAN, wirePlan = WIRE_PLAN, refuse = "", refuseTransfer = "", live = {}, run = null }: {
  control?: MachineControl; plan?: TransferPlan; wirePlan?: TransferPlan; refuse?: string; refuseTransfer?: string; live?: Record<string, number>;
  run?: MachineRun | null;
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
      if (url.endsWith("/correction")) {
        const command: MachineCommand = { id: "correction-1", status: "queued", status_text: "Ждёт агента станка", tone: "info",
          purpose: "settings", summary: "Поправка длины", fields: [], error: "", created_by: "Слава",
          created_at: "2026-11-23T10:00:00+03:00", finished_at: null };
        commands.unshift(command);
        return json({ offset: null, etk: [], written: "325,00", measured: "320,00", command });
      }
      if (url.endsWith("/versions")) return json({ recipe_id: "r1", version: 3, title: "ПВАМ 0,35", created_at: "2026-11-19T14:05:00+01:00" });
      if (refuse) return json({ detail: refuse }, 409);
      if (refuseTransfer && url.endsWith("/run")) return json({ detail: refuseTransfer }, 409);
      if (url.endsWith("/run") && body.kind === "cutting" && (JSON.stringify(body.expected_source) !== JSON.stringify(state.plan.expected_source)
        || body.expected_active_run_id !== state.plan.expected_active_run_id)) return json({ detail: "План загрузки изменился" }, 409);
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
    if (url.endsWith("/run")) return json({ run, expected_active_run_id: run?.id ?? null, manual: null, link_ok: true, problem: "", can_edit: true, can_control: true });
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

async function confirmLoad() {
  const dialog = await screen.findByRole("dialog", { name: "Загрузить в станок" }, { timeout: 4000 });
  const button = within(dialog).getByRole("button", { name: "Подтвердить загрузку" });
  await waitFor(() => expect(button).toBeEnabled(), { timeout: 4000 });
  fireEvent.click(button);
}

async function pickJob(confirm = true) {
  const pick = await screen.findByLabelText("Задание");
  await waitFor(() => expect(within(pick).getAllByRole("option")).toHaveLength(3));
  fireEvent.change(pick, { target: { value: "g1:325" } });
  await new Promise((resolve) => setTimeout(resolve, SELECT_WAIT_MS + 50));  // a choice goes out after the pause
  if (confirm && screen.queryByRole("dialog", { name: "Загрузить в станок" })) await confirmLoad();
  return pick;
}

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

const incoming: MachineTaskContext = {
  taskId: "t1", operationId: "o1", loadRequested: true,
  action: { etk_position: 2, equipment_id: "u19", inventory_number: "ОБ.019", display_name: "Резка", model: null, card_kind: "ob019",
    connectivity: { state: "ok", fresh: true, last_seen: null }, open_permitted: true, load_permitted: true, can_open: true, can_load: true, blocker: null,
    source: { kind: "ob019", group_id: "g1", version: 4, length_mm: "325", members: [
      { task_id: "t1", operation_id: "o1", item: "КБ1", quantity: "7" }, { task_id: "t2", operation_id: "o2", item: "КБ2", quantity: "5" }] } },
};
const incomingActions = () => ({ task_id: "t1", operation_id: "o1", task_version: 3, actions: [structuredClone(incoming.action)] });

it("incoming group/length only preview; load=1 requires confirmation with changed values and group quantities", async () => {
  const { posts } = server();
  vi.spyOn(productionApi, "operationMachineActions").mockResolvedValue(incomingActions());
  render(<Ob019SettingsPanel inventory="ОБ.019" equipmentId="u19" incoming={incoming} />);
  const dialog = await screen.findByRole("dialog", { name: "Загрузить в станок" });
  expect(screen.getByLabelText("Задание")).toHaveValue("g1:325");
  expect(within(dialog).getByText("КБ1 · 7 шт")).toBeVisible();
  expect(within(dialog).getByText("КБ2 · 5 шт")).toBeVisible();
  expect(within(dialog).getByRole("table", { name: "Изменённые значения" })).toHaveTextContent("5,40 мм");
  expect(within(dialog).queryByRole("rowheader", { name: "Длина провода" })).not.toBeInTheDocument();
  await act(() => new Promise(resolve => setTimeout(resolve, SELECT_WAIT_MS + 50)));
  expect(posts).toEqual([]);
  fireEvent.click(within(dialog).getByRole("button", { name: "Подтвердить загрузку" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toEqual({ url: "/api/production/machines/card/ОБ.019/run", body: { kind: "cutting", group_id: "g1", length_mm: "325", ...CONFIRMATION, confirm_state: 102 } });
  expect(productionApi.operationMachineActions).toHaveBeenCalledWith("t1", "o1");
});

it.each([
  { value: "6", blocker: "В ЭТК не задано: Зачистка конца", permitted: true, sends: true },
  { value: "5", blocker: "В ЭТК не задано: Зачистка конца", permitted: true, sends: true },
  { value: "6", blocker: "Нет связи", permitted: true, sends: false },
  { value: "6", blocker: "Нет права загрузки задания в станок", permitted: false, sends: false },
])("stages an incoming missing strip size ($value); readiness: $blocker", async ({ value, blocker, permitted, sends }) => {
  const plan: TransferPlan = { ...PLAN, rows: [...PLAN.rows, { key: "strip_back_mm", label: "Зачистка конца", unit: "мм",
    nominal: null, source: "Нет данных", offset: "0,00", value: null, live: "5,00" }], blocking: ["Зачистка конца"] };
  const { posts } = server({ plan });
  vi.spyOn(productionApi, "operationMachineActions").mockResolvedValue({ ...incomingActions(), actions: [
    { ...incoming.action, can_load: false, load_permitted: permitted, blocker }] });
  render(<Ob019SettingsPanel inventory="ОБ.019" equipmentId="u19" incoming={incoming} />);
  const initial = await screen.findByRole("dialog", { name: "Загрузить в станок" });
  expect(within(initial).getByRole("button", { name: "Подтвердить загрузку" })).toBeDisabled();
  fireEvent.click(within(initial).getByRole("button", { name: "Отмена" }));
  const back = screen.getByLabelText("Зачистка конца");
  fireEvent.change(back, { target: { value } });
  fireEvent.keyDown(back, { key: "Enter" });
  const dialog = await screen.findByRole("dialog", { name: "Загрузить в станок" });
  const row = within(dialog).getByRole("rowheader", { name: "Зачистка конца" }).closest("tr")!;
  expect(within(row).getAllByRole("cell")[1]).toHaveTextContent(`${value} мм`);
  expect(posts).toEqual([]);
  fireEvent.click(within(dialog).getByRole("button", { name: "Отмена" }));
  expect(posts).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "Загрузить в станок" }));
  const reopened = await screen.findByRole("dialog", { name: "Загрузить в станок" });
  const stagedRow = within(reopened).getByRole("rowheader", { name: "Зачистка конца" }).closest("tr")!;
  expect(within(stagedRow).getAllByRole("cell")[1]).toHaveTextContent(`${value} мм`);
  await confirmLoad();
  if (sends) {
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toMatchObject({ url: "/api/production/machines/card/ОБ.019/run", body: {
      kind: "cutting", group_id: "g1", length_mm: "325", values: { strip_back_mm: value, total_set: "12" }, ...CONFIRMATION } });
  } else {
    expect(await within(reopened).findByText(blocker)).toBeVisible();
    expect(posts).toEqual([]);
  }
});

it("incoming selection, cancellation and reopening never write; missing group never selects a different job", async () => {
  const { posts } = server();
  const first = render(<Ob019SettingsPanel inventory="ОБ.019" equipmentId="u19" incoming={incoming} />);
  const dialog = await screen.findByRole("dialog", { name: "Загрузить в станок" });
  fireEvent.click(within(dialog).getByRole("button", { name: "Отмена" }));
  fireEvent.change(screen.getByLabelText("Задание"), { target: { value: "g1:325" } });
  await act(() => new Promise(resolve => setTimeout(resolve, SELECT_WAIT_MS + 50)));
  first.unmount();
  render(<Ob019SettingsPanel inventory="ОБ.019" equipmentId="u19" incoming={{ ...incoming, loadRequested: false,
    action: { ...incoming.action, source: { ...incoming.action.source!, kind: "ob019", group_id: "missing", version: 4, length_mm: "325", members: [] } } }} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("больше недоступно");
  expect(screen.getByLabelText("Задание")).toHaveValue("");
  expect(screen.queryByRole("dialog", { name: "Загрузить в станок" })).not.toBeInTheDocument();
  expect(posts).toEqual([]);
});

it.each(["source", "active run"])("409 after a changed %s refreshes the preview and requires a new click without retrying", async (change) => {
  const { posts, gets, state } = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  await pickJob(false);
  const fresh = change === "source"
    ? { ...PLAN, quantity: 9, job: { ...PLAN.job, total_set: 9 }, expected_source: { ...CONFIRMATION.expected_source, version: 5, plan_hash: "b".repeat(64) } }
    : { ...PLAN, expected_active_run_id: "new-run" };
  state.plan = fresh;
  await confirmLoad();
  const dialog = await screen.findByRole("dialog", { name: "Загрузить в станок" });
  await within(dialog).findByText(/План загрузки изменился.*подтвердите загрузку ещё раз/);
  expect(gets.filter((url) => url.includes("/transfer?group_id"))).toHaveLength(2);
  expect(posts).toHaveLength(1);
  expect(posts[0].body).toMatchObject(CONFIRMATION);
  expect(within(dialog).getByText(change === "source" ? /ГР-0007.*9 шт$/ : /Задание на станке: Открытое задание/)).toBeVisible();
  await act(() => new Promise(resolve => setTimeout(resolve, SELECT_WAIT_MS + 50)));
  expect(posts).toHaveLength(1);
  await confirmLoad();
  await waitFor(() => expect(posts).toHaveLength(2));
  expect(posts[1].body).toMatchObject({ expected_source: fresh.expected_source, expected_active_run_id: fresh.expected_active_run_id });
});

it("legacy selection cancelled or missing atomic guards never writes", async () => {
  const { posts, state } = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  await pickJob(false);
  fireEvent.click(within(screen.getByRole("dialog", { name: "Загрузить в станок" })).getByRole("button", { name: "Отмена" }));
  await act(() => new Promise(resolve => setTimeout(resolve, SELECT_WAIT_MS + 50)));
  expect(posts).toEqual([]);
  state.plan = { ...PLAN, expected_source: undefined, expected_active_run_id: undefined };
  await pickJob(false);
  const dialog = await screen.findByRole("dialog", { name: "Загрузить в станок" });
  expect(within(dialog).getByRole("button", { name: "Подтвердить загрузку" })).toBeDisabled();
  expect(posts).toEqual([]);
});

it("incoming confirmation detects a new hash even when the displayed numeric values are unchanged", async () => {
  const { posts, state } = server();
  vi.spyOn(productionApi, "operationMachineActions").mockResolvedValue(incomingActions());
  render(<Ob019SettingsPanel inventory="ОБ.019" equipmentId="u19" incoming={incoming} />);
  await screen.findByRole("dialog", { name: "Загрузить в станок" });
  state.plan = { ...PLAN, expected_source: { ...CONFIRMATION.expected_source, plan_hash: "b".repeat(64) } };
  await confirmLoad();
  await within(screen.getByRole("dialog", { name: "Загрузить в станок" })).findByText(/Параметры задания изменились/);
  expect(posts).toEqual([]);
  await confirmLoad();
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body.expected_source).toEqual(state.plan.expected_source);
});

it("a stale incoming source is rejected at confirmation without writing", async () => {
  const { posts } = server();
  vi.spyOn(productionApi, "operationMachineActions").mockResolvedValue({ ...incomingActions(), actions: [{ ...incoming.action, source: null, can_load: false, blocker: "Группа изменилась" }] });
  render(<Ob019SettingsPanel inventory="ОБ.019" equipmentId="u19" incoming={incoming} />);
  fireEvent.click(within(await screen.findByRole("dialog", { name: "Загрузить в станок" })).getByRole("button", { name: "Подтвердить загрузку" }));
  expect(await screen.findByText("Группа изменилась")).toBeVisible();
  expect(posts).toEqual([]);
});

it.each(["Back", "cancel"])("%s while confirmation validation is pending cancels loading before any write", async (way) => {
  const { posts } = server();
  let finish!: (value: ReturnType<typeof incomingActions>) => void;
  vi.spyOn(productionApi, "operationMachineActions").mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const view = render(<Ob019SettingsPanel inventory="ОБ.019" equipmentId="u19" incoming={incoming} />);
  fireEvent.click(within(await screen.findByRole("dialog", { name: "Загрузить в станок" })).getByRole("button", { name: "Подтвердить загрузку" }));
  if (way === "Back") view.unmount();
  else fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
  await act(async () => { finish(incomingActions()); });
  expect(posts).toEqual([]);
});

it("readiness recovers after opening offline, and a changed preview requires a second confirmation", async () => {
  const { posts, state } = server();
  vi.spyOn(productionApi, "operationMachineActions").mockResolvedValue(incomingActions());
  render(<Ob019SettingsPanel inventory="ОБ.019" equipmentId="u19" incoming={{ ...incoming, action: { ...incoming.action, can_load: false, blocker: "Нет связи" } }} />);
  const dialog = await screen.findByRole("dialog", { name: "Загрузить в станок" });
  const button = within(dialog).getByRole("button", { name: "Подтвердить загрузку" });
  expect(button).toBeEnabled();
  state.plan = { ...PLAN, quantity: 11, job: { ...PLAN.job, total_set: 11 } };
  fireEvent.click(button);
  await within(dialog).findByText(/Параметры задания изменились/);
  expect(posts).toEqual([]);
  fireEvent.click(button);
  await waitFor(() => expect(posts).toHaveLength(1));
});

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
  expect(sourceText(row("Ø жилы + 0,15", "0,79", "0,79", "0,00"), "0,64", null)).toBe("0,79 = Ø жилы 0,64 + 0,15");
  // With a machine correction on top the sum still names the base; the correction is shown apart (not «0,62 = 0,52 + 0,15»).
  expect(sourceText(row("Ø жилы + 0,15", "0,62", "0,67", "-0,05"), "0,52", null)).toBe("0,67 = Ø жилы 0,52 + 0,15 + поправка -0,05");
  expect(sourceText(row("Рецепт, версия 2", "5,00", "5,00", null), null, "2026-11-18T12:00:00+01:00")).toBe("из настройки 18.11");
  expect(sourceText(row("ЭТК", "5,00", "5,00", "0,00"), null, null)).toBe("из ЭТК");
  expect(sourceText(row("ЭТК", "5,40", "5,00", "0,40"), null, null)).toBe("ЭТК 5,00 + поправка +0,40");
});

it("keeps the drawing on the card and opens all parameter groups in a separate window", async () => {
  server();
  const { rerender } = render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const drawing = screen.getByRole("region", { name: "Провод: длина и зачистка" });
  await waitFor(() => expect(within(drawing).getByLabelText("Длина провода")).toHaveValue("325,00"));
  expect(within(drawing).getByLabelText("Диаметр провода")).toHaveValue("0,80");
  // «Тираж» is pieces in this job (target − counter): no live counter in this render, so it is unknown, not D22 = 300.
  expect(screen.getByLabelText("Тираж")).toHaveValue("");
  expect(screen.queryByText(/счётчик станка|цель/)).toBeNull();  // no raw counter / target under «Тираж»
  expect(screen.queryByText(/на станке —/)).toBeNull();
  expect(screen.queryByRole("button", { name: /Записать на станок/ })).toBeNull();
  expect(screen.queryByText(/Остальные параметры/)).toBeNull();
  rerender(<Ob019SettingsPanel inventory="ОБ.019" toolsWindow="parameters" />);
  expect(screen.getByRole("dialog", { name: "Все параметры" })).toBeInTheDocument();
  expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Задание", "Провод и зачистка", "Скорости"]);
  fireEvent.click(screen.getByRole("tab", { name: "Провод и зачистка" }));
  expect(screen.getAllByRole("table")[0]).toHaveTextContent("Стягивание начала");
  fireEvent.click(screen.getByRole("tab", { name: "Скорости" }));
  expect(screen.getByLabelText("Подъём подающих роликов")).toBeInTheDocument();
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
  expect(await screen.findByText("отказ: Открыт пульт станка на ПК — закройте его, чтобы ERP могла управлять станком")).toBeInTheDocument();
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

it("picking an ETK job writes only after confirmation and tells where each value comes from", async () => {
  const { posts } = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  await pickJob(false);
  expect(posts).toEqual([]);
  await confirmLoad();
  await waitFor(() => expect(posts).toHaveLength(1));
  // The job goes as a run of the machine (production/machine_runs.py): only such a run opens «Пуск».
  expect(posts[0]).toEqual({ url: "/api/production/machines/card/ОБ.019/run",
    body: { kind: "cutting", group_id: "g1", length_mm: "325", ...CONFIRMATION, confirm_state: 102 } });
  expect(screen.queryByRole("button", { name: /Передать на станок|Записать на станок/ })).toBeNull();
  expect(await screen.findByText("ЭТК 5,00 + поправка +0,40", {}, { timeout: 3000 })).toBeInTheDocument();
});

const LIVE = { state: { code: 107, text: "Тираж готов", tone: "ok" as const }, alarm: { code: 5, text: "Тираж готов", tone: "info" as const },
  mode: { code: 2, text: "Автомат" }, job: { total_set: 582, total_finished: 570, batch_set: 0, batch_finished: 0, uph: 0, program: 1,
    order_quantity: null, order_finished: null, order_number: null, product: null, order_running: false },
  wire: { length_mm: 325, strip_front_mm: 5, pull_front_mm: 0, strip_back_mm: 5, pull_back_mm: 0, diameter_mm: 0.8, knife_back_mm: 0.1 } };

it("takes «Тираж» without an open job as pieces left to the target (target − counter), no raw counter under it", async () => {
  const { posts } = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" live={LIVE} />);
  expect(await screen.findByText("Тираж (штук в этом задании)")).toBeInTheDocument();
  expect(screen.queryByText(/счётчик станка|цель/)).toBeNull();
  const tirazh = screen.getByLabelText("Тираж");
  await waitFor(() => expect(tirazh).toHaveValue("12"));
  fireEvent.change(tirazh, { target: { value: "12" } });
  fireEvent.keyDown(tirazh, { key: "Enter" });  // the machine already aims at 12 more: nothing to write
  fireEvent.change(tirazh, { target: { value: "20" } });
  fireEvent.keyDown(tirazh, { key: "Enter" });
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toEqual({ url: "/api/production/machines/card/ОБ.019/write", body: { values: { total_set: "20" }, confirm_state: 102 } });
});

it("a «Тираж» edit with a job picked goes as that job's run, so «Пуск» stays allowed", async () => {
  const { posts } = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" live={{ ...LIVE, state: { code: 102, text: "Готов, ждёт пуска", tone: "ok" as const } }} />);
  await pickJob();
  await waitFor(() => expect(posts).toHaveLength(1));
  await waitFor(() => expect(screen.queryByText("записывается…")).toBeNull(), { timeout: 4000 });
  const tirazh = screen.getByLabelText("Тираж");
  fireEvent.change(tirazh, { target: { value: "30" } });
  fireEvent.keyDown(tirazh, { key: "Enter" });
  await confirmLoad();
  await waitFor(() => expect(posts).toHaveLength(2), { timeout: 4000 });
  expect(posts[1]).toEqual({ url: "/api/production/machines/card/ОБ.019/run",
    body: { kind: "cutting", group_id: "g1", length_mm: "325", values: { total_set: "30" }, ...CONFIRMATION, confirm_state: 102 } });
}, 10000);

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
  expect(posts[0].body).toEqual({ values: { diameter_mm: "0,79" }, confirm_state: 102 });
  expect(await screen.findByText("0,79 = Ø жилы 0,64 + 0,15", {}, { timeout: 3000 })).toBeInTheDocument();
});

it("lists the wires with a saved setting first and marks them", async () => {
  server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const picker = await screen.findByRole("combobox", { name: "Провод" });
  await waitFor(() => expect(screen.getByLabelText("Задание")).not.toHaveTextContent("Загрузка"));
  fireEvent.focus(picker);
  const options = within(screen.getByRole("listbox", { name: "Провода" })).getAllByRole("option");
  expect(options[0]).toHaveTextContent("ПВАМ 0,35");
  expect(options[0]).toHaveTextContent("есть настройка");
  expect(options[1]).not.toHaveTextContent("есть настройка");
});

it("an ETK job without a size writes nothing until the size is typed, then goes as one run with every value", async () => {
  const plan: TransferPlan = { ...PLAN, rows: [...PLAN.rows, { key: "strip_back_mm", label: "Зачистка конца", unit: "мм",
    nominal: null, source: "Нет данных", offset: "0,00", value: null, live: "5,00" }], blocking: ["Зачистка конца"] };
  const { posts } = server({ plan });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  await pickJob();
  expect(await screen.findByText(/В ЭТК не задано: Зачистка конца — введите на чертеже и подтвердите загрузку/)).toBeInTheDocument();
  expect(screen.getByText("в ЭТК не задано — введите")).toBeInTheDocument();
  // Another edit waits with the job: the old machine value must not stand in for the missing size.
  const length = screen.getByLabelText("Длина провода");
  fireEvent.change(length, { target: { value: "326" } });
  fireEvent.keyDown(length, { key: "Enter" });
  expect(await screen.findByText("уйдёт с заданием, когда введены все размеры")).toBeInTheDocument();
  expect(posts).toEqual([]);
  const back = screen.getByLabelText("Зачистка конца");
  fireEvent.change(back, { target: { value: "6" } });
  fireEvent.keyDown(back, { key: "Enter" });
  expect(posts).toEqual([]);
  await confirmLoad();
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toEqual({ url: "/api/production/machines/card/ОБ.019/run", body: { kind: "cutting", group_id: "g1", length_mm: "325",
    values: { strip_front_mm: "5,40", length_mm: "326", strip_back_mm: "6", pull_back_mm: "8,00", total_set: "12" }, ...CONFIRMATION, confirm_state: 102 } });
});

it("fields are read-only with one reason while the machine runs; the busy console is named in operator words", async () => {
  server({ control: { ...CONTROL, problem: "Открыт пульт станка на ПК — запись из ERP недоступна", state: 103 } });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  expect(await screen.findByText("Изменения не записываются: Открыт пульт станка на ПК — закройте его, чтобы ERP могла управлять станком")).toBeInTheDocument();
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

it("queues machine length correction and never writes a recalculated D32 even if a stale plan has an ERP offset", async () => {
  const { posts, state } = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  await pickJob();
  await waitFor(() => expect(posts).toHaveLength(1));
  state.plan = { ...PLAN, rows: PLAN.rows.map((row) => row.key === "length_mm" ? { ...row, offset: "5,00", value: "330,00" } : row) };
  fireEvent.change(await screen.findByLabelText("Получилось, мм"), { target: { value: "320" } });
  fireEvent.click(screen.getByRole("button", { name: "Записать поправку" }));
  await waitFor(() => expect(posts.map((post) => post.url.split("/").pop())).toEqual(["run", "correction"]));
  expect(posts[1].body).toEqual({ parameter: "length_mm", case: "machine", measured: "320", seen: "325,00" });
  expect(await screen.findByText("Поправка длины: отправлено…")).toBeInTheDocument();
  expect(screen.getByLabelText("Длина провода")).toHaveValue("325,00");
  expect(posts.filter((post) => post.url.endsWith("/write"))).toHaveLength(0);
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

it("rounds half a strip like the server (0,005 up) and caps the full pull-off at 655,35; other values are the operator's own", () => {
  expect(pullFor("half", 4.05)).toBe(2.03);  // 2,025 -> 2,03 (server ROUND_HALF_UP)
  expect(pullFor("half", 0.05)).toBe(0.03);
  expect(pullFor("half", 1.15)).toBe(0.58);  // 1,15 x 0,5 in floats is 0,574999...
  expect(pullFor("full", 654)).toBe(655.35);
  expect(pullMode(4.05, 2.03)).toBe("half");
  expect(pullMode(4.05, 2.02)).toBe("own");
  expect(pullMode(5, 3)).toBe("own");
  expect(pullMode(5, 4.99)).toBe("own");
  expect(pullMode(null, 3)).toBe("own");
});

it("an own pull-off is shown as «своё» and a strip edit leaves it as it is", async () => {
  const { posts } = server({ live: { pull_front_mm: 3 } });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const front = await screen.findByRole("radiogroup", { name: "Стягивание начала" });
  await waitFor(() => expect(screen.getByText("своё: 3,00 мм")).toBeInTheDocument());
  expect(within(front).getAllByRole("radio").filter((radio) => radio.getAttribute("aria-checked") === "true")).toEqual([]);
  const strip = screen.getByLabelText("Зачистка начала");
  fireEvent.change(strip, { target: { value: "6" } });
  fireEvent.keyDown(strip, { key: "Enter" });
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toEqual({ values: { strip_front_mm: "6" }, confirm_state: 102 });
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

/** A machine whose write stays «taken» until `release()` and shows up in the control poll one poll late — the order
 *  the real queue gives. The server keeps one open write per machine and refuses a second. */
function slowServer() {
  const posts: { url: string; body: Record<string, unknown> }[] = [];
  const window = machineWindow();
  const commands: (MachineCommand & { values: Record<string, string>; shownFrom: number })[] = [];
  let polls = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = decodeURIComponent(String(input));
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      posts.push({ url, body });
      if (commands.some((row) => row.status === "taken")) return json({ detail: "На станок уже идёт запись — дождитесь её результата" }, 409);
      commands.unshift({ id: `c${posts.length}`, status: "taken", status_text: "Записывается", tone: "info", purpose: "settings", summary: "Запись",
        fields: [], error: "", created_by: "Слава", created_at: "2026-11-19T10:00:00+03:00", finished_at: null, values: body.values, shownFrom: polls + 2 });
      return json({ ...commands[0], status: "queued" });
    }
    if (url.endsWith("/control")) {
      polls += 1;
      return json({ ...CONTROL, commands: commands.filter((row) => row.shownFrom <= polls) });
    }
    if (url.endsWith("/transfer")) return json({ choices: [], wires: [] });
    return json(window);
  });
  const release = () => {
    for (const row of commands) {
      if (row.status !== "taken") continue;
      row.status = "done";
      row.finished_at = "2026-11-19T10:00:02+03:00";
      for (const group of window.settings) for (const item of group.fields) {
        if (item.key && item.key in row.values) item.value = Number(row.values[item.key].replace(",", "."));
      }
    }
  };
  return { posts, release };
}

it("a second edit waits until the poll shows the first write finished, then goes — no refusal", async () => {
  const { posts, release } = slowServer();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const length = await screen.findByLabelText("Длина провода");
  await waitFor(() => expect(length).toHaveValue("325,00"));
  fireEvent.change(length, { target: { value: "326" } });
  fireEvent.keyDown(length, { key: "Enter" });
  const diameter = screen.getByLabelText("Диаметр провода");
  fireEvent.change(diameter, { target: { value: "0,82" } });
  fireEvent.keyDown(diameter, { key: "Enter" });
  await waitFor(() => expect(posts).toHaveLength(1));
  // The first write is on its way (the poll does not even show it yet): the second waits.
  await new Promise((resolve) => setTimeout(resolve, 2500));
  expect(posts).toHaveLength(1);
  release();
  await waitFor(() => expect(posts).toHaveLength(2), { timeout: 4000 });
  expect(posts[1].body).toEqual({ values: { diameter_mm: "0,82" }, confirm_state: 102 });
  expect(screen.queryByText(/отказ/)).toBeNull();
}, 15000);

it("arrowing through the job list previews only the final choice, then waits for confirmation", async () => {
  const { posts, gets } = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const pick = await screen.findByLabelText("Задание");
  await waitFor(() => expect(within(pick).getAllByRole("option")).toHaveLength(3));
  fireEvent.change(pick, { target: { value: "g1:325" } });
  fireEvent.change(pick, { target: { value: "" } });
  fireEvent.change(pick, { target: { value: "g1:325" } });
  await new Promise((resolve) => setTimeout(resolve, SELECT_WAIT_MS / 2));
  expect(gets.filter((url) => url.includes("/transfer?group_id"))).toEqual([]);
  expect(posts).toEqual([]);
  await confirmLoad();
  await waitFor(() => expect(posts).toHaveLength(1), { timeout: 3000 });
  expect(gets.filter((url) => url.includes("/transfer?group_id"))).toHaveLength(1);
  expect(posts[0].url).toBe("/api/production/machines/card/ОБ.019/run");
});

it("the mode list writes the final choice once, after the pause", async () => {
  const { posts } = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  await waitFor(() => expect(screen.getByLabelText("Режим")).toHaveValue("2"));  // a select once the window is read
  const mode = screen.getByLabelText("Режим");
  fireEvent.change(mode, { target: { value: "1" } });
  fireEvent.change(mode, { target: { value: "0" } });
  expect(mode).toHaveValue("0");
  expect(posts).toEqual([]);
  await waitFor(() => expect(posts).toHaveLength(1), { timeout: 3000 });
  expect(posts[0].body).toEqual({ values: { mode: "0" }, confirm_state: 102 });
});

it("edits outside the wire parameters never save the wire's setting", async () => {
  const { posts } = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" equipmentId="u19" />);
  await pickJob();
  await waitFor(() => expect(posts).toHaveLength(1));
  const total = screen.getByLabelText("Тираж");
  fireEvent.change(total, { target: { value: "20" } });
  fireEvent.keyDown(total, { key: "Enter" });
  await confirmLoad();
  const length = screen.getByLabelText("Длина провода");
  fireEvent.change(length, { target: { value: "330" } });
  fireEvent.keyDown(length, { key: "Enter" });
  await waitFor(() => expect(screen.getByLabelText("Длина провода")).toHaveValue("330,00"), { timeout: 4000 });
  await new Promise((resolve) => setTimeout(resolve, 6000));
  expect(posts.map((post) => post.url)).not.toContain("/api/production/equipment-setup/ob019/u19/versions");
}, 15000);

it("after a refused transfer of the job an own edit does not save the wire's setting", async () => {
  const { posts } = server({ refuseTransfer: "Станок не в ожидании" });
  render(<Ob019SettingsPanel inventory="ОБ.019" equipmentId="u19" />);
  await pickJob();
  await waitFor(() => expect(posts).toHaveLength(1));
  expect((await screen.findAllByText("отказ: Станок не в ожидании")).length).toBeGreaterThan(0);
  fireEvent.click(within(screen.getByRole("dialog", { name: "Загрузить в станок" })).getByRole("button", { name: "Отмена" }));
  const diameter = screen.getByLabelText("Диаметр провода");
  fireEvent.change(diameter, { target: { value: "0,82" } });
  fireEvent.keyDown(diameter, { key: "Enter" });
  await waitFor(() => expect(screen.getByLabelText("Диаметр провода")).toHaveValue("0,82"), { timeout: 4000 });
  expect(await screen.findByText(/Настройка провода не сохранена: значения задания или провода не записаны/, {}, { timeout: 8000 })).toBeInTheDocument();
  expect(posts.map((post) => post.url)).not.toContain("/api/production/equipment-setup/ob019/u19/versions");
}, 15000);

it("while the machine cuts the drawing is closed with one reason, not a refusal per field", async () => {
  const { posts } = server({ control: { ...CONTROL, state: 103,
    problem: "Записывать можно, когда станок готов и ждёт пуска или тираж готов; сейчас: Работает" } });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  expect(await screen.findByText("Станок режет — правка после остановки")).toBeInTheDocument();
  expect(screen.getAllByText("Станок режет — правка после остановки")).toHaveLength(1);
  expect(screen.queryByText(/Записывать можно, когда/)).toBeNull();
  expect(screen.getByLabelText("Длина провода")).toBeDisabled();
  expect(screen.getByLabelText("Диаметр провода")).toBeDisabled();
  expect(within(screen.getByRole("radiogroup", { name: "Стягивание начала" })).getByRole("radio", { name: "Полу" })).toBeDisabled();
  expect(posts).toEqual([]);
});

it("the card's own live state closes the drawing before this panel's poll sees the run", async () => {
  server();
  const live = { state: { code: 103, text: "Работает", tone: "ok" } } as unknown as Parameters<typeof Ob019SettingsPanel>[0]["live"];
  render(<Ob019SettingsPanel inventory="ОБ.019" live={live} />);
  expect(await screen.findByText("Станок режет — правка после остановки")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByLabelText("Длина провода")).toHaveValue("325,00"));
  expect(screen.getByLabelText("Длина провода")).toBeDisabled();
});

const OPEN_RUN: MachineRun = { id: "w1", kind: "cutting", label: "ГР-0007 · ПВАМ 0,35 · 325,00 мм", group_id: "g9", length_mm: "150",
  job_id: null, quantity: 120, write: { status: "done", status_text: "Записано", tone: "ok", error: "" },
  verified_at: "2026-11-20T01:30:00+01:00", closed_at: null, closed_why: "", machine_count: null, closed_reset: false,
  created_by: "Слава", created_at: "2026-11-20T01:30:00+01:00", stage: "ready", counter_start: 523, counter: 570, done: 47,
  reset: false, state: 102, alarm: 0, total_set: 643 };

it.each([true, false])("a different active run blocks confirmation and offers its card (incoming: %s)", async (fromInstruction) => {
  const { posts } = server({ run: OPEN_RUN });
  render(<Ob019SettingsPanel inventory="ОБ.019" equipmentId="u19" incoming={fromInstruction ? incoming : undefined} />);
  if (!fromInstruction) {
    const picker = await screen.findByLabelText("Задание");
    picker.focus();
    await pickJob(false);
  }
  const dialog = await screen.findByRole("dialog", { name: "Загрузить в станок" });
  expect(within(dialog).getByText(`На станке идёт задание ${OPEN_RUN.label} — сначала завершите или прервите его`)).toBeVisible();
  expect(within(dialog).getByRole("button", { name: "Подтвердить загрузку" })).toBeDisabled();
  expect(posts).toEqual([]);
  fireEvent.click(within(dialog).getByRole("button", { name: "К заданию на станке" }));
  expect(screen.queryByRole("dialog", { name: "Загрузить в станок" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("Текущее задание")).toHaveFocus();
  expect(screen.getByRole("button", { name: "Завершить" })).toBeVisible();
});

it("incoming same-job quantity sends the remaining pieces and keeps raw staged edits", async () => {
  const run = { ...OPEN_RUN, group_id: "g1", length_mm: "325" };
  const confirmation = { ...CONFIRMATION, expected_active_run_id: run.id };
  const { posts } = server({ run, plan: { ...PLAN, ...confirmation } });
  vi.spyOn(productionApi, "operationMachineActions").mockResolvedValue(incomingActions());
  render(<Ob019SettingsPanel inventory="ОБ.019" equipmentId="u19" incoming={incoming} />);
  const initial = await screen.findByRole("dialog", { name: "Загрузить в станок" });
  fireEvent.click(within(initial).getByRole("button", { name: "Отмена" }));
  const total = screen.getByLabelText("Тираж");
  fireEvent.change(total, { target: { value: "150" } });
  fireEvent.keyDown(total, { key: "Enter" });
  const preview = await screen.findByRole("dialog", { name: "Загрузить в станок" });
  fireEvent.click(within(preview).getByRole("button", { name: "Отмена" }));
  const back = screen.getByLabelText("Зачистка конца");
  fireEvent.change(back, { target: { value: "6" } });
  fireEvent.keyDown(back, { key: "Enter" });
  expect(posts).toEqual([]);
  await confirmLoad();
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toMatchObject({ kind: "cutting", group_id: "g1", length_mm: "325",
    values: { total_set: "103", strip_back_mm: "6" }, ...confirmation });
});

it("with a job open «Тираж» requires confirmation of the pieces left and the active run", async () => {
  const confirmation = { expected_source: { ...CONFIRMATION.expected_source, group_id: "g9", length_mm: "150" }, expected_active_run_id: "w1" };
  const { posts } = server({ run: OPEN_RUN, plan: { ...PLAN, group_id: "g9", length_mm: "150", ...confirmation } });
  render(<Ob019SettingsPanel inventory="ОБ.019" live={{ ...LIVE, state: { code: 102, text: "Готов, ждёт пуска", tone: "ok" as const } }} />);
  const tirazh = await screen.findByLabelText("Тираж");
  await waitFor(() => expect(tirazh).toHaveValue("120"));
  expect(screen.getByText("сделано 47 · осталось 73")).toBeInTheDocument();
  fireEvent.change(tirazh, { target: { value: "40" } });
  fireEvent.keyDown(tirazh, { key: "Enter" });
  expect(await screen.findByText("отказ: уже сделано 47 — тираж больше")).toBeInTheDocument();
  fireEvent.change(tirazh, { target: { value: "150" } });
  fireEvent.keyDown(tirazh, { key: "Enter" });
  expect(posts).toEqual([]);
  await confirmLoad();
  await waitFor(() => expect(posts).toHaveLength(1));
  // Nothing picked after the card was opened: the edit goes as the open job's run (its target follows, «Пуск» stays
  // allowed). 150 for the job, 47 done: the machine is asked for 103 more (target = counter + 103 = start + 150).
  expect(posts[0]).toEqual({ url: "/api/production/machines/card/ОБ.019/run",
    body: { kind: "cutting", group_id: "g9", length_mm: "150", values: { total_set: "103" }, ...confirmation, confirm_state: 102 } });
});

it("a «Тираж» of the open job without the machine counter is not written: how many are done is unknown", async () => {
  const { posts } = server({ run: { ...OPEN_RUN, stage: "nolink", done: null, counter: null } });
  render(<Ob019SettingsPanel inventory="ОБ.019" live={{ ...LIVE, state: { code: 102, text: "Готов, ждёт пуска", tone: "ok" as const } }} />);
  const tirazh = await screen.findByLabelText("Тираж");
  await waitFor(() => expect(tirazh).toHaveValue("120"));
  fireEvent.change(tirazh, { target: { value: "150" } });
  fireEvent.keyDown(tirazh, { key: "Enter" });
  expect(await screen.findByText("отказ: счётчик станка не прочитан — повторите, когда станок на связи")).toBeInTheDocument();
  expect(posts).toEqual([]);
});

it.each([false, true])("same-run confirmation keeps supplied sizes; a conflict refresh revalidates missing sizes (%s)", async (conflict) => {
  const plan: TransferPlan = { ...PLAN, group_id: "g9", length_mm: "150", expected_active_run_id: OPEN_RUN.id,
    expected_source: { ...CONFIRMATION.expected_source, group_id: "g9", length_mm: "150" },
    rows: [...PLAN.rows, { key: "strip_back_mm", label: "Зачистка конца", unit: "мм", nominal: null,
      value: null, live: "6,00", offset: "0,00", source: "Нет данных" }], blocking: ["Зачистка конца"] };
  const { posts, state } = server({ run: OPEN_RUN, plan, live: { length_mm: 326 } });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const total = await screen.findByLabelText("Тираж");
  await waitFor(() => expect(total).toHaveValue("120"));
  fireEvent.change(total, { target: { value: "150" } });
  fireEvent.keyDown(total, { key: "Enter" });
  const dialog = await screen.findByRole("dialog", { name: "Загрузить в станок" });
  expect(within(dialog).queryByRole("rowheader", { name: "Длина провода" })).toBeNull();
  expect(within(dialog).queryByRole("rowheader", { name: "Зачистка начала" })).toBeNull();
  expect(posts).toEqual([]);
  if (conflict) state.plan = { ...plan, expected_source: { ...plan.expected_source!, plan_hash: "b".repeat(64) } };
  await confirmLoad();
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body.values).toEqual({ total_set: "103" });
  if (conflict) {
    await within(dialog).findByText(/План загрузки изменился.*подтвердите загрузку ещё раз/);
    expect(within(dialog).getByText("Не заданы параметры: Зачистка конца")).toBeVisible();
    expect(within(dialog).getByRole("button", { name: "Подтвердить загрузку" })).toBeDisabled();
    expect(posts).toHaveLength(1);
  }
});

it("a submitted load cannot be dismissed before the POST result settles", async () => {
  const { posts } = server();
  const base = vi.mocked(globalThis.fetch).getMockImplementation()!;
  let release!: () => void;
  vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
    if (init?.method === "POST" && String(input).endsWith("/run")) await new Promise<void>(resolve => { release = resolve; });
    return base(input, init);
  });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  await pickJob(false);
  await confirmLoad();
  const dialog = screen.getByRole("dialog", { name: "Загрузить в станок" });
  expect(within(dialog).getByRole("button", { name: "Отмена" })).toBeDisabled();
  fireEvent.keyDown(dialog, { key: "Escape" });
  expect(dialog).toBeVisible();
  await act(async () => release());
  await waitFor(() => expect(posts).toHaveLength(1));
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Загрузить в станок" })).toBeNull());
});

it("the hand-wire picker is closed while a job is open: a wire over it would be counted into the job", async () => {
  const { posts } = server({ run: OPEN_RUN });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  expect(await screen.findByText("Открыто задание «ГР-0007 · ПВАМ 0,35 · 325,00 мм» — провод вручную после «Завершить»")).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Провод" })).toBeDisabled();
  expect(posts).toEqual([]);
});

it("a «Тираж» typed while the job waits for a missing size goes with the job instead of the job's own quantity", async () => {
  const plan: TransferPlan = { ...PLAN, rows: [...PLAN.rows, { key: "strip_back_mm", label: "Зачистка конца", unit: "мм",
    nominal: null, source: "Нет данных", offset: "0,00", value: null, live: "5,00" }], blocking: ["Зачистка конца"] };
  const { posts } = server({ plan });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  await pickJob();
  await screen.findByText(/В ЭТК не задано: Зачистка конца/);
  const tirazh = screen.getByLabelText("Тираж");
  fireEvent.change(tirazh, { target: { value: "20" } });
  fireEvent.keyDown(tirazh, { key: "Enter" });
  const back = screen.getByLabelText("Зачистка конца");
  fireEvent.change(back, { target: { value: "6" } });
  fireEvent.keyDown(back, { key: "Enter" });
  await confirmLoad();
  await waitFor(() => expect(posts).toHaveLength(1));
  expect((posts[0].body.values as Record<string, string>).total_set).toBe("20");
});

it("the parameters window without fresh data shows one line", async () => {
  server();
  render(<Ob019SettingsPanel inventory="ОБ.019" old toolsWindow="parameters" />);
  expect(await screen.findByText(NO_DATA)).toBeInTheDocument();
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.queryByRole("tab")).toBeNull();
});

it("every writable parameter there has «Задать» next to «На станке»; an edit is written like the drawing", async () => {
  const { posts } = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" toolsWindow="parameters" />);
  fireEvent.click(await screen.findByRole("tab", { name: "Провод и зачистка" }));
  const table = await screen.findByRole("table", { name: "Провод и зачистка" });
  expect(within(table).getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual(["Параметр", "Задать", "На станке", ""]);
  const row = within(table).getByText("Стягивание начала").closest("tr")!;
  const box = within(row).getByLabelText("Стягивание начала");
  await waitFor(() => expect(box).toHaveValue("5,00"));
  expect(row).toHaveTextContent("5,00 мм");
  fireEvent.change(box, { target: { value: "6" } });
  expect(row.className).toContain("is-diff");
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(posts.map((post) => post.body.values)).toContainEqual({ pull_front_mm: "6" }));
  fireEvent.click(screen.getByRole("tab", { name: "Скорости" }));
  expect(within(screen.getByRole("table", { name: "Скорости" })).getByText("только просмотр")).toBeInTheDocument();
});

it("the parameters window names the open operator panel", async () => {
  server();
  render(<Ob019SettingsPanel inventory="ОБ.019" old pult toolsWindow="parameters" />);
  expect(await screen.findByText(PULT_NO_DATA)).toBeInTheDocument();
  expect(screen.queryByText(NO_DATA)).toBeNull();
});
