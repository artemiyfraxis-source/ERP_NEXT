import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError, productionApi, vnaCaptureApi, type ProductionInstrument } from "./api";
import { VnaBench, connectionPrompt, dueText, phaseHint } from "./VnaBench";
import {
  refusalText,
  vnaBenchApi,
  vnaBenchAvailable,
  type VnaBenchItem,
  type VnaBenchPlan,
  type VnaBenchSession,
  type VnaBenchState,
  type VnaBenchStep,
  type VnaBenchTarget,
} from "./vnaBenchApi";

/* Формы — как отвечает сервер `backend/app/workflows/vna_bench_api.py` (PR #973): `_state`, `_plan`, `resolve`. */

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

const step = (key: VnaBenchStep["key"], status: VnaBenchStep["status"], text: string | null = null): VnaBenchStep =>
  ({ key, status, result_text: text, refusal: null, finished_at: status === "pending" ? null : "2026-11-19T14:10:00+03:00" });
const allPassed: VnaBenchStep[] = [
  step("calibration", "passed"), step("confidence", "passed", "передача 0,031 дБ ≤ 0,1; отражение 0,12 дБ ≤ 0,4/0,5"),
  step("load_port1", "passed", "КСВН 1,032 ≤ 1,06"), step("load_port2", "passed", "КСВН 1,028 ≤ 1,06"),
];

const session = (extra: Partial<VnaBenchSession> = {}): VnaBenchSession => ({
  id: "s1", version: 3, status: "open", state: "ready", owned: true, error: null, started_at: "2026-11-19T14:00:00+03:00",
  operator: "Контролёр", steps: [], command: null, items: [], checks: [], ...extra,
});

const state = (extra: Partial<VnaBenchState> = {}): VnaBenchState => ({
  device: {
    pc_name: "ОТК-121", agent_online: true, agent_version: "2.10.0", instrument_number: "СИ.002", instrument_id: "si-002",
    model: "Planar S50180", serial: "26030095", s2vna_ready: true, warmup_min: 95,
    temperatures: { vna_c: [31.2, 30.8], acm_c: 30.9 }, acm: { model: "ACM8000T", serial: "26140008" }, refusal: null,
    read_at: "2026-11-19T14:20:00+03:00",
  },
  calibration: { valid: true, calibrated_at: "2026-11-19T14:40:00+03:00", valid_until: "2026-11-19T18:40:00+03:00", reason: null, temperatures: null },
  due: [
    { kind: "flex", due_at: null, last_at: null, status: "due" },
    { kind: "weekly", due_at: "2026-11-23T00:00:00+03:00", last_at: "2026-11-16T10:00:00+03:00", status: "ok" },
    { kind: "monthly", due_at: "2026-11-18T00:00:00+03:00", last_at: null, status: "overdue" },
  ],
  session: session(),
  ...extra,
});

const si035: VnaBenchTarget = { kind: "instrument", instrument_id: "si-035", number: "СИ.035", name: "Переход ADP1A", serial_number: "N8B141" };
const kb: VnaBenchTarget = { kind: "assembly", task_id: "t1", task_number: "ПЗ-0001", position: 3, serial: null, name: "Кабель КБ1",
  item: "591К.3.КБ1", operation_id: "op-vswr" };
const plan: VnaBenchPlan = {
  screen_name: "ОТК — переход", band_mhz: [10, 18000], points: 1601, ifbw_hz: 1000, power_dbm: 0,
  traces: [{ label: "КСВН S11", scale: "1…1,1" }, { label: "Модуль S21", scale: "−0,5…0 дБ" }],
  norm_vswr: 1.06, norm_source: "ADP1A, изготовитель", uncertainty: 0.012,
  connection_hint: "Цепочка «напрямую»: сторона 1 — к порту 1 ВАЦ, сторона 2 — к порту 2", refusal: null,
};
const item = (extra: Partial<VnaBenchItem> = {}): VnaBenchItem => ({
  id: "i1", version: 3, target: si035, plan, connections_total: 1, connections: [], trace: [], verdict: null, refusal: null,
  recorded_at: null, recorded_to: null, label_instrument_id: null, ...extra,
});
const instrument = (id: string, number: string, files: string[]): ProductionInstrument => ({
  id, inventory_number: number, name: `Переход ${number}`, files: files.map((file_name, index) => ({
    id: `${id}-${index}`, kind: "Прочее", file_name, size: 1, uploaded_at: "2026-11-16T10:00:00+03:00", uploaded_by_name: "ОТК" })),
} as unknown as ProductionInstrument);

