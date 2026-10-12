import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RequestsPage } from "./RequestsPage";
import type { Principal, RequestSchema } from "./api";
import { purposeLabel } from "./purposeLabels";
import { resetStageOptions } from "./StageOptions";
import {
  applyHeaderToAll, chooseProject, emptyHeader, emptyLine, payloadV2, resolveLine, validateForm,
} from "./requestFormV2Logic";

const principal: Principal = {
  user: { id: "10000000-0000-0000-0000-000000000001", email: "b@example.test", name: "Закупщик", department: "Закупки" },
  roles: ["tech_lead"],
  access_level: "tech_lead",
  permissions: { newrequest: "edit", myrequests: "edit", requests: "edit", procurement: "edit" },
};
const MOSCOW = "г. Москва, ул. 6-я Радиальная, д. 17";
const schema: RequestSchema = {
  required: ["purpose", "name", "quantity", "unit"],
  purposes: ["RND", "FRX", "Хоз деятельность", "Обеспечение производства"],
  delivery_points: [MOSCOW, "г. Раменское, ул. 100-й Свирской Дивизии, д. 52"],
  units: ["шт", "кг"],
  categories: ["Общепром", "ИП", "ОП"],
  projects: ["591К.3"],
  item_types: ["Комплектующие", "Расходники"],
};
const item = {
  id: "i1", name: "Разъём Fakra", part_number: "FK-708", manufacturer: null, category: "connector", item_type: "Комплектующие",
  unit: "кг", notes: null, archived: false, version: 1, last_price: null, purchases: 0, linked_lines: 0, demand: [],
  available: null, available_unit: null, stock_lots: 0,
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function stub(options: { form: boolean; items?: boolean }) {
  const posts: Array<Record<string, string>[]> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/stage-options") {
      return json({
        available: true,
        options: [
          { key: "requests.form", block: "Заявки", title: "t", before: "", after: "", enabled: options.form },
          { key: "references.items", block: "Справочники", title: "t", before: "", after: "", enabled: Boolean(options.items) },
        ],
      });
    }
    if (path === "/api/requests/schema") return json(schema);
    if (path.startsWith("/api/purchasing/items")) {
      if (!options.items) return json({ detail: "not found" }, 404);
      return json({ items: [item], counts: {}, total: 1, page: 1, page_size: 50 });
    }
    if (path === "/api/requests" && init?.method === "POST") {
      posts.push(JSON.parse(String(init.body)).requests);
      return json({ requests: [{ legacy_id: "REQ-1" }] }, 201);
    }
    if (path === "/api/requests") return json({ requests: [] });
    throw new Error(`Unexpected request: ${path}`);
  });
  return posts;
}

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
  cleanup(); // unmount before the reset: a live tree would refetch the options without the mock
  resetStageOptions();
});

async function openForm(options: { form: boolean; items?: boolean }) {
  const posts = stub(options);
  render(<RequestsPage principal={principal} view="create" />);
  if (options.form) await screen.findByLabelText("Проект");
  else await screen.findByLabelText("Наименование, строка 1");
  return posts;
}

async function chooseOption(field: HTMLElement, text: string) {
  fireEvent.focus(field);
  fireEvent.mouseDown(within(await screen.findByRole("listbox")).getByRole("option", { name: text }));
}

async function fillLine(unit = "шт") {
  fireEvent.change(screen.getByLabelText("Наименование, строка 1"), { target: { value: "Кабель" } });
  fireEvent.change(screen.getByLabelText("Кол-во, строка 1"), { target: { value: "2" } });
  await chooseOption(screen.getByLabelText("Ед., строка 1"), unit);
}

