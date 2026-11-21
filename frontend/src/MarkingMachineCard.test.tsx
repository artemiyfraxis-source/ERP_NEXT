import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError, productionApi } from "./api";
import type { CuttingGroup } from "./cuttingGroups";
import { machinesApi, type MachineRow, type UnitSetup } from "./machinesApi";
import { MarkingMachineCard, copiesProblem, manualCount, parseManualLines } from "./MarkingMachineCard";
import { markingApi, markingProblem, type MarkingBatch, type MarkingCatalog, type MarkingMachineState, type MarkingPreview, type MarkingSourceProgress } from "./markingApi";
import { markingOutsideApi } from "./markingOutsideApi";

const ROW: MachineRow = {
  key: "machine:unit", kind: "machine", equipment_id: "unit", inventory: "ОБ.101", name: "Полуавтомат флажковых этикеток", pc: "SK-JOKI",
  ip: "192.168.1.70", service: "Агент ERP · печать", version: "2.11.0", link: { code: "ok", text: "На связи", tone: "ok" },
  last_seen: "2026-11-20T14:05:00+03:00", device: "Godex EZ2350i", state: { text: "Принтер виден", tone: "ok" }, card: false,
  marking: true, others: "", article: "ZCUT-RT430", site: "ЦАР", operations: ["Маркировка"], family: "Маркировка", controlled: true,
  setup: { code: "simple", label: "простая" }, pending_setup: 0,
};
const geometry = { width_mm: 30, height_mm: 12, rotation: 0 as const, feed_direction: "height" as const, zones: [{ x: 1, y: 1, width: 28, height: 10 }],
  blank_zones: [], offset_x_mm: 0, offset_y_mm: 0, min_font_mm: 1.6, max_font_mm: 5, max_lines: 2, alignment: "center" as const };
const CATALOG: MarkingCatalog = {
  machines: [{ equipment_id: "unit", inventory_number: "ОБ.101", name: "ZCUT-RT430", binding: { version: 1, machine_id: "pc", printer: "Godex EZ2350i" },
    profiles: [{ id: "flag", equipment_id: "unit", name: "Флажок 30×12", version: 3, calibrated: true, geometry }] }],
  printers: [{ machine_id: "pc", pc: "SK-JOKI", name: "Godex EZ2350i", driver: "Godex", port: "USB001", kind: "gdi_png", present: true, online: true }],
};
const STATE: MarkingMachineState = {
  inventory: "ОБ.101", name: ROW.name, state: { text: "Готов", tone: "ok" }, counter: { value: 312 }, last_seen: "2026-11-20T14:05:00+03:00",
  marking: { printer: "Godex EZ2350i", present: true, queue: 0, last_job: null },
  connection: { pc: "SK-JOKI", ip: "192.168.1.70", agent: "Агент печати", version: "2.11.0", link: { code: "ok", text: "На связи", tone: "ok" },
    last_seen: "2026-11-20T14:05:00+03:00", extra: [["Принтер", "Godex EZ2350i"]], error: "" },
  journal: [],
};
const GROUP = { id: "g1", number: "гр.3", version: 4, recipe_version: 2, recipe_key: "k", kind: "labels", kind_name: "Печать этикеток", project: "708К.",
  site: "ЦАР", state: "open", membership_frozen: false, planned_start: "2026-11-21T08:00:00+03:00", planned_finish: null, norm_seconds: 600,
  rows: [{ id: "m1", task_id: "t1", operation_id: "o1", task_version: 2, task_number: "1", project: "708К.", item: "КБ1", name: "Жгут", position: 1,
    assembly_quantity: 40, blank_quantity: "40", operation_quantity: "40", cables_per_assembly: 1, cut_length_mm: null, labels: [], good: "0", defects: "0",
    membership_state: "active", state: "waiting", operator_ids: [], planned_start: null, planned_finish: null, norm_seconds: 600, boxes: [], etk_url: "" }],
} as unknown as CuttingGroup;
const label = (text: string, quantity = 1) => ({ key: text, task_id: "", text, material: "", quantity, serial: null, png_b64: "iVBORw0KGgo=", sha256: "x" });
const jobPreview = (hash: string, total: number, extra: Partial<MarkingPreview> = {}): MarkingPreview => ({ manifest_hash: hash.repeat(64).slice(0, 64),
  profile_id: "flag", profile_version: 3, binding_version: 1, source_snapshot: {}, labels: [label("КБ1-1"), label("КБ1-2")], total,
  width_mm: 30, height_mm: 12, orientation: "portrait", dpi: 203, etk_size: null, profile_matches: true, ...extra });

