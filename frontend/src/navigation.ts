import type { AccessMode, Principal } from "./api";

/* NAV-1: the portal is four blocks you drill into, as in SUPPLYMAP (PortalApp.html HOME_BLOCKS_, labels
   from Portal.gs PORTAL_SECTIONS_). The home screen shows the blocks as tiles, a block shows its section
   cards, and the sidebar groups the same sections in the same order — one list feeds all three. */

export type Section =
  | "cabinet" | "card"
  | "projects" | "techcards" | "tctemplates" | "references" | "production" | "myops" | "quality"
  | "newrequest" | "myrequests" | "requests" | "procurement" | "purchasingrefs" | "primarydocs"
  | "receiving" | "stock" | "issues" | "handout" | "intake" | "shipdata"
  | "prodplan" | "staff" | "access" | "notifications" | "operations";

export type BlockId = "prod" | "supply" | "stock" | "mgmt" | "docs" | "refs";

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
  /** With `within`: still a menu item for a person to whom the `within` section is closed (their only way in). */
  menuWithoutParent?: boolean;
  /** A menu item that opens one tab of its section (`?section=…&tab=<tab>`): the sidebar shows the tabs of «Снабжение»
   *  as items of their own. */
  tab?: string;
}

/** The buyer's work in the order it is done (owner 28.10): each is a menu item of the «Снабжение» block and opens
 *  `?section=procurement&tab=<key>`; «stats» opens «Статистика» on its first inner tab. */
export const PROCUREMENT_TABS: ReadonlyArray<readonly [string, string, string]> = [
  ["today", "Сегодня", "что делать сегодня"],
  ["requests", "Заявки", "проверить и передать в закупку"],
  ["rfq", "Запросы КП", "запросы поставщикам"],
  ["buy", "Закупка", "заказы поставщикам"],
  ["orders", "Счета и поставки", "счета · оплата · приёмка"],
  ["stats", "Статистика", "очереди · весь лист · по поставщикам · показатели"],
];

/** The menu tab a `?tab=` value belongs to: «Статистика» holds its inner tabs; unknown or none → the default. */
export function procurementMenuTab(tab: string | null | undefined, readOnly: boolean): string {
  if (tab && PROCUREMENT_TABS.some(([key]) => key === tab)) return readOnly && tab === "today" ? "stats" : tab;
  if (tab && ["queues", "sheet", "suppliers", "kpi"].includes(tab)) return "stats";
  return readOnly ? "stats" : "today";
}

/** Unique React key of a menu item (several items share the section `procurement`). */
export const navKey = (item: NavItem) => (item.tab ? `${item.id}:${item.tab}` : item.id);

