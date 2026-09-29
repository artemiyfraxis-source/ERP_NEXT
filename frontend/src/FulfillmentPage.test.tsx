import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { FulfillmentPage, lotSimilarity } from "./FulfillmentPage";
import type { FulfillmentRequest, Principal } from "./api";

const principal: Principal = {
  user: {
    id: "10000000-0000-0000-0000-000000000001",
    email: "receiver@example.test",
    name: "Кладовщик",
    department: "Склад",
  },
  roles: ["receiver"],
  permissions: { fulfillment: "edit", handout: "edit" },
};

const request: FulfillmentRequest = {
  id: "50000000-0000-0000-0000-000000000001",
  number: "FRX-ISS-26-0001",
  version: 3,
  created_at: "2026-09-22T10:00:00+03:00",
  requester: "Инженер",
  purpose: "FRX.001",
  deadline: "2026-10-01",
  status: "Новая",
  handout_location: null,
  return_location: null,
  fully_reserved: false,
  lines: [{
    id: "51000000-0000-0000-0000-000000000001",
    requirement_id: "52000000-0000-0000-0000-000000000001",
    article: "CON-001",
    name: "Разъём",
    unit: "шт",
    requirement: "4",
    reserved: "0",
    issued: "0",
    returned_good: "0",
    defective_returned: "0",
    defective_missing: "0",
    consumed: "0",
    lot: null,
  }],
};

const boundLine = {
  ...request.lines[0],
  reserved: "4",
  lot: { id: "40000000-0000-0000-0000-000000000001", warehouse: "ЦАР", grn: "FRX.26.0000001", package: "", storage_location: "1F4" },
};

const places = { racks: 3, shelves: 2, per_rack: { "2": 4 }, slots: "ABCDEFGHIJKLMNOPQRSTUVWXYZ" };

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

async function expand(number: string) {
  fireEvent.click((await screen.findAllByText(number))[0]);
}

afterEach(() => vi.restoreAllMocks());

it("binds a lot picked from the searchable list, the similar lot on top", async () => {
  let bindPayload: unknown;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/issues") return json({ requests: [request], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [
      { id: "40000000-0000-0000-0000-000000000003", warehouse: "ЖУК", grn: "FRX.26.0000003", package: "", article: "", name: "Болт", unit: "шт", storage_location: "2A1", available: "9" },
      { id: "40000000-0000-0000-0000-000000000001", warehouse: "ЦАР", grn: "FRX.26.0000001", package: "", article: "CON-001", name: "Разъём", unit: "шт", storage_location: "1F4", available: "10" },
      { id: "40000000-0000-0000-0000-000000000002", warehouse: "ЦАР", grn: "FRX.26.0000002", package: "", article: "", name: "Разъём угловой", unit: "шт", storage_location: "", available: "3" },
      { id: "40000000-0000-0000-0000-000000000004", warehouse: "ЦАР", grn: "FRX.26.0000004", package: "", article: "", name: "Разъём", unit: "м", storage_location: "", available: "3" },
    ] });
    if (path.endsWith("/bind") && init?.method === "POST") {
      bindPayload = JSON.parse(String(init.body));
      return json({ request });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<FulfillmentPage principal={principal} />);
  await expand("FRX-ISS-26-0001");
  fireEvent.click(await screen.findByRole("button", { name: "Выбрать ГРН" }));

  const picks = screen.getAllByRole("button", { name: /^Выбрать FRX/ }).map((button) => button.getAttribute("aria-label"));
  expect(picks).toEqual(["Выбрать FRX.26.0000001", "Выбрать FRX.26.0000002", "Выбрать FRX.26.0000003"]);
  expect(screen.getByText("меньше нужного")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Поиск партии для Разъём"), { target: { value: "угловой" } });
  expect(screen.getAllByRole("button", { name: /^Выбрать FRX/ })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Выбрать FRX.26.0000002" }));

  await waitFor(() => expect(bindPayload).toEqual({
    version: 3,
    lot_id: "40000000-0000-0000-0000-000000000002",
  }));
});

it("ranks the same article above a name match", () => {
  const line = { article: "CON-001", name: "Разъём" };
  expect(lotSimilarity(line, { article: "CON-001", name: "Вилка" })).toBeGreaterThan(lotSimilarity(line, { article: "", name: "Разъём" }));
  expect(lotSimilarity(line, { article: "", name: "Болт" })).toBe(0);
});

