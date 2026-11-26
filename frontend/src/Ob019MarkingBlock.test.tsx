import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { MachineCommand, MachineControl, MachineWindow, MarkingPlan, TransferPlan } from "./machineControlApi";
import { etkText, latin, latinLength, markMode, modeValues, planValues } from "./Ob019MarkingBlock";
import { Ob019SettingsPanel, SELECT_WAIT_MS } from "./Ob019SettingsPanel";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const field = (label: string, key: string | null, value: number | boolean | string | null, unit = "мм", kind = "u32") =>
  ({ label, value, unit, read: true, key, kind });
function machineWindow(): MachineWindow {
  return { inventory: "ОБ.019", settings: [
    { title: "Задание", fields: [field("Тираж", "total_set", 12, "шт")] },
    { title: "Провод и зачистка", fields: [field("Длина провода", "length_mm", 325), field("Зачистка начала", "strip_front_mm", 5)] },
    { title: "Маркировка", fields: [
      field("Маркировка", "marking", true, "", "bit0"),
      field("Текст у начала", "mark_front", "X1", "", "text15"), field("Текст в середине", "mark_middle", "", "", "text15"),
      field("Текст у конца", "mark_back", "X2", "", "text15"),
      field("Метка у начала", "mark_pos_front_mm", 15), field("Метка у конца", "mark_pos_back_mm", 0),
      field("Средние метки по шагу", "mark_by_step", false, "", "bit0"), field("Шаг средних меток", "mark_step_mm", 0),
      field("Число средних меток", "mark_count", 0, "шт"),
      field("Передача текста в маркиратор", null, true, "", "bit0"), field("Размер шрифта", "mark_font", 3, "", "i16"),
    ] },
  ] };
}
const CONTROL: MachineControl = { problem: "", state: 102, commands: [], offsets: [], can_edit: true };
const ENDS: MarkingPlan = { mode: "ends", mode_label: "По краям", source: "ЭТК", warnings: [], blocking: [], rows: [
  { key: "marking", label: "Маркировка", value: "Вкл" }, { key: "mark_by_step", label: "Средние метки по шагу", value: "Выкл" },
  { key: "mark_count", label: "Число средних меток", value: "0" }, { key: "mark_front", label: "Текст у начала", value: "C1.5 to XS17.7" },
  { key: "mark_back", label: "Текст у конца", value: "XS17.7" }, { key: "mark_middle", label: "Текст в середине", value: "" },
  { key: "mark_pos_front_mm", label: "Метка у начала", value: "20" }, { key: "mark_pos_back_mm", label: "Метка у конца", value: "20" },
] };
const plan = (marking: MarkingPlan | null): TransferPlan => ({
  expected_source: { group_id: "g1", version: 4, length_mm: "325", plan_hash: "a".repeat(64) }, expected_active_run_id: null,
  group_id: "g1", number: "ГР-0007", wire: "ПВАМ 0,35", length_mm: "325,00", quantity: 12,
  rows: [{ key: "length_mm", label: "Длина провода", unit: "мм", nominal: "325,00", source: "Задание", offset: "0,00", value: "325,00", live: "325,00" }],
  job: { total_set: 12, order_quantity: 12, order_number: "GR-0007", product: "PVAM 0,35 L325,00" }, missing: [], blocking: [], marking,
});

/** A fake machine: a write is done at once and read back into the window on the next poll. */
function server({ control = CONTROL, transferPlan = plan(null), refuseTransfer = "" }: {
  control?: MachineControl; transferPlan?: TransferPlan; refuseTransfer?: string;
} = {}) {
  const posts: { url: string; body: Record<string, unknown> }[] = [];
  const window = machineWindow();
  const commands: MachineCommand[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = decodeURIComponent(String(input));
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      posts.push({ url, body });
      if (refuseTransfer && url.endsWith("/run")) return json({ detail: refuseTransfer }, 422);
      const values = (body.values ?? {}) as Record<string, string>;
      for (const group of window.settings) for (const item of group.fields) {
        if (!item.key || !(item.key in values)) continue;
        const value = values[item.key];
        item.value = item.kind === "bit0" ? value === "1" : item.kind === "text15" ? value : Number(value.replace(",", "."));
      }
      const id = `c${posts.length}`;
      commands.unshift({ id, status: "done", status_text: "Записано", tone: "ok", purpose: "settings", summary: "Запись", fields: [], error: "",
        created_by: "Слава", created_at: "2026-11-20T10:00:00+03:00", finished_at: "2026-11-20T10:00:02+03:00" });
      return json({ ...commands[0], status: "queued", status_text: "Ждёт агента станка" });
    }
    if (url.endsWith("/control")) return json({ ...control, commands });
    if (url.includes("/proposal")) return json({ wire: "ПВАМ 0,35", recipe: null, publish: { recipe_id: null, expected_version: null }, etk: null, values: [] });
    if (url.includes("/transfer?")) return json(transferPlan);
    if (url.endsWith("/transfer")) return json({ choices: [{ key: "g1:325", group_id: "g1", number: "ГР-0007", project: "708К.",
      wire: "ПВАМ 0,35", length_mm: "325", quantity: "12" }], wires: [] });
    return json(window);
  });
  return { posts };
}

