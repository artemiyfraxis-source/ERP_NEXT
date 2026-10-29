import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ShipmentDataPage, dataGaps, dataRows, sentNote } from "./ShipmentDataPage";

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
const complete = { ...base, id: "s2", number: "ОТГ-20261028-02", terms: "САМОВЫВОЗ", carrier: "Самовывоз", phone: "+7 900", upd_type: "ЭДО", upd_number: "5", missing_data: [], missing_ship: [] };
const shippedNoFile = { ...complete, id: "s3", number: "ОТГ-20261020-03", status: "Отгружена", shipped_at: "2026-10-25", upd_type: "БУМАГА", upd_number: "7", missing_data: ["файл УПД"], missing_ship: ["файл УПД"] };
// Уехала до новых правил: телефона нет, но УПД на месте — в списке менеджера её нет.
const shippedOld = { ...complete, id: "s4", number: "ОТГ-20261001-04", status: "Отгружена", shipped_at: "2026-10-05", missing_data: ["телефон получателя"], missing_ship: ["телефон получателя"] };
const working = { ...base, id: "s6", number: "ОТГ-20261027-06", status: "Новая" };
const cancelled = { ...complete, id: "s7", number: "ОТГ-20261001-07", status: "Отменена" };
const closed = { ...complete, id: "s5", number: "ОТГ-20260901-05", status: "Закрыта" };

// Строки приёмки: 713К.4 в отправках (ОТК); 800К.1 без отправки, В работе; 900К.2 уехала целиком; 810К.1 новый; 820К.3 «ТК готова» (старое слово).
const take = (project: string, project_stage: string, needed = "10", sent = "0") => ({ project, customer: `Заказчик ${project}`, deadline: null, item_index: "КБ1", name: "Жгут", unit: "шт",
  stage: "", project_stage, needed, launched: "0", delivered: "0", accepted: "0", packed: "0", sent, remaining: "0", good_id: null, good_version: null, grn: null, location: "", tasks: [] });
const INTAKE = [take("713К.4", "ОТК"), take("800К.1", "В работе"), take("900К.2", "Отгружено", "10", "10"), take("810К.1", "Новый"), take("820К.3", "ТК готова")];
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
    if (url.endsWith("/upd-scan/file")) return json(UPD_JOB);
    if (url.endsWith("/upd-scan/apply")) return json({ status: "applied", shipment: { ...APPLIED, version: JSON.parse(String(init?.body)).version + 1 }, invoice: "РЭ-777" });
    if (/\/head$/.test(url) || /\/documents$/.test(url)) return json({ shipment: { ...base, version: JSON.parse(String(init?.body)).version + 1 } });
    return json({});
  }));
}
const bodies = (suffix: string) => vi.mocked(fetch).mock.calls.filter(([target]) => String(target).endsWith(suffix)).map(([, init]) => JSON.parse(String(init?.body)));

