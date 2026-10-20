import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProcurementRequests } from "./ProcurementRequests";
import type { RqLine } from "./procurementRequestsApi";
import {
  GROUP_KEY, groupLines, orderedCounts, planSum, projectTitle, quantityInput, rangeIds, readGroupBy, setMany, shownOrder,
  writeGroupBy,
} from "./procurementRequestsLogic";
import { projectKeyFor } from "./requestFormV2Logic";
import { resetStageOptions } from "./StageOptions";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const line = (id: string, over: Partial<RqLine> = {}): RqLine => ({
  id, legacy_id: `SM-${id}`, version: 2, status: "В работе", rb_status: "new", name: `Позиция ${id}`, article: null,
  item_type: "Разъём", category: "Общепром", quantity: "10", to_buy: "10", stock_covered: "0", unit: "шт",
  planned_date: "2026-10-30", buyer: "Петров", mine: true, source_request_id: `r-${id}`, plan: null, last: null,
  free_stock: null, rfq: null, requote_from: null, cancel_reason: null, selectable: true, can_take: false,
  can_split: true, can_cover: true, can_cancel: true, can_resend: false, ...over,
});

describe("logic of «Заявки»", () => {
  it("groups by type, plan and last purchase, the unknown group last", () => {
    const lines = [
      line("a", { item_type: "Провод", plan: { price_with_vat: "10", link: null, label: "Элитан" } }),
      line("b", { item_type: "", category: "Общепром", last: { price: "5", date: "2026-09-01", supplier: "ООО Бета", unit: "шт", source: "upd" } }),
      line("c", { item_type: "Кабель" }),
    ];
    expect(groupLines(lines, "type").map(([name]) => name)).toEqual(["Кабель", "Общепром", "Провод"]);
    expect(groupLines(lines, "plan").map(([name, rows]) => [name, rows.length])).toEqual([["Элитан", 1], ["", 2]]);
    expect(groupLines(lines, "last").map(([name]) => name)).toEqual(["ООО Бета", ""]);
    expect(groupLines(lines, "none")).toHaveLength(1);
  });

  it("selects a Shift range of selectable lines only, in the shown order", () => {
    const groups = groupLines([line("a"), line("b", { selectable: false }), line("c"), line("d")], "none");
    const order = shownOrder(groups);
    expect(rangeIds(order, "a", "c")).toEqual(["a", "c"]);
    expect(rangeIds(order, "d", "a")).toEqual(["a", "c", "d"]);
    expect(rangeIds(order, null, "c")).toBeNull();
    expect([...setMany(new Set(["x"]), ["a", "c"], true)]).toEqual(["x", "a", "c"]);
    expect([...setMany(new Set(["x", "a"]), ["a"], false)]).toEqual(["x"]);
  });

  it("orders status chips like Roller_Buyer and sums the plan of a group", () => {
    expect(orderedCounts({ cancelled: 1, new: 2, quoted: 3 })).toEqual([["new", 2], ["quoted", 3], ["cancelled", 1]]);
    expect(planSum([
      line("a", { plan: { price_with_vat: "2.5", link: null, label: "" } }),
      line("b", { plan: { price_with_vat: "1", link: null, label: "" }, rb_status: "cancelled" }),
      line("c"),
    ])).toBe(25);
  });

  it("names projects, purposes and lines without a project", () => {
    expect(projectTitle({ kind: "project", value: "614К.1" })).toBe("614К.1");
    expect(projectTitle({ kind: "purpose", value: "FRX" })).toBe("Собственные нужды компании");
    expect(projectTitle({ kind: "none", value: "" })).toBe("Без проекта");
  });

  it("remembers the grouping and reads quantities with a comma", () => {
    writeGroupBy("last");
    expect(readGroupBy()).toBe("last");
    localStorage.setItem(GROUP_KEY, "nonsense");
    expect(readGroupBy()).toBe("type");
    expect(quantityInput(" 1 200,5 ")).toBe("1200.5");
    expect(quantityInput("0")).toBe("");
    expect(quantityInput("abc")).toBe("");
  });

  it("prefills the request form's project from ?project=", () => {
    const internal = [{ number: "ВП-001", name: "Склад", purpose: "Обеспечение производства" }];
    expect(projectKeyFor("614k.1", ["614К.1", "621К.2"], [], [], false)).toBe("c:614К.1");
    expect(projectKeyFor("Обеспечение производства", [], ["Обеспечение производства"], [], false)).toBe("i:Обеспечение производства");
    expect(projectKeyFor("Обеспечение производства", [], [], internal, true)).toBe("p:ВП-001");
    expect(projectKeyFor("вп-001", ["ВП-001"], [], internal, true)).toBe("p:ВП-001");
    expect(projectKeyFor("999К.1", ["614К.1"], [], [], false)).toBe("");
    expect(projectKeyFor("", ["614К.1"], [], [], false)).toBe("");
  });
});

