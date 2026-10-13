import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import type { KitsetBom, ProjectTechcard } from "./api";

/* «Предложить из КД» на «Ручной сборке»: схема из чертежа жгута в КД позиции (POST …/harness/{index}/derive), карточки
   операций — подбором генератора, версия чертежа — в build_config. */
const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000009", version: 1, project: "P-300", item_index: "КБ7", product_name: "Жгут",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-10-06T10:00:00+03:00", updated_at: "2026-10-06T10:00:00+03:00", issued_at: null,
};

const kit = (): KitsetBom => ({
  project: "P-300", index: "КБ7", name: "Жгут", run: "10", part_qty: "10", calc_source: "", calc_ops: [],
  components: [{ id: "kit-0", kd: "", type: "Провод", name: "МГТФ 0,35", art: "МГТФ 0,35", qty: 0.6 }],
});

/** Схема из КД, как её отдаёт derive: резка с разделкой и опрессовка — в блоке маршрута, сращивание и сборка — в итоге. */
const derived = () => ({
  mode: "manual", assemblyIndex: "КБ7", assemblyName: "Жгут", partQty: 10,
  ops: [
    { type: "cutWireStrip", side: "", tPrep: "", tOp: "", wires: [{ name: "W1", art: "МГТФ 0,35", qty: 1, length: 300 }], strip: { a: true, b: true },
      label: "Резка с разделкой: W1", kdData: [{}], origin: "harness:r1:cutWireStrip.AB", seq: 1 },
    { type: "prsTerm", side: "A", tPrep: "", tOp: "", ep: { connName: "X1", connArt: "", label: "X1", termArt: "ЛП-3", termName: "" },
      wires: [{ name: "W1", art: "МГТФ 0,35", qty: 1 }], label: "Опрессовка терминала (А) — X1", kdData: [{}], origin: "harness:X1:prsTerm.A", seq: 2 },
    { type: "splice", side: "", tPrep: "", tOp: "", units: 1, label: "Сращивание: J1", kdData: [{}], origin: "harness:j1:splice", seq: 3 },
    { type: "customOp", label: "Сборка жгута", qty: "1", tPrep: "", tOp: "", opNum: "", steps: [], tooling: [], origin: "harness:s1:assembly", seq: 4 },
  ],
  route: {
    blocks: [
      { key: "route:r1", name: "Провод W1", ops: [1, 2], final: false, kind: "semiFinished" },
      { key: "route:r2", name: "Жгут", ops: [3, 4], final: true },
    ],
    edges: [{ from: "route:r1", to: "route:r2", per_unit: 1 }],
  },
});

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function mockApi(revision: number | null) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = decodeURIComponent(String(input));
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [
      { id: "cut2", code: "CUT_WIRE+2strip", side: "", title: "CUT_WIRE+2strip | Резка с разделкой", name: "Резка с разделкой", category: "Резка", steps: 8, tooling: 3 },
      { id: "prsA", code: "PRS_TERMINAL_hand", side: "A", title: "PRS_TERMINAL_hand | Опрессовка терминалов (A)", name: "Опрессовка терминалов",
        category: "Опрессовка терминалов", steps: 8, tooling: 3 },
    ] });
    if (path.startsWith("/api/references/tech-operations?")) {
      const tab = new URLSearchParams(path.split("?")[1]).get("tab");
      return json({ metadata: null, columns: [], records: tab === "op"
        ? [{ id: "o1", tab: "op", display_text: "", source_sheet: "", fields: { opNumber: "CUT_WIRE+2strip", opName: "Резка с разделкой", tPrep: "600", tOp: "5" } }] : [] });
    }
    if (path.startsWith("/api/techcards/assembly/drafts?")) return json({ items: [] });
    if (path === "/api/techcards/kitsets/P-300/harness/КБ7") return json({ document: revision === null ? null : { id: "h1" }, design: revision === null ? null : { schemaVersion: 1 }, revision, warnings: [] });
    if (path === "/api/techcards/kitsets/P-300/harness/КБ7/derive") return json({ build_config: derived(), warnings: ["Длина W1 взята по чертежу"] });
    throw new Error(`Unexpected request: ${path}`);
  });
}

