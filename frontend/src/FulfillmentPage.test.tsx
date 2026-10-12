import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { FulfillmentPage } from "./FulfillmentPage";
import { candidateTone } from "./FulfillmentBulkLots";
import type { FulfillmentRequest, Principal } from "./api";
import { capturePrintFrames } from "./testPrintFrames";

const principal: Principal = {
  user: {
    id: "10000000-0000-0000-0000-000000000001",
    email: "receiver@example.test",
    name: "Кладовщик",
    department: "Склад",
  },
  roles: ["foreman"],
  access_level: "foreman",
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

/* «Выдача со списанием» opens on «Укомплектован» + «КБ Возвращена»; «Все» shows every stage of the screen. */
async function showAll() {
  fireEvent.click(await screen.findByRole("button", { name: /^Все · / }));
}

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.removeItem("erp.fulfillment.view");
  window.localStorage.removeItem("erp.fulfillment.give.stages");
});

it("opens the lot search already filtered by the position name and binds the picked lot", async () => {
  let bindPayload: unknown;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/issues") return json({ requests: [request], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [
      { id: "40000000-0000-0000-0000-000000000003", warehouse: "ЖУК", grn: "FRX.26.0000003", package: "", article: "", name: "Болт", unit: "шт", storage_location: "2A1", available: "9" },
      { id: "40000000-0000-0000-0000-000000000001", warehouse: "ЦАР", grn: "FRX.26.0000001", package: "", article: "CON-001", name: "Разъём", unit: "шт", storage_location: "1F4", available: "10" },
      { id: "40000000-0000-0000-0000-000000000002", warehouse: "ЦАР", grn: "FRX.26.0000002", package: "", article: "", name: "Разъём угловой", unit: "шт", storage_location: "", available: "3" },
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

  expect(screen.getByLabelText("Поиск партии для Разъём")).toHaveValue("Разъём");
  const picks = screen.getAllByRole("button", { name: /^Выбрать FRX/ }).map((button) => button.getAttribute("aria-label"));
  expect(picks).toEqual(["Выбрать FRX.26.0000001", "Выбрать FRX.26.0000002"]);
  expect(screen.getByText("меньше нужного")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Поиск партии для Разъём"), { target: { value: "" } });
  expect(screen.getAllByRole("button", { name: /^Выбрать FRX/ })).toHaveLength(3);
  fireEvent.change(screen.getByLabelText("Поиск партии для Разъём"), { target: { value: "угловой" } });
  fireEvent.click(screen.getByRole("button", { name: "Выбрать FRX.26.0000002" }));

  await waitFor(() => expect(bindPayload).toEqual({
    version: 3,
    lot_id: "40000000-0000-0000-0000-000000000002",
  }));
});

it("does not load lots or show editing actions to a view-only grant", async () => {
  const paths: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    paths.push(String(input));
    return json({ requests: [request], next_cursor: null });
  });

  render(<FulfillmentPage principal={{
    ...principal,
    roles: ["foreman"],
    access_level: "foreman",
    permissions: { fulfillment: "view" },
  }} />);

  await expand("FRX-ISS-26-0001");
  expect(await screen.findByText("Ждём поставки")).toBeInTheDocument();
  expect(paths).toEqual(["/api/issues"]);
  expect(screen.queryByRole("button", { name: "Выбрать ГРН" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Есть, не заведено" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Нет на складе" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Отменить заявку" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "+ Заявка" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Удалить заявку" })).not.toBeInTheDocument();
});

// Owner 02.10.2026: the production director could not pick a kit for the storekeeper. Access levels (04.10.2026): the
// grant on the section decides, whoever holds it — here a technical lead with edit on picking and handout.
const director: Principal = { ...principal, roles: ["tech_lead"], access_level: "tech_lead", permissions: { fulfillment: "edit", handout: "edit" } };

