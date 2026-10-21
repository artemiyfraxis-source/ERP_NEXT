import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import { resetPinoutHousings } from "./PinoutDiagram";
import type { KitsetBom, ProjectTechcard } from "./api";

// Владелец 15.10 (545К.9 КБ1, «Межплатная»): «визуал полуфабриката неправильный; так же нет резки ТУТ, установки и
// термоусаживания»; «почему это там — это настройка разъёма, а не провода» — термоусадка на срез оболочки живёт в
// строке «Кабели», а полуфабрикат рисует один кабель с жилами веером.
const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000545", version: 1, project: "545К.9", item_index: "КБ1", product_name: "Кабель TC-BLS-5-BLS-5-400-RLB",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-10-15T10:00:00+03:00", updated_at: "2026-10-15T10:00:00+03:00", issued_at: null,
};
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  resetPinoutHousings();
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [] });
    if (path.startsWith("/api/references/tech-operations?")) {
      const tab = new URLSearchParams(path.split("?")[1]).get("tab");
      return json({ metadata: null, columns: [], records: tab === "ter"
        ? [{ id: "t1", tab: "ter", display_text: "T-DS1071-SC600", source_sheet: "", fields: { terArticle: "T-DS1071-SC600", terLPlus: "5", terStep: "2.54", terStrip: "3.2", terApplicator: "M21" } }] : [] });
    }
    if (path.startsWith("/api/techcards/connector-families")) return json({ items: [], match: null });
    if (path === "/api/references/technology/resolve-terminal") return json({ applicator: "", crimp_height: "", pull_force: "", by_section: [] });
    if (path.startsWith("/api/harness/")) return json({ design: null });
    throw new Error(`Unexpected request: ${path}`);
  });
});
afterEach(() => vi.restoreAllMocks());

const kit: KitsetBom = {
  project: "545К.9", index: "КБ1", name: "Кабель TC-BLS-5-BLS-5-400-RLB", run: "50", part_qty: "50", calc_source: "", calc_ops: [],
  components: [
    { type: "Соед", name: "I-DS1071-SCB01X5", art: "I-DS1071-SCB01X5", qty: 2 },
    { type: "Терминал", name: "T-DS1071-SC600", art: "T-DS1071-SC600", qty: 8 },
    { type: "Провод", name: "UL2527 28AWG", art: "UL2527 28AWG", kd: "UL2575 26AWG", qty: 0.42 },
    { type: "ТУТ", name: "ТУТ 6/3мм черная", art: "ТУТ 6/3мм черная", qty: 0.06 },
  ].map((item, i) => ({ id: `kit-${i}`, kd: "", ...item })),
};

it("the jacket-cut heat-shrink sits in the cable row (not on the connector), on by default; the ops cut, put on and shrink it", async () => {
  render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kit} />);
  expect(await screen.findByLabelText("Жил в кабеле 1")).toHaveValue(4);
  const row = screen.getByRole("group", { name: "Термоусадка на срез оболочки, кабель 1" });
  const sides = within(row).getAllByRole("checkbox");
  expect(sides.map((box) => (box.closest("label")?.textContent || "").trim())).toEqual(["А", "В"]);
  expect(sides.every((box) => (box as HTMLInputElement).checked)).toBe(true);
  expect(within(row).getByLabelText("Трубка на срез оболочки, кабель 1")).toHaveDisplayValue("ТУТ 6/3мм черная");
  expect(within(row).getByLabelText("Длина трубки на срез оболочки, кабель 1")).toHaveAttribute("placeholder", "25");
  // Не в карточке разъёма и не в «Обработке».
  expect(screen.getByLabelText("Сторона А")).not.toHaveTextContent(/разделки|срез оболочки/);
  expect(screen.queryByText("Термоусадка на место разделки")).toBeNull();
  const body = document.body.textContent || "";
  expect(body).toContain("Резка ТУТ на место разделки А, В — 2×25 мм");
  expect(body).toContain("Надеть трубки (стороны А и В): ТУТ 6/3мм черная на место разделки А, В — 2 шт");
  expect(body).toContain("Усадка ТУТ на месте разделки А — 1 шт");
  // Сторона В выключена — трубка только на А; своя длина — в резке.
  fireEvent.click(sides[1]);
  fireEvent.change(within(row).getByLabelText("Длина трубки на срез оболочки, кабель 1"), { target: { value: "30" } });
  await waitFor(() => expect(document.body.textContent).toContain("Резка ТУТ на место разделки А — 1×30 мм"));
  expect(document.body.textContent).not.toContain("Усадка ТУТ на месте разделки В");
});

