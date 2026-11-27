import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AnalyserCard, arinstStatus } from "./AnalyserCard";
import type { MachineRow } from "./machinesApi";
import { MachinesPage } from "./MachinesPage";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const PLANAR: MachineRow = {
  key: "device:instrument:si2", kind: "device", equipment_id: null, inventory: "СИ.002", name: "Анализатор цепей векторный", pc: "DESKTOP-PTA57QF",
  ip: "192.168.1.121", service: "Агент ERP · приборы", version: "2.11.1", link: { code: "ok", text: "Подключён", tone: "ok" },
  last_seen: "2026-11-20T10:00:00+03:00", device: "Planar, S50180, 26030095, 26.2.2/2", state: { text: "Подключён", tone: "ok" },
  card: false, others: "DESKTOP-5O46RH6 — не найден", article: "S50180", site: "", operations: [], family: "Анализаторы цепей",
  controlled: true, vna: "s2vna", instrument_id: "si-002",
};
const ARINST: MachineRow = {
  ...PLANAR, key: "device:instrument:si1", inventory: "СИ.001", name: "Arinst VNA-DL 1–8800 МГц", device: "Arinst VNA-DL",
  state: { text: "Не найден", tone: "warn" }, others: "", article: "VNA-DL", vna: "arinst", instrument_id: "si-001",
};
const BENCH = {
  device: { pc_name: "DESKTOP-PTA57QF", agent_online: true, agent_version: "2.11.1", instrument_number: "СИ.002", instrument_id: "si-002",
    model: "Planar S50180", serial: "26030095", s2vna_ready: true, warmup_min: 60, temperatures: null, acm: null, refusal: null, read_at: null },
  calibration: { valid: false, calibrated_at: null, valid_until: null, reason: "Калибровки нет", temperatures: null }, due: [], session: null,
};
const FILE = { id: "job-1", file_name: "Кабель 001.s1p", source: "arinst", finished_at: "2026-11-20T09:58:00+03:00", pc: "DESKTOP-PTA57QF",
  capture_settings: { points: 1001, start_hz: 1e6, stop_hz: 8.8e9 } };

function serve(calls: string[], { captures = [FILE] as unknown[], bench = BENCH as unknown } = {}) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = decodeURIComponent(String(input));
    calls.push(`${init?.method ?? "GET"} ${url}`);
    if (url === "/api/production/vna-bench/state") return json(bench);
    if (url === "/api/production/vna-bench/state?instrument_id=si-001") return json({
      source: "arinst", device: { instrument_id: "si-001", control_ready: true, supported_ops: ["connect", "close"],
        reason: null, license: { state: "not_required", message: "Связь: протокол ArinstVL" } },
      profile: null, calibration: { valid: false, status: "required" }, acceptance: { eligible: false, message: "Не введён в эксплуатацию" }, session: null, latest_trace: null,
    });
    if (url.startsWith("/api/production/vna-bench/screens")) return json({ screens: [] });
    if (url === "/api/production/instruments") return json({ instruments: [] });
    if (url.startsWith("/api/stock/vna/jobs?")) return json({ captures });
    if (url === "/api/stock/vna/jobs" && init?.method === "POST") {
      return json({ id: "job-2", status: "done", error: "", vna: { name: "Arinst VNA-DL" }, source: "arinst", file_name: "Кабель 002.s1p",
        instrument_id: "si-001", capture_settings: null, agent: { id: "otk", pc: "DESKTOP-PTA57QF", version: "2.11.1", vna: null } });
    }
    return json({ now: "2026-11-20T10:00:02+03:00", rows: [PLANAR, ARINST], families: ["Анализаторы цепей"] });
  });
}

beforeEach(() => window.history.replaceState({}, "", "/?section=machines"));
afterEach(() => vi.restoreAllMocks());

it("opens the VNA workplace in the Planar card, keeps the passport folded in «Подключение» and the link in the address", async () => {
  const calls: string[] = [];
  serve(calls);
  render(<MachinesPage canQuality />);
  fireEvent.click(await screen.findByText("СИ.002"));
  const card = await screen.findByRole("dialog", { name: /СИ\.002/ });
  expect(await within(card).findByText("Калибровки нет")).toBeTruthy();
  expect(within(card).getByRole("button", { name: "Открыть сеанс" })).toBeEnabled();
  expect(within(card).getByRole("tab", { name: "Экраны" })).toBeTruthy();
  const connection = within(card).getByText("Подключение").closest("details")!;
  expect(connection.open).toBe(false);
  expect(within(connection).getByText("Planar, S50180, 26030095, 26.2.2/2")).toBeTruthy();
  expect(within(connection).queryByText("DESKTOP-5O46RH6 — не найден")).toBeNull();
  expect(new URLSearchParams(window.location.search).get("tab")).toBe("vna");
  fireEvent.click(within(card).getByRole("button", { name: "Закрыть" }));
  expect(new URLSearchParams(window.location.search).get("tab")).toBeNull();
});

