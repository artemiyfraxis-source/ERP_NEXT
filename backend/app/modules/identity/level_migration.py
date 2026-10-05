"""Перенос «роль → уровень доступа» (owner 04.10.2026, docs/migration/access-levels-plan.md): the pure rules.

Migration 0086 runs a frozen copy of `transfer` (a migration must not import application code); the test
`tests/identity/test_level_migration.py` keeps the two equal. `python -m app.identity_level_report` runs `transfer` and
`verify` over a database read-only, so the owner sees «было / стало» before anything is applied.

The OLD model, frozen here as of 04.10.2026 (commit 4ceb578), because the gates are about to change:
- a person's sections = the role's matrix row overlaid with their personal exceptions (`none` closes); the full-access
  roles (admin, buyer, prodhead, management) have every section on edit and their exceptions are dormant (ignored);
- a route (`require_section`) passes when one of its sections grants the minimum AND the role is on the route's role
  list (a full-access role passes every list); «Доступы» is the admin role alone; «Режим админа» lifts one account's
  session to the admin role.

The NEW model: superadmin/admin pass everything; any other level has the level's matrix row overlaid with personal
exceptions, and a route checks its sections alone (no role lists). The administrator's tools («Доступы», «Сотрудники»,
a stranger's personal card, the library / snapshot / stock imports, the stock journal, deleting a fulfillment request)
are superadmin/admin only. GATES says which new sections each route takes — the contract for switching the gates.

Transfer:
1. level = ROLE_TO_LEVEL[role] (an unknown role — operator); shop_worker = today's installer rule, site_master = role
   master, legacy_role = role; an empty job title gets the role's Russian label.
2. usable(person) = what the person can EFFECTIVELY use today, per new section: the matrix cell restricted by the
   route role lists of that section (WITNESSES). A master with a personal `warehouse = edit` still cannot pass
   `warehouse_edit` (receiver/admin only), so his usable warehouse is view. The split sections take the routes they
   come from: myops ← production_view (edit when it passes: the /my reads and the work writes shared that one gate),
   quality ← production_quality, shipping_goods ← shipping_warehouse. A former full-access role uses everything.
3. level row = the intersection (per section, the lowest mode) of usable() over the level's active members (all members
   when none is active; LEVEL_SEED when the level is empty); superadmin/admin rows are edit everywhere.
4. exceptions(person) = every section where usable() differs from the level row (`none` included), so row ⊕ exceptions
   == usable() and nobody's access changes. superadmin/admin need none.
5. The dormant exceptions of a former full-access role are archived (returned for the audit), never carried over.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from uuid import UUID

from app.shared.notifications import LEGACY_ROLE_AUDIENCES

from .audiences import AUDIENCE_LABELS, AUDIENCE_RULES, in_audience
from .domain import (
    ACCESS_LEVELS,
    FULL_ACCESS_LEVELS,
    LEVEL_SECTION_LABELS,
    LEVEL_SEED,
    can_admin_mode,
)

VIEW, EDIT, NONE = "view", "edit", "none"
_RANK = {NONE: 0, VIEW: 1, EDIT: 2}
_BY_RANK = {1: VIEW, 2: EDIT}

# ── The old model, frozen ────────────────────────────────────────────────────────────────────────────

OLD_FULL_ACCESS_ROLES = frozenset({"admin", "buyer", "prodhead", "management"})
OLD_SECTIONS = (
    "projects", "techcards", "erptctpl", "tcgen", "erpref", "production", "newrequest", "myrequests", "requests",
    "procurement", "receiving", "warehouse", "fulfillment", "handout", "shipping", "staffdir", "operations",
)
OLD_ROLE_LABELS: dict[str, str] = {
    "admin": "Технолог",
    "buyer": "Специалист по снабжению",
    "receiver": "Кладовщик",
    "estimator": "Сметчик",
    "prodhead": "Директор производства",
    "master": "Мастер",
    "management": "Генеральный директор",
    "pm": "Менеджер по проектам",
    "qc": "ОТК",
    "engineer": "Инженер",
    "staff": "Сотрудник",
}
ROLE_TO_LEVEL: dict[str, str] = {
    "management": "superadmin",
    "admin": "admin",
    "prodhead": "tech_lead",
    "buyer": "tech_lead",
    "engineer": "tech_lead",
    "pm": "commercial",
    "estimator": "commercial",
    "master": "foreman",
    "receiver": "foreman",
    "qc": "foreman",
    "staff": "operator",
}
UNKNOWN_ROLE_LEVEL = "operator"

# Route role lists of shared/http_auth.py (+ shipping_editor_principal of shipping/api.py) as of 04.10.2026.
_ADMIN = frozenset({"admin"})
_PROCUREMENT_VIEW = frozenset({"buyer", "receiver", "prodhead", "management", "admin"})
_PROCUREMENT_EDIT = frozenset({"buyer", "admin"})
_RECEIVING_VIEW = frozenset({"receiver", "prodhead", "management", "master", "staff", "admin"})
_RECEIVING_EDIT = frozenset({"receiver", "admin"})
_WAREHOUSE_VIEW = frozenset({"receiver", "buyer", "prodhead", "management", "master", "staff", "admin"})
_WAREHOUSE_EDIT = frozenset({"receiver", "admin"})
_FULFILLMENT_VIEW = frozenset({"engineer", "receiver", "prodhead", "master", "admin"})
_FULFILLMENT_EDIT = frozenset({"engineer", "receiver", "admin"})
_HANDOUT_EDIT = frozenset({"receiver", "admin"})
_PRODUCTION_VIEW = frozenset({"staff", "master", "prodhead", "management", "qc", "admin"})
_PRODUCTION_OVERVIEW = frozenset({"master", "prodhead", "management", "qc", "admin"})
_PRODUCTION_EDIT = frozenset({"master", "admin"})
_PRODUCTION_QUALITY = frozenset({"qc", "admin"})
_SHIPPING_VIEW = frozenset({"pm", "receiver", "master", "prodhead", "management", "admin"})
_SHIPPING_MANAGER = frozenset({"pm", "admin"})
_SHIPPING_WAREHOUSE = frozenset({"receiver", "admin"})
_SHIPPING_EDITOR = frozenset({"pm", "receiver", "admin"})
_OPERATIONS = frozenset({"management", "admin"})


@dataclass(frozen=True)
class Rule:
    """require_section(sections, minimum, roles=…): one of the sections grants `minimum`, and the role is listed."""

    sections: tuple[str, ...]
    minimum: str
    roles: frozenset[str] | None = None  # None — the matrix alone


@dataclass(frozen=True)
class Gate:
    key: str
    label: str
    before: Rule | None  # None — «Доступы»: the admin role itself (access_admin_principal)
    after: tuple[tuple[str, ...], str] | None  # new sections + minimum; None — superadmin/admin only


def _gate(key: str, label: str, sections: tuple[str, ...], minimum: str, roles: frozenset[str] | None = None,
          after: tuple[tuple[str, ...], str] | None | bool = True) -> Gate:
    """`after=True` — the same sections and minimum; None — superadmin/admin only; a tuple — new sections."""
    rule = Rule(sections, minimum, roles)
    return Gate(key, label, rule, (sections, minimum) if after is True else after)


# Every route gate of today, and what it becomes. The key is the dependency name without `_principal`; two rows share
# production_view: its reads move to myops view, its writes (start / pause / submit) to myops edit.
GATES: tuple[Gate, ...] = (
    _gate("requests_view", "Заявки — список", ("requests",), VIEW),
    _gate("requests_form", "Форма заявки — справочные данные", ("newrequest", "requests"), VIEW),
    _gate("newrequest", "Оформить заявку", ("newrequest",), EDIT),
    _gate("myrequests", "Мои заявки", ("myrequests",), VIEW),
    _gate("techcards_router", "Техкарты — вход в раздел", ("techcards", "erptctpl", "tcgen"), VIEW),
    _gate("techcards_view", "Техкарты проектов — просмотр", ("techcards",), VIEW),
    _gate("techcards_edit", "Техкарты проектов — правка", ("techcards",), EDIT),
    _gate("projects_view", "Проекты — просмотр", ("projects",), VIEW),
    _gate("projects_edit", "Проекты — правка", ("projects",), EDIT),
    _gate("kitset_tiles", "Плитки проектов (экран мастера)", ("projects", "production"), VIEW,
          after=(("projects", "production", "myops"), VIEW)),
    _gate("tctemplates_view", "Шаблоны техкарт — просмотр", ("erptctpl",), VIEW),
    _gate("tctemplates_edit", "Шаблоны техкарт — правка", ("erptctpl",), EDIT),
    _gate("tctemplates_admin", "Загрузка библиотеки шаблонов", ("erptctpl",), EDIT, _ADMIN, after=None),
    _gate("tcgen", "Генератор техкарт", ("tcgen",), VIEW),
    _gate("procurement_view", "Лист закупки — просмотр", ("procurement",), VIEW, _PROCUREMENT_VIEW),
    _gate("procurement_edit", "Лист закупки — правка", ("procurement",), EDIT, _PROCUREMENT_EDIT),
    _gate("receiving_view", "Приёмка — просмотр", ("receiving",), VIEW, _RECEIVING_VIEW),
    _gate("receiving_edit", "Приёмка — правка", ("receiving",), EDIT, _RECEIVING_EDIT),
    _gate("warehouse_view", "Склад — просмотр", ("warehouse",), VIEW, _WAREHOUSE_VIEW),
    _gate("warehouse_edit", "Склад — правка", ("warehouse",), EDIT, _WAREHOUSE_EDIT),
    _gate("warehouse_admin", "Импорт таблицы остатков", ("warehouse",), EDIT, _ADMIN, after=None),
    _gate("warehouse_journal", "Журнал склада", ("warehouse",), VIEW, _ADMIN, after=None),
    _gate("fulfillment_view", "Комплектация и выдача — список", ("fulfillment", "handout"), VIEW, _FULFILLMENT_VIEW),
    _gate("fulfillment_edit", "Комплектация — правка", ("fulfillment",), EDIT, _FULFILLMENT_EDIT),
    _gate("fulfillment_admin", "Удаление заявки на комплектацию", ("fulfillment",), EDIT, _ADMIN, after=None),
    _gate("fulfillment_handout", "Выдача со списанием — правка", ("handout",), EDIT, _HANDOUT_EDIT),
    _gate("production_view", "Мои задания — просмотр", ("production",), VIEW, _PRODUCTION_VIEW,
          after=(("myops",), VIEW)),
    _gate("production_view_work", "Мои задания — старт, пауза, сдача", ("production",), VIEW, _PRODUCTION_VIEW,
          after=(("myops",), EDIT)),
    _gate("production_overview", "Производство — просмотр", ("production",), VIEW, _PRODUCTION_OVERVIEW),
    _gate("production_edit", "Производство — правка", ("production",), EDIT, _PRODUCTION_EDIT),
    _gate("production_quality", "ОТК — правка", ("production",), EDIT, _PRODUCTION_QUALITY,
          after=(("quality",), EDIT)),
    _gate("shipping_view", "Отгрузка — просмотр", ("shipping",), VIEW, _SHIPPING_VIEW,
          after=(("shipping", "shipping_goods"), VIEW)),
    _gate("shipping_manager", "Отгрузка — действия менеджера", ("shipping",), EDIT, _SHIPPING_MANAGER),
    _gate("shipping_warehouse", "Отгрузка — действия склада", ("shipping",), EDIT, _SHIPPING_WAREHOUSE,
          after=(("shipping_goods",), EDIT)),
    _gate("shipping_editor", "Отгрузка — упаковка, строки, УПД", ("shipping",), EDIT, _SHIPPING_EDITOR,
          after=(("shipping", "shipping_goods"), EDIT)),
    _gate("references_view", "Справочник — просмотр", ("erpref",), VIEW),
    _gate("references_edit", "Справочник — правка", ("erpref",), EDIT),
    _gate("references_import", "Загрузка снимка баз", ("erpref",), EDIT, _ADMIN, after=None),
    _gate("references_maps_view", "Карты «ссылка → поставщик», «изготовитель → страна» — просмотр",
          ("erpref", "procurement"), VIEW),
    _gate("references_maps_edit", "Карты поставщиков и стран — сохранение", ("procurement", "erpref"), EDIT),
    # references/api.py `_map_rights`: the supplier map is replaced only with `procurement` edit, read off the matrix.
    _gate("references_suppliers", "Карта «ссылка → поставщик» — правка", ("procurement",), EDIT),
    _gate("operations_view", "Системный журнал", ("operations",), VIEW, _OPERATIONS),
    _gate("staffdir", "Сотрудники", ("staffdir",), EDIT, _ADMIN, after=None),
    # workflows/employee_card_api.py: another employee's card needs `staffdir` view.
    _gate("staffdir_view", "Чужая личная карточка", ("staffdir",), VIEW, after=None),
    Gate("access_admin", "Доступы", None, None),
)
GATE_BY_KEY: dict[str, Gate] = {gate.key: gate for gate in GATES}

# usable(): which old routes decide each new section (view rule, edit rule). A section missing here has no role list on
# any route, so its matrix cell is what the person can use.
WITNESSES: dict[str, tuple[Rule, Rule]] = {
    "procurement": (Rule(("procurement",), VIEW, _PROCUREMENT_VIEW), Rule(("procurement",), EDIT, _PROCUREMENT_EDIT)),
    "receiving": (Rule(("receiving",), VIEW, _RECEIVING_VIEW), Rule(("receiving",), EDIT, _RECEIVING_EDIT)),
    "warehouse": (Rule(("warehouse",), VIEW, _WAREHOUSE_VIEW), Rule(("warehouse",), EDIT, _WAREHOUSE_EDIT)),
    "fulfillment": (Rule(("fulfillment",), VIEW, _FULFILLMENT_VIEW), Rule(("fulfillment",), EDIT, _FULFILLMENT_EDIT)),
    "handout": (Rule(("handout",), VIEW, _FULFILLMENT_VIEW), Rule(("handout",), EDIT, _HANDOUT_EDIT)),
    "production": (Rule(("production",), VIEW, _PRODUCTION_OVERVIEW), Rule(("production",), EDIT, _PRODUCTION_EDIT)),
    "myops": (Rule(("production",), VIEW, _PRODUCTION_VIEW), Rule(("production",), VIEW, _PRODUCTION_VIEW)),
    "quality": (Rule(("production",), EDIT, _PRODUCTION_QUALITY), Rule(("production",), EDIT, _PRODUCTION_QUALITY)),
    "shipping": (Rule(("shipping",), VIEW, _SHIPPING_VIEW), Rule(("shipping",), EDIT, _SHIPPING_MANAGER)),
    "shipping_goods": (Rule(("shipping",), EDIT, _SHIPPING_WAREHOUSE), Rule(("shipping",), EDIT, _SHIPPING_WAREHOUSE)),
    "operations": (Rule(("operations",), VIEW, _OPERATIONS), Rule(("operations",), EDIT, _OPERATIONS)),
}

# Notification audiences: the role keys modules addressed before audiences (shared/notifications.py
# LEGACY_ROLE_AUDIENCES; the owner, security and technologist alerts all went to "admin"), each with the audience it
# resolves to now (identity/audiences.py: a section's editors; None — superadmin + admin).
NOTIFICATION_AUDIENCES: tuple[tuple[str, str, tuple[str, str] | None], ...] = tuple(
    (role, AUDIENCE_LABELS[audience], AUDIENCE_RULES[audience]) for role, audience in LEGACY_ROLE_AUDIENCES.items()
)


# ── Data ─────────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Person:
    id: UUID
    login: str
    name: str
    email: str = ""
    department: str = ""
    role: str = "staff"
    site: str | None = None
    active: bool = True
    job_title: str = ""
    # The report's «who can still log in»: a credential row (password) and a PIN.
    has_credential: bool = True
    has_pin: bool = False


@dataclass(frozen=True)
class Snapshot:
    people: tuple[Person, ...]
    role_matrix: Mapping[str, Mapping[str, str]]  # identity_role_access: role → section → mode (incl. `none`)
    user_access: Mapping[UUID, Mapping[str, str]]  # identity_user_access: personal exceptions (incl. `none`)


@dataclass(frozen=True)
class Assignment:
    access_level: str
    shop_worker: bool
    site_master: bool
    legacy_role: str
    job_title: str


@dataclass(frozen=True)
class Transfer:
    people: dict[UUID, Assignment]
    levels: dict[str, dict[str, str]]  # the level matrix: level → section → view|edit (an absent cell is closed)
    exceptions: dict[UUID, dict[str, str]]  # compensating personal exceptions: section → none|view|edit
    archived: dict[UUID, dict[str, str]]  # dormant exceptions of former full-access roles, for the audit
    usable: dict[UUID, dict[str, str]]  # what each person can use today, per new section

    def plain(self) -> dict:
        """The form migration 0086's frozen copy returns, for comparing the two."""
        return {
            "people": {
                str(user_id): {
                    "access_level": item.access_level, "shop_worker": item.shop_worker,
                    "site_master": item.site_master, "legacy_role": item.legacy_role, "job_title": item.job_title,
                }
                for user_id, item in self.people.items()
            },
            "levels": {level: dict(row) for level, row in self.levels.items()},
            "exceptions": {str(user_id): dict(row) for user_id, row in self.exceptions.items() if row},
            "archived": {str(user_id): dict(row) for user_id, row in self.archived.items() if row},
        }


