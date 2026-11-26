import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PackWindow } from "./PackWindow";
import type { IntakeRow } from "./productionIntakeApi";

afterEach(() => vi.unstubAllGlobals());
const row = { project: "П", item_index: "КБ1", name: "Сборка", accepted: "20", packed: "4", good_id: "g1", good_version: 2, pack_permission_remaining: "6", pack_decided: true } as IntakeRow;
const rights = { canEdit: true, canManage: false, canWarehouse: true, canUnship: false };
it("requires an explicit quantity, caps it by permission, and refreshes addressee CAS without losing intent", async () => {
  let dispatches = 0, posts = 0;
  const fetcher = vi.fn(async (input, init) => {
    if (String(input).includes("item-addressees")) return new Response(JSON.stringify({ items: [] }));
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
  expect(qty).toHaveAttribute("placeholder", "до 6");
  expect(qty).toHaveValue("");
  expect(screen.getByRole("button", { name: "Упаковать · 0 шт" })).toBeDisabled();
  fireEvent.change(qty, { target: { value: "7" } });
  await waitFor(() => expect(screen.getByRole("button", { name: "Упаковать · 7 шт" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Упаковать · 7 шт" }));
  expect(screen.getByRole("alert")).toHaveTextContent("не больше 6"); expect(posts).toBe(0);
  fireEvent.change(qty, { target: { value: "5" } });
  fireEvent.click(screen.getByRole("button", { name: "Упаковать · 5 шт" }));
  await waitFor(() => expect(dispatches).toBe(2));
  expect(qty).toHaveValue("5");
  await waitFor(() => expect(screen.getByRole("button", { name: "Упаковать · 5 шт" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Упаковать · 5 шт" }));
  await screen.findByText("Упаковано: ОТГ-ДЕМО");
  const bodies = fetcher.mock.calls.filter(([url]) => String(url).includes("project-ship")).map(([, init]) => JSON.parse(String(init.body)));
  expect(bodies.map((body) => body.addressee_version)).toEqual([2, 3]);
  expect(bodies[1].lines).toEqual([{ good_id: "g1", version: 2, quantity: "5" }]);
});

it("refreshes a cached destination conflict and packs with the same quantities and selection", async () => {
  let refreshed = false;
  const fetcher = vi.fn(async (input, init) => new Response(JSON.stringify(String(input).includes("project-ship")
    ? { shipment: { number: "ОТГ-ДЕМО", pl_number: "" } }
    : String(input).includes("item-addressees") ? {
    items: ["КБ1", "КБ2"].map((item_index, index) => ({ item_index, effective: {
      receiver: "Получатель", shipping_point: index && !refreshed ? "Тула" : "Москва", contact: "", phone: "123", terms: "ДОСТАВКА ТК", carrier: "СДЭК", upd_type: "ЭДО",
    } })),
  } : { addressee: { version: refreshed ? 3 : 2 } })));
  vi.stubGlobal("fetch", fetcher);
  render(<PackWindow project="П" rows={[row, { ...row, item_index: "КБ2", good_id: "g2" }]} shipments={[]} rights={rights} reload={vi.fn()} close={vi.fn()} />);
  await screen.findByText("Доставка 2 · КБ2");
  fireEvent.change(screen.getByLabelText("Сколько упаковать КБ1"), { target: { value: "3" } });
  fireEvent.change(screen.getByLabelText("Сколько упаковать КБ2"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("button", { name: "Упаковать · 5 шт" }));
  expect(screen.getByRole("alert")).toHaveTextContent("разные данные доставки");
  expect(screen.getByLabelText("Сколько упаковать КБ1")).toHaveValue("3");
  expect(screen.getByLabelText("Сколько упаковать КБ2")).toHaveValue("2");
  expect(fetcher.mock.calls.some(([url]) => String(url).includes("project-ship"))).toBe(false);
  refreshed = true;
  fireEvent.click(screen.getByRole("button", { name: "Обновить доставку КБ" }));
  await screen.findByText("Доставка · КБ1, КБ2");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Сколько упаковать КБ1")).toHaveValue("3");
  expect(screen.getByLabelText("Сколько упаковать КБ2")).toHaveValue("2");
  expect(screen.getByLabelText("Упаковать КБ1")).toBeChecked();
  expect(screen.getByLabelText("Упаковать КБ2")).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Упаковать · 5 шт" }));
  await screen.findByText("Упаковано: ОТГ-ДЕМО");
  const [, init] = fetcher.mock.calls.find(([url]) => String(url).includes("project-ship"))!;
  expect(JSON.parse(String(init.body))).toMatchObject({ addressee_version: 3, lines: [
    { good_id: "g1", version: 2, quantity: "3" }, { good_id: "g2", version: 2, quantity: "2" },
  ] });
});

it("allows retrying a failed delivery refresh without clearing quantities", async () => {
  let fail = false;
  vi.stubGlobal("fetch", vi.fn(async (input) => {
    if (fail) throw new TypeError("Failed to fetch");
    return new Response(JSON.stringify(String(input).includes("item-addressees") ? { items: [] } : { addressee: { version: 2 } }));
  }));
  render(<PackWindow project="П" rows={[row]} shipments={[]} rights={rights} reload={vi.fn()} close={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Сколько упаковать КБ1"), { target: { value: "3" } });
  await waitFor(() => expect(screen.getByRole("button", { name: "Упаковать · 3 шт" })).toBeEnabled());
  fail = true;
  fireEvent.click(screen.getByRole("button", { name: "Обновить доставку КБ" }));
  await screen.findByRole("alert");
  expect(screen.getByLabelText("Сколько упаковать КБ1")).toHaveValue("3");
  expect(screen.getByRole("button", { name: "Упаковать · 3 шт" })).toBeDisabled();
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "Обновить доставку КБ" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Упаковать · 3 шт" })).toBeEnabled());
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Сколько упаковать КБ1")).toHaveValue("3");
});

it("validates the packed quantity inline and recomputes the shortage automatically", async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(String(input).includes("item-addressees") ? { items: [] } : { addressee: { version: 2 } })));
  vi.stubGlobal("fetch", fetcher);
  const full = { ...row, needed: "20", packed: "4", unit: "шт", acts: [{ grn: "ГРН-1", quantity: "20", received_at: "", location: "" }] } as IntakeRow;
  render(<PackWindow project="П" rows={[full]} shipments={[]} rights={rights} reload={vi.fn()} close={vi.fn()} />);
  for (const name of ["Позиция", "Наименование", "Заказано", "Упаковано", "Недобор", "ГРН"]) expect(screen.getByRole("columnheader", { name })).toBeInTheDocument();
  expect(screen.getByText("ГРН-1")).toBeInTheDocument();
  const qty = screen.getByLabelText("Сколько упаковать КБ1");
  expect(screen.getByLabelText("Недобор КБ1")).toHaveTextContent("16");
  fireEvent.change(qty, { target: { value: "1,5" } });
  expect(screen.getByLabelText("Недобор КБ1")).toHaveTextContent("14.5");
  expect(qty).toHaveAttribute("aria-invalid", "false");
  fireEvent.change(qty, { target: { value: "7" } });
  expect(qty).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByText("Можно не больше 6")).toBeInTheDocument();
  fireEvent.change(qty, { target: { value: "0" } });
  expect(screen.getByText("Введите число больше нуля")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Упаковать · 0 шт" }));
  expect(fetcher.mock.calls.some(([url]) => String(url).includes("project-ship"))).toBe(false);
});
