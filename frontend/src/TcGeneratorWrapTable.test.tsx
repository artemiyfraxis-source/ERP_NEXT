import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import { resetPinoutHousings } from "./PinoutDiagram";
import { buildMark, draftKey } from "./useGeneratorDraft";
import { emptyManual } from "./manualLogic";
import { COMPONENTS_663, COMPONENTS_708K5, COMPONENTS_KB14, harness663Wrap, harness708k5, harnessKb14 } from "./tcGenComplex663.fixture";
import { wrapFixText, wrapSegsTotal } from "./tcGenLogic";
import type { Comp, GenState } from "./tcGenLogic";
import type { KitsetBom, ProjectTechcard } from "./api";

/* «Лента, оплётка, рукава» в «Сложной сборке»: 663К.1 КБ2 (ТСП-200 на трёх ветвях), КБ14 (флис, изолента точками, ТСП),
   708К.5 КБ1 (лента тканевая спиралью по участкам) — владелец 25.10. */
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
const USER = "u-wrap";

const cardOf = (project: string, index: string): ProjectTechcard => ({
  id: "40000000-0000-0000-0000-000000000014", version: 1, project, item_index: index, product_name: "Жгут", source_calculation: "", deadline: null,
  status: "Черновик", revision: 0, document: null, manual_operation_keys: [], created_at: "2026-10-25T10:00:00+03:00", updated_at: "2026-10-25T10:00:00+03:00", issued_at: null,
});
const kitOf = (project: string, index: string, comps: Comp[]): KitsetBom => ({
  project, index, name: "Жгут", run: "50", part_qty: "50", calc_source: "", calc_ops: [],
  components: comps.map((c) => ({ id: c.id, kd: "", type: c.type, name: c.name, art: c.art, qty: c.qty })),
});

beforeEach(() => {
  resetPinoutHousings();
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [] });
    if (path.startsWith("/api/techcards/connector-families")) return json({ items: [], match: null });
    if (path.includes("draft")) return json({ draft: null });
    return json({ items: [], metadata: null, records: [], columns: [], by_section: [], applicator: "", crimp_height: "", pull_force: "" });
  });
});
afterEach(() => { cleanup(); window.localStorage.clear(); vi.restoreAllMocks(); });

function mount(project: string, index: string, comps: Comp[], S: GenState) {
  window.localStorage.setItem(draftKey(USER, project, index), JSON.stringify({
    v: 1, rev: 1, savedAt: "2026-10-25T10:00:00.000Z", built: buildMark(null), server: 0, synced: true,
    value: { S, manual: emptyManual(), terEdits: {}, coaxAdd: {} },
  }));
  render(<TcGenerator card={cardOf(project, index)} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kitOf(project, index, comps)} userId={USER} />);
}
const layers = async () => within(await screen.findByRole("group", { name: "Слои ленты и оплётки" }));

it("logic: fixing text and the sum of segments", () => {
  expect(wrapSegsTotal("40, 40, 140, 40, 40")).toBe(300);
  expect(wrapSegsTotal("40;70.5 ")).toBe(110.5);
  expect(wrapSegsTotal("40,40,140")).toBe(220);
  expect(wrapSegsTotal("")).toBe(0);
  expect(wrapFixText({ fix: "spots", points: "8", step: "50" })).toBe("фиксация 8 точек с шагом 50 мм");
  expect(wrapFixText({ fix: "spots", points: "1", step: "" })).toBe("фиксация 1 точка");
  expect(wrapFixText({ fix: "spots", points: "", step: "50" })).toBe("фиксация точками с шагом 50 мм");
  expect(wrapFixText({ fix: "spiral", segs: "40, 40, 140" })).toBe("спиралью по участкам 40 + 40 + 140 мм");
  expect(wrapFixText({})).toBe("");
});

