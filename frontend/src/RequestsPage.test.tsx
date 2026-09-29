import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  roles: ["buyer"],
  permissions: { newrequest: "edit", myrequests: "edit", requests: "edit", procurement: "edit" },
};

const schema: RequestSchema = {
  required: ["purpose", "delivery_point", "name"],
  purposes: ["RND"],
  delivery_points: ["Москва"],
  units: ["шт"],
  categories: ["Общепром"],
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

afterEach(() => vi.restoreAllMocks());

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

    render(<RequestsPage principal={principal} />);
    fireEvent.change(await screen.findByLabelText(/Наименование/), {
      target: { value: "Разъём" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Создать заявку" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("network disconnected");

    fireEvent.click(screen.getByRole("button", { name: "Создать заявку" }));
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
    expect(await screen.findByText("Заявка передана в очередь закупки")).toBeInTheDocument();
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

  it("keeps view-only users read-only and hides buyer actions", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input) === "/api/requests/schema") return json(schema);
      return json({ requests: [row] });
    });

    render(
      <RequestsPage
        principal={{
          ...principal,
          roles: ["staff"],
          permissions: { requests: "view", procurement: "edit" },
        }}
      />,
    );

    expect(await screen.findByText("Только просмотр")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Создать заявку" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "В закупку" })).not.toBeInTheDocument();
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
    expect(screen.queryByRole("button", { name: "Создать заявку" })).not.toBeInTheDocument();
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
    render(<RequestsPage principal={{ ...principal, roles: ["staff"], permissions: { newrequest: "edit" } }} view="create" />);

    expect(await screen.findByRole("button", { name: "Создать заявку" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Оформить заявку", level: 1 })).toBeInTheDocument();
    expect(screen.queryByText("REQ-1")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => String(input) === "/api/requests")).toBe(false);
  });

  it("NAV-SECTIONS: «Заявки» shows the form only with `newrequest`, not with `requests: edit`", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/requests/schema") return json(schema);
      if (path === "/api/requests") return json({ requests: [row] });
      throw new Error(`Unexpected request: ${path}`);
    });

    render(<RequestsPage principal={{ ...principal, roles: ["buyer"], permissions: { requests: "edit" } }} />);

    expect(await screen.findByText("Только просмотр")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Создать заявку" })).not.toBeInTheDocument();
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
    await screen.findByRole("button", { name: "Создать заявку" });
    fireEvent.click(screen.getByRole("button", { name: "📥 Вставить из Excel" }));

    fireEvent.change(screen.getByPlaceholderText(/Вставь сюда диапазон из Excel/), {
      target: { value: "Наименование\tКоличество\tЕдиница\nРазъём SMA\t3\tшт\nКабель РК\t10\tм" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Распознать" }));

    expect(await screen.findByDisplayValue("Разъём SMA")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Кабель РК")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Создать заявки (2)" }));

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
    expect(await screen.findByText("Создано заявок из Excel: 2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Создать заявку" })).toBeInTheDocument();
  });

  it("RQ-IMPORT-UI: «Вставить из Excel» reports unrecognized headers instead of silently dropping them", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (String(input) === "/api/requests/schema") return json(schema);
      if (String(input) === "/api/requests") return json({ requests: [] });
      throw new Error(`Unexpected request: ${String(input)}`);
    });

    render(<RequestsPage principal={principal} view="create" />);
    await screen.findByRole("button", { name: "Создать заявку" });
    fireEvent.click(screen.getByRole("button", { name: "📥 Вставить из Excel" }));
    fireEvent.change(screen.getByPlaceholderText(/Вставь сюда диапазон из Excel/), {
      target: { value: "Наименование\tЦвет\nРазъём\tкрасный" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Распознать" }));

    expect(await screen.findByText(/Не распознаны \(пропущены\): Цвет/)).toBeInTheDocument();
  });

  it("RQ-IMPORT-UI: «Из расчёта» parses the pasted ЗКП range via RQ-5 and creates the selected positions", async () => {
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
      if (path === "/api/requests/import/parse" && init?.method === "POST") return json(preview);
      if (path === "/api/requests/import" && init?.method === "POST") {
        return json({ requests: [row], warnings: [] }, 201);
      }
      throw new Error(`Unexpected request: ${path}`);
    });

    render(<RequestsPage principal={principal} view="create" />);
    await screen.findByRole("button", { name: "Создать заявку" });
    fireEvent.click(screen.getByRole("button", { name: "🧮 Из расчёта" }));

    fireEvent.change(screen.getByPlaceholderText(/Вставь сюда диапазон листа ЗКП/), {
      target: { value: "Тип\tОбозначение\tНа проект\tБез НДС\nРазъём\tDB-9M\t4\t10" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Распознать" }));

    expect(await screen.findByText("DB-9M")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Дедлайн (на всю пачку) *"), { target: { value: "2026-10-01" } });
    fireEvent.change(screen.getByLabelText(/Точка отгрузки \(на всю пачку\)/), { target: { value: "Москва" } });
    fireEvent.change(screen.getByLabelText(/Документ по качеству — всем/), { target: { value: "Нет" } });
    fireEvent.change(screen.getByLabelText(/План. дата — всем/), { target: { value: "2026-10-20" } });
    fireEvent.click(screen.getByRole("button", { name: "Создать заявки" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/requests/import",
      expect.objectContaining({ method: "POST" }),
    ));
    const [, init] = fetchMock.mock.calls.find(([reqInput]) => String(reqInput) === "/api/requests/import")!;
    const body = JSON.parse(String(init?.body));
    expect(body).toMatchObject({ project: "591К.3", delivery_point: "Москва", procurement_deadline: "2026-10-01" });
    expect(body.items[0]).toMatchObject({ name: "DB-9M", quality_document: "Нет", planned_date: "2026-10-20" });
    expect(await screen.findByText(/Создано заявок из расчёта: 1/)).toBeInTheDocument();
  });
  it("manual form: several positions share «Общее для всех позиций» and go in one batch", async () => {
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
    await screen.findByRole("button", { name: "Создать заявку" });
    fireEvent.click(screen.getByRole("button", { name: "+ Добавить позицию" }));
    const names = screen.getAllByLabelText(/Наименование/);
    expect(names).toHaveLength(2);
    fireEvent.change(names[0], { target: { value: "Разъём" } });
    fireEvent.change(screen.getByLabelText("Цель (проект)"), { target: { value: "2000К.1" } });
    fireEvent.change(screen.getByLabelText("Категория"), { target: { value: "ОП" } });
    fireEvent.change(screen.getAllByLabelText("Артикул КД")[0], { target: { value: "АБВГ.123" } });
    fireEvent.change(screen.getAllByLabelText("Цена с НДС")[0], { target: { value: "120" } });

    fireEvent.click(screen.getByRole("button", { name: "Создать заявки (2)" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Позиция 2: заполни Наименование");

    fireEvent.change(screen.getAllByLabelText(/Наименование/)[1], { target: { value: "Кабель" } });
    fireEvent.click(screen.getByRole("button", { name: "Создать заявки (2)" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/requests", expect.objectContaining({ method: "POST" })));
    const [, init] = fetchMock.mock.calls.find(([reqInput, reqInit]) => String(reqInput) === "/api/requests" && reqInit?.method === "POST")!;
    const body = JSON.parse(String(init?.body));
    expect(body.requests).toHaveLength(2);
    expect(body.requests[0]).toMatchObject({ name: "Разъём", project_reference: "2000К.1", category: "ОП", quality_document: "Да", design_article: "АБВГ.123", price_with_vat: "120" });
    expect(body.requests[1]).toMatchObject({ name: "Кабель", project_reference: "2000К.1", category: "ОП", quality_document: "Да", delivery_point: "Москва" });
    expect(await screen.findByText("Создано заявок: 2 (REQ-1, REQ-2)")).toBeInTheDocument();
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
    await screen.findByRole("button", { name: "Создать заявку" });
    fireEvent.click(screen.getByRole("button", { name: "📥 Вставить из Excel" }));
    fireEvent.click(screen.getByRole("button", { name: "⧉ Скопировать заголовки" }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(writeText.mock.calls[0][0]).toContain("Наименование *\tТип *\tКатегория\tАртикул КД");
    expect(await screen.findByText(/Заголовки скопированы/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "⬇ Скачать шаблон (.csv)" }));
    expect(click).toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText(/Вставь сюда диапазон из Excel/), {
      target: { value: "НАИМЕНОВАНИЕ *\tКОЛ-ВО *\tАРТИКУЛ КД\tССЫЛКА\tЦЕНА\tПЛАН. ДАТА\nРазъём\t3\tАБВ.1\thttps://x.test\t10\t05.11.2026" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Распознать" }));

    expect(await screen.findByDisplayValue("АБВ.1")).toBeInTheDocument();
    expect(screen.getByDisplayValue("https://x.test")).toBeInTheDocument();
    expect(screen.getByText(/Своё у позиции: Плановая дата — 2026-11-05/)).toBeInTheDocument();
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
    expect(screen.getByText("1500.00")).toBeInTheDocument();

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
    expect(await screen.findByText("Передано в очередь закупки: 2")).toBeInTheDocument();
  });
});