async function findObject(text: string) {
  fireEvent.change(await screen.findByLabelText("СИ или КБ — скан или номер"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Найти" }));
}

it("shows the bench header: PC, VNA, S2VNA, temperatures, calibration time and check due dates", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state());
  render(<VnaBench canEdit />);
  expect(await screen.findByText("ОТК-121")).toBeInTheDocument();
  expect(screen.getByText("СИ.002 Planar S50180, зав. № 26030095")).toBeInTheDocument();
  expect(screen.getByText("Готов")).toBeInTheDocument();
  expect(screen.getByText("ВАЦ 31,2 °C / 30,8 °C · АКМ 30,9 °C")).toBeInTheDocument();
  expect(screen.getByText("действует до 18:40")).toBeInTheDocument();
  expect(screen.getByText("до 23.11")).toBeInTheDocument();
  expect(screen.getByText("просрочена с 18.11")).toBeInTheDocument();
});

it("keeps the agent online when the VNA is disconnected and shows the last PC", async () => {
  const message = "ВАЦ не подключён. Последний раз виден 20.11 16:09 МСК на DESKTOP-PTA57QF. Включите ВАЦ, подключите USB и запустите S2VNA.";
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: null, device: { ...state().device,
    bound_pc: "ОТК-121", vna_online: false, serial: null, s2vna_ready: null,
    last_seen_pc: "DESKTOP-PTA57QF", last_seen_at: "2026-11-20T16:09:00+03:00",
    refusal: { code: "vna_not_connected", message },
  } }));
  render(<VnaBench canEdit />);
  expect(await screen.findByText("Агент на связи")).toBeInTheDocument();
  expect(screen.getByText(/· ВАЦ не подключён/)).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent(message);
  expect(screen.queryByText(/программа печати/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Перенести ВАЦ/ })).not.toBeInTheDocument();
});

it("says when the bound PC agent is offline", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: null, device: { ...state().device,
    agent_online: false, vna_online: false, serial: null, s2vna_ready: null,
    refusal: { code: "agent_offline", message: "" },
  } }));
  render(<VnaBench canEdit />);
  expect(await screen.findByText("Агент не на связи")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent(/запущен агент ERP/);
});

it("uses the existing transfer API only on an explicit click and refreshes state", async () => {
  const original = state({ session: null, device: { ...state().device, bound_pc: "ОТК-121", vna_online: false,
    last_seen_pc: "DESKTOP-PTA57QF", last_seen_at: "2026-11-20T16:09:00+03:00",
    transfer: { serial: "26030095", owner_id: "pc-1", agents: [{ id: "pc-2", pc: "DESKTOP-PTA57QF", version: "2.12.0", vna: null }] },
  } });
  const load = vi.spyOn(vnaBenchApi, "state").mockResolvedValue(original);
  const transfer = vi.spyOn(vnaCaptureApi, "transfer").mockResolvedValue({ status: "transferred" });
  render(<VnaBench canEdit />);
  const button = await screen.findByRole("button", { name: "Перенести ВАЦ на DESKTOP-PTA57QF" });
  expect(transfer).not.toHaveBeenCalled();
  load.mockResolvedValue(state({ session: null, device: { ...state().device, pc_name: "DESKTOP-PTA57QF", bound_pc: "DESKTOP-PTA57QF" } }));
  fireEvent.click(button);
  await waitFor(() => expect(transfer).toHaveBeenCalledWith("si-002", "26030095", "pc-1", "pc-2"));
  expect(await screen.findByText("ВАЦ перенесён на DESKTOP-PTA57QF")).toBeInTheDocument();
  expect(load).toHaveBeenCalledTimes(2);
});

it("refreshes a disconnected station until the new PC reports the VNA", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const disconnected = state({ session: null, device: { ...state().device,
    refusal: { code: "vna_not_connected", message: "Подключите ВАЦ" }, transfer: null,
  } });
  const load = vi.spyOn(vnaBenchApi, "state").mockResolvedValue(disconnected);
  render(<VnaBench canEdit />);
  await screen.findByRole("alert");
  load.mockResolvedValue({ ...disconnected, device: { ...disconnected.device,
    transfer: { serial: "26030095", owner_id: "pc-1", agents: [{ id: "pc-2", pc: "НОВЫЙ-ПК", version: "2.12.0", vna: null }] },
  } });
  await act(async () => { await vi.advanceTimersByTimeAsync(10_100); });
  expect(screen.getByRole("button", { name: "Перенести ВАЦ на НОВЫЙ-ПК" })).toBeInTheDocument();
});

