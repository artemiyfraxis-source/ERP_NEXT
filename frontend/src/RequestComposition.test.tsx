import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RequestComposition, safeCompositionUrl } from "./RequestComposition";
import { plannedQuantity, SuggestPanel } from "./RfqPool";
import { requestsViewApi, type CompositionRow, type RqLine } from "./procurementRequestsApi";
import { requestsApi } from "./api";
import { rfqApi } from "./purchasingRfqApi";
import { ProcurementRequests } from "./ProcurementRequests";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });
const source = (over: Partial<CompositionRow> = {}): CompositionRow => ({ source_row: 7, name: "Разъём ABC", item_type: "Разъём", unit: "шт", quantity: "10", price: "5", price_with_vat: "6.1", link: "https://example.test/part", country: "", issues: [], relation: "unlinked", request_id: null, procurement_ids: [], lots: [{ id: "lot", grn: "TEST.1", package: "1", warehouse: "ЦАР", balance: "8", reserved: "3", free: "5", match_state: "exact" }], ...over });
const purchase = (over: Partial<RqLine> = {}): RqLine => ({ id: "p1", name: "Разъём ABC", legacy_id: "SM-TEST", version: 2, quantity: "10", to_buy: "7", unit: "шт", rb_status: "new", selectable: true, ...over } as RqLine);
function setup(rows = [source()], lines: RqLine[] = []) {
  vi.spyOn(requestsApi, "schema").mockResolvedValue({ purposes: ["На проект"], delivery_points: ["ЦАР"] } as never);
  vi.spyOn(requestsViewApi, "composition").mockResolvedValue({ project: "TEST.1", state: "saved", snapshot_id: "snapshot", version: 2, source: { url: "https://docs.google.com/test", imported_at: "2026-11-03", content_hash: "hash" }, rows });
  vi.spyOn(requestsViewApi, "view").mockResolvedValue({ batches: [{ key: "batch" }] } as never);
  vi.spyOn(requestsViewApi, "batch").mockResolvedValue({ lines } as never);
  return render(<RequestComposition project="TEST.1" projectKey="project:TEST.1" scope="mine" canAct onSaved={vi.fn()} />);
}

