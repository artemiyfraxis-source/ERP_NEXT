import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { WarehousePage } from "./WarehousePage";
import type { WarehouseLot } from "./api";

const oldLot: WarehouseLot = {
  id: "40000000-0000-0000-0000-000000000001", version: 1, warehouse: "ЦАР", grn: "FRX.26.0000001", package: "",
  article: "GHR-04V-S", series: "", name: "Разъём", item_type: "Комплектующие", purpose: "FRX.001", unit: "шт",
  storage_location: "1F4", inbound: "10", written_off: "0", reserved: "0", balance: "10", available: "10",
  source_receiving_ids: [],
};
const newLot: WarehouseLot = {
  ...oldLot, id: "40000000-0000-0000-0000-000000000002", grn: "FRX.26.0000002", article: "XHP-4", name: "Колодка XH 4pin",
};

// The receipt window itself is covered by WarehouseReceipt.test.tsx: here it only reports an accepted lot and closes.
vi.mock("./WarehouseReceipt", () => ({
  WarehouseReceipt: ({ onAccepted, onClose }: { onAccepted: (lot: WarehouseLot) => void; onClose: () => void }) => (
    <div>
      <button onClick={() => onAccepted(newLot)}>принять-в-окне</button>
      <button onClick={onClose}>закрыть-окно</button>
    </div>
  ),
}));

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); window.localStorage.clear(); });

it("shows a lot at once when the receipt window accepts it and reads the stock again on closing", async () => {
  let stockReads = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input).split("?")[0];
    if (path === "/api/stock") { stockReads += 1; return json({ lots: [oldLot], total: 1, truncated: 0, next_cursor: null }); }
    return json({ group: "article", groups: [] });
  });

  render(<WarehousePage />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Внести позиции" }, { timeout: 5000 }));
  await waitFor(() => expect(stockReads).toBeGreaterThan(0));
  const before = stockReads;

  fireEvent.click(screen.getByText("принять-в-окне"));
  // The server page does not carry the new lot (a later page of the id order): it stays in the table all the same.
  await waitFor(() => expect(stockReads).toBe(before + 1));
  expect(await screen.findByText("FRX.26.0000002")).toBeInTheDocument();

  fireEvent.click(screen.getByText("закрыть-окно"));
  await waitFor(() => expect(stockReads).toBe(before + 2));
});