beforeEach(() => {
  vi.spyOn(markingApi, "machines").mockResolvedValue(structuredClone(CATALOG));
  vi.spyOn(markingApi, "machineState").mockResolvedValue(structuredClone(STATE));
  vi.spyOn(markingApi, "cuttingGroups").mockResolvedValue({ groups: [GROUP] });
  vi.spyOn(markingApi, "progress").mockResolvedValue({ printed: 0, remaining: 40, remaining_labels: 40 });
  vi.spyOn(markingApi, "preview").mockResolvedValue(jobPreview("p", 40));
  vi.spyOn(productionApi, "mine").mockResolvedValue({ tasks: [], cutting_groups: [] });
  vi.spyOn(machinesApi, "setup").mockResolvedValue({ unit_id: "unit", inventory: "ОБ.101", name: "", model: "", requires_setup: { code: "simple", label: "простая" },
    reason: "", parameters: [{ key: "size", label: "Размер этикетки", unit: "", source: "ЭТК", source_field: "", register: "" }], configuration: null,
    references: [], open_tasks: [], history: [], online: true } as UnitSetup);
  sessionStorage.clear();
});
afterEach(() => { vi.restoreAllMocks(); });

it("parses own text: one line — one label, «× N» copies, blank lines dropped; {№} counts each number", () => {
  const lines = parseManualLines("X1-{№}\n\n  ПИТАНИЕ +24В × 4 \nGND", 2);
  expect(lines).toEqual([{ text: "X1-{№}", quantity: 2 }, { text: "ПИТАНИЕ +24В", quantity: 4 }, { text: "GND", quantity: 2 }]);
  expect(manualCount(lines, 1, 3)).toBe(2 * 3 + 4 + 2);
});

