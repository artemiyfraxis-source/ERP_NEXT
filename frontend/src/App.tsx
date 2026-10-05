import { useEffect, useMemo, useRef, useState } from "react";
import {
  AUTH_REQUIRED_EVENT,
  ApiError,
  accessLevelLabel,
  identityApi,
  type Principal,
} from "./api";
import {
  accessLevelOf,
  canAct,
  hasFullAccess,
  isBlock,
  isViewOnly,
  isSection,
  runsTheSystem,
  visibleBlocks,
  visibleNav as visibleNavFor,
  type BlockId,
  type NavItem,
  type Section,
} from "./navigation";
import { LoginPage } from "./LoginPage";
import { UpdateFailure } from "./UpdateBanner";
import { FulfillmentPage } from "./FulfillmentPage";
import { ProcurementPage } from "./ProcurementPage";
import { ProductionPage } from "./ProductionPage";
import { ProductionPlanPage } from "./ProductionPlanPage";
import { QualityPage } from "./QualityPage";
import { RequestsPage } from "./RequestsPage";
import { ReceivingPage } from "./ReceivingPage";
import { WarehousePage } from "./WarehousePage";
import { ShippingPage } from "./ShippingPage";
import { ReferencesPage } from "./ReferencesPage";
import { NotificationsPage } from "./NotificationsPage";
import { NotificationBell } from "./NotificationBell";
import { OperationsPage } from "./OperationsPage";
import { TechcardsPage } from "./TechcardsPage";
import { ProjectsPage } from "./ProjectsPage";
import { StaffPage } from "./StaffPage";
import { EmployeeCardPage } from "./EmployeeCardPage";
import { AccessPage } from "./AccessPage";
import { ProfilePanel } from "./ProfilePanel";
import { StageOptionsPanel } from "./StageOptions";

/** `user`: with `section=card` — whose card (empty — own); with `section=staff` — whose edit form opens at once. */
type Place = { section: Section; block: BlockId | null; user?: string | null };

function placeFromUrl(): Place {
  const params = new URLSearchParams(window.location.search);
  const value = params.get("section");
  if (value === "warehouse") return { section: "stock", block: null };
  if (isSection(value)) return { section: value, block: null, user: params.get("user") || null };
  const block = params.get("block");
  return { section: "cabinet", block: isBlock(block) ? block : null };
}

export function App() {
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [loading, setLoading] = useState(true);
  const [place, setPlace] = useState<Place>(placeFromUrl);
  const [shellError, setShellError] = useState("");
  // On a tablet the sidebar is a drawer behind «☰»; any navigation closes it.
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => {
    identityApi
      .me()
      .then(setPrincipal)
      .catch((error) => {
        if (!(error instanceof ApiError) || error.status !== 401) console.error(error);
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const listener = () => setPlace(placeFromUrl());
    window.addEventListener("popstate", listener);
    return () => window.removeEventListener("popstate", listener);
  }, []);

  useEffect(() => {
    const requireLogin = () => setPrincipal(null);
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

  async function signOut() {
    setShellError("");
    try {
      await identityApi.logout();
      setPrincipal(null);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setPrincipal(null);
        return;
      }
      setShellError(
        error instanceof Error
          ? `Выход не выполнен: ${error.message}`
          : "Выход не выполнен: сервер недоступен",
      );
    }
  }

  if (loading) return <div className="screen-center muted">Загружаем ERP…</div>;
  if (!principal) return <LoginPage onLogin={setPrincipal} />;
  // A section or block the principal cannot open falls back to the home screen.
  const activeSection = visibleNav.some(({ id }) => id === place.section) ? place.section : "cabinet";
  const activeWithin = visibleNav.find(({ id }) => id === activeSection)?.within;
  const activeBlock = activeSection === "cabinet" ? blocks.find((block) => block.id === place.block) ?? null : null;
  // The level's name under the user (the backend adds «· режим» while the admin mode is on).
  const levelName = principal.role_label || (accessLevelOf(principal) ? accessLevelLabel(accessLevelOf(principal)) : "");

  return (
    <div className={navOpen ? "app-shell shell nav-open" : "app-shell shell"}>
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
          <StageOptionsPanel canSwitch={hasFullAccess(principal)} />
          <button
            className="link-button"
            onClick={() => void signOut()}
          >
            Выйти
          </button>
        </div>
      </aside>
      <main className="workspace main">
        {shellError && <div className="shell-error error-banner" role="alert">{shellError}</div>}
        {runsTheSystem(principal) && <UpdateFailure />}
        {activeSection === "cabinet" && !activeBlock && <Home principal={principal} blocks={blocks} openBlock={openBlock} />}
        {activeBlock && <BlockPage principal={principal} block={activeBlock} navigate={navigate} home={() => go({ section: "cabinet", block: null })} />}
        {activeSection === "projects" && <ProjectsPage principal={principal} />}
        {activeSection === "techcards" && <TechcardsPage principal={principal} view="projects" />}
        {activeSection === "tctemplates" && <ReferencesPage principal={principal} initialTab="templates" />}
        {activeSection === "newrequest" && <RequestsPage principal={principal} view="create" />}
        {activeSection === "myrequests" && <RequestsPage principal={principal} view="mine" />}
        {activeSection === "requests" && <RequestsPage principal={principal} />}
        {activeSection === "procurement" && <ProcurementPage readOnly={!canAct(principal, "procurement")} buyerName={principal.user.name} />}
        {activeSection === "receiving" && <ReceivingPage readOnly={!canAct(principal, "receiving")} />}
        {activeSection === "stock" && <WarehousePage admin={hasFullAccess(principal)} readOnly={!canAct(principal, "warehouse")} />}
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
        />}
        {activeSection === "access" && <AccessPage />}
      </main>
    </div>
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
              <span className="tile-icon" aria-hidden="true">{block.icon}</span>
              <span className="tile-body">
                <strong className="t-h">{block.title}</strong>
                {/* With few sections the block's promise would lie; print the sections themselves. */}
                <span className="t-sub">{block.items.length >= 3 ? block.sub : names}</span>
                {block.items.length >= 3 && <span className="t-in">{names}</span>}
              </span>
              <span className="tile-count">{block.items.length}</span>
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
        <div><span className="eyebrow">Блок</span><h1><span aria-hidden="true">{block.icon}</span> {block.title}</h1><p>{block.sub}</p></div>
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

