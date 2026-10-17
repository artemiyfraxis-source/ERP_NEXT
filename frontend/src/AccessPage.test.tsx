import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AccessPage, levelModes } from "./AccessPage";
import type { AccessOverview } from "./api";

const LEVELS = [
  { key: "superadmin", label: "Суперадминистратор", full: true },
  { key: "commercial", label: "Коммерческое руководство", full: false },
  { key: "tech_lead", label: "Технический руководитель", full: false },
  { key: "pm", label: "Менеджер по проектам", full: false },
  { key: "storekeeper", label: "Кладовщик", full: false },
  { key: "foreman", label: "Бригадир", full: false },
  { key: "operator", label: "Оператор", full: false },
];
const SECTIONS = [
  { key: "requests", label: "Заявки" }, { key: "warehouse", label: "Склад" }, { key: "myops", label: "Задания" },
  { key: "quality", label: "ОТК" }, { key: "shipping_goods", label: "Отгрузка — склад" },
];
const FULL_ROW = Object.fromEntries(SECTIONS.map((section) => [section.key, "edit" as const]));

// superadmin is always full (`full` in the answer, nothing stored for it); the other six levels are driven by
// the stored matrix, an empty cell is closed. Бригадир holds a stored «закрыт» on the warehouse.
const OVERVIEW: AccessOverview = {
  sections: SECTIONS,
  levels: LEVELS,
  defaults: { superadmin: FULL_ROW, commercial: {}, tech_lead: {}, pm: {}, storekeeper: {}, foreman: {}, operator: {} },
  seed: { foreman: { myops: "edit" } },
  matrix: {
    commercial: { requests: "edit" },
    foreman: { warehouse: "none", myops: "edit", shipping_goods: "edit" },
  },
  users: [
    { id: "u1", login: "boss", name: "Директоров", access_level: "superadmin", active: true, access: {} },
    { id: "u2", login: "brig", name: "Бригадиров", access_level: "foreman", active: true, access: { quality: "view" } },
    { id: "u3", login: "oper", name: "Операторов", access_level: "operator", active: false, access: {} },
  ],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function serve(overview: AccessOverview = OVERVIEW) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (String(input) === "/api/auth/access") return json(overview);
    throw new Error(`Unexpected request: ${String(input)}`);
  });
}

afterEach(() => vi.restoreAllMocks());

it("computes a level's sections as what it has with nothing stored plus the stored row", () => {
  expect(levelModes(OVERVIEW, "foreman")).toEqual({ myops: "edit", shipping_goods: "edit" });
  expect(levelModes({ ...OVERVIEW, matrix: { foreman: { requests: "view", myops: "none" } } }, "foreman"))
    .toEqual({ requests: "view" });
  expect(levelModes(OVERVIEW, "superadmin")).toEqual(FULL_ROW);
  expect(levelModes(OVERVIEW, "operator")).toEqual({});
});

it("«Уровни × разделы»: one column per level with the owner's names, in the owner's order, no «Администратор»", async () => {
  serve();
  render(<AccessPage />);
  await screen.findByLabelText("Склад · Бригадир");
  expect(screen.getByRole("button", { name: "Уровни × разделы" })).toHaveClass("active");
  expect(screen.queryByText("Роли × разделы")).toBeNull();
  const table = screen.getByRole("table");
  expect(within(table).getAllByRole("columnheader").map((header) => header.textContent)).toEqual([
    "Раздел", "Суперадминистратор", "Коммерческое руководство", "Технический руководитель", "Менеджер по проектам",
    "Кладовщик", "Бригадир", "Оператор",
  ]);
  expect(within(table).queryByRole("columnheader", { name: "Администратор" })).toBeNull();
});

it("the new sections «Задания», «ОТК» and «Отгрузка — склад» are rows with editable cells for the six matrix levels", async () => {
  serve();
  render(<AccessPage />);
  await screen.findByLabelText("Склад · Бригадир");
  const table = screen.getByRole("table");
  for (const name of ["Задания", "ОТК", "Отгрузка — склад"]) {
    const row = within(table).getByText(name).closest("tr") as HTMLElement;
    expect(within(row).getAllByRole("combobox")).toHaveLength(6);
    for (const level of ["Коммерческое руководство", "Технический руководитель", "Менеджер по проектам", "Кладовщик", "Бригадир", "Оператор"]) {
      expect(screen.getByLabelText(`${name} · ${level}`)).toBeInTheDocument();
    }
  }
  expect((screen.getByLabelText("Задания · Бригадир") as HTMLSelectElement).value).toBe("edit");
  expect((screen.getByLabelText("Отгрузка — склад · Оператор") as HTMLSelectElement).value).toBe("");
});

