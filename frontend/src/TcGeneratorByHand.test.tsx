import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import type { KitsetBom, ProjectTechcard } from "./api";

/* «Чертёж → схема» (владелец 05.10): «Поправить вручную» из «Типовой» и «Черновики с чертежей» в ручной сборке. */
const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000001", version: 1, project: "P-100", item_index: "КБ1", product_name: "Жгут XHP 150мм",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-09-23T10:00:00+03:00", updated_at: "2026-09-23T10:00:00+03:00", issued_at: null,
};

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const kit = (): KitsetBom => ({
  project: "P-100", index: "КБ1", name: "Жгут XHP 150мм", run: "1", part_qty: "1", calc_source: "", calc_ops: [],
  components: [
    { id: "kit-0", kd: "", type: "Провод", name: "МГТФ 0,12", art: "МГТФ 0,12", qty: 0.3 },
    { id: "kit-1", kd: "", type: "Терминал", name: "Контакт XH", art: "SXH-001T-P0.6", qty: 4 },
    { id: "kit-2", kd: "", type: "Соед", name: "Колодка XH 2", art: "XHP-2", qty: 2 },
  ],
});

/** Ответ POST /api/techcards/assembly/from-generator: типовая сборка как ручная, операции с полями генератора. */
const fromGenerator = () => ({
  mode: "manual", assemblyIndex: "КБ1", assemblyName: "Жгут XHP 150мм", partQty: 1,
  wires: [{ name: "МГТФ 0,12", art: "МГТФ 0,12", qty: 2, length: 160 }], twist: { enabled: false }, bigAssembly: false,
  ops: [
    { type: "cutWireStrip", seq: 1, templateId: "cut", opNum: "CUT_WIRE_auto", tPrep: "60", tOp: "4",
      wires: [{ name: "МГТФ 0,12", art: "МГТФ 0,12", qty: 2, length: 160 }], strip: { a: true, lenA: "5", b: true, lenB: "5" } },
    { type: "prsTerm", seq: 2, side: "A", templateId: "prsA", opNum: "PRS", tPrep: "120", tOp: "6",
      ep: { termArt: "SXH-001T-P0.6", connArt: "XHP-2" }, wires: [{ name: "МГТФ 0,12", art: "МГТФ 0,12", qty: 2, length: 160 }] },
  ],
  route: { blocks: [{ key: "b1", name: "Жгут XHP 150мм", ops: [1, 2], final: true }], edges: [] },
});

type Calls = Array<{ path: string; method: string; body: Record<string, unknown> | null }>;

function mockApi(drafts: unknown[] | "error", extra?: (path: string, init?: RequestInit) => Response | undefined) {
  const calls: Calls = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    calls.push({ path, method: init?.method || "GET", body: init?.body ? JSON.parse(String(init.body)) : null });
    const custom = extra?.(path, init);
    if (custom) return custom;
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [
      { id: "cut", code: "CUT_WIRE_auto", side: "", title: "CUT_WIRE_auto | Резка", name: "Резка", category: "Резка", steps: 1, tooling: 1 },
      { id: "prsA", code: "PRS", side: "A", title: "PRS_TERMINAL | Опрессовка терминалов (А)", name: "", category: "Опрессовка терминалов", steps: 1, tooling: 1 },
    ] });
    if (path.startsWith("/api/references/tech-operations?")) return json({ metadata: null, columns: [], records: [] });
    if (path === "/api/references/technology/resolve-terminal") return json({ applicator: "", crimp_height: "", pull_force: "", by_section: [] });
    if (path.startsWith("/api/techcards/assembly/drafts?")) return drafts === "error" ? json({ detail: "Нет доступа" }, 403) : json({ items: drafts });
    throw new Error(`Unexpected request: ${path}`);
  });
  return calls;
}

afterEach(() => vi.restoreAllMocks());

function mount() {
  const onBuild = vi.fn();
  render(<TcGenerator card={card} busy={false} onBuild={onBuild} onDiscard={vi.fn()} kit={kit()} />);
  return onBuild;
}

const openManual = async () => {
  fireEvent.click(await screen.findByRole("tab", { name: "Ручная сборка" }, { timeout: 5000 }));
  expect(await screen.findByRole("tab", { name: "Ручная сборка", selected: true })).toBeInTheDocument();
};

it("«Поправить вручную» opens the typical assembly in the manual tab with blocks and generator operations", async () => {
  const calls = mockApi([], (path, init) => (path === "/api/techcards/assembly/from-generator" && init?.method === "POST" ? json(fromGenerator()) : undefined));
  const onBuild = mount();
  expect(await screen.findByText("Резка — МГТФ 0,12")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Поправить вручную" }));

  expect(await screen.findByRole("tab", { name: "Ручная сборка", selected: true })).toBeInTheDocument();
  // На сервер ушла текущая типовая сборка; русские названия операций — как их назвал генератор.
  const request = calls.find((call) => call.path === "/api/techcards/assembly/from-generator")!;
  const sent = request.body!.config as { ops: Array<{ type: string; label?: string }> };
  expect(sent.ops.map((op) => [op.type, op.label])).toEqual([["cutWire", "Резка — МГТФ 0,12"], ["prsTerm", "Опрессовка терминалов А1"], ["prsTerm", "Опрессовка терминалов В1"]]);

  // Блок схемы и обе операции; название — русское, не тип; параметры — серой строкой, без полей.
  expect(screen.getByRole("region", { name: "Блок «Жгут XHP 150мм»" })).toBeInTheDocument();
  const first = screen.getByLabelText("Операция 1: Резка с разделкой");
  expect(within(first).getByLabelText("Название операции 1").tagName).toBe("B");
  expect(first).toHaveTextContent("1 пров. · L 160 мм · зачистка А 5, В 5 мм");
  expect(first).not.toHaveTextContent("cutWireStrip");
  expect(screen.getByLabelText("Операция 2: Опрессовка терминалов")).toHaveTextContent("сторона А · 1 пров. · L 160 мм · терминал SXH-001T-P0.6 · разъём XHP-2");

  // Норма правится, параметров и «на изделие» нет; операцию можно убрать; сборка идёт как обычная ручная.
  fireEvent.click(screen.getByRole("button", { name: /^Развернуть операцию 1:/ }));
  expect(screen.queryByLabelText(/^Параметры:/)).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/^На изделие:/)).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Подготовка, мин: Резка с разделкой"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("button", { name: "Удалить операцию 2: Опрессовка терминалов" }));
  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  const [config, draftId] = onBuild.mock.calls[0];
  expect(draftId).toBeUndefined();
  expect(config).toMatchObject({ mode: "manual", wires: [{ art: "МГТФ 0,12", length: 160 }], twist: { enabled: false } });
  expect(config.ops).toHaveLength(1);
  expect(config.ops[0]).toMatchObject({
    type: "cutWireStrip", seq: 1, templateId: "cut", tPrep: "120", tOp: "4", label: "Резка с разделкой",
    wires: [{ length: 160 }], strip: { a: true, lenA: "5", b: true, lenB: "5" },
  });
  expect(config.ops[0]).not.toHaveProperty("qty");
});
