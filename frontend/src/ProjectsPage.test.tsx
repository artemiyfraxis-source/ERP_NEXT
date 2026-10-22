import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProjectsPage, deleteSummary, kitBatch } from "./ProjectsPage";

// Редактор чертежа — внутри карточки проекта вкладкой; сам редактор в jsdom не нужен, достаточно заглушки с «← Назад».
vi.mock("./HarnessPage", () => ({
  HarnessPage: ({ project, index, onBack }: { project: string; index: string; onBack: () => void }) =>
    <section aria-label={`Редактор ${project} ${index}`}><button onClick={onBack}>← Назад</button></section>,
}));

const editor = {
  user: { id: "u1", email: "engineer@example.test", name: "Технолог", department: "Производство" },
  roles: ["tech_lead"], access_level: "tech_lead", permissions: { projects: "edit" as const },
};
const viewer = { ...editor, permissions: { projects: "view" as const } };

const statuses = ["Новый", "ТК готова", "КБ набрана", "В работе", "КБ на складе", "Отгружено", "Отменён"];
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

const preview = (over: Record<string, unknown> = {}) => ({
  project: "708К.4", value: "В работе", kb: "", direction: "forward", reason_required: false, error: "", message: "",
  terminal: "", blocker: "", warnings: [], preview_token: "tok-kit",
  kbs: [{ kb: "КБ1", label: "КБ1", from: "Новый", to: "В работе", skipped: ["ТК готова", "КБ набрана"], change: true, note: "", warnings: [] }],
  ...over,
});

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
      if (url.startsWith("/api/techcards/kitsets/708К.4/status/preview")) return json(preview());
      if (url === "/api/techcards/kitsets/708К.4/lines") return json({ res: { updated: 1 } });
      if (url === "/api/techcards/kitsets/708К.4/issue") return json({ created: [{ no: "В-26-0001", index: "КБ1", shortfall: ["x"] }], skipped: [], message: "Заявок создано: 1, из них ждут поставки: 1." });
      if (url.startsWith("/api/techcards/kitsets/lots")) return json({ lots: [{ lot_id: "l1", warehouse: "ЦАР", grn: "FRX.26.0000001", package: "", article: "43025-0400", name: "Корпус Molex", unit: "шт", storage_location: "1F4", available: "15", reserved: "5" }] });
      return json({});
    }));
  });

  it("lists project tiles with facts, filters by status and keeps the choice", async () => {
    render(<ProjectsPage principal={editor} />);
    expect(await screen.findByText("708К.4")).toBeTruthy();
    // Факты — общим чипом статуса, с тем, о скольких КБ речь (аудит 10.10, ПР-4, ПР-11); корзины на плитке нет.
    expect(screen.getByText("ТК 1 из 2 КБ").classList.contains("status-chip")).toBe(true);
    expect(screen.getByText("ХОЛД").classList.contains("status-chip")).toBe(true);
    expect(screen.getByText("проверена — КБ1")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Удалить/ })).toBeNull();
    expect(document.body.textContent).not.toMatch(/[⛔🗑✓]/u);
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
    // Этап ставится после подтверждения в окне с предпросмотром.
    const dialog = await screen.findByRole("dialog", { name: "Смена этапа" });
    expect(await within(dialog).findByText("пропущены: ТК готова, КБ набрана")).toBeTruthy();
    expect(calls).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole("button", { name: "Сменить этап" }));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toEqual({ action: "status", value: "В работе", reason: "", preview_token: "tok-kit" });
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
    fireEvent.click(screen.getByRole("menuitemradio", { name: "Отменён" }));
    await waitFor(() => expect(calls).toHaveLength(3));
    expect(calls[2].body).toEqual({ action: "status", value: "Отменён" });
    // У проекта с активным блокером в меню — «Снять ХОЛД».
    fireEvent.click(screen.getByRole("button", { name: "Статус проекта 591К.3: В работе, блокер ХОЛД" }));
    expect(screen.getByRole("menuitem", { name: "Снять ХОЛД" })).toBeTruthy();
  });

  it("«Состав» has no finished-goods column and never asks for it (owner 14.10)", async () => {
    window.history.replaceState({}, "", "/?section=projects&project=708К.4");
    render(<ProjectsPage principal={editor} />);
    expect(await screen.findByLabelText("Этап КБ1")).toBeTruthy();
    expect(screen.queryByRole("columnheader", { name: "Готовая продукция" })).toBeNull();
    expect(vi.mocked(fetch).mock.calls.some(([target]) => String(target).includes("/finished-goods/"))).toBe(false);
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
    expect(screen.getByText("с 28.09.2026 · ждём оплату — РП")).toBeTruthy();
    expect(document.querySelector(".kit-blocker .status-chip")?.textContent).toBe("ХОЛД");
    fireEvent.click(screen.getByRole("tab", { name: "Журнал · 1" }));
    // Текст записи — растущее поле; Enter записывает, как раньше в однострочном.
    const text = screen.getByLabelText("Текст записи");
    expect(text.tagName).toBe("TEXTAREA");
    fireEvent.change(text, { target: { value: "позвонили заказчику" } });
    fireEvent.keyDown(text, { key: "Enter" });
    await waitFor(() => expect(posted).toEqual([{ kind: "note", text: "позвонили заказчику", deadline: null }]));
  });

  it("changes one КБ stage from «Состав» through the confirm dialog and shows the journal «было → стало»", async () => {
    const skippedCard = { ...card, status: "В работе",
      assemblies: [{ ...card.assemblies[0], stage: { ...card.assemblies[0].stage, status: "Отгружено", manual: true, skipped: ["КБ на складе"] } }],
      journal: [{ id: "e1", occurred_at: "2026-09-28T10:00:00", who: "РП", kind: "Этап КБ", blocker: "", text: "В работе → Отгружено · пропущены: КБ на складе · причина: сделали вживую", item_index: "КБ1", value: "Отгружено", from_value: "В работе", reason: "сделали вживую" }] };
    const marks: unknown[] = [];
    const previews: string[] = [];
    const base = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = decodeURIComponent(String(input));
      if (url === "/api/techcards/kitsets/708К.4" && (init?.method || "GET") === "GET") return json(skippedCard);
      if (url.startsWith("/api/techcards/kitsets/708К.4/status/preview")) { previews.push(url); return json(preview({ direction: "back", reason_required: true, kb: "КБ1" })); }
      if (url === "/api/techcards/kitsets/708К.4/marks") { marks.push(JSON.parse(String(init?.body))); return json({ msg: "КБ1: Отгружено → Новый." }); }
      return base(input, init);
    });
    window.history.replaceState({}, "", "/?section=projects&project=708К.4");
    render(<ProjectsPage principal={editor} />);
    const select = await screen.findByLabelText("Этап КБ1");
    expect(screen.getByText("пропущен").getAttribute("title")).toBe("Пропущены: КБ на складе");
    fireEvent.change(select, { target: { value: "Новый" } });
    const dialog = await screen.findByRole("dialog", { name: "Смена этапа" });
    const confirm = await within(dialog).findByRole("button", { name: "Сменить этап" });
    expect(previews[0]).toBe("/api/techcards/kitsets/708К.4/status/preview?value=Новый&kb=КБ1");
    await waitFor(() => expect(within(dialog).getByText("Причина (обязательно: этап назад)")).toBeTruthy());
    expect(confirm).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("Причина смены этапа"), { target: { value: "ошибка" } });
    fireEvent.click(confirm);
    await waitFor(() => expect(marks).toEqual([{ kb: "КБ1", kind: "stage", value: "Новый", text: "", reason: "ошибка", preview_token: "tok-kit" }]));

    fireEvent.click(screen.getByRole("tab", { name: "Журнал · 1" }));
    expect(screen.getByText("В работе → Отгружено · пропущены: КБ на складе · причина: сделали вживую")).toBeTruthy();
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

  it("hides projects in work on request — владелец 19.10", async () => {
    const more = [...tiles, { ...tiles[1], project: "630К.1", status: "В работе", blocker: "" }];
    const fetchMock = vi.mocked(fetch);
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => String(input).startsWith("/api/techcards/kitsets?sort=")
      ? json({ items: more, statuses }) : base(input, init));
    render(<ProjectsPage principal={editor} />);
    expect(await screen.findByText("630К.1")).toBeTruthy();
    const before = more.filter((item) => item.status !== "Отгружено").length;
    const working = more.filter((item) => item.status === "В работе").length;
    fireEvent.click(screen.getByLabelText("Скрыть в работе"));
    expect(screen.queryByText("630К.1")).toBeNull();
    expect(screen.getByText(`показано ${before - working} из ${more.length}`)).toBeTruthy();
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
    expect((await screen.findByRole("link", { name: "Расчёт" })).getAttribute("href")).toBe(linked.calc_source);
    fireEvent.click(screen.getByRole("button", { name: "Ещё действия с проектом 708К.4" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Обновить из расчёта/ }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === "/api/techcards/kitsets/load")).toBe(true));
    const [, init] = fetchMock.mock.calls.find(([url]) => String(url) === "/api/techcards/kitsets/load")!;
    expect(JSON.parse(String(init?.body))).toEqual({ url: linked.calc_source, number: "708К.4" });
  });

  it("shows the project folder link and sets it from the «Ещё» menu with a Russian error for a bad address", async () => {
    const fetchMock = vi.mocked(fetch);
    const base = fetchMock.getMockImplementation()!;
    const folder = "https://drive.google.com/drive/folders/abc";
    fetchMock.mockImplementation(async (input, init) => {
      const url = decodeURIComponent(String(input));
      if (url === "/api/techcards/kitsets/708К.4/folder" && init?.method === "PUT") return json({ card: { ...card, folder_url: JSON.parse(String(init.body)).url || null } });
      return base(input, init);
    });
    render(<ProjectsPage principal={editor} />);
    fireEvent.click(await screen.findByText("708К.4"));
    await screen.findByRole("button", { name: "Ещё действия с проектом 708К.4" });
    expect(screen.queryByRole("link", { name: "Папка" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Ещё действия с проектом 708К.4" }));
    fireEvent.click(screen.getByRole("menuitem", { name: /Папка проекта/ }));
    const input = await screen.findByLabelText("Ссылка на папку");
    fireEvent.change(input, { target: { value: "drive.google.com/x" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect((await screen.findByRole("alert")).textContent).toContain("http://");
    fireEvent.change(input, { target: { value: folder } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    const link = await screen.findByRole("link", { name: "Папка" });
    expect(link.getAttribute("href")).toBe(folder);
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener");
    const [, init] = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/folder"))!;
    expect(JSON.parse(String(init?.body))).toEqual({ url: folder });
  });

  it("finds the project folder from the calculation link with «Папка?» and then shows «Папка»", async () => {
    const fetchMock = vi.mocked(fetch);
    const base = fetchMock.getMockImplementation()!;
    const folder = "https://drive.google.com/drive/folders/fold123";
    fetchMock.mockImplementation(async (input, init) => {
      const url = decodeURIComponent(String(input));
      if (url === "/api/techcards/kitsets/708К.4/folder/from-calc" && init?.method === "POST") return json({ card: { ...card, folder_url: folder } });
      return base(input, init);
    });
    render(<ProjectsPage principal={editor} />);
    fireEvent.click(await screen.findByText("708К.4"));
    fireEvent.click(await screen.findByRole("button", { name: "Папка?" }));
    const link = await screen.findByRole("link", { name: "Папка" });
    expect(link.getAttribute("href")).toBe(folder);
    expect(link.getAttribute("target")).toBe("_blank");
    expect(screen.queryByRole("button", { name: "Папка?" })).toBeNull();
  });

  it("shows a Russian error when the folder cannot be found by the calculation", async () => {
    const fetchMock = vi.mocked(fetch);
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => {
      if (String(input).endsWith("/folder/from-calc")) return json({ detail: "Нет доступа к файлу расчёта у сервисного аккаунта ERP" }, 422);
      return base(input, init);
    });
    render(<ProjectsPage principal={editor} />);
    fireEvent.click(await screen.findByText("708К.4"));
    fireEvent.click(await screen.findByRole("button", { name: "Папка?" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Нет доступа к файлу расчёта");
    expect(screen.getByRole("button", { name: "Папка?" })).toBeTruthy();
  });

  it("keeps only «Расчёт», «Папка» and «КД» in the header line, КД opening in a new tab", async () => {
    const fetchMock = vi.mocked(fetch);
    const base = fetchMock.getMockImplementation()!;
    const doc = (id: string, name: string) => ({ id, index: "КБ1", name, size: 3, stored: true, url: null, uploaded_at: "2026-09-29T10:00:00", who: "Технолог" });
    const withKd = (docs: ReturnType<typeof doc>[]) => ({ ...card, deadline: "2026-10-28", assemblies: [{ ...card.assemblies[0], kd: docs }] });
    let current = withKd([doc("d1", "708К.4КБ1 — Чертёж.pdf")]);
    fetchMock.mockImplementation(async (input, init) => {
      if (decodeURIComponent(String(input)) === "/api/techcards/kitsets/708К.4" && (init?.method || "GET") === "GET") return json(current);
      return base(input, init);
    });
    const first = render(<ProjectsPage principal={editor} />);
    fireEvent.click(await screen.findByText("708К.4"));
    const kd = await screen.findByRole("link", { name: "КД" });
    expect(kd.getAttribute("href")).toBe("/api/techcards/kitsets/708%D0%9A.4/documents/d1");
    expect(kd.getAttribute("target")).toBe("_blank");
    expect(kd.getAttribute("rel")).toBe("noopener");
    expect(document.querySelector(".kit-links")!.textContent).not.toMatch(/дедлайн|обновлено|ООО Заказчик/);
    first.unmount();

    current = withKd([doc("d1", "708К.4КБ1 — Чертёж.pdf"), doc("d2", "708К.4КБ1 — ТЗ.docx")]);
    render(<ProjectsPage principal={editor} />);
    fireEvent.click(await screen.findByText("708К.4"));
    await screen.findByText("КД · 2");
    const names = Array.from(document.querySelectorAll(".kit-kd-list a")).map((link) => [link.textContent, link.getAttribute("target")]);
    expect(names).toEqual([["КБ1 · Чертёж.pdf", "_blank"], ["КБ1 · ТЗ.docx", "_blank"]]);
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
    // Одна кнопка: ТК ещё нет — «Создать»; блока «Ещё» в генераторе нет.
    expect(await screen.findByRole("button", { name: "Создать" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Создать техкарты" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Выдать ЭТК" })).toBeNull();
    expect(screen.queryByText("Ещё")).toBeNull();
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

  it("deletes a project only from «Ещё» through the confirm window and reports back in the list (ПР-2)", async () => {
    const deleted: string[] = [];
    const base = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = decodeURIComponent(String(input));
      if (url === "/api/techcards/kitsets/708К.4" && init?.method === "DELETE") {
        deleted.push(url);
        return json({ res: { deleted: 3, erp: { "Проект": 1 } } });
      }
      return base(input, init);
    });
    render(<ProjectsPage principal={editor} />);
    fireEvent.click(await screen.findByText("708К.4"));
    expect(await screen.findByText("Жгут питания")).toBeTruthy();
    // «Отгрузить» нет совсем, рабочие действия и удаление — только внутри «Ещё».
    expect(screen.queryByRole("button", { name: "Отгрузить" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Обновить из расчёта/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Удалить/ })).toBeNull();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("708К.4");

    fireEvent.click(screen.getByRole("button", { name: "Ещё действия с проектом 708К.4" }));
    expect(screen.getByRole("menuitem", { name: "Обновить из расчёта" })).toBeTruthy();
    expect(screen.getAllByRole("menuitem").at(-1)?.textContent).toBe("Удалить проект…");
    fireEvent.click(screen.getByRole("menuitem", { name: "Удалить проект…" }));
    const dialog = screen.getByRole("dialog", { name: "Удалить проект 708К.4" });
    expect(within(dialog).getByText(/Проект уйдёт целиком/)).toBeTruthy();
    // Отмена ничего не удаляет; фокус по умолчанию — на «Отмена».
    expect(document.activeElement?.textContent).toBe("Отмена");
    fireEvent.click(within(dialog).getByRole("button", { name: "Отмена" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(deleted).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Ещё действия с проектом 708К.4" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Удалить проект…" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Удалить проект 708К.4" }));
    await waitFor(() => expect(deleted).toHaveLength(1));
    expect(await screen.findByText("Проект «708К.4» удалён. Снесено строк: спецификация 3, проект 1")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Проекты" })).toBeTruthy();
  });

  it("says honestly that a project is not in ERP and offers the way back instead of a timed redirect", async () => {
    const base = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = decodeURIComponent(String(input));
      if (url === "/api/techcards/kitsets/999К.9") return json({ detail: { message: "Карточка набора «999К.9» не найдена", gone: true } }, 404);
      return base(input, init);
    });
    window.history.replaceState({}, "", "/?section=projects&project=999К.9");
    render(<ProjectsPage principal={editor} />);
    expect(await screen.findByText("Проекта «999К.9» в ERP нет: его удалили или в ссылке другой номер.")).toBeTruthy();
    expect(screen.queryByText(/уже удалена/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "К списку проектов" }));
    expect(await screen.findByText("708К.4")).toBeTruthy();
  });

  it("opens the generator of the first assembly at once when the project has one or two (ПР-10)", async () => {
    const fetchMock = vi.mocked(fetch);
    const base = fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation(async (input, init) => {
      const url = decodeURIComponent(String(input));
      if (url === "/api/techcards/kitsets/708К.4/techcards/КБ1") return json({ card_id: "tc-1" });
      if (url === "/api/techcards/projects/tc-1") return json({
        id: "tc-1", version: 1, project: "708К.4", item_index: "КБ1", product_name: "Жгут питания", source_calculation: "",
        deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
        created_at: "2026-09-29T10:00:00", updated_at: "2026-09-29T10:00:00", issued_at: null,
      });
      return base(input, init);
    });
    render(<ProjectsPage principal={{ ...editor, permissions: { projects: "edit", techcards: "edit", tcgen: "edit" } }} />);
    fireEvent.click(await screen.findByText("708К.4"));
    fireEvent.click(await screen.findByRole("tab", { name: "Генератор" }));
    expect(await screen.findByRole("tab", { name: "КБ1", selected: true })).toBeTruthy();
    expect(screen.queryByText(/Выбери/)).toBeNull();
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
    expect(screen.getAllByText("20")).toHaveLength(2);  // вровень: 2 × 10, без запаса на брак
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
      expect(await screen.findByRole("button", { name: "FRX.26.0000001 · резерв 6 из 20" })).toBeTruthy();
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

    it("lists a КБ already sent with its request number and sends only the rest (прод 180К.11)", async () => {
      const posts: string[] = [];
      serve(card, (url, method) => {
        if (!url.endsWith("/issue")) return undefined;
        if (method === "GET") return json({ project: "708К.4", assemblies: [
          { index: "КБ1", name: "Жгут питания", has_items: true, request: { id: "r1", no: "REQ.26.0000018", status: "Недоукомплектован" } },
          { index: "КБ2", name: "Жгут сигнальный", has_items: true, request: null },
        ] });
        posts.push(url);
        return json({ created: [{ no: "REQ.26.0000030", index: "КБ2", shortfall: [] }], skipped: [], message: "Заявок создано: 1; уже отправлены: КБ1 (REQ.26.0000018)." });
      });
      await openSpec();
      fireEvent.click(screen.getByRole("button", { name: "Отправить на комплектацию" }));

      const list = await screen.findByRole("list", { name: "КБ проекта" });
      expect(within(list).getByText("уже отправлена: REQ.26.0000018 · Недоукомплектован")).toBeTruthy();
      expect(within(list).getByText("уйдёт")).toBeTruthy();
      fireEvent.change(screen.getByLabelText("Дедлайн"), { target: { value: "2026-10-20" } });
      fireEvent.click(screen.getByRole("button", { name: "Отправить КБ2" }));
      await waitFor(() => expect(posts).toHaveLength(1));
    });

    it("sends nothing when every КБ already has its request", async () => {
      serve(card, (url, method) => url.endsWith("/issue") && method === "GET"
        ? json({ project: "708К.4", assemblies: [
          { index: "КБ1", name: "Жгут питания", has_items: true, request: { id: "r1", no: "REQ.26.0000018", status: "Новая" } },
        ] })
        : undefined);
      await openSpec();
      fireEvent.click(screen.getByRole("button", { name: "Отправить на комплектацию" }));

      expect(await screen.findByText(/Все КБ уже отправлены: КБ1 \(REQ\.26\.0000018\)/)).toBeTruthy();
      expect((screen.getByRole("button", { name: "Отправить" }) as HTMLButtonElement).disabled).toBe(true);
      expect(screen.queryByLabelText("Дедлайн")).toBeNull();
    });
  });

  it("shows a piece line's batch in whole pieces while typing", async () => {
    render(<ProjectsPage principal={editor} />);
    fireEvent.click(await screen.findByText("708К.4"));
    fireEvent.click(await screen.findByRole("tab", { name: "Спецификация" }));
    fireEvent.change(screen.getByLabelText("На 1 шт 43025-0400"), { target: { value: "1,5" } });
    // Выдаём вровень (10.10.2026): запас на брак не добавляется — 1,5 × 10 = 15 шт; провод 1,5 × 10 = 15 м.
    expect(screen.getAllByText("15")).toHaveLength(2);
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
          { art: "43025-0400", name: "Корпус Molex", kbs: ["КБ1"], need: "20", unit: "шт", free: "0", off_book: "20", ordered: "0", kitted: "0", short: "0", grn: "", stage: "Закупка · Готово · без ГРН", received: false,
            rows: [{ where: "Закупка", status: "Готово · без ГРН", source_id: "ЗК-2" }],
            state: "stock", state_label: "Со склада", state_tone: "info", state_hint: "Закупка отметила «Есть на складе, без ГРН»: 20" },
          { art: "43030-0007", name: "Контакт", kbs: ["КБ2"], need: "32", unit: "шт", free: "0", off_book: "12", ordered: "18", kitted: "0", short: "2", grn: "", stage: "Закупка · В работе", received: false, rows: [],
            state: "short", state_label: "Не хватает 2", state_tone: "danger", state_hint: "Нужно 32 → не хватает 2" },
        ] });
      }
      return base(input, init);
    });
    render(<ProjectsPage principal={{ ...editor, permissions: { projects: "edit", newrequest: "edit" } }} />);
    fireEvent.click(await screen.findByText("708К.4"));
    fireEvent.click(await screen.findByRole("tab", { name: "Снабжение" }));

    expect(await screen.findByRole("columnheader", { name: /Без ГРН/ })).toBeTruthy();
    expect(screen.getByText("12")).toBeTruthy();
    const stage = screen.getAllByText("Со склада").find((node) => node.className === "tone-info")!;
    // The chain and where to bind the ГРН stay in the tooltip.
    expect(stage.getAttribute("title")).toContain("Цепочка: Закупка · Готово · без ГРН");
    expect(stage.getAttribute("title")).toContain("ЗК-2 → «Привязать ГРН»");
    expect(screen.getAllByText("Не хватает 2").some((node) => node.className === "tone-danger")).toBe(true);
    const fromStock = screen.getAllByText("Со склада").find((node) => node.tagName === "DT")!.parentElement!;
    expect(fromStock.querySelector("dd")!.textContent).toBe("1");
    expect(screen.queryByText("не заведено")).toBeNull();
    expect(screen.queryByLabelText("Заказать 43025-0400")).toBeNull();  // covered without a ГРН: nothing to order
    expect(screen.getByLabelText("Заказать 43030-0007")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Заказать недостающее (1)" })).toBeTruthy();
  });

  it("shows where a name comes from, and «—» with the reason for an unknown article", async () => {
    const base = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (decodeURIComponent(String(input)) === "/api/techcards/kitsets/708К.4/supply") {
        return json({ project: "708К.4", lines: [
          { art: "XHP-4", name: "Корпус JST XH 4 конт.", name_source: "ref", name_hint: "Из справочника ERP", kbs: ["КБ1"], need: "20", unit: "шт", free: "20", ordered: "0", kitted: "0", short: "0", grn: "", stage: "со склада", received: false, rows: [], state: "stock", state_label: "Со склада", state_tone: "info" },
          { art: "QWE-1", name: "", name_source: "", name_hint: "Артикул неизвестен: нет в справочниках ERP, на складе не найдено, сайт elitan.ru не отдал название", kbs: ["КБ1"], need: "1", unit: "шт", free: "0", ordered: "0", kitted: "0", short: "1", grn: "", stage: "не заведено", received: false, rows: [], state: "short", state_label: "Не хватает 1", state_tone: "danger" },
        ] });
      }
      return base(input, init);
    });
    render(<ProjectsPage principal={editor} />);
    fireEvent.click(await screen.findByText("708К.4"));
    fireEvent.click(await screen.findByRole("tab", { name: "Снабжение" }));
    expect((await screen.findByText("Корпус JST XH 4 конт.")).getAttribute("title")).toBe("Из справочника ERP");
    const unknown = screen.getByText("артикул неизвестен").parentElement!;
    expect(unknown.getAttribute("title")).toContain("сайт elitan.ru не отдал название");
    // No stock rights: no «Возможно есть» column and no request for it.
    expect(screen.queryByRole("columnheader", { name: /Возможно есть/ })).toBeNull();
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("/candidates"))).toBe(false);
  });

  it("colours the rows short of stock by «Возможно есть» for whoever may see the warehouse", async () => {
    const base = vi.mocked(fetch).getMockImplementation()!;
    const asked: unknown[] = [];
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = decodeURIComponent(String(input));
      if (url === "/api/techcards/kitsets/708К.4/supply") {
        return json({ project: "708К.4", lines: [
          { art: "SXH-001T-P0.6", name: "Контакт обжимной JST XH гнездо", kbs: ["КБ1"], need: "80", unit: "шт", free: "0", ordered: "0", kitted: "0", short: "80", grn: "", stage: "не заведено", received: false, rows: [], state: "short", state_label: "Не хватает 80", state_tone: "danger" },
          { art: "QWE-1", name: "", kbs: ["КБ1"], need: "1", unit: "шт", free: "0", ordered: "0", kitted: "0", short: "1", grn: "", stage: "не заведено", received: false, rows: [], state: "short", state_label: "Не хватает 1", state_tone: "danger" },
          { art: "LP-12-C04PE-02-001", name: "Разъём CNLINKO LP-12 вилка 4 конт.", kbs: ["КБ1"], need: "20", unit: "шт", free: "25", ordered: "0", kitted: "0", short: "0", grn: "", stage: "со склада", received: false, rows: [], state: "stock", state_label: "Со склада", state_tone: "info" },
        ] });
      }
      if (url === "/api/issues/projects/708К.4/candidates") {
        asked.push(JSON.parse(String(init?.body)));
        return json({ positions: [
          { article: "SXH-001T-P0.6", name: "Контакт обжимной JST XH гнездо", unit: "шт", more: 0, candidates: [
            { id: "l1", lot_id: "l1", warehouse: "ЦАР", grn: "OLD.26.0000002", package: "", article: "SXH001TP06", name: "Контакт SXH 0,6", unit: "шт", storage_location: "1F4", available: "40", kind: "fuzzy", match: "fuzzy-separators" },
          ] },
          { article: "QWE-1", name: "", unit: "шт", more: 0, candidates: [] },
        ] });
      }
      return base(input, init);
    });
    render(<ProjectsPage principal={{ ...editor, permissions: { projects: "view", warehouse: "view" } }} />);
    fireEvent.click(await screen.findByText("708К.4"));
    fireEvent.click(await screen.findByRole("tab", { name: "Снабжение" }));

    expect(await screen.findByText("ГРН OLD.26.0000002 · 40 шт")).toBeTruthy();
    expect(screen.getByText("похожий артикул · ЦАР")).toBeTruthy();
    // Only the positions short of stock are asked about, with the names the list shows.
    expect(asked).toEqual([{ positions: [
      { article: "SXH-001T-P0.6", name: "Контакт обжимной JST XH гнездо", unit: "шт" }, { article: "QWE-1", name: "", unit: "шт" },
    ] }]);
    expect(screen.getByText("ГРН OLD.26.0000002 · 40 шт").closest("tr")!.className).toContain("cand-maybe");
    expect(screen.getByText("не найдено").closest("tr")!.className).toContain("cand-none");
    expect(screen.getByText("Разъём CNLINKO LP-12 вилка 4 конт.").closest("tr")!.className).not.toContain("cand-");
    expect(screen.getByLabelText("Цвета строк").textContent).toContain("возможно есть: похожий артикул или не хватает");
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
    expect(screen.getByText("Укажите дедлайн.")).toBeTruthy();
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
    expect(screen.queryByRole("tab", { name: "Чертёж КБ1" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Создать в редакторе" }));
    // Редактор открывается вкладкой карточки, а не уходит на отдельный экран.
    expect(await screen.findByRole("region", { name: "Редактор 708К.4 КБ1" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Чертёж КБ1", selected: true })).toBeTruthy();
    expect(window.location.search).not.toContain("harness=");
    fireEvent.click(screen.getByRole("button", { name: "← Назад" }));
    expect(await screen.findByRole("button", { name: "Создать в редакторе" })).toBeTruthy();
    view.unmount();

    window.history.replaceState({}, "", "/?section=projects");
    current = { ...card, assemblies: [{ ...card.assemblies[0], kd: [drawing] }] };
    render(<ProjectsPage principal={editor} />);
    fireEvent.click(await screen.findByText("708К.4"));
    const open = await screen.findByRole("button", { name: "Чертёж жгута" });
    expect(screen.getByRole("tab", { name: "Чертёж КБ1" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Создать в редакторе" })).toBeNull();
    expect(screen.queryByRole("link", { name: /harness/ })).toBeNull(); // не файл для скачивания
    expect(screen.getByRole("button", { name: "＋ файл" })).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Генератор" }));
    fireEvent.click(await screen.findByRole("tab", { name: "КБ1" }));
    expect((await screen.findAllByRole("button", { name: "Чертёж жгута" })).length).toBeGreaterThan(0);
    expect(open).toBeTruthy();
  });

  it("чертёж жгута: «Отвязать» оставляет его в КД, «Вернуть» привязывает обратно; файл КД так не удаляется", async () => {
    const drawing = { id: "h1", index: "КБ1", name: "708К.4КБ1 — Чертёж жгута", size: 900, stored: true, url: null, uploaded_at: "2026-10-06T14:30:00", who: "Технолог", kind: "harness" as const };
    const base = vi.mocked(fetch).getMockImplementation()!;
    let kd: unknown[] = [drawing];
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = decodeURIComponent(String(input));
      const method = init?.method || "GET";
      if (url === "/api/techcards/kitsets/708К.4" && method === "GET") return json({ ...card, assemblies: [{ ...card.assemblies[0], kd }] });
      if (url.endsWith("/harness/КБ1/unlink")) { kd = [{ ...drawing, kind: "harness_unlinked" }]; return json({ document: kd[0] }); }
      if (url.endsWith("/harness/КБ1/restore")) { kd = [drawing]; return json({ document: drawing, design: {}, version: "v1", warnings: [] }); }
      return base(input, init);
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<ProjectsPage principal={editor} />);
    fireEvent.click(await screen.findByText("708К.4"));
    fireEvent.click(await screen.findByRole("button", { name: "Отвязать" }));
    expect(confirm).toHaveBeenCalledWith("Отвязать чертёж жгута от КБ1? Он останется в КД отвязанным — вернуть можно кнопкой «Вернуть».");
    expect(await screen.findByText("Отвязанный чертёж жгута · сохранён 06.10.2026 14:30")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Чертёж жгута" })).toBeNull();
    expect(screen.queryByTitle("Отвязать")).toBeNull(); // не файл КД с «✕»: удалить его нельзя
    expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Вернуть" }));
    expect(await screen.findByRole("button", { name: "Чертёж жгута" })).toBeTruthy();
    const restore = vi.mocked(fetch).mock.calls.find(([url]) => decodeURIComponent(String(url)).endsWith("/restore"))!;
    expect(JSON.parse(String(restore[1]!.body))).toEqual({ document_id: "h1" });
  });

  it("отвязанный чертёж жгута: «Удалить» с подтверждением; у привязанного кнопки нет", async () => {
    const drawing = { id: "h1", index: "КБ1", name: "Чертёж жгута", size: 900, stored: true, url: null, uploaded_at: "2026-10-06T14:30:00", who: "Технолог", kind: "harness" as const };
    const base = vi.mocked(fetch).getMockImplementation()!;
    let kd: unknown[] = [drawing, { ...drawing, id: "h0", kind: "harness_unlinked" }];
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = decodeURIComponent(String(input));
      const method = init?.method || "GET";
      if (url === "/api/techcards/kitsets/708К.4" && method === "GET") return json({ ...card, assemblies: [{ ...card.assemblies[0], kd }] });
      if (url.endsWith("/harness/КБ1/unlinked/h0") && method === "DELETE") { kd = [drawing]; return json({ res: { deleted: 1, revisions: 2 } }); }
      return base(input, init);
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValue(true);
    render(<ProjectsPage principal={editor} />);
    fireEvent.click(await screen.findByText("708К.4"));
    const remove = await screen.findByRole("button", { name: "Удалить" });
    expect(screen.getAllByRole("button", { name: "Удалить" })).toHaveLength(1); // только у отвязанного
    fireEvent.click(remove);
    expect(confirm).toHaveBeenCalledWith("Удалить отвязанный чертёж жгута (сохранён 06.10.2026 14:30)? Вернуть его будет нельзя.");
    expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false); // отказались
    fireEvent.click(remove);
    await waitFor(() => expect(screen.queryByText(/Отвязанный чертёж жгута · сохранён/)).toBeNull());
    expect(screen.queryByRole("button", { name: "Удалить" })).toBeNull();
    expect(screen.getByRole("button", { name: "Чертёж жгута" })).toBeTruthy();
  });

  it("чертёж жгута: без права правки нет «Отвязать» и «Вернуть»", async () => {
    const drawing = { id: "h1", index: "КБ1", name: "Чертёж жгута", size: 900, stored: true, url: null, uploaded_at: "2026-10-06T14:30:00", who: "Технолог", kind: "harness" as const };
    const base = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = decodeURIComponent(String(input));
      if (url === "/api/techcards/kitsets/708К.4" && (init?.method || "GET") === "GET")
        return json({ ...card, assemblies: [{ ...card.assemblies[0], kd: [drawing, { ...drawing, id: "h0", kind: "harness_unlinked" }] }] });
      return base(input, init);
    });
    render(<ProjectsPage principal={viewer} />);
    fireEvent.click(await screen.findByText("708К.4"));
    expect(await screen.findByRole("button", { name: "Чертёж жгута" })).toBeTruthy();
    expect(screen.getByText("Отвязанный чертёж жгута · сохранён 06.10.2026 14:30")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Отвязать" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Вернуть" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Удалить" })).toBeNull();
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
    // Выдаём вровень (10.10.2026): «% брака» хранится, но к «На партию» не добавляется.
    expect(kitBatch("1,5", "10", "5", "м")).toBe(15);
    expect(kitBatch("1,5", "3", "5", "м")).toBe(4.5);
    expect(kitBatch("", "10", "5", "м")).toBe(0);
    // Штучное — вверх до целого (половинки штуки не бывает): 4,5 → 5; целое остаётся целым.
    expect(kitBatch("1,5", "3", "5", "шт")).toBe(5);
    expect(kitBatch("0.333", "3", "5", "шт")).toBe(1);  // 0,999 → 1
    expect(kitBatch("2", "10", "5", "шт")).toBe(20);
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
