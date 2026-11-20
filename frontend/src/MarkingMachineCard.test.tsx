import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { productionApi } from "./api";
import type { CuttingGroup } from "./cuttingGroups";
import { machinesApi, type MachineRow, type UnitSetup } from "./machinesApi";
import { MarkingMachineCard, manualCount, parseManualLines } from "./MarkingMachineCard";
import { markingApi, type MarkingBatch, type MarkingCatalog, type MarkingMachineState, type MarkingPreview } from "./markingApi";

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

beforeEach(() => {
  vi.spyOn(markingApi, "machines").mockResolvedValue(structuredClone(CATALOG));
  vi.spyOn(markingApi, "machineState").mockResolvedValue(structuredClone(STATE));
  vi.spyOn(markingApi, "cuttingGroups").mockResolvedValue({ groups: [GROUP] });
  vi.spyOn(markingApi, "progress").mockResolvedValue({ printed: 0, remaining: 40, remaining_labels: 40 });
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
  expect(screen.getByRole("button", { name: "Печатать 40 этикеток" })).toBeEnabled();
  expect(markingApi.progress).toHaveBeenCalledWith({ group_id: "g1" }, expect.anything());
});

it("keeps setup folded behind «Наладка»; no reason field on the print path", async () => {
  render(<MarkingMachineCard row={ROW} canWrite close={() => undefined} />);
  await screen.findByText("Готов");
  expect(screen.queryByText("Размер этикетки")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Причина")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Наладка" }));
  expect(await screen.findByText("Размер этикетки")).toBeVisible();
  expect(screen.getByRole("button", { name: "Профили и принтер" })).toBeVisible();
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
  fireEvent.click(screen.getByRole("radio", { name: "по номеру" }));
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
