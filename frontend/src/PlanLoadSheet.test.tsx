import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { identityApi, type ProductionPlanRow } from "./api";
import { PlanLoadSheet } from "./PlanLoadSheet";
import { forgetPersonalViews, SAVE_DELAY, setPersonalViewsUser } from "./personalViews";

const view = "production-plan.load";
const storageKey = (user = "a") => `erp-zg-columns:user:${user}`;
const row: ProductionPlanRow = {
  project: "708К.4", customer: "Заказчик", opened_on: null, deadline: "2026-11-03", days_left: 1,
  stage: "В работе", stage_manual: false, terminal: false, kits: [], supply: { total: 0, stock: 0, transit: 0, buying: 0, request: 0, customer_supplied: 0, customer_supplied_stock: 0, eta: "" },
  plan: { tasks: 0, assigned: 0, total: 0, start: null, finish: null }, blocker: null, last: null, events: [],
  sheet: { comment: { value: "Примечание", auto: "", manual: true }, test_sb: { value: "да", auto: "нет", manual: true } },
};
const props = {
  userId: "a", rows: [row], from: "2026-11-02", to: "2026-11-04", today: "2026-11-02", holidays: [],
  stages: ["В работе", "Отгружено"], suppliedValues: [], arrivalValues: [], canEdit: true, busy: false,
  onStatusChanged: vi.fn(), onMark: vi.fn(), onDeadline: vi.fn(), onOpenProject: vi.fn(), onOpenJournal: vi.fn(),
};
const headers = () => Array.from(document.querySelectorAll("th.zg-col-head")).map((head) => head.textContent);
const chooser = () => {
  fireEvent.click(screen.getByRole("button", { name: "Колонки" }));
  return within(screen.getByRole("group", { name: "Показывать колонки" }));
};
const settle = async () => { await act(async () => { await Promise.resolve(); }); };
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

beforeEach(() => {
  window.localStorage.clear();
  setPersonalViewsUser("a");
  vi.spyOn(identityApi, "uiSettings").mockResolvedValue({ views: {} });
  vi.spyOn(identityApi, "saveUiSetting").mockImplementation(async (view, value) => ({ view, value }));
});
afterEach(() => { cleanup(); forgetPersonalViews(); vi.restoreAllMocks(); vi.useRealTimers(); window.localStorage.clear(); });

it("uses the stock chooser; hides and reorders headers, cells, widths and frozen offsets, then restores from the server on another device", async () => {
  vi.useFakeTimers();
  const mounted = render(<PlanLoadSheet {...props} />);
  await settle();
  const menu = chooser();
  expect(screen.getByRole("group", { name: "Показывать колонки" })).toHaveClass("column-popover");
  expect(menu.getByLabelText("Проект")).toBeDisabled();
  fireEvent.click(menu.getByLabelText("Рабочих дней до дедлайна"));
  const up = menu.getByRole("button", { name: "Выше: Дедлайн" });
  up.focus();
  fireEvent.click(up);
  expect(up).toHaveFocus();
  const comment = menu.getByLabelText("Комментарий").closest("li")!;
  const customer = menu.getByLabelText("Заказчик").closest("li")!;
  const dataTransfer = { effectAllowed: "", setData: vi.fn() };
  fireEvent.dragStart(comment.querySelector("[draggable]")!, { dataTransfer });
  fireEvent.dragOver(customer, { dataTransfer });
  fireEvent.drop(customer, { dataTransfer });
  expect(headers().slice(0, 4)).toEqual(["Проект", "Комментарий", "Заказчик", "Статус"]);
  expect(headers().indexOf("Дедлайн")).toBeLessThan(headers().indexOf("Тестовая сборка"));
  const cells = Array.from(document.querySelectorAll<HTMLTableCellElement>("tr[data-project] > td.zg-freeze"));
  expect(cells.slice(0, 3).map((cell) => cell.textContent)).toEqual(["708К.4", "Примечание", "Заказчик"]);
  const widths = Array.from(document.querySelectorAll<HTMLTableColElement>(".zg-table colgroup col")).slice(0, cells.length).map((col) => parseFloat(col.style.width));
  let left = 0;
  for (const [index, cell] of cells.entries()) {
    expect(cell.style.getPropertyValue("--zg-left")).toBe(`${left}px`);
    expect(document.querySelectorAll<HTMLElement>("th.zg-col-head")[index].style.getPropertyValue("--zg-left")).toBe(`${left}px`);
    left += widths[index];
  }
  expect(document.querySelectorAll("th.zg-day-head")).toHaveLength(3);
  expect(cells.at(-1)).toHaveClass("zg-edge");
  expect(document.querySelector(".zg-col-test_sb button")).toHaveAttribute("aria-checked", "true");
  expect(document.querySelector(".zg-col-test_sb")).toHaveClass("zg-manual");
  await act(() => vi.advanceTimersByTimeAsync(SAVE_DELAY));
  const saved = JSON.parse(window.localStorage.getItem(storageKey())!);
  expect(identityApi.saveUiSetting).toHaveBeenLastCalledWith(view, saved);
  const names = headers();
  mounted.unmount();
  forgetPersonalViews();
  window.localStorage.clear();
  setPersonalViewsUser("a");
  vi.mocked(identityApi.uiSettings).mockResolvedValue({ views: { [view]: saved } });
  render(<PlanLoadSheet {...props} />);
  await settle();
  expect(headers()).toEqual(names);
  expect(JSON.parse(window.localStorage.getItem(storageKey())!)).toEqual(saved);
});

