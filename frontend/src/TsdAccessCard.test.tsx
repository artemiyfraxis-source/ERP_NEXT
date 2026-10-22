import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { EquipmentPage } from "./EquipmentPage";
import { resetStageOptions } from "./StageOptions";
import { TsdAccessCard } from "./TsdAccessCard";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); resetStageOptions(); });

function api(extra: Record<string, () => Response> = {}) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const route = extra[`${init?.method ?? "GET"} ${path}`];
    if (route) return route();
    if (path === "/api/stage-options") return json({ available: true, options: [] });
    if (path === "/api/devices/scanner-log") return json({ lines: ["--- 20.10.2026 10:00:00 192.168.1.50 Петров · Chrome/70", "   120ms keydown key=\"Enter\""] });
    if (path === "/api/devices/client-errors") return json({ errors: [] });
    return json({ detail: `Unexpected request: ${path}` }, 500);
  });
}

it("the card shows the page's own address with a QR, the PIN hint and no switch", async () => {
  api();
  render(<TsdAccessCard />);
  const card = await screen.findByRole("region", { name: "ТСД" });
  const address = `${window.location.origin}/tsd`;
  expect(within(card).getByText(address)).toBeInTheDocument();
  expect(within(card).getByAltText(`QR ${address}`).getAttribute("src")).toBe(`/api/devices/qr.svg?url=${encodeURIComponent(address)}`);
  expect(card).toHaveTextContent("Вход на ТСД — по PIN сотрудника («Сотрудники» → PIN). Кладовщику нужен раздел «Склад».");
  expect(within(card).queryByRole("switch")).not.toBeInTheDocument();
  expect(card).not.toHaveTextContent("Python");
  expect(await within(card).findByText(/keydown key="Enter"/)).toBeInTheDocument();
  expect(await within(card).findByText("Ошибок не было.")).toBeInTheDocument();
});

it("device browser errors are listed", async () => {
  api({
    "GET /api/devices/client-errors": () => json({ errors: [{ at: "20.10.2026 09:00:01", ip: "192.168.1.50", url: "/?section=tsd", message: "TypeError: x is not a function", browser: "Chrome/70" }] }),
  });
  render(<TsdAccessCard />);
  const errors = await screen.findByRole("table", { name: "Ошибки браузера устройства" });
  expect(errors).toHaveTextContent("TypeError: x is not a function");
});

it("«Оборудование» always has the ТСД card; the printer card needs its option", async () => {
  api({
    "GET /api/stock/label-settings": () => json({ detail: "нет" }, 404),
    "GET /api/stock/label-printers": () => json({ printers: [], on_windows: false }),
  });
  render(<EquipmentPage admin />);
  expect(await screen.findByRole("region", { name: "ТСД" })).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByRole("region", { name: "Печать этикеток ГРН" })).not.toBeInTheDocument());
});