it("opens on the jobs with what is left, prints the chosen group in the print window of this printer", async () => {
  render(<MarkingMachineCard row={ROW} canWrite close={() => undefined} />);
  expect(await screen.findByText("Готов")).toBeVisible();
  expect(screen.getByText(/сегодня напечатано 312/)).toBeVisible();
  const tabs = screen.getByRole("tablist", { name: "Что печатаем" });
  await waitFor(() => expect(within(tabs).getByRole("tab", { name: /Из заданий\s*1/ })).toHaveAttribute("aria-selected", "true"));
  const button = screen.getByRole("button", { name: "Печатать…" });
  expect(button).toBeDisabled();
  fireEvent.click(await screen.findByRole("radio", { name: "708К. · гр.3" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Печатать 40 этикеток" })).toBeEnabled());
  expect(markingApi.progress).toHaveBeenCalledWith({ group_id: "g1" }, expect.anything());
  // The pult is on the card: preview of the remaining labels, no second window.
  expect(markingApi.preview).toHaveBeenCalledWith({ profile_id: "flag", profile_version: 3, source: { group_id: "g1", version: 4 }, selected: [], remaining: true }, expect.anything());
  expect(await screen.findByAltText("Этикетка: КБ1-2")).toBeVisible();
  expect(screen.queryByRole("dialog", { name: "Печать надписей" })).not.toBeInTheDocument();
});

it("keeps «Наладка» a window behind its button: never opens by itself, even when setup is pending", async () => {
  render(<MarkingMachineCard row={{ ...ROW, pending_setup: 2 }} canWrite close={() => undefined} />);
  await screen.findByText("Готов");
  expect(screen.queryByRole("dialog", { name: "Наладка принтера" })).not.toBeInTheDocument();
  expect(screen.queryByText("Размер этикетки")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Причина")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Наладка · ждёт" }));
  const setup = await screen.findByRole("dialog", { name: "Наладка принтера" });
  expect(await within(setup).findByText("Размер этикетки")).toBeVisible();
  expect(within(setup).getByRole("button", { name: "Профили и принтер" })).toBeVisible();
  expect(within(setup).getByText("Причина")).toBeVisible();
  fireEvent.click(within(setup).getByRole("button", { name: "Закрыть" }));
  expect(screen.queryByRole("dialog", { name: "Наладка принтера" })).not.toBeInTheDocument();
});

it("prints own text: count on the button, preview of the first labels, trial of one label", async () => {
  const preview: MarkingPreview = { manifest_hash: "h".repeat(64), profile_id: "flag", profile_version: 3, binding_version: 1, source_snapshot: {},
    labels: [label("ПИТАНИЕ +24В", 4), label("X1-1"), label("X1-2")], total: 6, width_mm: 30, height_mm: 12, orientation: "portrait", dpi: 203 };
  const batch: MarkingBatch = { id: "b1", status: "pending", status_label: "В очереди", total: 6, reprint_of: null, jobs: [] };
  vi.spyOn(markingApi, "manualPreview").mockResolvedValue(preview);
  vi.spyOn(markingApi, "manualEnqueue").mockResolvedValue(batch);
  vi.spyOn(markingApi, "batch").mockResolvedValue(batch);
  render(<MarkingMachineCard row={ROW} canWrite close={() => undefined} />);
  await screen.findByText("Готов");
  fireEvent.click(screen.getByRole("tab", { name: "Свой текст" }));
  fireEvent.change(screen.getByLabelText(/одна строка = одна этикетка/), { target: { value: "ПИТАНИЕ +24В × 4\nX1-{№}" } });
  fireEvent.change(await screen.findByLabelText("{№} с"), { target: { value: "1" } });
  fireEvent.change(screen.getByLabelText("по"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("radio", { name: "по № провода" }));
  expect(screen.getByText("Номера изделий не выдаются · печать записывается в журнал")).toBeVisible();
  expect(await screen.findByAltText("Этикетка: X1-2")).toBeVisible();
  const print = await screen.findByRole("button", { name: "Печатать 6 этикеток" });
  await waitFor(() => expect(print).toBeEnabled());
  fireEvent.click(print);
  await waitFor(() => expect(markingApi.manualEnqueue).toHaveBeenCalledTimes(1));
  expect(vi.mocked(markingApi.manualEnqueue).mock.calls[0][0]).toMatchObject({
    profile_id: "flag", profile_version: 3, order: "number", numbering: { first: 1, last: 2 }, manifest_hash: "h".repeat(64),
    lines: [{ text: "ПИТАНИЕ +24В", quantity: 4 }, { text: "X1-{№}", quantity: 1 }],
  });
  expect(await screen.findByText("Поставлено в очередь: 6 этикеток")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Пробная (1 этикетка)" }));
  await waitFor(() => expect(markingApi.manualEnqueue).toHaveBeenCalledTimes(2));
  expect(vi.mocked(markingApi.manualEnqueue).mock.calls[1][0]).toMatchObject({ trial: true });
});

it("says why it cannot print when the printer is offline", async () => {
  vi.spyOn(markingApi, "machines").mockResolvedValue({ ...structuredClone(CATALOG), printers: [{ ...CATALOG.printers[0], online: false }] });
  render(<MarkingMachineCard row={ROW} canWrite close={() => undefined} />);
  fireEvent.click(await screen.findByRole("tab", { name: "Свой текст" }));
  expect(await screen.findByText("Принтер не на связи")).toBeVisible();
});

const shownPreview = (hash: string, total = 1): MarkingPreview => ({ manifest_hash: hash.repeat(64).slice(0, 64), profile_id: "flag", profile_version: 3,
  binding_version: 1, source_snapshot: {}, labels: [label("A", total)], total, width_mm: 30, height_mm: 12, orientation: "portrait", dpi: 203 });

it("aborts the stale preview when the text changes, and prints with the hash of the preview on screen", async () => {
  const signals: AbortSignal[] = [];
  const spy = vi.spyOn(markingApi, "manualPreview").mockImplementation((body, signal) => {
    if (signal) signals.push(signal);
    return Promise.resolve(shownPreview(body.lines[0].text === "A" ? "a" : "b"));
  });
  const batch: MarkingBatch = { id: "b1", status: "pending", status_label: "В очереди", total: 1, reprint_of: null, jobs: [] };
  vi.spyOn(markingApi, "manualEnqueue").mockResolvedValue(batch);
  vi.spyOn(markingApi, "batch").mockResolvedValue(batch);
  render(<MarkingMachineCard row={ROW} canWrite close={() => undefined} />);
  await screen.findByText("Готов");
  fireEvent.click(screen.getByRole("tab", { name: "Свой текст" }));
  const field = screen.getByLabelText(/одна строка = одна этикетка/);
  fireEvent.change(field, { target: { value: "A" } });
  await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
  fireEvent.change(field, { target: { value: "B" } });
  expect(signals[0].aborted).toBe(true);
  await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
  const print = await screen.findByRole("button", { name: "Печатать 1 этикетку" });
  fireEvent.click(print);
  await waitFor(() => expect(markingApi.manualEnqueue).toHaveBeenCalledTimes(1));
  expect(vi.mocked(markingApi.manualEnqueue).mock.calls[0][0].manifest_hash).toBe("b".repeat(64));
  // No fresh preview for the print itself: the two calls above were the debounced ones.
  expect(spy).toHaveBeenCalledTimes(2);
});

it("does not enqueue on a preview older than the request: refreshes it and asks for one more click", async () => {
  let release: (value: MarkingPreview) => void = () => undefined;
  const spy = vi.spyOn(markingApi, "manualPreview").mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }))
    .mockResolvedValue(shownPreview("c"));
  vi.spyOn(markingApi, "manualEnqueue").mockResolvedValue({ id: "b1", status: "pending", status_label: "", total: 1, reprint_of: null, jobs: [] });
  render(<MarkingMachineCard row={ROW} canWrite close={() => undefined} />);
  await screen.findByText("Готов");
  fireEvent.click(screen.getByRole("tab", { name: "Свой текст" }));
  fireEvent.change(screen.getByLabelText(/одна строка = одна этикетка/), { target: { value: "A" } });
  await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", { name: /^Печатать/ }));
  expect(markingApi.manualEnqueue).not.toHaveBeenCalled();
  expect(await screen.findByText(/нажмите «Печатать» ещё раз/)).toBeVisible();
  release(shownPreview("a"));
});

