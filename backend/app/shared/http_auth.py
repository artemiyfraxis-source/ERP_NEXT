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
):
    def dependency(
        principal: Annotated[Principal, Depends(authenticated_principal)],
    ) -> Principal:
        mode = principal.permissions.get(section)
        allowed = mode == "edit" or (minimum == "view" and mode == "view")
        if roles is not None and not principal.roles.intersection(roles):
            allowed = False
        if not allowed:
            raise HTTPException(status_code=403, detail="Недостаточно прав для раздела")
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
procurement_edit_principal = require_section(
    "procurement",
    "edit",
    roles=frozenset({"buyer", "admin"}),
)
receiving_edit_principal = require_section(
    "receiving",
    "edit",
    roles=frozenset({"receiver", "admin"}),
)
warehouse_edit_principal = require_section(
    "warehouse",
    "edit",
    roles=frozenset({"receiver", "admin"}),
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