# ── Rules ────────────────────────────────────────────────────────────────────────────────────────────


def level_for_role(role: str | None) -> str:
    return ROLE_TO_LEVEL.get(str(role or "").strip(), UNKNOWN_ROLE_LEVEL)


def shop_worker_rule(role: str | None, site: str | None, department: str | None) -> bool:
    """Today's installer rule (production Operator.installer, employee_card): role staff with a site or a «монтаж»
    department. The flag drops the «active» part — that is the person's own switch."""
    return str(role or "").strip() == "staff" and (bool(site) or "монтаж" in str(department or "").lower())


def site_master_rule(role: str | None) -> bool:
    return str(role or "").strip() == "master"


def followed_flags(stored, role: str | None, site: str | None, department: str | None) -> tuple[bool, bool]:
    """(shop_worker, site_master) after a change of the role, site or department that did not set the flags itself
    (the old «Сотрудники» screen, the staff import, a department rename). `stored` — the row or the user before the
    change (role, site, department, shop_worker, site_master). A flag that agrees with the rule for the old values
    follows the rule to the new ones; a flag set against the rule (by hand) stays."""
    shop_worker = bool(stored.shop_worker)
    if shop_worker == shop_worker_rule(stored.role, stored.site, stored.department):
        shop_worker = shop_worker_rule(role, site, department)
    site_master = bool(stored.site_master)
    if site_master == site_master_rule(stored.role):
        site_master = site_master_rule(role)
    return shop_worker, site_master


