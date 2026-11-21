import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { MachineRow } from "./machinesApi";
import { MachinesPage } from "./MachinesPage";
import { MfpCardView } from "./WarehouseDeviceCard";
import type { LabelPrinterView, MfpView } from "./warehouseDevicesApi";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { vi.restoreAllMocks(); });

const BASE = {
  equipment_id: null, inventory: "", pc: "DESKTOP-F40DLD9", ip: "192.168.1.50", service: "Агент ERP · печать, приборы",
  version: "2.12.0", link: { code: "ok", text: "На связи", tone: "ok" as const }, last_seen: "2026-11-20T10:00:00+03:00",
  card: false, others: "", article: "", site: "", operations: [], family: "Принтеры и МФУ", controlled: true,
};
const TSC: MachineRow = { ...BASE, key: "printer:pc:TSC MB341", kind: "printer", name: "Принтер этикеток TSC MB341", device: "TSC MB341",
  state: { text: "Принтер виден", tone: "ok" }, station: { kind: "labels" } };
const MFP: MachineRow = { ...BASE, key: "device:pc:scan:wia", kind: "device", name: "МФУ Pantum BM2300", device: "Pantum BM2300 Series Scanner",
  state: { text: "Подключён", tone: "ok" }, station: { kind: "mfp", machine_id: "pc", scanners: ["wia"], printers: ["Pantum BM2300 Series"] } };

const LABELS: LabelPrinterView = {
  now: "2026-11-20T10:00:05+03:00", online: true,
  agent: { last_seen: "2026-11-20T10:00:03+03:00", host: "DESKTOP-F40DLD9", printer: "TSC MB341", queue: 0, last_error: null },
  jobs: [
    { id: "j1", kind: "lot", what: "ГРН 1234/2", article: "DF13-2S", name: "Разъём Hirose", warehouse: "ЦАР", lot_id: "lot-1", copies: 2,
      by: "Кладовщик", at: "2026-11-20T09:58:00+03:00", status: "printed", finished_at: "2026-11-20T09:58:05+03:00", error: "" },
    { id: "j2", kind: "sample", what: "Пробная этикетка", article: "", name: "", warehouse: "", lot_id: null, copies: 1,
      by: "Кладовщик", at: "2026-11-20T09:50:00+03:00", status: "failed", finished_at: null, error: "Нет бумаги" },
  ],
};

function mfp(overrides: Partial<MfpView> = {}): MfpView {
  return {
    pc: { id: "pc", name: "DESKTOP-F40DLD9", version: "2.12.0", last_seen: "2026-11-20T10:00:00+03:00" }, online: true,
    scanners: [{ id: "wia", name: "Pantum BM2300 Series Scanner", present: true }],
    printers: [{ name: "Pantum BM2300 Series", driver: "Pantum BM2300 Series", present: true }],
    scans: [{ id: "s1", status: "done", error: "", by: "Кладовщик", at: "2026-11-20T09:40:00+03:00", finished_at: "2026-11-20T09:40:20+03:00",
      color: "gray", dpi: 300, pages: 2, append_to: null, scanner: "wia", file: "/api/stock/devices/scans/s1/file" }],
    prints: [{ id: "p1", title: "Паспорт.pdf", source: "upload", pages: 3, copies: 2, printer: "Pantum BM2300 Series", status: "pending",
      error: "", by: "Кладовщик", at: "2026-11-20T09:45:00+03:00", finished_at: null }],
    ...overrides,
  };
}

function page(calls: { url: string; init?: RequestInit }[]) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = decodeURIComponent(String(input));
    calls.push({ url, init });
    if (url.endsWith("/api/stock/devices/labels")) return json(LABELS);
    if (url.includes("/label") && init?.method === "POST") return json({ status: "queued" });
    if (url.endsWith("/api/stock/devices/mfp/pc")) return json(mfp());
    return json({ now: "2026-11-20T10:00:05+03:00", rows: [TSC, MFP], families: ["Принтеры и МФУ"] });
  });
}

