import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PurchasingRfq } from "./PurchasingRfq";
import { RfqPool } from "./RfqPool";
import { resetStageOptions } from "./StageOptions";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const line = (id: string, over: Record<string, unknown> = {}) => ({
  id, legacy_id: `SM-${id}`, name: `Позиция ${id}`, article: null, quantity: "2", unit: "шт", supplier: null,
  budget_unit_gross: "12", need_by: null, status: "В работе", version: 1, project: "RND.012", category: "cable",
  supplier_name: null, supplier_id: null, item: null, history: null, cheaper_before_pct: null, ...over,
});

const pool = {
  total: 3,
  groups: [
    {
      category: "cable", label: "Кабель и провод", count: 2,
      suppliers: [{ name: "ООО «Элитан Тре»", supplier_id: "s-el", lines: 2 }],
      lines: [
        line("a", { name: "Провод МГТФ 0,12", supplier: "Элитан", supplier_name: "ООО «Элитан Тре»", supplier_id: "s-el" }),
        line("b", {
          name: "Кабель ПВС 2х0,75", supplier: "ООО «Элитан Тре»", supplier_name: "ООО «Элитан Тре»", supplier_id: "s-el",
          item: { id: "i1", name: "Кабель ПВС 2х0,75", category: "cable", matched_by: "part_key" },
          history: {
            purchases: 4,
            last: { price: "9.8", date: "2026-09-10", supplier: "ООО Бета", unit: "шт" },
            min: { price: "9.1", date: "2026-09-03", supplier: "ООО Альфа", unit: "шт" },
          },
          cheaper_before_pct: "24.2",
        }),
      ],
    },
    { category: "connector", label: "Разъёмы и клеммы", count: 1, suppliers: [], lines: [line("c", { name: "Разъём XH-2P", category: "connector" })] },
  ],
};

const suggestion = {
  categories: ["cable"], lines: 2,
  suggestions: [
    {
      supplier_id: "s-al", name: "ООО Альфа", is_key: false, categories: [], reason: "items", rfq_reason: "category",
      preselect: true, in_rfq: false, last_asked_at: null,
      reasons: [{ code: "items", label: "поставлял эти позиции", lines: 2, last_date: "2026-09-03" }],
      lines: [{ line_id: "a", reasons: ["items", "category_history"] }],
    },
    {
      supplier_id: null, name: "ИП Неизвестный", is_key: false, categories: [], reason: "category_history",
      rfq_reason: "category", preselect: true, in_rfq: false, last_asked_at: null,
      reasons: [{ code: "category_history", label: "поставлял такие же (кабель и провод)", lines: 1, last_date: null }],
      lines: [{ line_id: "a", reasons: ["category_history"] }, { line_id: "b", reasons: ["category_history"] }],
    },
    {
      supplier_id: "s-rot", name: "ООО Ротация", is_key: false, categories: ["cable"], reason: "rotation",
      rfq_reason: "rotation", preselect: false, in_rfq: false, last_asked_at: null,
      reasons: [{ code: "rotation", label: "давно не спрашивали", lines: null, last_date: null }],
      lines: [{ line_id: "b", reasons: ["rotation"] }],
    },
  ],
};

function mockServer(posts: { path: string; body: unknown }[] = [], gets: string[] = []) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") {
      posts.push({ path, body: JSON.parse(String(init.body)) });
      return json({ id: "r-new", number: "КП-2610-002", suppliers: 2 }, 201);
    }
    gets.push(path);
    if (path === "/api/purchasing/rfqs/pool") return json(pool);
    if (path.startsWith("/api/purchasing/rfqs/suggest?")) return json(suggestion);
    if (path === "/api/purchasing/rfqs/directory") return json({ suppliers: [{ supplier_id: "s-om", name: "ООО Омега", is_key: true, categories: [] }] });
    if (path === "/api/purchasing/rfqs?scope=mine") return json({ rfqs: [] });
    return json({}, 404);
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
  resetStageOptions();
});