describe("full saved ZKP", () => {
  it("renders full source, exact linked status and manual lines once with stock provenance", async () => {
    const materialize = vi.spyOn(requestsViewApi, "materialize");
    setup([source({ relation: "recorded", request_id: "r", procurement_ids: ["p1"] }), source({ source_row: 8, name: "Провод" })], [purchase(), purchase({ id: "manual", name: "Ручная позиция" })]);
    await screen.findByRole("table", { name: "Позиции ЗКП и заявки" });
    expect(screen.getAllByText("Разъём ABC")).toHaveLength(1);
    expect(screen.getByText("Провод")).toBeTruthy(); expect(screen.getByText("Ручная позиция")).toBeTruthy();
    expect(screen.getAllByRole("link", { name: "TEST.1 · 1" })[0]).toHaveAttribute("href", "?section=stock&q=TEST.1");
    expect(screen.getAllByText(/резерв 3/)[0]).toHaveTextContent("свободно 5");
    expect(materialize).not.toHaveBeenCalled();
  });
  it("blocks invalid/stale source, never exposes unsafe hrefs", async () => {
    setup([source({ issues: ["unit"], link: "javascript:alert(1)" }), source({ source_row: 8, name: "Изменено", relation: "source_changed" })]);
    expect(await screen.findByRole("checkbox", { name: "Выбрать Разъём ABC" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Выбрать Изменено" })).toBeDisabled();
    expect(screen.getAllByRole("link", { name: /Описание/ })).toHaveLength(1);
    expect(document.querySelector('a[href^="javascript:"]')).toBeNull();
    expect(safeCompositionUrl("data:text/html,x")).toBeNull(); expect(safeCompositionUrl("https://example.test")).toBe("https://example.test/");
  });
  it("keeps hidden selection and preserves it when materialization conflicts", async () => {
    setup(); const materialize = vi.spyOn(requestsViewApi, "materialize").mockRejectedValue(new Error("Источник изменён"));
    fireEvent.click(await screen.findByRole("checkbox", { name: "Выбрать Разъём ABC" }));
    fireEvent.change(screen.getByLabelText("Назначение"), { target: { value: "На проект" } });
    fireEvent.change(screen.getByLabelText("Куда доставить"), { target: { value: "ЦАР" } });
    fireEvent.change(screen.getByLabelText("Закупить к"), { target: { value: "2026-11-04" } });
    fireEvent.change(screen.getByLabelText("Нужно на складе к"), { target: { value: "2026-11-05" } });
    fireEvent.change(screen.getByLabelText("Поиск в ЗКП"), { target: { value: "скрыто" } });
    fireEvent.click(screen.getByRole("button", { name: "Создать выбранные заявки и запросить КП" }));
    await waitFor(() => expect(materialize).toHaveBeenCalledWith(expect.objectContaining({ source_rows: [7], expected_version: 2, content_hash: "hash" })));
    await screen.findByRole("alert"); expect(screen.getByText("Выбрано: 1")).toBeTruthy();
  });
  it("shows a missing snapshot honestly without fetching Google", async () => {
    setup(); vi.mocked(requestsViewApi.composition).mockResolvedValue({ project: "TEST.1", state: "missing", version: 0, source: null, rows: [] });
    cleanup(); render(<RequestComposition project="TEST.1" projectKey="project:TEST.1" scope="mine" canAct={false} onSaved={vi.fn()} />);
    await screen.findByText(/Полный ЗКП не сохранён/);
    expect(screen.queryByRole("table", { name: "Позиции ЗКП и заявки" })).toBeNull();
  });
  it("does not mislabel another buyer's linked purchase as not handed over", async () => {
    setup([source({ request_id: "r", procurement_ids: ["other"], relation: "recorded" })]);
    await screen.findByText("Передана в закупку · смотрите «Все»");
    expect(screen.getByRole("checkbox", { name: "Выбрать Разъём ABC" })).toBeDisabled();
  });
  it("refreshes source relations with purchases after handoff without duplicate manual row", async () => {
    setup([source({ request_id: "r", relation: "recorded" })]); cleanup();
    const saved = vi.fn();
    const rendered = render(<RequestComposition project="TEST.1" projectKey="project:TEST.1" scope="mine" canAct onSaved={saved} refreshKey={0} />);
    await screen.findByText("Заявка создана, не передана");
    const initial = await requestsViewApi.composition("TEST.1");
    vi.mocked(requestsViewApi.composition).mockResolvedValue({ ...initial, rows: [source({ request_id: "r", relation: "recorded", procurement_ids: ["p1"] })] });
    vi.mocked(requestsViewApi.batch).mockResolvedValue({ lines: [purchase()] } as never);
    rendered.rerender(<RequestComposition project="TEST.1" projectKey="project:TEST.1" scope="mine" canAct onSaved={saved} refreshKey={1} />);
    await screen.findByText("SM-TEST");
    expect(screen.getAllByText("Разъём ABC")).toHaveLength(1);
    expect(screen.queryByText(/Вне ЗКП/)).toBeNull();
    expect(screen.queryByText("Заявка создана, не передана")).toBeNull();
  });
  it("keeps unhanded manual requests and suppresses contradictory empty state in the parent", async () => {
    setup(); cleanup();
    window.history.replaceState(null, "", "/?project=project:TEST.1");
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ projects: [], internal_projects: [] }), { headers: { "Content-Type": "application/json" } })));
    vi.mocked(requestsViewApi.view).mockResolvedValue({ scope: "mine", projects: [{ key: "project:TEST.1", kind: "project", value: "TEST.1", counts: {}, lines: 0 }], batches: [], batches_total: 0, focus: null });
    vi.spyOn(requestsApi, "list").mockResolvedValue({ requests: [{ id: "manual-new", project_reference: "TEST.1", name: "Ручная новая", status: "Новый" }] } as never);
    render(<ProcurementRequests canHandoff />);
    await screen.findByRole("table", { name: "Позиции ЗКП и заявки" });
    expect(screen.getByText("Не переданы в закупку")).toBeTruthy();
    expect(screen.getByRole("button", { name: "В закупку" })).toBeTruthy();
    expect(screen.queryByText(/Ваших позиций и неразобранных новых нет/)).toBeNull();
  });
});

describe("planned purchase quantity", () => {
  it("validates exact decimal bounds without changing demand", () => {
    expect(plannedQuantity("2,125")).toBe("2.125");
    for (const value of ["0", "-1", "1e3", "1000000000000", "1.0000001", "NaN"]) expect(plannedQuantity(value)).toBeNull();
  });
  it("sends planned quantity and CAS version; preserves edit after failure", async () => {
    vi.spyOn(rfqApi, "suggestForLines").mockResolvedValue({ suggestions: [], gate_min_quotes: 1 } as never);
    vi.spyOn(rfqApi, "directory").mockResolvedValue({ suppliers: [] });
    const create = vi.spyOn(rfqApi, "create").mockRejectedValue(new Error("Версия изменена"));
    render(<SuggestPanel lineIds={["p1"]} lines={[{ id: "p1", name: "Разъём", legacy_id: "SM", version: 2, quantity: "7", demand: "10", unit: "шт" }]} onBack={vi.fn()} onCreated={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Планируем купить Разъём"), { target: { value: "12,5" } });
    expect(screen.getByText("Больше потребности проекта")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Поставщик по названию"), { target: { value: "Поставщик" } });
    fireEvent.click(screen.getByRole("button", { name: "+ поставщик по названию" }));
    fireEvent.click(screen.getByRole("button", { name: "Создать запрос" }));
    await waitFor(() => expect(create).toHaveBeenCalledWith(["p1"], "", expect.any(Array), expect.objectContaining({ planned_lines: [{ id: "p1", expected_version: 2, planned_quantity: "12.5" }] })));
    expect(screen.getByLabelText("Планируем купить Разъём")).toHaveValue("12,5");
  });
});
