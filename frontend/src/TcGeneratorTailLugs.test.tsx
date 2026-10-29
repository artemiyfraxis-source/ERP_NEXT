import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterAll, beforeAll, beforeEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import { resetPinoutHousings } from "./PinoutDiagram";
import { resetPreviewTemplates } from "./GenDrawingPreview";
import { genSnapshot } from "./genBuildState";
import { DSA, FULL_OPS, FULL_TEMPLATES, harness } from "./tcGenRealHarness.fixture";
import type { KitsetBom, ProjectTechcard } from "./api";

vi.setConfig({ testTimeout: 150_000 });

/*
 * Владелец 29.10: «нет возможности сделать на свободные концы НШВИ, НКИ и т.д.». «Жгут НВ4» (хвосты 0,2 и 0,12 мм² на
 * стороне В): «Свободные концы» → «Наконечник», тип из справочника наконечников, наконечник по сечению на каждый провод
 * (нет — красным), свой выбор на провод — и в собранной карте зачистка и опрессовка наконечников.
 */
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
const LUGS = [
  { terComponent: "НШВИ", terArticle: "НШВИ 0.25-8", mm2From: "0,2", mm2To: "0,2", terStrip: "10", terLPlus: "2" },
  { terComponent: "НКИ", terArticle: "НКИ 1.5-4", mm2From: "0,5", mm2To: "1,5", terApplicator: "M31" },
];

beforeAll(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
});
afterAll(() => { vi.unstubAllGlobals(); });
beforeEach(() => {
  resetPinoutHousings();
  resetPreviewTemplates();
  window.localStorage.clear();
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    if (path === "/api/techcards/templates") {
      const templates = [...FULL_TEMPLATES, { id: "t-nki", title: "PRS_HKИ/НВИ/РПИ | Опрессовка наконечников", category: "Опрессовка наконечников" }];
      return json({ metadata: null, items: templates.map((t) => ({ ...t, code: t.title.split("|")[0].trim(), side: "", name: t.category })) });
    }
    if (path.startsWith("/api/references/tech-operations?tab=op")) return json({ metadata: null, columns: [], records: FULL_OPS.map((o, i) => ({ id: `op${i}`, fields: { ...o } })) });
    if (path.startsWith("/api/references/tech-operations?tab=ter")) return json({ metadata: null, columns: [], records: [
      ...DSA.terRecords.map((t, i) => ({ id: `ter${i}`, ter_kind: "contact", fields: { terArticle: t.article, terSeries: String(t.fields?.terSeries ?? ""),
        terApplicator: t.applicator, mm2From: t.mm2From, mm2To: t.mm2To, terStep: t.step, terStrip: t.strip, terLPlus: t.lPlus } })),
      ...LUGS.map((fields, i) => ({ id: `lug${i}`, ter_kind: "lug", fields })),
    ] });
    if (path.includes("connector-families")) return json({ items: [], match: null });
    if (path === "/api/references/technology/resolve-terminal") return json({ applicator: "", crimp_height: "", pull_force: "", by_section: [] });
    if (path.includes("/harness/") && method === "GET") return json({ document: null, design: null, revision: null, warnings: [] });
    if (method !== "GET") throw new Error(`Unexpected write: ${method} ${path}`);
    return json({ metadata: null, items: [], columns: [], records: [] });
  });
});

const topology = harness(DSA);
const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000714", version: 1, project: "713К.4", item_index: "КБ1", product_name: "Кабельная сборка Жгут НВ4",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0,
  document: { build_config: { tab: "complex", gen_state: genSnapshot(DSA.components, topology, {}, {}) } } as ProjectTechcard["document"],
  manual_operation_keys: [], created_at: "2026-10-29T10:00:00+03:00", updated_at: "2026-10-29T10:00:00+03:00", issued_at: null,
};
const kit: KitsetBom = {
  project: "713К.4", index: "КБ1", name: card.product_name, run: "500", part_qty: "500", calc_source: "", calc_ops: [],
  components: DSA.components.map((c) => ({ id: c.id, type: c.type, name: c.name, art: c.art, kd: "", qty: c.qty })),
};
const switchOn = (title: string) => {
  const sw = [...document.querySelectorAll<HTMLElement>(".gsw")].find((el) => el.querySelector(".gsw-text > span")?.textContent === title)!
    .querySelector("input") as HTMLInputElement;
  if (!sw.checked) fireEvent.click(sw);
};
const radio = (group: string, name: string) => fireEvent.click([...document.querySelectorAll(`[role="radiogroup"][aria-label="${group}"] button`)]
  .find((el) => el.textContent === name)!);