async function pickJob() {
  const pick = await screen.findByLabelText("Задание");
  await waitFor(() => expect(within(pick).getByRole("option", { name: /ГР-0007/ })).toBeInTheDocument());
  fireEvent.change(pick, { target: { value: "g1:325" } });
  await new Promise((resolve) => setTimeout(resolve, SELECT_WAIT_MS + 50));
}

const modes = () => screen.getByRole("radiogroup", { name: "Режим маркировки" });

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

it("reads the mode from the machine registers: off — none; on and by step — full length; otherwise ends", () => {
  expect(markMode("0", "1")).toBe("none");
  expect(markMode("1", "1")).toBe("full");
  expect(markMode("1", "0")).toBe("ends");
  expect(markMode("1", "")).toBe("ends");
  expect(markMode("", "1")).toBeNull();
});

it("transliterates like the server and measures the length before the cut", () => {
  expect(latin("Жгут  к ХS17")).toBe("ZHgut k KHS17");
  expect(latin("Ёлка №1")).toBe("Elka 1");
  expect(latinLength("щ".repeat(10))).toBe(30);
  expect(latinLength("щ".repeat(11))).toBe(33);
  expect(latin("щ".repeat(11))).toHaveLength(30);
});

it("a mode writes all its registers and clears the other mode's texts; empty values take the server defaults", () => {
  const now = (values: Record<string, string>) => (key: string) => values[key] ?? "";
  expect(modeValues("none", now({ marking: "1", mark_middle: "OLD JOB" }))).toEqual({ marking: "0", mark_by_step: "0",
    mark_count: "0", mark_front: "", mark_middle: "", mark_back: "" });
  expect(modeValues("ends", now({ mark_middle: "Ж1", mark_pos_front_mm: "15,00", mark_pos_back_mm: "0,00" }))).toEqual({
    marking: "1", mark_by_step: "0", mark_count: "0", mark_front: "ZH1", mark_back: "ZH1", mark_middle: "",
    mark_pos_front_mm: "15,00", mark_pos_back_mm: "20" });
  expect(modeValues("full", now({ mark_front: "X1", mark_back: "X2", mark_step_mm: "0,00" }))).toEqual({
    marking: "1", mark_by_step: "1", mark_step_mm: "100", mark_middle: "X1", mark_front: "", mark_back: "", mark_count: "0",
    mark_pos_front_mm: "20", mark_pos_back_mm: "20" });
  expect(planValues(ENDS)).toMatchObject({ marking: "1", mark_by_step: "0", mark_middle: "" });
  expect(etkText(ENDS)).toBe("из ЭТК: По краям «C1.5 to XS17.7» / «XS17.7»");
  expect(etkText({ ...ENDS, mode: "full", mode_label: "По всей длине", rows: [{ key: "mark_middle", label: "", value: "W1" },
    { key: "mark_step_mm", label: "", value: "150" }] })).toBe("из ЭТК: По всей длине «W1» · шаг 150 мм");
});

