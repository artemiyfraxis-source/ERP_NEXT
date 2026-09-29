"""STAFF-IMPORT CLI: the SUPPLYMAP staff directory → ERP employees, with the PIN of each printed for the owner.

    ERP_DATABASE_URL=... python -m app.modules.identity.import_staff --dry-run
    ERP_DATABASE_URL=... python -m app.modules.identity.import_staff --apply

Source (git snapshots, read-only — the live directory is ScriptProperties `staff_directory`, unreachable from here):
  · ``SUPPLYMAP/Config.gs`` ``STAFF_BY_EMAIL`` — name + department (+ a first-run role);
  · ``SUPPLYMAP/_local/access-backup-2026-09-01.json`` — the role each employee actually has now.
The SUPPLYMAP repo is the sibling ``../SUPPLYMAP`` unless ``SUPPLYMAP_SOURCE_REPOSITORY`` (or ``--source``) points
elsewhere. E-mail is the key; the ERP id is derived from it, so re-running touches the same rows.

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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from app.database import build_engine, build_identity_uow_factory

from .application import UnitOfWork
from .domain import (
    IdentityUser,
    hash_password,
    hash_pin,
    new_pin,
    normalize_department,
    normalize_email,
    normalize_login,
    normalize_name,
    normalize_role,
)

# ID-4/ID-8: the ERP id of an imported employee is fixed by e-mail, so the import is idempotent.
STAFF_ID_NAMESPACE = "fraxis-erp:identity:staff:"

DEFAULT_SOURCE = Path(__file__).resolve().parents[5] / "SUPPLYMAP"
CONFIG_RELATIVE = Path("Config.gs")
ACCESS_BACKUP_RELATIVE = Path("_local") / "access-backup-2026-09-01.json"

# SUPPLYMAP role keys → ERP role keys. Every current portal role already exists in ROLE_LABELS; the map is here so
# a renamed or retired SUPPLYMAP role lands on a real ERP role instead of a silent «staff».
ROLE_MAP = {
    "admin": "admin",
    "buyer": "buyer",
    "receiver": "receiver",
    "prodhead": "prodhead",
    "master": "master",
    "management": "management",
    "staff": "staff",
}


class StaffImportError(RuntimeError):
    """The SUPPLYMAP snapshot cannot be read."""


@dataclass(frozen=True)
class SourceEmployee:
    email: str
    name: str
    department: str
    role: str


# Монтажники (installers), owner 2026-09-29: they are not in the SUPPLYMAP snapshot (new people), so the import adds
# them itself, on the same run and with a PIN each — the master assigns operations to them, and the operator list is
# every active employee (production has no role filter), so the role is «Сотрудник» (staff); department «Монтаж» puts them on the master board (MasterBoard.installers).
# Logins are transliterated; there are no real mailboxes yet (login is by name + PIN), the administrator can edit them.
ADDITIONAL_STAFF: list[SourceEmployee] = [
    SourceEmployee(email="rybkina.olga@fraxis.local", name="Рыбкина Ольга", department="Монтаж", role="staff"),
    SourceEmployee(email="epifanov.ilya@fraxis.local", name="Епифанов Илья", department="Монтаж", role="staff"),
    SourceEmployee(email="popov.ignat@fraxis.local", name="Попов Игнат", department="Монтаж", role="staff"),
    SourceEmployee(email="bogorodskaya.elina@fraxis.local", name="Богородская Элина", department="Монтаж", role="staff"),
    SourceEmployee(email="makarova.polina@fraxis.local", name="Макарова Полина", department="Монтаж", role="staff"),
    SourceEmployee(email="naumova.elena@fraxis.local", name="Наумова Елена", department="Монтаж", role="staff"),
    SourceEmployee(email="puzanov.aleksandr@fraxis.local", name="Пузанов Александр", department="Монтаж", role="staff"),
    SourceEmployee(email="dumanskaya.anna@fraxis.local", name="Думанская Анна", department="Монтаж", role="staff"),
    SourceEmployee(email="shilov.artem@fraxis.local", name="Шилов Артем", department="Монтаж", role="staff"),
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


def read_source(source: Path) -> list[SourceEmployee]:
    config_path = source / CONFIG_RELATIVE
    if not config_path.is_file():
        raise StaffImportError(f"нет {config_path} — укажи --source или SUPPLYMAP_SOURCE_REPOSITORY")
    directory = _parse_config_staff(config_path.read_text(encoding="utf-8"))
    roles = _load_access_roles(source / ACCESS_BACKUP_RELATIVE)
    people: list[SourceEmployee] = []
    for email, info in directory.items():
        raw_role = roles.get(email) or info.get("role") or "staff"  # access backup is fresher than the seed's role
        people.append(SourceEmployee(
            email=email,
            name=info.get("name", "") or email,
            department=info.get("dept", ""),
            role=ROLE_MAP.get(raw_role, "staff"),
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
    role: str
    pin: str | None  # None → skipped (already present, PINs kept)
    action: str  # "создан" / "обновлён" / "пропущен (уже есть)"


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
            current = existing.get(email)
            if current is None:
                pin = new_pin()
                if apply:
                    user = IdentityUser(
                        id=staff_id(email), login=login, email=email, name=name,
                        department=department, role=role, site=None, active=True,
                    )
                    # No password: only the PIN opens the door (SUPPLYMAP had PINs only).
                    uow.identity.add_user(user, hash_password(secrets.token_urlsafe(32)), {}, hash_pin(pin))
                rows.append(ImportedRow(name=name, login=login, role=role, pin=pin, action="создан"))
            elif reset_pins:
                pin = new_pin()
                if apply:
                    uow.identity.set_pin(current.id, hash_pin(pin))
                rows.append(ImportedRow(name=current.name, login=current.login, role=current.role, pin=pin,
                                        action="PIN перевыдан"))
            else:
                rows.append(ImportedRow(name=current.name, login=current.login, role=current.role, pin=None,
                                        action="пропущен (уже есть)"))
        if apply:
            uow.commit()
    return rows


def _print_report(rows: list[ImportedRow], *, apply: bool) -> None:
    head = "ПЕРЕНОС СОТРУДНИКОВ" if apply else "ПЕРЕНОС СОТРУДНИКОВ (dry-run, база не тронута)"
    print(head)
    print(f"{'ФИО':<24} {'Логин':<32} {'Роль':<12} PIN")
    print("-" * 84)
    for row in rows:
        pin = row.pin if row.pin is not None else "—"
        print(f"{row.name:<24} {row.login:<32} {row.role:<12} {pin:<8} {row.action}")
    created = sum(1 for row in rows if row.action == "создан")
    reissued = sum(1 for row in rows if row.action == "PIN перевыдан")
    skipped = sum(1 for row in rows if row.pin is None)
    print("-" * 84)
    print(f"создано: {created}, PIN перевыдано: {reissued}, пропущено: {skipped}")
    if any(row.pin for row in rows):
        print("Раздайте PIN сотрудникам — вход по имени и PIN. Пароль не нужен.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Перенос сотрудников SUPPLYMAP → ERP с выдачей PIN")
    parser.add_argument("--source", type=Path, default=None, help="путь к репозиторию SUPPLYMAP")
    parser.add_argument("--dry-run", action="store_true", help="только показать (поведение по умолчанию)")
    parser.add_argument("--apply", action="store_true", help="записать в базу (по умолчанию — dry-run)")
    parser.add_argument("--reset-pins", action="store_true", help="перевыдать PIN и уже заведённым сотрудникам")
    args = parser.parse_args(argv)

    source = args.source or Path(os.getenv("SUPPLYMAP_SOURCE_REPOSITORY", str(DEFAULT_SOURCE)))
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
