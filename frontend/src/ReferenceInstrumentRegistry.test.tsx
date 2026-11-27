import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ProductionInstrument, ReferenceInstrumentsResult } from "./api";
import { ReferenceInstrumentRegistry } from "./ReferenceInstrumentRegistry";

const base = "/api/references/instruments";
const instrument: ProductionInstrument = {
  id: "canonical", legacy_unit_id: "legacy-unit", version: 7, inventory_number: "СИ.001", name: "Линейка",
  instrument_type: "Линейный", kind: "Средство измерений", parent_id: null, serial_number: "Л-123",
  manufacturer: "Производитель", doc_designation: "Л-01", quantity: 1, storage_location: "ОТК", warehouse_grn: "",
  verification_number: null, verified_at: null, verification_due: null, status: "Поверка не подтверждена",
  files: [], custom_fields: { Полка: "Ячейка 2", Источник: "Старый реестр" },
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Request = { path: string; method: string; body: Record<string, unknown> };
function setup(options: Partial<ReferenceInstrumentsResult> = {}, handle?: (request: Request) => Response | null) {
  let result: ReferenceInstrumentsResult = {
    instruments: [instrument], count: 1, quantity: 1, custom_columns: ["Полка"],
    can_edit_identity: false, can_edit_custom: true, ...options,
  };
  const requests: Request[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const request = { path: String(input), method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : {} };
    requests.push(request);
    const custom = handle?.(request);
    if (custom) return custom;
    if (request.path === base) return json(result);
    if (request.path.endsWith("/history")) return json({ instrument_id: instrument.id, events: [] });
    if (request.path === `${base}/legacy-unit` || request.path === `${base}/canonical`) {
      if (request.method === "PATCH") {
        const saved = { ...result.instruments[0], ...request.body, version: result.instruments[0].version + 1 } as ProductionInstrument;
        result = { ...result, instruments: [saved] };
        return json({ instrument: saved });
      }
      return json({ instrument: result.instruments[0] });
    }
    if (request.path === "/api/references/tech-operations/columns/remove") {
      result = { ...result, custom_columns: [], instruments: result.instruments.map((row) => ({ ...row, custom_fields: {} })) };
      return json({ tab: "ob_measuring", name: "Полка", records: 1 });
    }
    throw new Error(`Unexpected request: ${request.method} ${request.path}`);
  });
  const onCount = vi.fn();
  const rendered = render(<ReferenceInstrumentRegistry onCount={onCount} />);
  return { requests, onCount, ...rendered };
}

// With the ОТК edit right the card is the full instrument window: it saves through the production endpoint
// and reads its RF/matings/VNA blocks (ОТК-1: the registry tab moved here, the window kept every ОТК function).
const production = "/api/production/instruments";
function productionWindow(request: Request): Response | null {
  if (request.path.endsWith("/rf-interfaces")) return json({ rf: { version: 0, interfaces: [], role: "unknown", confirmed: false, source: "", f_min_ghz: null, max_points: null, kit_definition: "" } });
  if (request.path.endsWith("/matings")) return json({ instrument_id: "x", resource: null, status: "unknown", has_connectors: false, ends: [] });
  if (request.path.endsWith("/vna-checks")) return json({ instrument_id: "x", applicable: false, due: [], records: [] });
  if (request.path === `${production}/canonical` && request.method === "PATCH") {
    return json({ instrument: { ...instrument, ...request.body, version: 8 } });
  }
  return null;
}

afterEach(() => {
  cleanup(); vi.restoreAllMocks(); window.localStorage.clear(); window.history.replaceState({}, "", "/");
});

