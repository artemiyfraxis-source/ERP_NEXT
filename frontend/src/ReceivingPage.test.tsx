import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReceivingPage } from "./ReceivingPage";
import type { ReceivingLine } from "./api";

const line: ReceivingLine = {
  id: "20000000-0000-0000-0000-000000000001",
  legacy_id: "SM-20260921-01",
  status: "В пути",
  version: 2,
  warehouse: "ЦАР",
  article: "CON-001",
  series: "",
  name: "Разъём",
  item_type: "Комплектующие",
  purpose: "FRX.001",
  supplier: "ООО Поставка",
  ordered_quantity: "10",
  unit: "шт",
  quality_document_required: false,
  shipping_document_url: null,
  quality_document_url: null,
  document_quantity: null,
  fact_quantity: null,
  expiry_date: null,
  disposition: null,
  verdict: null,
  nonconformities: [],
  accepted_quantity: null,
  backorder_quantity: "0",
  grn: null,
  package: "",
  storage_location: null,
  inspector: null,
  warehouse_event_emitted: false,
  inspected_on: null,
};

function json(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => vi.restoreAllMocks());

it("submits document URLs and optimistic version without browser-owned presence flags", async () => {
  let inspection: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/receiving") return json({ lines: [line] });
    if (path.endsWith("/inspect") && init?.method === "POST") {
      inspection = JSON.parse(String(init.body));
      return json({ line });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReceivingPage />);
  fireEvent.change(await screen.findByLabelText("Ссылка на УПД/М-15"), {
    target: { value: "https://files.test/upd.pdf" },
  });
  fireEvent.change(screen.getByLabelText("Серия изготовителя"), {
    target: { value: "LOT-2409" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Провести входной контроль" }));

  await waitFor(() => expect(inspection).toEqual(expect.objectContaining({
    version: 2,
    fact_quantity: "10",
    document_quantity: "10",
    shipping_document_url: "https://files.test/upd.pdf",
    series: "LOT-2409",
  })));
  expect(inspection).not.toHaveProperty("has_shipping_document");
  expect(inspection).not.toHaveProperty("quality_document_present");
  expect(inspection).toEqual(expect.objectContaining({ historical: false, document_date: null }));
});

it("sends the УПД date for a historical receipt so the verdict uses it", async () => {
  let inspection: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/receiving") return json({ lines: [line] });
    if (path.endsWith("/inspect") && init?.method === "POST") {
      inspection = JSON.parse(String(init.body));
      return json({ line });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReceivingPage />);
  fireEvent.change(await screen.findByLabelText("Ссылка на УПД/М-15"), {
    target: { value: "https://files.test/upd.pdf" },
  });
  expect(screen.queryByLabelText("Дата УПД/М-15")).toBeNull();
  fireEvent.click(screen.getByLabelText("Историческая приёмка: срок годности сверяется с датой УПД"));
  fireEvent.change(screen.getByLabelText("Дата УПД/М-15"), { target: { value: "2026-02-10" } });
  fireEvent.click(screen.getByRole("button", { name: "Провести входной контроль" }));

  await waitFor(() => expect(inspection).toEqual(expect.objectContaining({
    historical: true,
    document_date: "2026-02-10",
  })));
});

it("sends the weighed mass and prefills the sample it was weighed with on repeated control", async () => {
  let inspection: Record<string, unknown> | undefined;
  const weighed = { ...line, mass_fields: { mass_1: "", mass_10: "", mass_100: "0.123" } };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/receiving") return json({ lines: [weighed] });
    if (path.endsWith("/inspect") && init?.method === "POST") {
      inspection = JSON.parse(String(init.body));
      return json({ line: weighed });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReceivingPage />);
  expect(await screen.findByLabelText("Масса 100 шт, г")).toHaveValue("0.123");
  fireEvent.change(screen.getByLabelText("Масса 10 шт, г"), { target: { value: "0,5" } });
  fireEvent.change(screen.getByLabelText("Ссылка на УПД/М-15"), { target: { value: "https://files.test/upd.pdf" } });
  fireEvent.click(screen.getByRole("button", { name: "Провести входной контроль" }));

  await waitFor(() => expect(inspection).toEqual(expect.objectContaining({
    mass_1: "", mass_10: "0,5", mass_100: "0.123",
  })));
});

it("keeps persisted documents and manual defects during repeated control", async () => {
  let inspection: Record<string, unknown> | undefined;
  const waiting: ReceivingLine = {
    ...line,
    status: "Ожидание",
    shipping_document_url: "https://files.test/upd.pdf",
    quality_document_url: "https://files.test/quality.pdf",
    nonconformities: ["Механическое повреждение / брак"],
  };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/receiving") return json({ lines: [waiting], next_cursor: null });
    if (path.endsWith("/inspect") && init?.method === "POST") {
      inspection = JSON.parse(String(init.body));
      return json({ line: waiting });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReceivingPage />);
  expect(await screen.findByLabelText("Ссылка на УПД/М-15")).toHaveValue(
    "https://files.test/upd.pdf",
  );
  expect(screen.getByLabelText("Документ качества")).toHaveValue(
    "https://files.test/quality.pdf",
  );
  expect(screen.getByLabelText("Механическое повреждение / брак")).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Провести входной контроль" }));

  await waitFor(() => expect(inspection).toEqual(expect.objectContaining({
    shipping_document_url: "https://files.test/upd.pdf",
    quality_document_url: "https://files.test/quality.pdf",
    manual_nonconformities: ["Механическое повреждение / брак"],
  })));
});

it("shows that a paid shortfall went back to procurement as a backorder", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (String(input) === "/api/receiving") {
      return json({ lines: [{ ...line, status: "Ожидание", backorder_quantity: "2.5", nonconformities: ["Недовоз (УПД < заказ)"] }] });
    }
    throw new Error(`Unexpected request: ${String(input)}`);
  });

  render(<ReceivingPage />);

  expect(await screen.findByText("Недовоз 2.5 шт — оплаченный остаток ушёл в закупку дозавозом")).toBeInTheDocument();
});