it("superadmin is fixed «полный» with a drawn padlock and a tooltip, never an editable cell", async () => {
  serve();
  render(<AccessPage />);
  expect(await screen.findByText("всегда полный доступ")).toBeInTheDocument();
  for (const level of ["Суперадминистратор"]) {
    for (const section of SECTIONS) expect(screen.queryByLabelText(`${section.label} · ${level}`)).toBeNull();
    expect(screen.getByRole("columnheader", { name: level })).toHaveAttribute("title", `${level} — всегда полный доступ`);
  }
  const locked = screen.getAllByTitle("Всегда полный доступ");
  expect(locked).toHaveLength(SECTIONS.length);
  for (const cell of locked) {
    expect(cell).toHaveTextContent(/^полный$/);
    expect(cell.querySelector("svg.access-lock")).not.toBeNull();
    expect(cell).toHaveClass("tone-ok");
  }
  expect(document.body.textContent).not.toContain("🔒");
  expect(screen.getByRole("columnheader", { name: "Бригадир" })).toHaveAttribute("title", "Бригадир");
});

it("keeps the screen short: no explanation paragraph, a one-line legend with a «?» tooltip, plain section names", async () => {
  serve();
  const { container } = render(<AccessPage />);
  await screen.findByLabelText("Склад · Бригадир");
  expect(container.querySelector("p.page-sub")).toBeNull();
  const legend = screen.getByRole("group", { name: "Обозначения" });
  expect(within(legend).getByText("не сохранено")).toBeInTheDocument();
  expect(within(legend).getByRole("note", { name: "Подсказка" }))
    .toHaveAttribute("title", expect.stringContaining("Исключение по сотруднику сильнее уровня"));
  expect(container.querySelector(".access-matrix tbody strong")).toBeNull(); // section names are regular weight
});

it("shows each cell as a chip in the tone of its mode; an empty or stored «закрыт» cell is the same pale «закрыт»", async () => {
  serve();
  render(<AccessPage />);
  await screen.findByLabelText("Склад · Бригадир");
  const chip = (label: string) => screen.getByLabelText(label).parentElement as HTMLElement;
  expect(chip("Задания · Бригадир")).toHaveTextContent(/^полный/);
  expect(chip("Задания · Бригадир")).toHaveClass("tone-ok", "access-set");
  // Склад: a stored «none» and Заявки: nothing stored both read as a closed section, and neither is a change.
  for (const label of ["Склад · Бригадир", "Заявки · Бригадир"]) {
    expect(chip(label)).toHaveTextContent(/^закрыт/);
    expect(chip(label)).toHaveClass("access-default", "access-none");
    expect(chip(label)).not.toHaveClass("access-changed");
    expect((screen.getByLabelText(label) as HTMLSelectElement).value).toBe("");
  }
  const options = within(screen.getByLabelText("Склад · Бригадир")).getAllByRole("option").map((option) => option.textContent);
  expect(options).toEqual(["закрыт", "просмотр", "полный"]);
  fireEvent.change(screen.getByLabelText("Склад · Бригадир"), { target: { value: "view" } });
  expect(chip("Склад · Бригадир")).toHaveTextContent(/^просмотр/);
  expect(chip("Склад · Бригадир")).toHaveClass("tone-info", "access-set", "access-changed");
});

it("saves the whole level matrix to /access/levels, without the rows that open nothing", async () => {
  const saved: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/auth/access/levels" && init?.method === "PUT") {
      saved.push(JSON.parse(String(init.body)));
      return json({ matrix: {} });
    }
    if (path === "/api/auth/access") return json(OVERVIEW);
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<AccessPage />);
  await screen.findByLabelText("Склад · Бригадир");
  const save = screen.getByRole("button", { name: "Сохранить доступы уровней" });
  expect(save).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Заявки · Коммерческое руководство"), { target: { value: "" } }); // the only cell of the row
  fireEvent.change(screen.getByLabelText("ОТК · Технический руководитель"), { target: { value: "view" } });
  fireEvent.change(screen.getByLabelText("Склад · Бригадир"), { target: { value: "edit" } });
  fireEvent.change(screen.getByLabelText("Задания · Оператор"), { target: { value: "edit" } });
  expect(save).toBeEnabled();
  fireEvent.click(save);

  await waitFor(() => expect(saved).toEqual([{
    matrix: {
      tech_lead: { quality: "view" },
      foreman: { warehouse: "edit", myops: "edit", shipping_goods: "edit" },
      operator: { myops: "edit" },
    },
  }]));
  expect(await screen.findByText(/Доступы уровней сохранены/)).toBeInTheDocument();
});

it("shows the server's refusal of a save in the error banner", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/auth/access/levels" && init?.method === "PUT") {
      return json({ detail: "Неизвестный раздел «чужой»." }, 422);
    }
    if (path === "/api/auth/access") return json(OVERVIEW);
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<AccessPage />);
  fireEvent.change(await screen.findByLabelText("Склад · Оператор"), { target: { value: "view" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить доступы уровней" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Неизвестный раздел «чужой».");
  expect(screen.getByRole("button", { name: "Сохранить доступы уровней" })).toBeEnabled(); // the edits are still there
});

