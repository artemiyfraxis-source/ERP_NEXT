import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { FulfillmentPage } from "./FulfillmentPage";
import type { FulfillmentRequest, Principal } from "./api";

const principal: Principal = {
  user: {
    id: "10000000-0000-0000-0000-000000000001",
    email: "receiver@example.test",
    name: "Кладовщик",
    department: "Склад",
  },
  roles: ["receiver"],
  permissions: { fulfillment: "edit" },
};

const request: FulfillmentRequest = {
  id: "50000000-0000-0000-0000-000000000001",
  number: "FRX-ISS-26-0001",
  version: 3,
  created_at: "2026-09-22T10:00:00+03:00",
  requester: "Инженер",
  purpose: "FRX.001",
  deadline: null,
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

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => vi.restoreAllMocks());

it("binds the lot selected by the warehouse worker with the current request version", async () => {
  let bindPayload: unknown;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/issues") return json({ requests: [request], next_cursor: null });
    if (path === "/api/issues/lots") return json({ lots: [
      {
        id: "40000000-0000-0000-0000-000000000001",
        warehouse: "ЦАР",
        grn: "FRX.26.0000001",
        package: "",
        name: "Разъём",
        unit: "шт",
        storage_location: "1F4",
        available: "10",
      },
      {
        id: "40000000-0000-0000-0000-000000000002",
        warehouse: "ЦАР",
        grn: "FRX.26.0000002",
        package: "",
        name: "Разъём",
        unit: "шт",
        storage_location: "",
        available: "7",
      },
    ] });
    if (path.endsWith("/bind") && init?.method === "POST") {
      bindPayload = JSON.parse(String(init.body));
      return json({ request });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<FulfillmentPage principal={principal} />);

  const select = await screen.findByLabelText("Партия для Разъём");
  expect(screen.getByRole("option", { name: /FRX\.26\.0000002 .* ЦАР \/ без места/ })).toBeInTheDocument();
  fireEvent.change(select, { target: { value: "40000000-0000-0000-0000-000000000002" } });
  fireEvent.click(screen.getByRole("button", { name: "Привязать" }));

  await waitFor(() => expect(bindPayload).toEqual({
    version: 3,
    lot_id: "40000000-0000-0000-0000-000000000002",
  }));
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

  expect(await screen.findByText("Ждём поставки")).toBeInTheDocument();
  expect(paths).toEqual(["/api/issues"]);
  expect(screen.queryByRole("button", { name: "Привязать" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Отменить заявку" })).not.toBeInTheDocument();
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
      lot: {
        id: "40000000-0000-0000-0000-000000000001",
        warehouse: "ЦАР",
        grn: "FRX.26.0000001",
        package: "",
        storage_location: "1F4",
      },
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
