import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MarkingBatchProgress } from "./MarkingPrintWindow";
import { MarkingProfilesWindow } from "./MarkingProfilesWindow";
import type { Principal } from "./api";
import { markingApi, parseSequences, type MarkingBatch, type MarkingCatalog, type MarkingPrinterCommand, type MarkingProfile } from "./markingApi";
import { setRightsPrincipal } from "./rights";

const texts = ["КБ-011", "КБ-012", "КБ-013", "Разъём А", "Разъём А"];
const batch: MarkingBatch = { id: "batch-1", status: "pending", status_label: "В очереди", total: 5, reprint_of: null, pending: 3, withdrawn: 0,
  jobs: texts.map((text, index) => ({ id: `job-${index}`, sequence: index + 1, status: index < 2 ? "spooled" : "pending",
    status_label: index < 2 ? "Передано принтеру" : "В очереди", message: "", text, serial: index < 3 ? 11 + index : null })) };
const profile: MarkingProfile = { id: "profile", equipment_id: "unit", name: "Флажок 92,5 × 20", version: 7, calibrated: true,
  geometry: { width_mm: 92.5, height_mm: 20, rotation: 0, feed_direction: "height", zones: [
    { x: 1, y: 1, width: 38, height: 18 }, { x: 53.5, y: 1, width: 38, height: 18 },
  ], blank_zones: [{ x: 40.25, y: 0, width: 12, height: 20 }], offset_x_mm: 0, offset_y_mm: 0, min_font_mm: 1.6, max_font_mm: 5, max_lines: 3,
  alignment: "center", peel: true } };
const command: MarkingPrinterCommand = { id: "cmd-1", kind: "clear", status: "pending", status_label: "Ждёт агента", reason: "Старые флажки",
  requested_by: "Мастер", created_at: "2026-11-19T14:30:00+03:00", finished_at: null, message: "", result: {} };
const catalog: MarkingCatalog = {
  machines: [{ equipment_id: "unit", inventory_number: "ОБ.101", name: "ZCUT-RT430", profiles: [profile], binding: { version: 2, machine_id: "pc-id", printer: "Godex EZ2350i", command: null } }],
  printers: [{ machine_id: "pc-id", pc: "Пост маркировки", name: "Godex EZ2350i", driver: "Godex", port: "USB001", kind: "gdi_png", present: true, online: true }],
};

beforeEach(() => {
  sessionStorage.clear();
  vi.spyOn(markingApi, "batch").mockResolvedValue(structuredClone(batch));
  vi.spyOn(markingApi, "machines").mockResolvedValue(structuredClone(catalog));
  vi.spyOn(markingApi, "profile").mockImplementation(async (current, geometry, calibrated, name) => ({ ...current, name: name ?? current.name, geometry, calibrated, version: current.version + 1 }));
});
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); setRightsPrincipal(null); });

it("parses label numbers and ranges", () => {
  expect(parseSequences("3, 5–7 1")).toEqual([1, 3, 5, 6, 7]);
  expect(parseSequences("2-2")).toEqual([2]);
  expect(parseSequences("")).toBeNull();
  expect(parseSequences("0")).toBeNull();
  expect(parseSequences("7-5")).toBeNull();
  expect(parseSequences("А1")).toBeNull();
});

