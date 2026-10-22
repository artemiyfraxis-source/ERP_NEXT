import { act, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { KitsetBom, ProjectTechcard } from "./api";
import { GenPfScheme, PREVIEW_DELAY, previewSummary, useBuildPreview, type BuildPreview } from "./GenPfScheme";
import { TcGenerator } from "./TcGenerator";

/* Схема ПФ генератора как в собранной ЭТК (владелец 22.10: «как на 713К»): сервер собирает ЭТК из формы без записи,
   схема — EtkRouteDiagram техкарты; правка формы перестраивает её через паузу, последний ответ побеждает. */

const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000001", version: 1, project: "P-100", item_index: "КБ1", product_name: "Жгут XHP 150мм",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-09-23T10:00:00+03:00", updated_at: "2026-09-23T10:00:00+03:00", issued_at: null,
};

/** Собранная ЭТК: резка → опрессовка А → опрессовка В, два ПФ (провода нарезаны → жгут). */
function built(name = "Жгут готов"): ProjectTechcard {
  return {
    ...card, version: 2,
    document: {
      kit: { part_quantity: "10" },
      operations: [
        { position: 1, type: "cutWire", code: "CUT_WIRE_auto", title: "CUT_WIRE_auto | Резка", result: [{ name: "Провода нарезаны", norm: "20", unit: "шт" }] },
        { position: 2, type: "prsTerm", code: "PRS", side: "A", title: "PRS | Опрессовка терминалов (А)" },
        { position: 3, type: "prsTerm", code: "PRS", side: "B", title: "PRS | Опрессовка терминалов (В)", result: [{ name, norm: "10", unit: "шт" }] },
      ],
      route: {
        source: "derived", final: "b2",
        blocks: [
          { key: "b1", name: "Провода нарезаны", ops: [1], final: false, minutes: 10, unit_minutes: 1, rolled_minutes: 1, rolled_ops: 1 },
          { key: "b2", name, ops: [2, 3], final: true, inputs: 1, minutes: 30, unit_minutes: 3, rolled_minutes: 4.5, rolled_ops: 3 },
        ],
        edges: [{ from: "b1", to: "b2", per_unit: 2 }],
      },
      build_config: { partQty: 10, ops: [{ label: "Резка проводов" }, { label: "Опрессовка А1" }, { label: "Опрессовка В1" }] },
      warnings: [],
    },
  };
}

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe("useBuildPreview", () => {
  it("builds once after the pause, the last config wins and a late answer to an old one is dropped", async () => {
    vi.useFakeTimers();
    const answers: Array<(value: ProjectTechcard) => void> = [];
    const load = vi.fn<BuildPreview>(() => new Promise((resolve) => { answers.push(resolve); }));
    const { result, rerender } = renderHook(({ config }) => useBuildPreview(config, load), { initialProps: { config: { n: 1 } as Record<string, unknown> | null } });

    expect(result.current.loading).toBe(true);
    act(() => { vi.advanceTimersByTime(PREVIEW_DELAY - 10); });
    expect(load).not.toHaveBeenCalled();
    rerender({ config: { n: 2 } });
    act(() => { vi.advanceTimersByTime(PREVIEW_DELAY); });
    expect(load).toHaveBeenCalledTimes(1);
    expect(load).toHaveBeenLastCalledWith({ n: 2 });

    rerender({ config: { n: 3 } });
    act(() => { vi.advanceTimersByTime(PREVIEW_DELAY); });
    expect(load).toHaveBeenCalledTimes(2);
    await act(async () => { answers[1](built("Третий")); });
    await act(async () => { answers[0](built("Второй")); });
    expect(result.current).toMatchObject({ loading: false, error: "" });
    expect((result.current.card?.document?.route as { blocks: Array<{ name: string }> }).blocks[1].name).toBe("Третий");

    // The same config again — no new request.
    rerender({ config: { n: 3 } });
    act(() => { vi.advanceTimersByTime(PREVIEW_DELAY * 2); });
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("keeps the last scheme on an error and while the form cannot be built", async () => {
    vi.useFakeTimers();
    const load = vi.fn<BuildPreview>().mockResolvedValueOnce(built()).mockRejectedValueOnce(new Error("Библиотека операционных карт не загружена"));
    const { result, rerender } = renderHook(({ config }) => useBuildPreview(config, load), { initialProps: { config: { n: 1 } as Record<string, unknown> | null } });
    await act(async () => { vi.advanceTimersByTime(PREVIEW_DELAY); });
    expect(result.current.card).not.toBeNull();

    rerender({ config: { n: 2 } });
    await act(async () => { vi.advanceTimersByTime(PREVIEW_DELAY); });
    expect(result.current.error).toBe("Библиотека операционных карт не загружена");
    expect(result.current.card).not.toBeNull();

    rerender({ config: null });
    act(() => { vi.advanceTimersByTime(PREVIEW_DELAY * 2); });
    expect(load).toHaveBeenCalledTimes(2);
    expect(result.current).toMatchObject({ loading: false });
    expect(result.current.card).not.toBeNull();
  });
});

describe("GenPfScheme", () => {
  it("draws the built ETK scheme: ПФ by level with inputs, operations by number and the output", () => {
    render(<GenPfScheme preview={{ card: built(), loading: false, error: "" }} />);
    const pf = screen.getAllByRole("region").find((item) => /^ПФ .*Жгут готов$/.test(item.getAttribute("aria-label") ?? ""))!;
    expect(pf).toBeTruthy();
    expect(within(pf).getByLabelText(/^Вход/)).toHaveTextContent(/Провода нарезаны/);
    expect(within(pf).getByRole("list", { name: /^Операции/ })).toHaveTextContent(/Опрессовка А1.*Опрессовка В1/);
    expect(within(pf).getByLabelText(/^Выход/)).toHaveTextContent("Жгут готов");
    expect(screen.queryByText("Схема ПФ")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("says «Строим схему…» while building, keeps the old scheme and shows the error banner", () => {
    const { rerender } = render(<GenPfScheme preview={{ card: null, loading: true, error: "" }} />);
    expect(screen.getByRole("status")).toHaveTextContent("Строим схему…");
    rerender(<GenPfScheme preview={{ card: built(), loading: true, error: "Нет связи с ERP" }} />);
    expect(screen.getByRole("status")).toHaveTextContent("Строим схему…");
    expect(screen.getByRole("alert")).toHaveTextContent("Нет связи с ERP");
    expect(screen.getAllByRole("region").some((item) => /Жгут готов$/.test(item.getAttribute("aria-label") ?? ""))).toBe(true);
  });

  it("opens the whole scheme in a window and sums it up as ПФ and operations", () => {
    render(<GenPfScheme preview={{ card: built(), loading: false, error: "" }} />);
    fireEvent.click(screen.getByRole("button", { name: "Во весь экран" }));
    const window = screen.getByRole("dialog", { name: "Схема полуфабрикатов" });
    expect(within(window).getByText(/2 ПФ · 3 операции/)).toBeInTheDocument();
    expect(within(window).getAllByRole("region").some((item) => /Жгут готов$/.test(item.getAttribute("aria-label") ?? ""))).toBe(true);
    render(<p data-testid="sum">{previewSummary(built())}</p>);
    expect(screen.getByTestId("sum")).toHaveTextContent("2 ПФ · 3 операции · на изделие 4,5 мин");
    const tiny = built();
    (tiny.document!.route as { blocks: Array<{ rolled_minutes: number }> }).blocks[1].rolled_minutes = 0.001;
    render(<p data-testid="tiny">{previewSummary(tiny)}</p>);
    expect(screen.getByTestId("tiny")).toHaveTextContent(/^2 ПФ · 3 операции$/);
  });
});

/* Генератор: схема из сервера вместо карточек; «Изменить схему» — прежние карточки и правка схемы. */
function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function mockApi() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [
      { id: "cut", code: "CUT_WIRE_auto", side: "", title: "CUT_WIRE_auto | Резка", name: "Резка", category: "Резка", steps: 1, tooling: 1 },
      { id: "prsA", code: "PRS", side: "A", title: "PRS_TERMINAL | Опрессовка терминалов (А)", name: "", category: "Опрессовка терминалов", steps: 1, tooling: 1 },
    ] });
    if (path.startsWith("/api/references/tech-operations?")) {
      const tab = new URLSearchParams(path.split("?")[1]).get("tab");
      return json({ metadata: null, columns: [], records: tab === "ter"
        ? [{ id: "t1", tab: "ter", display_text: "XH", source_sheet: "", fields: { terArticle: "SXH-001T-P0.6", terLPlus: "5", terStep: "2.50", terApplicator: "S12" } }] : [] });
    }
    if (path === "/api/references/technology/resolve-terminal") return json({ applicator: "S12", crimp_height: "0,8", pull_force: "", by_section: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
}

const XH = [
  { type: "Провод", name: "МГТФ 0,12", art: "МГТФ 0,12", qty: 0.3 },
  { type: "Терминал", name: "Контакт XH", art: "SXH-001T-P0.6", qty: 4 },
  { type: "Соед", name: "Колодка XH 2", art: "XHP-2", qty: 2 },
];
const kit = (): KitsetBom => ({
  project: "P-100", index: "КБ1", name: "Жгут XHP 150мм", run: "1", part_qty: "1", calc_source: "", calc_ops: [],
  components: XH.map((item, i) => ({ id: `kit-${i}`, kd: "", ...item })),
});

describe("TcGenerator with the build preview", () => {
  it("shows the server's ПФ scheme, edits on the cards and sends the edited scheme to the next preview", async () => {
    mockApi();
    const preview = vi.fn<BuildPreview>(async () => built());
    render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kit()} buildPreview={preview} />);

    await waitFor(() => expect(preview).toHaveBeenCalled(), { timeout: 3000 });
    const sent = preview.mock.calls.at(-1)![0];
    expect((sent.ops as unknown[]).length).toBe(3);
    expect(sent).not.toHaveProperty("route");
    await waitFor(() => expect(screen.getAllByRole("region").some((item) => /^ПФ .*Жгут готов$/.test(item.getAttribute("aria-label") ?? ""))).toBe(true));
    const editor = screen.getAllByRole("region", { name: "Схема полуфабрикатов" })[0];
    expect(within(editor).getByText(/2 ПФ · 3 операции/)).toBeInTheDocument();
    expect(screen.getByText("автоматическая")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Карточки схемы" })).not.toBeInTheDocument();

    // «Изменить схему» — карточки генератора и правка блока, как раньше.
    fireEvent.click(screen.getByRole("button", { name: "Изменить схему" }));
    const cards = screen.getByRole("group", { name: "Карточки схемы" });
    fireEvent.click(within(cards).getAllByRole("button", { name: /Опрессовка/ })[0]);
    const calls = preview.mock.calls.length;
    const merge = screen.getByRole("combobox", { name: "Объединить с блоком" });
    fireEvent.change(merge, { target: { value: merge.querySelectorAll("option")[1].getAttribute("value") } });
    fireEvent.click(screen.getByRole("button", { name: "Объединить" }));
    expect(screen.getByText("правлена вручную")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Готово" }));
    await waitFor(() => expect(preview.mock.calls.length).toBeGreaterThan(calls), { timeout: 3000 });
    expect(preview.mock.calls.at(-1)![0]).toHaveProperty("route");
    expect(screen.getByRole("button", { name: "Свернуть схему" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Создать" })).toBeInTheDocument();
  });
});
