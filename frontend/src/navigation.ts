import type { AccessMode, Principal } from "./api";

/* NAV-1: the portal is four blocks you drill into, as in SUPPLYMAP (PortalApp.html HOME_BLOCKS_, labels
   from Portal.gs PORTAL_SECTIONS_). The home screen shows the blocks as tiles, a block shows its section
   cards, and the sidebar groups the same sections in the same order — one list feeds all three. */

export type Section =
  | "cabinet" | "card"
  | "projects" | "techcards" | "tctemplates" | "references" | "production" | "myops" | "quality"
  | "newrequest" | "myrequests" | "requests" | "procurement"
  | "receiving" | "stock" | "equipment" | "issues" | "handout" | "shipping"
  | "prodplan" | "staff" | "access" | "notifications" | "operations";

export type BlockId = "prod" | "supply" | "stock" | "mgmt" | "refs";

export interface NavItem {
  id: Section;
  label: string;
  /** What the section is for — the line under the label on a section card. */
  sub?: string;
  permission?: string;
  /** Opens when any of these keys is granted (a section made of tabs with their own keys). */
  anyPermission?: string[];
  minimum?: AccessMode;
  /** superadmin / admin only, outside the matrix (backend require_full_access: «Доступы», «Сотрудники»). */
  fullAccessOnly?: boolean;
  block?: BlockId;
  /** A screen opened from another section, not a card or menu item of its own: SUPPLYMAP opens the ЭТК inside
   *  «Проекты» (hub 'tc'), so the menu highlights that section instead. */
  within?: Section;
  /** Stage options (Витя's hybrid stage): the section exists only while at least one of them is «Новое». */
  options?: string[];
}

export const BLOCKS: Array<{ id: BlockId; title: string; sub: string }> = [
  { id: "prod", title: "Производство", sub: "проекты · планирование · задания" },
  { id: "supply", title: "Снабжение", sub: "оформить · заявки · закупка" },
  // Приёмка sits in Склад: the storekeeper does incoming inspection, not the buyer (SUPPLYMAP, owner 26.07.2026).
  { id: "stock", title: "Склад", sub: "приёмка · остатки · выдача · отгрузка" },
  { id: "mgmt", title: "Менеджмент", sub: "производственный план · сотрудники · доступы · журнал" },
  // Reference books are not a production step (owner 29.09.2026): their own block, last in the menu.
  { id: "refs", title: "Справочники", sub: "база данных · шаблоны техкарт" },
];

