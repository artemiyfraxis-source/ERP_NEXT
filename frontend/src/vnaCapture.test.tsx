import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { productionApi, vnaCaptureApi, warehouseApi, type ProductionInstrument, type VswrTaskOverview, type VswrMeasurement, type VnaCaptureJob } from "./api";
import { VswrSection } from "./VswrSection";
import { VnaCapture, instrumentForVna, vnaNameFromHeader } from "./vnaCapture";

const name = "Planar, S50180, 26030095, 26.2.2/2";
const instruments = [
  { id: "arinst", inventory_number: "СИ-1", name: "Arinst", kind: "Средство измерений", status: "Действует", serial_number: "old" },
  { id: "planar", inventory_number: "СИ-2", name: "Planar S50180", kind: "Средство измерений", status: "Действует", serial_number: "26030095" },
] as ProductionInstrument[];
const agent = { id: "pc", pc: "ПК ОТК", version: "2.3.1", vna: { name, ready: true } };
const pending: VnaCaptureJob = { id: "job", status: "pending", error: "", vna: { name }, source: "s2vna", file_name: "vna.s1p", agent };
const trace = `! S2VNA ${name} 2026-11-12T10:00:00.1234567Z S11\n# Hz S RI R 50\n100000000 0.1 0\n200000000 0.2 0\n`;
const overview = {
  task: { id: "task", number: "ПЗ-1", name: "Сборка", item: "КС", quantity: 1 },
  norm: { status: "ok", reason: "", vswr_max: 1.8, source: "customer", band_mhz: [100, 200] },
  control: { mode: "all", every: null }, required: { measured: 0, total: 1 },
  serials: [{ position: 1, serial_id: "serial", serial_number: "КС-1", status: "В работе", attempts: 0, latest: null, in_sample: true }],
} as VswrTaskOverview;

