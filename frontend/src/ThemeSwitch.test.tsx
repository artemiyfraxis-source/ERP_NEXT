import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ThemeSwitch } from "./ThemeSwitch";

beforeEach(() => { window.localStorage.clear(); delete document.documentElement.dataset.theme; });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("«Тёмная» sets data-theme and remembers the choice", async () => {
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
  render(<ThemeSwitch />);
  fireEvent.click(await screen.findByRole("button", { name: "Как в системе" }));
  expect(document.documentElement.dataset.theme).toBe("dark");
  query.matches = false;
  listener!();
  await waitFor(() => expect(document.documentElement.dataset.theme).toBe("light"));
  expect(window.localStorage.getItem("erp-theme")).toBe("system");
});