it("cancels the rest of a batch only with a reason", async () => {
  vi.spyOn(markingApi, "cancelRest").mockResolvedValue({ ...structuredClone(batch), status: "spooled", pending: 0, withdrawn: 3 });
  render(<MarkingBatchProgress initial={batch} />);
  fireEvent.click(await screen.findByRole("button", { name: "Отменить оставшиеся" }));
  const confirm = screen.getByRole("button", { name: "Отменить 3 эт." });
  expect(confirm).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Причина отмены"), { target: { value: " Флажки мнутся " } });
  fireEvent.click(confirm);
  await waitFor(() => expect(markingApi.cancelRest).toHaveBeenCalledExactlyOnceWith("batch-1", "Флажки мнутся"));
  expect(await screen.findByText("Отменено этикеток: 3")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Отменить оставшиеся" })).not.toBeInTheDocument();
});

it("reprints a serial range as a new batch and reuses the key after a lost answer", async () => {
  const reprinted = { ...structuredClone(batch), id: "batch-2", reprint_of: "batch-1", total: 1 };
  vi.spyOn(markingApi, "reprint").mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValue(reprinted);
  const onReprinted = vi.fn();
  render(<MarkingBatchProgress initial={batch} onReprinted={onReprinted} />);
  fireEvent.click(await screen.findByRole("button", { name: "Перепечатать номера…" }));
  expect(screen.getByLabelText("№ этикеток партии")).toHaveValue("1–5");
  fireEvent.change(screen.getByLabelText("Что перепечатать"), { target: { value: "serials" } });
  expect(screen.getByLabelText("Номер с")).toHaveValue(11);
  expect(screen.getByLabelText("Номер по")).toHaveValue(13);
  fireEvent.change(screen.getByLabelText("Номер с"), { target: { value: "12" } });
  fireEvent.change(screen.getByLabelText("Номер по"), { target: { value: "12" } });
  const submit = screen.getByRole("button", { name: "Перепечатать 1 шт." });
  expect(submit).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Причина повторной печати"), { target: { value: "Смялся" } });
  fireEvent.click(submit);
  expect(await screen.findByRole("alert")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Перепечатать 1 шт." }));
  await waitFor(() => expect(onReprinted).toHaveBeenCalledWith(reprinted));
  const calls = vi.mocked(markingApi.reprint).mock.calls;
  expect(calls[0]).toEqual(["batch-1", { serial_from: 12, serial_to: 12, reason: "Смялся", idempotency_key: expect.any(String) }]);
  expect(calls[1][1].idempotency_key).toBe(calls[0][1].idempotency_key);
});

it("reprints selected labels of a batch by number and refuses numbers outside it", async () => {
  vi.spyOn(markingApi, "reprint").mockResolvedValue({ ...structuredClone(batch), id: "batch-3" });
  render(<MarkingBatchProgress initial={batch} />);
  fireEvent.click(await screen.findByRole("button", { name: "Перепечатать номера…" }));
  fireEvent.change(screen.getByLabelText("№ этикеток партии"), { target: { value: "2, 9" } });
  fireEvent.change(screen.getByLabelText("Причина повторной печати"), { target: { value: "Брак" } });
  expect(screen.getByRole("button", { name: "Перепечатать" })).toBeDisabled();
  expect(screen.getByText("Укажите № этикеток от 1 до 5")).toBeVisible();
  fireEvent.change(screen.getByLabelText("№ этикеток партии"), { target: { value: "2, 4–5" } });
  fireEvent.click(screen.getByRole("button", { name: "Перепечатать 3 шт." }));
  await waitFor(() => expect(markingApi.reprint).toHaveBeenCalledWith("batch-1", { sequences: [2, 4, 5], reason: "Брак", idempotency_key: expect.any(String) }));
  expect(await screen.findByText("Повторная печать поставлена в очередь: 5 шт.")).toBeVisible();
});

it("hides cancel without production rights and reprint without the print right", async () => {
  setRightsPrincipal({ user: { id: "viewer" }, roles: [], permissions: { myops: "view" } } as unknown as Principal);
  render(<MarkingBatchProgress initial={batch} />);
  await screen.findByRole("region", { name: "Очередь печати" });
  expect(screen.queryByRole("button", { name: "Отменить оставшиеся" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Перепечатать номера…" })).not.toBeInTheDocument();
});

it("clears the printer from the machine card with a reason and follows the agent's result", async () => {
  vi.spyOn(markingApi, "clearPrinter").mockResolvedValue(structuredClone(command));
  vi.spyOn(markingApi, "printerCommand").mockResolvedValue({ ...structuredClone(command), status: "done", status_label: "Выполнено",
    finished_at: "2026-11-19T14:31:00+03:00", message: "Удалено заданий ERP из очереди Windows: 2; буфер Godex очищен (~S,BUFCLR)", result: { removed_jobs: 2, buffer_cleared: true } });
  render(<MarkingProfilesWindow equipmentIds={["unit"]} canEdit close={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Очистить принтер…" }));
  const submit = screen.getByRole("button", { name: "Очистить принтер" });
  expect(submit).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Причина очистки"), { target: { value: "Старые флажки" } });
  fireEvent.click(submit);
  await waitFor(() => expect(markingApi.clearPrinter).toHaveBeenCalledExactlyOnceWith("unit", "Старые флажки", false));
  expect(await screen.findByText("Очистка принтера: Ждёт агента")).toBeVisible();
  expect(screen.getByRole("button", { name: "Очистить принтер…" })).toBeDisabled();
  expect(await screen.findByText("Очистка принтера: Выполнено", {}, { timeout: 4000 })).toBeVisible();
  expect(screen.getByText(/буфер Godex очищен/)).toBeVisible();
});

it("toggles the peel-off dispenser without dropping calibration", async () => {
  render(<MarkingProfilesWindow equipmentIds={["unit"]} canEdit close={vi.fn()} />);
  const peel = await screen.findByRole("checkbox", { name: "Выдача по одной через отделитель" });
  expect(peel).toBeChecked();
  fireEvent.click(peel);
  expect(screen.getByText("Откалиброван")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Сохранить профиль" }));
  await waitFor(() => expect(markingApi.profile).toHaveBeenCalledExactlyOnceWith(profile, { ...profile.geometry, peel: false }, true, profile.name));
});

it("tells that waiting batch labels keep printing after the clearing and can cancel them too", async () => {
  const withPending = structuredClone(catalog);
  withPending.machines[0].binding = { ...withPending.machines[0].binding!, pending_jobs: 7 };
  withPending.printers[0].hold = "Godex: отделитель (peel-off) выключен";
  vi.mocked(markingApi.machines).mockResolvedValue(withPending);
  vi.spyOn(markingApi, "clearPrinter").mockResolvedValue({ ...structuredClone(command), cancelled_jobs: 7 });
  vi.spyOn(markingApi, "printerCommand").mockResolvedValue(structuredClone(command));
  render(<MarkingProfilesWindow equipmentIds={["unit"]} canEdit close={vi.fn()} />);
  expect(await screen.findByText("Маркировка на паузе: Godex: отделитель (peel-off) выключен")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Очистить принтер…" }));
  expect(screen.getByText(/ещё не переданные принтеру \(7 шт\.\), после очистки продолжат печататься/)).toBeVisible();
  fireEvent.click(screen.getByRole("checkbox", { name: "Отменить оставшиеся 7 эт. партий на этом принтере" }));
  fireEvent.change(screen.getByLabelText("Причина очистки"), { target: { value: "Старые флажки" } });
  fireEvent.click(screen.getByRole("button", { name: "Очистить принтер" }));
  await waitFor(() => expect(markingApi.clearPrinter).toHaveBeenCalledExactlyOnceWith("unit", "Старые флажки", true));
  expect(await screen.findByText("Отменено этикеток партий: 7")).toBeVisible();
});

it("says why a batch stopped after a failed peel-off check and continues without that label", async () => {
  const halted: MarkingBatch = { ...structuredClone(batch), status: "failed", status_label: "Ошибка печати", halted: true,
    halt_label: "Печать партии остановлена на этикетке №3: она не напечатана — не прошла проверка отделителя Godex.",
    jobs: batch.jobs.map((job, index) => index === 2 ? { ...job, status: "failed", status_label: "Ошибка печати", halted: true } : job) };
  vi.mocked(markingApi.batch).mockResolvedValue(structuredClone(halted));
  vi.spyOn(markingApi, "resolve").mockResolvedValue({ ...structuredClone(halted), halted: false, halt_label: null });
  render(<MarkingBatchProgress initial={halted} />);
  expect(await screen.findByText(/остановлена на этикетке №3/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Продолжить без неё" }));
  expect(screen.queryByLabelText("Результат проверки")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Почему продолжить без неё"), { target: { value: "Перепечатаем позже" } });
  fireEvent.click(screen.getByRole("button", { name: "Продолжить без этикетки №3" }));
  await waitFor(() => expect(markingApi.resolve).toHaveBeenCalledExactlyOnceWith("batch-1", "job-2", "failed", "Перепечатаем позже"));
});