it("does not load lots or show editing actions to a view-only production role", async () => {
  const paths: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    paths.push(String(input));
    return json({ requests: [request], next_cursor: null });
  });

  render(<FulfillmentPage principal={{
    ...principal,
    roles: ["master"],
    permissions: { fulfillment: "view" },
  }} />);

  await expand("FRX-ISS-26-0001");
  expect(await screen.findByText("Ждём поставки")).toBeInTheDocument();
  expect(paths).toEqual(["/api/issues"]);
  expect(screen.queryByRole("button", { name: "Выбрать ГРН" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Отменить заявку" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "+ Заявка" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "🗑 Удалить заявку" })).not.toBeInTheDocument();
});

it("prints the issue sheet and, once something is issued, offers the return sheet too", async () => {
  const printedPaths: string[] = [];
  const printWindow = { document: { open: vi.fn(), write: vi.fn(), close: vi.fn() }, focus: vi.fn(), print: vi.fn() };
  vi.spyOn(window, "open").mockReturnValue(printWindow as unknown as Window);
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    printedPaths.push(path);
    if (path === "/api/issues") return json({ requests: [request], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    if (path.endsWith("/print")) return json({ sheet: {
      title: "ЗАЯВКА НА ВЫДАЧУ FRX-ISS-26-0001", subtitle: "", columns: [], rows: [], note: "", sign: "",
    } });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<FulfillmentPage principal={principal} />);
  await expand("FRX-ISS-26-0001");
  expect(await screen.findByRole("button", { name: "Печать заявки" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Печать листа возврата" })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Печать заявки" }));

  await waitFor(() => expect(printedPaths).toContain(`/api/issues/${request.id}/print`));
  await waitFor(() => expect(printWindow.print).toHaveBeenCalled());
  expect(printWindow.document.write.mock.calls[0][0]).toContain("ЗАЯВКА НА ВЫДАЧУ FRX-ISS-26-0001");
});

it("records good return and both defect quantities for every issued line", async () => {
  let payload: unknown;
  const returnedRequest: FulfillmentRequest = {
    ...request,
    version: 7,
    status: "КБ Возвращена",
    return_location: "Стеллаж возврата",
    lines: [{
      ...request.lines[0],
      issued: "4",
      consumed: "4",
      lot: boundLine.lot,
    }],
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/issues") return json({ requests: [returnedRequest], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    if (path.endsWith("/complete-return") && init?.method === "POST") {
      payload = JSON.parse(String(init.body));
      return json({ request: returnedRequest });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<FulfillmentPage principal={principal} />);
  await expand("FRX-ISS-26-0001");
  expect(await screen.findByRole("button", { name: "Печать листа возврата" })).toBeInTheDocument();
  fireEvent.change(await screen.findByLabelText("Годное Разъём"), { target: { value: "2" } });
  fireEvent.change(screen.getByLabelText("Брак возвращён Разъём"), { target: { value: "1" } });
  fireEvent.change(screen.getByLabelText("Брак не возвращён Разъём"), { target: { value: "0.5" } });
  fireEvent.change(screen.getByLabelText("Место хранения Разъём"), { target: { value: "2A1" } });
  fireEvent.click(screen.getByRole("button", { name: "Провести возврат" }));

  await waitFor(() => expect(payload).toEqual({
    version: 7,
    lines: [{
      line_id: returnedRequest.lines[0].id,
      returned_good: "2",
      defective_returned: "1",
      defective_missing: "0.5",
      storage_location: "2A1",
    }],
  }));
});

it("creates a manual request with a deadline and one idempotency key per form", async () => {
  const posts: Array<{ body: unknown; key: string | null }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/issues" && init?.method === "POST") {
      posts.push({ body: JSON.parse(String(init.body)), key: new Headers(init.headers).get("Idempotency-Key") });
      return json({ request });
    }
    if (path === "/api/issues") return json({ requests: [], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<FulfillmentPage principal={principal} mode="pick" />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Заявка" }));
  fireEvent.change(screen.getByLabelText("Назначение"), { target: { value: "48К.13" } });
  fireEvent.change(screen.getByLabelText("Наименование 1"), { target: { value: "Разъём" } });
  fireEvent.change(screen.getByLabelText("Потребность 1"), { target: { value: "4" } });
  fireEvent.click(screen.getByRole("button", { name: "+ строка" }));
  fireEvent.change(screen.getByLabelText("Наименование 2"), { target: { value: "Лишнее" } });
  fireEvent.click(screen.getByRole("button", { name: "Удалить строку 2" }));
  fireEvent.click(screen.getByRole("button", { name: "Оформить заявку" }));
  expect(await screen.findByText("Укажи дедлайн.")).toBeInTheDocument();
  expect(posts).toHaveLength(0);

  fireEvent.change(screen.getByLabelText("Дедлайн"), { target: { value: "2026-10-05" } });
  fireEvent.click(screen.getByRole("button", { name: "Оформить заявку" }));

  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts[0].body).toEqual({
    purpose: "48К.13",
    deadline: "2026-10-05",
    lines: [{ article: "", name: "Разъём", unit: "шт", requirement: "4" }],
  });
  expect(posts[0].key).toMatch(/[0-9a-f-]{36}/);
});

it("searches by number, purpose, requester and status and counts every stage in its chip", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/issues") return json({ requests: [
      request,
      { ...request, id: "r2", number: "FRX-ISS-26-0002", purpose: "708К.4 · КБ1 · Жгут", requester: "Петров", status: "Недоукомплектован" },
      { ...request, id: "r3", number: "FRX-ISS-26-0003", status: "Готов к выдаче" },
    ], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<FulfillmentPage principal={principal} mode="pick" />);
  expect(await screen.findByRole("button", { name: "Новая · 1" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Недоукомплектован · 1" })).toBeInTheDocument();
  // «Готов к выдаче» is done for the picker: hidden until ticked (ISS_MODES_.pick.hidden).
  expect(screen.queryByText("FRX-ISS-26-0003")).not.toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("показать «Готов к выдаче»"));
  expect(await screen.findByText("FRX-ISS-26-0003")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Поиск: Запросы на комплектацию"), { target: { value: "петров КБ1" } });
  expect(screen.getByText("FRX-ISS-26-0002")).toBeInTheDocument();
  expect(screen.queryByText("FRX-ISS-26-0001")).not.toBeInTheDocument();
});

it("opens a project with every request of it and a whole-order view", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/issues") return json({ requests: [
      { ...request, id: "k1", number: "REQ.26.0000001", purpose: "728К.2 · КБ1 · Жгут", project: "728К.2", status: "Недоукомплектован", lines: [boundLine] },
      { ...request, id: "k2", number: "REQ.26.0000002", purpose: "728К.2 · КБ2 · Жгут", project: "728К.2" },
      { ...request, id: "k3", number: "REQ.26.0000003", purpose: "Ручная", project: "" },
    ], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<FulfillmentPage principal={principal} mode="pick" />);
  fireEvent.click((await screen.findAllByRole("button", { name: "728К.2" }))[0]);

  const tabs = screen.getByRole("tablist", { name: "Заявки проекта" });
  expect(within(tabs).getByRole("tab", { name: "КБ1 ✓" })).toBeInTheDocument();
  expect(within(tabs).getByRole("tab", { name: "КБ2 · ждут 1" })).toBeInTheDocument();
  expect(screen.getByText(/набрано 0 из 1 поз\. по заказу/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "КБ2 0 / 4" }));
  expect(await screen.findByText("REQ.26.0000002")).toBeInTheDocument();
  expect(screen.queryByText("REQ.26.0000003")).not.toBeInTheDocument();
});