def prefilled_job_title(job_title: str | None, role: str | None) -> str:
    current = str(job_title or "")
    return current if current.strip() else OLD_ROLE_LABELS.get(str(role or "").strip(), "")


def _overlay(base: Mapping[str, str], overrides: Mapping[str, str]) -> dict[str, str]:
    result = {section: mode for section, mode in base.items() if mode in (VIEW, EDIT)}
    for section, mode in overrides.items():
        if mode in (VIEW, EDIT):
            result[section] = mode
        else:
            result.pop(section, None)
    return result


def old_permissions(role: str, role_row: Mapping[str, str], exceptions: Mapping[str, str]) -> dict[str, str]:
    """The old `_principal`: a full-access role has every section on edit and its exceptions are ignored."""
    if role in OLD_FULL_ACCESS_ROLES:
        return {section: EDIT for section in OLD_SECTIONS}
    return _overlay(_overlay({}, role_row), exceptions)


def passes_rule(role: str, permissions: Mapping[str, str], rule: Rule) -> bool:
    """The old `require_section`."""
    modes = [permissions.get(section) for section in rule.sections]
    allowed = EDIT in modes or (rule.minimum == VIEW and VIEW in modes)
    if rule.roles is not None and role not in rule.roles and role not in OLD_FULL_ACCESS_ROLES:
        allowed = False
    return allowed


