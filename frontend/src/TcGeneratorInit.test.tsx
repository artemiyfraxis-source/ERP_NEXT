import { fireEvent, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import type { KitsetBom, ProjectTechcard } from "./api";

const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000008", version: 1, project: "P-1", item_index: "КБ8", product_name: "Жгут",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-10-05T10:00:00+03:00", updated_at: "2026-10-05T10:00:00+03:00", issued_at: null,
};
const kit: KitsetBom = { project: "P-1", index: "КБ8", name: "Жгут", run: "1", part_qty: "1", calc_source: "", calc_ops: [], components: [] };

afterEach(() => vi.restoreAllMocks());

/* Вкладки вида сборки появляются в том же коммите, что и справочники. Клик, пойманный сразу после появления вкладок
   (так ждёт findBy*: MutationObserver срабатывает раньше обычных эффектов), не должен затираться засевом формы. */
it("keeps the tab chosen at the moment the tabs appear (the seeding does not overwrite it)", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    const body = path === "/api/techcards/templates" ? { metadata: null, items: [] }
      : path.includes("/assembly/drafts") ? { items: [] } : { metadata: null, columns: [], records: [] };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  const { container } = render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kit} />);
  const manualTab = await new Promise<Element>((resolve) => {
    const observer = new MutationObserver(() => {
      const tab = container.querySelector("[role=tab]:nth-child(4)");
      if (!tab) return;
      observer.disconnect();
      fireEvent.click(tab);
      resolve(tab);
    });
    observer.observe(container, { childList: true, subtree: true });
  });
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(manualTab).toHaveTextContent("Ручная сборка");
  expect(manualTab).toHaveAttribute("aria-selected", "true");
});