afterEach(() => vi.restoreAllMocks());

function mount(revision: number | null, props: Partial<Parameters<typeof TcGenerator>[0]> = {}) {
  const fetch = mockApi(revision);
  const onBuild = vi.fn();
  render(<TcGenerator card={card} busy={false} onBuild={onBuild} onDiscard={vi.fn()} kit={kit()} {...props} />);
  return { onBuild, fetch };
}

async function openManual() {
  fireEvent.click(await screen.findByText("Ручная сборка", { selector: "[role=tab]" }, { timeout: 10000 }));
  expect(await screen.findByText("Ручная сборка", { selector: "[role=tab][aria-selected=true]" }, { timeout: 10000 })).toBeInTheDocument();
}

const blocks = () => [...document.querySelectorAll("[aria-label=\"Блоки схемы\"] .rc-card")].map((block) => block.textContent || "");
const derives = (fetch: ReturnType<typeof mockApi>) => fetch.mock.calls.filter(([input]) => decodeURIComponent(String(input)).endsWith("/derive")).length;

it("keeps «Предложить из КД» off with a hint when the position has no harness drawing", async () => {
  const { fetch } = mount(null);
  await openManual();
  const button = await screen.findByRole("button", { name: "Предложить из КД" });
  await waitFor(() => expect(button.parentElement).toHaveAttribute("title", "В КД нет чертежа жгута"));
  expect(button).toBeDisabled();
  expect(derives(fetch)).toBe(0);
});

