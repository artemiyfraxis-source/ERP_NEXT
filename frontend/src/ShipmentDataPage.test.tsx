import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ShipmentDataPage, dataGaps, dataRows, sentNote } from "./ShipmentDataPage";
import { forgetPersonalViews } from "./personalViews";
import { openShippingForms } from "./shippingPrint";
import { setRightsPrincipal } from "./rights";
vi.mock("./shippingPrint", () => ({ openShippingForms: vi.fn() }));

vi.mock("./ScanAgent", () => ({ useScanAgent: (start: (id?: string) => Promise<unknown>) => ({ begin: async () => start(), dialogs: () => null }), ScanDeviceLine: () => null }));

const principal = {
  user: { id: "u2", email: "ladutko@example.test", name: "Ульяна Ладутько", department: "Продажи" },
  roles: ["pm"], access_level: "pm", permissions: { shipping: "edit" as const },
};

const base = {
  id: "s1", number: "ОТГ-20261028-01", version: 4, created_at: "2026-10-28T09:00:00", status: "Упакована",
  customer: "ООО «ТСР»", receiver: "ООО «ТСР»", shipping_point: "", contact: "", phone: "", terms: null, carrier: "",
  deadline: "2026-11-02", upd_type: null, upd_number: "", upd_date: null, upd_url: "", pl_number: "PL713К.4-1", dn_number: "DN713К.4-1",
  track_number: "", packed_at: "2026-10-28", shipped_at: null, manager: "Менеджер", note: "", tk_note: "", places: 1, weight: "", boxes: [],
  inherited: [] as string[],
  missing_data: ["условия доставки", "телефон получателя", "тип УПД"], missing_ship: ["условия доставки", "телефон получателя", "тип УПД"],
  lines: [{ id: "l1", good_id: "g1", project: "713К.4", item_index: "КБ2", article: "", name: "Жгут", requested: "500", picked: "500", shortage: "0", unit: "шт", grn: "ГП.26.0000001" }],
};
const complete = { ...base, id: "s2", number: "ОТГ-20261028-02", terms: "САМОВЫВОЗ", carrier: "Самовывоз", phone: "+7 900", upd_type: "ЭДО", upd_number: "5", missing_data: [], missing_ship: [], ready_to_ship: true };
const shippedNoFile = { ...complete, id: "s3", number: "ОТГ-20261020-03", status: "Отгружена", shipped_at: "2026-10-25", upd_type: "БУМАГА", upd_number: "7", missing_data: ["файл УПД"], missing_ship: ["файл УПД"] };
// Уехала до новых правил: телефона нет, но УПД на месте — в списке менеджера её нет.
const shippedOld = { ...complete, id: "s4", number: "ОТГ-20261001-04", status: "Отгружена", shipped_at: "2026-10-05", missing_data: ["телефон получателя"], missing_ship: ["телефон получателя"] };
const working = { ...base, id: "s6", number: "ОТГ-20261027-06", status: "Новая" };
const cancelled = { ...complete, id: "s7", number: "ОТГ-20261001-07", status: "Отменена" };
const closed = { ...complete, id: "s5", number: "ОТГ-20260901-05", status: "Закрыта" };

// Строки приёмки: 713К.4 в отправках (ОТК); 800К.1 без отправки, В работе; 900К.2 уехала целиком; 810К.1 новый; 820К.3 «ТК готова» (старое слово).
const take = (project: string, project_stage: string, needed = "10", sent = "0") => {
  const accepted = ["Принято складом", "Упаковано", "Отгружено"].includes(project_stage) ? needed : "0";
  const packed = ["Упаковано", "Отгружено"].includes(project_stage) ? needed : "0";
  return { project, customer: `Заказчик ${project}`, deadline: null, item_index: "КБ1", name: "Жгут", unit: "шт",
    stage: project_stage, project_stage, needed, launched: "0", delivered: "0", accepted, packed, sent,
    remaining: String(Number(needed) - Number(accepted)), good_id: null, good_version: null, grn: null, location: "", tasks: [] };
};
const INTAKE = [{ ...take("713К.4", "ОТК", "500"), item_index: "КБ2", accepted: "500", packed: "500", remaining: "0" }, take("800К.1", "В работе"), take("900К.2", "Отгружено", "10", "10"), take("810К.1", "Новый"), take("820К.3", "ТК готова")];
const UPD_JOB = { id: "j1", status: "done", error: "", recognising: false, scan_file: "/api/stock/receipts/files/f1", result: {
  scan_file: "/api/stock/receipts/files/f1", lines: [], note: "",
  recognised: { supplier: "", inn: "", upd_number: "РЭ-0915", upd_date: "2026-09-15", invoice_number: "РЭ-777", invoice_date: "2026-09-08" } } };
const APPLIED = { ...base, version: 6, upd_type: "ЭДО", upd_number: "РЭ-0915", upd_date: "2026-09-15", upd_url: "/api/shipping/files/f9" };

function json(body: unknown) { return new Response(JSON.stringify(body)); }
function stub(shipments: unknown[]) {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url === "/api/shipping/shipments" && method === "GET") return json({ shipments });
    if (url === "/api/shipping/intake" && method === "GET") return json({ rows: INTAKE });
    if (url.startsWith("/api/shipping/project-addressees")) return json({ addressees: new URL(url, "http://localhost").searchParams.getAll("project").map((project) => ({ project, version: 0, pack_decided: false, dispatch_gaps: ["получатель", "адрес доставки", "телефон", "вариант УПД"] })) });
    if (url.endsWith("/upd-scan/file")) return json(UPD_JOB);
    if (url.endsWith("/upd-scan/apply")) return json({ status: "applied", shipment: { ...APPLIED, version: JSON.parse(String(init?.body)).version + 1 }, invoice: "РЭ-777" });
    if (/\/head$/.test(url) || /\/documents$/.test(url)) return json({ shipment: { ...base, version: JSON.parse(String(init?.body)).version + 1 } });
    return json({});
  }));
}
const bodies = (suffix: string) => vi.mocked(fetch).mock.calls.filter(([target]) => String(target).endsWith(suffix)).map(([, init]) => JSON.parse(String(init?.body)));