it("КБ14: three layers in one table, each with place and length; spot fixing lands in the operation name", async () => {
  const { D, S } = harnessKb14();
  mount("663К.1", "КБ14", COMPONENTS_KB14, S);
  const table = await layers();
  expect(table.getByLabelText("Материал 1")).toHaveValue("kit-4");
  expect(table.getByLabelText("Материал 2")).toHaveValue("kit-5");
  expect(table.getByLabelText("Материал 3")).toHaveValue("kit-6");
  expect(table.getByLabelText("Материал 4")).toHaveValue("");
  expect(table.getAllByText("весь ствол")).toHaveLength(2);
  expect(table.getByText("ветви: 1")).toBeInTheDocument();
  fireEvent.change(table.getByLabelText("Закрепление слоя 2"), { target: { value: "spots" } });
  fireEvent.change(table.getByLabelText("Точек фиксации слоя 2"), { target: { value: "8" } });
  fireEvent.change(table.getByLabelText("Шаг точек слоя 2"), { target: { value: "50" } });
  expect(table.getByText("фиксация 8 точек с шагом 50 мм")).toBeInTheDocument();
  expect(await screen.findByText(/Монтаж ленты — Изолента ПВХ 19 мм, 300 мм, фиксация 8 точек с шагом 50 мм/)).toBeInTheDocument();
  expect(D.components).toBeTruthy();
});

it("708К.5: spiral by segments, the total fills an empty length", async () => {
  const { S } = harness708k5();
  mount("708К.5", "КБ1", COMPONENTS_708K5, S);
  const table = await layers();
  fireEvent.change(table.getByLabelText("Длина на изделие"), { target: { value: "" } });
  fireEvent.change(table.getByLabelText("Закрепление слоя 1"), { target: { value: "spiral" } });
  fireEvent.change(table.getByLabelText("Участки спирали слоя 1, мм"), { target: { value: "40, 40, 140, 40, 40" } });
  expect(table.getByText("всего 300 мм")).toBeInTheDocument();
  expect(await screen.findByText(/Монтаж ленты — Лента тканевая 19 мм, 300 мм, спиралью по участкам 40 \+ 40 \+ 140 \+ 40 \+ 40 мм/)).toBeInTheDocument();
  // «Сплошная намотка» снимает закрепление.
  fireEvent.change(table.getByLabelText("Закрепление слоя 1"), { target: { value: "" } });
  expect(table.queryByLabelText("Участки спирали слоя 1, мм")).toBeNull();
});

it("КБ2: the sleeve length per branch is set under the layer, not in the connector cards", async () => {
  mount("663К.1", "КБ2", COMPONENTS_663, harness663Wrap());
  const table = await layers();
  expect(table.getByLabelText("Оплётка ветви А1, отрезок")).toHaveValue("440");
  expect(table.getByText("Отрезки по ветвям: XS1 440 · XS2 340 · XS3 340 мм, всего 1120 мм.")).toBeInTheDocument();
  expect(table.getByText("по ветвям")).toBeInTheDocument();
  fireEvent.change(table.getByLabelText("Оплётка ветви А2, отрезок"), { target: { value: "325" } });
  expect(table.getByText("Отрезки по ветвям: XS1 440 · XS2 325 · XS3 340 мм, всего 1105 мм.")).toBeInTheDocument();
  fireEvent.change(table.getByLabelText("Оплётка ветви А3, отрезок"), { target: { value: "0" } });
  expect(screen.getAllByLabelText("Оплётка ветви А3, отрезок")).toHaveLength(1);
  const sideA = screen.getByRole("region", { name: "Сторона А" });
  expect(within(sideA).queryByLabelText("Оплётка ветви А1, отрезок")).toBeNull();
});

it("the technologist's note stays in the form", async () => {
  mount("663К.1", "КБ2", COMPONENTS_663, harness663Wrap());
  const note = await screen.findByRole("textbox", { name: /Примечание технолога/ });
  fireEvent.change(note, { target: { value: "провод 89 не вошёл — нет в составе" } });
  expect(note).toHaveValue("провод 89 не вошёл — нет в составе");
});
