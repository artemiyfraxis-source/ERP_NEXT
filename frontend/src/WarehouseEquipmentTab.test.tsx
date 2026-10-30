import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WarehousePage } from "./WarehousePage";
import { ALIAS_VIEW, NAV, sectionOfTab } from "./navigation";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); window.history.replaceState({}, "", "/"); });

function api() {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.split("?")[0] === "/api/stock") return json({ lots: [], total: 0, truncated: 0, next_cursor: null });
    if (path === "/api/devices/scanner-log") return json({ lines: [] });
    if (path === "/api/devices/client-errors") return json({ errors: [] });
    return json({}, 200);
  });
}

it("«Оборудование» is a sub-tab of «Склад»: the stock view is the default, the tab shows the ТСД card", async () => {
  api();
  render(<WarehousePage equipment />);
  expect(await screen.findByRole("button", { name: /Внести позиции/ })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "ТСД" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Оборудование" }));
  expect(await screen.findByRole("region", { name: "ТСД" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Склад" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Материалы и комплектующие" }));
  expect(await screen.findByRole("button", { name: /Внести позиции/ })).toBeInTheDocument();
});

it("without the access the sub-tab is absent and a link to it falls back to the stock", async () => {
  api();
  window.history.replaceState({}, "", "/?section=stock&view=equipment");
  render(<WarehousePage />);
  expect(await screen.findByRole("button", { name: /Внести позиции/ })).toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: "Оборудование" })).not.toBeInTheDocument();
});

it("the old link ?section=equipment lands on «Склад» with the «Оборудование» sub-tab open", async () => {
  expect(sectionOfTab("equipment", null)).toBe("stock");
  expect(ALIAS_VIEW.equipment).toBe("equipment");
  expect(NAV.some(({ id }) => (id as string) === "equipment")).toBe(false);
  api();
  window.history.replaceState({}, "", "/?section=stock&view=equipment");
  render(<WarehousePage equipment />);
  expect(await screen.findByRole("region", { name: "ТСД" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Оборудование" })).toHaveAttribute("aria-selected", "true");
});