it("renders the pool by category with the original suppliers in the group header", async () => {
  mockServer();
  render(<RfqPool onClose={() => undefined} onCreated={() => undefined} />);
  const head = (await screen.findByRole("button", { name: /Кабель и провод/ })).closest("td") as HTMLElement;
  expect(head).toHaveTextContent(/Кабель и провод · 2\sстроки · изначально: ООО «Элитан Тре» 2/);
  expect(screen.getByRole("button", { name: /Разъёмы и клеммы/ }).closest("td")).toHaveTextContent(/1\sстрока/);
  expect(screen.getByText("Провод МГТФ 0,12")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Кабель и провод/ }));  // collapses the group
  expect(screen.queryByText("Провод МГТФ 0,12")).not.toBeInTheDocument();
});

it("the group checkbox selects the whole group and the bar asks for quotes", async () => {
  mockServer();
  render(<RfqPool onClose={() => undefined} onCreated={() => undefined} />);
  fireEvent.click(await screen.findByLabelText("Выбрать группу «Кабель и провод»"));
  expect(screen.getByLabelText("Взять SM-a")).toBeChecked();
  expect(screen.getByLabelText("Взять SM-b")).toBeChecked();
  expect(screen.getByLabelText("Взять SM-c")).not.toBeChecked();
  const bar = screen.getByRole("toolbar", { name: "Выбранные строки" });
  expect(within(bar).getByText("Выбрано 2")).toBeInTheDocument();
  expect(within(bar).getByRole("button", { name: "Запросить КП (2)" })).toBeInTheDocument();
});

