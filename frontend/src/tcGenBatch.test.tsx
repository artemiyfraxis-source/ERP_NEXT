import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import type { KitsetBom, ProjectTechcard } from "./api";
import { runBatch, type BatchRefs } from "./tcGenBatch";
import { pressBuild } from "./test/pressBuild";

const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000001", version: 1, project: "P-100", item_index: "КБ1", product_name: "Жгут XHP 150мм",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-09-23T10:00:00+03:00", updated_at: "2026-09-23T10:00:00+03:00", issued_at: null,
};

const refs: BatchRefs = {
  templates: [
    { id: "cut", title: "CUT_WIRE_auto | Резка", category: "Резка" },
    { id: "prsA", title: "PRS_TERMINAL_2.50 | Опрессовка терминалов (А)", category: "Опрессовка терминалов" },
    { id: "prsB", title: "PRS_TERMINAL_2.50 | Опрессовка терминалов (В)", category: "Опрессовка терминалов" },
    { id: "insA", title: "INS_terminal_2.50 | Монтаж терминалов (А)", category: "Монтаж терминалов" },
    { id: "insB", title: "INS_terminal_2.50 | Монтаж терминалов (В)", category: "Монтаж терминалов" },
  ],
  op: [{ opNumber: "CUT_WIRE_auto", opName: "Резка", tOp: "12", tPrep: "60", tMachine: "3" }],
  ter: [{ id: "t1", fields: { terArticle: "SXH-001T-P0.6", terLPlus: "5", terStep: "2.50", terApplicator: "S12" } }],
  coax: [], kab_awg: [], kab_coax: [],
};

const kit: KitsetBom = {
  project: "P-100", index: "КБ1", name: "Жгут XHP 150мм", run: "10", part_qty: "10",
  calc_source: "https://docs.google.com/spreadsheets/d/calc/edit",
  components: [
    { id: "kit-0", kd: "", type: "Провод", name: "МГТФ 0,12", art: "МГТФ 0,12", qty: "0.3" },
    { id: "kit-1", kd: "", type: "Терминал", name: "Контакт XH", art: "SXH-001T-P0.6", qty: "4" },
    { id: "kit-2", kd: "", type: "Соед", name: "Колодка XH 2", art: "XHP-2", qty: "2" },
  ],
  calc_ops: [],
};

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

it("gives the same config as opening the generator and pressing «Создать»", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: refs.templates });
    if (path.startsWith("/api/references/tech-operations?")) {
      const tab = new URLSearchParams(path.split("?")[1]).get("tab") as keyof BatchRefs;
      const rows = tab === "ter" ? refs.ter : ((refs[tab] || []) as Array<Record<string, string>>).map((fields, i) => ({ id: `${tab}${i}`, fields }));
      return json({ metadata: null, columns: [], records: rows.map((row) => ({ tab, display_text: "", source_sheet: "", ...row })) });
    }
    if (path === "/api/references/technology/resolve-terminal") return json({ applicator: "S12", crimp_height: "", pull_force: "", by_section: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
  const onBuild = vi.fn();
  render(<TcGenerator card={card} busy={false} onBuild={onBuild} onDiscard={vi.fn()} kit={kit} />);
  expect(await screen.findByText("Резка — МГТФ 0,12")).toBeInTheDocument();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  pressBuild();
  await waitFor(() => expect(onBuild).toHaveBeenCalled());

  const [batch] = runBatch({ refs, items: [{ key: "1", bom: kit }] }).items;
  expect(batch.error).toBe("");
  expect(batch.config).toEqual(onBuild.mock.calls[0][0]);
  expect((batch.config?.ops as Array<{ templateId: string }>).map((op) => op.templateId)).toEqual(["cut", "prsA", "insA", "prsB", "insB"]);
});

it("reports what the form would refuse instead of building", () => {
  const empty = { ...kit, components: [] };
  const broken = { ...kit, components: null as unknown as KitsetBom["components"] };
  const { items } = runBatch({ refs, items: [{ key: "a", bom: empty }, { key: "b", bom: broken }] });
  expect(items[0]).toEqual({ key: "a", config: null, error: "Выберите шаблон хотя бы для одной операции.", warnings: [] });
  expect(items[1].config).toBeNull();
  expect(items[1].error).toMatch(/^генератор не справился: /);
});
