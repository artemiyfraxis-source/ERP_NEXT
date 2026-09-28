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
  permissions: { requests: "edit", procurement: "edit" },
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
});
