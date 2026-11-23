import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { productionApi, type TaskMachineAction } from "./api";
import { manualJobsApi } from "./manualJobsApi";
import { machinesApi, type MachineRow, type MachineTaskContext } from "./machinesApi";
import { MachinesPage } from "./MachinesPage";
import type { ReactNode } from "react";

// These tests cover the route boundary; each real card's source selection has its own suite.
vi.mock("./Ob019Card", () => ({ Ob019Card: ({ incoming, actions, close }: { incoming?: MachineTaskContext; actions?: ReactNode; close: () => void }) =>
  <div role="dialog" aria-label="Живая карточка"><span>{incoming?.action.card_kind}</span><span>{JSON.stringify(incoming)}</span>{actions}<button onClick={close}>Закрыть карточку</button></div> }));
vi.mock("./MarkingMachineCard", () => ({ MarkingMachineCard: ({ incoming, openJournal }: { incoming?: MachineTaskContext; openJournal: () => void }) =>
  <div role="dialog" aria-label="Карточка маркировки">{JSON.stringify(incoming)}<button onClick={openJournal}>Журнал</button></div> }));
vi.mock("./MachineWorkCard", () => ({ MachineWorkCard: ({ incoming, openJournal }: { incoming?: MachineTaskContext; openJournal: () => void }) =>
  <div role="dialog" aria-label="Рабочая карточка">{JSON.stringify(incoming)}<button onClick={openJournal}>Журнал</button></div> }));

const ROW: MachineRow = { key: "machine:unit", kind: "machine", equipment_id: "unit", inventory: "ОБ.019", name: "Резка", pc: "", ip: "",
  service: "", version: "", link: { code: "ok", text: "На связи", tone: "ok" }, last_seen: null, device: "", state: { text: "Готов", tone: "ok" },
  card: true, others: "", article: "", site: "ЦАР", operations: ["Резка"], family: "Резка", controlled: true };
const ACTION: TaskMachineAction = { etk_position: 2, equipment_id: "unit", inventory_number: "ОБ.019", display_name: "Резка", model: null,
  card_kind: "ob019", connectivity: { state: "ok", fresh: true, last_seen: null }, open_permitted: true, load_permitted: true,
  can_open: true, can_load: true, blocker: null, source: { kind: "ob019", group_id: "validated-group", version: 7, length_mm: "325", members: [] } };
const route = (query = "equipment=unit&task=t1&operation=o1&etk_position=2&load=1") => window.history.replaceState({}, "", `/?section=machines&${query}`);

beforeEach(() => {
  route();
  vi.spyOn(machinesApi, "list").mockResolvedValue({ now: "", rows: [ROW] });
  vi.spyOn(productionApi, "operationMachineActions").mockResolvedValue({ task_id: "t1", operation_id: "o1", task_version: 3, actions: [ACTION] });
  vi.spyOn(manualJobsApi, "list").mockResolvedValue({ date_from: "", date_to: "", rows: [], can_edit: false, waiting_count: 0 });
  vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Unexpected request"));
});
afterEach(() => { vi.restoreAllMocks(); window.history.replaceState({}, "", "/"); });

it.each([
  ["ob019", ROW, ACTION, "Живая карточка"],
  ["ob099", ROW, { ...ACTION, card_kind: "ob099", source: null, can_load: false }, "Живая карточка"],
  ["marking", { ...ROW, card: false, marking: true }, { ...ACTION, card_kind: "marking", source: { kind: "marking", source: { group_id: "g1", version: 1 }, selected: ["t1:1:0"] } }, "Карточка маркировки"],
  ["generic", { ...ROW, card: false, controlled: false }, { ...ACTION, card_kind: "generic", source: { kind: "generic", task_id: "t1", operation_id: "o1", etk_position: 2, queue_kind: "machine" } }, "Рабочая карточка"],
] satisfies [string, MachineRow, TaskMachineAction, string][])("resolves %s only from machine-actions, never from adapter values supplied in URL", async (_kind, row, action, label) => {
  route("equipment=unit&task=t1&operation=o1&etk_position=2&load=1&group_id=forged&length=999");
  vi.mocked(machinesApi.list).mockResolvedValue({ now: "", rows: [row] });
  vi.mocked(productionApi.operationMachineActions).mockResolvedValue({ task_id: "t1", operation_id: "o1", task_version: 3, actions: [action] });
  render(<MachinesPage />);
  const card = await screen.findByRole("dialog", { name: label });
  expect(card).toHaveTextContent(JSON.stringify({ taskId: "t1", operationId: "o1", action, loadRequested: true }));
  expect(card).not.toHaveTextContent("forged");
  expect(productionApi.operationMachineActions).toHaveBeenCalledWith("t1", "o1");
  expect(fetch).not.toHaveBeenCalled();
});

