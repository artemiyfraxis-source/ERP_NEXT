import { useEffect, useMemo, useRef, useState } from "react";
import {
  AUTH_REQUIRED_EVENT,
  ApiError,
  identityApi,
  legacyApi,
  type Principal,
} from "./api";
import {
  isBlock,
  isSection,
  visibleBlocks,
  visibleNav as visibleNavFor,
  type BlockId,
  type NavItem,
  type Section,
} from "./navigation";
import { LoginPage } from "./LoginPage";
import { FulfillmentPage } from "./FulfillmentPage";
import { ProcurementPage } from "./ProcurementPage";
import { ProductionPage } from "./ProductionPage";
import { ProductionPlanPage } from "./ProductionPlanPage";
import { RequestsPage } from "./RequestsPage";
import { ReceivingPage } from "./ReceivingPage";
import { WarehousePage } from "./WarehousePage";
import { ShippingPage } from "./ShippingPage";
import { ReferencesPage } from "./ReferencesPage";
import { NotificationsPage } from "./NotificationsPage";
import { OperationsPage } from "./OperationsPage";
import { TechcardsPage } from "./TechcardsPage";
import { StaffPage } from "./StaffPage";

// «Режим просмотра» (ID-6): edit controls only when the backend *_edit_principal would pass too.
function canEdit(principal: Principal, permission: string, roles: string[]) {
  return principal.permissions[permission] === "edit" && roles.some((role) => principal.roles.includes(role));
}

type Place = { section: Section; block: BlockId | null };

function placeFromUrl(): Place {
  const params = new URLSearchParams(window.location.search);
  const value = params.get("section");
  if (value === "warehouse") return { section: "stock", block: null };
  if (isSection(value)) return { section: value, block: null };
  const block = params.get("block");
  return { section: "cabinet", block: isBlock(block) ? block : null };
}

export function App() {
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [loading, setLoading] = useState(true);
  const [place, setPlace] = useState<Place>(placeFromUrl);
  const [shellError, setShellError] = useState("");

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
    if (next.section !== "cabinet") url.searchParams.set("section", next.section);
    else if (next.block) url.searchParams.set("block", next.block);
    window.history.pushState({}, "", url);
    setPlace(next);
  }
  const navigate = (section: Section) => go({ section, block: null });
  // A block with a single visible section opens that section directly: a screen with one card is a
  // wasted click (SUPPLYMAP openHomeBlock).
  function openBlock(id: BlockId) {
    const block = blocks.find((item) => item.id === id);
    if (block?.items.length === 1) navigate(block.items[0].id);
    else go({ section: "cabinet", block: id });
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
  const activeBlock = activeSection === "cabinet" ? blocks.find((block) => block.id === place.block) ?? null : null;

  return (
    <div className="app-shell shell">
      <aside className="sidebar side">
        <div className="brand">
          <strong className="wordmark">FRAXIS</strong>
          <span className="mod">ERP</span>
        </div>
        <nav aria-label="Основная навигация">
          {visibleNav.filter((item) => !item.block).map((item) => <NavButton key={item.id} item={item} active={activeSection === item.id && !activeBlock} navigate={navigate} />)}
          {blocks.map((block) => (
            <div className="nav-group" key={block.id}>
              <h4>{block.title}</h4>
              {block.items.map((item) => <NavButton key={item.id} item={item} active={activeSection === item.id} navigate={navigate} />)}
            </div>
          ))}
        </nav>
        <div className="profile side-foot">
          <strong>{principal.user.name}</strong>
          <span>{principal.user.department}</span>
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
        {activeSection === "cabinet" && !activeBlock && <Home principal={principal} blocks={blocks} openBlock={openBlock} />}
        {activeBlock && <BlockPage principal={principal} block={activeBlock} navigate={navigate} home={() => go({ section: "cabinet", block: null })} />}
        {activeSection === "projects" && <LegacyFrame section="projects" scope="projects" title="Проекты" />}
        {activeSection === "calculation" && <LegacyFrame section="calc" scope="calculation" title="Расчёт и КП" />}
        {activeSection === "techcards" && <TechcardsPage principal={principal} view="projects" />}
        {activeSection === "tctemplates" && <TechcardsPage principal={principal} view="templates" />}
        {activeSection === "newrequest" && <RequestsPage principal={principal} view="create" />}
        {activeSection === "myrequests" && <RequestsPage principal={principal} view="mine" />}
        {activeSection === "requests" && <RequestsPage principal={principal} />}
        {activeSection === "procurement" && <ProcurementPage readOnly={!canEdit(principal, "procurement", ["buyer", "admin"])} />}
        {activeSection === "receiving" && <ReceivingPage readOnly={!canEdit(principal, "receiving", ["receiver", "admin"])} />}
        {activeSection === "stock" && <WarehousePage admin={principal.roles.includes("admin")} readOnly={!canEdit(principal, "warehouse", ["receiver", "admin"])} />}
        {activeSection === "shipping" && <ShippingPage principal={principal} />}
        {activeSection === "issues" && <FulfillmentPage principal={principal} mode="pick" />}
        {activeSection === "handout" && <FulfillmentPage principal={principal} mode="give" />}
        {activeSection === "prodplan" && <ProductionPlanPage principal={principal} />}
        {activeSection === "production" && <ProductionPage principal={principal} />}
        {activeSection === "myops" && <ProductionPage principal={principal} mine />}
        {activeSection === "references" && <ReferencesPage principal={principal} />}
        {activeSection === "notifications" && <NotificationsPage />}
        {activeSection === "operations" && <OperationsPage />}
        {activeSection === "staff" && <StaffPage />}
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
      {!blocks.length && <div className="empty">Разделов для вашей роли пока нет. Обратитесь к администратору.</div>}
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
              {item.permission && principal.permissions[item.permission] === "view" && <span className="chip">только просмотр</span>}
            </span>
            <span className="tile-arrow" aria-hidden="true">→</span>
          </button>
        ))}
      </div>
    </section>
  );
}

