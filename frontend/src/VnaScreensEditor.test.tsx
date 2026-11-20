import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { ScreenSwitcher, VnaScreensEditor, allowed, draftOf, fieldsOf, showBlock, yRange } from "./VnaScreensEditor";
import { vnaScreensApi, type VnaBenchSession, type VnaScreen } from "./vnaBenchApi";

/* Формы — как отвечает сервер `backend/app/workflows/vna_screens_api.py` (S5b). */

afterEach(() => { vi.restoreAllMocks(); });

const adapter: VnaScreen = {
  id: "sc-adapter", name: "ОТК — переход", purpose: "adapter", is_default: true, split: 8, note: "замер переходника",
  markers_hz: [1e9, 3e9, 6e9, 12e9, 18e9], active: true, version: 1, updated_by: null, updated_at: "2026-11-20T00:00:00+00:00",
  traces: [
    { param: "S11", format: "SWR", scale: "norm", limit: "norm", fixed: null, hidden: false, memory: false },
    { param: "S21", format: "MLOG", scale: { pdiv: 0.05, rlev: 0, rpos: 10 }, limit: "norm", fixed: null, hidden: false, memory: false },
    { param: "S12", format: "MLOG", scale: { pdiv: 0.05, rlev: 0, rpos: 10 }, limit: "norm", fixed: null, hidden: false, memory: false },
    { param: "S22", format: "SWR", scale: "norm", limit: "norm", fixed: null, hidden: false, memory: false },
  ],
};
const engineer: VnaScreen = { ...adapter, id: "sc-eng", name: "Подробно (инженер)", purpose: "engineer", split: 15,
  traces: [{ param: "S11", format: "SMIT", scale: { pdiv: 1, rlev: null, rpos: null }, limit: "none", fixed: null, hidden: false, memory: false }] };
const off: VnaScreen = { ...adapter, id: "sc-off", name: "Старый экран", is_default: false, active: false, version: 3,
  updated_by: "Контролёр", updated_at: "2026-11-19T14:00:00+03:00" };

const session = (extra: Partial<VnaBenchSession> = {}): VnaBenchSession => ({
  id: "s1", version: 3, status: "open", state: "ready", owned: true, error: null, started_at: "2026-11-20T09:00:00+03:00",
  operator: "Контролёр", steps: [], command: null, items: [], checks: [], ...extra,
});

it("lists screens in Russian with purpose, graphs, layout and state", async () => {
  vi.spyOn(vnaScreensApi, "list").mockResolvedValue({ screens: [adapter, engineer, off] });
  render(<VnaScreensEditor session={session({ view_screen_id: "sc-eng" })} calibrationValid canEdit show={vi.fn()} />);
  const table = await screen.findByRole("table", { name: "Экраны ВАЦ" });
  const rows = within(table).getAllByRole("row");
  expect(rows[1].textContent).toContain("Переход (СИ)");
  expect(rows[1].textContent).toContain("КСВН S11 · Потери (LOGM) S21");
  expect(rows[1].textContent).toContain("4 — 2 × 2");
  expect(rows[1].textContent).toContain("основной");
  expect(rows[2].textContent).toContain("на ВАЦ");
  expect(rows[3].textContent).toContain("отключён");
  expect(table.textContent).not.toMatch(/SWR|MLOG|adapter|engineer/);
});

it("edits a manual scale, saves a new version and shows the saved screen on the VNA", async () => {
  vi.spyOn(vnaScreensApi, "list").mockResolvedValue({ screens: [adapter] });
  const save = vi.spyOn(vnaScreensApi, "save").mockImplementation(async (_id, version, fields) => ({ ...adapter, ...fields, version: version + 1 }));
  const show = vi.fn().mockResolvedValue("");
  render(<VnaScreensEditor session={session()} calibrationValid canEdit show={show} />);
  fireEvent.click(await screen.findByText("ОТК — переход"));
  const dialog = await screen.findByRole("dialog", { name: "Экран ВАЦ ОТК — переход" });
  expect(within(dialog).getByRole("button", { name: "Сохранить" })).toBeDisabled();   // nothing changed yet
  fireEvent.change(within(dialog).getByLabelText("График 1: шкала"), { target: { value: "manual" } });
  fireEvent.change(within(dialog).getByLabelText("График 1: деление"), { target: { value: "0,02" } });
  fireEvent.change(within(dialog).getByLabelText("График 1: уровень"), { target: { value: "1" } });
  fireEvent.change(within(dialog).getByLabelText("График 1: позиция"), { target: { value: "0" } });
  expect(within(dialog).getByRole("button", { name: "Показать на ВАЦ" })).toBeDisabled();   // unsaved changes first
  fireEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(save).toHaveBeenCalledTimes(1));
  const [id, version, fields] = save.mock.calls[0];
  expect([id, version]).toEqual(["sc-adapter", 1]);
  expect(fields.traces[0].scale).toEqual({ pdiv: 0.02, rlev: 1, rpos: 0 });
  expect(fields.traces[1].scale).toEqual({ pdiv: 0.05, rlev: 0, rpos: 10 });
  expect(fields.markers_hz).toEqual([1e9, 3e9, 6e9, 12e9, 18e9]);
  expect(await screen.findByText("Экран «ОТК — переход» сохранён, версия 2")).toBeTruthy();
  const reopened = await screen.findByRole("dialog");
  fireEvent.click(within(reopened).getByRole("button", { name: "Показать на ВАЦ" }));
  await waitFor(() => expect(show).toHaveBeenCalledWith("sc-adapter"));
  expect(await within(reopened).findByText(/отправлен на ВАЦ/)).toBeTruthy();
});

