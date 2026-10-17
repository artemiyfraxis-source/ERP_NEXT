import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Suspense } from "react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

// Ленивый редактор жгута под нагрузкой полного гейта грузится долго — тот же запас, что у тестов режима просмотра.
vi.setConfig({ testTimeout: 45_000 });
import { physicalFixture } from "./editor/physical-topology-fixture";
import { HarnessEditorLazy } from "./index";
import { isPhoneView, PHONE_VIEW_QUERY, pinchCamera } from "./phone-view";
import { createInMemoryHarnessDesignApi, inMemoryEditorPorts } from "./testing/in-memory";
import type { HarnessDocumentsSource } from "./panels/documents-source";

const documentsSource = {
  load: vi.fn((kind: string) => Promise.resolve({ kind, quantity: 1, rows: [], items: [], sheets: [], status: "ready" })),
  download: vi.fn(),
} as unknown as HarnessDocumentsSource;

/** A phone: every query of the editor's phone check matches, as on a 390 px touch screen. */
function phoneScreen(matches: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: matches && query === PHONE_VIEW_QUERY, media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  }));
}

beforeAll(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  phoneScreen(true);
});
afterAll(() => { vi.unstubAllGlobals(); });

async function openOnPhone() {
  const api = createInMemoryHarnessDesignApi();
  await api.save("p-1", "h-1", 0, physicalFixture());
  const stored = api.exportSnapshot("p-1", "h-1");
  const save = vi.spyOn(api, "save");
  // Editable props, as for a technologist with «Проекты: правка»: the phone alone makes it a view.
  const view = render(<Suspense fallback={<div>Загрузка редактора…</div>}>
    <HarnessEditorLazy projectId="p-1" harnessId="h-1" harnessDesignation="КБ1" initialView="e4" {...inMemoryEditorPorts({}, api)} documentsSource={documentsSource} />
  </Suspense>);
  await waitFor(() => expect(view.container.querySelector(".harness-root .he-host")).not.toBeNull(), { timeout: 30_000 });
  return { api, stored, save, ...view };
}

describe("чертёж жгута на телефоне — только просмотр", () => {
  it("поле, масштаб и переключатель вида; ни инструментов, ни панелей, ни каталога, ни сохранения", async () => {
    const { api, stored, save, container } = await openOnPhone();
    expect(container.querySelector(".harness-root.harness-phone .harness-editor.he-phone-view")).not.toBeNull();

    const toolbar = screen.getByRole("complementary", { name: "Инструменты редактора" });
    expect(within(toolbar).queryAllByRole("tab")).toHaveLength(0); // «Рисование / Правка / Вид / Привязки» нет
    expect(within(toolbar).queryByRole("button", { name: "Выбор, клавиша V" })).toBeNull();
    expect(within(toolbar).queryByRole("button", { name: "Соединитель, клавиша C" })).toBeNull();
    expect(within(toolbar).getByRole("button", { name: "Уменьшить масштаб" })).toBeTruthy();
    expect(within(toolbar).getByRole("button", { name: "Вписать в экран" })).toBeTruthy();

    expect(screen.queryByRole("region", { name: "Каталог объектов" })).toBeNull();
    expect(screen.queryByRole("complementary", { name: "Параметры и документы" })).toBeNull();
    expect((container.querySelector(".he-right-panel") as HTMLElement).hidden).toBe(true);
    expect((container.querySelector(".he-material-panel") as HTMLElement).hidden).toBe(true);
    expect(container.querySelector(".he-save-state")).toBeNull();
    // One finger moves the field.
    expect(container.querySelector(".he-canvas-frame.tool-pan")).not.toBeNull();

    // Масштаб кнопками работает.
    const before = screen.getByLabelText(/Поле схемы Э4\. Масштаб \d+ процентов/).getAttribute("aria-label");
    fireEvent.click(within(toolbar).getByRole("button", { name: "Увеличить масштаб" }));
    await waitFor(() => expect(screen.getByLabelText(/Поле схемы Э4\. Масштаб \d+ процентов/).getAttribute("aria-label")).not.toBe(before));

    // Горячие клавиши правки ничего не меняют, автосохранение не уходит.
    fireEvent.keyDown(document.body, { key: "c", code: "KeyC" });
    expect(container.querySelector(".he-canvas-frame.tool-connector")).toBeNull();
    fireEvent.keyDown(document.body, { key: "Delete" });
    fireEvent.keyDown(document.body, { key: "z", ctrlKey: true });

    // «Чертёж» открывается в том же режиме.
    fireEvent.click(screen.getByRole("tab", { name: "Чертёж" }));
    await waitFor(() => expect(screen.getByRole("tab", { name: "Чертёж" }).getAttribute("aria-selected")).toBe("true"));
    expect(screen.getByLabelText(/Поле чертежа\. Масштаб/)).toBeTruthy();
    expect(within(screen.getByRole("complementary", { name: "Инструменты редактора" })).queryAllByRole("tab")).toHaveLength(0);

    await act(() => new Promise((resolve) => setTimeout(resolve, 900)));
    expect(save).not.toHaveBeenCalled();
    expect(api.exportSnapshot("p-1", "h-1")).toBe(stored);
  }, 30_000);
});

describe("телефон или нет", () => {
  it("телефон — по запросу PHONE_VIEW_QUERY; без matchMedia (старый браузер, тесты) — не телефон", () => {
    phoneScreen(true);
    expect(isPhoneView()).toBe(true);
    phoneScreen(false);
    expect(isPhoneView()).toBe(false);
    vi.stubGlobal("matchMedia", undefined);
    expect(isPhoneView()).toBe(false);
    phoneScreen(true);
  });
});

describe("щипок двумя пальцами", () => {
  const camera = { offsetX: 10, offsetY: 20, zoom: 1 };
  it("пальцы разошлись вдвое — масштаб вдвое, точка между пальцами остаётся под ними", () => {
    const next = pinchCamera(camera, [{ x: 100, y: 100 }, { x: 200, y: 100 }], [{ x: 50, y: 100 }, { x: 250, y: 100 }]);
    expect(next.zoom).toBeCloseTo(2);
    // The world point under the middle (150, 100) before the pinch is still under it after.
    const world = { x: (150 - camera.offsetX) / camera.zoom, y: (100 - camera.offsetY) / camera.zoom };
    expect(world.x * next.zoom + next.offsetX).toBeCloseTo(150);
    expect(world.y * next.zoom + next.offsetY).toBeCloseTo(100);
  });
  it("пальцы сдвинулись вместе — поле едет за ними без масштаба", () => {
    const next = pinchCamera(camera, [{ x: 100, y: 100 }, { x: 200, y: 100 }], [{ x: 130, y: 60 }, { x: 230, y: 60 }]);
    expect(next).toEqual({ zoom: 1, offsetX: 40, offsetY: -20 });
  });
  it("пальцы в одной точке — масштаб не меняется, не NaN", () => {
    const next = pinchCamera(camera, [{ x: 100, y: 100 }, { x: 100, y: 100 }], [{ x: 120, y: 100 }, { x: 160, y: 100 }]);
    expect(next.zoom).toBe(1);
    expect(Number.isFinite(next.offsetX)).toBe(true);
  });
});
