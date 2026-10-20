import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TdIssuesPanel } from "./TdIssuesPanel";
import { openRouteSheetPrint, openStoPrint, openTdKitPrint } from "./tdPrint";
import type { TdFinding, TdIssue } from "./tdTypes";

vi.mock("./tdPrint", () => ({ openTdKitPrint: vi.fn(), openStoPrint: vi.fn(), openRouteSheetPrint: vi.fn() }));

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const person = (name = "", position = "") => ({ name, position });

function issue(over: Partial<TdIssue> = {}): TdIssue {
  return {
    id: "i1", version: 1, project_card_id: "c1", change_no: 0, status: "draft", status_label: "проект", etk_revision: 3,
    current_etk_revision: 3, has_changes: false, sequence_no: 1, org_code: "ФРАК", method_code: "85", litera: "О1",
    designations: { vtd: "ФРАК.60185.00001", mk: "", ke: "", ok: "", ok_tk: "", vm: "", vo: "" },
    signatories: { developer: person("Иванов И.И.", "Технолог"), checker: person("Петров П.П."), normcontrol: person("Сидоров С.С."), approver: person(), agreed_vp: person() },
    notice_no: "", reason: "", created_at: "2026-10-01T10:00:00+03:00", created_by: "u", approved_at: null, annulled_at: null, ...over,
  };
}

type Calls = { path: string; method: string; body: Record<string, unknown> | null }[];
function mount(issues: TdIssue[], findings: TdFinding[] = [], extra?: (path: string, method: string) => Response | undefined) {
  const calls: Calls = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const method = init?.method ?? "GET";
    calls.push({ path, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    const custom = extra?.(path, method);
    if (custom) return custom;
    if (path.endsWith("/normcontrol")) return json({ findings });
    if (path.endsWith("/print")) return json({ issue: issues[0], card: {}, sto_sections: {}, changes: [], sto: { designation: "СТО", revision: "1" } });
    if (path.endsWith("/api/techcards/sto")) return json({ designation: "СТО" });
    if (path.endsWith("/td-issues") && method === "GET") return json(issues);
    return json(issues[0], 201);
  });
  render(<TdIssuesPanel cardId="c1" canEdit />);
  return calls;
}

beforeEach(() => vi.clearAllMocks());
afterEach(() => vi.restoreAllMocks());

it("lists issues with the column labels and dates as dd.mm.yyyy", async () => {
  mount([issue({ status: "active", status_label: "действует", approved_at: "2026-10-05T12:00:00+03:00", change_no: 1 })]);
  expect(await screen.findByText("ФРАК.60185.00001")).toBeInTheDocument();
  for (const head of ["Обозначение", "Изм.", "Литера", "Статус", "Ревизия ЭТК", "Утверждён"]) expect(screen.getByRole("columnheader", { name: head })).toBeInTheDocument();
  expect(screen.getByText("действует")).toBeInTheDocument();
  expect(screen.getByText("05.10.2026")).toBeInTheDocument();
});

it("offers «Новый выпуск» only when the card has no issues", async () => {
  mount([]);
  expect(await screen.findByRole("button", { name: "Новый выпуск" })).toBeInTheDocument();
});

it("warns when the active kit lags behind the ETK and opens the notice form", async () => {
  mount([issue({ status: "active", status_label: "действует", has_changes: true, etk_revision: 3, current_etk_revision: 5 })]);
  expect(await screen.findByText("ЭТК изменилась (ревизия 5), действующий комплект — ревизия 3. Оформите извещение об изменении.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Оформить извещение" }));
  expect(await screen.findByRole("dialog", { name: "Извещение об изменении" })).toBeInTheDocument();
  expect(screen.getByLabelText("Причина изменения")).toBeInTheDocument();
});