describe("requests.form off", () => {
  it("renders the old form", async () => {
    await openForm({ form: false });
    expect(screen.getByText("Точка отгрузки")).toBeInTheDocument();
    expect(screen.queryByLabelText("Проект")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Применить ко всем строкам" })).not.toBeInTheDocument();
  });
});

describe("requests.form on", () => {
  it("requires a project and a warehouse and sends nothing until they are chosen", async () => {
    const posts = await openForm({ form: true });
    await fillLine();
    fireEvent.click(screen.getByRole("button", { name: "Оформить 1 позицию" }));
    const alerts = (await screen.findAllByRole("alert")).map((node) => node.textContent);
    expect(alerts).toContain("Выберите проект: заказчика или внутренний");
    expect(alerts).toContain("Выберите склад");
    expect(posts).toHaveLength(0);
  });

  it("has no Назначение control and no Категория; shows the project's purpose read-only", async () => {
    await openForm({ form: true });
    expect(screen.queryByLabelText("Назначение")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Категория")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Категория, строка 1")).not.toBeInTheDocument();
    expect(screen.queryByText("Категория")).not.toBeInTheDocument();
    expect(screen.queryByText(/Назначение:/)).not.toBeInTheDocument();

    await chooseOption(screen.getByLabelText("Проект"), "591К.3");
    expect(screen.getByText("Назначение: Коммерческий проект (заказ клиента)")).toBeInTheDocument();
    await chooseOption(screen.getByLabelText("Проект"), "НИОКР (опытный образец)");
    expect(screen.getByText("Назначение: НИОКР (опытный образец)")).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "RND" })).not.toBeInTheDocument();
  });

  it("док. качества: header Нет / Да (default Нет), a line как в шапке / Да / Нет", async () => {
    await openForm({ form: true });
    const header = screen.getByLabelText("Док. качества") as HTMLSelectElement;
    expect(header.value).toBe("Нет");
    expect(within(header).getAllByRole("option").map((o) => o.textContent)).toEqual(["Нет", "Да"]);
    const line = screen.getByLabelText("Док. качества, строка 1");
    expect(within(line).getAllByRole("option").map((o) => o.textContent)).toEqual(["как в шапке", "Да", "Нет"]);
  });

  it("shows warehouses with codes, no preselected value, and sends the address", async () => {
    const posts = await openForm({ form: true });
    const point = screen.getByLabelText("Склад (куда доставить)") as HTMLSelectElement;
    expect(point.value).toBe("");
    expect(within(point).getByRole("option", { name: `ЦАР — ${MOSCOW}` })).toBeInTheDocument();
    expect(within(point).getByRole("option", { name: /^ЖУК — / })).toBeInTheDocument();

    await chooseOption(screen.getByLabelText("Проект"), "591К.3");
    fireEvent.change(point, { target: { value: MOSCOW } });
    await fillLine();
    fireEvent.click(screen.getByRole("button", { name: "Оформить 1 позицию" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    // empty optional fields are omitted, not sent blank: the server applies its defaults
    expect(posts[0][0]).toEqual({
      purpose: "Коммерческий проект", project_reference: "591К.3", delivery_point: MOSCOW,
      name: "Кабель", quantity: "2", unit: "шт", quality_document: "Нет",
    });
    expect(posts[0][0]).not.toHaveProperty("category");
  });

  it("header «Применить ко всем строкам» overwrites the lines' own док. качества and план. дата", async () => {
    await openForm({ form: true });
    fireEvent.click(screen.getByRole("button", { name: "+ Добавить строку" }));
    fireEvent.change(screen.getByLabelText("Док. качества, строка 1"), { target: { value: "Да" } });
    fireEvent.change(screen.getByLabelText("План. дата, строка 2"), { target: { value: "2026-12-01" } });
    fireEvent.change(screen.getByLabelText("План. дата"), { target: { value: "2026-11-05" } });
    // the line edited by hand keeps its own value until the header is applied
    expect(screen.getByLabelText("Док. качества, строка 1")).toHaveValue("Да");
    expect(screen.getByLabelText("План. дата, строка 2")).toHaveValue("2026-12-01");

    fireEvent.click(screen.getByRole("button", { name: "Применить ко всем строкам" }));
    expect(screen.getByLabelText("Док. качества, строка 1")).toHaveValue("");
    expect(screen.getByLabelText("План. дата, строка 2")).toHaveValue("");
  });

  it("nomenclature autocomplete fills name, article, unit and type", async () => {
    await openForm({ form: true, items: true });
    const name = screen.getByLabelText("Наименование, строка 1");
    fireEvent.focus(name);
    fireEvent.change(name, { target: { value: "Fakra" } });
    fireEvent.mouseDown(await screen.findByRole("option", { name: /Разъём Fakra/ }));
    expect(screen.getByLabelText("Наименование, строка 1")).toHaveValue("Разъём Fakra");
    expect(screen.getByLabelText("Артикул КД, строка 1")).toHaveValue("FK-708");
    expect(screen.getByLabelText("Ед., строка 1")).toHaveValue("кг");
    expect(screen.getByLabelText("Тип, строка 1")).toHaveValue("Комплектующие");
  });

  it("the name stays a plain input when the nomenclature is off", async () => {
    await openForm({ form: true, items: false });
    const name = screen.getByLabelText("Наименование, строка 1");
    fireEvent.focus(name);
    fireEvent.change(name, { target: { value: "Fakra" } });
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(name).toHaveValue("Fakra");
  });

  it("the unit select rejects free text", async () => {
    await openForm({ form: true });
    const unit = screen.getByLabelText("Ед., строка 1");
    fireEvent.focus(unit);
    fireEvent.change(unit, { target: { value: "штучек" } });
    fireEvent.blur(unit);
    expect(unit).toHaveValue("");
    await chooseOption(unit, "кг");
    expect(unit).toHaveValue("кг");
  });

  it("pastes a block from Excel into the grid", async () => {
    await openForm({ form: true });
    fireEvent.paste(screen.getByLabelText("Наименование, строка 1"), {
      clipboardData: { getData: () => "Наименование\tКоличество\tЕдиница\nВинт\t5\tшт\nГайка\t7\tшт" },
    });
    expect(await screen.findByLabelText("Наименование, строка 2")).toHaveValue("Гайка");
    expect(screen.getByLabelText("Кол-во, строка 1")).toHaveValue("5");
  });
});

describe("logic", () => {
  it("purposeLabel writes purposes in full words and leaves other text as written", () => {
    expect(purposeLabel("RND")).toBe("НИОКР (опытный образец)");
    expect(purposeLabel("FRX")).toBe("Собственные нужды компании");
    expect(purposeLabel("Хоз деятельность")).toBe("Хозяйственная деятельность");
    expect(purposeLabel("Склад")).toBe("Склад");
    expect(purposeLabel("RND · 48К.13")).toBe("НИОКР (опытный образец) · 48К.13");
    expect(purposeLabel("FRX.26.0000001")).toBe("FRX.26.0000001");
  });

  it("a line value wins over the header; empty takes the header; category is never sent", () => {
    const header = { ...emptyHeader(), quality_document: "Да", planned_date: "2026-11-05" };
    const own = resolveLine({ ...emptyLine(), planned_date: "2026-12-01", quality_document: "Нет", category: "ОП" }, header);
    expect(own.planned_date).toBe("2026-12-01");
    expect(own.quality_document).toBe("Нет");
    expect(own.category).toBe("");
    expect(resolveLine(emptyLine(), header).planned_date).toBe("2026-11-05");
    expect(resolveLine(emptyLine(), header).quality_document).toBe("Да");
    expect(applyHeaderToAll([{ ...emptyLine(), quality_document: "Да", planned_date: "2026-12-01" }])[0].quality_document).toBe("");
    expect(emptyHeader().quality_document).toBe("Нет");
  });

  it("validates project, warehouse and unit; payload leaves empty fields out", () => {
    const line = { ...emptyLine(), name: "Х", quantity: "1", unit: "ящик" };
    const errors = validateForm([line], emptyHeader(), ["шт"]);
    expect(errors.project).toBeTruthy();
    expect(errors.delivery_point).toBeTruthy();
    expect(errors.rows[0].unit).toBeTruthy();
    const header = { ...chooseProject(emptyHeader(), "i:FRX"), delivery_point: MOSCOW };
    expect(header.project_reference).toBe("");
    expect(payloadV2({ ...line, unit: "шт" }, header)).toEqual({
      purpose: "FRX", delivery_point: MOSCOW, name: "Х", quantity: "1", unit: "шт", quality_document: "Нет",
    });
    expect(chooseProject(emptyHeader(), "c:591К.3").purpose).toBe("Коммерческий проект");
    expect(purposeLabel("Коммерческий проект")).toBe("Коммерческий проект (заказ клиента)");
  });
});
