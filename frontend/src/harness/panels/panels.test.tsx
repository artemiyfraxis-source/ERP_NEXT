import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { HarnessBomRow, HarnessConnectionRow, HarnessCutItem, HarnessCutSheet, HarnessDocuments } from "../../api";
import { connectionTableColumnIds, getConnectionTableSettings, updateConnectionTableSettings } from "../editor/connection-table-settings";
import { physicalFixture } from "../editor/physical-topology-fixture";
import { ConnectionsTable, colorFound } from "./ConnectionsTable";
import { CutListPanel, CutSheetSvg } from "./CutListPanel";
import { DOCUMENT_DELAY_MS, HarnessDocumentsContext, type HarnessDocumentsSource } from "./documents-source";
import { SpecificationTable } from "./SpecificationTable";

const connection = (patch: Partial<HarnessConnectionRow> = {}): HarnessConnectionRow => ({
  wire_id: "W1", index: "W1", mark: "—", section: "—", marking: "—", from: "A (ARTICLE-A):1", to: "B (ARTICLE-B):1",
  color: "желтый", color_hex: "#FBC02D", length_mm: 100, route: "Задан", ...patch,
});
const end = (label: string) => ({ label, profile: "A", steps: [{ index: 1, diameter_mm: 2, length_mm: 10, step_mm: 10 }, { index: 2, diameter_mm: 4, length_mm: 20, step_mm: 10 }] });
const sheet: HarnessCutSheet = { object_id: "W1", member_wire_ids: ["W1"], kind: "wire", title: "W1", material: "Wire", a: end("A:1"), b: end("B:1"), sheath: null,
  conductors: [{ id: "W1", a: end("A:1"), b: end("B:1") }], warnings: ["W1: сумма разделки A/B превышает длину заготовки."] };
const item: HarnessCutItem = { id: "W1", kind: "wire", title: "W1", material: "Wire", material_key: "WIRE", length_mm: 100, correction_a_mm: -1.25,
  correction_b_mm: 2, rounding_step_mm: 1, cut_mm: 101, unrounded_mm: 100.875, pieces: 2, total_m: 0.202, status: "ready", warnings: [] };
const bom = (patch: Partial<HarnessBomRow> = {}): HarnessBomRow => ({ key: '["free","A"]', position: 1, index: "A", designation: "PART", name: "2 конт. — Соединитель",
  amount: 1, unit: "шт.", note: "Без библиотечной привязки", object_ids: ["A"], ...patch });

function source(documents: Partial<HarnessDocuments>) {
  const load = vi.fn((kind: keyof HarnessDocuments) => Promise.resolve(documents[kind]!));
  const value = { load, download: vi.fn() } as unknown as HarnessDocumentsSource;
  const wrap = (children: ReactNode) => <HarnessDocumentsContext.Provider value={value}>{children}</HarnessDocumentsContext.Provider>;
  return { value, load, wrap };
}

