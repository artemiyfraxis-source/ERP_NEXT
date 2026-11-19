import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { VnaBench, connectionPrompt, phaseHint } from "./VnaBench";
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

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

const passed = (key: VnaBenchStep["key"], text: string | null = null): VnaBenchStep =>
  ({ key, status: "passed", result_text: text, refusal: null, finished_at: "2026-11-19T14:10:00+03:00" });
const allPassed: VnaBenchStep[] = [
  passed("calibration"), passed("confidence", "передача 0,031 дБ ≤ 0,1; отражение 0,12 дБ ≤ 0,4"),
  passed("load_port1", "КСВН 1,032 ≤ 1,06"), passed("load_port2", "КСВН 1,028 ≤ 1,06"),
];

const session = (extra: Partial<VnaBenchSession> = {}): VnaBenchSession => ({
  id: "s1", version: 3, status: "open", started_at: "2026-11-19T14:00:00+03:00", operator: "Контролёр",
  steps: [], command: null, items: [], checks: [], ...extra,
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
    { kind: "weekly", due_at: "2026-11-23T00:00:00+03:00", last_at: "2026-11-16T10:00:00+03:00", status: "ok" },
    { kind: "monthly", due_at: "2026-11-18T00:00:00+03:00", last_at: null, status: "overdue" },
  ],
  session: session(),
  ...extra,
});

const si035: VnaBenchTarget = { kind: "instrument", instrument_id: "si-035", number: "СИ.035", name: "Переход ADP1A", serial_number: "N8B141" };
const plan: VnaBenchPlan = {
  screen_name: "ОТК — переход", band_mhz: [10, 18000], points: 1601, ifbw_hz: 1000, power_dbm: 0,
  traces: [{ label: "КСВН S11", scale: "1,00–1,10" }, { label: "Потери S21", scale: "0…−0,5 дБ" }],
  norm_vswr: 1.06, norm_source: "ADP1A, изготовитель", uncertainty: 0.012, connection_hint: "Сторона 1 перехода — к кабелю порта 1, сторона 2 — к кабелю порта 2.",
  refusal: null,
};
const item = (extra: Partial<VnaBenchItem> = {}): VnaBenchItem => ({
  id: "i1", version: 1, target: si035, plan, connections_total: 1, connections: [], trace: [], verdict: null, refusal: null,
  recorded_at: null, recorded_to: null, ...extra,
});

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

it("says why the calibration is invalid and why the device is refused, in Russian", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({
    calibration: { valid: false, calibrated_at: null, valid_until: null, reason: "Температура ВАЦ ушла на 1,3 °C", temperatures: null },
    device: { ...state().device, s2vna_ready: false, warmup_min: 12, refusal: { code: "serial_changed", message: "serial mismatch" } },
    session: null,
  }));
  render(<VnaBench canEdit />);
  expect(await screen.findByText("Температура ВАЦ ушла на 1,3 °C")).toBeInTheDocument();
  expect(screen.getByText("Не готов — прогрев 12 из 40 мин")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("Заводской номер ВАЦ не совпал с карточкой СИ.002");
  expect(screen.getByRole("button", { name: "Начать сеанс" })).toBeDisabled();
});

it("opens a session and walks the steps in order with plain instructions", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: null }));
  const start = vi.spyOn(vnaBenchApi, "startSession").mockResolvedValue(state());
  const run = vi.spyOn(vnaBenchApi, "runStep").mockResolvedValue(state({ session: session({ steps: [passed("calibration")] }) }));
  render(<VnaBench canEdit />);
  fireEvent.click(await screen.findByRole("button", { name: "Начать сеанс" }));
  await waitFor(() => expect(start).toHaveBeenCalled());

  const steps = await screen.findByRole("list", { name: "Шаги сеанса" });
  expect(within(steps).getAllByRole("listitem").map((li) => li.querySelector("strong")?.textContent))
    .toEqual(["Калибровка АКМ", "Доверительный тест", "Нагрузка: порт 1", "Нагрузка: порт 2"]);
  expect(screen.getByText(/Подключите АКМ к концам обоих кабелей/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Калибровать АКМ" }));
  await waitFor(() => expect(run).toHaveBeenCalledWith("s1", 3, "calibration"));
  expect(await screen.findByRole("button", { name: "Запустить доверительный тест" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Калибровать АКМ" })).not.toBeInTheDocument();
});

it("shows a failed step with the agent's refusal and offers a retry", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: [passed("calibration"),
    { key: "confidence", status: "refused", result_text: null, refusal: { code: "correction_lost", message: "C?" }, finished_at: null }] }) }));
  render(<VnaBench canEdit />);
  expect(await screen.findByText(/Коррекция не «Кор» \(К\? или К!\)/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Повторить: запустить доверительный тест" })).toBeInTheDocument();
});

