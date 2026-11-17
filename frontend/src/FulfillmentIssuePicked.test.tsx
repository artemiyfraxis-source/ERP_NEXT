import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { FulfillmentPage, partlyIssuable } from "./FulfillmentPage";
import type { FulfillmentLine, FulfillmentRequest, Principal } from "./api";

/* «Выдать набранное…» (owner 17.11.2026): a kit still being picked hands out what is picked, the rest goes to «Довыдача». */

const principal: Principal = {
  user: { id: "10000000-0000-0000-0000-000000000001", email: "receiver@example.test", name: "Кладовщик", department: "Склад" },
  roles: ["foreman"],
  access_level: "foreman",
  permissions: { fulfillment: "edit", handout: "edit" },
};

const line = (id: string, article: string, name: string, extra: Partial<FulfillmentLine> & Record<string, unknown> = {}): FulfillmentLine => ({
  id, requirement_id: id, article, name, unit: "шт", requirement: "4", reserved: "0", issued: "0",
  returned_good: "0", defective_returned: "0", defective_missing: "0", consumed: "0", lot: null, ...extra,
});

const lot = { id: "lot-1", warehouse: "ЦАР", grn: "FRX.26.0000001", package: "", storage_location: "1F4" };

const kit: FulfillmentRequest = {
  id: "kit-7", number: "REQ.26.0000100", version: 6, created_at: "2026-11-17T10:00:00+03:00", requester: "Мастер",
  purpose: "663К.1 · КБ7 · Жгут", project: "663К.1", deadline: "2026-11-20", status: "Недоукомплектован", kind: "main",
  handout_location: null, return_location: null, fully_reserved: false,
  lines: [
    line("l-bound", "CON-001", "Разъём", { requirement: "2", reserved: "2", lot }),
    line("l-off", "TER-002", "Клемма", { off_book: true }),
    line("l-absent", "BUS-003", "Втулка", { requirement: "3", absent_at: "2026-11-17T12:00:00+03:00", absent_by: "Кладовщик" }),
    line("l-wait", "SLV-004", "Гильза", { requirement: "1" }),
  ],
};

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

function mockApi(requests: FulfillmentRequest[], bodies: Array<{ path: string; body: unknown }>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST" && path.endsWith("/issue-picked")) {
      bodies.push({ path, body: JSON.parse(String(init.body)) });
      return json({
        request: { ...kit, status: "КБ Выдана" }, extra: { ...kit, id: "extra-1", kind: "extra" },
        message: "Выдано позиций: 2 (КБ7 (REQ.26.0000100)). Остальное (2 поз.) — в довыдаче REQ.26.0000101.",
      });
    }
    if (path === "/api/issues") return json({ requests, next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    if (path.includes("/candidates")) return json({ positions: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
}

async function openKit(principalUsed: Principal = principal) {
  render(<FulfillmentPage principal={principalUsed} mode="pick" />);
  fireEvent.click(await screen.findByRole("button", { name: /^663К\.1/ }));
  fireEvent.click(screen.getByRole("tab", { name: /^КБ7/ }));
}

afterEach(() => {
  vi.restoreAllMocks();
});

it("partlyIssuable: a main kit being picked with something picked and something waiting", () => {
  expect(partlyIssuable(kit)).toBe(true);
  expect(partlyIssuable({ ...kit, status: "Новая" })).toBe(true);
  expect(partlyIssuable({ ...kit, kind: "extra" })).toBe(false);
  expect(partlyIssuable({ ...kit, status: "Укомплектован" })).toBe(false);
  expect(partlyIssuable({ ...kit, lines: kit.lines.slice(2) })).toBe(false); // nothing picked
  expect(partlyIssuable({ ...kit, lines: kit.lines.slice(0, 2) })).toBe(false); // nothing waits
});

it("lists what is issued and what moves to «Довыдача», then sends the kit's version", async () => {
  const bodies: Array<{ path: string; body: unknown }> = [];
  mockApi([kit], bodies);
  await openKit();

  fireEvent.click(screen.getByRole("button", { name: "Выдать набранное…" }));

  const dialog = screen.getByRole("dialog", { name: "Выдать набранное · REQ.26.0000100" });
  const given = within(within(dialog).getByRole("table", { name: "Выдаётся сейчас" }));
  expect(given.getAllByRole("row").slice(1).map((row) => row.textContent)).toEqual([
    "CON-001Разъём2штFRX.26.0000001",
    "TER-002Клемма4штесть, не заведено",
  ]);
  const moved = within(within(dialog).getByRole("table", { name: "В довыдачу" }));
  expect(moved.getAllByRole("row").slice(1).map((row) => row.textContent)).toEqual([
    "BUS-003Втулка3штнет на складе",
    "SLV-004Гильза1штждёт ГРН",
  ]);
  expect(within(dialog).getByText(/без списания \(«есть, не заведено»\): 1/)).toBeInTheDocument();

  fireEvent.click(within(dialog).getByRole("button", { name: "Выдать набранное (2 поз.)" }));

  await waitFor(() => expect(bodies).toEqual([{ path: "/api/issues/kit-7/issue-picked", body: { version: 6 } }]));
  expect(await screen.findByText("Выдано позиций: 2 (КБ7 (REQ.26.0000100)). Остальное (2 поз.) — в довыдаче REQ.26.0000101.")).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("«Отмена» closes the window without a call", async () => {
  const bodies: Array<{ path: string; body: unknown }> = [];
  mockApi([kit], bodies);
  await openKit();

  fireEvent.click(screen.getByRole("button", { name: "Выдать набранное…" }));
  fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Отмена" }));

  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(bodies).toEqual([]);
});

it("shows no «Выдать набранное…» without the handout right, on a kit with nothing picked or on an extra request", async () => {
  const picker: Principal = { ...principal, permissions: { fulfillment: "edit", handout: "view" } };
  mockApi([kit], []);
  const first = render(<FulfillmentPage principal={picker} mode="pick" />);
  fireEvent.click(await screen.findByRole("button", { name: /^663К\.1/ }));
  fireEvent.click(screen.getByRole("tab", { name: /^КБ7/ }));
  expect(screen.queryByRole("button", { name: "Выдать набранное…" })).not.toBeInTheDocument();
  first.unmount();
  vi.restoreAllMocks();

  mockApi([{ ...kit, lines: kit.lines.slice(2) }], []);
  const second = render(<FulfillmentPage principal={principal} mode="pick" />);
  fireEvent.click(await screen.findByRole("button", { name: /^663К\.1/ }));
  fireEvent.click(screen.getByRole("tab", { name: /^КБ7/ }));
  expect(screen.queryByRole("button", { name: "Выдать набранное…" })).not.toBeInTheDocument();
  second.unmount();
  vi.restoreAllMocks();

  mockApi([{ ...kit, kind: "extra" }], []);
  render(<FulfillmentPage principal={principal} mode="pick" />);
  fireEvent.click(await screen.findByRole("button", { name: /^663К\.1/ }));
  fireEvent.click(screen.getByRole("tab", { name: /^КБ7/ }));
  expect(screen.queryByRole("button", { name: "Выдать набранное…" })).not.toBeInTheDocument();
});