it("keeps a newer local edit when the initial server answer arrives late", async () => {
  const late = deferred<Awaited<ReturnType<typeof identityApi.uiSettings>>>();
  vi.mocked(identityApi.uiSettings).mockReturnValue(late.promise);
  render(<PlanLoadSheet {...props} />);
  fireEvent.click(chooser().getByLabelText("Комментарий"));
  await act(async () => late.resolve({ views: { [view]: { order: [], hidden: ["ready"], autoFit: false } } }));
  expect(headers()).not.toContain("Комментарий");
  expect(headers()).toContain("Готовность, %");
});

it("keeps a saved edit across remount when an older initial GET completes after its PUT", async () => {
  vi.useFakeTimers();
  const late = deferred<Awaited<ReturnType<typeof identityApi.uiSettings>>>();
  vi.mocked(identityApi.uiSettings).mockReturnValue(late.promise);
  const mounted = render(<PlanLoadSheet {...props} />);
  fireEvent.click(chooser().getByLabelText("Комментарий"));
  await act(() => vi.advanceTimersByTimeAsync(SAVE_DELAY));
  expect(identityApi.saveUiSetting).toHaveBeenCalledOnce();
  await act(async () => late.resolve({ views: { [view]: { order: [], hidden: ["ready"], autoFit: false }, untouched: { hidden: ["other"] } } }));
  mounted.unmount();
  render(<PlanLoadSheet {...props} />);
  await settle();
  expect(headers()).not.toContain("Комментарий");
  expect(headers()).toContain("Готовность, %");
  expect(JSON.parse(window.localStorage.getItem(storageKey())!)).toMatchObject({ hidden: ["comment"] });
});

it("isolates accounts immediately and cancels A's pending save and late load before B's settings arrive", async () => {
  vi.useFakeTimers();
  const a = deferred<Awaited<ReturnType<typeof identityApi.uiSettings>>>();
  const b = deferred<Awaited<ReturnType<typeof identityApi.uiSettings>>>();
  vi.mocked(identityApi.uiSettings).mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
  window.localStorage.setItem("erp-zg-columns", JSON.stringify(["customer"]));
  const mounted = render(<PlanLoadSheet {...props} />);
  fireEvent.click(chooser().getByLabelText("Комментарий"));
  setPersonalViewsUser("b");
  mounted.rerender(<PlanLoadSheet {...props} userId="b" />);
  expect(headers()).toContain("Комментарий");
  expect(headers()).toContain("Заказчик");
  await act(async () => a.resolve({ views: { [view]: { order: [], hidden: ["status"], autoFit: false } } }));
  expect(headers()).toContain("Статус");
  await act(async () => b.resolve({ views: { [view]: { order: ["project", "deadline"], hidden: ["customer"], autoFit: false } } }));
  expect(headers().slice(0, 2)).toEqual(["Проект", "Дедлайн"]);
  expect(headers()).not.toContain("Заказчик");
  await act(() => vi.advanceTimersByTimeAsync(SAVE_DELAY));
  expect(identityApi.saveUiSetting).not.toHaveBeenCalled();
  expect(window.localStorage.getItem("erp-zg-columns")).toBe(JSON.stringify(["customer"]));
  expect(JSON.parse(window.localStorage.getItem(storageKey("a"))!)).toMatchObject({ hidden: ["comment"] });
});

