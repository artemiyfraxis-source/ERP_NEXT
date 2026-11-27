import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { productionApi, rfInterfacesApi, type ProductionInstrument, type InstrumentRfDetails } from "./api";
import { ArinstBench } from "./VnaBench";
import { arinstBenchApi, type ArinstBenchState } from "./vnaBenchApi";

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });
const profile = { start_hz: 1e6, stop_hz: 8.8e9, points: 101, if_bandwidth_hz: 1000 };
function state(): ArinstBenchState {
  return {
    source: "arinst", device: { instrument_id: "si1", inventory_number: "СИ.001", serial: "arinst:1-2-3", printed_serial: "001",
      hardware_uid: "1-2-3", firmware: "1", machine_id: "pc1", connection_ready: true, control_ready: true, reason: null,
      supported_ops: ["connect", "close", "apply_profile", "measure_standard", "capture"],
      license: { state: "not_required", message: "Связь: протокол ArinstVL" },
      options: { scan_range_hz: [1e6, 9e9], bandwidths_hz: [20, 50, 100, 250, 500, 1000], max_slice_points: 255, raw: "" } },
    profile, calibration: { valid: false, status: "required" }, acceptance: { eligible: false, code: "commissioning_required", message: "Ожидается стендовое сравнение" },
    session: { id: "s1", version: 7, step: 0, state: "ready", status: "open", owned: true, operator: "Контролёр", error: null, steps: [], command: null, artifacts: [] },
    latest_trace: null,
  };
}
async function show(value = state()) {
  vi.spyOn(arinstBenchApi, "state").mockResolvedValue(value);
  const view = render(<ArinstBench instrumentId="si1" pc="ПК ОТК" agentOnline lastSeen={null} />);
  await screen.findByText(/Для управления через ERP программа ArinstVL/);
  return view;
}

it("routes the main action to calibration settings, then measurement when calibrated", async () => {
  vi.spyOn(productionApi, "instruments").mockResolvedValue({ instruments: [] });
  const first = await show();
  fireEvent.click(screen.getByRole("button", { name: "Калибровать" }));
  expect(screen.getByText(/^Калибровка по мерам ·/).closest("details")).toHaveAttribute("open");
  expect(screen.getByText(/^Калибровка по мерам ·/)).toHaveFocus();
  first.unmount(); vi.restoreAllMocks();
  const calibrated = state(); calibrated.calibration = { valid: true, status: "valid" };
  const capture = vi.spyOn(arinstBenchApi, "capture").mockResolvedValue(calibrated);
  await show(calibrated);
  fireEvent.click(screen.getByRole("button", { name: "Замерить" }));
  await waitFor(() => expect(capture).toHaveBeenCalledWith("s1", 7, "forward"));
});

