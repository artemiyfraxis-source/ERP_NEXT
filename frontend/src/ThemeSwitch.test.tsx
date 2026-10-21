import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { resetStageOptions } from "./StageOptions";
import { ThemeSwitch } from "./ThemeSwitch";

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}
const OPTION = { key: "ui.dark_theme", block: "Интерфейс", title: "Тёмная тема", before: "Светлая", after: "Переключатель", enabled: false };

function serve(enabled: boolean) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ available: true, options: [{ ...OPTION, enabled }] }));
}

beforeEach(() => { window.localStorage.clear(); delete document.documentElement.dataset.theme; });
afterEach(() => { cleanup(); resetStageOptions(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("renders nothing and keeps the light theme while the option is off", async () => {
  window.localStorage.setItem("erp-theme", "dark");
  document.documentElement.dataset.theme = "dark";
  serve(false);
  const { container } = render(<ThemeSwitch />);
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
  expect(document.documentElement.dataset.theme).toBeUndefined();
});

it("«Тёмная» sets data-theme and remembers the choice", async () => {
  serve(true);
  render(<ThemeSwitch />);
  fireEvent.click(await screen.findByRole("button", { name: "Тёмная" }));
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(window.localStorage.getItem("erp-theme")).toBe("dark");
  expect(screen.getByRole("button", { name: "Тёмная" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(screen.getByRole("button", { name: "Светлая" }));
  expect(document.documentElement.dataset.theme).toBe("light");
});

it("«Как в системе» follows prefers-color-scheme live", async () => {
  let listener: (() => void) | null = null;
  const query = {
    matches: true,
    addEventListener: (_: string, fn: () => void) => { listener = fn; },
    removeEventListener: () => { listener = null; },
  };
  vi.stubGlobal("matchMedia", () => query);
  serve(true);
  render(<ThemeSwitch />);
  fireEvent.click(await screen.findByRole("button", { name: "Как в системе" }));
  expect(document.documentElement.dataset.theme).toBe("dark");
  query.matches = false;
  listener!();
  await waitFor(() => expect(document.documentElement.dataset.theme).toBe("light"));
  expect(window.localStorage.getItem("erp-theme")).toBe("system");
});