it("opens a new incoming-control revision only for a line with a held act", async () => {
  const inspected: ReceivingLine = { ...line, status: "Ожидание", verdict: "Условно годен", version: 3 };
  let revision: Record<string, unknown> | undefined;
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/receiving") return json({ lines: revision ? [{ ...inspected, status: "Архив ВК", version: 4 }] : [inspected, { ...line, id: "other" }] });
    if (path.endsWith("/vk-revision") && init?.method === "POST") {
      revision = JSON.parse(String(init.body));
      return json({ archived: { ...inspected, status: "Архив ВК" }, line });
    }
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReceivingPage />);
  const buttons = await screen.findAllByRole("button", { name: "Провести новый ВК" });
  expect(buttons).toHaveLength(1);
  fireEvent.click(buttons[0]);

  await waitFor(() => expect(revision).toEqual({ version: 3 }));
  expect(confirm).toHaveBeenCalledOnce();
  expect(await screen.findByText(/Архив ВК: акты этой строки/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Провести новый ВК" })).toBeNull();
});

it("ID-6: view mode shows the queue without inspection, placement or a new ВК", async () => {
  const accepted = { ...line, id: "20000000-0000-0000-0000-000000000002", legacy_id: "SM-20260921-02", status: "Принято", verdict: "Годен", grn: "FRX.26.0000002" };
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/receiving") return json({ lines: [line, accepted], next_cursor: null });
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<ReceivingPage readOnly />);

  expect(await screen.findByText(/SM-20260921-01/)).toBeInTheDocument();
  expect(screen.getByText(/SM-20260921-02/)).toBeInTheDocument();
  expect(screen.getByText("Приёмка открыта вам только на просмотр.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Провести входной контроль" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Передать на склад" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Провести новый ВК" })).not.toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
});
