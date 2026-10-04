import { expect, it } from "vitest";
import { ACCESS_LEVEL_LABELS, accessLevelLabel, type AccessMode, type Principal } from "./api";
import { BLOCKS, NAV, canAct, canSee, hasFullAccess, isViewOnly, runsTheSystem, visibleBlocks } from "./navigation";

const user = { id: "1", email: "u@example.test", name: "Сотрудник", department: "КБ" };

// Access levels (owner 04.10.2026): `roles` holds the level key, `access_level` repeats it; screens read sections only.
function as(level: string, permissions: Record<string, AccessMode>): Principal {
  return { user, roles: [level], access_level: level, permissions };
}

function layout(principal: Principal) {
  return Object.fromEntries(visibleBlocks(principal).map((block) => [block.title, block.items.map((item) => item.label)]));
}

const section = (principal: Principal, block: string) => layout(principal)[block] ?? [];

const ALL_EDIT = Object.fromEntries(
  ["staffdir", "projects", "techcards", "erptctpl", "tcgen", "newrequest", "myrequests", "requests", "procurement",
    "receiving", "warehouse", "fulfillment", "handout", "production", "myops", "quality", "shipping", "shipping_goods",
    "erpref", "operations"].map((key) => [key, "edit" as AccessMode]),
);

const EVERYTHING = {
  "Производство": ["Проекты", "Ресурсное планирование", "Задания", "ОТК"],
  "Снабжение": ["Оформить заявку", "Мои заявки", "Заявки", "Лист закупки"],
  "Склад": ["Приёмка", "Склад", "Запросы на комплектацию", "Выдача со списанием", "Отгрузка"],
  "Менеджмент": ["Производственный план", "Сотрудники", "Доступы", "Уведомления", "Системный журнал"],
  "Справочники": ["База данных"],
};

it("keeps SUPPLYMAP's four blocks in SUPPLYMAP's order, reference books last", () => {
  expect(BLOCKS.map((block) => block.title)).toEqual(["Производство", "Снабжение", "Склад", "Менеджмент", "Справочники"]);
});

it("shows superadmin and admin every block, «Доступы» and «Сотрудники» included, whatever the permissions map holds", () => {
  for (const level of ["superadmin", "admin"]) {
    expect(layout(as(level, ALL_EDIT))).toEqual(EVERYTHING);
    // The backend map is not what makes a full level: an empty or partial one changes nothing.
    expect(layout(as(level, {}))).toEqual(EVERYTHING);
    expect(layout(as(level, { production: "view" }))).toEqual(EVERYTHING);
  }
});

// The default matrix of the plan (backend identity LEVEL_SEED) for every other level.
const OWN = { newrequest: "edit", myrequests: "edit" } as const;
const OPERATOR = { ...OWN, myops: "edit", receiving: "view", warehouse: "view", erpref: "view" } as const;
const FOREMAN = {
  ...OWN, production: "edit", myops: "edit", receiving: "edit", warehouse: "edit", fulfillment: "edit", handout: "edit",
  shipping_goods: "edit", procurement: "view", quality: "view",
} as const;
const TECH_LEAD = {
  ...OWN, projects: "edit", techcards: "edit", erptctpl: "edit", tcgen: "edit", erpref: "edit", production: "edit",
  procurement: "edit", requests: "edit", fulfillment: "edit", receiving: "view", warehouse: "view", handout: "view",
  quality: "view", shipping: "view",
} as const;
const COMMERCIAL = {
  ...OWN, projects: "view", shipping: "view", procurement: "view", warehouse: "view", production: "view", requests: "edit",
} as const;

it("gives the operator the request form, own rows, own tasks and read-only stock", () => {
  expect(layout(as("operator", OPERATOR))).toEqual({
    "Производство": ["Задания"],
    "Снабжение": ["Оформить заявку", "Мои заявки"],
    "Склад": ["Приёмка", "Склад"],
    "Менеджмент": ["Уведомления"],
    "Справочники": ["База данных"],
  });
});