it("shows the transfer refusal beside the action and refreshes a stale offer", async () => {
  const offered = state({ session: null, device: { ...state().device,
    transfer: { serial: "26030095", owner_id: "pc-1", agents: [{ id: "pc-2", pc: "НОВЫЙ-ПК", version: "2.12.0", vna: null }] },
  } });
  const load = vi.spyOn(vnaBenchApi, "state").mockResolvedValue(offered);
  vi.spyOn(vnaCaptureApi, "transfer").mockRejectedValue(new ApiError("Прибор уже отключён", 409));
  render(<VnaBench canEdit />);
  const button = await screen.findByRole("button", { name: "Перенести ВАЦ на НОВЫЙ-ПК" });
  load.mockResolvedValue(state({ session: null }));
  fireEvent.click(button);
  expect(await screen.findByRole("alert")).toHaveTextContent("Прибор уже отключён");
  await waitFor(() => expect(screen.queryByRole("button", { name: /Перенести ВАЦ/ })).not.toBeInTheDocument());
  expect(screen.queryByText(/ВАЦ перенесён на/)).not.toBeInTheDocument();
});

it("explains why an active session blocks transfer", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ device: { ...state().device, transfer: null,
    transfer_blocked_reason: "Сначала завершите сеанс ВАЦ на закреплённом ПК, затем перенесите прибор",
  } }));
  render(<VnaBench canEdit />);
  expect(await screen.findByText(/Сначала завершите сеанс ВАЦ/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Перенести ВАЦ/ })).not.toBeInTheDocument();
});

it("says why the calibration is invalid and why the device is refused, in Russian", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({
    calibration: { valid: false, calibrated_at: null, valid_until: null, reason: "Сеанс не открыт", temperatures: null },
    device: { ...state().device, s2vna_ready: false, warmup_min: 12, refusal: { code: "serial_changed", message: "Серийный номер не совпал" } },
    session: null,
  }));
  render(<VnaBench canEdit />);
  expect(await screen.findByText("Сеанс не открыт")).toBeInTheDocument();
  expect(screen.getByText("Не готов — прогрев 12 из 40 мин")).toBeInTheDocument();
  expect(screen.getAllByRole("alert")[0]).toHaveTextContent("Заводской номер ВАЦ не совпал с карточкой СИ.002");
  expect(screen.getByRole("button", { name: "Открыть сеанс для СИ и проверок" })).toBeDisabled();
});

it("opens a session on the adapter grid and confirms the calibration; the confidence test has no button", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: null }));
  const start = vi.spyOn(vnaBenchApi, "startSession").mockResolvedValue(state({ session: session({ steps: [step("calibration", "pending")] }) }));
  const run = vi.spyOn(vnaBenchApi, "runStep").mockResolvedValue(state({ session: session({ steps: [step("calibration", "passed"), step("confidence", "running")] }) }));
  render(<VnaBench canEdit />);
  fireEvent.click(await screen.findByRole("button", { name: "Открыть сеанс для СИ и проверок" }));
  await waitFor(() => expect(start).toHaveBeenCalledWith({}));

  const steps = await screen.findByRole("list", { name: "Шаги сеанса" });
  expect(within(steps).getAllByRole("listitem").map((li) => li.querySelector("strong")?.textContent))
    .toEqual(["Калибровка АКМ", "Доверительный тест", "Нагрузка: порт 1", "Нагрузка: порт 2"]);
  expect(screen.getByText(/Подключите АКМ к концам обоих кабелей/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Калибровать АКМ" }));
  await waitFor(() => expect(run).toHaveBeenCalledWith("s1", 3, "calibration"));
  expect(await screen.findByText(/Тест идёт сам сразу после калибровки/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /доверительный тест/i })).not.toBeInTheDocument();
});

it("offers only a new calibration after a failed confidence test", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: [step("calibration", "passed"),
    { ...step("confidence", "failed", "передача 0,141 дБ ≤ 0,1"), refusal: { code: "calibration_required", message: "…" } }] }) }));
  const run = vi.spyOn(vnaBenchApi, "runStep").mockResolvedValue(state());
  render(<VnaBench canEdit />);
  expect(await screen.findByText(/Калибровка не подтверждена/)).toBeInTheDocument();
  expect(screen.getByText(/Доверительный тест: не пройдено/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Откалибровать заново" }));
  await waitFor(() => expect(run).toHaveBeenCalledWith("s1", 3, "calibration"));
});

it("offers a new calibration when the calibration expired after all steps", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: allPassed }),
    calibration: { valid: false, calibrated_at: "2026-11-19T10:00:00+03:00", valid_until: "2026-11-19T14:00:00+03:00",
      reason: "Калибровка старше 4 часов — откалибруйте заново", temperatures: null } }));
  render(<VnaBench canEdit />);
  expect(await screen.findByText("Калибровка недействительна.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Откалибровать заново" })).toBeEnabled();
});

