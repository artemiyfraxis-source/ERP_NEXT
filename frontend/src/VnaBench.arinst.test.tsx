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
      hardware_uid: "1-2-3", firmware: "1", machine_id: "pc1", control_ready: true, reason: null,
      supported_ops: ["connect", "close", "apply_profile", "measure_standard", "capture"],
      license: { state: "TRIAL", trial_remaining_seconds: 121, message: "" } },
    profile, calibration: { valid: false, status: "required" }, acceptance: { eligible: false, code: "commissioning_required", message: "Сетка и коррекция не подтверждены" },
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

it("selects Arinst by UUID, shows live licence and the commissioning reason without Planar controls", async () => {
  await show();
  expect(arinstBenchApi.state).toHaveBeenCalledWith("si1", expect.any(AbortSignal));
  expect(screen.getByText("Пробный режим API — осталось 3 мин")).toBeInTheDocument();
  expect(screen.getByText(/ПК последнего обнаружения: ПК ОТК.*ArinstVL закрыт/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Сохранить в КБ/СИ" })).toBeDisabled();
  expect(screen.getByText("Сохранение в КБ/СИ — после ввода Arinst в эксплуатацию")).toBeInTheDocument();
  expect(screen.queryByText("Калибровка АКМ")).toBeNull();
  expect(screen.queryByRole("button", { name: /сброс|питание|активировать лицензию/i })).toBeNull();
});

it.each(["LOCKED", "UNKNOWN"] as const)("blocks acquisition when licence is %s while leaving release available", async (license) => {
  const value = state(); value.device.license.state = license;
  await show(value);
  expect(screen.getByRole("button", { name: "Снять S11 / S21" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Прервать сеанс" })).toBeEnabled();
});

it("counts down locally and refuses an expired trial even during a slow poll", async () => {
  vi.useFakeTimers();
  const value = state(); value.device.license.trial_remaining_seconds = 1;
  vi.spyOn(arinstBenchApi, "state").mockResolvedValueOnce(value).mockImplementation(() => new Promise(() => {}));
  render(<ArinstBench instrumentId="si1" pc="ПК" agentOnline lastSeen={null} />);
  await act(async () => {});
  await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
  expect(screen.getByText(/Время пробного режима вышло — переподключите прибор вручную/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Снять S11 / S21" })).toBeDisabled();
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
  fireEvent.click(screen.getByRole("button", { name: "Подтвердить запись профиля" }));
  await waitFor(() => expect(write).toHaveBeenCalledWith("s1", 7, { ...profile, start_hz: 10.5e6 }));
});

it.each([['Конец, МГц', '0,5'], ['Конец, МГц', '8801'], ['Число точек', '1001'], ['Число точек', '2.5']])("refuses invalid profile %s=%s", async (label, number) => {
  await show(); screen.getByText(/^Профиль ·/).closest("details")!.open = true;
  fireEvent.change(screen.getByLabelText(label), { target: { value: number } });
  fireEvent.click(screen.getByRole("button", { name: "Проверить изменения" }));
  expect(screen.getByRole("alert")).toHaveTextContent("Укажите возрастающий диапазон");
  expect(screen.queryByRole("button", { name: "Подтвердить запись профиля" })).toBeNull();
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

it("does not add reply transport time back to the trial countdown", async () => {
  vi.useFakeTimers();
  const delayed = state(); delayed.device.license.trial_remaining_seconds = 5;
  let resolve!: (value: ArinstBenchState) => void;
  vi.spyOn(arinstBenchApi, "state").mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  render(<ArinstBench instrumentId="si1" pc="Последний ПК" agentOnline lastSeen={null} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); resolve(delayed); });
  expect(screen.getByText(/Время пробного режима вышло — переподключите прибор вручную/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Снять S11 / S21" })).toBeDisabled();
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
