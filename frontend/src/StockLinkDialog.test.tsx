import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { StockLinkDialog } from "./StockLinkDialog";
import { WarehousePage } from "./WarehousePage";
import type { WarehouseLot } from "./api";

const lot: WarehouseLot = {
  id: "40000000-0000-0000-0000-000000000001", version: 1, warehouse: "ЦАР", grn: "FRX.26.0000001", package: "",
  article: "GHR-04V-S", series: "", name: "Разъём 4 pin", item_type: "Комплектующие", purpose: "", unit: "шт",
  storage_location: "1F4", inbound: "10", written_off: "0", reserved: "0", balance: "10", available: "10",
  source_receiving_ids: [], manufacturer: "JST",
};

const record = { id: "rec-1", tab: "ter", display_text: "GHR-04V-S · JST", source_sheet: "", fields: {} };

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

function stub(post: (body: Record<string, unknown>) => unknown) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path.startsWith("/api/references/tech-operations?")) return json({ metadata: null, records: [record] });
    if (path === "/api/references/stock-links" && init?.method === "POST") return json(post(JSON.parse(String(init.body))));
    throw new Error(`Unexpected request: ${path}`);
  });
}

it("searches a base by the lot article and links the picked record", async () => {
  const fetch = stub(() => ({ record, entry: { event: "linked", changes: [] } }));
  render(<StockLinkDialog lot={lot} onClose={() => undefined} />);

  expect(screen.getByLabelText("Поиск записи")).toHaveValue("GHR-04V-S");
  expect(String(fetch.mock.calls[0][0])).toBe("/api/references/tech-operations?tab=ter&q=GHR-04V-S&limit=30");
  const submit = screen.getByRole("button", { name: "Привязать" });
  expect(submit).toBeDisabled();
  fireEvent.click(await screen.findByRole("button", { name: "GHR-04V-S · JST" }));
  fireEvent.click(submit);

  const status = await screen.findByRole("status");
  expect(status).toHaveTextContent("Партия привязана к записи: GHR-04V-S · JST");
  expect(status).toHaveTextContent("База: Терминалы · Привязана");
  const post = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(String(post[1]?.body))).toEqual({ lot_id: lot.id, record_id: "rec-1" });
});

it("creates a record prefilled from the lot and shows what was done", async () => {
  const fetch = stub(() => ({
    record,
    entry: { event: "created", changes: [{ field: "terManufacturer", old: "", new: "JST" }] },
  }));
  render(<StockLinkDialog lot={lot} onClose={() => undefined} />);

  fireEvent.click(screen.getByRole("tab", { name: "Создать запись" }));
  expect(screen.getByLabelText("Артикул")).toHaveValue("GHR-04V-S");
  expect(screen.getByLabelText("Название")).toHaveValue("Разъём 4 pin");
  expect(screen.getByLabelText("Производитель")).toHaveValue("JST");
  fireEvent.change(screen.getByLabelText("Производитель"), { target: { value: "JST Mfg" } });
  fireEvent.click(screen.getByRole("button", { name: "Создать запись" }));

  const status = await screen.findByRole("status");
  expect(status).toHaveTextContent("Создана запись: GHR-04V-S · JST");
  expect(status).toHaveTextContent("terManufacturer: — → JST");
  const post = fetch.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(String(post[1]?.body))).toEqual({
    lot_id: lot.id, tab: "ter",
    fields: { terArticle: "GHR-04V-S", terComponent: "Разъём 4 pin", terManufacturer: "JST Mfg" },
  });
});

it("shows the server refusal and keeps the dialog open", async () => {
  stub(() => { throw new Error("x"); });
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (init?.method === "POST") return json({ detail: "Партия склада не найдена" }, 404);
    return json({ metadata: null, records: [record] });
  });
  render(<StockLinkDialog lot={lot} onClose={() => undefined} />);
  fireEvent.click(await screen.findByRole("button", { name: "GHR-04V-S · JST" }));
  fireEvent.click(screen.getByRole("button", { name: "Привязать" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Партия склада не найдена");
});

it("opens on the base that fits the lot name", () => {
  stub(() => ({}));
  render(<StockLinkDialog lot={{ ...lot, article: "MGTF-035", name: "Провод МГТФ 0.35 белый" }} onClose={() => undefined} />);
  expect(screen.getByLabelText("База")).toHaveValue("prov");
});

async function openLotCard() {
  fireEvent.click(await screen.findByText("GHR-04V-S"));
  return screen.findByLabelText("Партия FRX.26.0000001");
}

it("offers «В базу данных…» on the lot card only to a user who edits the reference", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => String(input).startsWith("/api/references") ? json({ metadata: null, records: [] }) : json({ lots: [lot], total: 1, truncated: 0, next_cursor: null }));
  const { unmount } = render(<WarehousePage canEditReferences />);
  const card = await openLotCard();
  fireEvent.click(within(card).getByRole("button", { name: "В базу данных…" }));
  await waitFor(() => expect(screen.getByRole("dialog", { name: "В базу данных" })).toBeInTheDocument());
  unmount();

  render(<WarehousePage />);
  const plain = await openLotCard();
  expect(within(plain).queryByRole("button", { name: "В базу данных…" })).toBeNull();
});
