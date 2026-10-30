import { expect, it } from "vitest";
import { ACCESS_LEVEL_LABELS, accessLevelLabel, type AccessMode, type Principal } from "./api";
import {
  BLOCKS, MOVED_TABS, NAV, canAct, canSee, hasFullAccess, isViewOnly, procurementMenuTab, runsTheSystem, sectionOfTab,
  visibleBlocks, visibleNav,
} from "./navigation";

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

const BUYER_MENU = ["Сегодня", "Заявки", "Запросы КП", "Закупка", "Счета и поставки", "Статистика"];
const READER_MENU = BUYER_MENU.filter((label) => label !== "Сегодня");

const EVERYTHING = {
  "Производство": ["Проекты", "Ресурсное планирование", "Задания", "ОТК"],
  // «Мои заявки» and «Заявки» are no menu items (Витя's edits 2–3): the card and «Снабжение → Заявки» open them.
  "Снабжение": ["Оформить заявку", ...BUYER_MENU],
  "Склад": ["Приёмка", "Склад", "Запросы на комплектацию", "Выдача со списанием", "Приёмка с производства"],
  "Менеджмент": ["Данные отправок", "Производственный план", "Сотрудники", "Доступы", "Уведомления", "Системный журнал"],
  "Документация": ["Первичная документация"],
  "Справочники": ["База данных", "Закупка"],
};

it("keeps SUPPLYMAP's four blocks in SUPPLYMAP's order, the documents next, reference books last", () => {
  expect(BLOCKS.map((block) => block.title)).toEqual(["Производство", "Снабжение", "Склад", "Менеджмент", "Документация", "Справочники"]);
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
    "Снабжение": ["Оформить заявку"],
    "Склад": ["Приёмка", "Склад"],
    "Менеджмент": ["Уведомления"],
    "Справочники": ["База данных"],
  });
});

it("gives the foreman the shop, the stock and the storekeeper's shipping, of the reference books only purchasing's", () => {
  expect(layout(as("foreman", FOREMAN))).toEqual({
    "Производство": ["Ресурсное планирование", "Задания", "ОТК"],
    "Снабжение": ["Оформить заявку", ...READER_MENU],
    "Склад": ["Приёмка", "Склад", "Запросы на комплектацию", "Выдача со списанием", "Приёмка с производства"],
    "Менеджмент": ["Производственный план", "Уведомления"],
    // «Снабжение» on view reads its moved tabs too.
    "Документация": ["Первичная документация"],
    "Справочники": ["Закупка"],
  });
});

it("gives the technical lead projects, planning, the whole supply block and the reference book", () => {
  expect(layout(as("tech_lead", TECH_LEAD))).toEqual({
    "Производство": ["Проекты", "Ресурсное планирование", "ОТК"],
    "Снабжение": ["Оформить заявку", ...BUYER_MENU],
    "Склад": ["Приёмка", "Склад", "Запросы на комплектацию", "Выдача со списанием", "Приёмка с производства"],
    "Менеджмент": ["Производственный план", "Уведомления"],
    "Документация": ["Первичная документация"],
    "Справочники": ["База данных", "Закупка"],
  });
});