it("finds an adapter, shows its plan card with the fixture chain and the suggested top frequency", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: allPassed.slice(0, 2) }) }));
  const resolve = vi.spyOn(vnaBenchApi, "resolve").mockResolvedValue({ candidates: [si035] });
  const planned = vi.spyOn(vnaBenchApi, "plan").mockResolvedValue(plan);
  vi.spyOn(productionApi, "instruments").mockResolvedValue({ instruments: [
    instrument("si-009", "СИ.009", ["Замер ВАЦ S50180, зав. N8B140.s2p"]), instrument("si-008", "СИ.008", ["СИ.008.s2p"]),
    instrument("si-040", "СИ.040", ["паспорт.pdf"]), instrument("si-035", "СИ.035", ["СИ.035.s2p"]),
  ] });
  render(<VnaBench canEdit />);
  await findObject("СИ.035");
  await waitFor(() => expect(resolve).toHaveBeenCalledWith("СИ.035"));
  expect(await screen.findByText("ОТК — переход")).toBeInTheDocument();
  expect(screen.getByText("10 – 18 000 МГц")).toBeInTheDocument();
  expect(screen.getByText("1 601")).toBeInTheDocument();
  expect(screen.getByText("1 кГц")).toBeInTheDocument();
  expect(screen.getByText("0 дБм")).toBeInTheDocument();
  expect(screen.getByText("КСВН S11 (1…1,1) · Передача (S21), дБ (−0,5…0 дБ)")).toBeInTheDocument();
  expect(screen.getByText("КСВН ≤ 1,06, U 0,012 · ADP1A, изготовитель")).toBeInTheDocument();
  expect(screen.getByText(/Цепочка «напрямую»/)).toBeInTheDocument();
  expect(screen.getByText("по серии и норме: до 18 ГГц")).toBeInTheDocument();
  expect(screen.getByText(/Сначала пройдите калибровку/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Измерить" })).toBeDisabled();

  const left = screen.getByLabelText("Оснастка слева (к порту 1)");
  await waitFor(() => expect(within(left).getAllByRole("option").map((option) => option.textContent))
    .toEqual(["нет — напрямую", "СИ.009 · Переход СИ.009", "СИ.008 · Переход СИ.008"]));
  fireEvent.change(left, { target: { value: "si-009" } });
  await waitFor(() => expect(planned).toHaveBeenLastCalledWith("s1", { ...si035, left: { instrument_id: "si-009", reversed: false } }));
  fireEvent.click(screen.getByLabelText("сторона 2 к ВАЦ"));
  await waitFor(() => expect(planned).toHaveBeenLastCalledWith("s1", { ...si035, left: { instrument_id: "si-009", reversed: true } }));
  const top = screen.getByLabelText(/Верхняя частота, ГГц/);
  expect(top).toHaveAttribute("placeholder", "18");
  fireEvent.change(top, { target: { value: "6" } });
  fireEvent.keyDown(top, { key: "Enter" });
  await waitFor(() => expect(planned).toHaveBeenLastCalledWith("s1", { ...si035, left: { instrument_id: "si-009", reversed: true }, fmax_ghz: 6 }));
});

it("measures a ready adapter with the chosen chain", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: allPassed }) }));
  vi.spyOn(vnaBenchApi, "resolve").mockResolvedValue({ candidates: [si035] });
  vi.spyOn(vnaBenchApi, "plan").mockResolvedValue(plan);
  vi.spyOn(productionApi, "instruments").mockResolvedValue({ instruments: [] });
  const measure = vi.spyOn(vnaBenchApi, "measure").mockResolvedValue(state({ session: session({ steps: allPassed, version: 4,
    items: [item({ connections: [{ no: 1, status: "running", max_vswr: null, max_mhz: null, measured_at: null }] })] }) }));
  render(<VnaBench canEdit />);
  await findObject("СИ.035");
  fireEvent.click(await screen.findByRole("button", { name: "Измерить" }));
  await waitFor(() => expect(measure).toHaveBeenCalledWith("s1", 3, si035));
});

it("opens a КБ session on its ETK band from a found item when no session is open", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: null }));
  vi.spyOn(vnaBenchApi, "resolve").mockResolvedValue({ candidates: [kb] });
  const start = vi.spyOn(vnaBenchApi, "startSession").mockResolvedValue(state({ session: session({ steps: [step("calibration", "pending")] }) }));
  vi.spyOn(vnaBenchApi, "plan").mockResolvedValue({ ...plan, screen_name: "ОТК — кабельная сборка", band_mhz: [1, 6000] });
  render(<VnaBench canEdit />);
  await findObject("ПЗ-0001/3");
  expect(await screen.findByText(/Для КБ калибровка идёт на полосе ЭТК задания ПЗ-0001/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Открыть сеанс для КБ" }));
  await waitFor(() => expect(start).toHaveBeenCalledWith({ assembly: { task_id: "t1", operation_id: "op-vswr" } }));
  expect(await screen.findByText("ОТК — кабельная сборка")).toBeInTheDocument();
});