it("rejects « × 0» and copies over the cap with a Russian message instead of silently printing one", async () => {
  expect(parseManualLines("A × 0", 1)).toEqual([{ text: "A", quantity: 0 }]);
  expect(copiesProblem(parseManualLines("A × 0", 1), "1")).toMatch(/не меньше 1/);
  expect(copiesProblem(parseManualLines("A", 1), "0")).toMatch(/не меньше 1/);
  expect(copiesProblem(parseManualLines("A", 501), "501")).toMatch(/не больше 500/);
  expect(copiesProblem(parseManualLines("A × 4", 1), "1")).toBe("");
  const spy = vi.spyOn(markingApi, "manualPreview").mockResolvedValue(shownPreview("a"));
  render(<MarkingMachineCard row={ROW} canWrite close={() => undefined} />);
  await screen.findByText("Готов");
  fireEvent.click(screen.getByRole("tab", { name: "Свой текст" }));
  fireEvent.change(screen.getByLabelText(/одна строка = одна этикетка/), { target: { value: "ПИТАНИЕ × 0" } });
  expect(await screen.findByRole("alert", { name: "" })).toHaveTextContent(/× 0 не печатает/);
  await new Promise((resolve) => setTimeout(resolve, 700));
  expect(spy).not.toHaveBeenCalled();
});

it("turns a Pydantic 422 into readable Russian", () => {
  const cap = new ApiError("HTTP 422", 422, [{ loc: ["body", "lines", 0, "quantity"], type: "less_than_equal", msg: "x", ctx: { le: 500 } }]);
  expect(markingProblem(cap, "?")).toBe("Копий: не больше 500");
  const order = new ApiError("HTTP 422", 422, [{ loc: ["body", "numbering"], type: "value_error", msg: "Value error, Номера: «с» не больше «по»" }]);
  expect(markingProblem(order, "?")).toBe("Номера: «с» не больше «по»");
  expect(markingProblem(new ApiError("За один раз — не более 500 этикеток", 422, "За один раз — не более 500 этикеток"), "?")).toBe("За один раз — не более 500 этикеток");
});

const TWO_PROFILES = (): MarkingCatalog => ({ ...structuredClone(CATALOG), machines: [{ ...structuredClone(CATALOG.machines[0]), profiles: [
  { id: "flag", equipment_id: "unit", name: "Флажок 30×12", version: 3, calibrated: true, geometry },
  { id: "small", equipment_id: "unit", name: "Флажок 25×9", version: 5, calibrated: true, geometry: { ...geometry, width_mm: 9, height_mm: 25 } },
] }] });
const ETK_PROGRESS: MarkingSourceProgress = { printed: 0, remaining: 12, remaining_labels: 12, label_size: { width_mm: 25, height_mm: 9, material: "Флажок 25×9 мм" },
  sizes_differ: false, wire_mm: [1.6] };

