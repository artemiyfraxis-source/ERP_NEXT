import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { DevicesStatus } from "./devicesApi";
import { EquipmentPage } from "./EquipmentPage";
import { resetStageOptions } from "./StageOptions";
import { TsdAccessCard } from "./TsdAccessCard";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

// Options must be forgotten after the components that read them are gone.
afterEach(() => { cleanup(); vi.restoreAllMocks(); resetStageOptions(); });

const OFF: DevicesStatus = { enabled: false, listening: false, port: 8031, api_port: 8030, addresses: [], https: false, reason: "", host: "desktop-x" };
const ON: DevicesStatus = {
  ...OFF, enabled: true, listening: true, https: true,
  addresses: [{ url: "http://192.168.1.124:8031", label: "сеть офиса" }, { url: "http://100.81.148.15:8031", label: "Tailscale" }],
};

type Call = { method: string; path: string; body?: unknown };

function stage(enabled: string[], status: DevicesStatus = OFF, extra: Record<string, () => Response> = {}) {
  const calls: Call[] = [];
  let current = status;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const call = { method: init?.method ?? "GET", path: String(input), body: init?.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    if (call.path === "/api/stage-options") {
      return json({ available: true, options: ["warehouse.labels", "warehouse.tsd"].map((key) => (
        { key, block: "Склад", title: "t", before: "", after: "", enabled: enabled.includes(key) })) });
    }
    const route = extra[`${call.method} ${call.path}`];
    if (route) return route();
    if (call.path === "/api/devices/status") return json(current);
    if (call.method === "PUT" && call.path === "/api/devices/network") {
      current = (call.body as { enabled: boolean }).enabled ? ON : OFF;
      return json(current);
    }
    if (call.path === "/api/devices/scanner-log") return json({ lines: ["--- 20.10.2026 10:00:00 192.168.1.50 Петров · Chrome/70", "   120ms keydown key=\"Enter\""] });
    if (call.path === "/api/devices/client-errors") return json({ errors: [] });
    return json({ detail: `Unexpected request: ${call.method} ${call.path}` }, 500);
  });
  return calls;
}

it("the administrator lets devices in: the addresses come with a QR to the ТСД screen and the https hint", async () => {
  const calls = stage(["warehouse.tsd"]);
  render(<TsdAccessCard admin />);
  const card = await screen.findByRole("region", { name: "ТСД и вход с устройств" });
  expect(await within(card).findByRole("status")).toHaveTextContent("Сейчас ERP открывается только на этом компьютере.");
  const toggle = within(card).getByRole("switch", { name: "Пускать устройства из сети (Wi-Fi офиса, Tailscale)" });
  expect(toggle).not.toBeChecked();
  fireEvent.click(toggle);
  await waitFor(() => expect(within(card).getByRole("status")).toHaveTextContent("Устройства пускаются: порт 8031, http:// и https://."));
  expect(calls.find((call) => call.method === "PUT")?.body).toEqual({ enabled: true });
  const qr = within(card).getByAltText("QR http://192.168.1.124:8031/tsd");
  expect(qr.getAttribute("src")).toBe(`/api/devices/qr.svg?url=${encodeURIComponent("http://192.168.1.124:8031/tsd")}`);
  expect(within(card).getByText("через Tailscale (нужно приложение на устройстве)")).toBeInTheDocument();
  expect(card).toHaveTextContent("«Дополнительные» → «Перейти на сайт», один раз на устройство");
  expect(card).toHaveTextContent("Вход на ТСД — по PIN сотрудника («Сотрудники» → PIN)");
  expect(card).toHaveTextContent("Windows может спросить, пускать ли Python в сеть");
  expect(within(card).getByText(/keydown key="Enter"/)).toBeInTheDocument();
  expect(within(card).getByText("Ошибок не было.")).toBeInTheDocument();
});

it("not an administrator: the state and addresses, the switch is not theirs", async () => {
  stage(["warehouse.tsd"], { ...ON, https: false, reason: "openssl не найден (ставится с Git для Windows) — устройства откроют только http://" });
  render(<TsdAccessCard admin={false} />);
  const card = await screen.findByRole("region", { name: "ТСД и вход с устройств" });
  expect(await within(card).findByRole("status")).toHaveTextContent("порт 8031, только http://.");
  expect(within(card).getByRole("switch")).toBeDisabled();
  expect(card).toHaveTextContent("Переключает администратор.");
  expect(card).toHaveTextContent("https:// не работает: openssl не найден");
  expect(card).not.toHaveTextContent("«Дополнительные» → «Перейти на сайт»");
});

it("a gate that does not listen says why; device errors are listed", async () => {
  stage(["warehouse.tsd"], { ...OFF, enabled: true, reason: "19.10.2026 10:00:00 порт 8031 занят другой программой" }, {
    "GET /api/devices/client-errors": () => json({ errors: [{ at: "20.10.2026 09:00:01", ip: "192.168.1.50", url: "http://192.168.1.124:8031/?section=tsd", message: "TypeError: x is not a function", browser: "Chrome/70" }] }),
  });
  render(<TsdAccessCard admin />);
  const card = await screen.findByRole("region", { name: "ТСД и вход с устройств" });
  expect(await within(card).findByRole("status")).toHaveTextContent("Устройства не пускаются: 19.10.2026 10:00:00 порт 8031 занят другой программой");
  const errors = await within(card).findByRole("table", { name: "Ошибки браузера устройства" });
  expect(errors).toHaveTextContent("TypeError: x is not a function");
});

it("in «Оборудование» the card is there only with its option", async () => {
  stage(["warehouse.labels"], OFF, {
    "GET /api/stock/label-settings": () => json({ detail: "нет" }, 404),
    "GET /api/stock/label-printers": () => json({ printers: [], on_windows: false }),
  });
  render(<EquipmentPage admin />);
  await screen.findByRole("heading", { name: "Оборудование" });
  await waitFor(() => expect(screen.queryByRole("region", { name: "ТСД и вход с устройств" })).not.toBeInTheDocument());
  cleanup();
  resetStageOptions();
  vi.restoreAllMocks();
  stage(["warehouse.tsd"]);
  render(<EquipmentPage admin />);
  expect(await screen.findByRole("region", { name: "ТСД и вход с устройств" })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Печать этикеток ГРН" })).not.toBeInTheDocument();
});
