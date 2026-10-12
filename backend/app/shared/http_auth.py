"""HTTP authentication dependencies backed by server-owned session state.

Уровни доступа (owner 04.10.2026, docs/migration/access-levels-plan.md): a route checks the mode of its section(s) and
nothing else — no role lists. superadmin/admin pass every gate; the administrator's tools are theirs alone. Which
section and minimum each dependency takes is the contract `app.modules.identity.level_migration.GATES`
(tests/identity/test_level_gates.py keeps the two equal).

Токены API (owner 05.10.2026): a request with `Authorization: Bearer erp_…` carries a service principal — the token's
name and its scopes, no level and no section rights. Default deny: `authenticated_principal` refuses it, and so does
every gate below, unless the gate names one of the token's scopes (`require_section(..., scopes=(...))`). The gate
says so through FastAPI security scopes (`Security(authenticated_principal, scopes=["token:<scope>"])`): they are part
of the dependency cache key, so the acceptance belongs to that gate alone — a `Depends(authenticated_principal)`
elsewhere on the same route is resolved on its own and still refuses the token. A router-level gate built with
`defer_tokens=True` lets a token through to the route's own gate without accepting it. The administrator's tools
(`require_full_access`) never accept a token."""

from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import SecurityScopes

from app.modules.identity.domain import FULL_ACCESS_LEVELS, LEVEL_SECTION_LABELS

from .identity import Actor


@dataclass(frozen=True)
class Principal:
    actor: Actor
    email: str
    # The access level key (one of ACCESS_LEVELS). «Режим админа» (SUPPLYMAP portalSession_) raises the session to the
    # level admin; `roles` already holds the effective level, `can_admin_mode` says whether the account may switch it.
    roles: frozenset[str]
    permissions: dict[str, Literal["view", "edit"]]
    admin_mode: bool = False
    can_admin_mode: bool = False
    # A service principal (an API token): acts with these scopes only, never with the creator's rights.
    service: bool = False
    scopes: frozenset[str] = frozenset()

    @property
    def level(self) -> str:
        return min(self.roles, default="")

    @property
    def full_access(self) -> bool:
        """superadmin / admin: every section on edit and the administrator's tools."""
        return bool(self.roles & FULL_ACCESS_LEVELS)


SERVICE_DENIED = "Токен API не открывает этот раздел."
TOKEN_SCOPE_PREFIX = "token:"
TOKEN_DEFER = TOKEN_SCOPE_PREFIX + "*"  # a router-level gate: the route's own gate decides


def authenticated_principal(request: Request, security_scopes: SecurityScopes = SecurityScopes()) -> Principal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal):
        raise HTTPException(status_code=401, detail="Требуется вход в ERP")
    if principal.service:
        named = {scope for scope in security_scopes.scopes if scope.startswith(TOKEN_SCOPE_PREFIX)}
        accepted = {scope.removeprefix(TOKEN_SCOPE_PREFIX) for scope in named}
        if TOKEN_DEFER not in named and not principal.scopes & accepted:
            raise HTTPException(status_code=403, detail=SERVICE_DENIED)
    return principal


def refuse_api_tokens(detail: str = SERVICE_DENIED):
    """A route no token reaches even when its gate names the token's scope (a write the scope must not cover). Listed
    in the route's `dependencies`, so it runs before the body is read."""

    def refuse(request: Request) -> None:
        principal = getattr(request.state, "principal", None)
        if isinstance(principal, Principal) and principal.service:
            raise HTTPException(status_code=403, detail=detail)

    return refuse


def section_allowed(principal: Principal, sections: tuple[str, ...], minimum: Literal["view", "edit"]) -> bool:
    """One of the sections grants `minimum` (SUPPLYMAP portalIssueGuard_ without a section: the fulfillment list is
    readable from «Запросы на комплектацию» or «Выдача со списанием»). A section outside the level matrix
    («Сотрудники») is never granted below superadmin/admin."""
    if principal.full_access:
        return True
    modes = [principal.permissions.get(key) for key in sections if key in LEVEL_SECTION_LABELS]
    return "edit" in modes or (minimum == "view" and "view" in modes)