async function pickGroup() {
  render(<MarkingMachineCard row={ROW} canWrite close={() => undefined} />);
  await screen.findByText("Готов");
  fireEvent.click(await screen.findByRole("radio", { name: "708К. · гр.3" }));
}

it("takes the label size and the wire Ø from the ETK: picks the matching profile and says «из ЭТК»", async () => {
  vi.spyOn(markingApi, "machines").mockResolvedValue(TWO_PROFILES());
  vi.spyOn(markingApi, "progress").mockResolvedValue(structuredClone(ETK_PROGRESS));
  vi.mocked(markingApi.preview).mockResolvedValue(jobPreview("p", 12, { profile_id: "small", profile_version: 5, etk_size: { width_mm: 25, height_mm: 9 } }));
  await pickGroup();
  expect(screen.getByRole("cell", { name: "25 × 9" })).toBeVisible();
  await waitFor(() => expect(screen.getByLabelText("Профиль этикетки")).toHaveValue("small"));
  expect(await screen.findByText("9 × 25 мм · из ЭТК")).toBeVisible();
  expect(screen.getByText("Ø провода 1,6 мм · из ЭТК")).toBeVisible();
  await waitFor(() => expect(markingApi.preview).toHaveBeenLastCalledWith(expect.objectContaining({ profile_id: "small", profile_version: 5, remaining: true }), expect.anything()));
  expect(screen.queryByLabelText(/причина/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("radio", { name: "по изделию" }));
  await waitFor(() => expect(markingApi.preview).toHaveBeenLastCalledWith(expect.objectContaining({ interleave: true }), expect.anything()));
});

it("says when the ETK has no label size or wire Ø, and asks no reason", async () => {
  await pickGroup();
  expect(await screen.findByText("30 × 12 мм · размер в ЭТК не указан")).toBeVisible();
  expect(screen.getByText("Ø провода в ЭТК не указан")).toBeVisible();
  await waitFor(() => expect(screen.getByRole("button", { name: "Печатать 40 этикеток" })).toBeEnabled());
});

it("prints the task with the hash of the preview on screen; the trial is one real label (limit 1) with its own preview", async () => {
  const batch: MarkingBatch = { id: "b1", status: "pending", status_label: "В очереди", total: 40, reprint_of: null, jobs: [] };
  vi.spyOn(markingApi, "enqueue").mockResolvedValue(batch);
  vi.spyOn(markingApi, "batch").mockResolvedValue({ ...batch, status: "spooled" });
  await pickGroup();
  const print = await screen.findByRole("button", { name: "Печатать 40 этикеток" });
  await waitFor(() => expect(print).toBeEnabled());
  // The trial gets its own one-label preview; then the remaining count and the preview come again from the server.
  vi.mocked(markingApi.progress).mockResolvedValue({ printed: 1, remaining: 39, remaining_labels: 39 });
  vi.mocked(markingApi.preview).mockResolvedValue(jobPreview("s", 39)).mockResolvedValueOnce(jobPreview("t", 1));
  fireEvent.click(screen.getByRole("button", { name: "Пробная (1 этикетка)" }));
  await waitFor(() => expect(markingApi.enqueue).toHaveBeenCalledTimes(1));
  expect(markingApi.preview).toHaveBeenCalledWith({ profile_id: "flag", profile_version: 3, source: { group_id: "g1", version: 4 },
    selected: [], remaining: true, limit: 1 });
  expect(vi.mocked(markingApi.enqueue).mock.calls[0][0]).toEqual({ profile_id: "flag", profile_version: 3, source: { group_id: "g1", version: 4 },
    selected: [], remaining: true, limit: 1, manifest_hash: "t".repeat(64), idempotency_key: expect.any(String) });
  expect(await screen.findByText(/Пробная этикетка поставлена в очередь/)).toBeVisible();
  await waitFor(() => expect(screen.getByRole("button", { name: "Печатать 39 этикеток" })).toBeEnabled());
  const previews = vi.mocked(markingApi.preview).mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: "Печатать 39 этикеток" }));
  await waitFor(() => expect(markingApi.enqueue).toHaveBeenCalledTimes(2));
  const body = vi.mocked(markingApi.enqueue).mock.calls[1][0];
  expect(body.manifest_hash).toBe("s".repeat(64));
  expect(body).not.toHaveProperty("limit");
  expect(body).not.toHaveProperty("reason");
  expect(vi.mocked(markingApi.preview).mock.calls.length).toBe(previews);
});

