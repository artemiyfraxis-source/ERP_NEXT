import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TcGenerator } from "./TcGenerator";
import type { KitsetBom, ProjectTechcard } from "./api";

/* «Записать в БД» карточки разъёма (владелец 13.10.2026: «не работает кнопка записать в бд, после записывания введённое
   число удаляется; в бд нет припусков в минус для таких чертежей» — кабель 350* от края X1 до края X2). */

const card: ProjectTechcard = {
  id: "40000000-0000-0000-0000-000000000013", version: 1, project: "P-350", item_index: "КБ1", product_name: "Жгут PicoBlade",
  source_calculation: "", deadline: null, status: "Черновик", revision: 0, document: null, manual_operation_keys: [],
  created_at: "2026-10-13T10:00:00+03:00", updated_at: "2026-10-13T10:00:00+03:00", issued_at: null,
};

const kit: KitsetBom = {
  project: "P-350", index: "КБ1", name: "Жгут PicoBlade", run: "1", part_qty: "1", calc_source: "",
  components: [
    { id: "kit-0", type: "Провод", name: "МГТФ 0,07", art: "МГТФ 0,07", kd: "", qty: 1.4 },
    { id: "kit-1", type: "Терминал", name: "Контакт PicoBlade", art: "500798000", kd: "", qty: 8 },
    { id: "kit-2", type: "Соед", name: "Колодка PicoBlade 4", art: "510210400", kd: "", qty: 2 },
  ],
  calc_ops: [],
};

const PICO = { terManufacturer: "Molex", terSeries: "51021", terArticle: "500798000", terLPlus: "", terLMinus: "", terStep: "1,25", terApplicator: "S19" };

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** Сервер справочников: `honest` — правка ложится в запись; иначе запись возвращается как была (так сервер до 13.10
    отдавал terLMinus, затёртый синхронизацией из колонки «L-»). */
