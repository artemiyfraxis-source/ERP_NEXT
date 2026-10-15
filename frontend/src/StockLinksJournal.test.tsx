import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReferencesPage } from "./ReferencesPage";
import { StockLinksJournal } from "./StockLinksJournal";
import type { Principal } from "./api";
import type { StockLinkEntry } from "./stockLinksApi";

const admin: Principal = {
  user: { id: "10000000-0000-0000-0000-000000000001", email: "admin@example.test", name: "Администратор", department: "ERP" },
  roles: ["admin"],
  access_level: "admin",
  permissions: { erpref: "edit", procurement: "edit" },
};

const RECORD = { id: "rec-1", tab: "mat", display_text: "Контакт SXH", source_sheet: "", fields: { "Наименование": "Контакт SXH", "Производитель": "JST" } };
const ENTRY = (over: Partial<StockLinkEntry>): StockLinkEntry => ({
  id: "e1", occurred_at: "2026-10-13T11:30:00", event: "linked", lot_id: "lot-1", warehouse: "ЦАР", grn: "FRX.26.0000042",
  package: "2", article: "SXH-001T-P0.6", name: "Контакт обжимной", record_id: "rec-1", record_tab: "mat",
  record_text: "Контакт SXH", changes: [], actor_name: "Кладовщик Петров", ...over,
});

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); });

it("shows the record's own stock events with who, when, the lot and what changed", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ entries: [
    ENTRY({ id: "e2", event: "updated", occurred_at: "2026-10-13T12:00:00", changes: [{ field: "Производитель", old: "", new: "JST" }] }),
    ENTRY({}),
  ] }));

  render(<StockLinksJournal recordId="rec-1" />);

  const table = await screen.findByRole("table");
  expect(String(fetch.mock.calls[0][0])).toBe("/api/references/stock-links?record_id=rec-1");
  expect(within(table).getByText("Обновлена")).toBeInTheDocument();
  expect(within(table).getByText("Привязана")).toBeInTheDocument();
  expect(within(table).getByText("Производитель: — → JST")).toBeInTheDocument();
  expect(within(table).getByText("запись не менялась")).toBeInTheDocument();
  expect(within(table).getAllByText("FRX.26.0000042")).toHaveLength(2);
  expect(within(table).getByText("13.10.2026 12:00")).toBeInTheDocument();
  expect(within(table).getAllByText("Кладовщик Петров")).toHaveLength(2);
  expect(within(table).queryByText("Запись")).toBeNull(); // the card is that record — no «Запись» column
});

it("the whole journal names the record and its base, a deleted record stays as text", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json({ entries: [
    ENTRY({ event: "created", record_id: null, record_text: "Трубка ТУТ 6/3", record_tab: "mat" }),
  ] }));

  render(<StockLinksJournal />);

  const table = await screen.findByRole("table");
  expect(within(table).getByText("Создана")).toBeInTheDocument();
  expect(within(table).getByText("Материалы")).toBeInTheDocument();
  expect(within(table).getByText("Трубка ТУТ 6/3 (удалена)")).toBeInTheDocument();
});

it("an empty journal says when a row appears, a failed one says it did not load", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementationOnce(async () => json({ entries: [] }))
    .mockImplementationOnce(async () => json({ detail: "Нет доступа" }, 403));

  const { unmount } = render(<StockLinksJournal recordId="rec-1" />);
  expect(await screen.findByText("Позицию склада к этой записи ещё не привязывали.")).toBeInTheDocument();
  unmount();
  render(<StockLinksJournal />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Нет доступа");
});

it("«База данных»: the record card shows «Со склада», the header button opens the whole journal", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.startsWith("/api/references/stock-links?record_id=rec-1")) return json({ entries: [ENTRY({ event: "updated", changes: [{ field: "Производитель", old: "", new: "JST" }] })] });
    if (path === "/api/references/stock-links") return json({ entries: [ENTRY({}), ENTRY({ id: "e3", record_text: "Другая" })] });
    if (path.startsWith("/api/references/tech-operations")) return json({ metadata: null, records: [RECORD], columns: ["Наименование", "Производитель"] });
    if (path === "/api/references/maps") return json({ suppliers: [], countries: [], gost_awg: [] });
    if (path === "/api/references/bases") return json({ bases: [] });
    return json({ items: [], records: [] });
  });

  render(<ReferencesPage principal={admin} initialTab="mat_use" />);

  fireEvent.click(await screen.findByText("Контакт SXH"));
  const card = await screen.findByRole("region", { name: "Со склада" });
  expect(await within(card).findByText("Производитель: — → JST")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Журнал «Склад → база»" }));
  await waitFor(() => expect(screen.getByText("Другая")).toBeInTheDocument());
  expect(screen.getByRole("button", { name: "Журнал «Склад → база»" })).toHaveAttribute("aria-pressed", "true");
});