it("lists all 118 canonical records without collapsing repeated models and counts their quantity", async () => {
  const rows = Array.from({ length: 118 }, (_, index) => ({
    ...instrument, id: `canonical-${index}`, legacy_unit_id: index < 103 ? `legacy-${index}` : null,
    inventory_number: `СИ.${String(index + 1).padStart(3, "0")}`, quantity: index === 0 ? 2 : 1,
  }));
  const { onCount, requests } = setup({ instruments: rows, count: 118, quantity: "119" });
  expect(await screen.findByText("118 записей · 119 шт.")).toBeInTheDocument();
  expect(screen.getAllByRole("row")).toHaveLength(119);
  expect(screen.getByText("СИ.118")).toBeInTheDocument();
  expect(screen.getAllByText("Линейка", { selector: "td .instrument-name" })).toHaveLength(118);
  expect(onCount).toHaveBeenCalledWith(118, 119);
  expect(screen.getByRole("button", { name: "+ Столбец" })).toBeEnabled();
  expect(screen.getByRole("button", { name: /Колонки/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Excel" })).toBeInTheDocument();
  expect(requests.every((request) => !request.path.startsWith("/api/production"))).toBe(true);
});

it.each([
  ["equipment", "legacy-unit"], ["instrument", "canonical"], ["si", "СИ.001"],
])("opens the canonical card from a %s deep link and clears it on close", async (parameter, value) => {
  window.history.replaceState({}, "", `/?section=references&tab=ob_measuring&${parameter}=${encodeURIComponent(value)}`);
  const { requests } = setup();
  const dialog = await screen.findByRole("dialog", { name: "Средство измерений: Линейка" });
  expect(within(dialog).getByLabelText("Инв. №")).toHaveValue("СИ.001");
  expect(new URLSearchParams(window.location.search).get("equipment")).toBe("canonical");
  if (parameter !== "si") expect(requests.some((request) => request.path === `${base}/${value}`)).toBe(true);
  expect(new URLSearchParams(window.location.search).has("instrument")).toBe(false);
  expect(new URLSearchParams(window.location.search).has("si")).toBe(false);
  fireEvent.click(within(dialog).getByRole("button", { name: "Закрыть" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(new URLSearchParams(window.location.search).has("equipment")).toBe(false);
  expect(new URLSearchParams(window.location.search).has("instrument")).toBe(false);
  expect(new URLSearchParams(window.location.search).has("si")).toBe(false);
});

it("disables passport editing for a reference editor while retaining editable custom values", async () => {
  setup();
  fireEvent.click(await screen.findByText("СИ.001"));
  const dialog = screen.getByRole("dialog", { name: "Средство измерений: Линейка" });
  expect(within(dialog).getByLabelText("Наименование")).toBeDisabled();
  expect(within(dialog).getByLabelText("Статус")).toBeDisabled();
  expect(within(dialog).getByText(/прав.*ОТК/)).toBeInTheDocument();
  expect(within(dialog).getByLabelText("Полка")).toBeEnabled();
});

it("keeps all editing disabled with visible reasons when both capabilities are absent", async () => {
  setup({ can_edit_custom: false, can_edit_identity: false });
  expect(await screen.findByRole("button", { name: "+ Столбец" })).toBeDisabled();
  fireEvent.click(await screen.findByText("СИ.001"));
  const dialog = screen.getByRole("dialog", { name: "Средство измерений: Линейка" });
  expect(within(dialog).getByLabelText("Наименование")).toBeDisabled();
  expect(within(dialog).getByLabelText("Полка")).toBeDisabled();
  expect(screen.getByText(/Столбцы и их значения доступны для просмотра/)).toBeInTheDocument();
});

it("removes only its own measuring column through the column menu", async () => {
  const { requests } = setup();
  await screen.findByText("СИ.001");
  fireEvent.click(screen.getByRole("button", { name: /Колонки/ }));
  fireEvent.click(screen.getByRole("button", { name: "Удалить столбец: Полка" }));
  const dialog = screen.getByRole("dialog", { name: "Удалить столбец" });
  fireEvent.click(within(dialog).getByRole("button", { name: /Удалить столбец и 1 знач./ }));
  await waitFor(() => expect(screen.queryByRole("columnheader", { name: "Полка" })).not.toBeInTheDocument());
  expect(requests.find((request) => request.path.endsWith("/columns/remove"))?.body).toEqual({ tab: "ob_measuring", name: "Полка" });
  expect(requests.some((request) => request.method === "DELETE")).toBe(false);
});

it("offers status filters only for states present in the registry", async () => {
  setup({ instruments: [instrument, { ...instrument, id: "unused", inventory_number: "СИ.002", status: "Не используется" }] });
  await screen.findByText("СИ.002");
  expect(screen.queryByRole("button", { name: /^На поверке/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Списаны/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /^Не используется/ }));
  expect(screen.queryByText("СИ.001")).not.toBeInTheDocument();
  expect(screen.getByText("СИ.002")).toBeInTheDocument();
});

it("saves only custom values with the canonical version and preserves non-owned metadata", async () => {
  const { requests } = setup();
  fireEvent.click(await screen.findByText("СИ.001"));
  const dialog = screen.getByRole("dialog", { name: "Средство измерений: Линейка" });
  fireEvent.change(within(dialog).getByLabelText("Полка"), { target: { value: "Ячейка 3" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));
  await screen.findByText("Запись сохранена");
  expect(requests.filter((request) => request.method === "PATCH")).toEqual([{
    path: `${base}/canonical`, method: "PATCH", body: {
      version: 7, custom_fields: { Полка: "Ячейка 3", Источник: "Старый реестр" },
    },
  }]);
});

it("explains version conflicts, retains the draft, and refreshes only through the scoped item endpoint", async () => {
  const { requests } = setup({}, (request) => request.method === "PATCH" ? json({ detail: "Version conflict" }, 409) : null);
  fireEvent.click(await screen.findByText("СИ.001"));
  const dialog = screen.getByRole("dialog", { name: "Средство измерений: Линейка" });
  fireEvent.change(within(dialog).getByLabelText("Полка"), { target: { value: "Ячейка 3" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("Запись изменил другой пользователь — обновите");
  expect(within(dialog).getByLabelText("Полка")).toHaveValue("Ячейка 3");
  expect(within(dialog).getByRole("button", { name: "Сохранить" })).toBeDisabled();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(within(dialog).getByRole("button", { name: "Обновить запись" }));
  await waitFor(() => expect(within(dialog).getByLabelText("Полка")).toHaveValue("Ячейка 2"));
  expect(requests.some((request) => request.path === `${base}/canonical` && request.method === "GET")).toBe(true);
  expect(requests.every((request) => !request.path.startsWith("/api/production"))).toBe(true);
});

it("updates a stale address to the measuring tab when opening a canonical row", async () => {
  window.history.replaceState({}, "", "/?section=references&tab=ter");
  setup();
  fireEvent.click(await screen.findByText("СИ.001"));
  expect(screen.getByRole("dialog", { name: "Средство измерений: Линейка" })).toBeInTheDocument();
  const query = new URLSearchParams(window.location.search);
  expect(query.get("section")).toBe("references");
  expect(query.get("tab")).toBe("ob_measuring");
  expect(query.get("equipment")).toBe("canonical");
});

it.each([false, true])("shows article, measurement category and notes with identity editing capability=%s", async (editable) => {
  const detailed = { ...instrument, article: "Л-150", measurement_category: "Линейные измерения", notes: "Беречь рабочую поверхность" };
  const { requests } = setup({ instruments: [detailed], can_edit_identity: editable }, editable ? productionWindow : undefined);
  fireEvent.click(await screen.findByText("СИ.001"));
  const dialog = screen.getByRole("dialog", { name: "Средство измерений: Линейка" });
  const fields = [
    ["Артикул", "Л-150", "Л-300"],
    ["Категория измерений", "Линейные измерения", "Контроль размеров"],
    ["Примечание", "Беречь рабочую поверхность", "Хранить в футляре"],
  ];
  for (const [label, original, changed] of fields) {
    const input = within(dialog).getByLabelText(label);
    expect(input).toHaveValue(original);
    if (editable) {
      expect(input).toBeEnabled();
      fireEvent.change(input, { target: { value: changed } });
    } else expect(input).toBeDisabled();
  }
  if (editable) {
    fireEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));
    await within(dialog).findByText("Запись сохранена");
    expect(requests.filter((request) => request.method === "PATCH")).toEqual([{
      path: `${production}/canonical`, method: "PATCH", body: {
        version: 7, article: "Л-300", measurement_category: "Контроль размеров", notes: "Хранить в футляре",
      },
    }]);
  }
});

it("reads files and combined legacy/canonical history through reference-scoped URLs", async () => {
  const withFile: ProductionInstrument = { ...instrument, files: [{
    id: "passport", kind: "Паспорт", file_name: "Паспорт линейки.pdf", size: 120,
    uploaded_at: "2026-11-20T12:00:00Z", uploaded_by_name: "Ольга",
  }] };
  const { requests } = setup({ instruments: [withFile] }, (request) => request.path === `${base}/canonical/history`
    ? json({ instrument_id: instrument.id, events: [
      { id: "old-event", aggregate_id: "legacy-unit", event_type: "ReferenceRecordEdited", actor_name: "Иван",
        occurred_at: "2026-11-19T12:00:00Z", details: { field: "Артикул" } },
      { id: "new-event", aggregate_id: "canonical", event_type: "InstrumentUpdated", actor_name: "Ольга",
        occurred_at: "2026-11-20T12:00:00Z", details: {} },
    ] }) : null);
  fireEvent.click(await screen.findByText("СИ.001"));
  const dialog = screen.getByRole("dialog", { name: "Средство измерений: Линейка" });
  expect(within(dialog).getByRole("link", { name: "Паспорт линейки.pdf" }))
    .toHaveAttribute("href", `${base}/canonical/files/passport`);
  fireEvent.click(within(dialog).getByRole("button", { name: "Показать историю" }));
  await within(dialog).findByText(/Иван · Изменение записи/);
  expect(within(dialog).getByText(/Ольга · Изменение записи/)).toBeInTheDocument();
  expect(within(dialog).queryByText(/ReferenceRecordEdited|InstrumentUpdated/)).not.toBeInTheDocument();
  expect(requests.some((request) => request.path === `${base}/canonical/history`)).toBe(true);
  expect(requests.every((request) => !request.path.startsWith("/api/production"))).toBe(true);
});

it("saves a custom-only change without revalidating an incomplete imported passport even with identity rights", async () => {
  const imported = { ...instrument, instrument_type: "" };
  const { requests } = setup({ instruments: [imported], can_edit_identity: true }, productionWindow);
  fireEvent.click(await screen.findByText("СИ.001"));
  const dialog = screen.getByRole("dialog", { name: "Средство измерений: Линейка" });
  expect(within(dialog).getByLabelText("Тип")).toHaveValue("");
  fireEvent.change(within(dialog).getByLabelText("Полка"), { target: { value: "Ячейка 3" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Сохранить" }));
  await within(dialog).findByText("Запись сохранена");
  expect(requests.filter((request) => request.method === "PATCH")).toEqual([{
    path: `${production}/canonical`, method: "PATCH", body: {
      version: 7, custom_fields: { Полка: "Ячейка 3", Источник: "Старый реестр" },
    },
  }]);
});

it("offers adding and label printing only to who edits the passport («ОТК»), and keeps the view toggle for everyone", async () => {
  const view = setup({ can_edit_identity: false });
  await screen.findByText("СИ.001");
  expect(screen.queryByRole("button", { name: "+ Средство измерений" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "+ Общая принадлежность" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Печать этикеток" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "По приборам" })).toBeInTheDocument();
  expect(screen.getByText("просрочена")).toBeInTheDocument();
  view.unmount();
  setup({ can_edit_identity: true });
  await screen.findByText("СИ.001");
  expect(screen.getByRole("button", { name: "+ Средство измерений" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "+ Общая принадлежность" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Печать этикеток" })).toBeInTheDocument();
});