it("gives the foreman the shop, the stock and the storekeeper's shipping, no reference book", () => {
  expect(layout(as("foreman", FOREMAN))).toEqual({
    "Производство": ["Ресурсное планирование", "Задания", "ОТК"],
    "Снабжение": ["Оформить заявку", "Мои заявки", "Лист закупки"],
    "Склад": ["Приёмка", "Склад", "Запросы на комплектацию", "Выдача со списанием", "Отгрузка"],
    "Менеджмент": ["Производственный план", "Уведомления"],
  });
});

it("gives the technical lead projects, planning, the whole supply block and the reference book", () => {
  expect(layout(as("tech_lead", TECH_LEAD))).toEqual({
    "Производство": ["Проекты", "Ресурсное планирование", "ОТК"],
    "Снабжение": ["Оформить заявку", "Мои заявки", "Заявки", "Лист закупки"],
    "Склад": ["Приёмка", "Склад", "Запросы на комплектацию", "Выдача со списанием", "Отгрузка"],
    "Менеджмент": ["Производственный план", "Уведомления"],
    "Справочники": ["База данных"],
  });
});

it("gives the commercial lead projects, the supply sheets and shipping for reading", () => {
  expect(layout(as("commercial", COMMERCIAL))).toEqual({
    "Производство": ["Проекты", "Ресурсное планирование"],
    "Снабжение": ["Оформить заявку", "Мои заявки", "Заявки", "Лист закупки"],
    "Склад": ["Склад", "Отгрузка"],
    "Менеджмент": ["Производственный план", "Уведомления"],
  });
});

// «Задания» is the section `myops`, split off `production` (owner 04.10.2026).
it("opens «Задания» by myops alone, for any level", () => {
  const tasks = (principal: Principal) => section(principal, "Производство").includes("Задания");
  expect(tasks(as("operator", { ...OWN }))).toBe(false);
  expect(tasks(as("operator", { ...OWN, myops: "view" }))).toBe(true);
  expect(tasks(as("operator", { ...OWN, myops: "edit" }))).toBe(true);
  // The planning key does not open the executor's screen.
  expect(tasks(as("operator", { production: "edit" }))).toBe(false);
  expect(tasks(as("foreman", { production: "edit" }))).toBe(false);
  expect(tasks(as("foreman", { myops: "view" }))).toBe(true);
  expect(tasks(as("admin", {}))).toBe(true);
});

it("opens «Ресурсное планирование» and «Производственный план» by production, not by myops", () => {
  expect(section(as("operator", { myops: "edit" }), "Производство")).toEqual(["Задания"]);
  expect(section(as("operator", { myops: "edit" }), "Менеджмент")).toEqual(["Уведомления"]);
  expect(section(as("foreman", { production: "view" }), "Производство")).toEqual(["Ресурсное планирование"]);
  expect(section(as("foreman", { production: "view" }), "Менеджмент")).toEqual(["Производственный план", "Уведомления"]);
});

// «ОТК» is the section `quality` (owner 04.10.2026): any grant opens the card, the page shows what the grant allows.
it("opens «ОТК» by the quality key, not by production", () => {
  const quality = (principal: Principal) => section(principal, "Производство").includes("ОТК");
  expect(quality(as("foreman", { quality: "view" }))).toBe(true);
  expect(quality(as("foreman", { quality: "edit" }))).toBe(true);
  expect(quality(as("foreman", { production: "edit" }))).toBe(false);
  expect(quality(as("tech_lead", { production: "edit", myops: "edit" }))).toBe(false);
  expect(quality(as("operator", {}))).toBe(false);
  expect(quality(as("superadmin", {}))).toBe(true);
});

it("keeps «Ресурсное планирование» in the production block next to «ОТК»", () => {
  expect(section(as("foreman", { production: "edit", quality: "edit" }), "Производство")).toEqual(["Ресурсное планирование", "ОТК"]);
});

it("opens «Отгрузка» by either shipping key: the manager's or the storekeeper's", () => {
  const shipping = (principal: Principal) => section(principal, "Склад").includes("Отгрузка");
  expect(shipping(as("commercial", { shipping: "view" }))).toBe(true);
  expect(shipping(as("foreman", { shipping_goods: "view" }))).toBe(true);
  expect(shipping(as("foreman", { shipping_goods: "edit" }))).toBe(true);
  expect(shipping(as("foreman", { production: "edit", receiving: "edit" }))).toBe(false);
});

