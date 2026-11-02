import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError, identityApi, warehouseApi, type Principal, type WarehouseScanStart, type ScanAgentInfo } from "./api";
import { forgetPersonalViews } from "./personalViews";
import { rememberedScanAgent, ScanDeviceLine, forgetScanDevice, useScanAgent, setScanDevice } from "./ScanAgent";
import { setRightsPrincipal } from "./rights";

const AGENTS = [
  { id: "m-1", pc: "СКЛАД", scanners: [{ id: "s-1", name: "Pantum" }, { id: "s-2", name: "Epson" }], version: "2.2.0", live: true },
  { id: "m-2", pc: "ОФИС", scanners: [{ id: "s-9", name: "Canon" }], version: "2.2.0", live: true },
];
function Harness({ start, onJob = () => undefined }: { start: (agentId?: string, targetScanner?: string) => Promise<WarehouseScanStart>; onJob?: (id: string | null) => void }) {
  const agent = useScanAgent(start);
  const press = () => void agent.begin().then((job) => onJob(job ? job.id : null));
  return (<><button onClick={press}>Сканировать</button>{agent.dialogs(press)}</>);
}
let saved: Array<[string, unknown]> = [];
function account(choice: unknown) {
  vi.mocked(identityApi.uiSettings).mockResolvedValue({ views: { "scan.device": choice } });
}
beforeEach(() => {
  window.localStorage.clear();
  forgetPersonalViews(); forgetScanDevice(); setRightsPrincipal(null); saved = [];
  vi.spyOn(identityApi, "uiSettings").mockResolvedValue({ views: {} });
  vi.spyOn(identityApi, "saveUiSetting").mockImplementation(async (view, value) => { saved.push([view, value]); return undefined as never; });
  vi.spyOn(identityApi, "resetUiSetting").mockResolvedValue(undefined as never);
  vi.spyOn(warehouseApi, "scanAgents").mockResolvedValue({ agents: AGENTS });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("legacy remembered PC cannot start a scan without explicit scanner choice", async () => {
  window.localStorage.setItem("erp.scanAgent", "m-2");
  const start = vi.fn(); render(<Harness start={start} />);
  fireEvent.click(screen.getByText("Сканировать"));
  expect(await screen.findByRole("dialog", { name: "Выбор сканера" })).toBeTruthy();
  expect(start).not.toHaveBeenCalled();
});
it("choice stores both ids, shows the actual PC and scanner, then starts only that pair", async () => {
  const start = vi.fn(async () => ({ id: "j1", status: "pending" as const })); const onJob = vi.fn();
  render(<><ScanDeviceLine /><Harness start={start} onJob={onJob} /></>);
  fireEvent.click(screen.getByText("Сканировать")); fireEvent.click(await screen.findByText("СКЛАД · Epson"));
  await waitFor(() => expect(onJob).toHaveBeenCalledWith("j1"));
  expect(start).toHaveBeenCalledExactlyOnceWith("m-1", "s-2");
  await waitFor(() => expect(saved).toEqual([["scan.device", { agent_id: "m-1", scanner_id: "s-2" }]]));
  expect(screen.getByTestId("scan-device").textContent).toContain("СКЛАД · Epson");
});
it("reset clears the legacy remote PC; the next scan asks for a scanner", async () => {
  account({ agent_id: "m-2", scanner_id: "s-9" }); window.localStorage.setItem("erp.scanAgent", "m-2");
  const start = vi.fn(); render(<><ScanDeviceLine /><Harness start={start} /></>);
  await screen.findByText("ОФИС · Canon"); fireEvent.click(screen.getByText("Сменить сканер"));
  fireEvent.click(await screen.findByText("Сбросить выбор"));
  expect(rememberedScanAgent()).toBe(""); expect(window.localStorage.getItem("erp.scanDevice")).toBeNull();
  fireEvent.click(screen.getByText("Сканировать"));
  expect(await screen.findByRole("dialog", { name: "Выбор сканера" })).toBeTruthy(); expect(start).not.toHaveBeenCalled();
});
it.each([{ agent_id: "gone", scanner_id: "s-9" }, { agent_id: "m-1", scanner_id: "gone" }, { agent_id: "m-1" }])(
  "an unavailable or incomplete stored choice never falls back: %j", async (choice) => {
    account(choice); const start = vi.fn(); render(<Harness start={start} />); fireEvent.click(screen.getByText("Сканировать"));
    expect(await screen.findByRole("dialog", { name: "Выбор сканера" })).toBeTruthy(); expect(start).not.toHaveBeenCalled();
  });
it("offline and pre-strict PCs show a reason and cannot be selected", async () => {
  vi.mocked(warehouseApi.scanAgents).mockResolvedValue({ agents: [{ ...AGENTS[0], live: false }, { ...AGENTS[1], version: "2.1.0" }] });
  account({ agent_id: "m-1", scanner_id: "s-2" }); const start = vi.fn();
  render(<><ScanDeviceLine /><Harness start={start} /></>);
  expect(await screen.findByText(/ПК не на связи/)).toBeTruthy();
  expect(screen.getByTestId("scan-device").textContent).not.toContain("этот ПК"); fireEvent.click(screen.getByText("Сканировать"));
  expect(await screen.findByRole("button", { name: /СКЛАД · Epson/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /ОФИС · Canon/ })).toBeDisabled();
  expect(screen.getByText(/Ожидается обновление агента на ОФИС/)).toBeTruthy(); expect(start).not.toHaveBeenCalled();
});
it("refreshes on picker open and before start; a disappeared scanner is never started", async () => {
  const listing = vi.mocked(warehouseApi.scanAgents); render(<ScanDeviceLine />);
  await waitFor(() => expect(listing).toHaveBeenCalledTimes(1));
  listing.mockResolvedValue({ agents: [{ ...AGENTS[0], scanners: [AGENTS[0].scanners[1]] }] });
  fireEvent.click(screen.getByText("Сменить сканер")); expect(await screen.findByText("СКЛАД · Epson")).toBeTruthy();
  expect(screen.queryByText("СКЛАД · Pantum")).toBeNull(); fireEvent.click(screen.getByText("СКЛАД · Epson"));
  listing.mockResolvedValue({ agents: [AGENTS[1]] }); const start = vi.fn(); render(<Harness start={start} />);
  fireEvent.click(screen.getByText("Сканировать")); expect(await screen.findByRole("dialog", { name: "Выбор сканера" })).toBeTruthy();
  expect(start).not.toHaveBeenCalled(); expect(listing).toHaveBeenCalledTimes(3);
});
it("a failed list explains the failure and never starts even a stored choice", async () => {
  account({ agent_id: "m-1", scanner_id: "s-2" }); vi.mocked(warehouseApi.scanAgents).mockRejectedValue(new Error("Нет связи с ERP"));
  const start = vi.fn(); render(<Harness start={start} />); fireEvent.click(screen.getByText("Сканировать"));
  expect(await screen.findByText(/Список ПК недоступен/)).toBeTruthy(); expect(start).not.toHaveBeenCalled();
});
it("server invalidation reopens choice and preserves scanner ids on the next request", async () => {
  account({ agent_id: "m-1", scanner_id: "s-1" });
  const start = vi.fn().mockResolvedValueOnce({ status: "choose", agents: AGENTS }).mockResolvedValueOnce({ id: "j2", status: "pending" });
  render(<Harness start={start} />); fireEvent.click(screen.getByText("Сканировать")); fireEvent.click(await screen.findByText("ОФИС · Canon"));
  await waitFor(() => expect(start).toHaveBeenLastCalledWith("m-2", "s-9"));
});
it("no scanner opens installation; missing permission points to an administrator", async () => {
  vi.mocked(warehouseApi.scanAgents).mockResolvedValue({ agents: [] });
  vi.spyOn(warehouseApi, "scanAgentInstallToken").mockRejectedValue(new ApiError("Только администратору.", 403));
  render(<Harness start={vi.fn()} />); fireEvent.click(screen.getByText("Сканировать")); expect(await screen.findByText(/попросите его/)).toBeTruthy();
});
it("without scan rights the line is hidden", () => {
  const principal = (permissions: Record<string, "view" | "edit" | "none">) =>
    ({ user: { id: "u", email: "", name: "", department: "" }, roles: ["operator"], access_level: "operator", permissions }) as Principal;
  setRightsPrincipal(principal({ scan: "view" })); const { rerender } = render(<ScanDeviceLine />);
  expect(screen.queryByTestId("scan-device")).toBeNull(); setRightsPrincipal(principal({ scan: "edit" })); rerender(<ScanDeviceLine />);
  expect(screen.getByTestId("scan-device")).toBeTruthy();
});
it("direct reset drops both browser copies", () => {
  window.localStorage.setItem("erp.scanAgent", "m-2"); setScanDevice({ agent_id: "m-1", scanner_id: "s-2" }); setScanDevice(null);
  expect(rememberedScanAgent()).toBe(""); expect(window.localStorage.getItem("erp.scanDevice")).toBeNull();
});

it("a reset while registry refresh is pending prevents the captured old pair from starting", async () => {
  account({ agent_id: "m-1", scanner_id: "s-2" });
  let finish!: (answer: { agents: ScanAgentInfo[] }) => void;
  vi.mocked(warehouseApi.scanAgents).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  const start = vi.fn(); render(<Harness start={start} />); fireEvent.click(screen.getByText("Сканировать"));
  await waitFor(() => expect(warehouseApi.scanAgents).toHaveBeenCalled());
  act(() => setScanDevice(null)); await act(async () => finish({ agents: AGENTS }));
  expect(await screen.findByRole("dialog", { name: "Выбор сканера" })).toBeTruthy(); expect(start).not.toHaveBeenCalled();
});

it("a reset during initial account loading wins over the old remote preference", async () => {
  let finish!: (answer: { views: Record<string, unknown> }) => void;
  vi.mocked(identityApi.uiSettings).mockReturnValue(new Promise((resolve) => { finish = resolve; }));
  const start = vi.fn(); render(<Harness start={start} />); fireEvent.click(screen.getByText("Сканировать"));
  act(() => setScanDevice(null));
  await act(async () => finish({ views: { "scan.device": { agent_id: "m-2", scanner_id: "s-9" } } }));
  expect(await screen.findByRole("dialog", { name: "Выбор сканера" })).toBeTruthy(); expect(start).not.toHaveBeenCalled();
});

it("server no_agent opens the guide, copies the command and rechecks on Проверить", async () => {
  account({ agent_id: "m-1", scanner_id: "s-2" });
  vi.spyOn(warehouseApi, "scanAgentInstallToken").mockResolvedValue({ command: "test-install-command", expires_at: "", valid_minutes: 60 });
  const writeText = vi.fn(async () => undefined);
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  const start = vi.fn().mockResolvedValueOnce({ status: "no_agent", idle: [] }).mockResolvedValueOnce({ id: "j3", status: "pending" });
  const onJob = vi.fn(); render(<Harness start={start} onJob={onJob} />); fireEvent.click(screen.getByText("Сканировать"));
  expect(await screen.findByTestId("scan-agent-command")).toHaveTextContent("test-install-command");
  fireEvent.click(screen.getByText("Копировать")); await waitFor(() => expect(writeText).toHaveBeenCalledWith("test-install-command"));
  fireEvent.click(screen.getByText("Проверить")); await waitFor(() => expect(onJob).toHaveBeenCalledWith("j3"));
  expect(start).toHaveBeenLastCalledWith("m-1", "s-2");
});

it("the explicit scanner chooser can open installation for a new PC", async () => {
  vi.spyOn(warehouseApi, "scanAgentInstallToken").mockResolvedValue({ command: "test-command", expires_at: "", valid_minutes: 60 });
  render(<Harness start={vi.fn()} />); fireEvent.click(screen.getByText("Сканировать"));
  fireEvent.click(await screen.findByText("Установить на этом ПК")); expect(await screen.findByRole("dialog", { name: "Установка сканера" })).toBeTruthy();
});