it("names the warehouse devices by what they are and opens the label printer card with recent jobs and reprint", async () => {
  const calls: { url: string; init?: RequestInit }[] = [];
  page(calls);
  render(<MachinesPage />);
  const printers = await screen.findByRole("region", { name: "Принтеры и МФУ" });
  expect(within(printers).queryByText("Без номера")).toBeNull();
  expect(within(printers).getByText("МФУ Pantum BM2300")).toBeTruthy();
  fireEvent.click(within(printers).getByText("Принтер этикеток TSC MB341"));
  const card = await screen.findByRole("dialog", { name: "Принтер этикеток TSC MB341" });
  expect(await within(card).findByText("Готов")).toBeTruthy();
  expect(within(card).getByText("Нет бумаги")).toBeTruthy();
  fireEvent.click(within(card).getByRole("button", { name: "Пробная этикетка" }));
  await waitFor(() => expect(calls.some((call) => call.url.endsWith("/api/stock/labels/test") && call.init?.method === "POST")).toBe(true));
  expect(await within(card).findByText(/Пробная этикетка отправлена/)).toBeTruthy();

  expect(within(card).queryByRole("button", { name: "Перепечатать" })).toBeNull();
  fireEvent.click(within(card).getByText("ГРН 1234/2"));
  expect(within(card).getByRole("spinbutton", { name: "Копий" })).toHaveValue(2);
  fireEvent.click(within(card).getByRole("button", { name: "Перепечатать" }));
  await waitFor(() => {
    const reprint = calls.find((call) => call.url.endsWith("/api/stock/batches/lot-1/label"));
    expect(reprint?.init?.body).toBe(JSON.stringify({ copies: 2 }));
  });
});

it("opens the MFP card from its tile", async () => {
  page([]);
  render(<MachinesPage />);
  fireEvent.click(await screen.findByText("МФУ Pantum BM2300"));
  const card = await screen.findByRole("dialog", { name: "МФУ Pantum BM2300" });
  expect(await within(card).findByRole("button", { name: "Сканировать" })).toBeTruthy();
  expect(within(card).getByText("Печатает «Паспорт.pdf»")).toBeTruthy();
});

it("MFP: plain scan with colour and resolution, the last scan to open, print waits for a file", async () => {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    calls.push({ url: String(input), init });
    return json({ id: "s2", status: "pending" });
  });
  const reload = vi.fn();
  render(<MfpCardView view={mfp({ prints: [] })} reload={reload} machineId="pc" />);
  expect(within(screen.getByRole("status", { name: "Состояние МФУ" })).getByText("Готово")).toBeTruthy();
  fireEvent.click(screen.getByRole("radio", { name: "Цветной" }));
  fireEvent.click(screen.getByRole("radio", { name: "600 dpi" }));
  fireEvent.click(screen.getByRole("button", { name: "Сканировать" }));
  await waitFor(() => expect(calls[0]?.url).toBe("/api/stock/devices/mfp/pc/scan"));
  expect(JSON.parse(String(calls[0].init?.body))).toEqual({ scanner: "wia", color: "color", dpi: 600 });
  await waitFor(() => expect(reload).toHaveBeenCalled());

  fireEvent.click(screen.getByRole("button", { name: /Добавить страницу к скану/ }));
  await waitFor(() => expect(JSON.parse(String(calls[1]?.init?.body)).append_to).toBe("s1"));

  const last = screen.getByLabelText("Последний скан");
  expect(within(last).getByRole("link", { name: "Открыть" }).getAttribute("href")).toBe("/api/stock/devices/scans/s1/file");
  expect(within(last).getByRole("link", { name: "Скачать PDF" }).getAttribute("href")).toBe("/api/stock/devices/scans/s1/file?download=1");
  const print = screen.getByRole("button", { name: "Печатать — выберите файл" }) as HTMLButtonElement;
  expect(print.disabled).toBe(true);
  fireEvent.change(screen.getByLabelText("Файл для печати"), { target: { files: [new File(["%PDF-1.4"], "Паспорт.pdf", { type: "application/pdf" })] } });
  fireEvent.click(screen.getByRole("button", { name: "Печатать" }));
  await waitFor(() => expect(calls.some((call) => call.url.startsWith("/api/stock/devices/mfp/pc/print?printer=Pantum+BM2300+Series&copies=1&idempotency_key="))).toBe(true));
});