describe("ShipmentDataPage («Данные отправок»)", () => {
  beforeEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); });

  it("splits the list into six stages with counts: «Упаковано» is open by default, «Все» holds the cancelled one too", async () => {
    stub([base, complete, working, shippedNoFile, shippedOld, closed, cancelled]);
    render(<ShipmentDataPage principal={principal} />);
    const table = await screen.findByRole("table");
    const headers = within(table).getAllByRole("columnheader").map((cell) => cell.textContent?.replace(/[▲▼↑↓⇅]/gu, "").trim()).filter(Boolean);
    expect(headers).toEqual(["№ отправки", "Проект", "Заказчик", "Статус", "Данные", "Срок"]);
    const chips = within(screen.getByRole("group", { name: "Быстрый отбор" }));
    // 713К.4 (ОТК) стоит в живых отправках; проекты без живой отправки: 800К.1 (В работе), 810К.1 и 820К.3 (ждут производства).
    expect(chips.getAllByRole("button").map((button) => button.textContent)).toEqual([
      "В ожидании производства · 2", "В производстве · 1", "Готово · 1", "Принято на склад · 0", "Упаковано · 2", "Отгружено · 3", "Все · 10"]);
    expect(chips.getByRole("button", { name: "Упаковано · 2" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("ОТГ-20261028-01")).toBeInTheDocument();
    expect(screen.getByText("Ждёт данных менеджера", { selector: ".status-chip" })).toHaveAttribute("title", "Не хватает: условия доставки, телефон получателя, тип УПД");
    expect(screen.queryByText("ОТГ-20261027-06")).not.toBeInTheDocument();
    fireEvent.click(chips.getByRole("button", { name: "Готово · 1" }));
    expect(await screen.findByText("ОТГ-20261027-06")).toBeInTheDocument();
    expect(screen.getByText("Новая", { selector: ".ship-sub" })).toBeInTheDocument();
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
    expect(await screen.findByText("ОТГ-20261028-02")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Только с недостающими данными"));
    await waitFor(() => expect(screen.queryByText("ОТГ-20261028-02")).not.toBeInTheDocument());
    expect(screen.getByText("ОТГ-20261028-01")).toBeInTheDocument();
    expect(chips.getByRole("button", { name: "Отгружено · 0" })).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Только с недостающими данными"));
    fireEvent.click(await chips.findByRole("button", { name: "Отгружено · 2" }));
    expect(await screen.findByText("Внести: файл УПД")).toBeInTheDocument();
    expect(screen.queryByText("Ждёт данных")).not.toBeInTheDocument();
  });

  it("project rows come from the intake: «В ожидании производства» and «В производстве» open the project's «Данные отправки» card", async () => {
    stub([base]);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByRole("button", { name: "В ожидании производства · 2" }));
    expect(await screen.findByText("810К.1")).toBeInTheDocument();
    expect(screen.getByText("820К.3")).toBeInTheDocument();
    expect(screen.queryByText("900К.2")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "В производстве · 1" }));
    expect(await screen.findByText("внести заранее")).toBeInTheDocument();
    fireEvent.click(screen.getByText("800К.1"));
    expect(await screen.findByRole("dialog", { name: "Данные отправки 800К.1" })).toBeInTheDocument();
  });

  it("counts gaps only for live shipments; projects without a shipment come after the shipments", () => {
    expect(dataGaps(base)).toEqual(base.missing_data);
    for (const sent of [shippedNoFile, shippedOld, closed]) expect(dataGaps(sent as never)).toEqual([]);
    expect(sentNote(shippedNoFile as never)).toBe("Внести: файл УПД");
    expect(sentNote(shippedOld as never)).toBe("");
    const rows = dataRows([closed, complete, base, cancelled] as never, [take("713К.4", "ОТК"), take("800К.1", "В работе")] as never);
    expect(rows.map((row) => row.shipment?.number ?? row.project)).toEqual(["ОТГ-20260901-05", "ОТГ-20261001-07", "ОТГ-20261028-01", "ОТГ-20261028-02", "800К.1"]);
  });

  it("maps project stages: below «В работе» and unknown words wait for production; the ladder otherwise", () => {
    const stages = (word: string) => dataRows([], [take("П", word)] as never).map((row) => row.stage);
    expect(stages("Новый")).toEqual(["В ожидании производства"]);
    for (const legacy of ["ТК готова", "КБ набрана", "Распланировано", "что-то новое", ""]) expect(stages(legacy)).toEqual(["В ожидании производства"]);
    expect(stages("В работе")).toEqual(["В производстве"]);
    expect(stages("ОТК")).toEqual(["Готово"]);
    expect(stages("Принято складом")).toEqual(["Принято на склад"]);
    expect(stages("Упаковано")).toEqual(["Упаковано"]);
    expect(stages("Отгружено")).toEqual(["Отгружено"]);
  });

  it("shipment stage: sent goes to Отгружено, Упакована to Упаковано, Новая/Собрана follow the project capped at «Принято на склад»", () => {
    const stageOf = (status: string, word: string) => dataRows([{ ...base, status }] as never, [take("713К.4", word)] as never).find((row) => row.kind === "shipment")?.stage;
    expect(stageOf("Отгружена", "ОТК")).toBe("Отгружено");
    expect(stageOf("Закрыта", "Новый")).toBe("Отгружено");
    expect(stageOf("Упакована", "ОТК")).toBe("Упаковано");
    expect(stageOf("Новая", "Новый")).toBe("В ожидании производства");
    expect(stageOf("Новая", "В работе")).toBe("В производстве");
    expect(stageOf("Собрана", "ОТК")).toBe("Готово");
    expect(stageOf("Собрана", "Принято складом")).toBe("Принято на склад");
    expect(stageOf("Собрана", "Упаковано")).toBe("Принято на склад");
    expect(stageOf("Новая", "Отгружено")).toBe("Принято на склад");
    expect(stageOf("Отменена", "ОТК")).toBe("Отменена");
  });

  it("partly shipped project: the shipped shipment stays in «Отгружено», the rest shows as a project row; a live shipment covers it; a cancelled one frees it", () => {
    const intake = [take("713К.4", "Принято складом", "10", "4")] as never;
    const rows = (list: unknown[]) => dataRows(list as never, intake).map((row) => `${row.shipment?.number ?? row.project}:${row.stage}`);
    expect(rows([shippedNoFile])).toEqual(["ОТГ-20261020-03:Отгружено", "713К.4:Принято на склад"]);
    expect(rows([shippedNoFile, base])).toEqual(["ОТГ-20261020-03:Отгружено", "ОТГ-20261028-01:Упаковано"]);
    expect(rows([shippedNoFile, base, working]).filter((row) => row.startsWith("ОТГ")).length).toBe(3);
    expect(rows([cancelled]).sort()).toEqual(["713К.4:Принято на склад", "ОТГ-20261001-07:Отменена"].sort());
    expect(dataRows([shippedNoFile] as never, [take("713К.4", "Отгружено", "10", "10")] as never).map((row) => row.kind)).toEqual(["shipment"]);
  });

  it("opens a window with only the manager's fields; a pickup hides the address and the carrier, a carrier delivery shows them", async () => {
    stub([base]);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByText("ОТГ-20261028-01"));
    const form = within(screen.getByRole("dialog", { name: "Данные отправки ОТГ-20261028-01" }));
    expect(form.getByText(/не хватает: условия доставки, телефон получателя, тип УПД/)).toBeInTheDocument();
    for (const label of ["Тип доставки", "Получатель", "Телефон получателя", "Тип УПД"]) expect(form.getByLabelText(label)).toBeInTheDocument();
    expect(form.queryByLabelText("Адрес доставки")).not.toBeInTheDocument();
    expect(form.queryByLabelText("Трек-номер")).not.toBeInTheDocument();
    fireEvent.change(form.getByLabelText("Тип доставки"), { target: { value: "ДОСТАВКА ТК" } });
    expect(form.getByLabelText("Адрес доставки")).toBeInTheDocument();
    expect(form.getByLabelText("Перевозчик")).toBeInTheDocument();
    fireEvent.change(form.getByLabelText("Тип доставки"), { target: { value: "САМОВЫВОЗ" } });
    expect(form.queryByLabelText("Адрес доставки")).not.toBeInTheDocument();
    expect(form.queryByLabelText("Перевозчик")).not.toBeInTheDocument();
    // «Без УПД» не просит ни номера, ни даты, ни файла.
    fireEvent.change(form.getByLabelText("Тип УПД"), { target: { value: "БУМАГА" } });
    expect(form.getByLabelText("№ УПД")).toBeInTheDocument();
    expect(form.getByLabelText("Файл УПД")).toBeInTheDocument();
    fireEvent.change(form.getByLabelText("Тип УПД"), { target: { value: "БЕЗ УПД" } });
    expect(form.queryByLabelText("№ УПД")).not.toBeInTheDocument();
    expect(form.queryByLabelText("Файл УПД")).not.toBeInTheDocument();
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

  it("has «Сканировать» and «Файл…», and no «+ Страница» before the first scan", async () => {
    stub([base]);
    render(<ShipmentDataPage principal={principal} />);
    fireEvent.click(await screen.findByText("ОТГ-20261028-01"));
    const form = within(screen.getByRole("dialog", { name: "Данные отправки ОТГ-20261028-01" }));
    expect(form.getByRole("button", { name: "Сканировать" })).toBeEnabled();
    expect(form.getByRole("button", { name: "Файл…" })).toBeEnabled();
    expect(form.queryByRole("button", { name: "+ Страница" })).not.toBeInTheDocument();
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
});
