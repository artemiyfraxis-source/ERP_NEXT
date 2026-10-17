import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Principal, ProjectTechcard } from "./api";
import { EtkSheet } from "./ProjectEtk";
import { etkPrintPages } from "./etkPrint";
import { etkCardSourceNote } from "./etkCardSource";

/* Владелец 17.10: карточка операции без правок идёт из базы (шаблона), а не копией; у операции видно, откуда она, а
   «Править операцию» может сохранить правку и в шаблон. */

function principal(permissions: Record<string, string>): Principal {
  return { user: { id: "u", email: "t@x.ru", name: "Технолог", department: "" }, roles: [], permissions } as Principal;
}

function card(source: string, changedAt = ""): ProjectTechcard {
  return { id: "t", version: 3, project: "614К.1", item_index: "КБ1", product_name: "Жгут", revision: 1, status: "Выдана",
    manual_operation_keys: [], document: {
      kit: { part_quantity: "10" },
      operations: [{
        position: 1, code: "CUT_WIRE", connector: "1", side: "", type: "cutWire", title: "Резка", template_id: "cut-wire",
        card_source: source, card_changed_at: changedAt,
        card: { name: "Резка", safety: "", defects: "", steps: [{ number: "1", kind: "Операция", text: "Отрезать провод", tolerance: "" }], tooling: [] },
        components: [], semis: [], result: [], time: [],
      }],
    } } as unknown as ProjectTechcard;
}

function mockFetch() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const body = String(input).includes("/operations/") && init?.method === "PUT" ? card("template", "2026-10-17T12:00:00+03:00") : { items: [] };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

afterEach(() => vi.restoreAllMocks());

it("names where the card comes from: the template with its date, a hand edit in this ETK, the copy of the build", () => {
  expect(etkCardSourceNote({ card_source: "template", card_changed_at: "2026-10-17T12:00:00+03:00" })).toBe("по шаблону от 17.10");
  expect(etkCardSourceNote({ card_source: "template", card_changed_at: "" })).toBe("по шаблону");
  expect(etkCardSourceNote({ card_source: "edited", card_changed_at: "2026-10-17T09:30:00" })).toBe("правлена в ТК 17.10");
  expect(etkCardSourceNote({ card_source: "snapshot" })).toBe("копия на момент сборки");
  expect(etkCardSourceNote({})).toBe("");
});

it("shows the note under the operation title and prints the same line", () => {
  mockFetch();
  render(<EtkSheet card={card("template", "2026-10-17T12:00:00+03:00")} principal={principal({ techcards: "view" })} onChange={() => undefined} />);

  expect(screen.getByText("по шаблону от 17.10")).toBeInTheDocument();
  const page = etkPrintPages(card("edited", "2026-10-16T08:00:00"), [], {}).find((item) => item.title.startsWith("Операция 1."));
  expect(page?.subtitle).toBe("правлена в ТК 16.10");
});

it("«Сохранить также в шаблон» asks first and sends the edit to the template too", async () => {
  const fetch = mockFetch();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  const changed = vi.fn();
  render(<EtkSheet card={card("template")} principal={principal({ techcards: "edit", erptctpl: "edit" })} onChange={changed} />);

  fireEvent.click(screen.getByRole("button", { name: "Править операцию" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Сохранить также в шаблон" }));
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

  await waitFor(() => expect(changed).toHaveBeenCalled());
  expect(confirm.mock.calls[0][0]).toMatch(/все ЭТК с этой операцией/);
  const put = fetch.mock.calls.find(([, init]) => init?.method === "PUT");
  expect(JSON.parse(String(put?.[1]?.body))).toMatchObject({ version: 3, to_template: true });
});

it("without the right to edit templates the checkbox is not offered; a plain edit stays in this ETK", async () => {
  const fetch = mockFetch();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  const changed = vi.fn();
  render(<EtkSheet card={card("template")} principal={principal({ techcards: "edit" })} onChange={changed} />);

  fireEvent.click(screen.getByRole("button", { name: "Править операцию" }));
  expect(screen.queryByRole("checkbox", { name: "Сохранить также в шаблон" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

  await waitFor(() => expect(changed).toHaveBeenCalled());
  expect(confirm).not.toHaveBeenCalled();
  const put = fetch.mock.calls.find(([, init]) => init?.method === "PUT");
  expect(JSON.parse(String(put?.[1]?.body))).not.toHaveProperty("to_template");
});
