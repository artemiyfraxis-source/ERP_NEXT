import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProcurementRequests } from "./ProcurementRequests";
import type { RqLine } from "./procurementRequestsApi";
import {
  GROUP_KEY, groupLines, orderedCounts, planSum, projectTitle, quantityInput, rangeIds, readGroupBy, setMany, shownOrder,
  writeGroupBy,
} from "./procurementRequestsLogic";
import { projectKeyFor } from "./requestFormV2Logic";

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
  batches_total: 1,
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
      if (path.startsWith("/api/procurement/requests-view")) {
        return json(path.includes("batch=") ? { scope: "mine", batch: BATCH, lines } : summary);
      }
      if (path === "/api/procurement/cancel-many") return json({ cancelled: ["a"], skipped: [{ id: "b", reason: "строка изменилась — обновите список" }] });
      if (path.startsWith("/api/purchasing/rfqs/suggest")) return json({ categories: [], lines: 2, suggestions: [] });
      if (path.startsWith("/api/purchasing/suppliers") || path.includes("directory")) return json({ suppliers: [] });
      return json({});
    });
  });

  afterEach(() => {
    cleanup();
   
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
    const dialog = screen.getByRole("dialog", { name: "Отменить закупку" });
    expect(within(dialog).getByText("Выбрано строк: 2")).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "Подтвердить отмену" }).hasAttribute("disabled")).toBe(true);
    fireEvent.change(within(dialog).getByLabelText("Причина отмены"), { target: { value: "дубль" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Подтвердить отмену" }));
    await screen.findByText(/Отменено строк: 1\. Не отменены: SM-b — строка изменилась/);
    const sent = calls.find((call) => call.path === "/api/procurement/cancel-many");
    expect(JSON.parse(String(sent?.init?.body))).toEqual({
      lines: [{ id: "a", version: 2 }, { id: "b", version: 2 }], reason: "дубль",
    });
  });

  const unclaimed = line("d", {
    status: "Новый", mine: false, buyer: null, selectable: true, can_take: true,
    can_split: false, can_cover: false,
  });

  function useLines(rows: RqLine[]) {
    const base = vi.mocked(globalThis.fetch).getMockImplementation()!;
    vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
      if (String(input).startsWith("/api/procurement/requests-view") && String(input).includes("batch=")) {
        calls.push({ path: String(input), init });
        return json({ scope: "mine", batch: BATCH, lines: rows });
      }
      return base(input, init);
    });
  }

  it("offers refusal and selection for an unclaimed new line without manual taking", async () => {
    useLines([...lines, unclaimed]);
    const table = await openBatch();
    const row = within(table).getByText("Позиция d").closest("tr")!;
    expect(screen.queryByRole("button", { name: "Взять в работу" })).toBeNull();
    expect(within(row).getByRole("checkbox")).toBeTruthy();
    fireEvent.click(within(row).getByRole("button", { name: "Отказать" }));
    const dialog = screen.getByRole("dialog", { name: "Отказать в закупке" });
    expect(within(dialog).getByText("Позиция d · SM-d")).toBeTruthy();
    const reason = within(dialog).getByLabelText("Причина отказа");
    expect(reason.getAttribute("maxlength")).toBe("500");
    fireEvent.change(reason, { target: { value: "   " } });
    expect(within(dialog).getByRole("button", { name: "Подтвердить отказ" }).hasAttribute("disabled")).toBe(true);
    fireEvent.submit(reason.closest("form")!);
    expect(calls.some((call) => call.path === "/api/procurement/cancel-many")).toBe(false);
    fireEvent.click(within(dialog).getByRole("button", { name: "Не отменять" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(within(row).getByRole("button", { name: "Отказать" }));
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls.some((call) => call.path === "/api/procurement/cancel-many")).toBe(false);
  });

  it("selects a new line, returns from suggestions without writes, then creates the RFQ without taking first", async () => {
    useLines([unclaimed]);
    const base = vi.mocked(globalThis.fetch).getMockImplementation()!;
    vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
      if (String(input) === "/api/purchasing/rfqs") {
        calls.push({ path: String(input), init });
        return json({ id: "rfq-direct", number: "КП-2611-001", suppliers: 1 }, 201);
      }
      return base(input, init);
    });
    await openBatch();
    expect(screen.queryByRole("button", { name: "Взять в работу" })).toBeNull();
    fireEvent.click(screen.getByRole("checkbox", { name: "Выбрать SM-d" }));
    fireEvent.click(screen.getByRole("button", { name: "Запросить КП (1)" }));
    await screen.findByText("Запрос КП по 1 строке");
    await screen.findByText(/Подсказать некого/);
    fireEvent.click(screen.getByRole("button", { name: "Назад" }));
    expect(calls.filter((call) => call.init?.method === "POST")).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Запросить КП (1)" }));
    await screen.findByText(/Подсказать некого/);
    fireEvent.change(screen.getByLabelText("Поставщик по названию"), { target: { value: "ООО Кабель" } });
    fireEvent.click(screen.getByRole("button", { name: "+ поставщик по названию" }));
    fireEvent.click(screen.getByRole("button", { name: "Создать запрос" }));
    await waitFor(() => expect(window.location.search).toContain("rfq=rfq-direct"));
    const posts = calls.filter((call) => call.init?.method === "POST");
    expect(posts).toHaveLength(1);
    expect(posts[0].path).toBe("/api/purchasing/rfqs");
    expect(JSON.parse(String(posts[0].init?.body))).toMatchObject({
      line_ids: ["d"], suppliers: [{ name: "ООО Кабель", reason: "manual", line_ids: ["d"] }],
    });
    expect(calls.some((call) => call.path === "/api/procurement/take")).toBe(false);
  });

  it("keeps the reason on HTTP error, retries once and reports a skipped refusal honestly", async () => {
    useLines([unclaimed]);
    const base = vi.mocked(globalThis.fetch).getMockImplementation()!;
    let attempts = 0;
    let finish: (response: Response) => void = () => undefined;
    vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
      if (String(input) === "/api/procurement/cancel-many") {
        calls.push({ path: String(input), init });
        attempts += 1;
        return attempts === 1 ? json({ detail: "Временная ошибка" }, 503) : new Promise<Response>((resolve) => { finish = resolve; });
      }
      return base(input, init);
    });
    await openBatch();
    fireEvent.click(screen.getByRole("button", { name: "Отказать" }));
    const dialog = screen.getByRole("dialog", { name: "Отказать в закупке" });
    const reason = within(dialog).getByLabelText("Причина отказа") as HTMLTextAreaElement;
    fireEvent.change(reason, { target: { value: "  Уже заказано  " } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Подтвердить отказ" }));
    await within(dialog).findByRole("alert");
    expect(reason.value).toBe("  Уже заказано  ");
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Подтвердить отказ" }).hasAttribute("disabled")).toBe(false));
    fireEvent.click(within(dialog).getByRole("button", { name: "Подтвердить отказ" }));
    fireEvent.submit(reason.closest("form")!);
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(attempts).toBe(2);
    expect(within(dialog).getByRole("button", { name: "Сохраняю…" }).hasAttribute("disabled")).toBe(true);
    finish(json({ cancelled: [], skipped: [{ id: "d", reason: "строка изменилась — обновите список" }] }));
    await screen.findByText("Отменено строк: 0. Не отменены: SM-d — строка изменилась — обновите список");
    expect(screen.queryByRole("dialog")).toBeNull();
    const requests = calls.filter((call) => call.path === "/api/procurement/cancel-many");
    expect(requests).toHaveLength(2);
    for (const request of requests) expect(JSON.parse(String(request.init?.body))).toEqual({
      lines: [{ id: "d", version: 2 }], reason: "Уже заказано",
    });
    expect(calls.some((call) => call.path === "/api/procurement/take")).toBe(false);
  });

  it("cancels an owned row through the same modal", async () => {
    useLines([lines[0]]);
    await openBatch();
    fireEvent.click(screen.getByRole("button", { name: "Отменить" }));
    const dialog = screen.getByRole("dialog", { name: "Отменить закупку" });
    fireEvent.change(within(dialog).getByLabelText("Причина отмены"), { target: { value: "  Не требуется  " } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Подтвердить отмену" }));
    await screen.findByRole("status");
    expect(JSON.parse(String(calls.find((call) => call.path === "/api/procurement/cancel-many")?.init?.body))).toEqual({
      lines: [{ id: "a", version: 2 }], reason: "Не требуется",
    });
  });

  it("hides cancellation for a foreign or non-cancellable row", async () => {
    useLines([line("e", { mine: false, selectable: false, can_cancel: false }), line("f", { can_cancel: false })]);
    await openBatch();
    expect(screen.queryByRole("button", { name: "Отказать" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Отменить" })).toBeNull();
  });

  it("hides refusal and cancellation in «Все»", async () => {
    useLines([...lines, unclaimed]);
    render(<ProcurementRequests />);
    fireEvent.click(await screen.findByRole("tab", { name: "Все" }));
    fireEvent.click(await screen.findByText("Обеспечение производства.001"));
    await screen.findByRole("table", { name: "Позиции заявки" });
    expect(screen.queryByRole("button", { name: "Отказать" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Отменить" })).toBeNull();
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
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

  describe("не переданные заявки", () => {
    const req = (id: string, over: Record<string, unknown> = {}) => ({
      id, legacy_id: `REQ-${id}`, name: `Разъём ${id}`, item_type: "Разъём", quantity: "5", unit: "шт", status: "Новый",
      project_reference: "614К.1", designation: "", initiator: "Орлова Анна", created_at: "2026-10-05", planned_date: "2026-10-15", ...over,
    });
    let handed: string[][];
    let fresh: boolean;
    beforeEach(() => {
      handed = [];
      fresh = true;
      const base = vi.mocked(globalThis.fetch).getMockImplementation()!;
      vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
        const path = String(input);
        if (path === "/api/requests") {
          calls.push({ path, init });
          const many = Array.from({ length: 101 }, (_, i) => req(`n${i}`));
          return json({ requests: fresh ? [...many, req("x", { status: "В работе" })] : [] });
        }
        if (path === "/api/requests/handoff") {
          calls.push({ path, init });
          handed.push(JSON.parse(String(init?.body)).request_ids);
          fresh = false;
          return json({ accepted: [] });
        }
        return base(input, init);
      });
    });

    it("shows the «Не передана» chip and the lines of the project group", async () => {
      render(<ProcurementRequests canHandoff />);
      expect(await screen.findByText("Не передана 101")).toBeTruthy();
      fireEvent.click(screen.getByText("Не переданы в закупку"));
      const table = await screen.findByRole("table", { name: "Не переданы в закупку: 614К.1" });
      expect(within(table).getByText("REQ-n0")).toBeTruthy();
      expect(within(table).queryByText("REQ-x")).toBeNull();
    });

    it("offers «В закупку» only with the handoff right and not read only", async () => {
      const { unmount } = render(<ProcurementRequests canHandoff readOnly />);
      await screen.findByText("Не передана 101");
      expect(screen.queryByRole("button", { name: "В закупку" })).toBeNull();
      unmount();
      calls.length = 0;
      render(<ProcurementRequests />);
      await screen.findByText("Обеспечение производства.001");
      expect(screen.queryByText(/Не передана/)).toBeNull();
      expect(calls.some((call) => call.path === "/api/requests")).toBe(false);
    });

    it("hands the group over in chunks of 100, reports and reloads", async () => {
      render(<ProcurementRequests canHandoff openRegister={() => undefined} />);
      fireEvent.click(await screen.findByRole("button", { name: "В закупку" }));
      await screen.findByText("Передано в закупку: 101 заявка");
      expect(handed.map((ids) => ids.length)).toEqual([100, 1]);
      await waitFor(() => expect(screen.queryByText(/Не передана/)).toBeNull());
      expect(screen.getByRole("button", { name: "Реестр заявок →" })).toBeTruthy();
    });
  });

  it("is read only in «Все» and for a viewer", async () => {
    useLines([...lines, unclaimed]);
    render(<ProcurementRequests readOnly />);
    fireEvent.click(await screen.findByText("Обеспечение производства.001"));
    await screen.findByRole("table", { name: "Позиции заявки" });
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Разделить" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Отказать" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Отменить" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Мои" })).toBeNull();
    expect(calls.some((call) => call.path.includes("scope=all"))).toBe(true);
  });
});