def require_section(
    section: str | tuple[str, ...],
    minimum: Literal["view", "edit"],
    *,
    detail: str = "Недостаточно прав для раздела",
    scopes: tuple[str, ...] = (),
    defer_tokens: bool = False,
):
    """A tuple of sections passes when ANY of them grants `minimum`. `scopes` — the API-token scopes this gate admits
    (a token never passes a gate without them). `defer_tokens` — a router-level gate: a token passes it untouched and
    the route's own gate decides."""
    sections = (section,) if isinstance(section, str) else section
    accepted = frozenset(scopes)
    if accepted and defer_tokens:
        raise ValueError("A gate either names token scopes or defers tokens, not both.")
    token_scopes = [TOKEN_DEFER] if defer_tokens else [TOKEN_SCOPE_PREFIX + scope for scope in sorted(accepted)]
    base = Security(authenticated_principal, scopes=token_scopes) if token_scopes else Depends(authenticated_principal)

    def dependency(
        principal: Annotated[Principal, base],
    ) -> Principal:
        if principal.service:
            if defer_tokens or principal.scopes & accepted:
                return principal
            raise HTTPException(status_code=403, detail=SERVICE_DENIED)
        if not section_allowed(principal, sections, minimum):
            raise HTTPException(status_code=403, detail=detail)
        return principal

    dependency.token_scopes = accepted  # type: ignore[attr-defined]  (the contract test reads it)
    return dependency


def require_full_access(detail: str = "Только администратору."):
    """The administrator's tools (SUPPLYMAP portalRealAdmin_): superadmin / admin only, never under the matrix."""

    def dependency(
        principal: Annotated[Principal, Depends(authenticated_principal)],
    ) -> Principal:
        if principal.service or not principal.full_access:
            raise HTTPException(status_code=403, detail=detail)
        return principal

    return dependency


