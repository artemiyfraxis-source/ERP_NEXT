import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import { resetPinoutHousings } from "./PinoutDiagram";
import { buildMark, draftKey } from "./useGeneratorDraft";
import { emptyManual } from "./manualLogic";
import { COMPONENTS_663, harness663 } from "./tcGenComplex663.fixture";
import type { KitsetBom, ProjectTechcard } from "./api";

/* «Распиновка и сростки» в «Сложной сборке»: 663К.1 КБ2 из черновика, правка концов в таблице (владелец 25.10). */
const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000663", version: 1, project: "663К.1", item_index: "КБ2", product_name: "Жгут периферийного питания",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-10-25T10:00:00+03:00", updated_at: "2026-10-25T10:00:00+03:00", issued_at: null,
};
const kit: KitsetBom = {
  project: "663К.1", index: "КБ2", name: "Жгут периферийного питания", run: "300", part_qty: "300", calc_source: "", calc_ops: [],
  components: COMPONENTS_663.map((c) => ({ id: c.id, kd: "", type: c.type, name: c.name, art: c.art, qty: c.qty })),
};
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
const USER = "u-663";

beforeEach(() => {
  resetPinoutHousings();
  window.localStorage.setItem(draftKey(USER, card.project, card.item_index), JSON.stringify({
    v: 1, rev: 1, savedAt: "2026-10-25T10:00:00.000Z", built: buildMark(null), server: 0, synced: true,
    value: { S: harness663(), manual: emptyManual(), terEdits: {}, coaxAdd: {} },
  }));
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [] });
    if (path.startsWith("/api/references/tech-operations?")) return json({ metadata: null, columns: [], records: [] });
    if (path.startsWith("/api/techcards/connector-families")) return json({ items: [], match: null });
    if (path.includes("draft")) return json({ draft: null });
    return json({ items: [], metadata: null, records: [], columns: [], by_section: [], applicator: "", crimp_height: "", pull_force: "" });
  });
});
afterEach(() => { cleanup(); window.localStorage.clear(); vi.restoreAllMocks(); });

async function mount() {
  render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kit} userId={USER} />);
  return within(await screen.findByRole("region", { name: "Распиновка и сростки" }));
}
const end = (table: ReturnType<typeof within>, side: string, num: string) => table.getByLabelText(`Конец ${side} провода № ${num}`) as HTMLSelectElement;

it("shows every wire with both ends, splice membership and connector designators", async () => {
  const table = await mount();
  expect(table.getByText("12 жил · сростков 2 · свободных концов 2")).toBeInTheDocument();
  expect(end(table, "А", "3").selectedOptions[0].textContent).toBe("XS1 · контакт 1");
  expect(end(table, "В", "3").selectedOptions[0].textContent).toBe("Сросток С1");
  expect(end(table, "В", "9.1").selectedOptions[0].textContent).toBe("Свободный конец");
  expect((table.getByLabelText("Обозначение разъёма В1") as HTMLInputElement).value).toBe("XP1");
  expect(table.getByLabelText("Гильза С1")).toHaveValue("kit-13");
});

it("moves a wire end out of a splice and back into it with one choice each", async () => {
  const table = await mount();
  fireEvent.change(end(table, "В", "5"), { target: { value: "" } });
  expect(table.getByText("Конец В не задан: выберите контакт, сросток или свободный конец")).toBeInTheDocument();
  expect(table.getByText("4 конца")).toBeInTheDocument();
  fireEvent.change(end(table, "В", "5"), { target: { value: "sp:s1" } });
  expect(table.queryByText(/не задан: выберите/)).toBeNull();
  expect(table.getAllByText("5 концов")).toHaveLength(2);
});

it("takes the first free contact, and a new splice is picked once and joined by naming it", async () => {
  const table = await mount();
  fireEvent.change(end(table, "А", "9.2"), { target: { value: "tail" } });
  fireEvent.change(end(table, "А", "9.2"), { target: { value: "pin:e3:auto" } });
  expect(end(table, "А", "9.2").selectedOptions[0].textContent).toBe("XS3 · контакт 4");
  fireEvent.change(end(table, "В", "9.1"), { target: { value: "new" } });
  fireEvent.change(end(table, "В", "9.2"), { target: { value: "sp:s3" } });
  expect(table.getByText("2 конца")).toBeInTheDocument();
});

it("edits the designator and the length in place; the splice list takes a wire out and dissolves a splice", async () => {
  const table = await mount();
  fireEvent.change(table.getByLabelText("Обозначение разъёма А1"), { target: { value: "XS1.1" } });
  expect(end(table, "А", "3").selectedOptions[0].textContent).toBe("XS1.1 · контакт 1");
  fireEvent.change(table.getByLabelText("Длина провода № 3"), { target: { value: "460" } });
  expect(table.getByLabelText("Длина провода № 3")).toHaveValue(460);
  fireEvent.click(table.getByLabelText(/Убрать № 3 конец В из С1/));
  expect(table.getByText("4 конца")).toBeInTheDocument();
  fireEvent.click(table.getByLabelText("Разъединить С2"));
  await waitFor(() => expect(table.queryByLabelText("Гильза С2")).toBeNull());
});
