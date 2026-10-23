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

it("has two buttons only, no «Как в системе»", () => {
  render(<ThemeSwitch />);
  expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["Светлая", "Тёмная"]);
  expect(screen.queryByRole("button", { name: "Как в системе" })).toBeNull();
});

it("a saved «system» reads as light and is migrated, even when the OS is dark", async () => {
  vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener: () => {}, removeEventListener: () => {} }));
  window.localStorage.setItem("erp-theme", "system");
  render(<ThemeSwitch />);
  await waitFor(() => expect(document.documentElement.dataset.theme).toBe("light"));
  expect(window.localStorage.getItem("erp-theme")).toBe("light");
  expect(screen.getByRole("button", { name: "Светлая" })).toHaveAttribute("aria-pressed", "true");
});