it("finds an object, shows its plan card and measures only after all steps", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: allPassed.slice(0, 2) }) }));
  const resolve = vi.spyOn(vnaBenchApi, "resolve").mockResolvedValue({ candidates: [si035] });
  vi.spyOn(vnaBenchApi, "plan").mockResolvedValue(plan);
  render(<VnaBench canEdit />);
  fireEvent.change(await screen.findByLabelText("СИ или КБ — скан или номер"), { target: { value: "СИ.035" } });
  fireEvent.click(screen.getByRole("button", { name: "Найти" }));
  await waitFor(() => expect(resolve).toHaveBeenCalledWith("СИ.035"));
  expect(await screen.findByText("ОТК — переход")).toBeInTheDocument();
  expect(screen.getByText("10 – 18 000 МГц")).toBeInTheDocument();
  expect(screen.getByText("1 601")).toBeInTheDocument();
  expect(screen.getByText("1 кГц")).toBeInTheDocument();
  expect(screen.getByText("0 дБм")).toBeInTheDocument();
  expect(screen.getByText("КСВН S11 (1,00–1,10) · Потери S21 (0…−0,5 дБ)")).toBeInTheDocument();
  expect(screen.getByText("КСВН ≤ 1,06, U 0,012 · ADP1A, изготовитель")).toBeInTheDocument();
  expect(screen.getByText(/Сторона 1 перехода — к кабелю порта 1/)).toBeInTheDocument();
  expect(screen.getByText(/Сначала пройдите калибровку/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Измерить" })).toBeDisabled();
});

it("measures a ready object", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: allPassed }) }));
  vi.spyOn(vnaBenchApi, "resolve").mockResolvedValue({ candidates: [si035] });
  vi.spyOn(vnaBenchApi, "plan").mockResolvedValue(plan);
  const measure = vi.spyOn(vnaBenchApi, "measure").mockResolvedValue(state({ session: session({ steps: allPassed, version: 4,
    items: [item({ connections: [{ no: 1, status: "running", max_vswr: null, max_mhz: null, measured_at: null }] })] }) }));
  render(<VnaBench canEdit />);
  fireEvent.change(await screen.findByLabelText("СИ или КБ — скан или номер"), { target: { value: "СИ.035" } });
  fireEvent.submit(screen.getByRole("button", { name: "Найти" }).closest("form")!);
  fireEvent.click(await screen.findByRole("button", { name: "Измерить" }));
  await waitFor(() => expect(measure).toHaveBeenCalledWith("s1", 3, si035));
});

