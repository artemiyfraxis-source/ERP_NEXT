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
techcards_view_principal = require_section("techcards", "view")
techcards_edit_principal = require_section(
    "techcards",
    "edit",
    roles=frozenset({"engineer", "admin"}),
)
techcards_admin_principal = require_section(
    "techcards",
    "edit",
    roles=frozenset({"admin"}),
)
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
references_view_principal = require_section("references", "view")
references_edit_principal = require_section(
    "references",
    "edit",
    roles=frozenset({"admin"}),
)
operations_view_principal = require_section(
    "operations",
    "view",
    roles=frozenset({"management", "admin"}),
)
identity_edit_principal = require_section(
    "identity",
    "edit",
    roles=frozenset({"admin"}),
)