export const BLOCKS: Array<{ id: BlockId; title: string; sub: string }> = [
  { id: "prod", title: "Производство", sub: "проекты · планирование · задания" },
  { id: "supply", title: "Снабжение", sub: "оформить заявку · сегодня · заявки · запросы КП · закупка · счета и поставки · статистика" },
  // Приёмка sits in Склад: the storekeeper does incoming inspection, not the buyer (SUPPLYMAP, owner 26.07.2026).
  { id: "stock", title: "Склад", sub: "приёмка · остатки · выдача · отгрузка" },
  { id: "mgmt", title: "Менеджмент", sub: "производственный план · сотрудники · доступы · журнал" },
  // Primary documents of purchasing (УПД, счета, их цепочка): a block of their own (Витя's edit 4, owner-approved 23.10).
  { id: "docs", title: "Документация", sub: "первичная документация" },
  // Reference books are not a production step (owner 29.09.2026): their own block, last in the menu.
  { id: "refs", title: "Справочники", sub: "база данных · шаблоны техкарт · закупка" },
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
  { id: "production", label: "Ресурсное планирование", sub: "назначение операторов · автоплан · график", permission: "production", block: "prod" },
  // «Задания» (`myops`) и «ОТК» (`quality`) — разделы матрицы со своими ключами (owner 04.10.2026), не часть `production`.
  // «ОТК»: очередь на проверку, брак цеха и ОТК, средства измерения; приёмку и реестр ведёт правка `quality`, решение по
  // браку — правка `production` (экран сам прячет то, что не открыто).
  { id: "myops", label: "Задания", sub: "мои операции", permission: "myops", block: "prod" },
  { id: "quality", label: "ОТК", sub: "очередь на проверку · брак · средства измерения", permission: "quality", block: "prod" },
  // Снабжение. «Оформить заявку» is a section of its own (SUPPLYMAP Portal.gs PORTAL_SECTIONS_, owner 27.08.2026).
  // NAV-SECTIONS: «Мои заявки» and «Заявки» keep their access keys but are no menu items since Витя's edits 2–3
  // (owner-approved 23.10): «Мои заявки» open from the personal card, the «Заявки» register from the «Заявки» tab of
  // «Снабжение». A level without «Снабжение» still finds «Заявки» in the menu (`visibleBlocks`).
  { id: "newrequest", label: "Оформить заявку", sub: "вручную · из Excel · из расчёта", permission: "newrequest", minimum: "edit", block: "supply" },
  { id: "myrequests", label: "Мои заявки", sub: "мои позиции и на каком они этапе", permission: "myrequests", block: "supply", within: "card" },
  { id: "requests", label: "Заявки", sub: "проверить и передать в закупку", permission: "requests", block: "supply", within: "procurement", menuWithoutParent: true },
  // «Снабжение» — the former «Лист закупки» (Витя's edit 4): the buyer's daily work plus «Статистика». Its reference
  // books moved to «Справочники → Закупка», its documents to «Документация → Первичная документация» (same key).
  { id: "procurement", label: "Снабжение", sub: "сегодня · заявки · запросы КП · счета и поставки · статистика", permission: "procurement", block: "supply" },
  // Склад
  { id: "receiving", label: "Приёмка", sub: "входной контроль и оприходование", permission: "receiving", block: "stock" },
  { id: "stock", label: "Склад", sub: "остатки · внести · переместить · оборудование", permission: "warehouse", block: "stock" },
  // Picking only reserves, handout writes off: two sections over one request (SUPPLYMAP, owner 02.09.2026), each with
  // its own access key (NAV-SECTIONS) so picking and writing off can go to different people.
  { id: "issues", label: "Запросы на комплектацию", sub: "заявки под КБ · набрать комплект", permission: "fulfillment", block: "stock" },
  { id: "handout", label: "Выдача со списанием", sub: "передать в цех · списать со склада", permission: "handout", block: "stock" },
  // «Приёмка с производства» (владелец 14.10, 29.10): всё, что в работе, — принять, упаковать, отгрузить; экран «Отгрузка»
  // убран, `?section=shipping` ведёт сюда (SECTION_ALIASES). «Отгрузка» (менеджер) и «Отгрузка — склад» (кладовщик) — два
  // ключа матрицы, одна карточка: список читает любой из них.
  { id: "intake", label: "Приёмка с производства", sub: "принять · упаковать · отгрузить", anyPermission: ["shipping", "shipping_goods"], block: "stock" },
  // Менеджмент
  // «Данные отправок» (владелец 28.10): менеджер (Ульяна) вносит данные для отгрузки — кладовщик без них не отправит.
  // Открывает правка «Отгрузки» (менеджер, администратор): тот же ключ, что у правки отправки на сервере.
  { id: "shipdata", label: "Данные отправок", sub: "тип доставки · адрес · получатель · УПД", permission: "shipping", minimum: "edit", block: "mgmt" },
  { id: "prodplan", label: "Производственный план", sub: "все проекты · блокеры · комплектующие · сроки", permission: "production", block: "mgmt" },
  // «Сотрудники» и «Доступы»: только superadmin / admin, вне матрицы (backend staffdir_principal, access_admin_principal).
  { id: "staff", label: "Сотрудники", sub: "справочник, уровни, отделы", fullAccessOnly: true, block: "mgmt" },
  { id: "access", label: "Доступы", sub: "уровни × разделы · исключения по сотрудникам", fullAccessOnly: true, block: "mgmt" },
  { id: "notifications", label: "Уведомления", sub: "мои события", block: "mgmt" },
  { id: "operations", label: "Системный журнал", sub: "аудит и очередь событий", permission: "operations", block: "mgmt" },
  // Документация
  { id: "primarydocs", label: "Первичная документация", sub: "УПД · счета Kaiten · цепочка документов · поиск по документам", permission: "procurement", block: "docs" },
  // Справочники: after «База данных» above
  { id: "purchasingrefs", label: "Закупка", sub: "поставщики · номенклатура · каталог закупок · настройки", permission: "procurement", block: "refs" },
];