it("asks for the next connection on a border result: turn by 120°, then the worst of three decides", async () => {
  const border = item({
    connections_total: 3,
    connections: [{ no: 1, status: "passed", max_vswr: 1.065, max_mhz: 17200, measured_at: "2026-11-19T15:00:00+03:00" }],
    trace: [{ mhz: 10, vswr: 1.01 }, { mhz: 17200, vswr: 1.065 }, { mhz: 18000, vswr: 1.04 }],
    verdict: { code: "border", worst_vswr: 1.065, worst_mhz: 17200, norm_vswr: 1.06, uncertainty: 0.012, reason: null, final: false },
  });
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: allPassed, items: [border] }) }));
  vi.spyOn(vnaBenchApi, "resolve").mockResolvedValue({ candidates: [si035] });
  vi.spyOn(vnaBenchApi, "plan").mockResolvedValue(plan);
  const next = vi.spyOn(vnaBenchApi, "nextConnection").mockResolvedValue(state({ session: session({ steps: allPassed, items: [border] }) }));
  render(<VnaBench canEdit />);
  fireEvent.change(await screen.findByLabelText("СИ или КБ — скан или номер"), { target: { value: "СИ.035" } });
  fireEvent.click(screen.getByRole("button", { name: "Найти" }));
  expect(await screen.findByText("На границе допуска")).toBeInTheDocument();
  expect(screen.getByText("наибольший КСВН 1,065 на 17 200 МГц · норма ≤ 1,06 · U 0,012 · худшее из 3 подключений")).toBeInTheDocument();
  expect(screen.getByText("Подключение 2 из 3 — отключите объект, поверните разъём на 120° и подключите снова. Кабели не двигайте.")).toBeInTheDocument();
  expect(screen.getByRole("img", { name: /норма не более 1,06/ })).toBeInTheDocument();
  expect(screen.getByText("Норма ± U 0,012")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Записать в карточку" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Измерить подключение 2" }));
  await waitFor(() => expect(next).toHaveBeenCalledWith("s1", "i1", 1));
});

it("records a final verdict into the card and then says where it went", async () => {
  const done = item({
    trace: [{ mhz: 10, vswr: 1.01 }, { mhz: 18000, vswr: 1.04 }],
    connections: [{ no: 1, status: "passed", max_vswr: 1.04, max_mhz: 18000, measured_at: null }],
    verdict: { code: "pass", worst_vswr: 1.04, worst_mhz: 18000, norm_vswr: 1.06, uncertainty: 0.012, reason: null, final: true },
  });
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: allPassed, items: [done] }) }));
  vi.spyOn(vnaBenchApi, "resolve").mockResolvedValue({ candidates: [si035] });
  vi.spyOn(vnaBenchApi, "plan").mockResolvedValue(plan);
  const record = vi.spyOn(vnaBenchApi, "record").mockResolvedValue(state({ session: session({ steps: allPassed,
    items: [{ ...done, recorded_at: "2026-11-19T15:10:00+03:00", recorded_to: "карточка СИ.035" }] }) }));
  render(<VnaBench canEdit />);
  fireEvent.change(await screen.findByLabelText("СИ или КБ — скан или номер"), { target: { value: "СИ.035" } });
  fireEvent.click(screen.getByRole("button", { name: "Найти" }));
  expect(await screen.findByText("Соответствует")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Записать в карточку" }));
  await waitFor(() => expect(record).toHaveBeenCalledWith("s1", "i1", 1));
  expect(await screen.findByText("Записано: карточка СИ.035, 19.11 15:10")).toBeInTheDocument();
});

it("lets a viewer read the bench but not act", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: [] }) }));
  render(<VnaBench canEdit={false} />);
  expect(await screen.findByText("Калибровка АКМ")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Калибровать АКМ" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Закрыть сеанс" })).not.toBeInTheDocument();
});

it("turns a server refusal code into a Russian reason, never the code", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session() }));
  vi.spyOn(vnaBenchApi, "runStep").mockRejectedValue(new ApiError("screen_changed", 409, { code: "screen_changed", message: "screen hash mismatch" }));
  render(<VnaBench canEdit />);
  fireEvent.click(await screen.findByRole("button", { name: "Калибровать АКМ" }));
  expect(await screen.findByText(/Экран S2VNA изменили вручную/)).toBeInTheDocument();
  expect(screen.queryByText(/screen_changed/)).not.toBeInTheDocument();
  expect(refusalText({ code: "new_code", message: "Новая причина" })).toBe("Новая причина");
});

