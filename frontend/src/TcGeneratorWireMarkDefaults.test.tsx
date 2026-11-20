import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TcGenerator, resetGeneratorRefs } from "./TcGenerator";
import { resetPinoutHousings } from "./PinoutDiagram";
import { genSnapshot } from "./genBuildState";
import { COMPONENTS_663, harness663 } from "./tcGenComplex663.fixture";
import type { KitsetBom, ProjectTechcard } from "./api";

/*
 * Новая КБ начинает маркировку проводов с последнего выбора проекта (владелец 20.11): GET
 * /api/techcards/generator-mark-defaults?project=… → `{kind, tapeMm, tubeByDia}`.
 */
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
const kit: KitsetBom = {
  project: "663К.1", index: "КБ2", name: "Жгут", run: "1", part_qty: "1", calc_source: "", calc_ops: [],
  components: COMPONENTS_663.map((c) => ({ ...c, kd: "" })),
};
let defaults: unknown = { kind: null, tapeMm: null, tubeByDia: {} };
const asked: string[] = [];
/** Справочники генератора ждут его (засев формы — после них); null — отвечают сразу. */
let gate: Promise<void> | null = null;

beforeEach(() => {
  resetGeneratorRefs(); resetPinoutHousings(); window.localStorage.clear(); asked.length = 0; gate = null;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.startsWith("/api/techcards/generator-mark-defaults")) { asked.push(path); return json(defaults); }
    if (path.startsWith("/api/techcards/connector-families")) return json({ items: [], match: { n: 8, kind: "grid", rows: 1, cols: 8, grid: [[1, 2, 3, 4, 5, 6, 7, 8]] } });
    if (path.includes("/generator-draft")) return json({ draft: null });
    if (gate) await gate;
    return json({ items: [], metadata: null, records: [], columns: [], by_section: [], applicator: "", crimp_height: "", pull_force: "" });
  });
});
afterEach(() => { cleanup(); window.localStorage.clear(); vi.restoreAllMocks(); });

function mount(buildConfig: Record<string, unknown> | null) {
  const onConfig = vi.fn();
  const card = {
    id: "marks-card", version: 1, project: kit.project, item_index: kit.index, product_name: kit.name,
    status: "Черновик", manual_operation_keys: [], document: buildConfig ? { build_config: buildConfig } : null,
  } as unknown as ProjectTechcard;
  render(<TcGenerator card={card} kit={kit} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} onConfig={onConfig} userId="marks-user" />);
  return { config: () => onConfig.mock.calls.at(-1)?.[0] as Record<string, unknown> | undefined };
}
const kindGroup = () => within(screen.getByRole("radiogroup", { name: "Метка на проводе" }));

it("новая КБ: лента 9 мм из прошлой КБ проекта, покрытие прозрачной трубкой включено", async () => {
  defaults = { kind: "tape", tapeMm: 9, tubeByDia: {} };
  mount(null);
  await waitFor(() => expect(screen.getByLabelText("Ширина ленты маркировки")).toHaveValue("9"));
  expect(asked).toEqual([`/api/techcards/generator-mark-defaults?project=${encodeURIComponent("663К.1")}`]);
  expect(kindGroup().getByRole("radio", { name: "лента" })).toHaveAttribute("aria-checked", "true");
  expect(screen.getByRole("checkbox", { name: "Покрыть прозрачной трубкой" })).toBeChecked();
  // Выбор технолога сильнее: «нет» остаётся «нет».
  fireEvent.click(kindGroup().getByRole("radio", { name: "нет" }));
  expect(kindGroup().getByRole("radio", { name: "нет" })).toHaveAttribute("aria-checked", "true");
});

it("новая КБ: трубка без запомненного Ø — «по Ø провода», без покрытия; проект ничего не печатал — маркировки нет", async () => {
  defaults = { kind: "tube", tapeMm: null, tubeByDia: { "1.1": 3.2 } };
  mount(null);
  await waitFor(() => expect(screen.getByLabelText("Диаметр трубки маркировки")).toHaveValue(""));
  expect(screen.getByRole("checkbox", { name: "Покрыть прозрачной трубкой" })).not.toBeChecked();
  cleanup(); resetGeneratorRefs();
  defaults = { kind: null, tapeMm: null, tubeByDia: {} };
  mount(null);
  await waitFor(() => expect(asked).toHaveLength(2));
  expect(kindGroup().getByRole("radio", { name: "нет" })).toHaveAttribute("aria-checked", "true");
});

it("собранная карта открывается со своей маркировкой: выбор проекта не спрашивается", async () => {
  defaults = { kind: "tape", tapeMm: 12, tubeByDia: {} };
  mount({ tab: "complex", gen_state: genSnapshot(COMPONENTS_663, harness663(), {}, {}) });
  await screen.findByRole("radiogroup", { name: "Метка на проводе" });
  expect(asked).toEqual([]);
  expect(kindGroup().getByRole("radio", { name: "нет" })).toHaveAttribute("aria-checked", "true");
});

it("ответ с выбором проекта пришёл раньше справочников (до засева формы) — выбор всё равно применяется", async () => {
  defaults = { kind: "tape", tapeMm: 12, tubeByDia: {} };
  let release = () => {};
  gate = new Promise<void>((resolve) => { release = resolve; });
  mount(null);
  await waitFor(() => expect(asked).toHaveLength(1));
  await new Promise((resolve) => setTimeout(resolve, 50));  // ответ разобран, справочники ещё ждут
  expect(screen.queryByRole("radiogroup", { name: "Метка на проводе" })).toBeNull();
  release();
  await waitFor(() => expect(screen.getByLabelText("Ширина ленты маркировки")).toHaveValue("12"));
  expect(kindGroup().getByRole("radio", { name: "лента" })).toHaveAttribute("aria-checked", "true");
});

it("последняя КБ проекта была без меток — новая тоже «нет», но включённая лента начинается с прошлой ширины", async () => {
  defaults = { kind: "none", tapeMm: 12, tubeByDia: {} };
  const { config } = mount(null);
  await waitFor(() => expect(config()?.wireMarks).toEqual({ enabled: false, kind: "none", tapeMm: "12" }));
  expect(kindGroup().getByRole("radio", { name: "нет" })).toHaveAttribute("aria-checked", "true");
  fireEvent.click(kindGroup().getByRole("radio", { name: "лента" }));
  expect(screen.getByLabelText("Ширина ленты маркировки")).toHaveValue("12");
});

it("последняя КБ проекта метилась лазером — новая тоже лазер", async () => {
  defaults = { kind: "laser", tapeMm: null, tubeByDia: {} };
  mount(null);
  await waitFor(() => expect(kindGroup().getByRole("radio", { name: "лазер ОБ.019" })).toHaveAttribute("aria-checked", "true"));
});