it("selects Arinst by UUID and shows the native link and bench-comparison gate", async () => {
  await show();
  expect(arinstBenchApi.state).toHaveBeenCalledWith("si1", expect.any(AbortSignal));
  expect(screen.getByText("Связь: протокол ArinstVL")).toBeInTheDocument();
  expect(screen.queryByText(/Пробный режим|Лицензия активна|лицензии API/)).toBeNull();
  expect(screen.getByText(/ПК последнего обнаружения: ПК ОТК.*ArinstVL закрыт/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Сохранить в КБ/СИ" })).toBeDisabled();
  expect(screen.getByText("Сохранение в КБ/СИ — после стендового сравнения Arinst")).toBeInTheDocument();
  expect(screen.queryByText("Калибровка АКМ")).toBeNull();
  expect(screen.queryByRole("button", { name: /сброс|питание|активировать лицензию/i })).toBeNull();
});

it("uses server control readiness while leaving release available", async () => {
  const value = state(); value.device.control_ready = false; value.device.reason = "Прибор не подключён";
  await show(value);
  expect(screen.getByRole("button", { name: "Снять S11 / S21" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Прервать сеанс" })).toBeEnabled();
});

it("opens a session to refresh an expired device observation", async () => {
  const value = state(); value.session = null;
  value.device.control_ready = false; value.device.supported_ops = [];
  value.device.reason = "Прибор или агент недоступен; проверьте подключение";
  const start = vi.spyOn(arinstBenchApi, "start").mockResolvedValue(value);
  await show(value);
  expect(screen.getByText("Подключите прибор через ERP — обновите данные о нём", { selector: "strong" })).toBeVisible();
  const button = screen.getByRole("button", { name: "Открыть сеанс через ERP" });
  expect(button).toBeEnabled();
  expect(screen.getByRole("button", { name: "Снять S11 / S21" })).toBeDisabled();
  fireEvent.click(button);
  await waitFor(() => expect(start).toHaveBeenCalledWith("si1"));
});

it("offers reconnect and close after a ready session's device observation expires", async () => {
  vi.useFakeTimers();
  const value = state(); value.calibration = { valid: true, status: "valid" };
  const expired = structuredClone(value);
  expired.device.control_ready = false; expired.device.supported_ops = [];
  vi.spyOn(arinstBenchApi, "state").mockResolvedValueOnce(value).mockResolvedValue(expired);
  const connect = vi.spyOn(arinstBenchApi, "connect").mockResolvedValue(expired);
  const close = vi.spyOn(arinstBenchApi, "close").mockResolvedValue(expired);
  render(<ArinstBench instrumentId="si1" pc="ПК" agentOnline lastSeen={null} />);
  await act(async () => {});
  expect(screen.getByRole("button", { name: "Замерить" })).toBeEnabled();
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(screen.getByRole("button", { name: "Снять S11 / S21" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Переподключить через ERP" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Закрыть сеанс" })).toBeEnabled();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Переподключить через ERP" })); });
  expect(connect).toHaveBeenCalledWith("s1", 7);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Закрыть сеанс" })); });
  expect(close).toHaveBeenCalledWith("s1", 7);
});

it("keeps connection actions disabled when the owning agent is unavailable", async () => {
  const value = state(); value.device.connection_ready = false; value.device.control_ready = false;
  value.device.supported_ops = [];
  const view = await show(value);
  expect(screen.getByRole("button", { name: "Переподключить через ERP" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Закрыть сеанс" })).toBeDisabled();
  view.unmount(); vi.restoreAllMocks();
  value.session = null;
  await show(value);
  expect(screen.getByRole("button", { name: "Открыть сеанс через ERP" })).toBeDisabled();
});

it("keeps native acquisition available without a trial countdown", async () => {
  vi.useFakeTimers();
  const value = state();
  vi.spyOn(arinstBenchApi, "state").mockResolvedValueOnce(value).mockImplementation(() => new Promise(() => {}));
  render(<ArinstBench instrumentId="si1" pc="ПК" agentOnline lastSeen={null} />);
  await act(async () => {});
  await act(async () => { await vi.advanceTimersByTimeAsync(601000); });
  expect(screen.queryByText(/пробного режима|лицензия/i)).toBeNull();
  expect(screen.getByRole("button", { name: "Снять S11 / S21" })).toBeEnabled();
});

it("opens a session without Planar arguments, then closes with the current version", async () => {
  const value = state(); value.session = null;
  vi.spyOn(arinstBenchApi, "start").mockResolvedValue(state());
  const close = vi.spyOn(arinstBenchApi, "close").mockResolvedValue(value);
  await show(value);
  vi.mocked(arinstBenchApi.state).mockResolvedValue(state());
  fireEvent.click(screen.getByRole("button", { name: "Открыть сеанс через ERP" }));
  await waitFor(() => expect(arinstBenchApi.start).toHaveBeenCalledWith("si1"));
  fireEvent.click(await screen.findByRole("button", { name: "Закрыть сеанс" }));
  await waitFor(() => expect(close).toHaveBeenCalledWith("s1", 7));
});

it("never controls a session owned by another operator", async () => {
  const value = state(); value.session!.owned = false;
  await show(value);
  for (const name of ["Переподключить через ERP", "Закрыть сеанс", "Прервать сеанс", "Снять S11 / S21"])
    expect(screen.getByRole("button", { name })).toBeDisabled();
  expect(screen.getAllByText("Сеанс ведёт Контролёр").length).toBeGreaterThan(0);
});

it("does not offer acquisition if it is absent from per-device supported operations", async () => {
  const value = state(); value.device.supported_ops = ["connect", "close"];
  await show(value);
  expect(screen.getByRole("button", { name: "Снять S11 / S21" })).toBeDisabled();
  expect(screen.getAllByText("Операция не подтверждена агентом для этого прибора").length).toBeGreaterThan(0);
});

it.each([['OPEN', 'открытый конец'], ['SHORT', 'короткое замыкание'], ['LOAD', 'согласованная нагрузка'], ['THRU', 'перемычка для S21']] as const)("guides %s and sends an explicit physical confirmation", async (standard, text) => {
  const value = state(); value.session!.state = "awaiting_standard";
  value.session!.steps = [{ op: "measure_standard", human: true, label: "Подключите меру", args: { standard } }];
  const confirm = vi.spyOn(arinstBenchApi, "confirm").mockResolvedValue(value);
  await show(value);
  expect(screen.getByRole("heading", { name: new RegExp(text) })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Подключено — измерить" }));
  await waitFor(() => expect(confirm).toHaveBeenCalledWith("s1", 7));
});

it("uses the server's DUT/reversal instruction and sends the requested orientation", async () => {
  const value = state();
  const awaiting = state(); awaiting.session!.state = "awaiting_dut";
  awaiting.session!.steps = [{ op: "capture", human: true, label: "Поверните изделие: сторона Б к порту 1", args: { orientation: "reverse" } }];
  const capture = vi.spyOn(arinstBenchApi, "capture").mockResolvedValue(awaiting);
  await show(value); vi.mocked(arinstBenchApi.state).mockResolvedValue(awaiting);
  fireEvent.change(screen.getByLabelText("Ориентация"), { target: { value: "reverse" } });
  fireEvent.click(screen.getByRole("button", { name: "Снять S11 / S21" }));
  await waitFor(() => expect(capture).toHaveBeenCalledWith("s1", 7, "reverse"));
  expect(await screen.findByText("Поверните изделие: сторона Б к порту 1")).toBeInTheDocument();
});

it("retains blocked calibration as invalid and still labels a capture diagnostic", async () => {
  const value = state(); value.calibration = { status: "blocked", valid: false, reason: "Нет подтверждённой сетки" };
  await show(value);
  expect(screen.getByText("Калибровка не подтверждена: Нет подтверждённой сетки")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Снять S11 / S21" })).toBeEnabled();
});

it("checks a profile, presents old to new, and writes only after confirmation", async () => {
  const write = vi.spyOn(arinstBenchApi, "profile").mockResolvedValue(state());
  await show(); screen.getByText(/^Профиль ·/).closest("details")!.open = true;
  fireEvent.change(screen.getByLabelText("Начало, МГц"), { target: { value: "10,5" } });
  fireEvent.click(screen.getByRole("button", { name: "Проверить изменения" }));
  expect(write).not.toHaveBeenCalled();
  expect(screen.getByText(/Калибровка станет недействительной/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Применить профиль сеанса" }));
  await waitFor(() => expect(write).toHaveBeenCalledWith("s1", 7, { ...profile, start_hz: 10.5e6 }));
});

it.each([['Конец, МГц', '0,5'], ['Конец, МГц', '9001'], ['Начало, МГц', '1,0000001'], ['Конец, МГц', '1,000001'], ['Число точек', '1001'], ['Число точек', '2.5']])("refuses invalid profile %s=%s", async (label, number) => {
  await show(); screen.getByText(/^Профиль ·/).closest("details")!.open = true;
  fireEvent.change(screen.getByLabelText(label), { target: { value: number } });
  fireEvent.click(screen.getByRole("button", { name: "Проверить изменения" }));
  expect(screen.getByRole("alert")).toHaveTextContent("Укажите возрастающий диапазон");
  expect(screen.queryByRole("button", { name: "Применить профиль сеанса" })).toBeNull();
});

it("starts with an explicit host profile and uses the advertised range and bandwidths", async () => {
  const value = state(); value.profile = null;
  value.device.options!.bandwidths_hz = [33, 333];
  const apply = vi.spyOn(arinstBenchApi, "profile").mockResolvedValue(state());
  await show(value);
  expect(screen.getByText("Задайте профиль сеанса")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Снять S11 / S21" })).toBeDisabled();
  screen.getByText(/^Профиль ·/).closest("details")!.open = true;
  expect(screen.queryByRole("option", { name: "20" })).toBeNull();
  fireEvent.change(screen.getByLabelText("Начало, МГц"), { target: { value: "1,000001" } });
  fireEvent.change(screen.getByLabelText("Конец, МГц"), { target: { value: "9000" } });
  fireEvent.change(screen.getByLabelText("Число точек"), { target: { value: "1000" } });
  fireEvent.change(screen.getByLabelText("Полоса ПЧ, Гц"), { target: { value: "333" } });
  fireEvent.click(screen.getByRole("button", { name: "Проверить изменения" }));
  expect(apply).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Применить профиль сеанса" }));
  await waitFor(() => expect(apply).toHaveBeenCalledWith("s1", 7, { start_hz: 1000001, stop_hz: 9e9, points: 1000, if_bandwidth_hz: 333 }));
});

it("does not invent bandwidth choices before the device options arrive", async () => {
  const value = state(); value.device.options = null;
  await show(value); screen.getByText(/^Профиль ·/).closest("details")!.open = true;
  expect(screen.getByRole("button", { name: "Проверить изменения" })).toBeDisabled();
  expect(screen.getByText("Переподключите прибор, чтобы получить доступные диапазон и полосу ПЧ")).toBeInTheDocument();
});

it("checks registry role instead of promoting an SMA-MF06 load kit into SOL", async () => {
  vi.spyOn(productionApi, "instruments").mockResolvedValue({ instruments: [
    { id: "loads", inventory_number: "СИ.6", name: "SMA-MF06 нагрузки", status: "Действует" },
    { id: "sol", inventory_number: "СИ.7", name: "Набор SOL", status: "Действует" },
  ] as ProductionInstrument[] });
  vi.spyOn(rfInterfacesApi, "get").mockImplementation(async (id) => ({ rf: { role: id === "sol" ? "solt_kit" : "load_kit" } as InstrumentRfDetails }));
  const calibrate = vi.spyOn(arinstBenchApi, "calibration").mockResolvedValue(state());
  await show(); fireEvent.click(screen.getByText(/^Калибровка по мерам ·/));
  await screen.findByRole("option", { name: "СИ.6 · SMA-MF06 нагрузки" });
  fireEvent.change(screen.getByLabelText("Калибровочный набор"), { target: { value: "loads" } });
  await screen.findByText(/Это не набор SOL/);
  fireEvent.change(screen.getByLabelText("Плоскость измерения"), { target: { value: "Концы кабелей" } });
  expect(screen.getByRole("button", { name: "Начать калибровку по мерам" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Калибровочный набор"), { target: { value: "sol" } });
  await waitFor(() => expect(screen.getByRole("button", { name: "Начать калибровку по мерам" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Начать калибровку по мерам" }));
  await waitFor(() => expect(calibrate).toHaveBeenCalledWith("s1", 7, "sol", "Концы кабелей", true));
});

it("shows partial S11/S21 by sample index, keeps zero magnitudes as gaps and links immutable receipts", async () => {
  const value = state();
  value.latest_trace = { command_id: "c1", artifact_id: "a1", sha256: "abc", profile, acceptance_eligible: false,
    acquisition: { schema: "arinst.partial.v1", points: 3, orientation: "reverse", measured_channels: ["S11", "S21"],
      data: { S11: [[.1, 0], [0, 0], [.2, 0]], S21: [[.8, 0], [.9, 0], [.7, 0]] }, grid_confirmed: false, frequencies_hz: null, correction_origin: "unknown" } };
  value.session!.artifacts = [{ id: "a1", command_id: "c1", file_name: "arinst-receipt.json", sha256: "abc" }];
  await show(value);
  const svg = screen.getByRole("img", { name: /по номеру отсчёта/ });
  expect(svg.querySelectorAll("path")).toHaveLength(2);
  expect(svg.innerHTML).not.toMatch(/NaN|Infinity/);
  expect(svg.querySelector("path")!.getAttribute("d")!.match(/M/g)).toHaveLength(2);
  expect(screen.getByText(/Частотная сетка не подтверждена/)).toBeInTheDocument();
  expect(screen.getByText(/Ориентация: обратная/)).toBeInTheDocument();
  fireEvent.click(screen.getByText(/Квитанции прибора/));
  expect(screen.getByRole("link", { name: "Скачать квитанцию 1" })).toHaveAttribute("href", "/api/production/vna-sessions/s1/artifacts/a1");
});

it("does not replay failed or unknown commands and refreshes state before permitting another action", async () => {
  const capture = vi.spyOn(arinstBenchApi, "capture").mockRejectedValue(new Error("Исход команды неизвестен"));
  await show();
  fireEvent.click(screen.getByRole("button", { name: "Снять S11 / S21" }));
  expect(await screen.findByText("Отказ: Исход команды неизвестен")).toBeInTheDocument();
  await waitFor(() => expect(arinstBenchApi.state).toHaveBeenCalledTimes(2));
  expect(capture).toHaveBeenCalledTimes(1);
});

it("never displays a response for another instrument", async () => {
  const value = state(); value.device.instrument_id = "si2";
  vi.spyOn(arinstBenchApi, "state").mockResolvedValue(value);
  render(<ArinstBench instrumentId="si1" pc="ПК" agentOnline lastSeen={null} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Ответ относится к другому прибору");
  expect(screen.queryByRole("button", { name: "Снять S11 / S21" })).toBeNull();
});

it.each(["close", "abort"] as const)("finishes %s feedback when polling no longer returns an active session", async (operation) => {
  vi.useFakeTimers();
  const releasing = state(); releasing.session!.state = "releasing";
  releasing.session!.command = { id: "c1", op: "close", status: "pending" };
  const closed = state(); closed.session = null;
  vi.spyOn(arinstBenchApi, "state").mockResolvedValueOnce(state()).mockResolvedValueOnce(releasing).mockResolvedValue(closed);
  vi.spyOn(arinstBenchApi, operation).mockResolvedValue(releasing);
  render(<ArinstBench instrumentId="si1" pc="ПК" agentOnline lastSeen={null} />);
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: operation === "close" ? "Закрыть сеанс" : "Прервать сеанс" }));
  await act(async () => {});
  expect(screen.getByText("Отправлено агенту — ожидается результат")).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(screen.getByText("Сеанс завершён; состояние обновлено")).toBeInTheDocument();
  expect(screen.queryByText("Отправлено агенту — ожидается результат")).toBeNull();
});

it("accepts a delayed native state without adding a licence requirement", async () => {
  vi.useFakeTimers();
  const delayed = state();
  let resolve!: (value: ArinstBenchState) => void;
  vi.spyOn(arinstBenchApi, "state").mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  render(<ArinstBench instrumentId="si1" pc="Последний ПК" agentOnline lastSeen={null} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); resolve(delayed); });
  expect(screen.getByText("Связь: протокол ArinstVL")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Снять S11 / S21" })).toBeEnabled();
  expect(screen.getByText(/ПК последнего обнаружения: Последний ПК/)).toBeInTheDocument();
});

it("preserves an unsaved profile across unchanged state polls", async () => {
  vi.useFakeTimers(); vi.spyOn(arinstBenchApi, "state").mockImplementation(async () => structuredClone(state()));
  render(<ArinstBench instrumentId="si1" pc="ПК" agentOnline lastSeen={null} />);
  await act(async () => {});
  screen.getByText(/^Профиль ·/).closest("details")!.open = true;
  fireEvent.change(screen.getByLabelText("Число точек"), { target: { value: "555" } });
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(screen.getByLabelText("Число точек")).toHaveValue("555");
});

it("fences an old poll response after a user command and never overlaps automatic polls", async () => {
  vi.useFakeTimers(); let resolve!: (value: ArinstBenchState) => void;
  const acquiring = state(); acquiring.session!.state = "acquiring"; acquiring.session!.version = 8;
  acquiring.session!.command = { id: "c1", op: "capture", status: "claimed" };
  const load = vi.spyOn(arinstBenchApi, "state").mockResolvedValueOnce(state())
    .mockImplementationOnce(() => new Promise((done) => { resolve = done; })).mockResolvedValue(acquiring);
  vi.spyOn(arinstBenchApi, "capture").mockResolvedValue(acquiring);
  render(<ArinstBench instrumentId="si1" pc="ПК" agentOnline lastSeen={null} />);
  await act(async () => {});
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(load).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "Снять S11 / S21" }));
  await act(async () => {});
  await act(async () => { resolve(state()); });
  expect(screen.getByText(/Команда выполняется агентом/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Снять S11 / S21" })).toBeDisabled();
});
