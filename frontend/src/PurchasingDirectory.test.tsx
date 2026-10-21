import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PurchasingSettings } from "./PurchasingSettings";
import { PurchasingSuppliers } from "./PurchasingSuppliers";
import { resetStageOptions } from "./StageOptions";
import { innValid } from "./purchasingDirectoryApi";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const supplier = {
  id: "s1", name: "ЧипДип", inn: null, categories: ["connector"], is_key: true, contact_person: "Анна", phone: "+7 495",
  email: null, site: null, domains: ["chipdip.ru"], notes: null, archived: false, version: 1, purchases_count: 4, purchases_sum: "1300.5",
};
const setting = {
  key: "gate_min_quotes", label: "Минимум КП для выбора", hint: "Меньше КП — нужна причина.", kind: "int", value: 3, default: 3,
  minimum: 1, maximum: 10,
};

function principal(level: string) {
  return { user: { id: "u", email: "e", name: "Имя", department: "Д" }, roles: [level], access_level: level, permissions: {} };
}

afterEach(() => {
  vi.restoreAllMocks();
  cleanup(); // unmount before the reset: a live tree would refetch the options without the mock
  resetStageOptions();
});

it("lists suppliers with stars and totals", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => json([supplier]));
  render(<PurchasingSuppliers />);
  expect(await screen.findByText("ЧипДип")).toBeInTheDocument();
  expect(screen.getByText("chipdip.ru")).toBeInTheDocument();
  expect(screen.getByText("★")).toBeInTheDocument();
  expect(screen.getAllByText("Разъёмы").length).toBeGreaterThan(1); // the cell and the filter option
});

it("refuses a bad INN in the create form before any request", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => json([]));
  render(<PurchasingSuppliers />);
  await screen.findByText("Поставщиков нет — добавьте или соберите из закупок");
  fireEvent.click(screen.getByRole("button", { name: "+ Поставщик" }));
  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  expect(await screen.findByText("Название обязательно")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Название"), { target: { value: "Ромашка" } });
  fireEvent.change(screen.getByLabelText("ИНН"), { target: { value: "7707083894" } });
  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  expect(await screen.findByText("ИНН не проходит проверку контрольной суммы")).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === "POST")).toBe(false);
  expect(innValid("7707083893")).toBe(true);
});

it("opens a card with stats on a row click", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (String(input) === "/api/purchasing/suppliers/s1") {
      return json({
        ...supplier,
        procurement: {
          lines_total: 4, paid_lines: 2, paid_sum: "1300.50", received: 2, received_with_date: 2, on_time: 1, on_time_pct: 50,
          open_by_stage: { "В пути": 1 },
          last_purchases: [{ date: "2026-09-19", name: "Разъём XT60", article: null, quantity: "2", unit: "шт", unit_price_with_vat: "500", order_number: "З-2" }],
        },
        rfq: { sent: 2, answered: 1, quotes: 3, answered_pct: 50 },
      });
    }
    return json([supplier]);
  });
  render(<PurchasingSuppliers />);
  fireEvent.click(await screen.findByText("ЧипДип"));
  expect(await screen.findByText("Разъём XT60")).toBeInTheDocument();
  expect(screen.getByText("З-2")).toBeInTheDocument();
  expect(screen.getByText(/В пути — 1/)).toBeInTheDocument();
});

it("shows settings read-only for a non-admin", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (String(input) === "/api/auth/me") return json(principal("commercial"));
    return json([setting]);
  });
  render(<PurchasingSettings />);
  const field = await screen.findByLabelText("Минимум КП для выбора");
  expect(field).toBeDisabled();
  expect(screen.getByText("Меняет администратор.")).toBeInTheDocument();
  expect(screen.getByText("по умолчанию: 3")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Сохранить" })).not.toBeInTheDocument();
});

it("lets an admin change and save a setting", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input) === "/api/auth/me") return json(principal("admin"));
    if (init?.method === "PUT") return json([{ ...setting, value: 2 }]);
    return json([setting]);
  });
  render(<PurchasingSettings />);
  await waitFor(() => expect(screen.getByLabelText("Минимум КП для выбора")).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Минимум КП для выбора"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  expect(await screen.findByText("Настройки сохранены")).toBeInTheDocument();
  const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(JSON.parse(String(put[1]!.body))).toEqual({ changes: { gate_min_quotes: "2" } });
});

it("edits the purposes directory: add, remove, protected RND / FRX stay", async () => {
  const purposes = {
    key: "purposes", label: "Назначения заявок", hint: "…", kind: "list", value: ["RND", "FRX", "Склад"],
    default: ["RND", "FRX", "Склад"], minimum: null, maximum: null,
  };
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    if (String(input) === "/api/auth/me") return json(principal("admin"));
    if (init?.method === "PUT") return json([{ ...purposes, value: JSON.parse(String(init.body)).changes.purposes }]);
    return json([purposes]);
  });
  render(<PurchasingSettings />);
  const list = await screen.findByRole("list", { name: "Назначения заявок" });
  expect(within(list).getByText("НИОКР (опытный образец)")).toBeInTheDocument();
  expect(within(list).getByText("Собственные нужды компании")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByLabelText("Новое назначение")).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: /Убрать назначение НИОКР/ })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Новое назначение"), { target: { value: "Оснастка и инструмент" } });
  fireEvent.click(screen.getByRole("button", { name: "Добавить" }));
  fireEvent.click(screen.getByRole("button", { name: "Убрать назначение Склад" }));
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
  expect(await screen.findByText("Настройки сохранены")).toBeInTheDocument();
  const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT")!;
  expect(JSON.parse(String(put[1]!.body))).toEqual({ changes: { purposes: ["RND", "FRX", "Оснастка и инструмент"] } });
});
