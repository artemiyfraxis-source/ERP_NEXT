import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MarkingProfilesWindow } from "./MarkingProfilesWindow";
import { ApiError } from "./api";
import { markingApi, type MarkingBatch, type MarkingCatalog, type MarkingProfile } from "./markingApi";
import { setRightsPrincipal } from "./rights";

const png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABAQAAAAA3bvkkAAAACklEQVR4nGNgAAAAAgABSK+kcQAAAABJRU5ErkJggg==";
const profile: MarkingProfile = { id: "profile", equipment_id: "unit", name: "Флажок 92,5 × 20", version: 7, calibrated: true,
  geometry: { width_mm: 92.5, height_mm: 20, rotation: 0, feed_direction: "height", zones: [
    { x: 1, y: 1, width: 38, height: 18 }, { x: 53.5, y: 1, width: 38, height: 18 },
  ], blank_zones: [{ x: 40.25, y: 0, width: 12, height: 20 }], offset_x_mm: 0, offset_y_mm: 0, min_font_mm: 1.6, max_font_mm: 5, max_lines: 3, alignment: "center" } };
const catalog: MarkingCatalog = {
  machines: [{ equipment_id: "unit", inventory_number: "ОБ.101", name: "ZCUT-RT430", profiles: [profile], binding: { version: 2, machine_id: "pc-id", printer: "Godex EZ2350i" } }],
  printers: [{ machine_id: "pc-id", pc: "Пост маркировки", name: "Godex EZ2350i", driver: "Godex", port: "USB001", kind: "gdi_png", present: true, online: true }],
};
const batch: MarkingBatch = { id: "batch-1", status: "spooled", status_label: "Передано принтеру", total: 6, reprint_of: null,
  jobs: Array.from({ length: 6 }, (_, sequence) => ({ id: `job-${sequence}`, sequence: sequence + 1, status: "spooled", status_label: "Передано принтеру", message: "" })) };

beforeEach(() => {
  sessionStorage.clear();
  vi.spyOn(markingApi, "machines").mockResolvedValue(structuredClone(catalog));
  vi.spyOn(markingApi, "enqueue").mockResolvedValue(structuredClone(batch));
  vi.spyOn(markingApi, "batch").mockResolvedValue(structuredClone(batch));
  vi.spyOn(markingApi, "profile").mockImplementation(async (current, geometry, calibrated) => ({ ...current, geometry, calibrated, version: current.version + 1 }));
  vi.spyOn(markingApi, "bind").mockImplementation(async (_id, body) => ({ ...body, version: body.version + 1 }));
  vi.spyOn(markingApi, "calibration").mockResolvedValue({ png_b64: png, sha256: "calibration", width_mm: 92.5, height_mm: 20, orientation: "landscape", dpi: 300 });
});
afterEach(() => { vi.restoreAllMocks(); setRightsPrincipal(null); });

function openProfiles(canEdit = true) { return render(<MarkingProfilesWindow equipmentIds={["unit"]} canEdit={canEdit} close={vi.fn()} />); }