it("needs a reason when the profile is not the ETK size, and sends it with the batch", async () => {
  vi.spyOn(markingApi, "progress").mockResolvedValue(structuredClone(ETK_PROGRESS));
  vi.mocked(markingApi.preview).mockResolvedValue(jobPreview("m", 12, { etk_size: { width_mm: 25, height_mm: 9 }, profile_matches: false }));
  vi.spyOn(markingApi, "enqueue").mockResolvedValue({ id: "b1", status: "spooled", status_label: "", total: 12, reprint_of: null, jobs: [] });
  vi.spyOn(markingApi, "batch").mockResolvedValue({ id: "b1", status: "spooled", status_label: "", total: 12, reprint_of: null, jobs: [] });
  await pickGroup();
  const reason = await screen.findByLabelText(/Профиль 30 × 12 мм, в ЭТК 25 × 9 мм — причина/);
  expect(screen.getByText("30 × 12 мм · в ЭТК 25 × 9 мм")).toHaveClass("tone-warn");
  const print = screen.getByRole("button", { name: "Печатать 12 этикеток" });
  expect(print).toBeDisabled();
  expect(screen.getByRole("button", { name: "Пробная (1 этикетка)" })).toBeDisabled();
  expect(screen.getByText("Укажите причину — размер этикетки не как в ЭТК")).toBeVisible();
  fireEvent.change(reason, { target: { value: " нет ленты 25×9 " } });
  expect(print).toBeEnabled();
  fireEvent.click(print);
  await waitFor(() => expect(markingApi.enqueue).toHaveBeenCalledTimes(1));
  expect(vi.mocked(markingApi.enqueue).mock.calls[0][0]).toMatchObject({ reason: "нет ленты 25×9", manifest_hash: "m".repeat(64) });
});

it("offers «Отметить напечатанные вне ERP» only for a labels group, not for a task operation", async () => {
  vi.spyOn(markingOutsideApi, "list").mockResolvedValue({ rows: [] });
  vi.spyOn(markingOutsideApi, "mark").mockResolvedValue({ rows: [], dry_run: true });
  vi.spyOn(productionApi, "mine").mockResolvedValue({ cutting_groups: [], tasks: [{ id: "t9", version: 3, project: "713К.", item: "КБ2", name: "Жгут",
    operations: [{ id: "op9", code: "INS_MARK_AUTO", stage: "Маркировка", state: "ожидает", planned_start: null }] }] } as unknown as Awaited<ReturnType<typeof productionApi.mine>>);
  await pickGroup();
  fireEvent.click(screen.getByRole("button", { name: "Отметить напечатанные вне ERP" }));
  const outside = await screen.findByRole("dialog", { name: "Отметить напечатанные вне ERP" });
  fireEvent.click(within(outside).getByRole("button", { name: "Закрыть" }));
  fireEvent.click(screen.getByRole("radio", { name: "713К. · КБ2" }));
  await waitFor(() => expect(markingApi.preview).toHaveBeenLastCalledWith(expect.objectContaining({ source: { task_id: "t9", operation_id: "op9", version: 3 } }), expect.anything()));
  expect(screen.queryByRole("button", { name: "Отметить напечатанные вне ERP" })).not.toBeInTheDocument();
});

it("keeps the old print window as the expert path «Выбрать надписи…»", async () => {
  await pickGroup();
  fireEvent.click(await screen.findByRole("button", { name: "Выбрать надписи…" }));
  const window = await screen.findByRole("dialog", { name: "Печать надписей" });
  expect(within(window).getByText("708К. · гр.3 · ОБ.101")).toBeVisible();
  await within(window).findByRole("option", { name: "ОБ.101 · ZCUT-RT430" });
  fireEvent.click(within(window).getByRole("button", { name: "Обновить предпросмотр" }));
  await waitFor(() => expect(markingApi.preview).toHaveBeenLastCalledWith({ profile_id: "flag", profile_version: 3, source: { group_id: "g1", version: 4 }, selected: [], remaining: true }));
});