def passes_before(role: str, permissions: Mapping[str, str], gate: Gate) -> bool:
    if gate.before is None:
        return role == "admin"
    return passes_rule(role, permissions, gate.before)


def usable(role: str, permissions: Mapping[str, str]) -> dict[str, str]:
    """What the person can effectively use today, per new section (step 2 of the module docstring)."""
    if role in OLD_FULL_ACCESS_ROLES:
        return {section: EDIT for section in LEVEL_SECTION_LABELS}
    result: dict[str, str] = {}
    for section in LEVEL_SECTION_LABELS:
        witness = WITNESSES.get(section)
        if witness is None:
            mode = permissions.get(section)
        elif passes_rule(role, permissions, witness[1]):
            mode = EDIT
        elif passes_rule(role, permissions, witness[0]):
            mode = VIEW
        else:
            mode = None
        if mode in (VIEW, EDIT):
            result[section] = mode
    return result


def _intersection(rows: Iterable[Mapping[str, str]]) -> dict[str, str]:
    rows = list(rows)
    result: dict[str, str] = {}
    for section in LEVEL_SECTION_LABELS:
        lowest = min(_RANK[row.get(section, NONE)] for row in rows)
        if lowest:
            result[section] = _BY_RANK[lowest]
    return result


def _difference(target: Mapping[str, str], row: Mapping[str, str]) -> dict[str, str]:
    return {
        section: target.get(section, NONE)
        for section in LEVEL_SECTION_LABELS
        if target.get(section, NONE) != row.get(section, NONE)
    }


