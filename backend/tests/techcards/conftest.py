from uuid import UUID

import pytest

from app.main import app
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.identity import Actor


@pytest.fixture(autouse=True)
def authenticated_techcards_user():
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(
            id=UUID("30000000-0000-0000-0000-000000000001"),
            name="Инженер",
            department="Производство",
        ),
        email="engineer@example.test",
        roles=frozenset({"engineer"}),
        permissions={"techcards": "edit"},
    )
    yield
    app.dependency_overrides.pop(authenticated_principal, None)
