import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { productionApi, vnaCaptureApi, warehouseApi, type Principal, type ProductionInstrument, type VswrTaskOverview, type VswrMeasurement, type VnaCaptureJob, type VnaCaptureSettings } from "./api";
import { VswrSection } from "./VswrSection";
import { VnaCapture, captureWarnings, VnaSettings } from "./vnaCapture";
import { setRightsPrincipal } from "./rights";

const principal = (id: string): Principal => ({ user: { id, email: "", name: "", department: "" }, roles: [], permissions: {} });

const name = "Planar, S50180, 26030095, 26.2.2/2";
const instruments = [
  { id: "arinst", inventory_number: "СИ.001", name: "Arinst", kind: "Средство измерений", status: "Действует", serial_number: "old", vna_app: "Arinst VL" },
  { id: "planar", inventory_number: "СИ.002", name: "Planar S50180", kind: "Средство измерений", status: "Действует", serial_number: "26030095", vna_app: "S2VNA" },
] as ProductionInstrument[];
const agent = { id: "pc", pc: "ПК ОТК", version: "2.4.1", vna: { name, ready: true } };
const settings: VnaCaptureSettings = {
  points: 101, start_hz: 100e6, stop_hz: 200e6, if_bandwidth_hz: 1000,
  averaging_enabled: true, averaging_factor: 4, smoothing_enabled: false, smoothing_aperture_percent: 1.5,
  correction_enabled: true, correction_status: "COR", s_parameter: "S11", frequency_step_hz: 1e6,
};
const pending: VnaCaptureJob = { id: "job", status: "pending", error: "", vna: { name }, source: "s2vna", file_name: "vna.s1p", agent,
  instrument_id: "planar", capture_settings: null };
const trace = `! S2VNA ${name} 2026-11-12T10:00:00.1234567Z S11\n# Hz S RI R 50\n100000000 0.1 0\n200000000 0.2 0\n`;
const overview = {
  task: { id: "task", number: "ПЗ-1", name: "Сборка", item: "КС", quantity: 1 },
  norm: { status: "ok", reason: "", vswr_max: 1.8, source: "customer", band_mhz: [100, 200], length_m: 1 },
  control: { mode: "all", every: null }, required: { measured: 0, total: 1 },
  serials: [{ position: 1, serial_id: "serial", serial_number: "КС-1", status: "В работе", attempts: 0, latest: null, in_sample: true }],
} as VswrTaskOverview;

beforeEach(() => {
  setRightsPrincipal(principal("user-a"));
  localStorage.clear();
  localStorage.setItem("erp.vna.pc:user-a", JSON.stringify({ id: agent.id, pc: agent.pc }));
  vi.spyOn(vnaCaptureApi, "start").mockResolvedValue(pending);
  vi.spyOn(vnaCaptureApi, "status").mockResolvedValue({ ...pending, status: "done", capture_settings: settings });
  vi.spyOn(vnaCaptureApi, "file").mockResolvedValue(new Blob([trace]));
  vi.spyOn(vnaCaptureApi, "cancel").mockResolvedValue({ ...pending, status: "failed" });
  vi.spyOn(productionApi, "vswrTasks").mockResolvedValue({ tasks: [{ ...overview.task, item: "КС", status: "В работе",
    norm: { status: "ok", vswr_max: 1.8, source: "customer" }, control: overview.control, required: overview.required }] });
  vi.spyOn(productionApi, "vswrTask").mockResolvedValue(overview);
  vi.spyOn(productionApi, "uploadVswr").mockResolvedValue({ position: 1, measurement: {
    id: "measure", verdict: "pass", verdict_name: "Годно", max_vswr: 1.5, max_vswr_mhz: 100,
    norm_vswr_max: 1.8, attempt: 1, trace: [{ mhz: 100, vswr: 1.5 }, { mhz: 200, vswr: 1.4 }], band_mhz: [100, 200],
  } as VswrMeasurement });
});
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); act(() => setRightsPrincipal(null)); });

