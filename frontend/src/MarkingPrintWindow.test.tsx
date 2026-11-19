import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MarkingPrintWindow, MarkingBatchProgress } from "./MarkingPrintWindow";
import { ApiError, type Principal } from "./api";
import { markingApi, type MarkingBatch, type MarkingCatalog, type MarkingPreview, type MarkingProfile } from "./markingApi";
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
const source = { task_id: "task", operation_id: "operation", version: 4 };
const preview: MarkingPreview = { manifest_hash: "manifest-1", profile_id: "profile", profile_version: 7, binding_version: 2, source_snapshot: {},
  labels: [{ key: "mark-1", task_id: "task", text: "КБ-011", material: "Лента", quantity: 2, serial: 11, png_b64: png, sha256: "png-1" },
    { key: "mark-1", task_id: "task", text: "КБ-012", material: "Лента", quantity: 2, serial: 12, png_b64: png, sha256: "png-2" },
    { key: "mark-2", task_id: "task", text: "Разъём А", material: "Лента", quantity: 2, serial: null, png_b64: png, sha256: "png-3" }],
  total: 6, width_mm: 92.5, height_mm: 20, orientation: "landscape", dpi: 300 };
const batch: MarkingBatch = { id: "batch-1", status: "spooled", status_label: "Передано принтеру", total: 6, reprint_of: null,
  jobs: Array.from({ length: 6 }, (_, sequence) => ({ id: `job-${sequence}`, sequence: sequence + 1, status: "spooled", status_label: "Передано принтеру", message: "" })) };

beforeEach(() => {
  sessionStorage.clear();
  vi.spyOn(markingApi, "machines").mockResolvedValue(structuredClone(catalog));
  vi.spyOn(markingApi, "preview").mockResolvedValue(structuredClone(preview));
  vi.spyOn(markingApi, "enqueue").mockResolvedValue(structuredClone(batch));
  vi.spyOn(markingApi, "batch").mockResolvedValue(structuredClone(batch));
  vi.spyOn(markingApi, "profile").mockImplementation(async (current, geometry, calibrated) => ({ ...current, geometry, calibrated, version: current.version + 1 }));
  vi.spyOn(markingApi, "bind").mockImplementation(async (_id, body) => ({ ...body, version: body.version + 1 }));
  vi.spyOn(markingApi, "calibration").mockResolvedValue({ png_b64: png, sha256: "calibration", width_mm: 92.5, height_mm: 20, orientation: "landscape", dpi: 300 });
});
afterEach(() => { vi.restoreAllMocks(); setRightsPrincipal(null); });

async function openPrint() {
  render(<MarkingPrintWindow source={source} title="ПР-1 · КБ1" close={vi.fn()} />);
  await screen.findByRole("option", { name: "ОБ.101 · ZCUT-RT430" });
  fireEvent.click(screen.getByRole("button", { name: "Обновить предпросмотр" }));
  await screen.findByRole("img", { name: "Этикетка: КБ-011" });
}

