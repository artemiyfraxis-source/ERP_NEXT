import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ProcurementLine } from "./api";
import { ProcurementPage } from "./ProcurementPage";

/* «Мои позиции»: действия на виду, без меню «⋯» (владелец, 05.10). */

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

const base = {
  id: "30000000-0000-0000-0000-000000000001", legacy_id: "REQ-1", status: "Жду счет", version: 3, buyer: "Закупщик", supplier: "Поставщик",
  calculated_name: "Разъём", calculated_quantity: "2", calculated_unit: "шт", actual_quantity: "2", actual_unit: "шт",
  pay_missing: [], note: null, invoice_url: null, invoice_number: null, invoice_item_name: null, actual_arrival: null, expected_arrival: "2026-10-12",
  order_number: null, order_date: null, order_url: null, paid_at: null, actual_sum: null, calculated_sum: "100",
} as unknown as ProcurementLine;

const staged = (n: number, status: string, extra: Partial<ProcurementLine> = {}): ProcurementLine => ({
  ...base, id: `30000000-0000-0000-0000-0000000003${n}0`, legacy_id: `REQ-${n}`, status, ...extra,
});

function mockQueue(lines: ProcurementLine[], posts: string[] = []) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/procurement/available") return json({ lines: [] });
    if (path === "/api/procurement/mine?limit=500") return json({ lines });
    if (init?.method === "PATCH" || init?.method === "POST") {
      posts.push(`${path} ${init.body}`);
      const body = JSON.parse(String(init.body));
      return json({ line: { ...lines[0], ...("actual_arrival" in body ? { actual_arrival: body.actual_arrival } : {}), version: lines[0].version + 1 } });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
}

async function openOrder(number: string) {
  const toggle = await screen.findByRole("button", { name: new RegExp(`^${number}`) });
  if (toggle.getAttribute("aria-expanded") !== "true") fireEvent.click(toggle);
  await waitFor(() => expect(toggle).toHaveAttribute("aria-expanded", "true"));
}

async function openBlock(title: RegExp) {
  const toggle = within(await screen.findByRole("heading", { name: title })).getByRole("button");
  if (toggle.getAttribute("aria-expanded") !== "true") fireEvent.click(toggle);
  await waitFor(() => expect(toggle).toHaveAttribute("aria-expanded", "true"));
}

async function pickRowAction(id: string, item: string | RegExp) {
  const group = await screen.findByRole("group", { name: `Действия ${id}` });
  await waitFor(() => expect(within(group).getByRole("button", { name: item })).toBeEnabled());
  fireEvent.click(within(group).getByRole("button", { name: item }));
}

const rowActionNames = (id: string) => within(screen.getByRole("group", { name: `Действия ${id}` })).getAllByRole("button").map((item) => item.textContent ?? "");

it("shows «Счёт» and «Запрос» in the order header, has no «⋯» menu and reaches every line action without opening anything", async () => {
  const shared = { invoice_url: "https://files.test/i.pdf", invoice_number: "17", order_url: "/api/procurement/supplier-requests/PO-009/document", order_number: "PO-009", order_date: "2026-10-05" };
  mockQueue([
    staged(1, "В оплату", { ...shared, invoice_item_name: "Разъём SMA (счёт)", calculated_name: "Разъём" }),
    staged(2, "В оплату", { ...shared, invoice_item_name: "Кабель", calculated_name: "Кабель" }),
  ]);

  render(<ProcurementPage />);
  const order = await screen.findByRole("rowgroup", { name: /^PO-009/ });
  expect(within(order).getByRole("link", { name: "Счёт" })).toHaveAttribute("href", "https://files.test/i.pdf");
  expect(within(order).getByRole("link", { name: "Счёт" })).toHaveAttribute("target", "_blank");
  expect(within(order).getByRole("link", { name: "Запрос" })).toHaveAttribute("href", shared.order_url);
  await openOrder("PO-009");
  // Ни кнопки «⋯», ни «Действия …», ни меню — действия лежат на строке.
  expect(screen.queryByText("⋯")).not.toBeInTheDocument();
  expect(screen.queryAllByRole("button", { name: /^Действия/ })).toHaveLength(0);
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  expect(rowActionNames("REQ-1")).toEqual(expect.arrayContaining(["Суммы по счёту…", "Оплатить одну строку…", "Отменить строку"]));
  // Счёт у строки тот же, что у заказа — колонка «—»; «в счёте» — серой строкой, только если название отличается.
  expect(screen.queryByRole("link", { name: "счёт" })).not.toBeInTheDocument();
  expect(screen.getByText("в счёте: Разъём SMA (счёт)")).toHaveClass("invoice-name");
  expect(screen.queryByText("в счёте: Кабель")).not.toBeInTheDocument();
  // Действие строки открывает форму одним щелчком.
  await pickRowAction("REQ-1", "Суммы по счёту…");
  expect(await screen.findByRole("heading", { name: /REQ-1 · Суммы по счёту/ })).toBeInTheDocument();
});