it("hides «Оформить заявку» from a view-only grant on newrequest", () => {
  expect(section(as("operator", { newrequest: "view", myrequests: "edit", requests: "view" }), "Снабжение"))
    .toEqual(["Мои заявки", "Заявки"]);
});

it("NAV-SECTIONS: closing «Заявки» keeps the form and own rows", () => {
  expect(section(as("operator", OWN), "Снабжение")).toEqual(["Оформить заявку", "Мои заявки"]);
  // «Заявки» no longer opens the other two.
  expect(section(as("operator", { requests: "edit" }), "Снабжение")).toEqual(["Заявки"]);
});

it("NAV-SECTIONS: picking and handout open by their own keys", () => {
  expect(section(as("foreman", { fulfillment: "edit" }), "Склад")).toEqual(["Запросы на комплектацию"]);
  expect(section(as("foreman", { handout: "edit" }), "Склад")).toEqual(["Выдача со списанием"]);
});

it("hides a block with no section the principal may open", () => {
  const blocks = visibleBlocks(as("operator", {}));
  expect(blocks.map((block) => block.title)).toEqual(["Менеджмент"]);
});

it("opens a section by its key and the mode alone: no level name decides, a missing key closes it", () => {
  // A grant is the whole decision — the level's name adds nothing and takes nothing away.
  for (const level of ["operator", "foreman", "tech_lead", "commercial"]) {
    expect(section(as(level, { procurement: "edit" }), "Снабжение")).toEqual(["Лист закупки"]);
    expect(section(as(level, { receiving: "view" }), "Склад")).toEqual(["Приёмка"]);
    expect(section(as(level, { warehouse: "view" }), "Склад")).toEqual(["Склад"]);
  }
  expect(layout(as("tech_lead", {}))).toEqual({ "Менеджмент": ["Уведомления"] });
});

it("opens techcards, templates and the reference book by their own keys to any level (ID-3)", () => {
  // The ЭТК screen opens from the project card, as in SUPPLYMAP — no card or menu item of its own.
  expect(section(as("operator", { techcards: "view" }), "Производство")).toEqual([]);
  // «База данных» holds templates and the reference book as tabs: either key opens it (owner, 29.09.2026).
  expect(section(as("foreman", { erptctpl: "view" }), "Справочники")).toEqual(["База данных"]);
  expect(section(as("commercial", { erpref: "view" }), "Справочники")).toEqual(["База данных"]);
  // The generator has no card of its own (SUPPLYMAP: a right, not a screen).
  expect(section(as("operator", { tcgen: "view" }), "Производство")).toEqual([]);
});

it("opens «Доступы» and «Сотрудники» to superadmin and admin alone, not by any matrix grant", () => {
  for (const level of ["superadmin", "admin"]) {
    expect(section(as(level, {}), "Менеджмент")).toEqual(
      ["Производственный план", "Сотрудники", "Доступы", "Уведомления", "Системный журнал"],
    );
  }
  for (const level of ["commercial", "tech_lead", "foreman", "operator"]) {
    const principal = as(level, { ...ALL_EDIT });
    expect(section(principal, "Менеджмент")).not.toContain("Доступы");
    expect(section(principal, "Менеджмент")).not.toContain("Сотрудники");
  }
});

it("opens «Системный журнал» by the operations key", () => {
  expect(section(as("tech_lead", { operations: "view" }), "Менеджмент")).toEqual(["Уведомления", "Системный журнал"]);
  expect(section(as("tech_lead", {}), "Менеджмент")).toEqual(["Уведомления"]);
});

it("tells a card open for reading only: all its keys are view grants, never for a full-access level", () => {
  const item = (id: string) => NAV.find((entry) => entry.id === id)!;
  const viewOnly = (principal: Principal, id: string) => isViewOnly(principal, item(id));
  expect(viewOnly(as("commercial", { warehouse: "view" }), "stock")).toBe(true);
  expect(viewOnly(as("commercial", { warehouse: "edit" }), "stock")).toBe(false);
  expect(viewOnly(as("commercial", {}), "stock")).toBe(false);
  // «Отгрузка» opens by either key: reading only when neither is on edit.
  expect(viewOnly(as("foreman", { shipping_goods: "view" }), "shipping")).toBe(true);
  expect(viewOnly(as("foreman", { shipping: "view", shipping_goods: "edit" }), "shipping")).toBe(false);
  expect(viewOnly(as("commercial", { shipping: "edit" }), "shipping")).toBe(false);
  // A card without a key of its own has nothing to be view-only about.
  expect(viewOnly(as("operator", {}), "notifications")).toBe(false);
  expect(viewOnly(as("admin", { warehouse: "view" }), "stock")).toBe(false);
});