it("sends the wire Ø of own text to the journal and refuses a wrong one", async () => {
  const spy = vi.spyOn(markingApi, "manualPreview").mockResolvedValue(shownPreview("w"));
  render(<MarkingMachineCard row={ROW} canWrite close={() => undefined} />);
  await screen.findByText("Готов");
  fireEvent.click(screen.getByRole("tab", { name: "Свой текст" }));
  fireEvent.change(screen.getByLabelText(/одна строка = одна этикетка/), { target: { value: "A" } });
  fireEvent.change(screen.getByLabelText("Ø провода, мм"), { target: { value: "1,6" } });
  await waitFor(() => expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ wire_mm: 1.6 }), expect.anything()));
  fireEvent.change(screen.getByLabelText("Ø провода, мм"), { target: { value: "75" } });
  expect(await screen.findByText("Ø провода — число от 0,1 до 60 мм")).toBeVisible();
  expect(screen.getByRole("button", { name: /^Печатать/ })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Ø провода, мм"), { target: { value: "" } });
  await waitFor(() => expect(spy.mock.calls.at(-1)?.[0]).not.toHaveProperty("wire_mm"));
});

it("sends the reason only to the batch, never to the preview, and shows the server's string 422 as is", async () => {
  vi.spyOn(markingApi, "progress").mockResolvedValue(structuredClone(ETK_PROGRESS));
  vi.mocked(markingApi.preview).mockResolvedValue(jobPreview("m", 12, { etk_size: { width_mm: 25, height_mm: 9 }, profile_matches: false }));
  vi.spyOn(markingApi, "enqueue").mockRejectedValue(new ApiError("Размер этикетки не как в ЭТК — укажите причину", 422,
    "Размер этикетки не как в ЭТК — укажите причину"));
  await pickGroup();
  fireEvent.change(await screen.findByLabelText(/— причина/), { target: { value: "нет ленты" } });
  fireEvent.click(screen.getByRole("button", { name: "Пробная (1 этикетка)" }));
  expect(await screen.findByText("Размер этикетки не как в ЭТК — укажите причину")).toBeVisible();
  for (const [body] of vi.mocked(markingApi.preview).mock.calls) expect(body).not.toHaveProperty("reason");
  expect(vi.mocked(markingApi.enqueue).mock.calls[0][0]).toMatchObject({ limit: 1, reason: "нет ленты" });
});

it("takes the wire Ø from the КБ rows when the task has no common list; no ETK size in the preview asks no reason", async () => {
  vi.spyOn(markingApi, "progress").mockResolvedValue({ printed: 0, remaining: 12, remaining_labels: 12, label_size: null, sizes_differ: false, wire_mm: [],
    rows: [{ printed: 0, remaining: 6, remaining_labels: 6, label_size: null, wire_mm: [2.1] }, { printed: 0, remaining: 6, remaining_labels: 6, label_size: null, wire_mm: [1.6, 2.1] }] });
  vi.mocked(markingApi.preview).mockResolvedValue(jobPreview("n", 12, { etk_size: null, profile_matches: true }));
  await pickGroup();
  expect(await screen.findByText("Ø провода 1,6; 2,1 мм · из ЭТК")).toBeVisible();
  expect(await screen.findByText("30 × 12 мм · размер в ЭТК не указан")).toBeVisible();
  expect(screen.queryByLabelText(/— причина/)).not.toBeInTheDocument();
});

