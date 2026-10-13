import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import { resetPinoutHousings } from "./PinoutDiagram";
import type { KitsetBom, ProjectTechcard } from "./api";

// Многожильный кабель в форме (владелец 12.10, Helukabel 15201 на 19 жил): число жил, цвета и экран — из БД.ПРОВ,
// на карточке разъёма — терминалов по жилам и строка кабеля (жилы здесь, экран).
const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000019", version: 1, project: "621К.2", item_index: "КБ3", product_name: "Кабель Helukabel 300мм",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-10-12T10:00:00+03:00", updated_at: "2026-10-12T10:00:00+03:00", issued_at: null,
};
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
const COLORS = "белый, коричневый, зелёный, жёлтый, серый, розовый, синий, красный, чёрный, фиолетовый, серо-розовый, красно-синий, бело-зелёный, коричнево-зелёный, бело-жёлтый, жёлто-коричневый, бело-серый, серо-коричневый, бело-розовый";

beforeEach(() => {
  resetPinoutHousings();
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [] });
    if (path.startsWith("/api/references/tech-operations?tab=prov")) return json({ metadata: null, columns: [], records: [
      { id: "p1", tab: "prov", display_text: "Helukabel 15201", source_sheet: "БД.ПРОВ",
        fields: { "Марка": "Helukabel 15201", "Число жил": "19", "Цвета": COLORS, "Экран": "оплётка луж. медь" } },
    ] });
    if (path.startsWith("/api/references/tech-operations?")) return json({ metadata: null, columns: [], records: [] });
    if (path.startsWith("/api/techcards/connector-families")) return json({ items: [], match: null });
    if (path === "/api/references/technology/resolve-terminal") return json({ applicator: "", crimp_height: "", pull_force: "", by_section: [] });
    if (path.startsWith("/api/harness/")) return json({ design: null });
    throw new Error(`Unexpected request: ${path}`);
  });
});
afterEach(() => vi.restoreAllMocks());

const kit: KitsetBom = {
  project: "621К.2", index: "КБ3", name: "Кабель Helukabel 300мм", run: "1", part_qty: "1", calc_source: "", calc_ops: [],
  components: [
    { type: "Кабель", name: "Helukabel 15201", art: "Helukabel 15201", qty: 0.3 },
    { type: "Соед", name: "DF1B-18DS-2.5RC", art: "DF1B-18DS-2.5RC", qty: 1 },
    { type: "Соед", name: "DF1B-5EP-2.5RC", art: "DF1B-5EP-2.5RC", qty: 1 },
    { type: "Терминал", name: "DF1B-2428SCFA", art: "DF1B-2428SCFA", qty: 19 },
    { type: "Терминал", name: "DF1-PD2428SCFB", art: "DF1-PD2428SCFB", qty: 19 },
  ].map((item, i) => ({ id: `kit-${i}`, kd: "", ...item })),
};

it("a 19-core cable from БД.ПРОВ: 19 terminals per connector, the cable line on the card, coloured cores in the palette", async () => {
  render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kit} />);
  expect(await screen.findByLabelText("Жил в кабеле 1")).toHaveValue(19);
  const a1 = screen.getByLabelText("Кабель Helukabel 15201 на А1");
  expect(a1).toHaveTextContent("Кабель Helukabel 15201: жил здесь 19 из 19");
  expect(a1).toHaveTextContent("экран — скрутить и изолировать");
  expect(screen.getAllByText(/^19 шт/)).toHaveLength(2);
  const palette = screen.getByRole("group", { name: "Провода" });
  await waitFor(() => expect(within(palette).getAllByRole("button", { name: /Цвет жилы/ })).toHaveLength(19));
  expect(within(palette).getByRole("button", { name: "Цвет жилы 1.2" })).toHaveAttribute("title", "коричневый");
  expect(screen.getAllByText(/Скрутка и изоляция экрана/).length).toBeGreaterThan(0);
});