it("shows the exact server raster and enqueues the approved source/version without browser text or copies", async () => {
  await openPrint();
  expect(markingApi.preview).toHaveBeenCalledWith({ profile_id: "profile", profile_version: 7, source, selected: [] });
  expect(screen.getByRole("img", { name: "Этикетка: КБ-011" })).toHaveAttribute("src", `data:image/png;base64,${png}`);
  expect(screen.getByRole("status")).toHaveTextContent("К печати: 6 шт.");
  fireEvent.click(screen.getByRole("button", { name: "Следующая" }));
  expect(screen.getByRole("img", { name: "Этикетка: КБ-012" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Поставить в очередь" }));
  await screen.findByRole("region", { name: "Очередь печати" });
  expect(markingApi.enqueue).toHaveBeenCalledExactlyOnceWith({ profile_id: "profile", profile_version: 7, source, selected: [], manifest_hash: "manifest-1", idempotency_key: expect.any(String) });
  expect(screen.getByRole("progressbar")).toHaveAttribute("value", "6");
});

it("retries an ambiguous enqueue with the same key and prevents a duplicate click", async () => {
  vi.mocked(markingApi.enqueue).mockRejectedValueOnce(new TypeError("Failed to fetch"));
  await openPrint();
  fireEvent.click(screen.getByRole("button", { name: "Поставить в очередь" }));
  await screen.findByRole("alert");
  const retryButton = screen.getByRole("button", { name: "Повторить запрос" });
  fireEvent.click(retryButton);
  fireEvent.click(retryButton);
  await screen.findByRole("region", { name: "Очередь печати" });
  expect(markingApi.enqueue).toHaveBeenCalledTimes(2);
  expect(vi.mocked(markingApi.enqueue).mock.calls[0]).toEqual(vi.mocked(markingApi.enqueue).mock.calls[1]);
});

it("invalidates a preview when the profile changes and exposes stale-source errors", async () => {
  const other = { ...profile, id: "profile-2", name: "Другой материал" };
  vi.mocked(markingApi.machines).mockResolvedValue({ ...catalog, machines: [{ ...catalog.machines[0], profiles: [profile, other] }] });
  render(<MarkingPrintWindow source={source} title="Задание" close={vi.fn()} />);
  await screen.findByRole("option", { name: "Другой материал" });
  fireEvent.change(screen.getByLabelText("Материал / профиль"), { target: { value: "profile" } });
  fireEvent.click(screen.getByRole("button", { name: "Обновить предпросмотр" }));
  await screen.findByRole("img");
  fireEvent.change(screen.getByLabelText("Материал / профиль"), { target: { value: "profile-2" } });
  expect(screen.queryByRole("img")).toBeNull();
  expect(screen.getByRole("button", { name: "Поставить в очередь" })).toBeDisabled();
  vi.mocked(markingApi.preview).mockRejectedValueOnce(new Error("Источник или профиль изменился — обновите предпросмотр"));
  fireEvent.click(screen.getByRole("button", { name: "Обновить предпросмотр" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Источник или профиль изменился");
});

it("requires a new preview after selecting inscriptions, never treating an empty selection as all", async () => {
  await openPrint();
  fireEvent.click(screen.getByText("Выбрать надписи"));
  fireEvent.click(screen.getByRole("checkbox", { name: "Разъём А" }));
  expect(screen.getByRole("button", { name: "Поставить в очередь" })).toBeDisabled();
  vi.mocked(markingApi.preview).mockResolvedValueOnce({ ...preview, labels: preview.labels.slice(0, 2), total: 4, manifest_hash: "selected" });
  fireEvent.click(screen.getByRole("button", { name: "Показать выбранные" }));
  await waitFor(() => expect(markingApi.preview).toHaveBeenLastCalledWith(expect.objectContaining({ selected: ["mark-1"] })));
  await screen.findByText("К печати: 4 шт.");
  expect(screen.getByRole("button", { name: "Поставить в очередь" })).toBeEnabled();
});

it("requires a reason and a fresh manifest for a separate reprint batch", async () => {
  await openPrint();
  fireEvent.click(screen.getByRole("button", { name: "Поставить в очередь" }));
  fireEvent.click(await screen.findByRole("button", { name: "Повторная печать…" }));
  expect(screen.getByRole("button", { name: "Повторить печать" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Причина повторной печати"), { target: { value: "Повреждена лента" } });
  expect(screen.getByRole("button", { name: "Повторить печать" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Обновить предпросмотр" }));
  await screen.findByText("К печати: 6 шт.");
  fireEvent.click(screen.getByRole("button", { name: "Повторить печать" }));
  await waitFor(() => expect(markingApi.enqueue).toHaveBeenCalledTimes(2));
  expect(markingApi.enqueue).toHaveBeenLastCalledWith(expect.objectContaining({ reprint_of: "batch-1", reason: "Повреждена лента" }));
  expect(vi.mocked(markingApi.enqueue).mock.calls[0][0].idempotency_key).not.toBe(vi.mocked(markingApi.enqueue).mock.calls[1][0].idempotency_key);
});

it("allows an offline preview but blocks enqueueing and an uncalibrated profile", async () => {
  vi.mocked(markingApi.machines).mockResolvedValue({ ...catalog, printers: [{ ...catalog.printers[0], online: false }] });
  await openPrint();
  expect(screen.getByText(/Не на связи/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Поставить в очередь" })).toBeDisabled();
});

it("does not auto-select one of two bound machines", async () => {
  vi.mocked(markingApi.machines).mockResolvedValue({ ...catalog, machines: [...catalog.machines, { ...catalog.machines[0], equipment_id: "second", inventory_number: "ОБ.102" }] });
  render(<MarkingPrintWindow source={source} title="Задание" close={vi.fn()} />);
  await screen.findByRole("option", { name: "ОБ.102 · ZCUT-RT430" });
  expect(screen.getByLabelText("Станок")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Обновить предпросмотр" })).toBeDisabled();
});

it("blocks preview until the selected profile is calibrated", async () => {
  vi.mocked(markingApi.machines).mockResolvedValue({ ...catalog, machines: [{ ...catalog.machines[0], profiles: [{ ...profile, calibrated: false }] }] });
  render(<MarkingPrintWindow source={source} title="Задание" close={vi.fn()} />);
  await screen.findByLabelText("Станок");
  fireEvent.change(screen.getByLabelText("Станок"), { target: { value: "unit" } });
  fireEvent.change(screen.getByLabelText("Материал / профиль"), { target: { value: "profile" } });
  expect(screen.getByText(/Профиль не откалиброван —/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Обновить предпросмотр" })).toBeDisabled();
});

it("shows catalog errors with a retry and an honest empty state", async () => {
  vi.mocked(markingApi.machines).mockRejectedValueOnce(new TypeError("network"));
  render(<MarkingPrintWindow source={source} title="Задание" close={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи с ERP");
  vi.mocked(markingApi.machines).mockResolvedValueOnce({ machines: [], printers: [] });
  fireEvent.click(screen.getByRole("button", { name: "Повторить загрузку" }));
  expect(await screen.findByText(/Станки маркировки не найдены/)).toBeVisible();
});

it("keeps unknown delivery visible without automatic reprinting and accepts a checked result separately", async () => {
  const unknown: MarkingBatch = { ...batch, status: "unknown", status_label: "Результат неизвестен", jobs: [{ ...batch.jobs[0], status: "unknown", status_label: "Результат неизвестен" }] };
  vi.mocked(markingApi.batch).mockResolvedValue(unknown);
  vi.spyOn(markingApi, "resolve").mockResolvedValue(batch);
  render(<MarkingBatchProgress initial={unknown} />);
  expect(await screen.findByText(/Очередь приостановлена/)).toBeVisible();
  expect(markingApi.enqueue).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Указать результат" }));
  expect(screen.getByRole("button", { name: "Подтвердить проверку" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Комментарий проверки"), { target: { value: "Этикетки отсутствуют" } });
  fireEvent.click(screen.getByRole("button", { name: "Подтвердить проверку" }));
  await waitFor(() => expect(markingApi.resolve).toHaveBeenCalledWith("batch-1", "job-0", "failed", "Этикетки отсутствуют"));
});

it("preserves the batch after a status-read failure and only retries the read", async () => {
  vi.mocked(markingApi.batch).mockRejectedValueOnce(new TypeError("network"));
  render(<MarkingBatchProgress initial={batch} />);
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Обновить состояние" }));
  await waitFor(() => expect(markingApi.batch).toHaveBeenCalledTimes(2));
  expect(markingApi.enqueue).not.toHaveBeenCalled();
});

it("recovers the identical outstanding enqueue after the window closes and reopens", async () => {
  vi.mocked(markingApi.enqueue).mockRejectedValueOnce(new TypeError("network"));
  const first = render(<MarkingPrintWindow source={source} title="Задание" close={vi.fn()} />);
  await screen.findByRole("option", { name: "ОБ.101 · ZCUT-RT430" });
  fireEvent.click(screen.getByRole("button", { name: "Обновить предпросмотр" }));
  await screen.findByRole("img");
  fireEvent.click(screen.getByRole("button", { name: "Поставить в очередь" }));
  await screen.findByRole("button", { name: "Повторить запрос" });
  first.unmount();
  render(<MarkingPrintWindow source={source} title="Задание" close={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Повторить запрос" }));
  await screen.findByRole("region", { name: "Очередь печати" });
  expect(vi.mocked(markingApi.enqueue).mock.calls[0]).toEqual(vi.mocked(markingApi.enqueue).mock.calls[1]);
  expect(markingApi.preview).toHaveBeenCalledTimes(1);
});

it("restores a known batch and preserves its selected inscriptions for explicit reprint", async () => {
  const selectedPreview = { ...preview, labels: [preview.labels[2]], total: 2 };
  sessionStorage.setItem("marking-print:local:task:task:operation", JSON.stringify({ pending: false, batch,
    request: { profile_id: "profile", profile_version: 7, source, selected: ["mark-2"], manifest_hash: "old", idempotency_key: "old-key" } }));
  vi.mocked(markingApi.preview).mockResolvedValue(selectedPreview);
  render(<MarkingPrintWindow source={source} title="Задание" close={vi.fn()} />);
  await screen.findByRole("region", { name: "Очередь печати" });
  expect(screen.queryByRole("button", { name: "Поставить в очередь" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Повторная печать…" }));
  await screen.findByRole("option", { name: "ОБ.101 · ZCUT-RT430" });
  fireEvent.click(screen.getByRole("button", { name: "Обновить предпросмотр" }));
  await screen.findByRole("img");
  expect(markingApi.preview).toHaveBeenCalledWith(expect.objectContaining({ selected: ["mark-2"] }));
  expect(screen.queryByText("Выбрать надписи")).toBeNull();
});

it("does not send a print request if the browser cannot persist the recovery key", async () => {
  await openPrint();
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("storage full"); });
  fireEvent.click(screen.getByRole("button", { name: "Поставить в очередь" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось сохранить запрос печати");
  expect(markingApi.enqueue).not.toHaveBeenCalled();
});

it("allows a fresh disjoint batch while reserving already queued inscriptions for explicit reprint", async () => {
  sessionStorage.setItem("marking-print:local:task:task:operation", JSON.stringify({ pending: false, batch, usedKeys: ["mark-1"],
    request: { profile_id: "profile", profile_version: 7, source, selected: ["mark-1"], manifest_hash: "old", idempotency_key: "old-key" } }));
  render(<MarkingPrintWindow source={source} title="Задание" close={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Новая партия…" }));
  await screen.findByRole("option", { name: "ОБ.101 · ZCUT-RT430" });
  fireEvent.click(screen.getByRole("button", { name: "Обновить предпросмотр" }));
  await screen.findByRole("img");
  expect(screen.getByRole("button", { name: "Поставить в очередь" })).toBeDisabled();
  fireEvent.click(screen.getByText("Выбрать надписи"));
  expect(screen.getByRole("checkbox", { name: "КБ-012" })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: "Разъём А" })).toBeChecked();
  vi.mocked(markingApi.preview).mockResolvedValueOnce({ ...preview, labels: [preview.labels[2]], total: 2 });
  fireEvent.click(screen.getByRole("button", { name: "Показать выбранные" }));
  await screen.findByText("К печати: 2 шт.");
  fireEvent.click(screen.getByRole("button", { name: "Поставить в очередь" }));
  await waitFor(() => expect(markingApi.enqueue).toHaveBeenCalledTimes(1));
  expect(markingApi.enqueue).toHaveBeenCalledWith(expect.objectContaining({ selected: ["mark-2"] }));
  expect(vi.mocked(markingApi.enqueue).mock.calls[0][0]).not.toHaveProperty("reprint_of");
});

it("retains the original reprint source after a definitive rejection of a new disjoint batch", async () => {
  sessionStorage.setItem("marking-print:local:task:task:operation", JSON.stringify({ pending: true, batch, usedKeys: ["mark-1", "mark-2"], previousKeys: ["mark-1"],
    previousRequest: { profile_id: "profile", profile_version: 7, source, selected: ["mark-1"], manifest_hash: "old", idempotency_key: "old-key" },
    request: { profile_id: "profile", profile_version: 7, source, selected: ["mark-2"], manifest_hash: "new", idempotency_key: "new-key" } }));
  vi.mocked(markingApi.enqueue).mockRejectedValueOnce(new ApiError("Источник изменился", 409));
  render(<MarkingPrintWindow source={source} title="Задание" close={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Повторить запрос" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Повторная печать…" }));
  await screen.findByRole("option", { name: "ОБ.101 · ZCUT-RT430" });
  fireEvent.click(screen.getByRole("button", { name: "Обновить предпросмотр" }));
  await waitFor(() => expect(markingApi.preview).toHaveBeenCalledWith(expect.objectContaining({ selected: ["mark-1"] })));
});

it("disables enqueue and operator reconciliation without the corresponding permissions", async () => {
  setRightsPrincipal({ user: { id: "viewer" }, roles: [], permissions: { myops: "view" } } as unknown as Principal);
  await openPrint();
  expect(screen.getByRole("button", { name: "Поставить в очередь" })).toBeDisabled();
});

it("can explicitly reprint an earlier subset after another disjoint batch", async () => {
  vi.mocked(markingApi.preview).mockResolvedValue({ ...preview, labels: preview.labels.slice(0, 2), total: 4 });
  const request = { profile_id: "profile", profile_version: 7, source, selected: ["mark-1"], manifest_hash: "first", idempotency_key: "first-key" };
  const second = { ...request, selected: ["mark-2"], manifest_hash: "second", idempotency_key: "second-key" };
  sessionStorage.setItem("marking-print:local:task:task:operation", JSON.stringify({ pending: false, batch: { ...batch, id: "batch-B" }, request: second, usedKeys: ["mark-1", "mark-2"],
    history: [{ id: "batch-A", request, total: 4 }, { id: "batch-B", request: second, total: 2 }] }));
  render(<MarkingPrintWindow source={source} title="Задание" close={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Повторная печать…" }));
  fireEvent.change(screen.getByLabelText("Партия для повторной печати"), { target: { value: "batch-A" } });
  await screen.findByRole("option", { name: "ОБ.101 · ZCUT-RT430" });
  fireEvent.click(screen.getByRole("button", { name: "Обновить предпросмотр" }));
  await screen.findByRole("img");
  expect(markingApi.preview).toHaveBeenCalledWith(expect.objectContaining({ selected: ["mark-1"] }));
  fireEvent.change(screen.getByLabelText("Причина повторной печати"), { target: { value: "Повреждена первая партия" } });
  fireEvent.click(screen.getByRole("button", { name: "Повторить печать" }));
  await waitFor(() => expect(markingApi.enqueue).toHaveBeenCalledWith(expect.objectContaining({ reprint_of: "batch-A", selected: ["mark-1"], reason: "Повреждена первая партия" })));
});

it("allocates a fresh key for another deliberate reprint of the same original batch and reason", async () => {
  vi.mocked(markingApi.batch).mockImplementation(async (id) => ({ ...batch, id }));
  const request = { profile_id: "profile", profile_version: 7, source, selected: ["mark-1"], manifest_hash: "original", idempotency_key: "original-key" };
  sessionStorage.setItem("marking-print:local:task:task:operation", JSON.stringify({ pending: false, batch: { ...batch, id: "original" },
    request, usedKeys: ["mark-1"], history: [{ id: "original", request, total: 4 }] }));
  vi.mocked(markingApi.preview).mockResolvedValue({ ...preview, labels: preview.labels.slice(0, 2), total: 4 });
  render(<MarkingPrintWindow source={source} title="Задание" close={vi.fn()} />);
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    fireEvent.click(await screen.findByRole("button", { name: "Повторная печать…" }));
    if (attempt === 2) fireEvent.change(screen.getByLabelText("Партия для повторной печати"), { target: { value: "original" } });
    await screen.findByRole("option", { name: "ОБ.101 · ZCUT-RT430" });
    fireEvent.click(screen.getByRole("button", { name: "Обновить предпросмотр" }));
    await screen.findByRole("img");
    fireEvent.change(screen.getByLabelText("Причина повторной печати"), { target: { value: "Брак" } });
    fireEvent.click(screen.getByRole("button", { name: "Повторить печать" }));
    await waitFor(() => expect(markingApi.enqueue).toHaveBeenCalledTimes(attempt));
  }
  const [first, second] = vi.mocked(markingApi.enqueue).mock.calls.map(([payload]) => payload);
  expect(first).toMatchObject({ reprint_of: "original", reason: "Брак" });
  expect(second).toEqual({ ...first, idempotency_key: expect.any(String) });
  expect(second.idempotency_key).not.toBe(first.idempotency_key);
});

it("shows each half of a flag in its own column when the ETK text differs per half", async () => {
  vi.mocked(markingApi.preview).mockResolvedValueOnce({ ...preview, labels: [
    { ...preview.labels[0], text: "X1 | КБ-011", halves: ["X1", "КБ-011"] },
    { ...preview.labels[2], halves: ["Разъём А", "Разъём А"] }], total: 4 });
  render(<MarkingPrintWindow source={source} title="ПР-1 · КБ1" close={vi.fn()} />);
  await screen.findByRole("option", { name: "ОБ.101 · ZCUT-RT430" });
  fireEvent.click(screen.getByRole("button", { name: "Обновить предпросмотр" }));
  await screen.findByRole("img", { name: "Этикетка: X1 | КБ-011" });
  const table = document.body;
  expect(within(table).getByRole("columnheader", { name: /Левая половина/ })).toBeInTheDocument();
  expect(within(table).getByRole("columnheader", { name: /Правая половина/ })).toBeInTheDocument();
  expect(within(table).getAllByText("Разъём А").length).toBeGreaterThanOrEqual(2);
  expect(within(table).queryByRole("columnheader", { name: /^Надпись/ })).toBeNull();
});

it("shows honest print progress: printed N of M, queued K, and a waiting printer instead of «Передано принтеру»", async () => {
  const states = ["printed", "queued", "queued", "queued", "error", "cancelled"] as const;
  const tracked: MarkingBatch = { ...structuredClone(batch), status_label: "Печатается", printed: 1, queued: 3, print_errors: 1, cancelled: 1, untracked: 0,
    progress_label: "Напечатано 1 из 6 · в очереди 4", stuck: true, stuck_label: "Принтер ждёт (снимите этикетку / проверьте принтер)",
    jobs: batch.jobs.map((job, index) => ({ ...job, print_state: states[index], status_label: states[index] === "printed" ? "Напечатано" : "В очереди принтера" })) };
  vi.mocked(markingApi.batch).mockResolvedValue(structuredClone(tracked));
  render(<MarkingBatchProgress initial={tracked} />);
  expect(screen.getByRole("status")).toHaveTextContent("Напечатано 1 из 6 · в очереди 4");
  expect(screen.queryByText(/Передано принтеру: /)).toBeNull();
  expect(screen.getByRole("progressbar")).toHaveAttribute("value", "1");
  expect(await screen.findByRole("alert")).toHaveTextContent("Принтер ждёт (снимите этикетку / проверьте принтер)");
  expect(screen.getByText(/Ошибок принтера: 1/)).toBeVisible();
  expect(screen.getByText(/Отменено в очереди Windows: 1/)).toBeVisible();
});
