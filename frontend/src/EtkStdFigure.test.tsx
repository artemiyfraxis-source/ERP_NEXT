import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ProjectTechcard, StdFigure } from "./api";
import { DefectPicture } from "./EtkStdFigure";
import { etkPrintPages } from "./etkPrint";
import { buildPrintDocumentHtml } from "./print";
import { customerFigures, indexStdFigures, loadStdFigures, resetStdFigures } from "./stdFigures";

/* Рисунки стандартов к строкам СТО: карточка дефекта показывает их вместо своего SVG, печать ЭТК — с подписями. */

function figure(over: Partial<StdFigure>): StdFigure {
  return {
    id: "f1", sto_code: "4.3.14", position: 1, role: "norm", source_kind: "gost", caption: "ГОСТ Р 72182.2-2025, табл. А.1, в)",
    content_type: "image/png", sha256: "a".repeat(64), width: 120, height: 40, size: 900, customer_allowed: true,
    created_at: "2026-10-23T12:00:00", created_by: "Технолог", ...over,
  };
}

const FIGURES = [
  figure({ id: "f2", position: 2, role: "defect", source_kind: "ipc", caption: "IPC/WHMA-A-620E, рис. 3-12", customer_allowed: false, sha256: "b".repeat(64) }),
  figure({}),
];

function serve(items: StdFigure[]) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
    new Response(JSON.stringify({ items }), { status: 200, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => { vi.restoreAllMocks(); resetStdFigures(); });

it("shows the standard figures of the item's СТО row side by side, by position, with role badges and source captions", async () => {
  serve(FIGURES);
  const { container } = render(<DefectPicture className="etk-def-pic" name="4.3.14 Высота обжима вне допуска" code="PRS_TERMINAL_auto" />);
  await waitFor(() => expect(container.querySelectorAll("img")).toHaveLength(2));
  const images = [...container.querySelectorAll("img")];
  expect(images.map((image) => image.getAttribute("alt"))).toEqual(["ГОСТ Р 72182.2-2025, табл. А.1, в)", "IPC/WHMA-A-620E, рис. 3-12"]);
  expect(images[0].getAttribute("src")).toMatch(/^\/api\/techcards\/std-figures\/f1\/image\?v=/);
  expect(images.every((image) => image.getAttribute("loading") === "lazy")).toBe(true);
  expect(screen.getByText("✓ Норма").className).toContain("norm");
  expect(screen.getByText("✗ Брак").className).toContain("defect");
  expect(images[0].closest("a")?.getAttribute("target")).toBe("_blank");
  expect(screen.getByText("IPC/WHMA-A-620E, рис. 3-12").tagName).toBe("FIGCAPTION");
  expect(container.querySelector("svg")).toBeNull();
  expect(globalThis.fetch).toHaveBeenCalledTimes(1);
});

it("falls back to the library SVG for an item without figures or without an СТО code", async () => {
  serve(FIGURES);
  const { container } = render(<>
    <DefectPicture className="etk-def-pic" name="4.1.2 Длина вне допуска" code="CUT_WIRE_auto" />
    <DefectPicture className="etk-def-pic" name="Высота обжима вне допуска" code="PRS_TERMINAL_auto" />
  </>);
  await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
  await loadStdFigures();
  expect(container.querySelectorAll("img")).toHaveLength(0);
  expect(container.querySelectorAll("span.etk-def-pic > svg")).toHaveLength(2);
});

it("keeps the library SVG when the list does not load", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 500 }));
  const { container } = render(<DefectPicture className="etk-def-pic" name="4.3.14 Высота обжима вне допуска" />);
  await loadStdFigures();
  expect(container.querySelector("span.etk-def-pic > svg")).not.toBeNull();
  expect(container.querySelector("img")).toBeNull();
});

it("the customer filter keeps only ГОСТ figures", () => {
  expect(customerFigures(FIGURES).map((item) => item.id)).toEqual(["f1"]);
});

it("prints the figures with captions under «Типовые дефекты» of the shop ETK, ГОСТ and IPC alike", () => {
  const card = { id: "t", project: "614К.1", item_index: "КБ1", product_name: "Жгут", revision: 1, status: "Выдана", document: {
    operations: [{ position: 1, code: "PRS_TERMINAL_auto", type: "insTerm", title: "Опрессовка", card: {
      name: "Опрессовка", defects: "4.3.14 Высота обжима вне допуска; 4.3.2 Контакт деформирован",
    } }],
  } } as unknown as ProjectTechcard;
  const index = indexStdFigures(FIGURES);
  const html = buildPrintDocumentHtml("ТК", etkPrintPages(card, [], {}, null, { index, base: "https://erp.example" }));
  expect(html).toContain('src="https://erp.example/api/techcards/std-figures/f1/image?v=');
  expect(html).toContain("IPC/WHMA-A-620E, рис. 3-12");
  expect(html.indexOf("/std-figures/f1/")).toBeLessThan(html.indexOf("/std-figures/f2/"));
  expect(html).toContain(">Норма<");
  const plain = buildPrintDocumentHtml("ТК", etkPrintPages(card, []));
  expect(plain).not.toContain("/api/techcards/std-figures");
});
