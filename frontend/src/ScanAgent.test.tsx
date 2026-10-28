import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError, warehouseApi, type WarehouseScanStart } from "./api";
import { rememberedScanAgent, useScanAgent } from "./ScanAgent";

const AGENTS = [
  { id: "m-1", pc: "SKLAD", scanners: ["Pantum BM2300W"], version: "2.0.0" },
  { id: "m-2", pc: "OFFICE", scanners: ["Canon LiDE"], version: "2.0.0" },
];

/** «Сканировать» on the shared hook: the first press, and what it did with the answer. */
function Harness({ start, onJob }: { start: (agentId?: string) => Promise<WarehouseScanStart>; onJob: (id: string | null) => void }) {
  const agent = useScanAgent(start);
  const press = () => void agent.begin().then((job) => onJob(job ? job.id : null));
  return (<><button onClick={press}>Сканировать</button>{agent.dialogs(press)}</>);
}

beforeEach(() => window.localStorage.clear());
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("a job from the server goes straight on, the remembered agent is sent along", async () => {
  window.localStorage.setItem("erp.scanAgent", "m-2");
  const start = vi.fn(async () => ({ id: "j1", status: "pending" as const }));
  const onJob = vi.fn();
  render(<Harness start={start} onJob={onJob} />);
  fireEvent.click(screen.getByText("Сканировать"));
  await waitFor(() => expect(onJob).toHaveBeenCalledWith("j1"));
  expect(start).toHaveBeenCalledWith("m-2");
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
  expect(start).toHaveBeenLastCalledWith("m-2");
});

it("the picker opens the install guide for this PC", async () => {
  vi.spyOn(warehouseApi, "scanAgentInstallToken").mockResolvedValue({ command: "cmd-1", expires_at: "", valid_minutes: 60 });
  render(<Harness start={async () => ({ status: "choose" as const, agents: AGENTS })} onJob={() => undefined} />);
  fireEvent.click(screen.getByText("Сканировать"));
  fireEvent.click(await screen.findByText("Установить на этом ПК"));
  expect(await screen.findByRole("dialog", { name: "Установка сканера" })).toBeTruthy();
});