describe("таблица соединений с сервера", () => {
  it("показывает строки сервера, цвет ячейкой и правит провод в строке", async () => {
    const { wrap, load } = source({ connections: { kind: "connections", rows: [connection(), connection({ wire_id: "W2", index: "W2", color: "мятный", color_hex: "#334155" })] } });
    const document = physicalFixture();
    const onCommand = vi.fn(() => true);
    render(wrap(<ConnectionsTable document={document} quantity={1} readOnly={false} selectedIds={[]} onCommand={onCommand} wireOptions={[]} />));
    const table = await screen.findByRole("region", { name: "Таблица соединений" });
    expect(load).toHaveBeenCalledWith("connections", 1, document);
    expect(within(table).getByText("Индекс (Wn)")).toBeTruthy();
    expect(within(table).getAllByText("A (ARTICLE-A):1")).toHaveLength(2);
    expect((within(table).getByText("желтый") as HTMLElement).style.backgroundColor).toBe("rgb(251, 192, 45)");
    expect(within(table).getByText("мятный (в базе не найден)")).toBeTruthy();
    fireEvent.change(within(table).getByLabelText("Маркировка W1"), { target: { value: "GND" } });
    expect(onCommand).toHaveBeenCalledWith({ type: "update-wire", wireId: "W1", circuit: "GND" });
  });

  it("в просмотре — только текст, без полей правки", async () => {
    const { wrap } = source({ connections: { kind: "connections", rows: [connection({ marking: "NET", length_mm: 123 })] } });
    render(wrap(<ConnectionsTable document={physicalFixture()} quantity={1} readOnly selectedIds={[]} onCommand={vi.fn()} />));
    const table = await screen.findByRole("region", { name: "Таблица соединений" });
    expect(within(table).getByText("NET")).toBeTruthy();
    expect(within(table).getByText("123")).toBeTruthy();
    expect(within(table).queryByLabelText("Длина W1")).toBeNull();
  });

  it("перезапрашивает строки после правки чертежа — один раз за паузу", async () => {
    vi.useFakeTimers();
    try {
      const { wrap, load } = source({ connections: { kind: "connections", rows: [connection()] } });
      const document = physicalFixture();
      const view = render(wrap(<ConnectionsTable document={document} quantity={1} readOnly selectedIds={[]} onCommand={vi.fn()} />));
      await act(async () => { await Promise.resolve(); });
      expect(load).toHaveBeenCalledTimes(1);
      const edited = { ...document, wires: document.wires.map(w => ({ ...w, circuit: "X" })) };
      view.rerender(wrap(<ConnectionsTable document={edited} quantity={1} readOnly selectedIds={[]} onCommand={vi.fn()} />));
      view.rerender(wrap(<ConnectionsTable document={{ ...edited }} quantity={1} readOnly selectedIds={[]} onCommand={vi.fn()} />));
      await act(async () => { vi.advanceTimersByTime(DOCUMENT_DELAY_MS + 10); await Promise.resolve(); });
      expect(load).toHaveBeenCalledTimes(2);
    } finally { vi.useRealTimers(); }
  });

  it("сообщает, если сервер не ответил", async () => {
    const value = { load: vi.fn(() => Promise.reject(new Error("Нет связи с ERP"))), download: vi.fn() } as unknown as HarnessDocumentsSource;
    render(<HarnessDocumentsContext.Provider value={value}><ConnectionsTable document={physicalFixture()} quantity={1} readOnly selectedIds={[]} onCommand={vi.fn()} /></HarnessDocumentsContext.Provider>);
    expect((await screen.findByRole("alert")).textContent).toContain("Нет связи с ERP");
  });

  it("цвет из палитры не помечается", () => {
    expect(colorFound(connection(), [])).toBe(true);
    expect(colorFound(connection({ color: "мятный", color_hex: "#334155" }), [])).toBe(false);
  });

  it("keeps column visibility and order as a shared preference", () => {
    const original = getConnectionTableSettings();
    try {
      updateConnectionTableSettings({ columns: [{ id: "route", visible: true }, { id: "index", visible: false }] });
      const next = getConnectionTableSettings();
      expect(next.columns.map(column => column.id)).toEqual(["route", "index", ...connectionTableColumnIds.filter(id => id !== "route" && id !== "index")]);
      expect(next.columns.find(column => column.id === "index")?.visible).toBe(false);
    } finally {
      updateConnectionTableSettings(original);
    }
  });
});

describe("спецификация с сервера", () => {
  it("правка текста пишет bomText по ключу строки, ↑ меняет порядок, «Добавить позиции» ставит номера", async () => {
    const rows = [bom(), bom({ key: '["free","B"]', position: 2, index: "B", object_ids: ["B"] })];
    const { wrap } = source({ bom: { kind: "bom", quantity: 3, rows } });
    const onChange = vi.fn(() => true);
    const document = physicalFixture();
    render(wrap(<SpecificationTable document={document} quantity={3} readOnly={false} selectedIds={[]} onChange={onChange} onReveal={vi.fn()} />));
    const table = await screen.findByRole("region", { name: "Спецификация" });
    expect(within(table).getByText("На 3 жгут(а)")).toBeTruthy();
    fireEvent.change(within(table).getByLabelText("Наименование позиции 1"), { target: { value: "Разъём" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ bomText: { '["free","A"]': { name: "Разъём" } } }));
    fireEvent.click(within(table).getByRole("button", { name: "Поднять позицию 2" }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ bomOrder: ['["free","B"]', '["free","A"]'] }));
    fireEvent.click(within(table).getByRole("button", { name: "Добавить позиции" }));
    expect((onChange.mock.lastCall as unknown as [{ leaders: unknown[] }])[0].leaders.length).toBeGreaterThan(0);
  });

  it("в просмотре нет полей, подъёма и «Добавить позиции»", async () => {
    const { wrap } = source({ bom: { kind: "bom", quantity: 1, rows: [bom({ amount: 0.6006, unit: "м" })] } });
    render(wrap(<SpecificationTable document={physicalFixture()} quantity={1} readOnly selectedIds={[]} onChange={vi.fn()} onReveal={vi.fn()} />));
    const table = await screen.findByRole("region", { name: "Спецификация" });
    expect(within(table).getByText("0,6006 м")).toBeTruthy();
    expect(within(table).queryByRole("button", { name: /Поднять/ })).toBeNull();
    expect(within(table).queryByRole("button", { name: "Добавить позиции" })).toBeNull();
    expect(within(table).queryByRole("textbox", { name: /позиции/ })).toBeNull();
  });
});

