import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LaunchReady, spans } from "./LaunchReady";
import type { LaunchReadyList, LaunchReadyRow } from "./launchReadyApi";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

function row(overrides: Partial<LaunchReadyRow>): LaunchReadyRow {
  return {
    techcard_id: "tc-1", techcard_version: 3, techcard_revision: 2, project: "713К.4", index: "КБ1", name: "Жгут НВ4",
    customer: "ООО «ТСР»", run: 500, launched: 200, remainder: 300, done: 0, done_source: "", quantity: 300,
    site: "ЦАР", site_note: "назначено монтажникам ЦАР", kit: "Укомплектован", deadline: "2026-11-10", task_deadline: "2026-11-05",
    warnings: [],
    operations: [
      { position: 1, code: "CUT_WIRE_auto", stage: "Резка провода", description: "", seconds_per_unit: 12, setup_seconds: 0, per_unit: 2, etk_ops: [] },
      { position: 2, code: "PRS_TERMINAL_hand", stage: "Опрессовка терминалов", description: "", seconds_per_unit: 30, setup_seconds: 600, per_unit: 1, etk_ops: [] },
    ],
    ...overrides,
  };
}

const LIST: LaunchReadyList = {
  items: [row({}), row({ techcard_id: "tc-2", project: "776К.1", index: "КБ2", name: "Кабель питания", launched: 0, remainder: 40,
    quantity: 40, run: 40, site: "не распределено", site_note: "", kit: "КБ Выдана", deadline: null, task_deadline: null })],
  waiting_kit: 5,
  no_run: [],
};

function mockApi(list: LaunchReadyList, launch: (body: { items: unknown[] }) => unknown) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path.endsWith("/api/production/launch-ready/launch") && init?.method === "POST") return json(launch(JSON.parse(String(init.body))));
    if (path.endsWith("/api/production/launch-ready")) return json(list);
    return json({});
  });
}

it("shows the strip with counts and opens the ready list", async () => {
  mockApi(LIST, () => ({ created: [], refused: [] }));
  render(<LaunchReady onLaunched={vi.fn()} />);

  expect(await screen.findByText("Готово к запуску: 2 КБ")).toBeTruthy();
  expect(screen.getByText(/ждут комплект: 5/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Открыть список" }));

  const dialog = await screen.findByRole("dialog", { name: "Готово к запуску" });
  expect(within(dialog).getByText("713К.4")).toBeTruthy();
  expect(within(dialog).getByText("776К.1")).toBeTruthy();
  expect(within(dialog).getAllByText("Укомплектован").length).toBeGreaterThan(0);
});

it("confirms one row with its stages, norms and site, then launches it", async () => {
  const launch = vi.fn(() => ({ created: [{ id: "t1", number: "ПЗ-26-0015", project: "713К.4", item: "КБ1", quantity: 250, techcard_id: "tc-1" }], refused: [] }));
  mockApi(LIST, launch);
  const onLaunched = vi.fn();
  render(<LaunchReady onLaunched={onLaunched} />);
  fireEvent.click(await screen.findByRole("button", { name: "Открыть список" }));
  const list = await screen.findByRole("dialog", { name: "Готово к запуску" });
  fireEvent.click(within(list).getAllByRole("button", { name: "Запустить" })[0]);

  const confirm = await screen.findByRole("dialog", { name: "Запуск задания" });
  expect(within(confirm).getByText("713К.4 КБ1 · Жгут НВ4")).toBeTruthy();
  expect(within(confirm).getByText("Резка провода")).toBeTruthy();
  expect(within(confirm).getByText("Опрессовка терминалов")).toBeTruthy();
  expect(within(confirm).getByText("ЦАР")).toBeTruthy();
  // 300 шт: резка 12 с × 2 × 300 = 2 ч; опрессовка 30 с × 300 + 10 мин = 2,7 ч → итого 4,7 ч.
  expect(within(confirm).getByText("4,7")).toBeTruthy();
  const quantity = within(confirm).getByLabelText("Тираж задания 713К.4 КБ1");
  fireEvent.change(quantity, { target: { value: "301" } });
  expect((within(confirm).getByRole("button", { name: /Запустить задание/ }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(quantity, { target: { value: "250" } });
  fireEvent.click(within(confirm).getByRole("button", { name: "Запустить задание · 250 шт" }));

  await waitFor(() => expect(onLaunched).toHaveBeenCalledWith("Запущено 1 задание: ПЗ-26-0015 (713К.4 КБ1, 250 шт)"));
  expect(launch).toHaveBeenCalledWith({ items: [{ techcard_id: "tc-1", techcard_version: 3, launched: 200, quantity: 250 }] });
  expect(screen.queryByRole("dialog", { name: "Запуск задания" })).toBeNull();
});

it("launches the selected rows in one batch and shows a refused one", async () => {
  const launch = vi.fn((_body: { items: unknown[] }) => ({
    created: [{ id: "t1", number: "ПЗ-26-0015", project: "713К.4", item: "КБ1", quantity: 300, techcard_id: "tc-1" }],
    refused: [{ techcard_id: "tc-2", reason: "Техкарта 776К.1 КБ2 перевыпущена — обновите доску" }],
  }));
  mockApi(LIST, launch);
  render(<LaunchReady onLaunched={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Открыть список" }));
  const list = await screen.findByRole("dialog", { name: "Готово к запуску" });
  fireEvent.click(within(list).getByLabelText("Выбрать все показанные"));
  fireEvent.click(await within(list).findByRole("button", { name: "Запустить выбранные" }));

  const confirm = await screen.findByRole("dialog", { name: "Запуск заданий" });
  fireEvent.click(within(confirm).getByRole("button", { name: "Запустить 2 задания" }));

  await waitFor(() => expect(launch).toHaveBeenCalledTimes(1));
  expect(launch.mock.calls[0]?.[0].items).toHaveLength(2);
  expect(await screen.findByText(/Не запущено: 776К.1 КБ2 — Техкарта 776К.1 КБ2 перевыпущена/)).toBeTruthy();
});

it("folds ETK operation numbers into ranges", () => {
  expect(spans([6, 7, 8, 9, 10])).toBe("6–10");
  expect(spans([14, 15])).toBe("14, 15");
  expect(spans([5, 3, 4, 7])).toBe("3–5, 7");
});

it("shows nothing when there is nothing to launch or wait for", async () => {
  const fetch = mockApi({ items: [], waiting_kit: 0, no_run: [] }, () => ({}));
  const { container } = render(<LaunchReady onLaunched={vi.fn()} />);

  await waitFor(() => expect(fetch).toHaveBeenCalled());
  expect(container.textContent).toBe("");
});
