import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterAll, beforeAll, beforeEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import { resetPinoutHousings } from "./PinoutDiagram";
import { resetPreviewTemplates } from "./GenDrawingPreview";
import { genSnapshot } from "./genBuildState";
import { DSA, FULL_OPS, FULL_TEMPLATES, harness } from "./tcGenRealHarness.fixture";
import type { KitsetBom, ProjectTechcard } from "./api";

// Вся форма перерисовывается на каждое поле: в jsdom ~1 с на правку, прогон — около минуты.
vi.setConfig({ testTimeout: 60_000 });

/*
 * Владелец 17.10: «проверить функциональность генератора — что всё, что мы делали, я смогу повторить сам». 713К.4 КБ1:
 * разъёмы, кабели, контакты, сростки и хвосты уже набраны (снимок формы), всю обработку владелец задаёт полями формы —
 * оплётка XP-3 по ветвям, ТТК 8/2 поверх разъёмов, ТТК 12/4 на ствол, свивка, пайка сростков с ТТК 3/1, лужение хвостов,
 * маркировка «По разъёмам». «Создать» отдаёт ту же сборку, что набранная в коде (tcGenRealHarness.fixture.ts): операции
 * и 16 ПФ.
 */
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
const records = (rows: Array<Record<string, string>>) => ({ metadata: null, columns: [], records: rows.map((fields, i) => ({ id: `r${i}`, fields })) });

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
    if (path === "/api/techcards/templates") return json({ metadata: null, items: FULL_TEMPLATES.map((t) => ({ ...t, code: t.title.split("|")[0].trim(), side: "", name: t.category })) });
    if (path.startsWith("/api/references/tech-operations?tab=op")) return json(records(FULL_OPS.map((o) => ({ ...o }))));
    if (path.startsWith("/api/references/tech-operations?tab=ter")) return json(records(DSA.terRecords.map((t) => ({
      terArticle: t.article, terSeries: String(t.fields?.terSeries ?? ""), terApplicator: t.applicator, mm2From: t.mm2From, mm2To: t.mm2To,
      terStep: t.step, terStrip: t.strip, terLPlus: t.lPlus }))));
    if (path.includes("connector-families")) return json({ items: [], match: null });
    if (path === "/api/references/technology/resolve-terminal") return json({ applicator: "", crimp_height: "", pull_force: "", by_section: [] });
    if (path.includes("/harness/") && method === "GET") return json({ document: null, design: null, revision: null, warnings: [] });
    if (method !== "GET") throw new Error(`Unexpected write: ${method} ${path}`);
    return json({ metadata: null, items: [], columns: [], records: [] });
  });
});

// Набрано до обработки: разъёмы «5», «I3», «C», «A6» и SCB01X3/SCB02X3, кабели по чертежу, контакты, четыре сростка, хвосты.
const topology = harness(DSA);
const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000713", version: 1, project: "713К.4", item_index: "КБ1", product_name: "Кабельная сборка Жгут НВ4",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0,
  document: { build_config: { tab: "complex", gen_state: genSnapshot(DSA.components, topology, {}, {}) } } as ProjectTechcard["document"],
  manual_operation_keys: [], created_at: "2026-10-17T10:00:00+03:00", updated_at: "2026-10-17T10:00:00+03:00", issued_at: null,
};
const kit: KitsetBom = {
  project: "713К.4", index: "КБ1", name: card.product_name, run: "500", part_qty: "500", calc_source: "", calc_ops: [],
  components: DSA.components.map((c) => ({ id: c.id, type: c.type, name: c.name, art: c.art, kd: "", qty: c.qty })),
};

const type = (label: string | HTMLElement, value: string) =>
  fireEvent.change(typeof label === "string" ? screen.getByLabelText(label) : label, { target: { value } });
// Большая форма: поиск по роли в jsdom медленный — переключатели, кнопки и группы ищутся по подписи и тексту.
const switchOn = (title: string) => {
  const sw = [...document.querySelectorAll<HTMLElement>(".gsw")].find((el) => el.querySelector(".gsw-text > span")?.textContent === title)!
    .querySelector("input") as HTMLInputElement;
  if (!sw.checked) fireEvent.click(sw);
};
const radio = (group: string, name: string) => fireEvent.click([...document.querySelectorAll(`[role="radiogroup"][aria-label="${group}"] button`)]
  .find((el) => el.textContent === name)!);
const button = (name: string) => fireEvent.click([...document.querySelectorAll("button")].find((el) => el.getAttribute("aria-label") === name || el.textContent === name)!);
const block = (label: string) => document.querySelector(`div[aria-label="${label}"]`) as HTMLElement;


// Владелец 18.10: «Ветвь по КД, мм» — размер ветви на схеме жгута по сборочному чертежу; в карточке каждого разъёма «Сложной сборки»,
// обе стороны; пустое поле — размер по длинам проводов.
it("every connector card of the complex tab has «Ветвь по КД», on both sides, empty by default", async () => {
  render(<TcGenerator card={card} kit={kit} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} />);
  await screen.findByText("Обработка", { selector: "h3" });
  const fields = [...document.querySelectorAll<HTMLInputElement>('input[aria-label^="Ветвь по КД "]')];
  expect(fields.map((el) => el.getAttribute("aria-label"))).toEqual(["А1", "А2", "А3", "А4", "В1", "В2"].map((end) => `Ветвь по КД ${end}, мм`));
  expect(fields.every((el) => el.value === "")).toBe(true);
  const first = screen.getByLabelText("Ветвь по КД А1, мм") as HTMLInputElement;
  fireEvent.change(first, { target: { value: "120" } });
  expect((screen.getByLabelText("Ветвь по КД А1, мм") as HTMLInputElement).value).toBe("120");
  fireEvent.change(screen.getByLabelText("Ветвь по КД В2, мм"), { target: { value: "170" } });
  expect((screen.getByLabelText("Ветвь по КД В2, мм") as HTMLInputElement).value).toBe("170");
});
