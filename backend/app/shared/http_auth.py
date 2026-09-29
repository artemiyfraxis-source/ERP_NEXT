"""HTTP authentication dependencies backed by server-owned session state."""

from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request

from .identity import Actor


@dataclass(frozen=True)
class Principal:
    actor: Actor
    email: str
    roles: frozenset[str]
    permissions: dict[str, Literal["view", "edit"]]


def authenticated_principal(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal):
        raise HTTPException(status_code=401, detail="Требуется вход в ERP")
    return principal


def require_section(
    section: str | tuple[str, ...],
    minimum: Literal["view", "edit"],
    *,
    roles: frozenset[str] | None = None,
    detail: str = "Недостаточно прав для раздела",
):
    """A tuple of sections passes when ANY of them grants `minimum` (SUPPLYMAP portalIssueGuard_ without a section:
    the fulfillment list is readable from «Запросы на комплектацию» or «Выдача со списанием»)."""
    sections = (section,) if isinstance(section, str) else section

    def dependency(
        principal: Annotated[Principal, Depends(authenticated_principal)],
    ) -> Principal:
        modes = [principal.permissions.get(key) for key in sections]
        allowed = "edit" in modes or (minimum == "view" and "view" in modes)
        if roles is not None and not principal.roles.intersection(roles):
            allowed = False
        if not allowed:
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
# ID-3 (SUPPLYMAP PORTAL_SECTIONS_, Portal.gs:661-664): Техкарты are gated by the matrix alone, without a role list
# («второй источник правды»). Templates (`erptctpl`) and the generator (`tcgen`) have keys of their own; the techcards
# router admits any of the three and each route narrows it down.
techcards_router_principal = require_section(("techcards", "erptctpl", "tcgen"), "view")
techcards_view_principal = require_section("techcards", "view", detail="Раздел «Техкарты проектов» вам не открыт.")
# «Проекты» — карточки наборов (SUPPLYMAP erptechmap): просмотр читает, запись (загрузка, правка, удаление,
# отправка на комплектацию, КД) — только с правом правки раздела (portalSectionEditGuard_).
projects_view_principal = require_section("projects", "view", detail="Раздел «Проекты» вам не открыт.")
projects_edit_principal = require_section("projects", "edit", detail="Раздел «Проекты» открыт только на просмотр.")
techcards_edit_principal = require_section(
    "techcards", "edit", detail="Раздел «Техкарты проектов» открыт вам только на просмотр.",
)
tctemplates_view_principal = require_section("erptctpl", "view", detail="Раздел «Шаблоны техкарт» вам не открыт.")
# ErpTechmap.gs portalTcRecordSave: portalSectionEditGuard_(token, 'erptctpl') — правка записи операции.
tctemplates_edit_principal = require_section(
    "erptctpl", "edit", detail="Раздел «Шаблоны техкарт» открыт вам только на просмотр.",
)
# Loading the SUPPLYMAP operation library is the administrator's tool (sync script), on top of `erptctpl` edit.
tctemplates_admin_principal = require_section(
    "erptctpl", "edit", roles=frozenset({"admin"}), detail="Загрузка библиотеки шаблонов — только администратору.",
)
# ErpTechmap.gs: every generator handle is portalErpGuard_(token, 'tcgen') — the section being open is enough.
tcgen_principal = require_section("tcgen", "view", detail="Раздел «Генератор техкарт» вам не открыт.")
# «Режим просмотра» (ID-6, SUPPLYMAP portalApplyAccess_ 'view' + portalErpGuard_): a section granted as view is
# readable, every write keeps the *_edit_principal gate. Role sets = the edit roles + the roles SUPPLYMAP opens the
# section to for reading (Portal.gs portalButtons_), same shape as fulfillment/production/shipping view.
procurement_view_principal = require_section(
    "procurement",
    "view",
    roles=frozenset({"buyer", "receiver", "prodhead", "management", "admin"}),
)
procurement_edit_principal = require_section(
    "procurement",
    "edit",
    roles=frozenset({"buyer", "admin"}),
)
receiving_view_principal = require_section(
    "receiving",
    "view",
    roles=frozenset({"receiver", "prodhead", "management", "master", "staff", "admin"}),
)
receiving_edit_principal = require_section(
    "receiving",
    "edit",
    roles=frozenset({"receiver", "admin"}),
)
warehouse_view_principal = require_section(
    "warehouse",
    "view",
    roles=frozenset({"receiver", "buyer", "prodhead", "management", "master", "staff", "admin"}),
)
warehouse_edit_principal = require_section(
    "warehouse",
    "edit",
    roles=frozenset({"receiver", "admin"}),
)
# Import of an edited stock table (SK-14): the administrator only (SUPPLYMAP portalStock05Bulk, adminOnly).
warehouse_admin_principal = require_section(
    "warehouse",
    "edit",
    roles=frozenset({"admin"}),
)
# «Журнал склада» (SK-16): SUPPLYMAP portalStock05Log — warehouse section + portalRealAdmin_ (admin role or admin
# mode); ERP has no admin mode, so the admin role.
warehouse_journal_principal = require_section(
    "warehouse",
    "view",
    roles=frozenset({"admin"}),
    detail="Журнал склада доступен только администратору.",
)
# Picking reserves, handout writes off: two sections over one request (SUPPLYMAP Issue.gs portalIssueGuard_, owner
# 02.09.2026). Reading the shared list takes either key; each write takes its own (NAV-SECTIONS).
fulfillment_view_principal = require_section(
    ("fulfillment", "handout"),
    "view",
    roles=frozenset({"engineer", "receiver", "prodhead", "master", "admin"}),
)
fulfillment_edit_principal = require_section(
    "fulfillment",
    "edit",
    roles=frozenset({"engineer", "receiver", "admin"}),
)
# FF-5: SUPPLYMAP portalIssueDelete — section `issues` on edit + portalRealAdmin_; ERP has no admin mode, so the role.
fulfillment_admin_principal = require_section(
    "fulfillment",
    "edit",
    roles=frozenset({"admin"}),
    detail="Удалять заявки может только администратор.",
)
fulfillment_handout_principal = require_section(
    "handout",
    "edit",
    roles=frozenset({"receiver", "admin"}),
    detail="Раздел «Выдача со списанием» вам не открыт на правку.",
)
production_view_principal = require_section(
    "production",
    "view",
    roles=frozenset({"staff", "master", "prodhead", "management", "qc", "admin"}),
)
production_overview_principal = require_section(
    "production",
    "view",
    roles=frozenset({"master", "prodhead", "management", "qc", "admin"}),
)
production_edit_principal = require_section(
    "production",
    "edit",
    roles=frozenset({"master", "admin"}),
)
production_quality_principal = require_section(
    "production",
    "edit",
    roles=frozenset({"qc", "admin"}),
)
shipping_view_principal = require_section(
    "shipping",
    "view",
    roles=frozenset({"pm", "receiver", "master", "prodhead", "management", "admin"}),
)
shipping_manager_principal = require_section(
    "shipping",
    "edit",
    roles=frozenset({"pm", "admin"}),
)
shipping_warehouse_principal = require_section(
    "shipping",
    "edit",
    roles=frozenset({"receiver", "admin"}),
)
# «Справочник» (SUPPLYMAP PortalRef.gs): portalErpGuard_(token, 'erpref') reads, portalSectionEditGuard_ writes —
# the `erpref` section decides, not the role.
references_view_principal = require_section("erpref", "view", detail="Раздел «Справочник» вам не открыт.")
references_edit_principal = require_section(
    "erpref", "edit", detail="Раздел «Справочник» открыт вам только на просмотр.",
)
# Whole-base snapshot loads (SUPPLYMAP mirror / snapshot scripts): an administrator's tool on top of `erpref` edit.
references_import_principal = require_section(
    "erpref", "edit", roles=frozenset({"admin"}), detail="Загрузка снимка баз — только администратору.",
)
# Maps «ссылка→поставщик» / «изготовитель→страна» (Portal.gs portalGet/SaveReferences): portalCanWork_ — the
# purchase card with «взять в работу», i.e. `procurement` edit. Reading also opens from «Справочник».
references_maps_view_principal = require_section(("erpref", "procurement"), "view")
# Saving takes either key; each map is replaced only by whom it belongs to (references/api.py `_map_rights`).
references_maps_edit_principal = require_section(
    ("procurement", "erpref"), "edit", detail="Справочники правит только закупщик.",
)
operations_view_principal = require_section(
    "operations",
    "view",
    roles=frozenset({"management", "admin"}),
)
# «Сотрудники» (Staff.gs portalStaffGet/Save: portalRealAdmin_): the `staffdir` section and the admin role.
staffdir_principal = require_section(
    "staffdir",
    "edit",
    roles=frozenset({"admin"}),
    detail="Раздел «Сотрудники» — только администратору.",
)


def access_admin_principal(
    principal: Annotated[Principal, Depends(authenticated_principal)],
) -> Principal:
    """«Доступы» (portalAccessGet/Save: portalRealAdmin_): always the administrator, never under the matrix — so the
    matrix cannot close the settings to the people who set it."""
    if "admin" not in principal.roles:
        raise HTTPException(status_code=403, detail="Только администратор.")
    return principal