def transfer(snapshot: Snapshot) -> Transfer:
    people = sorted(snapshot.people, key=lambda person: str(person.id))
    assignments: dict[UUID, Assignment] = {}
    usable_by_person: dict[UUID, dict[str, str]] = {}
    archived: dict[UUID, dict[str, str]] = {}
    for person in people:
        role = str(person.role or "").strip()
        assignments[person.id] = Assignment(
            access_level=level_for_role(role),
            shop_worker=shop_worker_rule(role, person.site, person.department),
            site_master=site_master_rule(role),
            legacy_role=role,
            job_title=prefilled_job_title(person.job_title, role),
        )
        exceptions = snapshot.user_access.get(person.id, {})
        permissions = old_permissions(role, snapshot.role_matrix.get(role, {}), exceptions)
        usable_by_person[person.id] = usable(role, permissions)
        if role in OLD_FULL_ACCESS_ROLES and exceptions:
            archived[person.id] = dict(sorted(exceptions.items()))

    levels: dict[str, dict[str, str]] = {}
    for level in ACCESS_LEVELS:
        if level in FULL_ACCESS_LEVELS:
            levels[level] = {section: EDIT for section in LEVEL_SECTION_LABELS}
            continue
        members = [person for person in people if assignments[person.id].access_level == level]
        pool = [person for person in members if person.active] or members
        if pool:
            levels[level] = _intersection(usable_by_person[person.id] for person in pool)
        else:
            seed = LEVEL_SEED.get(level, {})
            levels[level] = {section: seed[section].value for section in LEVEL_SECTION_LABELS if section in seed}

    exceptions_by_person = {
        person.id: (
            {} if assignments[person.id].access_level in FULL_ACCESS_LEVELS
            else _difference(usable_by_person[person.id], levels[assignments[person.id].access_level])
        )
        for person in people
    }
    return Transfer(
        people=assignments, levels=levels, exceptions=exceptions_by_person, archived=archived,
        usable=usable_by_person,
    )