function LegacyFrame({
  section,
  scope,
  title,
}: {
  section: string;
  scope: "projects" | "calculation";
  title: string;
}) {
  const [ticket, setTicket] = useState("");
  const [error, setError] = useState("");
  const frameRef = useRef<HTMLIFrameElement>(null);
  const grantRef = useRef("");
  const base = import.meta.env.VITE_LEGACY_ERP_URL ?? "http://localhost:5177";
  useEffect(() => {
    let active = true;
    setTicket("");
    setError("");
    grantRef.current = "";
    const refresh = async () => {
      try {
        const result = await legacyApi.grant(scope);
        if (!active) return;
        if (!grantRef.current) {
          const bootstrap = await legacyApi.exchange(base, result.grant);
          if (!active) return;
          setTicket(bootstrap.ticket);
        } else {
          frameRef.current?.contentWindow?.postMessage(
            { type: "erp:legacy-grant", grant: result.grant },
            new URL(base).origin,
          );
        }
        grantRef.current = result.grant;
      } catch (reason) {
        if (active && !grantRef.current) {
          setError(reason instanceof Error ? reason.message : "Legacy bridge недоступен");
        }
      }
    };
    refresh();
    const timer = window.setInterval(refresh, 60_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [base, scope]);
  const src = ticket ? resolveLegacyUrl(base, window.location.href, section, ticket) : null;
  if (error) {
    return <section className="page"><div className="error-banner" role="alert">{error}</div></section>;
  }
  if (!ticket) {
    return <div className="screen-center muted">Открываем защищённый legacy-раздел…</div>;
  }
  if (!src) {
    return (
      <section className="page">
        <div className="error-banner" role="alert">
          Восстановленный ERP должен работать на отдельном origin. Проверьте VITE_LEGACY_ERP_URL.
        </div>
      </section>
    );
  }
  return (
    <section className="legacy-page">
      <header className="page-header compact"><div><span className="eyebrow">Восстановленный модуль</span><h1>{title}</h1></div></header>
      <iframe
        ref={frameRef}
        onLoad={() => {
          if (grantRef.current) {
            frameRef.current?.contentWindow?.postMessage(
              { type: "erp:legacy-grant", grant: grantRef.current },
              new URL(base).origin,
            );
          }
        }}
        className="legacy-frame"
        src={src}
        title={title}
        sandbox="allow-forms allow-scripts allow-same-origin allow-downloads"
      />
    </section>
  );
}

export function resolveLegacyUrl(
  base: string,
  currentUrl: string,
  section: string,
  ticket: string,
) {
  try {
    const legacy = new URL(base, currentUrl);
    if (
      !["http:", "https:"].includes(legacy.protocol) ||
      legacy.hostname.toLowerCase() === new URL(currentUrl).hostname.toLowerCase() ||
      legacy.username ||
      legacy.password
    ) {
      return null;
    }
    legacy.pathname = "/login";
    legacy.search = new URLSearchParams({ section, ticket }).toString();
    return legacy.toString();
  } catch {
    return null;
  }
}
