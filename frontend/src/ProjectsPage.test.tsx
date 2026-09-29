import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectsPage, deleteSummary, kitBatch } from "./ProjectsPage";

const editor = {
  user: { id: "u1", email: "engineer@example.test", name: "Технолог", department: "Производство" },
  roles: ["engineer"], permissions: { projects: "edit" as const },
};
const viewer = { ...editor, permissions: { projects: "view" as const } };

const statuses = ["Новый", "ТК готова", "КБ набрана", "В работе", "КБ на складе", "Отгружено", "Отказ", "Слился", "Отменён"];
const tiles = [
  {
    project: "708К.4", customer: "ООО Заказчик", calc_source: "708К.4 Расчет.xlsx", updated_at: "2026-09-29T10:00:00",
    updated_by: "Технолог", assemblies: 2, lines: 3, kbs: ["КБ1", "КБ2"], status: "Новый", blocker: "",
    kb_stages: [
      { index: "КБ1", status: "ТК готова", manual: false, tc: true, kit: false, review: { value: "проверена", text: "", who: "" } },
      { index: "КБ2", status: "Новый", manual: false, tc: false, kit: false, review: null },
    ],
  },
  {
    project: "591К.3", customer: "АО Другой", calc_source: "", updated_at: "2026-09-20T10:00:00", updated_by: "",
    assemblies: 1, lines: 1, kbs: ["КБ1"], status: "В работе", blocker: "ХОЛД",
    kb_stages: [{ index: "КБ1", status: "В работе", manual: true, tc: false, kit: false, review: null }],
  },
];
const card = {
  project: "708К.4", customer: "ООО Заказчик", calc_source: "708К.4 Расчет.xlsx", updated_at: "2026-09-29T10:00:00",
  updated_by: "Технолог", version: 2, status: "Новый", blocker: "", stages: statuses.slice(0, 6),
  review_values: ["не проверена", "проверена", "с погрешностями"],
  assemblies: [{
    index: "КБ1", name: "Жгут питания", run: "10", item: "708К.4КБ1",
    stage: { index: "КБ1", status: "Новый", manual: false, tc: false, kit: false, review: null },
    techcard: null, kd: [],
    components: [
      { art: "UL1007 22AWG", type: "Провод", name: "", link: "", per: "1.5", scrap: "5", batch: "15.75", lot: null },
      { art: "43025-0400", type: "Разъём", name: "Корпус Molex", link: "Склад", per: "2", scrap: "5", batch: "21", lot: null },
    ],
  }],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

describe("ProjectsPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    window.history.replaceState({}, "", "/?section=projects");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = decodeURIComponent(String(input));
      const method = init?.method || "GET";
      if (url.startsWith("/api/techcards/kitsets?sort=")) return json({ items: tiles, statuses });
      if (url === "/api/techcards/kitsets/708К.4" && method === "GET") return json(card);
      if (url === "/api/techcards/kitsets/708К.4/lines") return json({ res: { updated: 1 } });
      if (url === "/api/techcards/kitsets/708К.4/issue") return json({ created: [{ no: "В-26-0001", index: "КБ1", shortfall: ["x"] }], skipped: [], message: "Заявок создано: 1, из них ждут поставки: 1." });
      if (url.startsWith("/api/techcards/kitsets/lots")) return json({ lots: [{ lot_id: "l1", warehouse: "ЦАР", grn: "FRX.26.0000001", package: "", article: "43025-0400", name: "Корпус Molex", unit: "шт", storage_location: "1F4", available: "15" }] });
      return json({});
    }));
  });

  it("lists project tiles with facts, filters by status and keeps the choice", async () => {
    render(<ProjectsPage principal={editor} />);
    expect(await screen.findByText("708К.4")).toBeTruthy();
    expect(screen.getByText("ТК 1/2")).toBeTruthy();
    expect(screen.getByText("⛔ ХОЛД")).toBeTruthy();
    expect(screen.getByText("✓ ТК проверено 1")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Статус"), { target: { value: "В работе" } });
    expect(screen.queryByText("708К.4")).toBeNull();
    expect(screen.getByText("показано 1 из 2")).toBeTruthy();
    expect(localStorage.getItem("kitFilt")).toBe("В работе");
  });

  it("hides writing controls for view-only access", async () => {
    render(<ProjectsPage principal={viewer} />);
    await screen.findByText("708К.4");
    expect(screen.queryByRole("button", { name: "➕ Создать проект из расчёта" })).toBeNull();
    expect(screen.queryByLabelText("Удалить 708К.4")).toBeNull();
  });

  it("autosaves one spec row with the recalculated batch and picks a warehouse lot", async () => {
    render(<ProjectsPage principal={editor} />);
    fireEvent.click(await screen.findByText("708К.4"));
    fireEvent.click(await screen.findByRole("tab", { name: "Спецификация" }));
    const per = screen.getByLabelText("На 1 шт UL1007 22AWG");
    fireEvent.change(per, { target: { value: "2" } });
    expect(screen.getAllByText("21")).toHaveLength(2);
    fireEvent.blur(per);
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/techcards/kitsets/708%D0%9A.4/lines", expect.objectContaining({ method: "PUT" })));
    const saved = vi.mocked(fetch).mock.calls.find(([url]) => decodeURIComponent(String(url)).endsWith("/lines"));
    expect(JSON.parse(String(saved?.[1]?.body)).edits).toEqual([{ index: "КБ1", art: "UL1007 22AWG", per: "2", scrap: "5", name: "", link: "", lot: null }]);

    fireEvent.click(screen.getAllByRole("button", { name: "📦 выбрать со склада" })[1]);
    fireEvent.click(await screen.findByRole("button", { name: "Выбрать" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "FRX.26.0000001" })).toBeTruthy());
    const lotCall = vi.mocked(fetch).mock.calls.filter(([url]) => decodeURIComponent(String(url)).endsWith("/lines")).at(-1);
    expect(JSON.parse(String(lotCall?.[1]?.body)).edits[0].lot).toEqual({ lot_id: "l1", warehouse: "ЦАР", grn: "FRX.26.0000001", package: "" });
  });

  it("sends the kit to picking only with a deadline", async () => {
    render(<ProjectsPage principal={editor} />);
    fireEvent.click(await screen.findByText("708К.4"));
    fireEvent.click(await screen.findByRole("tab", { name: "Спецификация" }));
    fireEvent.click(screen.getByRole("button", { name: "🧺 Отправить на комплектацию" }));
    fireEvent.click(screen.getByRole("button", { name: "Отправить" }));
    expect(screen.getByText("Укажи дедлайн.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Дедлайн"), { target: { value: "2026-10-10" } });
    fireEvent.click(screen.getByRole("button", { name: "Отправить" }));
    expect(await screen.findByText("Заявок создано: 1, из них ждут поставки: 1. В-26-0001 (недобор)")).toBeTruthy();
  });

  it("mirrors the server batch formula and delete summary", () => {
    expect(kitBatch("1,5", "10", "5")).toBe(15.75);
    expect(kitBatch("", "10", "5")).toBe(0);
    expect(deleteSummary({ project: "708К.4", deleted: 3, erp: { "Проекты": 1 }, cards: { cards: 2, revisions: 1 }, kd: 1, gp: { deleted: 2, kept: 1 } }))
      .toBe("Снесено строк: спецификация 3, проекты 1, техкарты 2, КД 1, склад ГП 2. На складе ГП осталось с движением: 1");
  });
});
