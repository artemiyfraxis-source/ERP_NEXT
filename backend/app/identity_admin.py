"""Interactive administration entrypoint for bootstrapping native Identity."""

from __future__ import annotations

import argparse
import getpass
import os
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.database import build_engine
from app.modules.identity.domain import (
    ACCESS_LEVELS,
    LEVEL_SECTION_LABELS,
    ROLES,
    AccessMode,
    IdentityUser,
    hash_password,
    normalize_login,
)
from app.modules.identity.infrastructure import IdentityIdentifierRow, provision_user
from app.modules.identity.level_migration import level_for_role, shop_worker_rule, site_master_rule


def requested_access(values: list[str]) -> dict[str, AccessMode]:
    """--permission SECTION=none|view|edit: the person's exceptions on top of their level row."""
    result: dict[str, AccessMode] = {}
    for value in values:
        try:
            section, raw_mode = value.split("=", 1)
            section = section.strip()
            if section not in LEVEL_SECTION_LABELS:
                raise KeyError(section)
            result[section] = AccessMode(raw_mode.strip())
        except (ValueError, KeyError) as error:
            raise SystemExit(
                f"Invalid permission {value!r}; expected SECTION=none|view|edit, SECTION one of "
                f"{', '.join(LEVEL_SECTION_LABELS)}"
            ) from error
    return result


def requested_level(args: argparse.Namespace) -> str:
    """--level; the legacy --role (kept for old scripts) gives the level its role maps to."""
    if args.level:
        return args.level
    if args.role:
        return level_for_role(args.role)
    raise SystemExit("Pass --level (" + ", ".join(ACCESS_LEVELS) + ")")


def create_user(args: argparse.Namespace, sessions: sessionmaker[Session]) -> None:
    # Scripts pass the password in ERP_NEW_USER_PASSWORD: getpass reads the console, not a pipe, on Windows.
    password = os.getenv("ERP_NEW_USER_PASSWORD", "")
    if not password:
        password = getpass.getpass("New ERP password: ")
        confirmation = getpass.getpass("Repeat password: ")
        if password != confirmation:
            raise SystemExit("Passwords do not match")
    level = requested_level(args)
    role = args.role or ""
    level_access = requested_access(args.permission)
    identifiers = {
        normalize_login(args.login),
        normalize_login(args.email),
    }

    with sessions() as session:
        existing = session.scalar(
            select(IdentityIdentifierRow).where(
                IdentityIdentifierRow.identifier.in_(identifiers)
            )
        )
        if existing is not None:
            raise SystemExit("A user with this login or email already exists")
        user = IdentityUser(
            id=uuid4(),
            login=args.login,
            email=args.email,
            name=args.name,
            department=args.department,
            role=role,
            site=args.site,
            active=True,
            access_level=level,
            shop_worker=shop_worker_rule(role, args.site, args.department),
            site_master=site_master_rule(role),
        )
        provision_user(
            session,
            user,
            hash_password(password),
            # The level's sections = its «Доступы» matrix row (empty = closed; superadmin/admin = edit everywhere).
            level_access=level_access,
        )
        session.commit()
        print(f"Created {user.login} ({user.id}), level {level}")


def pin_reveal(args: argparse.Namespace, sessions: sessionmaker[Session]) -> None:
    """SUPPLYMAP pinRevealOn / pinRevealOff: «узнать PIN при первом входе» on the login screen."""
    from app.modules.identity.application import set_pin_reveal
    from app.modules.identity.infrastructure import IdentityUserRow, SqlAlchemyIdentityUnitOfWork

    enabled = args.state == "on"
    set_pin_reveal(enabled, SqlAlchemyIdentityUnitOfWork(sessions))
    if enabled:
        with sessions() as session:
            seen = len(list(session.scalars(select(IdentityUserRow.id).where(IdentityUserRow.seen_at.is_not(None)))))
        print(
            "Режим «узнать пароль при первом входе» ВКЛЮЧЁН. Уже входивших (пароль им не выдаётся): "
            f"{seen}. Выключить: pin-reveal off."
        )
    else:
        print("Режим «узнать пароль» ВЫКЛЮЧЕН — вход только по PIN.")


CONSOLE_ACTOR_NAME = "Консоль сервера"


def release_login(args: argparse.Namespace, sessions: sessionmaker[Session]) -> None:
    """Audit U09 emergency path: lift a login lock from the server console when no administrator can sign in."""
    from uuid import UUID

    from app.modules.identity.application import release_login_locks
    from app.modules.identity.infrastructure import SqlAlchemyIdentityUnitOfWork
    from app.shared.clock import moscow_now
    from app.shared.identity import Actor

    if bool(args.login) == bool(args.all):
        raise SystemExit("Pass a login, or --all to lift every login lock")
    actor = Actor(id=UUID(int=0), name=CONSOLE_ACTOR_NAME, department="Система")
    released = release_login_locks(actor, moscow_now(), SqlAlchemyIdentityUnitOfWork(sessions), login=args.login or None)
    target = f"«{args.login}»" if args.login else "все"
    print(f"Блокировки входа сняты ({target}): {released}. Запись — в системном журнале.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Administer ERP Next Identity")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-user")
    create.add_argument("--login", required=True)
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--department", required=True)
    create.add_argument("--level", choices=ACCESS_LEVELS, help="access level: " + ", ".join(ACCESS_LEVELS))
    create.add_argument("--role", choices=sorted(ROLES), help="legacy role; without --level the level follows it")
    create.add_argument("--site")
    create.add_argument(
        "--permission",
        action="append",
        default=[],
        help="Personal exception on top of the level row, for example procurement=edit",
    )
    reveal = subparsers.add_parser("pin-reveal", help="«узнать PIN при первом входе» (pinRevealOn/Off)")
    reveal.add_argument("state", choices=("on", "off"))
    release = subparsers.add_parser("release-login", help="lift a login lock (one account, or --all)")
    release.add_argument("login", nargs="?", default="")
    release.add_argument("--all", action="store_true", help="every login lock: accounts, addresses, the overall count")
    args = parser.parse_args()

    database_url = os.getenv("ERP_DATABASE_URL")
    if not database_url:
        raise SystemExit("Set ERP_DATABASE_URL before administering Identity")
    engine = build_engine(database_url)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    try:
        if args.command == "create-user":
            create_user(args, sessions)
        elif args.command == "pin-reveal":
            pin_reveal(args, sessions)
        elif args.command == "release-login":
            release_login(args, sessions)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