it.each([
  ["equipment=unit&task=t1", "неполная"],
  ["equipment=unit&task=t1&operation=o1&etk_position=4", "изменились"],
  ["equipment=unknown&task=t1&operation=o1&etk_position=2", "изменились"],
])("bad route %s shows explicit unresolved source and opens no other machine", async (query, message) => {
  route(query);
  render(<MachinesPage />);
  expect(await screen.findByRole("alert")).toHaveTextContent(message);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});

it("stale or ambiguous machine-actions never fall back to the first action", async () => {
  vi.mocked(productionApi.operationMachineActions).mockResolvedValue({ task_id: "t1", operation_id: "o1", task_version: 3,
    actions: [{ ...ACTION, source: null, blocker: "Группа закрыта" }] });
  render(<MachinesPage />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Группа закрыта");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("Back switches to the route source, close clears it, reload resolves it again without writes", async () => {
  const first = render(<MachinesPage />);
  const card = await screen.findByRole("dialog", { name: "Живая карточка" });
  fireEvent.click(within(card).getByRole("button", { name: "Закрыть карточку" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(new URLSearchParams(window.location.search).has("task")).toBe(false);
  await act(async () => { route(); window.dispatchEvent(new PopStateEvent("popstate")); });
  expect(await screen.findByRole("dialog", { name: "Живая карточка" })).toHaveTextContent("validated-group");
  first.unmount();
  render(<MachinesPage />);
  expect(await screen.findByRole("dialog", { name: "Живая карточка" })).toHaveTextContent("validated-group");
  expect(fetch).not.toHaveBeenCalled();
});

it("full journal is available under equipment and card journal is scoped in a window", async () => {
  route("view=journal");
  const first = render(<MachinesPage />);
  expect((await screen.findAllByRole("region", { name: "Журнал работ" }))[0]).toBeVisible();
  await waitFor(() => expect(manualJobsApi.list).toHaveBeenCalledWith(expect.any(String), expect.any(String), "", expect.any(AbortSignal), false));
  first.unmount();
  route();
  render(<MachinesPage />);
  const card = await screen.findByRole("dialog", { name: "Живая карточка" });
  fireEvent.click(within(card).getByRole("button", { name: "Журнал" }));
  const journal = await screen.findByRole("dialog", { name: /Журнал · ОБ.019/ });
  expect(within(journal).getAllByRole("region", { name: "Журнал работ" })[0]).toBeVisible();
  await waitFor(() => expect(manualJobsApi.list).toHaveBeenCalledWith(expect.any(String), expect.any(String), "unit", expect.any(AbortSignal), false));
  expect(new URLSearchParams(window.location.search).get("task")).toBe("t1");
  expect(fetch).not.toHaveBeenCalled();
});

it("a pending outside-task lookup cannot reopen an old machine window after navigating to the journal", async () => {
  let finish!: (result: Awaited<ReturnType<typeof manualJobsApi.list>>) => void;
  vi.mocked(manualJobsApi.list).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  render(<MachinesPage />);
  const card = await screen.findByRole("dialog", { name: "Живая карточка" });
  fireEvent.click(within(card).getByRole("button", { name: "Ручная работа" }));
  fireEvent.click(screen.getByRole("tab", { name: "Журнал работ" }));
  await act(async () => { finish({ date_from: "", date_to: "", rows: [], can_edit: false }); });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getAllByRole("region", { name: "Журнал работ" })[0]).toBeVisible();
});
