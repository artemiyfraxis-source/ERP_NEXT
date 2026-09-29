"""Interactive administration entrypoint for bootstrapping native Identity."""

from __future__ import annotations

import argparse
import getpass
import os
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.database import build_engine
from app.modules.identity.domain import (  # noqa: F401 — ROLE_DEFAULTS is re-exported for callers
    ROLE_DEFAULTS,
    ROLES,
    AccessMode,
    IdentityUser,
    hash_password,
    normalize_login,
)
from app.modules.identity.infrastructure import IdentityIdentifierRow, provision_user


def requested_access(values: list[str], role: str) -> dict[str, AccessMode]:
    del role
    result: dict[str, AccessMode] = {}
    for value in values:
        try:
            section, raw_mode = value.split("=", 1)
            result[section.strip()] = AccessMode(raw_mode.strip())
        except (ValueError, KeyError) as error:
            raise SystemExit(
                f"Invalid permission {value!r}; expected SECTION=view|edit"
            ) from error
    return result


def create_user(args: argparse.Namespace, sessions: sessionmaker[Session]) -> None:
    password = getpass.getpass("New ERP password: ")
    confirmation = getpass.getpass("Repeat password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    user_access = requested_access(args.permission, args.role)
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
            role=args.role,
            site=args.site,
            active=True,
        )
        provision_user(
            session,
            user,
            hash_password(password),
            # The role's sections = ROLE_DEFAULTS + the «Доступы» matrix; nothing is seeded (NAV-ACCESS).
            user_access=user_access,
        )
        session.commit()
        print(f"Created {user.login} ({user.id})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Administer ERP Next Identity")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-user")
    create.add_argument("--login", required=True)
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--department", required=True)
    create.add_argument("--role", choices=sorted(ROLES), required=True)
    create.add_argument("--site")
    create.add_argument(
        "--permission",
        action="append",
        default=[],
        help="User permission override, for example procurement=edit",
    )
    args = parser.parse_args()

    database_url = os.getenv("ERP_DATABASE_URL")
    if not database_url:
        raise SystemExit("Set ERP_DATABASE_URL before administering Identity")
    engine = build_engine(database_url)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    try:
        if args.command == "create-user":
            create_user(args, sessions)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