it("disables «Отправить на подпись» while a blocking finding exists, warnings do not block", async () => {
  mount([issue()], [{ code: "x", severity: "blocking", operation_key: null, text: "Нет подписанта" }, { code: "y", severity: "warning", operation_key: null, text: "Нет эскиза" }]);
  expect(await screen.findByText("Нет подписанта")).toBeInTheDocument();
  expect(screen.getByText("блокирует")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Отправить на подпись" })).toBeDisabled();
});

it("sends to signing when only warnings exist and shows findings of a 422 refusal", async () => {
  const blocked: TdFinding = { code: "z", severity: "blocking", operation_key: null, text: "Нет литеры" };
  const calls = mount([issue()], [{ code: "y", severity: "warning", operation_key: null, text: "Нет эскиза" }],
    (path, method) => path.endsWith("/status") && method === "POST" ? json({ detail: { findings: [blocked] } }, 422) : undefined);
  const send = await screen.findByRole("button", { name: "Отправить на подпись" });
  await waitFor(() => expect(send).toBeEnabled());
  fireEvent.click(send);
  expect(await screen.findByText("Нет литеры")).toBeInTheDocument();
  expect(calls.find((call) => call.path.endsWith("/status"))?.body).toEqual({ version: 1, to: "signing" });
});

it("shows the buttons of a signing issue", async () => {
  mount([issue({ status: "signing", status_label: "на подписи" })]);
  expect(await screen.findByRole("button", { name: "Утвердить" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Вернуть в проект" })).toBeInTheDocument();
});

it("the form rejects a normcontroller who is also the developer", async () => {
  const calls = mount([issue()]);
  fireEvent.click(await screen.findByRole("button", { name: "Править" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("Н. контроль: ФИО"), { target: { value: "иванов и.и." } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));
  expect(await within(dialog).findByText(/Нормоконтролёр не может быть разработчиком/)).toBeInTheDocument();
  expect(calls.some((call) => call.method === "PUT")).toBe(false);
});

it("the form requires notice and reason from the second issue on and saves with the version", async () => {
  const calls = mount([issue({ change_no: 1, version: 4, notice_no: "", reason: "" })]);
  fireEvent.click(await screen.findByRole("button", { name: "Править" }));
  const dialog = await screen.findByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));
  expect(await within(dialog).findByText("Укажите номер извещения об изменении.")).toBeInTheDocument();
  expect(within(dialog).getByText("Укажите причину изменения.")).toBeInTheDocument();
  fireEvent.change(within(dialog).getByLabelText("№ извещения"), { target: { value: "ИЗВ-7" } });
  fireEvent.change(within(dialog).getByLabelText("Причина изменения"), { target: { value: "Замена контакта" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));
  await waitFor(() => expect(calls.find((call) => call.method === "PUT")?.body).toMatchObject({ version: 4, notice_no: "ИЗВ-7", reason: "Замена контакта", litera: "О1" }));
});

it("the first issue asks no notice", async () => {
  mount([]);
  fireEvent.click(await screen.findByRole("button", { name: "Новый выпуск" }));
  const dialog = await screen.findByRole("dialog", { name: "Новый выпуск комплекта ТД" });
  expect(within(dialog).queryByLabelText("№ извещения")).toBeNull();
});

it("print buttons call the print functions", async () => {
  mount([issue()]);
  fireEvent.click(await screen.findByRole("button", { name: "Печать комплекта ТД" }));
  await waitFor(() => expect(openTdKitPrint).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", { name: "Печать СТО" }));
  await waitFor(() => expect(openStoPrint).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", { name: "Сопроводительный лист" }));
  const dialog = await screen.findByRole("dialog", { name: "Сопроводительный лист" });
  fireEvent.change(within(dialog).getByLabelText("Заводской номер"), { target: { value: "17" } });
  fireEvent.change(within(dialog).getByLabelText("Партия"), { target: { value: "П-5" } });
  fireEvent.change(within(dialog).getByLabelText("Количество"), { target: { value: "10" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Печать" }));
  await waitFor(() => expect(openRouteSheetPrint).toHaveBeenCalledTimes(1));
  expect(vi.mocked(openRouteSheetPrint).mock.calls[0][1]).toEqual({ serial: "17", batch: "П-5", qty: "10" });
});
