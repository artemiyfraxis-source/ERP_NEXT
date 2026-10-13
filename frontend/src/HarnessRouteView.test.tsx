import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { ApiError, type HarnessRouteDraft, type HarnessRouteRead } from "./api";
import { designToScene } from "./harness/editor/HarnessDesignEditor";
import { physicalFixture } from "./harness/editor/physical-topology-fixture";
import { snapshotScene } from "./harness/route/RouteSnapshot";
import { appendRouteV3Fragment, captureRouteV3Selection, createInitialRouteV3, generateRouteV3 } from "./harness/route/route-v3-model";
import { HarnessRouteView } from "./HarnessRouteView";

beforeAll(() => {
  // jsdom: ни ResizeObserver, ни 2D-холста — рисунки карточек пустые, проверяется разметка.
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
});

const design = physicalFixture();
const scene = designToScene(design, "drawing");
const source = () => Promise.resolve({ design, scene: snapshotScene(design) });

function document(): Record<string, unknown> {
  const whole = captureRouteV3Selection(design, scene, scene.map((object) => object.id));
  const doc = appendRouteV3Fragment(createInitialRouteV3(design), { ...whole, id: "whole", title: "Сборка жгута", mode: "source", backgroundOpacity: .2, createdAt: "2026-10-12" });
  return JSON.parse(JSON.stringify(generateRouteV3(doc, design)));
}
const read = (patch: Partial<HarnessRouteRead> = {}, revision = 4): HarnessRouteRead => ({
  route: { revision, document: document(), drawing_document_id: "d1", drawing_revision: 2, updated_at: "2026-10-12T10:00:00+03:00", updated_by: "Технолог" },
  drawing: { document_id: "d1", revision: 2 }, stale: false, missing_refs: [], complete: true, ...patch,
});

function mount({ first = read(), canEdit = true }: { first?: HarnessRouteRead; canEdit?: boolean } = {}) {
  const client = {
    route: vi.fn().mockResolvedValue(first),
    saveRoute: vi.fn().mockImplementation(async (_p: string, _i: string, doc: Record<string, unknown>, revision: number | null) =>
      ({ ...read({}, (revision ?? 0) + 1), route: { ...read().route!, revision: (revision ?? 0) + 1, document: doc } })),
    generateRoute: vi.fn().mockResolvedValue(read({}, 5)),
    routeDraft: vi.fn().mockImplementation(async (_p: string, _i: string, revision: number, dryRun: boolean) => draftPreview(revision, dryRun)),
  };
  const library = vi.fn().mockResolvedValue([
    { id: "t1", code: "PRS_TERMINAL_auto", side: "A", title: "PRS_TERMINAL_auto | Опрессовка терминалов (А)", name: "", category: "Опрессовка", norms: { operation_seconds: "6", setup_seconds: "0" } },
  ]);
  const onOpenDrawing = vi.fn();
  render(<HarnessRouteView project="708К.4" index="КБ1" canEdit={canEdit} client={client} source={source} library={library} saveDelay={0} onOpenDrawing={onOpenDrawing} />);
  return { client, library, onOpenDrawing };
}
/** Ответ «Заполнить техкарту»: три блока, четыре операции; при сохранении — черновик. */
const draftPreview = (revision: number, dryRun: boolean): HarnessRouteDraft => ({
  dry_run: dryRun, revision, operations: 4,
  build_config: { ops: [
    { label: "Резка проводов", qty: "8", tOp: "30", seq: 1 }, { label: "Опрессовка", qty: "1", tOp: "120", seq: 2 },
    { label: "Сборка в корпус", qty: "1", tOp: "180", seq: 3 }, { label: "Контроль", qty: "1", tOp: "90", seq: 4 },
  ] },
  route: {
    source: "manual", final: "b3",
    blocks: [
      { key: "b1", name: "Нарезанные провода", ops: [1], final: false, minutes: 40 },
      { key: "b2", name: "Сборка жгута", ops: [2, 3], final: false, minutes: 50 },
      { key: "b3", name: "Жгут питания", ops: [4], final: true, minutes: 15 },
    ],
    edges: [{ from: "b1", to: "b2", per_unit: 4 }, { from: "b2", to: "b3", per_unit: 1 }],
  },
  warnings: ["Маршрут построен на чертеже ред. 1, чертёж уже ред. 2 — сверьте карточки", "Операция 2 (Опрессовка): не заполнен ход операции", "Операция 3 (Сборка в корпус): не заполнен ход операции"],
  draft: dryRun ? null : { id: "dr1", project: "708К.4", item_index: "КБ1", source: "manual", author: "Технолог", status: "draft", note: "", created_at: "2026-10-13T10:00:00+03:00" },
});
const titles = () => [...screen.getByRole("group", { name: "Карточки маршрута" }).querySelectorAll(".rc-title")].map((node) => node.textContent);
const card = (title: string) => screen.getByRole("article", { name: new RegExp(`: ${title}\\.`) });

