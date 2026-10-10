import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import type { KitsetBom, ProjectTechcard } from "./api";
import { draftKey } from "./useGeneratorDraft";
import { pressBuild } from "./test/pressBuild";

/* Владелец 10.10: «ввёл данные и переключился — всё слетело». Переход на другую вкладку карточки размонтирует генератор;
   введённое возвращается при следующем открытии, «Создать» черновик убирает. */

const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000021", version: 1, project: "P-21", item_index: "КБ3", product_name: "Жгут XHP 150мм",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-10-10T10:00:00+03:00", updated_at: "2026-10-10T10:00:00+03:00", issued_at: null,
};
const kit: KitsetBom = {
  project: "P-21", index: "КБ3", name: "Жгут XHP 150мм", run: "1", part_qty: "1", calc_source: "", calc_ops: [],
  components: [
    { id: "kit-0", kd: "", type: "Провод", name: "МГТФ 0,12", art: "МГТФ 0,12", qty: 0.3 },
    { id: "kit-1", kd: "", type: "Терминал", name: "Контакт XH", art: "SXH-001T-P0.6", qty: 4 },
    { id: "kit-2", kd: "", type: "Соед", name: "Колодка XH 2", art: "XHP-2", qty: 2 },
  ],
};
const KEY = draftKey("user-1", "P-21", "КБ3");

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  window.localStorage.clear();
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [
      { id: "cut", code: "CUT_WIRE_auto", side: "", title: "CUT_WIRE_auto | Резка", name: "Резка", category: "Резка", steps: 1, tooling: 1 },
      { id: "prsA", code: "PRS", side: "A", title: "PRS_TERMINAL | Опрессовка терминалов (А)", name: "", category: "Опрессовка терминалов", steps: 1, tooling: 1 },
    ] });
    if (path.includes("/assembly/drafts")) return json({ items: [] });
    if (path === "/api/references/technology/resolve-terminal") return json({ applicator: "", crimp_height: "", pull_force: "", by_section: [] });
    return json({ metadata: null, columns: [], records: [] });
  });
});
afterEach(() => { vi.restoreAllMocks(); window.localStorage.clear(); });

const sheetSwitch = () => screen.getByRole("switch", { name: "Свод материалов отдельным листом в ЭТК" });

it("keeps the typed form through a tab switch, restores it with a note and clears it after «Создать»", async () => {
  const first = render(<TcGenerator card={card} kit={kit} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} userId="user-1" />);
  await screen.findByRole("tab", { name: "Межплатная", selected: true });
  expect(sheetSwitch()).not.toBeChecked();
  fireEvent.click(sheetSwitch());
  expect(sheetSwitch()).toBeChecked();
  first.unmount(); // переход на «Состав»/«Снабжение» — генератор размонтирован до автосохранения

  const onBuild = vi.fn().mockResolvedValue(true);
  render(<TcGenerator card={card} kit={kit} busy={false} onBuild={onBuild} onDiscard={vi.fn()} userId="user-1" />);
  await screen.findByRole("tab", { name: "Межплатная", selected: true });
  expect(sheetSwitch()).toBeChecked();
  expect(screen.getByText(/Восстановлен несохранённый черновик от/)).toBeInTheDocument();

  vi.spyOn(window, "confirm").mockReturnValue(true);
  pressBuild();
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  expect(onBuild.mock.calls[0][0]).toMatchObject({ materialsSheet: true });
  await waitFor(() => expect(screen.queryByText(/Восстановлен несохранённый черновик/)).not.toBeInTheDocument());
  expect(window.localStorage.getItem(KEY)).toBeNull();
});

it("keeps the draft when the build fails", async () => {
  const first = render(<TcGenerator card={card} kit={kit} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} userId="user-1" />);
  await screen.findByRole("tab", { name: "Межплатная", selected: true });
  fireEvent.click(sheetSwitch());
  first.unmount();

  const onBuild = vi.fn().mockResolvedValue(false);
  render(<TcGenerator card={card} kit={kit} busy={false} onBuild={onBuild} onDiscard={vi.fn()} userId="user-1" />);
  await screen.findByRole("tab", { name: "Межплатная", selected: true });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  pressBuild();
  await waitFor(() => expect(onBuild).toHaveBeenCalled());
  expect(window.localStorage.getItem(KEY)).not.toBeNull();
});

it("«Отбросить» returns the form to the kit seeding", async () => {
  const first = render(<TcGenerator card={card} kit={kit} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} userId="user-1" />);
  await screen.findByRole("tab", { name: "Межплатная", selected: true });
  fireEvent.click(screen.getByRole("tab", { name: "Сложная сборка" }));
  first.unmount();

  render(<TcGenerator card={card} kit={kit} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} userId="user-1" />);
  expect(await screen.findByRole("tab", { name: "Сложная сборка", selected: true })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Отбросить" }));
  expect(await screen.findByRole("tab", { name: "Межплатная", selected: true })).toBeInTheDocument();
  expect(window.localStorage.getItem(KEY)).toBeNull();
});
