import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PackWindow } from "./PackWindow";
import type { IntakeRow } from "./productionIntakeApi";

afterEach(() => vi.unstubAllGlobals());
const row = { project: "П", item_index: "КБ1", name: "Сборка", accepted: "20", packed: "4", good_id: "g1", good_version: 2, pack_permission_remaining: "6", pack_decided: true } as IntakeRow;
const rights = { canEdit: true, canManage: false, canShip: true };
it("requires an explicit quantity, caps it by permission, and refreshes addressee CAS without losing intent", async () => {
  let dispatches = 0, posts = 0;
  const fetcher = vi.fn(async (input, init) => {
    if (String(input).includes("project-ship")) {
      posts++;
      return posts === 1 ? new Response(JSON.stringify({ detail: "Версия изменена" }), { status: 409 })
        : new Response(JSON.stringify({ shipment: { number: "ОТГ-ДЕМО", pl_number: "" } }));
    }
    dispatches++;
    return new Response(JSON.stringify({ addressee: { version: dispatches === 1 ? 2 : 3 } }));
  });
  vi.stubGlobal("fetch", fetcher);
  render(<PackWindow project="П" rows={[row]} shipments={[]} rights={rights} reload={vi.fn()} close={vi.fn()} />);
  const qty = screen.getByLabelText("Сколько упаковать КБ1");
  expect(qty).toHaveAttribute("max", "6");
  expect(qty).toHaveValue(null);
  expect(screen.getByRole("button", { name: "Упаковать · 0 шт" })).toBeDisabled();
  fireEvent.change(qty, { target: { value: "7" } });
  await waitFor(() => expect(screen.getByRole("button", { name: "Упаковать · 7 шт" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Упаковать · 7 шт" }));
  expect(screen.getByRole("alert")).toHaveTextContent("не больше 6"); expect(posts).toBe(0);
  fireEvent.change(qty, { target: { value: "5" } });
  fireEvent.click(screen.getByRole("button", { name: "Упаковать · 5 шт" }));
  await waitFor(() => expect(dispatches).toBe(2));
  expect(qty).toHaveValue(5);
  await waitFor(() => expect(screen.getByRole("button", { name: "Упаковать · 5 шт" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Упаковать · 5 шт" }));
  await screen.findByText("Упаковано: ОТГ-ДЕМО");
  const bodies = fetcher.mock.calls.filter(([url]) => String(url).includes("project-ship")).map(([, init]) => JSON.parse(String(init.body)));
  expect(bodies.map((body) => body.addressee_version)).toEqual([2, 3]);
  expect(bodies[1].lines).toEqual([{ good_id: "g1", version: 2, quantity: "5" }]);
});
