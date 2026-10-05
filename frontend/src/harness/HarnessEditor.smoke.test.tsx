import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Suspense } from "react";
import { beforeAll, expect, it, vi } from "vitest";
import { HarnessEditorLazy, loadHarnessAdapters } from "./index";

beforeAll(() => {
  // jsdom has neither ResizeObserver nor a 2D canvas; the editor draws nothing here, only the DOM is checked.
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
});

// Smoke test of the ported editor running from our code: lazy entry → in-memory adapters → autosave.
it("renders the harness editor and autosaves a placed connector as snapshot v1 into the in-memory store", async () => {
  const { createInMemoryHarnessDesignApi } = await loadHarnessAdapters();
  const api = createInMemoryHarnessDesignApi();
  const { container } = render(<Suspense fallback={<div>Загрузка редактора…</div>}>
    <HarnessEditorLazy projectId="p-1" harnessId="h-1" harnessDesignation="Ж-001" initialView="e4" apiOverride={api} />
  </Suspense>);

  await waitFor(() => expect(container.querySelector(".harness-root .he-host")).not.toBeNull(), { timeout: 10_000 });
  expect(screen.queryByText("Загружаем документ жгута…")).toBeNull();

  const catalog = screen.getByRole("region", { name: "Каталог объектов" });
  const toggle = within(catalog).getByRole("button", { name: /Объекты и материалы/ });
  if (toggle.getAttribute("aria-expanded") !== "true") fireEvent.click(toggle);
  fireEvent.click(within(catalog).getByRole("button", { name: "Соединители" }));
  fireEvent.doubleClick(await within(catalog).findByText("Свободный соединитель"));

  await waitFor(() => {
    const stored = api.exportSnapshot("p-1", "h-1");
    expect(stored).not.toBeNull();
    const snapshot = JSON.parse(stored!);
    expect(snapshot).toMatchObject({ harnessId: "h-1", schemaVersion: 1, revision: 1 });
    expect(snapshot.content.connectors).toHaveLength(1);
    expect(snapshot.content.connectors[0].designation).toBe("XS1");
  }, { timeout: 10_000 });

  // Ctrl+Z right after the save removes the connector, and the next autosave stores that (revision 2).
  fireEvent.keyDown(document.body, { key: "z", ctrlKey: true });
  await waitFor(() => {
    const snapshot = JSON.parse(api.exportSnapshot("p-1", "h-1")!);
    expect(snapshot.revision).toBe(2);
    expect(snapshot.content.connectors).toHaveLength(0);
  }, { timeout: 10_000 });

  // The drawing view renders without tripping the editor's error boundary.
  fireEvent.click(screen.getByRole("tab", { name: "Чертёж" }));
  await waitFor(() => expect(screen.getByRole("tab", { name: "Чертёж" }).getAttribute("aria-selected")).toBe("true"));
  expect(screen.queryByText("Редактор не смог отобразить последнее изменение.")).toBeNull();
  expect(screen.queryByText(/Маршрут v2|UML/)).toBeNull();
}, 30_000);