it("closes the adapter session and opens one for the КБ when the plan says the calibration is other", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: allPassed }) }));
  vi.spyOn(vnaBenchApi, "resolve").mockResolvedValue({ candidates: [kb] });
  vi.spyOn(vnaBenchApi, "plan").mockResolvedValue({ ...plan, screen_name: "", traces: [], norm_vswr: null,
    refusal: { code: "other_calibration", message: "Сеанс откалиброван на сетке перехода — для КБ откройте сеанс с заданием" } });
  const close = vi.spyOn(vnaBenchApi, "close").mockResolvedValue(state({ session: null }));
  const start = vi.spyOn(vnaBenchApi, "startSession").mockResolvedValue(state({ session: session({ id: "s2", steps: [] }) }));
  render(<VnaBench canEdit />);
  await findObject("ПЗ-0001/3");
  expect(await screen.findByText(/Этому объекту нужна другая калибровка/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Открыть сеанс для КБ" }));
  await waitFor(() => expect(close).toHaveBeenCalledWith("s1", 3));
  await waitFor(() => expect(start).toHaveBeenCalledWith({ assembly: { task_id: "t1", operation_id: "op-vswr" } }));
});

it("asks an adapter on the border for connection 2 of 3, turned by 120°", async () => {
  const border = item({
    connections_total: 3,
    connections: [{ no: 1, status: "passed", max_vswr: 1.065, max_mhz: 17200, measured_at: "2026-11-19T15:00:00+03:00" },
      { no: 2, status: "pending", max_vswr: null, max_mhz: null, measured_at: null }, { no: 3, status: "pending", max_vswr: null, max_mhz: null, measured_at: null }],
    trace: [{ mhz: 10, vswr: 1.01 }, { mhz: 17200, vswr: 1.065 }, { mhz: 18000, vswr: 1.04 }],
    verdict: { code: "border", worst_vswr: 1.065, worst_mhz: 17200, norm_vswr: 1.06, uncertainty: 0.012, reason: null, final: false },
  });
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: allPassed, items: [border] }) }));
  vi.spyOn(vnaBenchApi, "resolve").mockResolvedValue({ candidates: [si035] });
  vi.spyOn(vnaBenchApi, "plan").mockResolvedValue(plan);
  vi.spyOn(productionApi, "instruments").mockResolvedValue({ instruments: [] });
  const next = vi.spyOn(vnaBenchApi, "nextConnection").mockResolvedValue(state({ session: session({ steps: allPassed, items: [border] }) }));
  render(<VnaBench canEdit />);
  await findObject("СИ.035");
  expect(await screen.findByText("На границе допуска")).toBeInTheDocument();
  expect(screen.getByText("наибольший КСВН 1,065 на 17 200 МГц · норма ≤ 1,06 · U 0,012")).toBeInTheDocument();
  expect(screen.getByText("Подключение 2 из 3 — отключите объект, поверните разъём на 120° и подключите снова. Кабели не двигайте.")).toBeInTheDocument();
  expect(screen.getByRole("img", { name: /норма не более 1,06/ })).toBeInTheDocument();
  expect(screen.getByText("Норма ± U 0,012")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Записать в карточку" })).not.toBeInTheDocument();
  expect(within(screen.getByRole("table", { name: "Подключения" })).getByText("в пределах нормы + U")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Измерить заново" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Измерить: 2 из 3" }));
  await waitFor(() => expect(next).toHaveBeenCalledWith("s1", "i1", 3));
});

it("asks a КБ on the border for three repeats after the first connection", () => {
  const kbItem = item({ target: kb, connections_total: 2,
    connections: [{ no: 1, status: "passed", max_vswr: 1.33, max_mhz: 5400, measured_at: null }, { no: 2, status: "pending", max_vswr: null, max_mhz: null, measured_at: null }],
    verdict: { code: "border", worst_vswr: 1.33, worst_mhz: 5400, norm_vswr: 1.32, uncertainty: 0.02, reason: null, final: false } });
  expect(connectionPrompt(kbItem)).toBe("Повтор 1 из 3 — отключите изделие, поверните разъём на 120° и подключите снова. Кабели не двигайте.");
  expect(connectionPrompt({ ...kbItem, verdict: { ...kbItem.verdict!, final: true } })).toBeNull();
  expect(connectionPrompt(item())).toBeNull();
});

