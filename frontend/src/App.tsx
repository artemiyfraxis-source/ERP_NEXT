import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import {
  AUTH_REQUIRED_EVENT,
  ApiError,
  accessLevelLabel,
  failureText,
  identityApi,
  type Principal,
} from "./api";
import { DraftProvider, DraftStore } from "./formDrafts";
import {
  accessLevelOf,
  canAct,
  canSee,
  hasAccess,
  hasFullAccess,
  isBlock,
  isViewOnly,
  isSection,
  runsTheSystem,
  sectionOfTab,
  visibleBlocks,
  visibleNav as visibleNavFor,
  type BlockId,
  type NavItem,
  type Section,
} from "./navigation";
import { LoginPage, SessionExpiredDialog } from "./LoginPage";
import { UpdateFailure } from "./UpdateBanner";
import { BlockIcon } from "./BlockIcon";
import { countLabel } from "./format";
import { FulfillmentPage } from "./FulfillmentPage";
import { ProcurementPage } from "./ProcurementPage";
import { PurchasingRefsPage } from "./PurchasingRefsPage";
import { PrimaryDocsPage } from "./PrimaryDocsPage";
import { ProductionPage } from "./ProductionPage";
import { ProductionPlanPage } from "./ProductionPlanPage";
import { QualityPage } from "./QualityPage";
import { RequestsPage } from "./RequestsPage";
import { ReceivingPage } from "./ReceivingPage";
import { WarehousePage } from "./WarehousePage";
import { EquipmentPage } from "./EquipmentPage";
import { ShippingPage } from "./ShippingPage";
import { ProductionIntakePage } from "./ProductionIntakePage";
import { ReferencesPage } from "./ReferencesPage";
import { NotificationsPage } from "./NotificationsPage";
import { NotificationBell } from "./NotificationBell";
import { OperationsPage } from "./OperationsPage";
import { TechcardsPage } from "./TechcardsPage";
import { ProjectsPage } from "./ProjectsPage";
// Экран чертежа жгута грузится отдельным куском — вместе с редактором, главный пакет не растёт.
const HarnessPage = lazy(() => import("./HarnessPage").then((module) => ({ default: module.HarnessPage })));
// «Библиотека компонентов» — тоже отдельным куском, открывается из чертежа жгута.
const ComponentLibraryPage = lazy(() => import("./ComponentLibraryPage").then((module) => ({ default: module.ComponentLibraryPage })));
import { StaffPage } from "./StaffPage";
import { EmployeeCardPage } from "./EmployeeCardPage";
import { AccessPage } from "./AccessPage";
import { ProfilePanel } from "./ProfilePanel";
import { isTsdAddress } from "./devicesApi";
import { TsdEntry, TsdLogin, TsdScreen } from "./TsdPage";
import { ThemeSwitch } from "./ThemeSwitch";
import { CommandPalette } from "./CommandPalette";
import { ViewAsBanner } from "./ViewAs";

/** `user`: with `section=card` — whose card (empty — own); with `section=staff` — whose edit form opens at once.
 *  `tsd`: the ТСД screen (`?section=tsd`) — full screen, its own PIN sign-in. */
type Place = { section: Section; block: BlockId | null; user?: string | null; harness?: { project: string; index: string; library?: boolean }; tsd?: boolean };

function placeFromUrl(): Place {
  const params = new URLSearchParams(window.location.search);
  let value = params.get("section");
  if (isTsdAddress()) return { section: "cabinet", block: null, tsd: true };
  // A tab that moved to another section (MOVED_TABS): the address bar says the new place, the tab and its filters stay.
  const moved = value && sectionOfTab(value, params.get("tab"));
  if (moved && moved !== value) {
    const url = new URL(window.location.href);
    url.searchParams.set("section", moved);
    window.history.replaceState(window.history.state, "", url);
    value = moved;
  }
  if (value === "warehouse") return { section: "stock", block: null };
  if (value === "projects" && params.get("project") && params.get("harness")) {
    return { section: value, block: null, harness: { project: params.get("project")!, index: params.get("harness")!, library: params.get("library") === "1" } };
  }
  if (isSection(value)) return { section: value, block: null, user: params.get("user") || null };
  const block = params.get("block");
  return { section: "cabinet", block: isBlock(block) ? block : null };
}

