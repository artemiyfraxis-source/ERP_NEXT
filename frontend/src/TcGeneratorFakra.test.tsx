import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import type { KitsetBom, ProjectTechcard } from "./api";
import { genSnapshot, restoreFromSnapshot, restoreCoaxFromConfig, snapshotFromConfig } from "./genBuildState";
import { initState, makeGen, coaxFromFields, type GenData } from "./tcGenLogic";
import * as genLogic from "./tcGenLogic";
import { buildMark, draftKey } from "./useGeneratorDraft";
import { pressBuild } from "./test/pressBuild";
import configs from "./__fixtures__/fakra1116Configs.json";
import library from "../../backend/data/techcard-library-erp.json";

const config = configs[0].config;
const coax = config.coax;
const kit: KitsetBom = {
  project: "708К.", index: "КБ1", name: config.assemblyName, run: "15", part_qty: "15", calc_source: "", calc_ops: [],
  components: [
    ...[coax.sideA, coax.sideB].flatMap((end) => end.parts.map((part) => ({ id: part.art, type: "Разъем", art: part.art, name: part.name, qty: part.qty, kd: "" }))),
    { id: "cable", type: "Кабель", art: "RTK 031", name: "RTK 031", qty: 4.8, kd: "" },
    { id: "tape", type: "Материал", art: "VL-S641", name: "Лента Vell VL-S641", qty: 0.18, kd: "" },
    { id: "tube", type: "ТУТ", art: coax.tutArt, name: coax.tutName, qty: 0.18, kd: "" },
    { id: "mark", type: "Маркировка", art: "Маркировка", name: "Маркировка", qty: 3, kd: "" },
    { id: "paint", type: "Др", art: "151437", name: "Маркер-краска Brauberg", qty: 1, kd: "" },
  ],
};
const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000031", version: 1, project: kit.project, item_index: kit.index, product_name: kit.name,
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-11-16T10:00:00+03:00", updated_at: "2026-11-16T10:00:00+03:00", issued_at: null,
};
const fields = [coax.sideA, coax.sideB].map((end, i) => ({
  "Артикул": end.article, "Тип": end.type, "Провод": "RTK 031", "L+": i ? "25" : "30",
  L1: end.l1, L2: end.l2, L3: end.l3, "Тип пина": "Обжим", "Тип экрана": "обжим",
}));
const templates = [...new Map([
  ...library.templates.map((t) => ({ ...t, code: t.title.split(" | ")[0], side: "", name: t.title.split(" | ")[1] })),
  ...[...new Set(config.ops.map((op) => op.templateId))].filter((code) => !library.templates.some((t) => t.title.startsWith(`${code} |`)))
    .map((code) => ({ id: code, code, side: "", title: `${code} | Операция`, name: "Операция", category: "" })),
].map((t) => [t.id, t])).values()];
const data: GenData = {
  templates, ops: [], terRecords: [], components: kit.components,
  assemblyInfo: { "Индекс": kit.index, "Наименование": kit.name, "Кол-во": kit.run }, partQty: 15, calcOps: [],
  coaxRecords: fields.map(coaxFromFields), wireDia: null, coaxCableDia: null,
};
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
let serverDraft: Record<string, unknown> | null = null;

beforeEach(() => {
  localStorage.clear(); serverDraft = null;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path.startsWith("/api/techcards/generator-draft")) {
      if (init?.method === "PUT") {
        const body = JSON.parse(String(init.body));
        serverDraft = { ...body, version: Number(serverDraft?.version ?? 0) + 1, updated_at: new Date().toISOString() };
        return json(serverDraft);
      }
      if (init?.method === "DELETE") { serverDraft = null; return json({}); }
      return json({ draft: serverDraft });
    }
    if (path === "/api/techcards/templates") return json({ items: templates });
    if (path.startsWith("/api/references/tech-operations?") && new URLSearchParams(path.split("?")[1]).get("tab") === "coax")
      return json({ records: fields.map((fields, i) => ({ id: String(i), fields })) });
    return json({ items: [], records: [], columns: [] });
  });
});
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

