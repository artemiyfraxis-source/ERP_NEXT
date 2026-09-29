import type { AccessMode, Principal } from "./api";

/* NAV-1: the portal is four blocks you drill into, as in SUPPLYMAP (PortalApp.html HOME_BLOCKS_, labels
   from Portal.gs PORTAL_SECTIONS_). The home screen shows the blocks as tiles, a block shows its section
   cards, and the sidebar groups the same sections in the same order — one list feeds all three. */

export type Section =
  | "cabinet"
  | "projects" | "calculation" | "techcards" | "tctemplates" | "references" | "production" | "myops"
  | "newrequest" | "myrequests" | "requests" | "procurement"
  | "receiving" | "stock" | "issues" | "handout" | "shipping"
  | "prodplan" | "staff" | "access" | "notifications" | "operations";

export type BlockId = "prod" | "supply" | "stock" | "mgmt";

export interface NavItem {
  id: Section;
  label: string;
  /** What the section is for — the line under the label on a section card. */
  sub?: string;
  permission?: string;
  minimum?: AccessMode;
  roles?: string[];
  block?: BlockId;
}

export const BLOCKS: Array<{ id: BlockId; icon: string; title: string; sub: string }> = [
  { id: "prod", icon: "🏭", title: "Производство", sub: "проекты · шаблоны техкарт · справочник · планирование · задания" },
  { id: "supply", icon: "🛒", title: "Снабжение", sub: "оформить · заявки · закупка" },
  // Приёмка sits in Склад: the storekeeper does incoming inspection, not the buyer (SUPPLYMAP, owner 26.07.2026).
  { id: "stock", icon: "📦", title: "Склад", sub: "приёмка · остатки · выдача · отгрузка" },
  { id: "mgmt", icon: "📊", title: "Менеджмент", sub: "производственный план · сотрудники · доступы · журнал" },
];

const TECHCARD_ROLES = ["engineer", "master", "prodhead", "management", "admin"];
// ID-6: view grants open these sections read-only; the role sets mirror backend *_view_principal.
const FULFILLMENT_ROLES = ["engineer", "receiver", "prodhead", "master", "admin"];

