"""Box and defect numbers issued before 0588fba carry a 3/1 (EAN) check digit: recompute it by Luhn (PX-2).

Only a number whose check digit is exactly the old 3/1 one is rewritten; the six-digit body stays, so the
unique numbers stay unique. A number that already passes Luhn, or matches neither scheme, is left as it is.
Audit events keep the number that was issued at the time: they are history, not state.
"""

import re
from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0043_production_box_luhn_check_digits"
down_revision: str | None = "0042_shipping_project_addressees"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NUMBER = re.compile(r"^(Б-)?(\d{6})-(\d)$")
COLUMNS = (
    ("production_boxes", "number"),
    ("production_defects", "number"),
    ("production_qc_inspections", "defect_number"),
)


def _luhn(body: str) -> int:
    total = 0
    for offset, char in enumerate(reversed(body)):
        digit = int(char)
        if offset % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return (10 - total % 10) % 10


def _ean(body: str) -> int:
    weighted = sum(int(char) * (3 if offset % 2 == 0 else 1) for offset, char in enumerate(reversed(body)))
    return (10 - weighted % 10) % 10


def luhn_number(number: str | None) -> str | None:
    """The Luhn spelling of a number issued with the 3/1 check digit, or None when it needs no change."""
    match = NUMBER.fullmatch(number or "")
    if match is None:
        return None
    prefix, body, check = match.groups()
    if int(check) == _luhn(body) or int(check) != _ean(body):
        return None
    return f"{prefix or ''}{body}-{_luhn(body)}"


def upgrade() -> None:
    bind = op.get_bind()
    for table, column in COLUMNS:
        rows = bind.execute(sa.text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL")).fetchall()
        for row_id, number in rows:
            fixed = luhn_number(number)
            if fixed is not None:
                bind.execute(
                    sa.text(f"UPDATE {table} SET {column} = :fixed WHERE id = :id"),
                    {"fixed": fixed, "id": row_id},
                )


def downgrade() -> None:
    # Irreversible by design: after the upgrade a Luhn number cannot tell whether it was issued by Luhn
    # or rewritten here, and the 3/1 spelling is rejected by the code on both sides of this revision.
    pass