function mount(savedCard = card, savedKit = kit) {
  const onConfig = vi.fn(), onBuild = vi.fn().mockResolvedValue(true);
  const view = render(<TcGenerator card={savedCard} kit={savedKit} busy={false} userId="fakra-user" onConfig={onConfig} onBuild={onBuild} onDiscard={vi.fn()} />);
  const latest = () => onConfig.mock.calls.at(-1)![0] as typeof config;
  return { ...view, latest, onBuild };
}
const change = (name: string, value: string) => fireEvent.change(screen.getByLabelText(name), { target: { value } });
const ready = () => screen.findByRole("tab", { name: "КОАКС", selected: true });

it("binds finished/cut length, strip, marking and colour controls to the generated config", async () => {
  const { latest } = mount(); await ready();
  expect(screen.getByRole("switch", { name: "Резка с разделкой" })).toBeChecked();
  expect(screen.getByLabelText("L по КД, мм")).toHaveValue("4800");
  expect(screen.getByLabelText("Запас А, мм")).toHaveValue("30");
  expect(screen.getByLabelText("Запас В, мм")).toHaveValue("25");
  expect(screen.getByRole("status")).toHaveTextContent(/Длина резки = L \+ А \+ В = 4\s?855 мм/);
  change("Запас А, мм", "31,5");
  expect(latest().coax.cableLength).toBe(4856.5);
  change("Запас В, мм", "");
  expect(screen.getByText(/Для длины готового изделия задайте L и припуски/)).toHaveAttribute("role", "alert");
  change("Запас В, мм", "25");
  change("Длина по", "cut"); change("Длина коакс-кабеля", "4900");
  expect(latest().coax.cableLength).toBe(4900);
  change("Длина по", "finished");
  expect(latest().coax.cableLength).toBe(4856.5);
  fireEvent.click(screen.getByRole("switch", { name: "Резка с разделкой" }));
  expect(latest().ops.some((op) => op.opKey === "coaxFakraCutStrip")).toBe(false);
  change("Надпись метки 2", "Camera 15"); change("Место метки 2", "B"); change("Не ближе, мм, метка 2", "120");
  expect(latest().coax.marks[1]).toEqual({ text: "Camera 15", place: "B", minMm: 120 });
  fireEvent.click(screen.getByRole("button", { name: "Добавить метку" }));
  expect(latest().coax.marks).toHaveLength(4);
  fireEvent.click(screen.getByRole("button", { name: "Удалить метку 4" }));
  expect(latest().coax.marks).toHaveLength(3);
  fireEvent.click(screen.getByRole("switch", { name: "Прозрачная ТУТ поверх этикеток" }));
  expect(latest().coax.markProtect).toBe(false);
  change("Материал меток", "tube");
  expect(latest().coax.markMaterial).toBe("tube");
  expect(screen.queryByRole("switch", { name: "Прозрачная ТУТ поверх этикеток" })).not.toBeInTheDocument();
  change("Цвет", "PK");
  expect(latest().coax.connColor).toMatchObject({ color: "розовый", code: "PK", art: "" });
  change("Цветовая маркировка разъёмов", "laser");
  expect(latest().coax.connColor.method).toBe("laser");
  change("Цвет", "custom"); change("Название цвета", "белый"); change("Код цвета", "WH");
  expect(latest().coax.connColor).toMatchObject({ color: "белый", code: "WH", method: "laser", art: "" });
  change("Цветовая маркировка разъёмов", "tape");
  expect(latest().coax.connColor.method).toBe("tape");
  change("Цветовая маркировка разъёмов", "none");
  expect(screen.queryByLabelText("Цвет")).not.toBeInTheDocument();
  expect(latest().ops.some((op) => op.opKey === "coaxConnColor")).toBe(false);
});

