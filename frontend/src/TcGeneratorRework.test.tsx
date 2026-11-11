import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import type { KitsetBom, ProjectTechcard } from "./api";
import { draftKey } from "./useGeneratorDraft";

const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000013", version: 1, project: "P-1", item_index: "КБ13", product_name: "Доработанное изделие",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-11-11T12:00:00+03:00", updated_at: "2026-11-11T12:00:00+03:00", issued_at: null,
};
const kit: KitsetBom = { project: "P-1", index: "КБ13", name: "Доработанное изделие", run: "10", part_qty: "10", calc_source: "", calc_ops: [],
  components: [{ id: "kit-0", art: "ДИ-1", name: "Давальческое изделие", type: "Изделие", qty: 2.5, kd: "" }] };

function mockApi() {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    const body = path === "/api/techcards/templates" ? { metadata: null, items:
      [["CUT_WIRE_hand", "Резка"], ["STRIP_WIRE", "Разделка"], ["SDR_tin", "Лужение"], ["INS_MARK", "Монтаж маркировки"]]
        .map(([code, name]) => ({ id: code, code, title: `${code} | ${name}`, name, side: "", category: name, steps: 1, tooling: 1 })) }
      : path.includes("/generator-draft") ? { draft: null }
        : path.includes("/assembly/drafts") ? { items: [] } : { metadata: null, columns: [], records: [] };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); });

it("builds only rework operations and restores the issued form with its input and quantities", async () => {
  mockApi();
  const onBuild = vi.fn();
  const rendered = render(<TcGenerator card={card} kit={kit} busy={false} onBuild={onBuild} onDiscard={vi.fn()} />);
  fireEvent.click(await screen.findByRole("tab", { name: "Доработка давальческого изделия" }));
  fireEvent.change(screen.getByLabelText("Входное изделие, м"), { target: { value: "kit-0" } });
  fireEvent.change(screen.getByLabelText("Длина реза"), { target: { value: "250" } });
  fireEvent.change(screen.getByLabelText("Готовых изделий"), { target: { value: "10" } });
  fireEvent.change(screen.getByLabelText("Обработка концов"), { target: { value: "tin" } });
  fireEvent.change(screen.getByLabelText("Длина зачистки"), { target: { value: "5" } });
  fireEvent.click(screen.getByRole("radio", { name: "Оба конца" }));
  expect(screen.getByText("Расход входного изделия: 2,5 м")).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Кабели" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  await waitFor(() => expect(onBuild).toHaveBeenCalledOnce());
  const cfg = onBuild.mock.calls[0][0];
  expect(cfg).toMatchObject({ mode: "rework", partQty: 10, rework: { input: { id: "kit-0" }, ends: "AB" } });
  expect(cfg.ops.map((op: { type: string }) => op.type)).toEqual(["cutWire", "stripCable", "tin", "stripCable", "tin"]);
  expect(cfg).not.toHaveProperty("pinout");
  rendered.unmount();
  render(<TcGenerator card={{ ...card, status: "Выдана", document: { build_config: JSON.parse(JSON.stringify(cfg)) } as ProjectTechcard["document"] }}
    kit={kit} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} />);
  expect(await screen.findByRole("tab", { name: "Доработка давальческого изделия", selected: true })).toBeInTheDocument();
  expect(screen.getByLabelText("Входное изделие, м")).toHaveValue("kit-0");
  expect(screen.getByLabelText("Длина реза")).toHaveValue("250");
  expect(screen.getByLabelText("Готовых изделий")).toHaveValue("10");
  expect(screen.getByRole("radio", { name: "Оба конца" })).toHaveAttribute("aria-checked", "true");
});

it("blocks invalid length visibly before calling build", async () => {
  mockApi();
  const onBuild = vi.fn();
  render(<TcGenerator card={card} kit={kit} busy={false} onBuild={onBuild} onDiscard={vi.fn()} />);
  fireEvent.click(await screen.findByRole("tab", { name: "Доработка давальческого изделия" }));
  fireEvent.change(screen.getByLabelText("Входное изделие, м"), { target: { value: "kit-0" } });
  fireEvent.change(screen.getByLabelText("Длина реза"), { target: { value: "-250" } });
  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("положительную длину реза");
  expect(onBuild).not.toHaveBeenCalled();
});

it("reopens the unbuilt generator draft with its rework selection", async () => {
  mockApi();
  const props = { card, kit, userId: "rework-user", busy: false, onBuild: vi.fn(), onDiscard: vi.fn() };
  const view = render(<TcGenerator {...props} />);
  fireEvent.click(await screen.findByRole("tab", { name: "Доработка давальческого изделия" }));
  fireEvent.change(screen.getByLabelText("Входное изделие, м"), { target: { value: "kit-0" } });
  fireEvent.change(screen.getByLabelText("Длина реза"), { target: { value: "250,5" } });
  view.unmount();
  const saved = JSON.parse(window.localStorage.getItem(draftKey("rework-user", "P-1", "КБ13"))!);
  expect(saved.value.S.rework.length).toBe("250,5");
  render(<TcGenerator {...props} />);
  expect(await screen.findByRole("tab", { name: "Доработка давальческого изделия", selected: true })).toBeInTheDocument();
  expect(screen.getByLabelText("Длина реза")).toHaveValue("250,5");
  expect(screen.getByLabelText("Входное изделие, м")).toHaveValue("kit-0");
});

it("allows the length of a marking tube whose kit article does not name its type", async () => {
  mockApi();
  const onBuild = vi.fn();
  const markedKit = { ...kit, components: [...kit.components,
    { id: "kit-1", art: "MK-1", name: "Термотрубка маркировочная", type: "Маркировка", qty: 1, kd: "" }] };
  render(<TcGenerator card={card} kit={markedKit} busy={false} onBuild={onBuild} onDiscard={vi.fn()} />);
  fireEvent.click(await screen.findByRole("tab", { name: "Доработка давальческого изделия" }));
  fireEvent.change(screen.getByLabelText("Входное изделие, м"), { target: { value: "kit-0" } });
  fireEvent.change(screen.getByLabelText("Длина реза"), { target: { value: "250" } });
  fireEvent.click(screen.getByRole("switch", { name: "Маркировка жгута" }));
  fireEvent.click(screen.getByRole("button", { name: "+ Метка" }));
  fireEvent.change(screen.getByLabelText("Надпись 1"), { target: { value: "ДИ-1" } });
  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("длину трубки");
  expect(onBuild).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Длина трубки 1"), { target: { value: "25" } });
  fireEvent.click(screen.getByRole("button", { name: "Создать" }));
  await waitFor(() => expect(onBuild).toHaveBeenCalledOnce());
  expect(onBuild.mock.calls[0][0].ops.at(-1).marks).toEqual([expect.objectContaining({ art: "MK-1", lengthMm: 25 })]);
});