it("falls back to this account's local layout during server failure, including after reload, and saves the auto-fit reset intent", async () => {
  vi.useFakeTimers();
  vi.mocked(identityApi.uiSettings).mockRejectedValue(new Error("offline"));
  vi.mocked(identityApi.saveUiSetting).mockRejectedValue(new Error("offline"));
  window.localStorage.setItem(storageKey(), JSON.stringify({ order: ["project", "deadline"], hidden: ["customer"], autoFit: false }));
  const mounted = render(<PlanLoadSheet {...props} />);
  await settle();
  expect(headers().slice(0, 2)).toEqual(["Проект", "Дедлайн"]);
  fireEvent.click(chooser().getByLabelText("Комментарий"));
  await act(() => vi.advanceTimersByTimeAsync(SAVE_DELAY));
  mounted.unmount();
  forgetPersonalViews();
  setPersonalViewsUser("a");
  render(<PlanLoadSheet {...props} />);
  await settle();
  expect(headers()).not.toContain("Комментарий");
  fireEvent.click(chooser().getByRole("button", { name: "По ширине экрана" }));
  expect(headers().slice(0, 3)).toEqual(["Проект", "Заказчик", "Статус"]);
  expect(headers()).toContain("Комментарий");
  await act(() => vi.advanceTimersByTimeAsync(SAVE_DELAY));
  expect(identityApi.saveUiSetting).toHaveBeenLastCalledWith(view, expect.objectContaining({ autoFit: true, hidden: [] }));
});

it("normalizes unknown/duplicate columns, keeps project visible and first, and offers all new columns", async () => {
  vi.mocked(identityApi.uiSettings).mockResolvedValue({ views: { [view]: { order: ["deadline", "deadline", "missing", "project"], hidden: ["project", "customer", "customer", "missing"], autoFit: false } } });
  render(<PlanLoadSheet {...props} />);
  await settle();
  expect(headers().slice(0, 2)).toEqual(["Проект", "Дедлайн"]);
  expect(new Set(headers()).size).toBe(15);
  const menu = chooser();
  expect(menu.getAllByRole("checkbox")).toHaveLength(16);
  expect(menu.getByLabelText("Проект")).toBeChecked();
  expect(menu.getByRole("button", { name: "Выше: Дедлайн" })).toBeDisabled();
  expect(menu.getByLabelText("Комплект проверен · ручная отметка")).toBeChecked();
  expect(menu.getByLabelText("Техкарта выдана · также в статусе")).toBeChecked();
  fireEvent.keyDown(menu.getByLabelText("Заказчик"), { key: "Escape" });
  expect(screen.queryByRole("group", { name: "Показывать колонки" })).toBeNull();
  expect(screen.getByRole("button", { name: "Колонки" })).toHaveFocus();
});

it("works without browser storage and restores the saved reset from the server", async () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
  vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new Error("blocked"); });
  vi.mocked(identityApi.uiSettings).mockResolvedValue({ views: { [view]: { order: [], hidden: [], autoFit: true } } });
  render(<PlanLoadSheet {...props} />);
  await settle();
  const menu = chooser();
  fireEvent.click(menu.getByLabelText("Заказчик"));
  expect(headers()).not.toContain("Заказчик");
  fireEvent.click(menu.getByRole("button", { name: "По ширине экрана" }));
  expect(headers()).toHaveLength(16);
});