function mockApi(honest: boolean, kin: Record<string, string> | null = null) {
  const ter: Record<string, Record<string, string>> = { t1: { ...PICO } };
  if (kin) ter.t2 = kin;
  const patches: unknown[] = [];
  const spy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/techcards/templates") return json({ metadata: null, items: [] });
    if (path.startsWith("/api/references/tech-operations?")) {
      const tab = new URLSearchParams(path.split("?")[1]).get("tab");
      return json({ metadata: null, columns: [], records: tab === "ter"
        ? Object.entries(ter).map(([id, fields]) => ({ id, tab: "ter", display_text: "Molex", source_sheet: "", fields: { ...fields } })) : [] });
    }
    if (path === "/api/references/tech-operations/t1" && init?.method === "PATCH") {
      const body = JSON.parse(String(init.body)) as { edits: Array<{ field: string; old: string; value: string }> };
      patches.push(body);
      if (honest) for (const edit of body.edits) ter.t1[edit.field] = edit.value;
      return json({ id: "t1", tab: "ter", display_text: "Molex", source_sheet: "", fields: { ...ter.t1 } });
    }
    if (path === "/api/references/technology/resolve-terminal") return json({ applicator: "S19", crimp_height: "", pull_force: "", by_section: [] });
    if (path.startsWith("/api/references/")) return json({ metadata: null, columns: [], records: [], series: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
  return { spy, patches, ter };
}

afterEach(() => vi.restoreAllMocks());

async function openOuter350() {
  render(<TcGenerator card={card} busy={false} onBuild={vi.fn()} onDiscard={vi.fn()} kit={kit} />);
  await screen.findByLabelText("Нормы терминала А1");
  fireEvent.click(screen.getByRole("radio", { name: /От краёв разъёмов/ }));
  fireEvent.change(screen.getByLabelText("Длина по чертежу"), { target: { value: "350" } });
}

const norms = (end: string) => within(screen.getByLabelText(`Нормы терминала ${end}`));

it("«Записать в БД» пишет L− в БД.ТЕР, оставляет его в поле и вычитает с обоих концов из 350 от краёв разъёмов", async () => {
  const { patches } = mockApi(true);
  await openOuter350();
  expect(screen.getByText(/Нет L− в БД\.ТЕР: А1 \(500798000\), В1 \(500798000\)/)).toBeInTheDocument();

  fireEvent.change(norms("А1").getByLabelText("L−"), { target: { value: "3" } });
  fireEvent.click(norms("А1").getByRole("button", { name: "Записать в БД" }));

  expect(await screen.findByText("Записано в БД.ТЕР · 500798000: L− 3 мм")).toBeInTheDocument();
  expect(patches).toEqual([{ edits: [{ field: "terLMinus", old: "", value: "3" }] }]);
  // Введённое не пропадает: L− — из записанной записи, форма не пересеяна (длина по чертежу и режим на месте).
  expect(norms("А1").getByLabelText("L−")).toHaveValue("3");
  expect(norms("В1").getByLabelText("L−")).toHaveValue("3");
  expect(norms("А1").queryByRole("button", { name: "Записать в БД" })).toBeNull();
  expect(screen.getByLabelText("Длина по чертежу")).toHaveValue(350);
  expect(screen.getByRole("radio", { name: /От краёв разъёмов/ })).toHaveAttribute("aria-checked", "true");
  // 350 от края до края − L− 3 у А − L− 3 у В.
  expect(screen.getByText("344 мм")).toBeInTheDocument();
  expect(screen.getByText(/350 − 6 мм \[L− ст\.А: 3, L− ст\.В: 3\]/)).toBeInTheDocument();
  expect(screen.queryByText(/Нет L− в БД\.ТЕР/)).toBeNull();
});

it("сервер не сохранил L− — это ошибка у кнопки, введённое число остаётся в поле", async () => {
  const { patches } = mockApi(false);
  await openOuter350();

  fireEvent.change(norms("В1").getByLabelText("L−"), { target: { value: "3" } });
  fireEvent.click(norms("В1").getByRole("button", { name: "Записать в БД" }));

  expect(await norms("В1").findByRole("alert")).toHaveTextContent("БД.ТЕР не сохранила: L−");
  expect(patches).toHaveLength(1);
  expect(norms("В1").getByLabelText("L−")).toHaveValue("3");
  expect(norms("В1").getByRole("button", { name: "Записать в БД" })).toBeInTheDocument();
});

it("ошибку сервера показывает по-русски у кнопки и не теряет введённое", async () => {
  mockApi(true);
  const fetchMock = vi.mocked(globalThis.fetch);
  const base = fetchMock.getMockImplementation()!;
  fetchMock.mockImplementation(async (input, init) => (init?.method === "PATCH"
    ? json({ detail: "Не записано ничего — отклонено полей: 1; terLMinus: запись уже поменяли — откройте её заново" }, 409)
    : base(input, init)));
  await openOuter350();

  fireEvent.change(norms("А1").getByLabelText("L−"), { target: { value: "4" } });
  fireEvent.click(norms("А1").getByRole("button", { name: "Записать в БД" }));

  expect(await norms("А1").findByRole("alert")).toHaveTextContent(/Не записано в БД\.ТЕР: .*уже поменяли/);
  expect(norms("А1").getByLabelText("L−")).toHaveValue("4");
});

it("L− не числом не уходит на сервер", async () => {
  const { patches } = mockApi(true);
  await openOuter350();

  fireEvent.change(norms("А1").getByLabelText("L−"), { target: { value: "3мм" } });
  fireEvent.click(norms("А1").getByRole("button", { name: "Записать в БД" }));

  expect(await norms("А1").findByRole("alert")).toHaveTextContent("L−: число в мм, а не «3мм»");
  expect(patches).toEqual([]);
});

it("подсказывает L− по той же серии БД.ТЕР, не записывая его, и подставляет по кнопке", async () => {
  const { patches } = mockApi(true, { ...PICO, terArticle: "500798001", terLMinus: "2,5" });
  await openOuter350();

  expect(norms("А1").getByLabelText("L−")).toHaveValue("");
  expect(screen.getAllByText("Подсказка: 2,5 мм — как у 500798001 той же серии (не записано)")).toHaveLength(2);
  expect(document.querySelector(".cut-result")).toBeNull();

  fireEvent.click(screen.getAllByRole("button", { name: "Подставить" })[0]);
  expect(norms("А1").getByLabelText("L−")).toHaveValue("2,5");
  expect(patches).toEqual([]);
  fireEvent.click(norms("А1").getByRole("button", { name: "Записать в БД" }));
  await waitFor(() => expect(patches).toEqual([{ edits: [{ field: "terLMinus", old: "", value: "2,5" }] }]));
  expect(await screen.findByText("345 мм")).toBeInTheDocument();
});

it("L+ и L− в нормах терминала подсказывают, что они меряют: конец жилы в корпусе", async () => {
  mockApi(true);
  await openOuter350();
  expect(norms("А1").getByLabelText("L+").closest("label")).toHaveAttribute("title", "L+ — глубина конца жилы от торца корпуса со стороны провода");
  expect(norms("А1").getByLabelText("L−").closest("label")).toHaveAttribute("title", "L− — от конца жилы до стыковочного торца");
  expect(norms("А1").getByLabelText("Шаг").closest("label")).not.toHaveAttribute("title");
});
