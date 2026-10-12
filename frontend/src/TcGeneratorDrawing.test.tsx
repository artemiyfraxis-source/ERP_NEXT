import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import { resetPinoutHousings } from "./PinoutDiagram";
import type { KitsetBom, ProjectTechcard } from "./api";

// «Построить чертёж» в генераторе (владелец 12.10): форма → чертёж жгута позиции; правленый чертёж не перезаписывается молча.

const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000019", version: 1, project: "621К.2", item_index: "КБ1", product_name: "Жгут интерфейсный 300мм",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-10-12T10:00:00+03:00", updated_at: "2026-10-12T10:00:00+03:00", issued_at: null,
};
const HARNESS = "/api/techcards/kitsets/621%D0%9A.2/harness/%D0%9A%D0%911";
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
const kit: KitsetBom = {
  project: "621К.2", index: "КБ1", name: "Жгут интерфейсный 300мм", run: "1", part_qty: "1", calc_source: "", calc_ops: [],
  components: [
    { type: "Провод", name: "НВ4 0,2 красный", art: "НВ4 0,2 красный", qty: 0.35 },
    { type: "Провод", name: "НВ4 0,2 черный", art: "НВ4 0,2 черный", qty: 0.35 },
    { type: "Соед", name: "Колодка PH 2", art: "PHR-2", qty: 1 },
    { type: "Соед", name: "Колодка XH 2", art: "XHP-2", qty: 1 },
  ].map((item, i) => ({ id: `kit-${i}`, kd: "", ...item })),
};

let stored: { design: Record<string, unknown> | null; revision: number | null };
let calls: Array<{ method: string; path: string; body: Record<string, unknown> | null }>;

beforeEach(() => {
  resetPinoutHousings();
  window.localStorage.clear();
  stored = { design: null, revision: null };
  calls = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : null;
    calls.push({ method, path, body });
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [] });
    if (path.startsWith("/api/references/")) return json({ metadata: null, columns: [], records: [] });
    if (path.startsWith("/api/techcards/connector-families")) return json({ items: [], match: null });
    if (path === "/api/techcards/harness-components") return json([]);
    if (path === HARNESS && method === "GET") return json({ document: null, design: stored.design, revision: stored.revision, warnings: [] });
    if (path === HARNESS && method === "PUT") {
      stored = { design: body!.design as Record<string, unknown>, revision: (stored.revision ?? 0) + 1 };
      return json({ document: null, design: stored.design, revision: stored.revision, warnings: [], created: true });
    }
    if (path === `${HARNESS}/checkpoint`) return json({ revision: stored.revision, row: null });
    throw new Error(`Unexpected request: ${method} ${path}`);
  });
});
afterEach(() => vi.restoreAllMocks());

async function mountPinned(onOpenDrawing = vi.fn()) {
  render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kit} onOpenDrawing={onOpenDrawing} />);
  await screen.findByLabelText("Контакты А1: НВ4 0,2 красный");
  for (const [wire, pin] of [["НВ4 0,2 красный", "1"], ["НВ4 0,2 черный", "2"]]) {
    fireEvent.change(screen.getByLabelText(`Контакты А1: ${wire}`), { target: { value: pin } });
    fireEvent.change(screen.getByLabelText(`Контакты В1: ${wire}`), { target: { value: pin } });
  }
  return onOpenDrawing;
}

it("builds the drawing of the position from the form and opens it", async () => {
  const open = await mountPinned();
  fireEvent.click(await screen.findByRole("button", { name: "Построить чертёж" }));
  expect(await screen.findByText("Чертёж КБ1 построен: 2 разъёма, 2 провода.")).toBeTruthy();
  const save = calls.find((call) => call.method === "PUT")!;
  expect(save.body).toMatchObject({ revision: null, checkpoint: true });
  const design = save.body!.design as { connectors: Array<{ designation: string; partNumber: string }>; wires: unknown[] };
  expect(design.connectors.map((c) => [c.designation, c.partNumber])).toEqual([["XS1", "PHR-2"], ["XS2", "XHP-2"]]);
  expect(design.wires).toHaveLength(2);
  expect(screen.getByText(/Замечания: \d+/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Открыть чертёж КБ1" }));
  expect(open).toHaveBeenCalledTimes(1);
  // Свой же чертёж обновляется без вопроса — его никто не правил.
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Обновить чертёж" }));
  expect(await screen.findByText("Чертёж КБ1 обновлён: 2 разъёма, 2 провода.")).toBeTruthy();
  expect(confirm).not.toHaveBeenCalled();
});

it("asks before overwriting a drawing edited by hand and keeps the old version in history", async () => {
  stored = { design: { schemaVersion: 1, connectors: [], wires: [] }, revision: 7 };
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  await mountPinned();
  fireEvent.click(await screen.findByRole("button", { name: "Обновить чертёж" }));
  await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
  expect(confirm.mock.calls[0][0]).toMatch(/^Перезаписать ручные правки\?/);
  expect(calls.some((call) => call.method !== "GET")).toBe(false);

  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Обновить чертёж" }));
  expect(await screen.findByText("Чертёж КБ1 обновлён: 2 разъёма, 2 провода.")).toBeTruthy();
  const writes = calls.filter((call) => call.method !== "GET").map((call) => [call.method, call.path.replace(HARNESS, ""), call.body?.revision]);
  expect(writes).toEqual([["POST", "/checkpoint", 7], ["PUT", "", 7]]);
});