// Access levels (owner 04.10.2026): a card opens by its section key and nothing else — no role lists. superadmin / admin
// pass every card (backend `section_allowed`); the keys and minimums mirror backend level_migration.GATES.
// Array order = card order inside a block = sidebar order.
export const NAV: NavItem[] = [
  { id: "cabinet", label: "Личный кабинет" },
  // «Личная карточка» (owner concept 04.10): own card from the sidebar profile, someone else's from «Сотрудники»
  // (?section=card&user=<id>). No access key — own card is open to everyone, the backend gates other people's. Not a
  // menu item or a tile (`within`), and no menu highlight (the home screen is its parent).
  { id: "card", label: "Личная карточка", block: "mgmt", within: "cabinet" },
  // Производство
  // «Проекты» (SUPPLYMAP erptechmap): карточки наборов — состав, спецификация, снабжение, генератор, готовые ТК.
  { id: "projects", label: "Проекты", sub: "проекты · состав · спецификация · генератор", permission: "projects", block: "prod" },
  // Product cards with ETKs: in SUPPLYMAP they live inside «Проекты» (kit · generator · ready TCs). Here they are a
  // native screen of their own, so they sit next to «Проекты» instead of merging.
  // ID-3 (SUPPLYMAP PORTAL_SECTIONS_): each card has its own key and no role list — the matrix alone decides.
  // SUPPLYMAP has no «Техкарты проектов» card: the ЭТК opens from the project card (Portal.gs: «Проекты» =
  // состав · спецификация · генератор · готовые ТК). The screen stays, reached from «Проекты» and the plan.
  { id: "techcards", label: "Техкарты проектов", sub: "карточки изделий · сборка и выдача ЭТК", permission: "techcards", block: "prod", within: "projects" },
  // «База данных» (owner, 29.09.2026): «Справочник» and «Шаблоны техкарт» are tabs of one section. Each tab keeps
  // its access key, so the card opens on either; ?section=tctemplates still lands on the templates tab.
  { id: "references", label: "База данных", sub: "операции · оборудование · терминалы · коаксиал · провода · СПР.КАБ · соответствия", anyPermission: ["erpref", "erptctpl"], block: "refs" },
  { id: "tctemplates", label: "Шаблоны техкарт", sub: "карточки операций · шаги и оснастка", permission: "erptctpl", block: "refs", within: "references" },
  { id: "production", label: "Ресурсное планирование", sub: "запуск · назначение операторов · график", permission: "production", block: "prod" },
  // «Задания» (`myops`) и «ОТК» (`quality`) — разделы матрицы со своими ключами (owner 04.10.2026), не часть `production`.
  // «ОТК»: очередь на проверку, брак цеха и ОТК, средства измерения; приёмку и реестр ведёт правка `quality`, решение по
  // браку — правка `production` (экран сам прячет то, что не открыто).
  { id: "myops", label: "Задания", sub: "мои операции", permission: "myops", block: "prod" },
  { id: "quality", label: "ОТК", sub: "очередь на проверку · брак · средства измерения", permission: "quality", block: "prod" },
  // Снабжение. «Оформить заявку» and «Мои заявки» are sections of their own, not actions inside «Заявки»
  // (SUPPLYMAP Portal.gs PORTAL_SECTIONS_, owner 27.08.2026): a rank-and-file user needs the form and own rows.
  // NAV-SECTIONS: each has its own access key, so «Заявки» can be closed without taking them away.
  { id: "newrequest", label: "Оформить заявку", sub: "вручную · из Excel · из расчёта", permission: "newrequest", minimum: "edit", block: "supply" },
  { id: "myrequests", label: "Мои заявки", sub: "мои позиции и на каком они этапе", permission: "myrequests", block: "supply" },
  { id: "requests", label: "Заявки", sub: "проверить и передать в закупку", permission: "requests", block: "supply" },
  { id: "procurement", label: "Лист закупки", sub: "работа и взять в работу", permission: "procurement", block: "supply" },
  // Склад
  { id: "receiving", label: "Приёмка", sub: "входной контроль и оприходование", permission: "receiving", block: "stock" },
  { id: "stock", label: "Склад", sub: "остатки · внести · переместить", permission: "warehouse", block: "stock" },
  // «Оборудование» (stage options warehouse.labels, warehouse.tsd): the GRN label printer and access from a ТСД.
  { id: "equipment", label: "Оборудование", sub: "принтер этикеток · ТСД", permission: "warehouse", block: "stock", options: ["warehouse.labels", "warehouse.tsd"] },
  // Picking only reserves, handout writes off: two sections over one request (SUPPLYMAP, owner 02.09.2026), each with
  // its own access key (NAV-SECTIONS) so picking and writing off can go to different people.
  { id: "issues", label: "Запросы на комплектацию", sub: "заявки под КБ · набрать комплект", permission: "fulfillment", block: "stock" },
  { id: "handout", label: "Выдача со списанием", sub: "передать в цех · списать со склада", permission: "handout", block: "stock" },
  // «Отгрузка» (менеджер) и «Отгрузка — склад» (кладовщик) — два ключа матрицы, одна карточка: список читает любой из них.
  { id: "shipping", label: "Отгрузка", sub: "готовая продукция · отправки", anyPermission: ["shipping", "shipping_goods"], block: "stock" },
  // Менеджмент
  { id: "prodplan", label: "Производственный план", sub: "все проекты · блокеры · комплектующие · сроки", permission: "production", block: "mgmt" },
  // «Сотрудники» и «Доступы»: только superadmin / admin, вне матрицы (backend staffdir_principal, access_admin_principal).
  { id: "staff", label: "Сотрудники", sub: "справочник, уровни, отделы", fullAccessOnly: true, block: "mgmt" },
  { id: "access", label: "Доступы", sub: "уровни × разделы · исключения по сотрудникам", fullAccessOnly: true, block: "mgmt" },
  { id: "notifications", label: "Уведомления", sub: "мои события", block: "mgmt" },
  { id: "operations", label: "Системный журнал", sub: "аудит и очередь событий", permission: "operations", block: "mgmt" },
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

/** Levels that edit every section and alone see «Доступы», «Сотрудники» and the administrator's tools (backend
 *  identity FULL_ACCESS_LEVELS). */
export const FULL_ACCESS_LEVELS: ReadonlySet<string> = new Set(["superadmin", "admin"]);

/** The effective access level key: `access_level`, or the one element of `roles` (it holds the level key too). */
export function accessLevelOf(principal: Principal): string {
  return principal.access_level ?? principal.roles[0] ?? "";
}

/** superadmin / admin: every section on edit and the administrator's tools, whatever the matrix says. */
export function hasFullAccess(principal: Principal): boolean {
  return FULL_ACCESS_LEVELS.has(accessLevelOf(principal));
}

/** The section is open at `minimum` (default: for reading): a full-access level, or the matrix says so. */
export function canSee(principal: Principal, section: string, minimum: AccessMode = "view"): boolean {
  return hasFullAccess(principal) || hasAccess(principal.permissions, section, minimum);
}

/** «Режим просмотра» (ID-6): edit controls only when the backend would pass the write too — the section is on edit
 *  (`minimum` can ask for less), or the level is a full-access one. */
export function canAct(principal: Principal, section: string, minimum: AccessMode = "edit"): boolean {
  return canSee(principal, section, minimum);
}

// The sections of one's own work: filing and own rows, own operations. Edit rights here do not make a person someone
// who runs the shop or the system.
const OWN_WORK_SECTIONS: readonly string[] = ["newrequest", "myrequests", "myops"];

/** Edits something beyond one's own work, or has full access: who is told that an update failed. A rank-and-file
 *  operator (own operations and own requests only) is not. */
export function runsTheSystem(principal: Principal): boolean {
  return hasFullAccess(principal) || Object.entries(principal.permissions)
    .some(([section, mode]) => mode === "edit" && !OWN_WORK_SECTIONS.includes(section));
}

export function hasNavAccess(principal: Principal, item: NavItem) {
  if (item.fullAccessOnly) return hasFullAccess(principal);
  if (hasFullAccess(principal)) return true;
  return (
    hasAccess(principal.permissions, item.permission, item.minimum) &&
    (!item.anyPermission || item.anyPermission.some((key) => hasAccess(principal.permissions, key, item.minimum)))
  );
}

/** The card opens for reading only: every key that opens it is granted as «view» (never for a full-access level). */
export function isViewOnly(principal: Principal, item: NavItem): boolean {
  if (hasFullAccess(principal)) return false;
  const keys = item.permission ? [item.permission] : item.anyPermission ?? [];
  const modes = keys.map((key) => principal.permissions[key]).filter(Boolean);
  return modes.length > 0 && !modes.includes("edit");
}

const NO_OPTIONS: ReadonlySet<string> = new Set();

/** A section behind stage options is there only while one of them is on (`enabled` — the keys switched to «Новое»). */
export function optionsAllow(item: NavItem, enabled: ReadonlySet<string> = NO_OPTIONS): boolean {
  return !item.options || item.options.some((key) => enabled.has(key));
}

export function visibleNav(principal: Principal, enabled: ReadonlySet<string> = NO_OPTIONS) {
  return NAV.filter((item) => hasNavAccess(principal, item) && optionsAllow(item, enabled));
}

/** Blocks with at least one section this principal may open, sections in block order. */
export function visibleBlocks(principal: Principal, enabled: ReadonlySet<string> = NO_OPTIONS) {
  const items = visibleNav(principal, enabled);
  return BLOCKS
    .map((block) => ({ ...block, items: items.filter((item) => item.block === block.id && !item.within) }))
    .filter((block) => block.items.length > 0);
}

/** A query parameter of the address bar ("" when absent): the pages read their starting filter from it (`?q=`). */
export function urlParam(name: string): string {
  return new URLSearchParams(window.location.search).get(name) ?? "";
}

export function isSection(value: string | null): value is Section {
  return NAV.some(({ id }) => id === value);
}

export function isBlock(value: string | null): value is BlockId {
  return BLOCKS.some(({ id }) => id === value);
}

/** Open a section with exactly these parameters (the global search): `?section=…&tab=…&q=…`, no leftovers. */
export function openPlace(section: string, params: Record<string, string> = {}) {
  const url = new URL(window.location.href);
  url.search = "";
  url.searchParams.set("section", section);
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);
  window.history.pushState({}, "", url);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

/** Go to another section the way the menu does: `?section=…` in the address bar plus a popstate for the shell. */
export function openSection(section: string, extra: Record<string, string> = {}) {
  const url = new URL(window.location.href);
  url.searchParams.set("section", section);
  url.searchParams.delete("block");
  for (const [key, value] of Object.entries(extra)) url.searchParams.set(key, value);
  window.history.pushState({}, "", url);
  window.dispatchEvent(new PopStateEvent("popstate"));
}