it("shows colour controls on ordinary coax and hides the FAKRA strip switch", async () => {
  const ordinary = { ...kit, name: "Сборка SMA 150 мм", components: kit.components.filter((c) => c.type !== "Разъем" && c.id !== "paint")
    .concat([{ id: "sma", art: "SMA-J", name: "SMA", type: "Соед", qty: 2, kd: "" }]) };
  mount(card, ordinary); await ready();
  expect(screen.getByLabelText("Цветовая маркировка разъёмов")).toHaveValue("none");
  expect(screen.queryByRole("switch", { name: "Резка с разделкой" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Материал меток")).not.toBeInTheDocument();
  expect(screen.queryByRole("table", { name: "Метки коакс-кабеля" })).not.toBeInTheDocument();
});

it("uses paint marker articles only for paint and resolves them again when switching back", async () => {
  const { latest } = mount(); await ready();
  expect(latest().coax.connColor.art).toBe("151437");
  change("Цветовая маркировка разъёмов", "tape");
  expect(latest().coax.connColor).toMatchObject({ method: "tape", color: "оранжевый", art: "" });
  change("Цвет", "PK"); change("Цвет", "OG");
  expect(latest().coax.connColor.art).toBe("");
  change("Цветовая маркировка разъёмов", "paint");
  expect(latest().coax.connColor.art).toBe("151437");
  change("Цветовая маркировка разъёмов", "laser");
  change("Цвет", "PK"); change("Цвет", "OG");
  expect(latest().coax.connColor.art).toBe("");
});

it.each(["2FA1-NZSP-C30E2", "AMK11D-1M4Z5-Z"])("offers full stripping at cut for an assembly with only %s ends", async (art) => {
  const special = { ...kit, components: kit.components.filter((c) => c.type !== "Разъем")
    .concat([{ id: "special", art, name: art, type: "Разъем", qty: 2, kd: "" }]) };
  mount(card, special); await ready();
  expect(screen.getByRole("switch", { name: "Резка с разделкой" })).toBeChecked();
});

it("renders the generated coax route with the cable cut, labels and tubes as separate preparation cards", async () => {
  const { container, onBuild } = mount(); await ready();
  fireEvent.click(screen.getByRole("button", { name: "Подтвердить L" }));
  const scheme = await screen.findByRole("region", { name: "Схема полуфабрикатов" });
  expect([...container.querySelectorAll(".rc-card .rc-title")].map((el) => el.textContent)).toEqual([
    "Нарезанный и разделанный кабель", "Этикетки", "Нарезанные трубки", "Кабель с трубками и деталями", "Сторона А собрана",
    "Сторона В собрана", "Готовый кабель",
  ]);
  expect(within(scheme).getByText("автоматическая")).toBeInTheDocument();
  const [cut, labels, tubes] = [...container.querySelectorAll(".rc-card")] as HTMLElement[];
  expect(within(cut).getByText("Резка с разделкой (А и В)")).toBeInTheDocument();
  expect(within(cut).queryByText("Печать этикеток ×3")).toBeNull();
  expect(within(labels).getByText("Печать этикеток ×3")).toBeInTheDocument();
  expect(labels.querySelector(".rc-faces")).toBeNull();
  expect(within(tubes).getByText("Резка прозрачной ТУТ ×3")).toBeInTheDocument();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  pressBuild(); await waitFor(() => expect(onBuild).toHaveBeenCalled());
  expect(onBuild.mock.calls[0][0]).not.toHaveProperty("route"); // Automatic coax route is derived from operations.
});

it("restores legacy snapshots without enabling new coax defaults and preserves the recipe", () => {
  const seed = initState(data);
  const legacy = JSON.parse(JSON.stringify(seed));
  for (const key of ["fakraCutStrip", "lengthBasis", "finishedLength", "finishedLengthFrom", "lPlusA", "lPlusB", "markMaterial", "markProtect", "marks", "connColor"])
    delete legacy.coax[key];
  legacy.coax.len = "4800";
  const saved = genSnapshot(data.components, legacy, {}, {});
  const restored = restoreFromSnapshot(data.components, seed, saved).S;
  expect(JSON.parse(JSON.stringify(restored.coax))).toEqual(legacy.coax);
  expect(makeGen(data, restored).buildConfig()).toEqual(makeGen(data, legacy).buildConfig());
});

it("opens a saved legacy FAKRA config without a snapshot at its original cut length and recipe", async () => {
  const legacy = initState(data);
  legacy.coax = genLogic.restoreCoaxState(legacy.coax, { len: "4800", tutLen: "60", tutEnabled: true });
  const saved = makeGen(data, legacy).buildConfig();
  const { latest } = mount({ ...card, document: { operations: [], build_config: saved } }); await ready();
  expect(screen.getByLabelText("Длина коакс-кабеля")).toHaveValue("4800");
  expect(screen.getByRole("switch", { name: "Резка с разделкой" })).not.toBeChecked();
  expect(latest().coax.cableLength).toBe(4800);
  expect(latest().ops[0].opKey).toBe("coaxCut");
  expect(latest().ops).toEqual(saved.ops);
});

it("keeps BOM tape out of print-operation consumption", async () => {
  const withUnit = { ...kit, components: kit.components.map((row) => row.id === "tape" ? { ...row, qty: "0,18", unit: "м" } : row) };
  const { latest } = mount(card, withUnit); await ready();
  expect(latest().ops.find((op) => op.opKey === "coaxPrintLabel")).toMatchObject({ units: 3 });
  expect(latest().ops.find((op) => op.opKey === "coaxPrintLabel")).not.toHaveProperty("parts");
});

it("restores a saved single-ended coax cut machine and stripping of the free end", () => {
  const seed = initState(data);
  const state = { ...seed, coax: genLogic.restoreCoaxState(seed.coax, { len: "4800", sideA: { art: "SMA", name: "SMA" },
    sideB: null, stripB: true, stripMode: "machine", machine: "big" }) };
  const saved = makeGen(data, state).buildConfig();
  const restored = restoreCoaxFromConfig(seed, saved);
  expect(restored.coax).toMatchObject({ stripB: true, machine: "big" });
  expect((makeGen(data, restored).buildConfig().ops as unknown[])[0]).toEqual((saved.ops as unknown[])[0]);
});

it.each(["confirm", "edit"])("blocks calculated L across reopening until %s", async (action) => {
  const calcKit = { ...kit, name: "Кабельная сборка 120907/AA Camera 14 → Splitter B C6",
    calc_ops: [{ code: "CUT_COAX_auto", comp: "RTK 031", n: "1", l: "4,855", req: "" }] };
  const warning = "L взята из расчёта — подтвердите длину готового изделия по КД (без запаса на разъёмы)";
  const first = mount(card, calcKit); await ready();
  expect(screen.getAllByText(warning).length).toBeGreaterThan(0);
  expect(screen.getByLabelText("L по КД, мм")).toHaveValue("4855");
  expect(first.latest().coax.cableLength).toBe(4910);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  pressBuild();
  expect(first.onBuild).not.toHaveBeenCalled();
  const saved = first.latest();
  first.unmount(); localStorage.clear(); serverDraft = null;
  const second = mount({ ...card, document: { operations: [], build_config: saved } }, calcKit); await ready();
  expect(screen.getAllByText(warning).length).toBeGreaterThan(0);
  pressBuild();
  expect(second.onBuild).not.toHaveBeenCalled();
  if (action === "edit") change("L по КД, мм", "4800");
  else fireEvent.click(screen.getByRole("button", { name: "Подтвердить L" }));
  expect(screen.queryByText(warning)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Подтвердить L" })).not.toBeInTheDocument();
  const length = action === "edit" ? 4800 : 4855;
  expect(screen.getByLabelText("L по КД, мм")).toHaveValue(String(length));
  expect(second.latest().coax).toMatchObject({ cableLength: length + 55, finishedLength: length, finishedLengthFrom: "user" });
  pressBuild();
  await waitFor(() => expect(second.onBuild).toHaveBeenCalledOnce());
});

it.each([["винт", "винт"], ["обжим", "обжим"], ["винт", "обжим"], ["", "винт"]])(
  "restores legacy angled covers %s/%s without losing operations", async (coverA, coverB) => {
    const ordinaryKit = { ...kit, components: kit.components.filter((c) => c.type !== "Разъем")
      .concat([{ id: "sma", art: "SMA-RA", name: "SMA угловой", type: "Соед", qty: 2, kd: "" }]) };
    const ordinaryData = { ...data, components: ordinaryKit.components };
    const seed = initState(ordinaryData);
    const state = { ...seed, coax: genLogic.restoreCoaxState(seed.coax, {
      len: "4800", sideA: { art: "SMA-RA", name: "SMA угловой" }, sideB: { art: "SMA-RA", name: "SMA угловой" },
      pinA: "внутри корпуса", pinB: "внутри корпуса", coverA, coverB,
    }) };
    const saved = makeGen(ordinaryData, state).buildConfig();
    const { latest } = mount({ ...card, document: { operations: [], build_config: saved } }, ordinaryKit); await ready();
    expect(latest().ops).toEqual(saved.ops);
  },
);

it("uses an explicit generated coax route in the preview instead of deriving a replacement", async () => {
  const route = {
    source: "generated",
    blocks: [
      { key: "prep", name: "Заготовки по КД", ops: [1, 2, 3] },
      { key: "marked", name: "Кабель с метками и деталями", ops: [4, 5, 6, 7] },
      { key: "a", name: "Сторона А собрана", ops: [8, 9, 10, 11, 12] },
      { key: "b", name: "Сторона В собрана", ops: [13, 14, 15, 16, 17] },
      { key: "final", name: config.assemblyName, final: true, ops: [18, 19, 20] },
    ],
    edges: [{ from: "prep", to: "marked", per_unit: 1 }, { from: "marked", to: "a", per_unit: 1 },
      { from: "a", to: "b", per_unit: 1 }, { from: "b", to: "final", per_unit: 1 }],
  };
  const original = genLogic.makeGen;
  vi.spyOn(genLogic, "makeGen").mockImplementation((D, S) => {
    const gen = original(D, S);
    return { ...gen, buildConfig: () => ({ ...gen.buildConfig(), ...(S.tab === "coax" ? { route } : {}) }) };
  });
  const { container } = mount(); await ready();
  await screen.findByRole("region", { name: "Схема полуфабрикатов" });
  expect([...container.querySelectorAll(".rc-card .rc-title")].map((el) => el.textContent)).toEqual(route.blocks.map((block, i) => `${i + 1} · ${block.name}`));
  expect(screen.getByText("автоматическая")).toBeInTheDocument();
});

it("round-trips all new fields through a serialized snapshot and reopens the same form", async () => {
  const seed = initState(data);
  const state = { ...seed, coax: { ...seed.coax, lPlusA: "31,5", marks: [{ text: "Правка", place: "B" as const, minMm: 130 }],
    connColor: { method: "tape" as const, color: "белый", code: "WH", art: "white-tape" } } };
  const saved = snapshotFromConfig(JSON.parse(JSON.stringify({ gen_state: genSnapshot(data.components, state, {}, {}) })))!;
  expect(restoreFromSnapshot(data.components, seed, saved).S.coax).toEqual(state.coax);
  mount({ ...card, document: { operations: [], build_config: { gen_state: saved } } }); await ready();
  expect(screen.getByLabelText("Запас А, мм")).toHaveValue("31,5");
  expect(screen.getByLabelText("Надпись метки 1")).toHaveValue("Правка");
  expect(screen.getByLabelText("Цветовая маркировка разъёмов")).toHaveValue("tape");
  expect(screen.getByLabelText("Цвет")).toHaveValue("WH");
});

it("autosaves the new controls locally and restores the same state from the server on another computer", async () => {
  const first = mount(); await ready();
  change("L по КД, мм", "5000"); change("Надпись метки 1", "Сохранённая метка"); change("Цвет", "PK");
  first.unmount();
  const local = JSON.parse(localStorage.getItem(draftKey("fakra-user", kit.project, kit.index))!);
  expect(local.value.S.coax).toMatchObject({ finishedLength: "5000", marks: [{ text: "Сохранённая метка" }, {}, {}], connColor: { code: "PK" } });
  await waitFor(() => expect(serverDraft).not.toBeNull());
  localStorage.clear();
  mount(); await ready();
  await waitFor(() => expect(screen.getByLabelText("L по КД, мм")).toHaveValue("5000"));
  expect(screen.getByLabelText("Надпись метки 1")).toHaveValue("Сохранённая метка");
  expect(screen.getByLabelText("Цвет")).toHaveValue("PK");
});

it("opens an old local draft with its cut length and no new recipe defaults", async () => {
  const S = JSON.parse(JSON.stringify(initState(data)));
  for (const key of ["fakraCutStrip", "lengthBasis", "finishedLength", "finishedLengthFrom", "lPlusA", "lPlusB", "markMaterial", "markProtect", "marks", "connColor"]) delete S.coax[key];
  S.coax.len = "4777";
  localStorage.setItem(draftKey("fakra-user", kit.project, kit.index), JSON.stringify({ v: 1, rev: 1, savedAt: new Date().toISOString(), built: buildMark(null),
    value: { S, manual: { blocks: [], edges: [] }, terEdits: {}, coaxAdd: {} } }));
  const { latest } = mount(); await ready();
  expect(screen.getByLabelText("Длина по")).toHaveValue("cut");
  expect(screen.getByLabelText("Длина коакс-кабеля")).toHaveValue("4777");
  expect(screen.getByRole("switch", { name: "Резка с разделкой" })).not.toBeChecked();
  expect(latest().coax).not.toHaveProperty("marks");
  expect(latest().coax.cableLength).toBe(4777);
});

it("previews re-seeding a saved card, cancels without changes and applies only the proposed fields", async () => {
  const seed = initState(data);
  const state = { ...seed, note: "Ручное примечание", coax: { ...seed.coax, fakraCutStrip: false, lPlusA: "99", finishedLength: "5100",
    vswrMode: "sample" as const, vswrEvery: "13", machine: "big" as const, marks: [{ text: "Старая метка", place: "middle" as const, minMm: 140 }] } };
  const saved = { ...card, document: { operations: [], build_config: { mode: "coax", gen_state: genSnapshot(data.components, state, {}, {}) } } };
  const { latest } = mount(saved); await ready();
  const before = JSON.stringify(latest());
  fireEvent.click(screen.getByRole("button", { name: "Заполнить по составу и КД" }));
  const proposal = screen.getByRole("region", { name: "Заполнение по составу и КД" });
  expect(within(proposal).getByRole("columnheader", { name: /Сейчас/ })).toBeInTheDocument();
  expect(within(proposal).getByText(/FRX\.708К\.120907\/AA\.\{№\}/)).toBeInTheDocument();
  expect(JSON.stringify(latest())).toBe(before);
  fireEvent.click(within(proposal).getByRole("button", { name: "Отмена" }));
  expect(JSON.stringify(latest())).toBe(before);
  fireEvent.click(screen.getByRole("button", { name: "Заполнить по составу и КД" }));
  fireEvent.click(screen.getByRole("button", { name: "Применить заполнение" }));
  expect(latest().coax).toMatchObject({ fakraCutStrip: true, finishedLength: 4800, lPlusA: 30, lPlusB: 25, tutLengths: { A: 35, B: 35, middle: 55 },
    vswrControl: { mode: "sample", every: 13 } });
  expect(screen.getByText("Защитная ТУТ: концевые метки — 35 мм, центральная — 55 мм.")).toBeInTheDocument();
  expect(screen.queryByText("Длина ТУТ")).not.toBeInTheDocument();
  expect(latest().coax.marks[0].text).toBe("FRX.708К.120907/AA.{№}");
  expect(screen.getByRole("button", { name: "Подтвердить L" })).toBeInTheDocument();
  expect(latest().ops[0].machine?.model).toBe("BZW-50X");
  expect(screen.queryByRole("button", { name: "Применить заполнение" })).not.toBeInTheDocument();
  expect(screen.getByText("{№} — номер сборки 1…N")).toBeInTheDocument();
});

it("offers every dictionary colour and requires an explicit conflict choice", async () => {
  const conflictKit = { ...kit, components: kit.components.filter((row) => row.id !== "paint").concat([
    { id: "white", type: "Др", art: "white-marker", name: "Маркер белый", qty: 1, kd: "" },
    { id: "blue", type: "Др", art: "blue-marker", name: "Маркер голубой", qty: 1, kd: "" },
  ]) };
  const { latest, onBuild } = mount(card, conflictKit); await ready();
  expect(screen.getByText("В составе несколько цветов маркера-краски — выберите цвет.")).toBeInTheDocument();
  expect([...within(screen.getByLabelText("Цвет")).getAllByRole("option")].map((option) => (option as HTMLOptionElement).value))
    .toEqual(["WH", "BU", "GN", "VT", "YE", "RD", "BK", "OG", "PK", "custom"]);
  fireEvent.click(screen.getByRole("button", { name: "Подтвердить L" }));
  vi.spyOn(window, "confirm").mockReturnValue(true);
  pressBuild();
  expect(onBuild).not.toHaveBeenCalled();
  expect(screen.getByText("В составе несколько цветов маркера-краски — выберите цвет.", { selector: ".gen-error" })).toBeInTheDocument();
  change("Цвет", "WH");
  expect(latest().coax.connColor).toMatchObject({ color: "белый", code: "WH", art: "white-marker" });
  expect(screen.queryByText("В составе несколько цветов маркера-краски — выберите цвет.")).not.toBeInTheDocument();
  change("Цвет", "BU");
  expect(latest().coax.connColor.art).toBe("blue-marker");
});

it.each(["all", "sample", "none"] as const)("restores saved form VSWR %s before an older build config", async (mode) => {
  const state = initState(data); state.coax.vswrMode = mode; state.coax.vswrEvery = "17";
  const saved = { ...card, document: { operations: [], build_config: { mode: "coax", coax: { vswrControl: { mode: mode === "none" ? "all" : "none" } },
    gen_state: genSnapshot(data.components, state, {}, {}) } } };
  const { latest } = mount(saved); await ready();
  expect(latest().coax.vswrControl).toEqual({ mode, every: mode === "sample" ? 17 : null });
});

it.each(["all", "sample", "none"] as const)("keeps VSWR %s across local and server draft reopen", async (mode) => {
  const first = mount(); await ready();
  const group = screen.getByRole("radiogroup", { name: "Контроль КСВН" });
  fireEvent.click(within(group).getByRole("radio", { name: mode === "all" ? "Каждое изделие" : mode === "sample" ? /Выборочно/ : "Не проверять" }));
  // A second edit arms autosave even when the default VSWR mode was selected.
  change("L по КД, мм", "5100");
  const wanted = first.latest().coax.vswrControl;
  first.unmount();
  await waitFor(() => expect(serverDraft).not.toBeNull());
  const second = mount(); await ready();
  await waitFor(() => expect(second.latest().coax.vswrControl).toEqual(wanted));
  second.unmount(); localStorage.clear();
  const third = mount(); await ready();
  await waitFor(() => expect(third.latest().coax.vswrControl).toEqual(wanted));
});
