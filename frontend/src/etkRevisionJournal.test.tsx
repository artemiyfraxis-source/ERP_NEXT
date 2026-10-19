import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Principal, ProjectTechcard } from "./api";
import { EtkSheet } from "./ProjectEtk";
import { changeValue, changesByOperation, revisionChanges } from "./etkRevisions";

/* Владелец 19.10: выданная ЭТК не меняется молча — каждая правка ревизия; журнал показывает, кто и почему, а у
   автоматической ревизии — что именно изменилось по операциям; «Нужна проверка технолога» — заметно на карте. */

const principal = { user: { id: "u", email: "t@x.ru", name: "Технолог", department: "" }, roles: [], permissions: { techcards: "view" } } as unknown as Principal;

const DIFF = [
  { operation_key: "CUT_WIRE::1::", label: "Резка с разделкой", field: "Длина зачистки", old: "5", new: "6" },
  { operation_key: "CUT_WIRE::1::", label: "Резка с разделкой", field: "Допуск", old: "", new: "±1 мм" },
  { operation_key: "CRIMP::1::A", label: "Опрессовка терминалов", field: "Высота обжима", old: "1,2", new: "1,3" },
];

const REVISIONS = [
  { id: "r1", revision: 1, reason: "Выдача ЭТК", actor_name: "Технолог", occurred_at: "2026-10-18T09:00:00", source: "manual", diff: null, document: {} },
  { id: "r2", revision: 2, reason: "Изменён справочник обжимов", actor_name: "система", occurred_at: "2026-10-19T02:00:00", source: "auto", diff: DIFF, document: {} },
];

function card(reviewNote: string | null): ProjectTechcard {
  return { id: "t", version: 3, project: "614К.1", item_index: "КБ1", product_name: "Жгут", revision: 2, status: "Выдана",
    manual_operation_keys: [], review_note: reviewNote, document: {
      kit: { part_quantity: "10" },
      operations: [{ position: 1, code: "CUT_WIRE", connector: "1", side: "", type: "cutWire", title: "Резка", template_id: "cut-wire",
        card: { name: "Резка", safety: "", defects: "", steps: [], tooling: [] }, components: [], semis: [], result: [], time: [] }],
    } } as unknown as ProjectTechcard;
}

function mockFetch() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const body = String(input).includes("/revisions") ? { items: REVISIONS } : { items: [] };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

afterEach(() => vi.restoreAllMocks());

it("reads the diff as a list, groups it by operation and shows empty values as a dash", () => {
  expect(revisionChanges(DIFF)).toHaveLength(3);
  expect(revisionChanges({ changes: DIFF })).toHaveLength(3);
  expect(revisionChanges(null)).toEqual([]);
  expect(changesByOperation(revisionChanges(DIFF)).map((group) => [group.label, group.items.length]))
    .toEqual([["Резка с разделкой", 2], ["Опрессовка терминалов", 1]]);
  expect(changeValue("")).toBe("—");
  expect(changeValue(["a", "b"])).toBe("a; b");
});

it("the journal names the author («автоматически» for an auto revision) and opens «Что изменилось» in a window", async () => {
  mockFetch();
  render(<EtkSheet card={card(null)} principal={principal} onChange={() => undefined} />);

  fireEvent.click(await screen.findByText(/Журнал ревизий · 2/));
  const auto = (await screen.findByText(/Изменён справочник обжимов/)).closest("div") as HTMLElement;
  expect(within(auto).getByText(/автоматически/)).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "Что изменилось" })).toHaveLength(1); // the manual revision has no diff

  fireEvent.click(within(auto).getByRole("button", { name: "Что изменилось" }));
  const window = await screen.findByRole("dialog", { name: "Что изменилось в ревизии 2" });
  expect(within(window).getByText("Резка с разделкой")).toBeInTheDocument();
  expect(within(window).getByText("Опрессовка терминалов")).toBeInTheDocument();
  expect(within(window).getByText("Длина зачистки")).toBeInTheDocument();
  expect(within(window).getByText("±1 мм")).toBeInTheDocument();
  expect(within(window).getByText("1,2")).toBeInTheDocument();

  fireEvent.click(within(window).getByRole("button", { name: "Закрыть" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

it("a card with a review note shows the banner; without it there is none", async () => {
  mockFetch();
  const { unmount } = render(<EtkSheet card={card("после правки справочника обжимов проверьте высоту")} principal={principal} onChange={() => undefined} />);
  expect(await screen.findByText("Нужна проверка технолога:")).toBeInTheDocument();
  expect(screen.getByText(/проверьте высоту/)).toBeInTheDocument();
  unmount();
  render(<EtkSheet card={card(null)} principal={principal} onChange={() => undefined} />);
  await screen.findByText(/Журнал ревизий/);
  expect(screen.queryByText("Нужна проверка технолога:")).toBeNull();
});
