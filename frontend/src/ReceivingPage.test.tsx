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
