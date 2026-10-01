"""Short logins and passwords for every employee (owner 01.10.2026: «давай на логины перейдём… сделай список логинов
и паролей, только не битый» — replaces the «choose yourself + PIN» sign-in of #100).

    python -m app.modules.identity.issue_logins --out /app/data/import/logins.csv           # only who has none yet
    python -m app.modules.identity.issue_logins --out /app/data/import/logins.csv --reset   # new passwords for all

A short login is the transliterated surname («Безменов Никита» → bezmenov; a clash adds the first letter of the name,
then a digit). The e-mail keeps working as a login too. Whoever already has a short login (no «@») keeps it and their
password unless --reset; the technical «admin» account is left alone. The list is a CSV in UTF-8 with BOM and «;» —
Excel and Notepad open the Cyrillic as is. Passwords are printed nowhere else. A run that issues nothing leaves the
list alone; a run that issues something keeps the previous list as logins-<stamp>.csv. update.sh runs it after every
deploy, so a new employee gets a login on the next update.
"""

from __future__ import annotations

import argparse
import csv
import os
import secrets
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import build_engine
from app.modules.identity.domain import hash_password
from app.modules.identity.infrastructure import IdentityCredentialRow, IdentityIdentifierRow, IdentityUserRow

TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
# No 0/O, 1/l/I: the list is read off paper.
ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def translit(text: str) -> str:
    out = "".join(TRANSLIT.get(ch, ch) for ch in text.casefold())
    return "".join(ch for ch in out if ch.isascii() and ch.isalnum())


def short_login(name: str, taken: set[str]) -> str:
    parts = name.split()
    surname = translit(parts[0]) if parts else ""
    first = translit(parts[1])[:1] if len(parts) > 1 else ""
    base = surname or "user"
    for candidate in (base, base + first):
        if candidate and candidate not in taken:
            return candidate
    n = 2
    while f"{base}{first}{n}" in taken:
        n += 1
    return f"{base}{first}{n}"


def new_password() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(10))


def issue(session: Session, *, reset: bool) -> list[dict[str, str]]:
    taken = set(session.scalars(select(IdentityIdentifierRow.identifier)))
    rows: list[dict[str, str]] = []
    users = sorted(session.scalars(select(IdentityUserRow).where(IdentityUserRow.active.is_(True))), key=lambda u: u.name)
    for user in users:
        if user.login == "admin":
            continue
        has_short = "@" not in user.login
        password = ""
        if not has_short:
            login = short_login(user.name, taken)
            taken.add(login)
            session.add(IdentityIdentifierRow(identifier=login, user_id=user.id))  # the e-mail identifier stays
            user.login = login
        if not has_short or reset:
            password = new_password()
            credential = session.get(IdentityCredentialRow, user.id)
            if credential is None:
                session.add(IdentityCredentialRow(user_id=user.id, password_hash=hash_password(password)))
            else:
                credential.password_hash = hash_password(password)
        rows.append({
            "ФИО": user.name, "Логин": user.login, "Пароль": password or "(прежний)",
            "Роль": user.role, "Отдел": user.department, "Почта": user.email,
        })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="CSV (UTF-8 with BOM) for the owner")
    parser.add_argument("--reset", action="store_true", help="new passwords for everybody, short logins kept")
    args = parser.parse_args()
    url = os.getenv("ERP_DATABASE_URL")
    if not url:
        raise SystemExit("Set ERP_DATABASE_URL")
    engine = build_engine(url)
    try:
        with Session(engine) as session:
            rows = issue(session, reset=args.reset)
            issued = sum(1 for r in rows if r["Пароль"] != "(прежний)")
            out = Path(args.out)
            if not issued and out.exists():
                print(f"Новых логинов нет, список {args.out} не тронут")
                return
            if out.exists():  # the previous list still holds passwords nobody else has — keep it
                out.rename(out.with_name(f"{out.stem}-{datetime.now():%Y%m%d-%H%M%S}{out.suffix}"))
            with out.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["ФИО"], delimiter=";")
                writer.writeheader()
                writer.writerows(rows)
            os.chmod(out, 0o600)
            session.commit()
    finally:
        engine.dispose()
    print(f"Сотрудников: {len(rows)}, выдано паролей: {issued}. Список: {args.out}")


if __name__ == "__main__":
    main()