const BATCH = "614К.1|обеспечение производства.001";
const summary = {
  scope: "mine",
  projects: [{ key: "614К.1", kind: "project", value: "614К.1", lines: 3, counts: { new: 2, in_rfq: 1 } }],
  batches: [{
    key: BATCH, project: { key: "614К.1", kind: "project", value: "614К.1" }, designation: "Обеспечение производства.001",
    request_date: "2026-10-05", planned_date: "2026-10-15", initiators: ["Орлова Анна"], lines: 3,
    counts: { new: 2, in_rfq: 1 }, mine: 3, selectable: 2, to_take: 0,
  }],
  focus: null,
};
const lines = [
  line("a", { item_type: "Маркировка" }),
  line("b", { item_type: "Маркировка" }),
  line("c", { item_type: "Провод", rb_status: "in_rfq", selectable: false, rfq: { id: "rfq-1", number: "КП-2610-001" } }),
];

describe("the «Заявки» screen", () => {
  let calls: Array<{ path: string; init?: RequestInit }>;

  beforeEach(() => {
    calls = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      calls.push({ path, init });
      if (path === "/api/stage-options") {
        return json({ available: true, options: ["procurement.requests_rb", "procurement.rfq"].map((key) => ({ key, enabled: true, block: "", title: "", before: "", after: "" })) });
      }
      if (path.startsWith("/api/procurement/requests-view")) {
        return json(path.includes("batch=") ? { ...summary, lines } : summary);
      }
      if (path === "/api/procurement/cancel-many") return json({ cancelled: ["a"], skipped: [{ id: "b", reason: "строка изменилась — обновите список" }] });
      if (path.startsWith("/api/purchasing/rfqs/suggest")) return json({ categories: [], lines: 2, suggestions: [] });
      if (path.startsWith("/api/purchasing/suppliers") || path.includes("directory")) return json({ suppliers: [] });
      return json({});
    });
  });

  afterEach(() => {
    cleanup();
    resetStageOptions();
    vi.restoreAllMocks();
    window.history.replaceState(null, "", "/");
  });

  async function openBatch() {
    render(<ProcurementRequests />);
    fireEvent.click(await screen.findByText("Обеспечение производства.001"));
    return screen.findByRole("table", { name: "Позиции заявки" });
  }

  it("shows the project's progress and opens a request into grouped lines", async () => {
    const table = await openBatch();
    expect(within(table).getByText("Маркировка")).toBeTruthy();
    expect(within(table).getByText("КП-2610-001")).toBeTruthy();
    expect(screen.getAllByText("Новая 2").length).toBeGreaterThan(0);
    expect(window.location.search).toContain("tab=requests");
  });

  it("selects a group and cancels it with a reason, reporting the skipped lines", async () => {
    await openBatch();
    fireEvent.click(screen.getByRole("checkbox", { name: "Выбрать группу Маркировка" }));
    const bar = await screen.findByRole("toolbar", { name: "Выбранные строки" });
    expect(within(bar).getByText("Выбрано строк: 2")).toBeTruthy();
    expect(within(bar).getByRole("button", { name: "Запросить КП (2)" })).toBeTruthy();
    fireEvent.click(within(bar).getByRole("button", { name: "Отменить выбранные" }));
    fireEvent.change(within(bar).getByLabelText("Причина отмены"), { target: { value: "дубль" } });
    fireEvent.click(within(bar).getByRole("button", { name: "Подтвердить отмену" }));
    await screen.findByText(/Отменено строк: 1\. Не отменены: SM-b — строка изменилась/);
    const sent = calls.find((call) => call.path === "/api/procurement/cancel-many");
    expect(JSON.parse(String(sent?.init?.body))).toEqual({
      lines: [{ id: "a", version: 2 }, { id: "b", version: 2 }], reason: "дубль",
    });
  });

  it("«Запросить КП» opens the supplier step for the selected lines", async () => {
    await openBatch();
    const boxes = screen.getAllByRole("checkbox", { name: /^Выбрать SM-/ });
    fireEvent.click(boxes[0]);
    fireEvent.click(boxes[1], { shiftKey: true });
    fireEvent.click(await screen.findByRole("button", { name: "Запросить КП (2)" }));
    await screen.findByText("Запрос КП по 2 строкам");
    await waitFor(() => expect(calls.some((call) => call.path.startsWith("/api/purchasing/rfqs/suggest"))).toBe(true));
    expect(screen.queryByRole("toolbar", { name: "Выбранные строки" })).toBeNull();
  });

  it("is read only in «Все» and for a viewer", async () => {
    render(<ProcurementRequests readOnly />);
    fireEvent.click(await screen.findByText("Обеспечение производства.001"));
    await screen.findByRole("table", { name: "Позиции заявки" });
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Разделить" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Мои" })).toBeNull();
    expect(calls.some((call) => call.path.includes("scope=all"))).toBe(true);
  });
});