it("names the six access levels in Russian and shows an unknown key as it is", () => {
  expect(Object.keys(ACCESS_LEVEL_LABELS)).toEqual(["superadmin", "admin", "commercial", "tech_lead", "foreman", "operator"]);
  expect(accessLevelLabel("superadmin")).toBe("Суперадминистратор");
  expect(accessLevelLabel("admin")).toBe("Администратор");
  expect(accessLevelLabel("commercial")).toBe("Коммерческое руководство");
  expect(accessLevelLabel("tech_lead")).toBe("Технический руководитель");
  expect(accessLevelLabel("foreman")).toBe("Бригадир");
  expect(accessLevelLabel("operator")).toBe("Оператор");
  expect(accessLevelLabel("guest")).toBe("guest");
  expect(accessLevelLabel(undefined)).toBe("—");
});

it("tells a full-access level by the access level key, or by `roles` when the payload has none", () => {
  expect(hasFullAccess(as("superadmin", {}))).toBe(true);
  expect(hasFullAccess(as("admin", {}))).toBe(true);
  for (const level of ["commercial", "tech_lead", "foreman", "operator"]) expect(hasFullAccess(as(level, ALL_EDIT))).toBe(false);
  expect(hasFullAccess({ user, roles: ["admin"], permissions: {} })).toBe(true);
  expect(hasFullAccess({ user, roles: ["foreman"], permissions: ALL_EDIT })).toBe(false);
  expect(hasFullAccess({ user, roles: [], permissions: {} })).toBe(false);
});

it("lets a full-access level act in every section and nobody else beyond the matrix", () => {
  const admin = as("admin", {});
  expect(canAct(admin, "production")).toBe(true);
  expect(canAct(admin, "shipping_goods")).toBe(true);
  expect(canAct(admin, "quality")).toBe(true);
  expect(canSee(as("superadmin", {}), "warehouse")).toBe(true);
  // An edit grant acts, a view grant reads only, no key is closed — whatever the level is called.
  const foreman = as("foreman", { fulfillment: "edit", handout: "view" });
  expect(canAct(foreman, "fulfillment")).toBe(true);
  expect(canAct(foreman, "handout")).toBe(false);
  expect(canAct(foreman, "handout", "view")).toBe(true);
  expect(canAct(foreman, "procurement")).toBe(false);
  expect(canAct(foreman, "procurement", "view")).toBe(false);
  expect(canSee(foreman, "handout")).toBe(true);
  expect(canSee(foreman, "handout", "edit")).toBe(false);
  expect(canSee(foreman, "quality")).toBe(false);
  // A level's name never replaces the grant: the old full-access roles are plain levels now.
  expect(canAct(as("tech_lead", { fulfillment: "view" }), "fulfillment")).toBe(false);
  expect(canAct(as("operator", { fulfillment: "edit" }), "fulfillment")).toBe(true);
});

// Who is told that an update failed: not the rank-and-file operator whose edit rights are their own tasks and requests.
it("tells a person who runs the shop or the system from an operator with only their own work", () => {
  expect(runsTheSystem(as("operator", OPERATOR))).toBe(false);
  expect(runsTheSystem(as("operator", { ...OWN, myops: "edit" }))).toBe(false);
  expect(runsTheSystem(as("operator", {}))).toBe(false);
  expect(runsTheSystem(as("foreman", FOREMAN))).toBe(true);
  expect(runsTheSystem(as("operator", { ...OPERATOR, warehouse: "edit" }))).toBe(true);
  expect(runsTheSystem(as("commercial", COMMERCIAL))).toBe(true);
  // Reading is not running anything.
  expect(runsTheSystem(as("commercial", { production: "view", projects: "view" }))).toBe(false);
  expect(runsTheSystem(as("admin", {}))).toBe(true);
  expect(runsTheSystem(as("superadmin", {}))).toBe(true);
});