beforeEach(() => {
  vi.spyOn(vnaCaptureApi, "start").mockResolvedValue(pending);
  vi.spyOn(vnaCaptureApi, "status").mockResolvedValue({ ...pending, status: "done" });
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
afterEach(() => { vi.restoreAllMocks(); });

async function open(canUpload = true) {
  render(<VswrSection instruments={instruments} canUpload={canUpload} canReject={() => false} onReject={() => {}} />);
  await screen.findByRole("option", { name: /ПЗ-1/ });
  fireEvent.change(screen.getByLabelText("Задание"), { target: { value: "task" } });
  if (canUpload) fireEvent.click(await screen.findByRole("button", { name: "Загрузить замер" }));
}

it("captures into the existing upload path, selects the serial match, and renders the existing chart", async () => {
  vi.mocked(vnaCaptureApi.status).mockResolvedValue({ ...pending, status: "done", vna: { name: "S2VNA" } });
  await open();
  fireEvent.click(screen.getByRole("button", { name: "Снять с ВАЦ" }));
  expect(await screen.findByText(/ВАЦ: снимаю трассу/)).toBeInTheDocument();
  expect(within(screen.getByRole("dialog")).getByRole("button", { name: "Загрузить замер" })).toBeDisabled();
  await screen.findByText("Выбран файл: vna.s1p", {}, { timeout: 3000 });
  expect(screen.getByLabelText("Прибор (средство измерения)")).toHaveValue("planar");
  expect(productionApi.uploadVswr).not.toHaveBeenCalled();
  await waitFor(() => expect(within(screen.getByRole("dialog")).getByRole("button", { name: "Загрузить замер" })).toBeEnabled());
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Загрузить замер" }));
  await waitFor(() => expect(productionApi.uploadVswr).toHaveBeenCalledWith(expect.objectContaining({
    task_id: "task", instrument_id: "planar", file_name: "vna.s1p", file_base64: btoa(trace), position: 1,
  })));
  expect(await screen.findByRole("img", { name: /График КСВН/ })).toBeInTheDocument();
});

it("keeps the existing hand-picked file upload", async () => {
  await open();
  fireEvent.change(screen.getByLabelText("Файл замера"), { target: { files: [new File([trace], "old.s1p")] } });
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Загрузить замер" }));
  await waitFor(() => expect(productionApi.uploadVswr).toHaveBeenCalledWith(expect.objectContaining({ file_name: "old.s1p", file_base64: btoa(trace) })));
  expect(vnaCaptureApi.start).not.toHaveBeenCalled();
});

it("does not select a different instrument when the serial is unknown or ambiguous", () => {
  expect(instrumentForVna(name, instruments)).toBe("planar");
  expect(instrumentForVna("Planar, S50180, other, 1", instruments)).toBe("");
  expect(instrumentForVna("S2VNA", instruments)).toBe("");
  expect(instrumentForVna(name, [...instruments, { ...instruments[1], id: "duplicate" }])).toBe("");
});

it.each(["S11", "S22"])("parses the real Planar header through the ISO timestamp for %s", (parameter) => {
  expect(vnaNameFromHeader(`! S2VNA ${name} 2026-11-12T10:00:00.1234567Z ${parameter}\r\n# Hz S RI R 50`, "S2VNA")).toBe(name);
  expect(vnaNameFromHeader(`\uFEFF! S2VNA ${name} 2026-11-12T10:00:00+03:00 ${parameter}\n`, "S2VNA")).toBe(name);
});

it.each(["", "! S2VNA test", `! S2VNA ${name} no-timestamp S11`, `! S2VNA ${name} 2026-11-12T10:00:00Z S21`,
  `# Hz S RI R 50\n! S2VNA ${name} 2026-11-12T10:00:00Z S11`])("falls back to the cached IDN for an unsupported header: %s", (text) => {
  expect(vnaNameFromHeader(text, name)).toBe(name);
});

it("uses the agent IDN when a legacy file has no identity and the job name is empty", async () => {
  vi.mocked(vnaCaptureApi.start).mockResolvedValue({ ...pending, status: "done", vna: { name: "" } });
  vi.mocked(vnaCaptureApi.file).mockResolvedValue(new Blob(["! legacy file\n# Hz S RI R 50\n"]));
  const onFile = vi.fn();
  render(<VnaCapture onFile={onFile} onBusy={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Снять с ВАЦ" }));
  await waitFor(() => expect(onFile).toHaveBeenCalledWith(expect.any(File), name, "s2vna"));
});

it("uses the captured file's IDN instead of the previously cached instrument", async () => {
  vi.mocked(vnaCaptureApi.start).mockResolvedValue({ ...pending, status: "done" });
  const currentName = name.replace("26030095", "99999999");
  vi.mocked(vnaCaptureApi.file).mockResolvedValue(new Blob([trace.replace(name, currentName)]));
  const onFile = vi.fn();
  render(<VnaCapture onFile={onFile} onBusy={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Снять с ВАЦ" }));
  await waitFor(() => expect(onFile).toHaveBeenCalledWith(expect.any(File), currentName, "s2vna"));
});

it.each([
  [true, "", "готов"],
  [false, "s2vna_not_running", "S2VNA не запущена"],
  [false, "device_not_connected", "прибор не подключён"],
  [false, "probe_failed", "готовность не подтверждена"],
] as const)("shows PC readiness (%s, %s)", async (ready, reason, label) => {
  vi.mocked(vnaCaptureApi.start).mockResolvedValue({ status: "choose", agents: [{ ...agent, vna: { name, ready, reason } }] });
  render(<VnaCapture onFile={() => {}} onBusy={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Снять с ВАЦ" }));
  expect(await screen.findByRole("option", { name: `${agent.pc} · Planar S50180 (S2VNA) · ${label}` })).toBeInTheDocument();
});

it("requires the operator's instrument choice after a capture with an unknown serial", async () => {
  vi.mocked(vnaCaptureApi.status).mockResolvedValue({ ...pending, status: "done", vna: { name: "Planar, S50180, unknown, 1" } });
  vi.mocked(vnaCaptureApi.file).mockResolvedValue(new Blob([trace.replace("26030095", "unknown")]));
  await open();
  fireEvent.click(screen.getByRole("button", { name: "Снять с ВАЦ" }));
  await screen.findByText("Выбран файл: vna.s1p", {}, { timeout: 3000 });
  expect(screen.getByLabelText("Прибор (средство измерения)")).toHaveValue("");
  expect(within(screen.getByRole("dialog")).getByRole("button", { name: "Загрузить замер" })).toBeDisabled();
});

it("shows the agent's Russian error verbatim", async () => {
  const error = "Активная трасса — S21: выберите S11 или S22 в S2VNA";
  vi.mocked(vnaCaptureApi.start).mockResolvedValue({ ...pending, status: "failed", error });
  await open();
  fireEvent.click(screen.getByRole("button", { name: "Снять с ВАЦ" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(error);
  expect(productionApi.uploadVswr).not.toHaveBeenCalled();
});

it("requires explicit PC selection and then sends that agent id", async () => {
  vi.mocked(vnaCaptureApi.start).mockResolvedValueOnce({ status: "choose", agents: [agent] });
  render(<VnaCapture onFile={() => {}} onBusy={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Снять с ВАЦ" }));
  fireEvent.change(await screen.findByLabelText("ПК и источник ВАЦ"), { target: { value: "s2vna:pc" } });
  expect(vnaCaptureApi.start).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Снять на выбранном ПК" }));
  await waitFor(() => expect(vnaCaptureApi.start).toHaveBeenLastCalledWith("pc", "s2vna"));
});

it("offers the install guide if no VNA agent is present", async () => {
  vi.mocked(vnaCaptureApi.start).mockResolvedValue({ status: "no_agent" });
  vi.spyOn(warehouseApi, "scanAgentInstallToken").mockResolvedValue({ command: "test-install", valid_minutes: 60, expires_at: "2026-11-12T12:00:00" });
  render(<VnaCapture onFile={() => {}} onBusy={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Снять с ВАЦ" }));
  expect(await screen.findByText(/Агент с ВАЦ не найден/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Получить команду установки" }));
  expect(await screen.findByText("test-install")).toBeInTheDocument();
});

it("cancels a job that finishes creating after the window closes", async () => {
  let resolve!: (job: VnaCaptureJob) => void;
  vi.mocked(vnaCaptureApi.start).mockImplementation(() => new Promise((done) => { resolve = done; }));
  const onFile = vi.fn();
  const view = render(<VnaCapture onFile={onFile} onBusy={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Снять с ВАЦ" }));
  view.unmount();
  await act(async () => { resolve(pending); });
  expect(vnaCaptureApi.cancel).toHaveBeenCalledWith("job");
  expect(onFile).not.toHaveBeenCalled();
});

it("hides capture together with upload when the user cannot upload", async () => {
  await open(false);
  expect(await screen.findByText("КС-1")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Снять с ВАЦ" })).not.toBeInTheDocument();
  expect(within(screen.getByRole("table")).queryByRole("button", { name: "Загрузить замер" })).not.toBeInTheDocument();
});

it("offers both sources on one PC and submits the selected Arinst source", async () => {
  vi.mocked(vnaCaptureApi.start).mockResolvedValueOnce({ status: "choose", agents: [{ ...agent, vna_sources: ["s2vna", "arinst"] }] });
  render(<VnaCapture onFile={() => {}} onBusy={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Снять с ВАЦ" }));
  const select = await screen.findByLabelText("ПК и источник ВАЦ");
  expect(screen.getByRole("option", { name: `${agent.pc} · Planar S50180 (S2VNA) · готов` })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: `${agent.pc} · Arinst — последний файл` })).toBeInTheDocument();
  expect(select).toHaveValue("");
  fireEvent.change(select, { target: { value: "arinst:pc" } });
  fireEvent.click(screen.getByRole("button", { name: "Снять на выбранном ПК" }));
  await waitFor(() => expect(vnaCaptureApi.start).toHaveBeenLastCalledWith("pc", "arinst"));
});

it("offers an Arinst-only PC without inventing a Planar source", async () => {
  vi.mocked(vnaCaptureApi.start).mockResolvedValue({ status: "choose", agents: [{ ...agent, vna: null, vna_sources: ["arinst"] }] });
  render(<VnaCapture onFile={() => {}} onBusy={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Снять с ВАЦ" }));
  expect(await screen.findByRole("option", { name: `${agent.pc} · Arinst — последний файл` })).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: /Planar/ })).not.toBeInTheDocument();
});

it("preselects only a single active Arinst measuring instrument by name or manufacturer", () => {
  const renamed = { ...instruments[0], name: "ВАЦ VNA-DL", manufacturer: "ARINST" };
  expect(instrumentForVna("", instruments, "arinst")).toBe("arinst");
  expect(instrumentForVna("", [renamed], "arinst")).toBe("arinst");
  expect(instrumentForVna("", [{ ...renamed, status: "Не используется" }], "arinst")).toBe("");
  expect(instrumentForVna("", [{ ...renamed, kind: "Принадлежность" }], "arinst")).toBe("");
  expect(instrumentForVna("", [instruments[1]], "arinst")).toBe("");
  expect(instrumentForVna("", [renamed, { ...instruments[0], id: "second" }], "arinst")).toBe("");
});

it("passes the original Arinst s2p through upload and preselects its instrument", async () => {
  const job = { ...pending, source: "arinst" as const, vna: { name: "Arinst VNA-DL" }, file_name: "кабель.s2p" };
  vi.mocked(vnaCaptureApi.start).mockResolvedValue(job);
  vi.mocked(vnaCaptureApi.status).mockResolvedValue({ ...job, status: "done" });
  const arinstTrace = "! Arinst Virtual Lab\n# MHz S RI R 50\n100 0.1 0 0 0 0 0 0 0\n";
  vi.mocked(vnaCaptureApi.file).mockResolvedValue(new Blob([arinstTrace]));
  await open();
  fireEvent.click(screen.getByRole("button", { name: "Снять с ВАЦ" }));
  await screen.findByText("Выбран файл: кабель.s2p", {}, { timeout: 3000 });
  expect(screen.getByLabelText("Прибор (средство измерения)")).toHaveValue("arinst");
  await waitFor(() => expect(within(screen.getByRole("dialog")).getByRole("button", { name: "Загрузить замер" })).toBeEnabled());
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Загрузить замер" }));
  await waitFor(() => expect(productionApi.uploadVswr).toHaveBeenCalledWith(expect.objectContaining({
    instrument_id: "arinst", file_name: "кабель.s2p", file_base64: btoa(arinstTrace),
  })));
});
