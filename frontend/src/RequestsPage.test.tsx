import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RequestsPage } from "./RequestsPage";
import type { Principal, RequestRow, RequestSchema } from "./api";

const principal: Principal = {
  user: {
    id: "10000000-0000-0000-0000-000000000001",
    email: "buyer@example.test",
    name: "Закупщик",
    department: "Закупки",
  },
  roles: ["tech_lead"],
  access_level: "tech_lead",
  permissions: { newrequest: "edit", myrequests: "edit", requests: "edit", procurement: "edit" },
};

const schema: RequestSchema = {
  required: ["purpose", "delivery_point", "name"],
  purposes: ["RND"],
  delivery_points: ["Москва"],
  units: ["шт"],
  categories: ["Общепром", "ИП", "ОП"],
};

const row: RequestRow = {
  id: "20000000-0000-0000-0000-000000000001",
  legacy_id: "REQ-1",
  designation: "D-1",
  status: "Новый",
  stage: "Новый",
  created_at: "2026-09-16T10:00:00",
  initiator: "Закупщик",
  department: "Закупки",
  purpose: "RND",
  project_reference: null,
  delivery_point: "Москва",
  name: "Разъём",
  item_type: "Комплектующие",
  category: "Общепром",
  quantity: "1",
  unit: "шт",
  priority: "Обычный",
  planned_date: "2026-09-30",
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); }); // выбранный чип таблицы запоминается