it("records a final adapter verdict and prints its label", async () => {
  const done = item({
    trace: [{ mhz: 10, vswr: 1.01 }, { mhz: 18000, vswr: 1.04 }],
    connections: [{ no: 1, status: "passed", max_vswr: 1.04, max_mhz: 18000, measured_at: null }],
    verdict: { code: "pass", worst_vswr: 1.04, worst_mhz: 18000, norm_vswr: 1.06, uncertainty: 0.012, reason: null, final: true },
  });
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: allPassed, items: [done] }) }));
  vi.spyOn(vnaBenchApi, "resolve").mockResolvedValue({ candidates: [si035] });
  vi.spyOn(vnaBenchApi, "plan").mockResolvedValue(plan);
  vi.spyOn(productionApi, "instruments").mockResolvedValue({ instruments: [] });
  const record = vi.spyOn(vnaBenchApi, "record").mockResolvedValue(state({ session: session({ steps: allPassed,
    items: [{ ...done, recorded_at: "2026-11-19T15:10:00+03:00", recorded_to: "карточка СИ.035", label_instrument_id: "si-035" }] }) }));
  const print = vi.spyOn(productionApi, "printInstrumentLabel").mockResolvedValue({ status: "queued", job_ids: ["j1"] });
  render(<VnaBench canEdit />);
  await findObject("СИ.035");
  expect(await screen.findByText("Соответствует")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Записать в карточку" }));
  await waitFor(() => expect(record).toHaveBeenCalledWith("s1", "i1", 3));
  expect(await screen.findByText("Записано: карточка СИ.035, 19.11 15:10")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Печать этикетки" }));
  await waitFor(() => expect(print).toHaveBeenCalledWith("si-035", 1));
  expect(await screen.findByText("Этикетка отправлена на принтер.")).toBeInTheDocument();
});

it("lets a viewer and a colleague read the bench but not act", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: [step("calibration", "pending")] }) }));
  const { unmount } = render(<VnaBench canEdit={false} />);
  expect(await screen.findByText("Калибровка АКМ")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Калибровать АКМ" })).not.toBeInTheDocument();
  unmount();
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ owned: false, operator: "Иванов", steps: [step("calibration", "pending")] }) }));
  render(<VnaBench canEdit />);
  expect(await screen.findByText(/Сеанс открыл Иванов — управлять им может только он/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Калибровать АКМ" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Закрыть сеанс" })).not.toBeInTheDocument();
});

it("turns a server refusal code into a Russian reason, never the code", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: [step("calibration", "pending")] }) }));
  vi.spyOn(vnaBenchApi, "runStep").mockRejectedValue(new ApiError("screen_changed", 409, { code: "screen_changed", message: "screen hash mismatch" }));
  render(<VnaBench canEdit />);
  fireEvent.click(await screen.findByRole("button", { name: "Калибровать АКМ" }));
  expect(await screen.findByText(/Экран S2VNA изменили вручную/)).toBeInTheDocument();
  expect(screen.queryByText(/screen_changed/)).not.toBeInTheDocument();
  expect(refusalText({ code: "new_code", message: "Новая причина" })).toBe("Новая причина");
  expect(refusalText({ code: "kit_choice", message: "В наборе две перемычки: MA6666A, MA6666B" })).toBe("В наборе две перемычки: MA6666A, MA6666B");
  expect(refusalText({ code: "control_failed", message: "" })).toMatch(/замеры СИ и КБ остановлены/);
});

it("polls the state while the agent runs a command and offers to abort it", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const read = vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({
    command: { label: "калибровка АКМ", state: "running", since: "2026-11-19T14:30:00+03:00" },
    steps: [step("calibration", "running")],
  }) }));
  const abort = vi.spyOn(vnaBenchApi, "abort").mockResolvedValue(state({ session: session() }));
  render(<VnaBench canEdit />);
  expect(await screen.findByText("Выполняется: калибровка АКМ, с 14:30")).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
  await waitFor(() => expect(read.mock.calls.length).toBeGreaterThanOrEqual(2));
  const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
  fireEvent.click(screen.getByRole("button", { name: "Прервать" }));
  expect(confirm).toHaveBeenCalledTimes(1);
  expect(abort).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Прервать" }));
  await waitFor(() => expect(abort).toHaveBeenCalledWith("s1", 3));
  confirm.mockRestore();
  const calls = read.mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(read.mock.calls.length).toBe(calls);
});