it("polls the state while the agent runs a command and offers to abort it", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const read = vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({
    command: { label: "калибровка АКМ", state: "running", since: "2026-11-19T14:30:00+03:00" },
    steps: [{ key: "calibration", status: "running", result_text: null, refusal: null, finished_at: null }],
  }) }));
  const abort = vi.spyOn(vnaBenchApi, "abort").mockResolvedValue(state({ session: session() }));
  render(<VnaBench canEdit />);
  expect(await screen.findByText("Выполняется: калибровка АКМ, с 14:30")).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
  await waitFor(() => expect(read.mock.calls.length).toBeGreaterThanOrEqual(2));
  fireEvent.click(screen.getByRole("button", { name: "Прервать" }));
  await waitFor(() => expect(abort).toHaveBeenCalledWith("s1", 3));
  const calls = read.mock.calls.length;
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(read.mock.calls.length).toBe(calls);
});

it("lists the three checks with due dates and guides a monthly check phase by phase", async () => {
  vi.spyOn(vnaBenchApi, "state").mockResolvedValue(state({ session: session({ steps: allPassed, checks: [{
    id: "c1", version: 2, kind: "monthly", status: "running", result_text: null, refusal: null, started_at: "2026-11-19T15:00:00+03:00", finished_at: null,
    phases: [
      { key: "load_p1", label: "LOAD, порт 1", status: "passed", connection_no: 1, connections_total: 3, result_text: "0,42 допуска" },
      { key: "load_p1", label: "LOAD, порт 1", status: "pending", connection_no: 2, connections_total: 3, result_text: null },
    ],
  }] }) }));
  const next = vi.spyOn(vnaBenchApi, "checkNext").mockResolvedValue(state());
  render(<VnaBench canEdit />);
  fireEvent.click(await screen.findByRole("tab", { name: "Проверки" }));
  const list = screen.getByRole("table", { name: "Проверки ВАЦ" });
  expect(within(list).getByText("Шевеление кабелей")).toBeInTheDocument();
  expect(within(list).getByText("Еженедельная — контрольная мера СИ.009")).toBeInTheDocument();
  expect(within(list).getByText("Ежемесячная — меры 6650F27")).toBeInTheDocument();
  expect(within(list).getByText("до 23.11")).toBeInTheDocument();
  expect(screen.getByText("Наверните меру LOAD 6650F27 на кабель порта 1 и затяните ключом. Подключение 2 из 3 — отключите, поверните меру на 120° и подключите снова."))
    .toBeInTheDocument();
  within(list).getAllByRole("button", { name: "Начать" }).forEach((button) => expect(button).toBeDisabled());
  fireEvent.click(screen.getByRole("button", { name: "Измерить" }));
  await waitFor(() => expect(next).toHaveBeenCalledWith("s1", "c1", 2));
});

it("words the flex and weekly checks and the connection prompts", () => {
  expect(phaseHint("flex", { key: "thru_flex", label: "", status: "pending", connection_no: null, connections_total: null, result_text: null }))
    .toBe("Пошевелите кабели — S21 должен меняться не больше чем на ±0,05 дБ.");
  expect(phaseHint("weekly", { key: "si009", label: "", status: "pending", connection_no: null, connections_total: null, result_text: null }))
    .toMatch(/^Подключите контрольный переход СИ.009/);
  expect(phaseHint("monthly", { key: "thru_b", label: "", status: "pending", connection_no: 1, connections_total: 3, result_text: null }))
    .toBe("Соедините концы кабелей перемычкой MA6666B. Подключение 1 из 3.");
  expect(connectionPrompt(item())).toBe("Подключение 1 из 1.");
  expect(connectionPrompt(item({ verdict: { code: "fail", worst_vswr: 1.2, worst_mhz: 100, norm_vswr: 1.06, uncertainty: 0.01, reason: null, final: true } }))).toBeNull();
});

it("is switched on only when the server answers the bench state", async () => {
  const read = vi.spyOn(vnaBenchApi, "state").mockRejectedValueOnce(new ApiError("Not Found", 404));
  expect(await vnaBenchAvailable()).toBe(false);
  read.mockResolvedValueOnce(state());
  expect(await vnaBenchAvailable()).toBe(true);
});