describe("RequestsPage", () => {
  it("reuses the creation key after an ambiguous network failure", async () => {
    let createAttempts = 0;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/requests/schema") return json(schema);
      if (path === "/api/requests" && (init?.method ?? "GET") === "GET") {
        return json({ requests: createAttempts > 1 ? [row] : [] });
      }
      if (path === "/api/requests" && init?.method === "POST") {
        createAttempts += 1;
        if (createAttempts === 1) throw new TypeError("network disconnected");
        return json({ requests: [row] }, 201);
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    render(<RequestsPage principal={principal} view="create" />);
    fireEvent.change(await screen.findByLabelText("Наименование, строка 1"), {
      target: { value: "Разъём" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Оформить 1 позицию" }));
    // A broken connection on a write is an unknown result, not a failure: the same key is kept for the repeat.
    expect(await screen.findByRole("alert")).toHaveTextContent("Результат неизвестен");

    fireEvent.click(screen.getByRole("button", { name: "Оформить 1 позицию" }));
    expect(await screen.findByText("Создана заявка REQ-1")).toBeInTheDocument();

    const writes = fetchMock.mock.calls.filter(
      ([input, init]) => String(input) === "/api/requests" && init?.method === "POST",
    );
    expect(writes).toHaveLength(2);
    const firstHeaders = new Headers(writes[0][1]?.headers);
    const secondHeaders = new Headers(writes[1][1]?.headers);
    expect(firstHeaders.get("Idempotency-Key")).toBeTruthy();
    expect(secondHeaders.get("Idempotency-Key")).toBe(
      firstHeaders.get("Idempotency-Key"),
    );
  });

  it("ends a hung read of «Мои заявки» with the message and a working «Повторить» (U01)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let answer = false;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path.startsWith("/api/requests/mine")) {
        return answer ? json({ requests: [row], total: 1 }) : new Promise<Response>(() => undefined);
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    try {
      render(<RequestsPage principal={principal} view="mine" />);
      expect(await screen.findByText("Загружаю…")).toBeInTheDocument();
      await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
      expect(await screen.findByRole("alert")).toHaveTextContent("ERP не ответила за 20 с");
      expect(screen.queryByText("Загружаю…")).not.toBeInTheDocument();
      const retry = screen.getByRole("button", { name: "Повторить" });
      expect(retry).toBeEnabled();
      answer = true;
      fireEvent.click(retry);
      expect(await screen.findByText("REQ-1")).toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("hands a new request to procurement", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/requests/schema") return json(schema);
      if (path === "/api/requests" && (init?.method ?? "GET") === "GET") {
        return json({ requests: [row] });
      }
      if (path === "/api/requests/handoff" && init?.method === "POST") {
        return json({ accepted: [row.id] });
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    render(<RequestsPage principal={principal} />);
    fireEvent.click(await screen.findByRole("button", { name: "В закупку" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/requests/handoff",
      expect.objectContaining({ method: "POST" }),
    ));
    expect(await screen.findByText("Заявка передана в закупку: строка в блоке «В работе» на странице «Закупка»")).toBeInTheDocument();
  });

  it("re-transfers a request in work as a new revision after confirmation", async () => {
    const inWork = { ...row, status: "В работе" };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/requests/schema") return json(schema);
      if (path === "/api/requests" && (init?.method ?? "GET") === "GET") {
        return json({ requests: [inWork] });
      }
      if (path === "/api/requests/handoff" && init?.method === "POST") {
        return json({ accepted: [row.id] });
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);

    render(<RequestsPage principal={principal} />);
    expect(screen.queryByRole("button", { name: "В закупку" })).not.toBeInTheDocument();
    // По умолчанию открыт чип «Ждут переноса»: строка «В работе» — под чипом «В работе».
    fireEvent.click(await screen.findByRole("button", { name: /^В работе · / }));
    const button = await screen.findByRole("button", { name: "Повторно в закупку" });
    fireEvent.click(button);
    expect(fetchMock).not.toHaveBeenCalledWith("/api/requests/handoff", expect.anything());
    fireEvent.click(button);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/requests/handoff",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ request_ids: [row.id], retransfer: true }) }),
    ));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("Заявка перенесена повторно: в закупке новая ревизия")).toBeInTheDocument();
  });

  it("counts requests by procurement stage in the status chips", async () => {
    const stages = ["В работе", "Жду счет", "В оплату", "Готово", "Готово / Отменено", "Отменено", "Готово / В пути"];
    const rows = [
      row,
      ...stages.map((stage, index) => ({ ...row, id: `stage-${index}`, status: "В работе", stage })),
    ];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input) === "/api/requests/schema") return json(schema);
      return json({ requests: rows });
    });

    render(<RequestsPage principal={principal} />);
    expect(await screen.findByRole("button", { name: "Ждут переноса · 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "В работе · 4" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ждут счет · 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ждут оплаты · 1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Выполнено · 2" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отменено · 1" })).toBeInTheDocument();
  });

  it("keeps view-only users read-only and hides the hand-off to procurement", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input) === "/api/requests/schema") return json(schema);
      return json({ requests: [row] });
    });

    render(
      <RequestsPage
        principal={{
          ...principal,
          roles: ["operator"],
          access_level: "operator",
          permissions: { requests: "view", procurement: "view" },
        }}
      />,
    );

    expect(await screen.findByText("Последние заявки")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Оформить" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "В закупку" })).not.toBeInTheDocument();
  });

  it("offers «В закупку» by the edit grant on procurement, whatever the level is called", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input) === "/api/requests/schema") return json(schema);
      return json({ requests: [row] });
    });

    render(<RequestsPage principal={{ ...principal, roles: ["operator"], access_level: "operator", permissions: { requests: "view", procurement: "edit" } }} />);

    expect(await screen.findByRole("button", { name: "В закупку" })).toBeInTheDocument();
  });

  it("RQ-MINE: «Мои заявки» loads the server-side mine endpoint, not the shared list, and shows the stage", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path.startsWith("/api/requests/mine")) {
        return json({ requests: [{ ...row, stage: "Жду счет" }], total: 1, limit: 50, offset: 0 });
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    render(<RequestsPage principal={principal} view="mine" />);

    expect(await screen.findByText("REQ-1")).toBeInTheDocument();
    expect(screen.getByText("Жду счет")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Мои заявки", level: 1 })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Оформить" })).not.toBeInTheDocument();
    // Server-side filter by actor id: the shared /api/requests list is never fetched for this view.
    expect(fetchMock.mock.calls.some(([input]) => String(input) === "/api/requests")).toBe(false);
    expect(fetchMock).toHaveBeenCalledWith("/api/requests/mine?limit=50&offset=0", expect.anything());
  });

  it("RQ-MINE: «Показать ещё» loads the next page and appends it", async () => {
    const firstPage = { ...row };
    const secondPage = { ...row, id: "second", legacy_id: "REQ-2" };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/requests/mine?limit=50&offset=0") {
        return json({ requests: [firstPage], total: 2, limit: 50, offset: 0 });
      }
      if (path === "/api/requests/mine?limit=50&offset=1") {
        return json({ requests: [secondPage], total: 2, limit: 50, offset: 1 });
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    render(<RequestsPage principal={principal} view="mine" />);
    expect(await screen.findByText("REQ-1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Показать ещё" }));

    expect(await screen.findByText("REQ-2")).toBeInTheDocument();
    expect(screen.getByText("REQ-1")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Показать ещё" })).not.toBeInTheDocument();
  });

  it("NAV-1: «Оформить заявку» shows the form without the request table", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/requests/schema") return json(schema);
      if (path === "/api/requests") return json({ requests: [row] });
      throw new Error(`Unexpected request: ${path}`);
    });

    // NAV-SECTIONS: `newrequest` alone opens the form; the shared list (`requests`) is not even asked for.
    render(<RequestsPage principal={{ ...principal, roles: ["operator"], access_level: "operator", permissions: { newrequest: "edit" } }} view="create" />);

    expect(await screen.findByRole("button", { name: "Оформить" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Оформить заявку", level: 1 })).toBeInTheDocument();
    expect(screen.queryByText("REQ-1")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => String(input) === "/api/requests")).toBe(false);
  });

  it("NAV-SECTIONS: «Заявки» is the list only — a request is made in «Оформить заявку»", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/requests/schema") return json(schema);
      if (path === "/api/requests") return json({ requests: [row] });
      throw new Error(`Unexpected request: ${path}`);
    });

    render(<RequestsPage principal={{ ...principal, roles: ["tech_lead"], access_level: "tech_lead", permissions: { requests: "edit" } }} />);

    expect(await screen.findByText("Последние заявки")).toBeInTheDocument();
    expect(screen.queryByText("Новая заявка")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Оформить" })).not.toBeInTheDocument();
  });

  it("RQ-IMPORT-UI: «Вставить из Excel» recognizes pasted headers and creates the whole batch", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/requests/schema") return json(schema);
      if (path === "/api/requests" && (init?.method ?? "GET") === "GET") return json({ requests: [] });
      if (path === "/api/requests" && init?.method === "POST") {
        return json({ requests: [row, { ...row, id: "second", legacy_id: "REQ-2" }] }, 201);
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    render(<RequestsPage principal={principal} view="create" />);
    await screen.findByRole("button", { name: "Оформить" });
    fireEvent.click(screen.getByRole("button", { name: "Вставить из Excel" }));

    fireEvent.change(screen.getByPlaceholderText(/Вставь сюда диапазон из Excel/), {
      target: { value: "Наименование\tКоличество\tЕдиница\nРазъём SMA\t3\tшт\nКабель РК\t10\tм" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Распознать" }));

    expect(await screen.findByDisplayValue("Разъём SMA")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Кабель РК")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Оформить 2 позиции" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/requests",
      expect.objectContaining({ method: "POST" }),
    ));
    const [, init] = fetchMock.mock.calls.find(
      ([reqInput, reqInit]) => String(reqInput) === "/api/requests" && reqInit?.method === "POST",
    )!;
    const body = JSON.parse(String(init?.body));
    expect(body.requests).toHaveLength(2);
    expect(body.requests[0]).toMatchObject({ name: "Разъём SMA", quantity: "3", unit: "шт", delivery_point: "Москва" });
    expect(body.requests[1]).toMatchObject({ name: "Кабель РК", quantity: "10", unit: "м" });
    expect(await screen.findByText("Создано заявок: 2 (REQ-1, REQ-2)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Оформить" })).toBeInTheDocument();
  });

  it("RQ-IMPORT-UI: «Вставить из Excel» reports unrecognized headers instead of silently dropping them", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input) === "/api/requests/schema") return json(schema);
      if (String(input) === "/api/requests") return json({ requests: [] });
      throw new Error(`Unexpected request: ${String(input)}`);
    });

    render(<RequestsPage principal={principal} view="create" />);
    await screen.findByRole("button", { name: "Оформить" });
    fireEvent.click(screen.getByRole("button", { name: "Вставить из Excel" }));
    fireEvent.change(screen.getByPlaceholderText(/Вставь сюда диапазон из Excel/), {
      target: { value: "Наименование\tЦвет\nРазъём\tкрасный" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Распознать" }));

    expect(await screen.findByText(/Не распознаны \(пропущены\): Цвет/)).toBeInTheDocument();
  });

  it("RQ-IMPORT-UI: «Из расчёта» reads the calculation by its Google link and creates the selected positions", async () => {
    const preview = {
      project: "591К.3",
      items: [
        {
          item_type: "Разъём",
          name: "DB-9M",
          quantity: "4",
          price: null,
          price_with_vat: null,
          link: null,
          country: null,
          unit: "шт",
          quality_document: "",
          planned_date: null,
        },
      ],
    };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/requests/schema") return json(schema);
      if (path === "/api/requests" && (init?.method ?? "GET") === "GET") return json({ requests: [] });
      if (path === "/api/requests/import/calc" && init?.method === "POST") {
        return json({ ...preview, warnings: ["Проект «591К.3» не найден в реестре открытых проектов — импорт не заблокирован (переходный период)"] });
      }
      if (path === "/api/requests/import" && init?.method === "POST") {
        return json({ requests: [row], warnings: [] }, 201);
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    render(<RequestsPage principal={principal} view="create" />);
    await screen.findByRole("button", { name: "Оформить" });
    fireEvent.click(screen.getByRole("button", { name: "Из расчёта" }));

    fireEvent.click(screen.getByRole("button", { name: "Загрузить" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Вставь ссылку");
    fireEvent.change(screen.getByLabelText("Ссылка на расчёт"), {
      target: { value: "https://docs.google.com/spreadsheets/d/1AbC/edit" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Загрузить" }));

    expect(await screen.findByDisplayValue("DB-9M")).toBeInTheDocument();
    const [, loadInit] = fetchMock.mock.calls.find(([reqInput]) => String(reqInput) === "/api/requests/import/calc")!;
    expect(JSON.parse(String(loadInit?.body))).toEqual({ url: "https://docs.google.com/spreadsheets/d/1AbC/edit" });
    expect(screen.getByText(/не найден в реестре открытых проектов/)).toBeInTheDocument();
    expect(screen.getByLabelText("Ед., строка 1")).toHaveValue("шт");
    expect(screen.getByLabelText("Док. качества, строка 1")).toHaveValue("Нет");
    fireEvent.change(screen.getByLabelText("Категория, строка 1"), { target: { value: "ОП" } });
    expect(screen.getByLabelText("Док. качества, строка 1")).toHaveValue("Да");
    expect(screen.getByLabelText("Док. качества, строка 1")).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Ед., строка 1"), { target: { value: "компл" } });
    fireEvent.change(screen.getByLabelText("Дедлайн (на всю пачку) *"), { target: { value: "2026-10-01" } });
    fireEvent.change(screen.getByLabelText(/Точка отгрузки \(на всю пачку\)/), { target: { value: "Москва" } });
    fireEvent.change(screen.getByLabelText(/План. дата — всем/), { target: { value: "2026-10-20" } });
    fireEvent.click(screen.getByRole("button", { name: "Оформить 1 позицию" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/requests/import",
      expect.objectContaining({ method: "POST" }),
    ));
    const [, init] = fetchMock.mock.calls.find(([reqInput]) => String(reqInput) === "/api/requests/import")!;
    const body = JSON.parse(String(init?.body));
    expect(body).toMatchObject({ project: "591К.3", delivery_point: "Москва", procurement_deadline: "2026-10-01" });
    expect(body.items[0]).toMatchObject({ name: "DB-9M", unit: "компл", category: "ОП", quality_document: "Да", planned_date: "2026-10-20" });
    expect(await screen.findByText(/Создано заявок из расчёта: 1/)).toBeInTheDocument();
  });

  it("opens «Из расчёта» from the project card with the link and project number filled in", async () => {
    window.history.replaceState({}, "", "/?section=newrequest&calc_url=https%3A%2F%2Fdocs.google.com%2Fspreadsheets%2Fd%2F1AbC%2Fedit&calc_project=708%D0%9A.4");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/requests/schema") return json(schema);
      if (path === "/api/requests/import/calc" && init?.method === "POST") {
        return json({ project: "", items: [{ item_type: "Разъём", name: "DB-9M", quantity: "4", price: null, price_with_vat: null,
          link: null, country: null, unit: "шт", quality_document: "", planned_date: null }] });
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    render(<RequestsPage principal={principal} view="create" />);

    expect(await screen.findByDisplayValue("DB-9M")).toBeInTheDocument();
    expect(screen.getByLabelText("Ссылка на расчёт")).toHaveValue("https://docs.google.com/spreadsheets/d/1AbC/edit");
    const [, loadInit] = fetchMock.mock.calls.find(([reqInput]) => String(reqInput) === "/api/requests/import/calc")!;
    expect(JSON.parse(String(loadInit?.body))).toEqual({ url: "https://docs.google.com/spreadsheets/d/1AbC/edit" });
    expect(screen.getByText("708К.4")).toBeInTheDocument();
    expect(window.location.search).toBe("?section=newrequest");
    window.history.replaceState({}, "", "/");
  });

  it("«Из расчёта» fallback: the pasted ЗКП range still goes through /import/parse", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/requests/schema") return json(schema);
      if (path === "/api/requests/import/parse" && init?.method === "POST") {
        return json({ project: "", items: [{ item_type: "Кабель", name: "МГТФ", quantity: "2", price: null, price_with_vat: null, link: null, country: null, unit: "м", quality_document: "", planned_date: null }] });
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    render(<RequestsPage principal={principal} view="create" />);
    await screen.findByRole("button", { name: "Оформить" });
    fireEvent.click(screen.getByRole("button", { name: "Из расчёта" }));
    fireEvent.change(screen.getByPlaceholderText(/Вставь сюда диапазон листа ЗКП/), {
      target: { value: "Тип\tОбозначение\tНа проект\tБез НДС\nКабель\tМГТФ\t2\t10" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Распознать" }));

    expect(await screen.findByDisplayValue("МГТФ")).toBeInTheDocument();
    const [, init] = fetchMock.mock.calls.find(([reqInput]) => String(reqInput) === "/api/requests/import/parse")!;
    expect(JSON.parse(String(init?.body)).grid[1]).toEqual(["Кабель", "МГТФ", "2", "10"]);
  });
  it("manual grid: common fields go to every row, the whole batch is one POST", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/requests/schema") {
        return json({ ...schema, purposes: ["RND", "FRX"], projects: ["591К.3", "2000К.1"], categories: ["Общепром", "ИП", "ОП"] });
      }
      if (path === "/api/requests" && (init?.method ?? "GET") === "GET") return json({ requests: [] });
      if (path === "/api/requests" && init?.method === "POST") {
        return json({ requests: [row, { ...row, id: "second", legacy_id: "REQ-2" }] }, 201);
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    render(<RequestsPage principal={principal} view="create" />);
    await screen.findByRole("button", { name: "Оформить" });
    fireEvent.click(screen.getByRole("button", { name: "+ Добавить строку" }));
    expect(screen.getAllByLabelText(/^Наименование, строка/)).toHaveLength(2);
    fireEvent.change(screen.getByLabelText("Наименование, строка 1"), { target: { value: "Разъём" } });
    // Ручной ввод: номер не из реестра вписывается как есть, реестр только подсказывает.
    expect(screen.getByRole("combobox", { name: "Цель (проект)" })).toHaveAttribute("aria-autocomplete", "list");
    fireEvent.change(screen.getByLabelText("Цель (проект)"), { target: { value: "48К.13" } });
    fireEvent.change(screen.getByLabelText("Категория", { selector: ".rg-common select" }), { target: { value: "ОП" } });
    // the common category reached every row, and the rule «ОП → Да» with it
    expect(screen.getByLabelText("Категория, строка 2")).toHaveValue("ОП");
    expect(screen.getByLabelText("Док. качества, строка 2")).toHaveValue("Да");
    fireEvent.change(screen.getByLabelText("Артикул КД, строка 1"), { target: { value: "АБВГ.123" } });
    fireEvent.change(screen.getByLabelText("Цена с НДС, строка 1"), { target: { value: "120" } });

    // row 2 has no name: it is blank, not an error — only row 1 is sent
    expect(screen.getByRole("button", { name: "Оформить 1 позицию" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Наименование, строка 2"), { target: { value: "Кабель" } });
    fireEvent.click(screen.getByRole("button", { name: "Оформить 2 позиции" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/requests", expect.objectContaining({ method: "POST" })));
    const [, init] = fetchMock.mock.calls.find(([reqInput, reqInit]) => String(reqInput) === "/api/requests" && reqInit?.method === "POST")!;
    const body = JSON.parse(String(init?.body));
    expect(body.requests).toHaveLength(2);
    expect(body.requests[0]).toMatchObject({ name: "Разъём", project_reference: "48К.13", category: "ОП", quality_document: "Да", design_article: "АБВГ.123", price_with_vat: "120" });
    expect(body.requests[1]).toMatchObject({ name: "Кабель", project_reference: "48К.13", category: "ОП", quality_document: "Да", delivery_point: "Москва" });
    expect(await screen.findByText("Создано заявок: 2 (REQ-1, REQ-2)")).toBeInTheDocument();
  });

  it("manual form: only name, quantity, unit and purpose are required; cleared defaults are left to the server", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/requests/schema") return json(schema);
      if (path === "/api/requests" && init?.method === "POST") return json({ requests: [row] }, 201);
      if (path === "/api/requests") return json({ requests: [] });
      throw new Error(`Unexpected request: ${path}`);
    });
    render(<RequestsPage principal={principal} view="create" />);
    await screen.findByRole("button", { name: "Оформить" });

    // defaults are visible and editable
    expect(screen.getByLabelText("Документ качества")).toHaveValue("Нет");
    expect(screen.getByLabelText("Срок закупки")).not.toHaveValue("");
    expect(screen.getByLabelText("Плановая дата")).not.toHaveValue("");

    fireEvent.change(screen.getByLabelText("Наименование, строка 1"), { target: { value: "Болт М3" } });
    fireEvent.change(screen.getByLabelText("Тип, строка 1"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("План. дата, строка 1"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Док. качества, строка 1"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Оформить 1 позицию" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/api/requests" && init?.method === "POST")).toBe(true));
    const [, init] = fetchMock.mock.calls.find(([input, reqInit]) => String(input) === "/api/requests" && reqInit?.method === "POST")!;
    const sent = JSON.parse(String(init?.body)).requests[0];
    expect(sent).toMatchObject({ name: "Болт М3", quantity: "1", unit: "шт", purpose: "RND" });
    expect(sent).not.toHaveProperty("item_type");
    expect(sent).not.toHaveProperty("planned_date");
    expect(sent).not.toHaveProperty("quality_document");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("T7: a block pasted into the grid fills rows and columns; 100 rows fit, the rest is dropped", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input) === "/api/requests/schema") return json(schema);
      throw new Error(`Unexpected request: ${String(input)}`);
    });
    render(<RequestsPage principal={principal} view="create" />);
    await screen.findByRole("button", { name: "Оформить" });

    // without a header row: from the focused cell, in the order Наименование · Тип · Артикул КД · Кол-во · Ед.
    const block = Array.from({ length: 3 }, (_, i) => `Разъём ${i + 1}\tКомплектующие\tАБВ.${i}\t${i + 2},5\tм`).join("\n");
    fireEvent.paste(screen.getByLabelText("Наименование, строка 1"), { clipboardData: { getData: () => block } });
    expect(screen.getAllByLabelText(/^Наименование, строка/)).toHaveLength(3);
    expect(screen.getByLabelText("Наименование, строка 3")).toHaveValue("Разъём 3");
    expect(screen.getByLabelText("Артикул КД, строка 2")).toHaveValue("АБВ.1");
    expect(screen.getByLabelText("Кол-во, строка 1")).toHaveValue("2.5");
    expect(screen.getByLabelText("Ед., строка 3")).toHaveValue("м");
    expect(screen.getByRole("button", { name: "Оформить 3 позиции" })).toBeInTheDocument();

    // a pasted block with a header row is read by column names and goes after the filled rows
    const big = ["Наименование\tКоличество", ...Array.from({ length: 100 }, (_, i) => `Позиция ${i + 1}\t1`)].join("\n");
    fireEvent.paste(screen.getByLabelText("Наименование, строка 1"), { clipboardData: { getData: () => big } });
    expect(screen.getAllByLabelText(/^Наименование, строка/)).toHaveLength(100);
    expect(screen.getByRole("alert")).toHaveTextContent("Не вошло строк: 3");
    expect(screen.getByText(/Позиций:/)).toHaveTextContent("Позиций: 100 из 100");
    expect(screen.getByRole("button", { name: "+ Добавить строку" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Оформить 100 позиций" })).toBeInTheDocument();
  }, 15_000);  // renders 100+ grid rows: under a full parallel run 5 s is not enough

  it("T7: Enter moves down, on the last row it adds a row; copy and remove work per row", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input) === "/api/requests/schema") return json(schema);
      throw new Error(`Unexpected request: ${String(input)}`);
    });
    render(<RequestsPage principal={principal} view="create" />);
    await screen.findByRole("button", { name: "Оформить" });

    const first = screen.getByLabelText("Наименование, строка 1");
    fireEvent.change(first, { target: { value: "Разъём" } });
    fireEvent.keyDown(first, { key: "Enter" });
    const second = await screen.findByLabelText("Наименование, строка 2");
    await waitFor(() => expect(second).toHaveFocus());
    fireEvent.change(second, { target: { value: "Кабель" } });
    fireEvent.click(screen.getByRole("button", { name: "Копия строки 1" }));
    expect(screen.getByLabelText("Наименование, строка 2")).toHaveValue("Разъём");
    expect(screen.getByLabelText("Наименование, строка 3")).toHaveValue("Кабель");
    fireEvent.click(screen.getByRole("button", { name: "Убрать строку 1" }));
    expect(screen.getByLabelText("Наименование, строка 1")).toHaveValue("Разъём");
    expect(screen.getAllByLabelText(/^Наименование, строка/)).toHaveLength(2);
  });

  it("T7: errors are marked in the cells with a count, nothing is sent until they are fixed", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/requests/schema") return json(schema);
      if (path === "/api/requests" && init?.method === "POST") return json({ requests: [row] }, 201);
      if (path === "/api/requests") return json({ requests: [] });
      throw new Error(`Unexpected request: ${path}`);
    });
    render(<RequestsPage principal={principal} view="create" />);
    await screen.findByRole("button", { name: "Оформить" });

    const block = ["Разъём\tКомплектующие\tA\t0\tшт", "Кабель\tКомплектующие\tB\tabc\tм", "Клемма\tКомплектующие\tC\t3\tшт"].join("\n");
    fireEvent.paste(screen.getByLabelText("Наименование, строка 1"), { clipboardData: { getData: () => block } });
    expect(screen.getByRole("status")).toHaveTextContent("2 позиции с ошибкой");
    expect(screen.getByLabelText("Кол-во, строка 1")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Кол-во, строка 2")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Ед., строка 3")).not.toHaveAttribute("aria-invalid");

    fireEvent.click(screen.getByRole("button", { name: "Оформить 3 позиции" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("2 позиции с ошибкой");
    expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/api/requests" && init?.method === "POST")).toBe(false);

    fireEvent.change(screen.getByLabelText("Кол-во, строка 1"), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText("Кол-во, строка 2"), { target: { value: "5" } });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Оформить 3 позиции" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/api/requests" && init?.method === "POST")).toBe(true));
    const [, init] = fetchMock.mock.calls.find(([input, reqInit]) => String(input) === "/api/requests" && reqInit?.method === "POST")!;
    expect(JSON.parse(String(init?.body)).requests.map((item: { name: string; quantity: string; unit: string }) => [item.name, item.quantity, item.unit]))
      .toEqual([["Разъём", "4", "шт"], ["Кабель", "5", "м"], ["Клемма", "3", "шт"]]);
  });

  it("Excel template: copies the headers and offers the .csv; recognizes SUPPLYMAP headers and dd.mm.yyyy dates", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input) === "/api/requests/schema") return json(schema);
      throw new Error(`Unexpected request: ${String(input)}`);
    });
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);

    render(<RequestsPage principal={principal} view="create" />);
    await screen.findByRole("button", { name: "Оформить" });
    fireEvent.click(screen.getByRole("button", { name: "Вставить из Excel" }));
    fireEvent.click(screen.getByRole("button", { name: "Скопировать заголовки" }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(writeText.mock.calls[0][0]).toContain("Наименование *\tТип\tКатегория\tАртикул КД");
    expect(await screen.findByText(/Заголовки скопированы/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Скачать шаблон (.csv)" }));
    expect(click).toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText(/Вставь сюда диапазон из Excel/), {
      target: { value: "НАИМЕНОВАНИЕ *\tКОЛ-ВО *\tАРТИКУЛ КД\tССЫЛКА\tЦЕНА\tПЛАН. ДАТА\nРазъём\t3\tАБВ.1\thttps://x.test\t10\t05.11.2026" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Распознать" }));

    expect(await screen.findByDisplayValue("АБВ.1")).toBeInTheDocument();
    expect(screen.getByDisplayValue("https://x.test")).toBeInTheDocument();
    expect(screen.getByLabelText("План. дата, строка 1")).toHaveValue("2026-11-05");
  });

  it("«Мои заявки» shows the purchase columns: buyer, supplier, order, expected date, sum", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path.startsWith("/api/requests/mine")) {
        return json({
          requests: [{ ...row, stage: "В пути", buyer: "Петров", supplier: "ООО Ромашка", order_number: "З-17", expected_arrival: "2026-10-03", procurement_sum: "1500.00" }],
          total: 1,
          limit: 50,
          offset: 0,
        });
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    render(<RequestsPage principal={principal} view="mine" />);

    expect(await screen.findByText("Петров")).toBeInTheDocument();
    expect(screen.getByText("ООО Ромашка")).toBeInTheDocument();
    expect(screen.getByText("З-17")).toBeInTheDocument();
    expect(screen.getByText("03.10.2026")).toBeInTheDocument();
    expect(screen.getByText("1\u202f500", { normalizer: (text) => text })).toBeInTheDocument(); // числовая колонка DataTable — по-русски: разряды, без «.00»

    fireEvent.change(screen.getByLabelText("Искать во всех моих заявках"), { target: { value: "кабель" } });
    fireEvent.click(screen.getByRole("button", { name: "Найти" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/requests/mine?limit=50&offset=0&q=%D0%BA%D0%B0%D0%B1%D0%B5%D0%BB%D1%8C", expect.anything()));
  });

  it("«Заявки»: selected new requests go to procurement in one handoff", async () => {
    const second = { ...row, id: "20000000-0000-0000-0000-000000000002", legacy_id: "REQ-2", name: "Кабель" };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/requests/schema") return json(schema);
      if (path === "/api/requests" && (init?.method ?? "GET") === "GET") return json({ requests: [row, second] });
      if (path === "/api/requests/handoff" && init?.method === "POST") return json({ accepted: [row.id, second.id] });
      throw new Error(`Unexpected request: ${path}`);
    });

    render(<RequestsPage principal={principal} />);
    await screen.findByText("REQ-2");
    fireEvent.click(screen.getByLabelText("Выбрать все показанные"));
    fireEvent.click(screen.getByRole("button", { name: "Перенести в закупку" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/requests/handoff",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ request_ids: [row.id, second.id], retransfer: false }) }),
    ));
    expect(await screen.findByText("Передано в закупку: 2 — строки в блоке «В работе» на странице «Закупка»")).toBeInTheDocument();
  });
});