it("highlights unsaved cells, shows the note and takes the changes back", async () => {
  serve();
  render(<AccessPage />);
  const warehouse = await screen.findByLabelText("Склад · Бригадир");
  const save = screen.getByRole("button", { name: "Сохранить доступы уровней" });
  const cancel = screen.getByRole("button", { name: "Отменить правки" });
  expect(screen.queryByText("Есть несохранённые правки")).toBeNull();
  expect(warehouse.parentElement).not.toHaveClass("access-changed");
  expect(save).toBeDisabled();
  expect(cancel).toBeDisabled();

  fireEvent.change(warehouse, { target: { value: "edit" } });
  expect(warehouse.parentElement).toHaveClass("access-changed", "tone-ok");
  expect(screen.getByLabelText("Заявки · Бригадир").parentElement).not.toHaveClass("access-changed");
  expect(screen.getByText("Есть несохранённые правки")).toBeInTheDocument();
  expect(save).toBeEnabled();

  fireEvent.click(cancel);
  expect(warehouse.parentElement).not.toHaveClass("access-changed");
  expect(warehouse.parentElement).toHaveTextContent(/^закрыт/);
  expect(screen.queryByText("Есть несохранённые правки")).toBeNull();
  expect(save).toBeDisabled();
});

it("a cell set and set back is no change (the row left empty equals the row never stored)", async () => {
  serve();
  render(<AccessPage />);
  const requests = await screen.findByLabelText("Заявки · Оператор");
  fireEvent.change(requests, { target: { value: "view" } });
  expect(screen.getByText("Есть несохранённые правки")).toBeInTheDocument();
  fireEvent.change(requests, { target: { value: "" } });
  expect(screen.queryByText("Есть несохранённые правки")).toBeNull();
  expect(screen.getByRole("button", { name: "Сохранить доступы уровней" })).toBeDisabled();
});

it("edits one person's exceptions against what the level gives", async () => {
  const saved: unknown[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path === "/api/auth/access/users/u2" && init?.method === "PUT") {
      saved.push(JSON.parse(String(init.body)));
      return json({ user_id: "u2", access: { warehouse: "view" } });
    }
    if (path === "/api/auth/access") return json(OVERVIEW);
    throw new Error(`Unexpected request: ${path}`);
  });

  render(<AccessPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Исключения по сотрудникам" }));
  const person = screen.getByLabelText("Сотрудник");
  expect(within(person).getByRole("option", { name: "Бригадиров · Бригадир · исключений: 1" })).toBeInTheDocument();
  expect(within(person).getByRole("option", { name: "Операторов · Оператор · отключён" })).toBeInTheDocument();
  fireEvent.change(person, { target: { value: "u2" } });

  const table = within(screen.getByRole("region", { name: "Исключения: Бригадиров" })).getByRole("table");
  expect(within(table).getAllByRole("columnheader").map((header) => header.textContent)).toEqual([
    "Раздел", "У уровня «Бригадир»", "Исключение",
  ]);
  const warehouseRow = within(table).getByText("Склад").closest("tr")!;
  expect(within(warehouseRow).getByText("закрыт", { selector: "span" })).toBeInTheDocument(); // the level's row has it closed
  expect((screen.getByLabelText("Исключение: ОТК") as HTMLSelectElement).value).toBe("view");
  expect((screen.getByLabelText("Исключение: Склад") as HTMLSelectElement).value).toBe("");
  expect(within(screen.getByLabelText("Исключение: Склад")).getAllByRole("option").map((option) => option.textContent))
    .toEqual(["как у уровня", "закрыт", "просмотр", "полный"]);

  fireEvent.change(screen.getByLabelText("Исключение: Склад"), { target: { value: "view" } });
  fireEvent.change(screen.getByLabelText("Исключение: ОТК"), { target: { value: "" } });
  fireEvent.change(screen.getByLabelText("Исключение: Задания"), { target: { value: "none" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить исключения" }));

  await waitFor(() => expect(saved).toEqual([{ access: { warehouse: "view", myops: "none" } }]));
  expect(await screen.findByText("Исключения сохранены: Бригадиров")).toBeInTheDocument();
});

it("a person of a full level has only the padlock: nothing to edit, nothing to save", async () => {
  serve();
  const { container } = render(<AccessPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Исключения по сотрудникам" }));
  expect(container.querySelector("p.page-sub")).not.toBeNull(); // the empty state says what to do next
  fireEvent.change(screen.getByLabelText("Сотрудник"), { target: { value: "u1" } });
  expect(screen.queryByLabelText("Исключение: Склад")).toBeNull();
  expect(screen.getAllByTitle("Всегда полный доступ")).toHaveLength(SECTIONS.length);
  expect(screen.queryByRole("button", { name: "Сохранить исключения" })).toBeNull();
});