# NAV-SECTIONS (SUPPLYMAP Portal.gs PORTAL_SECTIONS_, owner 27.08.2026): filing and one's own rows have sections of
# their own, so «Заявки» (`requests`: every row) can be closed to a rank-and-file employee without taking them away.
requests_view_principal = require_section("requests", "view")
# The form's reference data (schema, validate) serves «Оформить заявку» and the form inside «Заявки».
requests_form_principal = require_section(("newrequest", "requests"), "view")
newrequest_principal = require_section(
    "newrequest",
    "edit",
    detail="Раздел «Оформить заявку» вам не открыт.",
)
myrequests_principal = require_section(
    "myrequests",
    "view",
    detail="Раздел «Мои заявки» вам не открыт.",
)
# ID-3 (SUPPLYMAP PORTAL_SECTIONS_, Portal.gs:661-664): templates (`erptctpl`) and the generator (`tcgen`) have keys of
# their own; the techcards router admits any of the three and each route narrows it down.
# A token passes the router gate untouched; each route's own gate decides (only tcgen_principal admits `tcgen`).
techcards_router_principal = require_section(("techcards", "erptctpl", "tcgen"), "view", defer_tokens=True)
techcards_view_principal = require_section("techcards", "view", detail="Раздел «Техкарты проектов» вам не открыт.")
# «Проекты» — карточки наборов (SUPPLYMAP erptechmap): просмотр читает, запись (загрузка, правка, удаление,
# отправка на комплектацию, КД) — только с правом правки раздела (portalSectionEditGuard_).
projects_view_principal = require_section("projects", "view", detail="Раздел «Проекты» вам не открыт.")
projects_edit_principal = require_section("projects", "edit", detail="Раздел «Проекты» открыт только на просмотр.")
# Плитки проектов читает и экран мастера («Ресурсное планирование», владелец 29.09) и исполнитель в «Заданиях».
kitset_tiles_principal = require_section(
    ("projects", "production", "myops"), "view", detail="Раздел «Проекты» вам не открыт.",
)
techcards_edit_principal = require_section(
    "techcards", "edit", detail="Раздел «Техкарты проектов» открыт вам только на просмотр.",
)
tctemplates_view_principal = require_section("erptctpl", "view", detail="Раздел «Шаблоны техкарт» вам не открыт.")
# ErpTechmap.gs portalTcRecordSave: portalSectionEditGuard_(token, 'erptctpl') — правка записи операции.
tctemplates_edit_principal = require_section(
    "erptctpl", "edit", detail="Раздел «Шаблоны техкарт» открыт вам только на просмотр.",
)
# Loading the SUPPLYMAP operation library is the administrator's tool (sync script).
tctemplates_admin_principal = require_full_access("Загрузка библиотеки шаблонов — только администратору.")
# ErpTechmap.gs: every generator handle is portalErpGuard_(token, 'tcgen') — the section being open is enough.
tcgen_principal = require_section(
    "tcgen", "view", detail="Раздел «Генератор техкарт» вам не открыт.", scopes=("tcgen",),
)
# «Режим просмотра» (ID-6, SUPPLYMAP portalApplyAccess_ 'view' + portalErpGuard_): a section granted as view is
# readable, every write keeps the *_edit_principal gate.
procurement_view_principal = require_section("procurement", "view")
procurement_edit_principal = require_section("procurement", "edit")
receiving_view_principal = require_section("receiving", "view")
receiving_edit_principal = require_section("receiving", "edit")
warehouse_view_principal = require_section("warehouse", "view")
warehouse_edit_principal = require_section("warehouse", "edit")
# Import of an edited stock table (SK-14): the administrator only (SUPPLYMAP portalStock05Bulk, adminOnly).
warehouse_admin_principal = require_full_access("Импорт таблицы остатков — только администратору.")
# «Журнал склада» (SK-16): SUPPLYMAP portalStock05Log — portalRealAdmin_.
warehouse_journal_principal = require_full_access("Журнал склада доступен только администратору.")
# Picking reserves, handout writes off: two sections over one request (SUPPLYMAP Issue.gs portalIssueGuard_, owner
# 02.09.2026). Reading the shared list takes either key; each write takes its own (NAV-SECTIONS).
fulfillment_view_principal = require_section(("fulfillment", "handout"), "view")
fulfillment_edit_principal = require_section("fulfillment", "edit")
# FF-5: SUPPLYMAP portalIssueDelete — portalRealAdmin_.
fulfillment_admin_principal = require_full_access("Удалять заявки может только администратор.")
fulfillment_handout_principal = require_section(
    "handout",
    "edit",
    detail="Раздел «Выдача со списанием» вам не открыт на правку.",
)
# «Задания» (`myops`): the executor's own operations — reading them, and start / pause / submit (правка).
production_view_principal = require_section("myops", "view", detail="Раздел «Задания» вам не открыт.")
production_view_work_principal = require_section("myops", "edit", detail="Раздел «Задания» открыт вам только на просмотр.")
# «Планирование и производственный план» (`production`); «ОТК» (`quality`) — the quality decisions.
production_overview_principal = require_section("production", "view")
production_edit_principal = require_section("production", "edit")
production_quality_principal = require_section("quality", "edit", detail="Раздел «ОТК» вам не открыт на правку.")
# «Отгрузка» (`shipping`, the manager's side) and «Отгрузка — склад» (`shipping_goods`, the storekeeper's side): the
# list reads from either; packing, lines and УПД are edited from either (shipping/api.py shipping_editor_principal).
shipping_view_principal = require_section(("shipping", "shipping_goods"), "view")
shipping_manager_principal = require_section("shipping", "edit")
shipping_warehouse_principal = require_section(
    "shipping_goods", "edit", detail="Раздел «Отгрузка — склад» вам не открыт на правку.",
)
# «Справочник» (SUPPLYMAP PortalRef.gs): portalErpGuard_(token, 'erpref') reads, portalSectionEditGuard_ writes.
# API tokens (owner 12.10.2026): `refs:read` reads the tables, `refs:write` also edits rows and loads them through the
# per-table import (shared/bulk_import.py). Neither replaces a whole snapshot nor the maps: those gates below name no
# scope, so a token never passes them.
references_view_principal = require_section(
    "erpref", "view", detail="Раздел «Справочник» вам не открыт.", scopes=("refs:read", "refs:write"),
)
references_edit_principal = require_section(
    "erpref", "edit", detail="Раздел «Справочник» открыт вам только на просмотр.", scopes=("refs:write",),
)
# Whole-base snapshot loads (SUPPLYMAP mirror / snapshot scripts): an administrator's tool, never a token.
references_import_principal = require_full_access("Загрузка снимка баз — только администратору.")
# Maps «ссылка→поставщик» / «изготовитель→страна» (Portal.gs portalGet/SaveReferences): portalCanWork_ — the
# purchase card with «взять в работу», i.e. `procurement` edit. Reading also opens from «Справочник».
references_maps_view_principal = require_section(("erpref", "procurement"), "view")
# Saving takes either key; each map is replaced only by whom it belongs to (references/api.py `_map_rights`).
references_maps_edit_principal = require_section(
    ("procurement", "erpref"), "edit", detail="Справочники правит только закупщик.",
)
operations_view_principal = require_section("operations", "view")
# «Сотрудники» (Staff.gs portalStaffGet/Save: portalRealAdmin_).
staffdir_principal = require_full_access("Раздел «Сотрудники» — только администратору.")
# «Доступы» (portalAccessGet/Save: portalRealAdmin_): never under the matrix, so it cannot close the settings to the
# people who set it.
access_admin_principal = require_full_access("Только администратор.")
