import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Suspense } from "react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { CANVAS_EDIT_PROPS, readOnlyCanvasProps, type CanvasViewportProps } from "./editor/CanvasViewport";
import { physicalFixture } from "./editor/physical-topology-fixture";
import { HarnessEditorLazy } from "./index";
import { createInMemoryHarnessDesignApi, inMemoryEditorPorts } from "./testing/in-memory";
import type { HarnessDocumentsSource } from "./panels/documents-source";

beforeAll(() => {
  // jsdom has neither ResizeObserver nor a 2D canvas; the editor draws nothing here, only the DOM is checked.
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 1600 }); // panels open as on a monitor
});

// Строки документов чертежа строит сервер; здесь — его ответ на чертёж-фикстуру.
const documentsSource = {
  load: vi.fn((kind: string) => Promise.resolve(kind === "connections"
    ? { kind, rows: [{ wire_id: "W1", index: "W1", mark: "—", section: "—", marking: "—", from: "A (A):1", to: "B (B):1", color: "#334155", color_hex: "#334155", length_mm: 100, route: "Задан" }] }
    : kind === "bom" ? { kind, quantity: 1, rows: [{ key: '["free","A"]', position: 1, index: "A", designation: "A", name: "2 конт. — Соединитель", amount: 1, unit: "шт.", note: "", object_ids: ["A"] }] }
    : { kind, quantity: 1, items: [], sheets: [], status: "ready" })),
  download: vi.fn(),
} as unknown as HarnessDocumentsSource;

async function openReadOnly() {
  const api = createInMemoryHarnessDesignApi();
  await api.save("p-1", "h-1", 0, physicalFixture());
  const stored = api.exportSnapshot("p-1", "h-1");
  const save = vi.spyOn(api, "save");
  const view = render(<Suspense fallback={<div>Загрузка редактора…</div>}>
    <HarnessEditorLazy projectId="p-1" harnessId="h-1" harnessDesignation="КБ1" initialView="e4" {...inMemoryEditorPorts({}, api)} documentsSource={documentsSource} readOnly />
  </Suspense>);
  await waitFor(() => expect(view.container.querySelector(".harness-root .he-host")).not.toBeNull(), { timeout: 10_000 });
  return { api, stored, save, ...view };
}