// Array order = card order inside a block = sidebar order.
export const NAV: NavItem[] = [
  { id: "cabinet", label: "Личный кабинет" },
  // Производство
  // «Проекты» (SUPPLYMAP erptechmap): карточки наборов — состав, спецификация, снабжение, генератор, готовые ТК.
  { id: "projects", label: "Проекты", sub: "проекты · состав · спецификация · генератор", permission: "projects", block: "prod" },
  { id: "calculation", label: "Расчёт и КП", sub: "расчёт стоимости · коммерческое предложение", permission: "calculation", block: "prod" },
  // Product cards with ETKs: in SUPPLYMAP they live inside «Проекты» (kit · generator · ready TCs). Here they are a
  // native screen apart from the legacy registry iframe, so they sit next to «Проекты» instead of merging.
  { id: "techcards", label: "Техкарты проектов", sub: "карточки изделий · сборка и выдача ЭТК", permission: "techcards", roles: TECHCARD_ROLES, block: "prod" },
  { id: "tctemplates", label: "Шаблоны техкарт", sub: "карточки операций · шаги и оснастка", permission: "techcards", roles: TECHCARD_ROLES, block: "prod" },
  { id: "references", label: "Справочник", sub: "операции · оборудование · терминалы · провода · материалы", permission: "references", block: "prod" },
  { id: "production", label: "Ресурсное планирование", sub: "запуск · назначение операторов · график", permission: "production", roles: ["master", "prodhead", "management", "qc", "admin"], block: "prod" },
  { id: "myops", label: "Задания", sub: "мои операции", permission: "production", roles: ["staff", "master", "admin"], block: "prod" },
  // Снабжение. «Оформить заявку» and «Мои заявки» are sections of their own, not actions inside «Заявки»
  // (SUPPLYMAP Portal.gs PORTAL_SECTIONS_, owner 27.08.2026): a rank-and-file user needs the form and own rows.
  // NAV-SECTIONS: each has its own access key, so «Заявки» can be closed without taking them away.
  { id: "newrequest", label: "Оформить заявку", sub: "вручную · из Excel · из расчёта", permission: "newrequest", minimum: "edit", block: "supply" },
  { id: "myrequests", label: "Мои заявки", sub: "мои позиции и на каком они этапе", permission: "myrequests", block: "supply" },
  { id: "requests", label: "Заявки", sub: "проверить и передать в закупку", permission: "requests", block: "supply" },
  { id: "procurement", label: "Лист закупки", sub: "работа и взять в работу", permission: "procurement", roles: ["buyer", "receiver", "prodhead", "management", "admin"], block: "supply" },
  // Склад
  { id: "receiving", label: "Приёмка", sub: "входной контроль и оприходование", permission: "receiving", roles: ["receiver", "prodhead", "management", "master", "staff", "admin"], block: "stock" },
  { id: "stock", label: "Склад", sub: "остатки · внести · переместить", permission: "warehouse", roles: ["receiver", "buyer", "prodhead", "management", "master", "staff", "admin"], block: "stock" },
  // Picking only reserves, handout writes off: two sections over one request (SUPPLYMAP, owner 02.09.2026), each with
  // its own access key (NAV-SECTIONS) so picking and writing off can go to different people.
  { id: "issues", label: "Запросы на комплектацию", sub: "заявки под КБ · набрать комплект", permission: "fulfillment", roles: FULFILLMENT_ROLES, block: "stock" },
  { id: "handout", label: "Выдача со списанием", sub: "передать в цех · списать со склада", permission: "handout", roles: FULFILLMENT_ROLES, block: "stock" },
  { id: "shipping", label: "Отгрузка", sub: "готовая продукция · отправки", permission: "shipping", roles: ["pm", "receiver", "master", "prodhead", "management", "admin"], block: "stock" },
  // Менеджмент
  { id: "prodplan", label: "Производственный план", sub: "все проекты · блокеры · комплектующие · сроки", permission: "production", roles: ["master", "prodhead", "management", "admin"], block: "mgmt" },
  { id: "staff", label: "Сотрудники", sub: "справочник, роли, отделы", permission: "identity", minimum: "edit", roles: ["admin"], block: "mgmt" },
  // NAV-ACCESS: same gate as «Сотрудники» — the backend `identity_edit_principal` (identity edit + admin role).
  { id: "access", label: "Доступы", sub: "роли × разделы · исключения по сотрудникам", permission: "identity", minimum: "edit", roles: ["admin"], block: "mgmt" },
  { id: "notifications", label: "Уведомления", sub: "мои события", block: "mgmt" },
  { id: "operations", label: "Системный журнал", sub: "аудит и очередь событий", permission: "operations", roles: ["management", "admin"], block: "mgmt" },
];

export function hasAccess(
  permissions: Principal["permissions"],
  permission?: string,
  minimum: AccessMode = "view",
) {
  if (!permission) return true;
  const actual = permissions[permission];
  return actual === "edit" || (minimum === "view" && actual === "view");
}

export function hasNavAccess(principal: Principal, item: NavItem) {
  return (
    hasAccess(principal.permissions, item.permission, item.minimum) &&
    (!item.roles || item.roles.some((role) => principal.roles.includes(role)))
  );
}

export function visibleNav(principal: Principal) {
  return NAV.filter((item) => hasNavAccess(principal, item));
}

/** Blocks with at least one section this principal may open, sections in block order. */
export function visibleBlocks(principal: Principal) {
  const items = visibleNav(principal);
  return BLOCKS
    .map((block) => ({ ...block, items: items.filter((item) => item.block === block.id) }))
    .filter((block) => block.items.length > 0);
}

export function isSection(value: string | null): value is Section {
  return NAV.some(({ id }) => id === value);
}

export function isBlock(value: string | null): value is BlockId {
  return BLOCKS.some(({ id }) => id === value);
}