it("lets a technical lead with edit on picking pick a kit for the storekeeper", async () => {
  const posts: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") { posts.push(path); return json({ request }); }
    if (path === "/api/issues") return json({ requests: [{ ...request, status: "Недоукомплектован", lines: [boundLine], at_work: "2026-09-22T11:00:00" }], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
  vi.spyOn(window, "confirm").mockReturnValue(true);

  render(<FulfillmentPage principal={director} />);
  await expand("FRX-ISS-26-0001");
  expect(await screen.findByRole("button", { name: "Снять резерв" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "+ Заявка" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Укомплектован" }));
  await waitFor(() => expect(posts).toEqual([`/api/issues/${request.id}/complete`]));
  // Deleting stays the administrator's cleanup.
  expect(screen.queryByRole("button", { name: "Удалить заявку" })).not.toBeInTheDocument();
});

it("lets a technical lead with edit on handout hand a kit out", async () => {
  const posts: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") { posts.push(path); return json({ request }); }
    if (path === "/api/issues") return json({ requests: [{ ...request, status: "Готов к выдаче", handout_location: "1B3", lines: [boundLine] }], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
  vi.spyOn(window, "confirm").mockReturnValue(true);

  render(<FulfillmentPage principal={director} mode="give" />);
  await showAll();
  await expand("FRX-ISS-26-0001");
  fireEvent.click(await screen.findByRole("button", { name: "Выдать комплект" }));
  await waitFor(() => expect(posts).toEqual(["/api/issues/issue-batch"]));
});

it("prints the issue sheet and, once something is issued, offers the return sheet too", async () => {
  const printedPaths: string[] = [];
  const open = vi.spyOn(window, "open");
  const printed = capturePrintFrames();
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
  await waitFor(() => expect(printed.print).toHaveBeenCalled());
  expect(printed.html[0]).toContain("ЗАЯВКА НА ВЫДАЧУ FRX-ISS-26-0001");
  expect(open).not.toHaveBeenCalled();
  printed.restore();
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

it("creates a manual request from warehouse lots picked FIFO, as IssueForm", async () => {
  const posts: Array<{ body: unknown; key: string | null }> = [];
  const lot = (id: string, grn: string, available: string, warehouse = "ЦАР") => ({
    id, warehouse, grn, package: "", article: "", name: "Разъём", unit: "шт", storage_location: "1F4", available,
  });
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/issues" && init?.method === "POST") {
      posts.push({ body: JSON.parse(String(init.body)), key: new Headers(init.headers).get("Idempotency-Key") });
      return json({ request });
    }
    if (path === "/api/issues") return json({ requests: [], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [
      lot("l3", "FRX.26.0000010", "5"),
      lot("l1", "OLD.25.0000500", "3"),
      lot("l2", "FRX.26.0000002", "2"),
      lot("l4", "FRX.26.0000001", "9", "ЖУК"),
    ] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<FulfillmentPage principal={principal} mode="pick" />);
  fireEvent.click(await screen.findByRole("button", { name: "+ Заявка" }));
  fireEvent.change(screen.getByLabelText("Назначение"), { target: { value: "48К.13" } });
  fireEvent.change(screen.getByLabelText("Поиск по складу"), { target: { value: "разъ" } });
  fireEvent.click(screen.getByRole("option", { name: "Разъём — доступно 10 шт" }));
  fireEvent.change(screen.getByLabelText("Кол-во"), { target: { value: "12" } });
  fireEvent.click(screen.getByRole("button", { name: "Добавить" }));
  expect(screen.getByText("Недобор (на складе меньше запрошенного): Разъём: −2")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Оформить заявку" }));
  expect(await screen.findByText("Укажи дедлайн.")).toBeInTheDocument();
  expect(posts).toHaveLength(0);

  fireEvent.change(screen.getByLabelText("Дедлайн"), { target: { value: "2026-10-05" } });
  fireEvent.click(screen.getByRole("button", { name: "Оформить заявку" }));

  await waitFor(() => expect(posts).toHaveLength(1));
  // FIFO: the 2025 lot first, then 2026 by number; the ЖУК lot is another warehouse.
  expect(posts[0].body).toEqual({
    purpose: "48К.13",
    deadline: "2026-10-05",
    lines: [
      { article: "", name: "Разъём", unit: "шт", requirement: "3", lot_id: "l1" },
      { article: "", name: "Разъём", unit: "шт", requirement: "2", lot_id: "l2" },
      { article: "", name: "Разъём", unit: "шт", requirement: "5", lot_id: "l3" },
    ],
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
  expect(await screen.findByRole("button", { name: "Не начата · 1" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Недоукомплектован · 1" })).toBeInTheDocument();
  // «Готов к выдаче» is not a stage of its own any more: it counts in «Укомплектован», no checkbox, no chip.
  expect(screen.getByRole("button", { name: "Укомплектован · 1" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^Готов к выдаче/ })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("показать «Готов к выдаче»")).not.toBeInTheDocument();
  expect(screen.queryByText("FRX-ISS-26-0003")).not.toBeInTheDocument();
  await showAll();
  expect(await screen.findByText("FRX-ISS-26-0003")).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Поиск: Запросы на комплектацию"), { target: { value: "петров КБ1" } });
  // A request of a project is found inside its project tile.
  expect(screen.getByRole("button", { name: /^708К\.4/ })).toBeInTheDocument();
  expect(screen.queryByText("FRX-ISS-26-0001")).not.toBeInTheDocument();
  // issFilterRender_: the chips count what the search left.
  expect(screen.getByRole("button", { name: "Не начата · 0" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Недоукомплектован · 1" })).toBeInTheDocument();
});

function kitRequests() {
  return [
    request,
    { ...request, id: "r2", number: "FRX-ISS-26-0002", purpose: "708К.4 · КБ1 · Жгут", project: "708К.4", status: "Недоукомплектован" },
    { ...request, id: "r4", number: "FRX-ISS-26-0004", purpose: "708К.4 · КБ2 · Жгут", project: "708К.4", status: "Укомплектован" },
    { ...request, id: "r5", number: "FRX-ISS-26-0005", purpose: "", project: "", status: "Укомплектован" },
  ];
}

function mockKit() {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/issues") return json({ requests: kitRequests(), next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
}

it("opens the picking screen on «К набору»: new and short requests only, «Новая» reads «Не начата», «Все» is last", async () => {
  mockKit();
  render(<FulfillmentPage principal={principal} mode="pick" />);
  const chip = await screen.findByRole("button", { name: "К набору · 2" });
  expect(chip).toHaveAttribute("aria-pressed", "true");
  const labels = within(screen.getByRole("group", { name: "Стадии" })).getAllByRole("button").map((button) => button.textContent);
  expect(labels).toEqual(["К набору · 2", "Не начата · 1", "Недоукомплектован · 1", "Укомплектован · 2", "Все · 4"]);
  expect(screen.getByRole("button", { name: "Не начата · 1" })).toHaveAttribute("title", "заявка пришла, комплектовщик ещё ничего не набрал");
  expect(screen.queryByText("Новая")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^708К.4/ })).toBeInTheDocument();
  expect(screen.getByText("FRX-ISS-26-0001")).toBeInTheDocument();
  expect(screen.queryByText("FRX-ISS-26-0005")).not.toBeInTheDocument();
  // The tile and the project card count only what the chip lets through: one КБ of 708К.4, not both.
  expect(screen.getByText("сборок: 1")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /^708К.4/ }));
  expect(await screen.findByRole("tab", { name: /КБ1/ })).toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: /КБ2/ })).not.toBeInTheDocument();
});

it("«Все» and a stage chip widen or narrow the picking screen; the table opens on the same chip", async () => {
  mockKit();
  render(<FulfillmentPage principal={principal} mode="pick" />);
  fireEvent.click(await screen.findByRole("button", { name: "Все · 4" }));
  expect(screen.getByText("FRX-ISS-26-0005")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Укомплектован · 2" }));
  expect(screen.queryByText("FRX-ISS-26-0001")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "К набору · 2" }));
  fireEvent.click(screen.getByRole("button", { name: "Таблицей" }));
  const group = await screen.findByRole("group", { name: "Быстрый отбор" });
  expect(within(group).getByRole("button", { name: "К набору · 2" })).toHaveAttribute("aria-pressed", "true");
  expect(within(group).getAllByRole("button").at(-1)).toHaveTextContent("Все · 4");
  expect(screen.queryByText("FRX-ISS-26-0005")).not.toBeInTheDocument();
  expect(screen.getByText("FRX-ISS-26-0001")).toBeInTheDocument();
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
  fireEvent.click(await screen.findByRole("button", { name: /^728К\.2/ }));

  const tabs = screen.getByRole("tablist", { name: "Заявки проекта" });
  expect(within(tabs).getByRole("tab", { name: "КБ1 ✓" })).toBeInTheDocument();
  expect(within(tabs).getByRole("tab", { name: "КБ2 · ждут 1" })).toBeInTheDocument();
  expect(screen.getByText(/набрано 0 из 1 поз\. по заказу/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "КБ2 0 / 4" }));
  expect(await screen.findByText("REQ.26.0000002")).toBeInTheDocument();
  expect(screen.queryByText("REQ.26.0000003")).not.toBeInTheDocument();
});

it("shows a project's requests as one tile and a manual request as its own, as SUPPLYMAP issRender", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/issues") return json({ requests: [
      { ...request, id: "k1", number: "REQ.26.0000001", purpose: "728К.2 · КБ1 · Жгут", requester: "Иванов", deadline: "2026-10-12", status: "Недоукомплектован", shortfall: "Провод: −2" },
      { ...request, id: "k2", number: "REQ.26.0000002", purpose: "728К.2 · КБ2 · Жгут", requester: "Петров", deadline: "2026-10-08" },
      { ...request, id: "k3", number: "REQ.26.0000003", purpose: "728К.2 · КБ3 · Кабель", requester: "Иванов", deadline: "2026-10-20" },
      { ...request, id: "k4", number: "REQ.26.0000004", purpose: "Ремонт стенда", requester: "Сидоров" },
    ], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<FulfillmentPage principal={principal} mode="pick" />);
  const tile = await screen.findByRole("button", { name: /^728К\.2/ });
  expect(within(tile).getByText("сборок: 3")).toBeInTheDocument();
  expect(within(tile).getByText("Сборки: КБ1 · КБ2 · КБ3")).toBeInTheDocument();
  expect(within(tile).getByText("×2")).toBeInTheDocument();
  expect(within(tile).getByText("с недобором: 1 из 3")).toBeInTheDocument();
  expect(within(tile).getByText("Иванов, Петров · до 08.10.2026 · позиций 3")).toBeInTheDocument();
  expect(screen.queryByText("REQ.26.0000001")).not.toBeInTheDocument();

  fireEvent.click(screen.getByText("REQ.26.0000004"));
  expect(await screen.findByRole("button", { name: "Печать заявки" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "← К заявкам" }));
  fireEvent.click(screen.getByRole("button", { name: "Таблицей" }));
  expect(screen.getByText("REQ.26.0000001")).toBeInTheDocument();
  expect(window.localStorage.getItem("erp.fulfillment.view")).toBe("table");
});

// A request every line of which got its lot before the automatic «Укомплектован» (or at creation) still has the button.
it("marks the kit complete once every line is closed and sends it to handout with another rack address", async () => {
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

  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<FulfillmentPage principal={{ ...principal, roles: ["tech_lead"], access_level: "tech_lead", permissions: { fulfillment: "edit" } }} />);
  await expand("FRX-ISS-26-0001");
  expect(await screen.findByText("Взята в работу")).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "Укомплектован" }));
  await waitFor(() => expect(calls[0]).toEqual({ path: `/api/issues/${request.id}/complete`, body: { version: 3 } }));

  // No place used at the site yet: «Зона выдачи» is offered, «Другое место» opens the form with it filled in.
  expect(await screen.findByText("Зона выдачи")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Другое место" }));
  expect(screen.getByLabelText("Место выдачи текстом")).toHaveValue("Зона выдачи");
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

it("sends a complete kit to handout in one click with the place the server offers", async () => {
  const calls: Array<{ path: string; body: unknown }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") { calls.push({ path, body: JSON.parse(String(init.body)) }); return json({ request }); }
    if (path === "/api/issues") return json({ requests: [{ ...request, status: "Укомплектован", handout_place: "2B3", fully_reserved: true, lines: [boundLine] }], next_cursor: null, places });
    if (path === "/api/issues/lots") return json({ lots: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);

  render(<FulfillmentPage principal={principal} />);
  await expand("FRX-ISS-26-0001");
  expect(await screen.findByText("2B3")).toBeInTheDocument();
  // The manual «Укомплектован» is gone: the last bound line completed the kit.
  expect(screen.queryByRole("button", { name: "Укомплектован" })).not.toBeInTheDocument();
  // «Другое место» opens the rack fields already set to the offered address.
  fireEvent.click(screen.getByRole("button", { name: "Другое место" }));
  expect(screen.getByLabelText("Стеллаж")).toHaveValue("2");
  expect(screen.getByLabelText("Полка")).toHaveValue("B");
  expect(screen.getByLabelText("№ на полке")).toHaveValue(3);
  fireEvent.click(screen.getByRole("button", { name: "Отмена" }));

  fireEvent.click(screen.getByRole("button", { name: "Отправить на выдачу" }));
  await waitFor(() => expect(calls).toEqual([{
    path: `/api/issues/${request.id}/ready`,
    body: { version: 3, handout_location: "2B3" },
  }]));
  expect(confirm).not.toHaveBeenCalled();
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

  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<FulfillmentPage principal={principal} mode="give" />);
  await showAll();
  await expand("FRX-ISS-26-0001");
  // issMove_: every stage move asks first; «Отмена» in the dialog leaves the request as it is.
  fireEvent.click(await screen.findByRole("button", { name: "Готово" }));
  expect(confirm).toHaveBeenCalledWith("Заявка FRX-ISS-26-0001 → Готово\n\nЗаявка закроется и уйдёт в архив.");
  expect(closed).toBe("");
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Готово" }));

  await waitFor(() => expect(closed).toBe(`/api/issues/${request.id}/close`));
});

it("warns that handing the kit out writes the stock off before it does", async () => {
  const posts: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") { posts.push(path); return json({ request }); }
    if (path === "/api/issues") return json({ requests: [{ ...request, status: "Готов к выдаче", handout_location: "1B3", lines: [boundLine] }], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);

  render(<FulfillmentPage principal={principal} mode="give" />);
  await showAll();
  await expand("FRX-ISS-26-0001");
  fireEvent.click(await screen.findByRole("button", { name: "Выдать комплект" }));

  expect(confirm.mock.calls[0][0]).toContain("Количество спишется со склада");
  await waitFor(() => expect(posts).toEqual(["/api/issues/issue-batch"]));
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

  render(<FulfillmentPage principal={{ ...principal, roles: ["admin"], access_level: "admin" }} mode="pick" />);
  await expand("FRX-ISS-26-0001");
  fireEvent.click(await screen.findByRole("button", { name: "Удалить заявку" }));

  await waitFor(() => expect(deleted).toBe(`/api/issues/${request.id}`));
  expect(await screen.findByText("Заявка удалена: позиций 1.")).toBeInTheDocument();
});

it("offers «Удалить заявку» to superadmin and admin alone, never by a grant on picking", async () => {
  const mocks = () => vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/issues") return json({ requests: [request], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
  for (const level of ["commercial", "tech_lead", "foreman", "operator"]) {
    mocks();
    const view = render(<FulfillmentPage principal={{ ...principal, roles: [level], access_level: level, permissions: { fulfillment: "edit", handout: "edit" } }} mode="pick" />);
    await expand("FRX-ISS-26-0001");
    await screen.findByRole("button", { name: "Выбрать ГРН" });
    expect(screen.queryByRole("button", { name: "Удалить заявку" })).not.toBeInTheDocument();
    view.unmount();
    vi.restoreAllMocks();
  }
  mocks();
  render(<FulfillmentPage principal={{ ...principal, roles: ["superadmin"], access_level: "superadmin", permissions: {} }} mode="pick" />);
  await expand("FRX-ISS-26-0001");
  expect(await screen.findByRole("button", { name: "Удалить заявку" })).toBeInTheDocument();
});

it("creates a request from a calculation link: preview by warehouse, picks and «нет на складе» (IssueCalc)", async () => {
  const posts: Array<{ path: string; body: unknown }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") {
      posts.push({ path, body: JSON.parse(String(init.body)) });
      if (path === "/api/issues/calc-preview") return json({ project: "591К.3", lines: [
        { name: "МГТФ 0,35", qty: "12.5", unit: "м", found: true, short: "2.5", picks: [
          { lot_id: "l1", warehouse: "ЦАР", grn: "OLD.25.0000009", package: "", article: "", name: "МГТФ 0,35", unit: "м", storage_location: "1A1", qty: "10" },
        ] },
        { name: "Разъём Molex", qty: "5", unit: "шт", found: true, short: "0", picks: [
          { lot_id: "l2", warehouse: "ЦАР", grn: "FRX.26.0000005", package: "", article: "", name: "Разъём Molex", unit: "шт", storage_location: "1F4", qty: "5", reserved: "3" },
        ] },
        { name: "DB-9M", qty: "4", unit: "шт", found: false, short: "4", picks: [] },
      ] });
      return json({ request });
    }
    if (path === "/api/issues") return json({ requests: [], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<FulfillmentPage principal={principal} mode="pick" />);
  fireEvent.click(await screen.findByRole("button", { name: "Заявка по расчёту" }));
  fireEvent.click(screen.getByRole("button", { name: "Подобрать" }));
  expect(await screen.findByText("Вставь ссылку на расчёт.")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Ссылка на расчёт"), { target: { value: "https://docs.google.com/spreadsheets/d/X/edit" } });
  fireEvent.change(screen.getByLabelText("Склад расчёта"), { target: { value: "ЖУК" } });
  fireEvent.click(screen.getByRole("button", { name: "Подобрать" }));

  expect(await screen.findByText("Позиций: 3 · полностью: 1 · с недобором: 1 · нет на складе: 1")).toBeInTheDocument();
  expect(posts[0]).toEqual({ path: "/api/issues/calc-preview", body: { url: "https://docs.google.com/spreadsheets/d/X/edit", warehouse: "ЖУК" } });
  expect(screen.getByLabelText("Назначение по расчёту")).toHaveValue("591К.3");
  expect(screen.getByText("OLD.25.0000009: 10")).toBeInTheDocument();
  expect(screen.getByText("FRX.26.0000005: 5 (резерв под заявку 3)")).toBeInTheDocument();
  expect(screen.getByText("недобор 2.5")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Дедлайн по расчёту"), { target: { value: "2026-10-05" } });
  fireEvent.click(screen.getByRole("button", { name: "Оформить заявку" }));

  await waitFor(() => expect(posts).toHaveLength(2));
  expect(posts[1]).toEqual({ path: "/api/issues", body: {
    purpose: "591К.3",
    deadline: "2026-10-05",
    lines: [
      { article: "", name: "МГТФ 0,35", unit: "м", requirement: "10", lot_id: "l1" },
      { article: "", name: "Разъём Molex", unit: "шт", requirement: "5", lot_id: "l2" },
    ],
    short_note: ["DB-9M: нет на складе (−4)"],
  } });
});

it("walks every page of requests, not only the first hundred", async () => {
  const second = { ...request, id: "second-page-id", number: "FRX-ISS-26-0101", purpose: "900К.1 · КБ2" };
  const paths: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    paths.push(path);
    if (path === "/api/issues") return json({ requests: [request], next_cursor: request.id });
    if (path === `/api/issues?after=${request.id}`) return json({ requests: [second], next_cursor: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<FulfillmentPage principal={{ ...principal, roles: ["foreman"], access_level: "foreman", permissions: { fulfillment: "view" } }} />);

  expect(await screen.findByRole("button", { name: /^900К\.1/ })).toBeInTheDocument();
  expect(screen.getByText("FRX-ISS-26-0001")).toBeInTheDocument();
  expect(paths).toEqual(["/api/issues", `/api/issues?after=${request.id}`]);
});

/* «ГРН на все КБ» and «Есть, не заведено» (owner 05.10.2026). */
const kb1 = { ...request, id: "k1", number: "REQ.26.0000001", purpose: "728К.2 · КБ1 · Жгут", project: "728К.2" };
const kb2 = { ...request, id: "k2", version: 5, number: "REQ.26.0000002", purpose: "728К.2 · КБ2 · Жгут", project: "728К.2", lines: [{ ...request.lines[0], id: "line-2" }] };
const projectLots = [
  { id: "lot-2", warehouse: "ЦАР", grn: "FRX.26.0000002", package: "", article: "CON-001", name: "Разъём", unit: "шт", storage_location: "1F5", available: "10" },
  { id: "lot-1", warehouse: "ЦАР", grn: "FRX.26.0000001", package: "", article: "CON-001", name: "Разъём", unit: "шт", storage_location: "1F4", available: "5" },
];

async function openWholeOrder() {
  render(<FulfillmentPage principal={principal} mode="pick" />);
  await showAll();
  fireEvent.click(await screen.findByRole("button", { name: /^728К\.2/ }));
}

/* «Возможно есть» (owner 12.10.2026): the column, the row colour and the legend of the project card. */
const candidateOf = (lot: (typeof projectLots)[number], kind: "exact" | "fuzzy", available = lot.available) =>
  ({ ...lot, lot_id: lot.id, kind, match: kind === "exact" ? "exact" : "fuzzy-separators", available });

async function openWithCandidates(candidates: ReturnType<typeof candidateOf>[], more = 0) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/issues") return json({ requests: [{ ...kb1, status: "Недоукомплектован" }, { ...kb2, status: "Недоукомплектован" }], next_cursor: null });
    if (path.startsWith("/api/issues/lots")) return json({ lots: projectLots });
    if (path === "/api/issues/projects/728%D0%9A.2/candidates") return json({ positions: [{ article: "CON-001", name: "Разъём", unit: "шт", candidates, more }] });
    throw new Error(`Unexpected request: ${path}`);
  });
  render(<FulfillmentPage principal={principal} mode="pick" />);
  fireEvent.click(await screen.findByRole("button", { name: /^728К\.2/ }));
}

const positionRow = () => screen.getByRole("cell", { name: "CON-001" }).closest("tr")!;

it("candidateTone: green exact and enough, yellow lookalike or too little, red nothing, neutral picked", () => {
  const exact = (available: string) => candidateOf(projectLots[0], "exact", available);
  expect(candidateTone(8, [exact("10")])).toBe("ok");
  expect(candidateTone(8, [exact("5"), candidateOf(projectLots[1], "exact", "3")])).toBe("ok");
  expect(candidateTone(8, [exact("5")])).toBe("maybe");
  expect(candidateTone(8, [candidateOf(projectLots[0], "fuzzy", "100")])).toBe("maybe");
  expect(candidateTone(8, [])).toBe("none");
  expect(candidateTone(0, [])).toBe("done");
});

it("shows the best lot of «Возможно есть» with «ещё N», a green row and the legend; a click opens the lot list with it marked", async () => {
  await openWithCandidates([candidateOf(projectLots[0], "exact"), candidateOf(projectLots[1], "fuzzy")], 3);

  expect(await screen.findByText("ГРН FRX.26.0000002 · 10 шт · ЦАР")).toBeInTheDocument();
  expect(screen.getByText("тот же артикул · ещё 4")).toBeInTheDocument();
  expect(positionRow()).toHaveClass("cand-ok");
  expect(screen.getByLabelText("Цвета строк")).toHaveTextContent("на складе не найдено");

  fireEvent.click(screen.getByText("ГРН FRX.26.0000002 · 10 шт · ЦАР"));
  expect(await screen.findByRole("group", { name: "ГРН на все КБ: Разъём" })).toBeInTheDocument();
  expect(screen.getByText("возможно есть · тот же артикул")).toBeInTheDocument();
  expect(screen.getByText("возможно есть · похожий артикул")).toBeInTheDocument();
});

it("paints the row yellow for lookalikes only or too little exact, red when nothing is found", async () => {
  await openWithCandidates([candidateOf(projectLots[1], "fuzzy")]);
  expect(await screen.findByText("похожий артикул")).toBeInTheDocument();
  expect(positionRow()).toHaveClass("cand-maybe");
  cleanup();
  vi.restoreAllMocks();

  await openWithCandidates([]);
  expect(await screen.findByText("не найдено")).toBeInTheDocument();
  expect(positionRow()).toHaveClass("cand-none");
});

it("«Возможно есть» is for those who can pick: the viewer gets neither the column nor the request", async () => {
  const paths: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    paths.push(path);
    if (path === "/api/issues") return json({ requests: [{ ...kb1, status: "Недоукомплектован" }], next_cursor: null });
    throw new Error(`Unexpected request: ${path}`);
  });
  render(<FulfillmentPage principal={{ ...principal, roles: ["foreman"], access_level: "foreman", permissions: { fulfillment: "view" } }} mode="pick" />);
  fireEvent.click(await screen.findByRole("button", { name: /^728К\.2/ }));
  await screen.findByRole("cell", { name: "CON-001" });
  expect(screen.queryByText("Возможно есть")).not.toBeInTheDocument();
  expect(paths.some((path) => path.includes("candidates"))).toBe(false);
});

it("counts a line «нет на складе» as waiting on its КБ chip and names it in the order, never as «есть»", async () => {
  const absentKb2 = { ...kb2, status: "Недоукомплектован", lines: [{ ...kb2.lines[0], absent_at: "2026-10-12T09:30:00", absent_by: "Кладовщик" }] };
  const offKb1 = { ...kb1, status: "Укомплектован", lines: [{ ...kb1.lines[0], off_book: true }] };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/issues") return json({ requests: [offKb1, absentKb2], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  await openWholeOrder();
  const tabs = await screen.findByRole("tablist", { name: "Заявки проекта" });
  expect(within(tabs).getByRole("tab", { name: "КБ1 ✓" })).toBeInTheDocument();
  expect(within(tabs).getByRole("tab", { name: "КБ2 · ждут 1 (нет 1)" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "КБ2 0 / 4 · нет на складе" })).toBeInTheDocument();
  expect(screen.getByText("4 нет на складе")).toBeInTheDocument();
  expect(screen.getByText("4 есть, не заведено")).toBeInTheDocument();
});

it("picks several lots for every КБ at once, previews the FIFO split and reports what was assigned", async () => {
  let payload: any;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/issues") return json({ requests: [kb1, kb2], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    if (path.startsWith("/api/issues/lots?project=")) return json({ lots: projectLots });
    if (path === `/api/issues/projects/${encodeURIComponent("728К.2")}/bind-lots`) {
      payload = JSON.parse(String(init?.body));
      return json({ bound: [
        { request_id: "k1", number: "REQ.26.0000001", kb: "КБ1", line_id: "l", lot_id: "lot-1", grn: "FRX.26.0000001", quantity: "4" },
        { request_id: "k2", number: "REQ.26.0000002", kb: "КБ2", line_id: "line-2", lot_id: "lot-1", grn: "FRX.26.0000001", quantity: "1" },
        { request_id: "k2", number: "REQ.26.0000002", kb: "КБ2", line_id: "line-2", lot_id: "lot-2", grn: "FRX.26.0000002", quantity: "3" },
      ], short: 0, message: "" });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  await openWholeOrder();
  fireEvent.click(await screen.findByRole("button", { name: "ГРН на все КБ" }));
  const assign = screen.getByRole("button", { name: "Назначить ГРН на все КБ" });
  expect(assign).toBeDisabled();
  fireEvent.click(await screen.findByRole("checkbox", { name: "ГРН FRX.26.0000002" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "ГРН FRX.26.0000001" }));

  const preview = screen.getByRole("table", { name: "Предварительная раскладка" });
  expect(within(preview).getByText("Предварительно, окончательно разложит сервер")).toBeInTheDocument();
  const rows = within(preview).getAllByRole("row");
  expect(rows[1]).toHaveTextContent("КБ1");
  expect(rows[1]).toHaveTextContent("FRX.26.0000001 4 шт");
  expect(rows[2]).toHaveTextContent("FRX.26.0000001 1 шт; FRX.26.0000002 3 шт");

  fireEvent.click(assign);
  expect(await screen.findByText("Назначено: КБ1 — ГРН FRX.26.0000001 4 шт; КБ2 — ГРН FRX.26.0000001 1 шт, ГРН FRX.26.0000002 3 шт")).toBeInTheDocument();
  expect(payload).toEqual({ article: "CON-001", name: "Разъём", lot_ids: ["lot-2", "lot-1"], off_book: false, versions: { k1: 3, k2: 5 } });
});

it("says how much is still missing when the chosen lots do not cover every КБ", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/issues") return json({ requests: [kb1, kb2], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    if (path.startsWith("/api/issues/lots?project=")) return json({ lots: projectLots });
    if (path.endsWith("/bind-lots")) return json({ bound: [{ request_id: "k1", number: "n", kb: "КБ1", line_id: "l", lot_id: "lot-1", grn: "FRX.26.0000001", quantity: "4" }, { request_id: "k2", number: "n", kb: "КБ2", line_id: "l2", lot_id: "lot-1", grn: "FRX.26.0000001", quantity: "1" }], short: "3" });
    throw new Error(`Unexpected request: ${path}`);
  });
  await openWholeOrder();
  fireEvent.click(await screen.findByRole("button", { name: "ГРН на все КБ" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "ГРН FRX.26.0000001" }));
  expect(within(screen.getByRole("table", { name: "Предварительная раскладка" })).getAllByRole("row")[2]).toHaveTextContent("3");
  fireEvent.click(screen.getByRole("button", { name: "Назначить ГРН на все КБ" }));
  expect(await screen.findByText(/; не хватает 3$/)).toBeInTheDocument();
});

it("reloads the requests and says so when a КБ changed under the bulk pick (409)", async () => {
  let loads = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/issues") { loads += 1; return json({ requests: [kb1, kb2], next_cursor: null }); }
    if (path === "/api/issues/lots") return json({ lots: [] });
    if (path.startsWith("/api/issues/lots?project=")) return json({ lots: projectLots });
    if (path.endsWith("/bind-lots")) return new Response(JSON.stringify({ detail: "Версия заявки устарела" }), { status: 409, headers: { "Content-Type": "application/json" } });
    throw new Error(`Unexpected request: ${path}`);
  });
  await openWholeOrder();
  fireEvent.click(await screen.findByRole("button", { name: "ГРН на все КБ" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "ГРН FRX.26.0000001" }));
  fireEvent.click(screen.getByRole("button", { name: "Назначить ГРН на все КБ" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Заявки изменились, пока выбирали ГРН");
  expect(loads).toBe(2);
});

it("shows a failed lot load honestly and retries it", async () => {
  let lotCalls = 0;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/issues") return json({ requests: [kb1, kb2], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    if (path.startsWith("/api/issues/lots?project=")) {
      lotCalls += 1;
      return lotCalls === 1 ? new Response(JSON.stringify({ detail: "Склад недоступен" }), { status: 500, headers: { "Content-Type": "application/json" } }) : json({ lots: projectLots });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  await openWholeOrder();
  fireEvent.click(await screen.findByRole("button", { name: "ГРН на все КБ" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Склад недоступен");
  fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
  expect(await screen.findByRole("checkbox", { name: "ГРН FRX.26.0000001" })).toBeInTheDocument();
});

it("marks a whole position «есть, не заведено» for every КБ", async () => {
  let payload: any;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/issues") return json({ requests: [kb1, kb2], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    if (path.endsWith("/bind-lots")) { payload = JSON.parse(String(init?.body)); return json({ bound: [], short: 0, message: "Отмечено: КБ1, КБ2" }); }
    throw new Error(`Unexpected request: ${path}`);
  });
  await openWholeOrder();
  fireEvent.click(await screen.findByRole("button", { name: "Есть, не заведено: Разъём" }));
  expect(await screen.findByText("Отмечено: КБ1, КБ2")).toBeInTheDocument();
  expect(payload).toEqual({ article: "CON-001", name: "Разъём", lot_ids: [], off_book: true, versions: { k1: 3, k2: 5 } });
});

it("marks one line «есть, не заведено», counts it as closed and takes the mark off", async () => {
  const calls: Array<[string, string, unknown]> = [];
  let marked = false;
  const url = `/api/issues/${request.id}/lines/${request.lines[0].id}/off-book`;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/issues") return json({ requests: [{ ...request, version: marked ? 4 : 3, lines: [{ ...request.lines[0], off_book: marked }] }], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    if (path === url) {
      calls.push([init?.method ?? "GET", path, JSON.parse(String(init?.body))]);
      marked = init?.method === "POST";
      return json({ request });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  render(<FulfillmentPage principal={principal} mode="pick" />);
  await expand(request.number);
  expect(await screen.findByText("Набрано 0 из 1 поз. · ждут ГРН: 1")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Есть, не заведено" }));
  expect(await screen.findByText("Набрано 1 из 1 поз.")).toBeInTheDocument();
  expect(screen.getByText("есть, не заведено")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Снять отметку" }));
  expect(await screen.findByText("Набрано 0 из 1 поз. · ждут ГРН: 1")).toBeInTheDocument();
  expect(calls).toEqual([["POST", url, { version: 3 }], ["DELETE", url, { version: 4 }]]);
});

it("marks one line «нет на складе»: it stays open, shows who and when, and the mark comes off", async () => {
  const calls: Array<[string, string, unknown]> = [];
  let marked = false;
  const url = `/api/issues/${request.id}/lines/${request.lines[0].id}/absent`;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    const line = marked ? { ...request.lines[0], absent_at: "2026-10-12T09:30:00", absent_by: "Кладовщик" } : request.lines[0];
    if (path === "/api/issues") return json({ requests: [{ ...request, version: marked ? 4 : 3, status: marked ? "Недоукомплектован" : "Новая", lines: [line] }], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    if (path === url) {
      calls.push([init?.method ?? "GET", path, JSON.parse(String(init?.body))]);
      marked = init?.method === "POST";
      return json({ request });
    }
    throw new Error(`Unexpected request: ${path}`);
  });
  render(<FulfillmentPage principal={principal} mode="pick" />);
  await expand(request.number);
  fireEvent.click(await screen.findByRole("button", { name: "Нет на складе" }));
  expect(await screen.findByText("Набрано 0 из 1 поз. · нет на складе: 1")).toBeInTheDocument();
  expect(screen.getByText("нет на складе")).toBeInTheDocument();
  expect(screen.getByText("Кладовщик · 12.10.2026, 09:30")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Есть, не заведено" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Укомплектован" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Снять отметку" }));
  expect(await screen.findByText("Набрано 0 из 1 поз. · ждут ГРН: 1")).toBeInTheDocument();
  expect(calls).toEqual([["POST", url, { version: 3 }], ["DELETE", url, { version: 4 }]]);
});

/* Owner 06.10.2026, project 33МФ.2: КБ1 and КБ2 «Укомплектован», КБ3 «Готов к выдаче» — issue any КБ or the whole order. */
const order = (overrides: Partial<FulfillmentRequest>[]): FulfillmentRequest[] => overrides.map((item, index) => ({
  ...request,
  id: `o${index + 1}`,
  number: `REQ.26.00000${19 + index}`,
  purpose: `33МФ.2 · КБ${index + 1} · Жгут`,
  project: "33МФ.2",
  version: 5 + index,
  lines: [boundLine],
  ...item,
}));

function issueFetch(requests: FulfillmentRequest[], bodies: unknown[]) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST" && path === "/api/issues/issue-batch") {
      bodies.push(JSON.parse(String(init.body)));
      return json({ requests: [], message: "Выдано: КБ1 (REQ.26.0000019). Списано со склада позиций: 1." });
    }
    if (path === "/api/issues") return json({ requests, next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
}

it("offers «Выдать комплект» on a КБ that is only «Укомплектован» and issues it in one click", async () => {
  const bodies: unknown[] = [];
  issueFetch(order([{ status: "Укомплектован", handout_place: "1B3" }, { status: "Готов к выдаче", handout_location: "1B3" }]), bodies);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);

  render(<FulfillmentPage principal={principal} mode="give" />);
  fireEvent.click(await screen.findByRole("button", { name: /^33МФ\.2/ }));
  fireEvent.click(screen.getByRole("tab", { name: "КБ1 ✓" }));
  fireEvent.click(screen.getByRole("button", { name: "Выдать комплект" }));

  expect(confirm.mock.calls[0][0]).toContain("Комплект уйдёт на выдачу (место: 1B3) и сразу будет выдан.");
  expect(confirm.mock.calls[0][0]).toContain("Количество спишется со склада");
  await waitFor(() => expect(bodies).toEqual([{ requests: [{ id: "o1", version: 5 }] }]));
  expect(await screen.findByText("Выдано: КБ1 (REQ.26.0000019). Списано со склада позиций: 1.")).toBeInTheDocument();
  // «Отправить на выдачу» stays the picker's step on «Запросы на комплектацию», not a second button here.
  expect(screen.queryByRole("button", { name: "Отправить на выдачу" })).not.toBeInTheDocument();
});

it("issues the ticked КБ of the whole order in one call; a КБ not picked is disabled with what is picked", async () => {
  const bodies: unknown[] = [];
  issueFetch(order([
    { status: "Укомплектован" },
    { status: "Готов к выдаче", handout_location: "1B3", at_picked: "2026-10-06T09:57:00" },
    { status: "Недоукомплектован", lines: [boundLine, { ...request.lines[0], id: "waiting" }] },
    { status: "КБ Выдана", at_issued: "2026-10-05T15:20:00", lines: [{ ...boundLine, reserved: "0", issued: "4" }] },
  ]), bodies);

  render(<FulfillmentPage principal={principal} mode="give" />);
  fireEvent.click(await screen.findByRole("button", { name: /^33МФ\.2/ }));
  fireEvent.click(screen.getByRole("button", { name: /^Выдать: (КБ\d+(, КБ\d+)*|\d+ готовых КБ) · \d+ поз\.$/ }));

  const chooser = screen.getByRole("group", { name: "Выдать комплекты проекта 33МФ.2" });
  expect(within(chooser).getByLabelText("Выдать КБ1")).toBeChecked();
  expect(within(chooser).getByLabelText("Выдать КБ2")).toBeChecked();
  expect(within(chooser).getByLabelText("Выдать КБ3")).toBeDisabled();
  expect(within(chooser).getByLabelText("Выдать КБ3")).not.toBeChecked();
  expect(within(chooser).getByText("набрано 1 из 2")).toBeInTheDocument();
  expect(within(chooser).getByLabelText("Выдать КБ4")).toBeDisabled();
  expect(within(chooser).getByText(/^выдана 05\.10\.2026/)).toBeInTheDocument();
  expect(within(chooser).getByText("сейчас · Зона выдачи")).toBeInTheDocument();
  expect(within(chooser).getByText(/^Спишется со склада: 1 поз\. по 2 КБ/)).toBeInTheDocument();

  fireEvent.click(within(chooser).getByLabelText("Выдать КБ2"));
  expect(within(chooser).getByText(/^Спишется со склада: 1 поз\. по 1 КБ/)).toBeInTheDocument();
  fireEvent.click(within(chooser).getByRole("button", { name: "Выдать выбранные (1)" }));

  await waitFor(() => expect(bodies).toEqual([{ requests: [{ id: "o1", version: 5 }] }]));
  await waitFor(() => expect(screen.queryByRole("group", { name: "Выдать комплекты проекта 33МФ.2" })).not.toBeInTheDocument());
});

it("keeps the chooser open and names the КБ when the server refuses the batch", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (init?.method === "POST") return new Response(JSON.stringify({ detail: "КБ2 (REQ.26.0000020): заявка изменилась после загрузки — обнови экран. Ничего не выдано." }), { status: 409, headers: { "Content-Type": "application/json" } });
    if (path === "/api/issues") return json({ requests: order([{ status: "Укомплектован" }, { status: "Укомплектован" }]), next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<FulfillmentPage principal={principal} mode="give" />);
  fireEvent.click(await screen.findByRole("button", { name: /^33МФ\.2/ }));
  fireEvent.click(screen.getByRole("button", { name: /^Выдать: (КБ\d+(, КБ\d+)*|\d+ готовых КБ) · \d+ поз\.$/ }));
  fireEvent.click(screen.getByRole("button", { name: "Выдать выбранные (2)" }));

  expect(await screen.findByRole("alert")).toHaveTextContent("КБ2 (REQ.26.0000020): заявка изменилась после загрузки");
  expect(screen.getByRole("group", { name: "Выдать комплекты проекта 33МФ.2" })).toBeInTheDocument();
});

it("keeps the issue button visible but disabled, with the reason, when no КБ is ready; an issued КБ says so on its chip", async () => {
  issueFetch(order([
    { status: "КБ Выдана", at_issued: "2026-10-05T15:20:00", lines: [{ ...boundLine, reserved: "0", issued: "4" }] },
    { status: "Недоукомплектован", lines: [boundLine, { ...request.lines[0], id: "waiting" }] },
  ]), []);

  render(<FulfillmentPage principal={principal} mode="give" />);
  fireEvent.click(await screen.findByRole("button", { name: /^33МФ\.2/ }));
  expect(screen.getByRole("tab", { name: "КБ1 · выдан" })).toBeInTheDocument();
  const issue = screen.getByRole("button", { name: "Выдать комплекты" });
  expect(issue).toBeDisabled();
  expect(issue).toHaveAttribute("title", expect.stringContaining("Нет КБ, готовых к выдаче"));
});

it("gives no issue action without the handout right or on the picking screen", async () => {
  issueFetch(order([{ status: "Укомплектован" }, { status: "Готов к выдаче" }]), []);
  const picker: Principal = { ...principal, permissions: { fulfillment: "edit", handout: "view" } };

  const { unmount } = render(<FulfillmentPage principal={picker} mode="give" />);
  fireEvent.click(await screen.findByRole("button", { name: /^33МФ\.2/ }));
  expect(screen.queryByRole("button", { name: /^Выдать: (КБ\d+(, КБ\d+)*|\d+ готовых КБ) · \d+ поз\.$/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "КБ1 ✓" }));
  expect(screen.queryByRole("button", { name: "Выдать комплект" })).not.toBeInTheDocument();
  unmount();

  render(<FulfillmentPage principal={principal} mode="pick" />);
  await showAll();
  fireEvent.click(await screen.findByRole("button", { name: /^33МФ\.2/ }));
  expect(screen.queryByRole("button", { name: /^Выдать: (КБ\d+(, КБ\d+)*|\d+ готовых КБ) · \d+ поз\.$/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "КБ1 ✓" }));
  expect(screen.queryByRole("button", { name: "Выдать комплект" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Отправить на выдачу" })).toBeInTheDocument();
});

it("opens «Выдача» on the stages that hold requests when the saved ones are empty (В-1); a kit at the handout point is «Укомплектован»", async () => {
  issueFetch(order([{ status: "Готов к выдаче", handout_location: "1B3" }, { status: "КБ Выдана", project: "40К.1", purpose: "40К.1 · КБ1 · Жгут" }]), []);
  render(<FulfillmentPage principal={principal} mode="give" />);
  const ready = await screen.findByRole("button", { name: "Укомплектован · 1" });
  await waitFor(() => expect(ready).toHaveAttribute("aria-pressed", "true"));
  expect(screen.getByRole("button", { name: "КБ Выдана · 1" })).toHaveAttribute("aria-pressed", "false"); // the default already holds a kit
  expect(screen.queryByRole("button", { name: /^Готов к выдаче/ })).not.toBeInTheDocument();
  expect(screen.queryByText(/заявок нет/)).not.toBeInTheDocument();
  expect(screen.getAllByText("Укомплектован").length).toBeGreaterThan(0);
  expect(screen.queryByText("Готов к выдаче")).not.toBeInTheDocument();
});

it("shows a kit at the handout point as «Укомплектован» with its place, never as «Готов к выдаче»", async () => {
  issueFetch([{ ...request, status: "Готов к выдаче", handout_location: "1B3", lines: [boundLine] }], []);
  render(<FulfillmentPage principal={principal} mode="give" />);
  const badge = await screen.findByText("Укомплектован · 1B3");
  expect(badge).toHaveAttribute("title", "Лежит в зоне выдачи: 1B3");
  expect(screen.queryByText("Готов к выдаче")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Таблицей" }));
  expect((await screen.findAllByText("Укомплектован · 1B3")).length).toBeGreaterThan(0);
});

it("keeps the default «Выдача» stages when they hold requests", async () => {
  issueFetch(order([{ status: "Укомплектован" }, { status: "Готов к выдаче" }]), []);
  render(<FulfillmentPage principal={principal} mode="give" />);
  const kitted = await screen.findByRole("button", { name: "Укомплектован · 2" });
  await waitFor(() => expect(kitted).toHaveAttribute("aria-pressed", "true"));
  expect(screen.queryByRole("button", { name: /^Готов к выдаче/ })).not.toBeInTheDocument();
});

it("draws «Отменить заявку» as a danger button and asks before cancelling (К-2)", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/issues") return json({ requests: [request], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  render(<FulfillmentPage principal={principal} mode="pick" />);
  await expand(request.number);
  const cancel = await screen.findByRole("button", { name: "Отменить заявку" });
  expect(cancel).toHaveClass("secondary", "danger");
  expect(screen.getByRole("button", { name: "Печать заявки" })).toHaveClass("secondary");
  expect(screen.getByRole("button", { name: "Печать заявки" })).not.toHaveClass("small");
  fireEvent.click(cancel);
  expect(confirm).toHaveBeenCalledTimes(1);
});
