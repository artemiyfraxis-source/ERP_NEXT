import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import type { ProjectTechcard } from "./api";

const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000001", version: 1, project: "P-100", item_index: "КБ1", product_name: "Жгут XHP 150мм",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-09-23T10:00:00+03:00", updated_at: "2026-09-23T10:00:00+03:00", issued_at: null,
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const records = (tab: string) => ({
  metadata: null, columns: [],
  records: tab === "ter" ? [{ id: "t1", tab: "ter", display_text: "XH", source_sheet: "", fields: { terArticle: "SXH-001T-P0.6", terLPlus: "5", terStep: "2.50", terApplicator: "S12" } }] : [],
});

function mockApi(extra?: (path: string, init?: RequestInit) => Response | undefined) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const custom = extra?.(path, init);
    if (custom) return custom;
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [
      { id: "cut", code: "CUT_WIRE_auto", side: "", title: "CUT_WIRE_auto | Резка", name: "Резка", category: "Резка", steps: 1, tooling: 1 },
      { id: "prsA", code: "PRS", side: "A", title: "PRS_TERMINAL | Опрессовка терминалов (А)", name: "", category: "Опрессовка терминалов", steps: 1, tooling: 1 },
    ] });
    if (path.startsWith("/api/references/tech-operations?")) return json(records(new URLSearchParams(path.split("?")[1]).get("tab") || ""));
    if (path === "/api/references/technology/resolve-terminal") return json({ applicator: "S12", crimp_height: "0,8", pull_force: "", by_section: [{ section: "0,12 мм²", crimp_height: "0,8", pull_force: "", applicator: "S12", range_only: false, found: true }] });
    throw new Error(`Unexpected request: ${path}`);
  });
}

afterEach(() => vi.restoreAllMocks());

it("seeds the form from the kit and sends the SUPPLYMAP config to the build", async () => {
  mockApi();
  const onBuild = vi.fn();
  render(<TcGenerator card={card} busy={false} onBuild={onBuild} onDiscard={vi.fn()} bom={[
    { type: "Провод", name: "МГТФ 0,12", art: "МГТФ 0,12", qty: 0.3 },
    { type: "Терминал", name: "Контакт XH", art: "SXH-001T-P0.6", qty: 4 },
    { type: "Соед", name: "Колодка XH 2", art: "XHP-2", qty: 2 },
  ]} />);

  expect(await screen.findByRole("tab", { name: "Межплатная", selected: true })).toBeInTheDocument();
  expect(await screen.findByText("Резка — МГТФ 0,12")).toBeInTheDocument();
  expect(screen.getByText("Опрессовка терминалов А1")).toBeInTheDocument();
  expect(await screen.findAllByText(/Подбор по сечению: 0,12 мм² → S12/)).toHaveLength(2);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Создать техкарты" }));

  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  const config = onBuild.mock.calls[0][0];
  expect(config.wires).toEqual([{ name: "МГТФ 0,12", art: "МГТФ 0,12", qty: 2, length: 160 }]);
  expect(config.ops.map((op: { type: string }) => op.type)).toEqual(["cutWire", "prsTerm", "prsTerm"]);
  expect(config.tcgen.state.tab).toBe("inter");
});

it("adds a missing coax connector to БД.КОАКС through the references record", async () => {
  let created: unknown;
  mockApi((path, init) => {
    if (path === "/api/references/tech-operations" && init?.method === "POST") {
      created = JSON.parse(String(init.body));
      return json({ id: "c1", tab: "coax", display_text: "SMA-J", source_sheet: "", fields: {} }, 201);
    }
    return undefined;
  });
  render(<TcGenerator card={{ ...card, product_name: "Сборка RG316 150mm" }} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} bom={[
    { type: "Кабель", name: "RG316", art: "RG316", qty: 0.2 },
    { type: "Соед", name: "SMA", art: "SMA-J", qty: 1 },
  ]} />);

  expect(await screen.findByRole("tab", { name: "КОАКС", selected: true })).toBeInTheDocument();
  expect(screen.getByText("⚠ «SMA-J» нет в БД.КОАКС — добавьте ниже")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("d1 A"), { target: { value: "0,51" } });
  fireEvent.click(screen.getByRole("button", { name: "➕ Добавить в БД.КОАКС" }));

  await waitFor(() => expect(created).toEqual({ tab: "coax", fields: {
    coaxArticle: "SMA-J", coaxWire: "RG316", d1: "0,51", pinType: "пайка", shieldType: "обжим", connArticle: "SMA-J",
  } }));
});