describe("ShipmentDataPage («Данные отправок»)", () => {
  beforeEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); forgetPersonalViews(); });

  const acceptedCard = { project: "780К.1", version: 7, pack_decided: false, dispatch_gaps: ["телефон", "вариант УПД"] };
  function projectStub(card = acceptedCard, status = 200, stage = "Принято складом") {
    stub([]);
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    let current = card;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === "/api/shipping/intake") return json({ rows: [take("780К.1", stage)] });
      if (url.startsWith("/api/shipping/project-addressees")) return status === 200 ? json({ addressees: [current] }) : new Response(JSON.stringify({ detail: "Нет связи с карточками" }), { status });
      if (url.startsWith("/api/shipping/project-addressee?")) return json({ addressee: current });
      if (url === "/api/shipping/project-pack-decision") {
        current = { ...current, version: current.version + 1, pack_decided: JSON.parse(String(init?.body)).decided };
        return json({ addressee: current });
      }
      return fallback(input, init);
    });
  }

  it.each(["ОТК", "Принято складом"])("%s: a missing stored card uses the server's virtual defaults and version zero", async (stage) => {
    projectStub({ ...acceptedCard, version: 0, dispatch_gaps: ["получатель", "адрес доставки", "телефон", "вариант УПД"] }, 200, stage);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByRole("button", { name: stage === "ОТК" ? /^Готово/ : /^Принято/ }));
    expect(await screen.findByText("Не заполнено: получатель, адрес доставки, телефон, вариант УПД")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("checkbox", { name: "Упаковывать 780К.1" }));
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Упаковывать 780К.1" })).toBeChecked());
    expect(bodies("/project-pack-decision")).toEqual([{ project: "780К.1", version: 0, decided: true }]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it.each(["table", "cards"])("%s: exposes server gaps and one version-aware packaging decision without opening a dialog", async (view) => {
    window.localStorage.setItem("shipment-data.view", view);
    projectStub(); render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByRole("button", { name: /^Принято/ }));
    expect(await screen.findByText("Не заполнено: телефон, вариант УПД")).toBeInTheDocument();
    const toggle = await screen.findByRole("checkbox", { name: "Упаковывать 780К.1" });
    expect(toggle).toBeEnabled();
    if (view === "cards") expect(toggle.closest("button")).toBeNull();
    fireEvent.click(toggle);
    await waitFor(() => expect(toggle).toBeChecked());
    expect(bodies("/project-pack-decision")).toEqual([{ project: "780К.1", version: 7, decided: true }]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).startsWith("/api/shipping/project-addressee?"))).toHaveLength(0);
  });

  it.each(["table", "cards"])("%s: read-only and warehouse-only viewers see the confirmed decision without manager privilege", async (view) => {
    for (const permissions of [{ shipping: "view" as const, shipping_goods: "view" as const }, { shipping: "view" as const, shipping_goods: "edit" as const }]) {
      window.localStorage.setItem("shipment-data.view", view);
      projectStub({ ...acceptedCard, pack_decided: true });
      const page = render(<ShipmentDataPage principal={{ ...principal, permissions }} />);
      fireEvent.click(await screen.findByRole("button", { name: /^Принято/ }));
      const toggle = await screen.findByRole("checkbox", { name: "Упаковывать 780К.1" });
      expect(toggle).toBeChecked(); expect(toggle).toBeDisabled();
      expect(bodies("/project-pack-decision")).toHaveLength(0);
      page.unmount();
    }
  });

  it("keeps missing-data and unresolved decisions in the filter; a confirmed decision removes a complete project", async () => {
    projectStub({ ...acceptedCard, dispatch_gaps: [] }); render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByRole("button", { name: /^Принято/ }));
    expect(await screen.findByText("Данные получателя и УПД заполнены")).toBeInTheDocument();
    expect(screen.queryByText("Готова к отправке")).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Только с недостающими данными"));
    fireEvent.click(await screen.findByRole("checkbox", { name: "Упаковывать 780К.1" }));
    await waitFor(() => expect(screen.queryByRole("checkbox", { name: "Упаковывать 780К.1" })).not.toBeInTheDocument());
    fireEvent.click(screen.getByLabelText("Только с недостающими данными"));
    expect(await screen.findByRole("checkbox", { name: "Упаковывать 780К.1" })).toBeChecked();
  });

  it.each(["table", "cards"])("%s: modal decisions refresh the list and gaps filter in both directions without losing typed fields", async (view) => {
    for (const initial of [false, true]) {
      window.localStorage.setItem("shipment-data.view", view);
      projectStub({ ...acceptedCard, pack_decided: initial, dispatch_gaps: [] });
      const page = render(<ShipmentDataPage principal={principal} />);
      fireEvent.click(await screen.findByRole("button", { name: /^Принято/ }));
      if (view === "cards") fireEvent.click(await screen.findByRole("button", { name: "Открыть: 780К.1" }));
      else fireEvent.click(await screen.findByText("780К.1", { selector: "strong" }));
      const dialog = within(await screen.findByRole("dialog"));
      const toggle = await dialog.findByRole("checkbox", { name: "Упаковывать 780К.1" });
      if (initial) fireEvent.click(screen.getByLabelText("Только с недостающими данными"));
      fireEvent.change(dialog.getByLabelText("Телефон"), { target: { value: "+7 набрано" } });
      fireEvent.click(toggle);
      await waitFor(() => expect(toggle).toHaveProperty("checked", !initial));
      expect(dialog.getByLabelText("Телефон")).toHaveValue("+7 набрано");
      await waitFor(() => expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).startsWith("/api/shipping/project-addressees"))).toHaveLength(2));
      fireEvent.click(dialog.getByRole("button", { name: "Закрыть" }));
      if (initial) expect(await screen.findByRole("checkbox", { name: "Упаковывать 780К.1" })).not.toBeChecked();
      else {
        expect(await screen.findByRole("checkbox", { name: "Упаковывать 780К.1" })).toBeChecked();
        fireEvent.click(screen.getByLabelText("Только с недостающими данными"));
        await waitFor(() => expect(screen.queryByRole("checkbox", { name: "Упаковывать 780К.1" })).not.toBeInTheDocument());
      }
      expect(bodies("/project-pack-decision")).toEqual([{ project: "780К.1", version: 7, decided: !initial }]);
      page.unmount(); forgetPersonalViews();
    }
  });

  it("bulk card failures remain unknown and included in the gaps filter, never false-ready or false-undecided", async () => {
    projectStub(acceptedCard, 503); render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByRole("button", { name: /^Принято/ }));
    expect(await screen.findByText("Данные проекта не загружены")).toBeInTheDocument();
    expect(screen.getByText("Упаковывать — решение не загружено")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Упаковывать 780К.1" })).not.toBeInTheDocument();
    expect(screen.queryByText(/склад ждёт решения|Данные получателя и УПД заполнены/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Только с недостающими данными"));
    expect(screen.getByText("780К.1", { selector: "strong" })).toBeInTheDocument();
  });

  it("409 reloads the server decision and never presents the rejected toggle as confirmed", async () => {
    projectStub();
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    let conflict = false;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      if (String(input) === "/api/shipping/project-pack-decision") { conflict = true; return new Response(JSON.stringify({ detail: "Версия изменилась" }), { status: 409 }); }
      if (conflict && String(input).startsWith("/api/shipping/project-addressees")) return json({ addressees: [{ ...acceptedCard, version: 9 }] });
      return fallback(input, init);
    });
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByRole("button", { name: /^Принято/ }));
    fireEvent.click(await screen.findByRole("checkbox", { name: "Упаковывать 780К.1" }));
    expect(await screen.findByText(/Данные проекта уже изменил другой сотрудник/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "Упаковывать 780К.1" })).toBeEnabled());
    expect(screen.getByRole("checkbox", { name: "Упаковывать 780К.1" })).not.toBeChecked();
    expect(bodies("/project-pack-decision")).toHaveLength(1);
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).startsWith("/api/shipping/project-addressees"))).toHaveLength(2);
  });

  it("renders twelve shipment positions as twelve rows under one project header", async () => {
    const lines = Array.from({ length: 12 }, (_, index) => ({ ...base.lines[0], id: `line${index}`, item_index: `КБ${index + 1}`, name: `Жгут сборка ${index + 1}`, grn: `ГП.26.${index + 1}` }));
    stub([{ ...base, lines }]);
    render(<ShipmentDataPage principal={principal} />);
    const table = await screen.findByRole("table");
    for (const line of lines) {
      const row = within(table).getByText(line.name).closest("tr")!;
      expect(row).toHaveTextContent(line.item_index);
      expect(row).toHaveTextContent(line.grn);
      expect(row).toHaveTextContent("500");
    }
    expect(table.querySelectorAll("tbody tr:not(.group-row)")).toHaveLength(12);
    expect(table.querySelectorAll(".group-row")).toHaveLength(1);
    expect(within(table).getAllByText(base.number)).toHaveLength(12);
  });

  it("preserves each shipment line and free partial slices without adding picked quantities twice", () => {
    const intake = { ...take("713К.4", "Новый", "100"), item_index: "КБ2", stage: "В работе", accepted: "60", packed: "50", remaining: "40" };
    const first = { ...base, lines: [{ ...base.lines[0], picked: "20" }] };
    const second = { ...closed, lines: [{ ...base.lines[0], picked: "30" }] };
    const rows = dataRows([first, second] as never, [intake] as never);
    expect(rows.map((row) => row.id)).toEqual(expect.arrayContaining(["s1:l1", "s5:l1"]));
    expect(rows.filter((row) => !row.shipment).map((row) => [row.stage, row.quantity])).toEqual([["production", 40], ["accepted", 10]]);
    expect(rows.filter((row) => row.shipment).map((row) => row.quantity).sort()).toEqual([20, 30]);
    const unknown = { ...take("Новый проект", "Новый", "0"), stage: "Неизвестный этап" };
    expect(dataRows([], [unknown] as never)).toHaveLength(1);
  });

  it.each([
    ["В ожидании", "Новый"], ["В производстве", "В работе"], ["Готово", "ОТК"], ["Принято", "Принято складом"],
    ["Упаковано", "Упакована"], ["На отгрузку", "Упакована"], ["Отгружено", "Закрыта"],
  ])("%s counts twelve KB positions and renders one project group", async (label, status) => {
    const actual = ["Упаковано", "На отгрузку", "Отгружено"].includes(label);
    const lines = Array.from({ length: 12 }, (_, index) => ({ ...base.lines[0], id: `twelve-${index}`, item_index: `КБ${index + 1}`, name: `Сборка позиции ${index + 1}` }));
    const sources = lines.map((line) => ({ ...take("713К.4", status, "500"), item_index: line.item_index, name: line.name,
      stage: actual ? "Принято складом" : status, project_stage: "Новый", accepted: actual || label === "Принято" ? "500" : "0",
      packed: actual ? "500" : "0", remaining: actual || label === "Принято" ? "0" : "500" }));
    stub(actual ? [{ ...base, status, ready_to_ship: label === "На отгрузку", lines }] : []);
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation((input, init) => String(input) === "/api/shipping/intake" ? Promise.resolve(json({ rows: sources })) : fallback(input, init));
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByRole("button", { name: `${label} · 12` }));
    const table = await screen.findByRole("table");
    expect(table.querySelectorAll("tbody tr:not(.group-row)")).toHaveLength(12);
    expect(table.querySelectorAll(".group-row")).toHaveLength(1);
    for (const line of lines) expect(within(table).getByText(line.name).closest("tr")).toHaveTextContent(line.item_index);
    if (label === "Принято" || label === "Готово") expect(screen.getAllByRole("checkbox", { name: "Упаковывать 713К.4" })).toHaveLength(1);
    if (!actual) expect(within(table).getAllByText("Не создана")).toHaveLength(12);
    expect(screen.getByText("Счётчики: позиции КБ")).toBeInTheDocument();
  });

  it.each(["table", "cards"])("%s keeps exact document IDs and UPD file links for two shipments of the same KB", async (view) => {
    window.localStorage.setItem("shipment-data.view", view);
    const one = { ...complete, id: "exact-one", number: "ОТГ-1", ready_to_ship: false, pl_number: "PL-1", dn_number: "DN-1", upd_url: "/file-one.pdf" };
    const two = { ...complete, id: "exact-two", number: "ОТГ-2", ready_to_ship: false, pl_number: "PL-2", dn_number: "DN-2", upd_url: "/file-two.pdf" };
    stub([one, two]); render(<ShipmentDataPage principal={principal} />);
    const row = (await screen.findByText("ОТГ-2")).closest<HTMLElement>(view === "cards" ? ".data-card" : "tr")!;
    const content = within(row);
    fireEvent.click(content.getByRole("button", { name: "Печать ПЛ PL-2" }));
    fireEvent.click(content.getByRole("button", { name: "Печать DN DN-2" }));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).startsWith("/api/shipping/shipments/exact-two/print?")).map(([url]) => String(url))).toEqual([
      "/api/shipping/shipments/exact-two/print?document=pl", "/api/shipping/shipments/exact-two/print?document=dn",
    ]));
    expect(content.getByRole("link", { name: "файл" })).toHaveAttribute("href", "/file-two.pdf");
    fireEvent.click(content.getByRole("link", { name: "файл" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(view === "cards" ? content.getByRole("button", { name: "Открыть: ОТГ-2" }) : content.getByText("Жгут"));
    expect(await screen.findByRole("dialog", { name: /ОТГ-2/ })).toBeInTheDocument();
    expect(bodies("/head")).toHaveLength(0); expect(bodies("/documents")).toHaveLength(0);
  });

  it("groups twelve free KB cards under one project decision outside every card button", async () => {
    window.localStorage.setItem("shipment-data.view", "cards");
    projectStub();
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation((input, init) => String(input) === "/api/shipping/intake"
      ? Promise.resolve(json({ rows: Array.from({ length: 12 }, (_, index) => ({ ...take("780К.1", "Принято складом"), item_index: `КБ${index + 1}`, name: `Жгут ${index + 1}` })) })) : fallback(input, init));
    const page = render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByRole("button", { name: "Принято · 12" }));
    expect(page.container.querySelectorAll(".data-card")).toHaveLength(12);
    expect(page.container.querySelectorAll(".dt-card-group-title")).toHaveLength(1);
    const decision = screen.getByRole("checkbox", { name: "Упаковывать 780К.1" });
    expect(decision.closest("button")).toBeNull();
    fireEvent.click(decision);
    await waitFor(() => expect(decision).toBeChecked());
    expect(bodies("/project-pack-decision")).toEqual([{ project: "780К.1", version: 7, decided: true }]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it.each(["table", "cards"])("%s opens the clicked free slice and the group's accepted context instead of its first packed row", async (view) => {
    window.localStorage.setItem("shipment-data.view", view);
    projectStub();
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    const source = { ...take("780К.1", "Новый", "70"), stage: "В работе", name: "Смешанная позиция", accepted: "30", packed: "20", remaining: "40" };
    const packed = { ...base, lines: [{ ...base.lines[0], project: "780К.1", item_index: "КБ1", picked: "20", name: "Упакованная часть" }] };
    vi.mocked(fetch).mockImplementation((input, init) => String(input) === "/api/shipping/intake" ? Promise.resolve(json({ rows: [source] }))
      : String(input) === "/api/shipping/shipments" ? Promise.resolve(json({ shipments: [packed] })) : fallback(input, init));
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByRole("button", { name: "Принято · 1" }));
    fireEvent.click(view === "cards" ? screen.getByRole("button", { name: "Открыть: 780К.1" }) : screen.getByText("Смешанная позиция"));
    let dialog = within(await screen.findByRole("dialog"));
    expect(await dialog.findByRole("checkbox", { name: "Упаковывать 780К.1" })).toBeInTheDocument();
    expect(dialog.getByRole("combobox", { name: "УПД" })).toBeInTheDocument();
    fireEvent.click(dialog.getByRole("button", { name: "Закрыть" }));
    fireEvent.click(screen.getByRole("button", { name: "В производстве · 1" }));
    fireEvent.click(view === "cards" ? screen.getByRole("button", { name: "Открыть: 780К.1" }) : screen.getByText("Смешанная позиция"));
    dialog = within(await screen.findByRole("dialog"));
    await dialog.findByRole("textbox", { name: "Телефон" });
    expect(dialog.queryByRole("checkbox", { name: "Упаковывать 780К.1" })).not.toBeInTheDocument();
    expect(dialog.queryByRole("combobox", { name: "УПД" })).not.toBeInTheDocument();
    fireEvent.click(dialog.getByRole("button", { name: "Закрыть" }));
    fireEvent.click(screen.getByRole("button", { name: "Все · 3" }));
    fireEvent.click(screen.getByRole("button", { name: "780К.1 · Заказчик 780К.1" }));
    dialog = within(await screen.findByRole("dialog"));
    expect(await dialog.findByRole("checkbox", { name: "Упаковывать 780К.1" })).toBeInTheDocument();
    expect(dialog.getByRole("combobox", { name: "УПД" })).toBeInTheDocument();
  });

  it.each(["table", "cards"])("%s preserves the clicked line stage within a mixed new shipment", async (view) => {
    window.localStorage.setItem("shipment-data.view", view);
    projectStub();
    const fallback = vi.mocked(fetch).getMockImplementation()!;
    const first = { ...take("780К.1", "Новый", "20"), stage: "В работе", accepted: "20", packed: "20", remaining: "0" };
    const second = { ...take("780К.1", "Новый", "10"), item_index: "КБ2", stage: "Принято складом", accepted: "10", packed: "10", remaining: "0" };
    const mixed = { ...working, lines: [first, second].map((source, index) => ({ ...base.lines[0], id: `mixed-${index}`, project: "780К.1", item_index: source.item_index, name: `Часть ${index + 1}`, picked: source.packed })) };
    vi.mocked(fetch).mockImplementation((input, init) => String(input) === "/api/shipping/intake" ? Promise.resolve(json({ rows: [first, second] }))
      : String(input) === "/api/shipping/shipments" ? Promise.resolve(json({ shipments: [mixed] })) : fallback(input, init));
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByRole("button", { name: "Принято · 1" }));
    fireEvent.click(view === "cards" ? screen.getByRole("button", { name: `Открыть: ${mixed.number}` }) : screen.getByText("Часть 2"));
    const dialog = within(await screen.findByRole("dialog", { name: new RegExp(mixed.number) }));
    expect(await dialog.findByRole("checkbox", { name: "Упаковывать 780К.1" })).toBeInTheDocument();
    expect(dialog.getByRole("combobox", { name: "Тип УПД" })).toBeInTheDocument();
  });

  it("explains blocked DN and isolates the old oversized column preferences", async () => {
    window.localStorage.setItem("erp-table:shipment-data-stages", JSON.stringify({ widths: { project: 1000 } }));
    stub([base]); render(<ShipmentDataPage principal={principal} />);
    const dn = await screen.findByRole("button", { name: `Печать DN ${base.dn_number}` });
    expect(dn).toBeDisabled(); expect(dn.parentElement).toHaveAttribute("title", `Не хватает: ${base.missing_data.join(", ")}`);
    const table = screen.getByRole("table");
    expect(within(table).queryByRole("columnheader", { name: "Срок" })).not.toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "КБ" }).style.width).not.toBe("1000px");
    await waitFor(() => expect(window.localStorage.getItem("erp-table:shipment-data-positions-v2")).not.toBeNull());
    expect(window.localStorage.getItem("erp-table:shipment-data-stages")).toContain("1000");
    expect(window.localStorage.getItem("shipment-data.view")).toBeNull();
  });

  it("prints the PL directly by shipment id without opening or modifying the shipment", async () => {
    stub([base]);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByRole("button", { name: `Печать ПЛ ${base.pl_number}` }));
    await waitFor(() => expect(openShippingForms).toHaveBeenCalled());
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url) === `/api/shipping/shipments/${base.id}/print?document=pl`)).toBe(true);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method && init.method !== "GET")).toHaveLength(0);
  });

  it("leaves PL text plain when printing is forbidden", async () => {
    setRightsPrincipal({ ...principal, permissions: { shipping: "edit", print: "view" } });
    try {
      stub([base]); render(<ShipmentDataPage principal={principal} />);
      await screen.findByRole("table");
      expect(screen.queryByRole("button", { name: `Печать ПЛ ${base.pl_number}` })).not.toBeInTheDocument();
    } finally { setRightsPrincipal(null); }
  });

  it("has no PL print action before a PL exists and reports print errors without changing the shipment", async () => {
    stub([{ ...base, pl_number: "" }]);
    const view = render(<ShipmentDataPage principal={principal} />);
    await screen.findByRole("table");
    expect(screen.queryByRole("button", { name: /^Печать ПЛ/ })).not.toBeInTheDocument();
    view.unmount();
    stub([base]);
    const original = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation((input, init) => String(input).includes("/print?")
      ? Promise.resolve(new Response(JSON.stringify({ detail: "Печать временно недоступна" }), { status: 503 })) : original(input, init));
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByRole("button", { name: `Печать ПЛ ${base.pl_number}` }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Печать временно недоступна");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method && init.method !== "GET")).toHaveLength(0);
  });

  it("prints PL explicitly from the shipment window without saving data", async () => {
    stub([base]); render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByText(base.number));
    const form = within(screen.getByRole("dialog"));
    fireEvent.click(form.getByRole("button", { name: `Печать ПЛ · ${base.pl_number}` }));
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url) === `/api/shipping/shipments/${base.id}/print?document=pl`)).toBe(true));
    expect(bodies("/head")).toHaveLength(0); expect(bodies("/documents")).toHaveLength(0);
  });

  it("keeps internal shipment numbers searchable after making project and assembly primary", async () => {
    stub([base]); render(<ShipmentDataPage principal={principal} />);
    const search = await screen.findByRole("searchbox", { name: "Поиск: Данные отправок" });
    fireEvent.change(search, { target: { value: base.number } });
    expect(await screen.findByText(base.number)).toBeInTheDocument();
    fireEvent.change(search, { target: { value: "does not exist" } });
    await waitFor(() => expect(screen.queryByText(base.number)).not.toBeInTheDocument());
  });

  it("splits the list into seven stages with counts: «Упаковано» is open by default, «Все» holds the cancelled one too", async () => {
    stub([base, complete, working, shippedNoFile, shippedOld, closed, cancelled]);
    render(<ShipmentDataPage principal={principal} />);
    const table = await screen.findByRole("table");
    const headers = within(table).getAllByRole("columnheader").map((cell) => cell.textContent?.replace(/[▲▼↑↓⇅]/gu, "").trim()).filter(Boolean);
    expect(headers).toEqual(["КБ", "Наименование", "Количество", "Этап позиции", "ГРН", "Статус отправки", "Дата отгрузки", "ПЛ", "DN", "УПД"]);
    const chips = within(screen.getByRole("group", { name: "Быстрый отбор" }));
    // 713К.4 (ОТК) стоит в живых отправках; проекты без живой отправки: 800К.1 (В работе), 810К.1 и 820К.3 (ждут производства).
    expect(chips.getAllByRole("button").map((button) => button.textContent)).toEqual([
      "В ожидании · 2", "В производстве · 1", "Готово · 1", "Принято · 0", "Упаковано · 1", "На отгрузку · 1", "Отгружено · 3", "Все · 10"]);
    expect(chips.getByRole("button", { name: "Упаковано · 1" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("ОТГ-20261028-01")).toBeInTheDocument();
    expect(screen.getByText("Ждёт данных менеджера", { selector: ".status-chip" })).toHaveAttribute("title", "Не хватает: условия доставки, телефон получателя, тип УПД");
    expect(screen.queryByText("ОТГ-20261027-06")).not.toBeInTheDocument();
    fireEvent.click(chips.getByRole("button", { name: "Готово · 1" }));
    expect(await screen.findByText("ОТГ-20261027-06")).toBeInTheDocument();
    expect(screen.getByText("Новая", { selector: ".ship-position-shipment > span" })).toBeInTheDocument();
    fireEvent.click(chips.getByRole("button", { name: "Отгружено · 3" }));
    expect(await screen.findByText("ОТГ-20261020-03")).toBeInTheDocument();
    expect(screen.getByText("ОТГ-20260901-05")).toBeInTheDocument();
    expect(screen.queryByText("ОТГ-20261001-07")).not.toBeInTheDocument();
    fireEvent.click(chips.getByRole("button", { name: "Все · 10" }));
    expect(await screen.findByText("ОТГ-20261001-07")).toBeInTheDocument();
  });

  it("shipped shipments are neutral: no «Ждёт данных», not in «Только с недостающими данными»; only a quiet «Внести: файл УПД»", async () => {
    stub([base, complete, shippedNoFile, shippedOld]);
    render(<ShipmentDataPage principal={principal} />);
    const chips = within(await screen.findByRole("group", { name: "Быстрый отбор" }));
    fireEvent.click(await chips.findByRole("button", { name: "На отгрузку · 1" }));
    expect(await screen.findByText("ОТГ-20261028-02")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Только с недостающими данными"));
    await waitFor(() => expect(screen.queryByText("ОТГ-20261028-02")).not.toBeInTheDocument());
    fireEvent.click(chips.getByRole("button", { name: /^Упаковано/ }));
    expect(await screen.findByText("ОТГ-20261028-01")).toBeInTheDocument();
    expect(chips.getByRole("button", { name: "Отгружено · 0" })).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Только с недостающими данными"));
    fireEvent.click(await chips.findByRole("button", { name: "Отгружено · 2" }));
    expect(await screen.findByText("Внести: файл УПД")).toBeInTheDocument();
    expect(screen.queryByText("Ждёт данных")).not.toBeInTheDocument();
  });

  it("project rows come from the intake: «В ожидании» and «В производстве» open the project's «Данные отправки» card", async () => {
    stub([base]);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByRole("button", { name: "В ожидании · 2" }));
    expect(await screen.findByText("810К.1")).toBeInTheDocument();
    expect(screen.getByText("820К.3")).toBeInTheDocument();
    expect(screen.queryByText("900К.2")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "В производстве · 1" }));
    expect(await screen.findByText("Не заполнено: получатель, адрес доставки, телефон, вариант УПД")).toBeInTheDocument();
    fireEvent.click(screen.getByText("800К.1"));
    expect(await screen.findByRole("dialog", { name: "Данные отправки 800К.1" })).toBeInTheDocument();
  });

  it("counts gaps only for live shipments; projects without a shipment come after the shipments", () => {
    expect(dataGaps(base)).toEqual(base.missing_data);
    for (const sent of [shippedNoFile, shippedOld, closed]) expect(dataGaps(sent as never)).toEqual([]);
    expect(sentNote(shippedNoFile as never)).toBe("Внести: файл УПД");
    expect(sentNote(shippedOld as never)).toBe("");
    const rows = dataRows([closed, complete, base, cancelled] as never, [take("713К.4", "ОТК"), take("800К.1", "В работе")] as never);
    expect(rows.map((row) => row.shipment?.number ?? row.project)).toEqual(["ОТГ-20260901-05", "ОТГ-20261001-07", "ОТГ-20261028-01", "ОТГ-20261028-02", "713К.4", "800К.1"]);
  });

  it("maps project stages: below «В работе» and unknown words wait for production; the ladder otherwise", () => {
    const stages = (word: string) => dataRows([], [take("П", word)] as never).map((row) => row.stage);
    expect(stages("Новый")).toEqual(["waiting"]);
    for (const legacy of ["ТК готова", "КБ набрана", "Распланировано", "что-то новое", ""]) expect(stages(legacy)).toEqual(["waiting"]);
    expect(stages("В работе")).toEqual(["production"]);
    // «Готово» (производство закончено), «ОТК» (на проверке), «ОТК пройден» — все ждут приёмки: вкладка «Готово».
    for (const ready of ["Готово", "ОТК", "ОТК пройден"]) expect(stages(ready)).toEqual(["ready"]);
    expect(stages("Принято складом")).toEqual(["accepted"]);
    // Packed/sent quantities appear only as actual shipment lines, not an invented free row.
    expect(stages("Упаковано")).toEqual([]);
    expect(stages("Отгружено")).toEqual([]);
  });

  it("shipment stage: sent goes to Отгружено, Упакована to Упаковано, Новая/Собрана follow the project capped at «Принято на склад»", () => {
    const stageOf = (status: string, word: string) => dataRows([{ ...base, status }] as never, [{ ...take("713К.4", word), item_index: "КБ2" }] as never).find((row) => row.kind === "shipment")?.stage;
    expect(stageOf("Отгружена", "ОТК")).toBe("shipped");
    expect(stageOf("Закрыта", "Новый")).toBe("shipped");
    expect(stageOf("Упакована", "ОТК")).toBe("packed");
    expect(stageOf("Новая", "Новый")).toBe("waiting");
    expect(stageOf("Новая", "В работе")).toBe("production");
    expect(stageOf("Собрана", "ОТК")).toBe("ready");
    expect(stageOf("Новая", "Готово")).toBe("ready");
    expect(stageOf("Собрана", "ОТК пройден")).toBe("ready");
    expect(stageOf("Собрана", "Принято складом")).toBe("accepted");
    expect(stageOf("Собрана", "Упаковано")).toBe("accepted");
    expect(stageOf("Новая", "Отгружено")).toBe("accepted");
    expect(stageOf("Отменена", "ОТК")).toBeNull();
  });

  it("partly shipped project: keeps shipment history and the uncovered accepted KB beside another KB's live shipment", () => {
    const accepted = { ...take("713К.4", "Принято складом", "10", "4"), accepted: "10", packed: "4" };
    const history = { ...shippedNoFile, lines: [{ ...base.lines[0], item_index: "КБ1", requested: "4", picked: "4" }] };
    const live = { ...base, lines: [{ ...base.lines[0], item_index: "КБ1", requested: "6", picked: "6" }] };
    const rows = (list: unknown[], packed = "4") => dataRows(list as never, [{ ...accepted, packed }] as never).map((row) => `${row.shipment?.number ?? row.project}:${row.stage}`);
    expect(rows([history])).toEqual(["ОТГ-20261020-03:shipped", "713К.4:accepted"]);
    expect(rows([history, live], "10")).toEqual(["ОТГ-20261020-03:shipped", "ОТГ-20261028-01:packed"]);
    expect(rows([cancelled]).sort()).toEqual(["713К.4:accepted", "ОТГ-20261001-07:null"].sort());
    expect(dataRows([history] as never, [{ ...accepted, packed: "10", sent: "10" }] as never).map((row) => row.kind)).toEqual(["shipment"]);
    const partial = { ...take("713К.4", "В работе", "500"), accepted: "24", remaining: "476", pack_decided: true };
    const covered = { ...partial, item_index: "КБ2", accepted: "500", packed: "500", remaining: "0", stage: "Упаковано" };
    const actual = dataRows([base] as never, [partial, covered] as never);
    expect(actual.filter((row) => row.kind === "project").map((row) => [row.project, row.stage])).toEqual([["713К.4", "production"], ["713К.4", "accepted"]]);
  });

  it("uses factual accepted residuals for 780К.1 and covers only picked quantities of each KB, including drafts", () => {
    const accepted = (quantity: string) => ({ ...take("780К.1", "Упаковано", quantity), accepted: quantity, packed: "0", pack_decided: false });
    const actual = ["15", "15", "50", "15"].map((quantity, index) => ({ ...accepted(quantity), item_index: `КБ${index + 1}` }));
    expect(dataRows([], actual as never).map((row) => [row.project, row.stage])).toEqual(Array.from({ length: 4 }, () => ["780К.1", "accepted"]));
    const partial = { ...take("713К.4", "В работе", "500"), accepted: "24", remaining: "476" };
    const packed = { ...partial, item_index: "КБ2", accepted: "500", packed: "500", remaining: "0" };
    expect(dataRows([base] as never, [...actual, partial, packed] as never).filter((row) => row.kind === "project")
      .map((row) => [row.project, row.stage])).toEqual([["713К.4", "production"], ["713К.4", "accepted"], ...Array.from({ length: 4 }, () => ["780К.1", "accepted"])]);
    const draft = (picked: string) => ({ ...working, lines: [{ ...base.lines[0], project: "780К.1", item_index: "КБ1", requested: "100", picked }] });
    for (const picked of ["0", "40"]) {
      const rows = dataRows([draft(picked)] as never, [{ ...accepted("100"), packed: picked }] as never);
      expect(rows.filter((row) => row.kind === "project").map((row) => row.stage)).toEqual(["accepted"]);
    }
    expect(dataRows([draft("100")] as never, [{ ...accepted("100"), packed: "100" }] as never).filter((row) => row.kind === "project")).toEqual([]);
    expect(dataRows([{ ...draft("100"), status: "Отменена" }] as never, [accepted("100")] as never).filter((row) => row.kind === "project")).toHaveLength(1);
  });

  it("accepted project with no addressee still offers the packing decision", async () => {
    stub([]);
    const original = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => String(input) === "/api/shipping/intake"
      ? json({ rows: [{ ...take("780К.1", "Упаковано", "15"), accepted: "15", packed: "0" }] })
      : String(input).startsWith("/api/shipping/project-addressee?") ? json({ addressee: { project: "780К.1", version: 0, pack_decided: false } }) : original(input, init));
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByRole("button", { name: "Принято · 1" }));
    fireEvent.click(await screen.findByText("780К.1", { selector: "strong" }));
    expect(await within(screen.getByRole("dialog")).findByRole("checkbox", { name: "Упаковывать 780К.1" })).toBeEnabled();
  });

  it("window: «Адрес» block (address, receiver, phone) apart from «ТК и УПД»; the carrier only for a carrier delivery", async () => {
    stub([base]);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByText("ОТГ-20261028-01"));
    const form = within(screen.getByRole("dialog", { name: "Данные отправки ОТГ-20261028-01" }));
    expect(form.getByText(/не хватает: условия доставки, телефон получателя, тип УПД/)).toBeInTheDocument();
    expect(form.getByRole("heading", { name: "Адрес" })).toBeInTheDocument();
    expect(form.getByRole("heading", { name: "ТК и УПД" })).toBeInTheDocument();
    for (const label of ["Адрес доставки", "Получатель", "Телефон получателя", "Тип доставки", "Тип УПД"]) expect(form.getByLabelText(label)).toBeInTheDocument();
    expect(form.queryByLabelText("Перевозчик")).not.toBeInTheDocument();
    fireEvent.change(form.getByLabelText("Тип доставки"), { target: { value: "ДОСТАВКА ТК" } });
    expect(form.getByLabelText("Перевозчик")).toBeInTheDocument();
    fireEvent.change(form.getByLabelText("Тип доставки"), { target: { value: "САМОВЫВОЗ" } });
    expect(form.queryByLabelText("Перевозчик")).not.toBeInTheDocument();
    // «Без УПД» не просит ни номера, ни даты, ни файла.
    fireEvent.change(form.getByLabelText("Тип УПД"), { target: { value: "БУМАГА" } });
    expect(form.getByLabelText("№ УПД")).toBeInTheDocument();
    expect(form.getByLabelText("Файл УПД")).toBeInTheDocument();
    fireEvent.change(form.getByLabelText("Тип УПД"), { target: { value: "БЕЗ УПД" } });
    expect(form.queryByLabelText("№ УПД")).not.toBeInTheDocument();
    expect(form.queryByLabelText("Файл УПД")).not.toBeInTheDocument();
  });

  it("window by stage: «Упаковано» marks УПД and ТК mandatory and waits for the warehouse track; «На отгрузку» shows the track and date", async () => {
    stub([base, { ...complete, id: "s8", number: "ОТГ-20261028-08", ready_to_ship: true, track_number: "TR-77", packed_at: "2026-10-29" }]);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByText("ОТГ-20261028-01"));
    let form = within(screen.getByRole("dialog", { name: "Данные отправки ОТГ-20261028-01" }));
    expect(form.getByText(/УПД и ТК обязательны/)).toBeInTheDocument();
    expect(form.queryByText("трек: ждём склад")).not.toBeInTheDocument();
    expect(form.getByLabelText("Тип УПД").closest("label")).toHaveClass("ship-required");
    fireEvent.click(form.getByRole("button", { name: "Отмена" }));
    fireEvent.click(await screen.findByRole("button", { name: "На отгрузку · 1" }));
    fireEvent.click(await screen.findByText("ОТГ-20261028-08"));
    form = within(screen.getByRole("dialog", { name: "Данные отправки ОТГ-20261028-08" }));
    expect(form.getByText("TR-77")).toBeInTheDocument();
    expect(form.getByText(/упаковано 29\.10\.2026/)).toBeInTheDocument();
    expect(form.queryByText(/УПД и ТК обязательны/)).not.toBeInTheDocument();
    expect(form.queryByText("трек: ждём склад")).not.toBeInTheDocument();
  });

  it("window on «Готово»: «Упаковывать» toggle calls pack-decision; shipped has no address block and names who allowed «без УПД»", async () => {
    stub([working, { ...shippedNoFile, upd_type: "БЕЗ УПД", missing_data: [], no_upd_approved_by: "Мастер Иванов", no_upd_approved_at: "2026-10-26T10:00:00" }]);
    const base0 = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith("/api/shipping/project-addressee?")) return json({ addressee: { project: "713К.4", version: 3, pack_decided: false } });
      if (url === "/api/shipping/project-pack-decision") return json({ addressee: { project: "713К.4", version: 4, pack_decided: true, pack_decided_by: "Ульяна", pack_decided_at: "2026-10-29T09:00:00" } });
      return base0(input, init);
    });
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByRole("button", { name: /^Готово/ }));
    fireEvent.click(await screen.findByText("ОТГ-20261027-06"));
    const form = within(screen.getByRole("dialog", { name: "Данные отправки ОТГ-20261027-06" }));
    const toggle = await form.findByLabelText("Упаковывать 713К.4");
    await waitFor(() => expect(toggle).toBeEnabled());
    fireEvent.click(toggle);
    await waitFor(() => expect(bodies("/project-pack-decision")).toEqual([{ project: "713К.4", version: 3, decided: true }]));
    expect(await form.findByText(/решил Ульяна, 29\.10\.2026/)).toBeInTheDocument();
    fireEvent.click(form.getByRole("button", { name: "Отмена" }));
    fireEvent.click(screen.getByRole("button", { name: /^Отгружено/ }));
    fireEvent.click(await screen.findByText("ОТГ-20261020-03"));
    const sent = within(screen.getByRole("dialog", { name: "Данные отправки ОТГ-20261020-03" }));
    expect(sent.queryByLabelText("Адрес доставки")).not.toBeInTheDocument();
    expect(sent.getByText(/без УПД — разрешил Мастер Иванов, 26\.10\.2026/)).toBeInTheDocument();
  });

  it("saves through the shipment edit endpoints with the version: the head first, then the documents", async () => {
    stub([base]);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByText("ОТГ-20261028-01"));
    const form = within(screen.getByRole("dialog", { name: "Данные отправки ОТГ-20261028-01" }));
    fireEvent.change(form.getByLabelText("Тип доставки"), { target: { value: "ДОСТАВКА ТК" } });
    fireEvent.change(form.getByLabelText("Перевозчик"), { target: { value: "СДЭК" } });
    fireEvent.change(form.getByLabelText("Адрес доставки"), { target: { value: "Тула, ул. Оружейная, 12" } });
    fireEvent.change(form.getByLabelText("Телефон получателя"), { target: { value: "+7 900 111-22-33" } });
    fireEvent.change(form.getByLabelText("Тип УПД"), { target: { value: "ЭДО" } });
    fireEvent.change(form.getByLabelText("№ УПД"), { target: { value: "474" } });
    fireEvent.change(form.getByLabelText("Дата УПД"), { target: { value: "2026-10-27" } });
    fireEvent.click(form.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(bodies("/documents")).toHaveLength(1));
    expect(bodies("/head")).toEqual([{ version: 4, phone: "+7 900 111-22-33", terms: "ДОСТАВКА ТК", carrier: "СДЭК", shipping_point: "Тула, ул. Оружейная, 12" }]);
    expect(bodies("/documents")).toEqual([{ version: 5, upd_type: "ЭДО", upd_number: "474", upd_date: "2026-10-27", upd_url: "", track_number: "" }]);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("the «УПД» column shows type, № and the file link even for shipped ones", async () => {
    stub([{ ...base, upd_type: "ЭДО", upd_number: "518", upd_url: "/api/shipping/files/f1" }]);
    render(<ShipmentDataPage principal={principal} />);
    const table = await screen.findByRole("table");
    expect(within(table).getByRole("columnheader", { name: /УПД/ })).toBeInTheDocument();
    expect(within(table).getByText("№ 518")).toBeInTheDocument();
    expect(within(table).getByRole("link", { name: "файл" })).toHaveAttribute("href", "/api/shipping/files/f1");
  });

  it("has «Сканировать» and «Файл…», and no «+ Страница» before the first scan", async () => {
    stub([base]);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByText("ОТГ-20261028-01"));
    const form = within(screen.getByRole("dialog", { name: "Данные отправки ОТГ-20261028-01" }));
    expect(form.getByRole("button", { name: "Сканировать" })).toBeEnabled();
    expect(form.getByRole("button", { name: "Файл…" })).toBeEnabled();
    expect(form.queryByRole("button", { name: "+ Страница" })).not.toBeInTheDocument();
  });

  it("with the type «ЭДО» a PDF exported from ЭДО is taken and lands as ЭДО", async () => {
    stub([base]);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByText("ОТГ-20261028-01"));
    const form = within(screen.getByRole("dialog", { name: "Данные отправки ОТГ-20261028-01" }));
    fireEvent.change(form.getByLabelText("Тип УПД"), { target: { value: "ЭДО" } });
    expect(form.getByLabelText("Файл УПД").getAttribute("accept")).toContain(".pdf");
    fireEvent.change(form.getByLabelText("Файл УПД"), { target: { files: [new File(["%PDF"], "upd.pdf", { type: "application/pdf" })] } });
    await waitFor(() => expect(bodies("/upd-scan/apply")).toHaveLength(1));
    expect(bodies("/upd-scan/apply")[0]).toMatchObject({ upd_number: "РЭ-0915", edo: true });
  });

  it("an ЭДО file fills the type, № and date at once and shows the attached file; the form stays editable", async () => {
    stub([base]);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByText("ОТГ-20261028-01"));
    const form = within(screen.getByRole("dialog", { name: "Данные отправки ОТГ-20261028-01" }));
    fireEvent.change(form.getByLabelText("Файл УПД"), { target: { files: [new File(["<?xml"], "upd.xml", { type: "application/xml" })] } });
    await waitFor(() => expect(form.getByLabelText("№ УПД")).toHaveValue("РЭ-0915"));
    expect(form.getByLabelText("Тип УПД")).toHaveValue("ЭДО");
    expect(form.getByLabelText("Дата УПД")).toHaveValue("2026-09-15");
    expect(form.getByRole("link", { name: "Файл ЭДО · открыть" })).toHaveAttribute("href", "/api/shipping/files/f9");
    expect(form.getByRole("status")).toHaveTextContent("УПД № РЭ-0915 внесён, № счёта РЭ-777 — в проект");
    expect(bodies("/upd-scan/apply")).toEqual([{ version: 4, scan_file: "/api/stock/receipts/files/f1", upd_number: "РЭ-0915", upd_date: "2026-09-15", invoice_number: "РЭ-777", number: "replace" }]);
    fireEvent.change(form.getByLabelText("№ УПД"), { target: { value: "РЭ-0915/1" } });
    expect(form.getByLabelText("№ УПД")).toHaveValue("РЭ-0915/1");
  });

  it("«Убрать файл» drops the attached file on «Сохранить»; «Вернуть» keeps it", async () => {
    stub([{ ...APPLIED, version: 4 }]);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByText("ОТГ-20261028-01"));
    const form = within(screen.getByRole("dialog", { name: "Данные отправки ОТГ-20261028-01" }));
    fireEvent.click(form.getByRole("button", { name: "Убрать файл" }));
    expect(form.queryByRole("link", { name: "Файл ЭДО · открыть" })).not.toBeInTheDocument();
    fireEvent.click(form.getByRole("link", { name: "Вернуть" }));
    expect(form.getByRole("link", { name: "Файл ЭДО · открыть" })).toBeInTheDocument();
    fireEvent.click(form.getByRole("button", { name: "Убрать файл" }));
    expect(form.getByText(/Файл уберётся при сохранении/)).toBeInTheDocument();
    fireEvent.click(form.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(bodies("/documents")).toHaveLength(1));
    expect(bodies("/documents")).toEqual([{ version: 4, upd_type: "ЭДО", upd_number: "РЭ-0915", upd_date: "2026-09-15", upd_url: "", track_number: "" }]);
  });

  it("never replaces a typed № silently: asks first, «Оставить мой №» sends keep, «Заменить» sends replace", async () => {
    stub([base]);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByText("ОТГ-20261028-01"));
    const form = within(screen.getByRole("dialog", { name: "Данные отправки ОТГ-20261028-01" }));
    fireEvent.change(form.getByLabelText("Тип УПД"), { target: { value: "БУМАГА" } });
    fireEvent.change(form.getByLabelText("№ УПД"), { target: { value: "474" } });
    fireEvent.change(form.getByLabelText("Файл УПД"), { target: { files: [new File(["x"], "upd.xml", { type: "application/xml" })] } });
    expect(await form.findByText(/Заменить № УПД «474» на «РЭ-0915» из документа\?/)).toBeInTheDocument();
    expect(bodies("/upd-scan/apply")).toHaveLength(0);
    fireEvent.click(form.getByRole("button", { name: "Оставить мой №" }));
    await waitFor(() => expect(bodies("/upd-scan/apply")).toHaveLength(1));
    expect(bodies("/upd-scan/apply")[0].number).toBe("keep");
    expect(form.getByLabelText("№ УПД")).toHaveValue("474");

    fireEvent.change(form.getByLabelText("Файл УПД"), { target: { files: [new File(["x"], "upd.xml", { type: "application/xml" })] } });
    fireEvent.click(await form.findByRole("button", { name: "Заменить" }));
    await waitFor(() => expect(bodies("/upd-scan/apply")).toHaveLength(2));
    expect(bodies("/upd-scan/apply")[1].number).toBe("replace");
    await waitFor(() => expect(form.getByLabelText("№ УПД")).toHaveValue("РЭ-0915"));
  });

  it("keeps the window open with the server's words when saving fails", async () => {
    stub([shippedNoFile]);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByRole("button", { name: /^Отгружено/ }));
    fireEvent.click(await screen.findByText("ОТГ-20261020-03"));
    const form = within(screen.getByRole("dialog", { name: "Данные отправки ОТГ-20261020-03" }));
    fireEvent.change(form.getByLabelText("№ УПД"), { target: { value: "8" } });
    vi.mocked(fetch).mockImplementation(async (input) => String(input).endsWith("/documents")
      ? new Response(JSON.stringify({ detail: "Закрытую или отменённую отгрузку нельзя менять" }), { status: 422 }) : json({ shipments: [shippedNoFile], goods: [] }));
    fireEvent.click(form.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Закрытую или отменённую отгрузку нельзя менять");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  describe("cards view and forecast", () => {
    /** The shipments stub plus the person's settings (`views`) and the workshop forecast. */
    function stubWith(views: Record<string, unknown>, forecast: Record<string, unknown> = {}) {
      const writes: Array<{ path: string; body: unknown }> = [];
      vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input), method = init?.method ?? "GET";
        if (url === "/api/auth/ui-settings" && method === "GET") return json({ views });
        if (url.startsWith("/api/auth/ui-settings/") && method === "PUT") { writes.push({ path: url, body: JSON.parse(String(init?.body)) }); return json({ view: "x", value: {} }); }
        if (url === "/api/shipping/forecast") return json({ projects: forecast });
        if (url === "/api/shipping/shipments") return json({ shipments: [base, complete] });
        if (url === "/api/shipping/intake") return json({ rows: INTAKE });
        return json({});
      }));
      return writes;
    }

    it("opens as a table by default; «Карточки» shows the same rows as cards, a card opens the shipment window, the choice is saved for the person", async () => {
      const writes = stubWith({});
      render(<ShipmentDataPage principal={principal} />);
      expect(await screen.findByRole("table")).toBeInTheDocument();
      const switcher = within(screen.getByRole("group", { name: "Вид списка" }));
      expect(switcher.getByRole("button", { name: "Таблица" })).toHaveAttribute("aria-pressed", "true");
      fireEvent.click(switcher.getByRole("button", { name: "Карточки" }));
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      const cardButton = await screen.findByRole("button", { name: "Открыть: ОТГ-20261028-01" });
      expect(within(cardButton).getByText("ООО «ТСР»")).toBeInTheDocument();
      expect(within(cardButton).getByText("Ждёт данных менеджера")).toBeInTheDocument();
      expect(within(cardButton).getByText("Дата отгрузки")).toBeInTheDocument();
      expect(within(cardButton).queryByText("02.11.2026")).not.toBeInTheDocument();
      // the stage chip still filters the cards
      fireEvent.click(within(screen.getByRole("group", { name: "Быстрый отбор" })).getByRole("button", { name: /^Все/ }));
      expect(await screen.findByRole("button", { name: "Открыть: ОТГ-20261028-02" })).toBeInTheDocument();
      fireEvent.click(cardButton);
      expect(await screen.findByRole("dialog")).toBeInTheDocument();
      await waitFor(() => expect(writes).toEqual([{ path: "/api/auth/ui-settings/shipment-data.view", body: { value: { mode: "cards" } } }]), { timeout: 2000 });
    });

    it("restores the saved view on reload: the server's choice wins", async () => {
      stubWith({ "shipment-data.view": { mode: "cards" } });
      render(<ShipmentDataPage principal={principal} />);
      expect(await screen.findByRole("button", { name: "Открыть: ОТГ-20261028-01" })).toBeInTheDocument();
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Карточки" })).toHaveAttribute("aria-pressed", "true");
    });

    it("shows the workshop forecast next to the deadline: red when it is after the deadline, quiet when in time, nothing without a forecast", async () => {
      stubWith({}, { "713К.4": { ready: "2026-11-05", status: "не успевает", slack_days: -3 } });
      render(<ShipmentDataPage principal={principal} />);
      expect(await screen.findByText("прогноз готовности 05.11.2026")).toHaveClass("tone-danger");
      cleanup();
      stubWith({}, { "713К.4": { ready: "2026-10-30", status: "в срок", slack_days: 3 } });
      render(<ShipmentDataPage principal={principal} />);
      expect(await screen.findByText("прогноз готовности 30.10.2026")).not.toHaveClass("tone-danger");
      cleanup();
      stubWith({}, {});
      render(<ShipmentDataPage principal={principal} />);
      await screen.findByText("ОТГ-20261028-01");
      expect(screen.queryByText(/прогноз готовности/)).not.toBeInTheDocument();
    });
  });
});