it("lists the three checks with due dates and guides a monthly check by the server's phases", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: allPassed, checks: [{
    id: "c1", version: 2, kind: "monthly", status: "running", result_text: null, refusal: null, started_at: "2026-11-19T15:00:00+03:00", finished_at: null,
    phases: [
      { key: "load_p1", label: "Нагрузка (LOAD), порт 1", status: "passed", connection_no: 1, connections_total: 3, result_text: "0,42 допуска" },
      { key: "load_p1", label: "Нагрузка (LOAD), порт 1", status: "pending", connection_no: 2, connections_total: 3, result_text: null },
    ],
  }] }) }));
  const next = vi.spyOn(vnaBenchApi, "checkNext").mockResolvedValue(state());
  render(<VnaBench canEdit />);
  fireEvent.click(await screen.findByRole("tab", { name: "Проверки" }));
  const list = screen.getByRole("table", { name: "Проверки ВАЦ" });
  expect(within(list).getByText("Шевеление кабелей")).toBeInTheDocument();
  expect(within(list).getByText("нужна после калибровки")).toBeInTheDocument();
  expect(within(list).getByText("Еженедельная — контрольная мера СИ.009")).toBeInTheDocument();
  expect(within(list).getByText("Ежемесячная — меры 6650F27")).toBeInTheDocument();
  expect(within(list).getByText("до 23.11")).toBeInTheDocument();
  expect(screen.getByText("Наверните меру LOAD из набора 6650F27 на кабель порта 1 и затяните ключом. Подключение 2 из 3 — отключите, поверните меру на 120° и подключите снова."))
    .toBeInTheDocument();
  within(list).getAllByRole("button", { name: "Начать" }).forEach((button) => expect(button).toBeDisabled());
  fireEvent.click(screen.getByRole("button", { name: "Измерить" }));
  await waitFor(() => expect(next).toHaveBeenCalledWith("s1", "c1", 2));
});

it("starts a check by kind", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: allPassed }) }));
  const start = vi.spyOn(vnaBenchApi, "startCheck").mockResolvedValue(state());
  render(<VnaBench canEdit />);
  fireEvent.click(await screen.findByRole("tab", { name: "Проверки" }));
  fireEvent.click(within(screen.getByRole("table", { name: "Проверки ВАЦ" })).getAllByRole("button", { name: "Начать" })[0]);
  await waitFor(() => expect(start).toHaveBeenCalledWith("s1", 3, { kind: "flex" }));
});

it("words the server's flex, weekly and monthly phases and the due states", () => {
  const phase = (key: string, label = "", no: number | null = null) =>
    ({ key, label, status: "pending" as const, connection_no: no, connections_total: no ? 3 : null, result_text: null });
  expect(phaseHint("flex", phase("load_p2"))).toMatch(/^Перенесите LOAD на кабель порта 2/);
  expect(phaseHint("flex", phase("thru_flex"))).toBe("Изогните кабели в рабочих пределах — S21 должен меняться не больше чем на ±0,05 дБ.");
  expect(phaseHint("weekly", phase("si009"))).toMatch(/^Подключите контрольный переход СИ.009/);
  expect(phaseHint("monthly", phase("thru", "MA6666B (THRU), перемычка", 1)))
    .toBe("Соедините концы кабелей перемычкой из набора (MA6666B (THRU), перемычка). Подключение 1 из 3.");
  expect(dueText({ kind: "flex", due_at: null, last_at: "2026-11-19T14:50:00+03:00", status: "ok" }).text).toBe("пройдена после калибровки");
  expect(dueText({ kind: "weekly", due_at: null, last_at: null, status: "due" }).text).toBe("ещё не выполнялась");
});

it("is switched on only when the server answers the bench state", async () => {
  const read = vi.spyOn(vnaBenchApi, "state").mockRejectedValueOnce(new ApiError("Not Found", 404));
  expect(await vnaBenchAvailable()).toBe(false);
  read.mockResolvedValueOnce(state());
  expect(await vnaBenchAvailable()).toBe(true);
});

it("measures the same object again after a final verdict, with its chain", async () => {
  const done = item({ connections: [{ no: 1, status: "failed", max_vswr: 1.2, max_mhz: 9000, measured_at: null }],
    verdict: { code: "fail", worst_vswr: 1.2, worst_mhz: 9000, norm_vswr: 1.06, uncertainty: 0.012, reason: null, final: true } });
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: allPassed, items: [done] }) }));
  vi.spyOn(vnaBenchApi, "resolve").mockResolvedValue({ candidates: [si035] });
  vi.spyOn(vnaBenchApi, "plan").mockResolvedValue(plan);
  vi.spyOn(productionApi, "instruments").mockResolvedValue({ instruments: [] });
  const measure = vi.spyOn(vnaBenchApi, "measure").mockResolvedValue(state({ session: session({ steps: allPassed, items: [done] }) }));
  render(<VnaBench canEdit />);
  await findObject("СИ.035");
  expect(await screen.findByText("Не соответствует")).toBeInTheDocument();
  expect(screen.getByLabelText(/Верхняя частота, ГГц/)).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Измерить заново" }));
  await waitFor(() => expect(measure).toHaveBeenCalledWith("s1", 3, si035));
});