it("marks the kit complete once every line is closed and sends it to handout with a rack address", async () => {
  const calls: Array<{ path: string; body: unknown }> = [];
  let status = "Недоукомплектован";
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") {
      calls.push({ path, body: JSON.parse(String(init.body)) });
      status = path.endsWith("/complete") ? "Укомплектован" : "Готов к выдаче";
      return json({ request });
    }
    if (path === "/api/issues") return json({ requests: [{ ...request, status, lines: [boundLine], at_work: "2026-09-22T11:00:00" }], next_cursor: null, places });
    if (path === "/api/issues/lots") return json({ lots: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<FulfillmentPage principal={{ ...principal, roles: ["engineer"], permissions: { fulfillment: "edit" } }} />);
  await expand("FRX-ISS-26-0001");
  expect(await screen.findByText("Взята в работу")).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "✅ Укомплектован" }));
  await waitFor(() => expect(calls[0]).toEqual({ path: `/api/issues/${request.id}/complete`, body: { version: 3 } }));

  fireEvent.click(await screen.findByRole("button", { name: "📤 Отправить на выдачу" }));
  fireEvent.change(screen.getByLabelText("Стеллаж"), { target: { value: "2" } });
  expect(within(screen.getByLabelText("Полка")).getAllByRole("option")).toHaveLength(5);
  fireEvent.change(screen.getByLabelText("Полка"), { target: { value: "C" } });
  fireEvent.change(screen.getByLabelText("№ на полке"), { target: { value: "7" } });
  expect(screen.getByText("2C7")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Отправить" }));

  await waitFor(() => expect(calls[1]).toEqual({
    path: `/api/issues/${request.id}/ready`,
    body: { version: 3, place: { rack: "2", shelf: "C", num: "7", slot: "" } },
  }));
});

it("closes an issued request without return on the handout screen", async () => {
  let closed = "";
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") { closed = path; return json({ request }); }
    if (path === "/api/issues") return json({ requests: [{ ...request, status: "КБ Выдана", lines: [{ ...boundLine, reserved: "0", issued: "4" }] }], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<FulfillmentPage principal={principal} mode="give" />);
  await expand("FRX-ISS-26-0001");
  fireEvent.click(await screen.findByRole("button", { name: "Готово" }));

  await waitFor(() => expect(closed).toBe(`/api/issues/${request.id}/close`));
});

it("lets only the administrator delete a request", async () => {
  let deleted = "";
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "DELETE") { deleted = path; return json({ message: "Заявка удалена: позиций 1." }); }
    if (path === "/api/issues") return json({ requests: [request], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<FulfillmentPage principal={{ ...principal, roles: ["admin"] }} mode="pick" />);
  await expand("FRX-ISS-26-0001");
  fireEvent.click(await screen.findByRole("button", { name: "🗑 Удалить заявку" }));

  await waitFor(() => expect(deleted).toBe(`/api/issues/${request.id}`));
  expect(await screen.findByText("Заявка удалена: позиций 1.")).toBeInTheDocument();
});