it("shows the server's refusal inside the window and blocks the VNA without a calibrated session", async () => {
  vi.spyOn(vnaScreensApi, "list").mockResolvedValue({ screens: [adapter] });
  vi.spyOn(vnaScreensApi, "save").mockRejectedValue(new ApiError("Экран уже изменили — откройте его заново", 409,
    { code: "stale", message: "Экран уже изменили — откройте его заново" }));
  render(<VnaScreensEditor session={session()} calibrationValid={false} canEdit show={vi.fn()} />);
  fireEvent.click(await screen.findByText("ОТК — переход"));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByRole("button", { name: "Показать на ВАЦ" })).toBeDisabled();
  expect(within(dialog).getByText("Сначала калибровка АКМ и нагрузка на обоих портах")).toBeTruthy();
  fireEvent.change(within(dialog).getByLabelText("Название"), { target: { value: "Переход крупно" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));
  expect((await within(dialog).findByRole("alert")).textContent).toContain("Экран уже изменили");
});

it("read-only for ОТК view: no save, no new screen", async () => {
  vi.spyOn(vnaScreensApi, "list").mockResolvedValue({ screens: [adapter] });
  render(<VnaScreensEditor session={null} calibrationValid={false} canEdit={false} show={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "Новый экран" })).toBeNull();
  fireEvent.click(await screen.findByText("ОТК — переход"));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).queryByRole("button", { name: "Сохранить" })).toBeNull();
  expect(within(dialog).getByLabelText("Название")).toBeDisabled();
});

it("a new screen is created with an own-number line and the format limits the choices", async () => {
  vi.spyOn(vnaScreensApi, "list").mockResolvedValue({ screens: [adapter] });
  const create = vi.spyOn(vnaScreensApi, "create").mockImplementation(async (fields) => ({ ...adapter, ...fields, id: "new", version: 1 }));
  render(<VnaScreensEditor session={null} calibrationValid={false} canEdit show={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Новый экран" }));
  const dialog = await screen.findByRole("dialog", { name: "Новый экран ВАЦ" });
  fireEvent.change(within(dialog).getByLabelText("Название"), { target: { value: "Потери крупно" } });
  fireEvent.change(within(dialog).getByLabelText("Расположение графиков"), { target: { value: "3" } });
  fireEvent.change(within(dialog).getByLabelText("График 2: линия допуска"), { target: { value: "fixed" } });
  fireEvent.change(within(dialog).getByLabelText("График 2: число линии"), { target: { value: "-0,3" } });
  const format = within(dialog).getByLabelText("График 1: формат");
  fireEvent.change(format, { target: { value: "SMIT" } });
  expect(within(within(dialog).getByLabelText("График 1: линия допуска") as HTMLElement).getAllByRole("option").map((o) => o.textContent))
    .toEqual(["без линии"]);
  fireEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  const fields = create.mock.calls[0][0];
  expect(fields.split).toBe(3);
  expect(fields.traces[1]).toMatchObject({ format: "MLOG", limit: "fixed", fixed: { kind: "lower", value: -0.3 } });
  expect(fields.traces[0]).toMatchObject({ format: "SMIT", scale: "auto", limit: "none" });
});

it("form helpers: number typing, scale range and the VNA block reasons", () => {
  const draft = draftOf(adapter);
  draft.traces[0].mode = "manual";
  draft.traces[0].pdiv = "0";
  expect(fieldsOf(draft)).toEqual({ error: "График 1: деление — число больше нуля" });
  draft.traces[0].pdiv = "0,05"; draft.traces[0].rlev = "1"; draft.traces[0].rpos = "11";
  expect(fieldsOf(draft)).toEqual({ error: "График 1: позиция — целое от 0 до 10" });
  draft.traces[0].rpos = "0";
  expect(yRange(draft.traces[0], [])).toEqual([1, 1.5]);
  draft.markers = "1; три";
  expect(fieldsOf(draft)).toEqual({ error: "Маркеры: «три» — не частота в ГГц" });
  expect(allowed("PHAS")).toEqual({ modes: ["auto", "manual"], limits: ["fixed", "none"] });
  expect(showBlock(null, false)).toMatch(/открытом сеансе/);
  expect(showBlock(session({ owned: false }), true)).toBe("Сеанс открыл другой сотрудник");
  expect(showBlock(session({ command: { label: "развёртка", state: "running", since: "" } }), true)).toMatch(/выполняет команду/);
  expect(showBlock(session(), true)).toBe("");
});

it("the switcher on «Замер» shows only active screens and reports a refusal", async () => {
  vi.spyOn(vnaScreensApi, "list").mockResolvedValue({ screens: [adapter, engineer, off] });
  const show = vi.fn().mockResolvedValue("Калибровка истекла (4 часа или конец смены) — откалибруйте заново.");
  render(<ScreenSwitcher session={session()} disabled={false} show={show} />);
  const select = await screen.findByLabelText("Вид на ВАЦ — сохранённый экран, без развёртки");
  expect(within(select as HTMLElement).getAllByRole("option").map((o) => o.textContent))
    .toEqual(["Выберите экран", "ОТК — переход", "Подробно (инженер)"]);
  fireEvent.change(select, { target: { value: "sc-eng" } });
  fireEvent.click(screen.getByRole("button", { name: "Показать на ВАЦ" }));
  await waitFor(() => expect(show).toHaveBeenCalledWith("sc-eng"));
  expect((await screen.findByRole("alert")).textContent).toMatch(/Калибровка истекла/);
});
