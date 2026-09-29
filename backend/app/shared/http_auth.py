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
    section: str,
    minimum: Literal["view", "edit"],
    *,
    roles: frozenset[str] | None = None,
    detail: str = "Недостаточно прав для раздела",
):
    def dependency(
        principal: Annotated[Principal, Depends(authenticated_principal)],
    ) -> Principal:
        mode = principal.permissions.get(section)
        allowed = mode == "edit" or (minimum == "view" and mode == "view")
        if roles is not None and not principal.roles.intersection(roles):
            allowed = False
        if not allowed:
            raise HTTPException(status_code=403, detail=detail)
        return principal

    return dependency


requests_view_principal = require_section("requests", "view")
requests_edit_principal = require_section("requests", "edit")
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
fulfillment_view_principal = require_section(
    "fulfillment",
    "view",
    roles=frozenset({"engineer", "receiver", "prodhead", "master", "admin"}),
)
fulfillment_edit_principal = require_section(
    "fulfillment",
    "edit",
    roles=frozenset({"engineer", "receiver", "admin"}),
)
fulfillment_handout_principal = require_section(
    "fulfillment",
    "edit",
    roles=frozenset({"receiver", "admin"}),
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
