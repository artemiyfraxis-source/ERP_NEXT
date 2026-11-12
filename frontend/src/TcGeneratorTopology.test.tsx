import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TcGenerator, resetGeneratorRefs } from "./TcGenerator";
import { resetPinoutHousings } from "./PinoutDiagram";
import { genSnapshot } from "./genBuildState";
import { COMPONENTS_663, harness663 } from "./tcGenComplex663.fixture";
import type { GenState, PhysicalConnection, TubePlacement } from "./tcGenLogic";
import type { KitsetBom, ProjectTechcard } from "./api";

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
const kit: KitsetBom = {
  project: "663К.1", index: "КБ2", name: "Жгут", run: "1", part_qty: "1", calc_source: "", calc_ops: [],
  components: COMPONENTS_663.map((c) => ({ ...c, kd: "" })),
};

beforeEach(() => {
  resetGeneratorRefs(); resetPinoutHousings(); window.localStorage.clear();
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.startsWith("/api/techcards/connector-families")) return json({ items: [], match: { n: 8, kind: "grid", rows: 1, cols: 8, grid: [[1, 2, 3, 4, 5, 6, 7, 8]] } });
    if (path.includes("tab=mat")) return json({ metadata: null, columns: [], records: [{ id: "tube-db", fields: { "Наименование": "ТУТ 3/1", "Группа": "ТУТ", "Ед. изм.": "м" } }] });
    return json({ items: [], metadata: null, records: [], columns: [], by_section: [], applicator: "", crimp_height: "", pull_force: "" });
  });
});
afterEach(() => { cleanup(); window.localStorage.clear(); vi.restoreAllMocks(); });

async function mount(S: GenState = harness663(), restored = false) {
  const onConfig = vi.fn();
  const onBuild = vi.fn();
  const card = {
    id: "topology-card", version: 1, project: kit.project, item_index: kit.index, product_name: kit.name,
    status: "Черновик", manual_operation_keys: [],
    document: { build_config: { tab: "complex", gen_state: genSnapshot(COMPONENTS_663, S, {}, {}) } },
  } as unknown as ProjectTechcard;
  const view = render(<TcGenerator card={card} kit={kit} busy={false} onBuild={onBuild} onDiscard={vi.fn()} onConfig={onConfig} userId="topology-user" />);
  if (restored) await screen.findByRole("button", { name: "Отбросить" });
  else await screen.findByRole("region", { name: "Распиновка и сростки" });
  const config = () => onConfig.mock.calls.at(-1)![0] as Record<string, unknown>;
  const segments = () => config().physicalConnections as PhysicalConnection[];
  const tubes = () => config().tubePlacements as TubePlacement[];
  return { config, segments, tubes, onBuild, unmount: view.unmount };
}
const change = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });

it("opens legacy drafts without enabling topology and keeps editing and confirmation in the consolidated table", async () => {
  const { config } = await mount();
  expect(config()).not.toHaveProperty("topologyVersion");
  expect(screen.queryByText("Распиновка — контакты разъёма")).toBeNull();
  expect(screen.getByRole("figure", { name: "Схема соединений" })).toBeInTheDocument();
  expect(screen.getByLabelText("Конец В провода № 3")).toHaveValue("sp:s1");
  fireEvent.click(screen.getByRole("button", { name: "Подтвердить распиновку А1" }));
  expect(screen.queryByRole("button", { name: "Подтвердить распиновку А1" })).toBeNull();
  change("Конец А провода № 3", "pin:e1:5");
  expect(screen.getByRole("button", { name: "Подтвердить распиновку А1" })).toBeInTheDocument();
  expect(config()).not.toHaveProperty("topologyVersion");
});