it("gives the commercial lead projects, the supply sheets and shipping for reading", () => {
  expect(layout(as("commercial", COMMERCIAL))).toEqual({
    "Производство": ["Проекты", "Ресурсное планирование"],
    "Снабжение": ["Оформить заявку", ...READER_MENU],
    "Склад": ["Склад", "Приёмка с производства"],
    "Менеджмент": ["Производственный план", "Уведомления"],
    "Документация": ["Первичная документация"],
    "Справочники": ["Закупка"],
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

it("opens «Приёмка с производства» by either shipping key; «Отгрузка» is no menu item since 29.10", () => {
  const intake = (principal: Principal) => section(principal, "Склад").includes("Приёмка с производства");
  expect(intake(as("commercial", { shipping: "view" }))).toBe(true);
  expect(intake(as("foreman", { shipping_goods: "view" }))).toBe(true);
  expect(intake(as("foreman", { shipping_goods: "edit" }))).toBe(true);
  expect(intake(as("foreman", { production: "edit", receiving: "edit" }))).toBe(false);
  expect(section(as("admin", {}), "Склад")).not.toContain("Отгрузка");
  expect(sectionOfTab("shipping", null)).toBe("intake");
  expect(sectionOfTab("shipping", "journal")).toBe("intake");
});

it("hides «Оформить заявку» from a view-only grant on newrequest", () => {
  // Without «Снабжение» the «Заявки» register stays in the menu: it is the person's only way in.
  expect(section(as("operator", { newrequest: "view", myrequests: "edit", requests: "view" }), "Снабжение"))
    .toEqual(["Заявки"]);
});

it("NAV-SECTIONS: closing «Заявки» keeps the form and own rows", () => {
  expect(section(as("operator", OWN), "Снабжение")).toEqual(["Оформить заявку"]);
  expect(visibleNav(as("operator", OWN)).map(({ id }) => id)).toContain("myrequests"); // open from the personal card
  // «Заявки» no longer opens the other two.
  expect(section(as("operator", { requests: "edit" }), "Снабжение")).toEqual(["Заявки"]);
});

it("Витя's edits 2–3: «Мои заявки» and «Заявки» leave the menu, both still open by their keys", () => {
  const buyer = as("tech_lead", { ...OWN, requests: "edit", procurement: "edit" });
  expect(section(buyer, "Снабжение")).toEqual(["Оформить заявку", ...BUYER_MENU]);
  expect(visibleNav(buyer).map(({ id }) => id)).toEqual(expect.arrayContaining(["myrequests", "requests"]));
  const register = NAV.find(({ id }) => id === "requests")!;
  expect(register.within).toBe("procurement"); // the menu highlights «Заявки» of «Снабжение» on the register
});

it("sends an old link of a moved «Лист закупки» tab to its new section through one table", () => {
  for (const tab of ["directory", "items", "catalog", "settings"]) expect(sectionOfTab("procurement", tab)).toBe("purchasingrefs");
  for (const tab of ["upd", "invoices", "chain", "docsearch"]) expect(sectionOfTab("procurement", tab)).toBe("primarydocs");
  for (const tab of ["today", "requests", "rfq", "orders", "queues", "sheet", "suppliers", "kpi", null, undefined]) {
    expect(sectionOfTab("procurement", tab)).toBe("procurement");
  }
  expect(sectionOfTab("stock", "upd")).toBe("stock"); // only «Снабжение»'s tabs moved
  expect(Object.values(MOVED_TABS.procurement).every((target) => NAV.some(({ id }) => id === target))).toBe(true);
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
    expect(section(as(level, { procurement: "edit" }), "Снабжение")).toEqual(BUYER_MENU);
    expect(section(as(level, { procurement: "view" }), "Документация")).toEqual(["Первичная документация"]);
    expect(section(as(level, { procurement: "view" }), "Справочники")).toEqual(["Закупка"]);
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
      ["Данные отправок", "Производственный план", "Сотрудники", "Доступы", "Уведомления", "Системный журнал"],
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
  // «Приёмка с производства» opens by either shipping key: reading only when neither is on edit.
  expect(viewOnly(as("foreman", { shipping_goods: "view" }), "intake")).toBe(true);
  expect(viewOnly(as("foreman", { shipping: "view", shipping_goods: "edit" }), "intake")).toBe(false);
  expect(viewOnly(as("commercial", { shipping: "edit" }), "intake")).toBe(false);
  // A card without a key of its own has nothing to be view-only about.
  expect(viewOnly(as("operator", {}), "notifications")).toBe(false);
  expect(viewOnly(as("admin", { warehouse: "view" }), "stock")).toBe(false);
});

it("names the seven access levels in Russian (and the admin-mode session) and shows an unknown key as it is", () => {
  expect(Object.keys(ACCESS_LEVEL_LABELS)).toEqual([
    "superadmin", "commercial", "tech_lead", "pm", "storekeeper", "foreman", "operator", "admin",
  ]);
  expect(accessLevelLabel("superadmin")).toBe("Суперадминистратор");
  expect(accessLevelLabel("pm")).toBe("Менеджер по проектам");
  expect(accessLevelLabel("storekeeper")).toBe("Кладовщик");
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

it("«Данные отправок» in «Менеджмент» opens to editors of «Отгрузка» (manager, admin), not to the storekeeper's «Отгрузка — склад»", () => {
  expect(section(as("pm", { shipping: "edit" }), "Менеджмент")).toContain("Данные отправок");
  expect(section(as("pm", { shipping: "view" }), "Менеджмент")).not.toContain("Данные отправок");
  expect(section(as("storekeeper", { shipping_goods: "edit" }), "Менеджмент")).not.toContain("Данные отправок");
  expect(section(as("admin", {}), "Менеджмент")).toContain("Данные отправок");
});

it("owner 28.10: the tabs of «Снабжение» are menu items in the order of doing, each opening its own ?tab=", () => {
  const items = visibleBlocks(as("tech_lead", TECH_LEAD)).find((block) => block.title === "Снабжение")!.items;
  expect(items.map((item) => [item.id, item.tab])).toEqual([
    ["newrequest", undefined], ["procurement", "today"], ["procurement", "requests"], ["procurement", "rfq"],
    ["procurement", "buy"], ["procurement", "orders"], ["procurement", "stats"],
  ]);
});

it("maps an address tab to the menu item that stays lit", () => {
  expect(procurementMenuTab("rfq", false)).toBe("rfq");
  expect(procurementMenuTab("kpi", false)).toBe("stats");
  expect(procurementMenuTab("sheet", true)).toBe("stats");
  expect(procurementMenuTab(null, false)).toBe("today");
  expect(procurementMenuTab("today", true)).toBe("stats"); // a reader has no «Сегодня»
  expect(procurementMenuTab("nonsense", true)).toBe("stats");
});