it("shows marking below the drawing, with tuning on the card and no unverified transfer control", async () => {
  server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  await waitFor(() => expect(within(modes()).getByRole("radio", { name: "По краям" })).toHaveAttribute("aria-checked", "true"));
  expect(screen.getByLabelText("Текст у начала")).toHaveValue("X1");
  expect(screen.getByLabelText("Текст у конца")).toHaveValue("X2");
  expect(screen.getByLabelText("Метка у начала")).toHaveValue("15,00");
  expect(screen.queryByLabelText("Текст в середине")).toBeNull();
  expect(screen.getByLabelText("Размер шрифта")).toHaveValue("3");
  expect(screen.queryByText("Передача текста в маркиратор")).toBeNull();
  expect(screen.queryByRole("dialog", { name: "Все параметры" })).toBeNull();
  const drawing = screen.getByRole("region", { name: "Провод: длина и зачистка" });
  const marking = screen.getByRole("region", { name: "Маркировка" });
  expect(drawing.compareDocumentPosition(marking) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
});

it("a mode click writes one batch with every register of the mode, the clears included", async () => {
  const { posts } = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  await waitFor(() => expect(within(modes()).getByRole("radio", { name: "По краям" })).toHaveAttribute("aria-checked", "true"));
  fireEvent.click(within(modes()).getByRole("radio", { name: "По всей длине" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toEqual({ url: "/api/production/machines/card/ОБ.019/write", body: { confirm_state: 102, values: {
    marking: "1", mark_by_step: "1", mark_step_mm: "100", mark_middle: "X1", mark_front: "", mark_back: "", mark_count: "0",
    mark_pos_front_mm: "15,00", mark_pos_back_mm: "20" } } });
  await waitFor(() => expect(within(modes()).getByRole("radio", { name: "По всей длине" })).toHaveAttribute("aria-checked", "true"));
  expect(await screen.findByLabelText("Текст в середине", {}, { timeout: 3000 })).toHaveValue("X1");
  await waitFor(() => expect(screen.getByLabelText("Шаг средних меток")).toHaveValue("100,00"), { timeout: 3000 });
  expect(screen.queryByLabelText("Текст у начала")).toBeNull();
  fireEvent.click(within(modes()).getByRole("radio", { name: "Без" }));
  await waitFor(() => expect(posts).toHaveLength(2), { timeout: 4000 });
  expect(posts[1].body.values).toEqual({ marking: "0", mark_by_step: "0", mark_count: "0",
    mark_front: "", mark_middle: "", mark_back: "" });
});

it("a text goes to the machine in Latin on Enter, with the preview while typing; too long is not written", async () => {
  const { posts } = server();
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  const front = await screen.findByLabelText("Текст у начала");
  await waitFor(() => expect(front).toHaveValue("X1"));
  expect(screen.getAllByText("латиница, до 30 знаков").length).toBeGreaterThan(0);
  fireEvent.change(front, { target: { value: "Ж1 к XS17" } });
  expect(screen.getByText("на станок: «ZH1 k XS17»")).toBeInTheDocument();
  expect(posts).toEqual([]);
  fireEvent.keyDown(front, { key: "Enter" });
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body.values).toEqual({ mark_front: "ZH1 k XS17" });
  expect(await screen.findByText("✓ на станке", {}, { timeout: 3000 })).toBeInTheDocument();
  fireEvent.change(front, { target: { value: "щ".repeat(11) } });
  expect(screen.getByText("длиннее 30 знаков (в латинице 33)")).toBeInTheDocument();
  fireEvent.blur(front);
  expect(await screen.findByText("отказ: не длиннее 30 знаков")).toBeInTheDocument();
  expect(posts).toHaveLength(1);
});

it("an ETK job with marking says what the ETK marks and sends the job; the marking goes with it", async () => {
  const { posts } = server({ transferPlan: plan({ ...ENDS, warnings: ["Текст переведён в латиницу: «Х1» → «Kh1»"] }) });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  await pickJob();
  expect(await screen.findByText("из ЭТК: По краям «C1.5 to XS17.7» / «XS17.7»")).toBeInTheDocument();
  expect(screen.getByText("Текст переведён в латиницу: «Х1» → «Kh1»")).toBeInTheDocument();
  expect(posts).toEqual([]);
  fireEvent.click(within(await screen.findByRole("dialog", { name: "Загрузить в станок" })).getByRole("button", { name: "Подтвердить загрузку" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  // The job goes as a run of the machine; the server writes the ETK marking with it (workflows/machine_runs_api.py).
  expect(posts[0].url).toBe("/api/production/machines/card/ОБ.019/run");
});

it("stages full-length mode and edited text until confirmation, overriding the ETK in the run payload", async () => {
  const { posts } = server({ transferPlan: plan(ENDS) });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  await pickJob();
  fireEvent.click(within(await screen.findByRole("dialog", { name: "Загрузить в станок" })).getByRole("button", { name: "Отмена" }));
  fireEvent.click(within(modes()).getByRole("radio", { name: "По всей длине" }));
  fireEvent.click(within(await screen.findByRole("dialog", { name: "Загрузить в станок" })).getByRole("button", { name: "Отмена" }));
  const text = await screen.findByLabelText("Текст в середине");
  fireEvent.change(text, { target: { value: "NEW WIRE" } });
  fireEvent.keyDown(text, { key: "Enter" });
  const confirmation = within(await screen.findByRole("dialog", { name: "Загрузить в станок" }));
  expect(posts).toEqual([]);
  expect(confirmation.getByRole("table", { name: "Изменённые значения" })).toHaveTextContent("NEW WIRE");
  fireEvent.click(confirmation.getByRole("button", { name: "Подтвердить загрузку" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].url).toBe("/api/production/machines/card/ОБ.019/run");
  expect(posts[0].body.values).toMatchObject({ marking: "1", mark_by_step: "1", mark_step_mm: "100",
    mark_middle: "NEW WIRE", mark_front: "", mark_back: "", mark_count: "0" });
});

it("keeps explicit off and cleared texts in the confirmed task instead of restoring ETK marking", async () => {
  const { posts } = server({ transferPlan: plan(ENDS) });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  await pickJob();
  fireEvent.click(within(await screen.findByRole("dialog", { name: "Загрузить в станок" })).getByRole("button", { name: "Отмена" }));
  fireEvent.click(within(modes()).getByRole("radio", { name: "Без" }));
  const confirmation = within(await screen.findByRole("dialog", { name: "Загрузить в станок" }));
  expect(posts).toEqual([]);
  fireEvent.click(confirmation.getByRole("button", { name: "Подтвердить загрузку" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body.values).toMatchObject({ marking: "0", mark_by_step: "0", mark_count: "0",
    mark_middle: "", mark_front: "", mark_back: "" });
});

it("blocking marking in the ETK is shown and the job is not sent at all (not without its marking)", async () => {
  const blocked = { ...ENDS, blocking: ["Серийные номера на лазер — не поддерживается"] };
  const job = plan(blocked);
  const { posts } = server({ transferPlan: { ...job, rows: [{ ...job.rows[0], value: "326,00" }] },
    refuseTransfer: "Маркировка: Серийные номера на лазер — не поддерживается" });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  await pickJob();
  expect(await screen.findByText(/Маркировка из ЭТК не записывается: Серийные номера на лазер/)).toBeInTheDocument();
  expect(await screen.findByText("Задание не передано: маркировка из ЭТК с ошибкой — исправьте ЭТК")).toBeInTheDocument();
  expect(posts).toEqual([]);
});

it("a job missing a size and with a blocked marking is not held for the size: nothing is posted", async () => {
  const blocked = { ...ENDS, blocking: ["Серийные номера на лазер — не поддерживается"] };
  const job = plan(blocked);
  const { posts } = server({ transferPlan: { ...job, blocking: [job.rows[0].label], rows: [{ ...job.rows[0], value: null }] } });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  await pickJob();
  expect(await screen.findByText("Задание не передано: маркировка из ЭТК с ошибкой — исправьте ЭТК")).toBeInTheDocument();
  expect(screen.queryByText(/задание уйдёт на станок, когда все размеры заданы/)).toBeNull();
  expect(posts).toEqual([]);
});

it("while the machine cuts the marking is closed with the drawing", async () => {
  const { posts } = server({ control: { ...CONTROL, state: 103,
    problem: "Записывать можно, когда станок готов и ждёт пуска или тираж готов; сейчас: Работает" } });
  render(<Ob019SettingsPanel inventory="ОБ.019" />);
  expect(await screen.findByText("Станок режет — правка после остановки")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByLabelText("Текст у начала")).toHaveValue("X1"));
  for (const radio of within(modes()).getAllByRole("radio")) expect(radio).toBeDisabled();
  expect(screen.getByLabelText("Текст у начала")).toBeDisabled();
  expect(screen.getByLabelText("Метка у начала")).toBeDisabled();
  expect(posts).toEqual([]);
});
