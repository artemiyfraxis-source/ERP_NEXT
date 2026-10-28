import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError, identityApi, warehouseApi, type Principal, type WarehouseScanStart } from "./api";
import { forgetPersonalViews } from "./personalViews";
import { rememberedScanAgent, ScanDeviceLine, forgetScanDevice, useScanAgent } from "./ScanAgent";
import { setRightsPrincipal } from "./rights";

const AGENTS = [
  { id: "m-1", pc: "SKLAD", scanners: ["Pantum BM2300W"], version: "2.0.0" },
  { id: "m-2", pc: "OFFICE", scanners: ["Canon LiDE"], version: "2.0.0" },
];
const AGENTS_WITH_IDS = [
  { id: "m-1", pc: "SKLAD", scanners: [{ id: "s-1", name: "Pantum BM2300W" }, { id: "s-2", name: "Epson V39" }], version: "2.0.0" },
  { id: "m-2", pc: "OFFICE", scanners: [{ id: "s-9", name: "Canon LiDE" }], version: "2.0.0" },
];

/** «Сканировать» on the shared hook: the first press, and what it did with the answer. */
function Harness({ start, onJob }: { start: (agentId?: string, targetScanner?: string) => Promise<WarehouseScanStart>; onJob: (id: string | null) => void }) {
  const agent = useScanAgent(start);
  const press = () => void agent.begin().then((job) => onJob(job ? job.id : null));
  return (<><button onClick={press}>Сканировать</button>{agent.dialogs(press)}</>);
}