it("MFP: a retry after a lost answer keeps the key, a new print after success gets a new one", async () => {
  const keys: Record<string, string[]> = { print: [], test: [], copy: [] };
  let lose = true;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes("/print?")) keys.print.push(new URL(url, "http://x").searchParams.get("idempotency_key") ?? "");
    if (url.endsWith("/print-test")) keys.test.push(JSON.parse(String(init?.body)).idempotency_key);
    if (url.endsWith("/scans/s1/print")) keys.copy.push(JSON.parse(String(init?.body)).idempotency_key);
    if (lose) throw new TypeError("Failed to fetch");
    return json({ id: "p2", status: "pending" });
  });
  render(<MfpCardView view={mfp({ prints: [] })} reload={() => undefined} machineId="pc" />);
  const passport = new File(["%PDF-1.4"], "Паспорт.pdf", { type: "application/pdf", lastModified: 1 });
  fireEvent.change(screen.getByLabelText("Файл для печати"), { target: { files: [passport] } });
  fireEvent.click(screen.getByRole("button", { name: "Печатать" }));
  await waitFor(() => expect(keys.print).toHaveLength(1));
  await waitFor(() => expect((screen.getByRole("button", { name: "Пробная страница" }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "Пробная страница" }));
  await waitFor(() => expect(keys.test).toHaveLength(1));
  lose = false;
  fireEvent.click(screen.getByRole("button", { name: "Печатать" }));
  await waitFor(() => expect(keys.print).toHaveLength(2));
  expect(keys.print[1]).toBe(keys.print[0]);
  expect(keys.print[0]).toMatch(/\S/);
  fireEvent.click(screen.getByRole("button", { name: "Пробная страница" }));
  await waitFor(() => expect(keys.test).toHaveLength(2));
  expect(keys.test[1]).toBe(keys.test[0]);
  fireEvent.click(screen.getByRole("button", { name: "Пробная страница" }));
  await waitFor(() => expect(keys.test).toHaveLength(3));
  expect(keys.test[2]).not.toBe(keys.test[1]);

  fireEvent.click(screen.getByRole("button", { name: "Напечатать копию" }));
  await waitFor(() => expect(keys.copy).toHaveLength(1));
  fireEvent.click(await screen.findByRole("button", { name: "Напечатать копию" }));
  await waitFor(() => expect(keys.copy).toHaveLength(2));
  expect(keys.copy[1]).not.toBe(keys.copy[0]);
});

it("MFP: an old agent says what to update, and no link stops both actions", () => {
  const { rerender } = render(<MfpCardView view={mfp({ printers: [], prints: [], pc: { id: "pc", name: "DESKTOP-F40DLD9", version: "2.11.1", last_seen: null } })}
    reload={() => undefined} machineId="pc" />);
  expect(screen.getByText("Готово к скану · печать не настроена")).toBeTruthy();
  expect(screen.getByText("Обновите агент ERP на ПК DESKTOP-F40DLD9 до 2.12.0 — тогда ERP увидит принтер МФУ")).toBeTruthy();
  expect(screen.getByText("Печатать нельзя: принтер МФУ не найден")).toBeTruthy();
  rerender(<MfpCardView view={mfp({ online: false })} reload={() => undefined} machineId="pc" />);
  expect(screen.getByText("Нет связи с ПК").className).toContain("tone-danger");
  expect((screen.getByRole("button", { name: "Сканировать" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText("Сканировать нельзя: нет связи с ПК")).toBeTruthy();
});