it("edits a branch tube and a through splice tube with an explicit preload end and phase", async () => {
  const { config, tubes, segments } = await mount();
  fireEvent.click(screen.getByRole("button", { name: "Добавить трубку" }));
  expect(screen.getByLabelText("Длина трубки 1, мм")).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("длина и количество должны быть положительными");
  expect(config()).not.toHaveProperty("topologyVersion");
  change("Длина трубки 1, мм", "25");
  change("Количество трубок 1", "2");
  change("Место трубки 1", "branch:2:0:B");
  change("Конец надевания трубки 1", "2:0:A");
  expect(tubes()[0]).toMatchObject({ lengthMm: 25, count: 2, phase: "preload", preloadEnd: { idx: 2, unit: 0, side: "A" }, target: { kind: "branch", end: { idx: 2, side: "B" } } });
  expect(segments().find((s) => s.idx === 2)?.to).toMatchObject({ kind: "splice", spliceId: "s1" });
  change("Место трубки 1", "splice:s1");
  change("Конец надевания трубки 1", "6:1:B");
  expect(tubes()[0]).toMatchObject({ target: { kind: "splice", spliceId: "s1" }, preloadEnd: { idx: 6, unit: 1, side: "B" } });
  change("Порядок установки трубки 1", "afterJoin");
  expect(tubes()[0]).toMatchObject({ phase: "afterJoin" });
  expect(tubes()[0].preloadEnd).toBeUndefined();
  expect(screen.queryByLabelText("Конец надевания трубки 1")).toBeNull();
  change("Количество трубок 1", "1.5");
  expect(config()).not.toHaveProperty("topologyVersion");
  change("Количество трубок 1", "1");
  expect(tubes()[0].count).toBe(1);
  fireEvent.click(screen.getByRole("button", { name: "Удалить трубку 1" }));
  expect(tubes()).toEqual([]);
});

it("allows a tube on a solder cup and a material outside the kit", async () => {
  const S = harness663();
  S.endpoints = S.endpoints.map((e) => e.label === "XS1" ? { ...e, method: "solder" } : e);
  const { tubes } = await mount(S);
  fireEvent.click(screen.getByRole("button", { name: "Добавить трубку" }));
  change("Длина трубки 1, мм", "12");
  change("Место трубки 1", "cup:2:0:A");
  change("Материал трубки 1", "art:ТУТ 3/1");
  expect(tubes()[0].material).toEqual({ art: "ТУТ 3/1", name: "ТУТ 3/1" });
  change("Материал трубки 1", "article");
  change("Материал трубки 1: артикул", "ТУТ особая");
  change("Материал трубки 1: название", "Трубка по КД");
  expect(tubes()[0]).toMatchObject({ target: { kind: "cup", end: { idx: 2, side: "A" } }, material: { art: "ТУТ особая", name: "Трубка по КД" } });
  expect(within(screen.getByLabelText("Место трубки 1")).queryByRole("option", { name: /Чашка.*конец В/ })).toBeNull();
});

it("marks a same-connector jumper and edits either physical end independently", async () => {
  const { segments } = await mount();
  await waitFor(() => expect(within(screen.getByLabelText("Конец А провода № 9.1")).getByRole("option", { name: "XS3 · контакт 8" })).toBeInTheDocument());
  change("Конец В провода № 9.1", "jumper");
  expect(segments().find((s) => s.idx === 8 && s.unit === 0)).toMatchObject({ from: { kind: "pin", epId: "e3", pin: 3 }, to: { kind: "pin", epId: "e3", pin: 7 } });
  expect(screen.getByLabelText("Конец В провода № 9.1")).toHaveValue("pin:e3:7");
  change("Конец В провода № 9.1", "pin:e3:8");
  expect(segments().find((s) => s.idx === 8 && s.unit === 0)?.to).toMatchObject({ epId: "e3", pin: 8 });
  change("Конец В провода № 9.1", "tail");
  expect(segments().find((s) => s.idx === 8 && s.unit === 0)?.to.kind).toBe("free");
});

it("edits a bare lug per end including cut allowances and can reconnect it to a splice", async () => {
  const { config, segments } = await mount();
  change("Конец В провода № 9.1", "lug");
  expect(config()).not.toHaveProperty("topologyVersion");
  const label = "Наконечник № 9.1, конец В";
  change(`Материал: ${label}: артикул`, "НКИ-0,5");
  change(`Материал: ${label}: название`, "Наконечник кольцевой");
  change(`Зачистка: ${label}`, "5");
  change(`L+: ${label}`, "2");
  expect(screen.getByRole("alert")).toHaveTextContent("Разные припуски концов");
  expect(config()).not.toHaveProperty("physicalConnections");
  change(`L+: ${label}`, "0");
  change(`L−: ${label}`, "0");
  expect(segments().find((s) => s.idx === 8 && s.unit === 0)?.to).toMatchObject({ kind: "lug", crimp: "lug", material: { art: "НКИ-0,5", name: "Наконечник кольцевой" } });
  expect(screen.getByLabelText(`L+: ${label}`)).toHaveValue("0");
  expect(screen.getByLabelText("Конец В провода № 9.2")).toHaveValue("tail");
  change("Конец В провода № 9.1", "sp:s1");
  expect(screen.queryByLabelText(label)).toBeNull();
  expect(segments().find((s) => s.idx === 8 && s.unit === 0)?.to).toMatchObject({ kind: "splice", spliceId: "s1" });
});