let saved: Array<[string, unknown]> = [];
function account(views: Record<string, unknown>, agents: unknown[] = AGENTS_WITH_IDS) {
  vi.spyOn(identityApi, "uiSettings").mockResolvedValue({ views });
  vi.spyOn(identityApi, "saveUiSetting").mockImplementation(async (view, value) => { saved.push([view, value]); return undefined as never; });
  vi.spyOn(warehouseApi, "scanAgents").mockResolvedValue({ agents } as never);
}
beforeEach(() => {
  window.localStorage.clear();
  forgetPersonalViews();
  forgetScanDevice();
  setRightsPrincipal(null);
  saved = [];
  vi.spyOn(identityApi, "uiSettings").mockResolvedValue({ views: {} });
  vi.spyOn(warehouseApi, "scanAgents").mockResolvedValue({ agents: AGENTS_WITH_IDS } as never);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("a job from the server goes straight on, the remembered agent is sent along", async () => {
  window.localStorage.setItem("erp.scanAgent", "m-2");
  const start = vi.fn(async () => ({ id: "j1", status: "pending" as const }));
  const onJob = vi.fn();
  render(<Harness start={start} onJob={onJob} />);
  fireEvent.click(screen.getByText("Сканировать"));
  await waitFor(() => expect(onJob).toHaveBeenCalledWith("j1"));
  expect(start).toHaveBeenCalledWith("m-2", undefined);
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("no agent: the install guide with the one-time command, «Копировать» and «Проверить» that asks again", async () => {
  const issued = vi.spyOn(warehouseApi, "scanAgentInstallToken").mockResolvedValue({
    command: "powershell -NoProfile -Command \"... -InstallToken 'frxi_abc'\"", expires_at: "2026-10-28T12:00:00", valid_minutes: 60,
  });
  const writeText = vi.fn(async () => undefined);
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  const start = vi.fn()
    .mockResolvedValueOnce({ status: "no_agent", idle: [] })
    .mockResolvedValueOnce({ id: "j2", status: "pending" });
  const onJob = vi.fn();
  render(<Harness start={start} onJob={onJob} />);
  fireEvent.click(screen.getByText("Сканировать"));

  expect(await screen.findByRole("dialog", { name: "Установка сканера" })).toBeTruthy();
  expect((await screen.findByTestId("scan-agent-command")).textContent).toContain("frxi_abc");
  expect(issued).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByText("Копировать"));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(expect.stringContaining("frxi_abc")));
  expect(await screen.findByText("Скопировано")).toBeTruthy();

  fireEvent.click(screen.getByText("Проверить"));
  await waitFor(() => expect(onJob).toHaveBeenLastCalledWith("j2"));
  expect(start).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("a user who may not issue the command is told to ask the administrator", async () => {
  vi.spyOn(warehouseApi, "scanAgentInstallToken").mockRejectedValue(new ApiError("Только администратору.", 403));
  render(<Harness start={async () => ({ status: "no_agent" as const })} onJob={() => undefined} />);
  fireEvent.click(screen.getByText("Сканировать"));
  expect(await screen.findByText(/попросите его/)).toBeTruthy();
  expect(screen.queryByTestId("scan-agent-command")).toBeNull();
  expect(screen.getByText("Проверить")).toBeTruthy();
});

it("several PCs: a picker «Сканер: <ПК> · <модель>», the choice is remembered and the press repeats", async () => {
  const start = vi.fn()
    .mockResolvedValueOnce({ status: "choose", agents: AGENTS })
    .mockResolvedValueOnce({ id: "j3", status: "pending" });
  const onJob = vi.fn();
  render(<Harness start={start} onJob={onJob} />);
  fireEvent.click(screen.getByText("Сканировать"));
  fireEvent.click(await screen.findByText("Сканер: OFFICE · Canon LiDE"));
  await waitFor(() => expect(onJob).toHaveBeenCalledWith("j3"));
  expect(rememberedScanAgent()).toBe("m-2");
  expect(start).toHaveBeenLastCalledWith("m-2", undefined);
});

it("the picker opens the install guide for this PC", async () => {
  vi.spyOn(warehouseApi, "scanAgentInstallToken").mockResolvedValue({ command: "cmd-1", expires_at: "", valid_minutes: 60 });
  render(<Harness start={async () => ({ status: "choose" as const, agents: AGENTS })} onJob={() => undefined} />);
  fireEvent.click(screen.getByText("Сканировать"));
  fireEvent.click(await screen.findByText("Установить на этом ПК"));
  expect(await screen.findByRole("dialog", { name: "Установка сканера" })).toBeTruthy();
});

it("the account's stored scanner is sent as target_scanner with its PC", async () => {
  account({ "scan.device": { agent_id: "m-1", scanner_id: "s-2" } });
  const start = vi.fn(async () => ({ id: "j9", status: "pending" as const }));
  const onJob = vi.fn();
  render(<Harness start={start} onJob={onJob} />);
  fireEvent.click(screen.getByText("Сканировать"));
  await waitFor(() => expect(onJob).toHaveBeenCalledWith("j9"));
  expect(start).toHaveBeenCalledWith("m-1", "s-2");
});

it("no stored choice: the request carries no PC and no scanner (this browser's PC, its first scanner)", async () => {
  const start = vi.fn(async () => ({ id: "j8", status: "pending" as const }));
  const onJob = vi.fn();
  render(<Harness start={start} onJob={onJob} />);
  fireEvent.click(screen.getByText("Сканировать"));
  await waitFor(() => expect(onJob).toHaveBeenCalledWith("j8"));
  expect(start).toHaveBeenCalledWith(undefined, undefined);
});

it("a stored scanner no longer reported: falls back to this PC and says so in one line", async () => {
  account({ "scan.device": { agent_id: "m-1", scanner_id: "s-gone" } });
  const start = vi.fn(async () => ({ id: "j7", status: "pending" as const }));
  const onJob = vi.fn();
  render(<><ScanDeviceLine /><Harness start={start} onJob={onJob} /></>);
  expect(await screen.findByText("Выбранный сканер не найден — сканируем с этого ПК")).toBeTruthy();
  expect(screen.getByTestId("scan-device").textContent).toContain("этот ПК · первый сканер");
  fireEvent.click(screen.getByText("Сканировать"));
  await waitFor(() => expect(onJob).toHaveBeenCalledWith("j7"));
  expect(start).toHaveBeenCalledWith(undefined, undefined);
});

it("the line shows «ПК · сканер», «Сменить сканер» lists PCs with scanners and the pick is saved to the account", async () => {
  account({ "scan.device": { agent_id: "m-2", scanner_id: "s-9" } });
  render(<ScanDeviceLine />);
  await waitFor(() => expect(screen.getByTestId("scan-device").textContent).toContain("OFFICE · Canon LiDE"));
  fireEvent.click(screen.getByText("Сменить сканер"));
  expect(await screen.findByRole("dialog", { name: "Выбор сканера" })).toBeTruthy();
  fireEvent.click(screen.getByText("SKLAD · Epson V39"));
  await waitFor(() => expect(saved).toEqual([["scan.device", { agent_id: "m-1", scanner_id: "s-2" }]]));
  expect(screen.getByTestId("scan-device").textContent).toContain("SKLAD · Epson V39");
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("without the «scan» right the line (and so the scanner choice) is hidden; with it, shown", () => {
  const principal = (permissions: Record<string, "view" | "edit" | "none">, level = "operator") =>
    ({ user: { id: "u", email: "", name: "", department: "" }, roles: [level], access_level: level, permissions }) as Principal;
  setRightsPrincipal(principal({ scan: "view" }));
  const { rerender } = render(<ScanDeviceLine />);
  expect(screen.queryByTestId("scan-device")).toBeNull();
  setRightsPrincipal(principal({ scan: "edit" }));
  rerender(<ScanDeviceLine />);
  expect(screen.getByTestId("scan-device")).toBeTruthy();
  setRightsPrincipal(principal({}, "superadmin"));
  rerender(<ScanDeviceLine />);
  expect(screen.getByTestId("scan-device")).toBeTruthy();
});