export function App() {
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [loading, setLoading] = useState(true);
  const [place, setPlace] = useState<Place>(placeFromUrl);
  // The address bar changed under us (global search, back button): pages that read their start filter from it remount.
  const [visit, setVisit] = useState(0);
  const [shellError, setShellError] = useState("");
  // On a tablet the sidebar is a drawer behind «☰»; any navigation closes it.
  const [navOpen, setNavOpen] = useState(false);
  // The first «who am I» failed without a 401 (no network, deadline): the error and «Повторить», not the login form.
  const [bootError, setBootError] = useState("");
  // A background 401 (bell poll, any page) while signed in: the page stays mounted under the sign-in dialog.
  const [expired, setExpired] = useState(false);
  const principalRef = useRef<Principal | null>(null);
  principalRef.current = principal;
  // Unsent forms per user (F07); lives as long as the tab, never in browser storage.
  const drafts = useRef(new DraftStore()).current;

  function loadPrincipal() {
    setLoading(true);
    setBootError("");
    identityApi
      .me()
      .then(setPrincipal)
      .catch((error) => {
        if (error instanceof ApiError && error.status === 401) return;
        setBootError(failureText(error, "ERP не ответила"));
      })
      .finally(() => setLoading(false));
  }

  useEffect(loadPrincipal, []);

  useEffect(() => {
    const listener = () => { setPlace(placeFromUrl()); setVisit((count) => count + 1); };
    window.addEventListener("popstate", listener);
    return () => window.removeEventListener("popstate", listener);
  }, []);

  useEffect(() => {
    const requireLogin = () => {
      if (principalRef.current) setExpired(true);
    };
    window.addEventListener(AUTH_REQUIRED_EVENT, requireLogin);
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, requireLogin);
  }, []);

  const visibleNav = useMemo(() => (principal ? visibleNavFor(principal) : []), [principal]);
  const blocks = useMemo(() => (principal ? visibleBlocks(principal) : []), [principal]);

  function go(next: Place) {
    const url = new URL(window.location.href);
    url.searchParams.delete("section");
    url.searchParams.delete("block");
    url.searchParams.delete("project");
    url.searchParams.delete("user");
    url.searchParams.delete("tab");
    url.searchParams.delete("q");
    url.searchParams.delete("harness");
    url.searchParams.delete("library");
    if (next.section !== "cabinet") url.searchParams.set("section", next.section);
    else if (next.block) url.searchParams.set("block", next.block);
    if (next.user) url.searchParams.set("user", next.user);
    window.history.pushState({}, "", url);
    setPlace(next);
    setNavOpen(false);
  }
  const navigate = (section: Section) => go({ section, block: null });
  // «Карточка проекта» from the production plan (SUPPLYMAP planAct_ 'card' → openKitsetCard): the kitset card
  // in «Проекты».
  function openProjectCard(project: string) {
    go({ section: "projects", block: null });
    const url = new URL(window.location.href);
    url.searchParams.set("project", project);
    window.history.replaceState({}, "", url);
  }
  // «Личная карточка»: own (no id) or someone else's (staffdir view); the edit form of «Сотрудники» opens on the card's «Редактировать».
  const openCard = (user?: string) => go({ section: "card", block: null, user: user ?? null });
  const openStaffEdit = (user: string) => go({ section: "staff", block: null, user });
  // A block with a single visible section opens that section directly: a screen with one card is a
  // wasted click (SUPPLYMAP openHomeBlock).
  function openBlock(id: BlockId) {
    const block = blocks.find((item) => item.id === id);
    if (block?.items.length === 1) navigate(block.items[0].id);
    else go({ section: "cabinet", block: id });
  }

  // «Режим админа» (SUPPLYMAP toggleAdmin → portalToggleAdmin): the role and sections change, so back to the home screen.
  const [switching, setSwitching] = useState(false);
  async function toggleAdmin() {
    if (!principal || switching) return;
    setShellError("");
    setSwitching(true);
    try {
      const next = await identityApi.adminMode(!principal.admin_mode);
      setPrincipal(next);
      go({ section: "cabinet", block: null });
    } catch (error) {
      setShellError(error instanceof Error ? error.message : "Режим админа не переключён");
    } finally {
      setSwitching(false);
    }
  }

  // «Смотреть как сотрудник» (из своей карточки): весь ERP глазами выбранного — с главного экрана, как у него.
  async function startViewAs(employee: { id: string }) {
    const viewed = await identityApi.viewAs(employee.id);
    setPrincipal(viewed);
    go({ section: "cabinet", block: null });
  }
  // «Вернуться к себе» — туда, откуда начали: своя карточка.
  async function exitViewAs() {
    const own = await identityApi.stopViewAs();
    setPrincipal(own);
    go({ section: "card", block: null });
  }

  async function signOut() {
    setShellError("");
    const leaving = principal;
    const signedOut = () => {
      if (leaving) drafts.clearUser(leaving.user.id);
      setExpired(false);
      setPrincipal(null);
    };
    try {
      await identityApi.logout();
      signedOut();
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        signedOut();
        return;
      }
      setShellError(
        error instanceof Error
          ? `Выход не выполнен: ${error.message}`
          : "Выход не выполнен: сервер недоступен",
      );
    }
  }

  // Signed in again over the kept page. The same user goes on where they were; someone else gets a fresh shell
  // (the `key` below), and the first user's unsent forms stay keyed to them, out of the newcomer's sight.
  function signInAgain(next: Principal) {
    setExpired(false);
    setPrincipal(next);
  }

  if (loading) return <div className="screen-center muted">Загружаем ERP…</div>;
  if (!principal && bootError) {
    return <div className="screen-center boot-error">
      <div className="error-banner" role="alert">{bootError}</div>
      <button className="primary" onClick={loadPrincipal}>Повторить</button>
    </div>;
  }
  if (!principal) return place.tsd ? <TsdEntry onLogin={setPrincipal} /> : <LoginPage onLogin={setPrincipal} />;
  // ТСД склада: its own full-screen frame and PIN sign-in; without the
  // warehouse section the screen hands over to the ordinary shell.
  if (place.tsd) {
    return <>
      <TsdScreen key={principal.user.id} principal={principal} onSignOut={() => void signOut()}
        onLeave={() => go({ section: "cabinet", block: null })} />
      {expired && <div className="tsd-relogin"><TsdLogin onLogin={signInAgain} title="Сессия истекла — войдите снова" /></div>}
    </>;
  }
  const relogin = expired && <SessionExpiredDialog onLogin={signInAgain} />;
  // A section or block the principal cannot open falls back to the home screen.
  const activeSection = visibleNav.some(({ id }) => id === place.section) ? place.section : "cabinet";
  const activeWithin = visibleNav.find(({ id }) => id === activeSection)?.within;
  const activeBlock = activeSection === "cabinet" ? blocks.find((block) => block.id === place.block) ?? null : null;
  // The level's name under the user (the backend adds «· режим» while the admin mode is on).
  // Чертёж жгута — на весь экран, без меню: редактору нужна вся площадь.
  if (place.harness && visibleNav.some(({ id }) => id === "projects")) {
    return <>
      <div key={principal.user.id} className="harness-host" inert={expired}>
        <ViewAsBanner principal={principal} onExit={exitViewAs} />
        <Suspense fallback={<div className="screen-center muted">Загрузка редактора…</div>}>
          {place.harness.library
            ? <ComponentLibraryPage project={place.harness.project} index={place.harness.index} canEdit={principal.permissions.projects === "edit"} />
            : <HarnessPage project={place.harness.project} index={place.harness.index} canEdit={principal.permissions.projects === "edit"} />}
        </Suspense>
      </div>
      {relogin}
    </>;
  }
  const levelName = principal.role_label || (accessLevelOf(principal) ? accessLevelLabel(accessLevelOf(principal)) : "");

  return (
    <DraftProvider store={drafts} userId={principal.user.id}>
    <div key={principal.user.id} className={navOpen ? "app-shell shell nav-open" : "app-shell shell"} inert={expired}>
      <div className="topbar">
        <button className="nav-toggle" aria-label="Меню" aria-expanded={navOpen} onClick={() => setNavOpen((open) => !open)}>☰</button>
        <strong className="wordmark">FRAXIS</strong>
        <span className="mod">ERP</span>
      </div>
      {navOpen && <div className="nav-scrim" aria-hidden="true" onClick={() => setNavOpen(false)} />}
      <aside className="sidebar side">
        <div className="brand">
          <strong className="wordmark">FRAXIS</strong>
          <span className="mod">ERP</span>
          {visibleNav.some(({ id }) => id === "notifications") && <NotificationBell open={() => navigate("notifications")} />}
        </div>
        <nav aria-label="Основная навигация">
          {visibleNav.filter((item) => !item.block).map((item) => <NavButton key={item.id} item={item} active={activeSection === item.id && !activeBlock} navigate={navigate} />)}
          {blocks.map((block) => (
            <div className="nav-group" key={block.id}>
              <h4>{block.title}</h4>
              {block.items.map((item) => <NavButton key={item.id} item={item} active={activeSection === item.id || activeWithin === item.id} navigate={navigate} />)}
            </div>
          ))}
        </nav>
        <div className="profile side-foot">
          <strong>{principal.user.name}</strong>
          {levelName && <span className="role-label">{levelName}</span>}
          <span>{principal.user.department}</span>
          {principal.can_admin_mode && (
            <button
              className={principal.admin_mode ? "secondary small admin-mode on" : "secondary small admin-mode"}
              disabled={switching}
              onClick={() => void toggleAdmin()}
            >
              {switching ? "…" : principal.admin_mode ? "Выйти из админа" : "Режим админа"}
            </button>
          )}
          <ProfilePanel openCard={() => openCard()} />
          <ThemeSwitch />
          <button
            className="link-button"
            onClick={() => void signOut()}
          >
            Выйти
          </button>
        </div>
      </aside>
      <CommandPalette principal={principal} />
      <main className="workspace main">
        <ViewAsBanner principal={principal} onExit={exitViewAs} />
        {shellError && <div className="shell-error error-banner" role="alert">{shellError}</div>}
        {runsTheSystem(principal) && <UpdateFailure />}
        {activeSection === "cabinet" && !activeBlock && <Home principal={principal} blocks={blocks} openBlock={openBlock} />}
        {activeBlock && <BlockPage principal={principal} block={activeBlock} navigate={navigate} home={() => go({ section: "cabinet", block: null })} />}
        {activeSection === "projects" && <ProjectsPage key={visit} principal={principal} />}
        {activeSection === "techcards" && <TechcardsPage principal={principal} view="projects" />}
        {activeSection === "tctemplates" && <ReferencesPage principal={principal} initialTab="templates" />}
        {activeSection === "newrequest" && <RequestsPage principal={principal} view="create" />}
        {activeSection === "myrequests" && <RequestsPage principal={principal} view="mine" />}
        {activeSection === "requests" && <RequestsPage principal={principal} />}
        {activeSection === "procurement" && <ProcurementPage key={visit} readOnly={!canAct(principal, "procurement")} buyerName={principal.user.name}
          canHandoff={canAct(principal, "requests")} openRegister={visibleNav.some(({ id }) => id === "requests") ? () => navigate("requests") : undefined} />}
        {activeSection === "purchasingrefs" && <PurchasingRefsPage key={visit} readOnly={!canAct(principal, "procurement")} />}
        {activeSection === "primarydocs" && <PrimaryDocsPage key={visit} readOnly={!canAct(principal, "procurement")} />}
        {activeSection === "receiving" && <ReceivingPage readOnly={!canAct(principal, "receiving")} />}
        {activeSection === "stock" && <WarehousePage key={visit} admin={hasFullAccess(principal)} readOnly={!canAct(principal, "warehouse")}
          canEditReferences={hasAccess(principal.permissions, "erpref", "edit")} finishedGoods={canSee(principal, "shipping") || canSee(principal, "shipping_goods")} />}
        {activeSection === "intake" && <ProductionIntakePage principal={principal} />}
        {activeSection === "equipment" && <EquipmentPage readOnly={!canAct(principal, "warehouse")} admin={hasFullAccess(principal)} />}
        {activeSection === "shipping" && <ShippingPage principal={principal} />}
        {activeSection === "issues" && <FulfillmentPage principal={principal} mode="pick" />}
        {activeSection === "handout" && <FulfillmentPage principal={principal} mode="give" />}
        {activeSection === "prodplan" && <ProductionPlanPage principal={principal} openProjectCard={visibleNav.some(({ id }) => id === "projects") ? openProjectCard : undefined} />}
        {activeSection === "production" && <ProductionPage principal={principal} />}
        {activeSection === "myops" && <ProductionPage principal={principal} mine />}
        {activeSection === "quality" && <QualityPage principal={principal} />}
        {activeSection === "references" && <ReferencesPage principal={principal} />}
        {activeSection === "notifications" && <NotificationsPage />}
        {activeSection === "operations" && <OperationsPage />}
        {activeSection === "staff" && <StaffPage openCard={openCard} openUserId={place.user} />}
        {activeSection === "card" && <EmployeeCardPage
          key={place.user ?? "own"}
          principal={principal}
          userId={place.user}
          openProject={visibleNav.some(({ id }) => id === "projects") ? openProjectCard : undefined}
          openMyRequests={visibleNav.some(({ id }) => id === "myrequests") ? () => navigate("myrequests") : undefined}
          openStaffEdit={openStaffEdit}
          openCard={openCard}
          openStaff={() => navigate("staff")}
          startViewAs={startViewAs}
        />}
        {activeSection === "access" && <AccessPage />}
      </main>
    </div>
    {relogin}
    </DraftProvider>
  );
}

