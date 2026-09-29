import { expect, it } from "vitest";
import type { AccessMode, Principal } from "./api";
import { BLOCKS, visibleBlocks } from "./navigation";

const user = { id: "1", email: "u@example.test", name: "Сотрудник", department: "КБ" };

function as(role: string, permissions: Record<string, AccessMode>): Principal {
  return { user, roles: [role], permissions };
}

function layout(principal: Principal) {
  return Object.fromEntries(visibleBlocks(principal).map((block) => [block.title, block.items.map((item) => item.label)]));
}

const ALL_EDIT = Object.fromEntries(
  ["identity", "projects", "calculation", "techcards", "newrequest", "myrequests", "requests", "procurement",
    "receiving", "warehouse", "fulfillment", "handout", "production", "shipping", "references", "operations"].map((key) => [key, "edit" as AccessMode]),
);

it("keeps SUPPLYMAP's four blocks in SUPPLYMAP's order", () => {
  expect(BLOCKS.map((block) => block.title)).toEqual(["Производство", "Снабжение", "Склад", "Менеджмент"]);
});

it("shows the administrator every block with SUPPLYMAP's cards in order, ERP-only sections in place", () => {
  expect(layout(as("admin", ALL_EDIT))).toEqual({
    "Производство": ["Проекты", "Расчёт и КП", "Техкарты проектов", "Шаблоны техкарт", "Справочник", "Ресурсное планирование", "Задания"],
    "Снабжение": ["Оформить заявку", "Мои заявки", "Заявки", "Лист закупки"],
    "Склад": ["Приёмка", "Склад", "Запросы на комплектацию", "Выдача со списанием", "Отгрузка"],
    "Менеджмент": ["Производственный план", "Сотрудники", "Доступы", "Уведомления", "Системный журнал"],
  });
});

// Permissions below mirror backend identity ROLE_DEFAULTS for each role.
const OWN = { newrequest: "edit", myrequests: "edit" } as const;
it("gives a rank-and-file employee the request form, own rows and read-only stock", () => {
  expect(layout(as("staff", {
    ...OWN, requests: "edit", receiving: "view", warehouse: "view", production: "view", references: "view",
  }))).toEqual({
    "Производство": ["Справочник", "Задания"],
    "Снабжение": ["Оформить заявку", "Мои заявки", "Заявки"],
    "Склад": ["Приёмка", "Склад"],
    "Менеджмент": ["Уведомления"],
  });
});

it("gives the buyer the whole supply block and the stock view", () => {
  expect(layout(as("buyer", {
    ...OWN, requests: "edit", procurement: "edit", warehouse: "view", references: "view",
  }))).toEqual({
    "Производство": ["Справочник"],
    "Снабжение": ["Оформить заявку", "Мои заявки", "Заявки", "Лист закупки"],
    "Склад": ["Склад"],
    "Менеджмент": ["Уведомления"],
  });
});

it("gives the storekeeper picking and handout as two sections", () => {
  expect(layout(as("receiver", {
    ...OWN, receiving: "edit", warehouse: "edit", fulfillment: "edit", handout: "edit", shipping: "edit",
    procurement: "view", references: "view",
  }))).toEqual({
    "Производство": ["Справочник"],
    "Снабжение": ["Оформить заявку", "Мои заявки", "Лист закупки"],
    "Склад": ["Приёмка", "Склад", "Запросы на комплектацию", "Выдача со списанием", "Отгрузка"],
    "Менеджмент": ["Уведомления"],
  });
});

it("gives the shop master planning, tasks and the production plan", () => {
  expect(layout(as("master", {
    ...OWN, receiving: "view", warehouse: "view", fulfillment: "view", handout: "view", production: "edit", shipping: "view", references: "view",
  }))).toEqual({
    "Производство": ["Справочник", "Ресурсное планирование", "Задания"],
    "Снабжение": ["Оформить заявку", "Мои заявки"],
    "Склад": ["Приёмка", "Склад", "Запросы на комплектацию", "Выдача со списанием", "Отгрузка"],
    "Менеджмент": ["Производственный план", "Уведомления"],
  });
});

it("hides «Оформить заявку» from a view-only grant on newrequest", () => {
  expect(layout(as("staff", { newrequest: "view", myrequests: "edit", requests: "view" }))["Снабжение"])
    .toEqual(["Мои заявки", "Заявки"]);
});

it("NAV-SECTIONS: closing «Заявки» keeps the form and own rows", () => {
  expect(layout(as("staff", OWN))["Снабжение"]).toEqual(["Оформить заявку", "Мои заявки"]);
  // «Заявки» no longer opens the other two.
  expect(layout(as("staff", { requests: "edit" }))["Снабжение"]).toEqual(["Заявки"]);
});

it("NAV-SECTIONS: picking and handout open by their own keys", () => {
  expect(layout(as("receiver", { fulfillment: "edit" }))["Склад"]).toEqual(["Запросы на комплектацию"]);
  expect(layout(as("receiver", { handout: "edit" }))["Склад"]).toEqual(["Выдача со списанием"]);
});

it("hides a block with no section the principal may open", () => {
  const blocks = visibleBlocks(as("staff", {}));
  expect(blocks.map((block) => block.title)).toEqual(["Менеджмент"]);
});

it("does not open a section on a grant alone when the role is outside its set", () => {
  // Procurement edit for a non-buyer role and techcards for a role outside the ETK set stay hidden (ID-6).
  const blocks = layout(as("staff", { procurement: "edit", techcards: "edit" }));
  expect(blocks["Снабжение"]).toBeUndefined();
  expect(blocks["Производство"]).toBeUndefined();
});

it("opens «Доступы» only to an administrator who can edit identity", () => {
  expect(layout(as("admin", { ...ALL_EDIT, identity: "view" }))["Менеджмент"]).not.toContain("Доступы");
  expect(layout(as("buyer", { identity: "edit" }))["Менеджмент"]).toEqual(["Уведомления"]);
  expect(layout(as("admin", { identity: "edit" }))["Менеджмент"]).toEqual(["Сотрудники", "Доступы", "Уведомления"]);
});
