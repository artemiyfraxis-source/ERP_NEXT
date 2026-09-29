"""Switch-to-real helper: deactivate SUPPLYMAP directory entries that are test records, not employees.

    ERP_DATABASE_URL=... python ../scripts/demo/drop_test_staff.py

«Вася Пупкин» (artemiyfraxis@gmail.com) is the owner's test entry in the Google staff directory (owner, 29.09:
«тестовых сотрудников нахер»). Deactivated, not deleted — the audit keeps who did it. Re-running is harmless.
"""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))

from app.database import build_engine, build_identity_uow_factory  # noqa: E402
from app.modules.identity.application import deactivate_user  # noqa: E402
from app.modules.identity.import_staff import staff_id  # noqa: E402
from app.shared.identity import Actor  # noqa: E402

TEST_EMAILS = ["artemiyfraxis@gmail.com"]

uow_factory = build_identity_uow_factory(build_engine(os.environ["ERP_DATABASE_URL"]))
actor = Actor(id=staff_id("system@erp.local"), name="Перенос из Google", department="Система")
for email in TEST_EMAILS:
    try:
        user = deactivate_user(staff_id(email), actor, datetime.now(timezone.utc), uow_factory())
        print(f"отключён тестовый сотрудник: {user.name} ({email})")
    except KeyError:
        print(f"нет в базе: {email}")