it("shows the nomenclature chip, the price history and «раньше дешевле»", async () => {
  mockServer();
  render(<RfqPool onClose={() => undefined} onCreated={() => undefined} />);
  const row = (await screen.findByText("Кабель ПВС 2х0,75")).closest("tr") as HTMLElement;
  expect(within(row).getByText("в номенклатуре")).toBeInTheDocument();
  expect(within(row).getByText("раньше дешевле на 24,2%")).toBeInTheDocument();
  expect(within(row).getByText(/покупали: 9,80\s₽ \(мин 9,10\s₽ у ООО Альфа, 03\.09\)/)).toBeInTheDocument();
  const plain = screen.getByText("Провод МГТФ 0,12").closest("tr") as HTMLElement;
  expect(within(plain).queryByText("в номенклатуре")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Поиск по строкам"), { target: { value: "пвс" } });
  expect(screen.queryByText("Провод МГТФ 0,12")).not.toBeInTheDocument();
  expect(screen.getByText("Кабель ПВС 2х0,75")).toBeInTheDocument();
});

it("preselects the suggested suppliers and creates the request with lines and suppliers at once", async () => {
  const posts: { path: string; body: unknown }[] = [];
  const gets: string[] = [];
  mockServer(posts, gets);
  const created = vi.fn();
  render(<RfqPool onClose={() => undefined} onCreated={created} />);
  fireEvent.click(await screen.findByLabelText("Выбрать группу «Кабель и провод»"));
  fireEvent.click(screen.getByRole("button", { name: "Запросить КП (2)" }));

  expect(await screen.findByLabelText("Спросить ООО Альфа")).toBeChecked();
  expect(gets).toContain("/api/purchasing/rfqs/suggest?line_ids=a,b");
  expect(screen.getByLabelText("Спросить ИП Неизвестный")).toBeChecked();
  expect(screen.getByLabelText("Спросить ООО Ротация")).not.toBeChecked();
  expect(screen.getByText("нет в справочнике")).toBeInTheDocument();
  expect(screen.getByText(/поставлял эти позиции · 2\sстроки · последняя 03\.09\.2026/)).toBeInTheDocument();
  expect(screen.getByText("ещё не спрашивали")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Поставщик по названию"), { target: { value: "ооо омега" } });
  fireEvent.click(screen.getByRole("button", { name: "+ поставщик по названию" }));
  expect(await screen.findByLabelText("Спросить ООО Омега")).toBeChecked();
  fireEvent.change(screen.getByLabelText(/Название/), { target: { value: "Кабель" } });

  fireEvent.click(screen.getByRole("button", { name: "Создать запрос" }));
  await waitFor(() => expect(created).toHaveBeenCalledWith("r-new"));
  expect(posts).toEqual([{
    path: "/api/purchasing/rfqs",
    body: {
      line_ids: ["a", "b"], title: "Кабель",
      suppliers: [
        { supplier_id: "s-al", reason: "items", line_ids: ["a"] },
        { name: "ИП Неизвестный", reason: "category_history", line_ids: ["a", "b"] },
        { supplier_id: "s-om", reason: "manual", line_ids: ["a", "b"] },
      ],
      sent_on: null, declined_suggestions: [],
    },
  }]);
});

async function openSuppliers(posts: { path: string; body: unknown }[] = []) {
  mockServer(posts);
  const created = vi.fn();
  render(<RfqPool onClose={() => undefined} onCreated={created} />);
  fireEvent.click(await screen.findByLabelText("Выбрать группу «Кабель и провод»"));
  fireEvent.click(screen.getByRole("button", { name: "Запросить КП (2)" }));
  await screen.findByLabelText("Спросить ООО Альфа");
  return created;
}

const supplierRow = (name: string) => screen.getByRole("group", { name: `Поставщик ${name}` });

it("a supplier row opens its positions with the reason of each", async () => {
  await openSuppliers();
  const alfa = supplierRow("ООО Альфа");
  const toggle = within(alfa).getByRole("button", { name: /позиций: 1 из 2/ });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(within(alfa).queryByLabelText("Запросить SM-a у ООО Альфа")).not.toBeInTheDocument();
  fireEvent.click(toggle);
  const lines = within(alfa).getByRole("group", { name: "Позиции для ООО Альфа" });
  expect(within(lines).getByText("Провод МГТФ 0,12")).toBeInTheDocument();
  expect(within(lines).getByText("Кабель ПВС 2х0,75")).toBeInTheDocument();
  expect(within(lines).getByLabelText("Запросить SM-a у ООО Альфа")).toBeChecked();
  expect(within(lines).getByLabelText("Запросить SM-b у ООО Альфа")).not.toBeChecked();
  expect(within(lines).getByText("поставлял")).toBeInTheDocument();
  expect(screen.getByRole("toolbar", { name: "Создание запроса" })).toHaveTextContent("Поставщиков: 2 · запросов позиций: 3");
});

it("the supplier checkbox is half-ticked when a position is dropped and takes or drops all of them", async () => {
  await openSuppliers();
  const unknown = supplierRow("ИП Неизвестный");
  const box = within(unknown).getByLabelText("Спросить ИП Неизвестный") as HTMLInputElement;
  expect(box.checked).toBe(true);
  fireEvent.click(within(unknown).getByRole("button", { name: /позиций: 2 из 2/ }));
  fireEvent.click(within(unknown).getByLabelText("Запросить SM-b у ИП Неизвестный"));
  expect(box.checked).toBe(false);
  expect(box.indeterminate).toBe(true);
  expect(within(unknown).getByRole("button", { name: /позиций: 1 из 2/ })).toBeInTheDocument();

  fireEvent.click(box);  // half-ticked → all its positions again
  expect(box.checked).toBe(true);
  expect(box.indeterminate).toBe(false);
  expect(within(unknown).getByLabelText("Запросить SM-b у ИП Неизвестный")).toBeChecked();

  fireEvent.click(box);  // ticked → the supplier is dropped
  expect(within(unknown).getByLabelText("Запросить SM-a у ИП Неизвестный")).not.toBeChecked();
  expect(within(unknown).getByLabelText("Запросить SM-b у ИП Неизвестный")).not.toBeChecked();
  expect(screen.getByRole("toolbar", { name: "Создание запроса" })).toHaveTextContent("Поставщиков: 1 · запросов позиций: 1");
});

it("one position ticked by hand and the quick links go into the request", async () => {
  const posts: { path: string; body: unknown }[] = [];
  const created = await openSuppliers(posts);
  const alfa = supplierRow("ООО Альфа");
  fireEvent.click(within(alfa).getByRole("button", { name: /позиций: 1 из 2/ }));
  fireEvent.click(within(alfa).getByLabelText("Запросить SM-b у ООО Альфа"));
  expect(within(alfa).getByLabelText("Спросить ООО Альфа")).toBeChecked();
  expect(within(alfa).getByRole("button", { name: /позиций: 2 из 2/ })).toBeInTheDocument();

  const rotation = supplierRow("ООО Ротация");
  expect(within(rotation).getByLabelText("Спросить ООО Ротация")).not.toBeChecked();
  fireEvent.click(within(rotation).getByRole("button", { name: /позиций: 0 из 2/ }));
  fireEvent.click(within(rotation).getByRole("button", { name: "все" }));
  fireEvent.click(within(rotation).getByRole("button", { name: "только по причинам" }));
  expect(within(rotation).getByLabelText("Запросить SM-a у ООО Ротация")).not.toBeChecked();
  expect(within(rotation).getByLabelText("Запросить SM-b у ООО Ротация")).toBeChecked();
  expect(screen.getByRole("toolbar", { name: "Создание запроса" })).toHaveTextContent("Поставщиков: 3 · запросов позиций: 5");

  fireEvent.click(screen.getByRole("button", { name: "Создать запрос" }));
  await waitFor(() => expect(created).toHaveBeenCalledWith("r-new"));
  expect((posts[0].body as { suppliers: unknown[] }).suppliers).toEqual([
    { supplier_id: "s-al", reason: "items", line_ids: ["a", "b"] },
    { name: "ИП Неизвестный", reason: "category_history", line_ids: ["a", "b"] },
    { supplier_id: "s-rot", reason: "rotation", line_ids: ["b"] },
  ]);
});

it("«+ Запрос» opens the pool across the page instead of the request list", async () => {
  mockServer();
  render(<PurchasingRfq />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Запрос" }));
  expect(await screen.findByRole("region", { name: "Новый запрос КП" })).toBeInTheDocument();
  expect(screen.queryByText("Запросы КП")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Закрыть" }));
  expect(await screen.findByText("Запросы КП")).toBeInTheDocument();
});

it("warns when fewer suppliers than the gate wants, records an unticked rotation suggestion and the send date", async () => {
  const posts: { path: string; body: any }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") {
      posts.push({ path, body: JSON.parse(String(init.body)) });
      return json({ id: "r-new", number: "КП-2610-002", suppliers: 1 }, 201);
    }
    if (path === "/api/purchasing/rfqs/pool") return json(pool);
    if (path.startsWith("/api/purchasing/rfqs/suggest?")) {
      return json({ ...suggestion, gate_min_quotes: 3, suggestions: suggestion.suggestions.map((item) => (item.reason === "rotation" ? { ...item, preselect: true } : item)) });
    }
    if (path === "/api/purchasing/rfqs/directory") return json({ suppliers: [] });
    return json({}, 404);
  });
  render(<RfqPool onClose={() => undefined} onCreated={() => undefined} />);
  fireEvent.click(await screen.findByLabelText("Выбрать группу «Кабель и провод»"));
  fireEvent.click(screen.getByRole("button", { name: "Запросить КП (2)" }));
  expect(await screen.findByLabelText("Спросить ООО Ротация")).toBeChecked();
  expect(screen.queryByText(/для гейта нужно не меньше 3 КП/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("Спросить ООО Ротация"));
  expect(screen.getByText("Поставщиков: 2 — для гейта нужно не меньше 3 КП, спросите ещё кого-нибудь")).toBeInTheDocument();
  expect(screen.getByText(/Подсказку ротации не берёте \(ООО Ротация\)/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Дата отправки"), { target: { value: "2026-10-15" } });
  fireEvent.click(screen.getByRole("button", { name: "Создать запрос" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toMatchObject({ sent_on: "2026-10-15", declined_suggestions: ["s-rot"] });
});