it("preserves exact 92.5 mm geometry, resets calibration on edit and requires a separate confirmation", async () => {
  openProfiles();
  await screen.findByLabelText("Ширина этикетки, мм");
  expect(screen.getByLabelText("Ширина этикетки, мм")).toHaveValue(92.5);
  fireEvent.change(screen.getByLabelText("Смещение по ширине, мм"), { target: { value: "0.2" } });
  expect(screen.getByRole("button", { name: "Пробная печать" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Сохранить размеры" }));
  await waitFor(() => expect(markingApi.profile).toHaveBeenCalledWith(profile, { ...profile.geometry, offset_x_mm: 0.2 }, false));
  fireEvent.click(await screen.findByRole("checkbox", { name: /Размер, положение текста и подача проверены/ }));
  fireEvent.click(screen.getByRole("button", { name: "Подтвердить калибровку" }));
  await waitFor(() => expect(markingApi.profile).toHaveBeenCalledTimes(2));
  expect(vi.mocked(markingApi.profile).mock.calls[1]).toEqual([expect.objectContaining({ version: 8 }), { ...profile.geometry, offset_x_mm: 0.2 }, true]);
});

it("keeps calibration print retries idempotent and does not confirm calibration automatically", async () => {
  vi.mocked(markingApi.machines).mockResolvedValue({ ...catalog, machines: [{ ...catalog.machines[0], profiles: [{ ...profile, calibrated: false }] }] });
  vi.mocked(markingApi.calibration).mockRejectedValueOnce(new TypeError("network")).mockResolvedValueOnce({ png_b64: png, sha256: "cal", width_mm: 92.5, height_mm: 20, orientation: "landscape", dpi: 300, batch_id: "cal-batch" });
  openProfiles();
  fireEvent.click(await screen.findByRole("button", { name: "Пробная печать" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Повторить запрос пробы" }));
  await screen.findByRole("img", { name: "Пробная этикетка: АБВ 123" });
  expect(vi.mocked(markingApi.calibration).mock.calls[0]).toEqual(vi.mocked(markingApi.calibration).mock.calls[1]);
  expect(markingApi.profile).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Подтвердить калибровку" })).toBeDisabled();
});

it("binds the exact equipment UUID, PC and named PNG printer, excluding TSPL printers", async () => {
  vi.mocked(markingApi.machines).mockResolvedValue({ ...catalog, printers: [...catalog.printers, { ...catalog.printers[0], name: "Склад TSC", kind: "tspl" }] });
  openProfiles();
  const picker = await screen.findByLabelText("Принтер на рабочем месте");
  expect(within(picker).queryByRole("option", { name: /Склад TSC/ })).toBeNull();
  fireEvent.change(picker, { target: { value: JSON.stringify(["pc-id", "Godex EZ2350i"]) } });
  fireEvent.click(screen.getByRole("button", { name: "Привязать принтер" }));
  await waitFor(() => expect(markingApi.bind).toHaveBeenCalledWith("unit", { version: 2, machine_id: "pc-id", printer: "Godex EZ2350i" }));
});

it("tells apart two PCs with the same hostname by IP and machine number", async () => {
  const twin = { ...catalog.printers[0], pc: "SK-20250818JOKI", ip: "192.168.1.31", machine_id: "b7c1e2d40000" };
  vi.mocked(markingApi.machines).mockResolvedValue({ ...catalog, printers: [
    { ...catalog.printers[0], pc: "SK-20250818JOKI", ip: "192.168.1.30", machine_id: "a1f09c3e0000" } as MarkingCatalog["printers"][number], twin as MarkingCatalog["printers"][number]] });
  openProfiles();
  const picker = await screen.findByLabelText("Принтер на рабочем месте");
  expect(within(picker).getByRole("option", { name: "SK-20250818JOKI (192.168.1.30, № a1f09c3e) · Godex EZ2350i" })).toBeInTheDocument();
  expect(within(picker).getByRole("option", { name: "SK-20250818JOKI (192.168.1.31, № b7c1e2d4) · Godex EZ2350i" })).toBeInTheDocument();
});

it("shows profiles only for matching stable equipment IDs and protects read-only access", async () => {
  const { unmount } = openProfiles(false);
  expect(await screen.findByLabelText("Ширина этикетки, мм")).toBeDisabled();
  expect(screen.queryByRole("button", { name: "Привязать принтер" })).toBeNull();
  expect(screen.getByRole("button", { name: "Пробная печать" })).toBeDisabled();
  unmount();
  render(<MarkingProfilesWindow equipmentIds={["other-unit"]} canEdit close={vi.fn()} />);
  expect(await screen.findByText(/Профилей для этих экземпляров нет/)).toBeVisible();
});

it("keeps unsaved geometry from being discarded by profile switching or closing", async () => {
  const close = vi.fn();
  render(<MarkingProfilesWindow equipmentIds={["unit"]} canEdit close={close} />);
  await screen.findByLabelText("Ширина этикетки, мм");
  fireEvent.change(screen.getByLabelText("Смещение по ширине, мм"), { target: { value: "0.1" } });
  expect(screen.getByLabelText("Материал / профиль")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Закрыть" })).toBeDisabled();
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(close).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Отменить изменения" }));
  expect(screen.getByRole("button", { name: "Закрыть" })).toBeEnabled();
});

it("recovers a calibration request across editor reopen without allocating another key", async () => {
  vi.mocked(markingApi.calibration).mockRejectedValueOnce(new TypeError("network")).mockResolvedValueOnce({ png_b64: png, sha256: "cal", width_mm: 92.5, height_mm: 20, orientation: "landscape", dpi: 300, batch_id: "trial" });
  const first = openProfiles();
  fireEvent.click(await screen.findByRole("button", { name: "Пробная печать" }));
  await screen.findByRole("button", { name: "Повторить запрос пробы" });
  first.unmount();
  openProfiles();
  fireEvent.click(await screen.findByRole("button", { name: "Повторить запрос пробы" }));
  await screen.findByText("Пробная этикетка поставлена в очередь");
  expect(vi.mocked(markingApi.calibration).mock.calls[0]).toEqual(vi.mocked(markingApi.calibration).mock.calls[1]);
});

it("retries a failed catalog load without losing the equipment filter", async () => {
  vi.mocked(markingApi.machines).mockRejectedValueOnce(new ApiError("Нет связи с ERP", 503));
  openProfiles();
  expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи с ERP");
  fireEvent.click(screen.getByRole("button", { name: "Повторить загрузку" }));
  expect(await screen.findByLabelText("Ширина этикетки, мм")).toHaveValue(92.5);
});

it("allows a raster preview offline and blocks trial printing until the exact queue is online", async () => {
  vi.mocked(markingApi.machines).mockResolvedValue({ ...catalog, printers: [{ ...catalog.printers[0], online: false }] });
  openProfiles();
  expect(await screen.findByRole("button", { name: "Пробная печать" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Показать пробную этикетку" }));
  expect(await screen.findByRole("img", { name: "Пробная этикетка: АБВ 123" })).toHaveAttribute("src", `data:image/png;base64,${png}`);
  expect(markingApi.calibration).toHaveBeenCalledExactlyOnceWith(profile, "АБВ 123", false, undefined);
});

it("retains edited geometry and the original version when a concurrent save is rejected", async () => {
  vi.mocked(markingApi.profile).mockRejectedValueOnce(new ApiError("Профиль изменился — обновите данные", 409));
  openProfiles();
  fireEvent.change(await screen.findByLabelText("Смещение по ширине, мм"), { target: { value: "0.2" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить размеры" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Профиль изменился");
  expect(screen.getByLabelText("Смещение по ширине, мм")).toHaveValue(0.2);
  expect(screen.getByRole("button", { name: "Закрыть" })).toBeDisabled();
  expect(markingApi.profile).toHaveBeenCalledExactlyOnceWith(profile, { ...profile.geometry, offset_x_mm: 0.2 }, false);
});

it("reports overflowing trial text without a raster or a production enqueue", async () => {
  vi.mocked(markingApi.calibration).mockRejectedValueOnce(new ApiError("Надпись не помещается: АБВ 123", 422));
  openProfiles();
  fireEvent.click(await screen.findByRole("button", { name: "Показать пробную этикетку" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Надпись не помещается: АБВ 123");
  expect(screen.queryByRole("img")).toBeNull();
  expect(markingApi.enqueue).not.toHaveBeenCalled();
});

it("does not send a trial print if its recovery key cannot be persisted", async () => {
  openProfiles();
  const trial = await screen.findByRole("button", { name: "Пробная печать" });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("storage full"); });
  fireEvent.click(trial);
  expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось сохранить запрос пробной печати");
  expect(markingApi.calibration).not.toHaveBeenCalled();
});