def new_permissions(level: str, level_row: Mapping[str, str], exceptions: Mapping[str, str]) -> dict[str, str]:
    """The new model: superadmin/admin edit everywhere; anyone else — the level row overlaid with the exceptions."""
    if level in FULL_ACCESS_LEVELS:
        return {section: EDIT for section in LEVEL_SECTION_LABELS}
    return _overlay(level_row, exceptions)


def passes_after(level: str, permissions: Mapping[str, str], gate: Gate) -> bool:
    if level in FULL_ACCESS_LEVELS:
        return True
    if gate.after is None:
        return False
    sections, minimum = gate.after
    modes = [permissions.get(section) for section in sections]
    return EDIT in modes or (minimum == VIEW and VIEW in modes)


# ── Verification («было / стало») ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GateCheck:
    gate: Gate
    before: bool
    after: bool

    @property
    def changed(self) -> bool:
        return self.before != self.after

    @property
    def planned(self) -> bool:
        """A change the plan makes on purpose: the tool is superadmin/admin only from now on."""
        return self.changed and self.gate.after is None


@dataclass(frozen=True)
class PersonCheck:
    person: Person
    assignment: Assignment
    admin_mode: bool  # the row of «Режим админа» (the session lifted to admin / to level admin)
    gates: tuple[GateCheck, ...]

    @property
    def mismatches(self) -> tuple[GateCheck, ...]:
        return tuple(check for check in self.gates if check.changed and not check.planned)

    @property
    def planned(self) -> tuple[GateCheck, ...]:
        return tuple(check for check in self.gates if check.planned)