it("reopens saved topology with the exact tube access end and lug settings", async () => {
  const S: GenState = { ...harness663(), topology: { version: 1,
    ends: [{ end: { idx: 8, unit: 0, side: "B" }, at: { kind: "lug", material: { art: "НКИ", name: "Кольцевой" }, crimp: "lug", strip: "5", lPlus: "0", lMinus: "0" } }],
    tubes: [{ id: "saved", material: { compId: "kit-15" }, lengthMm: 20, count: 1, target: { kind: "splice", spliceId: "s1" }, phase: "preload", preloadEnd: { idx: 2, unit: 0, side: "B" } }],
  } };
  const { tubes, segments } = await mount(S);
  expect(screen.getByLabelText("Конец В провода № 9.1")).toHaveValue("lug");
  expect(screen.getByLabelText("Конец надевания трубки 1")).toHaveValue("2:0:B");
  expect(tubes()[0]).toMatchObject({ ...S.topology!.tubes![0], material: { art: "ТНТ-1/0,5", name: "ТНТ-1/0,5" } });
  expect(screen.getByLabelText("Материал трубки 1")).toHaveValue("kit:kit-15");
  expect(segments().find((s) => s.idx === 8 && s.unit === 0)?.to.kind).toBe("lug");
});

it("applies per-end lug allowances to an individual segment without changing its other end", async () => {
  const { config, segments } = await mount();
  change("Конец В провода № 3", "lug");
  const label = "Наконечник № 3, конец В";
  change(`Материал: ${label}: артикул`, "НКИ-0,5");
  change(`Зачистка: ${label}`, "5");
  change(`Обжим: ${label}`, "tube");
  change(`L+: ${label}`, "2");
  change(`L−: ${label}`, "1");
  expect(segments().find((s) => s.idx === 2)).toMatchObject({ from: { kind: "pin", pin: 1 }, to: { kind: "lug", crimp: "tube" } });
  expect((config().wires as Array<{ idx: number; length: number }>).find((w) => w.idx === 2)?.length).toBe(452);
  fireEvent.click(screen.getByRole("radio", { name: /От краёв разъёмов/ }));
  expect((config().wires as Array<{ idx: number; length: number }>).find((w) => w.idx === 2)?.length).toBe(449);
});

it("preserves topology through another mode, autosave, and reopening the draft", async () => {
  const view = await mount();
  fireEvent.click(screen.getByRole("button", { name: "Добавить трубку" }));
  change("Длина трубки 1, мм", "22");
  change("Место трубки 1", "splice:s1");
  fireEvent.click(screen.getByRole("tab", { name: "Межплатная" }));
  expect(screen.queryAllByText("Распиновка — контакты разъёма").length).toBeGreaterThan(0);
  expect(view.config()).not.toHaveProperty("topologyVersion");
  view.unmount();
  await mount(harness663(), true);
  expect(screen.getByRole("tab", { name: "Межплатная" })).toHaveAttribute("aria-selected", "true");
  fireEvent.click(screen.getByRole("tab", { name: "Сложная сборка" }));
  expect(screen.getByLabelText("Длина трубки 1, мм")).toHaveValue(22);
  expect(screen.getByLabelText("Место трубки 1")).toHaveValue("splice:s1");
});

it("removes physical overrides and tube targets when their cable is deleted", async () => {
  const { config, tubes, segments } = await mount();
  fireEvent.click(screen.getByRole("button", { name: "Добавить трубку" }));
  change("Длина трубки 1, мм", "20");
  change("Место трубки 1", "branch:8:0:B");
  change("Конец В провода № 9.1", "pin:e3:8");
  fireEvent.click(screen.getByRole("button", { name: "Удалить кабель 9" }));
  expect(config()).toHaveProperty("topologyVersion", 1);
  expect(tubes()).toEqual([]);
  expect(segments().some((s) => s.idx === 8)).toBe(false);
  expect(screen.queryByRole("alert")).toBeNull();
});