describe("редактор жгута в режиме просмотра", () => {
  it("показывает чертёж, но не даёт добавить, удалить, отменить или сохранить", async () => {
    const { api, stored, save, container } = await openReadOnly();
    // Сохранять нечего: кнопки состояния сохранения нет.
    expect(container.querySelector(".he-save-state")).toBeNull();
    // Нечего добавить: каталога нет, в панели инструментов — только выбор и перемещение поля, без вкладок правки.
    expect(screen.queryByRole("region", { name: "Каталог объектов" })).toBeNull();
    const toolbar = screen.getByRole("complementary", { name: "Инструменты редактора" });
    expect(within(toolbar).getByRole("button", { name: "Выбор, клавиша V" })).toBeTruthy();
    expect(within(toolbar).getByRole("button", { name: "Перемещение поля, клавиша H" })).toBeTruthy();
    expect(within(toolbar).queryByRole("button", { name: "Соединитель, клавиша C" })).toBeNull();
    expect(within(toolbar).queryByRole("tab", { name: "Правка" })).toBeNull();
    expect(within(toolbar).queryByRole("tab", { name: "Привязки" })).toBeNull();
    fireEvent.keyDown(document.body, { key: "c", code: "KeyC" }); // горячая клавиша инструмента «Соединитель»
    expect(within(toolbar).getByRole("button", { name: "Выбор, клавиша V" }).getAttribute("aria-pressed")).toBe("true");

    // Выбрать, чтобы посмотреть, можно; свойства и настройки видны, их поля выключены.
    fireEvent.click(screen.getByRole("button", { name: "Показать объекты" }));
    const objects = screen.getByRole("complementary", { name: "Список материальных объектов" });
    fireEvent.click(within(objects).getByRole("button", { name: "Выбрать W1" }));
    expect(within(objects).getByRole("button", { name: "Выбрать W1" }).getAttribute("aria-pressed")).toBe("true");
    expect(container.querySelector(".he-right-panel fieldset.he-readonly-fieldset[disabled]")).not.toBeNull();
    expect(container.querySelector(".he-utility-content fieldset.he-readonly-fieldset[disabled]")).not.toBeNull();

    // Delete и Ctrl+Z ничего не меняют, автосохранение не уходит.
    fireEvent.keyDown(document.body, { key: "Delete" });
    fireEvent.keyDown(document.body, { key: "z", ctrlKey: true });
    await act(() => new Promise((resolve) => setTimeout(resolve, 900)));
    expect(within(objects).getByRole("button", { name: "Выбрать W1" })).toBeTruthy();
    expect(save).not.toHaveBeenCalled();
    expect(api.exportSnapshot("p-1", "h-1")).toBe(stored);
  }, 30_000);

  it("масштаб и переход на чертёж работают", async () => {
    const { save } = await openReadOnly();
    const toolbar = screen.getByRole("complementary", { name: "Инструменты редактора" });
    fireEvent.click(within(toolbar).getByRole("tab", { name: "Вид" }));
    fireEvent.click(within(toolbar).getByRole("button", { name: "Увеличить масштаб" }));
    expect(screen.getByLabelText(/Поле схемы Э4\. Масштаб 120 процентов/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Чертёж" }));
    await waitFor(() => expect(screen.getByRole("tab", { name: "Чертёж" }).getAttribute("aria-selected")).toBe("true"));
    expect(screen.queryByText("Редактор не смог отобразить последнее изменение.")).toBeNull();
    await act(() => new Promise((resolve) => setTimeout(resolve, 900)));
    expect(save).not.toHaveBeenCalled(); // нормализация чертежа при открытии остаётся в памяти
  }, 30_000);
});

describe("документы чертежа в режиме просмотра", () => {
  it("таблицу соединений и спецификацию можно открыть, двигать и закрыть — чертёж не меняется и не сохраняется", async () => {
    const { api, stored, save } = await openReadOnly();
    const documents = screen.getByRole("region", { name: "Документы" });
    const connections = within(documents).getByRole("button", { name: "Таблица соединений" });
    expect((connections as HTMLButtonElement).disabled).toBe(false);
    // Правка чертежа из «Документов» в просмотре не показывается.
    expect(within(documents).queryByRole("button", { name: "Добавить позиции" })).toBeNull();

    fireEvent.click(connections);
    const window = await screen.findByRole("region", { name: "Таблица соединений на поле" });
    const table = await within(window).findByRole("region", { name: "Таблица соединений" });
    expect(within(table).getByText("W1")).toBeTruthy();
    // Полей правки в таблице нет; «Скачать» (файл сохранённого чертежа) и окно — работают.
    expect(within(table).queryByLabelText("Длина W1")).toBeNull();
    fireEvent.click(within(window).getByRole("button", { name: "Скачать" }));
    expect(documentsSource.download).toHaveBeenCalledWith("connections", 1);
    fireEvent.change(within(window).getByLabelText("Закрепить: Таблица соединений"), { target: { value: "right" } });
    expect((within(window).getByLabelText("Закрепить: Таблица соединений") as HTMLSelectElement).value).toBe("right");

    fireEvent.click(within(documents).getByRole("button", { name: "Спецификация" }));
    await waitFor(() => expect(screen.getByRole("tab", { name: "Чертёж" }).getAttribute("aria-selected")).toBe("true"));
    const bom = await screen.findByRole("region", { name: "Спецификация на поле" });
    expect(within(bom).queryByRole("button", { name: "Добавить позиции" })).toBeNull();
    expect(within(bom).queryByRole("button", { name: "↑" })).toBeNull();
    fireEvent.click(within(bom).getByRole("button", { name: "Закрыть: Спецификация" }));
    expect(screen.queryByRole("region", { name: "Спецификация на поле" })).toBeNull();

    await act(() => new Promise((resolve) => setTimeout(resolve, 900)));
    expect(save).not.toHaveBeenCalled();
    expect(api.exportSnapshot("p-1", "h-1")).toBe(stored);
  }, 30_000);
});

describe("поле в режиме просмотра", () => {
  it("не получает ни одного обработчика правки: ни перетаскивания, ни соединения, ни редакторов", () => {
    const edit = Object.fromEntries(CANVAS_EDIT_PROPS.map((key) => [key, vi.fn()]));
    const onCatalogDrop = vi.fn();
    const props = { ...edit, onCatalogDrop, onCameraChange: vi.fn(), onObjectSelect: vi.fn(), readOnly: true } as unknown as CanvasViewportProps;
    const viewOnly = readOnlyCanvasProps(props);
    for (const key of CANVAS_EDIT_PROPS) expect(viewOnly[key]).toBeUndefined();
    expect(viewOnly.onCameraChange).toBe(props.onCameraChange);
    expect(viewOnly.onObjectSelect).toBe(props.onObjectSelect);
    viewOnly.onCatalogDrop("item", { x: 0, y: 0 });
    expect(onCatalogDrop).not.toHaveBeenCalled();
    expect(readOnlyCanvasProps({ ...props, readOnly: false })).toEqual({ ...props, readOnly: false });
  });
});