it("lists several invoices of one order as «Счета (2)» and keeps a differing invoice on its line", async () => {
  mockQueue([
    staged(1, "В оплату", { order_number: "PO-010", invoice_url: "https://files.test/a.pdf", invoice_number: "1" }),
    staged(2, "В оплату", { order_number: "PO-010", invoice_url: "https://files.test/b.pdf", invoice_number: "2" }),
  ]);

  render(<ProcurementPage />);
  const order = await screen.findByRole("rowgroup", { name: /^PO-010/ });
  expect(within(order).getByText("Счета (2)")).toBeInTheDocument();
  expect(within(order).getByRole("link", { name: "Счёт № 1" })).toHaveAttribute("href", "https://files.test/a.pdf");
  expect(within(order).getByRole("link", { name: "Счёт № 2" })).toHaveAttribute("href", "https://files.test/b.pdf");
  await openOrder("PO-010");
  expect(within(order).getByRole("link", { name: "№ 2" })).toHaveAttribute("href", "https://files.test/b.pdf");
});

it("asks before cancelling a line from its visible action and sends nothing when declined", async () => {
  const posts: string[] = [];
  mockQueue([staged(5, "Жду счет", { order_number: "PO-011", order_date: "2026-10-05" })], posts);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);

  render(<ProcurementPage />);
  await openOrder("PO-011");
  await pickRowAction("REQ-5", "Отменить строку");
  expect(confirm).toHaveBeenCalledWith("Отменить REQ-5? Отмена окончательная.");
  expect(posts).toEqual([]);
  confirm.mockReturnValue(true);
  await pickRowAction("REQ-5", "Отменить строку");
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0]).toContain("/cancel");
});

it("lets the buyer fill and clear «Факт. дата» from «В пути» on, and shows editable cells as fields", async () => {
  const transit = staged(6, "В пути", { order_number: "PO-012", order_date: "2026-10-05", paid_at: "2026-10-05T10:00:00+03:00" });
  const waiting = staged(7, "Жду счет", { order_number: "PO-013", order_date: "2026-10-05" });
  const posts: string[] = [];
  mockQueue([transit, waiting], posts);

  render(<ProcurementPage />);
  await openBlock(/^В пути/);
  await openOrder("PO-012");
  // Пустые правимые ячейки подсказывают, что в них можно ввести.
  const empty = screen.getByRole("button", { name: "Факт. дата REQ-6" });
  expect(empty).toHaveTextContent("дд.мм.гггг");
  expect(empty).toHaveClass("cell-edit");
  expect(screen.getByRole("button", { name: "Комментарий REQ-6" })).toHaveTextContent("добавить");
  fireEvent.click(empty);
  fireEvent.change(screen.getByLabelText("Факт. дата REQ-6"), { target: { value: "2026-10-07" } });
  fireEvent.keyDown(screen.getByLabelText("Факт. дата REQ-6"), { key: "Enter" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Факт. дата REQ-6" })).toHaveTextContent("07.10.2026"));
  expect(posts).toEqual([`/api/procurement/${transit.id}/actual-arrival {"version":3,"actual_arrival":"2026-10-07"}`]);
  // Стереть можно: пустая дата уходит как null.
  fireEvent.click(screen.getByRole("button", { name: "Факт. дата REQ-6" }));
  fireEvent.change(screen.getByLabelText("Факт. дата REQ-6"), { target: { value: "" } });
  fireEvent.keyDown(screen.getByLabelText("Факт. дата REQ-6"), { key: "Enter" });
  await waitFor(() => expect(posts).toHaveLength(2));
  expect(posts[1]).toMatch(/"actual_arrival":null/);
  // До «В пути» ячейка только показывает.
  await openOrder("PO-013");
  expect(screen.queryByRole("button", { name: "Факт. дата REQ-7" })).not.toBeInTheDocument();
});