@dataclass(frozen=True)
class Audience:
    key: str
    label: str
    rule: str
    before: tuple[UUID, ...]
    after: tuple[UUID, ...]


@dataclass(frozen=True)
class Verification:
    people: tuple[PersonCheck, ...]
    audiences: tuple[Audience, ...]
    # Who can still log in and return rights: active, with a password or a PIN.
    admins_before: tuple[UUID, ...]  # role admin: «Доступы» today
    admins_after: tuple[UUID, ...]  # superadmin / admin

    @property
    def mismatches(self) -> tuple[tuple[PersonCheck, GateCheck], ...]:
        return tuple((row, check) for row in self.people for check in row.mismatches)

    @property
    def planned(self) -> tuple[tuple[PersonCheck, GateCheck], ...]:
        return tuple((row, check) for row in self.people for check in row.planned)


def person_permissions_after(result: Transfer, user_id: UUID) -> dict[str, str]:
    level = result.people[user_id].access_level
    return new_permissions(level, result.levels.get(level, {}), result.exceptions.get(user_id, {}))


def verify(snapshot: Snapshot, result: Transfer) -> Verification:
    """Every person × every gate, before and after, plus the notification audiences and the administrators."""
    people = sorted(snapshot.people, key=lambda person: (not person.active, person.name.casefold(), person.login))
    rows: list[PersonCheck] = []
    for person in people:
        role = str(person.role or "").strip()
        assignment = result.people[person.id]
        before = old_permissions(role, snapshot.role_matrix.get(role, {}), snapshot.user_access.get(person.id, {}))
        after = person_permissions_after(result, person.id)
        rows.append(PersonCheck(person, assignment, False, tuple(
            GateCheck(gate, passes_before(role, before, gate), passes_after(assignment.access_level, after, gate))
            for gate in GATES
        )))
        if can_admin_mode(person.email):
            # «Режим админа»: before — the role admin with its own row; after — the level admin.
            lifted = old_permissions("admin", snapshot.role_matrix.get("admin", {}), {})
            rows.append(PersonCheck(person, assignment, True, tuple(
                GateCheck(gate, passes_before("admin", lifted, gate), passes_after("admin", {}, gate))
                for gate in GATES
            )))

    active = [person for person in people if person.active]
    audiences = []
    for key, label, rule in NOTIFICATION_AUDIENCES:
        before = tuple(person.id for person in active if str(person.role or "").strip() == key)
        if rule is None:
            after = tuple(person.id for person in active if result.people[person.id].access_level in FULL_ACCESS_LEVELS)
            described = "суперадминистраторы и администраторы"
        else:
            section, mode = rule
            after = tuple(
                person.id for person in active
                if in_audience(
                    rule, result.people[person.id].access_level,
                    result.levels.get(result.people[person.id].access_level, {}), result.exceptions.get(person.id, {}),
                )
            )
            described = (
                f"«{LEVEL_SECTION_LABELS[section]}» — {'правка' if mode == EDIT else 'просмотр'}"
                " (superadmin и admin — только при личном исключении на раздел)"
            )
        audiences.append(Audience(key, label, described, before, after))

    can_log_in = [person for person in active if person.has_credential or person.has_pin]
    return Verification(
        people=tuple(rows),
        audiences=tuple(audiences),
        admins_before=tuple(person.id for person in can_log_in if str(person.role or "").strip() == "admin"),
        admins_after=tuple(
            person.id for person in can_log_in if result.people[person.id].access_level in FULL_ACCESS_LEVELS
        ),
    )