it("takes the КД scheme into an empty scheme with the generator's cards and saves the drawing version with it", async () => {
  const { onBuild } = mount(1);
  await openManual();
  const button = await screen.findByRole("button", { name: "Предложить из КД" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);

  const bar = await screen.findByRole("region", { name: "Предложение из КД" });
  expect(within(bar).getByRole("status")).toHaveTextContent("Схема предложена из КД: 4 оп.");
  expect(within(bar).getByText("Длина W1 взята по чертежу")).toBeInTheDocument();
  // Блоки — по маршруту КД, ничего не свалено в «Новое из КД».
  expect(blocks()).toEqual([expect.stringContaining("Провод W1"), expect.stringContaining("Жгут")]);
  expect(blocks().some((text) => text.includes("Новое из КД"))).toBe(false);
  expect(screen.queryByRole("button", { name: /КД изменена/ })).not.toBeInTheDocument();
  fireEvent.click(within(bar).getByRole("button", { name: "Закрыть" }));
  expect(screen.queryByRole("region", { name: "Предложение из КД" })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  const config = onBuild.mock.calls[0][0];
  expect(config).toMatchObject({ mode: "manual", kdHarnessVersion: "1" });
  const ops = config.ops as Array<Record<string, unknown>>;
  // Резка — карточка по подсказке генератора и нормы БД.ОП; опрессовка — по категории и стороне; сращиванию карточки нет.
  expect(ops.find((op) => op.type === "cutWireStrip")).toMatchObject({ templateId: "cut2", opNum: "CUT_WIRE+2strip", tPrep: "600", tOp: "5" });
  expect(ops.find((op) => op.type === "prsTerm")).toMatchObject({ templateId: "prsA", opNum: "PRS_TERMINAL_hand", origin: "harness:X1:prsTerm.A" });
  expect(ops.find((op) => op.type === "splice")).not.toHaveProperty("templateId");
  expect(ops.find((op) => op.type === "customOp")).toMatchObject({ label: "Сборка жгута", origin: "harness:s1:assembly" });
});

/** Схема мастера, взятая из версии v0: резка из КД, своя прозвонка и опрессовка стороны В, которой в КД больше нет. */
const saved = {
  mode: "manual", assemblyIndex: "КБ7", assemblyName: "Жгут", partQty: 10, kdHarnessVersion: "0",
  ops: [
    { type: "cutWireStrip", seq: 1, label: "Резка с разделкой: W1", tPrep: "", tOp: "", wires: [{ name: "W1", art: "МГТФ 0,35", qty: 1, length: 280 }],
      strip: { a: true, b: true }, kdData: [{}], origin: "harness:r1:cutWireStrip.AB" },
    { type: "prsTerm", seq: 2, label: "Опрессовка терминала (В) — X2", side: "B", tPrep: "", tOp: "", ep: { connName: "X2", termArt: "ЛП-3" },
      wires: [{ name: "W1", art: "МГТФ 0,35", qty: 1 }], kdData: [{}], origin: "harness:X2:prsTerm.B" },
    { type: "customOp", seq: 3, label: "Прозвонка", qty: "1", tPrep: "", tOp: "30", steps: [], tooling: [] },
  ],
  route: {
    blocks: [{ key: "route:r1", name: "Провод W1", ops: [1, 2], final: false }, { key: "route:r2", name: "Жгут", ops: [3], final: true }],
    edges: [{ from: "route:r1", to: "route:r2", per_unit: 1 }],
  },
};
const savedCard: ProjectTechcard = {
  ...card, version: 2, document: { kit: { part_quantity: "10", mode: "manual" }, operations: [], warnings: [], build_config: saved },
};

it("marks a scheme from an older drawing, shows what the КД would change and merges only on «Применить»", async () => {
  const { onBuild, fetch } = mount(1, { card: savedCard });
  expect(await screen.findByText("Ручная сборка", { selector: "[role=tab][aria-selected=true]" }, { timeout: 10000 })).toBeInTheDocument();
  const stale = await screen.findByRole("button", { name: "КД изменена — предложить заново" });
  expect(screen.queryByRole("button", { name: "Предложить из КД" })).not.toBeInTheDocument();

  fireEvent.click(stale);
  const bar = await screen.findByRole("region", { name: "Предложение из КД" });
  // Добавятся опрессовка А, сращивание, сборка; изменится резка (длина и карточка); опрессовки В больше нет в КД.
  expect(within(bar).getByRole("status")).toHaveTextContent("Предложение из КД: добавится 3, изменится 1, нет в КД 1");
  expect(within(screen.getByRole("group", { name: "Порядок операций" })).getAllByRole("listitem")).toHaveLength(3);
  fireEvent.click(within(bar).getByRole("button", { name: "Отмена" }));
  expect(screen.queryByRole("region", { name: "Предложение из КД" })).not.toBeInTheDocument();
  expect(within(screen.getByRole("group", { name: "Порядок операций" })).getAllByRole("listitem")).toHaveLength(3);

  fireEvent.click(screen.getByRole("button", { name: "КД изменена — предложить заново" }));
  fireEvent.click(within(await screen.findByRole("region", { name: "Предложение из КД" })).getByRole("button", { name: "Применить" }));
  expect(derives(fetch)).toBe(2);
  const order = within(screen.getByRole("group", { name: "Порядок операций" }));
  expect(order.getAllByRole("listitem")).toHaveLength(6);
  expect(order.getByText("нет в КД")).toBeInTheDocument();
  expect(blocks().some((text) => text.includes("Новое из КД"))).toBe(false);
  // Схема теперь из текущей версии — отметки нет, кнопка обычная.
  expect(screen.queryByRole("button", { name: "КД изменена — предложить заново" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Предложить из КД" })).toBeEnabled();

  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  const config = onBuild.mock.calls[0][0];
  expect(config.kdHarnessVersion).toBe("1");
  const ops = config.ops as Array<Record<string, unknown>>;
  expect(ops.find((op) => op.origin === "harness:X2:prsTerm.B")).toMatchObject({ kdGone: true });
  expect(ops.find((op) => op.origin === "harness:r1:cutWireStrip.AB")).toMatchObject({ templateId: "cut2", wires: [expect.objectContaining({ length: 300 })] });
  expect(ops.find((op) => op.label === "Прозвонка")).toMatchObject({ type: "customOp", tOp: "30" });
});
