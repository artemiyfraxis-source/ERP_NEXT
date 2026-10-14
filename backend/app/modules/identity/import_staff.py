"""STAFF-IMPORT CLI: the SUPPLYMAP staff directory → ERP employees, with the PIN of each printed for the owner.

    ERP_DATABASE_URL=... python -m app.modules.identity.import_staff --dry-run
    ERP_DATABASE_URL=... python -m app.modules.identity.import_staff --apply

Source (git snapshots, read-only — the live directory is ScriptProperties `staff_directory`, unreachable from here):
  · ``SUPPLYMAP/Config.gs`` ``STAFF_BY_EMAIL`` — name + department (+ a first-run role);
  · ``SUPPLYMAP/_local/access-backup-2026-09-01.json`` — the role each employee actually has now.
The SUPPLYMAP snapshot is named explicitly — ``--source`` or ``SUPPLYMAP_SOURCE_REPOSITORY`` (the server sets it to
``/opt/supplymap``); there is no sibling-checkout default (owner 05.10: staff live in the ERP directory now, this is a
one-off import). E-mail is the key; the ERP id is derived from it, so re-running touches the same rows.

PINs: SUPPLYMAP keeps them as irreversible hashes, so the Google PINs cannot be carried over. Each employee is
given a fresh PIN here and the full name → login → PIN table is printed once — that is «все пины мне» (owner,
2026-09-29). Login is by name + PIN (the /login-staff list → /pin-login), no e-mail needed.

Exit code: 0 — done (or dry-run); 2 — the source cannot be read or ERP_DATABASE_URL is not set.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from app.database import build_engine, build_identity_uow_factory

from .application import UnitOfWork
from .domain import (
    LEVEL_LABELS,
    IdentityUser,
    hash_password,
    hash_pin,
    new_pin,
    normalize_department,
    normalize_email,
    normalize_login,
    normalize_name,
    normalize_role,
    normalize_site,
)
from .level_migration import shop_worker_rule, site_master_rule

# ID-4/ID-8: the ERP id of an imported employee is fixed by e-mail, so the import is idempotent.
STAFF_ID_NAMESPACE = "fraxis-erp:identity:staff:"

CONFIG_RELATIVE = Path("Config.gs")
ACCESS_BACKUP_RELATIVE = Path("_local") / "access-backup-2026-09-01.json"

# Test entries in the Google directory, not employees (mirror scripts/demo/drop_test_staff.py). «Вася Пупкин»
# (artemiyfraxis@gmail.com) is the owner's test account; the owner renamed it «Артемий» in the demo, so the login list
# showed two «Артемий» (owner 2026-09-29 «и без отдела … оставь одну запись»). The import never creates them, and
# deactivates such a row if it is still active, so only the real «Артемий Васильев» (tit1963yt) remains.
TEST_EMAILS = frozenset({"artemiyfraxis@gmail.com"})

# SUPPLYMAP role keys → ERP access levels (docs/migration/access-levels-plan.md: management → Суперадминистратор,
# admin → Администратор, prodhead / buyer → Технический руководитель, master / receiver → Бригадир, staff → Оператор).
# A role missing here lands on «staff» → Оператор, not on a silent wider level. The flags follow the plan: «Мастер
# площадки» = master, «Монтажник» = staff with a site or a «монтаж» department. The SUPPLYMAP role itself is kept as
# the legacy role (one release).
ROLE_MAP = {
    "admin": "admin",
    "buyer": "tech_lead",
    "receiver": "foreman",
    "prodhead": "tech_lead",
    "master": "foreman",
    "management": "superadmin",
    "staff": "operator",
}
# Office roles that span both sites (directors, технолог, снабжение): no participok is read from their department.
CROSS_SITE_ROLES = frozenset({"admin", "buyer", "prodhead", "management"})


class StaffImportError(RuntimeError):
    """The SUPPLYMAP snapshot cannot be read."""


@dataclass(frozen=True)
class SourceEmployee:
    email: str
    name: str
    department: str
    role: str  # the SUPPLYMAP role (a key of ROLE_MAP), stored as the legacy role
    site: str | None = None  # ЦАР / ЖУК; None — office roles (directors, технолог, снабжение) work across sites

    @property
    def access_level(self) -> str:
        return ROLE_MAP.get(self.role, ROLE_MAP["staff"])

    @property
    def shop_worker(self) -> bool:
        return shop_worker_rule(self.role, self.site, self.department)

    @property
    def site_master(self) -> bool:
        return site_master_rule(self.role)


# Монтажники (installers) and the Жуковский (ЖУК) participok, owner 2026-09-29: they are not in the SUPPLYMAP snapshot
# (new people), so the import adds them itself, on the same run and with a PIN each — the master assigns operations to
# them, and the operator list is every active employee (production has no role filter), so the role is «Сотрудник»
# (staff); department «Монтажный отдел» (0104, was «Монтаж») puts them on the master board (MasterBoard.installers).
# `site` splits the board by participok: ЦАР (Москва, master's default) and ЖУК (Раменское/Жуковский, master Степан — his board shows only ЖУК).
# Logins are transliterated; there are no real mailboxes yet (login is by name + PIN), the administrator can edit them.
ADDITIONAL_STAFF: list[SourceEmployee] = [
    # ЦАР — the nine installers of the Moscow participok.
    SourceEmployee(email="rybkina.olga@fraxis.local", name="Рыбкина Ольга", department="Монтажный отдел", role="staff", site="ЦАР"),
    SourceEmployee(email="epifanov.ilya@fraxis.local", name="Епифанов Илья", department="Монтажный отдел", role="staff", site="ЦАР"),
    SourceEmployee(email="popov.ignat@fraxis.local", name="Попов Игнат", department="Монтажный отдел", role="staff", site="ЦАР"),
    SourceEmployee(email="bogorodskaya.elina@fraxis.local", name="Богородская Элина", department="Монтажный отдел", role="staff", site="ЦАР"),
    SourceEmployee(email="makarova.polina@fraxis.local", name="Макарова Полина", department="Монтажный отдел", role="staff", site="ЦАР"),
    SourceEmployee(email="naumova.elena@fraxis.local", name="Наумова Елена", department="Монтажный отдел", role="staff", site="ЦАР"),
    SourceEmployee(email="puzanov.aleksandr@fraxis.local", name="Пузанов Александр", department="Монтажный отдел", role="staff", site="ЦАР"),
    SourceEmployee(email="dumanskaya.anna@fraxis.local", name="Думанская Анна", department="Монтажный отдел", role="staff", site="ЦАР"),
    SourceEmployee(email="shilov.artem@fraxis.local", name="Шилов Артем", department="Монтажный отдел", role="staff", site="ЦАР"),
    # ЖУК — Жуковский participok: Степан is its master (distributes on his own board), the rest are installers.
    SourceEmployee(email="stepan.zhuk@fraxis.local", name="Степан", department="Монтажный отдел", role="master", site="ЖУК"),
    SourceEmployee(email="zinger.anton@fraxis.local", name="Зингер Антон", department="Монтажный отдел", role="staff", site="ЖУК"),
    SourceEmployee(email="naumov.aleksandr@fraxis.local", name="Наумов Александр", department="Монтажный отдел", role="staff", site="ЖУК"),
    SourceEmployee(email="evseev.anton@fraxis.local", name="Евсеев Антон", department="Монтажный отдел", role="staff", site="ЖУК"),
    SourceEmployee(email="bezmenov.nikita@fraxis.local", name="Безменов Никита", department="Монтажный отдел", role="staff", site="ЖУК"),
]


def staff_id(email: str) -> UUID:
    return uuid5(NAMESPACE_URL, STAFF_ID_NAMESPACE + normalize_email(email))


def _parse_config_staff(text: str) -> dict[str, dict[str, str]]:
    """Pull ``STAFF_BY_EMAIL`` out of Config.gs: one ``'email': { name: '…', dept: '…', role: '…' },`` per line."""
    block = re.search(r"var\s+STAFF_BY_EMAIL\s*=\s*\{(.*?)\n\};", text, re.DOTALL)
    if not block:
        raise StaffImportError("STAFF_BY_EMAIL не найден в Config.gs")
    out: dict[str, dict[str, str]] = {}
    row = re.compile(r"^\s*'([^']+)'\s*:\s*\{(.+?)\}", re.MULTILINE)
    field = lambda body, key: (re.search(rf"\b{key}\s*:\s*'([^']*)'", body) or [None, ""])[1]
    for match in row.finditer(block.group(1)):
        email, body = match.group(1), match.group(2)
        out[normalize_email(email)] = {
            "name": field(body, "name"),
            "dept": field(body, "dept"),
            "role": field(body, "role"),
        }
    return out


def _load_access_roles(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        normalize_email(row["email"]): str(row.get("role") or "")
        for row in data.get("staff", [])
        if row.get("email")
    }


def _surname_first(name: str) -> str:
    """SUPPLYMAP stores «Имя Фамилия»; the login list wants «Фамилия Имя» so it sorts by surname (owner 2026-09-29).
    A two-word name is swapped; anything else (one word like «Артемий», or three words) is left alone."""
    parts = name.split()
    return f"{parts[1]} {parts[0]}" if len(parts) == 2 else name


def _infer_site(role: str, department: str) -> str | None:
    """Site for a SUPPLYMAP row: office roles (directors, технолог, снабжение) span sites (None); otherwise read
    the participok from the department text — «Жуковский»/ЖУК → ЖУК, «Царицыно»/ЦАР → ЦАР, else None."""
    if role in CROSS_SITE_ROLES:
        return None
    text = department.casefold()
    if "жук" in text or "жуковск" in text:
        return "ЖУК"
    if "цар" in text or "царицын" in text:
        return "ЦАР"
    return None


def read_source(source: Path) -> list[SourceEmployee]:
    config_path = source / CONFIG_RELATIVE
    if not config_path.is_file():
        raise StaffImportError(f"нет {config_path} — укажи --source или SUPPLYMAP_SOURCE_REPOSITORY")
    directory = _parse_config_staff(config_path.read_text(encoding="utf-8"))
    roles = _load_access_roles(source / ACCESS_BACKUP_RELATIVE)
    people: list[SourceEmployee] = []
    for email, info in directory.items():
        raw_role = roles.get(email) or info.get("role") or "staff"  # access backup is fresher than the seed's role
        role = raw_role if raw_role in ROLE_MAP else "staff"
        department = info.get("dept", "")
        people.append(SourceEmployee(
            email=email,
            name=_surname_first(info.get("name", "") or email),
            department=department,
            role=role,
            site=_infer_site(role, department),
        ))
    people.sort(key=lambda person: person.name.casefold())
    return people


def all_source_staff(source: Path) -> list[SourceEmployee]:
    """Everyone the import brings in: the SUPPLYMAP directory plus ADDITIONAL_STAFF (installers), deduped by e-mail —
    a SUPPLYMAP row wins over an addition with the same e-mail."""
    people = read_source(source)
    seen = {normalize_email(person.email) for person in people}
    people.extend(person for person in ADDITIONAL_STAFF if normalize_email(person.email) not in seen)
    people.sort(key=lambda person: person.name.casefold())
    return people


@dataclass
class ImportedRow:
    name: str
    login: str
    role: str  # the legacy role
    pin: str | None  # None → skipped (already present, PINs kept)
    action: str  # "создан" / "обновлён" / "пропущен (уже есть)"
    access_level: str = ""


def import_staff(
    people: list[SourceEmployee], uow: UnitOfWork, now: datetime, *, apply: bool, reset_pins: bool,
) -> list[ImportedRow]:
    rows: list[ImportedRow] = []
    with uow:
        existing = {user.email: user for user in uow.identity.all_users()}
        for person in people:
            email = normalize_email(person.email)
            login = normalize_login(email)
            name = normalize_name(person.name)
            department = normalize_department(person.department)
            role = normalize_role(person.role)
            site = normalize_site(person.site)
            current = existing.get(email)
            if email in TEST_EMAILS:
                # Not an employee — never create; deactivate the row if a run already made it (so it leaves the list).
                if current is not None and current.active:
                    if apply:
                        uow.identity.update_user(replace(current, active=False))
                    rows.append(ImportedRow(name=current.name, login=current.login, role=current.role, pin=None,
                                            action="тестовый — отключён", access_level=current.access_level))
                else:
                    rows.append(ImportedRow(name=name, login=login, role=role, pin=None, action="тестовый — пропущен",
                                            access_level=person.access_level))
            elif current is None:
                pin = new_pin()
                if apply:
                    user = IdentityUser(
                        id=staff_id(email), login=login, email=email, name=name,
                        department=department, role=role, site=site, active=True,
                        access_level=person.access_level,
                        shop_worker=shop_worker_rule(role, site, department), site_master=site_master_rule(role),
                    )
                    # No password: only the PIN opens the door (SUPPLYMAP had PINs only).
                    uow.identity.add_user(user, hash_password(secrets.token_urlsafe(32)), {}, hash_pin(pin))
                rows.append(ImportedRow(name=name, login=login, role=role, pin=pin, action="создан",
                                        access_level=person.access_level))
            elif reset_pins:
                pin = new_pin()
                if apply:
                    uow.identity.set_pin(current.id, hash_pin(pin))
                rows.append(ImportedRow(name=current.name, login=current.login, role=current.role, pin=pin,
                                        action="PIN перевыдан", access_level=current.access_level))
            elif current.name != name or current.department != department or current.site != site:
                # Already imported, but the source shape moved: name to «Фамилия Имя», the participok/department (ЖУК
                # added, installers → «Монтажный отдел»). Bring name, department and site to the source; keep the PIN,
                # role and level (the administrator may have changed the level since).
                if apply:
                    uow.identity.update_user(replace(current, name=name, department=department, site=site))
                rows.append(ImportedRow(name=name, login=current.login, role=current.role, pin=None,
                                        action="обновлён (имя/отдел/площадка)", access_level=current.access_level))
            else:
                rows.append(ImportedRow(name=current.name, login=current.login, role=current.role, pin=None,
                                        action="пропущен (уже есть)", access_level=current.access_level))
        if apply:
            uow.commit()
    return rows


def _print_report(rows: list[ImportedRow], *, apply: bool) -> None:
    head = "ПЕРЕНОС СОТРУДНИКОВ" if apply else "ПЕРЕНОС СОТРУДНИКОВ (dry-run, база не тронута)"
    print(head)
    print(f"{'ФИО':<24} {'Логин':<32} {'Уровень доступа':<26} PIN")
    print("-" * 98)
    for row in rows:
        pin = row.pin if row.pin is not None else "—"
        level = LEVEL_LABELS.get(row.access_level, row.access_level or row.role)
        print(f"{row.name:<24} {row.login:<32} {level:<26} {pin:<8} {row.action}")
    created = sum(1 for row in rows if row.action == "создан")
    reissued = sum(1 for row in rows if row.action == "PIN перевыдан")
    updated = sum(1 for row in rows if row.action.startswith("обновлён"))
    deactivated = sum(1 for row in rows if row.action == "тестовый — отключён")
    skipped = sum(1 for row in rows if row.action.startswith("пропущен") or row.action == "тестовый — пропущен")
    print("-" * 98)
    print(f"создано: {created}, PIN перевыдано: {reissued}, обновлено: {updated}, "
          f"тестовых отключено: {deactivated}, пропущено: {skipped}")
    if any(row.pin for row in rows):
        print("Раздайте PIN сотрудникам — вход по имени и PIN. Пароль не нужен.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Перенос сотрудников SUPPLYMAP → ERP с выдачей PIN")
    parser.add_argument("--source", type=Path, default=None, help="путь к репозиторию SUPPLYMAP")
    parser.add_argument("--dry-run", action="store_true", help="только показать (поведение по умолчанию)")
    parser.add_argument("--apply", action="store_true", help="записать в базу (по умолчанию — dry-run)")
    parser.add_argument("--reset-pins", action="store_true", help="перевыдать PIN и уже заведённым сотрудникам")
    args = parser.parse_args(argv)

    env_source = os.getenv("SUPPLYMAP_SOURCE_REPOSITORY", "").strip()
    source = args.source or (Path(env_source) if env_source else None)
    if source is None:
        print("Источник не задан: укажи --source (снимок SUPPLYMAP) или SUPPLYMAP_SOURCE_REPOSITORY", file=sys.stderr)
        return 2
    try:
        people = all_source_staff(source)
    except StaffImportError as error:
        print(f"Источник не прочитан: {error}", file=sys.stderr)
        return 2
    if not people:
        print("В источнике нет сотрудников.", file=sys.stderr)
        return 2

    database_url = os.getenv("ERP_DATABASE_URL", "").strip()
    if not database_url:
        print("ERP_DATABASE_URL не задан.", file=sys.stderr)
        return 2

    uow_factory = build_identity_uow_factory(build_engine(database_url))
    now = datetime.now(timezone.utc)
    rows = import_staff(people, uow_factory(), now, apply=args.apply, reset_pins=args.reset_pins)
    _print_report(rows, apply=args.apply)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