async function open(canUpload = true) {
  render(<VswrSection instruments={instruments} canUpload={canUpload} canReject={() => false} onReject={() => {}} />);
  await screen.findByRole("option", { name: /ПЗ-1/ });
  fireEvent.change(screen.getByLabelText("Задание"), { target: { value: "task" } });
  if (canUpload) fireEvent.click(await screen.findByRole("button", { name: "Загрузить замер" }));
}
function chooseInstrument(id = "planar") {
  fireEvent.click(screen.getByRole("button", { name: "Снять с ВАЦ" }));
  fireEvent.change(screen.getByLabelText("Средство измерений"), { target: { value: id } });
  fireEvent.click(screen.getByRole("button", { name: "Снять трассу" }));
}
const uploadButton = () => within(screen.getByRole("dialog", { name: "Замер КСВН КС-1" })).getByRole("button", { name: "Загрузить замер" });

it("requires an instrument from the configured active SI list before routing", () => {
  render(<VnaCapture instruments={[...instruments,
    { ...instruments[1], id: "inactive", status: "Не используется" },
    { ...instruments[1], id: "accessory", kind: "Принадлежность" },
    { ...instruments[1], id: "no-app", vna_app: "" },
  ]} onFile={() => {}} onBusy={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Снять с ВАЦ" }));
  expect(screen.getByLabelText("Средство измерений")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Снять трассу" })).toBeDisabled();
  expect(vnaCaptureApi.start).not.toHaveBeenCalled();
  expect(within(screen.getByLabelText("Средство измерений")).getAllByRole("option")).toHaveLength(3);
  expect(screen.getByRole("option", { name: "СИ.002 · Planar S50180 — S2VNA" })).toBeInTheDocument();
});

it("offers the admin an explicit transfer without starting another capture", async () => {
  const target = { ...agent, id: "new-pc", pc: "ЛАБОРАТОРИЯ" };
  vi.mocked(vnaCaptureApi.start).mockResolvedValue({ status: "no_agent", message: "ВАЦ № 26030095 закреплён за ПК ОТК",
    transfer: { serial: "26030095", owner_id: "old-pc", agents: [target] } });
  const move = vi.spyOn(vnaCaptureApi, "transfer").mockResolvedValue({ status: "transferred" });
  render(<VnaCapture instruments={instruments} onFile={vi.fn()} onBusy={vi.fn()} />);
  chooseInstrument();
  const button = await screen.findByRole("button", { name: "Перенести ВАЦ № 26030095 на ПК ЛАБОРАТОРИЯ" });
  expect(move).not.toHaveBeenCalled();
  fireEvent.click(button);
  expect(await screen.findByText("ВАЦ № 26030095 перенесён на ПК ЛАБОРАТОРИЯ")).toBeInTheDocument();
  expect(move).toHaveBeenCalledExactlyOnceWith("planar", "26030095", "old-pc", "new-pc");
  expect(vnaCaptureApi.start).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("does not offer transfer when the server grants no admin action", async () => {
  vi.mocked(vnaCaptureApi.start).mockResolvedValue({ status: "no_agent", message: "ВАЦ закреплён за другим ПК", transfer: null });
  render(<VnaCapture instruments={instruments} onFile={vi.fn()} onBusy={vi.fn()} />);
  chooseInstrument();
  await screen.findByText("ВАЦ закреплён за другим ПК");
  expect(screen.queryByRole("button", { name: /Перенести ВАЦ/ })).not.toBeInTheDocument();
});

it("keeps a failed transfer visible and never claims success", async () => {
  vi.mocked(vnaCaptureApi.start).mockResolvedValue({ status: "no_agent", message: "Конфликт ВАЦ",
    transfer: { serial: "26030095", owner_id: "old-pc", agents: [agent] } });
  vi.spyOn(vnaCaptureApi, "transfer").mockRejectedValue(new Error("Привязка ВАЦ уже изменилась"));
  render(<VnaCapture instruments={instruments} onFile={vi.fn()} onBusy={vi.fn()} />);
  chooseInstrument();
  fireEvent.click(await screen.findByRole("button", { name: /Перенести ВАЦ/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Привязка ВАЦ уже изменилась");
  expect(screen.queryByText(/перенесён на ПК/)).not.toBeInTheDocument();
  expect(vnaCaptureApi.start).toHaveBeenCalledTimes(1);
});

it("captures into the upload path with the chosen instrument and read-only settings", async () => {
  await open(); chooseInstrument();
  expect(await screen.findByText(/ВАЦ: снимаю трассу/)).toBeInTheDocument();
  expect(uploadButton()).toBeDisabled();
  await screen.findByText("Выбран файл: vna.s1p", {}, { timeout: 3000 });
  expect(vnaCaptureApi.start).toHaveBeenCalledWith("planar", "", false);
  expect(screen.getByLabelText("Прибор (средство измерения)")).toHaveValue("planar");
  expect(screen.getByLabelText("Прибор (средство измерения)")).toBeDisabled();
  expect(screen.getByRole("region", { name: "Настройки ВАЦ" })).toHaveTextContent("Точек: 101");
  expect(screen.queryByLabelText("Устройство ВАЦ")).not.toBeInTheDocument();
  await waitFor(() => expect(uploadButton()).toBeEnabled());
  fireEvent.click(uploadButton());
  await waitFor(() => expect(productionApi.uploadVswr).toHaveBeenCalledWith(expect.objectContaining({
    task_id: "task", instrument_id: "planar", file_name: "vna.s1p", file_base64: btoa(trace), position: 1, capture_settings: settings, capture_job_id: "job",
  })));
  expect(await screen.findByRole("img", { name: /График КСВН/ })).toBeInTheDocument();
});

it("shows an instrument mismatch without downloading or attaching the rejected capture", async () => {
  const error = "Серийный номер ВАЦ в файле не совпадает с выбранным СИ. Проверьте подключённый прибор и повторите снятие.";
  vi.mocked(vnaCaptureApi.status).mockResolvedValue({ ...pending, status: "failed", error });
  await open(); chooseInstrument();
  expect(await screen.findByText(error, {}, { timeout: 3000 })).toBeInTheDocument();
  expect(vnaCaptureApi.file).not.toHaveBeenCalled();
  expect(screen.queryByText("Выбран файл: vna.s1p")).not.toBeInTheDocument();
});

it("clears captured settings and unlocks SI when choosing an ordinary file", async () => {
  await open(); chooseInstrument();
  await screen.findByText("Выбран файл: vna.s1p", {}, { timeout: 3000 });
  fireEvent.change(screen.getByLabelText("Файл замера"), { target: { files: [new File([trace], "old.s1p")] } });
  expect(screen.getByLabelText("Прибор (средство измерения)")).toBeEnabled();
  expect(screen.queryByRole("region", { name: "Настройки ВАЦ" })).not.toBeInTheDocument();
  fireEvent.click(uploadButton());
  await waitFor(() => expect(productionApi.uploadVswr).toHaveBeenCalledWith(expect.objectContaining({ file_name: "old.s1p", file_base64: btoa(trace) })));
  expect(vi.mocked(productionApi.uploadVswr).mock.calls[0][0]).not.toHaveProperty("capture_settings");
  expect(vi.mocked(productionApi.uploadVswr).mock.calls[0][0]).not.toHaveProperty("capture_job_id");
});

it.each([
  [true, "", "готов"], [false, "s2vna_not_running", "S2VNA не запущена"],
  [false, "device_not_connected", "прибор не подключён"], [false, "probe_failed", "готовность не подтверждена"],
] as const)("shows PC readiness only when routing asks for a choice (%s, %s)", async (ready, reason, label) => {
  vi.mocked(vnaCaptureApi.start).mockResolvedValue({ status: "choose", agents: [{ ...agent, vna: { name, ready, reason } }] });
  render(<VnaCapture instruments={instruments} onFile={() => {}} onBusy={() => {}} />); chooseInstrument();
  expect(await screen.findByRole("option", { name: `${name} · ${agent.pc} · ${label}` })).toBeInTheDocument();
});

it("asks only for PC on ambiguous routing and resubmits the same instrument", async () => {
  vi.mocked(vnaCaptureApi.start).mockResolvedValueOnce({ status: "choose", agents: [agent, { ...agent, id: "pc2", pc: "Второй ПК" }] });
  render(<VnaCapture instruments={instruments} onFile={() => {}} onBusy={() => {}} />); chooseInstrument("arinst");
  fireEvent.change(await screen.findByLabelText("Устройство ВАЦ"), { target: { value: "pc2" } });
  expect(screen.queryByLabelText("ПК и источник ВАЦ")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Снять трассу" }));
  await waitFor(() => expect(vnaCaptureApi.start).toHaveBeenLastCalledWith("arinst", "pc2", false));
});

it.each([
  ["planar", "ВАЦ СИ.002 не в сети: на ПК ОТК не запущена S2VNA или прибор не подключён (последний раз 12.11.2026 12:00)"],
  ["arinst", "ВАЦ СИ.001 не в сети: запустите агент ERP на ПК с Arinst Virtual Lab"],
])("offers the source-specific install guide for %s", async (id, message) => {
  vi.mocked(vnaCaptureApi.start).mockResolvedValue({ status: "no_agent", message });
  vi.spyOn(warehouseApi, "scanAgentInstallToken").mockResolvedValue({ command: "test-install", valid_minutes: 60, expires_at: "2026-11-12T12:00:00" });
  render(<VnaCapture instruments={instruments} onFile={() => {}} onBusy={() => {}} />); chooseInstrument(id);
  expect(await screen.findByText(message)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Получить команду установки" }));
  expect(await screen.findByText("test-install")).toBeInTheDocument();
});

it("cancels a job that finishes creating after the capture window closes", async () => {
  let resolve!: (job: VnaCaptureJob) => void;
  vi.mocked(vnaCaptureApi.start).mockImplementation(() => new Promise((done) => { resolve = done; }));
  const onFile = vi.fn();
  render(<VnaCapture instruments={instruments} onFile={onFile} onBusy={() => {}} />); chooseInstrument();
  fireEvent.click(screen.getByRole("button", { name: "Отменить снятие" }));
  await act(async () => { resolve(pending); });
  expect(vnaCaptureApi.cancel).toHaveBeenCalledWith("job");
  expect(onFile).not.toHaveBeenCalled();
});

it("shows agent errors without discarding the previous upload file", async () => {
  vi.mocked(vnaCaptureApi.start).mockResolvedValue({ ...pending, status: "failed", error: "Активная трасса — S21: выберите S11 или S22 в S2VNA" });
  await open();
  fireEvent.change(screen.getByLabelText("Файл замера"), { target: { files: [new File([trace], "old.s1p")] } });
  chooseInstrument();
  expect(await screen.findByRole("alert")).toHaveTextContent("Активная трасса — S21");
  expect(screen.getByText("Выбран файл: old.s1p")).toBeInTheDocument();
  expect(productionApi.uploadVswr).not.toHaveBeenCalled();
});

it("hides capture together with upload when the user cannot upload", async () => {
  await open(false);
  expect(await screen.findByText("КС-1")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Снять с ВАЦ" })).not.toBeInTheDocument();
});

it("passes the original Arinst s2p and the explicitly selected instrument through upload", async () => {
  const job = { ...pending, status: "done" as const, source: "arinst" as const, instrument_id: "arinst", vna: { name: "Arinst VNA-DL" }, file_name: "кабель.s2p" };
  vi.mocked(vnaCaptureApi.start).mockResolvedValue(job);
  const arinstTrace = "! Arinst Virtual Lab\n# MHz S RI R 50\n100 0.1 0 0 0 0 0 0 0\n";
  vi.mocked(vnaCaptureApi.file).mockResolvedValue(new Blob([arinstTrace]));
  await open(); chooseInstrument("arinst");
  await screen.findByText("Выбран файл: кабель.s2p");
  expect(screen.getByLabelText("Прибор (средство измерения)")).toHaveValue("arinst");
  await waitFor(() => expect(uploadButton()).toBeEnabled());
  fireEvent.click(uploadButton());
  await waitFor(() => expect(productionApi.uploadVswr).toHaveBeenCalledWith(expect.objectContaining({ instrument_id: "arinst", file_name: "кабель.s2p", file_base64: btoa(arinstTrace) })));
});

it("shows all four warnings without blocking an upload", async () => {
  const warned = { ...settings, smoothing_enabled: true, correction_status: "C?", start_hz: 110e6, frequency_step_hz: 20e6 };
  vi.mocked(vnaCaptureApi.start).mockResolvedValue({ ...pending, status: "done", capture_settings: warned });
  await open(); chooseInstrument();
  for (const warning of captureWarnings(warned, overview.norm)) expect(await screen.findByText(warning)).toBeInTheDocument();
  expect(captureWarnings(warned, overview.norm)).toHaveLength(4);
  await waitFor(() => expect(uploadButton()).toBeEnabled());
  fireEvent.click(uploadButton());
  await waitFor(() => expect(productionApi.uploadVswr).toHaveBeenCalled());
});

it.each(["NONE", "C?", "C!", "RC?", "RC!"])("warns for calibration status %s", (correction_status) => {
  expect(captureWarnings({ ...settings, correction_status }, overview.norm)).toEqual(["Нет калибровки на этом диапазоне"]);
});

it("does not fabricate OFF states, coverage or a length-based warning from missing settings", () => {
  expect(captureWarnings({}, overview.norm)).toEqual([]);
  expect(captureWarnings({ frequency_step_hz: 1e12 }, { ...overview.norm, length_m: null })).toEqual([]);
  render(<VnaSettings settings={{ unavailable: ["smoothing_enabled"] }} norm={overview.norm} />);
  expect(screen.getByRole("region", { name: "Настройки ВАЦ" })).toHaveTextContent("не прочитано");
  expect(screen.queryByText("выключено")).not.toBeInTheDocument();
});

it("shows persisted settings and actual-file coverage warnings after saving", async () => {
  const result = vi.mocked(productionApi.uploadVswr).getMockImplementation()!;
  vi.mocked(productionApi.uploadVswr).mockImplementation(async (payload) => {
    const saved = await result(payload);
    return { ...saved, measurement: { ...saved.measurement, file_meta: {
      capture_settings: { ...settings, points: 51 },
      capture_warnings: ["Диапазон ВАЦ не покрывает норму"],
    } } };
  });
  vi.mocked(vnaCaptureApi.start).mockResolvedValue({ ...pending, status: "done", capture_settings: settings });
  await open(); chooseInstrument();
  await screen.findByText("Выбран файл: vna.s1p");
  await waitFor(() => expect(uploadButton()).toBeEnabled());
  fireEvent.click(uploadButton());
  expect(await screen.findByText("Диапазон ВАЦ не покрывает норму")).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Настройки ВАЦ" })).toHaveTextContent("Точек: 51");
});

it("handles an empty configured-instrument registry with a useful action", () => {
  render(<VnaCapture instruments={[]} onFile={() => {}} onBusy={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Снять с ВАЦ" }));
  expect(screen.getByText(/Укажите «Программу ВАЦ»/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Снять трассу" })).toBeDisabled();
});

it("requires explicit PC selection on the first capture even for a single candidate", async () => {
  localStorage.clear();
  vi.mocked(vnaCaptureApi.start).mockResolvedValueOnce({ status: "choose", agents: [agent] });
  render(<VnaCapture instruments={instruments} onFile={() => {}} onBusy={() => {}} />);
  chooseInstrument("arinst");
  expect(await screen.findByLabelText("Устройство ВАЦ")).toHaveValue("");
  expect(vnaCaptureApi.start).toHaveBeenCalledExactlyOnceWith("arinst", "", false);
  expect(screen.getByRole("button", { name: "Снять трассу" })).toBeDisabled();
  expect(localStorage.getItem("erp.vna.pc:user-a")).toBeNull();
  fireEvent.change(screen.getByLabelText("Устройство ВАЦ"), { target: { value: "pc" } });
  fireEvent.click(screen.getByRole("button", { name: "Снять трассу" }));
  await waitFor(() => expect(vnaCaptureApi.start).toHaveBeenLastCalledWith("arinst", "pc", false));
  expect(await screen.findByText("ПК: ПК ОТК")).toBeInTheDocument();
  expect(JSON.parse(localStorage.getItem("erp.vna.pc:user-a")!)).toEqual({ id: "pc", pc: "ПК ОТК" });
});

it("asks a new browser to confirm the user's server preference", async () => {
  localStorage.clear();
  vi.mocked(vnaCaptureApi.start).mockResolvedValue({ status: "choose", agents: [agent], preferred_agent_id: "pc" });
  render(<VnaCapture instruments={instruments} onFile={() => {}} onBusy={() => {}} />);
  chooseInstrument("arinst");
  expect(await screen.findByLabelText("Устройство ВАЦ")).toHaveValue("pc");
  expect(screen.getByRole("button", { name: "Снять трассу" })).toBeEnabled();
  expect(vnaCaptureApi.start).toHaveBeenCalledTimes(1);
  expect(localStorage.getItem("erp.vna.pc:user-a")).toBeNull();
});

it("shows the remembered PC and lets the user explicitly change it before capture", async () => {
  const second = { ...agent, id: "pc2", pc: "Другой ПК" };
  vi.mocked(vnaCaptureApi.start)
    .mockResolvedValueOnce({ status: "choose", agents: [agent, second], preferred_agent_id: "pc" })
    .mockResolvedValueOnce({ ...pending, agent: second });
  render(<VnaCapture instruments={instruments} onFile={() => {}} onBusy={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Снять с ВАЦ" }));
  expect(screen.queryByText("ПК: ПК ОТК")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Средство измерений"), { target: { value: "arinst" } });
  fireEvent.click(screen.getByRole("button", { name: "Сменить устройство" }));
  fireEvent.change(await screen.findByLabelText("Устройство ВАЦ"), { target: { value: "pc2" } });
  expect(vnaCaptureApi.start).toHaveBeenCalledExactlyOnceWith("arinst", "", true);
  fireEvent.click(screen.getByRole("button", { name: "Снять трассу" }));
  expect(await screen.findByText("ПК: Другой ПК")).toBeInTheDocument();
  expect(vnaCaptureApi.start).toHaveBeenLastCalledWith("arinst", "pc2", false);
  expect(JSON.parse(localStorage.getItem("erp.vna.pc:user-a")!)).toEqual({ id: "pc2", pc: "Другой ПК" });
});

it("uses a confirmed PC after remounting without asking again", async () => {
  localStorage.clear();
  vi.mocked(vnaCaptureApi.start)
    .mockResolvedValueOnce({ status: "choose", agents: [agent] })
    .mockResolvedValue({ ...pending, status: "done" });
  const onFile = vi.fn();
  const view = render(<VnaCapture instruments={instruments} onFile={onFile} onBusy={() => {}} />);
  chooseInstrument("arinst");
  fireEvent.change(await screen.findByLabelText("Устройство ВАЦ"), { target: { value: "pc" } });
  fireEvent.click(screen.getByRole("button", { name: "Снять трассу" }));
  await waitFor(() => expect(onFile).toHaveBeenCalledTimes(1));
  view.unmount();
  render(<VnaCapture instruments={instruments} onFile={onFile} onBusy={() => {}} />);
  chooseInstrument("arinst");
  await waitFor(() => expect(onFile).toHaveBeenCalledTimes(2));
  expect(vnaCaptureApi.start).toHaveBeenLastCalledWith("arinst", "", false);
  expect(screen.queryByLabelText("Устройство ВАЦ")).not.toBeInTheDocument();
});

it.each(["broken-json", "blocked-storage"])("still captures with explicit choice when storage is %s", async (mode) => {
  localStorage.setItem("erp.vna.pc:user-a", "{");
  if (mode === "blocked-storage") {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("unavailable"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("unavailable"); });
  }
  vi.mocked(vnaCaptureApi.start)
    .mockResolvedValueOnce({ status: "choose", agents: [agent] })
    .mockResolvedValue({ ...pending, status: "done" });
  const onFile = vi.fn();
  render(<VnaCapture instruments={instruments} onFile={onFile} onBusy={() => {}} />);
  chooseInstrument("arinst");
  fireEvent.change(await screen.findByLabelText("Устройство ВАЦ"), { target: { value: "pc" } });
  fireEvent.click(screen.getByRole("button", { name: "Снять трассу" }));
  await waitFor(() => expect(onFile).toHaveBeenCalledTimes(1));
});

it("asks again when the remembered PC is offline without silently choosing its replacement", async () => {
  vi.mocked(vnaCaptureApi.start).mockResolvedValue({ status: "choose", agents: [{ ...agent, id: "pc2" }], preferred_agent_id: "pc" });
  render(<VnaCapture instruments={instruments} onFile={() => {}} onBusy={() => {}} />);
  chooseInstrument("arinst");
  expect(await screen.findByLabelText("Устройство ВАЦ")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Снять трассу" })).toBeDisabled();
  expect(vnaCaptureApi.start).toHaveBeenCalledExactlyOnceWith("arinst", "", false);
});


it("isolates remembered PCs across users in the same browser and resets an open capture", async () => {
  localStorage.setItem("erp.vna.pc", JSON.stringify({ id: "legacy-pc", pc: "Legacy" }));
  vi.mocked(vnaCaptureApi.start).mockResolvedValue({ status: "choose", agents: [agent] });
  render(<VnaCapture instruments={instruments} onFile={() => {}} onBusy={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Снять с ВАЦ" }));
  expect(screen.queryByText("ПК: ПК ОТК")).not.toBeInTheDocument();
  expect(localStorage.getItem("erp.vna.pc")).toBeNull();
  act(() => setRightsPrincipal(principal("user-b")));
  expect(screen.queryByRole("dialog", { name: "Снять с ВАЦ" })).not.toBeInTheDocument();
  chooseInstrument("arinst");
  expect(await screen.findByLabelText("Устройство ВАЦ")).toHaveValue("");
  expect(vnaCaptureApi.start).toHaveBeenCalledExactlyOnceWith("arinst", "", false);
  expect(screen.getByRole("button", { name: "Снять трассу" })).toBeDisabled();
  expect(localStorage.getItem("erp.vna.pc:user-b")).toBeNull();
  fireEvent.change(screen.getByLabelText("Устройство ВАЦ"), { target: { value: "pc" } });
  vi.mocked(vnaCaptureApi.start).mockResolvedValue({ ...pending, status: "done" });
  fireEvent.click(screen.getByRole("button", { name: "Снять трассу" }));
  await waitFor(() => expect(localStorage.getItem("erp.vna.pc:user-b")).toBe(JSON.stringify({ id: "pc", pc: agent.pc })));
});

it("discards an old user's pending routing response after the identity changes", async () => {
  let resolve!: (job: VnaCaptureJob) => void;
  vi.mocked(vnaCaptureApi.start).mockImplementationOnce(() => new Promise((done) => { resolve = done; }));
  const onFile = vi.fn();
  render(<VnaCapture instruments={instruments} onFile={onFile} onBusy={() => {}} />);
  chooseInstrument();
  act(() => setRightsPrincipal(principal("user-b")));
  await act(async () => { resolve({ ...pending, status: "done" }); });
  expect(vnaCaptureApi.cancel).toHaveBeenCalledWith("job");
  expect(onFile).not.toHaveBeenCalled();
  expect(localStorage.getItem("erp.vna.pc:user-b")).toBeNull();
});

it("ignores legacy storage even when it is the only remembered choice", async () => {
  localStorage.clear();
  localStorage.setItem("erp.vna.pc", JSON.stringify({ id: agent.id, pc: agent.pc }));
  vi.mocked(vnaCaptureApi.start).mockResolvedValue({ status: "choose", agents: [agent] });
  render(<VnaCapture instruments={instruments} onFile={() => {}} onBusy={() => {}} />);
  chooseInstrument("arinst");
  expect(await screen.findByLabelText("Устройство ВАЦ")).toHaveValue("");
  expect(vnaCaptureApi.start).toHaveBeenCalledExactlyOnceWith("arinst", "", false);
  expect(localStorage.getItem("erp.vna.pc")).toBeNull();
});

it("uses the server preference when routing returns a choice despite the local copy", async () => {
  const second = { ...agent, id: "pc2", pc: "Другой ПК" };
  vi.mocked(vnaCaptureApi.start).mockResolvedValue({ status: "choose", agents: [agent, second], preferred_agent_id: "pc2" });
  render(<VnaCapture instruments={instruments} onFile={() => {}} onBusy={() => {}} />);
  chooseInstrument("arinst");
  expect(await screen.findByLabelText("Устройство ВАЦ")).toHaveValue("pc2");
  expect(vnaCaptureApi.start).toHaveBeenCalledExactlyOnceWith("arinst", "", false);
  expect(screen.getByRole("button", { name: "Снять трассу" })).toBeEnabled();
});


it("routes Planar by the chosen instrument without a remembered PC", async () => {
  vi.mocked(vnaCaptureApi.start).mockResolvedValue({ ...pending, status: "done" });
  const onFile = vi.fn();
  render(<VnaCapture instruments={instruments} onFile={onFile} onBusy={() => {}} />);
  chooseInstrument();
  await waitFor(() => expect(onFile).toHaveBeenCalledTimes(1));
  expect(vnaCaptureApi.start).toHaveBeenCalledExactlyOnceWith("planar", "", false);
  expect(screen.queryByLabelText("Устройство ВАЦ")).not.toBeInTheDocument();
});