const rows = () => [...screen.getByRole("table", { name: "Наконечники свободных концов" }).querySelectorAll("tbody tr")]
  .map((tr) => [...tr.querySelectorAll("td")].map((td) => td.textContent || ""));

it("«Наконечник» on the free ends: type from the reference, a lug per wire section, a missing one in red, own pick per wire", async () => {
  const onBuild = vi.fn();
  render(<TcGenerator card={card} kit={kit} busy={false} onBuild={onBuild} onDiscard={vi.fn()} />);
  await screen.findByText("Обработка", { selector: "h3" });
  switchOn("Свободные концы");
  expect(screen.queryByRole("table", { name: "Наконечники свободных концов" })).toBeNull();
  radio("Обработка свободных концов", "Наконечник");
  const type = screen.getByLabelText("Тип наконечника") as HTMLSelectElement;
  expect([...type.options].map((o) => o.value)).toEqual(["НШВИ", "НКИ"]);
  expect(type.value).toBe("НШВИ");
  // 0,2 мм² — НШВИ 0.25-8 (зачистка и L+ из справочника), 0,12 мм² — нет наконечника.
  expect(rows()).toHaveLength(4);
  expect(rows()[0]).toEqual(["НВ4 0,2 красный0,2 мм²", "1", expect.stringContaining("по сечению: НШВИ 0.25-8"), "10 мм", "2 / —"]);
  expect(rows()[2]).toEqual(["НВ4 0,12 белый0,12 мм²", "1", expect.stringContaining("нет наконечника под сечение"), "—", "—"]);
  expect(document.querySelectorAll(".lug-problem.tone-danger")).toHaveLength(2);
  // Свой наконечник на белый 0,12: НКИ (в справочнике нет длины зачистки — из поля формы, отмечено).
  fireEvent.change(screen.getByLabelText("Наконечник — НВ4 0,12 белый"), { target: { value: "НКИ 1.5-4" } });
  await waitFor(() => expect(rows()[2][2]).not.toContain("нет наконечника"));
  expect(rows()[2][3]).toBe("3 ммнет в БД");
  // Сборка: зачистка и опрессовка втулочных, зачистка и опрессовка кабельных; синий 0,12 без наконечника — предупреждение.
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click([...document.querySelectorAll("button")].find((el) => el.textContent === "Создать")!);
  if (document.querySelector('[role="dialog"][aria-label="Распиновка не проверена"]')) {
    fireEvent.click([...document.querySelectorAll("button")].find((el) => el.textContent === "Создать всё равно")!);
  }
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  expect(String(confirm.mock.calls[0][0])).toContain("Свободные концы «НВ4 0,12 синий»: нет наконечника под сечение");
  const config = onBuild.mock.calls[0][0] as { ops: Array<{ opKey: string; label?: string; templateId: string; ep?: { termArt: string } }> };
  const lugOps = config.ops.filter((o) => /Lug/.test(o.opKey));
  expect(lugOps.map((o) => [o.opKey, o.ep?.termArt ?? ""])).toEqual([
    ["stripLugF", ""], ["prsLugF1", "НШВИ 0.25-8"], ["stripLugL", ""], ["prsLugL1", "НКИ 1.5-4"]]);
  expect(lugOps.find((o) => o.opKey === "prsLugL1")!.templateId).toBe("t-nki");

});
