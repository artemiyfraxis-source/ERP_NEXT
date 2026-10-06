import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectsPage, deleteSummary, kitBatch } from "./ProjectsPage";

const editor = {
  user: { id: "u1", email: "engineer@example.test", name: "Технолог", department: "Производство" },
  roles: ["tech_lead"], access_level: "tech_lead", permissions: { projects: "edit" as const },
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
      { art: "UL1007 22AWG", type: "Провод", name: "", link: "", per: "1.5", scrap: "5", batch: "15.75", unit: "м", lot: null },
      { art: "43025-0400", type: "Разъём", name: "Корпус Molex", link: "Склад", per: "2", scrap: "5", batch: "21", unit: "шт", lot: null },
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
      if (url.startsWith("/api/techcards/kitsets/lots")) return json({ lots: [{ lot_id: "l1", warehouse: "ЦАР", grn: "FRX.26.0000001", package: "", article: "43025-0400", name: "Корпус Molex", unit: "шт", storage_location: "1F4", available: "15", reserved: "5" }] });
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

  it("changes the project status in one click from the tile: stage, ХОЛД with a reason, close", async () => {
    const calls: Array<{ url: string; body: unknown }> = [];
    const base = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = decodeURIComponent(String(input));
      if (url === "/api/techcards/kitsets/708К.4/status") {
        calls.push({ url, body: JSON.parse(String(init?.body)) });
        return json({ events: 1, card });
      }
      return base(input, init);
    });
    render(<ProjectsPage principal={editor} />);
    const chip = await screen.findByRole("button", { name: "Статус проекта 708К.4: Новый" });
    fireEvent.click(chip);
    const menu = screen.getByRole("menu", { name: "Статус проекта 708К.4" });
    fireEvent.click(within(menu).getByRole("menuitemradio", { name: "В работе" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toEqual({ action: "status", value: "В работе" });
    expect(screen.queryByText("Жгут питания")).toBeNull(); // клик по статусу не открыл карточку

    fireEvent.click(screen.getByRole("button", { name: "Статус проекта 708К.4: Новый" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "ХОЛД" }));
    const reason = screen.getByLabelText("Причина ХОЛД");
    expect(screen.getByRole("button", { name: "Поставить" })).toBeDisabled();
    fireEvent.change(reason, { target: { value: "ждём оплату" } });
    fireEvent.click(screen.getByRole("button", { name: "Поставить" }));
    await waitFor(() => expect(calls).toHaveLength(2));
    expect(calls[1].body).toEqual({ action: "block", value: "ХОЛД", text: "ждём оплату" });

    fireEvent.click(screen.getByRole("button", { name: "Статус проекта 708К.4: Новый" }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Отказ" }));
    await waitFor(() => expect(calls).toHaveLength(3));
    expect(calls[2].body).toEqual({ action: "status", value: "Отказ" });
    // У проекта с активным блокером в меню — «Снять ХОЛД».
    fireEvent.click(screen.getByRole("button", { name: "Статус проекта 591К.3: В работе, блокер ХОЛД" }));
    expect(screen.getByRole("menuitem", { name: "Снять ХОЛД" })).toBeTruthy();
  });

  it("shows the status, active blocker and journal in the project card", async () => {
    const held = { ...card, status: "В работе", blocker: "ХОЛД", deadline: "2026-10-20",
      blocker_info: { type: "ХОЛД", text: "ждём оплату", since: "2026-09-28T10:00:00", who: "РП" },
      journal: [{ id: "e1", occurred_at: "2026-09-28T10:00:00", who: "РП", kind: "Блокер", blocker: "ХОЛД", text: "ждём оплату", item_index: "", value: "" }] };
    const posted: unknown[] = [];
    const base = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = decodeURIComponent(String(input));
      if (url === "/api/techcards/kitsets/708К.4" && (init?.method || "GET") === "GET") return json(held);
      if (url === "/api/techcards/kitsets/708К.4/journal") { posted.push(JSON.parse(String(init?.body))); return json({ card: held }); }
      return base(input, init);
    });
    window.history.replaceState({}, "", "/?section=projects&project=708К.4");
    render(<ProjectsPage principal={editor} />);
    expect(await screen.findByRole("button", { name: "Статус проекта 708К.4: В работе, блокер ХОЛД" })).toBeTruthy();
    expect(screen.getByText(/ХОЛД с 28\.09\.2026 · ждём оплату — РП/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Журнал · 1" }));
    fireEvent.change(screen.getByLabelText("Текст записи"), { target: { value: "позвонили заказчику" } });
    fireEvent.click(screen.getByRole("button", { name: "Записать" }));
    await waitFor(() => expect(posted).toEqual([{ kind: "note", text: "позвонили заказчику", deadline: null }]));
  });

  it("hides projects with an active blocker on request", async () => {
    const more = [...tiles, { ...tiles[1], project: "620К.1", status: "Новый", blocker: "СТОП" }];
    const fetchMock = vi.mocked(fetch);
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => String(input).startsWith("/api/techcards/kitsets?sort=")
      ? json({ items: more, statuses }) : base(input, init));
    render(<ProjectsPage principal={editor} />);
    expect(await screen.findByText("620К.1")).toBeTruthy();
    const before = more.filter((item) => item.status !== "Отгружено").length;
    const blocked = more.filter((item) => item.status !== "Отгружено" && item.blocker).length;
    expect(screen.getByText(`показано ${before} из ${more.length}`)).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Скрыть с блокерами"));
    expect(screen.queryByText("620К.1")).toBeNull();
    expect(screen.getByText(`показано ${before - blocked} из ${more.length}`)).toBeTruthy();
  });

  it("shows the status as a plain badge to a viewer", async () => {
    render(<ProjectsPage principal={viewer} />);
    expect(await screen.findByText("708К.4")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Статус проекта/ })).toBeNull();
  });

  it("hides shipped projects by default and projects with ready or checked techcards on request, per user", async () => {
    const stage = (tc: boolean, review: string | null) => ({ index: "КБ1", status: "Новый", manual: false, tc, kit: false, review: review ? { value: review, text: "", who: "" } : null });
    const more = [
      ...tiles,
      { ...tiles[1], project: "500К.1", status: "Отгружено", blocker: "", kb_stages: [stage(true, "проверена")] },
      { ...tiles[1], project: "600К.1", status: "Новый", blocker: "", kb_stages: [stage(true, null)] },
      { ...tiles[1], project: "610К.1", status: "Новый", blocker: "", kb_stages: [stage(true, "с погрешностями")] },
    ];
    const fetchMock = vi.mocked(fetch);
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => String(input).startsWith("/api/techcards/kitsets?sort=")
      ? json({ items: more, statuses }) : base(input, init));
    const { unmount } = render(<ProjectsPage principal={editor} />);
    expect(await screen.findByText("708К.4")).toBeTruthy();
    expect(screen.queryByText("500К.1")).toBeNull();
    expect(screen.getByText("показано 4 из 5")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Скрыть с проверенными ТК"));
    expect(screen.queryByText("610К.1")).toBeNull();
    expect(screen.getByText("600К.1")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Скрыть с готовыми ТК"));
    expect(screen.queryByText("600К.1")).toBeNull();
    expect(screen.getByText("показано 2 из 5")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Скрыть отгруженные"));
    fireEvent.click(screen.getByLabelText("Скрыть с готовыми ТК"));
    fireEvent.click(screen.getByLabelText("Скрыть с проверенными ТК"));
    expect(screen.getByText("500К.1")).toBeTruthy();
    expect(localStorage.getItem("kitHide.shipped.u1")).toBe("0");
    fireEvent.click(screen.getByLabelText("Скрыть с готовыми ТК"));
    unmount();
    render(<ProjectsPage principal={editor} />);
    expect(await screen.findByText("708К.4")).toBeTruthy();
    expect(screen.getByLabelText<HTMLInputElement>("Скрыть отгруженные").checked).toBe(false);
    expect(screen.getByLabelText<HTMLInputElement>("Скрыть с готовыми ТК").checked).toBe(true);
    expect(screen.queryByText("500К.1")).toBeNull();
  });

  it("creates a project from the calculation link, as SUPPLYMAP does", async () => {
    const fetchMock = vi.mocked(fetch);
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => {
      const url = decodeURIComponent(String(input));
      if (url === "/api/techcards/kitsets/load") return json({ res: { project: "708К.4.1", assemblies: 1, lines: 1, erp: "", erp_error: "", ship: "", ship_error: "", ship_to_error: "", deadline_error: "" } });
      // После «Создать» страница открывает карточку нового проекта.
      if (url === "/api/techcards/kitsets/708К.4.1" && !init?.method) return json({ ...card, project: "708К.4.1" });
      return base(input, init);
    });
    render(<ProjectsPage principal={editor} />);
    fireEvent.click(await screen.findByRole("button", { name: "Создать проект из расчёта" }));
    fireEvent.change(screen.getByLabelText("Ссылка на расчёт"), { target: { value: " https://docs.google.com/spreadsheets/d/1AbC/edit " } });
    fireEvent.click(screen.getByRole("button", { name: "Создать" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/techcards/kitsets/load")).toBe(true));
    const [, init] = fetchMock.mock.calls.find(([url]) => String(url) === "/api/techcards/kitsets/load")!;
    const body = JSON.parse(String(init?.body));
    expect(body.url).toBe("https://docs.google.com/spreadsheets/d/1AbC/edit");
    expect(body.content_b64).toBeUndefined();    // Дождаться открытой карточки: иначе её загрузка дорисовывается уже после теста.
    expect(await screen.findByText("Жгут питания")).toBeInTheDocument();
  });

  it("files purchase requests with the project only when the box is ticked", async () => {
    const fetchMock = vi.mocked(fetch);
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/requests/schema") return json({ purposes: ["RND", "Обеспечение производства"], delivery_points: ["Москва", "Раменское"] });
      if (url === "/api/techcards/kitsets/load") return json({ res: {
        project: "708К.4", assemblies: 1, lines: 2, erp_error: "", ship_error: "", ship_to_error: "", deadline_error: "",
        requests: { created: 5, existing: 0, skipped: [] }, requests_error: "",
      } });
      return base(input, init);
    });
    render(<ProjectsPage principal={{ ...editor, permissions: { projects: "edit" as const, newrequest: "edit" as const } }} />);
    fireEvent.click(await screen.findByRole("button", { name: "Создать проект из расчёта" }));
    fireEvent.change(screen.getByLabelText("Ссылка на расчёт"), { target: { value: "https://docs.google.com/spreadsheets/d/1AbC/edit" } });
    fireEvent.click(screen.getByLabelText("Сразу создать заявки на закупку по листу ЗКП"));
    await waitFor(() => expect((screen.getByLabelText("Назначение заявок") as HTMLSelectElement).value).toBe("Обеспечение производства"));
    fireEvent.change(screen.getByLabelText("Дедлайн закупки"), { target: { value: "2026-10-06" } });
    fireEvent.click(screen.getByRole("button", { name: "Создать проект и заявки" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/techcards/kitsets/load")).toBe(true));
    const [, init] = fetchMock.mock.calls.find(([url]) => String(url) === "/api/techcards/kitsets/load")!;
    expect(JSON.parse(String(init?.body)).requests).toEqual({
      purpose: "Обеспечение производства", delivery_point: "Москва", procurement_deadline: "2026-10-06",
    });
  });

  it("hides the requests box without the right to file requests", async () => {
    render(<ProjectsPage principal={editor} />);
    fireEvent.click(await screen.findByRole("button", { name: "Создать проект из расчёта" }));
    expect(screen.queryByLabelText("Сразу создать заявки на закупку по листу ЗКП")).toBeNull();
  });

  it("refreshes from the stored calculation link with the card's own number (kitRefresh)", async () => {
    const fetchMock = vi.mocked(fetch);
    const base = fetchMock.getMockImplementation()!;
    const linked = { ...card, calc_source: "https://docs.google.com/spreadsheets/d/1AbC/edit" };
    fetchMock.mockImplementation(async (input, init) => {
      const url = decodeURIComponent(String(input));
      if (url === "/api/techcards/kitsets/load") return json({ res: { project: "708К.4", assemblies: 1, lines: 2 } });
      if (url === "/api/techcards/kitsets/708К.4" && (init?.method || "GET") === "GET") return json(linked);
      return base(input, init);
    });
    render(<ProjectsPage principal={editor} />);
    fireEvent.click(await screen.findByText("708К.4"));
    expect((await screen.findByRole("link", { name: "расчёт" })).getAttribute("href")).toBe(linked.calc_source);
    fireEvent.click(screen.getByRole("button", { name: /Обновить из расчёта/ }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/techcards/kitsets/load")).toBe(true));
    const [, init] = fetchMock.mock.calls.find(([url]) => String(url) === "/api/techcards/kitsets/load")!;
    expect(JSON.parse(String(init?.body))).toEqual({ url: linked.calc_source, number: "708К.4" });
  });

  it("opens the assembly techcard right inside the project card, as SUPPLYMAP does", async () => {
    const fetchMock = vi.mocked(fetch);
    const base = fetchMock.getMockImplementation()!;
    const etk = {
      id: "tc-1", version: 1, project: "708К.4", item_index: "КБ1", product_name: "Жгут питания", source_calculation: "",
      deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
      created_at: "2026-09-29T10:00:00", updated_at: "2026-09-29T10:00:00", issued_at: null,
    };
    fetchMock.mockImplementation(async (input, init) => {
      const url = decodeURIComponent(String(input));
      if (url === "/api/techcards/kitsets/708К.4/techcards/КБ1") return json({ card_id: "tc-1" });
      if (url === "/api/techcards/projects/tc-1") return json(etk);
      if (url.endsWith("/revisions")) return json({ items: [] });
      return base(input, init);
    });
    render(<ProjectsPage principal={{ ...editor, permissions: { projects: "edit", techcards: "edit", tcgen: "edit" } }} />);
    fireEvent.click(await screen.findByText("708К.4"));
    // SUPPLYMAP renderSostav_: клик по строке сборки открывает её генератор.
    fireEvent.click(await screen.findByText("Жгут питания"));
    expect(await screen.findByRole("tab", { name: "Генератор", selected: true })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "КБ1", selected: true })).toBeTruthy();
    // Одна кнопка: ТК ещё нет — «Создать»; служебное свёрнуто в «Ещё».
    expect(await screen.findByRole("button", { name: "Создать" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Создать техкарты" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Выдать ЭТК" })).toBeNull();
    expect(screen.getByText("Ещё")).toBeTruthy();
    expect(window.location.search).toBe("?section=projects");
  });

  it("says there is no connection instead of reading the composition forever", async () => {
    const fetchMock = vi.mocked(fetch);
    const base = fetchMock.getMockImplementation()!;
    const etk = {
      id: "tc-1", version: 1, project: "708К.4", item_index: "КБ1", product_name: "Жгут питания", source_calculation: "",
      deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
      created_at: "2026-09-29T10:00:00", updated_at: "2026-09-29T10:00:00", issued_at: null,
    };
    fetchMock.mockImplementation(async (input, init) => {
      const url = decodeURIComponent(String(input));
      if (url === "/api/techcards/kitsets/708К.4/techcards/КБ1") return json({ card_id: "tc-1" });
      if (url === "/api/techcards/projects/tc-1") return json(etk);
      if (url.includes("/bom/")) throw new TypeError("Failed to fetch");
      return base(input, init);
    });
    render(<ProjectsPage principal={{ ...editor, permissions: { projects: "edit", techcards: "edit", tcgen: "edit" } }} />);
    fireEvent.click(await screen.findByText("708К.4"));
    fireEvent.click(await screen.findByText("Жгут питания"));
    expect(await screen.findByText(/Нет связи с ERP/)).toBeTruthy();
    expect(screen.queryByText("Читаю состав КБ1…")).toBeNull();
  });

  it("shows an issued techcard on its own tab and sends «Пересобрать» to the generator", async () => {
    const fetchMock = vi.mocked(fetch);
    const base = fetchMock.getMockImplementation()!;
    const etk = {
      id: "tc-1", version: 3, project: "708К.4", item_index: "КБ1", product_name: "Жгут питания", source_calculation: "",
      deadline: null, status: "Выдана", revision: 1, manual_operation_keys: [],
      document: { operations: [{ position: 1, code: "CUT_WIRE_auto", card: { name: "Резка", steps: [{ number: "1", kind: "Операция", text: "Отрезать" }] }, result: [], time: [] }], warnings: [] },
      created_at: "2026-09-29T10:00:00", updated_at: "2026-09-29T10:00:00", issued_at: "2026-09-29T10:00:00",
    };
    const issuedCard = { ...card, assemblies: [{ ...card.assemblies[0], techcard: { card_id: "tc-1", status: "Выдана", issued: true, stages: 1, revision: 1 } }] };
    fetchMock.mockImplementation(async (input, init) => {
      const url = decodeURIComponent(String(input));
      if (url === "/api/techcards/kitsets/708К.4" && (init?.method || "GET") === "GET") return json(issuedCard);
      if (url === "/api/techcards/kitsets/708К.4/techcards/КБ1") return json({ card_id: "tc-1" });
      if (url === "/api/techcards/projects/tc-1") return json(etk);
      if (url.endsWith("/revisions")) return json({ items: [] });
      return base(input, init);
    });
    render(<ProjectsPage principal={{ ...editor, permissions: { projects: "edit", techcards: "edit", tcgen: "edit" } }} />);
    fireEvent.click(await screen.findByText("708К.4"));
    fireEvent.click(await screen.findByRole("button", { name: "готова · 1 эт." }));
    expect(await screen.findByRole("tab", { name: "ТК КБ1", selected: true })).toBeTruthy();
    expect(await screen.findByText("Отрезать")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Пересобрать/ }));
    expect(await screen.findByRole("tab", { name: "Генератор", selected: true })).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Пересобрать" })).toBeTruthy();
  });

  it("hides writing controls for view-only access", async () => {
    render(<ProjectsPage principal={viewer} />);
    await screen.findByText("708К.4");
    expect(screen.queryByRole("button", { name: "Создать проект из расчёта" })).toBeNull();
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

    fireEvent.click(screen.getAllByRole("button", { name: "выбрать со склада" })[1]);
    expect(await screen.findByText("из них резерв проекта 5 шт")).toBeTruthy();
    const lotsCall = vi.mocked(fetch).mock.calls.find(([url]) => decodeURIComponent(String(url)).startsWith("/api/techcards/kitsets/lots"));
    expect(decodeURIComponent(String(lotsCall?.[0]))).toContain("&project=708К.4");  // its own «Со склада» reserve counts as free
    fireEvent.click(screen.getByRole("button", { name: "Выбрать" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "FRX.26.0000001 · резерв при комплектации" })).toBeTruthy());
    const lotCall = vi.mocked(fetch).mock.calls.filter(([url]) => decodeURIComponent(String(url)).endsWith("/lines")).at(-1);
    expect(JSON.parse(String(lotCall?.[1]?.body)).edits[0].lot).toEqual({ lot_id: "l1", warehouse: "ЦАР", grn: "FRX.26.0000001", package: "" });
  });

  describe("ГРН в спецификации: резерв при выборе", () => {
    const lot = { lot_id: "l1", warehouse: "ЦАР", grn: "FRX.26.0000001", package: "" };
    const withLot = (reserve: unknown) => ({
      ...card,
      assemblies: [{ ...card.assemblies[0], components: [card.assemblies[0].components[0], { ...card.assemblies[0].components[1], lot, reserve }] }],
    });
    function serve(cardBody: unknown, extra?: (url: string, method: string) => Response | undefined) {
      const base = vi.mocked(fetch).getMockImplementation()!;
      vi.mocked(fetch).mockImplementation(async (input, init) => {
        const url = decodeURIComponent(String(input));
        const method = init?.method || "GET";
        if (url === "/api/techcards/kitsets/708К.4" && method === "GET") return json(cardBody);
        return extra?.(url, method) ?? base(input, init);
      });
    }
    async function openSpec() {
      render(<ProjectsPage principal={editor} />);
      fireEvent.click(await screen.findByText("708К.4"));
      fireEvent.click(await screen.findByRole("tab", { name: "Спецификация" }));
    }

    it("shows the reserved part against the batch need and the new wording", async () => {
      serve(withLot({ quantity: "6", unit: "шт", lot_id: "l1", warehouse: "ЦАР", grn: "FRX.26.0000001", package: "" }));
      await openSpec();
      expect(await screen.findByRole("button", { name: "FRX.26.0000001 · резерв 6 из 21" })).toBeTruthy();
      expect(screen.getByText(/резервируется сразу при выборе/)).toBeTruthy();
      expect(screen.queryByText(/при отправке на комплектацию;/)).toBeNull();
    });

    it("says «резерв при комплектации» when a ГРН is picked but nothing is held", async () => {
      serve(withLot(null));
      await openSpec();
      expect(await screen.findByRole("button", { name: "FRX.26.0000001 · резерв при комплектации" })).toBeTruthy();
    });

    it("sends the lot version from the list with the pick", async () => {
      serve(card, (url) => url.startsWith("/api/techcards/kitsets/lots")
        ? json({ lots: [{ lot_id: "l1", warehouse: "ЦАР", grn: "FRX.26.0000001", package: "", article: "43025-0400", name: "Корпус Молекс", unit: "шт", storage_location: "1F4", available: "15", version: 7 }] })
        : undefined);
      await openSpec();
      fireEvent.click(screen.getAllByRole("button", { name: "выбрать со склада" })[1]);
      fireEvent.click(await screen.findByRole("button", { name: "Выбрать" }));
      await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith("/lines"))).toBe(true));
      const saved = vi.mocked(fetch).mock.calls.filter(([url]) => String(url).endsWith("/lines")).at(-1);
      expect(JSON.parse(String(saved?.[1]?.body)).edits[0].lot).toEqual({ lot_id: "l1", warehouse: "ЦАР", grn: "FRX.26.0000001", package: "", version: 7 });
    });

    it("on 409 explains the stale lot, keeps the picker open, reloads the lots and drops the pick", async () => {
      serve(card, (url, method) => url.endsWith("/lines") && method === "PUT" ? json({ detail: "conflict" }, 409) : undefined);
      await openSpec();
      fireEvent.click(screen.getAllByRole("button", { name: "выбрать со склада" })[1]);
      fireEvent.click(await screen.findByRole("button", { name: "Выбрать" }));
      expect(await screen.findByText("Партия изменилась — обновите список ГРН")).toBeTruthy();
      await waitFor(() => expect(vi.mocked(fetch).mock.calls.filter(([url]) => decodeURIComponent(String(url)).startsWith("/api/techcards/kitsets/lots")).length).toBeGreaterThan(1));
      expect(screen.getByRole("dialog", { name: "Выбор ГРН" })).toBeTruthy();
      fireEvent.click(screen.getByRole("button", { name: "Закрыть" }));
      expect(screen.getAllByRole("button", { name: "выбрать со склада" })).toHaveLength(2);
    });

    it("shows a non-empty reserve_error of the issue as a warning", async () => {
      serve(card, (url) => url.endsWith("/issue")
        ? json({ created: [], skipped: [], message: "Заявок создано: 0.", reserve_error: "FRX.26.0000001: партия занята" })
        : undefined);
      await openSpec();
      fireEvent.click(screen.getByRole("button", { name: "Отправить на комплектацию" }));
      fireEvent.change(screen.getByLabelText("Дедлайн"), { target: { value: "2026-10-20" } });
      fireEvent.click(screen.getByRole("button", { name: "Отправить" }));
      const warning = await screen.findByText(/FRX\.26\.0000001: партия занята/);
      expect(warning.closest(".warning-banner")).toBeTruthy();
    });
  });

  it("shows a piece line's batch in whole pieces while typing", async () => {
    render(<ProjectsPage principal={editor} />);
    fireEvent.click(await screen.findByText("708К.4"));
    fireEvent.click(await screen.findByRole("tab", { name: "Спецификация" }));
    fireEvent.change(screen.getByLabelText("На 1 шт 43025-0400"), { target: { value: "1,5" } });
    expect(screen.getByText("16")).toBeTruthy();  // 1,5 × 10 × 1,05 = 15,75 → 16 шт
    expect(screen.getByText("15.75")).toBeTruthy();  // провод в метрах — до сотых
  });

  it("orders what the project lacks from «Снабжение»: picked lines, the project deadline, the result banner", async () => {
    const supplyLine = { kbs: ["КБ1"], kitted: "0", grn: "", received: false, rows: [] };
    const lines = [
      { ...supplyLine, art: "UL1007 22AWG", name: "", need: "15.75", unit: "м", free: "0", ordered: "0", short: "15.75", stage: "не заведено" },
      { ...supplyLine, art: "43025-0400", name: "Корпус Molex", need: "20", unit: "шт", free: "15", ordered: "3", short: "2", stage: "Запрос · Новый" },
      { ...supplyLine, art: "43030-0007", name: "", need: "32", unit: "шт", free: "0", ordered: "32", short: "0", stage: "Закупка · В работе" },
    ];
    let done = false;
    const orders: unknown[] = [];
    const base = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = decodeURIComponent(String(input));
      if (url === "/api/techcards/kitsets/708К.4" && (init?.method || "GET") === "GET") return json({ ...card, deadline: "2026-10-20" });
      if (url === "/api/techcards/kitsets/708К.4/supply") {
        return json({ project: "708К.4", lines: done ? lines.map((line) => ({ ...line, short: "0", ordered: line.need })) : lines });
      }
      if (url === "/api/techcards/kitsets/708К.4/order-missing") {
        orders.push(JSON.parse(String(init?.body)));
        done = true;
        return json({ created: [{ art: "43025-0400", name: "Корпус Molex", quantity: "2", unit: "шт", legacy_id: "SM-20261005-01" }], skipped: [], message: "Заявки созданы: 1." });
      }
      return base(input, init);
    });
    render(<ProjectsPage principal={{ ...editor, permissions: { projects: "edit", newrequest: "edit" } }} />);
    fireEvent.click(await screen.findByText("708К.4"));
    fireEvent.click(await screen.findByRole("tab", { name: "Снабжение" }));

    expect(await screen.findByRole("button", { name: "Заказать недостающее (2)" })).toBeTruthy();
    expect((screen.getByLabelText("Срок закупки") as HTMLInputElement).value).toBe("2026-10-20");
    expect(screen.queryByLabelText("Заказать 43030-0007")).toBeNull();  // covered by the purchase: nothing to pick
    fireEvent.click(screen.getByLabelText("Заказать 43025-0400"));
    fireEvent.click(screen.getByRole("button", { name: "Заказать недостающее (1)" }));

    expect(await screen.findByText("Заявки созданы: 1.")).toBeTruthy();
    expect(orders).toEqual([{ articles: ["43025-0400"], deadline: "2026-10-20" }]);
    await waitFor(() => expect((screen.getByRole("button", { name: "Заказать недостающее (0)" }) as HTMLButtonElement).disabled).toBe(true));
  });

  it("counts stock without a ГРН in «Снабжение»: its own column, from stock, nothing to order", async () => {
    const base = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (decodeURIComponent(String(input)) === "/api/techcards/kitsets/708К.4/supply") {
        return json({ project: "708К.4", lines: [
          { art: "43025-0400", name: "Корпус Molex", kbs: ["КБ1"], need: "20", unit: "шт", free: "0", off_book: "20", ordered: "0", kitted: "0", short: "0", grn: "", stage: "Закупка · Готово · без ГРН", received: false, rows: [] },
          { art: "43030-0007", name: "Контакт", kbs: ["КБ2"], need: "32", unit: "шт", free: "0", off_book: "12", ordered: "18", kitted: "0", short: "2", grn: "", stage: "Закупка · В работе", received: false, rows: [] },
        ] });
      }
      return base(input, init);
    });
    render(<ProjectsPage principal={{ ...editor, permissions: { projects: "edit", newrequest: "edit" } }} />);
    fireEvent.click(await screen.findByText("708К.4"));
    fireEvent.click(await screen.findByRole("tab", { name: "Снабжение" }));

    expect(await screen.findByRole("columnheader", { name: /Без ГРН/ })).toBeTruthy();
    expect(screen.getByText("12")).toBeTruthy();
    expect(screen.getAllByText("Закупка · Готово · без ГРН").some((node) => node.className === "tone-info")).toBe(true);
    const fromStock = screen.getByText("Со склада, без закупки").parentElement!;
    expect(fromStock.querySelector("strong")!.textContent).toBe("1");
    expect(screen.queryByLabelText("Заказать 43025-0400")).toBeNull();  // covered without a ГРН: nothing to order
    expect(screen.getByLabelText("Заказать 43030-0007")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Заказать недостающее (1)" })).toBeTruthy();
  });

  it("shows «Снабжение» without the order button to whoever cannot file requests", async () => {
    const base = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (decodeURIComponent(String(input)) === "/api/techcards/kitsets/708К.4/supply") {
        return json({ project: "708К.4", lines: [{ art: "43025-0400", name: "Корпус Molex", kbs: ["КБ1"], need: "20", unit: "шт", free: "15", ordered: "0", kitted: "0", short: "5", grn: "", stage: "не заведено", received: false, rows: [] }] });
      }
      return base(input, init);
    });
    render(<ProjectsPage principal={editor} />);
    fireEvent.click(await screen.findByText("708К.4"));
    fireEvent.click(await screen.findByRole("tab", { name: "Снабжение" }));
    expect(await screen.findByText("Корпус Molex")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Заказать недостающее/ })).toBeNull();
    expect(screen.queryByLabelText("Заказать 43025-0400")).toBeNull();
  });

  it("sends the kit to picking only with a deadline", async () => {
    render(<ProjectsPage principal={editor} />);
    fireEvent.click(await screen.findByText("708К.4"));
    fireEvent.click(await screen.findByRole("tab", { name: "Спецификация" }));
    fireEvent.click(screen.getByRole("button", { name: "Отправить на комплектацию" }));
    fireEvent.click(screen.getByRole("button", { name: "Отправить" }));
    expect(screen.getByText("Укажи дедлайн.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Дедлайн"), { target: { value: "2026-10-10" } });
    fireEvent.click(screen.getByRole("button", { name: "Отправить" }));
    expect(await screen.findByText("Заявок создано: 1, из них ждут поставки: 1. В-26-0001 (недобор)")).toBeTruthy();
  });

  it("links КД from ERP, shows old Drive links as archive and detaches from ERP", async () => {
    const withKd = { ...card, assemblies: [{ ...card.assemblies[0], kd: [
      { id: "d1", index: "КБ1", name: "708К.4КБ1 — Чертёж.pdf", size: 13, stored: true, url: null, uploaded_at: "2026-09-29T10:00:00", who: "Технолог" },
      { id: "d0", index: "КБ1", name: "708К.4КБ1 — Старый.pdf", size: null, stored: false, url: "https://drive.google.com/file/d/f-old/view", uploaded_at: "2026-09-29T09:00:00", who: "Технолог" },
    ] }] };
    const base = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = decodeURIComponent(String(input));
      if (url === "/api/techcards/kitsets/708К.4" && (init?.method || "GET") === "GET") return json(withKd);
      if (url === "/api/techcards/kitsets/708К.4/documents/d1") return json({ res: { deleted: 1 } });
      return base(input, init);
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<ProjectsPage principal={editor} />);
    fireEvent.click(await screen.findByText("708К.4"));
    expect((await screen.findByRole("link", { name: "Чертёж.pdf" })).getAttribute("href"))
      .toBe("/api/techcards/kitsets/708%D0%9A.4/documents/d1");
    expect(screen.getByRole("link", { name: "Старый.pdf" }).getAttribute("href")).toBe("https://drive.google.com/file/d/f-old/view");
    expect(screen.getByText("на Диске (архив)")).toBeTruthy();
    fireEvent.click(screen.getAllByTitle("Отвязать")[0]);
    expect(confirm).toHaveBeenCalledWith("Отвязать КД? Файл и строка вложения удалятся из ERP.");
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/techcards/kitsets/708%D0%9A.4/documents/d1", expect.objectContaining({ method: "DELETE" })));
  });

  it("КД-ячейка: «Создать в редакторе» без чертежа, «Чертёж жгута» с чертежом (не файл), ссылка в генераторе", async () => {
    const drawing = { id: "h1", index: "КБ1", name: "708К.4КБ1 — Чертёж жгута.harness.json", size: 900, stored: true, url: null, uploaded_at: "2026-09-29T10:00:00", who: "Технолог", kind: "harness" as const };
    const base = vi.mocked(fetch).getMockImplementation()!;
    let current: unknown = card;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = decodeURIComponent(String(input));
      if (url === "/api/techcards/kitsets/708К.4" && (init?.method || "GET") === "GET") return json(current);
      return base(input, init);
    });
    const view = render(<ProjectsPage principal={editor} />);
    fireEvent.click(await screen.findByText("708К.4"));
    expect(await screen.findByRole("button", { name: "Создать в редакторе" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "＋ файл" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Чертёж жгута" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Создать в редакторе" }));
    expect(window.location.search).toContain("harness=%D0%9A%D0%911");
    expect(window.location.search).toContain("section=projects");
    view.unmount();

    window.history.replaceState({}, "", "/?section=projects");
    current = { ...card, assemblies: [{ ...card.assemblies[0], kd: [drawing] }] };
    render(<ProjectsPage principal={editor} />);
    fireEvent.click(await screen.findByText("708К.4"));
    const open = await screen.findByRole("button", { name: "Чертёж жгута" });
    expect(screen.queryByRole("button", { name: "Создать в редакторе" })).toBeNull();
    expect(screen.queryByRole("link", { name: /harness/ })).toBeNull(); // не файл для скачивания
    expect(screen.getByRole("button", { name: "＋ файл" })).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Генератор" }));
    fireEvent.click(await screen.findByRole("tab", { name: "КБ1" }));
    expect((await screen.findAllByRole("button", { name: "Чертёж жгута" })).length).toBeGreaterThan(0);
    expect(open).toBeTruthy();
  });

  it("opens the track of the project only on the «Трек» tab", async () => {
    const trackCalls: string[] = [];
    const base = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = decodeURIComponent(String(input));
      if (url === "/api/techcards/kitsets/708К.4/track") {
        trackCalls.push(url);
        return json({ project: "708К.4", entries: [
          { at: "2026-10-04T14:30:00", module: "Техкарты", kind: "Техкарта выдана", ref: "708К.4КБ1", status: "Выдана", item_index: "КБ1" },
          { at: null, module: "Отгрузка", kind: "Отправка уехала", ref: "ОТ-0001", status: "Запланирована", item_index: "" },
        ] });
      }
      return base(input, init);
    });
    window.history.replaceState({}, "", "/?section=projects&project=708К.4");
    render(<ProjectsPage principal={editor} />);
    const tab = await screen.findByRole("tab", { name: "Трек" });
    expect(trackCalls).toHaveLength(0);
    fireEvent.click(tab);
    expect(await screen.findByText("04.10.2026 14:30")).toBeTruthy();
    expect(screen.getByText("Отправка уехала")).toBeTruthy();
    expect(trackCalls).toHaveLength(1);
  });

  it("mirrors the server batch formula and delete summary", () => {
    expect(kitBatch("1,5", "10", "5", "м")).toBe(15.75);
    expect(kitBatch("1,5", "3", "5", "м")).toBe(4.73);
    expect(kitBatch("", "10", "5", "м")).toBe(0);
    // Штучное — вверх до целого после брака (половинки штуки не бывает): 4,725 → 5; целое остаётся целым.
    expect(kitBatch("1,5", "3", "5", "шт")).toBe(5);
    expect(kitBatch("0.333", "3", "5", "шт")).toBe(2);
    expect(kitBatch("2", "10", "5", "шт")).toBe(21);
    expect(kitBatch("0.1", "30", "0", "шт")).toBe(3);
    expect(kitBatch("", "10", "5", "шт")).toBe(0);
    expect(deleteSummary({ project: "708К.4", deleted: 3, erp: { "Проекты": 1 }, cards: { cards: 2, revisions: 1 }, gp: { deleted: 2, kept: 1 } }))
      .toBe("Снесено строк: спецификация 3, проекты 1, техкарты 2, склад ГП 2. На складе ГП осталось с движением: 1");
    const tails = {
      fulfillment: { deleted: 12, kept: 1 }, production: { deleted: 1, kept: 0 }, requests: { deleted: 3, kept: 0 },
      journal: { deleted: 4, kept: 0 },
      freed: 0, kept: [{ section: "fulfillment", label: "ЗК-0005 · 719К.2 · КБ3", reason: "по нему уже выдано со склада" }],
      message: "Удалено: 12 запросов на комплектацию, 1 производственное задание, 3 заявки на закупку, 4 записи журнала проекта. "
        + "Осталось: 1 запрос на комплектацию — по нему уже выдано со склада (ЗК-0005 · 719К.2 · КБ3).",
    };
    // Журнал проекта (`journal.deleted`) назван в итоге сервера — вместе с хвостами, отдельной строки у него нет.
    expect(deleteSummary({ project: "719К.2", deleted: 3, erp: {}, cards: { cards: 0, revisions: 0 }, gp: { deleted: 0, kept: 0 }, journal: { deleted: 4 }, tails, tails_error: "" }))
      .toBe("Снесено строк: спецификация 3. " + tails.message);
    expect(deleteSummary({ project: "719К.2", deleted: 3, erp: {}, cards: { cards: 0, revisions: 0 }, gp: { deleted: 0, kept: 0 }, journal: { deleted: 0 }, tails: null, tails_error: "" }))
      .toBe("Снесено строк: спецификация 3");
    expect(deleteSummary({ project: "719К.2", deleted: 3, erp: {}, cards: { cards: 0, revisions: 0 }, gp: { deleted: 0, kept: 0 }, tails: null, tails_error: "Задания не удалены: сбой" }))
      .toBe("Снесено строк: спецификация 3. Задания не удалены: сбой");
  });
});