function NavButton({
  item,
  active,
  navigate,
}: {
  item: NavItem;
  active: boolean;
  navigate: (section: Section) => void;
}) {
  return (
    <button
      className={active ? "nav-item active" : "nav-item"}
      onClick={() => navigate(item.id)}
    >
      <span>{item.label}</span>
    </button>
  );
}

type VisibleBlock = ReturnType<typeof visibleBlocks>[number];

// Home: the four SUPPLYMAP blocks as tiles; a block with no section open to this principal is hidden.
function Home({
  principal,
  blocks,
  openBlock,
}: {
  principal: Principal;
  blocks: VisibleBlock[];
  openBlock: (id: BlockId) => void;
}) {
  return (
    <section className="page">
      <header className="page-header">
        <div><span className="eyebrow">Личный кабинет</span><h1>Добрый день, {principal.user.name}</h1><p>Выберите блок — внутри его разделы.</p></div>
      </header>
      <div className="tiles block-tiles">
        {blocks.map((block) => {
          const names = block.items.map((item) => item.label).join(" · ");
          return (
            <button className="tile block-tile" key={block.id} onClick={() => openBlock(block.id)} aria-label={block.title}>
              <span className="tile-icon" aria-hidden="true"><BlockIcon id={block.id} /></span>
              <span className="tile-body">
                <strong className="t-h">{block.title}</strong>
                {/* With few sections the block's promise would lie; print the sections themselves. */}
                <span className="t-sub">{block.items.length >= 3 ? block.sub : names}</span>
                {block.items.length >= 3 && <span className="t-in">{names}</span>}
              </span>
              {/* Счётчик с подписью (владелец 16.10: «1, 2, 2 — непонятно, что считают»): разделы блока, открытые сотруднику. */}
              <span className="tile-count">{countLabel(block.items.length, ["раздел", "раздела", "разделов"])}</span>
            </button>
          );
        })}
      </div>
      {!blocks.length && <div className="empty">Разделов для вашего уровня доступа пока нет. Обратитесь к администратору.</div>}
    </section>
  );
}

function BlockPage({
  principal,
  block,
  navigate,
  home,
}: {
  principal: Principal;
  block: VisibleBlock;
  navigate: (section: Section) => void;
  home: () => void;
}) {
  return (
    <section className="page">
      <button className="link-button back-link" onClick={home}>← Главная</button>
      <header className="page-header">
        <div><span className="eyebrow">Блок</span><h1>{block.title}</h1><p>{block.sub}</p></div>
      </header>
      <div className="tiles section-tiles">
        {block.items.map((item) => (
          <button className="tile section-tile" key={item.id} onClick={() => navigate(item.id)} aria-label={item.label}>
            <span className="tile-body">
              <strong className="t-h">{item.label}</strong>
              {item.sub && <span className="t-sub">{item.sub}</span>}
              {isViewOnly(principal, item) && <span className="chip">только просмотр</span>}
            </span>
            <span className="tile-arrow" aria-hidden="true">→</span>
          </button>
        ))}
      </div>
    </section>
  );
}