it.each([true, false])("header and connection use the bound agent when its online state is %s", async (online) => {
  serve([], { bench: { ...BENCH, device: { ...BENCH.device, pc_name: "DESKTOP-5O46RH6", bound_pc: "DESKTOP-5O46RH6",
    agent_online: online, agent_version: "2.12.0", agent_ip: "192.168.1.99", vna_online: false, serial: null, s2vna_ready: null,
    last_seen_pc: "DESKTOP-PTA57QF", last_seen_at: "2026-11-20T16:09:00+03:00",
    refusal: { code: online ? "vna_not_connected" : "agent_offline", message: "ВАЦ не подключён. Последний раз виден на DESKTOP-PTA57QF. Включите ВАЦ, подключите USB и запустите S2VNA." },
  } } });
  render(<AnalyserCard row={PLANAR} benchNumber="СИ.002" canQuality close={() => {}} />);
  expect(await screen.findByText(online ? "Агент на связи" : "Агент не на связи")).toBeTruthy();
  const connection = screen.getByText("Подключение").closest("details")!;
  expect(connection.querySelector("summary")).toHaveTextContent(`связь с агентом: ${online ? "На связи" : "Нет связи"}`);
  expect(connection.querySelector("summary")).toHaveTextContent("ПК DESKTOP-5O46RH6");
  expect(within(connection).getByText("2.12.0")).toBeTruthy();
  expect(within(connection).getAllByText("DESKTOP-5O46RH6").length).toBeGreaterThan(0);
  expect(within(connection).getByText("192.168.1.99")).toBeTruthy();
  expect(within(connection).getByText(/Последний раз ВАЦ виден .* на DESKTOP-PTA57QF/)).toBeTruthy();
  expect(within(connection).queryByText("192.168.1.121")).toBeNull();
});

it("an old «ОТК → ВАЦ» bookmark opens the analyser card the workplace drives", async () => {
  window.history.replaceState({}, "", "/?section=machines&tab=vna");
  serve([]);
  render(<MachinesPage canQuality />);
  const card = await screen.findByRole("dialog", { name: /СИ\.002/ });
  expect(await within(card).findByText("Калибровки нет")).toBeTruthy();
});

it("without ОТК on edit the Planar card says who runs the workplace and never asks the bench", async () => {
  const calls: string[] = [];
  serve(calls);
  render(<MachinesPage />);
  fireEvent.click(await screen.findByText("СИ.002"));
  const card = await screen.findByRole("dialog", { name: /СИ\.002/ });
  expect(within(card).getByText("Рабочее место ВАЦ открыто сотрудникам с «ОТК» на правку.")).toBeTruthy();
  expect(calls.some((call) => call.includes("/vna-bench/"))).toBe(false);
});

it("the Arinst card says why it is not found, takes the newest ArinstVL file with its own result and lists the last files", async () => {
  const calls: string[] = [];
  serve(calls);
  render(<MachinesPage canQuality />);
  fireEvent.click(await screen.findByText("СИ.001"));
  const card = await screen.findByRole("dialog", { name: /СИ\.001/ });
  expect(within(card).getByRole("tab", { name: "Управление через ERP" })).toHaveAttribute("aria-selected", "true");
  fireEvent.click(within(card).getByRole("tab", { name: "Файл из ArinstVL" }));
  expect(within(card).getByText(/Не найден: на DESKTOP-PTA57QF ArinstVL не запущена/)).toBeTruthy();
  const table = await within(card).findByRole("table", { name: "Последние файлы Arinst" });
  expect(within(table).getByText("Кабель 001.s1p")).toBeTruthy();
  expect(within(table).getByText("1–8 800 МГц")).toBeTruthy();
  expect(within(card).queryByText("Калибровки нет")).toBeNull();  // no Planar workplace on the Arinst
  fireEvent.click(within(card).getByRole("button", { name: "Забрать файл из ArinstVL" }));
  expect(await within(card).findByText(/Получен .*: Кабель 002\.s1p/)).toBeTruthy();
  expect(calls).toContain("POST /api/stock/vna/jobs");
  await waitFor(() => expect(calls.filter((call) => call.startsWith("GET /api/stock/vna/jobs?instrument_id=si-001"))).toHaveLength(2));
});

it("names the Arinst state in operator words from what the agent reports", () => {
  expect(arinstStatus({ ...ARINST, state: { text: "Подключён", tone: "ok" } }).tone).toBe("tone-ok");
  expect(arinstStatus({ ...ARINST, link: { code: "stale", text: "Нет связи с ПК", tone: "danger" } }))
    .toEqual({ text: "Нет связи с агентом на DESKTOP-PTA57QF (Нет связи с ПК)", tone: "tone-danger" });
  expect(arinstStatus({ ...ARINST, state: { text: "Установлена программа", tone: "warn" }, version: "2.9.2" }).text)
    .toContain("агент 2.9.2 старый");
});

it("selects the registered Arinst workplace and moves between fallback tabs using the keyboard", async () => {
  const calls: string[] = []; serve(calls);
  render(<AnalyserCard row={ARINST} benchNumber="СИ.002" canQuality close={() => {}} />);
  expect(await screen.findByText("Связь: протокол ArinstVL")).toBeInTheDocument();
  expect(screen.queryByText(/Пробный режим|Лицензия активна/)).toBeNull();
  expect(calls).toContain("GET /api/production/vna-bench/state?instrument_id=si-001");
  expect(calls).not.toContain("GET /api/production/vna-bench/state");
  const control = screen.getByRole("tab", { name: "Управление через ERP" });
  const file = screen.getByRole("tab", { name: "Файл из ArinstVL" });
  expect(control).toHaveClass("on");
  fireEvent.keyDown(control, { key: "ArrowRight" });
  expect(file).toHaveFocus(); expect(file).toHaveAttribute("aria-selected", "true");
  fireEvent.keyDown(file, { key: "Home" });
  expect(control).toHaveFocus(); expect(control).toHaveAttribute("aria-selected", "true");
});

it("does not request Arinst state or recent files without quality edit permission", () => {
  const calls: string[] = []; serve(calls);
  render(<AnalyserCard row={ARINST} benchNumber="СИ.002" canQuality={false} close={() => {}} />);
  expect(screen.getByText("Рабочее место Arinst открыто сотрудникам с «ОТК» на правку.")).toBeInTheDocument();
  expect(calls).toHaveLength(0);
});