/** Tabs that left «Снабжение» (Витя's edit 4): an old `?section=procurement&tab=<tab>` link — a bookmark, «Сегодня»,
 *  the global search — opens the tab in its new section. The one table of these moves. */
export const MOVED_TABS: Readonly<Record<string, Readonly<Record<string, Section>>>> = {
  procurement: {
    directory: "purchasingrefs", items: "purchasingrefs", catalog: "purchasingrefs", settings: "purchasingrefs",
    upd: "primarydocs", invoices: "primarydocs", chain: "primarydocs", docsearch: "primarydocs",
  },
};

/** Sections that are gone, and where their old links land: «Отгрузка» merged into «Приёмка с производства» (owner 29.10). */
export const SECTION_ALIASES: Readonly<Record<string, Section>> = { shipping: "intake", equipment: "stock" };

/** An old section that became a sub-tab of its new one: `?section=equipment` opens «Склад» on «Оборудование» (owner 29.10). */
export const ALIAS_VIEW: Readonly<Record<string, string>> = { equipment: "equipment" };

/** The section a link to `section` + `tab` lands in now: the moved tab's new section, or `section` itself. */
export function sectionOfTab(section: string, tab: string | null | undefined): string {
  return (tab && MOVED_TABS[section]?.[tab]) || SECTION_ALIASES[section] || section;
}

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

export function visibleNav(principal: Principal) {
  return NAV.filter((item) => hasNavAccess(principal, item));
}

/** Blocks with at least one section this principal may open, sections in block order. */
export function visibleBlocks(principal: Principal) {
  const items = visibleNav(principal);
  // A screen opened from another section is no menu item; one marked `menuWithoutParent` is, while that section is
  // closed to this person (a level with «Заявки» but no «Снабжение» keeps its way in).
  const inMenu = (item: NavItem) => !item.within || (item.menuWithoutParent && !items.some(({ id }) => id === item.within));
  // «Снабжение» is shown as its tabs, in the order of doing; «Сегодня» is the buyer's own list, closed to a reader.
  const expand = (item: NavItem): NavItem[] => item.id !== "procurement" ? [item]
    : PROCUREMENT_TABS
      .filter(([tab]) => !(tab === "today" && isViewOnly(principal, item)))
      .map(([tab, label, sub]) => ({ ...item, tab, label, sub }));
  return BLOCKS
    .map((block) => ({ ...block, items: items.filter((item) => item.block === block.id && inMenu(item)).flatMap(expand) }))
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
  url.searchParams.set("section", sectionOfTab(section, params.tab));
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, value);
  window.history.pushState({}, "", url);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

/** A UPD's card in «Документация → Первичная документация» (`&upd=<id>`), from any screen that names the document. */
export function openUpdCard(id: string) {
  openPlace("primarydocs", { tab: "upd", upd: id });
}

/** Go to another section the way the menu does: `?section=…` in the address bar plus a popstate for the shell. */
export function openSection(section: string, extra: Record<string, string> = {}) {
  const url = new URL(window.location.href);
  url.searchParams.set("section", sectionOfTab(section, extra.tab));
  url.searchParams.delete("block");
  for (const [key, value] of Object.entries(extra)) url.searchParams.set(key, value);
  window.history.pushState({}, "", url);
  window.dispatchEvent(new PopStateEvent("popstate"));
}