it("repeats a step refused for a reason other than the calibration; a calibration refusal asks for a new calibration", async () => {
  const refused = (code: string) => state({ session: session({ steps: [step("calibration", "passed"), step("confidence", "passed"),
    { ...step("load_port1", "refused"), refusal: { code, message: "" } }] }) });
  const read = vi.spyOn(vnaBenchApi, "state").mockResolvedValue(refused("device_busy"));
  const run = vi.spyOn(vnaBenchApi, "runStep").mockResolvedValue(state());
  const { unmount } = render(<VnaBench canEdit />);
  expect(await screen.findByText("Нагрузка: порт 1: команда не выполнена.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Откалибровать заново" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
  await waitFor(() => expect(run).toHaveBeenCalledWith("s1", 3, "load_port1"));
  unmount();
  read.mockResolvedValue(refused("temperature_drift"));
  render(<VnaBench canEdit />);
  expect(await screen.findByRole("button", { name: "Откалибровать заново" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Повторить" })).not.toBeInTheDocument();
});

it("never shows an empty refusal banner", async () => {
  expect(refusalText({ code: "unknown_code", message: "" })).toBe("Команда ВАЦ не выполнена");
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: [step("calibration", "pending")] }) }));
  vi.spyOn(vnaBenchApi, "runStep").mockRejectedValue(new ApiError("", 409, { code: "unknown_code", message: "" }));
  render(<VnaBench canEdit />);
  fireEvent.click(await screen.findByRole("button", { name: "Калибровать АКМ" }));
  expect(await screen.findByText("Команда ВАЦ не выполнена")).toBeInTheDocument();
});

it("keeps only the newest plan when an older answer comes late", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: allPassed }) }));
  const other: VnaBenchTarget = { ...si035, instrument_id: "si-036", number: "СИ.036" };
  vi.spyOn(vnaBenchApi, "resolve").mockResolvedValue({ candidates: [si035, other] });
  vi.spyOn(productionApi, "instruments").mockResolvedValue({ instruments: [] });
  let first: (value: VnaBenchPlan) => void = () => undefined;
  vi.spyOn(vnaBenchApi, "plan")
    .mockImplementationOnce(() => new Promise((resolve) => { first = resolve; }))
    .mockResolvedValueOnce({ ...plan, screen_name: "План СИ.036" });
  render(<VnaBench canEdit />);
  await findObject("СИ");
  fireEvent.click(await screen.findByRole("button", { name: /^СИ.035/ }));
  fireEvent.click(screen.getByRole("button", { name: /^СИ.036/ }));
  expect(await screen.findByText("План СИ.036")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^СИ.036/ })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: /^СИ.035/ })).toHaveAttribute("aria-pressed", "false");
  await act(async () => { first({ ...plan, screen_name: "План СИ.035" }); });
  expect(screen.queryByText("План СИ.035")).not.toBeInTheDocument();
  expect(screen.getByText("План СИ.036")).toBeInTheDocument();
});

it("refuses a non-numeric top frequency inline and keeps the previous one", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: allPassed }) }));
  vi.spyOn(vnaBenchApi, "resolve").mockResolvedValue({ candidates: [si035] });
  vi.spyOn(productionApi, "instruments").mockResolvedValue({ instruments: [] });
  const planned = vi.spyOn(vnaBenchApi, "plan").mockResolvedValue(plan);
  render(<VnaBench canEdit />);
  await findObject("СИ.035");
  const top = await screen.findByLabelText(/Верхняя частота, ГГц/);
  fireEvent.change(top, { target: { value: "шесть" } });
  fireEvent.keyDown(top, { key: "Enter" });
  expect(await screen.findByText("Введите число гигагерц больше нуля, например 6 или 12,4")).toBeInTheDocument();
  expect(top).toHaveAttribute("aria-invalid", "true");
  expect(planned).toHaveBeenCalledTimes(1);
});

it("switches the bench tabs with the arrow keys", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state());
  render(<VnaBench canEdit />);
  const measure = await screen.findByRole("tab", { name: "Замер" });
  fireEvent.keyDown(measure, { key: "ArrowRight" });
  expect(screen.getByRole("tab", { name: "Проверки", selected: true })).toHaveFocus();
  fireEvent.keyDown(screen.getByRole("tab", { name: "Проверки" }), { key: "Home" });
  expect(screen.getByRole("tab", { name: "Замер", selected: true })).toHaveFocus();
});

it("asks before closing the session and closes it only after the confirmation", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: allPassed }) }));
  const close = vi.spyOn(vnaBenchApi, "close").mockResolvedValue(state({ session: null }));
  const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
  render(<VnaBench canEdit />);
  fireEvent.click(await screen.findByRole("button", { name: "Закрыть сеанс" }));
  expect(close).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Закрыть сеанс" }));
  await waitFor(() => expect(close).toHaveBeenCalledWith("s1", 3));
  expect(confirm).toHaveBeenCalledTimes(2);
  confirm.mockRestore();
});