it("shows the server's refusal when everything left is already queued", async () => {
  vi.mocked(markingApi.preview).mockRejectedValue(new ApiError("Все выбранные надписи уже напечатаны или стоят в очереди печати", 422,
    "Все выбранные надписи уже напечатаны или стоят в очереди печати"));
  await pickGroup();
  expect(await screen.findByText("Все выбранные надписи уже напечатаны или стоят в очереди печати")).toBeVisible();
  expect(screen.getByRole("button", { name: "Печатать 40 этикеток" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Пробная (1 этикетка)" })).toBeDisabled();
});

it("does not enqueue a trial whose fresh first label differs from the preview on screen: refreshes it and asks again", async () => {
  vi.spyOn(markingApi, "enqueue");
  await pickGroup();
  await waitFor(() => expect(screen.getByRole("button", { name: "Пробная (1 этикетка)" })).toBeEnabled());
  const shown = vi.mocked(markingApi.preview).mock.calls.length;
  vi.mocked(markingApi.preview).mockResolvedValueOnce(jobPreview("t", 1, { labels: [label("КБ1-3")] }));
  fireEvent.click(screen.getByRole("button", { name: "Пробная (1 этикетка)" }));
  expect(await screen.findByText("Предпросмотр изменился — проверьте и нажмите ещё раз")).toBeVisible();
  expect(markingApi.enqueue).not.toHaveBeenCalled();
  // The trial preview plus a fresh on-screen preview.
  await waitFor(() => expect(vi.mocked(markingApi.preview).mock.calls.length).toBe(shown + 2));
});

it("does not enqueue a trial when the fresh preview says the profile no longer matches the ETK", async () => {
  vi.spyOn(markingApi, "enqueue");
  await pickGroup();
  await waitFor(() => expect(screen.getByRole("button", { name: "Пробная (1 этикетка)" })).toBeEnabled());
  vi.mocked(markingApi.preview).mockResolvedValueOnce(jobPreview("t", 1, { etk_size: { width_mm: 25, height_mm: 9 }, profile_matches: false }));
  fireEvent.click(screen.getByRole("button", { name: "Пробная (1 этикетка)" }));
  expect(await screen.findByText("Предпросмотр изменился — проверьте и нажмите ещё раз")).toBeVisible();
  expect(markingApi.enqueue).not.toHaveBeenCalled();
});

it("after a 409 reloads the jobs (new version), the remaining count and the preview, so the next click is not the same refusal", async () => {
  vi.mocked(markingApi.cuttingGroups).mockResolvedValueOnce({ groups: [GROUP] }).mockResolvedValue({ groups: [{ ...GROUP, version: 5 } as CuttingGroup] });
  vi.spyOn(markingApi, "enqueue").mockRejectedValueOnce(new ApiError("Задание изменилось — обновите предпросмотр", 409, "Задание изменилось — обновите предпросмотр"))
    .mockResolvedValue({ id: "b1", status: "spooled", status_label: "", total: 40, reprint_of: null, jobs: [] });
  vi.spyOn(markingApi, "batch").mockResolvedValue({ id: "b1", status: "spooled", status_label: "", total: 40, reprint_of: null, jobs: [] });
  await pickGroup();
  const print = await screen.findByRole("button", { name: "Печатать 40 этикеток" });
  await waitFor(() => expect(print).toBeEnabled());
  const progressCalls = vi.mocked(markingApi.progress).mock.calls.length;
  fireEvent.click(print);
  expect(await screen.findByText(/Задание изменилось — обновите предпросмотр — задания и предпросмотр обновлены/)).toBeVisible();
  await waitFor(() => expect(markingApi.cuttingGroups).toHaveBeenCalledTimes(2));
  expect(vi.mocked(markingApi.progress).mock.calls.length).toBeGreaterThan(progressCalls);
  await waitFor(() => expect(markingApi.preview).toHaveBeenLastCalledWith(expect.objectContaining({ source: { group_id: "g1", version: 5 } }), expect.anything()));
  await waitFor(() => expect(screen.getByRole("button", { name: "Печатать 40 этикеток" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Печатать 40 этикеток" }));
  await waitFor(() => expect(markingApi.enqueue).toHaveBeenCalledTimes(2));
  expect(vi.mocked(markingApi.enqueue).mock.calls[1][0].source).toEqual({ group_id: "g1", version: 5 });
});

it("refreshes the task preview on «Обновить предпросмотр»", async () => {
  await pickGroup();
  const refresh = await screen.findByRole("button", { name: "Обновить предпросмотр" });
  await waitFor(() => expect(refresh).toBeEnabled());
  await screen.findByAltText("Этикетка: КБ1-2");
  const calls = vi.mocked(markingApi.preview).mock.calls.length;
  fireEvent.click(refresh);
  await waitFor(() => expect(vi.mocked(markingApi.preview).mock.calls.length).toBe(calls + 1));
});

it("clears the reason when the profile changes", async () => {
  vi.spyOn(markingApi, "machines").mockResolvedValue(TWO_PROFILES());
  vi.mocked(markingApi.preview).mockResolvedValue(jobPreview("m", 40, { etk_size: { width_mm: 25, height_mm: 9 }, profile_matches: false }));
  await pickGroup();
  fireEvent.change(await screen.findByLabelText(/— причина/), { target: { value: "нет ленты" } });
  expect(screen.getByLabelText(/— причина/)).toHaveValue("нет ленты");
  fireEvent.change(screen.getByLabelText("Профиль этикетки"), { target: { value: "small" } });
  await waitFor(() => expect(markingApi.preview).toHaveBeenLastCalledWith(expect.objectContaining({ profile_id: "small" }), expect.anything()));
  expect(await screen.findByLabelText(/— причина/)).toHaveValue("");
});