describe("«Маршрут КБn» в карточке проекта", () => {
  it("draws the cards of the stored route with their kind, parts and time, and the cut wires «A:1 → B:1»", async () => {
    mount();
    await waitFor(() => expect(titles()).toEqual(["Нарезанные провода", "Сборка жгута", "Готовый жгут"]));
    expect(within(card("Нарезанные провода")).getByText("ПОЛУФАБРИКАТ")).toBeInTheDocument();
    expect(card("Готовый жгут")).toHaveTextContent("6 комплектующих · 1 шт · 0 мин");
    expect(within(card("Нарезанные провода")).getByText("A:1 → B:1")).toBeInTheDocument();
    expect(within(card("Готовый жгут")).getByRole("img", { name: "Чертёж: Готовый жгут" })).toBeInTheDocument();
    expect(screen.getByText(/ред\. 4 · чертёж ред\. 2 · Технолог/)).toBeInTheDocument();
    expect(screen.queryByText(/построен на чертеже ред/)).not.toBeInTheDocument();
  });

  it("says when the route was built on an older drawing and what is gone from it", async () => {
    mount({ first: read({ stale: true, drawing: { document_id: "d1", revision: 5 }, missing_refs: [{ kind: "wire", id: "W3" }], complete: false }) });
    expect(await screen.findByText(/Маршрут построен на чертеже ред\. 2, чертёж уже ред\. 5\./)).toHaveTextContent("В чертеже больше нет 1 части маршрута");
    expect(card("Нарезанные провода")).toHaveTextContent("Нет в чертеже: 1 часть");
    expect(screen.getByText(/нет финала со всеми частями чертежа/)).toBeInTheDocument();
  });

  it("builds the first route from the drawing without asking and asks before rebuilding an edited one", async () => {
    const { client } = mount({ first: { route: null, drawing: { document_id: "d1", revision: 2 }, stale: false, missing_refs: [], complete: false } });
    expect(await screen.findByText(/Маршрута ещё нет/)).toBeInTheDocument();
    const confirm = vi.spyOn(window, "confirm");
    fireEvent.click(screen.getAllByRole("button", { name: "Построить по чертежу" }).at(-1)!);
    await waitFor(() => expect(titles()).toHaveLength(3));
    expect(confirm).not.toHaveBeenCalled();
    expect(client.generateRoute).toHaveBeenCalledWith("708К.4", "КБ1", null);
    confirm.mockReturnValueOnce(false);
    fireEvent.click(screen.getByRole("button", { name: "Построить по чертежу" }));
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("ручные правки карточек пропадут"));
    expect(client.generateRoute).toHaveBeenCalledTimes(1);
    confirm.mockReturnValueOnce(true);
    fireEvent.click(screen.getByRole("button", { name: "Построить по чертежу" }));
    await waitFor(() => expect(client.generateRoute).toHaveBeenLastCalledWith("708К.4", "КБ1", 5));
    confirm.mockRestore();
  });

  it("saves an edit of the card with the route revision and the drawing revision it was built on", async () => {
    const { client } = mount();
    await waitFor(() => expect(titles()).toHaveLength(3));
    fireEvent.keyDown(card("Сборка жгута"), { key: "Enter" });
    const panel = screen.getByRole("complementary", { name: "Карточка «Сборка жгута»" });
    fireEvent.change(within(panel).getByLabelText("Название"), { target: { value: "Жгут без маркировки" } });
    await waitFor(() => expect(client.saveRoute).toHaveBeenCalled());
    const [, , saved, revision, drawingRevision] = client.saveRoute.mock.calls[0];
    expect([revision, drawingRevision]).toEqual([4, 2]);
    expect((saved.nodes as Array<{ title: string }>).map((node) => node.title)).toContain("Жгут без маркировки");
    expect(saved.graphEdited).toBe(true);
    expect(await screen.findByText("Сохранено")).toBeInTheDocument();
    // Следующая правка — уже с новой версией.
    fireEvent.change(within(screen.getByRole("complementary")).getByLabelText("Количество, шт"), { target: { value: "3" } });
    await waitFor(() => expect(client.saveRoute).toHaveBeenCalledTimes(2));
    expect(client.saveRoute.mock.calls[1][3]).toBe(5);
  });

  it("adds an operation from the ERP library and inserts a card on a link", async () => {
    const { client } = mount();
    await waitFor(() => expect(titles()).toHaveLength(3));
    fireEvent.keyDown(card("Готовый жгут"), { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "+ Операция из библиотеки" }));
    fireEvent.change(await screen.findByLabelText("Найти операцию в библиотеке"), { target: { value: "опрессовка" } });
    fireEvent.click(await screen.findByRole("button", { name: /Опрессовка терминалов \(А\)/ }));
    expect(screen.getByLabelText("Название операции")).toHaveValue("Опрессовка терминалов (А)");
    expect(card("Готовый жгут")).toHaveTextContent("0,1 мин");
    fireEvent.click(screen.getByRole("button", { name: "Вставить карточку: «Сборка жгута» → «Готовый жгут»" }));
    await waitFor(() => expect(titles()).toEqual(["Нарезанные провода", "Сборка жгута", "Готовый жгут", "Сборка 2"]));
    await waitFor(() => expect((client.saveRoute.mock.calls.at(-1)![2].nodes as unknown[]).length).toBe(4));
  });

  it("keeps the edits on a 409 and offers to load the saved route", async () => {
    const { client } = mount();
    client.saveRoute.mockRejectedValueOnce(new ApiError("Маршрут уже сохранил Иванов 12.10 в 11:00 — обновите страницу и повторите правку", 409));
    await waitFor(() => expect(titles()).toHaveLength(3));
    fireEvent.keyDown(card("Сборка жгута"), { key: "Enter" });
    fireEvent.change(screen.getByLabelText("Название"), { target: { value: "Моя сборка" } });
    const alert = await screen.findByText(/Правки не сохранены: Маршрут уже сохранил Иванов/);
    expect(alert).not.toHaveTextContent("обновите");
    expect(screen.getByText("Не сохранено — маршрут изменили")).toBeInTheDocument();
    // Дальше ничего не пишется, пока не загружен сохранённый.
    fireEvent.change(screen.getByLabelText("Название"), { target: { value: "Моя сборка 2" } });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
    expect(client.saveRoute).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Загрузить сохранённый" }));
    await waitFor(() => expect(client.route).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(titles()).toContain("Сборка жгута"));
  });

  it("lists the server's reasons when a route must not be stored", async () => {
    const { client } = mount();
    client.saveRoute.mockRejectedValueOnce(new ApiError("Маршрут не сохранён", 422, { message: "Маршрут не сохранён", errors: ["связи карточек образуют цикл"] }));
    await waitFor(() => expect(titles()).toHaveLength(3));
    fireEvent.keyDown(card("Сборка жгута"), { key: "Enter" });
    fireEvent.change(screen.getByLabelText("Название"), { target: { value: "Моя сборка" } });
    expect(await screen.findByText("связи карточек образуют цикл")).toBeInTheDocument();
  });

  it("refuses a second final with a reason and keeps the card as it was", async () => {
    const { client } = mount();
    await waitFor(() => expect(titles()).toHaveLength(3));
    fireEvent.keyDown(card("Сборка жгута"), { key: "Enter" });
    fireEvent.change(screen.getByLabelText("Вид"), { target: { value: "final" } });
    expect(screen.getByRole("alert")).toHaveTextContent("Финал уже есть — «Готовый жгут»");
    expect(client.saveRoute).not.toHaveBeenCalled();
  });

  it("asks before removing a card or a link", async () => {
    const { client } = mount();
    await waitFor(() => expect(titles()).toHaveLength(3));
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    fireEvent.click(screen.getByRole("button", { name: "Удалить карточку «Сборка жгута»" }));
    expect(titles()).toHaveLength(3);
    fireEvent.click(screen.getByRole("button", { name: "Удалить связь: «Сборка жгута» → «Готовый жгут»" }));
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining("Убрать связь «Сборка жгута» → «Готовый жгут»?"));
    await waitFor(() => expect(client.saveRoute).toHaveBeenCalled());
    confirm.mockRestore();
  });

  it("«Заполнить техкарту»: shows the result first, then saves the draft by the previewed route revision", async () => {
    const { client } = mount();
    await waitFor(() => expect(titles()).toHaveLength(3));
    fireEvent.click(screen.getByRole("button", { name: "Заполнить техкарту" }));
    const panel = await screen.findByRole("complementary", { name: "Техкарта из маршрута" });
    expect(client.routeDraft).toHaveBeenCalledWith("708К.4", "КБ1", 4, true);
    expect(panel).toHaveTextContent("4 операции · 3 блока · 10,5 мин на жгут · маршрут ред. 4");
    expect(within(panel).getByRole("status")).toHaveTextContent("Маршрут построен на чертеже ред. 1, чертёж уже ред. 2 — сверьте карточкиНе заполнен ход операции: операции 2, 3");
    const assembly = within(panel).getByRole("region", { name: "Блок «Сборка жгута»" });
    expect(assembly).toHaveTextContent("из: Нарезанные провода × 4");
    expect(assembly).toHaveTextContent("2. Опрессовка2 мин3. Сборка в корпус3 мин");
    expect(within(panel).getByRole("region", { name: "Блок «Жгут питания»" })).toHaveTextContent("изделие");
    expect(within(panel).getByRole("region", { name: "Блок «Нарезанные провода»" })).toHaveTextContent("1. Резка проводов4 мин");
    fireEvent.click(within(panel).getByRole("button", { name: "Сохранить черновик техкарты" }));
    expect(await screen.findByText(/Черновик техкарты КБ1 сохранён/)).toBeInTheDocument();
    expect(client.routeDraft).toHaveBeenLastCalledWith("708К.4", "КБ1", 4, false);
    expect(screen.queryByRole("complementary", { name: "Техкарта из маршрута" })).not.toBeInTheDocument();
    expect(client.saveRoute).not.toHaveBeenCalled();
  });

  it("«Заполнить техкарту» shows the server's reason and stores nothing for a route without «Готовый жгут»", async () => {
    const { client } = mount();
    client.routeDraft.mockRejectedValueOnce(new ApiError("Техкарта не заполнена", 422, {
      message: "Техкарта не заполнена", errors: ["В маршруте нет карточки «Готовый жгут» — отметьте итоговую карточку"] }));
    await waitFor(() => expect(titles()).toHaveLength(3));
    fireEvent.click(screen.getByRole("button", { name: "Заполнить техкарту" }));
    expect(await screen.findByText(/В маршруте нет карточки «Готовый жгут»/)).toBeInTheDocument();
    expect(screen.queryByRole("complementary", { name: "Техкарта из маршрута" })).not.toBeInTheDocument();
    expect(client.routeDraft).toHaveBeenCalledTimes(1);
  });

  it("an edit of the route closes the preview — it is shown again from the saved route", async () => {
    mount();
    await waitFor(() => expect(titles()).toHaveLength(3));
    fireEvent.click(screen.getByRole("button", { name: "Заполнить техкарту" }));
    await screen.findByRole("complementary", { name: "Техкарта из маршрута" });
    fireEvent.click(screen.getByRole("button", { name: "+ Карточка" }));
    expect(screen.queryByRole("complementary", { name: "Техкарта из маршрута" })).not.toBeInTheDocument();
  });

  it("shows the route read-only without the right to edit", async () => {
    mount({ canEdit: false });
    await waitFor(() => expect(titles()).toHaveLength(3));
    expect(screen.getByText("Только просмотр")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Построить по чертежу" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Удалить карточку/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Заполнить техкарту" })).not.toBeInTheDocument();
    fireEvent.keyDown(card("Сборка жгута"), { key: "Enter" });
    expect(screen.getByLabelText("Название")).toBeDisabled();
  });

  it("sends to the drawing when the position has none", async () => {
    const { onOpenDrawing } = mount({ first: { route: null, drawing: null, stale: false, missing_refs: [], complete: false } });
    expect(await screen.findByText("У КБ1 нет чертежа жгута — маршрут строится по чертежу.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Открыть чертёж" }));
    expect(onOpenDrawing).toHaveBeenCalled();
  });
});