describe("карта резки с сервера", () => {
  it("строки заготовок, лист выбранной и правка длины командой", async () => {
    const { wrap } = source({ cut: { kind: "cut", quantity: 2, items: [item], sheets: [sheet], status: "ready" } });
    const onCommand = vi.fn(() => true);
    render(wrap(<CutListPanel document={physicalFixture()} quantity={2} readOnly={false} relatedIds={[]} onReveal={vi.fn()} onCommand={onCommand} />));
    const region = await screen.findByRole("region", { name: "Схема резки и разделки" });
    expect(within(region).getByText("100 + (-1,25) + (2) = 100,875 мм →", { exact: false })).toBeTruthy();
    expect(within(region).getByText("W1: сумма разделки A/B превышает длину заготовки.")).toBeTruthy();
    const length = within(region).getByLabelText("Исходная длина, мм");
    fireEvent.change(length, { target: { value: "150" } });
    fireEvent.blur(length);
    expect(onCommand).toHaveBeenCalledWith({ type: "update-wire", wireId: "W1", lengthMm: 150 });
  });

  it("правка в поле сразу после появления карты не стирается отложенным эффектом поля", async () => {
    // Раньше поле сбрасывало черновик эффектом при монтировании: эффект, вышедший после ввода, возвращал пустую длину,
    // и blur не слал команду. Здесь ввод идёт в тот же тик, что и появление карты, до отложенных эффектов React.
    const { wrap } = source({ cut: { kind: "cut", quantity: 2, items: [item], sheets: [sheet], status: "ready" } });
    const onCommand = vi.fn(() => true);
    const host = window.document.createElement("div");
    window.document.body.appendChild(host);
    const flags = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean };
    const wasAct = flags.IS_REACT_ACT_ENVIRONMENT;
    flags.IS_REACT_ACT_ENVIRONMENT = false;
    const root = createRoot(host);
    try {
      const typed = new Promise<void>((resolve, reject) => {
        const observer = new MutationObserver(() => {
          const input = host.querySelector<HTMLInputElement>('input[aria-label="Исходная длина, мм"]');
          if (!input) return;
          observer.disconnect();
          try {
            act(() => { fireEvent.change(input, { target: { value: "150" } }); fireEvent.blur(input); });
            resolve();
          } catch (reason) { reject(reason); }
        });
        observer.observe(host, { childList: true, subtree: true });
      });
      root.render(wrap(<CutListPanel document={physicalFixture()} quantity={2} readOnly={false} relatedIds={[]} onReveal={vi.fn()} onCommand={onCommand} />));
      await typed;
      expect(onCommand).toHaveBeenCalledWith({ type: "update-wire", wireId: "W1", lengthMm: 150 });
    } finally {
      flags.IS_REACT_ACT_ENVIRONMENT = true;
      await act(async () => { root.unmount(); });
      flags.IS_REACT_ACT_ENVIRONMENT = wasAct;
      host.remove();
    }
  });

  it("рисует концы, размеры в мм и снятие оболочки кабеля", () => {
    const html = renderToStaticMarkup(createElement(CutSheetSvg, { sheet, cutMm: 100 }));
    expect(html).toContain("A · A:1"); expect(html).toContain("Б · B:1"); expect(html).toContain("Заготовка 100 мм");
    expect(html).toContain("L2=20 мм"); expect(html).toContain("Условное изображение");
    const cable = { ...sheet, kind: "cable" as const, sheath: { a_mm: 0, b_mm: 0, total_mm: 200, complete: true } };
    expect(renderToStaticMarkup(createElement(CutSheetSvg, { sheet: cable, cutMm: 200 }))).toContain('x="60" y="98" width="640"');
  });

  it("без источника документов говорит, что они недоступны", async () => {
    render(<CutListPanel document={physicalFixture()} quantity={1} readOnly relatedIds={[]} onReveal={vi.fn()} onCommand={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Документы чертежа недоступны"));
  });
});
